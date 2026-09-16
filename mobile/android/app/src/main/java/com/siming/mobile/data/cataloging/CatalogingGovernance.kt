package com.siming.mobile.data.cataloging

import java.security.MessageDigest
import kotlinx.serialization.json.*

internal fun CatalogingProjection.catalogSummary(payload: JsonObject) {
    val summary = find("chapter_summary") { it.text("chapter_id") == chapterId }
    put("summary", "chapter_summary", summary?.id ?: id("summary"), buildJsonObject {
        put("chapter_id", chapterId); put("summary_text", payload.text("summary_text"))
        put("key_events", jsonStrings(payload.strings("key_events")).toString())
        put("token_count", payload.text("summary_text").length)
    })
    val state = payload.obj("narrative_state")
    fact("chapter_narrative_state", JsonObject(state + mapOf("chapter_id" to JsonPrimitive(chapterId), "chapter_title" to JsonPrimitive(chapter.text("title")))))
    var findings = 0
    for ((field, type, status) in listOf(
        Triple("foreshadowing_planted", "foreshadowing", "open"),
        Triple("foreshadowing_resolved", "foreshadowing", "fulfilled"),
        Triple("unresolved_actions", "narrative_debt", "open"),
    )) {
        (state[field] as? JsonArray).orEmpty().forEach { raw ->
            val item = narrativeItem(raw)
            governance(JsonObject(item + mapOf("type" to JsonPrimitive(type), "status" to JsonPrimitive(status),
                "debt_type" to JsonPrimitive(item.text("debt_type").ifBlank { "unresolved_action" }))))
            findings++
        }
    }
    payload.objects("governance_candidates").forEach { governance(it); findings++ }
    val prior = find("chapter_governance_review") { it.text("chapter_id") == chapterId && it.number("chapter_version") == chapter.number("current_version") }
    val review = payload.obj("narrative_review")
    put("governance", "chapter_governance_review", prior?.id ?: id("review"), buildJsonObject {
        put("chapter_id", chapterId); put("chapter_version", chapter.number("current_version"))
        put("status", if (review.text("source") == "fallback") "needs_review" else "assessed")
        put("source", review.text("source").ifBlank { "provided" }); put("findings_count", findings)
        put("confidence", review["confidence"] ?: JsonPrimitive(0.7))
        put("evidence", review.text("evidence").ifBlank { review.text("reason").ifBlank { "作品建档已显式检查本章叙事状态；发现 $findings 条治理线索。" } })
    })
    for ((fields, type, status) in listOf(
        Triple(listOf("events", "timeline_events"), "completed_beat", "completed"),
        Triple(listOf("reader_known_facts"), "revealed_clue", "active"),
        Triple(listOf("foreshadowing_planted", "unresolved_actions"), "narrative_promise", "open"),
        Triple(listOf("foreshadowing_resolved"), "narrative_promise", "fulfilled"),
        Triple(listOf("storyline_progress", "new_storylines"), "storyline_state", "active"),
    )) fields.forEach { field ->
        (state[field] as? JsonArray).orEmpty().forEach ledgerItem@ { raw ->
            val item = narrativeItem(raw)
            val title = item.text("title")
            if (title.isBlank()) return@ledgerItem
            val scope = item.text("storyline").ifBlank { item.text("storyline_title") }
            val identity = listOf("ledger_key", "key", "id", "promise_id").firstNotNullOfOrNull { item.text(it).takeIf(String::isNotBlank) } ?: title
            val normalized = identity.lowercase().let { text ->
                if (type == "storyline_state") listOf("storyline", "story line", "plotline", "arc", "故事线", "剧情线", "主线", "支线")
                    .fold(text) { value, word -> value.replace(word, "") } else text
            }.replace(Regex("[^\\p{L}\\p{N}]+"), "").take(160).ifBlank { "untitled" }
            val key = "nl_" + sha1("$type|${scope.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), "").take(80)}|$normalized").take(16)
            val matches = ofType("cataloging_fact").filter { record ->
                record.payload.text("fact_type") == "narrative_ledger_entry" && record.payload.text("status") == "active" &&
                    Json.parseToJsonElement(record.payload.text("raw_payload")).jsonObject.text("ledger_key") == key
            }
            matches.forEach { put(it.entityType, it.recordType, it.id, buildJsonObject { put("status", "superseded") }) }
            val first = matches.lastOrNull()?.payload?.text("raw_payload")?.let { Json.parseToJsonElement(it).jsonObject }
            put("cataloging_fact", "cataloging_fact", id("ledger", type, key), buildJsonObject {
                put("chapter_id", chapterId); put("fact_type", "narrative_ledger_entry"); put("status", "active")
                put("raw_payload", JsonObject(item + mapOf(
                    "ledger_type" to JsonPrimitive(type), "ledger_key" to JsonPrimitive(key), "status" to JsonPrimitive(item.text("status").ifBlank { status }),
                    "storyline" to JsonPrimitive(scope), "category" to JsonPrimitive(when (field) { "unresolved_actions" -> "unresolved_action"; "foreshadowing_planted", "foreshadowing_resolved" -> "foreshadowing"; else -> "" }),
                    "first_chapter_id" to JsonPrimitive(first?.text("first_chapter_id").orEmpty().ifBlank { chapterId }),
                    "first_chapter_title" to JsonPrimitive(first?.text("first_chapter_title").orEmpty().ifBlank { chapter.text("title") }),
                    "last_chapter_id" to JsonPrimitive(chapterId), "last_chapter_title" to JsonPrimitive(chapter.text("title")),
                )).toString())
            })
        }
    }
}

private fun narrativeItem(value: JsonElement): JsonObject {
    val raw = if (value is JsonObject) value else buildJsonObject { put("description", value.jsonPrimitive.content) }
    val title = listOf("title", "name", "description", "summary", "promise", "action")
        .firstNotNullOfOrNull { raw.text(it).takeIf(String::isNotBlank) }.orEmpty()
    return JsonObject(raw + ("title" to JsonPrimitive(title)))
}

private fun CatalogingProjection.governance(raw: JsonObject) {
    val p = narrativeItem(raw)
    val type = when (p.text("type")) {
        "foreshadowing", "foreshadow", "narrative_promise" -> "foreshadowing"
        "narrative_debt", "debt" -> "narrative_debt"
        "causal_edge", "causal" -> "causal_edge"
        "character_state", "character_narrative_state", "character_mask", "emotion_ledger" -> "character_narrative_state"
        "chapter_quality", "quality_metric", "tension_dimensions" -> "chapter_quality_metric"
        else -> error("未知 governance_candidates.type：${p.text("type")}")
    }
    if (type == "character_narrative_state") {
        val characterId = p.text("character_id")
        require(plan.candidates.single { it.kind == "chapter_summary" }.payload.objects("character_bindings").any { it.text("id") == characterId }) {
            "治理角色状态必须使用本章计划绑定的 character_id"
        }
        put("governance", type, id(type, characterId), JsonObject(p.filterKeys { it in setOf("character_id", "current_goal", "public_stance", "hidden_intent", "emotional_residue", "relationship_tension", "behavior_boundaries", "evidence") } +
            mapOf("chapter_id" to JsonPrimitive(chapterId), "source" to JsonPrimitive("cataloging"))))
        return
    }
    if (type == "chapter_quality_metric") {
        val fields = p.filterKeys { it in setOf("plot_tension", "emotional_tension", "pacing_density", "character_consistency", "viewpoint_consistency", "world_consistency", "target_tension", "strict_mode", "passed", "warnings", "evidence", "total_score", "max_score", "dimension_scores", "overall_assessment", "model") }
        put("governance", type, id(type), JsonObject(fields + mapOf("chapter_id" to JsonPrimitive(chapterId), "chapter_version" to JsonPrimitive(chapter.number("current_version")), "source" to JsonPrimitive("cataloging"))))
        return
    }
    val requested = p.text("status").ifBlank { "open" }
    require(requested !in setOf("abandoned", "invalidated")) { "放弃或作废治理项须由作者确认" }
    val resolution = requested in setOf("fulfilled", "resolved", "pending_review")
    val explicitId = listOf("resolves_item_id", "governance_item_id", "item_id").firstNotNullOfOrNull { p.text(it).takeIf(String::isNotBlank) }
    val explicitKey = p.text("resolves_dedupe_key")
    require(!resolution || explicitId != null || explicitKey.isNotBlank()) { "解决治理项必须携带真实 resolves_item_id 或 resolves_dedupe_key" }
    val keyParts = when (type) {
        "foreshadowing" -> listOf(p.text("title"), p.text("storyline"))
        "causal_edge" -> listOf(p.text("cause"), p.text("effect"), p.text("causal_type"))
        else -> listOf(p.text("debt_type"), p.text("title"))
    }
    val key = p.text("dedupe_key").ifBlank { sha1(keyParts.joinToString("|") { it.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), "") }).take(32) }
    val old = when {
        explicitId != null -> find(type) { it.text("id") == explicitId } ?: error("治理 ID 不存在或不属于本作品")
        explicitKey.isNotBlank() -> find(type) { it.text("dedupe_key") == explicitKey } ?: error("治理去重键不存在")
        else -> find(type) { it.text("dedupe_key") == key }
    }
    require(old?.payload?.text("status") !in setOf("fulfilled", "resolved", "abandoned", "invalidated")) { "已关闭治理项必须先由作者重新打开" }
    val fields = p.filterKeys { it in GOVERNANCE_FIELDS }.toMutableMap()
    if (resolution) {
        listOf("source_chapter_id", "title", "description", "evidence").forEach(fields::remove)
        fields["resolved_chapter_id"] = JsonPrimitive(chapterId)
        fields["resolved_chapter_version"] = JsonPrimitive(chapter.number("current_version"))
        fields["resolution_note"] = JsonPrimitive(p.text("resolution_note").ifBlank { p.text("evidence").ifBlank { "模型检测到可能已解决，等待人工复检" } })
        fields["resolution_evidence"] = JsonPrimitive(p.text("resolution_evidence").ifBlank { p.text("evidence") })
    } else {
        require(p.text(if (type == "causal_edge") "cause" else "title").isNotBlank()) { "治理项内容不能为空" }
        fields["source_chapter_id"] = JsonPrimitive(p.text("source_chapter_id").ifBlank { chapterId })
        fields["source_chapter_version"] = JsonPrimitive(chapter.number("current_version"))
        fields["dedupe_key"] = JsonPrimitive(key)
        if (type == "foreshadowing") fields.putIfAbsent("importance", JsonPrimitive("normal"))
        if (type == "narrative_debt") {
            fields.putIfAbsent("priority", JsonPrimitive("normal"))
            fields.putIfAbsent("debt_type", JsonPrimitive("unresolved_action"))
        }
    }
    listOf("source_chapter_id", "target_chapter_id", "resolved_chapter_id").forEach { field ->
        (fields[field] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)?.let { get("chapter", it) }
    }
    val newStatus = if (resolution) "pending_review" else requested
    fields["status"] = JsonPrimitive(newStatus)
    fields["source"] = JsonPrimitive("cataloging")
    val record = put(if (type == "foreshadowing") "foreshadowing" else "governance", type, old?.id ?: catalogingId("governance", projectId, type, key), JsonObject(fields))
    if (old == null || old.payload.text("status") != newStatus) put("governance", "narrative_governance_event", id("governance_event", record.id), buildJsonObject {
        put("item_type", when (type) { "foreshadowing" -> "foreshadowings"; "causal_edge" -> "causal-edges"; else -> "narrative-debts" })
        put("item_id", record.id); put("chapter_id", chapterId); put("chapter_version", chapter.number("current_version"))
        put("from_status", old?.payload?.get("status") ?: JsonNull); put("to_status", newStatus)
        put("actor", "cataloging"); put("note", p.text("evidence").ifBlank { "建档更新治理项" })
    })
}

private fun sha1(value: String): String = MessageDigest.getInstance("SHA-1").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
private val GOVERNANCE_FIELDS = setOf("title", "description", "importance", "priority", "debt_type", "cause", "effect", "causal_type", "strength", "character_ids", "storyline", "source_chapter_id", "target_chapter_id", "target_chapter_number", "resolved_chapter_id", "linked_foreshadowing_id", "linked_causal_edge_id", "evidence", "resolution_note", "resolution_evidence")
