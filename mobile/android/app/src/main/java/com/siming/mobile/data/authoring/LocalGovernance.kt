package com.siming.mobile.data.authoring

import kotlinx.serialization.json.*

/** Deterministic lifecycle contract mirrored by governance_lifecycle.validate_transition. */
internal fun localGovernanceTransition(current: String, payload: JsonObject, now: String): JsonObject {
    val target = payload.text("status").ifBlank { current }
    val transitions = mapOf(
        "open" to setOf("pending_review", "deferred", "abandoned"),
        "deferred" to setOf("open", "pending_review", "abandoned"),
        "pending_review" to setOf("open", "fulfilled", "abandoned"),
        "fulfilled" to setOf("open"), "abandoned" to setOf("open"),
        "stale" to setOf("open", "pending_review", "abandoned"),
    )
    require(target in setOf("open", "pending_review", "deferred", "fulfilled", "abandoned")) { "该治理对象不支持目标状态" }
    require(target != "fulfilled" || current == "pending_review") { "必须先提交复检，不能直接关闭治理项" }
    require(target == current || target in transitions[current].orEmpty()) { "不能从 $current 直接变更为 $target" }
    val note = payload.text("resolution_note").trim()
    if (target in setOf("pending_review", "fulfilled")) {
        require(payload.text("resolved_chapter_id").isNotBlank()) { "复检或关闭治理项必须选择实际修订的章节" }
        require(note.length >= 4) { "请填写至少 4 个字符的解决说明" }
    }
    if (target == "fulfilled") require(payload.text("verification_note").trim().length >= 4) { "关闭治理项必须填写至少 4 个字符的复检结论" }
    if (target == "deferred") require(payload.text("target_chapter_id").isNotBlank() || payload.number("target_chapter_number") != 0) { "延期治理项必须指定计划处理章节" }
    if (target == "abandoned" || current in setOf("fulfilled", "abandoned", "stale") && target == "open") require(note.length >= 4) { "放弃或重新打开治理项必须填写原因" }
    return JsonObject(payload + buildJsonObject {
        put("status", target); put("last_checked_at", now)
        when (target) {
            "fulfilled" -> { put("verified_at", now); put("closed_by", payload.text("closed_by").ifBlank { "user" }.take(50)); put("stale_reason", JsonNull) }
            "abandoned" -> { put("verified_at", JsonNull); put("closed_by", payload.text("closed_by").ifBlank { "user" }.take(50)); put("stale_reason", JsonNull) }
            "open" -> { put("verified_at", JsonNull); put("verification_note", JsonNull); put("closed_by", JsonNull); put("stale_reason", JsonNull) }
        }
    })
}
