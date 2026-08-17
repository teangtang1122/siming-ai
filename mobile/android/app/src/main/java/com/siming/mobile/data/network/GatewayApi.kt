package com.siming.mobile.data.network

import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.security.PairingSecurity
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class GatewayHttpException(val status: Int, override val message: String) : IOException(message)

@OptIn(ExperimentalSerializationApi::class)
class GatewayApi(private val tokenStore: SecureTokenStore) {
    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.MINUTES)
        .writeTimeout(60, TimeUnit.SECONDS)
        .callTimeout(12, TimeUnit.MINUTES)
        .build()

    suspend fun completePairing(
        pairing: com.siming.mobile.security.VerifiedPairing,
        deviceName: String,
        publicKey: String,
    ): PairingCompleteResponse = request<ApiEnvelope<PairingCompleteResponse>>(
        baseUrl = pairing.gatewayUrl,
        path = PcApiPaths.PAIRING_COMPLETE,
        method = "POST",
        body = json.encodeToString(
            PairingCompleteRequest(
                pairingId = pairing.pairingId,
                pairingSecret = pairing.pairingSecret,
                deviceName = deviceName,
                publicKey = publicKey,
            ),
        ),
        authorized = false,
    ).data

    suspend fun listSyncProjects(connection: GatewayConnection): List<RemoteSyncProject> =
        request<ApiEnvelope<List<RemoteSyncProject>>>(
            connection.baseUrl,
            PcApiPaths.SYNC_PROJECTS,
        ).data

    suspend fun bootstrap(
        connection: GatewayConnection,
        projectIds: List<String>,
    ): SyncBootstrapResponse = request<ApiEnvelope<SyncBootstrapResponse>>(
        connection.baseUrl,
        PcApiPaths.SYNC_BOOTSTRAP,
        "POST",
        json.encodeToString(SyncBootstrapRequest(projectIds = projectIds)),
    ).data

    suspend fun push(
        connection: GatewayConnection,
        mutations: List<SyncMutationRequest>,
    ): SyncPushResponse = request<ApiEnvelope<SyncPushResponse>>(
        connection.baseUrl,
        PcApiPaths.SYNC_PUSH,
        "POST",
        json.encodeToString(SyncPushRequest(mutations = mutations)),
    ).data

    suspend fun pull(
        connection: GatewayConnection,
        cursor: Long,
        projectIds: List<String>,
        limit: Int = 200,
    ): SyncPullResponse {
        val url = (connection.baseUrl + PcApiPaths.SYNC_PULL).toHttpUrl().newBuilder()
            .addQueryParameter("cursor", cursor.toString())
            .addQueryParameter("limit", limit.toString())
            .addQueryParameter("protocol_version", "1")
            .apply { projectIds.forEach { addQueryParameter("project_id", it) } }
            .build()
            .toString()
        return request<ApiEnvelope<SyncPullResponse>>("", url, absolutePath = true).data
    }

    suspend fun listConflicts(connection: GatewayConnection): List<RemoteConflict> =
        request<ApiEnvelope<List<RemoteConflict>>>(
            connection.baseUrl,
            "${PcApiPaths.SYNC_CONFLICTS}?status=open",
        ).data

    suspend fun resolveConflict(
        connection: GatewayConnection,
        conflictId: String,
        choice: String,
    ): RemoteConflict = request<ApiEnvelope<RemoteConflict>>(
        connection.baseUrl,
        PcApiPaths.conflictResolution(conflictId),
        "POST",
        json.encodeToString(ConflictResolutionRequest(choice)),
    ).data

    suspend fun revokeSelf(connection: GatewayConnection) {
        request<ApiEnvelope<kotlinx.serialization.json.JsonElement>>(
            connection.baseUrl,
            PcApiPaths.DEVICES_ME,
            "DELETE",
        )
    }

    /** Create a work through the exact endpoint used by the PC frontend. */
    suspend fun createProject(
        connection: GatewayConnection,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.PROJECTS,
        method = "POST",
        payload = payload,
    )

    /**
     * Create or update one core authoring record through the canonical PC API.
     * This preserves server validation, snapshots, cataloging, governance, and
     * all other business side effects that raw sync projection cannot emulate.
     */
    suspend fun saveAuthoringEntity(
        connection: GatewayConnection,
        projectId: String,
        entityType: String,
        entityId: String,
        create: Boolean,
        payload: JsonObject,
    ): JsonObject {
        val path = if (entityType == "project") {
            PcApiPaths.project(projectId)
        } else if (create) {
            PcApiPaths.authoringCollection(projectId, entityType)
        } else {
            PcApiPaths.authoringItem(projectId, entityType, entityId)
        }
        return canonicalWrite(
            connection = connection,
            path = path,
            method = if (create) "POST" else "PUT",
            payload = payload,
        )
    }

    suspend fun deleteAuthoringEntity(
        connection: GatewayConnection,
        projectId: String,
        entityType: String,
        entityId: String,
    ) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.authoringItem(projectId, entityType, entityId),
            "DELETE",
        )
    }

    suspend fun saveGovernanceEntity(
        connection: GatewayConnection,
        projectId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.narrativeGovernanceItems(projectId),
        method = "POST",
        payload = payload,
    )

    suspend fun updateGovernanceStatus(
        connection: GatewayConnection,
        projectId: String,
        itemType: String,
        itemId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.narrativeGovernanceStatus(projectId, itemType, itemId),
        method = "PATCH",
        payload = payload,
    )

    suspend fun listNovelCreationSessions(connection: GatewayConnection): List<JsonObject> {
        val data = request<ApiEnvelope<JsonObject>>(
            connection.baseUrl,
            PcApiPaths.NOVEL_CREATION_SESSIONS,
        ).data
        return (data["sessions"] as? kotlinx.serialization.json.JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }
    }

    suspend fun startNovelCreation(
        connection: GatewayConnection,
        payload: JsonObject,
    ): JsonObject {
        val data = request<ApiEnvelope<JsonObject>>(
            connection.baseUrl,
            PcApiPaths.NOVEL_CREATION_START,
            "POST",
            json.encodeToString(payload),
        ).data
        return data["session"] as? JsonObject
            ?: throw GatewayHttpException(502, "PC 立项 API 没有返回 V3 会话")
    }

    suspend fun getNovelCreationSession(
        connection: GatewayConnection,
        sessionId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.novelCreationSession(sessionId),
    ).data

    suspend fun novelCreationAgentTurn(
        connection: GatewayConnection,
        payload: JsonObject,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.NOVEL_CREATION_AGENT_TURN,
        "POST",
        json.encodeToString(payload),
    ).data

    suspend fun startNovelCreationRun(
        connection: GatewayConnection,
        sessionId: String,
        payload: JsonObject,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.novelCreationRuns(sessionId),
        "POST",
        json.encodeToString(payload),
    ).data

    suspend fun getNovelCreationRun(
        connection: GatewayConnection,
        runId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.novelCreationRun(runId),
    ).data

    suspend fun confirmNovelCreationStage(
        connection: GatewayConnection,
        sessionId: String,
        stage: String,
        payload: JsonObject,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.novelCreationStageConfirm(sessionId, stage),
        "POST",
        json.encodeToString(payload),
    ).data

    suspend fun updateNovelCreationStage(
        connection: GatewayConnection,
        sessionId: String,
        stage: String,
        payload: JsonObject,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.novelCreationStage(sessionId, stage),
        "PATCH",
        json.encodeToString(payload),
    ).data

    suspend fun deleteNovelCreationSession(
        connection: GatewayConnection,
        sessionId: String,
    ) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.novelCreationSession(sessionId),
            "DELETE",
        )
    }

    suspend fun applyNovelCreation(
        connection: GatewayConnection,
        sessionId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.NOVEL_CREATION_APPLY,
        "POST",
        json.encodeToString(
            buildJsonObject {
                put("session_id", sessionId)
                put("mode", "auto")
            },
        ),
    ).data

    private suspend fun canonicalWrite(
        connection: GatewayConnection,
        path: String,
        method: String,
        payload: JsonObject,
    ): JsonObject {
        val response = request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            path,
            method,
            json.encodeToString(payload),
        )
        return response.data as? JsonObject
            ?: throw GatewayHttpException(502, "PC API 返回的数据结构无效")
    }

    suspend fun streamAssistant(
        connection: GatewayConnection,
        projectId: String,
        requestBody: WorkspaceAssistantRequest,
        onEvent: suspend (String) -> Unit,
    ) = withContext(Dispatchers.IO) {
        var token = validAccessToken(connection.baseUrl)
        repeat(2) { attempt ->
            val request = Request.Builder()
                .url(connection.baseUrl + PcApiPaths.assistantStream(projectId))
                .header("Authorization", "Bearer $token")
                .header("Accept", "text/event-stream")
                .post(json.encodeToString(requestBody).toRequestBody(JSON_MEDIA_TYPE))
                .build()
            client.newCall(request).execute().use { response ->
                if (response.code == 401 && attempt == 0) {
                    response.body?.close()
                    token = refresh(connection.baseUrl, token)
                    return@use
                }
                if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
                val source = response.body?.source() ?: throw IOException("AI 响应为空")
                while (!source.exhausted()) {
                    val line = source.readUtf8Line() ?: break
                    if (line.startsWith("data:")) {
                        val data = line.removePrefix("data:").trim()
                        if (data.isNotEmpty() && data != "[DONE]") onEvent(data)
                    }
                }
                return@withContext
            }
        }
        throw GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
    }

    private suspend fun validAccessToken(baseUrl: String): String {
        val current = tokenStore.read() ?: throw GatewayHttpException(401, "设备尚未配对")
        val stillValid = runCatching {
            Instant.parse(current.accessExpiresAt).isAfter(Instant.now().plusSeconds(30))
        }.getOrDefault(false)
        return if (stillValid) current.accessToken else refresh(baseUrl, current.accessToken)
    }

    private suspend inline fun <reified T> request(
        baseUrl: String,
        path: String,
        method: String = "GET",
        body: String? = null,
        authorized: Boolean = true,
        absolutePath: Boolean = false,
    ): T {
        PairingSecurity.validateGatewayUrl(if (absolutePath) URIBase(path) else baseUrl)
        val attemptedToken = if (authorized) tokenStore.read()?.accessToken else null
        val first = execute(baseUrl, path, method, body, attemptedToken, absolutePath)
        if (first.status != 401 || !authorized) {
            if (first.status !in 200..299) throw errorFrom(first.status, first.body)
            return json.decodeFromString(first.body)
        }
        val refreshed = refresh(baseUrl, attemptedToken)
        val retried = execute(baseUrl, path, method, body, refreshed, absolutePath)
        if (retried.status !in 200..299) throw errorFrom(retried.status, retried.body)
        return json.decodeFromString(retried.body)
    }

    private suspend fun refresh(baseUrl: String, attemptedAccessToken: String?): String =
        refreshMutex.withLock {
            val current = tokenStore.read() ?: throw GatewayHttpException(401, "设备授权已失效")
            if (attemptedAccessToken != null && current.accessToken != attemptedAccessToken) {
                return@withLock current.accessToken
            }
            val response = execute(
                baseUrl,
                PcApiPaths.AUTH_REFRESH,
                "POST",
                json.encodeToString(RefreshRequest(current.refreshToken)),
                null,
                false,
            )
            if (response.status !in 200..299) {
                tokenStore.clear()
                throw errorFrom(response.status, response.body)
            }
            val tokens = json.decodeFromString<ApiEnvelope<TokenPair>>(response.body).data
            tokenStore.save(
                StoredTokenPair(
                    tokens.accessToken,
                    tokens.accessExpiresAt,
                    tokens.refreshToken,
                    tokens.refreshExpiresAt,
                ),
            )
            tokens.accessToken
        }

    private suspend fun execute(
        baseUrl: String,
        path: String,
        method: String,
        body: String?,
        token: String?,
        absolutePath: Boolean,
    ): RawResponse = withContext(Dispatchers.IO) {
        val url = if (absolutePath) path else baseUrl.trimEnd('/') + path
        val builder = Request.Builder().url(url).header("Accept", "application/json")
        if (token != null) builder.header("Authorization", "Bearer $token")
        val requestBody = body?.toRequestBody(JSON_MEDIA_TYPE)
        when (method) {
            "GET" -> builder.get()
            "POST" -> builder.post(requestBody ?: EMPTY_BODY)
            "PUT" -> builder.put(requestBody ?: EMPTY_BODY)
            "PATCH" -> builder.patch(requestBody ?: EMPTY_BODY)
            "DELETE" -> builder.delete(requestBody)
            else -> error("Unsupported method")
        }
        client.newCall(builder.build()).execute().use { response ->
            RawResponse(response.code, response.body?.string().orEmpty())
        }
    }

    private fun errorFrom(status: Int, raw: String?): GatewayHttpException {
        val message = runCatching {
            json.parseToJsonElement(raw.orEmpty()).let { element ->
                (element as? JsonObject)
                    ?.get("message")
                    ?.let { it as? JsonPrimitive }
                    ?.content
            }
        }.getOrNull() ?: "Gateway 请求失败（HTTP $status）"
        return GatewayHttpException(status, message)
    }

    private fun URIBase(value: String): String {
        val parsed = value.toHttpUrl()
        return parsed.newBuilder().encodedPath("/").query(null).build().toString().trimEnd('/')
    }

    private data class RawResponse(val status: Int, val body: String)

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val EMPTY_BODY = ByteArray(0).toRequestBody(JSON_MEDIA_TYPE)
        private val refreshMutex = Mutex()
    }
}
