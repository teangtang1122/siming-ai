package com.siming.mobile.data

import android.content.Context
import androidx.room.withTransaction
import com.siming.mobile.BuildConfig
import com.siming.mobile.data.agent.MobileWorkspaceAgent
import com.siming.mobile.data.agent.MobileAssistantConversationStore
import com.siming.mobile.data.agent.mobileOutlineTreeHash
import com.siming.mobile.data.creation.CreationExecutionRoute
import com.siming.mobile.data.creation.CreationAgentProgressEvent
import com.siming.mobile.data.creation.CreationStartInput
import com.siming.mobile.data.creation.CreationAgentTurnRecords
import com.siming.mobile.data.creation.MobileCreationAgent
import com.siming.mobile.data.creation.MobileCreationConversationAgent
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.local.OutboxMutation
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDatabase
import com.siming.mobile.data.local.StoredProjectPackage
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
import com.siming.mobile.data.network.withMobileRefreshFailure
import com.siming.mobile.security.PairingSecurity
import com.siming.mobile.security.MobileProviderEncryption
import com.siming.mobile.security.SecureApiConfigStore
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import com.siming.mobile.security.VerifiedPairing
import java.io.IOException
import java.io.File
import java.net.URLDecoder
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
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
    private val mobileAssistantConversationStore = MobileAssistantConversationStore(appContext)
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
        availableModels: List<String>,
        taskModels: Map<String, String>,
    ): DirectApiSummary {
        val existing = directApiStore.read()
        val normalizedDefault = model.trim()
        val normalizedCatalog = (listOf(normalizedDefault) + availableModels + taskModels.values)
            .map(String::trim)
            .filter(String::isNotBlank)
            .distinct()
        val normalizedTasks = taskModels.mapNotNull { (taskType, taskModel) ->
            val normalizedModel = taskModel.trim()
            if (
                taskType !in DirectApiConfig.taskModelLabels ||
                normalizedModel.isBlank() ||
                normalizedModel == normalizedDefault ||
                normalizedModel !in normalizedCatalog
            ) {
                null
            } else {
                taskType to normalizedModel
            }
        }.toMap()
        val config = DirectApiConfig(
            displayName = displayName.trim().ifBlank { "自定义 API" },
            baseUrl = baseUrl.trim().trimEnd('/'),
            apiKey = apiKey.trim().ifBlank { existing?.apiKey.orEmpty() },
            model = normalizedDefault,
            protocol = protocol,
            availableModels = normalizedCatalog,
            taskModels = normalizedTasks,
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
        return bootstrapProjects(connection, projectIds)
    }

    private suspend fun bootstrapProjects(
        connection: GatewayConnection,
        projectIds: List<String>,
    ): Int {
        if (projectIds.isEmpty()) {
            dao.saveCursor(SyncCursor(cursor = 0, lastSuccessfulSyncAt = System.currentTimeMillis()))
            return 0
        }
        val response = api.bootstrap(connection, projectIds)
        database.withTransaction {
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

    suspend fun importNovel(
        file: MobileNovelImportFile,
        onProgress: suspend (String) -> Unit = {},
    ): MobileNovelImportResult {
        val extension = file.filename.substringAfterLast('.', "").lowercase()
        require(extension in NovelFileDecoder.supportedExtensions) { "仅支持导入 TXT、Markdown 或 DOCX 文件" }
        require(file.bytes.isNotEmpty()) { "导入文件内容为空" }
        require(file.bytes.size <= MAX_NOVEL_IMPORT_BYTES) {
            "单个导入文件不能超过 20 MiB"
        }

        val connection = dao.connection()
        if (connection != null && prepareCanonicalWrite()) {
            onProgress("正在将原始 ${extension.uppercase()} 一次性上传到 Gateway…")
            val remote = try {
                api.importNovelProject(connection, file.filename, file.bytes)
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                null
            }
            if (remote != null) {
                val projectId = remote.string("project_id")
                    .ifBlank { error("Gateway 批量导入结果缺少 project_id") }
                val chapterCount = remote.int("total")
                val encoding = remote.string("encoding").ifBlank { "未知" }
                onProgress("Gateway 已批量落库，正在下载作品离线副本…")
                val refreshWarning = runCatching {
                    bootstrapProjects(connection, listOf(projectId))
                }.exceptionOrNull()?.toUserFacingMessage()
                return MobileNovelImportResult(
                    projectId = projectId,
                    chapterCount = chapterCount,
                    encoding = encoding,
                    remote = true,
                    refreshWarning = refreshWarning,
                )
            }
        }
        return importNovelOffline(file, onProgress)
    }

    private suspend fun importNovelOffline(
        file: MobileNovelImportFile,
        onProgress: suspend (String) -> Unit,
    ): MobileNovelImportResult {
        val extension = file.filename.substringAfterLast('.', "").uppercase()
        onProgress("正在本机解析 $extension 正文…")
        val decoded = withContext(Dispatchers.Default) {
            NovelFileDecoder.decode(file.filename, file.bytes)
        }
        onProgress("正在本机识别章节边界…")
        val chapters = withContext(Dispatchers.Default) {
            NovelImportSplitter.split(decoded.text)
        }
        val projectId = UUID.randomUUID().toString()
        val title = file.filename.substringBeforeLast('.').trim().ifBlank { "导入作品" }
        val now = Instant.now().toString()
        val projectPayload = buildJsonObject {
            put("_record_type", "project")
            put("id", projectId)
            put("title", title.take(200))
            put("description", "由手机批量导入的已有小说")
            put("narrative_perspective", "third_person")
            put("writing_style", "natural")
            put("short_sentences", false)
            put("daily_word_goal", 6000)
        }

        onProgress("正在一个本地事务中写入 ${chapters.size} 章…")
        database.withTransaction {
            saveOfflineImportEntity(
                projectId = projectId,
                entityType = "project",
                entityId = projectId,
                payload = projectPayload,
                now = now,
            )
            chapters.forEachIndexed { index, chapter ->
                val chapterId = UUID.randomUUID().toString()
                saveOfflineImportEntity(
                    projectId = projectId,
                    entityType = "chapter",
                    entityId = chapterId,
                    payload = buildJsonObject {
                        put("_record_type", "chapter")
                        put("id", chapterId)
                        put("project_id", projectId)
                        put("title", chapter.title.take(200))
                        put("content", chapter.content)
                        put("word_count", chapter.wordCount)
                        put("current_version", 1)
                        put("sort_order", (index + 1) * 1000)
                    },
                    now = now,
                )
            }
        }
        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
        return MobileNovelImportResult(
            projectId = projectId,
            chapterCount = chapters.size,
            encoding = decoded.encoding,
            remote = false,
        )
    }

    private suspend fun saveOfflineImportEntity(
        projectId: String,
        entityType: String,
        entityId: String,
        payload: JsonObject,
        now: String,
    ) {
        validateEntitySize(payload)
        val encoded = json.encodeToString(payload)
        val mutationEncoded = canonicalMutationJson(
            projectId,
            entityType,
            entityId,
            encoded,
        ) ?: error("同步写入缺少 payload")
        dao.saveEntity(
            ReplicaEntity(
                key = ReplicaEntity.key(projectId, entityType, entityId),
                projectId = projectId,
                entityType = entityType,
                entityId = entityId,
                revision = 0,
                operation = "upsert",
                payloadJson = encoded,
                contentHash = sha256(encoded),
                serverModifiedAt = now,
                dirty = true,
                conflicted = false,
            ),
        )
        dao.saveMutation(
            OutboxMutation(
                mutationId = UUID.randomUUID().toString(),
                projectId = projectId,
                entityType = entityType,
                entityId = entityId,
                operation = "upsert",
                baseRevision = 0,
                payloadJson = mutationEncoded,
                clientModifiedAt = now,
            ),
        )
    }

    suspend fun importProjectPackage(
        file: MobileProjectPackageFile,
        newTitle: String? = null,
        onProgress: suspend (String) -> Unit = {},
    ): MobileProjectPackageImportResult {
        require(file.filename.lowercase().endsWith(PROJECT_PACKAGE_EXTENSION)) {
            "这里只接受 .siming-project；TXT、Markdown 或 DOCX 请使用“导入外部小说”"
        }
        require(file.file.isFile && file.sizeBytes > 0L) { "选择的项目包为空" }
        require(file.sizeBytes <= MAX_PROJECT_PACKAGE_BYTES) { "项目包不能超过 512 MiB" }
        require((newTitle?.trim()?.length ?: 0) <= 200) { "新作品标题不能超过 200 个字符" }
        onProgress("正在校验项目包格式、条目、大小和哈希…")
        val validated = withContext(Dispatchers.IO) {
            MobileProjectPackageValidator(file.file, file.sha256).validate()
        }
        val requestKey = UUID.randomUUID()
        val normalizedTitle = newTitle?.trim()?.takeIf(String::isNotBlank)
        val (projectId, replicas) = MobileProjectPackageMaterializer.materialize(
            validated,
            requestKey,
            normalizedTitle,
        )
        val retained = retainProjectPackage(file.file, requestKey)
        val now = Instant.now().toString()
        val stored = StoredProjectPackage(
            idempotencyKey = requestKey.toString(),
            packageId = validated.packageId,
            projectId = projectId,
            originalFilename = file.filename,
            localFilePath = retained.absolutePath,
            packageSha256 = validated.packageSha256,
            profile = validated.profile,
            requestedTitle = normalizedTitle,
        )
        try {
            onProgress("正在本机事务中恢复可编辑副本，并保留完整原始项目包…")
            database.withTransaction {
                check(dao.entity(ReplicaEntity.key(projectId, "project", projectId)) == null) {
                    "项目包导入目标已存在，请重新选择文件"
                }
                replicas.forEach { replica ->
                    val encoded = json.encodeToString(replica.payload)
                    dao.saveEntity(
                        ReplicaEntity(
                            key = ReplicaEntity.key(replica.projectId, replica.entityType, replica.entityId),
                            projectId = replica.projectId,
                            entityType = replica.entityType,
                            entityId = replica.entityId,
                            revision = 0,
                            operation = "upsert",
                            payloadJson = encoded,
                            contentHash = sha256(encoded),
                            serverModifiedAt = now,
                            dirty = false,
                            conflicted = false,
                        ),
                    )
                }
                dao.saveProjectPackage(stored)
            }
        } catch (error: Exception) {
            retained.delete()
            throw error
        }

        val projectTitle = replicas.first { it.entityType == "project" }.payload.string("title")
        val connection = dao.connection()
        if (connection != null) {
            onProgress("正在先上传完整项目包，再同步该作品的普通修改…")
            try {
                val result = uploadStoredProjectPackage(connection, stored)
                return MobileProjectPackageImportResult(
                    projectId = projectId,
                    projectTitle = result.string("project_title").ifBlank { projectTitle },
                    profile = validated.profile,
                    remote = true,
                    replayed = (result["replayed"] as? JsonPrimitive)?.booleanOrNull ?: false,
                )
            } catch (error: GatewayHttpException) {
                purgeLocalProject(projectId)
                throw error
            } catch (_: IOException) {
                // A validated local copy remains queued and is uploaded before
                // ordinary outbox mutations on the next successful sync.
            }
        }
        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
        return MobileProjectPackageImportResult(
            projectId = projectId,
            projectTitle = projectTitle,
            profile = validated.profile,
            remote = false,
        )
    }

    private fun retainProjectPackage(source: File, requestKey: UUID): File {
        val root = File(appContext.filesDir, "project-packages").apply { mkdirs() }
        val destination = File(root, "$requestKey$PROJECT_PACKAGE_EXTENSION")
        if (!source.renameTo(destination)) {
            source.inputStream().buffered().use { input ->
                destination.outputStream().buffered().use { output -> input.copyTo(output, 1024 * 1024) }
            }
            source.delete()
        }
        return destination
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

    suspend fun deleteProject(projectId: String) = canonicalCommandMutex.withLock {
        val key = ReplicaEntity.key(projectId, "project", projectId)
        val current = dao.entity(key) ?: return@withLock
        require(!current.conflicted) { "请先处理这部作品的版本分岔，再执行删除" }

        val connection = dao.connection()
        if (connection != null) {
            val canonicalReady = prepareCanonicalWrite()
            if (canonicalReady) {
                try {
                    api.deleteProject(connection, projectId)
                    purgeLocalProject(projectId)
                    return@withLock
                } catch (error: GatewayHttpException) {
                    throw error
                } catch (_: IOException) {
                    // A canonical project must not be converted into a local-only
                    // delete when the PC is unreachable: it would be resurrected
                    // on the next authoritative pull.
                }
            }
        }

        check(isUnsyncedLocalProject(current)) {
            "这部作品已经进入 PC 权威库；请连接 PC Gateway 后再删除，避免下次同步把作品重新拉回手机"
        }
        purgeLocalProject(projectId)
    }

    private suspend fun isUnsyncedLocalProject(project: ReplicaEntity): Boolean {
        val pending = dao.pendingMutation(project.projectId, "project", project.projectId)
        return project.dirty &&
            project.revision == 0L &&
            pending?.operation == "upsert" &&
            pending.baseRevision == 0L
    }

    private suspend fun purgeLocalProject(projectId: String) {
        val storedPackage = dao.projectPackage(projectId)
        database.withTransaction {
            dao.deleteProjectMutations(projectId)
            dao.deleteProjectConflicts(projectId)
            dao.deleteProjectReplica(projectId)
            dao.deleteProjectPackage(projectId)
        }
        storedPackage?.localFilePath?.let(::File)?.delete()
    }

    suspend fun deleteEntity(projectId: String, entityType: String, entityId: String) {
        require(entityType != "project") { "整部作品请使用作品库的删除操作" }
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

    private suspend fun canonicalCommandConnection(): GatewayConnection {
        val connection = requireConnection()
        check(prepareCanonicalWrite()) {
            "当前无法连接 PC Gateway，高级结构命令不会在手机端猜测执行"
        }
        return connection
    }

    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        canonicalCommandMutex.withLock {
            val connection = canonicalCommandConnection()
            val result = api.reorderChapters(connection, projectId, chapterIds)
            refreshAfterCanonicalWrite(connection, projectId, result)
        }

    suspend fun reorderOutline(
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject = canonicalCommandMutex.withLock {
        require(nodeIds.distinct().size == nodeIds.size) { "大纲排序包含重复节点" }
        val connection = dao.connection()
        if (connection != null && prepareCanonicalWrite()) {
            try {
                val result = api.reorderOutline(connection, projectId, parentId, nodeIds)
                return@withLock refreshAfterCanonicalWrite(connection, projectId, result)
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                // Reordering is replay-safe because each outline node already
                // carries parent_id + sort_order in the canonical mutation.
            }
        }
        reorderOutlineOffline(projectId, parentId, nodeIds)
    }

    private suspend fun reorderOutlineOffline(
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject {
        nodeIds.forEachIndexed { index, nodeId ->
            val key = ReplicaEntity.key(projectId, "outline", nodeId)
            val current = dao.entity(key) ?: error("大纲节点不存在：$nodeId")
            require(!current.conflicted) { "请先处理大纲节点的版本分岔，再调整顺序" }
            val payload = current.payloadJson
                ?.let(json::parseToJsonElement)
                as? JsonObject
                ?: error("大纲节点缺少结构化数据")
            val actualParent = (payload["parent_id"] as? JsonPrimitive)?.contentOrNull
                ?.takeIf { it.isNotBlank() }
            require(actualParent == parentId) { "只能调整同一父节点下的大纲顺序" }
            val reordered = JsonObject(
                payload.toMutableMap().apply {
                    put("sort_order", JsonPrimitive(index))
                },
            )
            saveOfflineEntity(projectId, "outline", nodeId, reordered)
        }
        return buildJsonObject {
            put("mode", "offline_replay")
            put("parent_id", parentId?.let(::JsonPrimitive) ?: JsonNull)
            put("ids", JsonArray(nodeIds.map(::JsonPrimitive)))
        }
    }

    private suspend fun refreshAfterCanonicalWrite(
        connection: GatewayConnection,
        projectId: String,
        result: JsonObject,
    ): JsonObject = try {
        pullAll(connection, listOf(projectId))
        result
    } catch (error: CancellationException) {
        throw error
    } catch (error: Exception) {
        result.withMobileRefreshFailure(error.toUserFacingMessage())
    }


suspend fun runCataloging(
    projectId: String,
    onProgress: suspend (MobileCatalogingProgress, String?) -> Unit,
): MobileCatalogingProgress {
    val connection = canonicalCommandConnection()
    val chapters = orderReplicaEntities(
        "chapter",
        dao.projectSnapshot(projectId).filter { it.entityType == "chapter" && it.operation == "upsert" },
    )
    require(chapters.isNotEmpty()) { "作品没有可建档章节" }
    val started = api.startCataloging(connection, projectId, chapters.map { it.entityId })
    var latest = started.toMobileCatalogingProgress()
    onProgress(latest, "作品建档任务已创建")
    api.streamCataloging(connection, projectId, latest.jobId) { raw ->
        val event = runCatching { json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
        val job = event?.get("job") as? JsonObject
        if (job != null) latest = job.toMobileCatalogingProgress()
        val message = (event?.get("message") as? JsonPrimitive)?.contentOrNull
            ?: (event?.get("detail") as? JsonPrimitive)?.contentOrNull
            ?: (event?.get("type") as? JsonPrimitive)?.contentOrNull
        onProgress(latest, message)
    }
    val finalData = api.getCatalogingJob(connection, projectId, latest.jobId)
    val finalJob = finalData["job"] as? JsonObject
    if (finalJob != null) latest = finalJob.toMobileCatalogingProgress()
    runCatching { pullAll(connection, listOf(projectId)) }
    return latest
}

suspend fun cancelCataloging(projectId: String, jobId: String) {
    val connection = requireConnection()
    api.cancelCataloging(connection, projectId, jobId)
}

suspend fun exportProject(projectId: String, format: String): MobileExportFile {
    val normalized = format.lowercase()
    require(normalized in setOf("txt", "docx", "pdf")) { "不支持的导出格式：$format" }
    val project = dao.entity(ReplicaEntity.key(projectId, "project", projectId))
        ?: error("作品不存在")
    val connection = dao.connection()
    if (connection == null) {
        require(normalized == "txt") { "Word / PDF 导出需要连接 PC Gateway" }
        val chapters = orderReplicaEntities(
            "chapter",
            dao.projectSnapshot(projectId).filter { it.entityType == "chapter" && it.operation == "upsert" },
        )
        return buildLocalNovelExport(project, chapters)
    }
    check(prepareCanonicalWrite()) { "当前无法同步本机修改，请恢复 Gateway 连接后再导出" }
    val metadata = api.createProjectExport(connection, projectId, normalized)
    val fileId = (metadata["file_id"] as? JsonPrimitive)?.contentOrNull
        ?: error("PC 导出结果缺少 file_id")
    val filename = (metadata["filename"] as? JsonPrimitive)?.contentOrNull
        ?: "司命导出.$normalized"
    return MobileExportFile(
        filename = filename,
        mimeType = exportMimeType(normalized),
        bytes = api.downloadProjectExport(connection, projectId, fileId),
    )
}

suspend fun exportProjectPackage(projectId: String, profile: String): MobileExportFile {
    val normalized = profile.lowercase()
    require(normalized in setOf("full", "structure")) { "项目包档位只能是完整或结构" }
    val project = dao.entity(ReplicaEntity.key(projectId, "project", projectId))
        ?: error("作品不存在")
    val title = project.payloadJson
        ?.let { runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
        ?.string("title")
        .orEmpty()
        .ifBlank { "未命名作品" }
    val exportRoot = File(appContext.cacheDir, "project-package-exports").apply { mkdirs() }
    val destination = File(exportRoot, "${UUID.randomUUID()}$PROJECT_PACKAGE_EXTENSION")
    val connection = dao.connection()
    val filename = if (connection != null) {
        check(prepareCanonicalWrite()) { "当前无法同步本机修改，请恢复 Gateway 连接后再导出项目包" }
        api.downloadProjectPackage(connection, projectId, normalized, destination)
            ?.let { URLDecoder.decode(it, Charsets.UTF_8.name()) }
    } else {
        val stored = dao.projectPackage(projectId)
        val snapshot = dao.projectPackageSnapshot(projectId)
        val draft = mobileWorkspaceAgent.pendingChapterDraft(projectId)
            ?.let { MobilePendingChapterDraft.fromJson(projectId, it) }
        if (stored != null) {
            val source = File(stored.localFilePath)
            require(source.isFile && sha256File(source) == stored.packageSha256) { "本机项目包副本已损坏" }
            withContext(Dispatchers.IO) {
                MobileProjectPackageWriter.rewriteImported(
                    source = source,
                    expectedSha256 = stored.packageSha256,
                    idempotencyKey = UUID.fromString(stored.idempotencyKey),
                    projectId = projectId,
                    snapshot = snapshot,
                    pendingDraft = draft,
                    profile = normalized,
                    destination = destination,
                )
            }
            null
        } else {
            withContext(Dispatchers.IO) {
                MobileProjectPackageWriter.write(projectId, snapshot, draft, normalized, destination)
            }
            null
        }
    }
    val safeTitle = title.replace(Regex("[\\\\/:*?\"<>|]"), "_").take(80).ifBlank { "司命导出" }
    val profileLabel = if (normalized == "full") "完整" else "结构"
    return MobileExportFile(
        filename = filename?.takeIf { it.endsWith(PROJECT_PACKAGE_EXTENSION, ignoreCase = true) }
            ?: "${safeTitle}_${profileLabel}项目包$PROJECT_PACKAGE_EXTENSION",
        mimeType = PROJECT_PACKAGE_MEDIA_TYPE,
        sourceFilePath = destination.absolutePath,
        deleteSourceAfterSave = true,
    )
}

    suspend fun listChapterSnapshots(projectId: String, chapterId: String): JsonObject =
        api.listChapterSnapshots(requireConnection(), projectId, chapterId)

    suspend fun getChapterSnapshot(
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = api.getChapterSnapshot(
        requireConnection(),
        projectId,
        chapterId,
        snapshotId,
    )

    suspend fun diffChapterSnapshots(
        projectId: String,
        chapterId: String,
        fromSnapshotId: String,
        toSnapshotId: String,
    ): JsonObject = api.diffChapterSnapshots(
        requireConnection(),
        projectId,
        chapterId,
        fromSnapshotId,
        toSnapshotId,
    )

    suspend fun restoreChapterSnapshot(
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = canonicalCommandMutex.withLock {
        val connection = canonicalCommandConnection()
        val result = api.restoreChapterSnapshot(connection, projectId, chapterId, snapshotId)
        refreshAfterCanonicalWrite(connection, projectId, result)
    }

    suspend fun characterRelationshipNetwork(projectId: String): JsonObject =
        api.getCharacterRelationshipNetwork(requireConnection(), projectId)

    suspend fun replaceCharacterRelationships(
        projectId: String,
        characterId: String,
        relationships: JsonArray,
    ): JsonObject = canonicalCommandMutex.withLock {
        val connection = canonicalCommandConnection()
        val result = api.replaceCharacterRelationships(
            connection,
            projectId,
            characterId,
            buildJsonObject { put("relationships", relationships) },
        )
        refreshAfterCanonicalWrite(connection, projectId, result)
    }

    suspend fun characterAiConfig(projectId: String, characterId: String): JsonObject =
        api.getCharacterAiConfig(requireConnection(), projectId, characterId)

    suspend fun updateCharacterAiConfig(
        projectId: String,
        characterId: String,
        payload: JsonObject,
    ): JsonObject = canonicalCommandMutex.withLock {
        val connection = canonicalCommandConnection()
        val result = api.updateCharacterAiConfig(connection, projectId, characterId, payload)
        refreshAfterCanonicalWrite(connection, projectId, result)
    }

    suspend fun characterVersions(projectId: String, characterId: String): JsonObject =
        api.listCharacterVersions(requireConnection(), projectId, characterId)

    suspend fun characterVersion(
        projectId: String,
        characterId: String,
        versionId: String,
    ): JsonObject = api.getCharacterVersion(
        requireConnection(),
        projectId,
        characterId,
        versionId,
    )

    suspend fun worldVersions(projectId: String, entryId: String): JsonObject =
        api.listWorldVersions(requireConnection(), projectId, entryId)

    suspend fun worldTimeline(projectId: String, entryId: String): JsonObject =
        api.listWorldTimeline(requireConnection(), projectId, entryId)

    private suspend fun uploadPendingProjectPackages(connection: GatewayConnection) {
        dao.pendingProjectPackages().forEach { stored -> uploadStoredProjectPackage(connection, stored) }
    }

    private suspend fun uploadStoredProjectPackage(
        connection: GatewayConnection,
        stored: StoredProjectPackage,
    ): JsonObject {
        val source = File(stored.localFilePath)
        require(source.isFile) { "待同步项目包的本地副本不存在：${stored.originalFilename}" }
        require(source.length() <= MAX_PROJECT_PACKAGE_BYTES) { "待同步项目包超过 512 MiB 上限" }
        require(withContext(Dispatchers.IO) { sha256File(source) } == stored.packageSha256) {
            "待同步项目包的本地副本已损坏：${stored.originalFilename}"
        }
        dao.saveProjectPackage(stored.copy(syncState = "uploading", lastError = null))
        try {
            val result = api.importProjectPackage(
                connection = connection,
                filename = stored.originalFilename,
                file = source,
                idempotencyKey = stored.idempotencyKey,
                newTitle = stored.requestedTitle,
            )
            require(result.string("project_id") == stored.projectId) {
                "Gateway 返回的项目包作品 ID 与本机确定性 ID 不一致"
            }
            require(result.string("package_id") == stored.packageId) {
                "Gateway 返回的项目包 ID 与本机副本不一致"
            }
            refreshUploadedProjectPackage(connection, stored.projectId)
            dao.saveProjectPackage(
                stored.copy(
                    syncState = "succeeded",
                    lastError = null,
                    uploadedAt = System.currentTimeMillis(),
                ),
            )
            return result
        } catch (error: Exception) {
            dao.saveProjectPackage(
                stored.copy(
                    syncState = "pending",
                    lastError = error.toUserFacingMessage(),
                ),
            )
            throw error
        }
    }

    private suspend fun refreshUploadedProjectPackage(
        connection: GatewayConnection,
        projectId: String,
    ) {
        val response = api.bootstrap(connection, listOf(projectId))
        database.withTransaction {
            dao.deleteCleanProjectReplicas(projectId)
            response.entities.filter { it.projectId == projectId }.forEach { snapshot ->
                val key = ReplicaEntity.key(snapshot.projectId, snapshot.entityType, snapshot.entityId)
                val current = dao.entity(key)
                if (current?.dirty == true) {
                    dao.saveEntity(
                        current.copy(
                            revision = snapshot.revision,
                            serverModifiedAt = snapshot.serverModifiedAt,
                        ),
                    )
                    dao.pendingMutation(snapshot.projectId, snapshot.entityType, snapshot.entityId)?.let { mutation ->
                        dao.updateMutation(mutation.copy(baseRevision = snapshot.revision))
                    }
                } else {
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
            }
            dao.saveCursor(
                SyncCursor(
                    cursor = response.cursor,
                    lastSuccessfulSyncAt = System.currentTimeMillis(),
                ),
            )
        }
    }

    suspend fun syncNow(): SyncOutcome = syncMutex.withLock {
        val connection = requireConnection()
        val localProjectIds = dao.localProjectIds()
        try {
            uploadPendingProjectPackages(connection)
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
        prompt: String,
        modelRoute: AssistantModelRoute,
        conversationId: String? = null,
        history: List<JsonObject> = emptyList(),
        onEvent: suspend (String) -> Unit,
    ): AssistantRoute {
        val connection = dao.connection()
        if (connection != null) {
            val directConfig = if (modelRoute == AssistantModelRoute.MobileKey) {
                resolvedDirectConfig(DirectApiConfig.TASK_ASSISTANT)
            } else {
                null
            }
            var runId: String? = null
            val trackingEvent: suspend (String) -> Unit = { raw ->
                runCatching { json.parseToJsonElement(raw) as? JsonObject }.getOrNull()?.let { event ->
                    if (event.string("type") == "run") {
                        val run = event["run"] as? JsonObject
                        runId = run?.let { value ->
                            value.string("run_id").ifBlank { value.string("id") }
                        }
                    }
                }
                onEvent(raw)
            }
            try {
                api.streamAssistant(
                    connection,
                    projectId,
                    WorkspaceAssistantRequest(
                        message = prompt,
                        conversationId = conversationId,
                        history = history,
                        modelRoute = if (directConfig == null) "pc" else "mobile",
                        mobileProvider = directConfig?.let {
                            MobileProviderEncryption.seal(it, connection, projectId)
                        },
                    ),
                    trackingEvent,
                )
            } catch (error: IOException) {
                val recoverableRunId = runId
                if (recoverableRunId.isNullOrBlank()) throw error
                recoverAssistantRun(connection, projectId, recoverableRunId, trackingEvent, error)
            }
            syncNow()
            return if (directConfig == null) AssistantRoute.GatewayPc else AssistantRoute.GatewayMobileKey
        }

        val directConfig = directApiStore.read()?.let {
            resolvedDirectConfig(DirectApiConfig.TASK_ASSISTANT)
        }
        if (directConfig != null) {
            val turnContext = mobileAssistantConversationStore.beginTurn(
                projectId = projectId,
                conversationId = conversationId,
                prompt = prompt,
            )
            onEvent(buildJsonObject {
                put("type", "conversation")
                put("conversation", buildJsonObject { put("id", turnContext.conversationId) })
            }.toString())
            val output = StringBuilder()
            val toolLogs = mutableListOf<String>()
            var draftProduced = false
            var outlineDraftProduced = false
            val trackedEvent: suspend (String) -> Unit = { raw ->
                runCatching { json.parseToJsonElement(raw) as? JsonObject }.getOrNull()?.let { event ->
                    when (event.string("type")) {
                        "content_delta" -> output.append(event.string("delta"))
                        "tool" -> event.string("detail").takeIf(String::isNotBlank)?.let(toolLogs::add)
                        "chapter_draft" -> draftProduced = true
                        "outline_draft" -> outlineDraftProduced = true
                        else -> Unit
                    }
                }
                onEvent(raw)
            }
            try {
                mobileWorkspaceAgent.run(
                    projectId = projectId,
                    prompt = prompt,
                    config = directConfig,
                    history = turnContext.history.takeLast(12).map { message ->
                        buildJsonObject {
                            put("role", message.role)
                            put("content", message.content)
                        }
                    },
                    onEvent = trackedEvent,
                )
                mobileAssistantConversationStore.finishTurn(
                    projectId = projectId,
                    conversationId = turnContext.conversationId,
                    content = output.toString().trim().ifBlank {
                        when {
                            draftProduced -> "章节草稿已交给正文编辑器，尚未保存。"
                            outlineDraftProduced -> "大纲草稿已交给结构页，尚未确认。"
                            else -> "本轮任务已完成。"
                        }
                    },
                    status = "completed",
                    toolLogs = toolLogs,
                )
            } catch (error: Exception) {
                mobileAssistantConversationStore.finishTurn(
                    projectId = projectId,
                    conversationId = turnContext.conversationId,
                    content = output.toString().trim().ifBlank {
                        if (error is CancellationException) "任务已取消。" else "任务未完成：${error.message.orEmpty()}"
                    },
                    status = if (error is CancellationException) "aborted" else "error",
                    toolLogs = toolLogs,
                )
                throw error
            }
            return AssistantRoute.DirectApi
        }
        error("请先配置手机直连 API，或连接自己的 Gateway")
    }

    suspend fun pendingChapterDraft(projectId: String): MobilePendingChapterDraft? {
        val connection = dao.connection()
        val value = if (connection != null) {
            api.pendingChapterDraft(connection, projectId) ?: importedPendingChapterDraft(projectId)
        } else {
            mobileWorkspaceAgent.pendingChapterDraft(projectId) ?: importedPendingChapterDraft(projectId)
        } ?: return null
        return MobilePendingChapterDraft.fromJson(projectId, value)
    }

    suspend fun pendingOutlineDraft(projectId: String): MobilePendingOutlineDraft? {
        val connection = dao.connection()
        val value = if (connection != null) {
            api.pendingOutlineDraft(connection, projectId)
        } else {
            mobileWorkspaceAgent.pendingOutlineDraft(projectId)
        } ?: return null
        return MobilePendingOutlineDraft.fromJson(projectId, value)
    }

    suspend fun updatePendingOutlineDraft(
        draft: MobilePendingOutlineDraft,
        nodes: List<MobileOutlineDraftNode>,
        designNotes: String,
    ): MobilePendingOutlineDraft {
        require(nodes.isNotEmpty()) { "大纲草稿至少需要一个节点" }
        require(nodes.size <= 8) { "单次大纲草稿最多包含 8 个节点" }
        val payload = buildJsonObject {
            put("nodes", JsonArray(nodes.map(MobileOutlineDraftNode::toJson)))
            put("design_notes", designNotes)
        }
        val connection = dao.connection()
        val value = if (connection != null) {
            api.updateOutlineDraft(connection, draft.projectId, draft.draftId, payload)
        } else {
            mobileWorkspaceAgent.updateOutlineDraft(
                draft.draftId,
                payload["nodes"] as JsonArray,
                designNotes,
            ) ?: error("大纲草稿不存在或已处理")
        }
        return MobilePendingOutlineDraft.fromJson(draft.projectId, value)
            ?: error("大纲草稿返回结构无效")
    }

    suspend fun discardPendingOutlineDraft(draft: MobilePendingOutlineDraft) {
        val connection = dao.connection()
        if (connection != null) {
            api.discardOutlineDraft(connection, draft.projectId, draft.draftId)
        } else {
            mobileWorkspaceAgent.discardOutlineDraft(draft.draftId)
                ?: error("大纲草稿不存在")
        }
    }

    suspend fun regeneratePendingOutlineDraft(draft: MobilePendingOutlineDraft): String {
        val connection = dao.connection()
        if (connection != null) {
            val response = api.regenerateOutlineDraft(connection, draft.projectId, draft.draftId)
            return (response["next_author_request"] as? JsonObject)
                ?.string("message")
                .orEmpty()
                .ifBlank { "请重新规划刚才的大纲草稿，保留作者已指定的插入位置。" }
        }
        mobileWorkspaceAgent.supersedeOutlineDraft(draft.draftId)
            ?: error("大纲草稿不存在")
        return "请重新规划刚才的大纲草稿，保留作者已指定的插入位置。"
    }

    suspend fun confirmPendingOutlineDraft(
        draft: MobilePendingOutlineDraft,
        writeAfterConfirm: Boolean,
    ): MobileOutlineDraftConfirmation {
        val connection = dao.connection()
        if (connection != null) {
            val response = api.confirmOutlineDraft(
                connection,
                draft.projectId,
                draft.draftId,
                writeAfterConfirm,
            )
            syncNow()
            return MobileOutlineDraftConfirmation(
                savedOutlineNodeIds = response.stringList("saved_outline_node_ids"),
                chapterOutlineNodeIds = response.stringList("chapter_outline_node_ids"),
                nextAuthorMessage = (response["next_author_request"] as? JsonObject)
                    ?.string("message")
                    ?.takeIf(String::isNotBlank),
            )
        }

        val current = mobileWorkspaceAgent.pendingOutlineDraft(draft.projectId)
            ?.let { MobilePendingOutlineDraft.fromJson(draft.projectId, it) }
            ?.takeIf { it.draftId == draft.draftId }
            ?: error("大纲草稿不存在或已处理")
        val snapshot = dao.projectSnapshot(draft.projectId)
        val existing = snapshot
            .filter { it.entityType == "outline" && it.operation == "upsert" }
            .mapNotNull { entity ->
                val payload = entity.payloadJson
                    ?.let { runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
                    ?: return@mapNotNull null
                entity to payload
            }
        val characterIdsByReference = linkedMapOf<String, String>()
        snapshot.asSequence()
            .filter { it.entityType == "character" && it.operation == "upsert" }
            .forEach { entity ->
                val payload = entity.payloadJson
                    ?.let { runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
                    ?: return@forEach
                characterIdsByReference.putIfAbsent(entity.entityId, entity.entityId)
                payload.string("name").trim().takeIf(String::isNotBlank)?.let { name ->
                    characterIdsByReference.putIfAbsent(name, entity.entityId)
                }
            }
        val insertAfter = current.insertAfterId?.let { id ->
            existing.firstOrNull { (entity, _) -> entity.entityId == id }
        }
        val resolvedParentId = current.parentId ?: insertAfter?.second?.string("parent_id")?.ifBlank { null }
        require(current.parentId == null || existing.any { (entity, _) -> entity.entityId == current.parentId }) {
            "大纲父节点已变化，请重新生成提案"
        }
        require(current.insertAfterId == null || insertAfter != null) {
            "大纲插入位置已变化，请重新生成提案"
        }
        require(mobileOutlineTreeHash(existing.map { (_, payload) -> payload }) == current.baseOutlineHash) {
            "正式大纲在提案生成后已变化，请重新生成后再确认"
        }
        require(current.nodes.size in 1..8) { "单次大纲草稿必须包含 1 至 8 个节点" }
        val titles = current.nodes.map { it.title.trim() }
        require(titles.all { it.isNotBlank() && it.length <= 200 }) {
            "大纲节点标题必须为 1 至 200 个字符"
        }
        require(titles.distinct().size == titles.size) { "大纲节点标题不能重复" }
        val nodesByTitle = current.nodes.associateBy { it.title.trim() }
        val allowedChildTypes: Map<String?, Set<String>> = mapOf(
            null to setOf("volume", "chapter"),
            "volume" to setOf("chapter"),
            "chapter" to setOf("section"),
            "section" to emptySet(),
        )
        require(current.nodes.all { it.nodeType in setOf("volume", "chapter", "section") }) {
            "大纲节点类型无效"
        }
        val visiting = mutableSetOf<String>()
        val visited = mutableSetOf<String>()
        val ordered = mutableListOf<MobileOutlineDraftNode>()
        fun visit(node: MobileOutlineDraftNode) {
            val title = node.title.trim()
            if (title in visited) return
            require(visiting.add(title)) { "大纲草稿父子关系形成循环" }
            val parentTitle = node.parentTitle?.trim().orEmpty()
            if (parentTitle.isNotBlank()) {
                val draftParent = nodesByTitle[parentTitle]
                    ?: error("大纲节点引用了本草稿中不存在的父标题：$parentTitle")
                visit(draftParent)
            }
            visiting.remove(title)
            visited += title
            ordered += node
        }
        current.nodes.forEach(::visit)
        val topLevel = ordered.filter { it.parentTitle.isNullOrBlank() }
        require(topLevel.isNotEmpty()) { "大纲草稿没有可保存的顶层节点" }
        val formalParentType = resolvedParentId
            ?.let { id -> existing.firstOrNull { (entity, _) -> entity.entityId == id } }
            ?.second
            ?.string("node_type")
            ?.ifBlank { null }
        ordered.forEach { node ->
            val parentTitle = node.parentTitle?.trim().orEmpty()
            val parentType = if (parentTitle.isBlank()) {
                formalParentType
            } else {
                nodesByTitle.getValue(parentTitle).nodeType
            }
            require(node.nodeType in allowedChildTypes[parentType].orEmpty()) {
                "当前大纲位置不能创建 ${node.nodeType} 类型节点"
            }
        }
        val existingTopTitles = existing.asSequence()
            .filter { (_, payload) ->
                payload.string("parent_id").ifBlank { null } == resolvedParentId
            }
            .map { (_, payload) -> payload.string("title") }
            .toSet()
        require(topLevel.none { it.title.trim() in existingTopTitles }) {
            "正式大纲中已存在同名节点"
        }
        val characterLinksByTitle = ordered.associate { node ->
            node.title.trim() to mobileOutlineCharacterLinks(
                node.characterNames,
                characterIdsByReference,
            )
        }
        val firstSort = insertAfter?.second?.string("sort_order")?.toIntOrNull()?.plus(1)
            ?: existing.asSequence()
                .map { it.second }
                .filter { it.string("parent_id").ifBlank { null } == resolvedParentId }
                .mapNotNull { it.string("sort_order").toIntOrNull() }
                .maxOrNull()
                ?.plus(1)
            ?: 0
        val savedIds = mutableListOf<String>()
        val chapterIds = mutableListOf<String>()
        val idsByTitle = linkedMapOf<String, String>()
        suspend fun saveNode(node: MobileOutlineDraftNode, parentId: String?, sortOrder: Int): String {
            require(node.title.isNotBlank()) { "大纲节点标题不能为空" }
            val id = UUID.randomUUID().toString()
            val payload = buildJsonObject {
                put("_record_type", "outline_node")
                put("id", id)
                put("project_id", draft.projectId)
                put("node_type", node.nodeType)
                put("title", node.title.trim())
                put("summary", node.summary.trim())
                put("status", "pending")
                if (parentId == null) put("parent_id", JsonNull) else put("parent_id", parentId)
                put("sort_order", sortOrder)
                put("characters", characterLinksByTitle.getValue(node.title.trim()))
            }
            saveEntity(draft.projectId, "outline", id, payload)
            savedIds += id
            if (node.nodeType == "chapter") chapterIds += id
            return id
        }
        database.withTransaction {
            existing.asSequence()
                .filter { (_, payload) ->
                    payload.string("parent_id").ifBlank { null } == resolvedParentId &&
                        (payload.string("sort_order").toIntOrNull() ?: 0) >= firstSort
                }
                .sortedByDescending { (_, payload) -> payload.string("sort_order").toIntOrNull() ?: 0 }
                .forEach { (entity, payload) ->
                    saveEntity(
                        draft.projectId,
                        "outline",
                        entity.entityId,
                        JsonObject(
                            payload.toMutableMap().apply {
                                put(
                                    "sort_order",
                                    JsonPrimitive(
                                        (payload.string("sort_order").toIntOrNull() ?: 0) + topLevel.size,
                                    ),
                                )
                            },
                        ),
                    )
                }
            topLevel.forEachIndexed { index, node ->
                idsByTitle[node.title.trim()] = saveNode(node, resolvedParentId, firstSort + index)
            }
            val childSort = mutableMapOf<String, Int>()
            ordered.filter { !it.parentTitle.isNullOrBlank() }.forEach { node ->
                val parentId = requireNotNull(idsByTitle[node.parentTitle?.trim()])
                val sortOrder = childSort.getOrDefault(parentId, 0)
                idsByTitle[node.title.trim()] = saveNode(node, parentId, sortOrder)
                childSort[parentId] = sortOrder + 1
            }
        }
        mobileWorkspaceAgent.markOutlineDraftConfirmed(draft.draftId, savedIds)
        val nextMessage = if (writeAfterConfirm && chapterIds.isNotEmpty()) {
            "请根据刚确认的章级大纲（ID：${chapterIds.first()}）写这一章。"
        } else {
            null
        }
        return MobileOutlineDraftConfirmation(savedIds, chapterIds, nextMessage)
    }

    suspend fun savePendingChapterDraft(
        draft: MobilePendingChapterDraft,
        title: String,
        content: String,
        catalogingMode: String,
    ): String {
        require(title.isNotBlank()) { "章节标题不能为空" }
        require(catalogingMode in setOf("save_only", "save_and_catalog")) { "未知的章节保存方式" }
        val connection = dao.connection()
        if (connection != null) {
            val payload = buildJsonObject {
                put("title", title.trim())
                put("content", content)
                draft.outlineNodeId?.let { put("outline_node_id", it) }
                draft.contextManifestId?.let { put("context_manifest_id", it) }
                put("draft_id", draft.draftId)
                put("cataloging_mode", catalogingMode)
                if (draft.revision) {
                    put("expected_version", requireNotNull(draft.baseChapterVersion) {
                        "修订候选缺少基准版本，不能安全保存"
                    })
                    put("trigger_type", "ai_revision")
                }
            }
            val response = if (draft.revision) {
                require(!draft.versionConflict) { "正式章节版本已变化，请重新生成或人工合并修订候选" }
                api.saveGeneratedChapterRevision(
                    connection,
                    draft.projectId,
                    requireNotNull(draft.targetChapterId) { "修订候选缺少目标章节" },
                    payload,
                )
            } else {
                api.saveGeneratedChapter(connection, draft.projectId, payload)
            }
            val chapterId = response.requiredId()
            saveCanonicalReplica(draft.projectId, "chapter", chapterId, response)
            markChapterDraftConsumed(draft)
            SyncScheduler.enqueue(appContext)
            return chapterId
        }
        require(!draft.revision) { "修订已有章节需要连接 PC，以核对正式章节版本后保存" }
        require(catalogingMode == "save_only") { "手机独立模式需先仅保存；连接 PC Gateway 后才能启动建档" }
        val chapterId = UUID.randomUUID().toString()
        val payload = buildJsonObject {
            put("id", chapterId)
            put("project_id", draft.projectId)
            put("title", title.trim())
            put("content", content)
            put("word_count", content.count { !it.isWhitespace() })
            put("current_version", 1)
            draft.outlineNodeId?.let { put("outline_node_id", it) }
            draft.contextManifestId?.let { put("context_manifest_id", it) }
        }
        saveEntity(draft.projectId, "chapter", chapterId, payload)
        markChapterDraftConsumed(draft)
        return chapterId
    }

    private suspend fun importedPendingChapterDraft(projectId: String): JsonObject? =
        dao.projectSnapshot(projectId)
            .asSequence()
            .filter { it.entityType == "chapter_draft" && it.operation == "upsert" }
            .mapNotNull { entity ->
                val payload = entity.payloadJson
                    ?.let { runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
                    ?: return@mapNotNull null
                if (payload.string("status") !in setOf("pending", "generated", "generating")) {
                    return@mapNotNull null
                }
                buildJsonObject {
                    put("draft_id", entity.entityId)
                    put("project_id", projectId)
                    put("content_ref", entity.entityId)
                    put("title", payload.string("title").ifBlank { "未保存章节草稿" })
                    payload.string("outline_node_id").takeIf(String::isNotBlank)?.let { put("outline_node_id", it) }
                    put("draft_status", payload.string("status"))
                    put("content", payload.string("content"))
                    put("execution_route", "project_package")
                }
            }
            .firstOrNull()

    private suspend fun markChapterDraftConsumed(draft: MobilePendingChapterDraft) {
        mobileWorkspaceAgent.markChapterDraftSaved(draft.draftId)
        val key = ReplicaEntity.key(draft.projectId, "chapter_draft", draft.draftId)
        val entity = dao.entity(key) ?: return
        val payload = entity.payloadJson
            ?.let { runCatching { json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
            ?: return
        val now = Instant.now().toString()
        val encoded = json.encodeToString(
            JsonObject(
                payload.toMutableMap().apply {
                    put("status", JsonPrimitive("saved"))
                    put("updated_at", JsonPrimitive(now))
                },
            ),
        )
        dao.saveEntity(
            entity.copy(
                payloadJson = encoded,
                contentHash = sha256(encoded),
                serverModifiedAt = now,
                dirty = false,
                conflicted = false,
                localModifiedAt = System.currentTimeMillis(),
            ),
        )
    }

    suspend fun cancelAssistantRun(projectId: String, runId: String) {
        val connection = requireConnection()
        api.cancelAssistantRun(connection, projectId, runId)
    }

    suspend fun assistantConversations(projectId: String): List<MobileAssistantConversation> {
        val connection = dao.connection()
            ?: return mobileAssistantConversationStore.conversations(projectId)
        val root = api.assistantConversations(connection, projectId)
        return (root["items"] as? JsonArray).orEmpty().mapNotNull { raw ->
            val item = raw as? JsonObject ?: return@mapNotNull null
            item.string("id").takeIf(String::isNotBlank)?.let { id ->
                MobileAssistantConversation(
                    id = id,
                    title = item.string("title").ifBlank { "新对话" },
                    messageCount = (item["message_count"] as? JsonPrimitive)?.intOrNull ?: 0,
                    updatedAt = item.string("updated_at"),
                )
            }
        }
    }

    suspend fun assistantMessages(
        projectId: String,
        conversationId: String,
    ): List<MobileAssistantMessage> {
        val connection = dao.connection()
            ?: return mobileAssistantConversationStore.messages(projectId, conversationId)
        val root = api.assistantConversation(connection, projectId, conversationId)
        return (root["messages"] as? JsonArray).orEmpty().mapNotNull { raw ->
            val item = raw as? JsonObject ?: return@mapNotNull null
            val role = item.string("role")
            if (role !in setOf("user", "assistant")) return@mapNotNull null
            MobileAssistantMessage(
                id = item.string("id").ifBlank { UUID.randomUUID().toString() },
                role = role,
                content = item.string("content"),
                status = item.string("status").ifBlank { "completed" },
                createdAt = item.string("created_at"),
                toolLogs = ((item["payload"] as? JsonObject)?.get("tool_logs") as? JsonArray)
                    .orEmpty()
                    .mapNotNull logs@{ rawLog ->
                        val log = rawLog as? JsonObject ?: return@logs null
                        log.string("detail").ifBlank { log.string("tool") }.takeIf(String::isNotBlank)
                    },
            )
        }
    }

    private suspend fun recoverAssistantRun(
        connection: GatewayConnection,
        projectId: String,
        runId: String,
        onEvent: suspend (String) -> Unit,
        streamError: IOException,
    ) {
        repeat(180) {
            val detail = api.assistantRun(connection, projectId, runId)
            val run = detail["run"] as? JsonObject ?: throw streamError
            val status = run.string("status")
            onEvent(buildJsonObject {
                put("type", "status")
                put("message", "连接已恢复，正在核对持久化任务：${status.ifBlank { "running" }}")
            }.toString())
            when (status) {
                "completed", "success" -> {
                    val assistantMessage = detail["assistant_message"] as? JsonObject
                    val payload = assistantMessage?.get("payload") as? JsonObject
                        ?: buildJsonObject {
                            put("reply", assistantMessage?.string("content").orEmpty())
                            put("run", run)
                        }
                    onEvent(buildJsonObject {
                        put("type", "complete")
                        put("data", payload)
                    }.toString())
                    return
                }
                "cancelled", "aborted" -> throw CancellationException("作品助手任务已取消")
                "error", "failed" -> throw IOException(run.string("error").ifBlank { "作品助手任务执行失败" })
            }
            kotlinx.coroutines.delay(1_000)
        }
        throw IOException("AI 流中断后仍未取得终态；运行 ID：$runId", streamError)
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
                resolvedDirectConfig(DirectApiConfig.TASK_PLANNING)
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
        onProgress: suspend (CreationAgentProgressEvent) -> Unit = {},
    ): JsonObject {
        require(message.isNotBlank()) { "请输入你想告诉 AI 的内容" }
        val current = loadCreationSession(sessionId)
        val turns = CreationAgentTurnRecords.turns(current)
        val pendingTurn = CreationAgentTurnRecords.pending(message)
        val pendingTurns = (turns + pendingTurn).takeLast(20)
        val pendingSession = CreationAgentTurnRecords.withTurns(current, pendingTurns)
        saveCreationSession(pendingSession)
        val route = creationRoute(current)
        val gatewayExecution = creationHost(current) == CREATION_HOST_GATEWAY
        val clientTurnId = UUID.randomUUID().toString()
        val capturedProgress = mutableListOf<JsonElement>()
        val seenSequences = mutableSetOf<Long>()
        var localSequence = 0L
        suspend fun emitProgress(event: CreationAgentProgressEvent) {
            val sequence = if (event.sequence > 0) event.sequence else ++localSequence
            if (sequence > 0 && !seenSequences.add(sequence)) return
            localSequence = maxOf(localSequence, sequence)
            val normalized = event.copy(
                clientTurnId = event.clientTurnId.ifBlank { clientTurnId },
                sequence = sequence,
            )
            capturedProgress += buildJsonObject {
                put("client_turn_id", normalized.clientTurnId)
                put("sequence", normalized.sequence)
                put("type", normalized.type)
                put("message", normalized.message)
                put("status", normalized.status)
                put("data", normalized.data)
            }
            onProgress(normalized)
        }
        val updated = try {
            when {
                route == CreationExecutionRoute.Pc || gatewayExecution -> {
                    val connection = requireConnection()
                    val mobileProvider = if (route == CreationExecutionRoute.MobileKey) {
                        mobileProviderPayload(connection, sessionId)
                    } else null
                    val result = api.novelCreationAgentTurn(
                        connection,
                        buildJsonObject {
                            put("session_id", sessionId)
                            put("message", message)
                            put("client_turn_id", clientTurnId)
                            put("after_sequence", 0)
                            CreationAgentTurnRecords.gatewayConversationId(current)
                                .takeIf(String::isNotBlank)
                                ?.let { put("conversation_id", it) }
                            put("model_route", if (mobileProvider == null) "pc" else "mobile")
                            mobileProvider?.let { put("mobile_provider", it) }
                        },
                    ) { event ->
                        val data = event["data"] as? JsonObject ?: JsonObject(emptyMap())
                        val type = event.string("type")
                        emitProgress(CreationAgentProgressEvent(
                            type = type,
                            message = event.string("message"),
                            status = data.string("status").ifBlank {
                                when (type) {
                                    "tool_completed", "complete" -> "ok"
                                    "error" -> "error"
                                    "cancelled" -> "cancelled"
                                    else -> "running"
                                }
                            },
                            data = data,
                            clientTurnId = event.string("client_turn_id"),
                            sequence = (event["sequence"] as? JsonPrimitive)?.longOrNull ?: 0L,
                        ))
                    }
                    val reply = result.string("reply").ifBlank {
                        "本轮服务没有返回可确认结果，因此无法确认读取或修改了立项数据。请重试。"
                    }
                    val fresh = tagCreationRoute(
                        api.getNovelCreationSession(connection, sessionId),
                        route,
                        CREATION_HOST_GATEWAY,
                    )
                    val status = result.string("message_status")
                        .takeIf { it in setOf("completed", "running", "error") }
                        ?: "completed"
                    val completedTurn = CreationAgentTurnRecords.complete(
                        pending = pendingTurn,
                        reply = reply,
                        modelMessages = JsonArray(emptyList()),
                        toolResults = result["tool_results"] as? JsonArray ?: JsonArray(emptyList()),
                        replayable = false,
                        status = status,
                        executionRoute = "gateway",
                        createdProjectId = result.string("created_project_id").takeIf(String::isNotBlank),
                        progressEvents = JsonArray(capturedProgress),
                        promptMetrics = (
                            ((result["_turn_trace"] as? JsonObject)?.get("prompt_metrics") as? JsonArray)
                                ?: (result["prompt_metrics"] as? JsonArray)
                                ?: JsonArray(emptyList())
                        ),
                    )
                    CreationAgentTurnRecords.withTurns(
                        fresh,
                        CreationAgentTurnRecords.replace(pendingTurns, completedTurn),
                        gatewayConversationId = result.string("conversation_id"),
                    )
                }
                else -> {
                    emitProgress(CreationAgentProgressEvent(
                        type = "turn_started",
                        message = "已接收请求，正在准备手机本地立项上下文…",
                    ))
                    val result = mobileCreationConversationAgent.run(
                        source = pendingSession,
                        message = message,
                        turns = turns,
                        config = resolvedDirectConfig(DirectApiConfig.TASK_PLANNING),
                        onProgress = ::emitProgress,
                    )
                    emitProgress(CreationAgentProgressEvent(
                        type = "complete",
                        message = "本轮立项处理完成",
                        status = "ok",
                    ))
                    val completedTurn = CreationAgentTurnRecords.complete(
                        pending = pendingTurn,
                        reply = result.reply,
                        modelMessages = result.modelMessages,
                        toolResults = result.toolResults,
                        replayable = result.replayable,
                        status = result.status,
                        executionRoute = "device",
                        createdProjectId = result.createdProjectId,
                        progressEvents = JsonArray(capturedProgress),
                        promptMetrics = result.promptMetrics,
                    )
                    CreationAgentTurnRecords.withTurns(
                        result.session,
                        CreationAgentTurnRecords.replace(
                            CreationAgentTurnRecords.turns(result.session),
                            completedTurn,
                        ),
                    )
                }
            }
        } catch (error: CancellationException) {
            val latest = runCatching { loadCreationSession(sessionId) }.getOrDefault(pendingSession)
            val cancelled = CreationAgentTurnRecords.fail(pendingTurn, "本轮已取消，未确认的操作不会进入后续上下文。")
            saveCreationSession(CreationAgentTurnRecords.withTurns(
                latest,
                CreationAgentTurnRecords.replace(CreationAgentTurnRecords.turns(latest), cancelled),
            ))
            throw error
        } catch (error: Exception) {
            val latest = runCatching { loadCreationSession(sessionId) }.getOrDefault(pendingSession)
            val failed = CreationAgentTurnRecords.fail(pendingTurn, error.message ?: "本轮立项处理失败")
            saveCreationSession(CreationAgentTurnRecords.withTurns(
                latest,
                CreationAgentTurnRecords.replace(CreationAgentTurnRecords.turns(latest), failed),
            ))
            throw error
        }
        saveCreationSession(updated)
        return updated
    }

    suspend fun generateCreationStage(
        sessionId: String,
        stage: String,
        operation: String = "generate",
        instruction: String = "",
        onProgress: suspend (String) -> Unit = {},
    ): JsonObject {
        require(stage in CREATION_STAGE_ORDER && stage != "constraints") { "不支持的立项阶段：$stage" }
        require(operation in setOf("generate", "regenerate", "refine")) { "不支持的生成操作：$operation" }
        if (operation == "refine") require(instruction.isNotBlank()) { "请先填写本次调整要求" }

        val current = loadCreationSession(sessionId)
        val route = creationRoute(current)
        val host = creationHost(current)
        val updated = if (host == CREATION_HOST_DEVICE) {
            onProgress("正在用手机模型生成${creationStageLabel(stage)}…")
            tagCreationRoute(
                mobileCreationAgent.generateStage(
                    current,
                    stage,
                    instruction.trim(),
                    resolvedDirectConfig(DirectApiConfig.TASK_PLANNING),
                ),
                route,
                CREATION_HOST_DEVICE,
            )
        } else {
            val connection = requireConnection()
            onProgress("正在由 PC 权威建档服务生成${creationStageLabel(stage)}…")
            val payload = buildJsonObject {
                put("stage", stage)
                put("model_route", if (route == CreationExecutionRoute.MobileKey) "mobile" else "pc")
                put("use_model", true)
                put("auto_confirm", false)
                put("operation", operation)
                put("expected_revision", current.int("revision"))
                if (instruction.isNotBlank()) put("instruction", instruction.trim())
                if (route == CreationExecutionRoute.MobileKey) {
                    put("mobile_provider", mobileProviderPayload(connection, sessionId))
                }
            }
            val started = api.startNovelCreationRun(connection, sessionId, payload)
            val run = started["run"] as? JsonObject ?: started
            val runId = run.string("id").ifBlank { run.string("run_id") }
            require(runId.isNotBlank()) { "PC 立项任务没有返回 run_id" }
            awaitCreationRun(connection, runId, onProgress)
            tagCreationRoute(
                api.getNovelCreationSession(connection, sessionId),
                route,
                CREATION_HOST_GATEWAY,
            )
        }
        saveCreationSession(updated)
        return updated
    }

    suspend fun updateCreationStage(
        sessionId: String,
        stage: String,
        data: JsonObject,
    ): JsonObject {
        require(stage in CREATION_STAGE_ORDER && stage != "constraints") { "不支持的立项阶段：$stage" }
        require(data.isNotEmpty()) { "阶段内容不能为空" }
        val current = loadCreationSession(sessionId)
        val route = creationRoute(current)
        val host = creationHost(current)
        val updated = if (host == CREATION_HOST_DEVICE) {
            tagCreationRoute(
                mobileCreationAgent.replaceArtifact(current, stage, data, sourceLabel = "author"),
                route,
                CREATION_HOST_DEVICE,
            )
        } else {
            val connection = requireConnection()
            tagCreationRoute(
                api.updateNovelCreationStage(
                    connection,
                    sessionId,
                    stage,
                    buildJsonObject {
                        put("data", data)
                        put("source", "author")
                        put("expected_revision", current.int("revision"))
                    },
                ),
                route,
                CREATION_HOST_GATEWAY,
            )
        }
        saveCreationSession(updated)
        return updated
    }

    suspend fun confirmCreationStage(
        sessionId: String,
        stage: String,
        data: JsonObject,
    ): JsonObject {
        require(stage in CREATION_STAGE_ORDER && stage != "constraints") { "不支持的立项阶段：$stage" }
        require(data.isNotEmpty()) { "阶段内容不能为空" }
        var current = loadCreationSession(sessionId)
        val route = creationRoute(current)
        val host = creationHost(current)

        if (stage == "concepts" && current.stageState("constraints").string("status") != "confirmed") {
            val constraintData = current.draft().objectValue("form")
            current = if (host == CREATION_HOST_DEVICE) {
                tagCreationRoute(
                    mobileCreationAgent.confirmStage(current, "constraints", constraintData),
                    route,
                    CREATION_HOST_DEVICE,
                )
            } else {
                val connection = requireConnection()
                tagCreationRoute(
                    api.confirmNovelCreationStage(
                        connection,
                        sessionId,
                        "constraints",
                        buildJsonObject {
                            put("data", constraintData)
                            put("confirm", true)
                            put("source", "author")
                            put("expected_revision", current.int("revision"))
                        },
                    ),
                    route,
                    CREATION_HOST_GATEWAY,
                )
            }
        }

        val normalizedData = if (stage == "concepts") {
            val selected = data.string("selected_concept_id")
                .ifBlank { current.draft().string("selected_concept_id") }
                .ifBlank {
                    ((data["options"] as? JsonArray)?.firstOrNull() as? JsonObject)
                        ?.string("id")
                        .orEmpty()
                }
            require(selected.isNotBlank()) { "请先选择一个创意方向" }
            JsonObject(data.toMutableMap().apply {
                put("selected_concept_id", JsonPrimitive(selected))
            })
        } else {
            data
        }

        val updated = if (host == CREATION_HOST_DEVICE) {
            tagCreationRoute(
                mobileCreationAgent.confirmStage(current, stage, normalizedData),
                route,
                CREATION_HOST_DEVICE,
            )
        } else {
            val connection = requireConnection()
            tagCreationRoute(
                api.confirmNovelCreationStage(
                    connection,
                    sessionId,
                    stage,
                    buildJsonObject {
                        put("data", normalizedData)
                        put("confirm", true)
                        put("source", "author")
                        put("expected_revision", current.int("revision"))
                    },
                ),
                route,
                CREATION_HOST_GATEWAY,
            )
        }
        saveCreationSession(updated)
        return updated
    }

    private suspend fun awaitCreationRun(
        connection: GatewayConnection,
        runId: String,
        onProgress: suspend (String) -> Unit,
    ): JsonObject {
        repeat(640) {
            val run = api.getNovelCreationRun(connection, runId)
            val status = run.string("status")
            val message = run.string("current_message")
                .ifBlank { run.objectValue("card_presentation").string("message") }
            if (message.isNotBlank()) onProgress(message)
            if (status in CREATION_TERMINAL_RUN_STATUSES) {
                if (status in CREATION_SUCCESS_RUN_STATUSES) return run
                val nextAction = run.string("next_action")
                    .ifBlank { run.objectValue("card_presentation").string("reason") }
                error(nextAction.ifBlank { "${creationStageLabel(run.string("stage"))}生成失败，请重试" })
            }
            kotlinx.coroutines.delay(750)
        }
        error("PC 立项任务等待超时；任务仍保留，可稍后继续查看")
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
                val finalized = api.finalizeNovelCreation(requireConnection(), sessionId)
                finalized.string("project_id").ifBlank { error("PC 建档结果缺少 project_id") }
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
        put("mode", "internal_llm")
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
        val stored = storedCreationSession(sessionId) ?: error("立项草稿不存在或已删除")
        val migrated = CreationAgentTurnRecords.migrateLegacyHistory(stored)
        if (migrated != stored) saveCreationSession(migrated)
        return migrated
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
            resolvedDirectConfig(DirectApiConfig.TASK_PLANNING),
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
                put("mode", "internal_llm")
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
        val finalized = api.finalizeNovelCreation(connection, remoteId)
        return finalized.string("project_id").ifBlank { error("PC 建档结果缺少 project_id") }
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

    private fun JsonObject.stringList(name: String): List<String> =
        (get(name) as? JsonArray)
            .orEmpty()
            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.trim() }
            .filter(String::isNotBlank)
    private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0

    private suspend fun resolvedDirectConfig(taskType: String): DirectApiConfig {
        val stored = directApiStore.read() ?: error("请先在设置中配置手机 API Key")
        val selected = stored.forTask(taskType)
        if (stored.protocol != DirectApiConfig.PROTOCOL_AUTO) return selected
        val resolved = stored.copy(protocol = directApi.testAndResolve(selected).protocol)
        directApiStore.save(resolved)
        return resolved.forTask(taskType)
    }

    suspend fun disconnect(clearOfflineData: Boolean): Boolean {
        val connection = dao.connection()
        val revokedRemotely = connection != null && runCatching {
            api.revokeSelf(connection)
        }.isSuccess
        tokenStore.clear()
        val packageFiles = if (clearOfflineData) {
            dao.pendingProjectPackages().map { it.localFilePath } +
                dao.localProjectIds().mapNotNull { dao.projectPackage(it)?.localFilePath }
        } else {
            emptyList()
        }
        database.withTransaction {
            dao.deleteConnection()
            dao.clearCursor()
            if (clearOfflineData) {
                dao.clearOutbox()
                dao.clearConflicts()
                dao.clearReplicas()
                dao.clearProjectPackages()
            }
        }
        packageFiles.distinct().forEach { File(it).delete() }
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
        private val CREATION_SUCCESS_RUN_STATUSES = setOf(
            "waiting_user",
            "waiting_author",
            "completed",
            "partial_success",
        )
        private val CREATION_TERMINAL_RUN_STATUSES = CREATION_SUCCESS_RUN_STATUSES + setOf(
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
        private val canonicalCommandMutex = Mutex()
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
