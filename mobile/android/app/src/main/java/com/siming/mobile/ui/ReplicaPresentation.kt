package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

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
    record.text("age").takeIf(String::isNotBlank)?.let { "$it岁" },
    record.text("realm_or_level").takeIf(String::isNotBlank),
    record.text("current_location").takeIf(String::isNotBlank),
    record.text("current_goal").takeIf(String::isNotBlank),
).filterNotNull().joinToString(" · ")

internal val replicaPresentationJson = Json { ignoreUnknownKeys = true }
