package com.siming.mobile.data.network

import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

@Serializable
data class DirectApiConfig(
    val displayName: String,
    val baseUrl: String,
    val apiKey: String,
    val model: String,
    val protocol: String = PROTOCOL_AUTO,
    val availableModels: List<String> = emptyList(),
    val taskModels: Map<String, String> = emptyMap(),
    /** Author-supplied capacity; null temporarily uses the bounded 256K fallback. */
    val contextWindowTokens: Int? = null,
    val maxOutputTokens: Int = DEFAULT_AGENT_OUTPUT_TOKENS,
    val safetyMarginTokens: Int = DEFAULT_SAFETY_MARGIN_TOKENS,
) {
    init {
        require(contextWindowTokens == null || contextWindowTokens > 0) { "模型上下文窗口必须大于零" }
        require(maxOutputTokens > 0) { "模型输出预留必须大于零" }
        require(safetyMarginTokens >= 0) { "模型安全余量不能为负数" }
        contextWindowTokens?.let { window ->
            require(maxOutputTokens + safetyMarginTokens < window) {
                "输出预留与安全余量必须小于模型上下文窗口"
            }
        }
    }

    fun modelForTask(taskType: String): String = taskModels[taskType]
        ?.trim()
        ?.takeIf(String::isNotBlank)
        ?: model

    fun isDeepSeekProvider(): Boolean = listOf(displayName, baseUrl, model)
        .any { it.contains("deepseek", ignoreCase = true) }

    fun withContextWindowFallback(): DirectApiConfig {
        if (contextWindowTokens != null) return this
        val outputLimit = DEFAULT_CONTEXT_WINDOW_TOKENS - safetyMarginTokens - 1
        require(outputLimit > 0) { "256K 兜底窗口无法为输入保留空间" }
        return copy(
            contextWindowTokens = DEFAULT_CONTEXT_WINDOW_TOKENS,
            maxOutputTokens = minOf(maxOutputTokens, outputLimit),
        )
    }

    /**
     * Preserve an explicit profile, use exact first-party metadata when available,
     * otherwise bind the selected task model to the shared 256K fallback.
     */
    fun forTask(taskType: String): DirectApiConfig {
        val defaultModel = MobileKnownModelCapacityCatalog.canonicalModelForOfficialEndpoint(
            baseUrl,
            model,
        )
        val selectedModel = MobileKnownModelCapacityCatalog.canonicalModelForOfficialEndpoint(
            baseUrl,
            modelForTask(taskType),
        )
        val selected = if (selectedModel == defaultModel) {
            copy(model = selectedModel)
        } else {
            copy(model = selectedModel, contextWindowTokens = null)
        }
        val bound = if (selected.contextWindowTokens != null) {
            selected
        } else {
            MobileKnownModelCapacityCatalog.applyIfKnown(selected)
        }
        return (bound ?: selected).withContextWindowFallback()
    }

    fun summary() = DirectApiSummary(
        displayName = displayName,
        baseUrl = baseUrl,
        model = model,
        protocol = protocol,
        availableModels = availableModels,
        taskModels = taskModels,
        contextWindowTokens = contextWindowTokens,
        maxOutputTokens = maxOutputTokens,
        safetyMarginTokens = safetyMarginTokens,
    )

    companion object {
        const val PROTOCOL_AUTO = "auto"
        const val PROTOCOL_RESPONSES = "responses"
        const val PROTOCOL_CHAT_COMPLETIONS = "chat_completions"
        const val TASK_ASSISTANT = "assistant"
        const val TASK_PLANNING = "planning"
        const val TASK_CATALOGING = "cataloging"
        const val TASK_WRITING = "writing"
        const val TASK_EVALUATION = "evaluation"
        const val TASK_DECONSTRUCT = "deconstruct"
        const val DEFAULT_CONTEXT_WINDOW_TOKENS = 256_000
        const val DEFAULT_AGENT_OUTPUT_TOKENS = 6_000
        const val DEFAULT_SAFETY_MARGIN_TOKENS = 4_096
        val supportedProtocols = setOf(
            PROTOCOL_AUTO,
            PROTOCOL_RESPONSES,
            PROTOCOL_CHAT_COMPLETIONS,
        )
        val taskModelLabels = linkedMapOf(
            TASK_ASSISTANT to "项目助手",
            TASK_PLANNING to "立项与规划",
            TASK_CATALOGING to "作品建档",
            TASK_WRITING to "章节写作",
            TASK_EVALUATION to "质量评估",
            TASK_DECONSTRUCT to "拆书分析",
        )
    }
}

data class DirectApiSummary(
    val displayName: String,
    val baseUrl: String,
    val model: String,
    val protocol: String,
    val availableModels: List<String> = emptyList(),
    val taskModels: Map<String, String> = emptyMap(),
    val contextWindowTokens: Int? = null,
    val maxOutputTokens: Int = DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS,
    val safetyMarginTokens: Int = DirectApiConfig.DEFAULT_SAFETY_MARGIN_TOKENS,
)

data class DirectApiProbe(
    val response: String,
    val protocol: String,
)

data class DirectAgentToolCall(
    val id: String,
    val name: String,
    val arguments: JsonObject,
    val rawArgumentsJson: String,
)

data class DirectAgentTurn(
    val content: String,
    val reasoningContent: String,
    val toolCalls: List<DirectAgentToolCall>,
    val assistantMessage: JsonObject,
    val promptTokens: Int? = null,
)

class DirectNativeToolProtocolException(message: String) : IllegalStateException(message) {
    val reason: String = REASON

    companion object {
        const val REASON = "native_assistant_transaction_invalid"
    }
}

private data class DirectStreamSegment(
    val finishReason: String,
    val terminalSeen: Boolean,
)

private data class DirectStreamEvent(
    val delta: String = "",
    val finishReason: String = "",
    val terminal: Boolean = false,
    val error: String? = null,
)

private data class DirectAgentToolCallBuffer(
    var id: String = "",
    var name: String = "",
    val arguments: StringBuilder = StringBuilder(),
) {
    fun replaceArguments(value: String) {
        arguments.clear()
        arguments.append(value)
    }
}

private class DirectResumeHandshake(
    private val expectedPrefix: String,
) {
    private val buffer = StringBuilder()
    var verified: Boolean = false
        private set

    fun consume(chunk: String): String {
        if (verified) return chunk
        buffer.append(chunk)
        val candidate = buffer.toString().trimStart()
        if (candidate.length < expectedPrefix.length) {
            require(expectedPrefix.startsWith(candidate)) { "模型没有按检查点恢复协议继续输出" }
            return ""
        }
        require(candidate.startsWith(expectedPrefix)) { "模型没有按检查点恢复协议继续输出" }
        verified = true
        buffer.clear()
        return candidate.removePrefix(expectedPrefix)
    }

    fun requireVerified() {
        require(verified) { "模型恢复响应在检查点握手完成前结束" }
    }
}

class DirectApiHttpException(
    val statusCode: Int,
    message: String,
) : IOException(message)

/** OpenAI-compatible client used only by Android standalone mode. */
class DirectApiClient(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(150, TimeUnit.SECONDS)
        .build(),
    private val allowCleartextForTests: Boolean = false,
    private val retryDelaysMillis: List<Long> = listOf(700, 1_500, 3_000),
) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun discoverModels(baseUrl: String, apiKey: String): List<String> {
        require(apiKey.isNotBlank()) { "请先填写 API Key" }
        val endpoints = endpointCandidates(baseUrl, "models")
        var lastError: Throwable? = null
        for (endpoint in endpoints) {
            try {
                val response = execute(endpoint, apiKey, null)
                if (response.statusCode in PATH_FALLBACK_STATUS_CODES) continue
                ensureSuccess(response)
                return parseModels(response.body)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                lastError = error
                if (error !is DirectApiHttpException || error.statusCode !in PATH_FALLBACK_STATUS_CODES) break
            }
        }
        throw lastError ?: IOException("接口没有返回可用模型，请手动填写模型名")
    }

    suspend fun test(config: DirectApiConfig): String = testAndResolve(config).response

    suspend fun testAndResolve(config: DirectApiConfig): DirectApiProbe = completeResolved(
        config,
        systemPrompt = "你正在执行连接测试。请严格按要求简短回复。",
        userPrompt = "只回复：连接成功",
        maxOutputTokens = 64,
        temperature = 0.0,
    )

    suspend fun complete(
        config: DirectApiConfig,
        systemPrompt: String,
        userPrompt: String,
        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.7,
        extraBody: JsonObject? = null,
    ): String = completeResolved(
        config,
        systemPrompt,
        userPrompt,
        maxOutputTokens,
        temperature,
        extraBody,
    ).response

    /**
     * Stream long standalone-mobile text with the same verified checkpoint
     * handshake used by the PC gateway. A broken segment is never appended
     * unless the replacement model response proves the exact join point.
     */
    suspend fun completeResumable(
        config: DirectApiConfig,
        systemPrompt: String,
        userPrompt: String,
        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.7,
        extraBody: JsonObject? = null,
        initialContent: String = "",
        maxResumeAttempts: Int = 8,
        onCheckpoint: suspend (String) -> Unit = {},
    ): String {
        validateConfig(config)
        val protocols = when (config.protocol) {
            DirectApiConfig.PROTOCOL_AUTO -> listOf(
                DirectApiConfig.PROTOCOL_RESPONSES,
                DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
            )
            else -> listOf(config.protocol)
        }
        var protocolIndex = 0
        var protocol = protocols[protocolIndex]
        var committed = initialContent
        var resumeAttempt = 0
        var preOutputRetry = 0

        while (true) {
            val resumeMarker = if (committed.isBlank()) null else "[SIMING_RESUME_${UUID.randomUUID().toString().replace("-", "")}]"
            val anchor = committed.takeLast(STREAM_RESUME_ANCHOR_CHARS)
            val handshake = resumeMarker?.let { DirectResumeHandshake(it + anchor) }
            val messages = directTextMessages(
                systemPrompt = systemPrompt,
                userPrompt = userPrompt,
                committed = committed,
                resumeMarker = resumeMarker,
                anchor = anchor,
            )
            var segmentProduced = false
            try {
                val segment = streamTextSegment(
                    config = config,
                    protocol = protocol,
                    messages = messages,
                    maxOutputTokens = maxOutputTokens,
                    temperature = temperature,
                    extraBody = extraBody,
                ) { rawDelta ->
                    segmentProduced = segmentProduced || rawDelta.isNotEmpty()
                    val delta = handshake?.consume(rawDelta) ?: rawDelta
                    if (delta.isNotEmpty()) {
                        committed += delta
                        onCheckpoint(committed)
                    }
                }
                handshake?.requireVerified()
                val incomplete = segment.finishReason.lowercase() in INCOMPLETE_FINISH_REASONS
                require(segment.terminalSeen && !incomplete) { "模型流在完整结束前停止" }
                require(committed.isNotBlank()) { "模型返回了空内容，请检查模型名或切换 API 协议" }
                return committed
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                val canTryProtocol = (
                    committed.isBlank() && !segmentProduced && protocolIndex < protocols.lastIndex &&
                        error.isProtocolMismatch()
                    )
                if (canTryProtocol) {
                    protocol = protocols[++protocolIndex]
                    continue
                }
                if (
                    committed.isBlank() && !segmentProduced &&
                    preOutputRetry < retryDelaysMillis.size
                ) {
                    delay(retryDelaysMillis[preOutputRetry++])
                    continue
                }
                if ((committed.isNotBlank() || segmentProduced) && resumeAttempt < maxResumeAttempts.coerceIn(0, 32)) {
                    resumeAttempt += 1
                    continue
                }
                throw error
            }
        }
    }

    /** One native function-calling turn used by the embedded PC prompt contract. */
    suspend fun agentTurn(
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        toolChoice: String? = null,
        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.3,
        extraBody: JsonObject? = null,
    ): DirectAgentTurn {
        validateConfig(config)
        val protocol = if (config.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            DirectApiConfig.PROTOCOL_RESPONSES
        } else {
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS
        }
        val path = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            "responses"
        } else {
            "chat/completions"
        }
        var effectiveToolChoice = providerSafeToolChoice(config, toolChoice)
        var lastError: Throwable? = null
        endpointLoop@ for (endpoint in endpointCandidates(config.baseUrl, path)) {
            while (true) {
                try {
                    val payload = agentRequestPayload(
                        config = config,
                        messages = messages,
                        tools = tools,
                        toolChoice = effectiveToolChoice,
                        maxOutputTokens = maxOutputTokens,
                        temperature = temperature,
                        extraBody = extraBody,
                    )
                    val response = executeWithRetry(endpoint, config.apiKey, json.encodeToString(payload))
                    if (response.statusCode in PATH_FALLBACK_STATUS_CODES) break
                    ensureSuccess(response)
                    val root = json.parseToJsonElement(response.body).jsonObject
                    return if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                        parseResponsesAgentTurn(root)
                    } else {
                        parseChatAgentTurn(root)
                    }
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    lastError = error
                    if (effectiveToolChoice != null && error.isToolChoiceRejection()) {
                        effectiveToolChoice = null
                        continue
                    }
                    if (error is DirectApiHttpException && error.statusCode in PATH_FALLBACK_STATUS_CODES) {
                        break
                    }
                    break@endpointLoop
                }
            }
        }
        throw lastError ?: IOException("API 地址没有提供 $path 接口")
    }

    /**
     * A native function-calling turn whose visible text and reasoning are
     * delivered from the provider SSE stream as they arrive. Tool calls are
     * buffered until the provider marks the turn complete, so an interrupted
     * stream can never execute partial JSON arguments.
     */
    suspend fun streamAgentTurn(
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        toolChoice: String? = null,
        maxOutputTokens: Int = 4_000,
        temperature: Double = 0.3,
        extraBody: JsonObject? = null,
        onContentDelta: suspend (String) -> Unit = {},
        onReasoningDelta: suspend (String) -> Unit = {},
    ): DirectAgentTurn {
        validateConfig(config)
        val protocol = if (config.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            DirectApiConfig.PROTOCOL_RESPONSES
        } else {
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS
        }
        val path = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            "responses"
        } else {
            "chat/completions"
        }
        var effectiveToolChoice = providerSafeToolChoice(config, toolChoice)
        var visibleOutputEmitted = false
        var lastError: Throwable? = null
        endpointLoop@ for (endpoint in endpointCandidates(config.baseUrl, path)) {
            while (true) {
                val payload = agentRequestPayload(
                    config = config,
                    messages = messages,
                    tools = tools,
                    toolChoice = effectiveToolChoice,
                    maxOutputTokens = maxOutputTokens,
                    temperature = temperature,
                    extraBody = extraBody,
                    stream = true,
                )
                try {
                    return executeAgentStream(
                        endpoint = endpoint,
                        apiKey = config.apiKey,
                        body = json.encodeToString(payload),
                        protocol = protocol,
                        onContentDelta = { delta ->
                            visibleOutputEmitted = true
                            onContentDelta(delta)
                        },
                        onReasoningDelta = { delta ->
                            visibleOutputEmitted = true
                            onReasoningDelta(delta)
                        },
                    )
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    lastError = error
                    if (
                        effectiveToolChoice != null &&
                        !visibleOutputEmitted &&
                        error.isToolChoiceRejection()
                    ) {
                        effectiveToolChoice = null
                        continue
                    }
                    if (error is DirectApiHttpException && error.statusCode in PATH_FALLBACK_STATUS_CODES) {
                        break
                    }
                    break@endpointLoop
                }
            }
        }
        throw lastError ?: IOException("API 地址没有提供 $path 接口")
    }

    /** Match the provider compatibility policy used by the PC model gateway. */
    private fun providerSafeToolChoice(config: DirectApiConfig, requested: String?): String? {
        if (requested == null) return null
        val providerIdentity = listOf(config.displayName, config.baseUrl, config.model)
            .joinToString(" ")
            .lowercase()
        return requested.takeUnless {
            TOOL_CHOICE_UNSUPPORTED_THINKING_PROVIDERS.any(providerIdentity::contains)
        }
    }

    private fun Throwable.isToolChoiceRejection(): Boolean {
        if (this !is DirectApiHttpException) return false
        val detail = message.orEmpty().lowercase()
        return "tool_choice" in detail || "tool choice" in detail
    }

    /**
     * The single request serializer shared by execution and conversation-budget
     * accounting. Keeping the provider transformation here prevents the sealed
     * budget from measuring a provider-neutral shape that is never sent.
     */
    internal fun agentRequestPayload(
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        toolChoice: String?,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
        stream: Boolean = false,
    ): JsonObject {
        val effectiveToolChoice = providerSafeToolChoice(config, toolChoice)
        return if (config.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            responsesAgentPayload(
                config = config,
                messages = messages,
                tools = tools,
                toolChoice = effectiveToolChoice,
                maxOutputTokens = maxOutputTokens,
                temperature = temperature,
                extraBody = extraBody,
                stream = stream,
            )
        } else {
            chatAgentPayload(
                config = config,
                messages = messages,
                tools = tools,
                toolChoice = effectiveToolChoice,
                maxOutputTokens = maxOutputTokens,
                temperature = temperature,
                extraBody = extraBody,
                stream = stream,
            )
        }
    }

    /** The exact non-streaming text payload used by [complete]. */
    internal fun completeRequestPayload(
        config: DirectApiConfig,
        protocol: String,
        systemPrompt: String,
        userPrompt: String,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
    ): JsonObject {
        require(protocol in setOf(
            DirectApiConfig.PROTOCOL_RESPONSES,
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
        )) { "文本请求必须先解析为明确的 provider 协议" }
        return if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            buildJsonObject {
                put("model", config.model.trim())
                put("instructions", systemPrompt)
                put("input", userPrompt)
                put("temperature", temperature)
                put("max_output_tokens", maxOutputTokens)
                put("stream", false)
                extraBody?.forEach { (key, value) -> put(key, value) }
            }
        } else {
            buildJsonObject {
                put("model", config.model.trim())
                put("messages", buildJsonArray {
                    add(buildJsonObject {
                        put("role", "system")
                        put("content", systemPrompt)
                    })
                    add(buildJsonObject {
                        put("role", "user")
                        put("content", userPrompt)
                    })
                })
                put("temperature", temperature)
                put("max_tokens", maxOutputTokens)
                put("stream", false)
                extraBody?.forEach { (key, value) -> put(key, value) }
            }
        }
    }

    private suspend fun completeResolved(
        config: DirectApiConfig,
        systemPrompt: String,
        userPrompt: String,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject? = null,
    ): DirectApiProbe {
        validateConfig(config)
        val protocols = when (config.protocol) {
            DirectApiConfig.PROTOCOL_AUTO -> listOf(
                DirectApiConfig.PROTOCOL_RESPONSES,
                DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
            )
            else -> listOf(config.protocol)
        }
        var lastError: Throwable? = null
        for ((index, protocol) in protocols.withIndex()) {
            try {
                val text = completeWithProtocol(
                    config,
                    protocol,
                    systemPrompt,
                    userPrompt,
                    maxOutputTokens,
                    temperature,
                    extraBody,
                )
                require(text.isNotBlank()) { "模型返回了空内容，请检查模型名或切换 API 协议" }
                return DirectApiProbe(text, protocol)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                lastError = error
                val canTryNext = index < protocols.lastIndex && error.isProtocolMismatch()
                if (!canTryNext) throw error
            }
        }
        throw lastError ?: IOException("模型调用没有完成")
    }

    private fun chatAgentPayload(
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        toolChoice: String?,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
        stream: Boolean = false,
    ): JsonObject = buildJsonObject {
        put("model", config.model.trim())
        put("messages", JsonArray(messages))
        put("tools", tools)
        toolChoice?.let { put("tool_choice", it) }
        put("temperature", temperature)
        put("max_tokens", maxOutputTokens)
        put("stream", stream)
        extraBody?.forEach { (key, value) -> put(key, value) }
    }

    private fun responsesAgentPayload(
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        toolChoice: String?,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
        stream: Boolean = false,
    ): JsonObject {
        val system = messages.firstOrNull { it.string("role") == "system" }?.string("content").orEmpty()
        val input = buildJsonArray {
            messages.filterNot { it.string("role") == "system" }.forEach { message ->
                when (message.string("role")) {
                    "tool" -> add(buildJsonObject {
                        put("type", "function_call_output")
                        put("call_id", message.string("tool_call_id"))
                        put("output", message.string("content"))
                    })
                    "assistant" -> {
                        (message["provider_state"] as? JsonArray).orEmpty().forEach { state ->
                            val value = state as? JsonObject ?: return@forEach
                            if (value.string("type") == "reasoning") add(value)
                        }
                        val content = message.string("content")
                        if (content.isNotBlank()) add(buildJsonObject {
                            put("role", "assistant")
                            put("content", content)
                        })
                        (message["tool_calls"] as? JsonArray).orEmpty().forEach toolCalls@{ rawCall ->
                            val call = rawCall as? JsonObject ?: return@toolCalls
                            val function = call["function"] as? JsonObject ?: return@toolCalls
                            add(buildJsonObject {
                                put("type", "function_call")
                                put("call_id", call.string("id"))
                                put("name", function.string("name"))
                                put("arguments", function.string("arguments"))
                            })
                        }
                    }
                    else -> add(buildJsonObject {
                        put("role", message.string("role"))
                        put("content", message.string("content"))
                    })
                }
            }
        }
        val responseTools = buildJsonArray {
            tools.forEach { rawTool ->
                val function = (rawTool as? JsonObject)?.get("function") as? JsonObject
                    ?: return@forEach
                add(buildJsonObject {
                    put("type", "function")
                    put("name", function.string("name"))
                    put("description", function.string("description"))
                    put("parameters", function["parameters"] ?: JsonObject(emptyMap()))
                })
            }
        }
        return buildJsonObject {
            put("model", config.model.trim())
            if (system.isNotBlank()) put("instructions", system)
            put("input", input)
            put("tools", responseTools)
            toolChoice?.let { put("tool_choice", it) }
            put("temperature", temperature)
            put("max_output_tokens", maxOutputTokens)
            put("stream", stream)
            extraBody?.forEach { (key, value) -> put(key, value) }
        }
    }

    private fun parseChatAgentTurn(root: JsonObject): DirectAgentTurn {
        val message = ((root["choices"] as? JsonArray)?.firstOrNull() as? JsonObject)
            ?.get("message") as? JsonObject
            ?: error("模型没有返回 assistant message")
        val content = when (val value = message["content"]) {
            is JsonPrimitive -> value.contentOrNull.orEmpty()
            is JsonArray -> value.mapNotNull { part ->
                ((part as? JsonObject)?.get("text") as? JsonPrimitive)?.contentOrNull
            }.joinToString("")
            else -> ""
        }
        val reasoning = listOf("reasoning_content", "reasoning", "reasoning_text")
            .firstNotNullOfOrNull { key -> (message[key] as? JsonPrimitive)?.contentOrNull }
            .orEmpty()
        val calls = (message["tool_calls"] as? JsonArray).orEmpty().mapNotNull(::parseToolCall)
        val canonicalToolCalls = buildJsonArray {
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
        }
        val canonical = buildJsonObject {
            put("role", "assistant")
            put("content", content)
            if (reasoning.isNotBlank()) put("reasoning_content", reasoning)
            if (calls.isNotEmpty()) {
                put("tool_calls", canonicalToolCalls)
            }
        }
        return DirectAgentTurn(content.trim(), reasoning, calls, canonical, promptTokens(root, "prompt_tokens"))
    }

    private fun parseResponsesAgentTurn(root: JsonObject): DirectAgentTurn {
        val output = (root["output"] as? JsonArray).orEmpty()
        val calls = output.mapNotNull { item ->
            val value = item as? JsonObject ?: return@mapNotNull null
            if (value.string("type") != "function_call") return@mapNotNull null
            parseFunctionCall(
                id = value.string("call_id").ifBlank { value.string("id") },
                name = value.string("name"),
                rawArguments = value.string("arguments"),
            )
        }
        val content = parseResponsesText(root)
        val reasoning = output
            .mapNotNull { it as? JsonObject }
            .filter { it.string("type") == "reasoning" }
            .flatMap { item ->
                listOf("summary", "content").flatMap { key ->
                    (item[key] as? JsonArray).orEmpty().mapNotNull { rawPart ->
                        val part = rawPart as? JsonObject ?: return@mapNotNull null
                        part.string("text")
                            .ifBlank { part.string("content") }
                            .takeIf(String::isNotBlank)
                    }
                } + listOf(item.string("reasoning_content"), item.string("text")).filter(String::isNotBlank)
            }
            .joinToString("\n")
        val providerState = output.mapNotNull { raw ->
            val item = raw as? JsonObject ?: return@mapNotNull null
            item.takeIf {
                it.string("type") == "reasoning" && it.string("encrypted_content").isNotBlank()
            }
        }
        val toolCalls = buildJsonArray {
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
        }
        val canonical = buildJsonObject {
            put("role", "assistant")
            put("content", content)
            if (reasoning.isNotBlank()) put("reasoning_content", reasoning)
            if (providerState.isNotEmpty()) put("provider_state", JsonArray(providerState))
            if (calls.isNotEmpty()) put("tool_calls", toolCalls)
        }
        return DirectAgentTurn(content.trim(), reasoning, calls, canonical, promptTokens(root, "input_tokens"))
    }

    private fun parseToolCall(element: JsonElement): DirectAgentToolCall? {
        val value = element as? JsonObject
            ?: throw DirectNativeToolProtocolException("原生 tool_call 必须是对象")
        val function = value["function"] as? JsonObject
            ?: throw DirectNativeToolProtocolException("原生 tool_call 缺少 function 对象")
        return parseFunctionCall(
            id = value.string("id"),
            name = function.string("name"),
            rawArguments = function.string("arguments"),
        )
    }

    private fun parseFunctionCall(
        id: String,
        name: String,
        rawArguments: String,
    ): DirectAgentToolCall {
        if (id.isBlank()) throw DirectNativeToolProtocolException("原生工具调用缺少 call_id，未执行工具")
        if (name.isBlank()) throw DirectNativeToolProtocolException("原生工具调用缺少函数名，未执行工具")
        if (rawArguments.isBlank()) {
            throw DirectNativeToolProtocolException("原生工具调用缺少 arguments JSON，未执行工具")
        }
        val arguments = runCatching {
            json.parseToJsonElement(rawArguments) as? JsonObject
        }.getOrNull() ?: throw DirectNativeToolProtocolException(
            "原生工具调用 arguments 不是有效 JSON 对象，未执行工具",
        )
        return DirectAgentToolCall(
            id = id,
            name = name,
            arguments = arguments,
            rawArgumentsJson = rawArguments,
        )
    }

    private suspend fun executeAgentStream(
        endpoint: HttpUrl,
        apiKey: String,
        body: String,
        protocol: String,
        onContentDelta: suspend (String) -> Unit,
        onReasoningDelta: suspend (String) -> Unit,
    ): DirectAgentTurn = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(endpoint)
            .header("Accept", "text/event-stream")
            .header("Authorization", "Bearer ${apiKey.trim()}")
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        val call = client.newCall(request)
        val cancellationHandle = currentCoroutineContext()[Job]?.invokeOnCompletion { cause ->
            if (cause is CancellationException) call.cancel()
        }
        try {
            call.execute().use { response ->
                if (!response.isSuccessful) {
                    val raw = response.body?.string().orEmpty()
                    ensureSuccess(RawResponse(response.code, raw))
                }
                val source = response.body?.source() ?: throw IOException("AI 流式响应为空")
                val content = StringBuilder()
                val reasoning = StringBuilder()
                val calls = linkedMapOf<String, DirectAgentToolCallBuffer>()
                var promptTokens: Int? = null
                var terminalSeen = false
                var terminalResponse: JsonObject? = null

                while (true) {
                    currentCoroutineContext().ensureActive()
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data:")) continue
                    val data = line.removePrefix("data:").trim()
                    if (data.isEmpty()) continue
                    if (data == "[DONE]") {
                        // Responses API has a structured response.completed event.
                        // Do not let a bare transport sentinel hide a truncated run.
                        if (protocol != DirectApiConfig.PROTOCOL_RESPONSES) terminalSeen = true
                        break
                    }
                    val root = runCatching { json.parseToJsonElement(data) as JsonObject }.getOrNull()
                        ?: continue
                    if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                        val type = root.string("type")
                        val responseRoot = root["response"] as? JsonObject
                        val error = (root["error"] as? JsonObject)?.string("message")
                            ?: (responseRoot?.get("error") as? JsonObject)?.string("message")
                        if (!error.isNullOrBlank()) throw DirectApiHttpException(502, error)

                        when {
                            type in RESPONSE_TEXT_DELTA_TYPES -> {
                                val delta = root.string("delta")
                                if (delta.isNotEmpty()) {
                                    content.append(delta)
                                    onContentDelta(delta)
                                }
                            }
                            type in RESPONSE_REASONING_DELTA_TYPES -> {
                                val delta = root.string("delta").ifBlank { root.string("text") }
                                if (delta.isNotEmpty()) {
                                    reasoning.append(delta)
                                    onReasoningDelta(delta)
                                }
                            }
                        }

                        val item = root["item"] as? JsonObject
                        if (item?.string("type") == "function_call") {
                            mergeResponseFunctionCall(calls, item, replaceArguments = type.endsWith(".done"))
                        }
                        if (type.contains("function_call_arguments")) {
                            val key = root.string("item_id")
                                .ifBlank { root.string("call_id") }
                                .ifBlank { root.string("output_index") }
                            val buffer = calls.getOrPut(key.ifBlank { "call-${calls.size}" }) {
                                DirectAgentToolCallBuffer()
                            }
                            val arguments = root.string("arguments")
                                .ifBlank { root.string("delta") }
                            if (type.endsWith(".done")) buffer.replaceArguments(arguments)
                            else buffer.arguments.append(arguments)
                        }

                        if (type in RESPONSE_TERMINAL_TYPES) {
                            terminalSeen = true
                            terminalResponse = responseRoot
                            if (type != "response.completed") {
                                val status = responseRoot?.string("status").orEmpty().ifBlank { type }
                                throw IOException("模型流式响应未完成：$status")
                            }
                        }
                        promptTokens = responseRoot?.let { promptTokens(it, "input_tokens") } ?: promptTokens
                    } else {
                        val choice = (root["choices"] as? JsonArray)?.firstOrNull() as? JsonObject
                            ?: continue
                        val delta = choice["delta"] as? JsonObject ?: JsonObject(emptyMap())
                        val contentDelta = when (val value = delta["content"]) {
                            is JsonPrimitive -> value.contentOrNull.orEmpty()
                            is JsonArray -> value.mapNotNull { part ->
                                ((part as? JsonObject)?.get("text") as? JsonPrimitive)?.contentOrNull
                            }.joinToString("")
                            else -> ""
                        }
                        if (contentDelta.isNotEmpty()) {
                            content.append(contentDelta)
                            onContentDelta(contentDelta)
                        }
                        val reasoningDelta = listOf("reasoning_content", "reasoning", "reasoning_text")
                            .firstNotNullOfOrNull { key ->
                                (delta[key] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotEmpty)
                            }.orEmpty()
                        if (reasoningDelta.isNotEmpty()) {
                            reasoning.append(reasoningDelta)
                            onReasoningDelta(reasoningDelta)
                        }
                        (delta["tool_calls"] as? JsonArray).orEmpty().forEachIndexed { position, raw ->
                            val streamed = raw as? JsonObject ?: return@forEachIndexed
                            val index = (streamed["index"] as? JsonPrimitive)?.intOrNull ?: position
                            val buffer = calls.getOrPut(index.toString()) { DirectAgentToolCallBuffer() }
                            streamed.string("id").takeIf(String::isNotBlank)?.let { buffer.id = it }
                            val function = streamed["function"] as? JsonObject
                            function?.string("name")?.takeIf(String::isNotBlank)?.let {
                                buffer.name += it
                            }
                            function?.string("arguments")?.takeIf(String::isNotEmpty)?.let {
                                buffer.arguments.append(it)
                            }
                        }
                        promptTokens = promptTokens(root, "prompt_tokens") ?: promptTokens
                        if (choice.string("finish_reason").isNotBlank()) terminalSeen = true
                    }
                }

                if (!terminalSeen) throw IOException("AI 流式连接提前结束，未收到完成事件")
                val parsedTerminal = terminalResponse?.let(::parseResponsesAgentTurn)
                val finalContent = content.toString().ifBlank { parsedTerminal?.content.orEmpty() }
                val finalReasoning = reasoning.toString().ifBlank { parsedTerminal?.reasoningContent.orEmpty() }
                val finalCalls = calls.values.distinct().mapNotNull { buffer ->
                    parseFunctionCall(buffer.id, buffer.name, buffer.arguments.toString())
                }.ifEmpty { parsedTerminal?.toolCalls.orEmpty() }
                val providerState = (parsedTerminal?.assistantMessage?.get("provider_state") as? JsonArray)
                    .orEmpty()
                    .mapNotNull { it as? JsonObject }
                canonicalAgentTurn(
                    content = finalContent,
                    reasoning = finalReasoning,
                    calls = finalCalls,
                    providerState = providerState,
                    promptTokens = promptTokens ?: parsedTerminal?.promptTokens,
                )
            }
        } finally {
            cancellationHandle?.dispose()
        }
    }

    private fun mergeResponseFunctionCall(
        calls: MutableMap<String, DirectAgentToolCallBuffer>,
        item: JsonObject,
        replaceArguments: Boolean,
    ) {
        val id = item.string("call_id").ifBlank { item.string("id") }
        val itemId = item.string("id")
        val key = id.ifBlank { itemId }.ifBlank {
            throw DirectNativeToolProtocolException(
                "Responses 原生函数调用缺少 call_id 和 item id，未执行工具",
            )
        }
        val buffer = calls[id] ?: calls[itemId] ?: calls.getOrPut(key) { DirectAgentToolCallBuffer() }
        if (id.isNotBlank()) calls[id] = buffer
        if (itemId.isNotBlank()) calls[itemId] = buffer
        if (id.isNotBlank()) buffer.id = id
        item.string("name").takeIf(String::isNotBlank)?.let { buffer.name = it }
        val arguments = item.string("arguments")
        if (replaceArguments) buffer.replaceArguments(arguments)
        else if (arguments.isNotBlank()) buffer.arguments.append(arguments)
    }

    private fun canonicalAgentTurn(
        content: String,
        reasoning: String,
        calls: List<DirectAgentToolCall>,
        providerState: List<JsonObject>,
        promptTokens: Int?,
    ): DirectAgentTurn {
        val toolCalls = buildJsonArray {
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
        }
        val canonical = buildJsonObject {
            put("role", "assistant")
            put("content", content)
            if (reasoning.isNotBlank()) put("reasoning_content", reasoning)
            if (providerState.isNotEmpty()) put("provider_state", JsonArray(providerState))
            if (calls.isNotEmpty()) put("tool_calls", toolCalls)
        }
        return DirectAgentTurn(content.trim(), reasoning, calls, canonical, promptTokens)
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

    private fun promptTokens(root: JsonObject, key: String): Int? =
        ((root["usage"] as? JsonObject)?.get(key) as? JsonPrimitive)
            ?.intOrNull
            ?.coerceAtLeast(0)

    private fun directTextMessages(
        systemPrompt: String,
        userPrompt: String,
        committed: String,
        resumeMarker: String?,
        anchor: String,
    ): List<JsonObject> {
        if (resumeMarker == null) {
            return listOf(
                buildJsonObject { put("role", "system"); put("content", systemPrompt) },
                buildJsonObject { put("role", "user"); put("content", userPrompt) },
            )
        }
        val resumeInstruction = (
            "这是运行时恢复协议，不是新的用户意图。上一条 assistant 输出因传输中断，已输出内容由运行时保存。" +
                "收到恢复请求时必须先逐字输出指定恢复标记和断点锚点，随后从锚点后的下一个字符继续；" +
                "不得重复更早内容，也不得解释恢复协议。"
            )
        val expected = resumeMarker + anchor
        return listOf(
            buildJsonObject {
                put("role", "system")
                put("content", "$systemPrompt\n\n$resumeInstruction")
            },
            buildJsonObject { put("role", "user"); put("content", userPrompt) },
            buildJsonObject { put("role", "assistant"); put("content", committed) },
            buildJsonObject {
                put("role", "user")
                put(
                    "content",
                    "继续刚才因传输中断的同一响应。回复开头必须严格等于下面一行，" +
                        "不能添加代码块、空格或说明；之后紧接尚未输出的内容：\n$expected",
                )
            },
        )
    }

    private fun streamTextPayload(
        config: DirectApiConfig,
        protocol: String,
        messages: List<JsonObject>,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
    ): JsonObject = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
        val instructions = messages
            .filter { it.string("role") == "system" }
            .joinToString("\n\n") { it.string("content") }
        buildJsonObject {
            put("model", config.model.trim())
            if (instructions.isNotBlank()) put("instructions", instructions)
            put("input", buildJsonArray {
                messages.filterNot { it.string("role") == "system" }.forEach { message ->
                    add(buildJsonObject {
                        put("role", message.string("role"))
                        put("content", message.string("content"))
                    })
                }
            })
            put("temperature", temperature)
            put("max_output_tokens", maxOutputTokens)
            put("stream", true)
            extraBody?.forEach { (key, value) -> put(key, value) }
        }
    } else {
        buildJsonObject {
            put("model", config.model.trim())
            put("messages", JsonArray(messages))
            put("temperature", temperature)
            put("max_tokens", maxOutputTokens)
            put("stream", true)
            extraBody?.forEach { (key, value) -> put(key, value) }
        }
    }

    private suspend fun streamTextSegment(
        config: DirectApiConfig,
        protocol: String,
        messages: List<JsonObject>,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
        onDelta: suspend (String) -> Unit,
    ): DirectStreamSegment {
        val path = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) "responses" else "chat/completions"
        val body = json.encodeToString(
            streamTextPayload(config, protocol, messages, maxOutputTokens, temperature, extraBody),
        )
        var lastError: Throwable? = null
        for (endpoint in endpointCandidates(config.baseUrl, path)) {
            var transientAttempt = 0
            while (true) {
                try {
                    return executeTextStream(endpoint, config.apiKey, body, protocol, onDelta)
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    lastError = error
                    val status = (error as? DirectApiHttpException)?.statusCode
                    if (status in PATH_FALLBACK_STATUS_CODES) break
                    if (status in TRANSIENT_STATUS_CODES && transientAttempt < retryDelaysMillis.size) {
                        delay(retryDelaysMillis[transientAttempt++])
                        continue
                    }
                    throw error
                }
            }
        }
        throw lastError ?: DirectApiHttpException(404, "API 地址没有提供 $path 接口")
    }

    private suspend fun executeTextStream(
        endpoint: HttpUrl,
        apiKey: String,
        body: String,
        protocol: String,
        onDelta: suspend (String) -> Unit,
    ): DirectStreamSegment = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(endpoint)
            .header("Accept", "text/event-stream")
            .header("Authorization", "Bearer ${apiKey.trim()}")
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        val call = client.newCall(request)
        val cancellationHandle = currentCoroutineContext()[Job]?.invokeOnCompletion { cause ->
            if (cause is CancellationException) call.cancel()
        }
        try {
            call.execute().use { response ->
                if (!response.isSuccessful) {
                    val raw = response.body?.string().orEmpty()
                    ensureSuccess(RawResponse(response.code, raw))
                }
                val source = response.body?.source() ?: throw IOException("AI 流式响应为空")
                var finishReason = ""
                var terminalSeen = false
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data:")) continue
                    val data = line.removePrefix("data:").trim()
                    if (data.isEmpty()) continue
                    if (data == "[DONE]") {
                        if (protocol != DirectApiConfig.PROTOCOL_RESPONSES) terminalSeen = true
                        break
                    }
                    val root = runCatching { json.parseToJsonElement(data) as JsonObject }.getOrNull() ?: continue
                    val event = parseDirectStreamEvent(root, protocol)
                    event.error?.let { throw DirectApiHttpException(502, it) }
                    if (event.delta.isNotEmpty()) onDelta(event.delta)
                    if (event.finishReason.isNotBlank()) finishReason = event.finishReason
                    terminalSeen = terminalSeen || event.terminal
                }
                DirectStreamSegment(finishReason.ifBlank { "stop" }, terminalSeen)
            }
        } finally {
            cancellationHandle?.dispose()
        }
    }

    private fun parseDirectStreamEvent(root: JsonObject, protocol: String): DirectStreamEvent {
        val choice = (root["choices"] as? JsonArray)?.firstOrNull() as? JsonObject
        if (choice != null) {
            val deltaObject = choice["delta"] as? JsonObject
            val delta = when (val content = deltaObject?.get("content")) {
                is JsonPrimitive -> content.contentOrNull.orEmpty()
                is JsonArray -> content.mapNotNull { part ->
                    ((part as? JsonObject)?.get("text") as? JsonPrimitive)?.contentOrNull
                }.joinToString("")
                else -> ""
            }
            val finish = choice.string("finish_reason")
            return DirectStreamEvent(delta, finish, finish.isNotBlank())
        }
        if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            val type = root.string("type")
            val response = root["response"] as? JsonObject
            val status = response?.string("status").orEmpty()
            val error = (root["error"] as? JsonObject)?.string("message")
                ?: (response?.get("error") as? JsonObject)?.string("message")
            val delta = if (type == "response.output_text.delta" || type == "output_text.delta") {
                root.string("delta")
            } else {
                ""
            }
            val terminal = type in setOf("response.completed", "response.incomplete", "response.failed")
            val finish = when {
                type == "response.incomplete" || status == "incomplete" -> "incomplete"
                type == "response.failed" || status == "failed" -> "failed"
                terminal -> "stop"
                else -> ""
            }
            return DirectStreamEvent(delta, finish, terminal, error)
        }
        return DirectStreamEvent()
    }

    private suspend fun completeWithProtocol(
        config: DirectApiConfig,
        protocol: String,
        systemPrompt: String,
        userPrompt: String,
        maxOutputTokens: Int,
        temperature: Double,
        extraBody: JsonObject?,
    ): String {
        val path = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            "responses"
        } else {
            "chat/completions"
        }
        val payload = completeRequestPayload(
            config = config,
            protocol = protocol,
            systemPrompt = systemPrompt,
            userPrompt = userPrompt,
            maxOutputTokens = maxOutputTokens,
            temperature = temperature,
            extraBody = extraBody,
        )
        var lastError: Throwable? = null
        for (endpoint in endpointCandidates(config.baseUrl, path)) {
            try {
                val response = executeWithRetry(
                    endpoint,
                    config.apiKey,
                    json.encodeToString(payload),
                )
                if (response.statusCode in PATH_FALLBACK_STATUS_CODES) continue
                ensureSuccess(response)
                val root = json.parseToJsonElement(response.body).jsonObject
                return if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    parseResponsesText(root)
                } else {
                    parseChatText(root)
                }
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                lastError = error
                if (error !is DirectApiHttpException || error.statusCode !in PATH_FALLBACK_STATUS_CODES) break
            }
        }
        throw lastError ?: DirectApiHttpException(404, "API 地址没有提供 $path 接口")
    }

    private suspend fun executeWithRetry(
        endpoint: HttpUrl,
        apiKey: String,
        body: String,
    ): RawResponse {
        var response = execute(endpoint, apiKey, body)
        for (delayMillis in retryDelaysMillis) {
            if (response.statusCode !in TRANSIENT_STATUS_CODES) return response
            delay(delayMillis)
            response = execute(endpoint, apiKey, body)
        }
        return response
    }

    private suspend fun execute(
        endpoint: HttpUrl,
        apiKey: String,
        body: String?,
    ): RawResponse = withContext(Dispatchers.IO) {
        val builder = Request.Builder()
            .url(endpoint)
            .header("Accept", "application/json")
            .header("Authorization", "Bearer ${apiKey.trim()}")
        if (body == null) {
            builder.get()
        } else {
            builder.post(body.toRequestBody(JSON_MEDIA_TYPE))
        }
        client.newCall(builder.build()).execute().use { response ->
            RawResponse(response.code, response.body?.string().orEmpty())
        }
    }

    private fun endpointCandidates(baseUrl: String, path: String): List<HttpUrl> {
        val base = validateBaseUrl(baseUrl).toString().trimEnd('/')
        return buildList {
            add(requireNotNull("$base/$path".toHttpUrlOrNull()))
            if (!base.endsWith("/v1")) {
                add(requireNotNull("$base/v1/$path".toHttpUrlOrNull()))
            }
        }.distinct()
    }

    private fun validateBaseUrl(value: String): HttpUrl {
        val parsed = value.trim().trimEnd('/').toHttpUrlOrNull()
            ?: error("请输入完整 API 地址，例如 https://api.example.com/v1")
        require(parsed.username.isEmpty() && parsed.password.isEmpty()) { "API 地址不能包含账号或密码" }
        require(parsed.query == null && parsed.fragment == null) { "API 地址不能包含查询参数或片段" }
        require(parsed.scheme == "https" || allowCleartextForTests) {
            "手机直连 API 必须使用 HTTPS，避免 API Key 被明文传输"
        }
        return parsed
    }

    private fun validateConfig(config: DirectApiConfig) {
        validateBaseUrl(config.baseUrl)
        require(config.apiKey.isNotBlank()) { "请填写 API Key" }
        require(config.model.isNotBlank()) { "请先自动获取或手动填写模型名" }
        require(config.protocol in DirectApiConfig.supportedProtocols) { "不支持的 API 协议" }
    }

    private fun ensureSuccess(response: RawResponse) {
        if (response.statusCode in 200..299) return
        val message = parseError(response.body)
        throw DirectApiHttpException(
            response.statusCode,
            when (response.statusCode) {
                401, 403 -> "API Key 无效或没有访问该模型的权限"
                429 -> "API 请求过于频繁或额度不足，请稍后重试"
                in TRANSIENT_STATUS_CODES -> "API 上游暂时不可用（HTTP ${response.statusCode}），系统已自动重试"
                else -> message ?: "API 请求失败（HTTP ${response.statusCode}）"
            },
        )
    }

    private fun parseModels(raw: String): List<String> {
        val root = json.parseToJsonElement(raw).jsonObject
        val candidates = (root["data"] as? JsonArray)
            ?: (root["models"] as? JsonArray)
            ?: JsonArray(emptyList())
        return candidates.mapNotNull { item ->
            when (item) {
                is JsonPrimitive -> item.contentOrNull
                is JsonObject -> listOf("id", "name", "model")
                    .firstNotNullOfOrNull { key -> (item[key] as? JsonPrimitive)?.contentOrNull }
                else -> null
            }
        }.map(String::trim).filter(String::isNotBlank).distinct().sorted()
    }

    private fun parseResponsesText(root: JsonObject): String {
        (root["output_text"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)?.let { return it }
        return (root["output"] as? JsonArray).orEmpty().flatMap { item ->
            val content = (item as? JsonObject)?.get("content") as? JsonArray ?: return@flatMap emptyList()
            content.mapNotNull { part ->
                val objectPart = part as? JsonObject ?: return@mapNotNull null
                (objectPart["text"] as? JsonPrimitive)?.contentOrNull
            }
        }.joinToString("").trim()
    }

    private fun parseChatText(root: JsonObject): String {
        val content = ((root["choices"] as? JsonArray)?.firstOrNull() as? JsonObject)
            ?.get("message")
            ?.let { it as? JsonObject }
            ?.get("content")
        return when (content) {
            is JsonPrimitive -> content.contentOrNull.orEmpty().trim()
            is JsonArray -> content.mapNotNull { part ->
                ((part as? JsonObject)?.get("text") as? JsonPrimitive)?.contentOrNull
            }.joinToString("").trim()
            else -> ""
        }
    }

    private fun parseError(raw: String): String? = runCatching {
        val root = json.parseToJsonElement(raw).jsonObject
        val error = root["error"]
        when (error) {
            is JsonPrimitive -> error.contentOrNull
            is JsonObject -> (error["message"] as? JsonPrimitive)?.contentOrNull
            else -> (root["message"] as? JsonPrimitive)?.contentOrNull
        }?.take(300)
    }.getOrNull()

    private fun Throwable.isProtocolMismatch(): Boolean =
        this is DirectApiHttpException && statusCode in setOf(400, 404, 405, 415, 422)

    private data class RawResponse(val statusCode: Int, val body: String)

    companion object {
        private const val STREAM_RESUME_ANCHOR_CHARS = 64
        private val RESPONSE_TEXT_DELTA_TYPES = setOf(
            "response.output_text.delta",
            "output_text.delta",
        )
        private val RESPONSE_REASONING_DELTA_TYPES = setOf(
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
            "response.reasoning.delta",
            "reasoning_summary_text.delta",
            "reasoning_text.delta",
        )
        private val RESPONSE_TERMINAL_TYPES = setOf(
            "response.completed",
            "response.incomplete",
            "response.failed",
        )
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val TRANSIENT_STATUS_CODES = setOf(500, 502, 503, 504)
        private val PATH_FALLBACK_STATUS_CODES = setOf(404, 405)
        private val TOOL_CHOICE_UNSUPPORTED_THINKING_PROVIDERS = setOf("deepseek", "gemini")
        private val INCOMPLETE_FINISH_REASONS = setOf(
            "length",
            "max_tokens",
            "token_limit",
            "incomplete",
            "failed",
        )
    }
}
