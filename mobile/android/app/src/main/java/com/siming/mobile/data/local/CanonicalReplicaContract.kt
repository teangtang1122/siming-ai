package com.siming.mobile.data.local

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Maps the coarse sync entity type to the canonical PC record shown in the
 * authoring UI. The sync protocol deliberately carries version/timeline rows
 * under the same coarse entity type; those rows stay in Room for AI/context
 * use, but must never be rendered as primary authoring cards.
 */
private val primaryRecordTypes = mapOf(
    "project" to setOf("project"),
    "chapter" to setOf("chapter"),
    "outline" to setOf("outline_node"),
    "character" to setOf("character"),
    "world" to setOf("world_entry"),
    "foreshadowing" to setOf("foreshadowing"),
    // The mobile governance editor currently edits the same narrative-debt
    // record as the PC governance panel. Checkpoints/metrics/reviews remain
    // synced for context but are not editable through this generic card list.
    "governance" to setOf("narrative_debt"),
)

internal fun primaryAuthoringRecords(
    entityType: String,
    records: List<ReplicaEntity>,
): List<ReplicaEntity> {
    val accepted = primaryRecordTypes[entityType] ?: return records.filter { it.operation == "upsert" }
    return records.filter { record ->
        if (record.operation != "upsert") return@filter false
        val recordType = record.recordType()
        // Replicas created by very old Android builds did not have
        // _record_type. Treat them as the primary row for compatibility.
        recordType == null || recordType in accepted
    }
}

internal fun ReplicaEntity.recordType(): String? {
    val raw = payloadJson ?: return null
    val payload = runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull() ?: return null
    return payload["_record_type"]?.jsonPrimitive?.contentOrNull?.takeIf(String::isNotBlank)
}
