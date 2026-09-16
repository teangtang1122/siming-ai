package com.siming.mobile.data.authoring

import com.siming.mobile.data.cataloging.catalogingId
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDao
import kotlinx.serialization.json.*

internal fun JsonObject.text(key: String) = (get(key) as? JsonPrimitive)?.contentOrNull.orEmpty()
internal fun JsonObject.number(key: String, default: Int = 0) = (get(key) as? JsonPrimitive)?.intOrNull ?: default
internal fun ReplicaEntity.payload() = Json.parseToJsonElement(requireNotNull(payloadJson)).jsonObject
internal fun JsonObject.parsedObject(key: String): JsonObject = when (val value = get(key)) {
    is JsonObject -> value
    is JsonPrimitive -> Json.parseToJsonElement(value.content).jsonObject
    else -> JsonObject(emptyMap())
}
internal fun items(rows: List<JsonObject>) = buildJsonObject { put("items", JsonArray(rows)); put("total", rows.size) }

/** Reads the same record types and ownership boundaries as the desktop domain. */
internal class LocalAuthoringRead(private val dao: SimingDao) {
    suspend fun requireEntity(projectId: String, type: String, id: String): JsonObject {
        val row = dao.entity(ReplicaEntity.key(projectId, type, id))
        require(row != null && row.operation == "upsert") { "资料不存在或不属于当前作品" }
        val payload = row.payload()
        require(row.projectId == projectId && row.entityId == id && payload.text("id") == id &&
            payload.text("project_id").let { it.isEmpty() || it == projectId }) { "资料归属或 ID 不一致" }
        val expected = when (type) {
            "world" -> "world_entry"
            "outline" -> "outline_node"
            "character_relation" -> "character_relationship"
            "world_relation" -> "world_relationship"
            "governance" -> "narrative_debt"
            else -> type
        }
        require(payload.text("_record_type").ifBlank { expected } == expected) { "资料类型不匹配" }
        return payload
    }

    suspend fun records(projectId: String, recordType: String) = dao.projectSnapshot(projectId)
        .filter { row -> com.siming.mobile.data.cataloging.CatalogRecord(row.entityType, row.entityId, row.payload()).recordType == recordType }
        .map(ReplicaEntity::payload)

    suspend fun history(projectId: String, ownerType: String, ownerId: String, recordType: String, ownerKey: String): JsonObject {
        requireEntity(projectId, ownerType, ownerId)
        return items(records(projectId, recordType).filter { it.text(ownerKey) == ownerId }
            .sortedWith(compareByDescending<JsonObject> { it.number("version_number") }.thenByDescending { it.text("created_at") }))
    }

    suspend fun snapshot(projectId: String, chapterId: String, snapshotId: String): JsonObject {
        requireEntity(projectId, "chapter", chapterId)
        return records(projectId, "chapter_snapshot").firstOrNull { it.text("id") == snapshotId && it.text("chapter_id") == chapterId }
            ?: error("章节快照不存在或不属于当前章节")
    }

    suspend fun characterVersion(projectId: String, characterId: String, versionId: String): JsonObject {
        requireEntity(projectId, "character", characterId)
        val version = records(projectId, "character_version").firstOrNull { it.text("id") == versionId && it.text("character_id") == characterId }
            ?: error("角色版本不存在或不属于当前角色")
        return JsonObject(version + ("snapshot_data" to version.parsedObject("snapshot_data")))
    }

    suspend fun worldTimeline(projectId: String, entryId: String): JsonObject {
        requireEntity(projectId, "world", entryId)
        return items(records(projectId, "world_timeline").filter { it.text("entry_id") == entryId }
            .sortedWith(compareBy<JsonObject> { it.number("sort_order") }.thenBy { it.text("created_at") }))
    }

    suspend fun relationshipNetwork(projectId: String): JsonObject {
        requireEntity(projectId, "project", projectId)
        val characters = records(projectId, "character")
        val names = characters.associate { it.text("id") to it.text("name") }
        val edges = records(projectId, "character_relationship").map { row ->
            val from = row.text("from").ifBlank { row.text("character_a_id") }
            val to = row.text("to").ifBlank { row.text("character_b_id") }
            JsonObject(row + mapOf("from" to JsonPrimitive(from), "to" to JsonPrimitive(to),
                "source_name" to JsonPrimitive(names[from].orEmpty()), "target_name" to JsonPrimitive(names[to].orEmpty())))
        }
        return buildJsonObject { put("nodes", JsonArray(characters)); put("edges", JsonArray(edges)) }
    }

    suspend fun aiConfig(projectId: String, characterId: String): JsonObject {
        requireEntity(projectId, "character", characterId)
        val existing = records(projectId, "character_ai_config").firstOrNull { it.text("character_id") == characterId }
        val defaults = buildJsonObject {
            put("id", catalogingId("character_ai_config", characterId)); put("character_id", characterId)
            put("tone_style", "neutral"); put("catchphrases", JsonArray(emptyList()))
            put("verbosity", "moderate"); put("emotion_tendency", "neutral")
            put("model_override", JsonNull); put("custom_system_prompt", JsonNull)
        }
        return JsonObject(defaults + existing.orEmpty().filterValues { it != JsonNull } + buildJsonObject {
            for (field in listOf("tone_style", "verbosity", "emotion_tendency")) {
                put(field, existing?.text(field)?.takeIf(String::isNotBlank)?.let(::JsonPrimitive) ?: defaults.getValue(field))
            }
        })
    }
}
