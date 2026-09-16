package com.siming.mobile.data.agent

import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.orderReplicaEntities
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

internal fun mobileWorkspaceRuntimeData(project: JsonObject, chapterWritingState: JsonObject): JsonObject =
    buildJsonObject {
        put("schema", "workspace_assistant_runtime.v1")
        put("data_only", true)
        put("project", buildJsonObject {
            put("id", project.text("id"))
            put("title", project.text("title"))
        })
        put("editor_selection", JsonNull)
        put("active_chapter_draft", chapterWritingState["pending_draft"] ?: JsonNull)
        put("chapter_writing_state", chapterWritingState)
        put("outline_batch_count", 3)
    }

/** Persisted state only: these checks never interpret the author's message or choose a target. */
internal fun mobileChapterWritingState(
    projectId: String,
    snapshot: List<ReplicaEntity>,
    pendingDraft: JsonObject?,
): JsonObject {
    val chapters = writingRecords(projectId, snapshot, "chapter")
        .filter { it.text("content").isNotBlank() }
    val required = chapters.firstOrNull { it.catalogingRequired() == true }
    val unknown = chapters.firstOrNull { it.catalogingRequired() == null }
    return buildJsonObject {
        put("pending_draft", pendingDraft?.let { draft ->
            buildJsonObject {
                put("id", draft.text("draft_id"))
                put("title", draft.text("title"))
                put("outline_node_id", draft["outline_node_id"] ?: JsonNull)
                put("draft_kind", draft.text("draft_kind").ifBlank { "new" })
                put("target_chapter_id", draft["target_chapter_id"] ?: JsonNull)
                put("status", "pending")
                put("instruction_priority", "none")
            }
        } ?: JsonNull)
        put("blocking_cataloging_job", JsonNull)
        put("cataloging_required_chapter", required?.chapterState() ?: JsonNull)
        // Imported packages restore this state before use. Other missing state stays explicit.
        put("cataloging_state_unknown_chapter", unknown?.chapterState() ?: JsonNull)
    }
}

internal fun isPendingMobileChapterDraft(
    projectId: String,
    snapshot: List<ReplicaEntity>,
    draft: JsonObject,
): Boolean {
    if (draft.text("project_id") != projectId || draft.text("draft_id").isBlank()) return false
    if (draft.text("draft_status") !in setOf("pending", "generated", "generating")) return false
    if (draft.text("saved_chapter_id").isNotBlank()) return false
    if (draft.text("draft_kind") == "revision") return true
    return existingMobileChapterIdForOutline(
        writingRecords(projectId, snapshot, "chapter"), draft.text("outline_node_id"),
    ) == null
}

internal fun mobileImportedChapterDraft(projectId: String, snapshot: List<ReplicaEntity>, draftId: String? = null): JsonObject? =
    writingRecords(projectId, snapshot, "chapter_draft").asReversed().asSequence()
        .filter { draftId == null || it.text("id") == draftId }
        .map { payload ->
            val target = writingRecords(projectId, snapshot, "chapter").firstOrNull { it.text("id") == payload.text("target_chapter_id") }
            JsonObject(payload + mapOf(
                "draft_id" to JsonPrimitive(payload.text("id")),
                "content_ref" to JsonPrimitive(payload.text("id")),
                "draft_status" to JsonPrimitive(payload.text("status")),
                "execution_route" to JsonPrimitive("project_package"),
            ) + if (payload.text("draft_kind") == "revision") mapOf(
                "target_chapter_current_version" to (target?.get("current_version") ?: JsonNull),
                "target_chapter_title" to (target?.get("title") ?: JsonNull),
                "target_chapter_content" to (target?.get("content") ?: JsonNull),
            ) else emptyMap())
        }
        .firstOrNull { isPendingMobileChapterDraft(projectId, snapshot, it) }

/** A local content edit invalidates cataloging just as a canonical chapter save does. */
internal fun mobileChapterPayloadForSave(current: JsonObject?, payload: JsonObject): JsonObject {
    val merged = JsonObject(current.orEmpty() + payload)
    val content = merged.text("content")
    val semanticChange = current == null || listOf("content", "title", "outline_node_id").any { current.text(it) != merged.text(it) }
    val required = when {
        content.isBlank() -> false
        semanticChange -> true
        else -> current?.catalogingRequired()
    }
    val version = (current?.get("current_version") as? JsonPrimitive)?.intOrNull ?: 1
    return JsonObject((merged - "cataloging_required") +
        (required?.let { mapOf("cataloging_required" to JsonPrimitive(it)) } ?: emptyMap()) +
        mapOf("current_version" to JsonPrimitive(if (current != null) version + 1 else version),
            "word_count" to JsonPrimitive(content.count { !it.isWhitespace() })))
}

internal fun mobileCatalogingBlockReason(state: JsonObject, sourceDraftId: String): String? = when {
    sourceDraftId.isNotBlank() -> null
    state["cataloging_required_chapter"] is JsonObject ->
        "正式章节当前版本尚未完成建档；完成建档后才能生成下一章。"
    state["cataloging_state_unknown_chapter"] is JsonObject ->
        "当前章节缺少可核验的建档状态；请导入包含当前版本快照和建档摘要的完整项目包，或完成该章建档。"
    else -> null
}

private fun writingRecords(
    projectId: String,
    snapshot: List<ReplicaEntity>,
    entityType: String,
): List<JsonObject> = orderReplicaEntities(entityType, snapshot.filter {
    it.projectId == projectId && it.entityType == entityType && it.operation == "upsert"
}).asSequence()
    .mapNotNull { entity ->
        val payload = entity.payloadJson?.let {
            runCatching { Json.parseToJsonElement(it) as? JsonObject }.getOrNull()
        } ?: return@mapNotNull null
        if (payload.text("project_id").let { it.isNotBlank() && it != projectId }) return@mapNotNull null
        JsonObject(payload + mapOf("id" to JsonPrimitive(entity.entityId), "project_id" to JsonPrimitive(projectId)))
    }.toList()

private fun JsonObject.chapterState(): JsonObject = buildJsonObject {
    put("id", getValue("id"))
    put("title", text("title"))
    put("current_version", get("current_version") ?: JsonNull)
    put("cataloging_required", get("cataloging_required") ?: JsonNull)
}

private fun JsonObject.catalogingRequired(): Boolean? =
    (get("cataloging_required") as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull

private fun JsonObject.text(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
