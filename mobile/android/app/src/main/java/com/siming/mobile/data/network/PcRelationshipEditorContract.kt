package com.siming.mobile.data.network

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** A directed PC relationship kept intact while editing from either endpoint. */
internal data class PcEditableRelationship(
    val sourceId: String,
    val targetId: String,
    val counterpartName: String,
    val relationshipType: String,
    val description: String,
) {
    fun counterpartId(currentCharacterId: String): String = when (currentCharacterId) {
        sourceId -> targetId
        targetId -> sourceId
        else -> ""
    }

    fun directionLabel(currentCharacterId: String): String = when (currentCharacterId) {
        sourceId -> "方向：当前角色 → ${counterpartName.ifBlank { targetId }}"
        targetId -> "方向：${counterpartName.ifBlank { sourceId }} → 当前角色"
        else -> "方向：该关系未连接当前角色"
    }
}

internal fun pcEditableRelationships(
    network: JsonObject,
    currentCharacterId: String,
): List<PcEditableRelationship> {
    val names = network.arrayObjects("nodes").associate { it.string("id") to it.string("name") }
    return network.arrayObjects("edges").mapNotNull { edge ->
        val sourceId = edge.string("from")
        val targetId = edge.string("to")
        val counterpartId = when (currentCharacterId) {
            sourceId -> targetId
            targetId -> sourceId
            else -> return@mapNotNull null
        }
        PcEditableRelationship(
            sourceId = sourceId,
            targetId = targetId,
            counterpartName = names[counterpartId].orEmpty(),
            relationshipType = edge.string("relationship_type"),
            description = edge.string("description"),
        )
    }
}

internal fun pcNewRelationship(
    currentCharacterId: String,
    targetCharacterId: String,
    targetName: String,
): PcEditableRelationship = PcEditableRelationship(
    sourceId = currentCharacterId,
    targetId = targetCharacterId,
    counterpartName = targetName,
    relationshipType = "related",
    description = "",
)

internal fun pcRelationshipMutationPayload(relation: PcEditableRelationship): JsonObject =
    buildJsonObject {
        put("source_character_id", relation.sourceId)
        put("target_character_id", relation.targetId)
        put("relationship_type", relation.relationshipType.ifBlank { "related" })
        if (relation.description.isNotBlank()) put("description", relation.description)
    }

private fun JsonObject.arrayObjects(name: String): List<JsonObject> =
    (get(name) as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
