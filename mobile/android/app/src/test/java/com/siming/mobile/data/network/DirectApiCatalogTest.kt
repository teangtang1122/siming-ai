package com.siming.mobile.data.network

import kotlin.test.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Test

class DirectApiCatalogTest {
    private fun config(name: String) = DirectApiConfig(name, "https://$name.example/v1", "fake-key-$name", "same-model",
        protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS, availableModels = listOf("same-model", "writer"),
        contextWindowTokens = if (name == "a") 100000 else 200000)
    private fun catalog() = DirectApiCatalog().upsert("a", config("a")).upsert("b", config("b"))

    @Test fun `blank credentials can only reuse the edited profile at the same endpoint`() {
        assertEquals("fake-key-a", retainedDirectApiKey("https://a.example/v1/", "", config("a")))
        assertFailsWith<IllegalArgumentException> { retainedDirectApiKey("https://b.example/v1", "", config("a")) }
        assertFailsWith<IllegalArgumentException> { retainedDirectApiKey("https://a.example/v1", "", null) }
        assertEquals("new-key", retainedDirectApiKey("https://b.example/v1", "new-key", config("a")))
    }

    @Test fun `multiple APIs persist independently and task binding includes provider identity`() {
        val value = catalog().bind(DirectApiConfig.TASK_WRITING, "b", "same-model")
        val reopened = Json.decodeFromString<DirectApiCatalog>(Json.encodeToString(value))
        assertEquals("fake-key-a", reopened.resolve(DirectApiConfig.TASK_PLANNING)!!.apiKey)
        assertEquals("fake-key-b", reopened.resolve(DirectApiConfig.TASK_WRITING)!!.apiKey)
        assertEquals(200000, reopened.resolve(DirectApiConfig.TASK_WRITING)!!.contextWindowTokens)
        assertEquals("b", reopened.taskProfileIds[DirectApiConfig.TASK_WRITING])
        assertEquals(2, reopened.profiles.size)
    }

    @Test fun `changing the default does not change explicit task bindings or captured runs`() {
        val captured = catalog().bind(DirectApiConfig.TASK_WRITING, "a", "writer")
        val changed = captured.selectDefault("b")
        assertEquals("fake-key-b", changed.resolve(DirectApiConfig.TASK_PLANNING)!!.apiKey)
        assertEquals("writer", changed.resolve(DirectApiConfig.TASK_WRITING)!!.model)
        assertEquals("fake-key-a", captured.resolve(DirectApiConfig.TASK_PLANNING)!!.apiKey)
        assertEquals("same-model", changed.upsert("a", config("a").copy(model = "writer"))
            .bind(DirectApiConfig.TASK_ASSISTANT, "b", "same-model")
            .upsert("b", config("b").copy(model = "writer")).resolve(DirectApiConfig.TASK_ASSISTANT)!!.model)
    }

    @Test fun `deletion requires explicit reassignment and removes all stale references`() {
        val bound = catalog().bind(DirectApiConfig.TASK_WRITING, "b", "writer")
        assertFailsWith<IllegalArgumentException> { bound.remove("b") }
        assertFailsWith<IllegalArgumentException> { bound.remove("a") }
        val deleted = bound.bind(DirectApiConfig.TASK_WRITING, null).remove("b")
        assertEquals(listOf("a"), deleted.profiles.map { it.id })
        assertEquals("same-model", deleted.resolve(DirectApiConfig.TASK_WRITING)!!.model)
        assertTrue(deleted.remove("a").profiles.isEmpty())
        assertFailsWith<IllegalStateException> { deleted.selectDefault("missing") }
        assertFailsWith<IllegalArgumentException> { deleted.bind("writing", "a", "unknown-model") }
    }

    @Test fun `legacy config migration preserves all credentials budgets and task models`() {
        val legacy = config("a").copy(taskModels = mapOf("writing" to "writer"), checkpointOutputTokens = 9000)
        val migrated = LegacyDirectApiMigration.convert(Json.encodeToString(legacy), Json)
        assertEquals(legacy, migrated.defaultConfig())
        assertEquals("writer", migrated.resolve("writing")!!.model)
        val restarted = Json.decodeFromString<DirectApiCatalog>(Json.encodeToString(migrated))
        assertEquals(migrated, restarted)
        assertEquals("fake-key-a", restarted.resolve("writing")!!.apiKey)
    }

    @Test fun `real text request uses only the selected API credential endpoint and model`() = runBlocking {
        val sent = mutableListOf<Triple<String, String?, String>>()
        val client = DirectApiClient(OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            val body = Json.parseToJsonElement(Buffer().also { request.body!!.writeTo(it) }.readUtf8()).jsonObject
            sent += Triple(request.url.host, request.header("Authorization"), body["model"]!!.jsonPrimitive.content)
            Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body("""{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}""".toResponseBody("application/json".toMediaType())).build()
        }.build())
        val value = catalog().bind("writing", "b", "writer")
        for (task in listOf("planning", "writing")) client.complete(value.resolve(task)!!, "test", "test", 100)
        assertEquals(listOf<Triple<String, String?, String>>(Triple("a.example", "Bearer fake-key-a", "same-model"),
            Triple("b.example", "Bearer fake-key-b", "writer")), sent)
    }
}
