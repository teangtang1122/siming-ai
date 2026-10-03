package com.siming.mobile.data.agent

import android.content.Context
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal fun bindOutlineOutputNodeCount(template: JsonArray, batchCount: Int): JsonArray {
    require(batchCount in 1..OUTLINE_PROPOSAL_MAX_NODES) { "大纲规划数量超出契约范围" }
    val tool = template.single() as JsonObject
    val function = tool.getValue("function") as JsonObject
    val parameters = function.getValue("parameters") as JsonObject
    val properties = parameters.getValue("properties") as JsonObject
    val nodes = properties.getValue("nodes") as JsonObject
    val boundedNodes = JsonObject(nodes + mapOf(
        "minItems" to JsonPrimitive(batchCount), "maxItems" to JsonPrimitive(batchCount),
    ))
    val boundedProperties = JsonObject(properties + ("nodes" to boundedNodes))
    val boundedParameters = JsonObject(parameters + ("properties" to boundedProperties))
    val boundedFunction = JsonObject(function + ("parameters" to boundedParameters))
    return JsonArray(listOf(JsonObject(tool + ("function" to boundedFunction))))
}

internal fun mobileChapterLengthInstruction(minimumHanCharacters: Int?): String {
    if (minimumHanCharacters == null) return "充分展开场景与人物行动。"
    require(minimumHanCharacters > 0) { "章节篇幅参考必须为正整数" }
    return "本次篇幅参考为 $minimumHanCharacters 个汉字；充分展开场景，标点不计入汉字数。"
}

/** Runtime view of the build-generated PC PromptSpec and tool catalog. */
internal class PcPromptContract(context: Context) {
    private val json = Json { ignoreUnknownKeys = true }
    private val root = context.assets.open(ASSET_NAME).bufferedReader(Charsets.UTF_8).use { reader ->
        json.parseToJsonElement(reader.readText()) as JsonObject
    }

    val sourceHash: String = root.string("source_sha256")
    private val allToolSchemas: JsonArray = root["tool_schemas"] as JsonArray
    val toolNames: Set<String> = (root["tool_names"] as JsonArray)
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        .toSet()
    val toolCategories = PcToolCategoryContract(root)

    fun workspaceSystem(): String = root.string("workspace_system_template").fill(
        "outline_batch_count" to defaultOutlineBatchCount().toString(),
    )

    fun defaultOutlineBatchCount(): Int = (root.getValue("outline_generation") as JsonObject)
        .getValue("default_batch_count").toString().toInt()

    fun workspaceRuntimeSystem(
        project: JsonObject,
        chapterWritingState: JsonObject,
    ): String {
        val runtime = mobileWorkspaceRuntimeData(project, chapterWritingState, defaultOutlineBatchCount())
        return listOf(
            workspaceSystem().trim(),
            listOf(
                "[SERVER_WORKSPACE_RUNTIME_DATA]",
                "authority: server_supplied_data",
                "selected_text_instruction_priority: none",
                mobileCanonicalJson(runtime),
                "[/SERVER_WORKSPACE_RUNTIME_DATA]",
            ).joinToString("\n"),
            root.string("chapter_writing_state_instruction"),
        ).joinToString("\n\n")
    }

    fun toolSchemas(activeCategories: List<String>): JsonArray = toolCategories.toolSchemas(
        allSchemas = allToolSchemas,
        activeCategories = activeCategories,
        eligibleNames = toolNames,
    )

    fun availableToolNames(activeCategories: List<String>): Set<String> =
        toolCategories.availableToolNames(activeCategories, toolNames)

    fun styleContext(project: JsonObject): String {
        val short = project.boolean("short_sentences")
        val rhetoric = project.string("rhetoric_guidelines")
        val custom = project.string("custom_style_prompt")
        val key = "short=$short;rhetoric=${rhetoric.isNotBlank()};custom=${custom.isNotBlank()}"
        val templates = root["style_templates"] as JsonObject
        val perspective = when (project.string("narrative_perspective")) {
            "first_person" -> "第一人称"
            "omniscient" -> "上帝视角"
            else -> "第三人称"
        }
        val writingStyle = when (project.string("writing_style")) {
            "vivid" -> "华丽生动"
            "concise" -> "白描简洁"
            "serious" -> "严肃"
            "humorous" -> "幽默"
            "poetic" -> "诗意"
            else -> "自然"
        }
        return templates.string(key).fill(
            "perspective" to perspective,
            "writing_style" to writingStyle,
            "rhetoric_guidelines" to rhetoric,
            "custom_style_prompt" to custom,
        )
    }

    fun chapterMessages(
        project: JsonObject,
        outlineContext: String,
        worldContext: String,
        characterProfiles: String,
        recentSummaries: String,
        requirements: String,
        sourceDraft: String = "",
        minimumHanCharacters: Int? = null,
    ): List<JsonObject> {
        val chapter = root["chapter"] as JsonObject
        val style = styleContext(project)
        val systemTemplate = chapter.string("quality_system_template")
        val system = systemTemplate.fill(
            "style_context" to style,
        )
        val template = if (sourceDraft.isBlank()) "user_template" else "revision_user_template"
        var user = chapter.string(template).fill(
            "requirements" to requirements,
            "outline_context" to outlineContext,
            "world_context" to worldContext,
            "character_profiles" to characterProfiles,
            "recent_summaries" to recentSummaries,
            "source_draft" to sourceDraft,
            "length_instruction" to mobileChapterLengthInstruction(minimumHanCharacters),
        )
        if (requirements.isBlank()) {
            user = user.replace("【写作要求】\n\n\n\n", "")
        }
        return listOf(message("system", system), message("user", user))
    }

    fun writerSystem(kind: String, styleContext: String, dimension: String = "culture"): String {
        val systems = root["writer_systems"] as JsonObject
        val template = if (kind == "world") {
            (systems["world"] as JsonObject).string(dimension)
        } else {
            systems.string(kind)
        }
        return template.fill("style_context" to styleContext)
    }

    fun writerOutputTool(kind: String): JsonArray = JsonArray(
        listOf((root["writer_output_tools"] as JsonObject).getValue(kind)),
    )

    fun outlineWriterOutputTool(batchCount: Int): JsonArray =
        bindOutlineOutputNodeCount(writerOutputTool("outline"), batchCount)

    fun outlineWriterIdleTimeoutMillis(): Long =
        (root.getValue("outline_generation") as JsonObject)
            .getValue("stream_idle_timeout_ms").toString().toLong()

    fun outlineWriterExtraBody(model: String): JsonObject? =
        outlineGenerationExtraBody(root.getValue("outline_generation") as JsonObject, model)

    fun characterWriterUser(
        requirements: String,
        name: String,
        roleType: String,
        worldContext: String,
        existingCharacters: String,
    ): String {
        val existing = existingCharacters.isNotBlank() && existingCharacters != "暂无角色。"
        val key = "requirements=${requirements.isNotBlank()};name=${name.isNotBlank()};" +
            "role=${roleType.isNotBlank()};existing=$existing"
        val templates = (root["writer_user_templates"] as JsonObject)["character"] as JsonObject
        return templates.string(key).fill(
            "requirements" to requirements,
            "name" to name,
            "role_type" to roleType,
            "world_context" to worldContext,
            "existing_characters" to existingCharacters,
        )
    }

    fun outlineWriterUser(
        taskContext: String,
        batchCount: Int,
    ): String {
        val templates = (root["writer_user_templates"] as JsonObject)["outline"] as JsonObject
        return templates.string("governed").fill(
            "task_context" to taskContext,
            "batch_count" to batchCount.toString(),
        )
    }

    fun worldWriterUser(
        requirements: String,
        title: String,
        dimension: String,
        worldContext: String,
    ): String {
        val normalizedDimension = dimension.takeIf { it in WORLD_DIMENSIONS } ?: "culture"
        val key = "requirements=${requirements.isNotBlank()};title=${title.isNotBlank()};" +
            "dimension=$normalizedDimension"
        val templates = (root["writer_user_templates"] as JsonObject)["world"] as JsonObject
        return templates.string(key).fill(
            "requirements" to requirements,
            "title" to title,
            "world_context" to worldContext,
        )
    }

    private fun message(role: String, content: String) = JsonObject(
        mapOf("role" to JsonPrimitive(role), "content" to JsonPrimitive(content)),
    )

    private fun String.fill(vararg values: Pair<String, String>): String =
        values.fold(this) { current, (key, value) -> current.replace("{{${key}}}", value) }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

    private fun JsonObject.boolean(name: String): Boolean =
        (get(name) as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() ?: false

    companion object {
        const val ASSET_NAME = "pc_workspace_prompt_contract.json"
        private val WORLD_DIMENSIONS = setOf(
            "geography",
            "history",
            "factions",
            "power_system",
            "races",
            "culture",
        )
    }
}
