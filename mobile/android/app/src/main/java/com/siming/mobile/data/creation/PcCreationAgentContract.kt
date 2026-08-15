package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.agent.PcPromptContract
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

    val maxIterations: Int = agent.string("max_iterations").toIntOrNull() ?: 6
    val toolSchemas: JsonArray = agent["tool_schemas"] as? JsonArray ?: JsonArray(emptyList())
    val toolNames: Set<String> = (agent["tool_names"] as? JsonArray)
        .orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        .toSet()
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

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}