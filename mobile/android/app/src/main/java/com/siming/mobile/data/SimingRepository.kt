package com.siming.mobile.data

import android.content.Context
import androidx.room.withTransaction
import com.siming.mobile.BuildConfig
import com.siming.mobile.data.agent.MobileWorkspaceAgent
import com.siming.mobile.data.creation.CreationExecutionRoute
import com.siming.mobile.data.creation.CreationStartInput
import com.siming.mobile.data.creation.MobileCreationAgent
import com.siming.mobile.data.creation.MobileCreationConversationAgent
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.local.OutboxMutation
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDatabase
import com.siming.mobile.data.local.SyncCursor
import com.siming.mobile.data.local.orderReplicaEntities
import com.siming.mobile.data.network.GatewayApi
import com.siming.mobile.data.network.GatewayHttpException
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.DirectApiSummary
import com.siming.mobile.data.network.PairingCompleteResponse
import com.siming.mobile.data.network.PcApiPayloads
import com.siming.mobile.data.network.RemoteSyncProject
import com.siming.mobile.data.network.SyncMutationRequest
import com.siming.mobile.data.network.WorkspaceAssistantRequest
import com.siming.mobile.security.PairingSecurity
import com.siming.mobile.security.MobileProviderEncryption
import com.siming.mobile.security.SecureApiConfigStore
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import com.siming.mobile.security.VerifiedPairing
import java.io.IOException
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

@OptIn(ExperimentalSerializationApi::class)
class SimingRepository(context: Context) {
    private val appContext = context.applicationContext
    private val database = SimingDatabase.get(appContext)
    private val dao = database.dao()
    private val tokenStore = SecureTokenStore(appContext)
    private val directApiStore = SecureApiConfigStore(appContext)
    private val api = GatewayApi(tokenStore)
    private val directApi = DirectApiClient(allowCleartextForTests = BuildConfig.DEBUG)
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val mobileWorkspaceAgent by lazy {
        MobileWorkspaceAgent(
            context = appContext,
            directApi = directApi,
            loadSnapshot = dao::projectSnapshot,
            saveEntity = { projectId, entityType, entityId, payload ->
                saveEntity(projectId, entityType, entityId, payload)
            },
        )
    }
    private val mobileCreationAgent by lazy { MobileCreationAgent(appContext, directApi) }
    private val mobileCreationConversationAgent by lazy {
        MobileCreationConversationAgent(
            context = appContext,
            stageAgent = mobileCreationAgent,
            directApi = directApi,
            persistSession = ::saveCreationSession,
            finalizeSession = { session ->
                saveCreationSession(session)
                val projectId = archiveCreation(session.string("id")) {}
                loadCreationSession(session.string("id")) to projectId
            },
        )
    }

    val connection: Flow<GatewayConnection?> = dao.observeConnection()
    val projects: Flow<List<ReplicaEntity>> = dao.observeProjects()
    val creationDrafts: Flow<List<ReplicaEntity>> = dao.observeCreationDrafts()
    val pendingCount: Flow<Int> = dao.observePendingCount()
    val cursor: Flow<SyncCursor?> = dao.observeCursor()
    val conflicts: Flow<List<LocalConflict>> = dao.observeConflicts()

    fun entities(projectId: String, entityType: String): Flow<List<ReplicaEntity>> =
        dao.observeEntities(projectId, entityType).map { records ->
            orderReplicaEntities(entityType, records)
        }

    fun directApiSummary(): DirectApiSummary? = directApiStore.read()?.summary()

    suspend fun discoverDirectModels(baseUrl: String, apiKey: String): List<String> {
        val effectiveKey = apiKey.trim().ifBlank { directApiStore.read()?.apiKey.orEmpty() }
        return directApi.discoverModels(baseUrl, effectiveKey)
    }

    suspend fun configureDirectApi(
        displayName: String,
        baseUrl: String,
        apiKey: String,
        model: String,
        protocol: String,
    ): DirectApiSummary {
        val existing = directApiStore.read()
        val config = DirectApiConfig(
            displayName = displayName.trim().ifBlank { "自定义 API" },
            baseUrl = baseUrl.trim().trimEnd('/'),
            apiKey = apiKey.trim().ifBlank { existing?.apiKey.orEmpty() },
            model = model.trim(),
            protocol = protocol,
        )
        val probe = directApi.testAndResolve(config)
        val resolved = config.copy(protocol = probe.protocol)
        directApiStore.save(resolved)
        return resolved.summary()
    }

    suspend fun testDirectApi(): DirectApiSummary {
        val config = directApiStore.read() ?: error("请先配置手机直连 API")
        val probe = directApi.testAndResolve(config)
        val resolved = config.copy(protocol = probe.protocol)
        directApiStore.save(resolved)
        return resolved.summary()
    }

    fun clearDirectApi() {
        directApiStore.clear()
    }

    fun verifyPairing(raw: String): VerifiedPairing = PairingSecurity.verify(raw)

    suspend fun requestPairing(
        pairing: VerifiedPairing,
        deviceName: String,
    ): PairingCompleteResponse {
        val response = api.completePairing(
            pairing,
            deviceName.trim().ifBlank { "Android 手机" },
            tokenStore.devicePublicKey(),
        )
        if (response.status == "approved" && response.tokens != null) {
            tokenStore.save(
                StoredTokenPair(
                    response.tokens.accessToken,
                    response.tokens.accessExpiresAt,
                    response.tokens.refreshToken,
                    response.tokens.refreshExpiresAt,
                ),
            )
            dao.saveConnection(
                GatewayConnection(
                    baseUrl = pairing.gatewayUrl,
                    gatewayName = pairing.gatewayName,
                    gatewayFingerprint = pairing.gatewayFingerprint,
                    gatewayEncryptionPublicKey = pairing.gatewayEncryptionPublicKey,
                    deviceId = requireNotNull(response.deviceId),
                    deviceRole = response.deviceRole ?: "member",
                    protocolVersion = 1,
                ),
            )
            SyncScheduler.install(appContext)
        }
        return response
    }

    suspend fun availableRemoteProjects(): List<RemoteSyncProject> {
        val connection = requireConnection()
        return api.listSyncProjects(connection).filter { it.status == "enabled" }
    }

    suspend fun bootstrapEnabledProjects(): Int {
        val connection = requireConnection()
        val projectIds = api.listSyncProjects(connection)
            .filter { it.status == "enabled" }
            .map { it.projectId }
        if (projectIds.isEmpty()) {
            dao.saveCursor(SyncCursor(cursor = 0, lastSuccessfulSyncAt = System.currentTimeMillis()))
            return 0
        }
        val response = api.bootstrap(connection, projectIds)
        database.withTransaction {
            // bootstrap is a full authoritative snapshot for enabled projects.
            // Remove only clean/non-conflicted replicas first so stale rows
            // from older mobile schemas disappear, while offline edits and
            // conflict branches remain available for upload/resolution.
            projectIds.forEach { projectId ->
                dao.deleteCleanProjectReplicas(projectId)
            }
            for (snapshot in response.entities) {
                val key = ReplicaEntity.key(
                    snapshot.projectId,
                    snapshot.entityType,
                    snapshot.entityId,
                )
                val local = dao.entity(key)
                if (local?.dirty == true || local?.conflicted == true) continue
                dao.saveEntity(
                    ReplicaEntity(
                        key = key,
                        projectId = snapshot.projectId,
                        entityType = snapshot.entityType,
                        entityId = snapshot.entityId,
                        revision = snapshot.revision,
                        operation = snapshot.operation,
                        payloadJson = snapshot.payload?.let(json::encodeToString),
                        contentHash = snapshot.contentHash,
                        serverModifiedAt = snapshot.serverModifiedAt,
                    ),
                )
            }
            dao.saveCursor(
                SyncCursor(
                    cursor = response.cursor,
                    lastSuccessfulSyncAt = System.currentTimeMillis(),
                ),
            )
        }
        return projectIds.size
    }

    suspend fun createProject(title: String, description: String = ""): String {
        val localId = UUID.randomUUID().toString()
        val payload = buildJsonObject {
            put("_record_type", "project")
            put("id", localId)
            put("title", title.trim().ifBlank { "未命名作品" })
            put("description", description.trim())
            put("narrative_perspective", "third_person")
            put("writing_style", "natural")
            put("short_sentences", false)
            put("daily_word_goal", 6000)
        }
        validateEntitySize(payload)
        val connection = dao.connection()
        if (connection == null || !prepareCanonicalWrite()) {
            return saveOfflineEntity(localId, "project", localId, payload)
        }

        val response = try {
            api.createProject(
                connection,
                PcApiPayloads.authoring("project", payload, create = true),
            )
        } catch (error: GatewayHttpException) {
            throw error
        } catch (_: IOException) {
            return saveOfflineEntity(localId, "project", localId, payload)
        }
        val projectId = response.requiredId()
        saveCanonicalReplica(projectId, "project", projectId, response)
        SyncScheduler.enqueue(appContext)
        return projectId
    }

    suspend fun saveEntity(
        projectId: String,
        entityType: String,
        entityId: String = UUID.randomUUID().toString(),
        payload: JsonObject,
    ): String {
        validateEntitySize(payload)
        val connection = dao.connection()
        if (
            connection != null &&
            entityType in CANONICAL_ENTITY_TYPES &&
            prepareCanonicalWrite()
        ) {
            val key = ReplicaEntity.key(projectId, entityType, entityId)
            val current = dao.entity(key)
            require(current?.conflicted != true) { "请先处理这条资料的版本分岔，再继续保存" }
            val create = entityType != "project" && current == null
            val response = try {
                if (entityType in GOVERNANCE_ENTITY_TYPES) {
                    var saved = api.saveGovernanceEntity(
                        connection,
                        projectId,
                        PcApiPayloads.governanceContent(entityType, payload, entityId, create),
                    )
                    val canonicalId = saved.requiredId()
                    val statusPayload = PcApiPayloads.governanceStatus(entityType, payload)
                    val desiredStatus = (statusPayload?.get("status") as? JsonPrimitive)?.content.orEmpty()
                    val serverStatus = (saved["status"] as? JsonPrimitive)?.content.orEmpty()
                    if (statusPayload != null && desiredStatus.isNotBlank() && desiredStatus != serverStatus) {
                        saved = api.updateGovernanceStatus(
                            connection,
                            projectId,
                            PcApiPayloads.governanceItemType(entityType),
                            canonicalId,
                            statusPayload,
                        )
                    }
                    saved
                } else {
                    api.saveAuthoringEntity(
                        connection = connection,
                        projectId = projectId,
                        entityType = entityType,
                        entityId = entityId,
                        create = create,
                        payload = PcApiPayloads.authoring(entityType, payload, create),
                    )
                }
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                return saveOfflineEntity(projectId, entityType, entityId, payload)
            }
            val canonicalId = response.requiredId()
            saveCanonicalReplica(projectId, entityType, canonicalId, response)
            SyncScheduler.enqueue(appContext)
            return canonicalId
        }
        return saveOfflineEntity(projectId, entityType, entityId, payload)
    }

    private suspend fun saveOfflineEntity(
        projectId: String,
        entityType: String,
        entityId: String,
        payload: JsonObject,
    ): String {
        val key = ReplicaEntity.key(projectId, entityType, entityId)
        val encoded = json.encodeToString(payload)
        val mutationEncoded = canonicalMutationJson(projectId, entityType, entityId, encoded)
            ?: error("同步写入缺少 payload")
        val now = Instant.now().toString()
        database.withTransaction {
            val current = dao.entity(key)
            val existingPending = dao.pendingMutation(projectId, entityType, entityId)
            dao.saveEntity(
                ReplicaEntity(
                    key = key,
                    projectId = projectId,
                    entityType = entityType,
                    entityId = entityId,
                    revision = current?.revision ?: 0,
                    operation = "upsert",
                    payloadJson = encoded,
                    contentHash = sha256(encoded),
                    serverModifiedAt = current?.serverModifiedAt ?: now,
                    dirty = true,
                    conflicted = current?.conflicted ?: false,
                ),
            )
            dao.saveMutation(
                (existingPending ?: OutboxMutation(
                    mutationId = UUID.randomUUID().toString(),
                    projectId = projectId,
                    entityType = entityType,
                    entityId = entityId,
                    operation = "upsert",
                    baseRevision = current?.revision ?: 0,
                    payloadJson = mutationEncoded,
                    clientModifiedAt = now,
                )).copy(
                    payloadJson = mutationEncoded,
                    clientModifiedAt = now,
                    state = "pending",
                    lastError = null,
                ),
            )
        }
        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
        return entityId
    }

    private fun canonicalMutationJson(
        projectId: String,
        entityType: String,
        entityId: String,
        rawPayload: String?,
    ): String? {
        if (rawPayload == null) return null
        val source = json.parseToJsonElement(rawPayload) as? JsonObject
            ?: error("本机资料 payload 不是 JSON 对象")
        return json.encodeToString(
            PcApiPayloads.syncMutation(entityType, source, projectId, entityId),
        )
    }

    suspend fun deleteEntity(projectId: String, entityType: String, entityId: String) {
        require(entityType != "project") { "移动端不会直接删除整部作品" }
        require(entityType !in GOVERNANCE_ENTITY_TYPES) {
            "PC 端叙事治理不直接删除记录；请把状态改为 abandoned"
        }
        val connection = dao.connection()
        if (
            connection != null &&
            entityType in CANONICAL_DELETABLE_ENTITY_TYPES &&
            prepareCanonicalWrite()
        ) {
            val key = ReplicaEntity.key(projectId, entityType, entityId)
            val current = dao.entity(key) ?: return
            require(!current.conflicted) { "请先处理这条资料的版本分岔，再继续删除" }
            try {
                api.deleteAuthoringEntity(connection, projectId, entityType, entityId)
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                deleteOfflineEntity(projectId, entityType, entityId)
                return
            }
            dao.saveEntity(
                current.copy(
                    operation = "delete",
                    payloadJson = null,
                    contentHash = sha256("null"),
                    serverModifiedAt = Instant.now().toString(),
                    dirty = false,
                    conflicted = false,
                    localModifiedAt = System.currentTimeMillis(),
                ),
            )
            SyncScheduler.enqueue(appContext)
            return
        }
        deleteOfflineEntity(projectId, entityType, entityId)
    }

    private suspend fun deleteOfflineEntity(projectId: String, entityType: String, entityId: String) {
        val key = ReplicaEntity.key(projectId, entityType, entityId)
        val now = Instant.now().toString()
        database.withTransaction {
            val current = dao.entity(key) ?: return@withTransaction
            val existingPending = dao.pendingMutation(projectId, entityType, entityId)
            dao.saveEntity(
                current.copy(
                    operation = "delete",
                    payloadJson = null,
                    contentHash = sha256("null"),
                    dirty = true,
                    localModifiedAt = System.currentTimeMillis(),
                ),
            )
            dao.saveMutation(
                (existingPending ?: OutboxMutation(
                    mutationId = UUID.randomUUID().toString(),
                    projectId = projectId,
                    entityType = entityType,
                    entityId = entityId,
                    operation = "delete",
                    baseRevision = current.revision,
                    payloadJson = null,
                    clientModifiedAt = now,
                )).copy(
                    operation = "delete",
                    payloadJson = null,
                    clientModifiedAt = now,
                    state = "pending",
                    lastError = null,
                ),
            )
        }
        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
    }

    /**
     * Preserve the ordering of previously queued offline edits. Connectivity
     * failures fall back to the outbox; authenticated HTTP errors stay visible
     * because silently replaying them could duplicate a server-side write.
     */
    private suspend fun prepareCanonicalWrite(): Boolean {
        if (dao.pendingMutationCount() == 0) return true
        return try {
            syncNow()
            check(dao.pendingMutationCount() == 0) {
                "仍有离线修订未通过 PC 端校验，请先在同步页处理"
            }
            true
        } catch (error: GatewayHttpException) {
            throw error
        } catch (_: IOException) {
            false
        }
    }

    private suspend fun saveCanonicalReplica(
        projectId: String,
        entityType: String,
        entityId: String,
        response: JsonObject,
    ) {
        val key = ReplicaEntity.key(projectId, entityType, entityId)
        val current = dao.entity(key)
        val replicaPayload = buildJsonObject {
            put("_record_type", RECORD_TYPES.getValue(entityType))
            response.forEach { (name, value) -> put(name, value) }
            put("id", entityId)
            if (entityType != "project" && response["project_id"] == null) {
                put("project_id", projectId)
            }
        }
        val encoded = json.encodeToString(replicaPayload)
        dao.saveEntity(
            ReplicaEntity(
                key = key,
                projectId = projectId,
                entityType = entityType,
                entityId = entityId,
                revision = current?.revision ?: 0,
                operation = "upsert",
                payloadJson = encoded,
                contentHash = sha256(encoded),
                serverModifiedAt = (response["updated_at"] as? JsonPrimitive)?.content
                    ?: Instant.now().toString(),
                dirty = false,
                conflicted = false,
            ),
        )
    }

    private fun JsonObject.requiredId(): String =
        (get("id") as? JsonPrimitive)?.content?.takeIf { it.isNotBlank() }
            ?: error("PC API 返回的数据缺少 id")

    private fun validateEntitySize(payload: JsonObject) {
        val encoded = json.encodeToString(payload)
        require(encoded.toByteArray(Charsets.UTF_8).size <= MAX_ENTITY_BYTES) {
            "单条资料不能超过 1 MiB；请把超长正文拆成多个章节"
        }
    }

    suspend fun syncNow(): SyncOutcome = syncMutex.withLock {
        val connection = requireConnection()
        val localProjectIds = dao.localProjectIds()
        try {
            pushPending(connection)
            refreshConflicts(connection)
            if (localProjectIds.isNotEmpty()) pullAll(connection, localProjectIds)
            val current = dao.cursor() ?: SyncCursor()
            dao.saveCursor(
                current.copy(
                    lastSuccessfulSyncAt = System.currentTimeMillis(),
                    lastError = null,
                ),
            )
            SyncOutcome.Success
        } catch (error: Exception) {
            val current = dao.cursor() ?: SyncCursor()
            dao.saveCursor(current.copy(lastError = error.toUserFacingMessage()))
            throw error
        }
    }

    private suspend fun pushPending(connection: GatewayConnection) {
        while (true) {
            val candidates = dao.pendingMutations(100)
            val pending = buildList {
                var estimatedBytes = 0
                for (mutation in candidates) {
                    val mutationBytes = (mutation.payloadJson?.toByteArray(Charsets.UTF_8)?.size ?: 4) + 512
                    if (isNotEmpty() && estimatedBytes + mutationBytes > MAX_PUSH_BYTES) break
                    add(mutation)
                    estimatedBytes += mutationBytes
                }
            }
            if (pending.isEmpty()) return
            database.withTransaction {
                pending.forEach { mutation ->
                    dao.updateMutation(
                        mutation.copy(
                            state = "sending",
                            sentPayloadHash = sha256(mutation.payloadJson ?: "null"),
                        ),
                    )
                }
            }
            val response = try {
                api.push(
                    connection,
                    pending.map { mutation ->
                        SyncMutationRequest(
                            mutationId = mutation.mutationId,
                            projectId = mutation.projectId,
                            entityType = mutation.entityType,
                            entityId = mutation.entityId,
                            operation = mutation.operation,
                            baseRevision = mutation.baseRevision,
                            payload = mutation.payloadJson?.let {
                                json.parseToJsonElement(it) as JsonObject
                            },
                            clientModifiedAt = mutation.clientModifiedAt,
                        )
                    },
                )
            } catch (error: Exception) {
                database.withTransaction {
                    pending.forEach { mutation ->
                        resetForRetry(mutation, error.toUserFacingMessage())
                    }
                }
                throw error
            }
            database.withTransaction {
                val returnedIds = response.results.mapTo(mutableSetOf()) { it.mutationId }
                for (result in response.results) {
                    val sent = pending.firstOrNull { it.mutationId == result.mutationId } ?: continue
                    val key = ReplicaEntity.key(sent.projectId, sent.entityType, sent.entityId)
                    val current = dao.entity(key)
                    when (result.status) {
                        "applied", "duplicate" -> {
                            val revision = result.revision ?: current?.revision ?: sent.baseRevision
                            dao.deleteMutation(sent.mutationId)
                            if (current != null) {
                                val currentMutation = if (current.operation == "delete") {
                                    null
                                } else {
                                    canonicalMutationJson(
                                        sent.projectId,
                                        sent.entityType,
                                        sent.entityId,
                                        current.payloadJson,
                                    )
                                }
                                val unchanged = sha256(currentMutation ?: "null") ==
                                    sha256(sent.payloadJson ?: "null") && current.operation == sent.operation
                                dao.saveEntity(
                                    current.copy(
                                        revision = revision,
                                        dirty = !unchanged,
                                        conflicted = false,
                                    ),
                                )
                                if (!unchanged && dao.pendingMutation(
                                        sent.projectId,
                                        sent.entityType,
                                        sent.entityId,
                                    ) == null
                                ) {
                                    dao.saveMutation(
                                        OutboxMutation(
                                            mutationId = UUID.randomUUID().toString(),
                                            projectId = sent.projectId,
                                            entityType = sent.entityType,
                                            entityId = sent.entityId,
                                            operation = current.operation,
                                            baseRevision = revision,
                                            payloadJson = currentMutation,
                                            clientModifiedAt = Instant.now().toString(),
                                        ),
                                    )
                                }
                            }
                        }
                        "conflict" -> {
                            dao.updateMutation(
                                sent.copy(
                                    state = "conflict",
                                    lastError = result.message ?: "双方均有离线修改",
                                ),
                            )
                            if (current != null) dao.saveEntity(current.copy(conflicted = true))
                            dao.saveConflict(
                                LocalConflict(
                                    id = result.conflictId ?: UUID.randomUUID().toString(),
                                    projectId = sent.projectId,
                                    entityType = sent.entityType,
                                    entityId = sent.entityId,
                                    clientPayloadJson = sent.payloadJson,
                                    serverPayloadJson = (result.serverSnapshot?.get("payload") as? JsonObject)
                                        ?.let(json::encodeToString),
                                    serverRevision = result.revision ?: sent.baseRevision,
                                ),
                            )
                        }
                        else -> dao.updateMutation(
                            sent.copy(state = "pending", lastError = result.message ?: "内容未通过校验"),
                        )
                    }
                }
                pending.filterNot { it.mutationId in returnedIds }.forEach { mutation ->
                    resetForRetry(mutation, "Gateway 未返回该修订的处理结果")
                }
            }
            if (response.results.none { it.status in setOf("applied", "duplicate") }) return
        }
    }

    private suspend fun pullAll(connection: GatewayConnection, projectIds: List<String>) {
        var cursorValue = dao.cursor()?.cursor ?: 0
        do {
            val response = api.pull(connection, cursorValue, projectIds)
            database.withTransaction {
                for (change in response.changes) {
                    val key = ReplicaEntity.key(change.projectId, change.entityType, change.entityId)
                    val local = dao.entity(key)
                    if (local?.dirty == true || local?.conflicted == true) continue
                    dao.saveEntity(
                        ReplicaEntity(
                            key = key,
                            projectId = change.projectId,
                            entityType = change.entityType,
                            entityId = change.entityId,
                            revision = change.revision,
                            operation = change.operation,
                            payloadJson = change.payload?.let(json::encodeToString),
                            contentHash = change.contentHash,
                            serverModifiedAt = change.changedAt,
                        ),
                    )
                }
                cursorValue = response.nextCursor
                dao.saveCursor(
                    (dao.cursor() ?: SyncCursor()).copy(cursor = cursorValue),
                )
            }
        } while (response.hasMore)
    }

    private suspend fun refreshConflicts(connection: GatewayConnection) {
        val remote = api.listConflicts(connection)
        database.withTransaction {
            val remoteIds = remote.mapTo(mutableSetOf()) { it.id }
            dao.openConflictsSnapshot()
                .filterNot { it.id in remoteIds }
                .forEach { resolved ->
                    dao.resolveConflict(resolved.id)
                    dao.deleteConflictMutation(
                        resolved.projectId,
                        resolved.entityType,
                        resolved.entityId,
                    )
                    val key = ReplicaEntity.key(
                        resolved.projectId,
                        resolved.entityType,
                        resolved.entityId,
                    )
                    dao.entity(key)?.let {
                        dao.saveEntity(it.copy(dirty = false, conflicted = false))
                    }
                }
            for (conflict in remote) {
                dao.saveConflict(
                    LocalConflict(
                        id = conflict.id,
                        projectId = conflict.projectId,
                        entityType = conflict.entityType,
                        entityId = conflict.entityId,
                        clientPayloadJson = conflict.clientPayload?.let(json::encodeToString),
                        serverPayloadJson = conflict.serverPayload?.let(json::encodeToString),
                        serverRevision = conflict.serverRevision,
                        status = conflict.status,
                    ),
                )
                val key = ReplicaEntity.key(
                    conflict.projectId,
                    conflict.entityType,
                    conflict.entityId,
                )
                dao.entity(key)?.let { dao.saveEntity(it.copy(conflicted = true)) }
            }
        }
    }

    private suspend fun resetForRetry(mutation: OutboxMutation, error: String) {
        val newer = dao.pendingMutation(
            mutation.projectId,
            mutation.entityType,
            mutation.entityId,
        )
        if (newer != null && newer.mutationId != mutation.mutationId) {
            // A save made while this request was in flight already contains the
            // latest payload at the same base revision, so the older send can
            // be discarded instead of creating an avoidable self-conflict.
            dao.deleteMutation(mutation.mutationId)
        } else {
            dao.resetMutationForRetry(mutation.mutationId, error)
        }
    }

    suspend fun resolveConflict(conflict: LocalConflict, choice: String) {
        val connection = requireConnection()
        api.resolveConflict(connection, conflict.id, choice)
        dao.resolveConflict(conflict.id)
        dao.deleteConflictMutation(conflict.projectId, conflict.entityType, conflict.entityId)
        val key = ReplicaEntity.key(conflict.projectId, conflict.entityType, conflict.entityId)
        dao.entity(key)?.let { dao.saveEntity(it.copy(dirty = false, conflicted = false)) }
        syncNow()
    }

    suspend fun runAssistant(
        projectId: String,
        scope: String,
        prompt: String,
        modelRoute: AssistantModelRoute,
        onEvent: suspend (String) -> Unit,
    ): AssistantRoute {
        val connection = dao.connection()
        if (connection != null) {
            val directConfig = if (modelRoute == AssistantModelRoute.MobileKey) {
                resolvedDirectConfig()
            } else {
                null
            }
            api.streamAssistant(
                connection,
                projectId,
                WorkspaceAssistantRequest(
                    scope = scope,
                    message = prompt,
                    modelRoute = if (directConfig == null) "pc" else "mobile",
                    mobileProvider = directConfig?.let {
                        MobileProviderEncryption.seal(it, connection, projectId)
                    },
                ),
                onEvent,
            )
            syncNow()
            return if (directConfig == null) AssistantRoute.GatewayPc else AssistantRoute.GatewayMobileKey
        }

        val directConfig = directApiStore.read()?.let { resolvedDirectConfig() }
        if (directConfig != null) {
            mobileWorkspaceAgent.run(projectId, scope, prompt, directConfig, onEvent)
            return AssistantRoute.DirectApi
        }
        error("请先配置手机直连 API，或连接自己的 Gateway")
    }

    suspend fun refreshCreationDrafts(): Int {
        val connection = dao.connection() ?: return 0
        val sessions = api.listNovelCreationSessions(connection)
        sessions.forEach { remote ->
            val sessionId = remote.string("id")
            val local = storedCreationSession(sessionId)
            val route = local?.let(::creationRoute) ?: CreationExecutionRoute.Pc
            saveCreationSession(tagCreationRoute(remote, route, CREATION_HOST_GATEWAY))
        }
        return sessions.size
    }

    suspend fun beginCreation(
        input: CreationStartInput,
        route: CreationExecutionRoute,
    ): JsonObject {
        require(input.brief.isNotBlank()) { "先用一两句话告诉 AI 你想写什么" }
        val session = when (route) {
            CreationExecutionRoute.Pc -> {
                val connection = requireConnection()
                tagCreationRoute(
                    api.startNovelCreation(connection, creationStartPayload(input)),
                    route,
                    CREATION_HOST_GATEWAY,
                )
            }
            CreationExecutionRoute.MobileKey -> {
                resolvedDirectConfig()
                val connection = dao.connection()
                if (connection == null) {
                    tagCreationRoute(
                        mobileCreationAgent.start(input),
                        route,
                        CREATION_HOST_DEVICE,
                    )
                } else {
                    tagCreationRoute(
                        api.startNovelCreation(connection, creationStartPayload(input)),
                        route,
                        CREATION_HOST_GATEWAY,
                    )
                }
            }
        }
        saveCreationSession(session)
        return session
    }

    suspend fun getCreationSession(sessionId: String): JsonObject = loadCreationSession(sessionId)

    suspend fun runCreationAgentTurn(
        sessionId: String,
        message: String,
        onProgress: suspend (String) -> Unit = {},
    ): JsonObject {
        require(message.isNotBlank()) { "请输入你想告诉 AI 的内容" }
        val current = loadCreationSession(sessionId)
        val history = creationAgentHistory(current)
        val userHistory = history + agentHistoryMessage("user", message)
        saveCreationSession(withCreationAgentHistory(current, userHistory))
        val route = creationRoute(current)
        val gatewayExecution = creationHost(current) == CREATION_HOST_GATEWAY
        val updated = when {
            route == CreationExecutionRoute.Pc || gatewayExecution -> {
                val connection = requireConnection()
                val mobileProvider = if (route == CreationExecutionRoute.MobileKey) {
                    mobileProviderPayload(connection, sessionId)
                } else null
                onProgress(
                    if (mobileProvider == null) "PC Creation Agent 正在读取并增量写入…"
                    else "手机 Key 正在驱动 PC Creation Agent…"
                )
                val result = api.novelCreationAgentTurn(
                    connection,
                    buildJsonObject {
                        put("session_id", sessionId)
                        put("message", message)
                        put("history", JsonArray(history.takeLast(12)))
                        put("model_route", if (mobileProvider == null) "pc" else "mobile")
                        mobileProvider?.let { put("mobile_provider", it) }
                    },
                )
                val reply = result.string("reply").ifBlank { "已完成本轮立项工具调用" }
                val fresh = tagCreationRoute(
                    api.getNovelCreationSession(connection, sessionId),
                    route,
                    CREATION_HOST_GATEWAY,
                )
                withCreationAgentHistory(
                    fresh,
                    userHistory + agentHistoryMessage("assistant", reply),
                )
            }
            else -> {
                onProgress("手机 Creation Agent 正在读取资料并执行工具…")
                val result = mobileCreationConversationAgent.run(
                    source = current,
                    message = message,
                    history = history,
                    config = resolvedDirectConfig(),
                    onProgress = onProgress,
                )
                withCreationAgentHistory(
                    result.session,
                    userHistory + agentHistoryMessage("assistant", result.reply),
                )
            }
        }
        saveCreationSession(updated)
        return updated
    }

    suspend fun archiveCreation(
        sessionId: String,
        onProgress: suspend (String) -> Unit = {},
    ): String {
        val current = loadCreationSession(sessionId)
        requireCreationReady(current)
        val route = creationRoute(current)
        val executionHost = creationHost(current)
        val connection = dao.connection()
        val projectId = when {
            executionHost == CREATION_HOST_GATEWAY -> {
                onProgress("正在通过 PC 立项服务创建正式作品…")
                val applied = api.applyNovelCreation(requireConnection(), sessionId)
                applied.string("project_id").ifBlank { error("PC 建档结果缺少 project_id") }
            }
            connection != null -> {
                onProgress("正在把手机 V3 草稿提交给 PC 的正式建档流程…")
                applyLocalCreationThroughPc(connection, current, onProgress)
            }
            else -> {
                onProgress("正在手机本地建立作品、角色、设定与大纲档案…")
                archiveLocalCreation(current)
            }
        }
        val completed = mobileCreationAgent.markCompleted(current, projectId)
        saveCreationSession(tagCreationRoute(completed, route, executionHost))
        if (connection != null) {
            onProgress("正在下载正式作品的可离线副本…")
            bootstrapEnabledProjects()
        }
        return projectId
    }

    suspend fun discardCreation(sessionId: String) {
        val key = ReplicaEntity.key(CREATION_REPLICA_PROJECT, "creation_session", sessionId)
        val entity = dao.entity(key) ?: return
        val session = entity.payloadJson
            ?.let { json.parseToJsonElement(it) as? JsonObject }
        if (session != null && creationHost(session) == CREATION_HOST_GATEWAY) {
            api.deleteNovelCreationSession(requireConnection(), sessionId)
        }
        dao.saveEntity(entity.copy(operation = "delete", localModifiedAt = System.currentTimeMillis()))
    }

    private fun creationStartPayload(input: CreationStartInput): JsonObject = buildJsonObject {
        put("mode", "hybrid")
        put("user_brief", input.brief.trim())
        put("target_audience", input.targetAudience.trim())
        put("genre", input.genre.trim())
        put("platform", input.platform.trim())
        put("preset_id", input.presetId)
        put("theme_id", input.themeId)
        put("target_words", input.targetWords)
        put("target_chapters", input.targetChapters)
        put("world_tone", input.worldTone.trim())
        put("story_structure", input.storyStructure.trim())
        put("pacing", input.pacing.trim())
        put("writing_style", input.writingStyle.trim())
        put("special_requirements", JsonArray(input.specialRequirements.map(::JsonPrimitive)))
        put("avoid", JsonArray(input.avoid.map(::JsonPrimitive)))
        put("author_overrides", buildJsonObject {})
        put("creation_mode", input.creationMode)
        put("author_brief", if (input.creationMode == "author_led") input.brief.trim() else "")
        put("author_outline", input.authorOutline.trim())
        put("locked_requirements", JsonArray(input.lockedRequirements.map(::JsonPrimitive)))
    }

    private fun creationAgentHistory(session: JsonObject): List<JsonObject> =
        (session.draft()["agent_history"] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }

    private fun agentHistoryMessage(role: String, content: String): JsonObject = buildJsonObject {
        put("id", UUID.randomUUID().toString())
        put("role", role)
        put("content", content)
        put("created_at", Instant.now().toString())
    }

    private fun withCreationAgentHistory(session: JsonObject, history: List<JsonObject>): JsonObject {
        val draft = session.draft().toMutableMap()
        draft["agent_history"] = JsonArray(history.takeLast(40))
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

    private suspend fun saveCreationSession(session: JsonObject) {
        val sessionId = session.string("id")
        require(sessionId.isNotBlank()) { "立项会话缺少 id" }
        val encoded = json.encodeToString(session)
        val key = ReplicaEntity.key(CREATION_REPLICA_PROJECT, "creation_session", sessionId)
        dao.saveEntity(
            ReplicaEntity(
                key = key,
                projectId = CREATION_REPLICA_PROJECT,
                entityType = "creation_session",
                entityId = sessionId,
                revision = session.int("revision").toLong(),
                operation = "upsert",
                payloadJson = encoded,
                contentHash = sha256(encoded),
                serverModifiedAt = session.string("updated_at").ifBlank { Instant.now().toString() },
                dirty = false,
                conflicted = false,
            ),
        )
    }

    private suspend fun loadCreationSession(sessionId: String): JsonObject {
        return storedCreationSession(sessionId) ?: error("立项草稿不存在或已删除")
    }

    private suspend fun storedCreationSession(sessionId: String): JsonObject? {
        val key = ReplicaEntity.key(CREATION_REPLICA_PROJECT, "creation_session", sessionId)
        val raw = dao.entity(key)?.payloadJson ?: return null
        return json.parseToJsonElement(raw) as? JsonObject
    }

    private fun tagCreationRoute(
        session: JsonObject,
        route: CreationExecutionRoute,
        executionHost: String,
    ): JsonObject {
        val draft = session.draft().toMutableMap().apply {
            put("execution_route", JsonPrimitive(if (route == CreationExecutionRoute.Pc) "pc" else "mobile"))
            put("execution_host", JsonPrimitive(executionHost))
        }
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

    private fun creationRoute(session: JsonObject): CreationExecutionRoute =
        if (session.draft().string("execution_route") == "pc") {
            CreationExecutionRoute.Pc
        } else {
            CreationExecutionRoute.MobileKey
        }

    private fun creationHost(session: JsonObject): String =
        session.draft().string("execution_host").ifBlank {
            if (creationRoute(session) == CreationExecutionRoute.Pc) {
                CREATION_HOST_GATEWAY
            } else {
                CREATION_HOST_DEVICE
            }
        }

    private suspend fun mobileProviderPayload(
        connection: GatewayConnection,
        sessionId: String,
    ): JsonObject {
        val envelope = MobileProviderEncryption.seal(
            resolvedDirectConfig(),
            connection,
            sessionId,
        )
        return buildJsonObject {
            put("version", envelope.version)
            put("ephemeral_public_key", envelope.ephemeralPublicKey)
            put("nonce", envelope.nonce)
            put("ciphertext", envelope.ciphertext)
        }
    }

    private fun ensureSelectedConcept(data: JsonObject): JsonObject {
        if (data.string("selected_concept_id").isNotBlank()) return data
        val id = ((data["options"] as? JsonArray)?.firstOrNull() as? JsonObject)
            ?.string("id")
            .orEmpty()
        require(id.isNotBlank()) { "请选择一个创意方向" }
        return JsonObject(data.toMutableMap().apply { put("selected_concept_id", JsonPrimitive(id)) })
    }

    private fun requireCreationReady(session: JsonObject) {
        val requiredStages = listOf("constraints", "concepts", "world_style", "characters", "locations", "macro_outline")
        val missing = requiredStages.filter { stage ->
            session.stageState(stage).string("status") != "confirmed"
        }
        require(missing.isEmpty()) {
            "请先确认${creationStageLabel(missing.first())}，再建立正式作品"
        }
        val reviewState = session.stageState("final_review")
        require(reviewState.string("status") in setOf("generated", "confirmed")) {
            "请先让 AI 完成最终审阅"
        }
        val review = reviewState["data"] as? JsonObject
            ?: error("请先完成最终审阅")
        require((review["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() == true) {
            val blocking = (review["blocking"] as? JsonArray).orEmpty()
                .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
                .joinToString("；")
            blocking.ifBlank { "最终审阅尚未通过" }
        }
    }

    private suspend fun applyLocalCreationThroughPc(
        connection: GatewayConnection,
        local: JsonObject,
        onProgress: suspend (String) -> Unit,
    ): String {
        val draft = local.draft()
        val form = draft.objectValue("form")
        var remote = api.startNovelCreation(
            connection,
            buildJsonObject {
                put("mode", "hybrid")
                put("user_brief", local.string("user_brief"))
                listOf("target_audience", "genre", "platform").forEach { key ->
                    put(key, form[key] ?: JsonPrimitive(local.string(key)))
                }
                listOf(
                    "preset_id", "theme_id", "target_words", "target_chapters", "world_tone",
                    "story_structure", "pacing", "writing_style", "special_requirements", "avoid",
                    "author_overrides",
                ).forEach { key -> form[key]?.let { put(key, it) } }
                put("creation_mode", draft.string("creation_mode"))
                put("author_brief", draft.string("author_brief"))
                put("author_outline", draft.string("author_outline"))
                put("locked_requirements", draft["locked_requirements"] ?: JsonArray(emptyList()))
            },
        )
        val remoteId = remote.string("id")
        for (stage in CREATION_STAGE_ORDER) {
            val state = local.stageState(stage)
            if (state.string("status") != "confirmed") continue
            var data = state["data"] as? JsonObject ?: continue
            if (stage == "concepts") data = ensureSelectedConcept(data)
            onProgress("正在提交${creationStageLabel(stage)}…")
            remote = api.confirmNovelCreationStage(
                connection,
                remoteId,
                stage,
                buildJsonObject {
                    put("data", data)
                    put("confirm", true)
                    put("source", "author")
                    put("expected_revision", remote.int("revision"))
                },
            )
        }
        onProgress("结构校验通过，正在执行 PC 正式建档…")
        val applied = api.applyNovelCreation(connection, remoteId)
        return applied.string("project_id").ifBlank { error("PC 建档结果缺少 project_id") }
    }

    private suspend fun archiveLocalCreation(session: JsonObject): String {
        val draft = session.draft()
        val form = draft.objectValue("form")
        val conceptData = session.stageData("concepts")
        val concepts = (conceptData["options"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val selectedId = conceptData.string("selected_concept_id").ifBlank { draft.string("selected_concept_id") }
        val concept = concepts.firstOrNull { it.string("id") == selectedId }
            ?: concepts.firstOrNull()
            ?: error("创意方向为空")
        val worldStyle = session.stageData("world_style")
        val characters = session.stageData("characters")
        val locations = session.stageData("locations")
        val macro = session.stageData("macro_outline")
        val opening = if (session.stageState("opening_outline").string("status") == "confirmed") {
            session.stageData("opening_outline")
        } else {
            JsonObject(emptyMap())
        }
        val projectId = UUID.randomUUID().toString()
        val title = concept.string("title").ifBlank { "未命名作品" }
        val projectTags = listOfNotNull(
            form.string("genre").takeIf(String::isNotBlank),
            concept.string("subtitle").takeIf(String::isNotBlank),
            form.string("platform").takeIf(String::isNotBlank),
        )

        saveEntity(
            projectId,
            "project",
            projectId,
            buildJsonObject {
                put("_record_type", "project")
                put("id", projectId)
                put("title", title.take(200))
                put("description", concept.string("logline"))
                if (projectTags.isNotEmpty()) {
                    // Project.tags is a JSON-array *string* in the canonical
                    // database/sync record, not a nested JSON array.
                    put("tags", JsonArray(projectTags.map(::JsonPrimitive)).toString())
                }
                put("narrative_perspective", "third_person")
                put("writing_style", worldStyle.string("writing_style").ifBlank { "natural" }.take(50))
                put("short_sentences", false)
                put("daily_word_goal", 6000)
                put("forbidden_sentence_patterns", jsonLines(worldStyle["forbidden_patterns"]))
                put("rhetoric_guidelines", jsonLines(worldStyle["style_rules"]))
                put("custom_style_prompt", concept.string("subtitle"))
            },
        )

        val characterIds = linkedMapOf<String, String>()
        (characters["characters"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEach { row ->
            val name = row.string("name").ifBlank { "未命名角色" }.take(100)
            if (characterIds.containsKey(name)) return@forEach
            val id = UUID.randomUUID().toString()
            characterIds[name] = id
            saveEntity(projectId, "character", id, buildJsonObject {
                put("_record_type", "character")
                put("id", id)
                put("project_id", projectId)
                put("name", name)
                put("role_type", normalizeCharacterRole(row.string("role_type")))
                put("appearance", row.string("appearance"))
                put("personality", row.string("personality"))
                put("background", row.string("background"))
                put("age", row.string("age"))
                put("life_status", row.string("life_status").ifBlank { "active" })
                put("current_location", row.string("current_location"))
                put("realm_or_level", row.string("realm_or_level"))
                put("physical_state", row.string("physical_state"))
                put("mental_state", row.string("mental_state"))
                put("current_goal", row.string("goal").ifBlank { row.string("current_goal") })
                put("active_conflict", row.string("conflict").ifBlank { row.string("active_conflict") })
                put("abilities_state", row.string("abilities_state"))
                put("items_or_assets", row.string("items_or_assets"))
                row["abilities"]?.let { put("abilities", if (it is JsonPrimitive) it else JsonPrimitive(it.toString())) }
                row["profile"]?.let { put("profile_json", it) }
                put("is_evolution_tracked", true)
            })
        }

        (characters["relationships"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEach { row ->
            val sourceName = row.string("character_a").ifBlank { row.string("source") }
            val targetName = row.string("character_b").ifBlank { row.string("target") }
            val a = characterIds[sourceName]
            val b = characterIds[targetName]
            if (a != null && b != null && a != b) {
                val id = UUID.randomUUID().toString()
                saveEntity(projectId, "character_relation", id, buildJsonObject {
                    put("_record_type", "character_relationship")
                    put("id", id)
                    put("project_id", projectId)
                    put("character_a_id", a)
                    put("character_b_id", b)
                    put("relationship_type", row.string("relationship_type").ifBlank { "related" }.take(100))
                    put("description", row.string("description"))
                })
            }
        }

        val worldIds = linkedMapOf<String, String>()
        val worldRows = buildList {
            addAll((worldStyle["worldbuilding"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject })
            addAll((locations["entries"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject })
        }.distinctBy { it.string("title") }
        worldRows.forEachIndexed { index, row ->
            val titleValue = row.string("title").ifBlank { "未命名设定 ${index + 1}" }.take(200)
            val id = UUID.randomUUID().toString()
            worldIds[titleValue] = id
            saveEntity(projectId, "world", id, buildJsonObject {
                put("_record_type", "world_entry")
                put("id", id)
                put("project_id", projectId)
                put("title", titleValue)
                put("dimension", normalizeWorldDimension(row.string("dimension")))
                put("content", row.string("content").ifBlank { row.string("description") }.ifBlank { "待补充" })
            })
        }
        (locations["relations"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEach { row ->
            val sourceId = worldIds[row.string("source_title")]
            val targetId = worldIds[row.string("target_title")]
            if (sourceId != null && targetId != null && sourceId != targetId) {
                val id = UUID.randomUUID().toString()
                saveEntity(projectId, "world_relation", id, buildJsonObject {
                    put("_record_type", "world_relationship")
                    put("id", id)
                    put("project_id", projectId)
                    put("source_entry_id", sourceId)
                    put("target_entry_id", targetId)
                    put("relation_type", row.string("relation_type").ifBlank { "related" }.take(100))
                    put("description", row.string("description"))
                    row["metadata"]?.let { put("metadata_json", it) }
                })
            }
        }

        val volumeIds = mutableListOf<String>()
        (macro["volumes"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEachIndexed { index, row ->
            val id = UUID.randomUUID().toString()
            volumeIds += id
            saveEntity(projectId, "outline", id, buildJsonObject {
                put("_record_type", "outline_node")
                put("id", id)
                put("project_id", projectId)
                put("parent_id", JsonNull)
                put("node_type", "volume")
                put("title", row.string("title").ifBlank { "第 ${index + 1} 卷" }.take(200))
                put("summary", row.string("summary"))
                put("planned_summary", row.string("summary"))
                put("status", "pending")
                put("sort_order", index)
                put("metadata_json", buildJsonObject {
                    row["start_chapter"]?.let { put("start_chapter", it) }
                    row["end_chapter"]?.let { put("end_chapter", it) }
                })
            })
        }
        val outlineIdsByClient = linkedMapOf<String, String>()
        (opening["chapters"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEachIndexed { index, row ->
            val id = UUID.randomUUID().toString()
            outlineIdsByClient[row.string("client_id")] = id
            val parentIndex = row.int("parent_index")
            saveEntity(projectId, "outline", id, buildJsonObject {
                put("_record_type", "outline_node")
                put("id", id)
                put("project_id", projectId)
                put("parent_id", volumeIds.getOrNull(parentIndex)?.let(::JsonPrimitive) ?: JsonNull)
                put("node_type", "chapter")
                put("title", row.string("title").take(200))
                put("summary", row.string("summary"))
                put("planned_summary", row.string("planned_summary").ifBlank { row.string("summary") })
                put("status", "pending")
                put("sort_order", row.int("sort_order").takeIf { it > 0 } ?: index + 1)
                outlineMetadata(row).takeIf(JsonObject::isNotEmpty)?.let { put("metadata_json", it) }
            })
        }
        (opening["sections"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEachIndexed { index, row ->
            val parent = outlineIdsByClient[row.string("parent_client_id")] ?: return@forEachIndexed
            val id = UUID.randomUUID().toString()
            saveEntity(projectId, "outline", id, buildJsonObject {
                put("_record_type", "outline_node")
                put("id", id)
                put("project_id", projectId)
                put("parent_id", parent)
                put("node_type", "section")
                put("title", row.string("title").take(200))
                put("summary", row.string("summary"))
                put("planned_summary", row.string("planned_summary").ifBlank { row.string("summary") })
                put("status", "pending")
                put("sort_order", row.int("sort_order").takeIf { it > 0 } ?: index + 1)
                outlineMetadata(row).takeIf(JsonObject::isNotEmpty)?.let { put("metadata_json", it) }
            })
        }
        return projectId
    }

    private fun jsonLines(value: JsonElement?): String = when (value) {
        is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.joinToString("\n")
        is JsonPrimitive -> value.contentOrNull.orEmpty()
        else -> ""
    }

    private fun normalizeWorldDimension(value: String): String = value.takeIf {
        it in setOf("geography", "history", "factions", "power_system", "races", "culture")
    } ?: "culture"

    private fun normalizeCharacterRole(value: String): String = when (value.trim().lowercase()) {
        "protagonist", "primary", "lead", "main character", "主角", "主人公", "男主", "女主" -> "protagonist"
        "antagonist", "villain", "rival", "反派", "敌人", "对手", "宿敌" -> "antagonist"
        "mentor", "guide", "导师", "师父", "师傅", "老师", "引路人" -> "mentor"
        "other", "其他", "路人", "背景角色" -> "other"
        else -> "supporting"
    }

    private fun outlineMetadata(row: JsonObject): JsonObject {
        val metadata = ((row["metadata"] as? JsonObject)?.toMutableMap() ?: mutableMapOf())
        listOf(
            "scene_number",
            "purpose",
            "location",
            "timeline",
            "pov_character",
            "characters",
            "entry_state",
            "exit_state",
            "emotional_residue",
            "unresolved_actions",
        ).forEach { field ->
            if (field !in metadata) row[field]?.let { metadata[field] = it }
        }
        return JsonObject(metadata)
    }

    private fun creationStageLabel(stage: String): String = CREATION_STAGE_LABELS[stage] ?: stage

    private fun JsonObject.draft(): JsonObject = objectValue("draft")
    private fun JsonObject.stageState(stage: String): JsonObject = draft().objectValue("stages").objectValue(stage)
    private fun JsonObject.stageData(stage: String): JsonObject = stageState(stage)["data"] as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0

    private suspend fun resolvedDirectConfig(): DirectApiConfig {
        val config = directApiStore.read() ?: error("请先在设置中配置手机 API Key")
        if (config.protocol != DirectApiConfig.PROTOCOL_AUTO) return config
        val resolved = config.copy(protocol = directApi.testAndResolve(config).protocol)
        directApiStore.save(resolved)
        return resolved
    }

    suspend fun disconnect(clearOfflineData: Boolean): Boolean {
        val connection = dao.connection()
        val revokedRemotely = connection != null && runCatching {
            api.revokeSelf(connection)
        }.isSuccess
        tokenStore.clear()
        database.withTransaction {
            dao.deleteConnection()
            dao.clearCursor()
            if (clearOfflineData) {
                dao.clearOutbox()
                dao.clearConflicts()
                dao.clearReplicas()
            }
        }
        SyncScheduler.cancel(appContext)
        return revokedRemotely
    }

    private suspend fun requireConnection(): GatewayConnection =
        dao.connection() ?: error("请先连接自己的 Gateway")

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    companion object {
        private const val MAX_ENTITY_BYTES = 1024 * 1024
        private const val MAX_PUSH_BYTES = 6 * 1024 * 1024
        private const val CREATION_REPLICA_PROJECT = "__novel_creation__"
        private const val CREATION_HOST_GATEWAY = "gateway"
        private const val CREATION_HOST_DEVICE = "device"
        private val CREATION_STAGE_ORDER = listOf(
            "constraints",
            "concepts",
            "world_style",
            "characters",
            "locations",
            "macro_outline",
            "opening_outline",
            "final_review",
        )
        private val CREATION_STAGE_LABELS = mapOf(
            "constraints" to "创作约束",
            "concepts" to "创意方向",
            "world_style" to "文风与世界观",
            "characters" to "角色与关系",
            "locations" to "地点与势力",
            "macro_outline" to "全书主线与卷纲",
            "opening_outline" to "前3章细纲",
            "final_review" to "最终审阅",
        )
        private val CREATION_TERMINAL_RUN_STATUSES = setOf(
            "waiting_user",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "superseded",
        )
        private val GOVERNANCE_ENTITY_TYPES = setOf("foreshadowing", "governance")
        private val CANONICAL_DELETABLE_ENTITY_TYPES = setOf("chapter", "outline", "character", "world")
        private val CANONICAL_ENTITY_TYPES = CANONICAL_DELETABLE_ENTITY_TYPES +
            GOVERNANCE_ENTITY_TYPES + "project"
        private val RECORD_TYPES = mapOf(
            "project" to "project",
            "chapter" to "chapter",
            "outline" to "outline_node",
            "character" to "character",
            "world" to "world_entry",
            "foreshadowing" to "foreshadowing",
            "governance" to "narrative_debt",
        )
        private val syncMutex = Mutex()
    }
}

sealed interface SyncOutcome {
    data object Success : SyncOutcome
}

enum class AssistantRoute {
    GatewayPc,
    GatewayMobileKey,
    DirectApi,
}

enum class AssistantModelRoute {
    Pc,
    MobileKey,
}
