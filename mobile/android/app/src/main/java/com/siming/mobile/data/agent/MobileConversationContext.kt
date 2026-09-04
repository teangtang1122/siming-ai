package com.siming.mobile.data.agent

import com.siming.mobile.data.MobileAssistantMessage
import java.security.MessageDigest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

internal object MobileConversationContextSchema {
    const val FRAME = "conversation_context_frame.v1"
    const val CHECKPOINT = "conversation_checkpoint.v1"
    const val STORAGE_VERSION = 2
    const val POLICY_VERSION = 1
}

internal object MobileConversationContextErrorCode {
    const val CAPACITY_UNKNOWN = "conversation_capacity_unknown"
    const val CURRENT_USER_OVER_CAPACITY = "current_user_message_over_capacity"
    const val CHECKPOINT_REQUIRED = "conversation_checkpoint_required"
    const val CHECKPOINT_FAILED = "conversation_checkpoint_failed"
    const val CHECKPOINT_CANCELLED = "conversation_checkpoint_cancelled"
    const val CHECKPOINT_SUPERSEDED = "conversation_checkpoint_superseded"
    const val SOURCE_CHANGED = "conversation_source_changed"
    const val REQUIRED_STATE_OVER_CAPACITY = "conversation_required_state_over_capacity"
    const val PROTOCOL_INVALID = "conversation_protocol_invalid"
    const val ORPHAN_TOOL_RESULT = "orphan_tool_result"
    const val INCOMPLETE_TOOL_TRANSACTION = "incomplete_tool_transaction"
    const val TOOL_CAPABILITY_UNAVAILABLE = "tool_capability_unavailable"
    const val TOOL_RESULT_OVER_CAPACITY = "tool_result_over_capacity"
    const val PROVIDER_MAPPING_FAILED = "provider_message_mapping_failed"
    const val FINAL_REQUEST_OVER_CAPACITY = "final_agent_request_over_capacity"
    val ALL = setOf(
        CAPACITY_UNKNOWN,
        CURRENT_USER_OVER_CAPACITY,
        CHECKPOINT_REQUIRED,
        CHECKPOINT_FAILED,
        CHECKPOINT_CANCELLED,
        CHECKPOINT_SUPERSEDED,
        SOURCE_CHANGED,
        REQUIRED_STATE_OVER_CAPACITY,
        PROTOCOL_INVALID,
        ORPHAN_TOOL_RESULT,
        INCOMPLETE_TOOL_TRANSACTION,
        TOOL_CAPABILITY_UNAVAILABLE,
        TOOL_RESULT_OVER_CAPACITY,
        PROVIDER_MAPPING_FAILED,
        FINAL_REQUEST_OVER_CAPACITY,
    )
}

internal class MobileConversationContextException(
    val code: String,
    override val message: String,
) : IllegalStateException(message)

/** A stable transcript record. Sequence numbers are scoped to one conversation. */
internal data class MobileTranscriptMessage(
    val id: String,
    val sequenceNo: Long,
    val turnId: String,
    val role: String,
    val content: String,
    val status: String,
    val createdAt: String,
    val toolLogs: List<String> = emptyList(),
) {
    init {
        require(id.isNotBlank()) { "会话消息缺少 ID" }
        require(sequenceNo >= 1L) { "会话消息 sequence_no 必须大于零" }
        require(turnId.isNotBlank()) { "会话消息缺少 turn_id" }
        require(role in HUMAN_VISIBLE_ROLES) { "会话 transcript 只接受 user/assistant 消息" }
    }

    fun displayMessage(): MobileAssistantMessage = MobileAssistantMessage(
        id = id,
        role = role,
        content = content,
        status = status,
        createdAt = createdAt,
        toolLogs = toolLogs,
    )

    fun toJson(): JsonObject = buildJsonObject {
        put("id", id)
        put("sequence_no", sequenceNo)
        put("turn_id", turnId)
        put("role", role)
        put("content", content)
        put("status", status)
        put("created_at", createdAt)
        put("tool_logs", JsonArray(toolLogs.map(::JsonPrimitive)))
    }

    fun toContextJson(): JsonObject = buildJsonObject {
        put("message_id", id)
        put("sequence_no", sequenceNo)
        put("role", role)
        put("content", content)
        put("tool_call_id", JsonNull)
        put("tool_calls", JsonArray(emptyList()))
    }

    fun toCheckpointSourceJson(): JsonObject = buildJsonObject {
        put("message_id", id)
        put("sequence_no", sequenceNo)
        put("role", role)
        put("content", content)
        put("status", status)
    }

    companion object {
        private val HUMAN_VISIBLE_ROLES = setOf("user", "assistant")

        fun fromJson(root: JsonObject): MobileTranscriptMessage = MobileTranscriptMessage(
            id = root.string("id"),
            sequenceNo = root.long("sequence_no"),
            turnId = root.string("turn_id"),
            role = root.string("role"),
            content = root.string("content"),
            status = root.string("status").ifBlank { "completed" },
            createdAt = root.string("created_at"),
            toolLogs = root.array("tool_logs").map { raw ->
                (raw as? JsonPrimitive)?.contentOrNull
                    ?: throw MobileConversationStorageException("会话 tool_logs 包含无效记录")
            },
        )
    }
}

internal data class MobileConversationTurn(
    val turnId: String,
    val status: String,
    val messages: List<MobileTranscriptMessage>,
) {
    val firstSequence: Long get() = messages.first().sequenceNo
    val lastSequence: Long get() = messages.last().sequenceNo
    val isClosed: Boolean
        get() = messages.firstOrNull()?.role == "user" &&
            messages.lastOrNull()?.role == "assistant" &&
            messages.size == 2
    val isCheckpointEligible: Boolean
        get() = isClosed && status == "completed" &&
            messages[0].content.isNotBlank() && messages[1].content.isNotBlank() &&
            messages[1].sequenceNo == messages[0].sequenceNo + 1L

    init {
        require(turnId.isNotBlank() && messages.isNotEmpty()) { "会话回合不能为空" }
        require(messages.all { it.turnId == turnId }) { "一个回合不能混入其他 turn_id" }
        require(messages.zipWithNext().all { (left, right) -> left.sequenceNo < right.sequenceNo }) {
            "回合内消息 sequence_no 必须严格递增"
        }
    }

    fun toContextJson(): JsonObject = buildJsonObject {
        put("turn_id", turnId)
        put("status", status)
        put("messages", buildJsonArray { messages.forEach { add(it.toContextJson()) } })
    }
}

internal fun mobileConversationTurns(messages: List<MobileTranscriptMessage>): List<MobileConversationTurn> {
    if (messages.isEmpty()) return emptyList()
    require(messages.zipWithNext().all { (left, right) -> left.sequenceNo < right.sequenceNo }) {
        "会话消息 sequence_no 必须严格递增"
    }
    return messages.groupBy(MobileTranscriptMessage::turnId).values
        .map { records ->
            val ordered = records.sortedBy(MobileTranscriptMessage::sequenceNo)
            val assistant = ordered.lastOrNull { it.role == "assistant" }
            MobileConversationTurn(
                turnId = ordered.first().turnId,
                status = assistant?.status ?: "running",
                messages = ordered,
            )
        }
        .sortedBy(MobileConversationTurn::firstSequence)
}

internal data class MobileConversationSourceRange(
    val firstSequence: Long,
    val lastSequence: Long,
    val messageCount: Int,
    val sourceHash: String,
) {
    init {
        require(firstSequence >= 1L && lastSequence >= firstSequence) { "checkpoint 来源范围无效" }
        require(messageCount >= 1) { "checkpoint 来源消息数必须大于零" }
        require(sourceHash.matches(SHA256_PATTERN)) { "checkpoint source_hash 无效" }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("first_sequence", firstSequence)
        put("last_sequence", lastSequence)
        put("message_count", messageCount)
        put("source_hash", sourceHash)
    }

    fun covers(turn: MobileConversationTurn): Boolean =
        firstSequence <= turn.firstSequence && turn.lastSequence <= lastSequence

    fun overlaps(turn: MobileConversationTurn): Boolean =
        firstSequence <= turn.lastSequence && turn.firstSequence <= lastSequence

    companion object {
        fun fromJson(root: JsonObject): MobileConversationSourceRange = MobileConversationSourceRange(
            firstSequence = root.long("first_sequence"),
            lastSequence = root.long("last_sequence"),
            messageCount = root.int("message_count"),
            sourceHash = root.string("source_hash"),
        )
    }
}

internal data class MobileCheckpointAuthorQuote(
    val messageId: String,
    val startChar: Int,
    val endChar: Int,
    val exactQuote: String,
    val quoteSha256: String,
    val purpose: String,
    val superseded: Boolean = false,
) {
    init {
        require(messageId.isNotBlank() && purpose.isNotBlank()) { "author quote 缺少标识或用途" }
        require(startChar >= 0 && endChar > startChar && exactQuote.isNotEmpty()) { "author quote 范围无效" }
        require(quoteSha256.matches(SHA256_PATTERN)) { "author quote hash 无效" }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("message_id", messageId)
        put("start_char", startChar)
        put("end_char", endChar)
        put("exact_quote", exactQuote)
        put("quote_sha256", quoteSha256)
        put("purpose", purpose)
        put("superseded", superseded)
    }

    companion object {
        fun fromJson(root: JsonObject): MobileCheckpointAuthorQuote = MobileCheckpointAuthorQuote(
            messageId = root.string("message_id"),
            startChar = root.int("start_char"),
            endChar = root.int("end_char"),
            exactQuote = root.string("exact_quote"),
            quoteSha256 = root.string("quote_sha256"),
            purpose = root.string("purpose"),
            superseded = root.boolean("superseded", false),
        )
    }
}

internal data class MobileCheckpointSource(
    val sourceKind: String,
    val sourceId: String,
    val sourceSequence: Long?,
    val sourceHash: String,
) {
    init {
        require(sourceKind in KINDS) { "checkpoint source_kind 无效" }
        require(sourceId.isNotBlank() && sourceHash.matches(SHA256_PATTERN)) {
            "checkpoint source 缺少可信来源"
        }
        sourceSequence?.let { require(it >= 1L) { "checkpoint source_sequence 无效" } }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("source_kind", sourceKind)
        put("source_id", sourceId)
        sourceSequence?.let { put("source_sequence", it) }
        put("source_hash", sourceHash)
    }

    companion object {
        private val KINDS = setOf("message", "run_step", "prior_segment")

        fun fromJson(root: JsonObject): MobileCheckpointSource = MobileCheckpointSource(
            sourceKind = root.string("source_kind"),
            sourceId = root.string("source_id"),
            sourceSequence = root.optionalLong("source_sequence"),
            sourceHash = root.string("source_hash"),
        )
    }
}

internal object MobileConversationCheckpointStatus {
    const val PENDING = "pending"
    const val COMPRESSING = "compressing"
    const val READY = "ready"
    const val FAILED = "failed"
    const val CANCELLED = "cancelled"
    const val SUPERSEDED = "superseded"
    val ALL = setOf(PENDING, COMPRESSING, READY, FAILED, CANCELLED, SUPERSEDED)
}

/**
 * Durable checkpoint data. semanticNavigation is deliberately non-authoritative;
 * author quotes and execution receipts must be validated before READY is published.
 */
internal data class MobileConversationCheckpoint(
    val id: String,
    val conversationId: String,
    val scope: String = "workspace",
    val parentCheckpointId: String? = null,
    val policyVersion: Int = MobileConversationContextSchema.POLICY_VERSION,
    val schemaVersion: String = MobileConversationContextSchema.CHECKPOINT,
    val status: String,
    val sourceRange: MobileConversationSourceRange,
    val transcriptRevision: Long,
    val idempotencyKey: String,
    val modelBinding: JsonObject = JsonObject(emptyMap()),
    val modelBindingFingerprint: String = "",
    val semanticNavigation: JsonObject = emptySemanticNavigation(),
    val authorQuotes: List<MobileCheckpointAuthorQuote> = emptyList(),
    val executionLedger: List<JsonObject> = emptyList(),
    val projectRefs: List<JsonObject> = emptyList(),
    val validation: JsonObject = JsonObject(emptyMap()),
    val sources: List<MobileCheckpointSource> = emptyList(),
    val warnings: List<String> = emptyList(),
    val segmentIds: List<String> = emptyList(),
    val originalTokens: Int? = null,
    val checkpointTokens: Int? = null,
    val errorCode: String? = null,
    val errorDetail: String? = null,
    val cancelRequestedAt: String? = null,
    val createdAt: String,
    val updatedAt: String,
    val completedAt: String? = null,
) {
    init {
        require(id.isNotBlank() && conversationId.isNotBlank() && idempotencyKey.isNotBlank()) {
            "checkpoint 缺少标识"
        }
        require(scope in setOf("workspace", "creation")) { "checkpoint scope 无效" }
        require(schemaVersion == MobileConversationContextSchema.CHECKPOINT) { "checkpoint Schema 不受支持" }
        require(policyVersion >= 1) { "checkpoint policy_version 无效" }
        require(status in MobileConversationCheckpointStatus.ALL) { "checkpoint 状态无效" }
        require(transcriptRevision >= 0L) { "checkpoint transcript_revision 无效" }
        require(semanticNavigation.string("authority") == NON_AUTHORITATIVE) {
            "checkpoint semantic_navigation 必须标记为非权威导航"
        }
        NAVIGATION_ARRAY_FIELDS.forEach { field -> semanticNavigation.array(field).strings(field) }
        if (modelBindingFingerprint.isNotBlank()) {
            require(modelBindingFingerprint.matches(SHA256_PATTERN)) { "checkpoint 模型绑定指纹无效" }
        }
        require(segmentIds.distinct().size == segmentIds.size) { "checkpoint segment_ids 不能重复" }
        require(id !in segmentIds) { "checkpoint segment_ids 只能包含先前分段" }
        val stepIds = executionLedger.map { entry ->
            require(
                listOf("run_id", "step_id", "tool", "status").all { entry.string(it).isNotBlank() },
            ) { "checkpoint execution_ledger 缺少权威字段" }
            entry.string("step_id")
        }
        require(stepIds.distinct().size == stepIds.size) { "checkpoint execution_ledger step_id 重复" }
        val projectKeys = projectRefs.map { reference ->
            require(listOf("type", "id", "reason").all { reference.string(it).isNotBlank() }) {
                "checkpoint project_ref 字段不完整"
            }
            reference.string("type") to reference.string("id")
        }
        require(projectKeys.distinct().size == projectKeys.size) { "checkpoint project_refs 重复" }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("id", id)
        put("conversation_id", conversationId)
        put("scope", scope)
        parentCheckpointId?.let { put("parent_checkpoint_id", it) }
        put("policy_version", policyVersion)
        put("schema_version", schemaVersion)
        put("status", status)
        put("source_range", sourceRange.toJson())
        put("transcript_revision", transcriptRevision)
        put("idempotency_key", idempotencyKey)
        put("model_binding", modelBinding)
        put("model_binding_fingerprint", modelBindingFingerprint)
        put("semantic_navigation", semanticNavigation)
        put("author_quotes", buildJsonArray { authorQuotes.forEach { add(it.toJson()) } })
        put("execution_ledger", JsonArray(executionLedger))
        put("project_refs", JsonArray(projectRefs))
        put("validation", validation)
        put("sources", buildJsonArray { sources.forEach { add(it.toJson()) } })
        put("warnings", JsonArray(warnings.map(::JsonPrimitive)))
        put("segment_ids", JsonArray(segmentIds.map(::JsonPrimitive)))
        originalTokens?.let { put("original_tokens", it) }
        checkpointTokens?.let { put("checkpoint_tokens", it) }
        errorCode?.let { put("error_code", it) }
        errorDetail?.let { put("error_detail", it) }
        cancelRequestedAt?.let { put("cancel_requested_at", it) }
        put("created_at", createdAt)
        put("updated_at", updatedAt)
        completedAt?.let { put("completed_at", it) }
    }

    /** Provider-neutral logical checkpoint shared with the Python core. */
    fun toFrameJson(): JsonObject = buildJsonObject {
        put("scope", scope)
        put("conversation_id", conversationId)
        put("source_range", sourceRange.toJson())
        put("semantic_navigation", semanticNavigation)
        put("author_quotes", buildJsonArray { authorQuotes.forEach { add(it.toJson()) } })
        put("execution_ledger", JsonArray(executionLedger))
        put("project_refs", JsonArray(projectRefs))
        put("warnings", JsonArray(warnings.map(::JsonPrimitive)))
        put("segment_ids", JsonArray(segmentIds.map(::JsonPrimitive)))
        put("policy_version", policyVersion)
        put("schema", schemaVersion)
    }

    val fingerprint: String get() = mobileCanonicalSha256(toFrameJson())

    companion object {
        const val NON_AUTHORITATIVE = "non_authoritative_navigation"
        private val NAVIGATION_ARRAY_FIELDS = listOf(
            "current_objectives",
            "resolved_decisions",
            "superseded_directions",
            "unresolved_questions",
            "next_context_needed",
        )

        fun emptySemanticNavigation(): JsonObject = buildJsonObject {
            put("authority", NON_AUTHORITATIVE)
            put("current_objectives", JsonArray(emptyList()))
            put("resolved_decisions", JsonArray(emptyList()))
            put("superseded_directions", JsonArray(emptyList()))
            put("unresolved_questions", JsonArray(emptyList()))
            put("next_context_needed", JsonArray(emptyList()))
        }

        fun fromJson(root: JsonObject): MobileConversationCheckpoint = MobileConversationCheckpoint(
            id = root.string("id"),
            conversationId = root.string("conversation_id"),
            scope = root.string("scope").ifBlank { "workspace" },
            parentCheckpointId = root.string("parent_checkpoint_id").ifBlank { null },
            policyVersion = root.int("policy_version", MobileConversationContextSchema.POLICY_VERSION),
            schemaVersion = root.string("schema_version"),
            status = root.string("status"),
            sourceRange = MobileConversationSourceRange.fromJson(root.objectValue("source_range")),
            transcriptRevision = root.long("transcript_revision"),
            idempotencyKey = root.string("idempotency_key"),
            modelBinding = root.objectValue("model_binding"),
            modelBindingFingerprint = root.string("model_binding_fingerprint"),
            semanticNavigation = root.objectValue("semantic_navigation"),
            authorQuotes = root.array("author_quotes").objects("author_quotes")
                .map(MobileCheckpointAuthorQuote::fromJson),
            executionLedger = root.array("execution_ledger").objects("execution_ledger"),
            projectRefs = root.array("project_refs").objects("project_refs"),
            validation = root.objectValue("validation"),
            sources = root.array("sources").objects("sources").map(MobileCheckpointSource::fromJson),
            warnings = root.array("warnings").strings("warnings"),
            segmentIds = root.array("segment_ids").strings("segment_ids"),
            originalTokens = root.optionalInt("original_tokens"),
            checkpointTokens = root.optionalInt("checkpoint_tokens"),
            errorCode = root.string("error_code").ifBlank { null },
            errorDetail = root.string("error_detail").ifBlank { null },
            cancelRequestedAt = root.string("cancel_requested_at").ifBlank { null },
            createdAt = root.string("created_at"),
            updatedAt = root.string("updated_at"),
            completedAt = root.string("completed_at").ifBlank { null },
        )
    }
}

/** Resolve the immutable chronological segment chain addressed by the active checkpoint. */
internal fun mobileResolveCheckpointSegments(
    checkpoints: List<MobileConversationCheckpoint>,
    activeCheckpointId: String?,
): List<MobileConversationCheckpoint> {
    if (activeCheckpointId == null) return emptyList()
    val byId = checkpoints.associateBy(MobileConversationCheckpoint::id)
    require(byId.size == checkpoints.size) { "checkpoint ID 不能重复" }
    val active = byId[activeCheckpointId]
        ?: throw MobileConversationStorageException("活动 checkpoint 记录不存在")
    require(active.status == MobileConversationCheckpointStatus.READY) {
        "活动 checkpoint 必须是 ready"
    }
    val ids = active.segmentIds + active.id
    require(ids.distinct().size == ids.size) { "checkpoint segment chain 不能重复" }
    val segments = ids.mapIndexed { index, id ->
        val segment = byId[id]
            ?: throw MobileConversationStorageException("checkpoint prior segment 不存在：$id")
        require(segment.status in setOf(
            MobileConversationCheckpointStatus.READY,
            MobileConversationCheckpointStatus.SUPERSEDED,
        )) { "checkpoint prior segment 状态无效" }
        require(segment.conversationId == active.conversationId && segment.scope == active.scope) {
            "checkpoint segment 归属不一致"
        }
        require(segment.segmentIds == ids.take(index)) {
            "checkpoint segment_ids 必须是按时间排序的完整前缀"
        }
        val expectedParentId = ids.getOrNull(index - 1)
        require(segment.parentCheckpointId == expectedParentId) {
            "checkpoint parent 链与 segment_ids 不一致"
        }
        segment
    }
    segments.zipWithNext().forEach { (left, right) ->
        require(left.sourceRange.lastSequence < right.sourceRange.firstSequence) {
            "checkpoint segment ranges 必须按时间排序且互不重叠"
        }
    }
    return segments
}

internal data class MobileConversationContextState(
    val revision: Long = 0L,
    val activeCheckpointId: String? = null,
    val activeSourceLastSequence: Long = 0L,
    val lastBudget: JsonObject? = null,
    val lastCompactedAt: String? = null,
    val updatedAt: String = "",
) {
    init {
        require(revision >= 0L && activeSourceLastSequence >= 0L) { "会话上下文状态无效" }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("revision", revision)
        activeCheckpointId?.let { put("active_checkpoint_id", it) }
        put("active_source_last_sequence", activeSourceLastSequence)
        lastBudget?.let { put("last_budget", it) }
        lastCompactedAt?.let { put("last_compacted_at", it) }
        put("updated_at", updatedAt)
    }

    companion object {
        fun fromJson(root: JsonObject): MobileConversationContextState = MobileConversationContextState(
            revision = root.long("revision", 0L),
            activeCheckpointId = root.string("active_checkpoint_id").ifBlank { null },
            activeSourceLastSequence = root.long("active_source_last_sequence", 0L),
            lastBudget = root["last_budget"] as? JsonObject,
            lastCompactedAt = root.string("last_compacted_at").ifBlank { null },
            updatedAt = root.string("updated_at"),
        )
    }
}

internal data class MobileCheckpointGenerationRequest(
    val scope: String,
    val conversationId: String,
    val transcriptRevision: Long,
    val sourceRange: MobileConversationSourceRange,
    val sourceMessages: List<MobileTranscriptMessage>,
    val priorSegments: List<MobileConversationCheckpoint>,
    val deterministicExecutionLedger: List<JsonObject>,
    val modelBinding: JsonObject,
) {
    init {
        require(scope in setOf("workspace", "creation")) { "checkpoint generation scope 无效" }
    }
}

internal data class MobileCheckpointSemanticDraft(
    val semanticNavigation: JsonObject,
    val quoteSelections: List<MobileCheckpointQuoteSelection>,
    val priorAuthorQuoteStates: List<MobilePriorAuthorQuoteDecision> = emptyList(),
)

internal data class MobilePriorAuthorQuoteDecision(
    val messageId: String,
    val startChar: Int,
    val endChar: Int,
    val quoteSha256: String,
    val status: String,
) {
    init {
        require(messageId.isNotBlank() && startChar >= 0 && endChar > startChar) {
            "prior author quote state identity 无效"
        }
        require(quoteSha256.matches(SHA256_PATTERN)) { "prior author quote state hash 无效" }
        require(status in setOf("active", "superseded")) { "prior author quote state status 无效" }
    }
}

internal data class MobileCheckpointQuoteSelection(
    val messageId: String,
    val startChar: Int,
    val endChar: Int,
    val purpose: String,
)

/**
 * A model-backed implementation must be supplied by the DirectApi integration.
 * There is intentionally no truncating or heuristic fallback implementation.
 */
internal fun interface MobileConversationCheckpointGenerator {
    suspend fun generate(request: MobileCheckpointGenerationRequest): MobileCheckpointSemanticDraft
}

internal fun validateAndMaterializeCheckpointDraft(
    draft: MobileCheckpointSemanticDraft,
    sourceMessages: List<MobileTranscriptMessage>,
): Pair<JsonObject, List<MobileCheckpointAuthorQuote>> {
    require(draft.semanticNavigation.string("authority") == MobileConversationCheckpoint.NON_AUTHORITATIVE) {
        "模型摘要必须明确标记 non_authoritative_navigation"
    }
    val sourceById = sourceMessages.associateBy(MobileTranscriptMessage::id)
    val seenRanges = mutableSetOf<Triple<String, Int, Int>>()
    val quotes = draft.quoteSelections.map { selection ->
        val source = sourceById[selection.messageId]
            ?: throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "作者原话引用的消息不存在",
            )
        require(source.role == "user") { "author_quotes 只能引用作者消息" }
        require(selection.purpose.isNotBlank()) { "author_quotes purpose 不能为空" }
        require(selection.startChar >= 0 && selection.endChar > selection.startChar) { "作者原话位置无效" }
        val rangeKey = Triple(selection.messageId, selection.startChar, selection.endChar)
        require(seenRanges.add(rangeKey)) { "author_quotes 不能重复引用同一范围" }
        val codePointCount = source.content.codePointCount(0, source.content.length)
        require(selection.endChar <= codePointCount) { "作者原话超出原消息范围" }
        val startOffset = source.content.offsetByCodePoints(0, selection.startChar)
        val endOffset = source.content.offsetByCodePoints(0, selection.endChar)
        val exact = source.content.substring(startOffset, endOffset)
        MobileCheckpointAuthorQuote(
            messageId = source.id,
            startChar = selection.startChar,
            endChar = selection.endChar,
            exactQuote = exact,
            quoteSha256 = mobileConversationSha256(exact),
            purpose = selection.purpose,
        )
    }
    return draft.semanticNavigation to quotes
}

internal data class MobileRecentTurnBudget(
    val requestInputLimitTokens: Int,
    val systemAndToolsTokens: Int,
    val providerWrapperTokens: Int,
    val checkpointTokens: Int,
    val currentUserTokens: Int,
    val currentTurnLedgerTokens: Int,
    val pendingToolTransactionTokens: Int,
    val providerStateTokens: Int = 0,
) {
    val fixedInputTokens: Int
        get() = systemAndToolsTokens + providerWrapperTokens + checkpointTokens + currentUserTokens +
            currentTurnLedgerTokens + pendingToolTransactionTokens + providerStateTokens
}

internal data class MobileRecentTurnPlan(
    val recentExactTurns: List<MobileConversationTurn>,
    val checkpointTurns: List<MobileConversationTurn>,
    val checkpointRanges: List<List<MobileConversationTurn>>,
    val fixedInputTokens: Int,
    val recentExactTokens: Int,
    val remainingInputTokens: Int,
) {
    val requiresCheckpoint: Boolean get() = checkpointTurns.isNotEmpty()
}

/** Selects a chronological exact tail by token cost; it never drops a turn. */
internal object MobileRecentTurnPlanner {
    fun planWithCounter(
        turns: List<MobileConversationTurn>,
        coveredSequenceRanges: List<MobileConversationSourceRange>,
        counter: MobileConversationTokenCounter,
        baselineBudget: MobileRequestBudgetEnvelope,
    ): MobileRecentTurnPlan {
        if (!baselineBudget.verified && !baselineBudget.boundedFallback) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CAPACITY_UNKNOWN,
                "动态历史规划需要已验证容量或有界 256K 兜底",
            )
        }
        require(baselineBudget.tokenCounterId == counter.counterId &&
            baselineBudget.capacityAssurance == counter.assurance
        ) { "recent tail TokenCounter 与请求预算不匹配" }
        require(baselineBudget.recentExactTurnTokens == 0) {
            "recent tail 基线预算不能预先包含 recent_exact_turn_tokens"
        }
        return plan(
            turns = turns,
            coveredSequenceRanges = coveredSequenceRanges,
            tokenCountByTurnId = turns.associate { turn ->
                turn.turnId to counter.countValue(turn.toContextJson())
            },
            budget = baselineBudget.recentTurnBudget(),
        )
    }

    fun plan(
        turns: List<MobileConversationTurn>,
        coveredSequenceRanges: List<MobileConversationSourceRange>,
        tokenCountByTurnId: Map<String, Int>,
        budget: MobileRecentTurnBudget,
    ): MobileRecentTurnPlan {
        require(budget.requestInputLimitTokens > 0) { "request_input_limit 必须大于零" }
        if (budget.currentUserTokens <= 0) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CAPACITY_UNKNOWN,
                "当前用户消息没有可验证的 token 计数",
            )
        }
        val withoutUser = budget.fixedInputTokens - budget.currentUserTokens
        if (withoutUser + budget.currentUserTokens > budget.requestInputLimitTokens) {
            val code = if (withoutUser < budget.requestInputLimitTokens &&
                budget.currentUserTokens > budget.requestInputLimitTokens - withoutUser
            ) {
                MobileConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY
            } else {
                MobileConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY
            }
            throw MobileConversationContextException(code, "当前请求的不可压缩内容超过模型容量")
        }

        val ordered = turns.sortedBy(MobileConversationTurn::firstSequence)
        require(ordered.zipWithNext().all { (left, right) -> left.lastSequence < right.firstSequence }) {
            "会话回合 sequence 范围重叠"
        }
        require(ordered.all(MobileConversationTurn::isClosed)) {
            "历史 recent-turn 规划只接受已闭合回合"
        }
        val ranges = coveredSequenceRanges.sortedBy(MobileConversationSourceRange::firstSequence)
        require(ranges.zipWithNext().all { (left, right) ->
            left.lastSequence < right.firstSequence
        }) { "checkpoint covered ranges 必须按时间排序且互不重叠" }
        val uncovered = ordered.filter { turn ->
            val covering = ranges.filter { it.covers(turn) }
            if (covering.isNotEmpty()) {
                if (!turn.isCheckpointEligible) {
                    throw MobileConversationContextException(
                        MobileConversationContextErrorCode.SOURCE_CHANGED,
                        "checkpoint segment 不能覆盖 error/aborted/cancelled 回合",
                    )
                }
                false
            } else {
                if (ranges.any { it.overlaps(turn) }) {
                    throw MobileConversationContextException(
                        MobileConversationContextErrorCode.SOURCE_CHANGED,
                        "活动 checkpoint segment 切断了一个完整回合",
                    )
                }
                true
            }
        }
        fun tokens(turn: MobileConversationTurn): Int = tokenCountByTurnId[turn.turnId]
            ?.takeIf { it > 0 }
            ?: throw MobileConversationContextException(
                MobileConversationContextErrorCode.CAPACITY_UNKNOWN,
                "回合 ${turn.turnId} 没有可验证的 token 计数",
            )

        val requiredExact = uncovered.filterNot(MobileConversationTurn::isCheckpointEligible)
        var used = budget.fixedInputTokens + requiredExact.sumOf(::tokens)
        if (used > budget.requestInputLimitTokens) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
                "未闭合或失败回合等必保留状态超过模型容量",
            )
        }

        val selectedCompleted = mutableListOf<MobileConversationTurn>()
        for (turn in uncovered.filter(MobileConversationTurn::isCheckpointEligible).asReversed()) {
            val cost = tokens(turn)
            if (used + cost <= budget.requestInputLimitTokens) {
                selectedCompleted += turn
                used += cost
            } else {
                // Recent exact history is a chronological suffix, never a relevance sample.
                break
            }
        }
        val selectedIds = selectedCompleted.mapTo(mutableSetOf(), MobileConversationTurn::turnId)
        val exact = uncovered.filter { !it.isCheckpointEligible || it.turnId in selectedIds }
        val checkpoint = uncovered.filter { it.isCheckpointEligible && it.turnId !in selectedIds }
        return MobileRecentTurnPlan(
            recentExactTurns = exact,
            checkpointTurns = checkpoint,
            checkpointRanges = contiguousCheckpointRanges(checkpoint),
            fixedInputTokens = budget.fixedInputTokens,
            recentExactTokens = exact.sumOf(::tokens),
            remainingInputTokens = budget.requestInputLimitTokens - used,
        )
    }

    private fun contiguousCheckpointRanges(
        turns: List<MobileConversationTurn>,
    ): List<List<MobileConversationTurn>> {
        if (turns.isEmpty()) return emptyList()
        val result = mutableListOf<MutableList<MobileConversationTurn>>()
        turns.sortedBy(MobileConversationTurn::firstSequence).forEach { turn ->
            val current = result.lastOrNull()
            if (current == null || current.last().lastSequence + 1L != turn.firstSequence) {
                result += mutableListOf(turn)
            } else {
                current += turn
            }
        }
        return result
    }
}

internal object MobileToolTransactionState {
    const val PENDING = "pending"
    const val DELIVERED = "delivered"
    const val CONSUMED = "consumed"
    const val COMPACTABLE = "compactable"
    val ALL = setOf(PENDING, DELIVERED, CONSUMED, COMPACTABLE)
}

internal data class MobileNativeToolBatchAdmission(
    val accepted: Boolean,
    val reason: String? = null,
    val declaredJsonBytes: Int,
    val maxJsonBytes: Int,
    val callCount: Int,
)

/**
 * Cross-runtime hard boundary for one exact native assistant/tool transaction.
 *
 * The model response is admitted as a whole before any handler can run.  The
 * Android executor never drops calls, rewrites arguments, or executes a prefix
 * of an over-capacity batch.
 */
internal object MobileNativeToolBudgetContract {
    const val SCHEMA = "native_tool_transaction_budget.v2"
    const val MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES = 16 * 1024
    const val MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES = 32 * 1024
    const val NEXT_STEP_WRAPPER_TOKENS =
        2 * MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES
    const val NATIVE_ASSISTANT_TRANSACTION_OVER_CAPACITY =
        "native_assistant_transaction_over_capacity"
    const val NATIVE_ASSISTANT_TRANSACTION_INVALID = "native_assistant_transaction_invalid"
    const val TOOL_RESULT_BATCH_OVER_CAPACITY = "tool_result_batch_over_capacity"

    private const val STATUS_ONLY_RESULT_BYTES = 4 * 1024
    private const val OUTLINE_BATCH_STATUS_RESULT_BYTES = 12 * 1024
    private const val ARTIFACT_REFERENCE_RESULT_BYTES = 16 * 1024
    private const val STANDARD_RESULT_BYTES = 16 * 1024
    private const val LARGE_READ_RESULT_BYTES = 32 * 1024
    private const val CONTEXT_SELECTION_RECEIPT_BYTES = 24 * 1024

    private val resultBytesByTool = mapOf(
        "set_tool_categories" to STATUS_ONLY_RESULT_BYTES,
        "chapter_writer" to ARTIFACT_REFERENCE_RESULT_BYTES,
        "outline_writer" to ARTIFACT_REFERENCE_RESULT_BYTES,
        "character_writer" to STANDARD_RESULT_BYTES,
        "worldbuilding_writer" to STANDARD_RESULT_BYTES,
        "get_project_info" to STANDARD_RESULT_BYTES,
        "list_characters" to STANDARD_RESULT_BYTES,
        "list_chapters" to STANDARD_RESULT_BYTES,
        "list_worldbuilding" to STANDARD_RESULT_BYTES,
        "search_characters" to STANDARD_RESULT_BYTES,
        "search_chapters" to STANDARD_RESULT_BYTES,
        "search_outline" to STANDARD_RESULT_BYTES,
        "search_outline_tree" to STANDARD_RESULT_BYTES,
        "search_worldbuilding" to STANDARD_RESULT_BYTES,
        "prepare_task_context" to LARGE_READ_RESULT_BYTES,
        "search_task_context" to LARGE_READ_RESULT_BYTES,
        "submit_context_evidence" to CONTEXT_SELECTION_RECEIPT_BYTES,
        "update_project_info" to STATUS_ONLY_RESULT_BYTES,
        "create_character" to STATUS_ONLY_RESULT_BYTES,
        "update_character" to STATUS_ONLY_RESULT_BYTES,
        "create_outline_node" to STATUS_ONLY_RESULT_BYTES,
        "create_outline_nodes" to OUTLINE_BATCH_STATUS_RESULT_BYTES,
        "update_outline_node" to STATUS_ONLY_RESULT_BYTES,
        "create_worldbuilding_entry" to STATUS_ONLY_RESULT_BYTES,
        "update_worldbuilding_entry" to STATUS_ONLY_RESULT_BYTES,
    )

    fun declaredResultJsonBytes(toolName: String): Int = resultBytesByTool[toolName]
        ?: throw MobileConversationContextException(
            MobileConversationContextErrorCode.PROTOCOL_INVALID,
            "工具 $toolName 没有声明模型可见结果契约",
        )

    fun nextStepWrapperTokens(toolsOffered: Boolean): Int =
        if (toolsOffered) NEXT_STEP_WRAPPER_TOKENS else 0

    fun maxModelVisibleResultTokens(toolsOffered: Boolean): Int =
        if (toolsOffered) MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES else 0

    fun admitExactAssistantTransaction(
        assistantPayload: JsonObject,
        orderedToolNames: List<String>,
        resultJsonBytes: (String) -> Int = ::declaredResultJsonBytes,
    ): MobileNativeToolBatchAdmission {
        val rawCalls = assistantPayload.array("tool_calls").objects("tool_calls")
        val payloadCalls = rawCalls.map { rawCall ->
            val function = rawCall["function"] as? JsonObject
                ?: throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "原生 tool_call 缺少 function 对象",
                )
            val id = rawCall.string("id").ifBlank { rawCall.string("call_id") }
            val name = function.string("name").ifBlank { rawCall.string("name") }
            if (id.isBlank() || name.isBlank()) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "原生 tool_call 必须完整保留 ID、名称和 JSON arguments 字符串",
                )
            }
            requireMobileNativeToolArgumentsObject(rawCall)
            id to name
        }
        if (payloadCalls.map { it.first }.distinct().size != payloadCalls.size) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "原生 tool_call ID 重复，整批未执行",
            )
        }
        val payloadNames = payloadCalls.map { it.second }
        if (payloadNames != orderedToolNames) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "原生 assistant 事务与解析后的工具调用批次不一致",
            )
        }
        val assistantBytes = mobileCanonicalJson(assistantPayload).toByteArray(Charsets.UTF_8).size
        if (assistantBytes > MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES) {
            return MobileNativeToolBatchAdmission(
                accepted = false,
                reason = NATIVE_ASSISTANT_TRANSACTION_OVER_CAPACITY,
                declaredJsonBytes = assistantBytes,
                maxJsonBytes = MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
                callCount = orderedToolNames.size,
            )
        }
        val declaredResultBytes = orderedToolNames.sumOf(resultJsonBytes)
        if (declaredResultBytes > MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES) {
            return MobileNativeToolBatchAdmission(
                accepted = false,
                reason = TOOL_RESULT_BATCH_OVER_CAPACITY,
                declaredJsonBytes = declaredResultBytes,
                maxJsonBytes = MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
                callCount = orderedToolNames.size,
            )
        }
        return MobileNativeToolBatchAdmission(
            accepted = true,
            declaredJsonBytes = declaredResultBytes,
            maxJsonBytes = MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
            callCount = orderedToolNames.size,
        )
    }

    fun actualResultFits(toolName: String, result: JsonObject): Boolean =
        mobileCanonicalJson(result).toByteArray(Charsets.UTF_8).size <=
            declaredResultJsonBytes(toolName)
}

internal data class MobileToolCallRecord(
    val id: String,
    val name: String,
    val argumentsJson: String = "{}",
) {
    fun toFrameJson(): JsonObject = buildJsonObject {
        put("call_id", id)
        put("name", name)
        put("arguments_json", argumentsJson)
    }

    companion object {
        fun fromFrameJson(root: JsonObject): MobileToolCallRecord = MobileToolCallRecord(
            id = root.string("call_id"),
            name = root.string("name"),
            argumentsJson = root.string("arguments_json"),
        )
    }
}

internal data class MobileToolResultRecord(
    val toolCallId: String,
    val content: String = "",
    val resultRef: String? = null,
    val persistedStepId: String? = null,
) {
    fun toFrameJson(): JsonObject = buildJsonObject {
        put("call_id", toolCallId)
        put("content", content)
        put("result_ref", resultRef?.let(::JsonPrimitive) ?: JsonNull)
        put("persisted_step_id", persistedStepId?.let(::JsonPrimitive) ?: JsonNull)
    }

    companion object {
        fun fromFrameJson(root: JsonObject): MobileToolResultRecord = MobileToolResultRecord(
            toolCallId = root.string("call_id"),
            content = root.string("content"),
            resultRef = root.string("result_ref").ifBlank { null },
            persistedStepId = root.string("persisted_step_id").ifBlank { null },
        )
    }
}

/** Server/local-store authored replacement for a consumed native tool transaction. */
internal data class MobileToolExecutionReceipt(
    val stepId: String,
    val tool: String,
    val status: String,
    val summary: String,
    val resourceIds: List<String>,
    val resultRef: String,
    val reread: String? = null,
    val writeCommitted: Boolean,
) {
    init {
        require(stepId.isNotBlank() && tool.isNotBlank() && status.isNotBlank() && resultRef.isNotBlank()) {
            "工具执行回执缺少权威标识"
        }
        require(resourceIds.distinct().size == resourceIds.size) { "工具执行回执 resource_ids 重复" }
    }

    fun toFrameJson(): JsonObject = buildJsonObject {
        put("step_id", stepId)
        put("tool", tool)
        put("status", status)
        put("summary", summary)
        put("resource_ids", JsonArray(resourceIds.map(::JsonPrimitive)))
        put("result_ref", resultRef)
        put("reread", reread?.let(::JsonPrimitive) ?: JsonNull)
        put("write_committed", writeCommitted)
    }

    companion object {
        fun fromFrameJson(root: JsonObject): MobileToolExecutionReceipt = MobileToolExecutionReceipt(
            stepId = root.string("step_id"),
            tool = root.string("tool"),
            status = root.string("status"),
            summary = root.string("summary"),
            resourceIds = root.array("resource_ids").strings("resource_ids"),
            resultRef = root.string("result_ref"),
            reread = root.string("reread").ifBlank { null },
            writeCommitted = root.boolean("write_committed", false),
        )
    }
}

internal data class MobileToolTransaction(
    val transactionId: String,
    val assistantMessageId: String,
    val assistantContent: String = "",
    val assistantReasoningContent: String = "",
    val assistantProviderState: List<JsonObject> = emptyList(),
    val state: String,
    val calls: List<MobileToolCallRecord>,
    val results: List<MobileToolResultRecord>,
) {
    val canCompact: Boolean get() = state == MobileToolTransactionState.COMPACTABLE

    init {
        require(transactionId.isNotBlank() && assistantMessageId.isNotBlank() &&
            state in MobileToolTransactionState.ALL
        ) {
            "工具事务标识或状态无效"
        }
        require(calls.isNotEmpty()) { "工具事务至少包含一个调用" }
        require(calls.all { it.id.isNotBlank() && it.name.isNotBlank() && it.argumentsJson.isNotBlank() }) {
            "工具调用缺少 ID、名称或参数 JSON"
        }
        require(calls.map(MobileToolCallRecord::id).distinct().size == calls.size) { "工具调用 ID 重复" }
        require(results.map(MobileToolResultRecord::toolCallId).distinct().size == results.size) {
            "工具结果 ID 重复"
        }
        require(results.all { result -> calls.any { it.id == result.toolCallId } }) { "工具结果没有对应调用" }
        if (state != MobileToolTransactionState.PENDING) {
            require(results.map(MobileToolResultRecord::toolCallId).toSet() == calls.map(MobileToolCallRecord::id).toSet()) {
                "delivered/consumed/compactable 工具事务必须包含全部结果"
            }
        }
        if (state == MobileToolTransactionState.COMPACTABLE) {
            require(results.all { !it.resultRef.isNullOrBlank() && !it.persistedStepId.isNullOrBlank() }) {
                "可回收工具事务的每个结果都必须有持久化 RunStep 与 result_ref"
            }
        }
    }

    fun addResult(result: MobileToolResultRecord): MobileToolTransaction {
        require(state == MobileToolTransactionState.PENDING) { "只有 pending 工具事务可以追加结果" }
        if (calls.none { it.id == result.toolCallId }) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.ORPHAN_TOOL_RESULT,
                "工具结果没有对应调用",
            )
        }
        require(results.none { it.toolCallId == result.toolCallId }) { "同一工具调用只能有一个结果" }
        return copy(results = results + result)
    }

    fun markDelivered(): MobileToolTransaction = transition(
        MobileToolTransactionState.PENDING,
        MobileToolTransactionState.DELIVERED,
    )

    fun markConsumed(): MobileToolTransaction = transition(
        MobileToolTransactionState.DELIVERED,
        MobileToolTransactionState.CONSUMED,
    )

    fun markCompactable(): MobileToolTransaction = transition(
        MobileToolTransactionState.CONSUMED,
        MobileToolTransactionState.COMPACTABLE,
    )

    fun toFrameJson(): JsonObject = buildJsonObject {
        put("transaction_id", transactionId)
        put("assistant_message_id", assistantMessageId)
        put("assistant_content", assistantContent)
        put("assistant_reasoning_content", assistantReasoningContent)
        put("assistant_provider_state", JsonArray(assistantProviderState))
        put("calls", buildJsonArray { calls.forEach { add(it.toFrameJson()) } })
        put("results", buildJsonArray { results.forEach { add(it.toFrameJson()) } })
        put("state", state)
    }

    fun nativeMessages(): List<JsonObject> {
        val resultsById = results.associateBy(MobileToolResultRecord::toolCallId)
        return buildList {
            add(buildJsonObject {
                put("role", "assistant")
                put("content", assistantContent)
                if (assistantReasoningContent.isNotBlank()) {
                    put("reasoning_content", assistantReasoningContent)
                }
                if (assistantProviderState.isNotEmpty()) {
                    put("provider_state", JsonArray(assistantProviderState))
                }
                put("tool_calls", buildJsonArray {
                    calls.forEach { call ->
                        add(buildJsonObject {
                            put("id", call.id)
                            put("type", "function")
                            put("function", buildJsonObject {
                                put("name", call.name)
                                put("arguments", call.argumentsJson)
                            })
                        })
                    }
                })
            })
            calls.forEach { call ->
                resultsById[call.id]?.let { result ->
                    add(buildJsonObject {
                        put("role", "tool")
                        put("tool_call_id", result.toolCallId)
                        put("content", result.content)
                    })
                }
            }
        }
    }

    private fun transition(from: String, to: String): MobileToolTransaction {
        require(state == from) { "工具事务只能从 $from 转换到 $to" }
        return copy(state = to)
    }

    companion object {
        fun fromFrameJson(root: JsonObject): MobileToolTransaction = MobileToolTransaction(
            transactionId = root.string("transaction_id"),
            assistantMessageId = root.string("assistant_message_id"),
            assistantContent = root.string("assistant_content"),
            assistantReasoningContent = root.string("assistant_reasoning_content"),
            assistantProviderState = root.array("assistant_provider_state")
                .objects("assistant_provider_state"),
            state = root.string("state"),
            calls = root.array("calls").objects("calls").map(MobileToolCallRecord::fromFrameJson),
            results = root.array("results").objects("results").map(MobileToolResultRecord::fromFrameJson),
        )
    }
}

/** Durable audit for one local assistant turn; compactable payloads stay on disk, not in prompts. */
internal data class MobileTurnToolRuntimeState(
    val turnId: String,
    val transactions: List<MobileToolTransaction> = emptyList(),
    val executionLedger: List<MobileToolExecutionReceipt> = emptyList(),
) {
    init {
        require(turnId.isNotBlank()) { "工具运行状态缺少 turn_id" }
        require(transactions.map(MobileToolTransaction::transactionId).distinct().size == transactions.size) {
            "同一回合工具事务 ID 重复"
        }
        require(executionLedger.map(MobileToolExecutionReceipt::stepId).distinct().size == executionLedger.size) {
            "同一回合工具执行回执 step_id 重复"
        }
    }

    val deliveredTransactions: List<MobileToolTransaction>
        get() = transactions.filter { it.state == MobileToolTransactionState.DELIVERED }

    fun recordDelivered(transaction: MobileToolTransaction): MobileTurnToolRuntimeState {
        require(transaction.state == MobileToolTransactionState.DELIVERED) {
            "只能持久化完整 delivered 工具事务"
        }
        transactions.firstOrNull { it.transactionId == transaction.transactionId }?.let { existing ->
            require(existing.toFrameJson() == transaction.toFrameJson()) { "工具事务 ID 冲突" }
            return this
        }
        return copy(transactions = transactions + transaction)
    }

    /** Called only after the provider accepted the request containing every delivered transaction. */
    fun markDeliveredConsumed(): MobileTurnToolRuntimeState {
        if (deliveredTransactions.isEmpty()) return this
        val receiptsByStep = executionLedger.associateBy(MobileToolExecutionReceipt::stepId).toMutableMap()
        val updated = transactions.map { transaction ->
            if (transaction.state != MobileToolTransactionState.DELIVERED) return@map transaction
            val receipts = transaction.calls.map { call ->
                val result = transaction.results.first { it.toolCallId == call.id }
                mobileToolExecutionReceipt(turnId, transaction.transactionId, call, result)
            }
            receipts.forEach { receipt ->
                val existing = receiptsByStep.putIfAbsent(receipt.stepId, receipt)
                require(existing == null || existing == receipt) { "工具执行回执 step_id 冲突" }
            }
            val receiptByCall = transaction.calls.zip(receipts).associate { (call, receipt) -> call.id to receipt }
            transaction.copy(
                results = transaction.results.map { result ->
                    val receipt = receiptByCall.getValue(result.toolCallId)
                    result.copy(
                        resultRef = receipt.resultRef,
                        persistedStepId = receipt.stepId,
                    )
                },
            ).markConsumed().markCompactable()
        }
        return copy(
            transactions = updated,
            executionLedger = receiptsByStep.values.sortedBy(MobileToolExecutionReceipt::stepId),
        )
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("turn_id", turnId)
        put("transactions", buildJsonArray { transactions.forEach { add(it.toFrameJson()) } })
        put("execution_ledger", buildJsonArray { executionLedger.forEach { add(it.toFrameJson()) } })
    }

    companion object {
        fun fromJson(root: JsonObject): MobileTurnToolRuntimeState = MobileTurnToolRuntimeState(
            turnId = root.string("turn_id"),
            transactions = root.array("transactions").objects("transactions")
                .map(MobileToolTransaction::fromFrameJson),
            executionLedger = root.array("execution_ledger").objects("execution_ledger")
                .map(MobileToolExecutionReceipt::fromFrameJson),
        )
    }
}

private fun mobileToolExecutionReceipt(
    turnId: String,
    transactionId: String,
    call: MobileToolCallRecord,
    result: MobileToolResultRecord,
): MobileToolExecutionReceipt {
    val parsed = runCatching { Json.parseToJsonElement(result.content) as? JsonObject }.getOrNull()
        ?: JsonObject(emptyMap())
    val data = parsed["data"] as? JsonObject
    val resourceIds = TOOL_RECEIPT_RESOURCE_ID_FIELDS.mapNotNull { field ->
        (data?.get(field) as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
    }.distinct()
    val resultHash = mobileConversationSha256(result.content)
    val stepId = "mobile-step-${mobileConversationSha256("$turnId\u001f$transactionId\u001f${call.id}").take(32)}"
    return MobileToolExecutionReceipt(
        stepId = stepId,
        tool = call.name,
        status = parsed.string("status").ifBlank { "unknown" },
        summary = parsed.string("detail").ifBlank { "${call.name} 已返回确定性结果" },
        resourceIds = resourceIds,
        resultRef = "mobile-tool-result:$resultHash",
        reread = null,
        writeCommitted = call.name in MOBILE_WRITE_TOOLS && parsed.string("status") == "ok",
    )
}

private val TOOL_RECEIPT_RESOURCE_ID_FIELDS = listOf(
    "id", "project_id", "chapter_id", "outline_node_id", "character_id",
    "worldbuilding_id", "manifest_id", "context_manifest_id", "draft_id", "content_ref",
)

private val MOBILE_WRITE_TOOLS = setOf(
    "update_project_info", "create_character", "update_character", "create_outline_node",
    "create_outline_nodes", "update_outline_node", "create_worldbuilding_entry",
    "update_worldbuilding_entry", "chapter_writer", "outline_writer",
)

/** Validates native provider messages before every DirectApi model request. */
internal object MobileToolProtocolValidator {
    fun validate(
        messages: List<JsonObject>,
        supportsNativeToolCalling: Boolean,
        toolsOffered: Boolean,
        directMcpValidated: Boolean = false,
        currentUserMessageId: String? = null,
        checkpointMessageId: String? = null,
    ) {
        if (toolsOffered && !supportsNativeToolCalling && !directMcpValidated) {
            fail(
                MobileConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                "当前模型不支持原生工具调用",
            )
        }
        val allCallIds = mutableSetOf<String>()
        val messageById = mutableMapOf<String, Pair<Int, JsonObject>>()
        val userPositions = mutableListOf<Pair<Int, String>>()
        var pendingCallIds = linkedSetOf<String>()
        var systemSeen = false
        messages.forEachIndexed { index, message ->
            val role = message.string("role")
            val messageId = message.string("message_id")
            if (messageId.isNotBlank()) {
                if (messageById.put(messageId, index to message) != null) fail(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "message_id 在当前请求中重复",
                )
            }
            if (role == "user") userPositions += index to messageId
            if (role == "system") {
                if (index != 0 || systemSeen) fail(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "system 消息必须且只能位于开头",
                )
                systemSeen = true
                return@forEachIndexed
            }
            val rawCallItems = message.array("tool_calls")
            if (rawCallItems.any { it !is JsonObject }) fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "tool_calls 必须是结构化对象数组",
            )
            val rawCalls = rawCallItems.map { it as JsonObject }
            if (rawCalls.isNotEmpty() && role != "assistant") fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "只有 assistant 消息可以包含 tool_calls",
            )
            if (pendingCallIds.isNotEmpty() && role != "tool") {
                fail(
                    MobileConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
                    "工具结果尚未齐全，不能发送下一条 $role 消息",
                )
            }
            when (role) {
                "user" -> Unit
                "assistant" -> {
                    val calls = rawCalls
                    if (calls.isEmpty()) return@forEachIndexed
                    if (!supportsNativeToolCalling) fail(
                        MobileConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                        "消息包含原生 tool_calls，但模型不支持",
                    )
                    calls.forEach { call ->
                        val id = call.string("id").ifBlank { call.string("call_id") }
                        val function = call["function"] as? JsonObject
                        val name = function?.string("name").orEmpty().ifBlank { call.string("name") }
                        if (id.isBlank() || name.isBlank()) fail(
                            MobileConversationContextErrorCode.PROTOCOL_INVALID,
                            "assistant tool_call 缺少 ID 或函数名",
                        )
                        requireMobileNativeToolArgumentsObject(call)
                        if (!allCallIds.add(id)) fail(
                            MobileConversationContextErrorCode.PROTOCOL_INVALID,
                            "tool_call_id 在当前请求中重复",
                        )
                        pendingCallIds += id
                    }
                }
                "tool" -> {
                    val callId = message.string("tool_call_id")
                    if (!pendingCallIds.remove(callId)) fail(
                        MobileConversationContextErrorCode.ORPHAN_TOOL_RESULT,
                        "tool 消息没有对应的 assistant tool_call",
                    )
                }
                else -> fail(MobileConversationContextErrorCode.PROTOCOL_INVALID, "不支持的消息角色：$role")
            }
        }
        if (pendingCallIds.isNotEmpty()) fail(
            MobileConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
            "请求中存在未闭合的工具事务",
        )
        currentUserMessageId?.let { currentId ->
            val current = messageById[currentId]
            if (current == null || current.second.string("role") != "user") fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "最新用户消息必须以独立 user 消息存在",
            )
            if (userPositions.any { (position, _) -> position > current.first }) fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "最新用户消息之后不能出现另一个用户意图消息",
            )
        }
        checkpointMessageId?.let { checkpointId ->
            val checkpoint = messageById[checkpointId]
                ?: fail(MobileConversationContextErrorCode.PROTOCOL_INVALID, "checkpoint 历史参考消息不存在")
            if (checkpoint.second.string("role") == "tool") fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "checkpoint 不能映射为 tool role",
            )
            if (checkpointId == currentUserMessageId) fail(
                MobileConversationContextErrorCode.PROTOCOL_INVALID,
                "checkpoint 不能与最新用户消息合并",
            )
        }
    }

    private fun fail(code: String, detail: String): Nothing =
        throw MobileConversationContextException(code, detail)
}

private fun requireMobileNativeToolArgumentsObject(call: JsonObject) {
    val function = call["function"] as? JsonObject
    val arguments = (function?.get("arguments") ?: call["arguments"]) as? JsonPrimitive
    if (arguments?.isString != true) {
        throw MobileConversationContextException(
            MobileConversationContextErrorCode.PROTOCOL_INVALID,
            "assistant tool call 的 arguments 必须是 JSON 字符串",
        )
    }
    val parsed = runCatching { Json.parseToJsonElement(arguments.content) }.getOrNull()
    if (parsed !is JsonObject) {
        throw MobileConversationContextException(
            MobileConversationContextErrorCode.PROTOCOL_INVALID,
            "assistant tool call 的 arguments 必须是合法 JSON 对象",
        )
    }
}

internal data class MobileConversationIdentity(
    val kind: String,
    val id: String,
    val revision: Long,
    val projectId: String? = null,
    val creationSessionId: String? = null,
) {
    init {
        require(kind in setOf("workspace", "creation") && id.isNotBlank() && revision >= 0L) {
            "ContextFrame conversation identity 无效"
        }
        if (kind == "workspace") require(!projectId.isNullOrBlank()) { "workspace 会话缺少 project_id" }
        if (kind == "creation") require(!creationSessionId.isNullOrBlank()) { "creation 会话缺少 session_id" }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("kind", kind)
        put("id", id)
        put("revision", revision)
        put("project_id", projectId?.let(::JsonPrimitive) ?: JsonNull)
        put("creation_session_id", creationSessionId?.let(::JsonPrimitive) ?: JsonNull)
    }
}

internal data class MobileSystemContract(
    val promptHash: String,
    val activeToolCategoryHash: String,
) {
    init {
        require(promptHash.isNotBlank() && activeToolCategoryHash.isNotBlank()) {
            "ContextFrame system contract hash 不能为空"
        }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("prompt_hash", promptHash)
        put("active_tool_category_hash", activeToolCategoryHash)
    }
}

/** Provider adapters render these events in sequence order without rewriting native roles. */
internal data class MobileHistoricalContextEvent(
    val firstSequence: Long,
    val checkpointSegment: MobileConversationCheckpoint? = null,
    val exactTurn: MobileConversationTurn? = null,
    val isActiveCheckpoint: Boolean = false,
) {
    init {
        require((checkpointSegment == null) != (exactTurn == null)) {
            "历史事件必须且只能携带 checkpoint segment 或 exact turn"
        }
        require(firstSequence >= 1L) { "历史事件 sequence 无效" }
        require(!isActiveCheckpoint || checkpointSegment != null) {
            "只有 checkpoint segment 可标记为 active"
        }
    }
}

internal data class MobileConversationContextFrame(
    val conversation: MobileConversationIdentity,
    val modelBinding: MobileGenerationModelBinding,
    val systemContract: MobileSystemContract,
    val checkpoint: MobileConversationCheckpoint?,
    val recentTurns: List<MobileConversationTurn>,
    val currentUserMessage: MobileTranscriptMessage,
    val currentTurnLedger: List<MobileToolExecutionReceipt>,
    val pendingToolTransactions: List<MobileToolTransaction>,
    val budget: MobileRequestBudgetEnvelope,
    val transcriptRevision: Long,
    val checkpointSegments: List<MobileConversationCheckpoint> = emptyList(),
) {
    val effectiveCheckpointSegments: List<MobileConversationCheckpoint>
        get() = when {
            checkpoint == null -> emptyList()
            checkpointSegments.isEmpty() -> listOf(checkpoint)
            else -> checkpointSegments
        }

    init {
        require(currentUserMessage.role == "user") { "ContextFrame 当前消息必须是 user" }
        require(currentUserMessage.content.isNotEmpty()) { "ContextFrame 当前用户消息必须逐字保留" }
        require(conversation.revision == transcriptRevision) {
            "ContextFrame transcript revision 与 conversation 不一致"
        }
        require(budget.modelBindingFingerprint == modelBinding.fingerprint) {
            "ContextFrame budget 与模型绑定不一致"
        }
        checkpoint?.let {
            require(it.conversationId == conversation.id && it.scope == conversation.kind) {
                "ContextFrame checkpoint 归属不匹配"
            }
            require(it.sourceRange.lastSequence < currentUserMessage.sequenceNo) {
                "ContextFrame checkpoint 必须早于当前用户消息"
            }
        }
        if (checkpoint == null) {
            require(checkpointSegments.isEmpty()) { "ContextFrame checkpoint_segments 需要活动 checkpoint" }
        } else {
            val segments = effectiveCheckpointSegments
            require(segments.last().fingerprint == checkpoint.fingerprint) {
                "ContextFrame 活动 checkpoint 必须是 checkpoint_segments 链尾"
            }
            segments.forEach { segment ->
                require(segment.conversationId == conversation.id && segment.scope == conversation.kind) {
                    "ContextFrame checkpoint segment 归属不匹配"
                }
                require(segment.sourceRange.lastSequence < currentUserMessage.sequenceNo) {
                    "ContextFrame checkpoint segment 必须早于当前用户消息"
                }
            }
            require(segments.zipWithNext().all { (left, right) ->
                left.sourceRange.lastSequence < right.sourceRange.firstSequence
            }) { "ContextFrame checkpoint segment ranges 必须按时间排序且互不重叠" }
        }
        val recentMessages = recentTurns.flatMap(MobileConversationTurn::messages)
        require(recentTurns.all(MobileConversationTurn::isClosed)) { "ContextFrame recent_turns 只能包含闭合回合" }
        require(recentMessages.zipWithNext().all { (left, right) -> left.sequenceNo < right.sequenceNo }) {
            "ContextFrame recent_turns 必须全局按 sequence 排序"
        }
        require(recentMessages.all { it.sequenceNo < currentUserMessage.sequenceNo }) {
            "ContextFrame recent_turns 必须早于当前用户消息"
        }
        require(recentTurns.none { turn ->
            effectiveCheckpointSegments.any { it.sourceRange.overlaps(turn) }
        }) { "ContextFrame recent_turns 不能与 checkpoint segment 重叠" }
        require(recentMessages.map(MobileTranscriptMessage::id).distinct().size == recentMessages.size &&
            recentMessages.none { it.id == currentUserMessage.id }
        ) { "ContextFrame 消息 ID 重复" }
        val transactionIds = pendingToolTransactions.map(MobileToolTransaction::transactionId)
        require(transactionIds.distinct().size == transactionIds.size) {
            "ContextFrame pending_tool_transactions 标识无效"
        }
        require(pendingToolTransactions.all { it.state == MobileToolTransactionState.DELIVERED }) {
            "ContextFrame 只能携带完整 delivered 工具事务"
        }
    }

    fun toJson(): JsonObject {
        val checkpointJson = checkpoint?.toFrameJson()
        val checkpointHash = checkpoint?.fingerprint
        val withoutFrameHash = buildJsonObject {
            put("schema", MobileConversationContextSchema.FRAME)
            put("conversation", conversation.toJson())
            put("model_binding", modelBinding.toJson())
            put("system_contract", systemContract.toJson())
            put("checkpoint", checkpointJson ?: JsonNull)
            put("checkpoint_segments", buildJsonArray {
                effectiveCheckpointSegments.forEach { add(it.toFrameJson()) }
            })
            put("recent_turns", buildJsonArray { recentTurns.forEach { add(it.toContextJson()) } })
            put("current_user_message", currentUserMessage.toContextJson())
            put("current_turn_ledger", buildJsonArray { currentTurnLedger.forEach { add(it.toFrameJson()) } })
            put("pending_tool_transactions", buildJsonArray {
                pendingToolTransactions.forEach { add(it.toFrameJson()) }
            })
            put("budget", budget.toJson())
            put("integrity", buildJsonObject {
                put("transcript_revision", transcriptRevision)
                put("checkpoint_hash", checkpointHash?.let(::JsonPrimitive) ?: JsonNull)
                put("frame_hash", JsonNull)
            })
        }
        val frameHash = mobileCanonicalSha256(withoutFrameHash)
        return JsonObject(withoutFrameHash.toMutableMap().apply {
            val integrity = (get("integrity") as JsonObject).toMutableMap()
            integrity["frame_hash"] = JsonPrimitive(frameHash)
            put("integrity", JsonObject(integrity))
        })
    }

    /** Segments and exact exceptional/recent turns in their original transcript order. */
    fun historicalEvents(): List<MobileHistoricalContextEvent> {
        val checkpointHash = checkpoint?.fingerprint
        return (
            effectiveCheckpointSegments.map { segment ->
                MobileHistoricalContextEvent(
                    firstSequence = segment.sourceRange.firstSequence,
                    checkpointSegment = segment,
                    isActiveCheckpoint = segment.fingerprint == checkpointHash,
                )
            } + recentTurns.map { turn ->
                MobileHistoricalContextEvent(
                    firstSequence = turn.firstSequence,
                    exactTurn = turn,
                )
            }
        ).sortedWith(
            compareBy<MobileHistoricalContextEvent>(MobileHistoricalContextEvent::firstSequence)
                .thenBy { if (it.checkpointSegment != null) 0 else 1 },
        )
    }
}

internal fun mobileConversationSourceHash(messages: List<MobileTranscriptMessage>): String {
    require(messages.isNotEmpty()) { "不能计算空消息范围的 source_hash" }
    val ordered = messages.sortedBy(MobileTranscriptMessage::sequenceNo)
    require(ordered.zipWithNext().all { (left, right) -> left.sequenceNo + 1L == right.sequenceNo }) {
        "checkpoint 来源消息必须是连续 sequence 范围"
    }
    return mobileCanonicalSha256(JsonArray(ordered.map(MobileTranscriptMessage::toCheckpointSourceJson)))
}

internal fun mobileCanonicalSha256(value: JsonElement): String =
    mobileConversationSha256(mobileCanonicalJson(value))

internal fun mobileConversationSha256(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray(Charsets.UTF_8))
    .joinToString("") { byte -> "%02x".format(byte) }

/** Cross-platform canonical JSON: sorted keys, compact separators, UTF-8 and explicit nulls. */
internal fun mobileCanonicalJson(value: JsonElement): String = when (value) {
    JsonNull -> "null"
    is JsonObject -> value.entries.sortedBy { it.key }.joinToString(
        prefix = "{",
        postfix = "}",
        separator = ",",
    ) { (key, item) -> "${JsonPrimitive(key)}:${mobileCanonicalJson(item)}" }
    is JsonArray -> value.joinToString(prefix = "[", postfix = "]", separator = ",", transform = ::mobileCanonicalJson)
    is JsonPrimitive -> value.toString()
    else -> error("不支持的 JSON 类型")
}

private val SHA256_PATTERN = Regex("[0-9a-f]{64}")

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String, fallback: Int = 0): Int =
    (get(name) as? JsonPrimitive)?.intOrNull ?: fallback

private fun JsonObject.long(name: String, fallback: Long = 0L): Long =
    (get(name) as? JsonPrimitive)?.longOrNull ?: fallback

private fun JsonObject.boolean(name: String, fallback: Boolean = false): Boolean =
    (get(name) as? JsonPrimitive)?.booleanOrNull ?: fallback

private fun JsonObject.optionalInt(name: String): Int? =
    (get(name) as? JsonPrimitive)?.intOrNull

private fun JsonObject.optionalLong(name: String): Long? =
    (get(name) as? JsonPrimitive)?.longOrNull

private fun JsonObject.array(name: String): JsonArray = get(name) as? JsonArray ?: JsonArray(emptyList())

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())

private fun JsonArray.objects(field: String): List<JsonObject> = map { raw ->
    raw as? JsonObject ?: throw MobileConversationStorageException("checkpoint $field 包含无效对象")
}

private fun JsonArray.strings(field: String): List<String> = map { raw ->
    (raw as? JsonPrimitive)?.contentOrNull
        ?: throw MobileConversationStorageException("checkpoint $field 包含无效字符串")
}
