package com.siming.mobile.data.creation

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import kotlinx.serialization.json.*

/** Generation and repair share the PC request budget and validate a completed stream. */
internal class CreationModelRequest(private val definition: JsonObject) {
    suspend fun execute(
        api: DirectApiClient,
        config: DirectApiConfig,
        stage: String,
        system: String,
        user: String,
        repair: Boolean = false,
        onProgress: suspend (String) -> Unit = {},
    ): String {
        val concept = stage == "concepts"
        val maxTokens = if (concept) minOf(config.maxOutputTokens,
            definition.getValue("concept_max_output_tokens").jsonPrimitive.int) else config.maxOutputTokens
        val temperature = if (repair) 0.0 else definition.getValue(
            if (concept) "concept_temperature" else "stage_temperature").jsonPrimitive.double
        var outputChars = 0
        var reportedChars = 0
        val result = api.streamAgentTurn(
            config = config,
            messages = listOf(
                buildJsonObject { put("role", "system"); put("content", system) },
                buildJsonObject { put("role", "user"); put("content", user) },
            ),
            tools = JsonArray(emptyList()),
            maxOutputTokens = maxTokens,
            temperature = temperature,
            extraBody = if (config.isDeepSeekProvider()) buildJsonObject {
                if (config.protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                    put("reasoning", buildJsonObject { put("effort", "none") })
                } else {
                    put("thinking", buildJsonObject { put("type", "disabled") })
                }
            } else null,
            streamIdleTimeoutMillis = definition.getValue("stream_idle_timeout_seconds").jsonPrimitive.long * 1_000,
            onContentDelta = { delta ->
                outputChars += delta.length
                if (outputChars - reportedChars >= 200) {
                    reportedChars = outputChars
                    onProgress("${if (repair) "正在修复资料结构" else "正在生成资料"} · 已接收 $outputChars 字")
                }
            },
        )
        require(result.toolCalls.isEmpty()) { "阶段生成不能返回工具调用，原资料未修改" }
        require(result.content.isNotBlank()) { "模型没有返回阶段内容，原资料未修改" }
        return result.content
    }
}
