package com.siming.mobile.data.agent

import com.siming.mobile.data.network.*
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Test

class MobileCheckpointRecoveryTest {
    private fun request(): MobileCheckpointGenerationRequest {
        val messages = listOf(MobileTranscriptMessage("m1", 1, "t1", "user", "keep this", "completed", "2026-01-01T00:00:00Z"))
        return MobileCheckpointGenerationRequest("creation", "chat", 1,
            MobileConversationSourceRange(1, 1, 1, mobileConversationSourceHash(messages)), messages, emptyList(), emptyList(), buildJsonObject {})
    }
    private val valid = buildJsonObject {
        put("schema", "conversation_checkpoint_navigation.v1")
        put("semantic_navigation", MobileConversationCheckpoint.emptySemanticNavigation())
        put("author_quote_positions", JsonArray(emptyList()))
        put("prior_author_quote_states", JsonArray(emptyList()))
    }.toString()

    private fun generator(responses: List<Pair<String, String>>, calls: MutableList<JsonObject>, budget: MobileCheckpointCallBudget = MobileCheckpointCallBudget()): MobileDirectCheckpointGenerator {
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            val body = Buffer().also { chain.request().body!!.writeTo(it) }.readUtf8()
            calls += Json.parseToJsonElement(body).jsonObject
            val (reason, content) = responses[calls.lastIndex]
            val payload = buildJsonObject { put("choices", buildJsonArray { add(buildJsonObject {
                put("finish_reason", reason); put("message", buildJsonObject { put("role", "assistant"); put("content", content) })
            }) }) }
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(payload.toString().toResponseBody("application/json".toMediaType())).build()
        }.build()
        val config = DirectApiConfig("test", "https://example.com/v1", "key", "model",
            protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, contextWindowTokens = 100_000,
            maxOutputTokens = 1000, checkpointOutputTokens = 1000, modelMaxOutputTokens = 8000)
        return MobileDirectCheckpointGenerator(DirectApiClient(http), config, MobileUtf8ByteTokenCounter, 100_000, 1000, 512, budget)
    }

    @Test fun `length grows output and never publishes truncated draft`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        generator(listOf("length" to "partial", "stop" to valid), calls).generate(request())
        assertEquals(listOf(1000, 2000), calls.map { it["max_tokens"]!!.jsonPrimitive.int })
        assertTrue(calls.all { "tools" !in it })
    }

    @Test fun `length and invalid JSON share the total call budget`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        val budget = MobileCheckpointCallBudget(2)
        assertFailsWith<MobileConversationContextException> {
            generator(listOf("length" to "partial", "stop" to "invalid"), calls, budget).generate(request())
        }
        assertEquals(2, calls.size)
        assertEquals(0, budget.remaining)
    }

    @Test fun `two length results stop with a stage specific error`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        val error = assertFailsWith<MobileCheckpointOutputLimitException> {
            generator(listOf("length" to "", "length" to ""), calls).generate(request())
        }
        assertTrue(error.message!!.contains("历史整理输出超限"))
        assertEquals(2, calls.size)
    }

    @Test fun `invalid structure has one repair`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        generator(listOf("stop" to "invalid", "stop" to valid), calls).generate(request())
        assertEquals(2, calls.size)
    }
    @Test fun `invalid source reference is repaired before publishing`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        val invalid = JsonObject(Json.parseToJsonElement(valid).jsonObject.toMutableMap().apply {
            put("author_quote_positions", buildJsonArray { add(buildJsonObject {
                put("message_id", "foreign"); put("start_char", 0); put("end_char", 1); put("purpose", "constraint")
            }) })
        }).toString()
        generator(listOf("stop" to invalid, "stop" to valid), calls).generate(request())
        assertEquals(2, calls.size)
    }

    @Test fun `oversized source fails preflight without model calls`() = runBlocking {
        val calls = mutableListOf<JsonObject>()
        val base = request()
        val messages = base.sourceMessages.map { it.copy(content = "x".repeat(200_000)) }
        val oversized = base.copy(sourceMessages = messages,
            sourceRange = base.sourceRange.copy(sourceHash = mobileConversationSourceHash(messages)))
        assertFailsWith<MobileConversationContextException> {
            generator(emptyList(), calls).generate(oversized)
        }
        assertTrue(calls.isEmpty())
    }

}
