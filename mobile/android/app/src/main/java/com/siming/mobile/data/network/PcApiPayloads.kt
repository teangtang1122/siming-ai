package com.siming.mobile.data.network

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/**
 * Converts rich local replicas into the request schemas published by the PC.
 *
 * A replica may contain read-only response fields, derived metrics, historical
 * metadata, or compatibility aliases. None of those fields may leak into a PC
 * write request or an offline sync mutation.
 */
internal object PcApiPayloads {
    private val coreAuthoringTypes = setOf("project", "chapter", "outline", "character", "world")

    fun authoring(entityType: String, source: JsonObject, create: Boolean): JsonObject {
        require(entityType in coreAuthoringTypes) { "PC API 暂不支持资料类型：$entityType" }
        val values = canonicalFields(entityType, source)
        normalizeCore(entityType, source, values)
        if (!create && entityType == "chapter") {
            values["trigger_type"] = JsonPrimitive("manual_save")
        }
        if (create && entityType == "character") {
            values.remove("change_summary")
        }
        if (create) addCreateDefaults(entityType, values)
        return JsonObject(values)
    }

    /**
     * POST body for the PC narrative-governance `/items` endpoint.
     * Lifecycle status is deliberately excluded; explicit user transitions use
     * the canonical PATCH endpoint instead of masquerading as an upsert.
     */
    fun governanceContent(
        entityType: String,
        source: JsonObject,
        entityId: String,
        create: Boolean,
    ): JsonObject {
        val allowed = governanceContentKeys(entityType)
        val data = buildJsonObject {
            allowed.forEach { key -> source[key]?.let { put(key, it) } }
            if (!create) put("item_id", entityId)
            if (source["source"] == null) put("source", "manual")
            if (source["dedupe_key"] == null) put("dedupe_key", "mobile-$entityId")
        }
        return buildJsonObject {
            put("type", governanceItemType(entityType))
            put("data", data)
        }
    }

    /** Canonical user lifecycle PATCH payload, or null when no status exists. */
    fun governanceStatus(entityType: String, source: JsonObject): JsonObject? {
        governanceItemType(entityType)
        val status = (source["status"] as? JsonPrimitive)?.contentOrNull.orEmpty().trim()
        if (status.isBlank()) return null
        return buildJsonObject {
            GOVERNANCE_STATUS_KEYS.forEach { key -> source[key]?.let { put(key, it) } }
        }
    }

    fun governanceItemType(entityType: String): String = when (entityType) {
        "foreshadowing" -> "foreshadowing"
        "governance" -> "narrative_debt"
        else -> error("PC 叙事治理 API 暂不支持资料类型：$entityType")
    }

    /**
     * Minimal public mutation used by the revisioned offline sync protocol.
     * The Room replica remains rich; only PC-writable fields enter the outbox.
     */
    fun syncMutation(
        entityType: String,
        source: JsonObject,
        projectId: String,
        entityId: String,
    ): JsonObject {
        val values = if (entityType in coreAuthoringTypes) {
            canonicalFields(entityType, source).also { normalizeCore(entityType, source, it) }
        } else {
            linkedMapOf<String, JsonElement>().apply {
                PcAuthoringContract.writableKeys(entityType).forEach { key ->
                    source[key]?.let { put(key, it) }
                }
            }
        }
        return buildJsonObject {
            put("_record_type", recordType(entityType))
            put("id", entityId)
            if (entityType != "project") put("project_id", projectId)
            values.forEach { (key, value) -> put(key, value) }
            if (entityType in GOVERNANCE_TYPES) {
                if (source["source"] == null) put("source", "manual")
                if (source["dedupe_key"] == null) put("dedupe_key", "mobile-$entityId")
            }
        }
    }

    private fun canonicalFields(
        entityType: String,
        source: JsonObject,
    ): LinkedHashMap<String, JsonElement> = linkedMapOf<String, JsonElement>().apply {
        PcAuthoringContract.writableKeys(entityType).forEach { key ->
            source[key]?.let { put(key, it) }
        }
    }

    private fun normalizeCore(
        entityType: String,
        source: JsonObject,
        values: MutableMap<String, JsonElement>,
    ) {
        when (entityType) {
            "project" -> normalizeProject(values)
            "outline" -> normalizeOutline(source, values)
            "character" -> normalizeCharacter(values)
            "world" -> {
                val dimension = (values["dimension"] as? JsonPrimitive)?.content.orEmpty()
                if (dimension !in WORLD_DIMENSIONS) values["dimension"] = JsonPrimitive("culture")
            }
        }
    }

    private fun normalizeProject(values: MutableMap<String, JsonElement>) {
        val tags = values["tags"]
        if (tags is JsonPrimitive) {
            val raw = tags.content.trim()
            values["tags"] = runCatching { Json.parseToJsonElement(raw) as? JsonArray }.getOrNull()
                ?: stringArray(raw)
        }
        val shortSentences = values["short_sentences"]
        if (shortSentences is JsonPrimitive && shortSentences.booleanOrNull == null) {
            values["short_sentences"] = JsonPrimitive(parseBoolean(shortSentences.content))
        }
    }

    private fun normalizeOutline(
        source: JsonObject,
        values: MutableMap<String, JsonElement>,
    ) {
        if (values["metadata"] == null) {
            source["metadata_json"]?.let { values["metadata"] = it }
        }
        if (values["characters"] == null) {
            val linked = source["linked_characters"] as? JsonArray
            if (linked != null) {
                values["characters"] = JsonArray(
                    linked.mapNotNull { element ->
                        val item = element as? JsonObject ?: return@mapNotNull null
                        val id = ((item["character_id"] ?: item["id"]) as? JsonPrimitive)
                            ?.contentOrNull
                            .orEmpty()
                        if (id.isBlank()) return@mapNotNull null
                        buildJsonObject {
                            put("character_id", id)
                            item["role_in_scene"]?.let { put("role_in_scene", it) }
                        }
                    },
                )
            }
        }
        values.normalizeJsonArray("characters", "大纲角色关联")
        values.normalizeJsonObject("metadata", "大纲 metadata")
        values.normalizeStringArray("character_ids")
    }

    private fun normalizeCharacter(values: MutableMap<String, JsonElement>) {
        values.normalizeStringArray("abilities")
        values.normalizeStringArray("aliases")
        values.normalizeJsonObject("profile", "角色稳定写作档案")

        val tracked = values["is_evolution_tracked"]
        if (tracked is JsonPrimitive && tracked.booleanOrNull == null) {
            values["is_evolution_tracked"] = JsonPrimitive(parseBoolean(tracked.content))
        }
    }

    private fun MutableMap<String, JsonElement>.normalizeStringArray(key: String) {
        val value = get(key) ?: return
        if (value is JsonNull || value is JsonArray) return
        if (value is JsonPrimitive) {
            val raw = value.content.trim()
            put(
                key,
                runCatching { Json.parseToJsonElement(raw) as? JsonArray }.getOrNull()
                    ?: stringArray(raw),
            )
        }
    }

    private fun MutableMap<String, JsonElement>.normalizeJsonObject(key: String, label: String) {
        val value = get(key) ?: return
        if (value is JsonNull || value is JsonObject) return
        if (value is JsonPrimitive) {
            val raw = value.content.trim()
            put(
                key,
                if (raw.isBlank()) JsonObject(emptyMap())
                else runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
                    ?: error("$label 必须是 JSON 对象"),
            )
        }
    }

    private fun MutableMap<String, JsonElement>.normalizeJsonArray(key: String, label: String) {
        val value = get(key) ?: return
        if (value is JsonNull || value is JsonArray) return
        if (value is JsonPrimitive) {
            val raw = value.content.trim()
            put(
                key,
                if (raw.isBlank()) JsonArray(emptyList())
                else runCatching { Json.parseToJsonElement(raw) as? JsonArray }.getOrNull()
                    ?: error("$label 必须是 JSON 数组"),
            )
        }
    }

    private fun stringArray(raw: String): JsonArray = JsonArray(
        raw.split('\n', ',', '，', '、')
            .map(String::trim)
            .filter(String::isNotBlank)
            .distinct()
            .map(::JsonPrimitive),
    )

    private fun parseBoolean(raw: String): Boolean =
        raw.trim().lowercase() !in setOf("0", "false", "no", "off", "否")

    private fun governanceContentKeys(entityType: String): Set<String> = when (entityType) {
        "foreshadowing" -> setOf(
            "title", "description", "importance", "storyline",
            "source_chapter_id", "target_chapter_id", "target_chapter_number",
            "resolved_chapter_id", "evidence", "resolution_note", "resolution_evidence",
            "dedupe_key", "source",
        )
        "governance" -> setOf(
            "debt_type", "title", "description", "priority",
            "source_chapter_id", "target_chapter_id", "target_chapter_number",
            "resolved_chapter_id", "linked_foreshadowing_id", "linked_causal_edge_id",
            "evidence", "resolution_note", "resolution_evidence", "dedupe_key", "source",
        )
        else -> error("PC 叙事治理 API 暂不支持资料类型：$entityType")
    }

    private fun recordType(entityType: String): String = when (entityType) {
        "project" -> "project"
        "chapter" -> "chapter"
        "outline" -> "outline_node"
        "character" -> "character"
        "world" -> "world_entry"
        "foreshadowing" -> "foreshadowing"
        "governance" -> "narrative_debt"
        else -> error("暂不支持的资料类型：$entityType")
    }

    private fun addCreateDefaults(entityType: String, values: MutableMap<String, JsonElement>) {
        when (entityType) {
            "project" -> {
                values.ensureText("title", "未命名作品")
                values.putIfAbsent("narrative_perspective", JsonPrimitive("third_person"))
                values.putIfAbsent("writing_style", JsonPrimitive("natural"))
                values.putIfAbsent("short_sentences", JsonPrimitive(false))
                values.putIfAbsent("daily_word_goal", JsonPrimitive(6000))
            }
            "chapter" -> {
                values.ensureText("title", "未命名章节")
                values.putIfAbsent("content", JsonPrimitive(""))
            }
            "outline" -> {
                values.ensureText("title", "未命名大纲")
                values.putIfAbsent("node_type", JsonPrimitive("chapter"))
                values.putIfAbsent("status", JsonPrimitive("pending"))
                values.putIfAbsent("sort_order", JsonPrimitive(0))
                values.putIfAbsent("characters", JsonArray(emptyList()))
                values.putIfAbsent("metadata", JsonObject(emptyMap()))
            }
            "character" -> {
                values.ensureText("name", "未命名角色")
                values.putIfAbsent("abilities", JsonArray(emptyList()))
                values.putIfAbsent("aliases", JsonArray(emptyList()))
                values.putIfAbsent("profile", JsonObject(emptyMap()))
                values.putIfAbsent("is_evolution_tracked", JsonPrimitive(true))
            }
            "world" -> {
                values.ensureText("title", "未命名设定")
                values.ensureText("content", "待补充")
                values.putIfAbsent("sort_order", JsonPrimitive(0))
            }
        }
    }

    private fun MutableMap<String, JsonElement>.ensureText(key: String, fallback: String) {
        val current = (get(key) as? JsonPrimitive)?.content.orEmpty()
        if (current.isBlank()) put(key, JsonPrimitive(fallback))
    }

    private val GOVERNANCE_TYPES = setOf("foreshadowing", "governance")
    private val GOVERNANCE_STATUS_KEYS = setOf(
        "status",
        "target_chapter_id",
        "target_chapter_number",
        "resolved_chapter_id",
        "evidence",
        "resolution_note",
        "resolution_evidence",
        "verification_note",
        "closed_by",
    )
    private val WORLD_DIMENSIONS = setOf(
        "geography",
        "history",
        "factions",
        "power_system",
        "races",
        "culture",
    )
}
