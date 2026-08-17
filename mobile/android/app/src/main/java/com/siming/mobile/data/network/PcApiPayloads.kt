package com.siming.mobile.data.network

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Converts the local replica shape into the request schemas published by the
 * desktop API. Replica-only identifiers, derived fields, and database JSON
 * strings must never leak into canonical authoring requests.
 */
internal object PcApiPayloads {
    private val fields = mapOf(
        "project" to setOf(
            "title",
            "description",
            "tags",
            "narrative_perspective",
            "writing_style",
            "forbidden_sentence_patterns",
            "rhetoric_guidelines",
            "short_sentences",
            "custom_style_prompt",
            "daily_word_goal",
        ),
        "chapter" to setOf("title", "outline_node_id", "content", "context_manifest_id"),
        "outline" to setOf(
            "parent_id",
            "node_type",
            "title",
            "summary",
            "status",
            "sort_order",
            "character_ids",
            "characters",
            "metadata",
        ),
        "character" to setOf(
            "name",
            "appearance",
            "role_type",
            "personality",
            "background",
            "abilities",
            "aliases",
            "age",
            "life_status",
            "current_location",
            "realm_or_level",
            "physical_state",
            "mental_state",
            "current_goal",
            "active_conflict",
            "abilities_state",
            "items_or_assets",
            "profile",
            "is_evolution_tracked",
            "change_summary",
        ),
        "world" to setOf("dimension", "title", "content", "sort_order"),
    )

    fun authoring(entityType: String, source: JsonObject, create: Boolean): JsonObject {
        val allowed = fields[entityType] ?: error("PC API 暂不支持资料类型：$entityType")
        val values = linkedMapOf<String, JsonElement>()
        allowed.forEach { key -> source[key]?.let { values[key] = it } }

        if (entityType == "project") normalizeProject(values)
        if (entityType == "character") normalizeCharacter(values)
        if (entityType == "world") {
            val dimension = (values["dimension"] as? JsonPrimitive)?.content.orEmpty()
            if (dimension !in WORLD_DIMENSIONS) values["dimension"] = JsonPrimitive("culture")
        }
        if (!create && entityType == "chapter") {
            values["trigger_type"] = JsonPrimitive("manual_save")
        }
        if (create && entityType == "character") {
            values.remove("change_summary")
        }
        if (create) addCreateDefaults(entityType, values)
        return JsonObject(values)
    }

    fun governance(
        entityType: String,
        source: JsonObject,
        entityId: String,
        create: Boolean,
    ): JsonObject {
        val itemType = when (entityType) {
            "foreshadowing" -> "foreshadowing"
            "governance" -> "narrative_debt"
            else -> error("PC 叙事治理 API 暂不支持资料类型：$entityType")
        }
        val allowed = if (entityType == "foreshadowing") {
            setOf("title", "description", "status", "importance", "storyline", "dedupe_key", "source")
        } else {
            setOf("title", "description", "status", "priority", "dedupe_key", "source", "debt_type")
        }
        val data = buildJsonObject {
            allowed.forEach { key -> source[key]?.let { put(key, it) } }
            if (!create) put("item_id", entityId)
        }
        return buildJsonObject {
            put("type", itemType)
            put("data", data)
        }
    }

    private fun normalizeProject(values: MutableMap<String, JsonElement>) {
        val tags = values["tags"]
        if (tags is JsonPrimitive) {
            val raw = tags.content.trim()
            values["tags"] = runCatching { Json.parseToJsonElement(raw) as? JsonArray }.getOrNull()
                ?: stringArray(raw)
        }
    }

    private fun normalizeCharacter(values: MutableMap<String, JsonElement>) {
        values.normalizeStringArray("abilities")
        values.normalizeStringArray("aliases")

        val profile = values["profile"]
        if (profile is JsonPrimitive) {
            val raw = profile.content.trim()
            values["profile"] = if (raw.isBlank()) {
                JsonObject(emptyMap())
            } else {
                runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
                    ?: error("角色稳定写作档案必须是 JSON 对象")
            }
        }

        val tracked = values["is_evolution_tracked"]
        if (tracked is JsonPrimitive && tracked.booleanOrNull == null) {
            values["is_evolution_tracked"] = JsonPrimitive(
                tracked.content.trim().lowercase() !in setOf("0", "false", "no", "off", "否"),
            )
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

    private fun stringArray(raw: String): JsonArray = JsonArray(
        raw.split('\n', ',', '，', '、')
            .map(String::trim)
            .filter(String::isNotBlank)
            .distinct()
            .map(::JsonPrimitive),
    )

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

    private val WORLD_DIMENSIONS = setOf(
        "geography",
        "history",
        "factions",
        "power_system",
        "races",
        "culture",
    )
}
