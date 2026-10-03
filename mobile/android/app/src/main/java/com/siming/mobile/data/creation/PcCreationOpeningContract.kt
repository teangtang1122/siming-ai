package com.siming.mobile.data.creation

import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.*

/** The PC-exported body/link contract, including standalone materialization. */
internal class PcCreationOpeningContract(private val entities: PcCreationEntityContract, contract: JsonObject) {
    private val sceneFields = (contract["scene_metadata_fields"] as JsonArray).map { it.jsonPrimitive.content }
    private val chapterFields = (contract["chapter_metadata_fields"] as JsonArray).map { it.jsonPrimitive.content }

    fun validate(data: JsonObject, volumes: JsonArray? = null, partial: Boolean = false, characters: JsonArray? = null) {
        val structure = "creation_opening_structure_invalid"
        val parentError = "creation_opening_parent_invalid"
        val countError = "creation_opening_count_invalid"
        val knownCharacters = characters?.map { it.jsonObject.text("id") }?.toSet()
        val ids = mutableSetOf<String>()
        val collections = listOf("chapters", "sections").associateWith { field ->
            val rows = data[field] ?: if (partial) JsonArray(emptyList()) else JsonNull
            if (rows !is JsonArray || rows.any { it !is JsonObject }) entities.reject(structure, "$.data.$field")
            rows.mapIndexed { index, value ->
                val row = value.jsonObject
                val path = "$.data.$field[$index]"
                listOf("client_id", "title").forEach { name ->
                    if (!row.hasText(name)) entities.reject(structure, "$path.$name")
                }
                if (!ids.add(row.text("client_id"))) entities.reject(structure, "$path.client_id")
                if (!row.hasText("summary")) entities.reject("creation_opening_summary_missing", "$path.summary")
                if ("planned_summary" in row && (row["planned_summary"] as? JsonPrimitive)?.isString != true) {
                    entities.reject(structure, "$path.planned_summary")
                }
                listOf("parent_index", "parent_title", "sections", "scene_outline").forEach { name ->
                    if (name in row) entities.reject(structure, "$path.$name")
                }
                val type = if (field == "chapters") "chapter" else "section"
                if ("node_type" in row && row.text("node_type") != type) entities.reject(structure, "$path.node_type")
                val references = row["character_ids"] as? JsonArray
                if (references == null || references.any { (it as? JsonPrimitive)?.let { value -> value.isString && value.content.isNotBlank() } != true }
                    || references.distinct().size != references.size
                    || (knownCharacters != null && references.any { it.jsonPrimitive.content !in knownCharacters })) {
                    entities.reject("creation_opening_characters_invalid", "$path.character_ids")
                }
                row
            }
        }
        val chapters = collections.getValue("chapters")
        val volumeMap = volumes?.associate { it.jsonObject.text("id") to it.jsonObject }
        chapters.forEachIndexed { index, row ->
            val path = "$.data.chapters[$index]"
            val number = row.number("chapter_number")
            if (number == null || number <= 0) entities.reject(structure, "$path.chapter_number")
            if (!row.hasText("volume_id")) entities.reject(parentError, "$path.volume_id")
            if (volumeMap != null) {
                val volume = volumeMap[row.text("volume_id")]
                val start = volume?.number("start_chapter")
                val end = volume?.number("end_chapter")
                if (start == null || end == null || number !in start..end) entities.reject(parentError, "$path.volume_id")
            }
        }
        val chapterIds = chapters.map { it.text("client_id") }.toSet()
        val scenes = chapterIds.associateWith { mutableListOf<Int>() }.toMutableMap()
        collections.getValue("sections").forEachIndexed { index, row ->
            val path = "$.data.sections[$index]"
            val parent = row.text("parent_client_id")
            if (!row.hasText("parent_client_id") || (!partial && parent !in chapterIds)) {
                entities.reject(parentError, "$path.parent_client_id")
            }
            val metadata = row["metadata"] as? JsonObject
            if (metadata == null || !sceneFields.all(metadata::containsKey)) entities.reject(structure, "$path.metadata")
            val number = metadata.number("scene_number")
            if (number == null || number !in 1..6) entities.reject(countError, "$path.metadata.scene_number")
            scenes.getOrPut(parent) { mutableListOf() }.add(number)
        }
        if (!partial) {
            val count = if (data.number("opening_chapter_count") == 15) 15 else 3
            if (chapters.map { it.number("chapter_number")!! }.sorted() != (1..count).toList()) {
                entities.reject(countError, "$.data.chapters")
            }
            if (scenes.values.any { it.size !in 2..6 || it.sorted() != (1..it.size).toList() }) {
                entities.reject(countError, "$.data.sections")
            }
        }
    }

    fun normalize(data: JsonObject): JsonObject {
        validate(data, partial = true)
        return JsonObject(data.toMutableMap().apply {
            listOf("chapters" to "chapter", "sections" to "section").forEach { (field, type) ->
                (data[field] as? JsonArray)?.let { rows ->
                    put(field, JsonArray(rows.map { raw ->
                        val row = raw.jsonObject
                        JsonObject(row.toMutableMap().apply {
                            put("node_type", JsonPrimitive(type))
                            if (!row.hasText("planned_summary")) put("planned_summary", row.getValue("summary"))
                            put("sort_order", if (type == "chapter") row.getValue("chapter_number")
                                else row.getValue("metadata").jsonObject.getValue("scene_number"))
                        })
                    }))
                }
            }
        })
    }

    fun volumeIndex(session: JsonObject): JsonArray = JsonArray(volumeRows(session).map { row ->
        buildJsonObject {
            put("id", volumeId(session, row))
            listOf("title", "start_chapter", "end_chapter").forEach { put(it, row[it] ?: JsonNull) }
        }
    })

    // Exact content identity is independent of array order and is scoped to the local session.
    // Editing a volume changes its identity and already marks the dependent outline stale.
    fun volumeId(session: JsonObject, row: JsonObject): String = entityId(session, row, "volume")
    fun characterId(session: JsonObject, row: JsonObject): String = entityId(session, row, "character")
    fun characterIndex(session: JsonObject): JsonArray = JsonArray(characterRows(session).map { row ->
        buildJsonObject { put("id", characterId(session, row)); put("name", row.text("name")) }
    })
    fun characterRecordIds(session: JsonObject): Map<String, String> =
        characterRows(session).associate { characterId(session, it) to UUID.randomUUID().toString() }

    private fun entityId(session: JsonObject, row: JsonObject, type: String): String {
        fun canonical(value: JsonElement): JsonElement = when (value) {
            is JsonObject -> JsonObject(value.toSortedMap().mapValues { canonical(it.value) })
            is JsonArray -> JsonArray(value.map(::canonical))
            else -> value
        }
        val digest = MessageDigest.getInstance("SHA-256").digest(canonical(row).toString().toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        return "${session.text("id")}:$type:$digest"
    }

    fun validateSaved(
        session: JsonObject,
        volumes: JsonArray = volumeIndex(session),
        characters: JsonArray = characterIndex(session),
    ) {
        val state = stages(session)["opening_outline"] as? JsonObject ?: return
        if (state.text("status") == "confirmed") {
            validate(state["data"] as? JsonObject ?: JsonObject(emptyMap()), volumes, characters = characters)
        }
    }

    fun transferVolumes(session: JsonObject, data: JsonObject): JsonObject = JsonObject(data.toMutableMap().apply {
        put("volumes", JsonArray(volumeRows(session).map { row -> JsonObject(row.toMutableMap().apply {
            put("client_id", JsonPrimitive(volumeId(session, row)))
        }) }))
    })

    fun transferCharacters(session: JsonObject, data: JsonObject): JsonObject = JsonObject(data.toMutableMap().apply {
        put("characters", JsonArray(characterRows(session).map { row -> JsonObject(row.toMutableMap().apply {
            put("client_id", JsonPrimitive(characterId(session, row)))
        }) }))
    })

    fun transferOpening(data: JsonObject, remoteIndex: JsonArray, remoteCharacters: JsonArray = JsonArray(emptyList())): JsonObject {
        val volumes = remoteIndex.associate { it.jsonObject.text("client_id") to it.jsonObject.text("id") }
        val characters = remoteCharacters.associate { it.jsonObject.text("client_id") to it.jsonObject.text("id") }
        return JsonObject(data.toMutableMap().apply {
            listOf("chapters", "sections").forEach { field ->
                put(field, JsonArray((data[field] as JsonArray).map { raw ->
                    JsonObject(raw.jsonObject.toMutableMap().apply {
                        if (field == "chapters") put("volume_id", JsonPrimitive(volumes[raw.jsonObject.text("volume_id")]
                            ?: entities.reject("creation_opening_parent_invalid", "$.data.chapters.volume_id")))
                        put("character_ids", JsonArray((raw.jsonObject["character_ids"] as JsonArray).map { ref ->
                            JsonPrimitive(characters[ref.jsonPrimitive.content]
                                ?: entities.reject("creation_opening_characters_invalid", "$.data.$field.character_ids"))
                        }))
                    })
                }))
            }
        })
    }

    /** Build all records before any repository writes, so an invalid outline leaves no partial book. */
    fun materialize(session: JsonObject, projectId: String, characterIds: Map<String, String> = characterRecordIds(session)): List<JsonObject> {
        validateSaved(session)
        val result = mutableListOf<JsonObject>()
        val volumeIds = linkedMapOf<String, String>()
        val chapterIds = linkedMapOf<String, String>()
        fun record(row: JsonObject, type: String, parent: String?, order: Int, metadata: JsonObject): String {
            val id = UUID.randomUUID().toString()
            result += buildJsonObject {
                put("_record_type", "outline_node"); put("id", id); put("project_id", projectId)
                put("parent_id", parent?.let(::JsonPrimitive) ?: JsonNull); put("node_type", type)
                put("title", row.text("title").take(200)); put("summary", row.text("summary"))
                put("planned_summary", row.text("planned_summary").ifBlank { row.text("summary") }); put("status", "pending"); put("sort_order", order)
                put("metadata_json", metadata)
                put("linked_characters", JsonArray((row["character_ids"] as? JsonArray).orEmpty().map { ref ->
                    buildJsonObject { put("character_id", characterIds.getValue(ref.jsonPrimitive.content)); put("role_in_scene", "立项规划") }
                }))
            }
            return id
        }
        volumeRows(session).forEachIndexed { index, row ->
            volumeIds[volumeId(session, row)] = record(row, "volume", null, index, buildJsonObject {
                put("start_chapter", row.getValue("start_chapter")); put("end_chapter", row.getValue("end_chapter"))
            })
        }
        val state = stages(session)["opening_outline"] as? JsonObject
        if (state?.text("status") == "confirmed") {
            val opening = state.getValue("data").jsonObject
            fun metadata(row: JsonObject): JsonObject = JsonObject(((row["metadata"] as? JsonObject).orEmpty()).toMutableMap().apply {
                (chapterFields + sceneFields).forEach { field -> if (field !in this) row[field]?.let { put(field, it) } }
            })
            (opening["chapters"] as JsonArray).forEach { raw ->
                val row = raw.jsonObject
                chapterIds[row.text("client_id")] = record(row, "chapter", volumeIds.getValue(row.text("volume_id")),
                    row.number("chapter_number")!!, metadata(row))
            }
            (opening["sections"] as JsonArray).forEach { raw ->
                val row = raw.jsonObject
                record(row, "section", chapterIds.getValue(row.text("parent_client_id")),
                    row.getValue("metadata").jsonObject.number("scene_number")!!, metadata(row))
            }
        }
        return result
    }

    private fun stages(session: JsonObject) = session.getValue("draft").jsonObject.getValue("stages").jsonObject
    private fun characterRows(session: JsonObject): List<JsonObject> =
        (((stages(session)["characters"] as? JsonObject)?.get("data") as? JsonObject)?.get("characters") as? JsonArray)
            .orEmpty().map { it.jsonObject }
    private fun volumeRows(session: JsonObject): List<JsonObject> =
        (((stages(session)["macro_outline"] as? JsonObject)?.get("data") as? JsonObject)?.get("volumes") as? JsonArray)
            .orEmpty().map { it.jsonObject }
    private fun JsonObject.text(name: String) = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.hasText(name: String) = (get(name) as? JsonPrimitive)?.let { it.isString && it.content.isNotBlank() } == true
    private fun JsonObject.number(name: String) = (get(name) as? JsonPrimitive)?.takeUnless { it.isString }?.intOrNull
}
