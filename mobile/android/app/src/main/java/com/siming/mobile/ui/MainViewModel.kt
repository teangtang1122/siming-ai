package com.siming.mobile.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.AssistantRoute
import com.siming.mobile.data.AssistantModelRoute
import com.siming.mobile.data.MobileCatalogingProgress
import com.siming.mobile.data.MobileExportFile
import com.siming.mobile.data.MobileNovelImportFile
import com.siming.mobile.data.MobileProjectPackageFile
import com.siming.mobile.data.MobilePendingChapterDraft
import com.siming.mobile.data.MobilePendingOutlineDraft
import com.siming.mobile.data.MobileOutlineDraftNode
import com.siming.mobile.data.MobileAssistantConversation
import com.siming.mobile.data.MobileAssistantMessage
import com.siming.mobile.data.creation.CreationExecutionRoute
import com.siming.mobile.data.creation.CreationAgentProgressEvent
import com.siming.mobile.data.creation.CreationStartInput
import com.siming.mobile.data.toUserFacingMessage
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.security.VerifiedPairing
import com.siming.mobile.data.network.DirectApiSummary
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

data class MobileUiState(
    val busy: Boolean = false,
    val activity: String = "",
    val error: String? = null,
    val notice: String? = null,
    val pairing: VerifiedPairing? = null,
    val pairingStatus: String? = null,
    val assistantOutput: String = "",
    val assistantReasoning: String = "",
    val assistantActivity: String = "",
    val assistantRunning: Boolean = false,
    val assistantConversationId: String? = null,
    val assistantRunId: String? = null,
    val assistantOperationId: String? = null,
    val assistantConversations: List<MobileAssistantConversation> = emptyList(),
    val assistantMessages: List<MobileAssistantMessage> = emptyList(),
    val assistantToolLog: List<String> = emptyList(),
    val pendingChapterDraft: MobilePendingChapterDraft? = null,
    val pendingOutlineDraft: MobilePendingOutlineDraft? = null,
    val pendingAssistantRequest: String? = null,
    val directApi: DirectApiSummary? = null,
    val discoveredModels: List<String> = emptyList(),
    val activeCreationId: String? = null,
    val creationRunning: Boolean = false,
    val creationActivity: String = "",
    val creationReplyDelta: String = "",
    val creationProgressEvents: List<CreationAgentProgressEvent> = emptyList(),
    val pendingCatalogingProjectId: String? = null,
    val importedChapterCount: Int = 0,
    val catalogingProjectId: String? = null,
    val catalogingJobId: String? = null,
    val catalogingStatus: String = "",
    val catalogingTotal: Int = 0,
    val catalogingCompleted: Int = 0,
    val catalogingFailed: Int = 0,
    val catalogingRunning: Boolean = false,
    val catalogingActivity: String = "",
    val exportRunning: Boolean = false,
)

@OptIn(ExperimentalSerializationApi::class)
class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = SimingRepository(application)
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private var assistantJob: Job? = null
    private var assistantCancelRequested = false
    private var catalogingJob: Job? = null

    val connection = repository.connection.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        null,
    )
    val projects = repository.projects.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        emptyList(),
    )
    val creationDrafts = repository.creationDrafts.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        emptyList(),
    )
    val pendingCount = repository.pendingCount.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        0,
    )
    val cursor = repository.cursor.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        null,
    )
    val conflicts = repository.conflicts.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        emptyList(),
    )

    var uiState = androidx.compose.runtime.mutableStateOf(
        MobileUiState(directApi = repository.directApiSummary()),
    )
        private set

    init {
        viewModelScope.launch {
            runCatching { repository.refreshCreationDrafts() }
        }
    }

    fun entities(projectId: String, entityType: String) =
        repository.entities(projectId, entityType)

    fun beginCreation(input: CreationStartInput, route: CreationExecutionRoute) {
        launchCreation("正在建立对话式立项会话…") {
            val started = repository.beginCreation(input, route)
            val sessionId = started["id"]?.jsonPrimitive?.contentOrNull
                ?: error("立项草稿缺少 id")
            uiState.value = uiState.value.copy(activeCreationId = sessionId)
            repository.runCreationAgentTurn(sessionId, input.brief, ::showCreationProgress)
            "Creation Agent 已边聊边写入第一轮立项资料"
        }
    }

    fun resumeCreation(sessionId: String) {
        uiState.value = uiState.value.copy(activeCreationId = sessionId, error = null)
    }

    fun closeCreation() {
        uiState.value = uiState.value.copy(activeCreationId = null, creationActivity = "")
    }

    fun sendCreationMessage(sessionId: String, message: String) {
        if (message.isBlank()) return
        launchCreation("Creation Agent 正在处理…") {
            repository.runCreationAgentTurn(sessionId, message, ::showCreationProgress)
            "本轮已完成；确定事实已立即写入结构化立项资料"
        }
    }

    fun generateCreationStage(
        sessionId: String,
        stage: String,
        operation: String,
        instruction: String,
    ) = launchCreation("正在生成立项资料…") {
        repository.generateCreationStage(
            sessionId = sessionId,
            stage = stage,
            operation = operation,
            instruction = instruction,
            onProgress = { message ->
                uiState.value = uiState.value.copy(creationActivity = message)
            },
        )
        when (operation) {
            "refine" -> "已按要求更新当前阶段；请检查后再确认"
            "regenerate" -> "已重新生成当前阶段；旧内容仍可从修订历史追溯"
            else -> "阶段内容已生成并保存，等待作者确认"
        }
    }

    fun saveCreationStage(
        sessionId: String,
        stage: String,
        data: JsonObject,
        onSaved: () -> Unit = {},
    ) = launchCreation("正在保存建档修改…") {
        repository.updateCreationStage(sessionId, stage, data)
        onSaved()
        "建档修改已保存；受影响的下游阶段会按 PC 规则重新校验"
    }

    fun confirmCreationStage(
        sessionId: String,
        stage: String,
        data: JsonObject,
        onConfirmed: () -> Unit = {},
    ) = launchCreation("正在确认立项阶段…") {
        repository.confirmCreationStage(sessionId, stage, data)
        onConfirmed()
        "当前阶段已确认"
    }

    fun archiveCreation(sessionId: String, onArchived: (String) -> Unit) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                creationRunning = true,
                creationActivity = "正在执行正式作品建档…",
                error = null,
            )
            try {
                val projectId = repository.archiveCreation(sessionId) { activity ->
                    uiState.value = uiState.value.copy(creationActivity = activity)
                }
                uiState.value = uiState.value.copy(
                    activeCreationId = null,
                    creationRunning = false,
                    creationActivity = "",
                    notice = "正式作品已建档；角色、设定、关系和大纲已进入作品库",
                )
                onArchived(projectId)
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(creationRunning = false, creationActivity = "")
                showError(error)
            }
        }
    }

    fun discardCreation(sessionId: String) {
        viewModelScope.launch {
            runCatching { repository.discardCreation(sessionId) }
                .onSuccess {
                    uiState.value = uiState.value.copy(
                        activeCreationId = null,
                        notice = "立项草稿已移除；正式作品和其他草稿没有变化",
                    )
                }
                .onFailure(::showError)
        }
    }

    fun refreshCreationDrafts() {
        viewModelScope.launch {
            runCatching { repository.refreshCreationDrafts() }
                .onFailure { error ->
                    uiState.value = uiState.value.copy(error = error.toUserFacingMessage())
                }
        }
    }

    fun acceptPairingQr(raw: String) {
        runCatching { repository.verifyPairing(raw) }
            .onSuccess { verified ->
                uiState.value = uiState.value.copy(
                    pairing = verified,
                    pairingStatus = "已验证 Gateway 签名，请核对地址与指纹",
                    error = null,
                )
            }
            .onFailure(::showError)
    }

    fun cancelPairing() {
        uiState.value = uiState.value.copy(pairing = null, pairingStatus = null, error = null)
    }

    fun discoverDirectModels(baseUrl: String, apiKey: String) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在安全获取模型列表…",
                error = null,
                discoveredModels = emptyList(),
            )
            try {
                val models = repository.discoverDirectModels(baseUrl, apiKey)
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    discoveredModels = models,
                    notice = if (models.isEmpty()) {
                        "接口返回了空模型列表，请手动填写模型名"
                    } else {
                        "已获取 ${models.size} 个模型"
                    },
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    discoveredModels = emptyList(),
                    error = "自动获取模型失败：${error.toUserFacingMessage()}；仍可手动填写模型名",
                )
            }
        }
    }

    fun configureDirectApi(
        displayName: String,
        baseUrl: String,
        apiKey: String,
        model: String,
        protocol: String,
        availableModels: List<String>,
        taskModels: Map<String, String>,
        onConfigured: () -> Unit,
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在用当前模型进行真实对话测试…",
                error = null,
            )
            try {
                var discovered = uiState.value.discoveredModels
                val effectiveModel = model.trim().ifBlank {
                    val models = repository.discoverDirectModels(baseUrl, apiKey)
                    discovered = models
                    uiState.value = uiState.value.copy(discoveredModels = models)
                    models.firstOrNull()
                        ?: error("接口没有返回可用模型，请手动填写模型名后重试")
                }
                val summary = repository.configureDirectApi(
                    displayName,
                    baseUrl,
                    apiKey,
                    effectiveModel,
                    protocol,
                    (availableModels + discovered + effectiveModel).distinct(),
                    taskModels,
                )
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    directApi = summary,
                    discoveredModels = emptyList(),
                    notice = "API 已加密保存，手机独立模式可以使用",
                )
                onConfigured()
            } catch (error: Exception) {
                val message = error.toUserFacingMessage()
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    error = if (model.isBlank() && !message.contains("手动填写")) {
                        "$message；自动获取模型失败，请手动填写模型名后重试"
                    } else {
                        message
                    },
                )
            }
        }
    }

    fun testDirectApi() = launchActivity("正在测试手机直连 API…") {
        val summary = repository.testDirectApi()
        uiState.value = uiState.value.copy(directApi = summary)
        "${summary.displayName} · ${summary.model} 真实对话成功"
    }

    fun clearDirectApi() {
        runCatching { repository.clearDirectApi() }
            .onSuccess {
                uiState.value = uiState.value.copy(
                    directApi = null,
                    discoveredModels = emptyList(),
                    assistantOutput = "",
                    notice = "已清除手机直连 API 配置",
                )
            }
            .onFailure(::showError)
    }

    fun connectPairing(deviceName: String) {
        val pairing = uiState.value.pairing ?: return
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在提交设备申请…",
                error = null,
            )
            try {
                while (Instant.parse(pairing.expiresAt).isAfter(Instant.now())) {
                    val result = repository.requestPairing(pairing, deviceName)
                    when (result.status) {
                        "approved" -> {
                            uiState.value = uiState.value.copy(
                                busy = true,
                                activity = "授权完成，正在下载已启用作品…",
                                pairingStatus = "电脑已批准",
                            )
                            val count = repository.bootstrapEnabledProjects()
                            uiState.value = uiState.value.copy(
                                busy = false,
                                activity = "",
                                pairing = null,
                                pairingStatus = null,
                                notice = "已安全连接，并下载 $count 部作品",
                            )
                            return@launch
                        }
                        "expired" -> error("二维码已经过期，请重新扫描")
                        else -> {
                            uiState.value = uiState.value.copy(
                                activity = "已提交申请，请在电脑上确认这台手机…",
                                pairingStatus = "等待电脑批准",
                            )
                            delay(4_000)
                        }
                    }
                }
                error("二维码已经过期，请重新扫描")
            } catch (error: Exception) {
                showError(error)
                uiState.value = uiState.value.copy(busy = false, activity = "")
            }
        }
    }

    fun bootstrap() = launchActivity("正在校验并下载作品…") {
        val count = repository.bootstrapEnabledProjects()
        "已校验并更新 $count 部作品的离线副本"
    }

    fun syncNow() = launchActivity("正在先上传本机修改，再拉取新修订…") {
        repository.syncNow()
        "同步完成"
    }

    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        repository.reorderChapters(projectId, chapterIds)

    fun reorderOutline(projectId: String, parentId: String?, nodeIds: List<String>) {
        viewModelScope.launch {
            try {
                repository.reorderOutline(projectId, parentId, nodeIds)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "大纲顺序已通过 PC 端同一排序 API 更新"
                    } else {
                        "大纲顺序已保存到手机，恢复连接后按节点修订同步"
                    },
                )
            } catch (error: Exception) {
                showError(error)
            }
        }
    }


fun dismissImportCatalogingPrompt() {
    uiState.value = uiState.value.copy(pendingCatalogingProjectId = null, importedChapterCount = 0)
}

fun startCataloging(projectId: String) {
    if (catalogingJob?.isActive == true) return
    catalogingJob = viewModelScope.launch {
        uiState.value = uiState.value.copy(
            pendingCatalogingProjectId = null,
            catalogingProjectId = projectId,
            catalogingRunning = true,
            catalogingActivity = "正在准备作品建档…",
            error = null,
        )
        try {
            val result = repository.runCataloging(projectId) { progress, message ->
                updateCatalogingProgress(projectId, progress, message)
            }
            updateCatalogingProgress(projectId, result, "作品建档已结束")
            uiState.value = uiState.value.copy(
                catalogingRunning = false,
                catalogingActivity = "",
                notice = when (result.status) {
                    "completed" -> "作品建档完成，手机已刷新角色、设定和摘要副本"
                    "cancelled" -> "作品建档已取消"
                    else -> "作品建档已停止：${result.status}"
                },
            )
        } catch (_: CancellationException) {
            uiState.value = uiState.value.copy(catalogingRunning = false, catalogingActivity = "")
        } catch (error: Exception) {
            uiState.value = uiState.value.copy(catalogingRunning = false, catalogingActivity = "")
            showError(error)
        } finally {
            catalogingJob = null
        }
    }
}

fun cancelCataloging(projectId: String) {
    val jobId = uiState.value.catalogingJobId ?: return
    viewModelScope.launch {
        runCatching { repository.cancelCataloging(projectId, jobId) }
            .onFailure(::showError)
        catalogingJob?.cancel(CancellationException("用户取消作品建档"))
        uiState.value = uiState.value.copy(
            catalogingRunning = false,
            catalogingStatus = "cancelled",
            catalogingActivity = "",
            notice = "作品建档已取消",
        )
    }
}

fun prepareExport(
    projectId: String,
    format: String,
    onReady: (MobileExportFile) -> Unit,
) {
    viewModelScope.launch {
        uiState.value = uiState.value.copy(exportRunning = true, error = null)
        try {
            val file = repository.exportProject(projectId, format)
            uiState.value = uiState.value.copy(exportRunning = false)
            onReady(file)
        } catch (error: Exception) {
            uiState.value = uiState.value.copy(exportRunning = false)
            showError(error)
        }
    }
}

fun prepareProjectPackageExport(
    projectId: String,
    profile: String,
    onReady: (MobileExportFile) -> Unit,
) {
    viewModelScope.launch {
        uiState.value = uiState.value.copy(exportRunning = true, error = null)
        try {
            val file = repository.exportProjectPackage(projectId, profile)
            uiState.value = uiState.value.copy(exportRunning = false)
            onReady(file)
        } catch (error: Exception) {
            uiState.value = uiState.value.copy(exportRunning = false)
            showError(error)
        }
    }
}

private fun updateCatalogingProgress(
    projectId: String,
    progress: MobileCatalogingProgress,
    message: String?,
) {
    uiState.value = uiState.value.copy(
        catalogingProjectId = projectId,
        catalogingJobId = progress.jobId,
        catalogingStatus = progress.status,
        catalogingTotal = progress.totalChapters,
        catalogingCompleted = progress.completedChapters,
        catalogingFailed = progress.failedChapters,
        catalogingActivity = message.orEmpty(),
    )
}

    suspend fun chapterSnapshots(projectId: String, chapterId: String): JsonObject =
        repository.listChapterSnapshots(projectId, chapterId)

    suspend fun chapterSnapshot(
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = repository.getChapterSnapshot(projectId, chapterId, snapshotId)

    suspend fun chapterSnapshotDiff(
        projectId: String,
        chapterId: String,
        fromSnapshotId: String,
        toSnapshotId: String,
    ): JsonObject = repository.diffChapterSnapshots(
        projectId,
        chapterId,
        fromSnapshotId,
        toSnapshotId,
    )

    suspend fun restoreChapterSnapshot(
        projectId: String,
        chapterId: String,
        snapshotId: String,
    ): JsonObject = repository.restoreChapterSnapshot(projectId, chapterId, snapshotId)

    suspend fun characterRelationshipNetwork(projectId: String): JsonObject =
        repository.characterRelationshipNetwork(projectId)

    suspend fun replaceCharacterRelationships(
        projectId: String,
        characterId: String,
        relationships: JsonArray,
    ): JsonObject = repository.replaceCharacterRelationships(
        projectId,
        characterId,
        relationships,
    )

    suspend fun characterAiConfig(projectId: String, characterId: String): JsonObject =
        repository.characterAiConfig(projectId, characterId)

    suspend fun updateCharacterAiConfig(
        projectId: String,
        characterId: String,
        payload: JsonObject,
    ): JsonObject = repository.updateCharacterAiConfig(projectId, characterId, payload)

    suspend fun characterVersions(projectId: String, characterId: String): JsonObject =
        repository.characterVersions(projectId, characterId)

    suspend fun characterVersion(
        projectId: String,
        characterId: String,
        versionId: String,
    ): JsonObject = repository.characterVersion(projectId, characterId, versionId)

    suspend fun worldVersions(projectId: String, entryId: String): JsonObject =
        repository.worldVersions(projectId, entryId)

    suspend fun worldTimeline(projectId: String, entryId: String): JsonObject =
        repository.worldTimeline(projectId, entryId)

    fun createProject(title: String, description: String, onCreated: (String) -> Unit) {
        viewModelScope.launch {
            try {
                val id = repository.createProject(title, description)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "新作品已通过 PC 端同一 API 创建，手机副本已更新"
                    } else {
                        "新作品已保存到手机，连接 Gateway 后自动同步"
                    },
                )
                onCreated(id)
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun deleteProject(projectId: String, onDeleted: () -> Unit) {
        viewModelScope.launch {
            try {
                repository.deleteProject(projectId)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "作品已从 PC 权威库删除，手机副本已清理"
                    } else {
                        "尚未同步的本地作品已从手机删除"
                    },
                )
                onDeleted()
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun importNovel(file: MobileNovelImportFile, onCreated: (String) -> Unit) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在识别编码并准备批量导入…",
                error = null,
            )
            try {
                val result = repository.importNovel(file) { activity ->
                    uiState.value = uiState.value.copy(activity = activity)
                }
                val refreshWarning = result.refreshWarning
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    notice = when {
                        refreshWarning != null ->
                            "Gateway 已导入 ${result.chapterCount} 章（${result.encoding}），" +
                                "但手机刷新失败：$refreshWarning；请在同步页重试"
                        result.remote ->
                            "已通过 Gateway 单次批量导入 ${result.chapterCount} 章，识别编码：${result.encoding}"
                        else ->
                            "已在手机本地事务中导入 ${result.chapterCount} 章，识别编码：${result.encoding}"
                    },
                    pendingCatalogingProjectId = if (refreshWarning == null) result.projectId else null,
                    importedChapterCount = if (refreshWarning == null) result.chapterCount else 0,
                )
                if (refreshWarning == null) onCreated(result.projectId)
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun importProjectPackage(file: MobileProjectPackageFile, onCreated: (String) -> Unit) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在流式校验司命项目包…",
                error = null,
            )
            try {
                val result = repository.importProjectPackage(file) { activity ->
                    uiState.value = uiState.value.copy(activity = activity)
                }
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    notice = if (result.remote) {
                        "已通过 Gateway 导入${if (result.profile == "full") "完整" else "结构"}项目包：${result.projectTitle}"
                    } else {
                        "项目包已安全导入手机并排队同步：${result.projectTitle}"
                    },
                )
                onCreated(result.projectId)
            } catch (error: Exception) {
                file.file.delete()
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    fun saveRecord(
        projectId: String,
        entityType: String,
        entityId: String?,
        fields: Map<String, Any?>,
        basePayload: JsonObject? = null,
        onSaved: () -> Unit,
    ) {
        viewModelScope.launch {
            try {
                saveRecordInternal(projectId, entityType, entityId, fields, basePayload)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "已通过 PC 端同一 API 保存，服务端副作用与桌面端一致"
                    } else {
                        "已保存到手机；连接 Gateway 后自动同步"
                    },
                )
                onSaved()
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    private suspend fun saveRecordInternal(
        projectId: String,
        entityType: String,
        entityId: String?,
        fields: Map<String, Any?>,
        basePayload: JsonObject? = null,
    ): String {
        val id = entityId ?: UUID.randomUUID().toString()
        val recordType = when (entityType) {
            "project" -> "project"
            "chapter" -> "chapter"
            "outline" -> "outline_node"
            "character" -> "character"
            "world" -> "world_entry"
            "foreshadowing" -> "foreshadowing"
            "governance" -> "narrative_debt"
            "summary" -> "chapter_summary"
            "timeline" -> "character_timeline"
            else -> error("暂不支持的资料类型")
        }
        val payload = buildJsonObject {
            basePayload?.forEach { (key, value) -> put(key, value) }
            put("_record_type", recordType)
            put("id", id)
            if (entityType in setOf("chapter", "outline", "character", "world", "foreshadowing", "governance")) {
                put("project_id", projectId)
            }
            fields.forEach { (key, value) -> putAny(key, value) }
            if (entityType == "foreshadowing" && fields["dedupe_key"] == null) {
                put("dedupe_key", "mobile-$id")
                put("source", "manual")
            }
            if (entityType == "governance" && fields["dedupe_key"] == null) {
                put("dedupe_key", "mobile-$id")
                put("source", "manual")
                put("debt_type", "promise")
            }
        }
        return repository.saveEntity(projectId, entityType, id, payload)
    }

    fun deleteRecord(projectId: String, entityType: String, entityId: String, onDeleted: () -> Unit) {
        viewModelScope.launch {
            try {
                repository.deleteEntity(projectId, entityType, entityId)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "已通过 PC 端同一 API 删除，手机副本已更新"
                    } else {
                        "删除已保存到手机，连接 Gateway 后自动同步"
                    },
                )
                onDeleted()
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun runAssistant(
        projectId: String,
        prompt: String,
        modelRoute: AssistantModelRoute,
    ) {
        if (prompt.isBlank() || assistantJob?.isActive == true) return
        assistantJob = viewModelScope.launch {
            assistantCancelRequested = false
            uiState.value = uiState.value.copy(
                assistantRunning = true,
                assistantOutput = "",
                assistantReasoning = "",
                assistantActivity = "正在加载与 PC 同源的工作区流程…",
                assistantRunId = null,
                assistantOperationId = null,
                assistantToolLog = emptyList(),
                error = null,
            )
            try {
                val route = repository.runAssistant(
                    projectId = projectId,
                    prompt = prompt,
                    modelRoute = modelRoute,
                    conversationId = uiState.value.assistantConversationId,
                    history = uiState.value.assistantMessages.takeLast(12).map { message ->
                        buildJsonObject {
                            put("role", message.role)
                            put("content", message.content)
                        }
                    },
                ) { event ->
                    val update = parseAssistantEvent(event)
                    val current = uiState.value
                    val nextDraft = when {
                        update.draftData != null -> {
                            val parsed = MobilePendingChapterDraft.fromJson(projectId, update.draftData)
                            if (parsed != null && update.draftDelta != null) {
                                val previous = current.pendingChapterDraft
                                    ?.takeIf { it.draftId == parsed.draftId }
                                parsed.copy(content = previous?.content.orEmpty() + update.draftDelta)
                            } else parsed ?: current.pendingChapterDraft
                        }
                        else -> current.pendingChapterDraft
                    }
                    val nextOutlineDraft = update.outlineDraftData
                        ?.let { MobilePendingOutlineDraft.fromJson(projectId, it) }
                        ?: current.pendingOutlineDraft
                    uiState.value = current.copy(
                        assistantOutput = when {
                            update.output == null -> current.assistantOutput
                            update.replaceOutput -> update.output
                            else -> current.assistantOutput + update.output
                        },
                        assistantReasoning = when {
                            update.reasoning == null -> current.assistantReasoning
                            update.replaceReasoning -> update.reasoning
                            else -> current.assistantReasoning + update.reasoning
                        },
                        assistantActivity = update.activity ?: current.assistantActivity,
                        assistantConversationId = update.conversationId ?: current.assistantConversationId,
                        assistantRunId = update.runId ?: current.assistantRunId,
                        assistantOperationId = update.operationId ?: current.assistantOperationId,
                        assistantToolLog = update.toolLog?.let {
                            (current.assistantToolLog + it).takeLast(100)
                        } ?: current.assistantToolLog,
                        pendingChapterDraft = nextDraft,
                        pendingOutlineDraft = nextOutlineDraft,
                    )
                    val runId = uiState.value.assistantRunId
                    if (assistantCancelRequested && !runId.isNullOrBlank()) {
                        repository.cancelAssistantRun(projectId, runId)
                        throw CancellationException("用户取消手机工作区任务")
                    }
                }
                val refreshedChapterDraft = if (
                    uiState.value.pendingChapterDraft?.revision == true
                ) {
                    runCatching { repository.pendingChapterDraft(projectId) }.getOrNull()
                } else {
                    uiState.value.pendingChapterDraft
                }
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                    pendingChapterDraft = refreshedChapterDraft
                        ?: uiState.value.pendingChapterDraft,
                    notice = when (route) {
                        AssistantRoute.GatewayPc ->
                            "AI 任务已使用 PC 配置线路执行，相关修改已同步到手机"
                        AssistantRoute.GatewayMobileKey ->
                            "AI 任务已使用手机 Key 执行；提示词、工具和落库流程与 PC 一致"
                        AssistantRoute.DirectApi ->
                            if (uiState.value.pendingChapterDraft != null) {
                                "章节草稿已交给正文编辑器，等待你明确保存"
                            } else if (uiState.value.pendingOutlineDraft != null) {
                                "大纲草稿已交给结构页，等待你审阅确认"
                            } else {
                                "手机独立工作区任务已完成，本地产生的修改已写入手机副本"
                            }
                    },
                )
                refreshAssistantConversations(projectId, selectCurrent = true)
            } catch (_: CancellationException) {
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                    pendingChapterDraft = uiState.value.pendingChapterDraft?.copy(status = "cancelled"),
                    notice = "任务已取消；未提交的章节不会写入，已生成草稿可在下次相同请求中恢复",
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                    pendingChapterDraft = uiState.value.pendingChapterDraft?.copy(status = "error"),
                )
                showError(error)
            } finally {
                assistantCancelRequested = false
                assistantJob = null
            }
        }
    }

    fun cancelAssistant(projectId: String) {
        val job = assistantJob ?: return
        if (!job.isActive) return
        assistantCancelRequested = true
        val runId = uiState.value.assistantRunId
        uiState.value = uiState.value.copy(
            assistantActivity = if (runId.isNullOrBlank()) {
                "正在等待服务器登记任务并安全取消…"
            } else {
                "正在向服务器取消任务；不会写入未提交的章节…"
            },
        )
        if (!runId.isNullOrBlank()) {
            viewModelScope.launch {
                runCatching { repository.cancelAssistantRun(projectId, runId) }
                    .onSuccess {
                        job.cancel(CancellationException("用户取消手机工作区任务"))
                    }
                    .onFailure { error ->
                        assistantCancelRequested = false
                        uiState.value = uiState.value.copy(
                            assistantActivity = "取消请求未确认，服务器任务仍在跟踪中…",
                        )
                        showError(error)
                    }
            }
        } else if (connection.value == null) {
            job.cancel(CancellationException("用户取消手机工作区任务"))
        }
    }

    fun restorePendingChapterDraft(projectId: String) {
        if (uiState.value.pendingChapterDraft?.projectId == projectId) return
        viewModelScope.launch {
            try {
                val draft = repository.pendingChapterDraft(projectId)
                if (draft != null) uiState.value = uiState.value.copy(pendingChapterDraft = draft)
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun hidePendingChapterDraft() {
        uiState.value = uiState.value.copy(pendingChapterDraft = null)
    }

    fun savePendingChapterDraft(
        draft: MobilePendingChapterDraft,
        title: String,
        content: String,
        catalogingMode: String,
        onSaved: (String) -> Unit = {},
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在保存章节草稿…", error = null)
            try {
                val chapterId = repository.savePendingChapterDraft(
                    draft = draft,
                    title = title,
                    content = content,
                    catalogingMode = catalogingMode,
                )
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingChapterDraft = null,
                    notice = if (catalogingMode == "save_and_catalog") {
                        "章节已保存，建档任务已按你的选择启动"
                    } else {
                        "章节已保存；未自动建档"
                    },
                )
                onSaved(chapterId)
            } catch (error: Exception) {
                val reconciledDraft = runCatching {
                    repository.pendingChapterDraft(draft.projectId)
                }
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingChapterDraft = if (reconciledDraft.isSuccess) {
                        reconciledDraft.getOrNull()
                    } else {
                        uiState.value.pendingChapterDraft
                    },
                )
                showError(error)
            }
        }
    }

    fun restorePendingOutlineDraft(projectId: String) {
        if (uiState.value.pendingOutlineDraft?.projectId == projectId) return
        viewModelScope.launch {
            try {
                val draft = repository.pendingOutlineDraft(projectId)
                if (draft != null) uiState.value = uiState.value.copy(pendingOutlineDraft = draft)
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun updatePendingOutlineDraft(
        draft: MobilePendingOutlineDraft,
        nodes: List<MobileOutlineDraftNode>,
        designNotes: String,
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在保存大纲草稿…", error = null)
            try {
                val updated = repository.updatePendingOutlineDraft(draft, nodes, designNotes)
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingOutlineDraft = updated,
                    notice = "大纲草稿修改已保存；正式大纲尚未改变",
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    fun confirmPendingOutlineDraft(
        draft: MobilePendingOutlineDraft,
        nodes: List<MobileOutlineDraftNode>,
        designNotes: String,
        writeAfterConfirm: Boolean,
        onConfirmed: () -> Unit = {},
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在确认大纲草稿…", error = null)
            try {
                val updated = repository.updatePendingOutlineDraft(draft, nodes, designNotes)
                val result = repository.confirmPendingOutlineDraft(updated, writeAfterConfirm)
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingOutlineDraft = null,
                    pendingAssistantRequest = result.nextAuthorMessage,
                    notice = if (result.nextAuthorMessage != null) {
                        "大纲已确认；将以新的作者请求发起写章"
                    } else {
                        "大纲已确认并写入正式结构"
                    },
                )
                onConfirmed()
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    fun regeneratePendingOutlineDraft(
        draft: MobilePendingOutlineDraft,
        onRequested: () -> Unit = {},
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在释放旧大纲草稿…", error = null)
            try {
                val request = repository.regeneratePendingOutlineDraft(draft)
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingOutlineDraft = null,
                    pendingAssistantRequest = request,
                    notice = "旧草稿已丢弃；将以新的作者请求重新规划",
                )
                onRequested()
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    fun discardPendingOutlineDraft(draft: MobilePendingOutlineDraft) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在丢弃大纲草稿…", error = null)
            try {
                repository.discardPendingOutlineDraft(draft)
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    pendingOutlineDraft = null,
                    notice = "大纲草稿已丢弃；正式大纲未改变",
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    fun takePendingAssistantRequest(): String? {
        val request = uiState.value.pendingAssistantRequest
        if (request != null) uiState.value = uiState.value.copy(pendingAssistantRequest = null)
        return request
    }

    fun refreshAssistantConversations(projectId: String, selectCurrent: Boolean = false) {
        viewModelScope.launch {
            runCatching { repository.assistantConversations(projectId) }
                .onSuccess { conversations ->
                    val current = uiState.value.assistantConversationId
                    val selected = when {
                        current != null && conversations.any { it.id == current } -> current
                        selectCurrent -> conversations.firstOrNull()?.id
                        else -> null
                    }
                    uiState.value = uiState.value.copy(
                        assistantConversations = conversations,
                        assistantConversationId = selected,
                    )
                    if (selected != null) loadAssistantConversation(projectId, selected)
                }
        }
    }

    fun loadAssistantConversation(projectId: String, conversationId: String) {
        viewModelScope.launch {
            runCatching { repository.assistantMessages(projectId, conversationId) }
                .onSuccess { messages ->
                    uiState.value = uiState.value.copy(
                        assistantConversationId = conversationId,
                        assistantMessages = messages,
                        assistantOutput = messages.lastOrNull { it.role == "assistant" }?.content.orEmpty(),
                        assistantToolLog = messages.lastOrNull { it.role == "assistant" }?.toolLogs.orEmpty(),
                    )
                }
                .onFailure(::showError)
        }
    }

    fun newAssistantConversation() {
        uiState.value = uiState.value.copy(
            assistantConversationId = null,
            assistantMessages = emptyList(),
            assistantOutput = "",
            assistantReasoning = "",
            assistantToolLog = emptyList(),
        )
    }

    fun resolveConflict(conflict: LocalConflict, choice: String) = launchActivity("正在处理版本分岔…") {
        repository.resolveConflict(conflict, choice)
        "冲突已处理；双方原始版本仍保留在 Gateway"
    }

    fun disconnect(clearOfflineData: Boolean) = launchActivity("正在断开设备…") {
        val revokedRemotely = repository.disconnect(clearOfflineData)
        when {
            !revokedRemotely -> "本机已断开；Gateway 当前不可达，请稍后在管理页撤销这台设备"
            clearOfflineData -> "已撤销设备授权并清除本机离线副本"
            else -> "已撤销设备授权，离线副本仍保留"
        }
    }

    fun clearNotice() {
        uiState.value = uiState.value.copy(notice = null, error = null)
    }

    fun reportError(message: String) {
        uiState.value = uiState.value.copy(error = message)
    }

    fun reportNotice(message: String) {
        uiState.value = uiState.value.copy(notice = message)
    }

    private fun launchActivity(label: String, action: suspend () -> String) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = label, error = null)
            try {
                val notice = action()
                uiState.value = uiState.value.copy(busy = false, activity = "", notice = notice)
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(busy = false, activity = "")
                showError(error)
            }
        }
    }

    private fun launchCreation(label: String, action: suspend () -> String) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                creationRunning = true,
                creationActivity = label,
                creationReplyDelta = "",
                creationProgressEvents = emptyList(),
                error = null,
            )
            try {
                val notice = action()
                uiState.value = uiState.value.copy(
                    creationRunning = false,
                    creationActivity = "",
                    notice = notice,
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(creationRunning = false, creationActivity = "")
                showError(error)
            }
        }
    }

    private suspend fun showCreationProgress(event: CreationAgentProgressEvent) {
        val current = uiState.value
        val duplicate = event.sequence > 0 && current.creationProgressEvents.any {
            it.clientTurnId == event.clientTurnId && it.sequence == event.sequence
        }
        if (duplicate) return
        val delta = (event.data["delta"] as? JsonPrimitive)?.contentOrNull.orEmpty()
        uiState.value = current.copy(
            creationActivity = event.message.ifBlank { current.creationActivity },
            creationReplyDelta = if (event.type == "reply_delta") {
                current.creationReplyDelta + delta
            } else current.creationReplyDelta,
            creationProgressEvents = (current.creationProgressEvents + event).takeLast(80),
        )
    }

    private fun showError(error: Throwable) {
        uiState.value = uiState.value.copy(error = error.toUserFacingMessage())
    }

    private fun parseAssistantEvent(raw: String): AssistantEventUpdate = runCatching {
        if (raw == "[DONE]") return@runCatching AssistantEventUpdate(activity = "")
        val event = json.parseToJsonElement(raw) as? JsonObject
            ?: return@runCatching AssistantEventUpdate(output = raw)
        val type = event["type"]?.jsonPrimitive?.contentOrNull.orEmpty()
        val directContent = listOf("content", "text", "reply")
            .firstNotNullOfOrNull { key -> event[key]?.jsonPrimitive?.contentOrNull }
        val message = event["message"]?.jsonPrimitive?.contentOrNull
        val detail = event["detail"]?.jsonPrimitive?.contentOrNull
        val tool = event["tool"]?.jsonPrimitive?.contentOrNull
        val delta = event["delta"]?.jsonPrimitive?.contentOrNull.orEmpty()

        when (type) {
            "content_delta" -> AssistantEventUpdate(output = delta)
            "reasoning_delta" -> AssistantEventUpdate(
                reasoning = delta,
                activity = "模型正在思考…",
            )
            "complete" -> {
                val data = event["data"] as? JsonObject
                val reply = data?.get("reply")?.jsonPrimitive?.contentOrNull
                    ?: (data?.get("message") as? JsonObject)
                        ?.get("content")?.jsonPrimitive?.contentOrNull
                    ?: directContent.orEmpty()
                val reasoning = data?.get("reasoning_content")?.jsonPrimitive?.contentOrNull
                val draft = (data?.get("applied_actions") as? JsonArray)
                    .orEmpty()
                    .mapNotNull { it as? JsonObject }
                    .firstOrNull { action ->
                        action["tool"]?.jsonPrimitive?.contentOrNull == "chapter_writer" &&
                            action["status"]?.jsonPrimitive?.contentOrNull == "ok"
                    }
                    ?.get("data") as? JsonObject
                val outlineDraft = (data?.get("applied_actions") as? JsonArray)
                    .orEmpty()
                    .mapNotNull { it as? JsonObject }
                    .firstOrNull { action ->
                        action["tool"]?.jsonPrimitive?.contentOrNull in
                            setOf("outline_writer", "save_external_outline_draft") &&
                            action["status"]?.jsonPrimitive?.contentOrNull in setOf("ok", "blocked")
                    }
                    ?.get("data") as? JsonObject
                AssistantEventUpdate(
                    output = reply,
                    replaceOutput = true,
                    reasoning = reasoning,
                    replaceReasoning = reasoning != null,
                    activity = "",
                    draftData = draft,
                    outlineDraftData = outlineDraft,
                )
            }
            "chapter_draft" -> AssistantEventUpdate(
                activity = detail ?: "章节草稿已生成，等待作者确认",
                draftData = event["data"] as? JsonObject,
            )
            "chapter_draft_delta" -> AssistantEventUpdate(
                activity = "章节正在正文编辑器中实时生成…",
                draftData = event["data"] as? JsonObject,
                draftDelta = delta,
            )
            "outline_draft" -> AssistantEventUpdate(
                activity = detail ?: "大纲草稿已生成，等待作者审阅",
                outlineDraftData = event["data"] as? JsonObject,
            )
            "conversation" -> {
                val conversation = event["conversation"] as? JsonObject
                AssistantEventUpdate(conversationId = conversation?.get("id")?.jsonPrimitive?.contentOrNull)
            }
            "run" -> {
                val run = event["run"] as? JsonObject
                AssistantEventUpdate(
                    runId = run?.get("run_id")?.jsonPrimitive?.contentOrNull
                        ?: run?.get("id")?.jsonPrimitive?.contentOrNull,
                    operationId = run?.get("operation_id")?.jsonPrimitive?.contentOrNull,
                    activity = "服务端任务已登记，正在执行…",
                )
            }
            "done" -> AssistantEventUpdate(activity = "")
            "error", "permission_required" -> AssistantEventUpdate(
                output = message ?: detail ?: directContent.orEmpty(),
                replaceOutput = true,
                activity = "",
            )
            "tool_call" -> AssistantEventUpdate(
                activity = "模型准备调用：${tool ?: "工作区工具"}",
                toolLog = "准备调用：${tool ?: "工作区工具"}",
            )
            "tool", "search_result", "write_result" -> AssistantEventUpdate(
                activity = detail ?: message ?: tool?.let { "$it 已执行" } ?: "工作区工具已执行",
                toolLog = detail ?: message ?: tool?.let { "$it 已执行" },
            )
            "search_start", "write_start" -> AssistantEventUpdate(
                activity = message ?: tool?.let { "正在执行：$it" } ?: "正在执行工作区工具…",
            )
            "iteration_start", "iteration_end", "status" -> AssistantEventUpdate(
                activity = message ?: detail ?: "正在执行工作区流程…",
            )
            else -> when {
                directContent != null -> AssistantEventUpdate(output = directContent)
                message != null || detail != null -> AssistantEventUpdate(activity = message ?: detail)
                else -> AssistantEventUpdate()
            }
        }
    }.getOrElse { AssistantEventUpdate(output = raw) }
}

private data class AssistantEventUpdate(
    val output: String? = null,
    val replaceOutput: Boolean = false,
    val reasoning: String? = null,
    val replaceReasoning: Boolean = false,
    val activity: String? = null,
    val conversationId: String? = null,
    val runId: String? = null,
    val operationId: String? = null,
    val toolLog: String? = null,
    val draftData: JsonObject? = null,
    val draftDelta: String? = null,
    val outlineDraftData: JsonObject? = null,
)

fun ReplicaEntity.payload(): JsonObject? = payloadJson?.let {
    runCatching { Json.parseToJsonElement(it) as JsonObject }.getOrNull()
}

fun ReplicaEntity.text(name: String): String =
    payload()?.get(name)?.jsonPrimitive?.contentOrNull.orEmpty()

fun ReplicaEntity.number(name: String): Int =
    payload()?.get(name)?.jsonPrimitive?.intOrNull ?: 0

private fun kotlinx.serialization.json.JsonObjectBuilder.putAny(key: String, value: Any?) {
    when (value) {
        null -> put(key, kotlinx.serialization.json.JsonNull)
        is String -> put(key, value)
        is Int -> put(key, value)
        is Long -> put(key, value)
        is Float -> put(key, value)
        is Double -> put(key, value)
        is Boolean -> put(key, value)
        is JsonElement -> put(key, value)
        else -> put(key, value.toString())
    }
}
