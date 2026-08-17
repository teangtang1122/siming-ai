package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Convert canonical PC JSON values into an editor-friendly string without
 * changing their stored/synced types. Lists are one item per line and objects
 * remain JSON so the Android form never rewrites arrays/objects as Kotlin
 * toString() output.
 */
internal fun ReplicaEntity.formText(name: String): String = when (val value = payload()?.get(name)) {
    null, JsonNull -> ""
    is JsonPrimitive -> value.contentOrNull.orEmpty()
    is JsonArray -> value.joinToString("\n") { item ->
        (item as? JsonPrimitive)?.contentOrNull ?: item.toString()
    }
    is JsonObject -> value.toString()
    else -> value.toString()
}

internal fun canonicalCharacterSummary(record: ReplicaEntity): String = listOf(
    record.text("role_type").takeIf(String::isNotBlank),
    record.text("age").takeIf(String::isNotBlank),
    record.text("realm_or_level").takeIf(String::isNotBlank),
    record.text("current_location").takeIf(String::isNotBlank),
    record.text("current_goal").takeIf(String::isNotBlank),
).filterNotNull().joinToString(" · ")

internal fun canonicalCharacterFormValues(values: Map<String, String>): MutableMap<String, Any?> =
    linkedMapOf<String, Any?>().apply {
        values.forEach { (key, value) -> this[key] = value }
        this["abilities"] = stringArray(values["abilities"].orEmpty())
        this["aliases"] = stringArray(values["aliases"].orEmpty())
        this["is_evolution_tracked"] = values["is_evolution_tracked"]
            .orEmpty()
            .trim()
            .lowercase()
            .let { it !in setOf("0", "false", "no", "off", "否") }
    }

private fun stringArray(raw: String): JsonArray = JsonArray(
    raw.split('\n', ',', '，', '、')
        .map(String::trim)
        .filter(String::isNotBlank)
        .distinct()
        .map(::JsonPrimitive),
)
