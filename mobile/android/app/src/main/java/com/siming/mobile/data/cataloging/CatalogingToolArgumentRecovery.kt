package com.siming.mobile.data.cataloging

import com.siming.mobile.data.network.DirectToolArgumentsException
import kotlinx.serialization.json.*

internal fun catalogingToolArgumentFailure(
    error: DirectToolArgumentsException, schemas: JsonArray, failures: Int, maxErrors: Int,
): List<JsonObject> {
    val calls = error.assistantMessage.objects("tool_calls")
    val available = schemas.map { it.jsonObject.obj("function").text("name") }.sorted()
    return listOf(error.assistantMessage) + calls.map { call ->
        val result = buildJsonObject {
            put("tool", call.obj("function").text("name")); put("status", "error")
            put("detail", "本批次 arguments JSON 无效，所有调用均未执行。请由模型按当前 Schema 重新提交完整 JSON 对象；不能把本批其他调用当作已成功。")
            put("data", buildJsonObject {
                put("reason", "native_tool_arguments_invalid"); put("executed", false)
                put("retryable", failures < maxErrors); put("available_tools", jsonStrings(available))
                put("batch_call_count", calls.size); put("validation_errors", JsonArray(error.validationErrors))
            })
        }
        buildJsonObject { put("role", "tool"); put("tool_call_id", call.text("id")); put("content", result.toString()) }
    }
}
