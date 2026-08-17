package com.siming.mobile.data.local

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Maps the coarse sync entity type to the canonical PC record shown in the
 * authoring UI and generic mobile authoring agent. Version/history rows remain
 * stored in Room, but they are not interchangeable with their parent PC entity.
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
    // stored for sync/history but are not generic authoring cards.
    "governance" to setOf("narrative_debt"),
)

private val identityFields = mapOf(
    "project" to "title",
    "chapter" to "title",
    "outline" to "title",
    "character" to "name",
    "world" to "title",
    "foreshadowing" to "title",
    "governance" to "title",
)

internal fun primaryAuthoringRecords(
    entityType: String,
    records: List<ReplicaEntity>,
): List<ReplicaEntity> = records.filter { record ->
    isPrimaryAuthoringRecord(entityType, record)
}

internal fun primaryAuthoringSnapshot(records: List<ReplicaEntity>): List<ReplicaEntity> =
    records.filter { record ->
        if (record.entityType in primaryRecordTypes) {
            isPrimaryAuthoringRecord(record.entityType, record)
        } else {
            record.operation == "upsert"
        }
    }

private fun isPrimaryAuthoringRecord(entityType: String, record: ReplicaEntity): Boolean {
    if (record.operation != "upsert") return false
    val accepted = primaryRecordTypes[entityType] ?: return true
    val payload = record.payloadObject() ?: return false
    val recordType = (payload["_record_type"] as? JsonPrimitive)
        ?.contentOrNull
        ?.takeIf(String::isNotBlank)
    // Replicas created by very old Android builds did not have _record_type.
    // Keep well-formed legacy primary rows, but never render malformed leftovers
    // as "unnamed" PC entities.
    val typeMatches = recordType == null || recordType in accepted
    val identityField = identityFields[entityType]
    val identity = identityField?.let { payload[it] as? JsonPrimitive }?.contentOrNull
    val hasIdentity = identityField == null || !identity.isNullOrBlank()
    return typeMatches && hasIdentity
}

internal fun ReplicaEntity.recordType(): String? =
    (payloadObject()?.get("_record_type") as? JsonPrimitive)
        ?.contentOrNull
        ?.takeIf(String::isNotBlank)

private fun ReplicaEntity.payloadObject(): JsonObject? {
    val raw = payloadJson ?: return null
    return runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
}
