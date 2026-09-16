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

    @Test fun `real cataloging adapter applies PC request policy for both DeepSeek protocols`() = runBlocking {
        listOf(DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, DirectApiConfig.PROTOCOL_RESPONSES).forEach { protocol ->
            MockWebServer().use { server ->
                val event = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    """{"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","call_id":"call-1","name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"cataloging\"]}"}]}}"""
                } else {
                    """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"cataloging\"]}"}}]},"finish_reason":"tool_calls"}]}"""
                }
                server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                    .setBody("data: $event\n\ndata: [DONE]\n\n").setHeadersDelay(300, TimeUnit.MILLISECONDS))
                server.start()
                val client = DirectApiClient(OkHttpClient.Builder().proxy(Proxy.NO_PROXY).readTimeout(120, TimeUnit.MILLISECONDS)
                    .callTimeout(150, TimeUnit.MILLISECONDS).build(), allowCleartextForTests = true)
                val config = DirectApiConfig("DeepSeek", server.url("/").newBuilder().host("127.0.0.1").build().toString().trimEnd('/'), "fixture-key",
                    "deepseek-flash", protocol, maxOutputTokens = 5_000)
                val result = contract.modelRequest.execute(client, config,
                    listOf(buildJsonObject { put("role", "user"); put("content", "catalog saved chapter") }), contract.tools(emptyList())) {}
                assertEquals("set_tool_categories", result.toolCalls.single().name)
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
                assertEquals(listOf("set_tool_categories"), payload.objects("tools").map {
                    if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) it.text("name") else it.obj("function").text("name")
                })
                assertEquals(300, contract.modelRequest.idleTimeoutSeconds)
            }
        }
    }
}
