package com.siming.mobile.data.observability

import kotlinx.serialization.json.*
import kotlin.math.roundToLong

internal fun traceSpans(events: List<JsonObject>, trace: JsonObject, complete: Boolean): Map<String, JsonObject> {
    val spans = linkedMapOf<String, JsonObject>()
    events.filter { it.text("event_type") in setOf("span_started", "span_finished", "http_response", "usage") }.forEach { event ->
        val data = event.getValue("data").jsonObject
        val id = data.text("span_id").ifBlank { event.text("span_id") }
        if (id.isNotBlank()) spans[id] = JsonObject(spans[id].orEmpty() + data)
    }
    return spans.mapValues { (_, span) ->
        if (trace["finished"] != null && trace["finished"] != JsonNull && span.text("status") == "running") {
            val status = when {
                !complete -> "pending_record"
                trace.text("capture_status") == "interrupted" -> "interrupted"
                else -> "incomplete"
            }
            JsonObject(span + ("status" to JsonPrimitive(status)))
        } else span
    }
}

internal fun traceDuration(data: JsonObject): String {
    val duration = (data["duration_ms"] as? JsonPrimitive)?.doubleOrNull ?: return "耗时未知"
    return if (duration < 1) "<1 ms" else "${duration.roundToLong()} ms"
}
