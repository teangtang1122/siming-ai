package com.siming.mobile.data.cataloging

import com.siming.mobile.data.network.DirectAgentStreamActivity
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import kotlinx.serialization.json.*

/** Model settings are exported from the same PC cataloging contract as the tools. */
internal class CatalogingModelRequest(definition: JsonObject) {
    val idleTimeoutSeconds = definition.number("stream_idle_timeout_seconds")
    private val maxOutputTokens = definition.number("max_output_tokens")
    private val temperature = definition.getValue("temperature").jsonPrimitive.double
    private val nonThinkingProviders = definition.strings("non_thinking_providers")

    init {
        require(idleTimeoutSeconds > 0 && maxOutputTokens > 0) { "建档模型调用契约无效" }
    }

    suspend fun execute(
        api: DirectApiClient,
        config: DirectApiConfig,
        messages: List<JsonObject>,
        tools: JsonArray,
        onActivity: suspend (DirectAgentStreamActivity) -> Unit,
    ) = api.streamAgentTurn(
        config = config,
        messages = messages,
        tools = tools,
        toolChoice = "auto",
        temperature = temperature,
        maxOutputTokens = minOf(config.maxOutputTokens, maxOutputTokens),
        extraBody = if ("deepseek" in nonThinkingProviders && config.isDeepSeekProvider()) {
            // The two provider protocols encode the same non-thinking policy differently.
            if (config.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                buildJsonObject { put("reasoning", buildJsonObject { put("effort", "none") }) }
            } else {
                buildJsonObject { put("thinking", buildJsonObject { put("type", "disabled") }) }
            }
        } else null,
        streamIdleTimeoutMillis = idleTimeoutSeconds * 1000L,
        onActivity = onActivity,
    )
}
