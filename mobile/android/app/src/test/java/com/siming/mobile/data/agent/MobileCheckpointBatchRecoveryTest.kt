package com.siming.mobile.data.agent

import com.siming.mobile.data.network.*
import java.nio.file.Files
import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Test

class MobileCheckpointBatchRecoveryTest {
    @Test fun `length recovery shrinks whole turns and fails before any business request`() = runBlocking {
        val directory = Files.createTempDirectory("checkpoint-batches").toFile()
        try {
            val calls = mutableListOf<JsonObject>()
            val http = OkHttpClient.Builder().addInterceptor { chain ->
                calls += Json.parseToJsonElement(Buffer().also { chain.request().body!!.writeTo(it) }.readUtf8()).jsonObject
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                    .body("""{"choices":[{"finish_reason":"length","message":{"role":"assistant","content":"partial"}}]}""".toResponseBody("application/json".toMediaType())).build()
            }.build()
            val store = MobileAssistantConversationStore(directory)
            val archived = (1..100).map { index -> MobileArchivedConversationTurn("t$index", "u".repeat(200), "a".repeat(200), "completed", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z") }
            val original = store.ensureConversationArchive("book", "chat", "creation", "book", "chat", archived)
            val turn = store.beginTurn("book", "chat", "continue", "creation", "book")
            val before = assertNotNull(store.snapshot("book", "chat"))
            val config = DirectApiConfig("test", "https://example.com/v1", "key", "model",
                protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, contextWindowTokens = 16000,
                maxOutputTokens = 1000, checkpointOutputTokens = 1000, modelMaxOutputTokens = 1000, safetyMarginTokens = 512)
            val error = assertFailsWith<MobileConversationContextException> {
                MobileDirectConversationContextRuntime(DirectApiClient(http), store).prepare(
                    "book", "continue", config, before, turn, "system", JsonArray(emptyList()),
                    "planning", 1000, currentTurnLedger = emptyList(), pendingTransactions = emptyList())
            }
            assertEquals(MobileConversationContextErrorCode.CHECKPOINT_OUTPUT_LIMIT, error.code)
            assertTrue(calls.size in 2..6)
            val sizes = calls.map { payload ->
                assertEquals(false, payload["stream"]!!.jsonPrimitive.boolean)
                val prompt = payload["messages"]!!.jsonArray.last().jsonObject["content"]!!.jsonPrimitive.content
                Json.parseToJsonElement(prompt).jsonObject["new_source_messages"]!!.jsonArray.size
            }
            assertTrue(sizes.zipWithNext().all { (a, b) -> a > b })
            assertTrue(sizes.all { it % 2 == 0 })
            val after = assertNotNull(store.snapshot("book", "chat"))
            assertEquals(before.messages, after.messages)
            assertEquals(original.messages, after.messages.take(original.messages.size))
            assertNull(after.activeCheckpoint)
            assertTrue(after.checkpoints.all { it.status == MobileConversationCheckpointStatus.FAILED })
        } finally { directory.deleteRecursively() }
    }
    @Test fun `single complete turn can grow beyond twice the initial output within the shared limit`() = runBlocking {
        val directory = Files.createTempDirectory("checkpoint-output-growth").toFile()
        try {
            val outputs = mutableListOf<Int>()
            val valid = buildJsonObject {
                put("schema", "conversation_checkpoint_navigation.v1")
                put("semantic_navigation", MobileConversationCheckpoint.emptySemanticNavigation())
                put("author_quote_positions", JsonArray(emptyList())); put("prior_author_quote_states", JsonArray(emptyList()))
            }.toString()
            val http = OkHttpClient.Builder().addInterceptor { chain ->
                val payload = Json.parseToJsonElement(Buffer().also { chain.request().body!!.writeTo(it) }.readUtf8()).jsonObject
                outputs += payload["max_tokens"]!!.jsonPrimitive.int
                val response = buildJsonObject { put("choices", buildJsonArray { add(buildJsonObject {
                    put("finish_reason", if (outputs.size < 4) "length" else "stop")
                    put("message", buildJsonObject { put("role", "assistant"); put("content", if (outputs.size < 4) "partial" else valid) })
                }) }) }
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                    .body(response.toString().toResponseBody("application/json".toMediaType())).build()
            }.build()
            val store = MobileAssistantConversationStore(directory)
            store.ensureConversationArchive("book", "chat", "creation", "book", "chat",
                listOf(MobileArchivedConversationTurn("t1", "u".repeat(20000), "a".repeat(20000), "completed", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")))
            val turn = store.beginTurn("book", "chat", "continue", "creation", "book")
            val config = DirectApiConfig("test", "https://example.com/v1", "key", "model",
                protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, contextWindowTokens = 100000,
                maxOutputTokens = 1000, checkpointOutputTokens = 1000, modelMaxOutputTokens = 16000, safetyMarginTokens = 512)
            val prepared = MobileDirectConversationContextRuntime(DirectApiClient(http), store).prepare(
                "book", "continue", config, assertNotNull(store.snapshot("book", "chat")), turn,
                "s".repeat(60000), JsonArray(emptyList()), "planning", 1000,
                currentTurnLedger = emptyList(), pendingTransactions = emptyList())
            assertEquals(listOf(1000, 2000, 4000, 8000), outputs)
            assertNotNull(prepared.conversation.activeCheckpoint)
            assertEquals(3, prepared.conversation.messages.size)
        } finally { directory.deleteRecursively() }
    }

}
