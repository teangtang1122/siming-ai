package com.siming.mobile.data.cataloging

import com.siming.mobile.data.network.*
import java.net.Proxy
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class CatalogingToolArgumentRecoveryTest {
    private val broken = "{\"candidates\":[{\"type\":\"chapter_summary\"}},\"finalize\":false}"
    private val calls = listOf("good-read" to "{}", "bad-save" to broken).map { (id, args) -> buildJsonObject {
        put("id", id); put("type", "function")
        put("function", buildJsonObject { put("name", if (id == "good-read") "read_cataloging_archive" else "save_external_cataloging_candidates"); put("arguments", args) })
    } }
    private val schemas = buildJsonArray { add(buildJsonObject { put("function", buildJsonObject { put("name", "save_external_cataloging_candidates") }) }) }

    @Test fun `completed malformed calls retain complete native batch for bounded correction`() = runBlocking {
        for (protocol in listOf(DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, DirectApiConfig.PROTOCOL_RESPONSES)) {
            for (stream in listOf(false, true)) MockWebServer().use { server ->
                val nativeMessage = buildJsonObject { put("role", "assistant"); put("content", "unaccepted"); put("tool_calls", JsonArray(calls)) }
                val response = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) buildJsonObject {
                    put("status", "completed")
                    put("output", JsonArray(calls.map { c -> buildJsonObject {
                        put("type", "function_call"); put("call_id", c.getValue("id"))
                        put("name", c.obj("function").getValue("name")); put("arguments", c.obj("function").getValue("arguments"))
                    } }))
                } else buildJsonObject { put("choices", buildJsonArray { add(buildJsonObject {
                    put("message", nativeMessage); put("finish_reason", "tool_calls")
                }) }) }
                val event = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) buildJsonObject {
                    put("type", "response.completed"); put("response", response)
                } else buildJsonObject { put("choices", buildJsonArray { add(buildJsonObject {
                    put("delta", buildJsonObject {
                        put("content", "unaccepted"); put("tool_calls", JsonArray(calls.mapIndexed { i, c -> JsonObject(c + ("index" to JsonPrimitive(i))) }))
                    }); put("finish_reason", "tool_calls")
                }) }) }
                server.enqueue(MockResponse().setHeader("Content-Type", if (stream) "text/event-stream" else "application/json")
                    .setBody(if (stream) "data: $event\n\ndata: [DONE]\n\n" else response.toString()))
                server.start()
                val client = DirectApiClient(OkHttpClient.Builder().proxy(Proxy.NO_PROXY).build(), allowCleartextForTests = true, retryDelaysMillis = listOf(0))
                val config = DirectApiConfig("fixture", server.url("/").toString(), "fixture-key", "fixture-model", protocol)
                val delivered = mutableListOf<String>()
                val error = assertFailsWith<DirectToolArgumentsException> {
                    if (stream) client.streamAgentTurn(config, emptyList(), schemas, onContentDelta = { delivered += it })
                    else client.agentTurn(config, emptyList(), schemas)
                }
                assertTrue(delivered.isEmpty())
                assertEquals(1, server.requestCount)
                assertEquals(listOf("good-read", "bad-save"), error.assistantMessage.objects("tool_calls").map { it.text("id") })
                assertEquals(broken, error.assistantMessage.objects("tool_calls").last().obj("function").text("arguments"))
                for (attempt in 1..3) {
                    val tx = catalogingToolArgumentFailure(error, schemas, attempt, 3)
                    assertEquals(error.assistantMessage, tx.first())
                    assertEquals(listOf("good-read", "bad-save"), tx.drop(1).map { it.text("tool_call_id") })
                    tx.drop(1).forEach { receipt ->
                        val data = Json.parseToJsonElement(receipt.text("content")).jsonObject.obj("data")
                        assertEquals(JsonPrimitive(false), data["executed"])
                        assertEquals(JsonPrimitive(attempt < 3), data["retryable"])
                    }
                }
            }
        }
    }

    @Test fun `missing or duplicate native identities cannot enter receipt recovery`() {
        for (bad in listOf("", "good-read")) {
            val changed = JsonObject(calls.last() + ("id" to JsonPrimitive(bad)))
            val assistant = buildJsonObject { put("tool_calls", JsonArray(listOf(calls.first(), changed))) }
            val error = assertFailsWith<DirectNativeToolProtocolException> { validateCompletedNativeArguments(assistant) }
            assertFalse(error is DirectToolArgumentsException)
        }
    }
}
