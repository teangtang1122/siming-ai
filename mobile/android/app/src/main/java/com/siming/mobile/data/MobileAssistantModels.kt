package com.siming.mobile.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

data class MobilePendingChapterDraft(
    val draftId: String,
    val projectId: String,
    val title: String,
    val content: String,
    val outlineNodeId: String? = null,
    val contextManifestId: String? = null,
    val status: String = "pending",
    val executionRoute: String = "gateway",
) {
    val generating: Boolean get() = status == "generating"

    companion object {
        fun fromJson(projectId: String, value: JsonObject): MobilePendingChapterDraft? {
            val id = value.string("draft_id").ifBlank { value.string("content_ref") }
            if (id.isBlank()) return null
            return MobilePendingChapterDraft(
                draftId = id,
                projectId = value.string("project_id").ifBlank { projectId },
                title = value.string("title").ifBlank { "AI 生成章节" },
                content = value.string("content"),
                outlineNodeId = value.string("outline_node_id").ifBlank { null },
                contextManifestId = value.string("context_manifest_id").ifBlank {
                    (value["context_snapshot"] as? JsonObject)?.string("context_manifest_id").orEmpty()
                }.ifBlank { null },
                status = value.string("draft_status").ifBlank { "pending" },
                executionRoute = value.string("execution_route").ifBlank {
                    (value["context_snapshot"] as? JsonObject)?.string("execution_route").orEmpty()
                }.ifBlank { "gateway" },
            )
        }
    }
}

data class MobileOutlineDraftNode(
    val nodeType: String = "chapter",
    val title: String,
    val summary: String,
    val parentTitle: String? = null,
    val characterNames: List<String> = emptyList(),
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("node_type", nodeType)
        put("title", title)
        put("summary", summary)
        parentTitle?.takeIf(String::isNotBlank)?.let { put("parent_title", it) }
        put("character_names", buildJsonArray { characterNames.forEach { add(JsonPrimitive(it)) } })
    }

    companion object {
        fun fromJson(value: JsonObject): MobileOutlineDraftNode = MobileOutlineDraftNode(
            nodeType = value.string("node_type").ifBlank { "chapter" },
            title = value.string("title"),
            summary = value.string("summary"),
            parentTitle = value.string("parent_title").ifBlank { null },
            characterNames = (value["character_names"] as? JsonArray)
                .orEmpty()
                .mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }
                .filter(String::isNotBlank),
        )
    }
}

internal fun mobileOutlineCharacterLinks(
    characterNames: List<String>,
    characterIdsByReference: Map<String, String>,
): JsonArray {
    val unresolved = mutableListOf<String>()
    val seenIds = mutableSetOf<String>()
    val links = characterNames.mapNotNull { rawReference ->
        val reference = rawReference.trim()
        val characterId = characterIdsByReference[reference]
        if (reference.isBlank() || characterId == null) {
            val label = reference.ifBlank { "<空名称>" }
            if (label !in unresolved) unresolved += label
            return@mapNotNull null
        }
        if (!seenIds.add(characterId)) return@mapNotNull null
        buildJsonObject {
            put("character_id", characterId)
            put("role_in_scene", "AI关联")
        }
    }
    require(unresolved.isEmpty()) {
        "未找到当前作品内的关联角色：${unresolved.joinToString("、")}"
    }
    return JsonArray(links)
}

data class MobilePendingOutlineDraft(
    val draftId: String,
    val projectId: String,
    val nodes: List<MobileOutlineDraftNode>,
    val designNotes: String = "",
    val parentId: String? = null,
    val insertAfterId: String? = null,
    val contextManifestId: String? = null,
    val contextSelectionDigest: String = "",
    val baseOutlineHash: String = "",
    val status: String = "pending",
    val executionRoute: String = "gateway",
) {
    companion object {
        fun fromJson(projectId: String, value: JsonObject): MobilePendingOutlineDraft? {
            val id = value.string("draft_id")
            if (id.isBlank()) return null
            return MobilePendingOutlineDraft(
                draftId = id,
                projectId = value.string("project_id").ifBlank { projectId },
                nodes = (value["nodes"] as? JsonArray)
                    .orEmpty()
                    .mapNotNull { (it as? JsonObject)?.let(MobileOutlineDraftNode::fromJson) },
                designNotes = value.string("design_notes"),
                parentId = value.string("parent_id").ifBlank { null },
                insertAfterId = value.string("insert_after_id").ifBlank { null },
                contextManifestId = value.string("context_manifest_id").ifBlank { null },
                contextSelectionDigest = value.string("context_selection_digest"),
                baseOutlineHash = value.string("base_outline_hash"),
                status = value.string("draft_status").ifBlank { "pending" },
                executionRoute = value.string("execution_route").ifBlank { "gateway" },
            )
        }
    }
}

data class MobileOutlineDraftConfirmation(
    val savedOutlineNodeIds: List<String>,
    val chapterOutlineNodeIds: List<String>,
    val nextAuthorMessage: String? = null,
)

data class MobileAssistantConversation(
    val id: String,
    val title: String,
    val messageCount: Int = 0,
    val updatedAt: String = "",
)

data class MobileAssistantMessage(
    val id: String,
    val role: String,
    val content: String,
    val status: String = "completed",
    val createdAt: String = "",
    val toolLogs: List<String> = emptyList(),
)

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
