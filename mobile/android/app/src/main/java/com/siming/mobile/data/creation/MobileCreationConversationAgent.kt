package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.agent.MobileAssistantConversationStore
import com.siming.mobile.data.agent.MobileAssistantTurnContext
import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import com.siming.mobile.data.agent.MobileConversationContextException
import com.siming.mobile.data.agent.MobileConversationSnapshot
import com.siming.mobile.data.agent.MobileDirectConversationContextRuntime
import com.siming.mobile.data.agent.MobileNativeToolBudgetContract
import com.siming.mobile.data.agent.MobileToolCallRecord
import com.siming.mobile.data.agent.MobileToolExecutionReceipt
import com.siming.mobile.data.agent.MobileToolProtocolValidator
import com.siming.mobile.data.agent.MobileToolResultRecord
import com.siming.mobile.data.agent.MobileToolTransaction
import com.siming.mobile.data.agent.MobileToolTransactionState
import com.siming.mobile.data.agent.mobileCanonicalJson
import com.siming.mobile.data.agent.mobileConversationContextStatePayload
import com.siming.mobile.data.agent.persistRejectedMobileNativeToolBatch
import com.siming.mobile.data.agent.providerMessages
import com.siming.mobile.data.network.DirectAgentTurn
import com.siming.mobile.data.network.DirectAgentToolCall
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put

internal data class MobileCreationConversationResult(
    val session: JsonObject,
    val reply: String,
    val toolResults: JsonArray,
    val modelMessages: JsonArray,
    val replayable: Boolean,
    val status: String,
    val createdProjectId: String? = null,
    val promptMetrics: JsonArray = JsonArray(emptyList()),
    val replyStatus: String = "model",
    val replyDiagnostics: JsonArray = JsonArray(emptyList()),
)

/**
 * Standalone Android projection of backend/services/novel_creation_agent.py.
 *
 * The model gets the same build-generated system prompt and tool schemas as PC.
 * Storage is the only mobile-specific layer: tools mutate the local creation
 * session and the repository persists each successful write immediately.
 */
internal class MobileCreationConversationAgent(
    private val contract: PcCreationAgentContract,
    private val stageAgent: MobileCreationAgent,
    private val directApi: DirectApiClient,
    private val conversationStore: MobileAssistantConversationStore,
    private val persistSession: suspend (JsonObject) -> Unit,
    private val finalizeSession: suspend (JsonObject) -> Pair<JsonObject, String>,
) {
    constructor(
        context: Context,
        stageAgent: MobileCreationAgent,
        directApi: DirectApiClient,
        conversationStore: MobileAssistantConversationStore,
        persistSession: suspend (JsonObject) -> Unit,
        finalizeSession: suspend (JsonObject) -> Pair<JsonObject, String>,
    ) : this(
        PcCreationAgentContract(context.applicationContext),
        stageAgent,
        directApi,
        conversationStore,
        persistSession,
        finalizeSession,
    )

    private val conversationContextRuntime = MobileDirectConversationContextRuntime(
        directApi = directApi,
        conversationStore = conversationStore,
    )

    suspend fun run(
        source: JsonObject,
        message: String,
        storageId: String,
        conversation: MobileConversationSnapshot,
        turnContext: MobileAssistantTurnContext,
        config: DirectApiConfig,
        onProgress: suspend (CreationAgentProgressEvent) -> Unit = {},
    ): MobileCreationConversationResult = com.siming.mobile.data.observability.MobileTrace.turn(
        "creation_session", source.string("id"), buildJsonObject {
            put("turn_id", turnContext.turnId); put("conversation_id", turnContext.conversationId); put("user_message_id", turnContext.userMessageId)
        },
    ) { executeTurn(source, message, storageId, conversation, turnContext, config, onProgress).also {
        com.siming.mobile.data.observability.MobileTrace.current.get()?.businessStatus = it.status
    } }

    private suspend fun executeTurn(
        source: JsonObject,
        message: String,
        storageId: String,
        conversation: MobileConversationSnapshot,
        turnContext: MobileAssistantTurnContext,
        config: DirectApiConfig,
        onProgress: suspend (CreationAgentProgressEvent) -> Unit = {},
    ): MobileCreationConversationResult {
        require(message.isNotBlank()) { "请输入你想告诉 AI 的内容" }
        var working = source
        var createdProjectId: String? = null
        val toolResults = mutableListOf<JsonElement>()
        val turnProtocolMessages = mutableListOf<JsonElement>()
        val promptMetrics = mutableListOf<JsonElement>()
        val userMessage = chatMessage("user", message)
        var currentConversation = conversation
        val initialRuntime = conversation.toolRuntimeState(turnContext.turnId)
        val deliveredTransactions = initialRuntime?.activeTransactions.orEmpty().toMutableList()
        val executionLedger = initialRuntime?.executionLedger.orEmpty().toMutableList()

        var finalReply = ""
        var iteration = 0
        var consecutiveCapacityRejections = 0
        var activeCategories = emptyList<String>()
        var categorySelected = false
        var successfulWriteCount = 0
        var failedWriteCount = 0
        var replyStatus = "model"
        val replyDiagnostics = mutableListOf<JsonElement>()
        suspend fun rejectReply(reason: String) {
            val diagnostic = buildJsonObject {
                put("reason", reason)
                put("attempt", replyDiagnostics.size + 1)
            }
            replyDiagnostics += diagnostic
            onProgress(CreationAgentProgressEvent(
                type = "reply_rejected",
                message = "模型总结未通过校验，已保留真实执行结果",
                status = "warning",
                data = diagnostic,
            ))
        }
        suspend fun emitReplyDelta(delta: String) {
            if (delta.isEmpty()) return
            onProgress(CreationAgentProgressEvent(
                type = "reply_delta",
                message = "",
                status = "running",
                data = buildJsonObject { put("delta", delta) },
            ))
        }
        while (finalReply.isBlank()) {
            if (successfulWriteCount >= contract.maxSuccessfulWritesPerTurn ||
                failedWriteCount >= contract.maxFailedWritesPerTurn
            ) break
            onProgress(CreationAgentProgressEvent(
                type = "model_step_started",
                message = if (iteration == 0) "正在判断需要哪些立项能力…" else "正在根据真实工具结果继续处理…",
                data = buildJsonObject { put("iteration", iteration + 1) },
            ))
            val scopedTools = contract.toolSchemas(activeCategories)
            val requestToolChoice = if (categorySelected) "auto" else "required"
            val hasBusinessReads = toolResults.any {
                val result = it as? JsonObject ?: return@any false
                result.string("status") == "ok" && result.string("tool") != contract.categoryController &&
                    result.string("tool") !in contract.writeToolNames
            }
            val stepSystemPrompt = contract.systemPrompt(source.string("id")) +
                if (hasBusinessReads && successfulWriteCount == 0) {
                    "\n\n[SERVER_RUNTIME_INSTRUCTION]\nauthority: server_current_turn\n" +
                        contract.readOnlyCompletionInstruction + "\n[/SERVER_RUNTIME_INSTRUCTION]"
                } else ""
            val prepared = conversationContextRuntime.prepare(
                resultJsonBytes = { tool, _ -> creationDeclaredResultBytes(tool) },
                storageId = storageId,
                currentUserPrompt = message,
                config = config,
                conversation = currentConversation,
                turnContext = turnContext,
                systemPrompt = stepSystemPrompt,
                scopedTools = scopedTools,
                taskType = DirectApiConfig.TASK_PLANNING,
                maxOutputTokens = CREATION_OUTPUT_TOKENS,
                toolChoice = requestToolChoice,
                temperature = 0.25,
                currentTurnLedger = executionLedger,
                pendingTransactions = deliveredTransactions,
                onStatus = { status ->
                    onProgress(
                        CreationAgentProgressEvent(
                            type = "conversation_context",
                            message = status.detail,
                            status = status.status,
                            data = mobileConversationContextStatePayload(
                                status = status.status,
                                detail = status.detail,
                                conversation = status.conversation,
                                budget = status.budget,
                                checkpointId = status.checkpointId,
                                recentExactTurnCount = status.recentExactTurnCount,
                                provider = "android_direct_api",
                                model = config.model,
                            ),
                        ),
                    )
                },
            )
            currentConversation = prepared.conversation
            MobileToolProtocolValidator.validate(
                messages = prepared.rendered.messages,
                supportsNativeToolCalling = true,
                toolsOffered = scopedTools.isNotEmpty(),
                currentUserMessageId = prepared.rendered.currentUserMessageId,
                checkpointMessageId = prepared.rendered.checkpointMessageId,
            )
            val turn = directApi.streamAgentTurn(
                config = config,
                messages = providerMessages(prepared.rendered.messages),
                tools = scopedTools,
                toolChoice = requestToolChoice,
                maxOutputTokens = 6_000,
                temperature = 0.25,
            )
            if (deliveredTransactions.isNotEmpty()) {
                val consumed = conversationStore.markDeliveredToolTransactionsConsumed(
                    projectId = storageId,
                    turnContext = turnContext,
                )
                deliveredTransactions.clear()
                deliveredTransactions += consumed.activeTransactions
                executionLedger.clear()
                executionLedger += consumed.executionLedger
                currentConversation = conversationStore.snapshot(storageId, turnContext.conversationId)
                    ?: error("立项工具事务消费状态保存后会话丢失")
            }
            promptMetrics += promptMetric(
                iteration = iteration + 1,
                phase = "standalone",
                activeCategories = activeCategories,
                messages = prepared.rendered.messages,
                tools = scopedTools,
                promptTokens = turn.promptTokens,
            )
            val calls = turn.toolCalls
            if (calls.isEmpty()) {
                check(categorySelected) {
                    "模型没有调用本步骤唯一开放的 set_tool_categories，本轮未接受文字回复"
                }
                finalReply = turn.content.trim()
                if (finalReply.isNotBlank()) {
                    contract.replyError(finalReply)?.let { reason ->
                        rejectReply(reason)
                        finalReply = ""
                    }
                }
                break
            }

            val offeredToolNames = scopedTools.mapTo(linkedSetOf()) { rawSchema ->
                val function = (rawSchema as? JsonObject)?.get("function") as? JsonObject
                    ?: throw MobileConversationContextException(
                        MobileConversationContextErrorCode.PROTOCOL_INVALID,
                        "立项工具 Schema 缺少 function 对象",
                    )
                function.string("name").ifBlank {
                    throw MobileConversationContextException(
                        MobileConversationContextErrorCode.PROTOCOL_INVALID,
                        "立项工具 Schema 缺少 function.name",
                    )
                }
            }
            val ids = calls.map(DirectAgentToolCall::id)
            if (ids.any(String::isBlank) || ids.distinct().size != ids.size) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "立项原生工具调用 ID 为空或重复，整批未执行",
                )
            }
            val undeclared = calls.map(DirectAgentToolCall::name).filterNot(offeredToolNames::contains)
            if (undeclared.isNotEmpty()) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "模型调用了本步骤未声明的立项工具，整批未执行：${undeclared.joinToString()}",
                )
            }
            val categoryCalls = calls.filter { it.name == contract.categoryController }
            if (categoryCalls.isNotEmpty() && calls.size != 1) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "set_tool_categories 必须是模型步骤中唯一的原生调用，整批未执行",
                )
            }
            if (categoryCalls.isEmpty() && !categorySelected) {
                throw MobileConversationContextException(
                    MobileConversationContextErrorCode.PROTOCOL_INVALID,
                    "模型没有调用本步骤唯一开放的 set_tool_categories，整批未执行",
                )
            }
            var batchAdmission = MobileNativeToolBudgetContract.admitExactAssistantTransaction(
                assistantPayload = turn.assistantMessage,
                orderedToolNames = calls.map(DirectAgentToolCall::name),
                requestBudget = prepared.budget,
                resultJsonBytes = { tool, _ -> creationDeclaredResultBytes(tool) },
            )
            val stagedReadResults = mutableMapOf<String, Pair<ToolExecution, JsonObject>>()
            if (!batchAdmission.accepted &&
                batchAdmission.reason == MobileNativeToolBudgetContract.TOOL_RESULT_BATCH_OVER_CAPACITY &&
                calls.isNotEmpty() && calls.all { it.name in contract.capacityPreflightReadToolNames }
            ) {
                // Only audited, side-effect-free local reads may run before
                // result admission. Writes still use declared pre-handler bounds.
                calls.forEach { call ->
                    val execution = try {
                        execute(working, call.name, call.arguments, config)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        ToolExecution(working, result(call.name, "error", "工具读取失败"))
                    }
                    check(!execution.wrote && execution.session == working) {
                        "容量预检读取不得修改立项资料"
                    }
                    stagedReadResults[call.id] = execution to
                        creationModelVisibleResult(call.name, execution.result)
                }
                batchAdmission = MobileNativeToolBudgetContract.admitExactAssistantTransaction(
                    assistantPayload = turn.assistantMessage,
                    orderedToolNames = calls.map(DirectAgentToolCall::name),
                    requestBudget = prepared.budget,
                    resultJsonBytes = { tool, _ -> creationDeclaredResultBytes(tool) },
                    resultContents = calls.map { call ->
                        mobileCanonicalJson(stagedReadResults.getValue(call.id).second)
                    },
                )
            }
            if (!batchAdmission.accepted) {
                consecutiveCapacityRejections += 1
                batchAdmission = batchAdmission.copy(recoveryFits = batchAdmission.recoveryFits &&
                    consecutiveCapacityRejections < MobileNativeToolBudgetContract.MAX_CONSECUTIVE_CAPACITY_REJECTIONS)
                val rejectedResults = calls.map { call ->
                    batchAdmission.errorResult(call.name)
                }
                persistRejectedMobileNativeToolBatch(
                    conversationStore = conversationStore,
                    projectId = storageId,
                    turnContext = turnContext,
                    transaction = deliveredTransaction(turn, calls, rejectedResults),
                    recoveryFits = batchAdmission.recoveryFits,
                    terminalError = MobileConversationContextException(
                        MobileConversationContextErrorCode.TOOL_TRANSACTION_OVER_CAPACITY,
                        "工具批次无法在当前模型预算内恢复，已保留进度；本批次未执行。" +
                            "立项原生 assistant 工具事务超过容量协议；整批业务处理器未执行。",
                    ),
                ) { runtime ->
                    deliveredTransactions.clear()
                    deliveredTransactions += runtime.activeTransactions
                    toolResults += rejectedResults
                    rejectedResults.forEach { result ->
                        onProgress(
                            CreationAgentProgressEvent(
                                type = "tool_completed",
                                message = result.string("detail"),
                                status = "denied",
                                data = result["data"] as? JsonObject ?: JsonObject(emptyMap()),
                            ),
                        )
                    }
                }
                iteration += 1
                continue
            }

            consecutiveCapacityRejections = 0
            val categoryCall = categoryCalls.firstOrNull()
            if (categoryCall != null) {
                val assistantToolMessage = assistantToolMessage(turn.content, listOf(categoryCall))
                turnProtocolMessages += assistantToolMessage
                val selected = runCatching {
                    contract.normalizeCategories(
                        (categoryCall.arguments["enabled_categories"] as? JsonArray)
                            .orEmpty()
                            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
                    )
                }
                val categoryResult = selected.fold(
                    onSuccess = contract::categoryResult,
                    onFailure = { result(categoryCall.name, "error", it.message ?: "工具类别参数无效") },
                )
                toolResults += categoryResult
                val toolMessage = buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", categoryCall.id)
                    put("content", mobileCanonicalJson(categoryResult))
                }
                turnProtocolMessages += toolMessage
                val runtime = conversationStore.recordDeliveredToolTransaction(
                    projectId = storageId,
                    turnContext = turnContext,
                    transaction = deliveredTransaction(
                        turn = turn,
                        calls = listOf(categoryCall),
                        results = listOf(categoryResult),
                    ),
                )
                deliveredTransactions.clear()
                deliveredTransactions += runtime.activeTransactions
                selected.getOrNull()?.let { categories ->
                    activeCategories = categories
                    categorySelected = true
                    onProgress(CreationAgentProgressEvent(
                        type = "tool_categories_changed",
                        message = categoryResult.string("detail"),
                        status = "ok",
                        data = categoryResult["data"] as? JsonObject ?: JsonObject(emptyMap()),
                    ))
                }
                iteration += 1
                continue
            }

            val assistantToolMessage = assistantToolMessage(turn.content, calls)
            turnProtocolMessages += assistantToolMessage

            val availableTools = contract.availableToolNames(activeCategories)
            val modelVisibleResults = mutableListOf<JsonObject>()
            for (call in calls) {
                var attemptedWrite = false
                com.siming.mobile.data.observability.MobileTrace.span("tool", call.name) {
                com.siming.mobile.data.observability.MobileTrace.payload("tool_arguments", buildJsonObject {
                    put("tool_call_id", call.id); put("arguments", call.arguments)
                })
                val execution = try {
                    when {
                        call.name !in availableTools -> ToolExecution(
                            working,
                            result(call.name, "skipped", "该工具当前未向立项会话开放"),
                        )
                        call.name in contract.writeToolNames &&
                            successfulWriteCount >= contract.maxSuccessfulWritesPerTurn -> ToolExecution(
                            working,
                            result(
                                call.name,
                                "denied",
                                "本条用户消息已经成功写入一次；本轮不得继续确认、生成或修改其他资料。请结束回复并等待作者的下一条消息。",
                                buildJsonObject { put("reason", "successful_write_limit") },
                            ),
                        )
                        call.name in contract.writeToolNames &&
                            failedWriteCount >= contract.maxFailedWritesPerTurn -> ToolExecution(
                            working,
                            result(
                                call.name,
                                "denied",
                                "本轮写入失败已达上限；为避免自动重试循环，本轮写工具已经关闭。",
                                buildJsonObject { put("reason", "failed_write_limit") },
                            ),
                        )
                        else -> {
                            attemptedWrite = call.name in contract.writeToolNames
                            onProgress(CreationAgentProgressEvent(
                                type = "tool_started",
                                message = "正在${toolLabel(call.name)}…",
                                data = buildJsonObject { put("tool", call.name) },
                            ))
                            stagedReadResults[call.id]?.first
                                ?: execute(working, call.name, call.arguments, config)
                        }
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    ToolExecution(
                        working,
                        result(call.name, "error", error.message ?: "工具执行失败"),
                    )
                }
                com.siming.mobile.data.observability.MobileTrace.toolOutcome(execution.result)
                com.siming.mobile.data.observability.MobileTrace.payload("tool_receipt", execution.result)
                working = execution.session
                execution.createdProjectId?.let { createdProjectId = it }
                toolResults += execution.result
                if (execution.wrote) persistSession(working)
                if (attemptedWrite) {
                    if (execution.result.string("status") in setOf("ok", "running")) {
                        successfulWriteCount += 1
                    } else {
                        failedWriteCount += 1
                    }
                }
                onProgress(CreationAgentProgressEvent(
                    type = "tool_completed",
                    message = execution.result.string("detail").ifBlank { "${toolLabel(call.name)}完成" },
                    status = execution.result.string("status").ifBlank { "ok" },
                    data = buildJsonObject {
                        put("tool", call.name)
                        put("label", toolLabel(call.name))
                    },
                ))
                if (attemptedWrite && failedWriteCount == contract.maxFailedWritesPerTurn) {
                    onProgress(CreationAgentProgressEvent(
                        type = "tool_completed",
                        message = "写入连续失败已达上限，本轮已停止自动重试",
                        status = "denied",
                        data = buildJsonObject {
                            put("tool", call.name)
                            put("turn_boundary", "failed_write_limit")
                            put("failed_writes", failedWriteCount)
                        },
                    ))
                }
                val modelVisibleResult = stagedReadResults[call.id]?.second
                    ?: creationModelVisibleResult(call.name, execution.result)
                com.siming.mobile.data.observability.MobileTrace.toolOutcome(modelVisibleResult)
                com.siming.mobile.data.observability.MobileTrace.payload("model_visible_tool_result", modelVisibleResult)
                val toolMessage = buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", call.id)
                    put("content", mobileCanonicalJson(modelVisibleResult))
                }
                turnProtocolMessages += toolMessage
                modelVisibleResults += modelVisibleResult
                }
            }
            val runtime = conversationStore.recordDeliveredToolTransaction(
                projectId = storageId,
                turnContext = turnContext,
                transaction = deliveredTransaction(turn, calls, modelVisibleResults),
            )
            deliveredTransactions.clear()
            deliveredTransactions += runtime.activeTransactions
            iteration += 1
        }

        if (failedWriteCount > 0 && successfulWriteCount == 0 && createdProjectId == null) {
            replyStatus = "receipt_only"
            finalReply = truthfulNoWrite(toolResults)
        }
        if (finalReply.isBlank() && toolResults.isNotEmpty() && createdProjectId == null) {
            for (attempt in replyDiagnostics.size until contract.maxReplyAttempts) {
                onProgress(CreationAgentProgressEvent(
                    type = "model_step_started",
                    message = "正在根据真实执行结果整理回复…",
                    data = buildJsonObject { put("phase", "summary"); put("attempt", attempt + 1) },
                ))
                val summarySystem = contract.systemPrompt(source.string("id")) +
                    "\n\n[SERVER_RUNTIME_INSTRUCTION]\n" +
                    contract.replyInstruction +
                    (if (replyDiagnostics.isNotEmpty()) contract.replyRepairInstruction else "") + "\n" +
                    "[/SERVER_RUNTIME_INSTRUCTION]"
                val summaryExtraBody = if (config.isDeepSeekProvider()) buildJsonObject {
                    put("thinking", buildJsonObject { put("type", "disabled") })
                } else null
                val prepared = conversationContextRuntime.prepare(
                    resultJsonBytes = { tool, _ -> creationDeclaredResultBytes(tool) },
                    storageId = storageId,
                    currentUserPrompt = message,
                    config = config,
                    conversation = currentConversation,
                    turnContext = turnContext,
                    systemPrompt = summarySystem,
                    scopedTools = JsonArray(emptyList()),
                    taskType = DirectApiConfig.TASK_PLANNING,
                    maxOutputTokens = 1_200,
                    temperature = 0.2,
                    extraBody = summaryExtraBody,
                    currentTurnLedger = executionLedger,
                    pendingTransactions = deliveredTransactions,
                )
                currentConversation = prepared.conversation
                MobileToolProtocolValidator.validate(
                    messages = prepared.rendered.messages,
                    supportsNativeToolCalling = true,
                    toolsOffered = false,
                    currentUserMessageId = prepared.rendered.currentUserMessageId,
                    checkpointMessageId = prepared.rendered.checkpointMessageId,
                )
                val summaryTurn = try {
                    directApi.streamAgentTurn(
                        config = config,
                        messages = providerMessages(prepared.rendered.messages),
                        tools = JsonArray(emptyList()),
                        maxOutputTokens = 1_200,
                        temperature = 0.2,
                        extraBody = summaryExtraBody,
                    )
                } catch (error: CancellationException) {
                    throw error
                } catch (error: MobileConversationContextException) {
                    throw error
                } catch (_: Exception) {
                    rejectReply("summary_request_failed")
                    break
                }
                if (deliveredTransactions.isNotEmpty()) {
                    val consumed = conversationStore.markDeliveredToolTransactionsConsumed(storageId, turnContext)
                    deliveredTransactions.clear()
                    deliveredTransactions += consumed.activeTransactions
                    executionLedger.clear()
                    executionLedger += consumed.executionLedger
                    currentConversation = conversationStore.snapshot(storageId, turnContext.conversationId)
                        ?: error("立项总结消费工具事务后会话丢失")
                }
                promptMetrics += promptMetric(
                    iteration = promptMetrics.size + 1,
                    phase = "summary",
                    activeCategories = activeCategories,
                    messages = prepared.rendered.messages,
                    tools = JsonArray(emptyList()),
                    promptTokens = summaryTurn.promptTokens,
                )
                val reason = contract.replyError(summaryTurn.content, summaryTurn.toolCalls.isNotEmpty())
                if (reason == null) {
                    finalReply = summaryTurn.content.trim()
                    break
                }
                rejectReply(reason)
            }
        }
        if (createdProjectId != null) {
            replyStatus = "project_created"
            finalReply = "正式作品已创建并进入作品库。请点击下方按钮进入正式作品；进入后项目助手会自动展开，后续正文与项目资料都在那里继续。"
        }
        if (finalReply.isBlank()) {
            replyStatus = "receipt_only"
            val writes = toolResults.mapNotNull { it as? JsonObject }
                .filter { it.string("tool") in contract.writeToolNames && it.string("status") in setOf("ok", "running") }
            finalReply = if (writes.isNotEmpty()) {
                val receipt = if (writes.any { it.string("status") == "running" }) {
                    "本轮任务已启动，请在任务状态中查看结果。"
                } else "本轮修改已保存，请在立项资料中查看结果。"
                val details = writes.take(3).map { it.string("detail") }
                    .filter { contract.replyError(it) == null }
                receipt + if (details.isNotEmpty()) "回执：${details.joinToString("；")}。" else ""
            } else truthfulNoWrite(toolResults)
            if (replyDiagnostics.isNotEmpty()) finalReply += contract.replyFailureNotice
        }
        if (replyStatus == "model" && successfulWriteCount == 0 && toolResults.any {
            val result = it as? JsonObject ?: return@any false
            result.string("status") == "ok" && result.string("tool") != contract.categoryController &&
                result.string("tool") !in contract.writeToolNames
        }) {
            replyStatus = "read_only"
            finalReply = contract.readOnlyNotice + "\n\n" + finalReply
        }
        // A terminal server receipt can close the turn without another model request.
        if (deliveredTransactions.any { it.state == MobileToolTransactionState.DELIVERED }) {
            conversationStore.markDeliveredToolTransactionsConsumed(storageId, turnContext)
        }
        // Buffer provider content until its native-call and plain-reply checks pass.
        finalReply.chunked(240).forEach { emitReplyDelta(it) }
        val modelMessages = buildJsonArray {
            add(userMessage)
            turnProtocolMessages.forEach(::add)
            add(chatMessage("assistant", finalReply.take(80_000)))
        }
        return MobileCreationConversationResult(
            working,
            finalReply,
            JsonArray(toolResults),
            modelMessages,
            replayable = true,
            status = "completed",
            createdProjectId = createdProjectId,
            promptMetrics = JsonArray(promptMetrics),
            replyStatus = replyStatus,
            replyDiagnostics = JsonArray(replyDiagnostics),
        )
    }

    private fun deliveredTransaction(
        turn: DirectAgentTurn,
        calls: List<DirectAgentToolCall>,
        results: List<JsonObject>,
    ): MobileToolTransaction {
        require(calls.size == results.size) { "立项工具调用与结果必须原子配对" }
        return MobileToolTransaction(
            transactionId = "creation-tool-transaction-${UUID.randomUUID()}",
            assistantMessageId = "creation-tool-assistant-${UUID.randomUUID()}",
            assistantContent = turn.assistantMessage.string("content"),
            assistantReasoningContent = turn.assistantMessage.string("reasoning_content"),
            assistantProviderState = (turn.assistantMessage["provider_state"] as? JsonArray)
                .orEmpty()
                .mapNotNull { it as? JsonObject },
            state = MobileToolTransactionState.PENDING,
            calls = calls.map { call ->
                val exactCall = (turn.assistantMessage["tool_calls"] as? JsonArray)
                    .orEmpty()
                    .mapNotNull { it as? JsonObject }
                    .firstOrNull { it.string("id").ifBlank { it.string("call_id") } == call.id }
                    ?: throw MobileConversationContextException(
                        MobileConversationContextErrorCode.PROTOCOL_INVALID,
                        "立项原生 assistant payload 缺少工具调用 ${call.id}",
                    )
                val function = exactCall["function"] as? JsonObject
                    ?: throw MobileConversationContextException(
                        MobileConversationContextErrorCode.PROTOCOL_INVALID,
                        "立项原生 assistant payload 缺少 function 对象",
                    )
                MobileToolCallRecord(
                    id = call.id,
                    name = call.name,
                    argumentsJson = function.string("arguments"),
                )
            },
            results = emptyList(),
        ).let { transaction ->
            calls.zip(results).fold(transaction) { current, (call, result) ->
                current.addResult(
                    MobileToolResultRecord(
                        toolCallId = call.id,
                        content = mobileCanonicalJson(result),
                    ),
                )
            }.markDelivered()
        }
    }

    private fun creationDeclaredResultBytes(tool: String): Int = when {
        tool == contract.categoryController -> CREATION_STATUS_RESULT_BYTES
        tool in contract.writeToolNames -> contract.writeResultMaxBytesFor(tool)
        tool in CREATION_LARGE_READ_TOOLS -> CREATION_LARGE_READ_RESULT_BYTES
        else -> CREATION_STANDARD_RESULT_BYTES
    }

    private fun creationModelVisibleResult(tool: String, raw: JsonObject): JsonObject {
        val projected = if (tool in contract.writeToolNames) {
            buildJsonObject {
                put("tool", raw.string("tool").ifBlank { tool })
                put("status", raw.string("status"))
                put("detail", raw.string("detail"))
                val data = raw["data"] as? JsonObject
                put("data", contract.projectWriteResultData(data))
            }
        } else {
            raw
        }
        val maxBytes = creationDeclaredResultBytes(tool)
        if (mobileCanonicalJson(projected).toByteArray(Charsets.UTF_8).size <= maxBytes) return projected
        return result(
            tool = tool,
            status = "error",
            detail = "立项工具结果超过声明的模型可见 JSON 上限；结果未进入下一模型步骤，请缩小读取范围。",
            data = buildJsonObject {
                put("error_code", MobileConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY)
                put("retryable", true)
                put("declared_max_json_bytes", maxBytes)
            },
        )
    }

    private fun promptMetric(
        iteration: Int,
        phase: String,
        activeCategories: List<String>,
        messages: List<JsonObject>,
        tools: JsonArray,
        promptTokens: Int?,
    ): JsonObject {
        val systemPrompt = messages.firstOrNull { it.string("role") == "system" }
            ?.string("content")
            .orEmpty()
        val requestProjection = buildJsonObject {
            put("messages", JsonArray(messages))
            put("tools", tools)
        }.toString()
        return buildJsonObject {
            put("iteration", iteration)
            put("phase", phase)
            put("enabled_categories", JsonArray(activeCategories.map(::JsonPrimitive)))
            put("tool_count", tools.size)
            put("tool_schema_estimated_tokens", estimateTokens(tools.toString()))
            put("system_prompt_estimated_tokens", estimateTokens(systemPrompt))
            put("request_estimated_tokens", estimateTokens(requestProjection))
            if (promptTokens == null) put("prompt_tokens", JsonNull)
            else put("prompt_tokens", promptTokens.coerceAtLeast(0))
            put("usage_reported", promptTokens != null)
        }
    }

    private fun estimateTokens(text: String): Int {
        if (text.isEmpty()) return 0
        val cjkCount = text.count { char ->
            char in '\u4E00'..'\u9FFF' || char in '\u3400'..'\u4DBF'
        }
        return cjkCount + maxOf(1, (text.length - cjkCount) / 4)
    }

    private fun truthfulNoWrite(toolResults: List<JsonElement>): String {
        val results = toolResults.mapNotNull { it as? JsonObject }
        val failures = results
            .filter { it.string("status") !in setOf("ok", "running") }
            .map { it.string("detail").ifBlank { "工具未完成" } }
        if (failures.isNotEmpty()) {
            val detail = failures.last().takeIf { contract.replyError(it) == null } ?: "工具未完成"
            return "本轮没有保存任何修改：$detail。请调整要求后重试。"
        }
        val readSucceeded = results.any {
            it.string("status") == "ok" && it.string("tool") !in contract.writeToolNames
        }
        if (readSucceeded) return "本轮只完成了立项工具读取，没有保存任何修改。请明确要写入的对象和内容后重试。"
        if (results.isNotEmpty()) return "本轮执行了立项工具，但没有产生可确认的写入。请调整要求后重试。"
        return "本轮未执行任何立项工具，因此没有读取或修改立项数据。请重试。"
    }

    private suspend fun execute(
        source: JsonObject,
        tool: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): ToolExecution {
        if (tool !in contract.toolNames) {
            return ToolExecution(source, result(tool, "skipped", "该工具不属于当前立项 Agent 契约"))
        }
        val expected = args.intOrNull("expected_revision")
        if (tool in contract.revisionToolNames && expected != null && expected != source.int("revision")) {
            return ToolExecution(
                source,
                result(tool, "error", "Novel creation session revision conflict", buildJsonObject {
                    put("failure_class", "revision_conflict")
                    put("current_revision", source.int("revision"))
                }),
            )
        }
        return when (tool) {
            "get_creation_session", "get_creation_snapshot" -> ToolExecution(
                source,
                result(tool, "ok", "已读取当前立项快照", snapshot(source)),
            )
            "get_creation_artifact" -> {
                val artifact = args.string("artifact")
                if (artifact !in contract.stageOrder) {
                    ToolExecution(source, result(tool, "error", "阶段 ID 无效；请使用立项快照中的 artifact 原值", buildJsonObject {
                        put("reason", "creation_unknown_artifact")
                    }))
                } else {
                    ToolExecution(source, result(tool, "ok", "已读取${stageLabel(artifact)}", artifactSnapshot(source, artifact)))
                }
            }
            "list_creation_artifacts" -> ToolExecution(
                source,
                result(tool, "ok", "已读取全部立项对象", buildJsonObject {
                    put("revision", source.int("revision"))
                    put("artifacts", artifactSummaries(source))
                }),
            )
            "get_creation_dependencies", "get_creation_dependency_graph" -> ToolExecution(
                source,
                result(tool, "ok", "已读取立项依赖关系", dependencySnapshot(args.string("artifact"))),
            )
            "validate_creation_consistency", "validate_creation_session" -> ToolExecution(
                source,
                result(tool, "ok", "已检查当前立项完整性", localValidation(source)),
            )
            "patch_creation_session" -> patchSession(source, args)
            "patch_creation_artifact" -> patchArtifact(source, args)
            "lock_creation_fields" -> setLocks(source, args, true)
            "unlock_creation_fields" -> setLocks(source, args, false)
            "list_creation_entities" -> ToolExecution(
                source,
                result(tool, "ok", "已读取立项实体", buildJsonObject {
                    put("revision", source.int("revision"))
                    put("entities", JsonArray(listEntities(source, args.string("artifact"), args.string("entity_type"))))
                }),
            )
            "get_creation_entity" -> {
                val entity = resolveEntity(source, args.string("entity_id"))
                if (entity == null) ToolExecution(source, result(tool, "skipped", "未找到目标立项实体"))
                else ToolExecution(source, result(tool, "ok", "已读取目标立项实体", entity.descriptor))
            }
            "patch_creation_entity" -> patchEntity(source, args)
            "delete_creation_entity" -> deleteEntity(source, args)
            "confirm_creation_artifact" -> {
                val artifact = args.string("artifact")
                if ("data" in args) {
                    ToolExecution(
                        source,
                        result(tool, "error", "确认工具不能同时修改内容；请先保存修改，再由作者确认当前版本"),
                    )
                } else {
                    val updated = stageAgent.confirmStage(source, artifact)
                    ToolExecution(updated, result(tool, "ok", "${stageLabel(artifact)}已确认", artifactSnapshot(updated, artifact)), wrote = true)
                }
            }
            "generate_creation_artifact", "refine_creation_artifact", "regenerate_creation_artifact" ->
                generateArtifact(source, tool, args, config)
            "finalize_creation_session" -> {
                val validation = localValidation(source)
                if ((validation["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() != true) {
                    ToolExecution(source, result(tool, "error", "当前立项数据还没有达到正式建档条件", validation))
                } else {
                    val (updated, projectId) = finalizeSession(source)
                    ToolExecution(
                        updated,
                        result(tool, "ok", "正式作品已创建", buildJsonObject { put("project_id", projectId) }),
                        wrote = true,
                        createdProjectId = projectId,
                    )
                }
            }
            else -> ToolExecution(source, result(tool, "skipped", "手机独立模式暂未实现该立项工具"))
        }
    }

    private fun patchSession(source: JsonObject, args: JsonObject): ToolExecution {
        val changes = args["changes"] as? JsonObject ?: JsonObject(emptyMap())
        if (changes.isEmpty()) return ToolExecution(source, result("patch_creation_session", "skipped", "没有可写入的会话变化"))
        val sessionFields = setOf("form", "creation_mode", "author_brief", "author_outline", "locked_requirements", "selected_concept_id", "quick_mode")
        val formFields = setOf("brief", "preset_id", "theme_id", "genre", "target_audience", "platform", "target_words", "target_chapters", "world_tone", "story_structure", "pacing", "writing_style", "special_requirements", "avoid", "author_overrides")
        val unknownSessionFields = changes.keys - sessionFields
        val formPatch = changes["form"] as? JsonObject
        val unknownFormFields = formPatch?.keys?.minus(formFields).orEmpty()
        if (unknownSessionFields.isNotEmpty() || unknownFormFields.isNotEmpty() || ("form" in changes && formPatch.isNullOrEmpty())) {
            return ToolExecution(source, result("patch_creation_session", "error", "立项会话字段无效；创作约束须放在 changes.form 内"))
        }
        val draft = source.objectValue("draft").toMutableMap()
        val form = (draft["form"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        changes.forEach { (key, value) ->
            when (key) {
                "creation_mode", "author_brief", "author_outline", "locked_requirements", "selected_concept_id", "quick_mode" -> draft[key] = value
                "form" -> (value as? JsonObject)?.forEach { (formKey, formValue) -> form[formKey] = formValue }
            }
        }
        val formChanged = JsonObject(form) != source.objectValue("draft").objectValue("form")
        if (!formChanged && changes.keys.all { key -> key == "form" || draft[key] == source.objectValue("draft")[key] }) {
            return ToolExecution(source, result("patch_creation_session", "skipped", "立项会话资料没有变化"))
        }
        draft["form"] = JsonObject(form)
        val stages = (draft["stages"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        if (formChanged) {
            val constraints = (stages["constraints"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
            constraints["status"] = JsonPrimitive("generated")
            constraints["data"] = JsonObject(form)
            constraints["source"] = JsonPrimitive("assistant")
            constraints["updated_at"] = JsonPrimitive(Instant.now().toString())
            stages["constraints"] = JsonObject(constraints)
            contract.impactDependencies["constraints"].orEmpty().forEach { downstream ->
                val state = stages[downstream] as? JsonObject ?: return@forEach
                if (state.string("status") in setOf("generated", "confirmed")) {
                    stages[downstream] = JsonObject(state.toMutableMap().apply {
                        put("status", JsonPrimitive("stale"))
                        put("stale_reason", JsonPrimitive("创作约束已修改"))
                        put("stale_source", JsonPrimitive("constraints"))
                    })
                }
            }
        }
        draft["stages"] = JsonObject(stages)
        val updated = bump(source, draft) { root ->
            if (formChanged) {
                root["user_brief"] = form["brief"] ?: JsonPrimitive("")
                if ((draft["concepts"] as? JsonArray).isNullOrEmpty()) {
                    root["display_title"] = (form["brief"] as? JsonPrimitive)
                        ?.contentOrNull?.takeIf { it.isNotBlank() }?.let { JsonPrimitive(it) }
                        ?: JsonPrimitive("未命名作品")
                }
                listOf("genre", "target_audience", "platform").forEach { field ->
                    root[field] = form[field] ?: JsonPrimitive("")
                }
            }
        }
        return ToolExecution(
            updated,
            result("patch_creation_session", "ok", "立项会话已增量更新", snapshot(updated)),
            wrote = true,
        )
    }

    private fun patchArtifact(source: JsonObject, args: JsonObject): ToolExecution {
        val artifact = args.string("artifact")
        val current = source.stageData(artifact)
        if (current.isEmpty()) {
            return ToolExecution(source, result("patch_creation_artifact", "error", "${stageLabel(artifact)}尚无可局部修改的数据；请先生成该对象"))
        }
        val changes = (args["changes"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        if (changes.isEmpty()) return ToolExecution(source, result("patch_creation_artifact", "skipped", "没有可应用的局部修改"))
        val patched = try {
            applyChanges(current, changes)
        } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_artifact", "error", error.message ?: "局部修改无效"))
        }
        val updated = try {
            stageAgent.replaceArtifact(source, artifact, patched, "assistant")
        } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_artifact", "error", error.message ?: "修改后数据未通过校验"))
        }
        return ToolExecution(updated, result("patch_creation_artifact", "ok", "${stageLabel(artifact)}已局部更新", artifactSnapshot(updated, artifact)), wrote = true)
    }

    private fun patchEntity(source: JsonObject, args: JsonObject): ToolExecution {
        val entity = resolveEntity(source, args.string("entity_id"))
            ?: return ToolExecution(source, result("patch_creation_entity", "skipped", "未找到目标立项实体"))
        val changes = (args["changes"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val patched = try { applyChanges(entity.data, changes) } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_entity", "error", error.message ?: "实体修改无效"))
        }
        val artifactData = source.stageData(entity.artifact).toMutableMap()
        val rows = (artifactData[entity.field] as? JsonArray).orEmpty().toMutableList()
        rows[entity.index] = patched
        artifactData[entity.field] = JsonArray(rows)
        val updated = try { stageAgent.replaceArtifact(source, entity.artifact, JsonObject(artifactData), "assistant") } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_entity", "error", error.message ?: "实体修改后未通过校验"))
        }
        val saved = updated.stageData(entity.artifact).getValue(entity.field).jsonArray[entity.index].jsonObject
        val next = entityDescriptor(updated, entity.artifact, entity.field, entity.type, entity.index, saved)
        return ToolExecution(updated, result("patch_creation_entity", "ok", "立项实体已更新", next), wrote = true)
    }

    private fun deleteEntity(source: JsonObject, args: JsonObject): ToolExecution {
        val entity = resolveEntity(source, args.string("entity_id"))
            ?: return ToolExecution(source, result("delete_creation_entity", "skipped", "未找到目标立项实体"))
        val artifactData = source.stageData(entity.artifact).toMutableMap()
        val rows = (artifactData[entity.field] as? JsonArray).orEmpty().toMutableList()
        rows.removeAt(entity.index)
        artifactData[entity.field] = JsonArray(rows)
        val updated = try { stageAgent.replaceArtifact(source, entity.artifact, JsonObject(artifactData), "assistant") } catch (error: Exception) {
            return ToolExecution(source, result("delete_creation_entity", "error", error.message ?: "删除后数据未通过校验"))
        }
        return ToolExecution(updated, result("delete_creation_entity", "ok", "立项实体已删除"), wrote = true)
    }

    private suspend fun generateArtifact(
        source: JsonObject,
        tool: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): ToolExecution {
        val artifact = args.string("artifact")
        if (artifact == "all") {
            return ToolExecution(source, result(tool, "error", "对话式立项请按实际缺口逐个生成对象，不使用一次性 all 阶段"))
        }
        if (artifact !in contract.stageOrder || artifact == "constraints") {
            return ToolExecution(source, result(tool, "error", "未知或不可生成的立项对象：$artifact"))
        }
        val instruction = args.string("instruction")
        val entityId = args.string("entity_id")
        val entityType = args.string("entity_type")
        if (entityId.isNotBlank() && entityType.isNotBlank()) {
            return ToolExecution(source, result(tool, "error", "entity_id 和 entity_type 不能同时指定"))
        }
        val target = if (entityId.isNotBlank()) resolveEntity(source, entityId) else null
        if (entityId.isNotBlank() && (target == null || target.artifact != artifact)) {
            val reason = if (target == null) "creation_target_entity_unavailable" else "creation_target_artifact_mismatch"
            val path = if (target == null) "$.entity_id" else "$.artifact"
            return ToolExecution(source, contract.referenceError(tool, reason, path))
        }
        val mapping = if (entityType.isNotBlank()) entityFieldMapping(artifact, entityType) else null
        if (entityType.isNotBlank() && mapping == null) {
            return ToolExecution(source, contract.referenceError(tool, "creation_entity_type_invalid", "$.entity_type"))
        }
        val contextEntities = mutableListOf<JsonObject>()
        (args["context_entity_ids"] as? JsonArray).orEmpty().forEachIndexed { index, value ->
            val id = (value as? JsonPrimitive)?.contentOrNull.orEmpty()
            if (id == entityId && id.isNotBlank()) return@forEachIndexed
            val reference = resolveEntity(source, id)
                ?: return ToolExecution(source, contract.referenceError(
                    tool, "creation_context_entity_unavailable", "$.context_entity_ids[$index]",
                ))
            contextEntities += reference.descriptor
        }
        if ((args["context_artifacts"] as? JsonArray).orEmpty().any {
                (it as? JsonPrimitive)?.contentOrNull !in contract.stageOrder
            }) {
            return ToolExecution(source, contract.referenceError(tool, "creation_context_artifact_invalid", "$.context_artifacts"))
        }
        val targetField = target?.field ?: mapping?.first
        val entityTarget = targetField?.let { field ->
            buildJsonObject {
                put("field", field)
                put("entity_type", target?.type ?: entityType)
                put("mode", if (target == null) "new" else "existing")
                put("initialize_stage", source.stageData(artifact).isEmpty())
                target?.let {
                    put("id", it.id)
                    put("entity_key", entityKey(it.data))
                }
            }
        }
        val entityBaseline = targetField?.let { field ->
            JsonObject(source.stageData(artifact).toMutableMap().apply {
                contract.entities.collections[artifact].orEmpty().forEach { (collection, _) ->
                    put(collection, JsonArray(emptyList()))
                }
                put(field, JsonArray(listOfNotNull(target?.data)))
            })
        }
        val generated = try {
            stageAgent.generateStage(source, artifact, instruction, config, entityTarget, entityBaseline, contextEntities)
        } catch (error: CancellationException) {
            throw error
        } catch (error: CreationGenerationException) {
            return ToolExecution(source, JsonObject(error.diagnostic + ("tool" to JsonPrimitive(tool))))
        } catch (error: Exception) {
            return ToolExecution(source, result(tool, "error", error.message ?: "${stageLabel(artifact)}生成失败"))
        }
        var updated = generated
        if (target != null) {
            val oldArtifact = source.stageData(target.artifact).toMutableMap()
            val oldRows = (oldArtifact[target.field] as? JsonArray).orEmpty().toMutableList()
            val generatedRows = (generated.stageData(target.artifact)[target.field] as? JsonArray).orEmpty()
            val replacement = generatedRows.singleOrNull() as? JsonObject
            if (replacement != null && target.index in oldRows.indices) {
                oldRows[target.index] = replacement
                oldArtifact[target.field] = JsonArray(oldRows)
                updated = stageAgent.replaceArtifact(
                    source, target.artifact, JsonObject(oldArtifact), generated.stageState(artifact).string("source"),
                )
            } else {
                return ToolExecution(source, result(tool, "error", "模型没有返回唯一目标实体，原资料未修改"))
            }
        } else if (entityType.isNotBlank()) {
            updated = mergeOnlyNewEntities(source, generated, artifact, entityType)
            if (updated == source) {
                return ToolExecution(source, result(tool, "error", "模型没有生成新的目标实体，原资料未修改"))
            }
        }
        return ToolExecution(
            updated,
            result(tool, "ok", "${stageLabel(artifact)}已生成并写入草稿", buildJsonObject {
                artifactSnapshot(updated, artifact).forEach { (key, value) -> put(key, value) }
                put("saved", true)
                put("requires_confirmation", true)
                put("next_action", "审阅并确认本阶段，或编辑后重新生成")
            }),
            wrote = true,
        )
    }

    private fun mergeOnlyNewEntities(
        original: JsonObject,
        generated: JsonObject,
        artifact: String,
        entityType: String,
    ): JsonObject {
        val mapping = entityFieldMapping(artifact, entityType) ?: return generated
        val (field, _) = mapping
        val oldData = original.stageData(artifact)
        if (oldData.isEmpty()) return generated
        val newData = generated.stageData(artifact)
        val oldRows = (oldData[field] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val newRows = (newData[field] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val oldKeys = oldRows.map(::entityKey).filter(String::isNotBlank).toSet()
        val additions = newRows.filter { entityKey(it).let { key -> key.isBlank() || key !in oldKeys } }
        if (additions.isEmpty()) return original
        val mergedData = oldData.toMutableMap()
        mergedData[field] = JsonArray(oldRows + additions)
        return stageAgent.replaceArtifact(original, artifact, JsonObject(mergedData), "model")
    }

    private fun setLocks(source: JsonObject, args: JsonObject, locked: Boolean): ToolExecution {
        val artifact = args.string("artifact")
        val paths = (args["paths"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        val draft = source.objectValue("draft").toMutableMap()
        val locks = (draft["artifact_locks"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        val current = (locks[artifact] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.toMutableSet()
        if (locked) current.addAll(paths) else current.removeAll(paths.toSet())
        locks[artifact] = JsonArray(current.sorted().map(::JsonPrimitive))
        draft["artifact_locks"] = JsonObject(locks)
        val updated = bump(source, draft)
        return ToolExecution(updated, result(if (locked) "lock_creation_fields" else "unlock_creation_fields", "ok", "字段锁定状态已更新"), wrote = true)
    }

    private fun snapshot(source: JsonObject): JsonObject = buildJsonObject {
        put("id", source.string("id"))
        put("revision", source.int("revision"))
        put("status", source.string("status"))
        put("user_brief", source.string("user_brief"))
        put("display_title", source.string("display_title"))
        put("draft", CreationAgentTurnRecords.agentVisibleDraft(source))
        put("artifacts", artifactSummaries(source))
    }

    private fun artifactSummaries(source: JsonObject): JsonArray = buildJsonArray {
        contract.stageOrder.forEach { artifact ->
            val state = source.stageState(artifact)
            add(buildJsonObject {
                put("artifact", artifact)
                put("label", stageLabel(artifact))
                put("status", state.string("status").ifBlank { "pending" })
                put("source", state.string("source"))
                put("data", state["data"] ?: JsonNull)
            })
        }
    }

    private fun artifactSnapshot(source: JsonObject, artifact: String): JsonObject = buildJsonObject {
        val state = source.stageState(artifact)
        put("session_id", source.string("id"))
        put("artifact", artifact)
        put("label", stageLabel(artifact))
        put("revision", source.int("revision"))
        put("status", state.string("status").ifBlank { "pending" })
        put("source", state.string("source"))
        put("data", state["data"] ?: JsonNull)
        put("collection_counts", buildJsonObject {
            contract.entities.collections[artifact].orEmpty().forEach { (field, _) ->
                (source.stageData(artifact)[field] as? JsonArray)?.let { put(field, it.size) }
            }
        })
    }

    private fun dependencySnapshot(artifact: String): JsonObject = buildJsonObject {
        put("artifact", artifact)
        put("downstream", JsonArray(contract.impactDependencies[artifact].orEmpty().map(::JsonPrimitive)))
        put("graph", buildJsonObject {
            contract.impactDependencies.forEach { (key, value) ->
                put(key, JsonArray(value.map(::JsonPrimitive)))
            }
        })
    }

    private fun localValidation(source: JsonObject): JsonObject {
        val required = listOf("constraints", "concepts", "world_style", "characters", "locations", "macro_outline")
        val missing = required.filter { source.stageState(it).string("status") != "confirmed" }
        val review = source.stageData("final_review")
        val reviewReady = source.stageState("final_review").string("status") in setOf("generated", "confirmed") &&
            (review["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() == true
        return buildJsonObject {
            put("ready", missing.isEmpty() && reviewReady)
            put("revision", source.int("revision"))
            put("missing_confirmations", JsonArray(missing.map(::JsonPrimitive)))
            put("final_review_ready", reviewReady)
        }
    }

    private fun listEntities(source: JsonObject, artifactFilter: String, typeFilter: String): List<JsonObject> {
        val result = mutableListOf<JsonObject>()
        contract.entities.collections.forEach { (artifact, mappings) ->
            if (artifactFilter.isNotBlank() && artifact != artifactFilter) return@forEach
            val data = source.stageData(artifact)
            mappings.forEach { (field, kind) ->
                (data[field] as? JsonArray).orEmpty().forEachIndexed row@{ index, item ->
                    val row = item as? JsonObject ?: return@row
                    val type = contract.entities.entityType(kind, row)
                    if (typeFilter.isNotBlank() && type != typeFilter) return@row
                    result += entityDescriptor(source, artifact, field, type, index, row)
                }
            }
        }
        return result
    }

    private fun resolveEntity(source: JsonObject, entityId: String): LocalCreationEntity? {
        (source.stageData("characters")["characters"] as? JsonArray).orEmpty().forEachIndexed { index, raw ->
            val row = raw as? JsonObject ?: return@forEachIndexed
            if (contract.entities.opening.characterId(source, row) == entityId) {
                return LocalCreationEntity(entityId, "characters", "characters", "character", index, row,
                    entityDescriptor(source, "characters", "characters", "character", index, row))
            }
        }
        val volumes = (source.stageData("macro_outline")["volumes"] as? JsonArray).orEmpty()
        volumes.forEachIndexed { index, raw ->
            val row = raw as? JsonObject ?: return@forEachIndexed
            if (contract.entities.opening.volumeId(source, row) == entityId) {
                return LocalCreationEntity(entityId, "macro_outline", "volumes", "volume", index, row,
                    entityDescriptor(source, "macro_outline", "volumes", "volume", index, row))
            }
        }
        val parts = entityId.split(':')
        if (parts.size != 3) return null
        val artifact = parts[0]
        val field = parts[1]
        val index = parts[2].toIntOrNull() ?: return null
        if (artifact == "macro_outline") return null
        val kind = contract.entities.collections[artifact]?.firstOrNull { it.first == field }?.second ?: return null
        val data = ((source.stageData(artifact)[field] as? JsonArray)?.getOrNull(index) as? JsonObject) ?: return null
        val type = contract.entities.entityType(kind, data)
        if (type == "character") return null
        return LocalCreationEntity(entityId, artifact, field, type, index, data, entityDescriptor(source, artifact, field, type, index, data))
    }

    private fun entityDescriptor(source: JsonObject, artifact: String, field: String, type: String, index: Int, data: JsonObject): JsonObject = buildJsonObject {
        put("id", if (type == "volume") contract.entities.opening.volumeId(source, data) else if (type == "character") contract.entities.opening.characterId(source, data) else "$artifact:$field:$index")
        put("artifact", artifact)
        put("entity_type", type)
        put("entity_key", entityKey(data).ifBlank { "$field-$index" })
        put("data", data)
    }

    private fun entityKey(data: JsonObject): String =
        data.string("name").ifBlank { data.string("title") }.ifBlank { data.string("id") }

    private fun entityFieldMapping(artifact: String, entityType: String): Pair<String, String>? =
        contract.entities.output(artifact, entityType)?.let { it.string("field") to entityType }

    internal fun applyChanges(source: JsonObject, changes: List<JsonObject>): JsonObject {
        var current: JsonElement = source
        changes.forEach { change ->
            require(change.keys.all { it in setOf("path", "action", "op", "value", "target_count", "fill_value") }) {
                "patch changes 含未声明字段"
            }
            val declaredAction = change.string("action")
            val declaredOp = change.string("op")
            require(("action" in change) != ("op" in change)) {
                "action 与 op 必须且只能提供一个"
            }
            var path = change.string("path")
            require((change["path"] as? JsonPrimitive)?.isString == true && path.startsWith("/")) {
                "patch path 必须是 JSON Pointer"
            }
            if ("action" in change) {
                require(declaredAction in setOf("set", "replace", "append", "remove", "resize")) {
                    "不支持的 patch action：$declaredAction"
                }
            } else {
                require(declaredOp in setOf("add", "replace", "remove")) {
                    "不支持的 JSON Patch op：$declaredOp"
                }
            }
            val action = if (declaredAction.isNotBlank()) declaredAction else when (declaredOp) {
                "add" -> if (path.endsWith("/-")) "append" else "set"
                else -> declaredOp
            }
            if (action in setOf("set", "replace", "append")) {
                require("value" in change) { "写入操作必须提供 value" }
            }
            if (declaredOp == "add" && path.endsWith("/-")) path = path.removeSuffix("/-").ifBlank { "/" }
            if ("target_count" in change) {
                require((change["target_count"] as? JsonPrimitive)?.isString == false &&
                    change.intOrNull("target_count")?.let { it >= 0 } == true) {
                    "target_count 必须是非负整数"
                }
            }
            if (action == "resize") require(change.intOrNull("target_count")?.let { it >= 0 } == true) {
                "resize 操作必须提供非负 target_count"
            }
            val parts = path.trim('/').takeIf(String::isNotBlank)?.split('/')
                ?.map { it.replace("~1", "/").replace("~0", "~") }
                ?: emptyList()
            require(parts.isNotEmpty() || action in setOf("set", "replace")) {
                "the artifact root cannot be removed or appended"
            }
            current = mutate(
                current, parts, action, change["value"], change.intOrNull("target_count"),
                change["fill_value"] ?: JsonObject(emptyMap()),
            )
        }
        return current as? JsonObject ?: error("立项对象根节点必须保持为 JSON 对象")
    }

    private fun mutate(
        current: JsonElement,
        parts: List<String>,
        action: String,
        value: JsonElement?,
        targetCount: Int?,
        fillValue: JsonElement?,
    ): JsonElement {
        if (parts.isEmpty()) {
            return when (action) {
                "append" -> JsonArray((current as? JsonArray
                    ?: error("append target is not a list")) + (value ?: JsonNull))
                "resize" -> {
                    val rows = (current as? JsonArray
                        ?: error("resize target is not a list")).toMutableList()
                    val target = requireNotNull(targetCount) { "resize 操作必须提供 target_count" }
                    while (rows.size > target) rows.removeAt(rows.lastIndex)
                    while (rows.size < target) rows += fillValue ?: JsonNull
                    JsonArray(rows)
                }
                "set", "replace" -> value ?: current
                else -> error("the artifact root cannot be removed or appended")
            }
        }
        return when (current) {
            is JsonObject -> {
                val key = parts.first()
                val map = current.toMutableMap()
                if (parts.size == 1 && action == "remove") {
                    require(map.remove(key) != null) { "remove target does not exist: $key" }
                } else {
                    val child = map[key] ?: if (parts.size == 1) JsonNull else JsonObject(emptyMap())
                    map[key] = mutate(child, parts.drop(1), action, value, targetCount, fillValue)
                }
                JsonObject(map)
            }
            is JsonArray -> {
                val index = parts.first().toIntOrNull() ?: error("数组路径必须使用数字下标")
                val rows = current.toMutableList()
                require(index in rows.indices) { "数组路径超出范围" }
                if (parts.size == 1 && action == "remove") rows.removeAt(index)
                else rows[index] = mutate(rows[index], parts.drop(1), action, value, targetCount, fillValue)
                JsonArray(rows)
            }
            else -> error("JSON Pointer 指向了不可继续展开的值")
        }
    }

    private fun bump(
        source: JsonObject,
        draftMap: MutableMap<String, JsonElement>,
        rootChange: (MutableMap<String, JsonElement>) -> Unit = {},
    ): JsonObject {
        val now = Instant.now().toString()
        draftMap["updated_at"] = JsonPrimitive(now)
        val root = source.toMutableMap()
        root["draft"] = JsonObject(draftMap)
        root["revision"] = JsonPrimitive(source.int("revision") + 1)
        root["updated_at"] = JsonPrimitive(now)
        rootChange(root)
        return JsonObject(root)
    }

    private fun result(tool: String, status: String, detail: String, data: JsonElement? = null): JsonObject = buildJsonObject {
        put("tool", tool)
        put("status", status)
        put("detail", detail)
        if (data != null) put("data", data)
    }

    private fun chatMessage(role: String, content: String): JsonObject = buildJsonObject {
        put("role", role)
        put("content", content)
    }

    private fun assistantToolMessage(
        content: String,
        calls: List<DirectAgentToolCall>,
    ): JsonObject = buildJsonObject {
        put("role", "assistant")
        put("content", content)
        put("tool_calls", buildJsonArray {
            calls.forEach { call ->
                add(buildJsonObject {
                    put("id", call.id)
                    put("type", "function")
                    put("function", buildJsonObject {
                        put("name", call.name)
                        put("arguments", call.rawArgumentsJson)
                    })
                })
            }
        })
    }

    private fun stageLabel(stage: String): String = contract.stageLabels[stage] ?: stage

    private fun toolLabel(tool: String): String = when (tool) {
        "get_creation_snapshot", "get_creation_session" -> "读取当前立项"
        "patch_creation_session" -> "写入创作约束"
        "patch_creation_artifact" -> "增量写入结构化资料"
        "generate_creation_artifact" -> "生成缺失的立项对象"
        "refine_creation_artifact" -> "定向调整立项对象"
        "confirm_creation_artifact" -> "确认立项对象"
        "finalize_creation_session" -> "创建正式作品"
        else -> tool
    }

    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
    private fun JsonObject.intOrNull(name: String): Int? = (get(name) as? JsonPrimitive)?.intOrNull
    private fun JsonObject.stageState(stage: String): JsonObject = objectValue("draft").objectValue("stages").objectValue(stage)
    private fun JsonObject.stageData(stage: String): JsonObject = stageState(stage)["data"] as? JsonObject ?: JsonObject(emptyMap())
    private data class ToolExecution(
        val session: JsonObject,
        val result: JsonObject,
        val wrote: Boolean = false,
        val createdProjectId: String? = null,
    )

    private data class LocalCreationEntity(
        val id: String,
        val artifact: String,
        val field: String,
        val type: String,
        val index: Int,
        val data: JsonObject,
        val descriptor: JsonObject,
    )

    private companion object {
        const val CREATION_OUTPUT_TOKENS = 6_000
        const val CREATION_STATUS_RESULT_BYTES = 4 * 1024
        const val CREATION_STANDARD_RESULT_BYTES = 16 * 1024
        const val CREATION_LARGE_READ_RESULT_BYTES = 32 * 1024
        val CREATION_LARGE_READ_TOOLS = setOf(
            "get_creation_session",
            "get_creation_snapshot",
            "get_creation_artifact",
            "list_creation_artifacts",
            "get_creation_entity",
            "list_creation_entities",
        )
    }
}
