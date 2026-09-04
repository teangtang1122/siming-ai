package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.agent.PcPromptContract
import com.siming.mobile.data.agent.PcToolCategoryContract
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** Build-generated projection of the current PC conversational creation Agent. */
internal class PcCreationAgentContract private constructor(
    private val root: JsonObject,
) {
    constructor(context: Context) : this(
        context.assets.open(PcPromptContract.ASSET_NAME)
            .bufferedReader(Charsets.UTF_8)
            .use { reader -> Json.parseToJsonElement(reader.readText()) as JsonObject },
    )

    internal constructor(contractJson: String) : this(Json.parseToJsonElement(contractJson) as JsonObject)

    private val agent: JsonObject = root["creation_agent"] as? JsonObject
        ?: error("手机内置契约缺少 creation_agent；请重新生成移动端 Prompt 契约")
    private val creation: JsonObject = root["creation"] as? JsonObject ?: JsonObject(emptyMap())
    private val allToolSchemas: JsonArray = agent["tool_schemas"] as? JsonArray ?: JsonArray(emptyList())
    val toolCategories = PcToolCategoryContract(root)
    val categoryController: String = toolCategories.controller
    val toolNames: Set<String> = (agent["tool_names"] as? JsonArray)
        .orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        .toSet()
    val excludedPcToolNames: Set<String> = requiredToolNames("excluded_pc_tool_names")
    val revisionToolNames: Set<String> = requiredToolNames("revision_tool_names")
    val writeToolNames: Set<String> = requiredToolNames("write_tool_names")
    val maxSuccessfulWritesPerTurn: Int = agent.string("max_successful_writes_per_turn")
        .toIntOrNull()
        ?.takeIf { it > 0 }
        ?: error("手机内置契约缺少有效的 max_successful_writes_per_turn")
    val maxFailedWritesPerTurn: Int = agent.string("max_failed_writes_per_turn")
        .toIntOrNull()
        ?.takeIf { it > 0 }
        ?: error("手机内置契约缺少有效的 max_failed_writes_per_turn")
    val stageOrder: List<String> = (creation["stage_order"] as? JsonArray)
        .orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
    val stageLabels: Map<String, String> = (creation["stage_labels"] as? JsonObject)
        .orEmpty()
        .mapValues { (_, value) -> (value as? JsonPrimitive)?.contentOrNull.orEmpty() }
    val impactDependencies: Map<String, List<String>> =
        ((creation["impact_dependencies"] as? JsonObject) ?: JsonObject(emptyMap())).mapValues { (_, value) ->
            (value as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        }

    fun systemPrompt(sessionId: String): String = agent.string("system_template")
        .replace("{{session_id}}", sessionId)
        .replace("{session_id}", sessionId)

    fun normalizeCategories(raw: List<String>): List<String> = toolCategories.normalize(raw)

    fun toolSchemas(activeCategories: List<String>): JsonArray = toolCategories.toolSchemas(
        allSchemas = allToolSchemas,
        activeCategories = activeCategories,
        eligibleNames = toolNames,
    )

    fun availableToolNames(activeCategories: List<String>): Set<String> =
        toolCategories.availableToolNames(activeCategories, toolNames)

    fun categoryResult(activeCategories: List<String>): JsonObject =
        toolCategories.selectionResult(activeCategories, toolNames)

    private fun requiredToolNames(field: String): Set<String> =
        (agent[field] as? JsonArray)
            .orEmpty()
            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
            .toSet()
            .takeIf { it.isNotEmpty() }
            ?: error("手机内置契约缺少 $field；请重新生成移动端 Prompt 契约")

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
