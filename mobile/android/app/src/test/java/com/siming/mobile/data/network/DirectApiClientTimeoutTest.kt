package com.siming.mobile.data.network

import com.siming.mobile.data.toUserFacingMessage
import java.net.Proxy
import java.util.concurrent.TimeUnit
import kotlin.test.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class DirectApiClientTimeoutTest {
    private fun client() = DirectApiClient(
        client = OkHttpClient.Builder().proxy(Proxy.NO_PROXY).readTimeout(120, TimeUnit.MILLISECONDS)
            .callTimeout(150, TimeUnit.MILLISECONDS).build(),
        allowCleartextForTests = true,
        retryDelaysMillis = emptyList(),
    )
    private fun config(server: MockWebServer, protocol: String) = DirectApiConfig(
        "fixture", server.url("/").newBuilder().host("127.0.0.1").build().toString().trimEnd('/'), "fixture-key", "fixture-model", protocol,
    )
    private val messages = listOf(buildJsonObject { put("role", "user"); put("content", "read") })
    private val tools = Json.parseToJsonElement("""[{"type":"function","function":{"name":"read_archive","parameters":{"type":"object","properties":{}}}}]""").jsonArray
    private val chatCall = """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"read_archive","arguments":"{\"id\":\"chapter-1\"}"}}]},"finish_reason":"tool_calls"}]}"""
    private fun sse(vararg events: String) = events.joinToString("") { "data: $it\n\n" }
    private fun response(body: String) = MockResponse().setHeader("Content-Type", "text/event-stream").setBody(body)
    // MockWebServer also throttles request reads. Make the first response chunk larger
    // than the request so these tests stall after the first SSE event, not before headers.
    private fun firstChunk(event: String) = ": ${" ".repeat(4_096)}\n\n" + sse(event)
    private fun frame(body: String): String = ": ${" ".repeat(8_192 - 4 - body.toByteArray().size)}\n\n" + body

    @Test fun `cataloging budget lets a slow chat response outlive ordinary timeouts`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response(sse(chatCall, "[DONE]")).setHeadersDelay(300, TimeUnit.MILLISECONDS))
            server.start()
            val activities = mutableListOf<DirectAgentStreamActivity>()
            val turn = client().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS), messages, tools,
                streamIdleTimeoutMillis = 2_000, onActivity = { activities += it },
            )
            assertEquals("chapter-1", turn.toolCalls.single().arguments["id"]?.jsonPrimitive?.content)
            assertEquals(listOf(DirectAgentStreamActivity.TOOL_ARGUMENTS), activities)
            assertEquals(JsonPrimitive(true), Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject["stream"])
        }
    }

    @Test fun `active responses stream outlives its idle window and ordinary call timeout`() = runBlocking {
        MockWebServer().use { server ->
            val events = listOf(
                """{"type":"response.reasoning_summary_text.delta","delta":"checking"}""",
                """{"type":"response.output_text.delta","delta":"checked"}""",
                """{"type":"response.output_item.added","item":{"type":"function_call","id":"item-1","call_id":"call-1","name":"read_archive","arguments":""}}""",
                """{"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"{"}""",
                """{"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"}"}""",
                """{"type":"response.completed","response":{"status":"completed","output":[]}}""",
            )
            server.enqueue(response(events.joinToString("") { frame(sse(it)) }).throttleBody(8_192, 150, TimeUnit.MILLISECONDS))
            server.start()
            val activities = mutableListOf<DirectAgentStreamActivity>()
            val started = System.nanoTime()
            val turn = client().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES), messages, tools,
                streamIdleTimeoutMillis = 500, onActivity = { activities += it },
            )
            assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) > 500)
            assertEquals("read_archive", turn.toolCalls.single().name)
            assertEquals(JsonObject(emptyMap()), turn.toolCalls.single().arguments)
            assertTrue(DirectAgentStreamActivity.REASONING in activities)
            assertTrue(DirectAgentStreamActivity.CONTENT in activities)
            assertTrue(DirectAgentStreamActivity.TOOL_ARGUMENTS in activities)
        }
    }

    @Test fun `shared PC stream cases have the same completion and idle timeout boundary`() = runBlocking {
        val fixture = javaClass.getResourceAsStream("/agent-stream-idle-v1.json")!!.bufferedReader().use {
            Json.parseToJsonElement(it.readText()).jsonObject
        }
        val idleMillis = fixture.getValue("idle_timeout_ms").jsonPrimitive.long
        fixture.getValue("cases").jsonArray.forEach { rawCase ->
            val case = rawCase.jsonObject
            MockWebServer().use { server ->
                val events = case.getValue("events").jsonArray
                val body = events.mapIndexed { index, event ->
                    val data = if (event == JsonNull) ": keep-alive\n\n" else sse(event.toString())
                    frame(data + if (index == events.lastIndex) sse("[DONE]") else "")
                }.joinToString("")
                server.enqueue(response(body)
                    .setHeadersDelay(case.getValue("headers_delay_ms").jsonPrimitive.long, TimeUnit.MILLISECONDS)
                    .throttleBody(8_192, case.getValue("interval_ms").jsonPrimitive.long, TimeUnit.MILLISECONDS))
                server.start()
                val activities = mutableListOf<DirectAgentStreamActivity>()
                val started = System.nanoTime()
                val result = runCatching {
                    client().streamAgentTurn(config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS), messages, tools,
                        streamIdleTimeoutMillis = idleMillis, onActivity = { activities += it })
                }
                if (case.getValue("expected").jsonPrimitive.content == "completed") {
                    assertEquals("chapter-1", result.getOrThrow().toolCalls.single().arguments["id"]?.jsonPrimitive?.content)
                    assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) > idleMillis)
                } else {
                    val error = assertIs<DirectApiTimeoutException>(result.exceptionOrNull(), case.toString())
                    assertEquals(DirectApiTimeoutException.Phase.STREAM_IDLE, error.phase)
                    assertTrue(error.toUserFacingMessage().contains("未收到模型有效输出"))
                    assertFalse(error.toUserFacingMessage().contains("检查网络"))
                    if (case.getValue("headers_delay_ms").jsonPrimitive.long == 0L) {
                        assertEquals(listOf(DirectAgentStreamActivity.TOOL_ARGUMENTS), activities)
                    }
                }
                assertEquals(1, server.requestCount)
            }
        }
    }

    @Test fun `local event processing is excluded from provider idle time`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response(sse(chatCall, "[DONE]")))
            server.start()
            val turn = client().streamAgentTurn(config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS), messages, tools,
                streamIdleTimeoutMillis = 300, onActivity = { delay(500) })
            assertEquals("read_archive", turn.toolCalls.single().name)
        }
    }

    @Test fun `responses terminal event ends the wait while the HTTP connection remains open`() = runBlocking {
        MockWebServer().use { server ->
            val terminal = firstChunk("""{"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","call_id":"call-1","name":"read_archive","arguments":"{}"}]}}""")
            server.enqueue(response(terminal + ": keep-alive\n\n")
                .throttleBody(terminal.toByteArray().size.toLong(), 2, TimeUnit.SECONDS))
            server.start()
            val turn = client().streamAgentTurn(config(server, DirectApiConfig.PROTOCOL_RESPONSES), messages, tools,
                streamIdleTimeoutMillis = 400)
            assertEquals("read_archive", turn.toolCalls.single().name)
        }
    }

    @Test fun `ordinary requests retain their existing timeout when no idle policy is supplied`() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response(sse(chatCall, "[DONE]")).setHeadersDelay(500, TimeUnit.MILLISECONDS))
            server.start()
            val error = assertFailsWith<DirectApiTimeoutException> {
                client().streamAgentTurn(config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS), messages, tools)
            }
            assertNotEquals(DirectApiTimeoutException.Phase.STREAM_IDLE, error.phase)
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun `author cancellation interrupts socket reading before model timeout`() = runBlocking {
        MockWebServer().use { server ->
            val first = firstChunk("""{"choices":[{"delta":{"reasoning_content":"checking"},"finish_reason":null}]}""")
            server.enqueue(response(first + sse(chatCall, "[DONE]")).throttleBody(first.toByteArray().size.toLong(), 2, TimeUnit.SECONDS))
            server.start()
            val received = CompletableDeferred<Unit>()
            val work = launch {
                client().streamAgentTurn(config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS), messages, tools,
                    streamIdleTimeoutMillis = 5_000, onActivity = { received.complete(Unit) })
                fail("A cancelled response must not return a tool call")
            }
            withTimeout(1_000) { received.await() }
            delay(50) // Let the reader block waiting for the next provider chunk.
            withTimeout(1_000) { work.cancelAndJoin() }
            assertTrue(work.isCancelled)
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun `length limited and incomplete responses are model errors not network failures`() = runBlocking {
        listOf(
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS to "length",
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS to "failed",
            DirectApiConfig.PROTOCOL_RESPONSES to "incomplete",
        ).forEach { (protocol, finishReason) ->
            MockWebServer().use { server ->
                val body = if (protocol == DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS) {
                    sse(chatCall.replace("\"finish_reason\":\"tool_calls\"", "\"finish_reason\":\"$finishReason\""), "[DONE]")
                } else {
                    sse("""{"type":"response.incomplete","response":{"status":"incomplete"}}""")
                }
                server.enqueue(response(body))
                server.start()
                val error = assertFailsWith<DirectNativeToolProtocolException> {
                    client().streamAgentTurn(config(server, protocol), messages, tools, streamIdleTimeoutMillis = 2_000)
                }
                assertTrue(error.toUserFacingMessage().contains("未执行本次工具调用"))
                assertFalse(error.toUserFacingMessage().contains("网络"))
                assertEquals(finishReason == "length", error.toUserFacingMessage().contains("长度上限"))
                assertEquals(1, server.requestCount)
            }
        }
    }
}
