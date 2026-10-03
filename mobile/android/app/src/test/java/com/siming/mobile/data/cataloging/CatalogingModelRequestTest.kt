package com.siming.mobile.data.cataloging

import com.siming.mobile.data.network.*
import java.io.File
import java.net.Proxy
import java.util.concurrent.TimeUnit
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class CatalogingModelRequestTest {
    private val contract = CatalogingContract(Json.parseToJsonElement(File("src/main/assets/pc_workspace_prompt_contract.json").readText()).jsonObject)

    @Test fun `flat model schema keeps strict candidate validation`() {
        val saveSchema = contract.schemas.first {
            it.jsonObject.getValue("function").jsonObject.text("name") == "save_external_cataloging_candidates"
        }.jsonObject.getValue("function").jsonObject.getValue("parameters").jsonObject
            .getValue("properties").jsonObject.getValue("candidates").jsonObject.getValue("items").jsonObject
        assertFalse("anyOf" in saveSchema)
        assertTrue("character_state_update" in saveSchema.getValue("properties").jsonObject
            .getValue("type").jsonObject.strings("enum"))
        val state = buildJsonObject {
            put("type", "character_state_update")
            put("id", "existing-id")
            put("name", "已有角色")
            put("current_location", "宗门")
        }
        contract.validateCandidate(state)
        val invalidCreate = buildJsonObject {
            put("type", "character_create")
            put("id", "existing-id")
            put("name", "已有角色")
        }
        assertFailsWith<IllegalArgumentException> { contract.validateCandidate(invalidCreate) }
    }

    @Test fun `first cataloging write exposes only the required chapter summary fields`() {
        fun candidateItem(schemas: JsonArray): JsonObject = schemas.first {
            it.jsonObject.getValue("function").jsonObject.text("name") == "save_external_cataloging_candidates"
        }.jsonObject.getValue("function").jsonObject.getValue("parameters").jsonObject
            .getValue("properties").jsonObject.getValue("candidates").jsonObject.getValue("items").jsonObject

        val summary = candidateItem(contract.schemasForPlan(summaryRequired = true))
        assertEquals(listOf("chapter_summary"), summary.getValue("properties").jsonObject
            .getValue("type").jsonObject.strings("enum"))
        assertTrue(summary.strings("required").containsAll(listOf(
            "type", "summary_text", "coverage_manifest", "scenes", "character_bindings",
            "worldbuilding_bindings", "narrative_state", "narrative_review",
        )))
        assertFalse("title" in summary.getValue("properties").jsonObject)
        val selectionStep = contract.schemasForPlan(summaryRequired = false)
        assertTrue(selectionStep.any { it.jsonObject.getValue("function").jsonObject.text("name") ==
            "select_cataloging_candidate_types" })
        assertTrue(selectionStep.any { it.jsonObject.getValue("function").jsonObject.text("name") ==
            "save_external_cataloging_candidates" })
        val selectionSave = selectionStep.first { it.jsonObject.getValue("function").jsonObject.text("name") ==
            "save_external_cataloging_candidates" }.jsonObject.getValue("function").jsonObject
            .getValue("parameters").jsonObject.getValue("properties").jsonObject.getValue("candidates").jsonObject
        assertEquals(0, selectionSave.number("maxItems"))
        val select = selectionStep.first { it.jsonObject.obj("function").text("name") ==
            "select_cataloging_candidate_types" }.jsonObject
        val selection = select.obj("function").obj("parameters").obj("properties").obj("types")
        assertEquals(1, selection.number("maxItems"))
        assertFailsWith<IllegalArgumentException> {
            contract.validateTool("select_cataloging_candidate_types", buildJsonObject {
                put("types", jsonStrings(listOf("outline_create_chapter", "chapter_link")))
            }, selectionStep)
        }
        val chapterOutline = candidateItem(contract.schemasForPlan(
            summaryRequired = false, selectedType = "outline_create_chapter"
        ))
        assertEquals(listOf("outline_create"), chapterOutline
            .getValue("properties").jsonObject.getValue("type").jsonObject.strings("enum"))
        assertEquals(listOf("chapter"), chapterOutline
            .getValue("properties").jsonObject.getValue("node_type").jsonObject.strings("enum"))
        val sectionOutline = candidateItem(contract.schemasForPlan(
            summaryRequired = false, selectedType = "outline_update_section",
            chapterOutlineId = "chapter-outline", sectionOutlineIds = listOf("section-outline"), sceneCount = 3,
        ))
        assertEquals(listOf("outline_update"), sectionOutline.getValue("properties").jsonObject
            .getValue("type").jsonObject.strings("enum"))
        assertTrue("scene_number" in sectionOutline.strings("required"))
        assertEquals(listOf("section-outline"), sectionOutline.getValue("properties").jsonObject
            .getValue("id").jsonObject.strings("enum"))
        val sectionBatch = contract.schemasForPlan(
            summaryRequired = false, selectedType = "outline_update_section",
            chapterOutlineId = "chapter-outline", sectionOutlineIds = listOf("section-outline"), sceneCount = 3,
        ).first { it.jsonObject.getValue("function").jsonObject.text("name") == "save_external_cataloging_candidates" }
            .jsonObject.obj("function").obj("parameters").obj("properties").obj("candidates")
        assertEquals(3, sectionBatch.number("maxItems"))
        val planSummary = buildJsonObject {
            put("character_bindings", JsonArray(emptyList()))
            put("worldbuilding_bindings", JsonArray(listOf("world-1", "world-2", "world-3").map { id ->
                buildJsonObject { put("id", id); put("decision", "existing") }
            }))
            put("coverage_manifest", buildJsonObject { put("relationships", JsonArray(emptyList())) })
        }
        val worldTools = contract.schemasForPlan(
            summaryRequired = false, selectedType = "worldbuilding_update", summary = planSummary,
        )
        val world = candidateItem(worldTools)
        assertTrue("content" in world.strings("required"))
        assertEquals(listOf("world-1", "world-2", "world-3"), world.obj("properties")
            .obj("id").strings("enum"))
        val worldBatch = worldTools.first { it.jsonObject.obj("function").text("name") ==
            "save_external_cataloging_candidates" }.jsonObject.obj("function").obj("parameters")
            .obj("properties").obj("candidates")
        assertEquals(3, worldBatch.number("maxItems"))
        val linkSummary = JsonObject(planSummary + ("character_bindings" to JsonArray(listOf(
            buildJsonObject { put("name", "李玄"); put("id", "character-1"); put("decision", "existing") },
            buildJsonObject { put("name", "苏星河"); put("id", "character-2"); put("decision", "existing") },
        ))))
        val link = candidateItem(contract.schemasForPlan(
            summaryRequired = false, selectedType = "chapter_link", summary = linkSummary,
        ))
        assertTrue(link.strings("required").containsAll(listOf("characters", "worldbuilding_titles")))
        assertEquals(2, link.obj("properties").obj("characters").number("minItems"))
        assertEquals(3, link.obj("properties").obj("worldbuilding_titles").number("minItems"))
    }

    @Test fun `real cataloging adapter applies PC request policy for both DeepSeek protocols`() = runBlocking {
        listOf(DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, DirectApiConfig.PROTOCOL_RESPONSES).forEach { protocol ->
            MockWebServer().use { server ->
                val event = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    """{"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","call_id":"call-1","name":"get_next_external_cataloging_chapter","arguments":"{\"job_id\":\"test-job\"}"}]}}"""
                } else {
                    """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"get_next_external_cataloging_chapter","arguments":"{\"job_id\":\"test-job\"}"}}]},"finish_reason":"tool_calls"}]}"""
                }
                server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                    .setBody("data: $event\n\ndata: [DONE]\n\n").setHeadersDelay(300, TimeUnit.MILLISECONDS))
                server.start()
                val client = DirectApiClient(OkHttpClient.Builder().proxy(Proxy.NO_PROXY).readTimeout(120, TimeUnit.MILLISECONDS)
                    .callTimeout(150, TimeUnit.MILLISECONDS).build(), allowCleartextForTests = true)
                val config = DirectApiConfig("DeepSeek", server.url("/").newBuilder().host("127.0.0.1").build().toString().trimEnd('/'), "fixture-key",
                    "deepseek-flash", protocol, maxOutputTokens = 5_000)
                val result = contract.modelRequest.execute(client, config,
                    listOf(buildJsonObject { put("role", "user"); put("content", "catalog saved chapter") }), contract.schemas) {}
                assertEquals("get_next_external_cataloging_chapter", result.toolCalls.single().name)
                val payload = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
                if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    assertEquals("none", payload.obj("reasoning").text("effort"))
                    assertEquals(5_000, payload.number("max_output_tokens"))
                    assertFalse("thinking" in payload)
                } else {
                    assertEquals("disabled", payload.obj("thinking").text("type"))
                    assertEquals(5_000, payload.number("max_tokens"))
                    assertFalse("reasoning" in payload)
                }
                assertEquals(contract.names, payload.objects("tools").map {
                    if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) it.text("name") else it.obj("function").text("name")
                }.toSet())
                assertEquals(300, contract.modelRequest.idleTimeoutSeconds)
            }
        }
    }
}
