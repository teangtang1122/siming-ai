package com.siming.mobile.data

import com.siming.mobile.data.network.GatewayHttpException
import com.siming.mobile.data.network.DirectApiHttpException
import com.siming.mobile.data.network.DirectApiTimeoutException
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlinx.serialization.SerializationException

internal fun Throwable.toUserFacingMessage(): String = when (this) {
    is DirectApiTimeoutException -> requireNotNull(message)
    is DirectApiHttpException -> message?.takeIf(String::isNotBlank) ?: "API 请求没有完成，请稍后重试"
    is GatewayHttpException -> message.ifBlank { "Gateway 请求没有完成，请稍后重试" }
    is UnknownHostException -> "找不到服务器，请检查 API 或 Gateway 地址后重试"
    is ConnectException -> "无法连接服务器，请确认网络和服务运行状态后重试"
    is SocketTimeoutException -> "请求超时，未收到完整响应，请稍后重试"
    is IOException -> "网络通信失败，请检查连接后重试"
    is SerializationException -> "模型返回的数据格式异常，自动修复未完成，请重试或切换模型"
    else -> message?.takeIf(String::isNotBlank)?.let { raw ->
        if (raw.contains("Unexpected JSON token") || raw.contains("Expected quotation mark")) {
            "模型返回的数据格式异常，自动修复未完成，请重试或切换模型"
        } else raw
    } ?: "操作没有完成，请重试"
}
