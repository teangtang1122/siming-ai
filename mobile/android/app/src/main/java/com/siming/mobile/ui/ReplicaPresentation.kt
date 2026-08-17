package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.PcAuthoringContract
import com.siming.mobile.data.network.PcFieldKind
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

private val formJson = Json { ignoreUnknownKeys = true }

/**
 * Convert canonical PC JSON values into editor-friendly text without changing
 * the stored replica. Response aliases are normalized only for presentation.
 */
internal fun ReplicaEntity.formText(name: String): String {
    val payload = payload() ?: return ""
    val value = when {
        name == "metadata" && payload[name] == null -> payload["metadata_json"]
        name == "characters" && payload[name] == null -> linkedCharactersForForm(payload)
        else -> payload[name]
    }
    if (name == "tags" && value is JsonPrimitive) {
        val parsed = runCatching { formJson.parseToJsonElement(value.content) as? JsonArray }.getOrNull()
        if (parsed != null) return parsed.joinToString("\n") { item ->
            (item as? JsonPrimitive)?.contentOrNull ?: item.toString()
        }
    }
    return when (value) {
        null, JsonNull -> ""
        is JsonPrimitive -> value.contentOrNull.orEmpty()
        is JsonArray -> value.joinToString("\n") { item ->
            (item as? JsonPrimitive)?.contentOrNull ?: item.toString()
        }
        is JsonObject -> value.toString()
        else -> value.toString()
    }
}

internal fun canonicalCharacterSummary(record: ReplicaEntity): String = listOf(
    record.text("role_type").takeIf(String::isNotBlank),
    record.text("age").takeIf(String::isNotBlank),
    record.text("realm_or_level").takeIf(String::isNotBlank),
    record.text("current_location").takeIf(String::isNotBlank),
    record.text("current_goal").takeIf(String::isNotBlank),
).filterNotNull().joinToString(" · ")

/** Convert the generic Android form back into the exact PC request value types. */
internal fun canonicalFormValues(
    entityType: String,
    values: Map<String, String>,
): MutableMap<String, Any?> = linkedMapOf<String, Any?>().apply {
    PcAuthoringContract.mobileFields(entityType).forEach { field ->
        val raw = values[field.key].orEmpty()
        this[field.key] = when (field.kind) {
            PcFieldKind.Text, PcFieldKind.Multiline -> raw
            PcFieldKind.NullableText -> raw.trim().takeIf(String::isNotBlank)
            PcFieldKind.Integer -> raw.toIntOrNull() ?: 0
            PcFieldKind.NullableInteger -> raw.trim().takeIf(String::isNotBlank)?.toIntOrNull()
            PcFieldKind.Boolean -> parseBoolean(raw)
            PcFieldKind.StringArray -> stringArray(raw)
            PcFieldKind.JsonObject -> parseObject(raw, field.key)
            PcFieldKind.JsonArray -> parseArray(raw, field.key)
        }
    }
}

private fun linkedCharactersForForm(payload: JsonObject): JsonArray? {
    val linked = payload["linked_characters"] as? JsonArray ?: return null
    return JsonArray(
        linked.mapNotNull { element ->
            val item = element as? JsonObject ?: return@mapNotNull null
            val id = ((item["character_id"] ?: item["id"]) as? JsonPrimitive)
                ?.contentOrNull
                .orEmpty()
            if (id.isBlank()) return@mapNotNull null
            JsonObject(
                buildMap {
                    put("character_id", JsonPrimitive(id))
                    item["role_in_scene"]?.let { put("role_in_scene", it) }
                },
            )
        },
    )
}

private fun parseBoolean(raw: String): Boolean =
    raw.trim().lowercase() !in setOf("0", "false", "no", "off", "否")

private fun parseObject(raw: String, key: String): JsonObject {
    if (raw.isBlank()) return JsonObject(emptyMap())
    return runCatching { formJson.parseToJsonElement(raw) as? JsonObject }.getOrNull()
        ?: error("$key 必须是 JSON 对象")
}

private fun parseArray(raw: String, key: String): JsonArray {
    if (raw.isBlank()) return JsonArray(emptyList())
    return runCatching { formJson.parseToJsonElement(raw) as? JsonArray }.getOrNull()
        ?: error("$key 必须是 JSON 数组")
}

private fun stringArray(raw: String): JsonArray = JsonArray(
    raw.split('\n', ',', '，', '、')
        .map(String::trim)
        .filter(String::isNotBlank)
        .distinct()
        .map(::JsonPrimitive),
)
