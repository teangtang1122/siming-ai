package com.siming.mobile.data.authoring

import com.siming.mobile.data.cataloging.catalogingHash
import com.siming.mobile.data.cataloging.catalogingId
import com.siming.mobile.data.local.SimingDao
import kotlinx.serialization.json.*

internal class LocalLinkedRecords(private val dao: SimingDao, private val store: LocalAuthoringStore) {
    suspend fun apply(projectId: String, type: String, id: String, payload: JsonObject, changed: Set<String>): JsonObject {
        if (type == "outline" && changed.any { it in setOf("characters", "character_ids") }) {
            val values = if ("characters" in changed) (payload["characters"] as? JsonArray).orEmpty().map { it.jsonObject }
                else (payload["character_ids"] as? JsonArray).orEmpty().map { buildJsonObject { put("character_id", it) } }
            val ids = values.map { it.text("character_id") }
            require(ids.distinct().size == ids.size) { "大纲角色关联不能重复" }
            val existing = store.read.records(projectId, "outline_node_character").filter { it.text("outline_node_id") == id }
            val linked = values.map { link ->
                val characterId = link.text("character_id")
                val character = store.read.requireEntity(projectId, "character", characterId)
                val linkId = existing.firstOrNull { it.text("character_id") == characterId }?.text("id")
                    ?: catalogingId("outline_node_character", projectId, id, characterId)
                store.put(projectId, "outline", linkId, buildJsonObject {
                    put("id", linkId); put("project_id", projectId); put("_record_type", "outline_node_character")
                    put("outline_node_id", id); put("character_id", characterId); put("role_in_scene", link.text("role_in_scene"))
                }, true)
                buildJsonObject { put("id", characterId); put("character_id", characterId); put("name", character.text("name")); put("role_type", character.text("role_type")); put("role_in_scene", link.text("role_in_scene")) }
            }
            val keptIds = ids.map { characterId -> existing.firstOrNull { it.text("character_id") == characterId }?.text("id") ?: catalogingId("outline_node_character", projectId, id, characterId) }.toSet()
            remove(projectId) { p -> p.text("_record_type") == "outline_node_character" && p.text("outline_node_id") == id && p.text("id") !in keptIds }
            return JsonObject((payload - "character_ids") + mapOf("linked_characters" to JsonArray(linked), "characters" to JsonArray(values)))
        }
        if (type == "character" && "aliases" in changed) {
            val aliases = (payload["aliases"] as? JsonArray).orEmpty().map { it.jsonPrimitive.content.trim() }.filter { it.isNotEmpty() && it != payload.text("name") }.distinct()
            val existing = store.read.records(projectId, "character_alias").filter { it.text("character_id") == id }
            aliases.forEach { alias ->
                if (existing.none { it.text("alias") == alias }) {
                    val aliasId = catalogingId("character_alias", projectId, id, alias)
                    store.put(projectId, "character_alias", aliasId, buildJsonObject {
                        put("id", aliasId); put("project_id", projectId); put("_record_type", "character_alias")
                        put("character_id", id); put("alias", alias); put("alias_type", "alias"); put("source_chapter_id", JsonNull)
                    }, true)
                }
            }
            remove(projectId) { p -> p.text("_record_type") == "character_alias" && p.text("character_id") == id && p.text("alias") !in aliases }
            return JsonObject(payload + ("aliases" to JsonArray(aliases.map(::JsonPrimitive))))
        }
        return payload
    }

    private suspend fun remove(projectId: String, predicate: (JsonObject) -> Boolean) {
        dao.projectSnapshot(projectId).filter { predicate(it.payload()) }.forEach { row ->
            dao.saveEntity(row.copy(operation = "delete", payloadJson = null, contentHash = catalogingHash("null"), dirty = true))
        }
    }
}
