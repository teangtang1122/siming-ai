package com.siming.mobile.data.network

import kotlinx.serialization.json.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Uses the generated PC contract after the actual wire protocol is resolved. */
internal object ModelRequestPolicy {
    private val definition = Json.parseToJsonElement(MODEL_REQUEST_POLICY_JSON).jsonObject
    private val endpoints = definition.getValue("endpoints").jsonObject
    private val explicitThinkingFields = definition.getValue("explicit_thinking_fields")
        .jsonArray.map { it.jsonPrimitive.content }.toSet()

    private fun endpoint(config: DirectApiConfig, protocol: String): JsonObject? {
        val url = config.baseUrl.trim().toHttpUrlOrNull() ?: return null
        if (!url.isHttps || url.port != 443) return null
        return endpoints[url.host.removeSuffix(".")]?.jsonObject?.get(protocol)?.jsonObject
    }

    private val configurable = definition.getValue("configurable_parameters").jsonArray.map { it.jsonPrimitive.content }.toSet()

    fun validateParameters(profiles: Map<String, JsonObject>) {
        require(profiles.size <= 5000 && profiles.keys.all { it.isNotBlank() && it == it.trim() && it.length <= 512 }) { "请求参数的模型 ID 无效" }
        require(profiles.values.all { it.keys.all { key -> key in configurable } }) {
            "请求参数包含不支持的字段；模型、消息、工具、密钥及输出额度由专用设置管理"
        }
        require(JsonObject(profiles).toString().toByteArray().size <= 65536) { "模型请求参数过大" }
    }

    /** Applied after all overrides, before both budget accounting and transport. */
    fun normalize(config: DirectApiConfig, protocol: String, payload: JsonObject): JsonObject {
        val configured = config.modelRequestParameters[config.model.trim()].orEmpty()
        val values = payload.filterNot { (key, value) -> key in configurable && value == JsonNull }.toMutableMap()
        configured.forEach { (key, value) -> if (value == JsonNull) values.remove(key) else values[key] = value }
        val request = JsonObject(values)
        val url = config.baseUrl.trim().toHttpUrlOrNull() ?: return request
        if (!url.isHttps || url.port != 443) return request
        val rule = definition["model_capabilities"]?.jsonObject?.get(url.host.removeSuffix("."))
            ?.jsonObject?.get(config.model.trim())?.jsonObject ?: return request
        if (rule["kind"]?.jsonPrimitive?.content == "anthropic") return request
        val wrongField = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) "reasoning_effort" else "reasoning"
        require(wrongField !in request) { "model_request_incompatible: $protocol 不接受 $wrongField，请使用该协议的推理字段" }
        val effort = if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
            val reasoning = request["reasoning"]
            require(reasoning == null || reasoning is JsonObject) { "model_request_incompatible: reasoning 必须是对象" }
            (reasoning as? JsonObject)?.get("effort")?.jsonPrimitive?.content
        } else request["reasoning_effort"]?.jsonPrimitive?.content
        val effective = effort ?: rule.getValue("default_reasoning_effort").jsonPrimitive.content
        require(effective in rule.getValue("reasoning_efforts").jsonArray.map { it.jsonPrimitive.content }) {
            "model_request_incompatible: 当前模型不支持推理档位 $effective"
        }
        val tools = (request["tools"] as? JsonArray).orEmpty()
        require(protocol != DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS || tools.isEmpty() ||
            (rule.getValue("chat_tools").jsonPrimitive.content != "unavailable" && effective == "none")) {
            "model_request_incompatible: 当前模型及推理设置的工具调用需要 Responses API；请调整协议，推理设置未更改"
        }
        if (effective == "none") return request
        require(configured.none { (key, value) -> value != JsonNull && key in setOf("temperature", "top_p", "top_logprobs", "logprobs") }) {
            "model_request_incompatible: 自定义采样参数与当前推理档位不兼容；请用 null 省略该参数，或显式调整推理设置"
        }
        val removed = setOf("temperature", "top_p", "top_logprobs") +
            if (protocol == DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS) setOf("logprobs") else emptySet()
        val normalized = request.filterKeys { it !in removed }.toMutableMap()
        if (protocol == DirectApiConfig.PROTOCOL_RESPONSES && normalized["include"] is JsonArray) {
            normalized["include"] = JsonArray(normalized.getValue("include").jsonArray.filter {
                it != JsonPrimitive("message.output_text.logprobs")
            })
        }
        return JsonObject(normalized)
    }

    fun outputLimitField(config: DirectApiConfig): String =
        endpoint(config, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS)?.get("output_limit_field")?.jsonPrimitive?.content ?: "max_tokens"

    fun toolChoice(config: DirectApiConfig, requested: String?): String? =
        requested.takeUnless {
            endpoint(config, config.protocol)?.get("supports_tool_choice")?.jsonPrimitive?.boolean == false
        }

    fun extraBody(
        config: DirectApiConfig,
        protocol: String,
        extraBody: JsonObject?,
        textOnly: Boolean,
    ): JsonObject? {
        val internal = definition.getValue("internal_parameters").jsonArray.map { it.jsonPrimitive.content }.toSet()
        val payload = (extraBody.orEmpty() + config.modelRequestParameters[config.model.trim()].orEmpty()).filterKeys {
            it !in internal && !it.startsWith("moshu_") && !it.startsWith("local_cli_")
        }
        val mode = if (extraBody?.get("moshu_task_type")?.jsonPrimitive?.content == "cataloging") {
            "cataloging"
        } else if (textOnly) "text" else "tools"
        val policy = endpoint(config, protocol)
        val modes = policy?.get("non_thinking_modes")?.jsonArray.orEmpty().map { it.jsonPrimitive.content }
        val defaults = if (mode in modes && payload.keys.none { it in explicitThinkingFields }) {
            policy?.get("non_thinking_body")?.jsonObject.orEmpty()
        } else emptyMap()
        return (defaults + payload).takeIf { it.isNotEmpty() }?.let(::JsonObject)
    }
}
