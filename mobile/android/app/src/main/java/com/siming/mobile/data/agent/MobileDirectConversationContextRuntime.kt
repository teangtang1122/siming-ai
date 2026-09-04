package com.siming.mobile.data.agent

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

internal data class MobileConversationPreparationStatus(
    val status: String,
    val detail: String,
    val conversation: MobileConversationSnapshot,
    val budget: MobileRequestBudgetEnvelope? = null,
    val checkpointId: String? = conversation.activeCheckpoint?.id,
    val recentExactTurnCount: Int? = null,
)

internal data class MobilePreparedConversationRequest(
    val conversation: MobileConversationSnapshot,
    val frame: MobileConversationContextFrame,
    val rendered: MobileRenderedContextRequest,
)

/**
 * Measure the exact rendered/provider request used by DirectApi. Atomic message
 * content remains assigned to its semantic layer; JSON/message structure and
 * the provider envelope are then added once. A final protocol delta covers any
 * Responses-specific message/tool transformation without duplicating the body.
 */
internal fun countMobileRenderedRequestComponents(
    directApi: DirectApiClient,
    config: DirectApiConfig,
    frame: MobileConversationContextFrame,
    rendered: MobileRenderedContextRequest,
    scopedTools: JsonArray,
    maxOutputTokens: Int,
    toolChoice: String?,
    temperature: Double,
    extraBody: JsonObject?,
    counter: MobileConversationTokenCounter = MobileUtf8ByteTokenCounter,
): MobileRequestTokenComponents {
    val recentMessageIds = frame.recentTurns
        .flatMap(MobileConversationTurn::messages)
        .map(MobileTranscriptMessage::id)
        .toSet()
    val recentStatusMessageIds = frame.recentTurns
        .filter { it.status != "completed" }
        .map { "context-turn-status:${it.turnId}" }
        .toSet()
    val pendingMessageIds = mutableSetOf<String>()
    frame.pendingToolTransactions.forEach { transaction ->
        pendingMessageIds += transaction.assistantMessageId
        transaction.results.forEach { result ->
            pendingMessageIds += "${transaction.transactionId}:result:${result.toolCallId}"
        }
    }

    var systemPromptTokens = 0
    var checkpointTokens = 0
    var recentExactTurnTokens = 0
    var currentUserTokens = 0
    var currentTurnLedgerTokens = 0
    var pendingToolTransactionTokens = 0
    var providerStateTokens = 0
    var atomicTokens = 0
    rendered.messages.forEach { message ->
        val messageId = message.stringValue("message_id")
        require(messageId.isNotBlank()) { "渲染后的上下文消息缺少 message_id" }
        val toolCalls = message["tool_calls"] as? JsonArray
        val toolCallId = message.stringValue("tool_call_id")
        val semanticTokens = counter.countText(message.stringValue("content")) +
            (toolCalls?.takeIf { it.isNotEmpty() }?.let(counter::countValue) ?: 0) +
            (toolCallId.takeIf(String::isNotBlank)?.let(counter::countText) ?: 0)
        val providerState = message["provider_state"]
        val stateTokens = counter.countText(message.stringValue("reasoning_content")) +
            (providerState?.let(counter::countValue) ?: 0)
        providerStateTokens += stateTokens
        atomicTokens += semanticTokens + stateTokens
        when {
            messageId == "context-system-contract" -> systemPromptTokens += semanticTokens
            messageId == rendered.checkpointMessageId -> checkpointTokens += semanticTokens
            messageId == rendered.currentUserMessageId -> currentUserTokens += semanticTokens
            messageId in recentMessageIds || messageId in recentStatusMessageIds ->
                recentExactTurnTokens += semanticTokens
            messageId.startsWith("context-ledger:") -> currentTurnLedgerTokens += semanticTokens
            messageId in pendingMessageIds -> pendingToolTransactionTokens += semanticTokens
            else -> error("渲染后的上下文消息没有预算层归属：$messageId")
        }
    }

    val requestMessages = providerMessages(rendered.messages)
    val renderedMessageTokens = counter.countValue(JsonArray(requestMessages))
    val structuralTokens = (renderedMessageTokens - atomicTokens).coerceAtLeast(0)
    val toolSchemaTokens = counter.countValue(scopedTools)
    val providerEnvelopeTokens = counter.countValue(
        directApi.agentRequestPayload(
            config = config,
            messages = emptyList(),
            tools = JsonArray(emptyList()),
            toolChoice = toolChoice,
            maxOutputTokens = maxOutputTokens,
            temperature = temperature,
            extraBody = extraBody,
            stream = true,
        ),
    )
    val providerPayloadTokens = counter.countValue(
        directApi.agentRequestPayload(
            config = config,
            messages = requestMessages,
            tools = scopedTools,
            toolChoice = toolChoice,
            maxOutputTokens = maxOutputTokens,
            temperature = temperature,
            extraBody = extraBody,
            stream = true,
        ),
    )
    val toolsOffered = scopedTools.isNotEmpty()
    val components = MobileRequestTokenComponents(
        systemPromptTokens = systemPromptTokens,
        toolSchemaTokens = toolSchemaTokens,
        messageWrapperTokens = structuralTokens,
        providerProtocolTokens = providerEnvelopeTokens + (
            providerPayloadTokens - renderedMessageTokens - toolSchemaTokens - providerEnvelopeTokens
        ).coerceAtLeast(0),
        checkpointTokens = checkpointTokens,
        recentExactTurnTokens = recentExactTurnTokens,
        currentUserTokens = currentUserTokens,
        currentTurnLedgerTokens = currentTurnLedgerTokens,
        pendingToolTransactionTokens = pendingToolTransactionTokens,
        providerStateTokens = providerStateTokens,
        maxModelVisibleResultTokensForOpenTools =
            MobileNativeToolBudgetContract.maxModelVisibleResultTokens(toolsOffered),
        nextStepWrapperTokens = MobileNativeToolBudgetContract.nextStepWrapperTokens(toolsOffered),
    )
    check(components.currentInputTokens >= providerPayloadTokens) {
        "请求预算没有覆盖实际 DirectApi provider payload"
    }
    return components
}

/**
 * Shared Android DirectApi context assembler for workspace and creation Agents.
 * It is the only path that selects exact recent turns, creates checkpoints and
 * seals the provider-neutral ContextFrame before a business model step.
 */
internal class MobileDirectConversationContextRuntime(
    private val directApi: DirectApiClient,
    private val conversationStore: MobileAssistantConversationStore,
) {
    suspend fun prepare(
        storageId: String,
        currentUserPrompt: String,
        config: DirectApiConfig,
        conversation: MobileConversationSnapshot,
        turnContext: MobileAssistantTurnContext,
        systemPrompt: String,
        scopedTools: JsonArray,
        taskType: String,
        maxOutputTokens: Int,
        toolChoice: String? = null,
        temperature: Double = 0.3,
        extraBody: JsonObject? = null,
        currentTurnLedger: List<MobileToolExecutionReceipt>,
        pendingTransactions: List<MobileToolTransaction>,
        onStatus: suspend (MobileConversationPreparationStatus) -> Unit = {},
    ): MobilePreparedConversationRequest {
        val effectiveConfig = config.withContextWindowFallback()
        val contextWindow = requireNotNull(effectiveConfig.contextWindowTokens)
        val counter = if (
            effectiveConfig.contextCapacitySource == DirectApiConfig.CONTEXT_CAPACITY_FALLBACK
        ) {
            MobileFallbackUtf8ByteTokenCounter
        } else {
            MobileUtf8ByteTokenCounter
        }
        val currentUserContent = conversation.messages.firstOrNull {
            it.id == turnContext.userMessageId && it.sequenceNo == turnContext.userSequence
        }?.content ?: throw MobileConversationContextException(
            MobileConversationContextErrorCode.SOURCE_CHANGED,
            "当前用户消息不再存在，不能组装 Agent 请求",
        )
        if (currentUserContent != currentUserPrompt) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.SOURCE_CHANGED,
                "运行参数与持久化的当前用户原文不一致",
            )
        }
        val toolSchemaHash = mobileCanonicalSha256(scopedTools)
        val promptHash = mobileConversationSha256(systemPrompt)
        val binding = MobileGenerationModelBinding(
            taskType = taskType,
            provider = "android_direct_api",
            modelName = effectiveConfig.model.trim(),
            normalizedModel = effectiveConfig.model.trim(),
            protocol = effectiveConfig.protocol,
            contextWindowTokens = contextWindow,
            maxOutputTokens = maxOutputTokens,
            tokenCounterId = counter.counterId,
            capacityAssurance = counter.assurance,
            promptContractHash = promptHash,
            toolSchemaHash = toolSchemaHash,
            configFingerprint = mobileCanonicalSha256(buildJsonObject {
                put("base_url", effectiveConfig.baseUrl.trim().trimEnd('/'))
                put("model", effectiveConfig.model.trim())
                put("protocol", effectiveConfig.protocol)
                put("task_type", taskType)
                put("context_window_tokens", contextWindow)
                put("max_output_tokens", maxOutputTokens)
                put("safety_margin_tokens", effectiveConfig.safetyMarginTokens)
                put("tool_choice", toolChoice?.let(::JsonPrimitive) ?: kotlinx.serialization.json.JsonNull)
                put("temperature", temperature)
                put("extra_body", extraBody ?: JsonObject(emptyMap()))
            }),
        )
        val systemContract = MobileSystemContract(promptHash, toolSchemaHash)
        var current = conversation
        while (true) {
            val emptyBudget = buildMobileRequestBudget(
                binding = binding,
                counter = counter,
                components = MobileRequestTokenComponents(),
                safetyMarginTokens = effectiveConfig.safetyMarginTokens,
            )
            fun provisionalFrame(
                recentExactTurns: List<MobileConversationTurn>,
                budget: MobileRequestBudgetEnvelope,
            ): MobileConversationContextFrame = current.assembleContextFrame(
                turnContext = turnContext,
                modelBinding = binding,
                systemContract = systemContract,
                recentExactTurns = recentExactTurns,
                currentTurnLedger = currentTurnLedger,
                pendingToolTransactions = pendingTransactions,
                budget = budget,
            )

            val baselineFrame = provisionalFrame(emptyList(), emptyBudget)
            val baselineRendered = renderMobileContextFrameUnchecked(baselineFrame, systemPrompt)
            val baseline = buildMobileRequestBudget(
                binding = binding,
                counter = counter,
                components = countMobileRenderedRequestComponents(
                    directApi = directApi,
                    config = effectiveConfig,
                    frame = baselineFrame,
                    rendered = baselineRendered,
                    scopedTools = scopedTools,
                    maxOutputTokens = maxOutputTokens,
                    toolChoice = toolChoice,
                    temperature = temperature,
                    extraBody = extraBody,
                    counter = counter,
                ),
                safetyMarginTokens = effectiveConfig.safetyMarginTokens,
            )
            baseline.requireSendable()
            if (!baseline.fitsProjected) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY,
                    "当前工具整批结果与原生事务回放预算无法放入下一模型步骤",
                )
            }
            val plan = current.planRecentTurns(turnContext, counter, baseline)
            if (!plan.requiresCheckpoint) {
                val provisional = provisionalFrame(plan.recentExactTurns, baseline)
                val provisionalRendered = renderMobileContextFrameUnchecked(provisional, systemPrompt)
                val finalBudget = buildMobileRequestBudget(
                    binding = binding,
                    counter = counter,
                    components = countMobileRenderedRequestComponents(
                        directApi = directApi,
                        config = effectiveConfig,
                        frame = provisional,
                        rendered = provisionalRendered,
                        scopedTools = scopedTools,
                        maxOutputTokens = maxOutputTokens,
                        toolChoice = toolChoice,
                        temperature = temperature,
                        extraBody = extraBody,
                        counter = counter,
                    ),
                    safetyMarginTokens = effectiveConfig.safetyMarginTokens,
                )
                finalBudget.requireSendable()
                if (!finalBudget.fitsProjected) {
                    throw MobileConversationContextException(
                        MobileConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY,
                        "下一步工具结果预算超过模型容量",
                    )
                }
                val frame = provisionalFrame(plan.recentExactTurns, finalBudget)
                val rendered = renderMobileContextFrame(frame, systemPrompt)
                check(providerMessages(rendered.messages) == providerMessages(provisionalRendered.messages)) {
                    "回填最终预算后 provider 请求正文发生变化"
                }
                conversationStore.updateBudgetState(
                    projectId = storageId,
                    conversationId = current.conversationId,
                    budget = JsonObject(finalBudget.toJson().toMutableMap().apply {
                        put("recent_exact_turn_count", JsonPrimitive(plan.recentExactTurns.size))
                        put("original_history_tokens", JsonPrimitive(current.turns
                            .filter(MobileConversationTurn::isClosed)
                            .sumOf { turn -> counter.countValue(turn.toContextJson()) }))
                        put(
                            "active_history_tokens",
                            JsonPrimitive(finalBudget.checkpointTokens + finalBudget.recentExactTurnTokens),
                        )
                        put(
                            "trigger",
                            JsonPrimitive(
                                if (current.activeCheckpoint == null) "within_capacity"
                                else "projected_next_step_over_capacity",
                            ),
                        )
                        put("provider", JsonPrimitive(binding.provider))
                        put("model", JsonPrimitive(binding.modelName))
                    }),
                    expectedContextStateRevision = current.contextState.revision,
                )
                val persisted = conversationStore.snapshot(storageId, current.conversationId)
                    ?: error("会话预算状态保存后无法重新读取")
                onStatus(
                    MobileConversationPreparationStatus(
                        status = "ready",
                        detail = if (current.activeCheckpoint == null) {
                            "完整会话已按模型容量保留 ${plan.recentExactTurns.size} 个原文回合"
                        } else {
                            "已整理较早上下文 · 保留最近 ${plan.recentExactTurns.size} 轮原文"
                        },
                        conversation = persisted,
                        budget = finalBudget,
                        recentExactTurnCount = plan.recentExactTurns.size,
                    ),
                )
                return MobilePreparedConversationRequest(
                    conversation = persisted,
                    frame = frame,
                    rendered = rendered,
                )
            }

            val range = plan.checkpointRanges.firstOrNull()
                ?: throw MobileConversationContextException(
                    MobileConversationContextErrorCode.CHECKPOINT_REQUIRED,
                    "历史规划需要 checkpoint，但没有可整理的完整回合范围",
                )
            val generator = MobileDirectCheckpointGenerator(
                directApi = directApi,
                config = effectiveConfig,
                counter = counter,
                contextWindowTokens = contextWindow,
                maxOutputTokens = minOf(maxOutputTokens, CHECKPOINT_OUTPUT_TOKENS),
                safetyMarginTokens = effectiveConfig.safetyMarginTokens,
            )
            val sourceTurns = largestCheckpointPrefix(generator, current, range, binding)
            val sourceFirst = sourceTurns.first().firstSequence
            val sourceLast = sourceTurns.last().lastSequence
            val originalTokens = sourceTurns.sumOf { counter.countValue(it.toContextJson()) }
            val pending = conversationStore.beginCheckpoint(
                projectId = storageId,
                conversationId = current.conversationId,
                sourceFirstSequence = sourceFirst,
                sourceLastSequence = sourceLast,
                modelBinding = binding.toJson(),
                modelBindingFingerprint = binding.fingerprint,
                expectedContextStateRevision = current.contextState.revision,
                originalTokens = originalTokens,
            )
            val compressing = conversationStore.markCheckpointCompressing(
                storageId,
                current.conversationId,
                pending.checkpoint.id,
                pending.contextStateRevision,
            )
            try {
                val checkpointSnapshot = conversationStore.snapshot(storageId, current.conversationId)
                    ?: error("checkpoint attempt 创建后会话丢失")
                onStatus(
                    MobileConversationPreparationStatus(
                        status = "compressing",
                        detail = "正在整理较早上下文（消息 $sourceFirst–$sourceLast）…",
                        conversation = checkpointSnapshot,
                        checkpointId = pending.checkpoint.id,
                    ),
                )
                val checkpointSourceTurns = checkpointSnapshot.turns.filter { turn ->
                    sourceFirst <= turn.firstSequence && turn.lastSequence <= sourceLast
                }
                val checkpointLedger = checkpointSnapshot.deterministicExecutionLedger(checkpointSourceTurns)
                val request = checkpointSnapshot.checkpointGenerationRequest(
                    pending.checkpoint.id,
                    deterministicExecutionLedger = checkpointLedger,
                )
                val draft = generator.generate(request)
                val checkpointTokensEstimate = counter.countValue(buildJsonObject {
                    put("semantic_navigation", draft.semanticNavigation)
                    put("author_quote_positions", buildJsonArray {
                        draft.quoteSelections.forEach { quote ->
                            add(buildJsonObject {
                                put("message_id", quote.messageId)
                                put("start_char", quote.startChar)
                                put("end_char", quote.endChar)
                                put("purpose", quote.purpose)
                            })
                        }
                    })
                    put("prior_author_quote_states", buildJsonArray {
                        draft.priorAuthorQuoteStates.forEach { state ->
                            add(buildJsonObject {
                                put("message_id", state.messageId)
                                put("start_char", state.startChar)
                                put("end_char", state.endChar)
                                put("quote_sha256", state.quoteSha256)
                                put("status", state.status)
                            })
                        }
                    })
                })
                conversationStore.publishCheckpoint(
                    projectId = storageId,
                    conversationId = current.conversationId,
                    checkpointId = pending.checkpoint.id,
                    expectedContextStateRevision = compressing.contextStateRevision,
                    semanticDraft = draft,
                    deterministicExecutionLedger = checkpointLedger,
                    projectRefs = emptyList(),
                    validation = buildJsonObject {
                        put("capacity_assurance", counter.assurance)
                        put("token_counter_id", counter.counterId)
                        put("isolated_without_business_tools", true)
                    },
                    checkpointTokens = checkpointTokensEstimate.coerceAtLeast(1),
                )
            } catch (error: CancellationException) {
                runCatching {
                    conversationStore.cancelCheckpoint(
                        storageId,
                        current.conversationId,
                        pending.checkpoint.id,
                        compressing.contextStateRevision,
                    )
                }
                throw error
            } catch (error: Exception) {
                runCatching {
                    conversationStore.failCheckpoint(
                        projectId = storageId,
                        conversationId = current.conversationId,
                        checkpointId = pending.checkpoint.id,
                        expectedContextStateRevision = compressing.contextStateRevision,
                        errorDetail = error.message ?: "checkpoint 生成失败",
                    )
                }
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                    "较早上下文整理失败，尚未执行当前业务任务：${error.message}",
                )
            }
            current = conversationStore.snapshot(storageId, current.conversationId)
                ?: error("checkpoint 发布后会话丢失")
        }
    }

    private fun largestCheckpointPrefix(
        generator: MobileDirectCheckpointGenerator,
        conversation: MobileConversationSnapshot,
        turns: List<MobileConversationTurn>,
        binding: MobileGenerationModelBinding,
    ): List<MobileConversationTurn> {
        var accepted = emptyList<MobileConversationTurn>()
        turns.forEachIndexed { index, _ ->
            val candidate = turns.take(index + 1)
            val messages = candidate.flatMap(MobileConversationTurn::messages)
            val request = MobileCheckpointGenerationRequest(
                scope = conversation.conversationKind,
                conversationId = conversation.conversationId,
                transcriptRevision = conversation.transcriptRevision,
                sourceRange = MobileConversationSourceRange(
                    firstSequence = messages.first().sequenceNo,
                    lastSequence = messages.last().sequenceNo,
                    messageCount = messages.size,
                    sourceHash = mobileConversationSourceHash(messages),
                ),
                sourceMessages = messages,
                priorSegments = conversation.activeCheckpointSegments,
                deterministicExecutionLedger = conversation.deterministicExecutionLedger(candidate),
                modelBinding = binding.toJson(),
            )
            if (generator.promptFits(request)) accepted = candidate else return@forEachIndexed
        }
        if (accepted.isEmpty()) {
            throw MobileConversationContextException(
                MobileConversationContextErrorCode.CHECKPOINT_FAILED,
                "最早一个完整回合也无法放入 checkpoint 隔离整理请求",
            )
        }
        return accepted
    }

    private companion object {
        const val CHECKPOINT_OUTPUT_TOKENS = 4_000
    }
}
