#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: anchor count {count} for {old[:80]!r}")
    write(path, text.replace(old, new, 1))


write(
    "mobile/android/app/src/main/java/com/siming/mobile/data/MobileProjectTools.kt",
    dedent(
        r'''
        package com.siming.mobile.data

        import com.siming.mobile.data.local.ReplicaEntity
        import kotlinx.serialization.json.Json
        import kotlinx.serialization.json.JsonObject
        import kotlinx.serialization.json.JsonPrimitive
        import kotlinx.serialization.json.contentOrNull
        import kotlinx.serialization.json.intOrNull

        data class MobileExportFile(
            val filename: String,
            val mimeType: String,
            val bytes: ByteArray,
        )

        data class MobileCatalogingProgress(
            val jobId: String,
            val status: String,
            val totalChapters: Int,
            val completedChapters: Int,
            val failedChapters: Int,
        )

        private val projectToolsJson = Json { ignoreUnknownKeys = true }

        internal fun JsonObject.toMobileCatalogingProgress(): MobileCatalogingProgress =
            MobileCatalogingProgress(
                jobId = (get("id") as? JsonPrimitive)?.contentOrNull.orEmpty(),
                status = (get("status") as? JsonPrimitive)?.contentOrNull.orEmpty(),
                totalChapters = (get("total_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
                completedChapters = (get("completed_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
                failedChapters = (get("failed_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
            )

        internal fun exportMimeType(format: String): String = when (format.lowercase()) {
            "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            "pdf" -> "application/pdf"
            else -> "text/plain"
        }

        internal fun buildLocalNovelExport(
            project: ReplicaEntity,
            chapters: List<ReplicaEntity>,
        ): MobileExportFile {
            val title = project.payloadText("title").ifBlank { "未命名作品" }
            val safeTitle = title
                .replace(Regex("[\\\\/:*?\"<>|]"), "_")
                .trim()
                .ifBlank { "司命导出" }
                .take(80)
            val content = buildString {
                append(title).append("\n\n")
                chapters.forEachIndexed { index, chapter ->
                    val chapterTitle = chapter.payloadText("title").ifBlank { "第 ${index + 1} 章" }
                    append(chapterTitle).append("\n\n")
                    append(chapter.payloadText("content").trim()).append("\n\n")
                }
            }.trimEnd() + "\n"
            return MobileExportFile(
                filename = "$safeTitle.txt",
                mimeType = "text/plain",
                bytes = content.toByteArray(Charsets.UTF_8),
            )
        }

        private fun ReplicaEntity.payloadText(key: String): String {
            val payload = payloadJson
                ?.let { runCatching { projectToolsJson.parseToJsonElement(it) as? JsonObject }.getOrNull() }
                ?: return ""
            return (payload[key] as? JsonPrimitive)?.contentOrNull.orEmpty()
        }
        '''
    ).lstrip(),
)

write(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/ProjectSectionNavigation.kt",
    dedent(
        r'''
        package com.siming.mobile.ui

        import androidx.compose.foundation.layout.Arrangement
        import androidx.compose.foundation.layout.Column
        import androidx.compose.foundation.layout.ExperimentalLayoutApi
        import androidx.compose.foundation.layout.FlowRow
        import androidx.compose.foundation.layout.Row
        import androidx.compose.foundation.layout.fillMaxWidth
        import androidx.compose.foundation.layout.padding
        import androidx.compose.material3.AssistChip
        import androidx.compose.material3.AssistChipDefaults
        import androidx.compose.material3.MaterialTheme
        import androidx.compose.material3.OutlinedButton
        import androidx.compose.material3.Surface
        import androidx.compose.material3.Text
        import androidx.compose.runtime.Composable
        import androidx.compose.ui.Modifier
        import androidx.compose.ui.graphics.Color
        import androidx.compose.ui.unit.dp

        private data class ProjectNavGroup(
            val key: String,
            val label: String,
            val sections: List<Pair<String, String>>,
        )

        private val projectNavGroups = listOf(
            ProjectNavGroup("create", "创作", listOf("assistant" to "AI 共创", "chapter" to "正文")),
            ProjectNavGroup("structure", "结构", listOf("outline" to "大纲", "character" to "角色", "world" to "世界")),
            ProjectNavGroup("manage", "管理", listOf("foreshadowing" to "伏笔", "governance" to "治理", "tools" to "工具")),
        )

        @OptIn(ExperimentalLayoutApi::class)
        @Composable
        internal fun ProjectSectionNavigation(
            selected: String,
            onSelected: (String) -> Unit,
        ) {
            val activeGroup = projectNavGroups.firstOrNull { group ->
                group.sections.any { it.first == selected }
            } ?: projectNavGroups.first()
            Surface(color = SimingPaperWarm) {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 9.dp),
                    verticalArrangement = Arrangement.spacedBy(7.dp),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(7.dp),
                    ) {
                        projectNavGroups.forEach { group ->
                            OutlinedButton(
                                onClick = { onSelected(group.sections.first().first) },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(group.label)
                            }
                        }
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        activeGroup.sections.forEach { (key, label) ->
                            AssistChip(
                                onClick = { onSelected(key) },
                                label = { Text(label) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = if (selected == key) MaterialTheme.colorScheme.primaryContainer else Color.White,
                                    labelColor = if (selected == key) SimingCinnabar else MaterialTheme.colorScheme.onSurface,
                                ),
                            )
                        }
                    }
                }
            }
        }
        '''
    ).lstrip(),
)

write(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/ProjectToolsPanel.kt",
    dedent(
        r'''
        package com.siming.mobile.ui

        import androidx.compose.foundation.layout.Arrangement
        import androidx.compose.foundation.layout.Column
        import androidx.compose.foundation.layout.FlowRow
        import androidx.compose.foundation.layout.PaddingValues
        import androidx.compose.foundation.layout.Row
        import androidx.compose.foundation.layout.Spacer
        import androidx.compose.foundation.layout.fillMaxSize
        import androidx.compose.foundation.layout.fillMaxWidth
        import androidx.compose.foundation.layout.padding
        import androidx.compose.foundation.layout.width
        import androidx.compose.foundation.lazy.LazyColumn
        import androidx.compose.material.icons.Icons
        import androidx.compose.material.icons.outlined.AutoAwesome
        import androidx.compose.material.icons.outlined.Cancel
        import androidx.compose.material.icons.outlined.Download
        import androidx.compose.material.icons.outlined.FilePresent
        import androidx.compose.material3.Button
        import androidx.compose.material3.Card
        import androidx.compose.material3.CardDefaults
        import androidx.compose.material3.CircularProgressIndicator
        import androidx.compose.material3.Icon
        import androidx.compose.material3.LinearProgressIndicator
        import androidx.compose.material3.MaterialTheme
        import androidx.compose.material3.OutlinedButton
        import androidx.compose.material3.Text
        import androidx.compose.material3.TextButton
        import androidx.compose.runtime.Composable
        import androidx.compose.runtime.getValue
        import androidx.compose.ui.Modifier
        import androidx.compose.ui.unit.dp
        import androidx.lifecycle.compose.collectAsStateWithLifecycle
        import com.siming.mobile.data.MobileExportFile
        import com.siming.mobile.data.local.ReplicaEntity

        @Composable
        internal fun ProjectToolsPanel(
            project: ReplicaEntity,
            online: Boolean,
            ui: MobileUiState,
            viewModel: MainViewModel,
            onExportReady: (MobileExportFile) -> Unit,
        ) {
            val chapters by viewModel.entities(project.projectId, "chapter")
                .collectAsStateWithLifecycle(initialValue = emptyList())
            val totalWords = chapters.sumOf { it.text("content").count { char -> !char.isWhitespace() } }
            val catalogingHere = ui.catalogingProjectId == project.projectId
            val progress = if (ui.catalogingTotal > 0) {
                ui.catalogingCompleted.toFloat() / ui.catalogingTotal.toFloat()
            } else 0f

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp, 18.dp, 16.dp, 96.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                item {
                    ScreenHeading(
                        kicker = "PROJECT TOOLBOX",
                        title = "作品工具",
                        detail = "导入后的建档、全书导出和作品维护集中在这里，减少在多个页面之间来回寻找功能。",
                    )
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        MicroTag("${chapters.size} 章", SimingBlue)
                        MicroTag("${totalWords} 字", SimingGreen)
                        MicroTag(if (online) "PC 权威模式" else "本机模式", MaterialTheme.colorScheme.secondary)
                    }
                }
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Outlined.AutoAwesome, null)
                                Text("作品建档", style = MaterialTheme.typography.titleMedium)
                            }
                            Text(
                                if (online) {
                                    "使用 PC 与桌面端相同的 Cataloging 流程扫描已导入章节，生成章节摘要、角色/设定变化和可写入候选资料。"
                                } else {
                                    "完整作品建档依赖 PC 权威 Cataloging，以保证角色、世界观、摘要和治理数据不会出现两套口径。连接 Gateway 后即可启动。"
                                },
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            if (catalogingHere && ui.catalogingRunning) {
                                LinearProgressIndicator(
                                    progress = { progress.coerceIn(0f, 1f) },
                                    modifier = Modifier.fillMaxWidth(),
                                )
                                Text(
                                    "${ui.catalogingCompleted}/${ui.catalogingTotal} 章 · ${ui.catalogingActivity.ifBlank { "正在建档" }}",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                                if (ui.catalogingFailed > 0) {
                                    Text("${ui.catalogingFailed} 章需要处理", color = MaterialTheme.colorScheme.error)
                                }
                                TextButton(onClick = { viewModel.cancelCataloging(project.projectId) }) {
                                    Icon(Icons.Outlined.Cancel, null)
                                    Spacer(Modifier.width(6.dp))
                                    Text("取消建档")
                                }
                            } else {
                                Button(
                                    onClick = { viewModel.startCataloging(project.projectId) },
                                    enabled = online && chapters.isNotEmpty() && !ui.catalogingRunning,
                                ) {
                                    Icon(Icons.Outlined.AutoAwesome, null)
                                    Spacer(Modifier.width(7.dp))
                                    Text(if (online) "开始全书建档" else "连接 PC 后建档")
                                }
                            }
                        }
                    }
                }
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Outlined.Download, null)
                                Text("导出小说", style = MaterialTheme.typography.titleMedium)
                            }
                            Text(
                                if (online) {
                                    "TXT、Word 和 PDF 复用 PC 的正式导出服务；导出完成后由 Android 系统文件选择器决定保存位置。"
                                } else {
                                    "离线和手机独立模式仍可从本机章节副本导出 TXT；Word / PDF 需要连接 PC。"
                                },
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            if (ui.exportRunning) {
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    CircularProgressIndicator()
                                    Text("正在准备导出文件…")
                                }
                            } else {
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(onClick = { viewModel.prepareExport(project.projectId, "txt", onExportReady) }) {
                                        Icon(Icons.Outlined.FilePresent, null)
                                        Spacer(Modifier.width(6.dp))
                                        Text("TXT")
                                    }
                                    OutlinedButton(
                                        onClick = { viewModel.prepareExport(project.projectId, "docx", onExportReady) },
                                        enabled = online,
                                    ) { Text("Word") }
                                    OutlinedButton(
                                        onClick = { viewModel.prepareExport(project.projectId, "pdf", onExportReady) },
                                        enabled = online,
                                    ) { Text("PDF") }
                                }
                            }
                        }
                    }
                }
            }
        }
        '''
    ).lstrip(),
)

write(
    "mobile/android/app/src/test/java/com/siming/mobile/data/MobileProjectToolsTest.kt",
    dedent(
        r'''
        package com.siming.mobile.data

        import com.siming.mobile.data.local.ReplicaEntity
        import kotlin.test.Test
        import kotlin.test.assertEquals
        import kotlin.test.assertTrue

        class MobileProjectToolsTest {
            @Test
            fun `local txt export keeps chapter order and content`() {
                val project = entity("p|project|p", "project", "p", "{\"title\":\"测试/小说\"}")
                val first = entity("p|chapter|1", "chapter", "1", "{\"title\":\"第一章\",\"content\":\"正文一\"}")
                val second = entity("p|chapter|2", "chapter", "2", "{\"title\":\"第二章\",\"content\":\"正文二\"}")
                val file = buildLocalNovelExport(project, listOf(first, second))
                val text = file.bytes.toString(Charsets.UTF_8)
                assertEquals("测试_小说.txt", file.filename)
                assertTrue(text.indexOf("第一章") < text.indexOf("第二章"))
                assertTrue(text.contains("正文一"))
                assertTrue(text.contains("正文二"))
            }

            private fun entity(key: String, type: String, id: String, payload: String) = ReplicaEntity(
                key = key,
                projectId = "p",
                entityType = type,
                entityId = id,
                revision = 0,
                operation = "upsert",
                payloadJson = payload,
                contentHash = "hash",
                serverModifiedAt = "2026-08-18T00:00:00Z",
            )
        }
        '''
    ).lstrip(),
)

write(
    "mobile/android/app/src/test/java/com/siming/mobile/data/network/ProjectToolPathsTest.kt",
    dedent(
        r'''
        package com.siming.mobile.data.network

        import kotlin.test.Test
        import kotlin.test.assertEquals

        class ProjectToolPathsTest {
            @Test
            fun `cataloging and export use canonical PC routes`() {
                assertEquals("/api/v1/projects/p1/cataloging/start", PcApiPaths.catalogingStart("p1"))
                assertEquals("/api/v1/projects/p1/cataloging/j1", PcApiPaths.catalogingJob("p1", "j1"))
                assertEquals("/api/v1/projects/p1/cataloging/j1/stream", PcApiPaths.catalogingStream("p1", "j1"))
                assertEquals("/api/v1/projects/p1/cataloging/j1/cancel", PcApiPaths.catalogingCancel("p1", "j1"))
                assertEquals("/api/v1/projects/p1/export", PcApiPaths.projectExport("p1"))
                assertEquals("/api/v1/projects/p1/export/download/f1", PcApiPaths.projectExportDownload("p1", "f1"))
            }
        }
        '''
    ).lstrip(),
)

# PC route registry.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/PcApiPaths.kt",
    '    fun assistantStream(projectId: String): String =\n        "${project(projectId)}/ai/workspace-assistant/stream"\n',
    '    fun catalogingStart(projectId: String): String = "${project(projectId)}/cataloging/start"\n\n'
    '    fun catalogingJob(projectId: String, jobId: String): String =\n        "${project(projectId)}/cataloging/${segment(jobId)}"\n\n'
    '    fun catalogingStream(projectId: String, jobId: String): String =\n        "${catalogingJob(projectId, jobId)}/stream"\n\n'
    '    fun catalogingCancel(projectId: String, jobId: String): String =\n        "${catalogingJob(projectId, jobId)}/cancel"\n\n'
    '    fun projectExport(projectId: String): String = "${project(projectId)}/export"\n\n'
    '    fun projectExportDownload(projectId: String, fileId: String): String =\n        "${projectExport(projectId)}/download/${segment(fileId)}"\n\n'
    '    fun assistantStream(projectId: String): String =\n        "${project(projectId)}/ai/workspace-assistant/stream"\n',
)

# Gateway API: canonical cataloging + export, including SSE and binary download.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt",
    '    suspend fun saveGovernanceEntity(\n',
    dedent(
        r'''
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

        '''
    ) + '    suspend fun saveGovernanceEntity(\n',
)

replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt",
    '    suspend fun streamAssistant(\n',
    dedent(
        r'''
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

        '''
    ) + '    suspend fun streamAssistant(\n',
)

# Repository project tools.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt",
    '    suspend fun listChapterSnapshots(projectId: String, chapterId: String): JsonObject =\n',
    dedent(
        r'''
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

        '''
    ) + '    suspend fun listChapterSnapshots(projectId: String, chapterId: String): JsonObject =\n',
)

# MainViewModel state and actions.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    'import com.siming.mobile.data.AssistantModelRoute\n',
    'import com.siming.mobile.data.AssistantModelRoute\nimport com.siming.mobile.data.MobileCatalogingProgress\nimport com.siming.mobile.data.MobileExportFile\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '    val creationActivity: String = "",\n)',
    '    val creationActivity: String = "",\n'
    '    val pendingCatalogingProjectId: String? = null,\n'
    '    val importedChapterCount: Int = 0,\n'
    '    val catalogingProjectId: String? = null,\n'
    '    val catalogingJobId: String? = null,\n'
    '    val catalogingStatus: String = "",\n'
    '    val catalogingTotal: Int = 0,\n'
    '    val catalogingCompleted: Int = 0,\n'
    '    val catalogingFailed: Int = 0,\n'
    '    val catalogingRunning: Boolean = false,\n'
    '    val catalogingActivity: String = "",\n'
    '    val exportRunning: Boolean = false,\n'
    ')',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '    private var assistantJob: Job? = null\n',
    '    private var assistantJob: Job? = null\n    private var catalogingJob: Job? = null\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '    suspend fun chapterSnapshots(projectId: String, chapterId: String): JsonObject =\n',
    dedent(
        r'''
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

        '''
    ) + '    suspend fun chapterSnapshots(projectId: String, chapterId: String): JsonObject =\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '                    notice = if (connection.value != null) {\n                        "已通过 PC 端规范 API 导入 ${chapters.size} 章"\n                    } else {\n                        "已在手机导入 ${chapters.size} 章，连接 Gateway 后自动同步"\n                    },\n',
    '                    notice = "已导入 ${chapters.size} 章，可以继续作品建档或直接阅读编辑",\n'
    '                    pendingCatalogingProjectId = projectId,\n'
    '                    importedChapterCount = chapters.size,\n',
)

# MainActivity file-system export handoff.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/MainActivity.kt",
    'import android.os.Bundle\n',
    'import android.app.Activity\nimport android.content.Intent\nimport android.os.Bundle\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/MainActivity.kt",
    'import com.siming.mobile.ui.MainViewModel\n',
    'import com.siming.mobile.data.MobileExportFile\nimport com.siming.mobile.ui.MainViewModel\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/MainActivity.kt",
    '    private var importCallback: ((String, String) -> Unit)? = null\n',
    dedent(
        r'''
            private var pendingExport: MobileExportFile? = null
            private val exportSaver = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
                val file = pendingExport
                pendingExport = null
                val uri = result.data?.data
                if (result.resultCode != Activity.RESULT_OK || uri == null || file == null) return@registerForActivityResult
                lifecycleScope.launch {
                    runCatching {
                        withContext(Dispatchers.IO) {
                            contentResolver.openOutputStream(uri, "w")?.use { output ->
                                output.write(file.bytes)
                            } ?: error("无法打开导出位置")
                        }
                    }.onSuccess { viewModel.reportNotice("已导出：${file.filename}") }
                        .onFailure { viewModel.reportError(it.message ?: "导出文件写入失败") }
                }
            }

            private fun saveExport(file: MobileExportFile) {
                pendingExport = file
                exportSaver.launch(
                    Intent(Intent.ACTION_CREATE_DOCUMENT)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .setType(file.mimeType)
                        .putExtra(Intent.EXTRA_TITLE, file.filename),
                )
            }

            private var importCallback: ((String, String) -> Unit)? = null
        '''
    ),
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/MainActivity.kt",
    '                    onPickText = { callback ->\n                        importCallback = callback\n                        textPicker.launch("text/*")\n                    },\n',
    '                    onPickText = { callback ->\n                        importCallback = callback\n                        textPicker.launch("text/*")\n                    },\n'
    '                    onSaveExport = ::saveExport,\n',
)

# SimingApp: tools section, grouped navigation, import prompt and export handoff.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    'import com.siming.mobile.data.AssistantModelRoute\n',
    'import com.siming.mobile.data.AssistantModelRoute\nimport com.siming.mobile.data.MobileExportFile\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '    EntitySection("governance", "治理", Icons.Outlined.WarningAmber, "还没有叙事承诺或治理记录"),\n)',
    '    EntitySection("governance", "治理", Icons.Outlined.WarningAmber, "还没有叙事承诺或治理记录"),\n'
    '    EntitySection("tools", "工具", Icons.Outlined.Settings, ""),\n'
    ')',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '    onPickText: (((String, String) -> Unit) -> Unit),\n)',
    '    onPickText: (((String, String) -> Unit) -> Unit),\n    onSaveExport: (MobileExportFile) -> Unit,\n)',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '            snackbar = snackbar,\n        )\n',
    '            snackbar = snackbar,\n            onSaveExport = onSaveExport,\n        )\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    'private fun ProjectScreen(\n    viewModel: MainViewModel,\n    project: ReplicaEntity,\n    onBack: () -> Unit,\n    snackbar: SnackbarHostState,\n) {',
    'private fun ProjectScreen(\n    viewModel: MainViewModel,\n    project: ReplicaEntity,\n    onBack: () -> Unit,\n    snackbar: SnackbarHostState,\n    onSaveExport: (MobileExportFile) -> Unit,\n) {',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '            if (section != "assistant") {\n',
    '            if (section !in setOf("assistant", "tools")) {\n',
)
nav_old = dedent(
    r'''
                Row(
                    modifier = Modifier
                        .horizontalScroll(rememberScrollState())
                        .background(SimingPaperWarm)
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    AssistChip(
                        onClick = { section = "assistant" },
                        label = { Text("AI 共创") },
                        leadingIcon = { Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(17.dp)) },
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = if (section == "assistant") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            labelColor = if (section == "assistant") SimingCinnabar else MaterialTheme.colorScheme.onSurface,
                        ),
                        border = AssistChipDefaults.assistChipBorder(
                            enabled = true,
                            borderColor = if (section == "assistant") SimingCinnabar else MaterialTheme.colorScheme.outlineVariant,
                        ),
                    )
                    entitySections.forEach { item ->
                        AssistChip(
                            onClick = { section = item.type },
                            label = { Text(item.label) },
                            leadingIcon = { Icon(item.icon, null, Modifier.size(17.dp)) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (section == item.type) MaterialTheme.colorScheme.primaryContainer else Color.White,
                                labelColor = if (section == item.type) SimingCinnabar else MaterialTheme.colorScheme.onSurface,
                            ),
                            border = AssistChipDefaults.assistChipBorder(
                                enabled = true,
                                borderColor = if (section == item.type) SimingCinnabar else MaterialTheme.colorScheme.outlineVariant,
                            ),
                        )
                    }
                }
        ''')
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    nav_old,
    '            ProjectSectionNavigation(selected = section, onSelected = { section = it })\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '                "outline" -> OutlineTreeList(\n',
    '                "tools" -> ProjectToolsPanel(\n'
    '                    project = project,\n'
    '                    online = connection != null,\n'
    '                    ui = ui,\n'
    '                    viewModel = viewModel,\n'
    '                    onExportReady = onSaveExport,\n'
    '                )\n'
    '                "outline" -> OutlineTreeList(\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '    if (showChapterOrder) {\n',
    dedent(
        r'''
            if (ui.pendingCatalogingProjectId == project.projectId) {
                AlertDialog(
                    onDismissRequest = viewModel::dismissImportCatalogingPrompt,
                    title = { Text("导入完成 · ${ui.importedChapterCount} 章") },
                    text = {
                        Text(
                            if (connection != null) {
                                "正文已经导入作品库。现在可以启动与 PC 相同的作品建档流程，让司命从现有章节整理摘要、角色变化和世界观资料。"
                            } else {
                                "正文已经保存在手机。完整作品建档需要连接 PC Gateway；你可以先阅读、编辑或导出 TXT，连接后再到“管理 → 工具”启动建档。"
                            },
                        )
                    },
                    confirmButton = {
                        TextButton(
                            onClick = {
                                if (connection != null) viewModel.startCataloging(project.projectId)
                                section = "tools"
                                viewModel.dismissImportCatalogingPrompt()
                            },
                        ) {
                            Text(if (connection != null) "开始建档" else "打开作品工具")
                        }
                    },
                    dismissButton = {
                        TextButton(onClick = viewModel::dismissImportCatalogingPrompt) { Text("稍后") }
                    },
                )
            }

        '''
    ) + '    if (showChapterOrder) {\n',
)

# Refine library copy to make import a first-class action.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '                    detail = "创建新小说，或导入已有正文继续二创；资料先落手机，联网后按修订号同步。",\n',
    '                    detail = "从零立项、导入现有小说、继续写作都从这里开始；导入后可直接进入作品建档与导出。",\n',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '                        Text("快速建档")\n',
    '                        Text("空白作品")\n',
)

# Parity contract: add PC path coverage + explicit cataloging/export capabilities.
contract_path = ROOT / "contracts/mobile-pc-parity.json"
contract = json.loads(contract_path.read_text(encoding="utf-8"))
for target in contract["coverage_targets"]:
    if target.get("id") == "pc_api_paths":
        target["symbols"].update({
            "catalogingStart": "chapter.cataloging",
            "catalogingJob": "chapter.cataloging",
            "catalogingStream": "chapter.cataloging",
            "catalogingCancel": "chapter.cataloging",
            "projectExport": "authoring.export",
            "projectExportDownload": "authoring.export",
        })

def add_capability(capability: dict) -> None:
    if any(item["id"] == capability["id"] for item in contract["capabilities"]):
        raise SystemExit(f"capability already exists: {capability['id']}")
    contract["capabilities"].append(capability)

add_capability({
    "id": "chapter.cataloging",
    "area": "chapter",
    "summary": "导入或既有章节的作品建档任务",
    "status": "partial",
    "authority": {
        "type": "pc_http",
        "entrypoint": "/api/v1/projects/{project_id}/cataloging",
        "source_refs": [{"path": "backend/app/routers/cataloging.py", "contains": "start_cataloging"}],
    },
    "modes": {
        "pc": {"support": "canonical"},
        "android_online": {
            "support": "canonical_proxy",
            "source_refs": [
                {"path": "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt", "contains": "suspend fun startCataloging("},
                {"path": "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt", "contains": "suspend fun streamCataloging("},
            ],
        },
        "android_offline": {"support": "blocked", "reason": "完整建档会同时更新摘要、角色、世界观和治理资料，离线时不复制第二套权威实现。"},
        "android_standalone": {"support": "blocked", "reason": "手机独立 Agent 暂不伪装成 PC Cataloging；连接 Gateway 后运行权威建档。"},
    },
    "side_effects": ["cataloging"],
    "idempotency": {"required": true, "strategy": "client_serialization"},
    "tests": [{"path": "mobile/android/app/src/test/java/com/siming/mobile/data/network/ProjectToolPathsTest.kt", "contains": "cataloging and export use canonical PC routes"}],
    "known_gaps": ["手机独立模型尚未实现与 PC 完全一致的批量 Cataloging 运行时。"],
})
add_capability({
    "id": "authoring.export",
    "area": "authoring",
    "summary": "小说 TXT / Word / PDF 导出与本机保存",
    "status": "aligned",
    "authority": {
        "type": "pc_http",
        "entrypoint": "/api/v1/projects/{project_id}/export",
        "source_refs": [{"path": "backend/app/routers/export.py", "contains": "def export_project("}],
    },
    "modes": {
        "pc": {"support": "canonical"},
        "android_online": {
            "support": "canonical_proxy",
            "source_refs": [{"path": "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt", "contains": "suspend fun createProjectExport("}],
        },
        "android_offline": {
            "support": "degraded",
            "source_refs": [{"path": "mobile/android/app/src/main/java/com/siming/mobile/data/MobileProjectTools.kt", "contains": "buildLocalNovelExport"}],
            "limitations": "离线只导出本机章节 TXT；Word/PDF 需要 PC 的正式导出服务。",
        },
        "android_standalone": {
            "support": "degraded",
            "source_refs": [{"path": "mobile/android/app/src/main/java/com/siming/mobile/data/MobileProjectTools.kt", "contains": "buildLocalNovelExport"}],
            "limitations": "手机独立模式可导出 TXT，Word/PDF 连接 PC 后生成。",
        },
    },
    "side_effects": [],
    "idempotency": {"required": false, "strategy": "not_applicable"},
    "tests": [
        {"path": "mobile/android/app/src/test/java/com/siming/mobile/data/MobileProjectToolsTest.kt", "contains": "local txt export keeps chapter order and content"},
        {"path": "mobile/android/app/src/test/java/com/siming/mobile/data/network/ProjectToolPathsTest.kt", "contains": "cataloging and export use canonical PC routes"},
    ],
})
contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("Android mobile import/export/UI phase 2 patch applied")
