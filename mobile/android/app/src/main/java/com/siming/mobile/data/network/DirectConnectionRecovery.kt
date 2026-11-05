package com.siming.mobile.data.network

import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import okhttp3.Call
import okhttp3.EventListener

/** Origin request headers, including a reused connection, close the replay boundary. */
internal class DirectConnectionAttempt : EventListener() {
    val requestStarted = AtomicBoolean(false)
    val cancelled = AtomicBoolean(false)
    override fun requestHeadersStart(call: Call) { requestStarted.set(true) }
    override fun canceled(call: Call) { cancelled.set(true) }
}

internal class DirectApiConnectionException(cause: IOException) : IOException(
    "连接模型服务失败，尚未收到响应；请检查代理和网络后重试。", cause,
)

/** Exactly one retry before origin headers; never replay a transmitted request here. */
internal suspend fun <T> recoverDirectConnection(
    retryDelayMillis: Long = 700,
    block: suspend (DirectConnectionAttempt) -> T,
): T {
    repeat(2) { index ->
        val attempt = DirectConnectionAttempt()
        try { return block(attempt) }
        catch (error: IOException) {
            currentCoroutineContext().ensureActive()
            if (attempt.requestStarted.get() || attempt.cancelled.get()) throw error
            if (index == 1) throw DirectApiConnectionException(error)
        }
        delay(retryDelayMillis)
    }
    error("连接恢复未产生结果")
}
