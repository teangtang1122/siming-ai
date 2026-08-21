package com.siming.mobile.data.creation

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal object CreationWorkbenchContract {
    val requiredArchiveStages: List<String> = listOf(
        "constraints",
        "concepts",
        "world_style",
        "characters",
        "locations",
        "macro_outline",
    )

    fun recommendedStage(session: JsonObject, visibleOrder: List<String>): String {
        val current = session.stringValue("current_stage")
        if (current in visibleOrder && session.stageStatus(current) != "confirmed") return current
        return visibleOrder.firstOrNull { session.stageStatus(it) in setOf("generated", "stale", "conflict") }
            ?: visibleOrder.firstOrNull { session.stageStatus(it) != "confirmed" }
            ?: visibleOrder.lastOrNull()
            .orEmpty()
    }

    fun nextStage(visibleOrder: List<String>, current: String): String? {
        val index = visibleOrder.indexOf(current)
        return visibleOrder.getOrNull(index + 1)
    }

    fun stageCanGenerate(session: JsonObject, stage: String): Boolean =
        stage != "constraints" && session.stageStatus(stage) in setOf("pending", "generated", "stale", "conflict", "confirmed")

    fun stageCanConfirm(session: JsonObject, stage: String): Boolean {
        val data = session.stageData(stage)
        if (data.isEmpty()) return false
        if (stage != "concepts") return session.stageStatus(stage) != "confirmed"
        val selected = selectedConceptId(session, data)
        return selected.isNotBlank() && (data["options"] as? JsonArray).orEmpty().isNotEmpty()
    }

    fun selectedConceptId(session: JsonObject, data: JsonObject = session.stageData("concepts")): String =
        data.stringValue("selected_concept_id")
            .ifBlank { session.draft().stringValue("selected_concept_id") }
            .ifBlank {
                ((data["options"] as? JsonArray)?.firstOrNull() as? JsonObject)
                    ?.stringValue("id")
                    .orEmpty()
            }

    fun conceptDataWithSelection(data: JsonObject, selectedId: String): JsonObject =
        JsonObject(data.toMutableMap().apply {
            put("selected_concept_id", JsonPrimitive(selectedId))
        })

    fun canArchive(session: JsonObject): Boolean {
        if (requiredArchiveStages.any { session.stageStatus(it) != "confirmed" }) return false
        val reviewStatus = session.stageStatus("final_review")
        if (reviewStatus !in setOf("generated", "confirmed")) return false
        return (session.stageData("final_review")["ready"] as? JsonPrimitive)?.booleanOrNull == true
    }

    fun archiveBlockers(
        session: JsonObject,
        labels: Map<String, String>,
    ): List<String> {
        val blockers = requiredArchiveStages
            .filter { session.stageStatus(it) != "confirmed" }
            .map { "请先确认${labels[it] ?: if (it == "constraints") "创作约束" else it}" }
            .toMutableList()
        val review = session.stageData("final_review")
        val reviewStatus = session.stageStatus("final_review")
        if (reviewStatus !in setOf("generated", "confirmed")) {
            blockers += "请先生成${labels["final_review"] ?: "最终审阅"}"
        } else if ((review["ready"] as? JsonPrimitive)?.booleanOrNull != true) {
            val detailed = (review["blocking"] as? JsonArray)
                .orEmpty()
                .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
                .filter(String::isNotBlank)
            blockers += detailed.ifEmpty { listOf("最终审阅尚未通过") }
        }
        return blockers.distinct()
    }

    private fun JsonObject.draft(): JsonObject = objectValue("draft")
    private fun JsonObject.stageState(stage: String): JsonObject =
        draft().objectValue("stages").objectValue(stage)
    private fun JsonObject.stageData(stage: String): JsonObject =
        stageState(stage)["data"] as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.stageStatus(stage: String): String =
        stageState(stage).stringValue("status").ifBlank { "pending" }
    private fun JsonObject.objectValue(name: String): JsonObject =
        get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.stringValue(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
