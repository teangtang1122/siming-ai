package com.siming.mobile.data.agent

import android.content.Context
import com.siming.mobile.data.MobileAssistantConversation
import com.siming.mobile.data.MobileAssistantMessage
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

internal data class MobileAssistantTurnContext(
    val conversationId: String,
    val turnId: String,
    val userMessageId: String,
    val userSequence: Long,
    val transcriptRevision: Long,
)

internal data class MobileArchivedConversationTurn(
    val turnId: String,
    val userContent: String,
    val assistantContent: String,
    val status: String,
    val createdAt: String,
    val updatedAt: String,
) {
    init {
        require(turnId.isNotBlank() && userContent.isNotBlank() && assistantContent.isNotBlank()) {
            "归档回合缺少标识或完整消息"
        }
        require(status in setOf("completed", "error", "aborted", "cancelled")) {
            "归档回合状态无效"
        }
    }
}

internal data class MobileConversationSnapshot(
    val conversationId: String,
    val projectId: String,
    val conversationKind: String,
    val creationSessionId: String? = null,
    val title: String,
    val transcriptRevision: Long,
    val messages: List<MobileTranscriptMessage>,
    val contextState: MobileConversationContextState,
    val checkpoints: List<MobileConversationCheckpoint>,
    val replicaState: MobileTranscriptReplicaState,
    val toolRuntimeStates: List<MobileTurnToolRuntimeState>,
) {
    val turns: List<MobileConversationTurn> get() = mobileConversationTurns(messages)
    val activeCheckpoint: MobileConversationCheckpoint?
        get() = contextState.activeCheckpointId?.let { id -> checkpoints.firstOrNull { it.id == id } }
    val activeCheckpointSegments: List<MobileConversationCheckpoint>
        get() = mobileResolveCheckpointSegments(checkpoints, contextState.activeCheckpointId)
    val coveredSequenceRanges: List<MobileConversationSourceRange>
        get() = activeCheckpointSegments.map(MobileConversationCheckpoint::sourceRange)

    fun toolRuntimeState(turnId: String): MobileTurnToolRuntimeState? =
        toolRuntimeStates.firstOrNull { it.turnId == turnId }

    fun deterministicExecutionLedger(turns: List<MobileConversationTurn>): List<JsonObject> {
        val turnIds = turns.mapTo(linkedSetOf(), MobileConversationTurn::turnId)
        return toolRuntimeStates
            .filter { it.turnId in turnIds }
            .flatMap(MobileTurnToolRuntimeState::executionLedger)
            .distinctBy(MobileToolExecutionReceipt::stepId)
            .map(MobileToolExecutionReceipt::toFrameJson)
    }

    fun historicalTurns(turnContext: MobileAssistantTurnContext): List<MobileConversationTurn> {
        require(turnContext.conversationId == conversationId) { "TurnContext 不属于当前会话" }
        return turns.filter { it.lastSequence < turnContext.userSequence }
    }

    fun nextTranscriptImportRequest(
        maxMessages: Int = MOBILE_TRANSCRIPT_IMPORT_MAX_MESSAGES,
    ): MobileTranscriptImportRequest? = buildMobileTranscriptImportRequest(
        projectId = projectId,
        clientConversationId = conversationId,
        serverConversationId = replicaState.serverConversationId,
        title = title,
        closedMessages = turns.flatMap(MobileConversationTurn::messages),
        confirmedSourceRevision = replicaState.confirmedSourceRevision,
        maxMessages = maxMessages,
    )

    fun planRecentTurns(
        turnContext: MobileAssistantTurnContext,
        counter: MobileConversationTokenCounter,
        baselineBudget: MobileRequestBudgetEnvelope,
    ): MobileRecentTurnPlan = MobileRecentTurnPlanner.planWithCounter(
        turns = historicalTurns(turnContext),
        coveredSequenceRanges = coveredSequenceRanges,
        counter = counter,
        baselineBudget = baselineBudget,
    )

    fun checkpointGenerationRequest(
        checkpointId: String,
        deterministicExecutionLedger: List<JsonObject>,
    ): MobileCheckpointGenerationRequest {
        val attempt = checkpoints.firstOrNull { it.id == checkpointId }
            ?: throw MobileConversationStorageException("找不到 checkpoint generation attempt")
        require(attempt.status in setOf(
            MobileConversationCheckpointStatus.PENDING,
            MobileConversationCheckpointStatus.COMPRESSING,
        )) { "只有 pending/compressing attempt 可以生成 checkpoint" }
        val source = messages.filter {
            it.sequenceNo in attempt.sourceRange.firstSequence..attempt.sourceRange.lastSequence
        }
        if (source.size != attempt.sourceRange.messageCount ||
            mobileConversationSourceHash(source) != attempt.sourceRange.sourceHash
        ) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint generation source 已变化",
            )
        }
        val prior = buildList {
            var parentId = attempt.parentCheckpointId
            val seen = mutableSetOf<String>()
            while (true) {
                val currentParentId = parentId ?: break
                if (!seen.add(currentParentId)) throw MobileConversationStorageException("checkpoint parent 链形成循环")
                val parent = checkpoints.firstOrNull {
                    it.id == currentParentId && it.status == MobileConversationCheckpointStatus.READY
                } ?: throw MobileConversationContextException(
                    MobileConversationContextErrorCode.SOURCE_CHANGED,
                    "checkpoint parent 已失效",
                )
                add(parent)
                parentId = parent.parentCheckpointId
            }
        }.asReversed()
        return MobileCheckpointGenerationRequest(
            scope = attempt.scope,
            conversationId = conversationId,
            transcriptRevision = attempt.transcriptRevision,
            sourceRange = attempt.sourceRange,
            sourceMessages = source,
            priorSegments = prior,
            deterministicExecutionLedger = deterministicExecutionLedger,
            modelBinding = attempt.modelBinding,
        )
    }

    fun assembleContextFrame(
        turnContext: MobileAssistantTurnContext,
        modelBinding: MobileGenerationModelBinding,
        systemContract: MobileSystemContract,
        recentExactTurns: List<MobileConversationTurn>,
        currentTurnLedger: List<MobileToolExecutionReceipt>,
        pendingToolTransactions: List<MobileToolTransaction>,
        budget: MobileRequestBudgetEnvelope,
    ): MobileConversationContextFrame {
        require(turnContext.conversationId == conversationId) { "TurnContext 不属于当前会话" }
        if (turnContext.transcriptRevision != transcriptRevision) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "当前会话 revision 已变化，不能继续旧任务",
            )
        }
        val current = messages.firstOrNull {
            it.id == turnContext.userMessageId &&
                it.turnId == turnContext.turnId &&
                it.sequenceNo == turnContext.userSequence &&
                it.role == "user"
        } ?: throw MobileConversationContextException(
            MobileConversationContextErrorCode.SOURCE_CHANGED,
            "当前用户消息不再存在",
        )
        require(messages.lastOrNull()?.id == current.id) { "最新用户消息必须保持为当前唯一任务" }
        return MobileConversationContextFrame(
            conversation = MobileConversationIdentity(
                kind = conversationKind,
                id = conversationId,
                revision = transcriptRevision,
                projectId = projectId.takeIf { conversationKind == "workspace" },
                creationSessionId = creationSessionId.takeIf { conversationKind == "creation" },
            ),
            modelBinding = modelBinding,
            systemContract = systemContract,
            checkpoint = activeCheckpoint,
            recentTurns = recentExactTurns,
            currentUserMessage = current,
            currentTurnLedger = currentTurnLedger,
            pendingToolTransactions = pendingToolTransactions,
            budget = budget,
            transcriptRevision = transcriptRevision,
            checkpointSegments = activeCheckpointSegments,
        )
    }
}

internal data class MobileCheckpointAttemptContext(
    val checkpoint: MobileConversationCheckpoint,
    val contextStateRevision: Long,
)

internal data class MobileTranscriptReplicaUpdate(
    val replicaState: MobileTranscriptReplicaState,
    val transcriptRevision: Long,
)

internal class MobileConversationStorageException(
    message: String,
    cause: Throwable? = null,
) : IllegalStateException(message, cause)

/**
 * Durable standalone assistant transcript and derived conversation context state.
 *
 * Transcript and conversations are never silently pruned. Checkpoints are derived
 * records in the same atomic snapshot and cannot overwrite their source messages.
 */
internal class MobileAssistantConversationStore(
    private val directory: File,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    constructor(context: Context) : this(
        File(context.applicationContext.filesDir, DIRECTORY_NAME),
    )

    private val mutex = Mutex()
    private val liveCheckpointAttempts = mutableSetOf<String>()
    /**
     * Process-local executions that are allowed to own an open user message.
     * The set is intentionally not persisted: after a process restart every
     * surviving half-turn is interrupted by definition and is closed with the
     * deterministic aborted receipt before it can enter another request.
     */
    private val liveTurnIds = mutableSetOf<String>()

    suspend fun conversations(projectId: String): List<MobileAssistantConversation> = read(projectId)
        .sortedByDescending(LocalConversation::updatedAt)
        .map { value ->
            MobileAssistantConversation(
                id = value.id,
                title = value.title,
                messageCount = value.messages.size,
                updatedAt = value.updatedAt,
            )
        }

    suspend fun messages(projectId: String, conversationId: String): List<MobileAssistantMessage> =
        read(projectId).firstOrNull { it.id == conversationId }
            ?.messages
            .orEmpty()
            .map(MobileTranscriptMessage::displayMessage)

    suspend fun snapshot(projectId: String, conversationId: String): MobileConversationSnapshot? =
        read(projectId).firstOrNull { it.id == conversationId }?.snapshot(projectId)

    /**
     * Returns a stable, fully closed local transcript for the explicit Gateway
     * import bridge. A process-interrupted user turn is first closed as aborted
     * in the same atomic file update; the import endpoint never receives a
     * dangling half-turn and the original author message is never discarded.
     */
    suspend fun prepareTranscriptSync(
        projectId: String,
        conversationId: String,
    ): MobileConversationSnapshot? = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) return@mutate null
        val open = mobileConversationTurns(conversations[index].messages)
            .filterNot(MobileConversationTurn::isClosed)
        if (open.any { it.turnId in liveTurnIds }) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "手机独立 Agent 当前仍在执行，不能同步未闭合的实时回合",
            )
        }
        val closed = closeInterruptedTurn(conversations[index], Instant.now().toString())
        conversations[index] = closed
        closed.snapshot(projectId)
    }

    suspend fun beginTurn(
        projectId: String,
        conversationId: String?,
        prompt: String,
        conversationKind: String = "workspace",
        creationSessionId: String? = null,
    ): MobileAssistantTurnContext {
        var allocatedTurnId: String? = null
        return try {
            mutate(projectId) { conversations ->
                require(conversationKind in setOf("workspace", "creation")) { "会话 kind 无效" }
                if (conversationKind == "creation") require(!creationSessionId.isNullOrBlank()) {
                    "creation 会话缺少 session id"
                }
                val now = Instant.now().toString()
                val index = conversationId?.let { id -> conversations.indexOfFirst { it.id == id } } ?: -1
                val current = if (index >= 0) {
                    conversations[index].also { existing ->
                        require(existing.conversationKind == conversationKind) { "会话 kind 与已有归档不一致" }
                        require(existing.creationSessionId == creationSessionId) { "creation session 与已有归档不一致" }
                        require(mobileConversationTurns(existing.messages).all(MobileConversationTurn::isClosed)) {
                            "当前会话已有仍在执行的回合，不能并发开始新任务"
                        }
                    }
                } else {
                    LocalConversation(
                        id = UUID.randomUUID().toString(),
                        conversationKind = conversationKind,
                        creationSessionId = creationSessionId,
                        title = prompt.trim().replace(Regex("\\s+"), " ").take(36).ifBlank { "新对话" },
                        createdAt = now,
                        updatedAt = now,
                        transcriptRevision = 0L,
                        nextSequenceNo = 1L,
                        messages = emptyList(),
                        contextState = MobileConversationContextState(updatedAt = now),
                        checkpoints = emptyList(),
                        replicaState = MobileTranscriptReplicaState(updatedAt = now),
                        toolRuntimeStates = emptyList(),
                    )
                }
                val turnId = UUID.randomUUID().toString().also {
                    allocatedTurnId = it
                    liveTurnIds += it
                }
                val userMessage = MobileTranscriptMessage(
                    id = UUID.randomUUID().toString(),
                    sequenceNo = current.nextSequenceNo,
                    turnId = turnId,
                    role = "user",
                    content = prompt,
                    status = "running",
                    createdAt = now,
                )
                val updated = current.copy(
                    updatedAt = now,
                    transcriptRevision = current.transcriptRevision + 1L,
                    nextSequenceNo = current.nextSequenceNo + 1L,
                    messages = current.messages + userMessage,
                )
                if (index >= 0) conversations[index] = updated else conversations += updated
                MobileAssistantTurnContext(
                    conversationId = updated.id,
                    turnId = turnId,
                    userMessageId = userMessage.id,
                    userSequence = userMessage.sequenceNo,
                    transcriptRevision = updated.transcriptRevision,
                )
            }
        } catch (error: Exception) {
            allocatedTurnId?.let(liveTurnIds::remove)
            throw error
        }
    }

    /**
     * One-time/import reconciliation for the legacy creation-session audit.
     * The shared transcript becomes canonical after import; an older audit may
     * be a prefix after a crash, but it may never rewrite or contradict it.
     */
    suspend fun ensureConversationArchive(
        projectId: String,
        conversationId: String,
        conversationKind: String,
        creationSessionId: String?,
        title: String,
        archivedTurns: List<MobileArchivedConversationTurn>,
    ): MobileConversationSnapshot = mutate(projectId) { conversations ->
        require(conversationId.isNotBlank()) { "归档会话 ID 不能为空" }
        require(conversationKind in setOf("workspace", "creation")) { "归档会话 kind 无效" }
        if (conversationKind == "creation") require(!creationSessionId.isNullOrBlank()) {
            "creation 归档缺少 session id"
        }
        require(archivedTurns.map(MobileArchivedConversationTurn::turnId).distinct().size == archivedTurns.size) {
            "归档回合 ID 重复"
        }
        val archivedMessages = archivedTurns.flatMapIndexed { index, turn ->
            val firstSequence = index.toLong() * 2L + 1L
            listOf(
                MobileTranscriptMessage(
                    id = "archive-${mobileConversationSha256("${turn.turnId}\u001fuser").take(28)}",
                    sequenceNo = firstSequence,
                    turnId = turn.turnId,
                    role = "user",
                    content = turn.userContent,
                    status = "completed",
                    createdAt = turn.createdAt,
                ),
                MobileTranscriptMessage(
                    id = "archive-${mobileConversationSha256("${turn.turnId}\u001fassistant").take(28)}",
                    sequenceNo = firstSequence + 1L,
                    turnId = turn.turnId,
                    role = "assistant",
                    content = turn.assistantContent,
                    status = turn.status,
                    createdAt = turn.updatedAt,
                ),
            )
        }
        val index = conversations.indexOfFirst { it.id == conversationId }
        val now = Instant.now().toString()
        val current = if (index < 0) {
            LocalConversation(
                id = conversationId,
                conversationKind = conversationKind,
                creationSessionId = creationSessionId,
                title = title.ifBlank { "新对话" },
                createdAt = archivedTurns.firstOrNull()?.createdAt ?: now,
                updatedAt = archivedTurns.lastOrNull()?.updatedAt ?: now,
                transcriptRevision = archivedMessages.size.toLong(),
                nextSequenceNo = archivedMessages.size.toLong() + 1L,
                messages = archivedMessages,
                contextState = MobileConversationContextState(updatedAt = now),
                checkpoints = emptyList(),
                replicaState = MobileTranscriptReplicaState(updatedAt = now),
                toolRuntimeStates = emptyList(),
            ).also(conversations::add)
        } else {
            val existing = conversations[index]
            require(existing.conversationKind == conversationKind &&
                existing.creationSessionId == creationSessionId
            ) { "归档会话归属不匹配" }
            val commonCount = minOf(existing.messages.size, archivedMessages.size)
            val commonExisting = existing.messages.take(commonCount)
            val commonArchived = archivedMessages.take(commonCount)
            require(commonExisting.zip(commonArchived).all { (stored, archived) ->
                stored.turnId == archived.turnId && stored.role == archived.role &&
                    stored.content == archived.content && stored.status == archived.status
            }) { "creation audit 与 canonical transcript 冲突" }
            require(existing.messages.size % 2 == 0 || archivedMessages.size <= existing.messages.size) {
                "不能在未闭合 canonical 回合之后导入旧归档"
            }
            val merged = if (archivedMessages.size > existing.messages.size) {
                existing.messages + archivedMessages.drop(existing.messages.size).mapIndexed { offset, message ->
                    message.copy(sequenceNo = existing.messages.size.toLong() + offset + 1L)
                }
            } else {
                existing.messages
            }
            existing.copy(
                updatedAt = maxOf(existing.updatedAt, archivedTurns.lastOrNull()?.updatedAt.orEmpty()),
                transcriptRevision = merged.size.toLong(),
                nextSequenceNo = merged.size.toLong() + 1L,
                messages = merged,
            ).also { conversations[index] = it }
        }
        current.snapshot(projectId)
    }

    /**
     * Closes exactly the turn returned by beginTurn. A retry with identical data is idempotent;
     * conflicting or stale completion is rejected instead of appending a duplicate assistant.
     */
    suspend fun finishTurn(
        projectId: String,
        turnContext: MobileAssistantTurnContext,
        content: String,
        status: String,
        toolLogs: List<String>,
    ) {
        require(content.isNotBlank()) { "手机独立 Agent 回合结果不能为空" }
        require(status in CLOSED_TURN_STATUSES) { "手机独立 Agent 回合状态无效" }
        mutate(projectId) { conversations ->
            val index = conversations.indexOfFirst { it.id == turnContext.conversationId }
            if (index < 0) throw MobileConversationStorageException("找不到待完成的手机独立会话")
            val current = conversations[index]
            val userIndex = current.messages.indexOfFirst {
                it.id == turnContext.userMessageId &&
                    it.turnId == turnContext.turnId &&
                    it.sequenceNo == turnContext.userSequence &&
                    it.role == "user"
            }
            if (userIndex < 0) throw MobileConversationStorageException("待完成回合的作者消息不存在")
            val existing = current.messages.firstOrNull {
                it.turnId == turnContext.turnId && it.role == "assistant"
            }
            if (existing != null) {
                if (existing.content == content && existing.status == status && existing.toolLogs == toolLogs) {
                    return@mutate Unit
                }
                if (existing.status == "aborted" && current.transcriptRevision != turnContext.transcriptRevision) {
                    throw MobileConversationContextException(
                        MobileConversationContextErrorCode.SOURCE_CHANGED,
                        "该旧回合已被更新的用户任务安全终止",
                    )
                }
                throw MobileConversationStorageException("同一手机独立回合不能写入两个不同结果")
            }
            if (current.transcriptRevision != turnContext.transcriptRevision ||
                current.messages.lastOrNull()?.id != turnContext.userMessageId
            ) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.SOURCE_CHANGED,
                    "会话在本轮执行期间已出现更新，旧回合不会继续写入",
                )
            }
            val now = Instant.now().toString()
            // A closed turn keeps the author's message completed; the assistant owns
            // completed/error/aborted/cancelled execution outcome semantics.
            val closedUser = current.messages[userIndex].copy(status = "completed")
            val updatedMessages = current.messages.toMutableList().apply {
                this[userIndex] = closedUser
                add(
                    MobileTranscriptMessage(
                        id = UUID.randomUUID().toString(),
                        sequenceNo = current.nextSequenceNo,
                        turnId = turnContext.turnId,
                        role = "assistant",
                        content = content,
                        status = status,
                        createdAt = now,
                        toolLogs = toolLogs,
                    ),
                )
            }
            conversations[index] = current.copy(
                updatedAt = now,
                transcriptRevision = current.transcriptRevision + 1L,
                nextSequenceNo = current.nextSequenceNo + 1L,
                messages = updatedMessages,
            )
            liveTurnIds.remove(turnContext.turnId)
        }
    }

    suspend fun updateBudgetState(
        projectId: String,
        conversationId: String,
        budget: JsonObject,
        expectedContextStateRevision: Long,
    ): MobileConversationContextState = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到会话上下文状态")
        val current = conversations[index]
        requireContextRevision(current, expectedContextStateRevision)
        val now = Instant.now().toString()
        val state = current.contextState.copy(
            revision = current.contextState.revision + 1L,
            lastBudget = budget,
            updatedAt = now,
        )
        conversations[index] = current.copy(contextState = state, updatedAt = now)
        state
    }

    suspend fun recordDeliveredToolTransaction(
        projectId: String,
        turnContext: MobileAssistantTurnContext,
        transaction: MobileToolTransaction,
    ): MobileTurnToolRuntimeState = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == turnContext.conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到工具事务所属会话")
        val current = conversations[index]
        requireCurrentTurn(current, turnContext)
        val runtimeIndex = current.toolRuntimeStates.indexOfFirst { it.turnId == turnContext.turnId }
        val runtime = if (runtimeIndex >= 0) {
            current.toolRuntimeStates[runtimeIndex]
        } else {
            MobileTurnToolRuntimeState(turnContext.turnId)
        }
        val updatedRuntime = runtime.recordDelivered(transaction)
        val updatedStates = current.toolRuntimeStates.toMutableList().apply {
            if (runtimeIndex >= 0) this[runtimeIndex] = updatedRuntime else add(updatedRuntime)
        }
        conversations[index] = current.copy(
            toolRuntimeStates = updatedStates,
            updatedAt = Instant.now().toString(),
        )
        updatedRuntime
    }

    /** Atomically replaces provider-consumed native payloads with deterministic receipts. */
    suspend fun markDeliveredToolTransactionsConsumed(
        projectId: String,
        turnContext: MobileAssistantTurnContext,
    ): MobileTurnToolRuntimeState = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == turnContext.conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到工具事务所属会话")
        val current = conversations[index]
        requireCurrentTurn(current, turnContext)
        val runtimeIndex = current.toolRuntimeStates.indexOfFirst { it.turnId == turnContext.turnId }
        if (runtimeIndex < 0) return@mutate MobileTurnToolRuntimeState(turnContext.turnId)
        val updatedRuntime = current.toolRuntimeStates[runtimeIndex].markDeliveredConsumed()
        val updatedStates = current.toolRuntimeStates.toMutableList().apply {
            this[runtimeIndex] = updatedRuntime
        }
        conversations[index] = current.copy(
            toolRuntimeStates = updatedStates,
            updatedAt = Instant.now().toString(),
        )
        updatedRuntime
    }

    /** Atomically advances only the exact local prefix confirmed by transcript-import. */
    suspend fun recordTranscriptImportReceipt(
        projectId: String,
        conversationId: String,
        request: MobileTranscriptImportRequest,
        receipt: MobileTranscriptImportReceipt,
        expectedReplicaRevision: Long,
    ): MobileTranscriptReplicaUpdate = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到 transcript replica 所属会话")
        val current = conversations[index]
        val replica = current.replicaState
        if (replica.revision != expectedReplicaRevision) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "transcript replica 状态已更新，请按最新 revision 重试",
            )
        }
        if (request.clientConversationId != null && request.clientConversationId != current.id) {
            throw MobileConversationStorageException("transcript import client conversation 归属不匹配")
        }
        if (request.serverConversationId != null &&
            request.serverConversationId != replica.serverConversationId
        ) {
            throw MobileConversationStorageException("transcript import server conversation 归属不匹配")
        }
        if (replica.serverConversationId != null &&
            replica.serverConversationId != receipt.conversationId
        ) {
            throw MobileConversationStorageException("transcript import receipt 更换了已绑定的服务端会话")
        }
        if (receipt.transcriptRevision < request.transcriptRevision ||
            receipt.appliedRevision < request.transcriptRevision
        ) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "服务端未确认完整 transcript import 前缀",
            )
        }
        val localBySequence = current.messages.associateBy(MobileTranscriptMessage::sequenceNo)
        request.messages.forEach { incoming ->
            val local = localBySequence[incoming.sequenceNo]
                ?: throw MobileConversationContextException(
                    MobileConversationContextErrorCode.SOURCE_CHANGED,
                    "transcript import 本地源消息已变化",
                )
            if (MobileTranscriptImportMessage.fromTranscript(local).messageHash != incoming.messageHash) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.SOURCE_CHANGED,
                    "transcript import 本地源 hash 已变化",
                )
            }
        }
        if (replica.confirmedSourceRevision >= request.transcriptRevision) {
            return@mutate MobileTranscriptReplicaUpdate(replica, current.transcriptRevision)
        }
        if (request.messages.first().sequenceNo != replica.confirmedSourceRevision + 1L) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "transcript import receipt 不能跳过本地尚未确认的回合",
            )
        }
        val now = Instant.now().toString()
        val updatedReplica = replica.copy(
            revision = replica.revision + 1L,
            serverConversationId = receipt.conversationId,
            confirmedSourceRevision = request.transcriptRevision,
            updatedAt = now,
        )
        conversations[index] = current.copy(updatedAt = now, replicaState = updatedReplica)
        MobileTranscriptReplicaUpdate(updatedReplica, current.transcriptRevision)
    }

    suspend fun beginCheckpoint(
        projectId: String,
        conversationId: String,
        sourceFirstSequence: Long,
        sourceLastSequence: Long,
        modelBinding: JsonObject,
        modelBindingFingerprint: String,
        expectedContextStateRevision: Long,
        originalTokens: Int? = null,
    ): MobileCheckpointAttemptContext = mutate(projectId) { conversations ->
        require(modelBindingFingerprint.matches(Regex("[0-9a-f]{64}"))) {
            "checkpoint 必须绑定可验证的模型配置指纹"
        }
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到要整理的手机独立会话")
        val current = conversations[index]
        requireContextRevision(current, expectedContextStateRevision)
        val activeSegments = mobileResolveCheckpointSegments(
            current.checkpoints,
            current.contextState.activeCheckpointId,
        )
        val active = activeSegments.lastOrNull()
        if (active != null && sourceFirstSequence <= active.sourceRange.lastSequence) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "新 checkpoint segment 必须位于当前分段链之后，但允许跨过异常回合",
            )
        }
        val sourceMessages = sourceMessages(current, sourceFirstSequence, sourceLastSequence)
        validateCheckpointSourceTurns(sourceMessages)
        val sourceHash = mobileConversationSourceHash(sourceMessages)
        val idempotencyKey = mobileConversationSha256(
            listOf(
                conversationId,
                MobileConversationContextSchema.POLICY_VERSION.toString(),
                sourceFirstSequence.toString(),
                sourceLastSequence.toString(),
                sourceHash,
                modelBindingFingerprint,
                current.transcriptRevision.toString(),
                active?.id.orEmpty(),
                active?.fingerprint.orEmpty(),
            ).joinToString("\u001f"),
        )
        current.checkpoints.firstOrNull {
            it.idempotencyKey == idempotencyKey &&
                it.status in setOf(
                    MobileConversationCheckpointStatus.PENDING,
                    MobileConversationCheckpointStatus.COMPRESSING,
                    MobileConversationCheckpointStatus.READY,
                )
        }?.let { existing ->
            if (existing.status != MobileConversationCheckpointStatus.READY) liveCheckpointAttempts += existing.id
            return@mutate MobileCheckpointAttemptContext(existing, current.contextState.revision)
        }

        val now = Instant.now().toString()
        // Attempt identity is unique; idempotency_key coalesces only live/ready attempts.
        // A failed attempt can therefore be retried without duplicating a durable ID.
        val checkpointId = "checkpoint-${UUID.randomUUID()}"
        val superseded = current.checkpoints.map { checkpoint ->
            if (checkpoint.sourceRange.firstSequence == sourceFirstSequence &&
                checkpoint.sourceRange.lastSequence == sourceLastSequence &&
                checkpoint.status in setOf(
                    MobileConversationCheckpointStatus.PENDING,
                    MobileConversationCheckpointStatus.COMPRESSING,
                )
            ) {
                liveCheckpointAttempts.remove(checkpoint.id)
                checkpoint.copy(
                    status = MobileConversationCheckpointStatus.SUPERSEDED,
                    errorCode = MobileConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
                    errorDetail = "同一来源范围已有更新的 checkpoint attempt",
                    updatedAt = now,
                    completedAt = now,
                )
            } else {
                checkpoint
            }
        }
        val attempt = MobileConversationCheckpoint(
            id = checkpointId,
            conversationId = conversationId,
            scope = current.conversationKind,
            parentCheckpointId = current.contextState.activeCheckpointId,
            status = MobileConversationCheckpointStatus.PENDING,
            sourceRange = MobileConversationSourceRange(
                firstSequence = sourceFirstSequence,
                lastSequence = sourceLastSequence,
                messageCount = sourceMessages.size,
                sourceHash = sourceHash,
            ),
            transcriptRevision = current.transcriptRevision,
            idempotencyKey = idempotencyKey,
            modelBinding = modelBinding,
            modelBindingFingerprint = modelBindingFingerprint,
            sources = buildList {
                active?.let { parent ->
                    add(
                        MobileCheckpointSource(
                            sourceKind = "prior_segment",
                            sourceId = parent.id,
                            sourceSequence = null,
                            sourceHash = parent.fingerprint,
                        ),
                    )
                }
                sourceMessages.forEach { message ->
                    add(
                        MobileCheckpointSource(
                            sourceKind = "message",
                            sourceId = message.id,
                            sourceSequence = message.sequenceNo,
                            sourceHash = mobileCanonicalSha256(message.toCheckpointSourceJson()),
                        ),
                    )
                }
            },
            originalTokens = originalTokens,
            createdAt = now,
            updatedAt = now,
        )
        val state = current.contextState.copy(
            revision = current.contextState.revision + 1L,
            updatedAt = now,
        )
        conversations[index] = current.copy(
            updatedAt = now,
            contextState = state,
            checkpoints = superseded + attempt,
        )
        liveCheckpointAttempts += attempt.id
        MobileCheckpointAttemptContext(attempt, state.revision)
    }

    suspend fun markCheckpointCompressing(
        projectId: String,
        conversationId: String,
        checkpointId: String,
        expectedContextStateRevision: Long,
    ): MobileCheckpointAttemptContext = mutate(projectId) { conversations ->
        transitionCheckpoint(
            conversations = conversations,
            conversationId = conversationId,
            checkpointId = checkpointId,
            expectedContextStateRevision = expectedContextStateRevision,
            allowedFrom = setOf(MobileConversationCheckpointStatus.PENDING),
        ) { checkpoint, now -> checkpoint.copy(
            status = MobileConversationCheckpointStatus.COMPRESSING,
            updatedAt = now,
        ) }.also { liveCheckpointAttempts += checkpointId }
    }

    suspend fun publishCheckpoint(
        projectId: String,
        conversationId: String,
        checkpointId: String,
        expectedContextStateRevision: Long,
        semanticDraft: MobileCheckpointSemanticDraft,
        deterministicExecutionLedger: List<JsonObject>,
        projectRefs: List<JsonObject>,
        validation: JsonObject,
        checkpointTokens: Int,
    ): MobileCheckpointAttemptContext = mutate(projectId) { conversations ->
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到 checkpoint 所属会话")
        val current = conversations[index]
        requireContextRevision(current, expectedContextStateRevision)
        val checkpointIndex = current.checkpoints.indexOfFirst { it.id == checkpointId }
        if (checkpointIndex < 0) throw MobileConversationStorageException("找不到 checkpoint attempt")
        val attempt = current.checkpoints[checkpointIndex]
        require(attempt.status in setOf(
            MobileConversationCheckpointStatus.PENDING,
            MobileConversationCheckpointStatus.COMPRESSING,
        )) { "只有 pending/compressing checkpoint 可以发布" }
        val sourceMessages = runCatching {
            sourceMessages(
                current,
                attempt.sourceRange.firstSequence,
                attempt.sourceRange.lastSequence,
            )
        }.getOrElse {
            return@mutate supersedeChangedCheckpoint(conversations, index, checkpointIndex, attempt, it.message)
        }
        val actualSourceHash = mobileConversationSourceHash(sourceMessages)
        if (actualSourceHash != attempt.sourceRange.sourceHash) {
            return@mutate supersedeChangedCheckpoint(
                conversations,
                index,
                checkpointIndex,
                attempt,
                "checkpoint 来源 hash 已变化",
            )
        }
        validateCheckpointSourceTurns(sourceMessages)
        val (navigation, quotes) = validateAndMaterializeCheckpointDraft(semanticDraft, sourceMessages)
        require(checkpointTokens > 0) { "checkpoint token 计数必须可验证" }
        val parent = attempt.parentCheckpointId?.let { parentId ->
            current.checkpoints.firstOrNull {
                it.id == parentId && it.status == MobileConversationCheckpointStatus.READY
            } ?: throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint parent 已失效",
            )
        }
        if (parent?.id != current.contextState.activeCheckpointId) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
                "checkpoint parent 不再是当前活动链尾",
            )
        }
        if (parent != null && attempt.sourceRange.firstSequence <= parent.sourceRange.lastSequence) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint segment ranges 必须按时间排序且互不重叠",
            )
        }
        val activePreviousQuotes = parent?.authorQuotes.orEmpty().filterNot { it.superseded }
        val previousByIdentity = activePreviousQuotes.associateBy {
            Triple(it.messageId, it.startChar, it.endChar)
        }
        val decisionByIdentity = semanticDraft.priorAuthorQuoteStates.associateBy {
            Triple(it.messageId, it.startChar, it.endChar)
        }
        if (decisionByIdentity.keys != previousByIdentity.keys) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                "prior_author_quote_states 必须完整且仅引用全部 previous active author quotes",
            )
        }
        val rolledQuotes = previousByIdentity.map { (identity, quote) ->
            val decision = decisionByIdentity.getValue(identity)
            if (decision.quoteSha256 != quote.quoteSha256) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                    "prior author quote hash 与已验证作者原话不一致",
                )
            }
            quote.copy(superseded = decision.status == "superseded")
        }
        val rolledIdentities = rolledQuotes.mapTo(mutableSetOf()) {
            Triple(it.messageId, it.startChar, it.endChar)
        }
        quotes.forEach { quote ->
            val identity = Triple(quote.messageId, quote.startChar, quote.endChar)
            if (quote.superseded || !rolledIdentities.add(identity)) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                    "新来源 author quote 无效或与 prior quote identity 重复",
                )
            }
        }
        val aggregateQuotes = rolledQuotes + quotes
        val aggregateLedger = (parent?.executionLedger.orEmpty() + deterministicExecutionLedger)
            .associateBy { it.string("step_id") }
            .values
            .toList()
        val aggregateProjectRefs = (parent?.projectRefs.orEmpty() + projectRefs)
            .associateBy { "${it.string("type")}\u001f${it.string("id")}" }
            .values
            .toList()
        val now = Instant.now().toString()
        val ready = attempt.copy(
            status = MobileConversationCheckpointStatus.READY,
            semanticNavigation = navigation,
            authorQuotes = aggregateQuotes,
            executionLedger = aggregateLedger,
            projectRefs = aggregateProjectRefs,
            validation = validation,
            warnings = parent?.warnings.orEmpty(),
            segmentIds = parent?.let { it.segmentIds + it.id }.orEmpty(),
            checkpointTokens = checkpointTokens,
            errorCode = null,
            errorDetail = null,
            updatedAt = now,
            completedAt = now,
        )
        val checkpoints = current.checkpoints.toMutableList().apply { this[checkpointIndex] = ready }
        val state = current.contextState.copy(
            revision = current.contextState.revision + 1L,
            activeCheckpointId = ready.id,
            activeSourceLastSequence = ready.sourceRange.lastSequence,
            lastCompactedAt = now,
            updatedAt = now,
        )
        conversations[index] = current.copy(updatedAt = now, contextState = state, checkpoints = checkpoints)
        liveCheckpointAttempts.remove(checkpointId)
        MobileCheckpointAttemptContext(ready, state.revision)
    }

    suspend fun failCheckpoint(
        projectId: String,
        conversationId: String,
        checkpointId: String,
        expectedContextStateRevision: Long,
        errorCode: String = MobileConversationContextErrorCode.CHECKPOINT_FAILED,
        errorDetail: String,
    ): MobileCheckpointAttemptContext = mutate(projectId) { conversations ->
        transitionCheckpoint(
            conversations = conversations,
            conversationId = conversationId,
            checkpointId = checkpointId,
            expectedContextStateRevision = expectedContextStateRevision,
            allowedFrom = setOf(
                MobileConversationCheckpointStatus.PENDING,
                MobileConversationCheckpointStatus.COMPRESSING,
            ),
        ) { checkpoint, now -> checkpoint.copy(
            status = MobileConversationCheckpointStatus.FAILED,
            errorCode = errorCode,
            errorDetail = errorDetail,
            updatedAt = now,
            completedAt = now,
        ) }.also { liveCheckpointAttempts.remove(checkpointId) }
    }

    suspend fun cancelCheckpoint(
        projectId: String,
        conversationId: String,
        checkpointId: String,
        expectedContextStateRevision: Long,
    ): MobileCheckpointAttemptContext = mutate(projectId) { conversations ->
        transitionCheckpoint(
            conversations = conversations,
            conversationId = conversationId,
            checkpointId = checkpointId,
            expectedContextStateRevision = expectedContextStateRevision,
            allowedFrom = setOf(
                MobileConversationCheckpointStatus.PENDING,
                MobileConversationCheckpointStatus.COMPRESSING,
            ),
        ) { checkpoint, now -> checkpoint.copy(
            status = MobileConversationCheckpointStatus.CANCELLED,
            errorCode = MobileConversationContextErrorCode.CHECKPOINT_CANCELLED,
            cancelRequestedAt = now,
            updatedAt = now,
            completedAt = now,
        ) }.also { liveCheckpointAttempts.remove(checkpointId) }
    }

    private suspend fun read(projectId: String): List<LocalConversation> = withContext(Dispatchers.IO) {
        mutex.withLock {
            val loaded = readLocked(projectId)
            val recovered = recoverInterruptedTurns(
                recoverInterruptedCheckpoints(loaded.conversations),
            )
            if (loaded.migrated || recovered != loaded.conversations) {
                writeLocked(projectId, recovered)
            }
            recovered
        }
    }

    private suspend fun <T> mutate(
        projectId: String,
        action: (MutableList<LocalConversation>) -> T,
    ): T = withContext(Dispatchers.IO) {
        mutex.withLock {
            val loaded = readLocked(projectId)
            val conversations = recoverInterruptedTurns(
                recoverInterruptedCheckpoints(loaded.conversations),
            ).toMutableList()
            val result = action(conversations)
            writeLocked(projectId, conversations.sortedByDescending(LocalConversation::updatedAt))
            result
        }
    }

    private fun readLocked(projectId: String): LoadedConversations {
        val target = file(projectId)
        if (!target.isFile) return LoadedConversations(emptyList(), migrated = false)
        val root = try {
            json.parseToJsonElement(target.readText(Charsets.UTF_8)) as? JsonObject
                ?: throw MobileConversationStorageException("手机独立会话文件根节点不是对象")
        } catch (error: MobileConversationStorageException) {
            throw error
        } catch (error: Exception) {
            throw MobileConversationStorageException("手机独立会话文件已损坏，未覆盖原始文件", error)
        }
        return try {
            val schemaVersion = root.int("schema_version", LEGACY_SCHEMA_VERSION)
            if (schemaVersion !in setOf(LEGACY_SCHEMA_VERSION, MobileConversationContextSchema.STORAGE_VERSION)) {
                throw MobileConversationStorageException("不支持的手机独立会话存储版本：$schemaVersion")
            }
            val storedProjectId = root.string("project_id")
            if (schemaVersion == MobileConversationContextSchema.STORAGE_VERSION && storedProjectId != projectId) {
                throw MobileConversationStorageException("手机独立会话文件与当前作品不匹配")
            }
            val rawConversations = root.array("conversations").map { raw ->
                raw as? JsonObject
                    ?: throw MobileConversationStorageException("手机独立会话列表包含无效记录")
            }
            val conversations = rawConversations.map { raw ->
                if (schemaVersion == LEGACY_SCHEMA_VERSION) {
                    LocalConversation.fromLegacyJson(raw)
                } else {
                    LocalConversation.fromJson(raw)
                }
            }
            LoadedConversations(conversations, migrated = schemaVersion == LEGACY_SCHEMA_VERSION)
        } catch (error: MobileConversationStorageException) {
            throw error
        } catch (error: Exception) {
            throw MobileConversationStorageException("手机独立会话数据校验失败，未覆盖原始文件", error)
        }
    }

    private fun writeLocked(projectId: String, conversations: List<LocalConversation>) {
        directory.mkdirs()
        val target = file(projectId)
        val temporary = File(directory, ".${target.name}.${UUID.randomUUID()}.tmp")
        val bytes = buildJsonObject {
            put("schema_version", MobileConversationContextSchema.STORAGE_VERSION)
            put("project_id", projectId)
            put("conversations", buildJsonArray { conversations.forEach { add(it.toJson()) } })
        }.toString().toByteArray(Charsets.UTF_8)
        try {
            FileOutputStream(temporary).use { output ->
                output.write(bytes)
                output.flush()
                output.fd.sync()
            }
            Files.move(
                temporary.toPath(),
                target.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (error: Exception) {
            temporary.delete()
            throw MobileConversationStorageException("手机独立会话原子写入失败，原文件保持不变", error)
        }
    }

    private fun recoverInterruptedCheckpoints(
        conversations: List<LocalConversation>,
    ): List<LocalConversation> {
        val now = Instant.now().toString()
        return conversations.map { conversation ->
            var changed = false
            val recovered = conversation.checkpoints.map { checkpoint ->
                if (checkpoint.id !in liveCheckpointAttempts && checkpoint.status in setOf(
                    MobileConversationCheckpointStatus.PENDING,
                    MobileConversationCheckpointStatus.COMPRESSING,
                )) {
                    changed = true
                    checkpoint.copy(
                        status = MobileConversationCheckpointStatus.FAILED,
                        errorCode = MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                        errorDetail = "应用在 checkpoint 发布前退出，已确定性恢复为 failed",
                        updatedAt = now,
                        completedAt = now,
                    )
                } else {
                    checkpoint
                }
            }
            if (changed) {
                conversation.copy(
                    updatedAt = now,
                    checkpoints = recovered,
                    contextState = conversation.contextState.copy(
                        revision = conversation.contextState.revision + 1L,
                        updatedAt = now,
                    ),
                )
            } else {
                conversation
            }
        }
    }

    private fun recoverInterruptedTurns(
        conversations: List<LocalConversation>,
    ): List<LocalConversation> {
        val now = Instant.now().toString()
        return conversations.map { conversation ->
            val openTurns = mobileConversationTurns(conversation.messages)
                .filterNot(MobileConversationTurn::isClosed)
            when {
                openTurns.isEmpty() -> conversation
                openTurns.any { it.turnId in liveTurnIds } -> conversation
                else -> closeInterruptedTurn(conversation, now)
            }
        }
    }

    private fun closeInterruptedTurn(
        conversation: LocalConversation,
        now: String,
    ): LocalConversation {
        val openTurns = mobileConversationTurns(conversation.messages).filterNot(MobileConversationTurn::isClosed)
        if (openTurns.isEmpty()) return conversation
        if (openTurns.size != 1 || openTurns.single().messages.singleOrNull()?.id != conversation.messages.lastOrNull()?.id) {
            throw MobileConversationStorageException("会话包含无法确定性恢复的多个未闭合回合")
        }
        val open = openTurns.single()
        val user = open.messages.single()
        require(user.role == "user") { "未闭合回合缺少作者消息" }
        val messages = conversation.messages.toMutableList()
        val userIndex = messages.indexOfFirst { it.id == user.id }
        messages[userIndex] = user.copy(status = "completed")
        messages += MobileTranscriptMessage(
            id = UUID.randomUUID().toString(),
            sequenceNo = conversation.nextSequenceNo,
            turnId = user.turnId,
            role = "assistant",
            content = "上一轮任务未完成，已在新任务开始前安全终止。",
            status = "aborted",
            createdAt = now,
        )
        return conversation.copy(
            updatedAt = now,
            transcriptRevision = conversation.transcriptRevision + 1L,
            nextSequenceNo = conversation.nextSequenceNo + 1L,
            messages = messages,
        )
    }

    private fun transitionCheckpoint(
        conversations: MutableList<LocalConversation>,
        conversationId: String,
        checkpointId: String,
        expectedContextStateRevision: Long,
        allowedFrom: Set<String>,
        transition: (MobileConversationCheckpoint, String) -> MobileConversationCheckpoint,
    ): MobileCheckpointAttemptContext {
        val index = conversations.indexOfFirst { it.id == conversationId }
        if (index < 0) throw MobileConversationStorageException("找不到 checkpoint 所属会话")
        val current = conversations[index]
        requireContextRevision(current, expectedContextStateRevision)
        val checkpointIndex = current.checkpoints.indexOfFirst { it.id == checkpointId }
        if (checkpointIndex < 0) throw MobileConversationStorageException("找不到 checkpoint attempt")
        val existing = current.checkpoints[checkpointIndex]
        require(existing.status in allowedFrom) { "checkpoint 状态 ${existing.status} 不允许本次转换" }
        val now = Instant.now().toString()
        val updatedCheckpoint = transition(existing, now)
        val checkpoints = current.checkpoints.toMutableList().apply { this[checkpointIndex] = updatedCheckpoint }
        val state = current.contextState.copy(
            revision = current.contextState.revision + 1L,
            updatedAt = now,
        )
        conversations[index] = current.copy(updatedAt = now, contextState = state, checkpoints = checkpoints)
        return MobileCheckpointAttemptContext(updatedCheckpoint, state.revision)
    }

    private fun supersedeChangedCheckpoint(
        conversations: MutableList<LocalConversation>,
        conversationIndex: Int,
        checkpointIndex: Int,
        attempt: MobileConversationCheckpoint,
        detail: String?,
    ): MobileCheckpointAttemptContext {
        val current = conversations[conversationIndex]
        val now = Instant.now().toString()
        val superseded = attempt.copy(
            status = MobileConversationCheckpointStatus.SUPERSEDED,
            errorCode = MobileConversationContextErrorCode.SOURCE_CHANGED,
            errorDetail = detail ?: "checkpoint 来源已变化",
            updatedAt = now,
            completedAt = now,
        )
        val checkpoints = current.checkpoints.toMutableList().apply { this[checkpointIndex] = superseded }
        val state = current.contextState.copy(
            revision = current.contextState.revision + 1L,
            updatedAt = now,
        )
        conversations[conversationIndex] = current.copy(
            updatedAt = now,
            contextState = state,
            checkpoints = checkpoints,
        )
        liveCheckpointAttempts.remove(attempt.id)
        return MobileCheckpointAttemptContext(superseded, state.revision)
    }

    private fun requireContextRevision(current: LocalConversation, expected: Long) {
        if (current.contextState.revision != expected) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
                "会话上下文状态已更新，请按最新 revision 重试",
            )
        }
    }

    private fun requireCurrentTurn(
        current: LocalConversation,
        turnContext: MobileAssistantTurnContext,
    ) {
        val last = current.messages.lastOrNull()
        if (current.transcriptRevision != turnContext.transcriptRevision ||
            last == null ||
            last.id != turnContext.userMessageId ||
            last.turnId != turnContext.turnId ||
            last.role != "user"
        ) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "工具事务不属于当前运行中的作者回合",
            )
        }
    }

    private fun sourceMessages(
        conversation: LocalConversation,
        firstSequence: Long,
        lastSequence: Long,
    ): List<MobileTranscriptMessage> {
        require(firstSequence >= 1L && lastSequence >= firstSequence) { "checkpoint source range 无效" }
        val selected = conversation.messages.filter { it.sequenceNo in firstSequence..lastSequence }
        if (selected.size.toLong() != lastSequence - firstSequence + 1L ||
            selected.firstOrNull()?.sequenceNo != firstSequence ||
            selected.lastOrNull()?.sequenceNo != lastSequence
        ) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint 来源消息不再连续",
            )
        }
        return selected
    }

    private fun validateCheckpointSourceTurns(sourceMessages: List<MobileTranscriptMessage>) {
        val turns = mobileConversationTurns(sourceMessages)
        if (turns.isEmpty() || turns.any { !it.isCheckpointEligible }) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint 只能覆盖连续、完整、completed 的旧回合",
            )
        }
    }

    private fun file(projectId: String): File {
        require(projectId.matches(PROJECT_ID_PATTERN)) { "无效的作品 ID" }
        return File(directory, "$projectId.json")
    }

    private data class LoadedConversations(
        val conversations: List<LocalConversation>,
        val migrated: Boolean,
    )

    private data class LocalConversation(
        val id: String,
        val conversationKind: String,
        val creationSessionId: String?,
        val title: String,
        val createdAt: String,
        val updatedAt: String,
        val transcriptRevision: Long,
        val nextSequenceNo: Long,
        val messages: List<MobileTranscriptMessage>,
        val contextState: MobileConversationContextState,
        val checkpoints: List<MobileConversationCheckpoint>,
        val replicaState: MobileTranscriptReplicaState,
        val toolRuntimeStates: List<MobileTurnToolRuntimeState>,
    ) {
        init {
            require(conversationKind in setOf("workspace", "creation")) { "会话 kind 无效" }
            require(
                (conversationKind == "creation" && !creationSessionId.isNullOrBlank()) ||
                    (conversationKind == "workspace" && creationSessionId == null),
            ) { "会话 kind 与 creation session 归属不一致" }
            require(checkpoints.all { it.scope == conversationKind }) {
                "checkpoint scope 与会话 kind 不一致"
            }
        }

        fun snapshot(projectId: String): MobileConversationSnapshot = MobileConversationSnapshot(
            conversationId = id,
            projectId = projectId,
            conversationKind = conversationKind,
            creationSessionId = creationSessionId,
            title = title,
            transcriptRevision = transcriptRevision,
            messages = messages,
            contextState = contextState,
            checkpoints = checkpoints,
            replicaState = replicaState,
            toolRuntimeStates = toolRuntimeStates,
        )

        fun toJson(): JsonObject = buildJsonObject {
            put("id", id)
            put("conversation_kind", conversationKind)
            creationSessionId?.let { put("creation_session_id", it) }
            put("title", title)
            put("created_at", createdAt)
            put("updated_at", updatedAt)
            put("transcript_revision", transcriptRevision)
            put("next_sequence_no", nextSequenceNo)
            put("messages", buildJsonArray { messages.forEach { add(it.toJson()) } })
            put("context_state", contextState.toJson())
            put("checkpoints", buildJsonArray { checkpoints.forEach { add(it.toJson()) } })
            put("replica_state", replicaState.toJson())
            put("tool_runtime_states", buildJsonArray {
                toolRuntimeStates.forEach { add(it.toJson()) }
            })
        }

        companion object {
            fun fromJson(root: JsonObject): LocalConversation {
                val id = root.string("id")
                if (id.isBlank()) throw MobileConversationStorageException("手机独立会话缺少 ID")
                val messages = root.array("messages").map { raw ->
                    val item = raw as? JsonObject
                        ?: throw MobileConversationStorageException("手机独立会话包含无效消息")
                    MobileTranscriptMessage.fromJson(item)
                }
                validateSequences(messages)
                val transcriptRevision = (root["transcript_revision"] as? JsonPrimitive)?.longOrNull
                    ?: throw MobileConversationStorageException("手机独立会话缺少有效 transcript_revision")
                val nextSequenceNo = (root["next_sequence_no"] as? JsonPrimitive)?.longOrNull
                    ?: throw MobileConversationStorageException("手机独立会话缺少有效 next_sequence_no")
                val expectedTranscriptRevision = messages.size.toLong()
                if (transcriptRevision != expectedTranscriptRevision) {
                    throw MobileConversationStorageException(
                        "手机独立会话 transcript_revision 必须等于完整消息数",
                    )
                }
                if (nextSequenceNo != expectedTranscriptRevision + 1L) {
                    throw MobileConversationStorageException(
                        "手机独立会话 next_sequence_no 必须紧接最后一条消息",
                    )
                }
                val checkpoints = root.array("checkpoints").map { raw ->
                    val item = raw as? JsonObject
                        ?: throw MobileConversationStorageException("手机独立会话包含无效 checkpoint")
                    MobileConversationCheckpoint.fromJson(item)
                }
                val contextState = MobileConversationContextState.fromJson(root.objectValue("context_state"))
                val replicaState = MobileTranscriptReplicaState.fromJson(root.objectValue("replica_state"))
                val toolRuntimeStates = root.array("tool_runtime_states").map { raw ->
                    MobileTurnToolRuntimeState.fromJson(
                        raw as? JsonObject
                            ?: throw MobileConversationStorageException("工具运行状态包含无效记录"),
                    )
                }
                if (toolRuntimeStates.map(MobileTurnToolRuntimeState::turnId).distinct().size !=
                    toolRuntimeStates.size ||
                    toolRuntimeStates.any { runtime -> messages.none { it.turnId == runtime.turnId } }
                ) {
                    throw MobileConversationStorageException("工具运行状态与 transcript 回合不一致")
                }
                if (replicaState.confirmedSourceRevision > 0L) {
                    if (replicaState.serverConversationId == null ||
                        replicaState.confirmedSourceRevision % 2L != 0L ||
                        replicaState.confirmedSourceRevision > messages.size.toLong() ||
                        mobileConversationTurns(
                            messages.take(replicaState.confirmedSourceRevision.toInt()),
                        ).any { !it.isClosed }
                    ) {
                        throw MobileConversationStorageException("transcript replica cursor 不是已确认的完整回合前缀")
                    }
                    messages.take(replicaState.confirmedSourceRevision.toInt())
                        .forEach(MobileTranscriptImportMessage::fromTranscript)
                }
                if (contextState.activeCheckpointId != null &&
                    checkpoints.none {
                        it.id == contextState.activeCheckpointId &&
                            it.status == MobileConversationCheckpointStatus.READY
                    }
                ) {
                    throw MobileConversationStorageException("活动 checkpoint 指针无效")
                }
                val activeSegments = mobileResolveCheckpointSegments(
                    checkpoints,
                    contextState.activeCheckpointId,
                )
                val activeLastSequence = activeSegments.lastOrNull()?.sourceRange?.lastSequence ?: 0L
                if (contextState.activeSourceLastSequence != activeLastSequence) {
                    throw MobileConversationStorageException(
                        "active_source_last_sequence 与活动 checkpoint 链尾不一致",
                    )
                }
                activeSegments.forEach { segment ->
                    val range = segment.sourceRange
                    val source = messages.filter { it.sequenceNo in range.firstSequence..range.lastSequence }
                    if (source.size != range.messageCount || source.isEmpty() ||
                        source.first().sequenceNo != range.firstSequence ||
                        source.last().sequenceNo != range.lastSequence ||
                        mobileConversationSourceHash(source) != range.sourceHash ||
                        mobileConversationTurns(source).any { !it.isCheckpointEligible }
                    ) {
                        throw MobileConversationStorageException(
                            "checkpoint segment 与完整 completed 原始回合不一致",
                        )
                    }
                }
                return LocalConversation(
                    id = id,
                    conversationKind = root.string("conversation_kind").ifBlank { "workspace" },
                    creationSessionId = root.string("creation_session_id").ifBlank { null },
                    title = root.string("title").ifBlank { "新对话" },
                    createdAt = root.string("created_at"),
                    updatedAt = root.string("updated_at"),
                    transcriptRevision = transcriptRevision,
                    nextSequenceNo = nextSequenceNo,
                    messages = messages,
                    contextState = contextState,
                    checkpoints = checkpoints,
                    replicaState = replicaState,
                    toolRuntimeStates = toolRuntimeStates,
                )
            }

            fun fromLegacyJson(root: JsonObject): LocalConversation {
                val id = root.string("id")
                if (id.isBlank()) throw MobileConversationStorageException("旧手机独立会话缺少 ID")
                var currentTurnId: String? = null
                val messages = root.array("messages").mapIndexed { index, raw ->
                    val item = raw as? JsonObject
                        ?: throw MobileConversationStorageException("旧手机独立会话包含无效消息")
                    val sequence = index.toLong() + 1L
                    val role = item.string("role")
                    val content = item.string("content")
                    val messageId = item.string("id").ifBlank {
                        "legacy-${mobileConversationSha256("$id\u001f$sequence\u001f$role\u001f$content").take(29)}"
                    }
                    val turnId = when (role) {
                        "user" -> "legacy-turn-${messageId.take(48)}".also { currentTurnId = it }
                        "assistant" -> currentTurnId ?: "legacy-turn-${messageId.take(48)}"
                        else -> throw MobileConversationStorageException("旧会话包含不支持的消息角色")
                    }
                    if (role == "assistant") currentTurnId = null
                    MobileTranscriptMessage(
                        id = messageId,
                        sequenceNo = sequence,
                        turnId = turnId,
                        role = role,
                        content = content,
                        status = if (role == "user") {
                            "completed"
                        } else {
                            item.string("status").ifBlank { "completed" }
                        },
                        createdAt = item.string("created_at"),
                        toolLogs = item.array("tool_logs").map { rawLog ->
                            (rawLog as? JsonPrimitive)?.contentOrNull
                                ?: throw MobileConversationStorageException("旧会话 tool_logs 包含无效记录")
                        },
                    )
                }
                validateSequences(messages)
                return LocalConversation(
                    id = id,
                    conversationKind = "workspace",
                    creationSessionId = null,
                    title = root.string("title").ifBlank { "新对话" },
                    createdAt = root.string("created_at"),
                    updatedAt = root.string("updated_at"),
                    transcriptRevision = messages.size.toLong(),
                    nextSequenceNo = messages.size.toLong() + 1L,
                    messages = messages,
                    contextState = MobileConversationContextState(updatedAt = root.string("updated_at")),
                    checkpoints = emptyList(),
                    replicaState = MobileTranscriptReplicaState(updatedAt = root.string("updated_at")),
                    toolRuntimeStates = emptyList(),
                )
            }

            private fun validateSequences(messages: List<MobileTranscriptMessage>) {
                require(messages.map(MobileTranscriptMessage::id).distinct().size == messages.size) {
                    "手机独立会话消息 ID 重复"
                }
                require(messages.map(MobileTranscriptMessage::sequenceNo) ==
                    (1L..messages.size.toLong()).toList()
                ) { "手机独立会话 sequence_no 必须从 1 连续递增" }
            }
        }
    }

    companion object {
        private const val DIRECTORY_NAME = "mobile-assistant-conversations"
        private const val LEGACY_SCHEMA_VERSION = 1
        private val CLOSED_TURN_STATUSES = setOf("completed", "error", "aborted", "cancelled")
        private val PROJECT_ID_PATTERN = Regex("[A-Za-z0-9._:-]{1,64}")
    }
}

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String, fallback: Int = 0): Int =
    (get(name) as? JsonPrimitive)?.intOrNull ?: fallback

private fun JsonObject.long(name: String, fallback: Long = 0L): Long =
    (get(name) as? JsonPrimitive)?.longOrNull ?: fallback

private fun JsonObject.array(name: String): JsonArray = get(name) as? JsonArray ?: JsonArray(emptyList())

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
