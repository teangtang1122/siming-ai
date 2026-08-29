package com.siming.mobile.data.network

import com.siming.mobile.data.PROJECT_PACKAGE_MEDIA_TYPE
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.security.PairingSecurity
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import java.io.IOException
import java.io.File
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.RequestBody.Companion.asRequestBody

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

    suspend fun pendingChapterDraft(
        connection: GatewayConnection,
        projectId: String,
    ): JsonObject? {
        val response = request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.pendingChapterDraft(projectId),
        )
        return response.data as? JsonObject
    }

    suspend fun pendingOutlineDraft(
        connection: GatewayConnection,
        projectId: String,
    ): JsonObject? {
        val response = request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.pendingOutlineDraft(projectId),
        )
        return response.data as? JsonObject
    }

    suspend fun updateOutlineDraft(
        connection: GatewayConnection,
        projectId: String,
        draftId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection,
        PcApiPaths.outlineDraft(projectId, draftId),
        "PUT",
        payload,
    )

    suspend fun confirmOutlineDraft(
        connection: GatewayConnection,
        projectId: String,
        draftId: String,
        writeAfterConfirm: Boolean,
    ): JsonObject = canonicalWrite(
        connection,
        PcApiPaths.confirmOutlineDraft(projectId, draftId),
        "POST",
        buildJsonObject { put("write_after_confirm", writeAfterConfirm) },
    )

    suspend fun regenerateOutlineDraft(
        connection: GatewayConnection,
        projectId: String,
        draftId: String,
    ): JsonObject = canonicalWrite(
        connection,
        PcApiPaths.regenerateOutlineDraft(projectId, draftId),
        "POST",
        JsonObject(emptyMap()),
    )

    suspend fun discardOutlineDraft(
        connection: GatewayConnection,
        projectId: String,
        draftId: String,
    ): JsonObject {
        val response = request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.outlineDraft(projectId, draftId),
            "DELETE",
        )
        return response.data as? JsonObject
            ?: throw GatewayHttpException(502, "PC API 返回的大纲草稿结构无效")
    }

    suspend fun saveGeneratedChapter(
        connection: GatewayConnection,
        projectId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.authoringCollection(projectId, "chapter"),
        method = "POST",
        payload = payload,
    )

    suspend fun assistantConversations(
        connection: GatewayConnection,
        projectId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.assistantConversations(projectId),
    ).data

    suspend fun assistantConversation(
        connection: GatewayConnection,
        projectId: String,
        conversationId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.assistantConversation(projectId, conversationId),
    ).data

    suspend fun assistantRun(
        connection: GatewayConnection,
        projectId: String,
        runId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.assistantRun(projectId, runId),
    ).data

    suspend fun cancelAssistantRun(
        connection: GatewayConnection,
        projectId: String,
        runId: String,
    ) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.assistantRunCancel(projectId, runId),
            "POST",
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

    suspend fun importNovelProject(
        connection: GatewayConnection,
        filename: String,
        bytes: ByteArray,
    ): JsonObject = withContext(Dispatchers.IO) {
        var token = validAccessToken(connection.baseUrl)
        repeat(2) { attempt ->
            val multipart = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "file",
                    filename,
                    bytes.toRequestBody(BINARY_MEDIA_TYPE),
                )
                .build()
            val request = Request.Builder()
                .url(connection.baseUrl + PcApiPaths.IMPORT_PROJECT_FILE)
                .header("Authorization", "Bearer $token")
                .header("Accept", "application/json")
                .post(multipart)
                .build()
            client.newCall(request).execute().use { response ->
                if (response.code == 401 && attempt == 0) {
                    response.body?.close()
                    token = refresh(connection.baseUrl, token)
                    return@use
                }
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw errorFrom(response.code, raw)
                return@withContext json.decodeFromString<ApiEnvelope<JsonObject>>(raw).data
            }
        }
        throw GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
    }

    suspend fun importProjectPackage(
        connection: GatewayConnection,
        filename: String,
        file: File,
        idempotencyKey: String,
        newTitle: String?,
    ): JsonObject = withContext(Dispatchers.IO) {
        var token = validAccessToken(connection.baseUrl)
        repeat(2) { attempt ->
            val multipart = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "file",
                    filename,
                    file.asRequestBody(PROJECT_PACKAGE_MEDIA_TYPE.toMediaType()),
                )
                .apply {
                    newTitle?.trim()?.takeIf(String::isNotBlank)?.let { addFormDataPart("new_title", it) }
                }
                .build()
            val request = Request.Builder()
                .url(connection.baseUrl + PcApiPaths.PROJECT_PACKAGE_IMPORT)
                .header("Authorization", "Bearer $token")
                .header("Accept", "application/json")
                .header("Idempotency-Key", idempotencyKey)
                .post(multipart)
                .build()
            client.newCall(request).execute().use { response ->
                if (response.code == 401 && attempt == 0) {
                    response.body?.close()
                    token = refresh(connection.baseUrl, token)
                    return@use
                }
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw errorFrom(response.code, raw)
                return@withContext json.decodeFromString<ApiEnvelope<JsonObject>>(raw).data
            }
        }
        throw GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
    }

    suspend fun downloadProjectPackage(
        connection: GatewayConnection,
        projectId: String,
        profile: String,
        destination: File,
    ): String? = withContext(Dispatchers.IO) {
        var token = validAccessToken(connection.baseUrl)
        try {
            repeat(2) { attempt ->
                val request = Request.Builder()
                    .url(connection.baseUrl + PcApiPaths.projectPackageExport(projectId, profile))
                    .header("Authorization", "Bearer $token")
                    .header("Accept", PROJECT_PACKAGE_MEDIA_TYPE)
                    .post(EMPTY_BODY)
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.code == 401 && attempt == 0) {
                        response.body?.close()
                        token = refresh(connection.baseUrl, token)
                        return@use
                    }
                    if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
                    val body = response.body ?: throw IOException("项目包导出响应为空")
                    destination.outputStream().buffered().use { output ->
                        body.byteStream().buffered().use { input -> input.copyTo(output, 1024 * 1024) }
                    }
                    if (destination.length() == 0L) throw IOException("项目包导出响应为空")
                    return@withContext response.header("Content-Disposition")
                        ?.substringAfter("filename*=UTF-8''", "")
                        ?.takeIf(String::isNotBlank)
                        ?: response.header("Content-Disposition")
                            ?.substringAfter("filename=", "")
                            ?.trim(' ', '"')
                            ?.takeIf(String::isNotBlank)
                }
            }
            throw GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
        } catch (error: Exception) {
            destination.delete()
            throw error
        }
    }

    suspend fun deleteProject(connection: GatewayConnection, projectId: String) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.project(projectId),
            "DELETE",
        )
    }

    suspend fun reorderOutline(
        connection: GatewayConnection,
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.outlineReorder(projectId),
        method = "PUT",
        payload = buildJsonObject {
            put(
                "items",
                JsonArray(
                    nodeIds.mapIndexed { index, nodeId ->
                        buildJsonObject {
                            put("id", nodeId)
                            put("parent_id", parentId?.let(::JsonPrimitive) ?: JsonNull)
                            put("sort_order", index)
                        }
                    },
                ),
            )
        },
    )


suspend fun startCataloging(
    connection: GatewayConnection,
    projectId: String,
    chapterIds: List<String>,
): JsonObject = request<ApiEnvelope<JsonObject>>(
    connection.baseUrl,
    PcApiPaths.catalogingStart(projectId),
    "POST",
    json.encodeToString(
        buildJsonObject {
            put("execution_mode", "auto")
            put("chapter_ids", JsonArray(chapterIds.map(::JsonPrimitive)))
        },
    ),
).data

suspend fun getCatalogingJob(
    connection: GatewayConnection,
    projectId: String,
    jobId: String,
): JsonObject = request<ApiEnvelope<JsonObject>>(
    connection.baseUrl,
    PcApiPaths.catalogingJob(projectId, jobId),
).data

suspend fun cancelCataloging(
    connection: GatewayConnection,
    projectId: String,
    jobId: String,
): JsonObject = request<ApiEnvelope<JsonObject>>(
    connection.baseUrl,
    PcApiPaths.catalogingCancel(projectId, jobId),
    "POST",
    json.encodeToString(JsonObject(emptyMap())),
).data

suspend fun createProjectExport(
    connection: GatewayConnection,
    projectId: String,
    format: String,
): JsonObject = request<ApiEnvelope<JsonObject>>(
    connection.baseUrl,
    PcApiPaths.projectExport(projectId),
    "POST",
    json.encodeToString(
        buildJsonObject {
            put("scope", "chapters")
            put("format", format)
        },
    ),
).data

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

    suspend fun reorderChapters(
        connection: GatewayConnection,
        projectId: String,
        chapterIds: List<String>,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.chapterReorder(projectId),
        method = "PUT",
        payload = buildJsonObject {
            put("ids", JsonArray(chapterIds.map(::JsonPrimitive)))
        },
    )

    suspend fun listChapterSnapshots(
        connection: GatewayConnection,
        projectId: String,
        chapterId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.chapterSnapshots(projectId, chapterId),
    ).data

    suspend fun getChapterSnapshot(
        connection: GatewayConnection,
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.chapterSnapshot(projectId, chapterId, snapshotId),
    ).data

    suspend fun diffChapterSnapshots(
        connection: GatewayConnection,
        projectId: String,
        chapterId: String,
        fromSnapshotId: String,
        toSnapshotId: String,
    ): JsonObject {
        val url = (connection.baseUrl + PcApiPaths.chapterSnapshotDiff(projectId, chapterId))
            .toHttpUrl()
            .newBuilder()
            .addQueryParameter("from_snapshot_id", fromSnapshotId)
            .addQueryParameter("to_snapshot_id", toSnapshotId)
            .build()
            .toString()
        return request<ApiEnvelope<JsonObject>>(connection.baseUrl, url, absolutePath = true).data
    }

    suspend fun restoreChapterSnapshot(
        connection: GatewayConnection,
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.chapterRestore(projectId, chapterId, snapshotId),
        "POST",
        json.encodeToString(JsonObject(emptyMap())),
    ).data

    suspend fun getCharacterRelationshipNetwork(
        connection: GatewayConnection,
        projectId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.characterRelationshipNetwork(projectId),
    ).data

    suspend fun replaceCharacterRelationships(
        connection: GatewayConnection,
        projectId: String,
        characterId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.characterRelationships(projectId, characterId),
        method = "PUT",
        payload = payload,
    )

    suspend fun getCharacterAiConfig(
        connection: GatewayConnection,
        projectId: String,
        characterId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.characterAiConfig(projectId, characterId),
    ).data

    suspend fun updateCharacterAiConfig(
        connection: GatewayConnection,
        projectId: String,
        characterId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.characterAiConfig(projectId, characterId),
        method = "PUT",
        payload = payload,
    )

    suspend fun listCharacterVersions(
        connection: GatewayConnection,
        projectId: String,
        characterId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.characterVersions(projectId, characterId),
    ).data

    suspend fun getCharacterVersion(
        connection: GatewayConnection,
        projectId: String,
        characterId: String,
        versionId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.characterVersion(projectId, characterId, versionId),
    ).data

    suspend fun listWorldVersions(
        connection: GatewayConnection,
        projectId: String,
        entryId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.worldVersions(projectId, entryId),
    ).data

    suspend fun listWorldTimeline(
        connection: GatewayConnection,
        projectId: String,
        entryId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.worldTimeline(projectId, entryId),
    ).data

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
        onEvent: suspend (JsonObject) -> Unit = {},
    ): JsonObject = withContext(Dispatchers.IO) {
        val clientTurnId = (payload["client_turn_id"] as? JsonPrimitive)?.contentOrNull.orEmpty()
        require(clientTurnId.isNotBlank()) { "立项 SSE 请求缺少 client_turn_id" }
        var afterSequence = (payload["after_sequence"] as? JsonPrimitive)?.longOrNull ?: 0L
        var token = validAccessToken(connection.baseUrl)
        var lastDisconnect: IOException? = null

        repeat(3) reconnect@{
            try {
                var authenticatedRetry = false
                while (true) {
                    val reconnectPayload = JsonObject(payload.toMutableMap().apply {
                        put("client_turn_id", JsonPrimitive(clientTurnId))
                        put("after_sequence", JsonPrimitive(afterSequence))
                    })
                    val request = Request.Builder()
                        .url(connection.baseUrl + PcApiPaths.NOVEL_CREATION_AGENT_TURN)
                        .header("Authorization", "Bearer $token")
                        .header("Accept", "text/event-stream")
                        .post(json.encodeToString(reconnectPayload).toRequestBody(JSON_MEDIA_TYPE))
                        .build()
                    val response = client.newCall(request).execute()
                    try {
                        if (response.code == 401 && !authenticatedRetry) {
                            token = refresh(connection.baseUrl, token)
                            authenticatedRetry = true
                            continue
                        }
                        if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
                        val source = response.body?.source() ?: throw IOException("立项助手响应为空")
                        while (!source.exhausted()) {
                            val line = source.readUtf8Line() ?: break
                            if (!line.startsWith("data:")) continue
                            val raw = line.removePrefix("data:").trim()
                            if (raw.isEmpty() || raw == "[DONE]") continue
                            val event = json.parseToJsonElement(raw) as? JsonObject ?: continue
                            afterSequence = maxOf(
                                afterSequence,
                                (event["sequence"] as? JsonPrimitive)?.longOrNull ?: 0L,
                            )
                            onEvent(event)
                            val type = (event["type"] as? JsonPrimitive)?.contentOrNull.orEmpty()
                            val message = (event["message"] as? JsonPrimitive)?.contentOrNull.orEmpty()
                            when (type) {
                                "complete" -> return@withContext event["data"] as? JsonObject
                                    ?: throw GatewayHttpException(502, "立项助手完成事件缺少结果")
                                "error" -> throw GatewayHttpException(
                                    422,
                                    message.ifBlank { "立项助手处理失败" },
                                )
                                "cancelled" -> throw kotlinx.coroutines.CancellationException(
                                    message.ifBlank { "本轮立项已取消" },
                                )
                            }
                        }
                        lastDisconnect = IOException("立项助手流提前结束")
                        break
                    } finally {
                        response.close()
                    }
                }
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: GatewayHttpException) {
                throw error
            } catch (error: IOException) {
                lastDisconnect = error
                return@reconnect
            }
        }
        throw IOException(
            "立项助手连接连续中断，后台任务可能仍在执行，请稍后重新进入",
            lastDisconnect,
        )
    }

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

    suspend fun finalizeNovelCreation(
        connection: GatewayConnection,
        sessionId: String,
    ): JsonObject = request<ApiEnvelope<JsonObject>>(
        connection.baseUrl,
        PcApiPaths.NOVEL_CREATION_FINALIZE,
        "POST",
        json.encodeToString(
            buildJsonObject {
                put("session_id", sessionId)
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


suspend fun streamCataloging(
    connection: GatewayConnection,
    projectId: String,
    jobId: String,
    onEvent: suspend (String) -> Unit,
) = withContext(Dispatchers.IO) {
    var token = validAccessToken(connection.baseUrl)
    repeat(2) { attempt ->
        val request = Request.Builder()
            .url(connection.baseUrl + PcApiPaths.catalogingStream(projectId, jobId))
            .header("Authorization", "Bearer $token")
            .header("Accept", "text/event-stream")
            .post(EMPTY_BODY)
            .build()
        client.newCall(request).execute().use { response ->
            if (response.code == 401 && attempt == 0) {
                response.body?.close()
                token = refresh(connection.baseUrl, token)
                return@use
            }
            if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
            val source = response.body?.source() ?: throw IOException("作品建档响应为空")
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

suspend fun downloadProjectExport(
    connection: GatewayConnection,
    projectId: String,
    fileId: String,
): ByteArray = withContext(Dispatchers.IO) {
    var token = validAccessToken(connection.baseUrl)
    repeat(2) { attempt ->
        val request = Request.Builder()
            .url(connection.baseUrl + PcApiPaths.projectExportDownload(projectId, fileId))
            .header("Authorization", "Bearer $token")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (response.code == 401 && attempt == 0) {
                response.body?.close()
                token = refresh(connection.baseUrl, token)
                return@use
            }
            if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
            return@withContext response.body?.bytes() ?: throw IOException("导出文件为空")
        }
    }
    throw GatewayHttpException(401, "设备授权已失效，请重新连接 Gateway")
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
            val call = client.newCall(request)
            val cancellationHandle = currentCoroutineContext()[Job]?.invokeOnCompletion { cause ->
                if (cause is CancellationException) call.cancel()
            }
            try {
                call.execute().use { response ->
                    if (response.code == 401 && attempt == 0) {
                        response.body?.close()
                        token = refresh(connection.baseUrl, token)
                        return@use
                    }
                    if (!response.isSuccessful) throw errorFrom(response.code, response.body?.string())
                    val source = response.body?.source() ?: throw IOException("AI 响应为空")
                    var terminalSeen = false
                    var streamError: String? = null
                    while (!source.exhausted()) {
                        val line = source.readUtf8Line() ?: break
                        if (line.startsWith("data:")) {
                            val data = line.removePrefix("data:").trim()
                            if (data.isEmpty()) continue
                            if (data == "[DONE]") break
                            val event = runCatching { json.parseToJsonElement(data) as? JsonObject }.getOrNull()
                            when ((event?.get("type") as? JsonPrimitive)?.contentOrNull) {
                                "complete" -> terminalSeen = true
                                "error", "permission_required" -> {
                                    streamError = (event["message"] as? JsonPrimitive)?.contentOrNull
                                        ?: (event["detail"] as? JsonPrimitive)?.contentOrNull
                                        ?: "AI 任务执行失败"
                                }
                            }
                            onEvent(data)
                        }
                    }
                    streamError?.let { throw GatewayHttpException(502, it) }
                    if (!terminalSeen) {
                        throw IOException("AI 流式连接提前结束，正在根据持久化运行记录恢复")
                    }
                    return@withContext
                }
            } finally {
                cancellationHandle?.dispose()
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
        private val BINARY_MEDIA_TYPE = "application/octet-stream".toMediaType()
        private val EMPTY_BODY = ByteArray(0).toRequestBody(JSON_MEDIA_TYPE)
        private val refreshMutex = Mutex()
    }
}
