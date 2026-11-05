package com.siming.mobile.data.cataloging

import com.siming.mobile.data.network.*
import java.io.File
import java.net.Proxy
import java.util.concurrent.TimeUnit
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class CatalogingModelRequestTest {
    private val contract = CatalogingContract(Json.parseToJsonElement(File("src/main/assets/pc_workspace_prompt_contract.json").readText()).jsonObject)

    @Test fun `failure display retains field path without dumping full candidate payloads`() {
        val receipt = buildJsonObject {
            put("status", "error")
            put("data", buildJsonObject {
                put("candidate_errors", JsonArray(listOf(buildJsonObject { put("message", "narrative_state.foreshadowing_planted[0].title 必填") })))
                put("scene_repair", buildJsonObject { put("large_private_payload", "not for error banner") })
            })
        }
        assertEquals("narrative_state.foreshadowing_planted[0].title 必填", catalogingFailureDetail(listOf(buildJsonObject { put("role", "tool"); put("content", receipt.toString()) })))
    }

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
                    .callTimeout(150, TimeUnit.MILLISECONDS).addInterceptor { chain ->
                        chain.proceed(chain.request().newBuilder().url(server.url(chain.request().url.encodedPath)).build())
                    }.build(), allowCleartextForTests = true)
                val config = DirectApiConfig("renamed-api", "https://api.deepseek.com", "fixture-key",
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

    @Test fun `standalone SiliconFlow cataloging uses host policy for both Qwen and DeepSeek`() = runBlocking {
        listOf("Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V3.2").forEach { model ->
            var payload: JsonObject? = null
            val http = OkHttpClient.Builder().addInterceptor { chain ->
                val request = chain.request()
                val buffer = Buffer()
                request.body!!.writeTo(buffer)
                payload = Json.parseToJsonElement(buffer.readUtf8()).jsonObject
                val event = """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"get_next_external_cataloging_chapter","arguments":"{\"job_id\":\"test-job\"}"}}]},"finish_reason":"tool_calls"}]}"""
                Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                    .body("data: $event\n\ndata: [DONE]\n\n".toResponseBody("text/event-stream".toMediaType()))
                    .build()
            }.build()
            val config = DirectApiConfig("guiji", "https://api.siliconflow.cn/v1", "fixture-key",
                model, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
            val result = contract.modelRequest.execute(DirectApiClient(http), config,
                listOf(buildJsonObject { put("role", "user"); put("content", "catalog saved chapter") }), contract.schemas) {}
            assertEquals("get_next_external_cataloging_chapter", result.toolCalls.single().name)
            val sent = requireNotNull(payload)
            assertEquals(false, sent.getValue("enable_thinking").jsonPrimitive.boolean)
            assertFalse("thinking" in sent)
            assertFalse("reasoning" in sent)
            assertEquals(contract.names, sent.objects("tools").map { it.obj("function").text("name") }.toSet())
        }
    }

    @Test fun `cataloging refuses oversized actual payload before any HTTP request`() = runBlocking {
        var requests = 0
        val client = DirectApiClient(OkHttpClient.Builder().addInterceptor {
            requests++
            error("oversized request must never reach HTTP")
        }.build())
        val config = DirectApiConfig("local", "https://example.test/v1", "fixture-key", "local-model",
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, contextWindowTokens = 64_000)
        val messages = listOf(buildJsonObject { put("role", "user"); put("content", "完整资料".repeat(10_000)) })
        val budget = contract.modelRequest.budget(client, config, messages, contract.schemasForPlan(false))
        assertFalse(budget.fitsCurrent)
        assertEquals(64_000 - 6_000 - 4_096, budget.requestInputLimit)
        assertFailsWith<com.siming.mobile.data.agent.MobileConversationContextException> {
            contract.modelRequest.execute(client, config, messages, contract.schemasForPlan(false)) {}
        }
        assertEquals(0, requests)
    }
}
