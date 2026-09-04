package com.siming.mobile.data.agent

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

internal const val MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES = 200
private const val MOBILE_TRANSCRIPT_MESSAGE_SCHEMA = "assistant_transcript_message.v1"
private const val MOBILE_TRANSCRIPT_IMPORT_KEY_SCHEMA = "assistant_transcript_import_key.v2"
private val MOBILE_TRANSCRIPT_ASSISTANT_STATUSES = setOf("completed", "error", "aborted", "cancelled")

private fun Char.isTranscriptTitleWhitespace(): Boolean = when (code) {
    in 0x0009..0x000D,
    in 0x001C..0x0020,
    0x0085,
    0x00A0,
    0x1680,
    in 0x2000..0x200A,
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
    -> true
    else -> false
}

internal fun normalizeMobileTranscriptImportTitle(value: String): String {
    var normalized = value.trim(Char::isTranscriptTitleWhitespace).replace("\r\n", " ")
    listOf('\r', '\n', '\u0085', '\u2028', '\u2029').forEach { lineBreak ->
        normalized = normalized.replace(lineBreak, ' ')
    }
    require(normalized.isNotEmpty()) { "transcript import title 不能为空" }
    require(normalized.codePointCount(0, normalized.length) <= 200) {
        "transcript import title 超出协议上限"
    }
    return normalized
}

internal data class MobileTranscriptImportMessage(
    val id: String,
    val sequenceNo: Long,
    val role: String,
    val content: String,
    val status: String,
) {
    init {
        require(id.isNotBlank() && id.length <= 36) { "transcript import message ID 无效" }
        require(sequenceNo >= 1L) { "transcript import sequence_no 无效" }
        require(role in setOf("user", "assistant")) { "transcript import role 无效" }
        require(content.isNotBlank() && content.length <= 1_000_000) {
            "transcript import 不能丢失或静默截断消息原文"
        }
        if (role == "user") {
            require(status == "completed") { "已闭合回合的 user 消息必须是 completed" }
        } else {
            require(status in MOBILE_TRANSCRIPT_ASSISTANT_STATUSES) {
                "transcript import assistant 回合尚未闭合"
            }
        }
    }

    fun canonicalRecord(): JsonObject = buildJsonObject {
        put("schema", MOBILE_TRANSCRIPT_MESSAGE_SCHEMA)
        put("id", id)
        put("sequence_no", sequenceNo)
        put("role", role)
        put("content", content)
        put("status", status)
    }

    val messageHash: String get() = mobileCanonicalSha256(canonicalRecord())

    fun toJson(): JsonObject = buildJsonObject {
        put("id", id)
        put("sequence_no", sequenceNo)
        put("role", role)
        put("content", content)
        put("status", status)
        put("message_hash", messageHash)
    }

    companion object {
        fun fromTranscript(message: MobileTranscriptMessage): MobileTranscriptImportMessage =
            MobileTranscriptImportMessage(
                id = message.id,
                sequenceNo = message.sequenceNo,
                role = message.role,
                content = message.content,
                status = message.status,
            )
    }
}

internal data class MobileTranscriptImportRequest(
    val clientConversationId: String?,
    val serverConversationId: String?,
    val transcriptRevision: Long,
    val idempotencyKey: String,
    val title: String?,
    val messages: List<MobileTranscriptImportMessage>,
) {
    init {
        require((clientConversationId == null) != (serverConversationId == null)) {
            "transcript import 必须且只能指定 client/server conversation ID 之一"
        }
        require(!clientConversationId.isNullOrBlank() || !serverConversationId.isNullOrBlank()) {
            "transcript import conversation ID 不能为空"
        }
        require(clientConversationId == null || clientConversationId.length <= 128) {
            "transcript import client_conversation_id 超出协议上限"
        }
        require(serverConversationId == null || serverConversationId.length <= 36) {
            "transcript import server_conversation_id 超出协议上限"
        }
        require(idempotencyKey.isNotBlank() && idempotencyKey.length <= 200) {
            "transcript import idempotency_key 无效"
        }
        require(title == null || title == normalizeMobileTranscriptImportTitle(title)) {
            "transcript import title 必须使用规范化值"
        }
        require(messages.isNotEmpty() && messages.size % 2 == 0 &&
            messages.size <= MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES
        ) { "transcript import 必须包含不超过 100 个完整回合" }
        require(messages.zipWithNext().all { (left, right) ->
            left.sequenceNo + 1L == right.sequenceNo
        }) { "transcript import sequence_no 必须连续" }
        require(messages.mapIndexed { index, message ->
            message.role == if (index % 2 == 0) "user" else "assistant"
        }.all { it }) { "transcript import 必须保持 user/assistant 完整配对" }
        require(messages.first().sequenceNo % 2L == 1L) {
            "transcript import 必须从 user sequence 开始"
        }
        require(transcriptRevision == messages.last().sequenceNo) {
            "transcript_revision 必须等于本批最后一条 sequence"
        }
    }

    fun toJson(): JsonObject = buildJsonObject {
        clientConversationId?.let { put("client_conversation_id", it) }
        serverConversationId?.let { put("server_conversation_id", it) }
        put("transcript_revision", transcriptRevision)
        put("idempotency_key", idempotencyKey)
        title?.takeIf(String::isNotBlank)?.let { put("title", it) }
        put("messages", buildJsonArray { messages.forEach { add(it.toJson()) } })
    }
}

internal data class MobileTranscriptImportReceipt(
    val conversationId: String,
    val transcriptRevision: Long,
    val appliedRevision: Long,
    val importedMessageCount: Int,
    val idempotent: Boolean,
) {
    init {
        require(conversationId.isNotBlank()) { "transcript import receipt 缺少 conversation_id" }
        require(transcriptRevision >= 0L && appliedRevision >= 0L && importedMessageCount >= 0) {
            "transcript import receipt revision 无效"
        }
        require(appliedRevision <= transcriptRevision) { "applied_revision 不能超过服务端 revision" }
    }

    companion object {
        fun fromJson(root: JsonObject): MobileTranscriptImportReceipt {
            val imported = root.requiredNumber("imported_message_count")
            if (imported !in 0L..MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES.toLong()) {
                throw MobileConversationStorageException("transcript import receipt 消息数无效")
            }
            return MobileTranscriptImportReceipt(
                conversationId = root.text("conversation_id"),
                transcriptRevision = root.requiredNumber("transcript_revision"),
                appliedRevision = root.requiredNumber("applied_revision"),
                importedMessageCount = imported.toInt(),
                idempotent = (root["idempotent"] as? JsonPrimitive)?.booleanOrNull
                    ?: throw MobileConversationStorageException("transcript import receipt 缺少 idempotent"),
            )
        }
    }
}

internal data class MobileTranscriptReplicaState(
    val revision: Long = 0L,
    val serverConversationId: String? = null,
    val confirmedSourceRevision: Long = 0L,
    val updatedAt: String = "",
) {
    init {
        require(revision >= 0L && confirmedSourceRevision >= 0L) { "transcript replica state 无效" }
        require(serverConversationId == null || serverConversationId.isNotBlank()) {
            "transcript replica server ID 无效"
        }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("revision", revision)
        serverConversationId?.let { put("server_conversation_id", it) }
        put("confirmed_source_revision", confirmedSourceRevision)
        put("updated_at", updatedAt)
    }

    companion object {
        fun fromJson(root: JsonObject): MobileTranscriptReplicaState = MobileTranscriptReplicaState(
            revision = root.number("revision", 0L),
            serverConversationId = root.text("server_conversation_id").ifBlank { null },
            confirmedSourceRevision = root.number("confirmed_source_revision", 0L),
            updatedAt = root.text("updated_at"),
        )
    }
}

/**
 * Builds one lossless transport batch. The 200-message wire cap is batching only;
 * it is never used to prune or overwrite the local transcript archive.
 */
internal fun buildMobileTranscriptImportRequest(
    projectId: String,
    clientConversationId: String,
    serverConversationId: String?,
    title: String,
    closedMessages: List<MobileTranscriptMessage>,
    confirmedSourceRevision: Long,
    maxMessages: Int = MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES,
): MobileTranscriptImportRequest? {
    val normalizedProjectId = projectId.trim()
    val normalizedClientId = clientConversationId.trim()
    val normalizedServerId = serverConversationId?.trim()?.takeIf(String::isNotEmpty)
    require(normalizedProjectId.isNotEmpty() && normalizedProjectId.length <= 36) {
        "transcript import project_id 无效"
    }
    require(normalizedClientId.isNotEmpty() && normalizedClientId.length <= 128) {
        "transcript import client_conversation_id 无效"
    }
    require(confirmedSourceRevision >= 0L && confirmedSourceRevision % 2L == 0L) {
        "confirmed transcript revision 必须停在完整回合边界"
    }
    require(maxMessages in 2..MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES && maxMessages % 2 == 0) {
        "transcript import batch 必须容纳 1..100 个完整回合"
    }
    val turns = mobileConversationTurns(closedMessages)
    require(turns.all(MobileConversationTurn::isClosed)) { "transcript import 不能包含未闭合回合" }
    require(closedMessages.map(MobileTranscriptMessage::sequenceNo) ==
        (1L..closedMessages.size.toLong()).toList()
    ) { "transcript import 源必须是从 sequence 1 开始的完整归档" }
    require(confirmedSourceRevision <= closedMessages.size.toLong()) {
        "服务端已确认 revision 超过当前本地归档"
    }
    if (confirmedSourceRevision == closedMessages.size.toLong()) return null
    val batch = closedMessages
        .drop(confirmedSourceRevision.toInt())
        .take(maxMessages)
        .map(MobileTranscriptImportMessage::fromTranscript)
    val lastSequence = batch.last().sequenceNo
    val clientId = normalizedClientId.takeIf { normalizedServerId == null }
    val normalizedTitle = normalizeMobileTranscriptImportTitle(title)
    val keyPayload = buildJsonObject {
        put("schema", MOBILE_TRANSCRIPT_IMPORT_KEY_SCHEMA)
        put("project_id", normalizedProjectId)
        put(
            "client_conversation_id",
            clientId?.let(::JsonPrimitive) ?: kotlinx.serialization.json.JsonNull,
        )
        put(
            "server_conversation_id",
            normalizedServerId?.let(::JsonPrimitive) ?: kotlinx.serialization.json.JsonNull,
        )
        put("transcript_revision", lastSequence)
        put("title", normalizedTitle)
        put("messages", JsonArray(batch.map(MobileTranscriptImportMessage::canonicalRecord)))
    }
    return MobileTranscriptImportRequest(
        clientConversationId = clientId,
        serverConversationId = normalizedServerId,
        transcriptRevision = lastSequence,
        idempotencyKey = "mobile-transcript:${mobileCanonicalSha256(keyPayload)}",
        title = normalizedTitle,
        messages = batch,
    )
}

private fun JsonObject.text(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.number(name: String, fallback: Long = 0L): Long =
    (get(name) as? JsonPrimitive)?.longOrNull ?: fallback

private fun JsonObject.requiredNumber(name: String): Long =
    (get(name) as? JsonPrimitive)?.longOrNull
        ?: throw MobileConversationStorageException("transcript import receipt 缺少 $name")
