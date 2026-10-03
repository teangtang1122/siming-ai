package com.siming.mobile.data.observability

import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.asContextElement
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

internal const val TRACE_SCHEMA = "siming.context_trace.v1"
internal const val TRACE_PAYLOAD_LIMIT = 16 * 1024 * 1024

internal interface ContextTraceSink {
    fun mode(): String
    fun submit(event: JsonObject, content: String? = null): Boolean
}

internal class ContextTrace(
    val sink: ContextTraceSink,
    val scopeKind: String,
    val scopeId: String,
    val mode: String = runCatching { sink.mode() }.getOrDefault("off"),
) {
    val id = UUID.randomUUID().toString().replace("-", "")
    private val sequence = AtomicInteger()
    val dropped = AtomicInteger()
    val secrets = java.util.Collections.synchronizedSet(mutableSetOf<String>())
    var businessStatus = "completed"
    @Volatile var partial = false
    val outcomes = java.util.concurrent.ConcurrentHashMap<String, String>()

    fun event(type: String, data: JsonObject, spanId: String? = MobileTrace.parent.get(), content: String? = null) {
        if (mode == "off") return
        synchronized(this) {
            val value = buildJsonObject {
                put("event_id", UUID.randomUUID().toString().replace("-", ""))
                put("trace_id", id); put("source_id", id)
                put("sequence", sequence.incrementAndGet()); put("timestamp", System.currentTimeMillis() / 1000.0)
                put("event_type", type); put("span_id", spanId?.let(::JsonPrimitive) ?: JsonNull)
                put("data", data)
            }
            if (!runCatching { sink.submit(value, content) }.getOrDefault(false)) dropped.incrementAndGet()
        }
    }

    fun payload(layer: String, value: JsonElement, source: String = "application", spanId: String? = MobileTrace.parent.get()) {
        runCatching {
            if (mode != "full") {
                payloadRecord(layer, null, source, spanId, "recording_not_enabled")
            } else if (!TraceRedaction.withinBudget(value)) {
                payloadRecord(layer, null, source, spanId, "size_limit")
            } else if (!TraceRedaction.processing.tryAcquire()) {
                payloadRecord(layer, null, source, spanId, "capture_busy")
            } else {
                try {
                    val clean = TraceRedaction.clean(value, synchronized(secrets) { secrets.toList() }).toString()
                    val tooLarge = clean.toByteArray().size > TRACE_PAYLOAD_LIMIT
                    payloadRecord(layer, clean.takeUnless { tooLarge }, source, spanId,
                        if (tooLarge) "size_limit" else null)
                } finally { TraceRedaction.processing.release() }
            }
        }.onFailure { dropped.incrementAndGet() }
    }

    fun payloadRecord(layer: String, content: String?, source: String, spanId: String?, reason: String?, extra: JsonObject = buildJsonObject {}) {
        if (reason != null && reason != "recording_not_enabled") partial = true
        event("payload", buildJsonObject {
            put("layer", layer); put("capture_source", source); put("media_type", "application/json")
            put("span_id", spanId?.let(::JsonPrimitive) ?: JsonNull)
            put("completeness", if (reason == "recording_not_enabled") "not_recorded" else if (reason == null) "complete" else "partial")
            put("redaction", "applied"); put("missing_reason", reason?.let(::JsonPrimitive) ?: JsonNull)
            if (content != null) {
                put("stored_bytes", content.toByteArray().size)
                put("content_hash", TraceRedaction.hash(content))
            }
            extra.forEach { (key, value) -> put(key, value) }
        }, spanId, content)
    }
}

internal class ContextSpan(val trace: ContextTrace?, val kind: String, label: String) {
    val id = UUID.randomUUID().toString().replace("-", "")
    private val started = System.nanoTime()
    private var finished = false
    init {
        trace?.event("span_started", buildJsonObject {
            put("span_id", id); put("parent_span_id", MobileTrace.parent.get()?.let(::JsonPrimitive) ?: JsonNull)
            put("kind", kind); put("label", label.take(200)); put("status", "running")
        })
    }
    @Synchronized fun finish(status: String, errorType: String? = null) {
        if (finished) return
        finished = true
        val outcome = if (status == "completed") trace?.outcomes?.remove(id) ?: status else status
        if (kind == "turn" && outcome != "completed") trace?.businessStatus = outcome
        trace?.event("span_finished", buildJsonObject {
            put("span_id", id); put("status", outcome)
            put("duration_ms", (System.nanoTime() - started) / 1_000_000.0)
            errorType?.let { put("error_type", it) }
        })
    }
}

internal object MobileTrace {
    val current = ThreadLocal<ContextTrace?>()
    val parent = ThreadLocal<String?>()
    @Volatile var sink: ContextTraceSink? = null

    suspend fun <T> turn(kind: String, id: String, correlations: JsonObject, block: suspend () -> T): T {
        if (current.get() != null) return block()
        val configured = sink ?: return block()
        val trace = ContextTrace(configured, kind, id)
        trace.event("trace_started", buildJsonObject {
            put("schema", TRACE_SCHEMA); put("origin", "android"); put("mode", trace.mode)
            put("scope", buildJsonObject { put("kind", kind); put("id", id) })
            put("correlations", correlations); put("status", "running"); put("capture_status", "recording")
            put("started_at", System.currentTimeMillis() / 1000.0)
        })
        return withContext(current.asContextElement(trace)) {
            var status = "completed"
            try { span("turn", "任务", block) }
            catch (error: Throwable) {
                status = if (error is CancellationException) "cancelled" else "error"
                throw error
            } finally {
                trace.event("trace_finished", buildJsonObject {
                    put("status", if (status == "completed") trace.businessStatus else status); put("finished_at", System.currentTimeMillis() / 1000.0)
                    put("dropped_events", trace.dropped.get())
                    put("capture_status", if (trace.dropped.get() > 0 || trace.partial) "partial" else "recorded")
                })
            }
        }
    }

    suspend fun <T> span(kind: String, label: String, block: suspend () -> T): T {
        val span = ContextSpan(current.get(), kind, label)
        return withContext(parent.asContextElement(span.id)) {
            try { block().also {
                (it as? JsonObject)?.let(::toolOutcome)
                span.finish("completed")
            } }
            catch (error: Throwable) {
                span.finish(if (error is CancellationException) "cancelled" else "error", error.javaClass.simpleName)
                throw error
            }
        }
    }

    fun payload(layer: String, value: JsonElement) { current.get()?.payload(layer, value) }

    fun toolOutcome(value: JsonObject) {
        if (value.text("status") in setOf("error", "denied", "blocked", "failed", "conflict")) {
            parent.get()?.let { current.get()?.outcomes?.put(it, "error") }
        }
    }

    fun lazyPayload(layer: String, value: () -> JsonElement) {
        val trace = current.get() ?: return
        runCatching { trace.payload(layer, if (trace.mode == "full") value() else JsonNull) }
            .onFailure { trace.dropped.incrementAndGet() }
    }

    fun usage(value: JsonElement, trace: ContextTrace? = current.get(), spanId: String? = parent.get()) {
        if (trace == null) return
        if (value is JsonArray) { value.forEach { usage(it, trace, spanId) }; return }
        val fields = (value as? JsonObject)?.get("usage") as? JsonObject ?: return
        val known = setOf("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
        val counters = fields.filter { (key, item) -> key in known && (item as? JsonPrimitive)?.longOrNull?.let { it >= 0 } == true }
        if (counters.isNotEmpty()) trace.event("usage", buildJsonObject {
            put("usage_source", "provider_reported"); put("span_id", spanId?.let(::JsonPrimitive) ?: JsonNull)
            put("usage", JsonObject(counters))
        }, spanId)
    }

    suspend fun tool(name: String, args: JsonObject, callId: String? = null,
        project: (JsonObject) -> JsonObject = { it }, block: suspend () -> JsonObject): JsonObject = span("tool", name) {
        payload("tool_arguments", buildJsonObject { put("arguments", args); callId?.let { put("tool_call_id", it) } })
        val raw = block()
        payload("tool_receipt", raw)
        project(raw).also { toolOutcome(it); payload("model_visible_tool_result", it) }
    }
}
