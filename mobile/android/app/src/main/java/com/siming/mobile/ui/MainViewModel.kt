package com.siming.mobile.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.AssistantRoute
import com.siming.mobile.data.AssistantModelRoute
import com.siming.mobile.data.creation.CreationExecutionRoute
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
    val assistantActivity: String = "",
    val assistantRunning: Boolean = false,
    val directApi: DirectApiSummary? = null,
    val discoveredModels: List<String> = emptyList(),
    val activeCreationId: String? = null,
    val creationRunning: Boolean = false,
    val creationActivity: String = "",
)

@OptIn(ExperimentalSerializationApi::class)
class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = SimingRepository(application)
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private var assistantJob: Job? = null

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
            repository.runCreationAgentTurn(sessionId, input.brief) { activity ->
                uiState.value = uiState.value.copy(creationActivity = activity)
            }
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
            repository.runCreationAgentTurn(sessionId, message) { activity ->
                uiState.value = uiState.value.copy(creationActivity = activity)
            }
            "本轮已完成；确定事实已立即写入结构化立项资料"
        }
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
        onConfigured: () -> Unit,
    ) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(
                busy = true,
                activity = "正在用当前模型进行真实对话测试…",
                error = null,
            )
            try {
                val effectiveModel = model.trim().ifBlank {
                    val models = repository.discoverDirectModels(baseUrl, apiKey)
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

    fun importNovel(fileName: String, content: String, onCreated: (String) -> Unit) {
        viewModelScope.launch {
            uiState.value = uiState.value.copy(busy = true, activity = "正在拆分并通过当前创作通道建档…")
            try {
                require(content.length <= 20_000_000) { "单个导入文件不能超过 2000 万字符" }
                val title = fileName.substringBeforeLast('.').ifBlank { "导入作品" }
                val projectId = repository.createProject(title, "由手机导入的已有小说")
                val chapters = splitChapters(content)
                chapters.forEachIndexed { index, (chapterTitle, chapterContent) ->
                    saveRecordInternal(
                        projectId,
                        "chapter",
                        null,
                        mapOf(
                            "title" to chapterTitle.ifBlank { "第 ${index + 1} 章" },
                            "content" to chapterContent,
                            "word_count" to chapterContent.count { !it.isWhitespace() },
                            "current_version" to 1,
                        ),
                    )
                }
                uiState.value = uiState.value.copy(
                    busy = false,
                    activity = "",
                    notice = if (connection.value != null) {
                        "已通过 PC 端规范 API 导入 ${chapters.size} 章"
                    } else {
                        "已在手机导入 ${chapters.size} 章，连接 Gateway 后自动同步"
                    },
                )
                onCreated(projectId)
            } catch (error: Exception) {
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
        scope: String,
        prompt: String,
        modelRoute: AssistantModelRoute,
    ) {
        if (prompt.isBlank() || assistantJob?.isActive == true) return
        assistantJob = viewModelScope.launch {
            uiState.value = uiState.value.copy(
                assistantRunning = true,
                assistantOutput = "",
                assistantActivity = "正在加载与 PC 同源的工作区流程…",
                error = null,
            )
            try {
                val route = repository.runAssistant(projectId, scope, prompt, modelRoute) { event ->
                    val update = parseAssistantEvent(event)
                    val current = uiState.value
                    uiState.value = uiState.value.copy(
                        assistantOutput = when {
                            update.output == null -> current.assistantOutput
                            update.replaceOutput -> update.output
                            else -> current.assistantOutput + update.output
                        },
                        assistantActivity = update.activity ?: current.assistantActivity,
                    )
                }
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                    notice = when (route) {
                        AssistantRoute.GatewayPc ->
                            "AI 任务已使用 PC 配置线路执行，相关修改已同步到手机"
                        AssistantRoute.GatewayMobileKey ->
                            "AI 任务已使用手机 Key 执行；提示词、工具和落库流程与 PC 一致"
                        AssistantRoute.DirectApi ->
                            "手机独立工作区任务已完成，本地产生的修改已写入手机副本"
                    },
                )
            } catch (_: CancellationException) {
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                    notice = "任务已取消；未提交的章节不会写入，已生成草稿可在下次相同请求中恢复",
                )
            } catch (error: Exception) {
                uiState.value = uiState.value.copy(
                    assistantRunning = false,
                    assistantActivity = "",
                )
                showError(error)
            } finally {
                assistantJob = null
            }
        }
    }

    fun cancelAssistant() {
        val job = assistantJob ?: return
        if (!job.isActive) return
        uiState.value = uiState.value.copy(assistantActivity = "正在取消；不会写入未提交的章节…")
        job.cancel(CancellationException("用户取消手机工作区任务"))
    }

    fun saveAssistantAsChapter(projectId: String, onSaved: () -> Unit = {}) {
        val content = uiState.value.assistantOutput.trim()
        if (content.isBlank()) return
        viewModelScope.launch {
            try {
                val stamp = Instant.now().toString().take(16).replace('T', ' ')
                saveRecordInternal(
                    projectId,
                    "chapter",
                    null,
                    mapOf(
                        "title" to "AI 生成 $stamp",
                        "content" to content,
                        "word_count" to content.count { !it.isWhitespace() },
                        "current_version" to 1,
                    ),
                )
                uiState.value = uiState.value.copy(notice = "AI 结果已保存为本机新章节")
                onSaved()
            } catch (error: Exception) {
                showError(error)
            }
        }
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

    private fun showError(error: Throwable) {
        uiState.value = uiState.value.copy(error = error.toUserFacingMessage())
    }

    private fun splitChapters(content: String): List<Pair<String, String>> {
        val marker = Regex("(?m)^(第[\\p{L}\\p{N}一二三四五六七八九十百千万零〇两]{1,16}[章节卷回部].*)$")
        val matches = marker.findAll(content).toList()
        val chapters = if (matches.isEmpty()) {
            content.chunked(5_000).mapIndexed { index, text ->
                "第 ${index + 1} 章" to text.trim()
            }.filter { it.second.isNotBlank() }
        } else {
            matches.mapIndexed { index, match ->
                val start = match.range.last + 1
                val end = matches.getOrNull(index + 1)?.range?.first ?: content.length
                match.value.trim() to content.substring(start, end).trim()
            }.filter { it.second.isNotBlank() }
        }
        return chapters.flatMap { (title, body) ->
            body.chunked(200_000).mapIndexed { index, part ->
                (if (index == 0) title else "$title（续 ${index + 1}）") to part
            }
        }
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

        when (type) {
            "content" -> AssistantEventUpdate(output = directContent.orEmpty())
            "complete" -> {
                val data = event["data"] as? JsonObject
                val reply = data?.get("reply")?.jsonPrimitive?.contentOrNull
                    ?: (data?.get("message") as? JsonObject)
                        ?.get("content")?.jsonPrimitive?.contentOrNull
                    ?: directContent.orEmpty()
                AssistantEventUpdate(output = reply, replaceOutput = true, activity = "")
            }
            "done" -> AssistantEventUpdate(activity = "")
            "error", "permission_required" -> AssistantEventUpdate(
                output = message ?: detail ?: directContent.orEmpty(),
                replaceOutput = true,
                activity = "",
            )
            "thinking", "thinking_delta" -> AssistantEventUpdate(activity = "模型正在生成回复…")
            "tool_call" -> AssistantEventUpdate(activity = "模型准备调用：${tool ?: "工作区工具"}")
            "tool", "search_result", "write_result" -> AssistantEventUpdate(
                activity = detail ?: message ?: tool?.let { "$it 已执行" } ?: "工作区工具已执行",
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
    val activity: String? = null,
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
