package com.siming.mobile.data.network

import kotlinx.serialization.json.*

class DirectToolArgumentsException(
    val assistantMessage: JsonObject,
    val validationErrors: List<JsonObject>,
) : DirectNativeToolProtocolException("模型返回的工具参数 JSON 无效，整批未执行：" +
    validationErrors.joinToString("；") { it.getValue("message").jsonPrimitive.content })

/** Validate identities before retaining an unexecutable batch for same-model correction. */
internal fun validateCompletedNativeArguments(assistant: JsonObject) {
    val calls = (assistant["tool_calls"] as? JsonArray).orEmpty()
    val ids = mutableSetOf<String>()
    val errors = mutableListOf<JsonObject>()
    fun JsonObject.text(key: String) = (this[key] as? JsonPrimitive)?.contentOrNull.orEmpty()
    for (raw in calls) {
        val call = raw as? JsonObject ?: throw DirectNativeToolProtocolException("原生 tool_call 必须是对象")
        val function = call["function"] as? JsonObject ?: throw DirectNativeToolProtocolException("原生 tool_call 缺少 function 对象")
        val id = call.text("id"); val name = function.text("name")
        if (id.isBlank() || name.isBlank() || !ids.add(id)) {
            throw DirectNativeToolProtocolException("原生工具 call_id 或函数名无效，整批未执行")
        }
        val arguments = function["arguments"] as? JsonPrimitive
        if (arguments == null || !arguments.isString) throw DirectNativeToolProtocolException("arguments 必须是 JSON 字符串")
        if (runCatching { Json.parseToJsonElement(arguments.content) is JsonObject }.getOrDefault(false).not()) {
            errors += buildJsonObject {
                put("tool", name); put("call_id", id)
                put("message", "$name：arguments 必须是完整、有效的 JSON 对象")
            }
        }
    }
    if (errors.isNotEmpty()) throw DirectToolArgumentsException(assistant, errors)
}
