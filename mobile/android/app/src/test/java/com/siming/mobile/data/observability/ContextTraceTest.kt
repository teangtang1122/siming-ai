package com.siming.mobile.data.observability

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.GZIPOutputStream
import kotlin.test.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer

class ContextTraceTest {
    private class MemorySink(var level: String = "full") : ContextTraceSink {
        val events = java.util.Collections.synchronizedList(mutableListOf<JsonObject>())
        val contents = java.util.Collections.synchronizedMap(mutableMapOf<String, String>())
        override fun mode() = level
        override fun submit(event: JsonObject, content: String?): Boolean {
            events += event
            if (content != null) contents[event.text("event_id")] = content
            return true
        }
    }
    @AfterTest fun reset() { MobileTrace.sink = null }

    @Test fun `explicit failed and conflict receipts finish as failed steps`() = runBlocking {
        val sink = MemorySink("summary"); MobileTrace.sink = sink
        MobileTrace.turn("creation_session", "session", buildJsonObject {}) {
            listOf("error", "denied", "blocked", "failed", "conflict").forEach { status ->
                val receipt = MobileTrace.span("tool", "refine_creation_artifact") { buildJsonObject { put("status", status) } }
                assertEquals(status, receipt.text("status"))
            }
        }
        val toolIds = sink.events.filter { it.text("event_type") == "span_started" && it.getValue("data").jsonObject.text("kind") == "tool" }
            .map { it.getValue("data").jsonObject.text("span_id") }.toSet()
        val finished = sink.events.filter { it.text("event_type") == "span_finished" && it.getValue("data").jsonObject.text("span_id") in toolIds }
        assertEquals(5, finished.size)
        assertEquals(List(5) { "error" }, finished.map { it.getValue("data").jsonObject.text("status") })
        assertEquals("completed", sink.events.last().getValue("data").jsonObject.text("status"))
    }

    @Test fun `terminal trace never presents an unfinished span as running or zero milliseconds`() {
        val started = buildJsonObject {
            put("event_type", "span_started")
            put("data", buildJsonObject { put("span_id", "tool"); put("status", "running") })
        }
        val interrupted = buildJsonObject { put("finished", 2); put("capture_status", "interrupted") }
        assertEquals("interrupted", traceSpans(listOf(started), interrupted, true).getValue("tool").text("status"))
        assertEquals("pending_record", traceSpans(listOf(started), interrupted, false).getValue("tool").text("status"))
        assertEquals("incomplete", traceSpans(listOf(started), buildJsonObject { put("finished", 2) }, true).getValue("tool").text("status"))
        assertEquals("running", traceSpans(listOf(started), buildJsonObject { put("finished", JsonNull) }, true).getValue("tool").text("status"))
        val finished = buildJsonObject {
            put("event_type", "span_finished")
            put("data", buildJsonObject { put("span_id", "tool"); put("status", "error"); put("duration_ms", 0.2) })
        }
        val span = traceSpans(listOf(started, finished), interrupted, true).getValue("tool")
        assertEquals("error", span.text("status")); assertEquals("<1 ms", traceDuration(span))
        assertEquals("耗时未知", traceDuration(buildJsonObject {}))
    }

    @Test fun `returned turn failure is reflected in the trace status`() = runBlocking {
        val sink = MemorySink("summary"); MobileTrace.sink = sink
        val result = MobileTrace.turn("creation_session", "session", buildJsonObject {}) {
            buildJsonObject { put("status", "failed") }
        }
        assertEquals("failed", result.text("status"))
        assertEquals("error", sink.events.last().getValue("data").jsonObject.text("status"))
    }

    @Test fun `actual streamed request and response are captured without changing content or endpoint`() = runBlocking {
        val sink = MemorySink(); MobileTrace.sink = sink
        val stream = "data: {\"choices\":[{\"delta\":{\"content\":\"hello\",\"reasoning_content\":\"visible\"},\"finish_reason\":null}]}\n\n" +
            "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":7}}\n\ndata: [DONE]\n\n"
        val compressed = ByteArrayOutputStream().also { target -> GZIPOutputStream(target).use { it.write(stream.toByteArray()) } }.toByteArray()
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream").setHeader("Content-Encoding", "gzip").setBody(Buffer().write(compressed)))
            server.start()
            val config = DirectApiConfig("test", server.url("/v1").toString(), "secret-fixture", "synthetic", DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)
            var streamed = ""
            val result = MobileTrace.turn("creation_session", "fixture", buildJsonObject { put("turn_id", "turn-fixture") }) {
                DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList()).streamAgentTurn(config,
                    listOf(buildJsonObject { put("role", "user"); put("content", "secret-fixture") }), JsonArray(emptyList()),
                    onContentDelta = { streamed += it })
            }
            assertEquals("hello", result.content); assertEquals("hello", streamed)
            assertEquals("visible", result.reasoningContent)
            val request = server.takeRequest()
            assertEquals("/v1/chat/completions", request.path)
            assertEquals("Bearer secret-fixture", request.getHeader("Authorization"))
            assertTrue(request.body.readUtf8().contains("secret-fixture"))
            val payloads = sink.events.filter { it.text("event_type") == "payload" }
            assertTrue(payloads.any { it["data"]!!.jsonObject.text("layer") == "provider_request" })
            val output = payloads.first { it["data"]!!.jsonObject.text("layer") == "provider_response" }
            assertTrue(sink.contents[output.text("event_id")]!!.contains("visible"))
            assertFalse(sink.contents.values.any { it.contains("secret-fixture") })
        }
    }

    @Test fun `export inventories sanitized payload and fails if records disappear`() = runBlocking<Unit> {
        val event = buildJsonObject {
            put("event_id", "payload"); put("sequence", 1)
            put("data", buildJsonObject { put("content_hash", "fixture"); put("layer", "tool_receipt") })
        }
        val raw = "{\"token\":\"hidden\",\"text\":\"🙂\"}"
        val output = ByteArrayOutputStream()
        writeTraceArchive(output, buildJsonObject { put("mode", "full"); put("capture_status", "recorded") }, "fixture",
            events = { if (it == 0) listOf(event) else emptyList() },
            payload = { _, offset -> buildJsonObject { put("content", raw); put("next_offset", offset + raw.codePointCount(0, raw.length)); put("total_characters", raw.codePointCount(0, raw.length)) } },
            stillExists = { true })
        val entries = mutableMapOf<String, String>()
        java.util.zip.ZipInputStream(output.toByteArray().inputStream()).use { zip ->
            while (true) { val next = zip.nextEntry ?: break; entries[next.name] = zip.readBytes().toString(Charsets.UTF_8) }
        }
        assertFalse(entries.values.any { it.contains("hidden") })
        assertTrue(entries["payloads/payload.json"]!!.contains("[REDACTED]"))
        val files = Json.parseToJsonElement(entries["manifest.json"]!!).jsonObject["files"]!!.jsonArray
        files.forEach { file -> assertEquals(file.jsonObject.text("sha256"), TraceRedaction.hash(entries[file.jsonObject.text("path")]!!)) }
        assertFailsWith<IllegalStateException> {
            writeTraceArchive(ByteArrayOutputStream(), buildJsonObject {}, "fixture",
                events = { emptyList() }, payload = { _, _ -> error("no payload") }, stillExists = { false })
        }
    }

    @Test fun `summary leaves messages unrecorded and sink failure never blocks business`() = runBlocking {
        val sink = MemorySink("summary"); MobileTrace.sink = sink
        MobileTrace.turn("operation", "task", buildJsonObject {}) {
            MobileTrace.payload("tool_arguments", buildJsonObject { put("content", "private draft") })
        }
        assertTrue(sink.contents.isEmpty())
        assertTrue(sink.events.any { it["data"]!!.jsonObject.text("missing_reason") == "recording_not_enabled" })
        MobileTrace.sink = object : ContextTraceSink {
            override fun mode() = "full"
            override fun submit(event: JsonObject, content: String?): Boolean = throw java.io.IOException("disk full")
        }
        assertEquals(42, MobileTrace.turn("operation", "task", buildJsonObject {}) { 42 })
    }

    @Test fun `coroutine context separates concurrent turns and cancellation is recorded`() = runBlocking {
        val sink = MemorySink(); MobileTrace.sink = sink
        coroutineScope {
            (1..2).map { index -> async(Dispatchers.Default) {
                MobileTrace.turn("creation_session", "session-$index", buildJsonObject {}) {
                    delay(5)
                    MobileTrace.payload("tool_arguments", buildJsonObject { put("entity_id", "entity-$index") })
                }
            } }.awaitAll()
        }
        assertEquals(2, sink.events.filter { it.text("event_type") == "trace_started" }.map { it.text("trace_id") }.toSet().size)
        val job = launch {
            MobileTrace.turn("operation", "cancelled", buildJsonObject {}) { delay(60_000) }
        }
        yield(); job.cancelAndJoin()
        assertTrue(sink.events.any { it.text("event_type") == "trace_finished" && it["data"]!!.jsonObject.text("status") == "cancelled" })
        assertNull(MobileTrace.current.get())
    }

    @Test fun `busy capture drops diagnostic content without delaying business`() = runBlocking {
        val sink = MemorySink(); MobileTrace.sink = sink
        assertTrue(TraceRedaction.processing.tryAcquire())
        try {
            val result = withTimeout(2_000) {
                MobileTrace.turn("operation", "busy", buildJsonObject {}) {
                    MobileTrace.payload("tool_receipt", buildJsonObject { put("content", "private draft") })
                    42
                }
            }
            assertEquals(42, result)
            assertTrue(sink.contents.isEmpty())
            assertTrue(sink.events.any { it["data"]!!.jsonObject.text("missing_reason") == "capture_busy" })
            val finished = sink.events.last { it.text("event_type") == "trace_finished" }["data"]!!.jsonObject
            assertEquals("completed", finished.text("status"))
            assertEquals("partial", finished.text("capture_status"))
        } finally { TraceRedaction.processing.release() }
    }

    @Test fun `shared redaction and event fixtures use the same contract as PC`() {
        val root = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .first { File(it, "contracts/fixtures/context-trace-v1-interop.json").exists() }
        val fixture = Json.parseToJsonElement(File(root, "contracts/fixtures/context-trace-v1-interop.json").readText()).jsonObject
        assertEquals(TRACE_SCHEMA, fixture.text("schema"))
        fixture["redaction_cases"]!!.jsonArray.forEach { raw ->
            val item = raw.jsonObject
            assertEquals(item["expected"], TraceRedaction.clean(item["input"]!!, item["secrets"]!!.jsonArray.map { it.jsonPrimitive.content }))
        }
    }
}
