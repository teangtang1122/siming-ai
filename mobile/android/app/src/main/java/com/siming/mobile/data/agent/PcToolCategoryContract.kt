package com.siming.mobile.data.agent

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** Build-generated cross-entrypoint tool-category contract. */
internal class PcToolCategoryContract(root: JsonObject) {
    private val contract = root["tool_categories"] as? JsonObject
        ?: error("手机内置契约缺少 tool_categories；请重新生成移动端 Prompt 契约")
    private val categories = contract["categories"] as? JsonObject
        ?: error("手机内置契约缺少 tool_categories.categories")

    val controller: String = contract.string("controller")
    private val maxActiveCategories = contract.string("max_active_categories").toInt()
    val labels: Map<String, String> = categories.mapValues { (_, value) ->
        (value as? JsonObject)?.string("label").orEmpty()
    }
    private val toolNamesByCategory: Map<String, Set<String>> = categories.mapValues { (_, value) ->
        (((value as? JsonObject)?.get("tools") as? JsonArray).orEmpty())
            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
            .toSet()
    }

    fun normalize(raw: List<String>, eligibleNames: Set<String>): List<String> {
        val normalized = raw.map(String::trim).distinct()
        normalized.forEach {
            require(toolNamesByCategory[it]?.any(eligibleNames::contains) == true) { "当前任务未开放工具类别：$it" }
        }
        require(normalized.size <= maxActiveCategories) { "每个模型步骤最多开放 $maxActiveCategories 个工具类别，请按需分步切换" }
        return normalized
    }

    fun availableToolNames(activeCategories: List<String>, eligibleNames: Set<String>): Set<String> {
        val normalized = normalize(activeCategories, eligibleNames)
        return buildSet {
            add(controller)
            normalized.forEach { category ->
                addAll(toolNamesByCategory.getValue(category).intersect(eligibleNames))
            }
        }
    }

    fun toolSchemas(
        allSchemas: JsonArray,
        activeCategories: List<String>,
        eligibleNames: Set<String>,
    ): JsonArray {
        val allowed = availableToolNames(activeCategories, eligibleNames)
        return JsonArray(allSchemas.filter { schema -> schema.toolName() in allowed })
    }

    fun selectionResult(activeCategories: List<String>, eligibleNames: Set<String>): JsonObject {
        val normalized = normalize(activeCategories, eligibleNames)
        val selectedLabels = normalized.mapNotNull(labels::get)
        val toolCount = availableToolNames(normalized, eligibleNames).size - 1
        return buildJsonObject {
            put("tool", controller)
            put("status", "ok")
            put(
                "detail",
                if (selectedLabels.isEmpty()) "已关闭全部业务工具"
                else "已准备${selectedLabels.joinToString("、")}能力，共 $toolCount 项可用工具",
            )
            put("data", buildJsonObject {
                put("enabled_categories", JsonArray(normalized.map(::JsonPrimitive)))
                put("labels", JsonArray(selectedLabels.map(::JsonPrimitive)))
                put("available_tool_count", toolCount)
            })
        }
    }

    private fun JsonElement.toolName(): String {
        val function = (this as? JsonObject)?.get("function") as? JsonObject
        return function?.string("name").orEmpty()
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
