package com.siming.mobile.data.network

import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import okhttp3.Call
import okhttp3.Response
import okio.AsyncTimeout

/** Cancellation must interrupt a blocked socket read, not wait for it to complete. */
internal suspend fun <T> Call.withCancellableResponse(block: suspend (Response) -> T): T = coroutineScope {
    currentCoroutineContext().ensureActive()
    val finished = AtomicBoolean(false)
    val watcher = launch(start = CoroutineStart.UNDISPATCHED) {
        try { awaitCancellation() }
        finally { if (!finished.get()) this@withCancellableResponse.cancel() }
    }
    try {
        execute().use { response -> block(response) }
    } catch (error: IOException) {
        currentCoroutineContext().ensureActive()
        throw error
    } finally {
        finished.set(true)
        watcher.cancel()
    }
}

/** The PC gateway times out each wait for a meaningful model event, not the whole stream. */
internal class DirectStreamIdleTimeout(private val call: Call, val timeoutMillis: Long) {
    private val expired = AtomicBoolean(false)
    private val timer = object : AsyncTimeout() {
        override fun timedOut() {
            expired.set(true)
            call.cancel()
        }
    }.apply { timeout(timeoutMillis, TimeUnit.MILLISECONDS) }

    fun startWaiting() {
        if (expired.get()) throw failure()
        timer.enter()
    }

    fun stopWaiting() {
        if (stop()) throw failure()
    }

    fun stop(): Boolean {
        // exit() can report expiry before the watchdog callback runs.
        if (timer.exit()) expired.set(true)
        return expired.get()
    }

    fun failure(cause: IOException? = null) = DirectApiTimeoutException(
        DirectApiTimeoutException.Phase.STREAM_IDLE, timeoutMillis, cause,
    )
}

class DirectApiTimeoutException(
    val phase: Phase,
    val timeoutMillis: Long,
    cause: IOException? = null,
) : IOException(
    when (phase) {
        Phase.STREAM_IDLE -> "连续 ${(timeoutMillis + 999) / 1000} 秒未收到模型有效输出，本轮尚未完成，请重试"
        Phase.DEADLINE -> "本次模型请求达到 ${(timeoutMillis + 999) / 1000} 秒时限，尚未收到完整结果，请重试"
        Phase.RECEIVING -> "接收模型响应超时，尚未收到完整结果，请稍后重试"
        Phase.BEFORE_RESPONSE -> "API 请求超时，尚未收到服务响应，请稍后重试"
    }, cause,
) {
    enum class Phase { STREAM_IDLE, DEADLINE, RECEIVING, BEFORE_RESPONSE }
}

enum class DirectAgentStreamActivity { REASONING, CONTENT, TOOL_ARGUMENTS }
