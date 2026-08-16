package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.agent.PcPromptContract
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal data class PcCreationPreset(
    val id: String,
    val label: String,
    val description: String,
    val themes: List<Pair<String, String>>,
)

/** Exact build-generated projection of the PC V3 novel-creation PromptSpec. */
internal class PcCreationPromptContract private constructor(
    private val creation: JsonObject,
) {
    private val json = Json { ignoreUnknownKeys = true }

    constructor(context: Context) : this(
        context.assets.open(PcPromptContract.ASSET_NAME)
            .bufferedReader(Charsets.UTF_8)
            .use { reader -> parseCreation(reader.readText()) },
    )

    internal constructor(contractJson: String) : this(parseCreation(contractJson))

    val schemaVersion: Int = creation.primitive("schema_version").toIntOrNull() ?: 3
    val stageOrder: List<String> = (creation["stage_order"] as JsonArray)
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
    val stageLabels: Map<String, String> = (creation["stage_labels"] as JsonObject)
        .mapValues { (_, value) -> (value as? JsonPrimitive)?.contentOrNull.orEmpty() }
    val impactDependencies: Map<String, List<String>> =
        creation.objectValue("impact_dependencies").mapValues { (_, value) ->
            (value as? JsonArray)
                .orEmpty()
                .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        }
    val presets: List<PcCreationPreset> =
        (creation.objectValue("presets")["categories"] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }
            .map { category ->
                PcCreationPreset(
                    id = category.string("id"),
                    label = category.string("label"),
                    description = category.string("description"),
                    themes = (category["themes"] as? JsonArray)
                        .orEmpty()
                        .mapNotNull { it as? JsonObject }
                        .map { theme -> theme.string("id") to theme.string("label") },
                )
            }

    fun conceptMessages(session: JsonObject, instruction: String = ""): Pair<String, String> {
        val draft = session.draft()
        val mode = if (draft.string("creation_mode") == "author_led") "author_led" else "explore"
        val conceptState = draft.stages().objectValue("concepts")
        val context = buildJsonObject {
            put("brief", session.string("user_brief"))
            put("form", draft.objectValue("form"))
            put("author_source", authorSource(draft))
            put("current_stage_data", conceptState["data"] ?: JsonNull)
            put("interview_history", draft["agent_history"] ?: JsonArray(emptyList()))
            put("interview_reason", "")
            put("refinement_instruction", instruction)
            put("entity_target", JsonNull)
        }
        val taskKind = creation.objectValue("concept_task_kinds").string(mode)
        val taskRules = creation.objectValue("concept_task_rules").string(mode)
        val system = creation.string("stage_system_template").fill(
            "task_kind" to taskKind,
            "task_rules" to taskRules,
        )
        val user = creation.objectValue("concept_user_intros").string(mode) +
            "输出结构：${creation.string("concept_shape_json")}\n" +
            "作者上下文：${pythonJson(context)}"
        return system to user
    }

    fun stageMessages(
        session: JsonObject,
        stage: String,
        baseline: JsonObject,
        instruction: String = "",
    ): Pair<String, String> {
        val draft = session.draft()
        val label = stageLabels.getValue(stage)
        val confirmed = buildJsonObject {
            draft.stages().forEach { (name, rawState) ->
                val state = rawState as? JsonObject ?: return@forEach
                if (state.string("status") == "confirmed") {
                    state["data"]?.let { put(name, it) }
                }
            }
        }
        val context = buildJsonObject {
            put("form", draft.objectValue("form"))
            put("author_source", authorSource(draft))
            put("selected_concept_id", draft["selected_concept_id"] ?: JsonNull)
            put("current_stage_data", draft.stages().objectValue(stage)["data"] ?: JsonNull)
            put("confirmed_stages", confirmed)
            put("baseline", baseline)
            put("refinement_instruction", instruction)
            put("entity_target", JsonNull)
        }
        val system = creation.string("stage_system_template").fill(
            "task_kind" to "深化阶段：$label",
            "task_rules" to creation.string("stage_task_rules"),
        )
        val contract = creation.objectValue("stage_contracts").string(stage)
        val prefix = creation.string("stage_user_prefix").fill(
            "stage_label" to label,
            "stage_contract" to contract,
        )
        val user = prefix +
            (if (instruction.isBlank()) "" else "作者本次调整要求：${instruction.trim()}\n") +
            "上下文：${pythonJson(context)}"
        return system to user
    }

    fun stageContract(stage: String): String =
        creation.objectValue("stage_contracts").string(stage)

    fun presetDefaults(presetId: String): JsonObject = preset(presetId)?.objectValue("defaults")
        ?: JsonObject(emptyMap())

    fun presetLabel(presetId: String): String = preset(presetId)?.string("label").orEmpty()

    private fun preset(presetId: String): JsonObject? =
        (creation.objectValue("presets")["categories"] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }
            .firstOrNull { it.string("id") == presetId }

    fun repairMessages(raw: String, error: String, stage: String): Pair<String, String> {
        val structure = if (stage == "concepts") {
            "顶层 concepts 数组必须恰好包含 1 张卡，字段与示例完全一致"
        } else {
            stageContract(stage)
        }
        return creation.string("repair_system_prompt") to
            creation.string("repair_user_template").fill(
                "contract" to structure,
                "error" to error.take(1_000),
                "raw" to raw.take(120_000),
            )
    }

    private fun authorSource(draft: JsonObject) = buildJsonObject {
        put("creation_mode", draft.string("creation_mode").ifBlank { "explore" })
        put("author_brief", draft.string("author_brief"))
        put("author_outline", draft.string("author_outline"))
        put("locked_requirements", draft["locked_requirements"] ?: buildJsonArray {})
    }

    private fun JsonObject.draft(): JsonObject = objectValue("draft")
    private fun JsonObject.stages(): JsonObject = objectValue("stages")
    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.primitive(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun String.fill(vararg values: Pair<String, String>): String =
        values.fold(this) { current, (name, value) ->
            current.replace("{{${name}}}", value).replace("{${name}}", value)
        }

    private fun pythonJson(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.joinToString(prefix = "{", postfix = "}", separator = ", ") { (key, child) ->
            "${json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(key))}: ${pythonJson(child)}"
        }
        is JsonArray -> value.joinToString(prefix = "[", postfix = "]", separator = ", ") { pythonJson(it) }
        else -> value.toString()
    }

    private companion object {
        fun parseCreation(raw: String): JsonObject {
            val root = Json.parseToJsonElement(raw) as JsonObject
            return root["creation"] as JsonObject
        }
    }
}
