package com.siming.mobile.data.creation

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.File
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Test
import kotlin.test.*

class CreationModelRequestTest {
    private val rawContract = listOf(File("app/src/main/assets/pc_workspace_prompt_contract.json"),
        File("src/main/assets/pc_workspace_prompt_contract.json")).first { it.isFile }.readText(Charsets.UTF_8)
    private val contract = PcCreationPromptContract(rawContract)
    private fun card() = Json.parseToJsonElement(rawContract).jsonObject.getValue("creation").jsonObject
        .getValue("concept_shape").jsonObject.getValue("concepts").jsonArray.first()
    private fun stream(text: String, finish: String = "stop") = MockResponse().setHeader("Content-Type", "text/event-stream")
        .setBody("data: " + buildJsonObject {
            put("choices", buildJsonArray { add(buildJsonObject {
                put("delta", buildJsonObject { put("content", text) }); put("finish_reason", finish)
            }) })
        } + "\n\ndata: [DONE]\n\n")

    @Test fun repairsSingleConceptObjectUsingTheFullCardSchemaAndKeepsSourceUntouched() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(stream(buildJsonObject { put("concepts", card()) }.toString()))
            server.enqueue(stream(buildJsonObject { put("concepts", JsonArray(listOf(card()))) }.toString()))
            val agent = MobileCreationAgent(rawContract, DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList()))
            val source = agent.start(CreationStartInput("author_led", "单一方向"))
            val config = DirectApiConfig("deepseek", server.url("/").toString(), "fixture-key", "deepseek-flash",
                protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
            val result = agent.generateStage(source, "concepts", "一个方向", config)
            assertEquals(0, source.getValue("revision").jsonPrimitive.int)
            assertEquals(1, result.getValue("revision").jsonPrimitive.int)
            val generation = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            val repair = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            assertEquals(JsonPrimitive(true), generation["stream"])
            assertEquals("disabled", generation.getValue("thinking").jsonObject.getValue("type").jsonPrimitive.content)
            val prompt = repair.getValue("messages").jsonArray.last().jsonObject.getValue("content").jsonPrimitive.content
            assertTrue("protagonist_seed" in prompt && "opening_hook" in prompt && "数组" in prompt)
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun truncatedStreamCannotSaveEvenSyntacticallyCompleteJson() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(stream(buildJsonObject { put("concepts", JsonArray(listOf(card()))) }.toString(), "length"))
            val agent = MobileCreationAgent(rawContract, DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList()))
            val source = agent.start(CreationStartInput("explore", "奇幻小说"))
            val config = DirectApiConfig("test", server.url("/").toString(), "fixture", "model",
                protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
            assertFailsWith<Exception> { agent.generateStage(source, "concepts", "", config) }
            assertEquals(0, source.getValue("revision").jsonPrimitive.int)
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun responsesUsesItsOwnNonThinkingParameterAndConfiguredStageBudget() = runBlocking {
        MockWebServer().use { server ->
            val response = buildJsonObject {
                put("type", "response.completed")
                put("response", buildJsonObject {
                    put("id", "fixture"); put("status", "completed")
                    put("output", buildJsonArray { add(buildJsonObject {
                        put("type", "message"); put("role", "assistant")
                        put("content", buildJsonArray { add(buildJsonObject {
                            put("type", "output_text"); put("text", "{\"data\":{}}")
                        }) })
                    }) })
                })
            }
            server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream").setBody("data: $response\n\n"))
            val config = DirectApiConfig("deepseek", server.url("/").toString(), "fixture", "deepseek-flash",
                protocol = DirectApiConfig.PROTOCOL_RESPONSES, maxOutputTokens = 12_000)
            assertEquals("{\"data\":{}}", contract.modelRequest.execute(DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList()), config, "opening_outline", "system", "user"))
            val request = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            assertEquals("none", request.getValue("reasoning").jsonObject.getValue("effort").jsonPrimitive.content)
            assertFalse("thinking" in request)
            assertEquals(12_000, request.getValue("max_output_tokens").jsonPrimitive.int)
        }
    }
}
