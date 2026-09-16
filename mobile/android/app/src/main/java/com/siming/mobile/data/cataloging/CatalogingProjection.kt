package com.siming.mobile.data.cataloging

import com.siming.mobile.data.agent.pcExactCharacterArchive
import kotlinx.serialization.json.*

internal data class CatalogingChanges(val upserts: List<CatalogRecord>, val deletes: List<CatalogRecord>) {
    fun toJson() = buildJsonObject {
        put("upserts", JsonArray(upserts.map(CatalogRecord::toJson)))
        put("deletes", JsonArray(deletes.map(CatalogRecord::toJson)))
    }
}

/** Deterministic domain projection; the caller commits all changes in one Room transaction. */
internal class CatalogingProjection(val plan: CatalogingPlan, val runId: String, val now: String) {
    private val originals = plan.records.associateBy { it.entityType to it.id }
    val rows = originals.toMutableMap()
    val projectId = plan.projectId
    val chapterId = plan.chapterId
    val chapter get() = get("chapter", chapterId)

    fun apply(): CatalogingChanges {
        plan.requireComplete()
        plan.candidates.sortedWith(compareBy<CatalogCandidate>(
            { plan.contract.applyOrder.number(it.kind, 999) },
            { when (it.payload.text("node_type")) { "volume" -> 0; "chapter" -> 1; else -> 2 } },
            { it.payload.number("sort_order") },
        )).forEach { candidate ->
            val p = candidate.payload
            when (candidate.kind) {
                "chapter_summary" -> catalogSummary(p)
                "character_create", "character_update", "character_state_update" -> character(candidate)
                "character_timeline" -> timeline(candidate, false)
                "worldbuilding_create", "worldbuilding_update" -> world(candidate)
                "worldbuilding_timeline" -> timeline(candidate, true)
                "character_relationship" -> relationship(p)
                "character_merge_candidate" -> mergeCharacter(p)
                "outline_create", "outline_update" -> outline(candidate)
                "chapter_link" -> chapterLinks(p)
                else -> error("建档候选没有实际执行器：${candidate.kind}")
            }
        }
        reconcileScenes()
        val snapshotId = snapshot()
        put("chapter", "chapter", chapterId, buildJsonObject { put("cataloging_required", false) })
        put("governance", "narrative_checkpoint", id("checkpoint"), buildJsonObject {
            put("chapter_id", chapterId); put("chapter_snapshot_id", snapshotId)
            put("sequence", ofType("narrative_checkpoint").maxOfOrNull { it.payload.number("sequence") }?.plus(1) ?: 1)
            put("label", "${chapter.text("title")} 建档完成"); put("trigger_type", "cataloging")
            put("state_json", buildJsonObject {
                for ((key, type) in mapOf("foreshadowings" to "foreshadowing", "causal_edges" to "causal_edge", "narrative_debts" to "narrative_debt", "character_states" to "character_narrative_state", "quality_metrics" to "chapter_quality_metric", "chapter_reviews" to "chapter_governance_review")) {
                    put(key, JsonArray(ofType(type).map { it.payload }))
                }
            })
        })
        return CatalogingChanges(rows.values.filter { it != originals[it.entityType to it.id] },
            originals.filterKeys { it !in rows }.values.toList())
    }

    fun id(vararg keys: String) = catalogingId(runId, *keys)
    fun ofType(type: String) = rows.values.filter { it.recordType == type }
    fun find(type: String, predicate: (JsonObject) -> Boolean) = ofType(type).firstOrNull { predicate(it.payload) }
    fun get(type: String, identity: String): JsonObject = ofType(type).firstOrNull { it.id == identity }?.payload
        ?: error("$type ID=$identity 不属于当前作品")
    fun characterSnapshot(character: JsonObject): String {
        val archive = Json.parseToJsonElement(pcExactCharacterArchive(rows.values.map { it.payload }, character)).jsonObject
        return JsonObject(archive.filterKeys { it != "state" } + archive.obj("state")).toString()
    }
    fun put(entityType: String, recordType: String, identity: String, values: JsonObject): CatalogRecord {
        val key = entityType to identity
        val old = rows[key]?.payload.orEmpty()
        val payload = JsonObject(mapOf("id" to JsonPrimitive(identity), "project_id" to JsonPrimitive(projectId),
            "created_at" to JsonPrimitive(now)) + old + values + mapOf("_record_type" to JsonPrimitive(recordType), "updated_at" to JsonPrimitive(now)))
        return CatalogRecord(entityType, identity, payload).also { rows[key] = it }
    }

    private fun character(candidate: CatalogCandidate) {
        val p = candidate.payload
        val create = candidate.kind == "character_create"
        val stateOnly = candidate.kind == "character_state_update"
        val identity = p.text(if (create) "client_id" else "id")
        val old = if (create) JsonObject(emptyMap()) else get("character", identity)
        val fields = mutableMapOf<String, JsonElement>()
        if (create) fields.putAll(mapOf("name" to p.getValue("name"), "current_version" to JsonPrimitive(1), "is_evolution_tracked" to JsonPrimitive(true)))
        if (!stateOnly) {
            listOf("personality", "background").forEach { if (p.text(it).isNotBlank()) fields[it] = JsonPrimitive(p.text(it).trim().let { value -> if (it == "personality") value.take(8000) else value }) }
            if (p.text("role_type").isNotBlank() && old.text("role_type") in setOf("", "other")) fields["role_type"] = p.getValue("role_type")
            if ("abilities" in p) fields["abilities"] = jsonStrings((old.strings("abilities") + p.strings("abilities")).distinct())
            if (p.obj("profile").isNotEmpty()) fields["profile"] = JsonObject(old.obj("profile") + p.obj("profile").filterValues { it != JsonNull && it != JsonPrimitive("") }.mapValues { (key, value) ->
                if (key == "reveal_chapter") JsonPrimitive(maxOf(1, value.jsonPrimitive.int)) else JsonPrimitive(value.jsonPrimitive.content.trim().take(4000))
            })
        }
        if (canAdvance(old)) {
            plan.contract.stateFields.forEach { field -> if (p.text(field).isNotBlank()) fields[field] = JsonPrimitive(p.text(field).trim().take(plan.contract.stateLimits.number(field, Int.MAX_VALUE))) }
        }
        val aliases = (old.strings("aliases") + p.strings("aliases")).map { it.trim().take(200) }.filter { it.isNotBlank() && it != p.text("name").ifBlank { old.text("name") } }.distinct()
        if (aliases.isNotEmpty()) fields["aliases"] = jsonStrings(aliases)
        val config = p.filterKeys { it in CONFIG_FIELDS }.filterValues { it != JsonNull && it != JsonPrimitive("") }.mapValues { (key, value) ->
            if (key == "catchphrases") value else JsonPrimitive(value.jsonPrimitive.content.trim().take(when (key) { "custom_system_prompt" -> 12000; "verbosity" -> 50; else -> 100 }))
        }
        val priorConfig = find("character_ai_config") { it.text("character_id") == identity }
        val changed = fields.any { (field, value) -> (!stateOnly || field in plan.contract.stateFields) && old[field] != value } ||
            config.any { (field, value) -> priorConfig?.payload?.get(field) != value }
        if (canAdvance(old)) {
            fields["last_seen_chapter_id"] = JsonPrimitive(chapterId)
            fields["last_updated_chapter_id"] = JsonPrimitive(chapterId)
        }
        if (!create && changed) fields["current_version"] = JsonPrimitive(old.number("current_version", 1) + 1)
        val saved = put("character", "character", identity, JsonObject(fields)).payload
        aliases.forEach { alias ->
            val prior = find("character_alias") { it.text("character_id") == identity && it.text("alias") == alias }
            put("character_alias", "character_alias", prior?.id ?: id("alias", identity, alias), buildJsonObject {
                put("character_id", identity); put("alias", alias); put("alias_type", "alias")
                put("source_chapter_id", chapterId); put("description", "建档识别到的角色别名/称呼：$alias")
            })
        }
        if (config.isNotEmpty()) {
            val defaults = if (priorConfig == null) mapOf("tone_style" to JsonPrimitive("neutral"), "verbosity" to JsonPrimitive("moderate"), "emotion_tendency" to JsonPrimitive("neutral")) else emptyMap()
            put("character_ai_config", "character_ai_config", priorConfig?.id ?: id("character_ai_config", identity),
                JsonObject(defaults + config + ("character_id" to JsonPrimitive(identity))))
        }
        if (create || changed) put("character", "character_version", id("character_version", identity, saved.number("current_version").toString()), buildJsonObject {
            put("character_id", identity); put("version_number", saved.number("current_version")); put("snapshot_data", characterSnapshot(saved))
            put("change_summary", "《${chapter.text("title")}》：${p.text("change_summary").ifBlank { if (stateOnly) "更新角色剧情状态" else "更新角色档案与写作约束" }}")
            put("source_chapter_id", chapterId)
        })
    }

    private fun canAdvance(entity: JsonObject): Boolean {
        val last = entity.text("last_updated_chapter_id")
        val later = ofType("chapter").firstOrNull { it.id == last }
        return last.isBlank() || last == chapterId || later == null || chapter.number("sort_order") >= later.payload.number("sort_order")
    }

    private fun world(candidate: CatalogCandidate) {
        val p = candidate.payload
        val create = candidate.kind == "worldbuilding_create"
        val identity = p.text(if (create) "client_id" else "id")
        val old = if (create) JsonObject(emptyMap()) else get("world_entry", identity)
        val content = listOf("content", "description", "evidence").map { p.text(it) }.first { it.isNotBlank() }
        val saved = put("world", "world_entry", identity, buildJsonObject {
            if (create) {
                put("title", p.text("title")); put("sort_order", ofType("world_entry").maxOfOrNull { it.payload.number("sort_order") }?.plus(1) ?: 0)
                put("first_seen_chapter_id", chapterId)
            }
            put("dimension", p.text("dimension").ifBlank { old.text("dimension") })
            put("content", if (create) content else mergeCatalogText(old.text("content"), content, chapter.text("title"), 12000))
            put("status", "active"); if (canAdvance(old)) put("last_updated_chapter_id", chapterId)
            p["confidence"]?.let { put("confidence", it) }
        }).payload
        val version = ofType("world_version").filter { it.payload.text("entry_id") == identity }.maxOfOrNull { it.payload.number("version_number") }?.plus(1) ?: 1
        put("world", "world_version", id("world_version", identity), buildJsonObject {
            put("entry_id", identity); put("version_number", version); put("snapshot_data", saved.toString())
            put("source_chapter_id", chapterId); put("change_summary", "《${chapter.text("title")}》：${p.text("change_summary").ifBlank { "更新设定档案" }}")
        })
        worldLink(identity, p.text("description").ifBlank { p.text("evidence") })
    }

    private fun timeline(candidate: CatalogCandidate, world: Boolean) {
        val p = candidate.payload
        val identity = p.text("id")
        get(if (world) "world_entry" else "character", identity)
        val type = if (world) "world_timeline" else "character_timeline"
        val key = if (world) "entry_id" else "character_id"
        val eventType = p.text("event_type").ifBlank { if (world) "fact_change" else "key_event" }
        val description = p.text("event_description").ifBlank { p.text("description") }
        require(description.isNotBlank()) { "时间线事件内容不能为空" }
        val previous = find(type) { it.text(key) == identity && it.text("chapter_id") == chapterId && it.text("event_type") == eventType && it.number("sort_order") == p.number("sort_order") }
        put("timeline", type, previous?.id ?: id(type, candidate.id), buildJsonObject {
            put(key, identity); put("chapter_id", chapterId); put("event_type", eventType)
            put("event_description", description); put("sort_order", p.number("sort_order"))
            put(if (world) "evidence" else "emotional_state_change", p.text(if (world) "evidence" else "emotional_state_change"))
        })
        if (world) worldLink(identity, description)
    }

    private fun relationship(p: JsonObject) {
        val from = plan.binding(p.text("source_name")).text("id")
        val to = plan.binding(p.text("target_name")).text("id")
        get("character", from); get("character", to)
        val existing = ofType("character_relationship").filter { it.payload.text("from") == from && it.payload.text("to") == to }.sortedBy { it.id }
        existing.drop(1).forEach { rows.remove(it.entityType to it.id) }
        val old = existing.firstOrNull()
        put("character_relation", "character_relationship", old?.id ?: catalogingId("cataloging_relationship", projectId, from, to), buildJsonObject {
            put("from", from); put("to", to); put("relationship_type", p.text("relationship_type"))
            put("description", mergeCatalogText(old?.payload?.text("description").orEmpty(), p.text("description").ifBlank { p.text("evidence") }, chapter.text("title"), 4000))
        })
    }

    private fun outline(candidate: CatalogCandidate) {
        val p = candidate.payload
        val type = p.text("node_type")
        val linked = chapter.text("outline_node_id")
        val existing = when {
            p.text("id").isNotBlank() -> ofType("outline_node").first { it.id == p.text("id") }
            type == "chapter" && linked.isNotBlank() -> ofType("outline_node").first { it.id == linked }
            type == "section" -> ofType("outline_node").singleOrNull { it.payload.text("source_chapter_id") == chapterId &&
                it.payload.text("node_type") == "section" && it.payload.obj("metadata").number("scene_number") == p.number("scene_number") }
            else -> null
        }
        val parent = when (type) {
            "volume" -> ""
            "section" -> chapter.text("outline_node_id").also { require(it.isNotBlank()) { "场景缺少本章大纲容器" } }
            else -> existing?.payload?.text("parent_id").orEmpty().ifBlank { p.text("parent_id").ifBlank { defaultVolume() } }
        }
        val identity = existing?.id ?: p.text("client_id").ifBlank { id("outline", candidate.id) }
        val actual = p.text("actual_summary").ifBlank { p.text("summary") }
        put("outline", "outline_node", identity, buildJsonObject {
            put("parent_id", parent.takeIf(String::isNotBlank)?.let(::JsonPrimitive) ?: JsonNull)
            put("node_type", type); put("title", p.text("title")); put("summary", actual); put("actual_summary", actual)
            put("planned_summary", existing?.payload?.text("planned_summary").orEmpty().ifBlank {
                p.text("planned_summary").ifBlank { existing?.payload?.text("summary").orEmpty() }
            })
            put("source_chapter_id", existing?.payload?.text("source_chapter_id").orEmpty().ifBlank { chapterId })
            put("cataloging_status", "cataloged"); put("status", if (type == "chapter") "completed" else p.text("status").ifBlank { "completed" })
            put("sort_order", existing?.payload?.number("sort_order") ?: nextOutlineOrder(parent))
            if (type == "section") put("metadata", JsonObject(existing?.payload?.obj("metadata").orEmpty() + p.filterKeys { it in plan.contract.sceneFields } + mapOf(
                "source" to JsonPrimitive("cataloging"), "source_chapter_id" to JsonPrimitive(chapterId),
            )))
        })
        outlineLinks(identity, p.strings("character_ids"), replace = type == "section")
        if (type == "chapter") put("chapter", "chapter", chapterId, buildJsonObject { put("outline_node_id", identity) })
    }

    private fun nextOutlineOrder(parent: String): Int = ofType("outline_node").filter { it.payload.text("parent_id") == parent }
        .maxOfOrNull { it.payload.number("sort_order") }?.plus(1) ?: 0

    private fun defaultVolume(): String {
        val volumes = ofType("outline_node").filter { it.payload.text("node_type") == "volume" }
        if (volumes.isNotEmpty()) return volumes.single { it.payload.obj("metadata").text("source") == "cataloging_default_volume" }.id
        return put("outline", "outline_node", catalogingId("cataloging_default_volume", projectId), buildJsonObject {
            put("node_type", "volume"); put("title", "第一卷"); put("parent_id", JsonNull)
            put("summary", "作品建档自动建立的默认分卷；可在大纲中重命名或调整章节范围。")
            put("status", "in_progress"); put("sort_order", nextOutlineOrder("")); put("source_chapter_id", chapterId)
            put("actual_summary", ""); put("planned_summary", ""); put("cataloging_status", "cataloged")
            put("metadata", buildJsonObject { put("source", "cataloging_default_volume"); put("start_chapter", 1) })
        }).id
    }

    fun outlineLinks(outlineId: String, ids: List<String>, replace: Boolean = false) {
        val old = get("outline_node", outlineId)
        val prior = if (replace) emptyList() else old.objects("linked_characters")
        val links = (prior + ids.map { identity ->
            val character = get("character", identity)
            buildJsonObject { put("id", identity); put("name", character.text("name")); put("role_type", character["role_type"] ?: JsonNull); put("role_in_scene", "建档关联") }
        }).distinctBy { it.text("id") }
        put("outline", "outline_node", outlineId, buildJsonObject { put("linked_characters", JsonArray(links)) })
    }

    private fun chapterLinks(p: JsonObject) {
        val links = p.objects("characters").map { value ->
            val identity = plan.binding(value.text("name")).text("id")
            get("character", identity)
            buildJsonObject { put("character_id", identity); put("appearance_type", value.text("appearance_type")); put("description", p.text("description").ifBlank { "关联" }) }
        }
        val world = p.strings("worldbuilding_titles").map { plan.binding(it, true).text("id") }
        put("chapter", "chapter", chapterId, buildJsonObject {
            put("characters", JsonArray(links)); put("worldbuilding_ids", jsonStrings(world))
        })
        outlineLinks(chapter.text("outline_node_id"), links.map { it.text("character_id") })
        world.forEach { worldLink(it, p.text("description")) }
        fact("chapter_element_links", p)
    }

    private fun worldLink(identity: String, @Suppress("UNUSED_PARAMETER") description: String) {
        val ids = (chapter.strings("worldbuilding_ids") + identity).distinct()
        put("chapter", "chapter", chapterId, buildJsonObject { put("worldbuilding_ids", jsonStrings(ids)) })
    }

    fun fact(type: String, payload: JsonObject) {
        put("cataloging_fact", "cataloging_fact", id("fact", type), buildJsonObject {
            put("chapter_id", chapterId); put("fact_type", type); put("status", "active"); put("raw_payload", payload.toString())
        })
    }

    private fun snapshot(): String {
        val current = find("chapter_snapshot") { it.text("chapter_id") == chapterId &&
            it.number("version_number") == chapter.number("current_version") && it.text("content") == chapter.text("content") }
        if (current != null) return current.id
        return put("chapter_version", "chapter_snapshot", id("snapshot"), buildJsonObject {
            put("chapter_id", chapterId); put("version_number", chapter.number("current_version")); put("content", chapter.text("content"))
            put("word_count", chapter.number("word_count")); put("trigger_type", "cataloging")
        }).id
    }

    private fun reconcileScenes() {
        val count = plan.candidates.single { it.kind == "chapter_summary" }.payload.strings("scenes").size
        ofType("outline_node").filter { it.payload.text("source_chapter_id") == chapterId && it.payload.text("node_type") == "section" &&
            it.payload.text("cataloging_status") == "cataloged" && it.payload.obj("metadata").number("scene_number") > count }.forEach {
            put("outline", "outline_node", it.id, buildJsonObject { put("cataloging_status", "superseded") })
        }
    }

    companion object { val CONFIG_FIELDS = setOf("tone_style", "catchphrases", "verbosity", "emotion_tendency", "custom_system_prompt") }
}

/** Exact text-fragment deduplication, matching the PC archive merger; no entity/intent inference. */
internal fun mergeCatalogText(old: String, incoming: String, chapterTitle: String, limit: Int): String {
    val previous = old.trim()
    val next = incoming.trim()
    if (previous.isEmpty()) return next.take(limit)
    if (next.isEmpty()) return previous.take(limit)
    val marker = "《$chapterTitle》："
    val start = previous.indexOf(marker)
    val prefix = if (start >= 0) previous.substring(0, start).trimEnd() else previous
    val end = if (start >= 0) previous.indexOf("\n\n《", start + marker.length) else -1
    val suffix = if (end >= 0) previous.substring(end).trim() else ""
    val base = listOf(prefix, suffix).filter(String::isNotEmpty).joinToString("\n\n")
    if (start < 0 && next in previous) return previous.take(limit)
    fun strip(value: String) = value.trim().replace(Regex("^《[^》]{1,200}》[:：]\\s*"), "")
    fun parts(value: String) = value.split(Regex("(?<=[。！？!?；;])|[\\r\\n]+" )).map(::strip).filter(String::isNotBlank)
    fun key(value: String) = value.lowercase().replace(Regex("[^\\p{L}\\p{N}]+"), "")
    val seen = parts(base).map(::key).toMutableSet()
    val delta = buildString {
        parts(next).filter { key(it).isNotBlank() && seen.add(key(it)) }.forEach {
            if (isNotEmpty() && last() !in "。！？!?；;" && it.first() !in "，,。！？!?；;") append('；')
            append(it)
        }
    }
    return listOf(prefix, if (delta.isEmpty()) "" else marker + delta, suffix).filter(String::isNotEmpty).joinToString("\n\n").take(limit)
}
