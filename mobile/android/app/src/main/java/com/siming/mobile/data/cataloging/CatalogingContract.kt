package com.siming.mobile.data.cataloging

import android.content.Context
import com.siming.mobile.data.agent.PcPromptContract
import com.siming.mobile.data.agent.mobileCanonicalJson
import com.siming.mobile.data.local.ReplicaEntity
import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.*

/** The Android storage adapter consumes the PC catalog, never a second prompt/schema. */
internal class CatalogingContract(root: JsonObject) {
    constructor(context: Context) : this(context.assets.open(PcPromptContract.ASSET_NAME)
        .bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject })

    private val definition = root.getValue("cataloging").jsonObject
    val systemPrompt = definition.text("system_prompt")
    val schemas = definition.getValue("tool_schemas").jsonArray
    val selectionSchema = definition.getValue("selection_tool_schema").jsonObject
    private val selectionCandidateSchemas = definition.getValue("selection_candidate_schemas").jsonObject
    val names = schemas.map { it.jsonObject.getValue("function").jsonObject.text("name") }.toSet()
    val candidateSchema = definition.getValue("candidate_schema").jsonObject
    val applyOrder = definition.getValue("apply_order").jsonObject
    val stateFields = definition.strings("character_state_fields")
    val stateLimits = definition.getValue("character_state_limits").jsonObject
    val sceneFields = definition.strings("scene_state_fields")
    val maxSteps = definition.number("max_steps")
    val maxErrors = definition.number("max_consecutive_errors")
    val maxToolNameRejections = definition.number("max_unopened_tool_batches")
    val guardFields = definition.getValue("guard_fields").jsonObject
    val modelRequest = CatalogingModelRequest(definition.getValue("model_request").jsonObject)

    fun schemasForPlan(
        summaryRequired: Boolean,
        selectedType: String? = null,
        chapterOutlineId: String? = null,
        sectionOutlineIds: List<String> = emptyList(),
        sceneCount: Int? = null,
        summary: JsonObject? = null,
    ): JsonArray {
        val scopedType = if (summaryRequired) "chapter_summary" else selectedType
        val characters = summary?.objects("character_bindings").orEmpty()
        val worldbuilding = summary?.objects("worldbuilding_bindings").orEmpty()
        val relationships = summary?.obj("coverage_manifest")?.objects("relationships").orEmpty()
        val boundIds = when (selectedType) {
            "character_create" -> characters.filter { it.text("decision") == "new" }.map { it.text("id") }
            "character_update" -> characters.filter { it.text("decision") == "existing" }.map { it.text("id") }
            "character_state_update" -> characters.map { it.text("id") }
            "worldbuilding_create" -> worldbuilding.filter { it.text("decision") == "new" }.map { it.text("id") }
            "worldbuilding_update" -> worldbuilding.filter { it.text("decision") == "existing" }.map { it.text("id") }
            else -> null
        }
        val boundLimit = when (selectedType) {
            "outline_create_chapter", "outline_update_chapter", "chapter_link", "scene_outline_replace" -> 1
            "character_create", "character_update", "character_state_update",
            "worldbuilding_create", "worldbuilding_update" -> boundIds?.size
            "character_relationship" -> if (summary != null) relationships.size else null
            else -> null
        }
        val scopedItem = when {
            summaryRequired -> candidateSchema.getValue("anyOf").jsonArray.map { it.jsonObject }.single {
                it.getValue("properties").jsonObject.getValue("type").jsonObject.strings("enum") == listOf("chapter_summary")
            }
            selectedType != null -> {
                val base = selectionCandidateSchemas.getValue(selectedType).jsonObject
                val properties = base.getValue("properties").jsonObject
                val targets = when (selectedType) {
                    "outline_update_chapter" -> listOfNotNull(chapterOutlineId)
                    "outline_update_section" -> sectionOutlineIds
                    else -> boundIds
                }
                val targetField = if (selectedType in setOf("character_create", "worldbuilding_create"))
                    "client_id" else "id"
                var scopedProperties = JsonObject(properties +
                    (targets?.let { mapOf(targetField to buildJsonObject {
                        put("type", "string"); put("enum", jsonStrings(it))
                    }) } ?: emptyMap()) +
                    (if (selectedType.endsWith("_section") && sceneCount != null) mapOf(
                        "scene_number" to JsonObject(properties.getValue("scene_number").jsonObject +
                            ("maximum" to JsonPrimitive(sceneCount)))
                    ) else emptyMap()))
                if (selectedType == "chapter_link" && summary != null) {
                    val characterNames = characters.map { it.text("name") }
                    val worldNames = worldbuilding.map { it.text("name") }
                    val characterArray = properties.getValue("characters").jsonObject
                    val characterItem = characterArray.getValue("items").jsonObject
                    val characterItemProperties = characterItem.getValue("properties").jsonObject
                    val scopedCharacters = JsonObject(characterArray + mapOf(
                        "minItems" to JsonPrimitive(characterNames.size),
                        "maxItems" to JsonPrimitive(characterNames.size),
                        "items" to JsonObject(characterItem + ("properties" to JsonObject(
                            characterItemProperties + ("name" to buildJsonObject {
                                put("type", "string"); put("enum", jsonStrings(characterNames))
                            })
                        ))),
                    ))
                    val worldArray = properties.getValue("worldbuilding_titles").jsonObject
                    val scopedWorld = JsonObject(worldArray + mapOf(
                        "minItems" to JsonPrimitive(worldNames.size),
                        "maxItems" to JsonPrimitive(worldNames.size),
                        "items" to buildJsonObject {
                            put("type", "string"); put("enum", jsonStrings(worldNames))
                        },
                    ))
                    scopedProperties = JsonObject(scopedProperties + mapOf(
                        "characters" to scopedCharacters, "worldbuilding_titles" to scopedWorld,
                    ))
                }
                JsonObject(base + ("properties" to scopedProperties))
            }
            else -> null
        }
        val tools = schemas.map { tool ->
            val exposed = tool.jsonObject
            val function = exposed.getValue("function").jsonObject
            if (function.text("name") != "save_external_cataloging_candidates") return@map exposed
            val parameters = function.getValue("parameters").jsonObject
            val properties = parameters.getValue("properties").jsonObject
            val candidates = properties.getValue("candidates").jsonObject
            val batchLimit = if (selectedType?.endsWith("_section") == true) sceneCount else boundLimit
            val scopedCandidates = if (scopedItem != null) JsonObject(candidates + mapOf(
                "items" to scopedItem,
                "description" to JsonPrimitive("本步骤只提交 $scopedType 类型；同类可一次或分批提交，格式保持到你重新选择类型。每个 scene_number 只出现一次。"),
            ) + if (batchLimit != null)
                mapOf("maxItems" to JsonPrimitive(batchLimit)) else emptyMap()) else JsonObject(candidates + mapOf(
                "maxItems" to JsonPrimitive(0),
                "description" to JsonPrimitive("若计划已完整，传 [] 并设置 finalize=true；若仍需候选，先调用 select_cataloging_candidate_types 选择类型。"),
            ))
            val scopedProperties = JsonObject(properties + ("candidates" to scopedCandidates) +
                if (scopedItem == null) mapOf("finalize" to buildJsonObject {
                    put("type", "boolean"); put("enum", JsonArray(listOf(JsonPrimitive(true))))
                }) else emptyMap())
            val scopedParameters = JsonObject(parameters + ("properties" to scopedProperties) +
                if (scopedItem == null) mapOf("required" to jsonStrings(
                    (parameters.strings("required") + "finalize").distinct()
                )) else emptyMap())
            JsonObject(exposed + ("function" to JsonObject(function + ("parameters" to JsonObject(
                scopedParameters
            )))))
        }
        val availableSelection = if (summaryRequired) emptyList() else {
            val function = selectionSchema.getValue("function").jsonObject
            val parameters = function.getValue("parameters").jsonObject
            val properties = parameters.getValue("properties").jsonObject
            val types = properties.getValue("types").jsonObject
            val items = types.getValue("items").jsonObject
            val available = items.strings("enum").filter { type ->
                when (type) {
                    "outline_create_chapter" -> chapterOutlineId == null
                    "outline_update_chapter" -> chapterOutlineId != null
                    "outline_update_section" -> sectionOutlineIds.isNotEmpty()
                    else -> true
                }
            }
            val scopedItems = JsonObject(items + ("enum" to jsonStrings(available)))
            val scopedTypes = JsonObject(types + ("items" to scopedItems))
            val scopedParameters = JsonObject(parameters + ("properties" to JsonObject(properties +
                ("types" to scopedTypes))))
            listOf(JsonObject(selectionSchema + ("function" to JsonObject(function +
                ("parameters" to scopedParameters)))))
        }
        return JsonArray(tools + availableSelection)
    }

    fun validateTool(name: String, args: JsonObject, offeredSchemas: JsonArray) {
        val schema = offeredSchemas.first { it.jsonObject.getValue("function").jsonObject.text("name") == name }
            .jsonObject.getValue("function").jsonObject.getValue("parameters").jsonObject
        validateCatalogingSchema(schema, args, name)
    }

    fun validateCandidate(candidate: JsonObject) {
        val kind = candidate.text("type")
        val schema = candidateSchema.getValue("anyOf").jsonArray.map { it.jsonObject }.firstOrNull {
            it.getValue("properties").jsonObject.getValue("type").jsonObject.strings("enum") == listOf(kind)
        } ?: error("未知建档候选类型：$kind")
        validateCatalogingSchema(schema, candidate, kind)
    }
}

/** Only structural validation. No names, prose or user intent are interpreted here. */
internal fun validateCatalogingSchema(schema: JsonObject, value: JsonElement, path: String) {
    val variants = schema["anyOf"] as? JsonArray
    if (variants != null) {
        require(variants.any { runCatching { validateCatalogingSchema(it.jsonObject, value, path) }.isSuccess }) {
            "$path 不符合任何允许的字段结构"
        }
        return
    }
    val types = when (val type = schema["type"]) {
        is JsonArray -> type.map { it.jsonPrimitive.content }
        is JsonPrimitive -> listOf(type.content)
        else -> emptyList()
    }
    val valid = types.isEmpty() || types.any { type -> when (type) {
        "null" -> value == JsonNull
        "object" -> value is JsonObject
        "array" -> value is JsonArray
        "string" -> value is JsonPrimitive && value != JsonNull && value.isString
        "boolean" -> value is JsonPrimitive && !value.isString && value.booleanOrNull != null
        "integer" -> value is JsonPrimitive && !value.isString && value.longOrNull != null
        "number" -> value is JsonPrimitive && !value.isString && value.doubleOrNull?.isFinite() == true
        else -> false
    } }
    require(valid) { "$path 类型必须是 ${types.joinToString("/")}" }
    (schema["enum"] as? JsonArray)?.let { require(value in it) { "$path 必须使用枚举 $it" } }
    if (value is JsonObject) {
        val props = schema["properties"] as? JsonObject ?: JsonObject(emptyMap())
        schema.strings("required").forEach { require(it in value) { "$path.$it 必填" } }
        if (schema["additionalProperties"] == JsonPrimitive(false)) {
            require(value.keys.all { it in props }) { "$path 包含未知字段：${value.keys - props.keys}" }
        }
        value.forEach { (key, item) -> (props[key] as? JsonObject)?.let { validateCatalogingSchema(it, item, "$path.$key") } }
    }
    if (value is JsonArray) {
        require(value.size >= schema.number("minItems", 0) && value.size <= schema.number("maxItems", Int.MAX_VALUE)) {
            "$path 数组长度超出契约范围"
        }
        if (schema["uniqueItems"] == JsonPrimitive(true)) require(value.distinct().size == value.size) { "$path 不能重复" }
        (schema["items"] as? JsonObject)?.let { itemSchema -> value.forEachIndexed { i, item ->
            validateCatalogingSchema(itemSchema, item, "$path[$i]")
        } }
    }
    if (value is JsonPrimitive && value.isString) {
        require(value.content.length >= schema.number("minLength", 0) &&
            value.content.length <= schema.number("maxLength", Int.MAX_VALUE)) { "$path 文本长度超出契约范围" }
        schema.text("pattern").takeIf(String::isNotBlank)?.let {
            require(Regex(it).containsMatchIn(value.content)) { "$path 格式无效" }
        }
    }
    if (value is JsonPrimitive && !value.isString) value.doubleOrNull?.let { number ->
        (schema["minimum"] as? JsonPrimitive)?.doubleOrNull?.let { require(number >= it) { "$path 小于 $it" } }
        (schema["maximum"] as? JsonPrimitive)?.doubleOrNull?.let { require(number <= it) { "$path 大于 $it" } }
    }
}

internal fun JsonObject.text(key: String): String = (get(key) as? JsonPrimitive)?.contentOrNull.orEmpty()
internal fun JsonObject.number(key: String, default: Int = 0): Int = (get(key) as? JsonPrimitive)?.intOrNull ?: default
internal fun JsonObject.objects(key: String): List<JsonObject> = (get(key) as? JsonArray).orEmpty().map { it.jsonObject }
internal fun JsonObject.strings(key: String): List<String> = (get(key) as? JsonArray).orEmpty().map { it.jsonPrimitive.content }
internal fun JsonObject.obj(key: String): JsonObject = get(key) as? JsonObject ?: JsonObject(emptyMap())
internal fun jsonStrings(values: Iterable<String>): JsonArray = JsonArray(values.map(::JsonPrimitive))
internal fun catalogingHash(text: String): String = MessageDigest.getInstance("SHA-256")
    .digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
internal fun catalogingId(vararg parts: String): String = UUID.nameUUIDFromBytes(parts.joinToString("|").toByteArray()).toString()

internal data class CatalogRecord(val entityType: String, val id: String, val payload: JsonObject) {
    val recordType: String get() = payload.text("_record_type").ifBlank {
        when (entityType) { "world" -> "world_entry"; "outline" -> "outline_node"; else -> entityType }
    }
    fun toJson() = buildJsonObject { put("entity_type", entityType); put("id", id); put("payload", payload) }
    companion object {
        fun fromJson(value: JsonObject) = CatalogRecord(value.text("entity_type"), value.text("id"), value.getValue("payload").jsonObject)
    }
}

internal fun catalogRecords(projectId: String, rows: List<ReplicaEntity>): List<CatalogRecord> = rows
    .filter { it.projectId == projectId && it.operation == "upsert" }
    .map { row ->
        require(!row.conflicted) { "作品包含未解决的同步冲突，请先处理后再建档" }
        CatalogRecord(row.entityType, row.entityId, Json.parseToJsonElement(requireNotNull(row.payloadJson)).jsonObject)
    }

internal fun catalogingFingerprint(records: List<CatalogRecord>): String = catalogingHash(mobileCanonicalJson(
    JsonArray(records.sortedWith(compareBy({ it.entityType }, { it.id })).map(CatalogRecord::toJson)),
))
