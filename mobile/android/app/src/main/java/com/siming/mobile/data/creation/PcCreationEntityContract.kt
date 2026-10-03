package com.siming.mobile.data.creation

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal class CreationGenerationException(val diagnostic: JsonObject) : IllegalArgumentException(
    "${(diagnostic["data"] as JsonObject)["path"]}: ${(diagnostic["detail"] as JsonPrimitive).content}",
)

/** Entity collections, type discriminators and output rules exported from PC. */
internal class PcCreationEntityContract(creation: JsonObject) {
    private val contract = creation["entity_contract"] as? JsonObject
        ?: error("手机内置契约缺少 entity_contract；请重新导出 PC 契约")
    val opening = PcCreationOpeningContract(this, contract.getValue("opening_outline") as JsonObject)
    private val outputs = contract["outputs"] as JsonObject
    private val placeDimensions = contract["place_dimensions"] as JsonObject
    val instructionTemplate = contract.string("generation_instruction")
    val collections: Map<String, List<Pair<String, String>>> = (contract["collections"] as JsonObject)
        .mapValues { (_, rows) ->
            (rows as JsonArray).map { row ->
                val pair = row as JsonArray
                (pair[0] as JsonPrimitive).content to (pair[1] as JsonPrimitive).content
            }
        }

    fun output(artifact: String, entityType: String): JsonObject? = (outputs[entityType] as? JsonObject)
        ?.takeIf { it.string("artifact") == artifact }

    fun validateLocks(stage: String, data: JsonObject, baseline: JsonObject, paths: JsonArray) {
        fun read(root: JsonElement, path: String): JsonElement? {
            var cursor: JsonElement? = root
            val parts = if (path in setOf("", "/")) emptyList() else path.trimStart('/').split('/')
            parts.forEach { part ->
                val key = part.replace("~1", "/").replace("~0", "~")
                cursor = when (val value = cursor) {
                    is JsonObject -> value[key]
                    is JsonArray -> key.toIntOrNull()?.let(value::getOrNull)
                    else -> null
                }
            }
            return cursor
        }
        val reason = if (stage == "opening_outline") "creation_opening_locked_changed" else "creation_artifact_locked_changed"
        paths.forEach { raw ->
            val path = (raw as JsonPrimitive).content
            if (read(data, path) != read(baseline, path)) reject(reason, path)
        }
    }

    fun entityType(kind: String, row: JsonObject): String = when {
        kind != "place" -> kind
        row["dimension"] == placeDimensions["faction"] -> "faction"
        else -> "location"
    }

    fun validateGenerated(stage: String, data: JsonObject, target: JsonObject?, volumes: JsonArray? = null, characters: JsonArray? = null) {
        if (target == null || target.string("initialize_stage") == "true") {
            validateStageTextFields(stage, data)
        }
        if (target != null) {
            val output = output(stage, target.string("entity_type")) ?: error("目标实体不属于当前阶段")
            val field = output.string("field")
            val path = "$.data.$field"
            val rows = data[field] as? JsonArray
            if (rows.isNullOrEmpty() || rows.any { it !is JsonObject }) {
                reject("creation_generated_collection_invalid", path)
            }
            if (target.string("mode") == "existing" && rows.size != 1) {
                reject("creation_generated_count_invalid", path)
            }
            val required = output["required_values"] as JsonObject
            rows.forEachIndexed { index, item ->
                val row = item as JsonObject
                required.forEach { (name, value) ->
                    if (row[name] != value) reject("creation_generated_dimension_invalid", "$path[$index].$name")
                }
            }
        }
        if (stage == "opening_outline") {
            opening.validate(data, volumes, partial = target != null && target.string("initialize_stage") != "true", characters = characters)
        }
        if (stage == "locations") {
            (data["entries"] as? JsonArray).orEmpty().forEachIndexed { index, item ->
                if (item is JsonObject && item["dimension"] !in placeDimensions.values) {
                    reject("creation_generated_dimension_invalid", "$.data.entries[$index].dimension")
                }
            }
        }
    }

    fun validateStageTextFields(stage: String, data: JsonObject) {
        val fields = (contract["required_stage_text_fields"] as JsonObject)[stage] as? JsonArray
        fields.orEmpty().forEach { item ->
            val field = (item as JsonPrimitive).content
            val value = data[field] as? JsonPrimitive
            if (value == null || !value.isString || value.content.isBlank()) {
                reject("creation_generated_stage_fields_missing", "$.data.$field")
            }
        }
    }

    fun reject(reason: String, path: String): Nothing {
        val details = contract["generation_diagnostics"] as JsonObject
        throw CreationGenerationException(buildJsonObject {
            put("status", "error")
            put("detail", details.getValue(reason))
            put("data", buildJsonObject {
                put("reason", reason); put("path", path); put("retryable", true)
            })
        })
    }

    private fun JsonObject.string(key: String) = (get(key) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
