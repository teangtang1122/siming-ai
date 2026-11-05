package com.siming.mobile.data.network

import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Test

class ModelRequestPolicyTest {
    private val cases = javaClass.getResourceAsStream("/model_request_policy_cases.json")!!
        .bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonArray }
        .map { it.jsonObject }
    private val controlFields = setOf("thinking", "reasoning", "reasoning_effort", "enable_thinking", "thinking_budget")
    private val tools = Json.parseToJsonElement("""[{"type":"function","function":{"name":"read","parameters":{"type":"object"}}}]""").jsonArray
    private fun config(case: JsonObject) = DirectApiConfig(
        case.getValue("label").jsonPrimitive.content, case.getValue("url").jsonPrimitive.content,
        "fixture-key", case.getValue("model").jsonPrimitive.content, case.getValue("protocol").jsonPrimitive.content,
    )

    private fun assertBody(case: JsonObject, payload: JsonObject) {
        assertEquals(case.getValue("expected"), JsonObject(payload.filterKeys { it in controlFields }), case["id"].toString())
        assertFalse(payload.keys.any { it.startsWith("moshu_") || it.startsWith("local_cli_") })
        assertFalse("storage_target" in payload)
        assertFalse("operation_id" in payload)
    }

    private fun client(case: JsonObject, onPayload: (JsonObject) -> Unit): DirectApiClient {
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            val buffer = Buffer()
            request.body!!.writeTo(buffer)
            onPayload(Json.parseToJsonElement(buffer.readUtf8()).jsonObject)
            val event = if (case["text_delta"] == JsonPrimitive(true) && case.getValue("protocol").jsonPrimitive.content == "responses") {
                """{"type":"response.completed","response":{"status":"completed","output":[]}}"""
            } else if (case.getValue("protocol").jsonPrimitive.content == DirectApiConfig.PROTOCOL_RESPONSES) {
                """{"type":"response.completed","response":{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}]}}"""
            } else {
                """{"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}"""
            }
            Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(((if (case["text_delta"] == JsonPrimitive(true) && case.getValue("protocol").jsonPrimitive.content == "responses") "data: {\"type\":\"response.output_text.delta\",\"delta\":\"ok\"}\n\n" else "") + "data: $event\n\ndata: [DONE]\n\n").toResponseBody("text/event-stream".toMediaType())).build()
        }.build()
        return DirectApiClient(http, retryDelaysMillis = emptyList())
    }

    @Test fun `standalone agent wire requests match all PC policy cases`() = runBlocking {
        cases.forEach { case ->
            var calls = 0
            val api = client(case) { payload ->
                calls++
                assertBody(case, payload)
                assertEquals(case["tool_choice"], payload["tool_choice"] ?: JsonNull, case["id"].toString())
            }
            val result = api.streamAgentTurn(config(case),
                listOf(buildJsonObject { put("role", "user"); put("content", "fixture") }),
                if (case.getValue("text_only").jsonPrimitive.boolean) JsonArray(emptyList()) else tools,
                toolChoice = "required", extraBody = case.getValue("extra").jsonObject)
            assertEquals("ok", result.content)
            assertEquals(1, calls)
        }
    }

    @Test fun `standalone text serializers use the same protocol policy`() = runBlocking {
        cases.filter { it.getValue("text_only").jsonPrimitive.boolean }.forEach { case ->
            val api = client(case) { assertBody(case, it) }
            val config = config(case)
            val extra = case.getValue("extra").jsonObject
            assertBody(case, api.completeRequestPayload(config, config.protocol, "system", "fixture", 500, 0.2, extra))
            assertEquals("ok", api.completeResumable(config, "system", "fixture", extraBody = extra))
        }
    }
    @Test fun `official OpenAI sends total completion budget including reasoning`() {
        val config = DirectApiConfig("OpenAI", "https://api.openai.com/v1", "key", "model")
        val payload = DirectApiClient().completeRequestPayload(config, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
            "system", "history", 12000, 0.0, null)
        assertEquals(12000, payload["max_completion_tokens"]!!.jsonPrimitive.int)
        assertFalse("max_tokens" in payload)
        val responses = DirectApiClient().completeRequestPayload(config, DirectApiConfig.PROTOCOL_RESPONSES,
            "system", "history", 12000, 0.0, null)
        assertEquals(12000, responses["max_output_tokens"]!!.jsonPrimitive.int)
    }

    @Test fun `final parameter policy matches PC and preserves explicit reasoning`() {
        val rows = javaClass.classLoader!!.getResourceAsStream("model_parameter_policy_cases.json")!!
            .bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonArray }
        for (row in rows) {
            val case = row.jsonObject
            val config = DirectApiConfig("any-label", case.getValue("url").jsonPrimitive.content,
                "fixture", case.getValue("model").jsonPrimitive.content,
                protocol = case.getValue("protocol").jsonPrimitive.content)
            val payload = case.getValue("payload").jsonObject
            if ("error" in case) {
                val failure = assertFailsWith<IllegalArgumentException> { ModelRequestPolicy.normalize(config, config.protocol, payload) }
                assertTrue(failure.message.orEmpty().contains(case.getValue("error").jsonPrimitive.content), case.toString())
            } else assertEquals(case.getValue("expected"), ModelRequestPolicy.normalize(config, config.protocol, payload), case.toString())
        }
    }

    @Test fun `all text and agent serializers omit incompatible sampling including extra overrides`() = runBlocking {
        for (protocol in listOf(DirectApiConfig.PROTOCOL_RESPONSES, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)) {
            val config = DirectApiConfig("OpenAI", "https://api.openai.com/v1", "fixture", "gpt-6-luna", protocol = protocol)
            val template = buildJsonObject { put("protocol", protocol); put("text_delta", true) }
            var calls = 0
            val api = client(template) { payload ->
                assertFalse("temperature" in payload); assertFalse("top_p" in payload); calls++
            }
            val extra = buildJsonObject { put("temperature", 0.8); put("top_p", 0.9) }
            val messages = listOf(buildJsonObject { put("role", "user"); put("content", "test") })
            assertFalse("temperature" in api.completeRequestPayload(config, protocol, "s", "u", 1000, 0.8, extra))
            val wire = api.agentRequestPayload(config, messages,
                if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) tools else JsonArray(emptyList()), null, 1000, 0.8, extra)
            assertFalse("temperature" in wire)
            assertEquals("ok", api.completeResumable(config, "s", "u", extraBody = extra))
            assertEquals("ok", api.streamAgentTurn(config, messages, JsonArray(emptyList()), extraBody = extra).content)
            assertEquals(2, calls)
        }
    }

    @Test fun `connection probe length recovery is bounded and never changes reasoning or protocol`() = runBlocking {
        val budgets = mutableListOf<Int>()
        val api = DirectApiClient(OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            val payload = Json.parseToJsonElement(Buffer().also { request.body!!.writeTo(it) }.readUtf8()).jsonObject
            assertEquals("/v1/responses", request.url.encodedPath)
            assertFalse("temperature" in payload); assertFalse("reasoning" in payload)
            budgets += payload.getValue("max_output_tokens").jsonPrimitive.int
            Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body("""{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"output":[]}""".toResponseBody("application/json".toMediaType())).build()
        }.build(), retryDelaysMillis = emptyList())
        val config = DirectApiConfig("OpenAI", "https://api.openai.com/v1", "fixture", "gpt-6-luna",
            maxOutputTokens = 3000)
        val error = assertFailsWith<IllegalStateException> { api.testAndResolve(config) }
        assertTrue(error.message.orEmpty().contains("连接测试输出超限"))
        assertEquals(listOf(1024, 3000), budgets)
    }

    @Test fun `parameter and model errors do not trigger protocol or path fallback`() = runBlocking {
        for ((status, body) in listOf(400 to """{"error":{"param":"temperature","code":"unsupported_value","message":"bad parameter"}}""",
            404 to """{"error":{"param":null,"code":"model_not_found","message":"model missing"}}""")) {
            var calls = 0
            val api = DirectApiClient(OkHttpClient.Builder().addInterceptor { chain ->
                calls++
                assertEquals("/v1/responses", chain.request().url.encodedPath)
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("Error")
                    .body(body.toResponseBody("application/json".toMediaType())).build()
            }.build(), retryDelaysMillis = emptyList())
            assertFailsWith<DirectApiHttpException> { api.testAndResolve(DirectApiConfig(
                "OpenAI", "https://api.openai.com/v1", "fixture", "gpt-6-luna")) }
            assertEquals(1, calls)
        }
    }

    @Test fun `incompatible chat tools fail before transport while Responses accepts the same reasoning`() = runBlocking {
        var calls = 0
        val api = client(buildJsonObject { put("protocol", "chat_completions") }) { calls++ }
        val config = DirectApiConfig("OpenAI", "https://api.openai.com/v1", "fixture", "gpt-6-luna",
            protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
        val messages = listOf(buildJsonObject { put("role", "user"); put("content", "test") })
        val error = assertFailsWith<IllegalArgumentException> { api.streamAgentTurn(config, messages, tools) }
        assertTrue(error.message.orEmpty().contains("Responses API"))
        assertEquals(0, calls)
        val extra = buildJsonObject { put("reasoning", buildJsonObject { put("effort", "high") }) }
        val payload = api.agentRequestPayload(config.copy(protocol = DirectApiConfig.PROTOCOL_RESPONSES),
            messages, tools, null, 1200, 0.3, extra)
        assertEquals(extra.getValue("reasoning"), payload.getValue("reasoning"))
        assertFalse("temperature" in payload)
    }

    @Test fun `user parameters persist per model and omission overrides business defaults`() {
        val parameters = mapOf("gpt-6-luna" to buildJsonObject { put("temperature", JsonNull) },
            "other" to buildJsonObject { put("temperature", 0.8) })
        val config = DirectApiConfig("OpenAI", "https://api.openai.com/v1", "fixture", "gpt-6-luna",
            modelRequestParameters = parameters)
        val reopened = Json.decodeFromString(DirectApiConfig.serializer(), Json.encodeToString(DirectApiConfig.serializer(), config))
        assertEquals(parameters, reopened.modelRequestParameters)
        val api = DirectApiClient()
        val payload = api.completeRequestPayload(reopened, "responses", "s", "u", 1200, 0.3, null)
        assertFalse("temperature" in payload)
        val other = api.completeRequestPayload(reopened.copy(model = "other"), "responses", "s", "u", 1200, 0.3, null)
        assertEquals(0.8, other.getValue("temperature").jsonPrimitive.double)
        val invalid = config.copy(modelRequestParameters = mapOf("gpt-6-luna" to buildJsonObject { put("temperature", 0.8) }))
        assertFailsWith<IllegalArgumentException> { api.completeRequestPayload(invalid, "responses", "s", "u", 1200, 0.3, null) }
        assertFailsWith<IllegalArgumentException> { config.copy(modelRequestParameters = mapOf("gpt-6-luna" to buildJsonObject { put("max_output_tokens", 10) })) }
    }

}
