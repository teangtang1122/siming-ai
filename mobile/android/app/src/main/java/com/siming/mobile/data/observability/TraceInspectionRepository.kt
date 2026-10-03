package com.siming.mobile.data.observability

import android.content.Context
import java.io.OutputStream
import com.siming.mobile.BuildConfig
import com.siming.mobile.data.local.SimingDatabase
import com.siming.mobile.data.network.GatewayApi
import com.siming.mobile.security.SecureTokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

/** Explicit local/Gateway source selection; no automatic source fallback or PC UI dependency. */
internal class TraceInspectionRepository(context: Context) {
    private val store = ContextTraceStore.get(context)
    private val dao = SimingDatabase.get(context.applicationContext).dao()
    private val api = GatewayApi(SecureTokenStore(context.applicationContext))
    private suspend fun connection() = requireNotNull(dao.connection()) { "尚未连接 Gateway" }
    suspend fun hasGateway() = dao.connection() != null

    suspend fun settings(remote: Boolean): JsonObject = if (remote) api.contextTraceRequest(connection(), "settings") else buildJsonObject {
        put("policy", store.policy())
        put("dropped_events", store.dropped.get()); put("write_errors", store.writeErrors.get())
    }
    suspend fun setMode(remote: Boolean, mode: String, timed: Boolean) {
        if (remote) api.contextTraceRequest(connection(), "settings", "PUT", buildJsonObject {
            put("mode", mode); put("duration_minutes", if (timed) JsonPrimitive(60) else JsonNull)
        }) else store.setMode(mode, timed)
    }
    suspend fun traces(remote: Boolean, kind: String?, scopeId: String?, correlationId: String?, before: Long?): JsonObject {
        if (remote) return api.contextTraceRequest(connection(), "search", "POST", buildJsonObject {
            if (kind != null && scopeId != null) put("scope", buildJsonObject { put("kind", kind); put("id", scopeId) })
            correlationId?.let { put("correlation_id", it) }; before?.let { put("before", it) }
        })
        val items = store.traces(kind, scopeId, correlationId, before)
        return buildJsonObject {
            put("items", JsonArray(items.map(::traceJson)))
            put("next_cursor", if (items.size == 30) JsonPrimitive(items.last().cursor) else JsonNull)
        }
    }
    suspend fun events(remote: Boolean, id: String, after: Int): List<JsonObject> =
        if (remote) api.contextTraceRequest(connection(), "$id/events?after=$after")["items"]!!.jsonArray.map { it.jsonObject }
        else store.events(id, after)
    suspend fun trace(remote: Boolean, id: String): JsonObject =
        if (remote) api.contextTraceRequest(connection(), id)
        else traceJson(requireNotNull(store.trace(id)) { "记录已清理" })
    suspend fun payload(remote: Boolean, id: String, payloadId: String, offset: Int): JsonObject =
        if (remote) api.contextTraceRequest(connection(), "$id/payloads/$payloadId?offset=$offset") else store.payload(id, payloadId, offset)
    suspend fun clear(remote: Boolean) { if (remote) api.contextTraceRequest(connection(), "", "DELETE") else store.clear() }

    suspend fun export(remote: Boolean, id: String, output: OutputStream) {
        if (remote) { api.downloadContextTrace(connection(), id, output); return }
        val trace = requireNotNull(store.trace(id)) { "记录已清理" }
        require(trace.finished != null) { "任务尚未结束，请结束后再导出" }
        withContext(Dispatchers.IO) {
            writeTraceArchive(output, traceJson(trace), BuildConfig.VERSION_NAME,
                events = { after -> store.events(id, after) },
                payload = { payloadId, offset -> store.payload(id, payloadId, offset) },
                stillExists = { store.trace(id) != null })
        }
    }
}

internal fun traceJson(row: TraceRow) = buildJsonObject {
    put("origin", "android")
    put("schema", TRACE_SCHEMA); put("id", row.id); put("cursor", row.cursor)
    put("scope_kind", row.scopeKind); put("scope_id", row.scopeId)
    put("started", row.started); put("finished", row.finished?.let(::JsonPrimitive) ?: JsonNull)
    put("status", row.status); put("mode", row.mode); put("capture_status", row.captureStatus)
    put("correlations", Json.parseToJsonElement(row.correlations))
}
