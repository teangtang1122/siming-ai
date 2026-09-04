package com.siming.mobile.data.agent

import java.time.Instant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

internal data class MobileContextDeliveryState(
    val status: String,
    val ready: Boolean,
    val sha256: String,
    val totalChars: Int,
    val deliveredUntil: Int,
    val expectedCursor: Int?,
    val pageLimit: Int,
    val lastCursor: Int,
    val lastLimit: Int,
    val selectionTokenSha256: String,
    val startedAt: String,
    val completedAt: String? = null,
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("status", status)
        put("ready", ready)
        put("sha256", sha256)
        put("total_chars", totalChars)
        put("delivered_until", deliveredUntil)
        if (expectedCursor == null) put("expected_cursor", JsonNull) else put("expected_cursor", expectedCursor)
        put("page_limit", pageLimit)
        put("last_cursor", lastCursor)
        put("last_limit", lastLimit)
        put("selection_token_sha256", selectionTokenSha256)
        put("started_at", startedAt)
        if (completedAt == null) put("completed_at", JsonNull) else put("completed_at", completedAt)
    }

    companion object {
        fun fromJson(root: JsonObject): MobileContextDeliveryState = MobileContextDeliveryState(
            status = root.stringValue("status"),
            ready = (root["ready"] as? JsonPrimitive)?.let { !it.isString && it.content == "true" } ?: false,
            sha256 = root.stringValue("sha256"),
            totalChars = (root["total_chars"] as? JsonPrimitive)?.intOrNull ?: 0,
            deliveredUntil = (root["delivered_until"] as? JsonPrimitive)?.intOrNull ?: 0,
            expectedCursor = (root["expected_cursor"] as? JsonPrimitive)?.intOrNull,
            pageLimit = (root["page_limit"] as? JsonPrimitive)?.intOrNull ?: 6000,
            lastCursor = (root["last_cursor"] as? JsonPrimitive)?.intOrNull ?: 0,
            lastLimit = (root["last_limit"] as? JsonPrimitive)?.intOrNull ?: 6000,
            selectionTokenSha256 = root.stringValue("selection_token_sha256"),
            startedAt = root.stringValue("started_at"),
            completedAt = (root["completed_at"] as? JsonPrimitive)?.contentOrNull,
        )
    }
}

internal data class MobileContextDeliveryAdvance(
    val manifest: MobileContextManifest,
    val page: JsonObject,
    val state: MobileContextDeliveryState,
)

internal fun mobileContextPage(text: String, args: JsonObject = JsonObject(emptyMap())): JsonObject {
    fun integer(name: String, default: Int): Int {
        val value = args[name] ?: return default
        require(value is JsonPrimitive && !value.isString && value.intOrNull != null) { "$name must be an integer" }
        return value.intOrNull!!
    }
    val total = text.codePointCount(0, text.length)
    val cursor = integer("content_cursor", 0)
    val limit = integer("content_limit", 6000)
    require(cursor in 0..total) { "content_cursor must be a Unicode code-point offset within this context document" }
    require(limit in 1..7000) { "content_limit must be an integer from 1 to 7000" }
    val hash = mobileSha256(text)
    val expected = args.stringValue("expected_context_sha256")
    require(expected.isBlank() || expected == hash) { "Context document changed; restart at content_cursor=0 without the old hash" }
    val start = Character.offsetByCodePoints(text, 0, cursor)
    fun part(end: Int): String = text.substring(start, Character.offsetByCodePoints(text, start, end - cursor))
    var end = minOf(total, cursor + limit)
    if (JsonPrimitive(part(end)).toString().toByteArray(Charsets.UTF_8).size > 20 * 1024) {
        var low = cursor
        var high = end
        while (low < high) {
            val middle = (low + high + 1) / 2
            if (JsonPrimitive(part(middle)).toString().toByteArray(Charsets.UTF_8).size <= 20 * 1024) low = middle
            else high = middle - 1
        }
        end = low
    }
    return buildJsonObject {
        put("text", part(end)); put("cursor", cursor); put("limit", limit)
        if (end < total) put("next_cursor", end) else put("next_cursor", JsonNull)
        put("has_more", end < total); put("total_chars", total)
        put("sha256", hash); put("offset_unit", "unicode_code_points")
    }
}

internal fun mobileContextPageArguments(manifest: MobileContextManifest, page: JsonObject): JsonObject = buildJsonObject {
    put("context_manifest_id", manifest.id); put("task_type", manifest.request.taskType)
    put("content_cursor", page["next_cursor"] ?: JsonPrimitive(0))
    put("content_limit", page.getValue("limit")); put("expected_context_sha256", page.getValue("sha256"))
}

internal fun beginMobileContextDelivery(
    manifest: MobileContextManifest,
    page: JsonObject,
): MobileContextManifest {
    require(page.getValue("cursor").jsonPrimitive.int == 0) {
        "Selected context delivery must begin at content_cursor=0"
    }
    val token = manifest.selectionToken.orEmpty()
    require(token.isNotBlank()) { "Selected context has no active selection token" }
    val complete = !page.getValue("has_more").jsonPrimitive.boolean
    val now = Instant.now().toString()
    val total = page.getValue("total_chars").jsonPrimitive.int
    val next = (page["next_cursor"] as? JsonPrimitive)?.intOrNull
    return manifest.copy(
        contextDelivery = MobileContextDeliveryState(
            status = if (complete) "complete" else "pending",
            ready = complete,
            sha256 = page.getValue("sha256").jsonPrimitive.content,
            totalChars = total,
            deliveredUntil = if (complete) total else next ?: 0,
            expectedCursor = if (complete) null else next,
            pageLimit = page.getValue("limit").jsonPrimitive.int,
            lastCursor = 0,
            lastLimit = page.getValue("limit").jsonPrimitive.int,
            selectionTokenSha256 = mobileSha256(token),
            startedAt = now,
            completedAt = now.takeIf { complete },
        ),
    )
}

internal fun mobileContextDeliveryReady(manifest: MobileContextManifest, token: String): Boolean {
    val state = manifest.contextDelivery ?: return false
    return state.status == "complete" && state.ready && state.selectionTokenSha256 == mobileSha256(token)
}

internal fun deliverMobileNextContextPage(
    manifest: MobileContextManifest,
    text: String,
    args: JsonObject,
): MobileContextDeliveryAdvance {
    val token = manifest.selectionToken.orEmpty()
    require(token.isNotBlank()) { "Selected context has no active selection token" }
    val state = manifest.contextDelivery
    if (state == null) {
        require((args["content_cursor"] as? JsonPrimitive)?.intOrNull ?: 0 == 0) {
            "Selected context delivery must restart at content_cursor=0"
        }
        val page = mobileContextPage(text, args)
        val started = beginMobileContextDelivery(manifest, page)
        return MobileContextDeliveryAdvance(started, page, started.contextDelivery!!)
    }
    require(state.selectionTokenSha256 == mobileSha256(token)) {
        "Context selection changed; submit evidence again before reading pages"
    }
    require(args.stringValue("expected_context_sha256") == state.sha256) {
        "expected_context_sha256 must match the active selected context document"
    }
    require((args["content_limit"] as? JsonPrimitive)?.intOrNull == state.pageLimit) {
        "content_limit must remain ${state.pageLimit} for this context delivery"
    }
    val cursor = (args["content_cursor"] as? JsonPrimitive)?.intOrNull
    if (state.status == "complete") {
        require(cursor == state.lastCursor) {
            "Selected context is already complete; only the final page may be replayed"
        }
        val page = mobileContextPage(text, args)
        require(!page.getValue("has_more").jsonPrimitive.boolean && page.stringValue("sha256") == state.sha256) {
            "Completed context delivery no longer matches the selected document"
        }
        return MobileContextDeliveryAdvance(manifest, page, state)
    }
    require(cursor == state.expectedCursor) {
        "context_page delivery is out of order; expected content_cursor=${state.expectedCursor}"
    }
    val page = mobileContextPage(text, args)
    val total = page.getValue("total_chars").jsonPrimitive.int
    require(page.stringValue("sha256") == state.sha256 && total == state.totalChars) {
        "Context document changed; submit evidence again before generation"
    }
    val complete = !page.getValue("has_more").jsonPrimitive.boolean
    val next = (page["next_cursor"] as? JsonPrimitive)?.intOrNull
    val advanced = state.copy(
        status = if (complete) "complete" else "pending",
        ready = complete,
        deliveredUntil = if (complete) total else next ?: 0,
        expectedCursor = if (complete) null else next,
        lastCursor = page.getValue("cursor").jsonPrimitive.int,
        lastLimit = page.getValue("limit").jsonPrimitive.int,
        completedAt = Instant.now().toString().takeIf { complete },
    )
    val updated = manifest.copy(contextDelivery = advanced)
    return MobileContextDeliveryAdvance(updated, page, advanced)
}

internal fun mobileContextDeliveryStatus(state: MobileContextDeliveryState?): JsonObject = buildJsonObject {
    put("status", state?.status ?: "not_started")
    put("ready", state?.ready ?: false)
    state?.let {
        put("sha256", it.sha256)
        put("total_chars", it.totalChars)
        put("delivered_until", it.deliveredUntil)
        if (it.expectedCursor == null) put("expected_cursor", JsonNull) else put("expected_cursor", it.expectedCursor)
        put("page_limit", it.pageLimit)
        if (it.completedAt == null) put("completed_at", JsonNull) else put("completed_at", it.completedAt)
    }
}

internal fun compactMobileContextManifest(manifest: MobileContextManifest): JsonObject {
    val payload = manifest.toJson(includeContent = false)
    return buildJsonObject {
        listOf("id", "task_type", "status", "budget", "coverage", "warnings").forEach { key -> payload[key]?.let { put(key, it) } }
        put("item_count", (payload["items"] as? JsonArray)?.size ?: 0)
        put("selection", buildJsonObject { put("status", if (manifest.selectionToken.isNullOrBlank()) "pending" else "ready") })
    }
}

internal fun mobileContextSelectionDiagnostics(rejected: List<String>): JsonObject = buildJsonObject {
    put("validation_errors", JsonArray(rejected.take(6).map { reason ->
        val end = reason.offsetByCodePoints(0, minOf(240, reason.codePointCount(0, reason.length)))
        buildJsonObject { put("reason", reason.substring(0, end)) }
    }))
    put("validation_error_count", rejected.size)
    put("validation_errors_has_more", rejected.size > 6)
}
