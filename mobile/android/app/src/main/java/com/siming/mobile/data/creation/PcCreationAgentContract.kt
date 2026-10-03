package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.agent.PcPromptContract
import com.siming.mobile.data.agent.PcToolCategoryContract
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

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
    private val replyContract = agent["reply_contract"] as? JsonObject
        ?: error("手机内置契约缺少立项 reply_contract；请重新生成移动端 Prompt 契约")
    private val writeResultContract = agent["write_result_contract"] as? JsonObject
        ?: error("手机内置契约缺少立项 write_result_contract；请重新生成移动端 Prompt 契约")
    val writeResultMaxBytes = writeResultContract.string("max_json_bytes").toInt().also {
        require(it > 0) { "立项写入回执容量无效" }
    }
    private val writeResultMaxBytesByTool = writeResultContract["max_json_bytes_by_tool"] as? JsonObject
        ?: error("手机内置契约缺少逐工具写入回执容量；请重新生成移动端 Prompt 契约")

    fun writeResultMaxBytesFor(tool: String): Int =
        (writeResultMaxBytesByTool[tool] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()
            ?.takeIf { it > 0 }
            ?: error("手机内置契约缺少 $tool 的写入回执容量")
    val replyInstruction = requiredReplyString("instruction")
    val readOnlyCompletionInstruction = requiredReplyString("read_only_completion_instruction")
    val readOnlyNotice = requiredReplyString("read_only_notice")
    val replyRepairInstruction = requiredReplyString("repair_instruction")
    val replyFailureNotice = requiredReplyString("failure_notice")
    val maxReplyAttempts = requiredReplyString("max_attempts").toInt().also {
        require(it > 0) { "立项总结重试上限无效" }
    }
    private val replyToolMarkup = Regex(requiredReplyString("tool_markup_pattern"))
    private val creation: JsonObject = root["creation"] as? JsonObject ?: JsonObject(emptyMap())
    val entities = PcCreationEntityContract(creation)
    private val allToolSchemas: JsonArray = agent["tool_schemas"] as? JsonArray ?: JsonArray(emptyList())
    val toolCategories = PcToolCategoryContract(root)
    val categoryController: String = toolCategories.controller
    val toolNames: Set<String> = (agent["tool_names"] as? JsonArray)
        .orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        .toSet()
    val excludedPcToolNames: Set<String> = requiredToolNames("excluded_pc_tool_names")
    val capacityPreflightReadToolNames: Set<String> = requiredToolNames("capacity_preflight_read_tool_names")
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

    fun replyError(content: String, hasToolCalls: Boolean = false): String? = when {
        hasToolCalls -> "unexpected_tool_calls"
        content.isBlank() -> "empty_reply"
        replyToolMarkup.containsMatchIn(content) -> "tool_protocol_text"
        else -> null
    }

    fun projectWriteResultData(data: JsonObject?): JsonObject = buildJsonObject {
        val source = data ?: return@buildJsonObject
        val fields = writeResultContract["data_fields"] as JsonArray
        fields.forEach { field ->
            val name = (field as JsonPrimitive).content
            source[name]?.let { put(name, it) }
        }
        val objects = writeResultContract["object_projections"] as JsonArray
        objects.forEach { item ->
            val projection = item as JsonObject
            val name = projection.string("source_field")
            val nested = source[name] as? JsonObject ?: return@forEach
            put(name, buildJsonObject {
                (projection["fields"] as JsonArray).forEach { field ->
                    val member = (field as JsonPrimitive).content
                    nested[member]?.let { put(member, it) }
                }
            })
        }
    }

    fun referenceError(tool: String, reason: String, path: String): JsonObject = buildJsonObject {
        val details = agent["reference_diagnostics"] as? JsonObject
            ?: error("手机内置契约缺少 reference_diagnostics")
        put("tool", tool)
        put("status", "error")
        put("detail", details.string(reason).ifBlank { error("未知立项引用错误：$reason") })
        put("data", buildJsonObject {
            put("reason", reason)
            put("path", path)
            put("retryable", true)
        })
    }

    private fun requiredReplyString(field: String): String = replyContract.string(field).ifBlank {
        error("手机内置立项 reply_contract 缺少 $field")
    }

    fun normalizeCategories(raw: List<String>): List<String> = toolCategories.normalize(raw, toolNames)

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
