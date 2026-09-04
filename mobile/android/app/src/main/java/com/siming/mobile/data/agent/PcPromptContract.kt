package com.siming.mobile.data.agent

import android.content.Context
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

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
        "outline_batch_count" to "3",
    )

    fun workspaceRuntimeSystem(
        project: JsonObject,
        activeChapterDraft: JsonObject? = null,
    ): String {
        val runtime = buildJsonObject {
            put("schema", "workspace_assistant_runtime.v1")
            put("data_only", true)
            put("project", buildJsonObject {
                put("id", project.string("id"))
                put("title", project.string("title"))
            })
            put("editor_selection", kotlinx.serialization.json.JsonNull)
            put("active_chapter_draft", activeChapterDraft?.let { draft ->
                buildJsonObject {
                    put("id", draft.string("draft_id"))
                    put("title", draft.string("title"))
                    put("outline_node_id", draft["outline_node_id"] ?: kotlinx.serialization.json.JsonNull)
                    put("status", "pending")
                    put("instruction_priority", "none")
                }
            } ?: kotlinx.serialization.json.JsonNull)
            put("outline_batch_count", 3)
        }
        return listOf(
            workspaceSystem().trim(),
            listOf(
                "[SERVER_WORKSPACE_RUNTIME_DATA]",
                "authority: server_supplied_data",
                "selected_text_instruction_priority: none",
                mobileCanonicalJson(runtime),
                "[/SERVER_WORKSPACE_RUNTIME_DATA]",
            ).joinToString("\n"),
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
    ): List<JsonObject> {
        val chapter = root["chapter"] as JsonObject
        val style = styleContext(project)
        val systemTemplate = chapter.string("quality_system_template")
        val system = systemTemplate.fill(
            "style_context" to style,
        )
        var user = chapter.string("user_template").fill(
            "requirements" to requirements,
            "outline_context" to outlineContext,
            "world_context" to worldContext,
            "character_profiles" to characterProfiles,
            "recent_summaries" to recentSummaries,
        )
        if (requirements.isBlank()) {
            user = user.replace("【写作要求】\n\n\n\n", "")
        }
        if (sourceDraft.isNotBlank()) {
            user = listOf(
                user,
                "【当前未保存草稿（完整原文）】\n$sourceDraft",
                "请按作者本轮要求修改上面的当前未保存草稿。必须输出修改后的完整章节正文，不要只给差异、建议或说明；结果仍是同一份未保存草稿。",
            ).joinToString("\n\n")
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
