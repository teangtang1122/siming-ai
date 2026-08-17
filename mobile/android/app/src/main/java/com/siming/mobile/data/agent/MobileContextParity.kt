package com.siming.mobile.data.agent

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

internal data class PcCharacterResolution(
    val characters: List<JsonObject>,
    val resolvedAliases: Map<String, String>,
)

/** Mirrors PC character resolution: outline links -> direct name -> alias. */
internal fun resolvePcCharacters(
    allRecords: List<JsonObject>,
    outlineNodeId: String?,
    involvedNames: List<String>,
    limit: Int,
): PcCharacterResolution {
    val characters = allRecords.filter { it.recordType() == "character" }
    val byId = characters.associateBy { it.string("id") }
    val selected = mutableListOf<JsonObject>()
    val seen = linkedSetOf<String>()
    val resolvedAliases = linkedMapOf<String, String>()

    fun add(character: JsonObject?) {
        val id = character?.string("id").orEmpty()
        if (id.isBlank() || id in seen || selected.size >= limit) return
        seen += id
        selected += requireNotNull(character)
    }

    if (!outlineNodeId.isNullOrBlank()) {
        val outline = allRecords.firstOrNull {
            it.recordType() == "outline_node" && it.string("id") == outlineNodeId
        }
        outline?.linkedCharacterIds().orEmpty().forEach { id -> add(byId[id]) }
    }

    val matchedNames = mutableSetOf<String>()
    involvedNames.forEach { requested ->
        val character = characters.firstOrNull { it.string("name") == requested }
        if (character != null) {
            matchedNames += requested
            add(character)
        }
    }

    involvedNames.filterNot { it in matchedNames }.forEach { requested ->
        val character = characters.firstOrNull { requested in it.stringList("aliases") }
        if (character != null) {
            resolvedAliases[requested] = character.string("name")
            add(character)
        }
    }

    return PcCharacterResolution(selected, resolvedAliases)
}

/** Mirrors PC preview_writing_context relationship payload selection. */
internal fun pcRelationshipPayloads(
    allRecords: List<JsonObject>,
    selectedCharacters: List<JsonObject>,
): JsonArray {
    val selectedIds = selectedCharacters.mapTo(linkedSetOf()) { it.string("id") }
    if (selectedIds.isEmpty()) return JsonArray(emptyList())
    val names = allRecords
        .filter { it.recordType() == "character" }
        .associate { it.string("id") to it.string("name") }
    val rows = allRecords.asSequence()
        .filter { it.recordType() == "character_relationship" }
        .filter { relation ->
            relation.relationFrom() in selectedIds || relation.relationTo() in selectedIds
        }
        .take(40)
        .map { relation ->
            JsonObject(
                linkedMapOf(
                    "source" to JsonPrimitive(names[relation.relationFrom()] ?: relation.relationFrom().take(8)),
                    "target" to JsonPrimitive(names[relation.relationTo()] ?: relation.relationTo().take(8)),
                    "relationship_type" to JsonPrimitive(relation.string("relationship_type")),
                    "description" to JsonPrimitive(relation.string("description").take(260)),
                ),
            )
        }
        .toList()
    return JsonArray(rows)
}

/** Mirrors PC chapter writer character blocks, including relationship lines. */
internal fun pcCharacterDetails(
    allRecords: List<JsonObject>,
    selectedCharacters: List<JsonObject>,
): String {
    if (selectedCharacters.isEmpty()) return "未指定角色。"
    val names = allRecords
        .filter { it.recordType() == "character" }
        .associate { it.string("id") to it.string("name") }
    val relations = allRecords.filter { it.recordType() == "character_relationship" }
    return selectedCharacters.joinToString("\n\n") { character ->
        val id = character.string("id")
        buildString {
            append("【${character.string("name")}】\n")
            append("  身份: ${character.string("role_type").ifBlank { "未设定" }}\n")
            append("  性格: ${character.string("personality").ifBlank { "未设定" }.take(300)}\n")
            append("  背景: ${character.string("background").ifBlank { "未设定" }.take(300)}\n")
            append("  能力: ${character.stringList("abilities").joinToString("、").ifBlank { "未设定" }.take(200)}\n")
            append("  外貌: ${character.string("appearance").ifBlank { "未设定" }.take(150)}")
            val connected = relations.asSequence()
                .filter { it.relationFrom() == id || it.relationTo() == id }
                .take(10)
                .map { relation ->
                    val otherId = if (relation.relationFrom() == id) relation.relationTo() else relation.relationFrom()
                    "    ${names[otherId] ?: otherId.take(8)}: ${relation.string("relationship_type")}" +
                        relation.string("description").takeIf(String::isNotBlank)?.let { "（${it.take(220)}）" }.orEmpty()
                }
                .toList()
            if (connected.isNotEmpty()) {
                append("\n  关系:\n")
                append(connected.joinToString("\n"))
            }
        }
    }
}

/** Mirrors PC governance_context ordering, open-status filtering, and latest-state selection. */
internal fun pcGovernanceContext(allRecords: List<JsonObject>, limit: Int = 12): String {
    val weighted = mutableListOf<Pair<Int, String>>()
    allRecords.forEach { row ->
        when (row.recordType()) {
            "narrative_debt" -> if (row.string("status") in OPEN_STATUSES) {
                val priority = row.string("priority").ifBlank { "medium" }
                weighted += importance(priority) + 4 to
                    "[叙事债务/$priority/ID:${row.string("id")}] ${row.string("title")}"
            }
            "foreshadowing" -> if (row.string("status") in OPEN_STATUSES) {
                val importance = row.string("importance").ifBlank { "medium" }
                val due = row.intValue("target_chapter_number")
                    ?.takeIf { it > 0 }
                    ?.let { "，目标第${it}章" }
                    .orEmpty()
                weighted += importance(importance) + 3 to
                    "[伏笔/$importance/ID:${row.string("id")}] ${row.string("title")}$due"
            }
            "causal_edge" -> if (row.string("status") in OPEN_STATUSES) {
                val strength = row.doubleValue("strength") ?: 0.0
                weighted += (strength * 5).toInt() + 2 to
                    "[未闭环因果/ID:${row.string("id")}] ${row.string("cause")} -> ${row.string("effect")}"
            }
        }
    }

    latestCharacterStates(allRecords).forEach { row ->
        val detail = listOf(
            row.string("current_goal"),
            row.string("emotional_residue"),
            row.string("behavior_boundaries"),
        ).filter(String::isNotBlank).joinToString("；")
        if (detail.isNotBlank()) {
            weighted += 4 to "[角色动态/${row.string("character_id")}] $detail"
        }
    }

    if (weighted.isEmpty()) return ""
    return "叙事治理锁：\n" + weighted
        .sortedByDescending { it.first }
        .take(limit)
        .joinToString("\n") { it.second }
}

private fun latestCharacterStates(allRecords: List<JsonObject>): List<JsonObject> {
    val latest = linkedMapOf<String, JsonObject>()
    allRecords.asSequence()
        .filter { it.recordType() == "character_narrative_state" }
        .sortedWith(
            compareByDescending<JsonObject> { it.string("created_at") }
                .thenByDescending { it.string("id") },
        )
        .take(100)
        .forEach { row ->
            val characterId = row.string("character_id")
            if (characterId.isNotBlank()) latest.putIfAbsent(characterId, row)
        }
    return latest.values.toList()
}

private fun JsonObject.recordType(): String = string("_record_type")

private fun JsonObject.linkedCharacterIds(): List<String> {
    val rows = (get("linked_characters") as? JsonArray)
        ?: (get("characters") as? JsonArray)
        ?: return emptyList()
    return rows.mapNotNull { raw ->
        val item = raw as? JsonObject ?: return@mapNotNull null
        item.string("character_id").ifBlank { item.string("id") }.takeIf(String::isNotBlank)
    }
}

private fun JsonObject.relationFrom(): String =
    string("from").ifBlank { string("character_a_id") }

private fun JsonObject.relationTo(): String =
    string("to").ifBlank { string("character_b_id") }

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.stringList(name: String): List<String> = when (val value = get(name)) {
    is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }
        .filter(String::isNotBlank)
    is JsonPrimitive -> value.contentOrNull.orEmpty()
        .split('\n', ',', '，', '、')
        .map(String::trim)
        .filter(String::isNotBlank)
    else -> emptyList()
}

private fun JsonObject.intValue(name: String): Int? =
    get(name)?.jsonPrimitive?.let { it.intOrNull ?: it.contentOrNull?.toIntOrNull() }

private fun JsonObject.doubleValue(name: String): Double? =
    get(name)?.jsonPrimitive?.let { it.doubleOrNull ?: it.contentOrNull?.toDoubleOrNull() }

private fun importance(value: String): Int = IMPORTANCE_WEIGHT[value] ?: 2

private val OPEN_STATUSES = setOf("open", "deferred", "pending_review", "stale")
private val IMPORTANCE_WEIGHT = mapOf("critical" to 4, "high" to 3, "medium" to 2, "low" to 1)
