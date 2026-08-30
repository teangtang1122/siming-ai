package com.siming.mobile.data.agent

import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.MobileKnownModelCapacityCatalog
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

internal object MobileCapacityAssurance {
    const val EXACT = "exact"
    const val CONSERVATIVE = "conservative"
    const val UNVERIFIED = "unverified"
    val ALL = setOf(EXACT, CONSERVATIVE, UNVERIFIED)
}

/** Resolves a task model only from an author profile or an exact first-party fact. */
internal fun mobileCapacityBoundTaskConfig(
    config: DirectApiConfig,
    taskType: String,
): DirectApiConfig {
    val defaultModel = config.model.trim()
    val selectedModel = config.modelForTask(taskType).trim()
    val selected = if (selectedModel == defaultModel) {
        config.copy(model = selectedModel)
    } else {
        // The author-confirmed default profile cannot be inherited by another
        // task model.  An exact catalog entry may still bind the selected model.
        config.copy(model = selectedModel, contextWindowTokens = null)
    }
    val bound = if (selected.contextWindowTokens != null) {
        selected
    } else {
        MobileKnownModelCapacityCatalog.applyIfKnown(selected)
    }
    if (bound?.contextWindowTokens == null) {
        throw MobileConversationContextException(
            MobileConversationContextErrorCode.CAPACITY_UNKNOWN,
            "任务 $taskType 的模型 $selectedModel 未配置独立容量档案",
        )
    }
    return bound
}

/** Immutable model/capacity identity. It never guesses a window from a model name. */
internal data class MobileGenerationModelBinding(
    val taskType: String,
    val provider: String,
    val modelName: String,
    val normalizedModel: String,
    val protocol: String,
    val contextWindowTokens: Int,
    val maxOutputTokens: Int,
    val tokenCounterId: String,
    val capacityAssurance: String,
    val promptContractHash: String,
    val toolSchemaHash: String,
    val configFingerprint: String,
) {
    init {
        require(
            listOf(
                taskType,
                provider,
                modelName,
                normalizedModel,
                protocol,
                promptContractHash,
                toolSchemaHash,
                configFingerprint,
            ).all(String::isNotBlank),
        ) { "模型容量绑定字段不能为空" }
        require(contextWindowTokens > 0 && maxOutputTokens >= 0) { "模型容量字段无效" }
        require(capacityAssurance in MobileCapacityAssurance.ALL) { "capacity_assurance 无效" }
        if (capacityAssurance != MobileCapacityAssurance.UNVERIFIED) {
            require(tokenCounterId.isNotBlank()) { "已验证容量必须绑定 TokenCounter" }
        }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("task_type", taskType)
        put("provider", provider)
        put("model_name", modelName)
        put("normalized_model", normalizedModel)
        put("protocol", protocol)
        put("context_window_tokens", contextWindowTokens)
        put("max_output_tokens", maxOutputTokens)
        put("token_counter_id", tokenCounterId)
        put("capacity_assurance", capacityAssurance)
        put("prompt_contract_hash", promptContractHash)
        put("tool_schema_hash", toolSchemaHash)
        put("config_fingerprint", configFingerprint)
    }

    val fingerprint: String get() = mobileCanonicalSha256(toJson())
}

internal interface MobileConversationTokenCounter {
    val counterId: String
    val assurance: String
    fun countText(text: String): Int
    fun countValue(value: JsonElement): Int
}

/** Conservative provider-neutral upper bound; provider wrappers still need explicit accounting. */
internal object MobileUtf8ByteTokenCounter : MobileConversationTokenCounter {
    override val counterId: String = "conservative.utf8_bytes.v1"
    override val assurance: String = MobileCapacityAssurance.CONSERVATIVE
    override fun countText(text: String): Int = text.toByteArray(Charsets.UTF_8).size
    override fun countValue(value: JsonElement): Int = countText(mobileCanonicalJson(value))
}

internal data class MobileRequestTokenComponents(
    val systemPromptTokens: Int = 0,
    val generatorTemplateTokens: Int = 0,
    val toolSchemaTokens: Int = 0,
    val messageWrapperTokens: Int = 0,
    val providerProtocolTokens: Int = 0,
    val checkpointTokens: Int = 0,
    val recentExactTurnTokens: Int = 0,
    val currentUserTokens: Int = 0,
    val currentTurnLedgerTokens: Int = 0,
    val pendingToolTransactionTokens: Int = 0,
    val providerStateTokens: Int = 0,
    val extraRuntimeInstructionTokens: Int = 0,
    val maxModelVisibleResultTokensForOpenTools: Int = 0,
    val nextStepWrapperTokens: Int = 0,
) {
    init {
        require(values().all { it >= 0 }) { "请求 Token 分项不能为负数" }
    }

    val currentInputTokens: Int
        get() = systemPromptTokens + generatorTemplateTokens + toolSchemaTokens +
            messageWrapperTokens + providerProtocolTokens + checkpointTokens +
            recentExactTurnTokens + currentUserTokens + currentTurnLedgerTokens +
            pendingToolTransactionTokens + providerStateTokens + extraRuntimeInstructionTokens

    private fun values(): List<Int> = listOf(
        systemPromptTokens,
        generatorTemplateTokens,
        toolSchemaTokens,
        messageWrapperTokens,
        providerProtocolTokens,
        checkpointTokens,
        recentExactTurnTokens,
        currentUserTokens,
        currentTurnLedgerTokens,
        pendingToolTransactionTokens,
        providerStateTokens,
        extraRuntimeInstructionTokens,
        maxModelVisibleResultTokensForOpenTools,
        nextStepWrapperTokens,
    )
}

internal data class MobileRequestBudgetEnvelope(
    val schema: String = SCHEMA,
    val modelBindingFingerprint: String,
    val tokenCounterId: String,
    val capacityAssurance: String,
    val contextWindowTokens: Int,
    val outputReserveTokens: Int,
    val safetyMarginTokens: Int,
    val systemPromptTokens: Int,
    val generatorTemplateTokens: Int,
    val toolSchemaTokens: Int,
    val messageWrapperTokens: Int,
    val providerProtocolTokens: Int,
    val checkpointTokens: Int,
    val recentExactTurnTokens: Int,
    val currentUserTokens: Int,
    val currentTurnLedgerTokens: Int,
    val pendingToolTransactionTokens: Int,
    val providerStateTokens: Int,
    val extraRuntimeInstructionTokens: Int,
    val maxModelVisibleResultTokensForOpenTools: Int,
    val nextStepWrapperTokens: Int,
    val currentInputTokens: Int,
    val requestInputLimit: Int,
    val projectedNextStepTokens: Int,
    val fitsCurrent: Boolean,
    val fitsProjected: Boolean,
) {
    val verified: Boolean
        get() = capacityAssurance in setOf(MobileCapacityAssurance.EXACT, MobileCapacityAssurance.CONSERVATIVE)

    init {
        require(schema == SCHEMA) { "请求预算 Schema 不受支持" }
        require(modelBindingFingerprint.isNotBlank() && tokenCounterId.isNotBlank()) {
            "请求预算缺少模型或计数器绑定"
        }
        require(capacityAssurance in MobileCapacityAssurance.ALL) { "请求预算 assurance 无效" }
    }

    fun requireSendable() {
        if (!verified) throw MobileConversationContextException(
            MobileConversationContextErrorCode.CAPACITY_UNKNOWN,
            "当前模型缺少可验证的 Token 计数与容量档案",
        )
        if (currentUserTokens > requestInputLimit) throw MobileConversationContextException(
            MobileConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY,
            "当前用户消息自身超过模型可用输入容量",
        )
        if (!fitsCurrent) throw MobileConversationContextException(
            MobileConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY,
            "最终 Agent 请求超过绑定模型容量",
        )
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("schema", schema)
        put("model_binding_fingerprint", modelBindingFingerprint)
        put("token_counter_id", tokenCounterId)
        put("capacity_assurance", capacityAssurance)
        put("context_window_tokens", contextWindowTokens)
        put("output_reserve_tokens", outputReserveTokens)
        put("safety_margin_tokens", safetyMarginTokens)
        put("system_prompt_tokens", systemPromptTokens)
        put("generator_template_tokens", generatorTemplateTokens)
        put("tool_schema_tokens", toolSchemaTokens)
        put("message_wrapper_tokens", messageWrapperTokens)
        put("provider_protocol_tokens", providerProtocolTokens)
        put("checkpoint_tokens", checkpointTokens)
        put("recent_exact_turn_tokens", recentExactTurnTokens)
        put("current_user_tokens", currentUserTokens)
        put("current_turn_ledger_tokens", currentTurnLedgerTokens)
        put("pending_tool_transaction_tokens", pendingToolTransactionTokens)
        put("provider_state_tokens", providerStateTokens)
        put("extra_runtime_instruction_tokens", extraRuntimeInstructionTokens)
        put("max_model_visible_result_tokens_for_open_tools", maxModelVisibleResultTokensForOpenTools)
        put("next_step_wrapper_tokens", nextStepWrapperTokens)
        put("current_input_tokens", currentInputTokens)
        put("request_input_limit", requestInputLimit)
        put("projected_next_step_tokens", projectedNextStepTokens)
        put("fits_current", fitsCurrent)
        put("fits_projected", fitsProjected)
    }

    fun recentTurnBudget(): MobileRecentTurnBudget = MobileRecentTurnBudget(
        requestInputLimitTokens = requestInputLimit,
        systemAndToolsTokens = systemPromptTokens + generatorTemplateTokens + toolSchemaTokens +
            extraRuntimeInstructionTokens,
        // Selection reserves the complete admissible native transaction for
        // the *next* model step.  It is not part of current_input_tokens, but
        // history cannot consume the space promised by fits_projected.
        providerWrapperTokens = messageWrapperTokens + providerProtocolTokens +
            maxModelVisibleResultTokensForOpenTools + nextStepWrapperTokens,
        checkpointTokens = checkpointTokens,
        currentUserTokens = currentUserTokens,
        currentTurnLedgerTokens = currentTurnLedgerTokens,
        pendingToolTransactionTokens = pendingToolTransactionTokens,
        providerStateTokens = providerStateTokens,
    )

    companion object {
        const val SCHEMA = "request_budget_envelope.v1"
    }
}

internal fun buildMobileRequestBudget(
    binding: MobileGenerationModelBinding,
    counter: MobileConversationTokenCounter,
    components: MobileRequestTokenComponents,
    outputReserveTokens: Int = binding.maxOutputTokens,
    safetyMarginTokens: Int,
): MobileRequestBudgetEnvelope {
    require(binding.tokenCounterId == counter.counterId) { "TokenCounter 与模型绑定不匹配" }
    require(binding.capacityAssurance == counter.assurance) { "TokenCounter assurance 与模型绑定不匹配" }
    val output = outputReserveTokens.coerceAtLeast(0)
    val margin = safetyMarginTokens.coerceAtLeast(0)
    val inputLimit = (binding.contextWindowTokens - output - margin).coerceAtLeast(0)
    val current = components.currentInputTokens
    val projected = current + components.maxModelVisibleResultTokensForOpenTools +
        components.nextStepWrapperTokens
    return MobileRequestBudgetEnvelope(
        modelBindingFingerprint = binding.fingerprint,
        tokenCounterId = counter.counterId,
        capacityAssurance = counter.assurance,
        contextWindowTokens = binding.contextWindowTokens,
        outputReserveTokens = output,
        safetyMarginTokens = margin,
        systemPromptTokens = components.systemPromptTokens,
        generatorTemplateTokens = components.generatorTemplateTokens,
        toolSchemaTokens = components.toolSchemaTokens,
        messageWrapperTokens = components.messageWrapperTokens,
        providerProtocolTokens = components.providerProtocolTokens,
        checkpointTokens = components.checkpointTokens,
        recentExactTurnTokens = components.recentExactTurnTokens,
        currentUserTokens = components.currentUserTokens,
        currentTurnLedgerTokens = components.currentTurnLedgerTokens,
        pendingToolTransactionTokens = components.pendingToolTransactionTokens,
        providerStateTokens = components.providerStateTokens,
        extraRuntimeInstructionTokens = components.extraRuntimeInstructionTokens,
        maxModelVisibleResultTokensForOpenTools = components.maxModelVisibleResultTokensForOpenTools,
        nextStepWrapperTokens = components.nextStepWrapperTokens,
        currentInputTokens = current,
        requestInputLimit = inputLimit,
        projectedNextStepTokens = projected,
        fitsCurrent = current <= inputLimit,
        fitsProjected = projected <= inputLimit,
    )
}
