package com.siming.mobile.ui

import android.os.Build
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.FileOpen
import androidx.compose.material.icons.outlined.Fingerprint
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.automirrored.outlined.LibraryBooks
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.QrCodeScanner
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Save
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.AssistantModelRoute
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.DirectApiSummary
import com.siming.mobile.data.network.PcAuthoringContract
import com.siming.mobile.data.network.PcFieldKind
import com.siming.mobile.BuildConfig

private enum class RootTab(val label: String, val icon: ImageVector) {
    Create("AI 立项", Icons.Outlined.AutoAwesome),
    Library("作品", Icons.AutoMirrored.Outlined.LibraryBooks),
    Sync("同步", Icons.Outlined.Sync),
    Settings("设置", Icons.Outlined.Settings),
}

private data class EditorTarget(val entityType: String, val record: ReplicaEntity?)

private data class EntitySection(
    val type: String,
    val label: String,
    val icon: ImageVector,
    val emptyText: String,
)

private val entitySections = listOf(
    EntitySection("chapter", "正文", Icons.AutoMirrored.Outlined.MenuBook, "还没有章节，可以新建正文"),
    EntitySection("outline", "大纲", Icons.Outlined.MoreHoriz, "还没有大纲节点"),
    EntitySection("character", "角色", Icons.Outlined.Person, "还没有角色资料"),
    EntitySection("world", "世界", Icons.Outlined.Hub, "还没有世界观设定"),
    EntitySection("foreshadowing", "伏笔", Icons.Outlined.Link, "还没有伏笔记录"),
    EntitySection("governance", "治理", Icons.Outlined.WarningAmber, "还没有叙事承诺或治理记录"),
)

@Composable
fun SimingApp(
    viewModel: MainViewModel,
    onScanQr: () -> Unit,
    onPickText: (((String, String) -> Unit) -> Unit),
) {
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val projects by viewModel.projects.collectAsStateWithLifecycle()
    val creationDrafts by viewModel.creationDrafts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    val snackbar = remember { SnackbarHostState() }
    var rootTab by rememberSaveable { mutableStateOf(RootTab.Create) }
    var selectedProjectId by rememberSaveable { mutableStateOf<String?>(null) }
    var showDirectApiSetup by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(ui.notice, ui.error) {
        val message = ui.error ?: ui.notice ?: return@LaunchedEffect
        snackbar.showSnackbar(message)
        viewModel.clearNotice()
    }

    if (showDirectApiSetup) {
        DirectApiSetupScreen(
            viewModel = viewModel,
            existing = ui.directApi,
            onBack = { showDirectApiSetup = false },
            onConfigured = { showDirectApiSetup = false },
            snackbar = snackbar,
        )
        return
    }

    val pairingRequired = connection == null && projects.isEmpty() && creationDrafts.isEmpty() && ui.directApi == null
    if (pairingRequired || ui.pairing != null) {
        PairingScreen(
            viewModel = viewModel,
            allowBack = !pairingRequired,
            onBack = viewModel::cancelPairing,
            onScanQr = onScanQr,
            onConfigureApi = { showDirectApiSetup = true },
            snackbar = snackbar,
        )
        return
    }

    val selectedProject = projects.firstOrNull { it.projectId == selectedProjectId }
    if (selectedProject != null) {
        ProjectScreen(
            viewModel = viewModel,
            project = selectedProject,
            onBack = { selectedProjectId = null },
            snackbar = snackbar,
        )
        return
    }

    Scaffold(
        containerColor = SimingPaper,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                SimingTopBar(connection, ui.directApi)
                if (ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        },
        bottomBar = {
            NavigationBar(
                containerColor = SimingPaperWarm,
                tonalElevation = 0.dp,
                modifier = Modifier.navigationBarsPadding(),
            ) {
                RootTab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = rootTab == tab,
                        onClick = { rootTab = tab },
                        icon = { Icon(tab.icon, contentDescription = null) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        when (rootTab) {
            RootTab.Create -> CreationScreen(
                modifier = Modifier.padding(padding),
                viewModel = viewModel,
                connection = connection,
                directApi = ui.directApi,
                onConfigureApi = { showDirectApiSetup = true },
                onOpenProject = { projectId ->
                    rootTab = RootTab.Library
                    selectedProjectId = projectId
                },
            )
            RootTab.Library -> LibraryScreen(
                modifier = Modifier.padding(padding),
                projects = projects,
                connection = connection,
                directApi = ui.directApi,
                viewModel = viewModel,
                onOpenProject = { selectedProjectId = it },
                onScanQr = onScanQr,
                onPickText = onPickText,
                onStartAiCreation = { rootTab = RootTab.Create },
            )
            RootTab.Sync -> SyncScreen(
                modifier = Modifier.padding(padding),
                viewModel = viewModel,
                connection = connection,
                onScanQr = onScanQr,
            )
            RootTab.Settings -> AboutScreen(
                modifier = Modifier.padding(padding),
                connection = connection,
                directApi = ui.directApi,
                viewModel = viewModel,
                onConfigureApi = { showDirectApiSetup = true },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SimingTopBar(connection: GatewayConnection?, directApi: DirectApiSummary?) {
    CenterAlignedTopAppBar(
        title = {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("司命", fontWeight = FontWeight.SemiBold, letterSpacing = 2.sp)
                Text(
                    when {
                        connection != null -> "自己的 Gateway · 跨设备创作"
                        directApi != null -> "手机独立 · ${directApi.model}"
                        else -> "离线创作"
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        navigationIcon = {
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = CircleShape,
                modifier = Modifier.padding(start = 12.dp).size(34.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text("命", color = SimingCinnabar, fontWeight = FontWeight.Bold)
                }
            }
        },
        actions = {
            Icon(
                when {
                    connection != null -> Icons.Outlined.CloudQueue
                    directApi != null -> Icons.Outlined.AutoAwesome
                    else -> Icons.Outlined.CloudOff
                },
                contentDescription = when {
                    connection != null -> "已连接 Gateway"
                    directApi != null -> "手机独立 API 可用"
                    else -> "未连接 Gateway"
                },
                tint = if (connection != null || directApi != null) SimingGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(end = 16.dp),
            )
        },
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LibraryScreen(
    modifier: Modifier,
    projects: List<ReplicaEntity>,
    connection: GatewayConnection?,
    directApi: DirectApiSummary?,
    viewModel: MainViewModel,
    onOpenProject: (String) -> Unit,
    onScanQr: () -> Unit,
    onPickText: (((String, String) -> Unit) -> Unit),
    onStartAiCreation: () -> Unit,
) {
    var showCreate by rememberSaveable { mutableStateOf(false) }
    Column(modifier.fillMaxSize()) {
        if (connection == null) {
            StatusBanner(
                icon = if (directApi == null) Icons.Outlined.CloudOff else Icons.Outlined.PhoneAndroid,
                title = if (directApi == null) "当前离线，仍可继续写作" else "手机独立模式",
                detail = if (directApi == null) {
                    "修改已保存到手机；配置 API 后无需电脑也能使用 AI。"
                } else {
                    "${directApi.displayName} · ${directApi.model} 可直接使用；作品保存在本机。"
                },
                action = "连接",
                onAction = onScanQr,
                warning = directApi == null,
            )
        }
        LazyColumn(
            contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 96.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            item {
                ScreenHeading(
                    kicker = "LOCAL-FIRST LIBRARY",
                    title = "作品库",
                    detail = "创建新小说，或导入已有正文继续二创；资料先落手机，联网后按修订号同步。",
                )
            }
            item {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onStartAiCreation) {
                        Icon(Icons.Outlined.AutoAwesome, null)
                        Spacer(Modifier.width(7.dp))
                        Text("AI 立项")
                    }
                    OutlinedButton(onClick = { showCreate = true }) {
                        Icon(Icons.Outlined.Add, null)
                        Spacer(Modifier.width(7.dp))
                        Text("快速建档")
                    }
                    OutlinedButton(
                        onClick = {
                            onPickText { name, text ->
                                viewModel.importNovel(name, text, onOpenProject)
                            }
                        },
                    ) {
                        Icon(Icons.Outlined.FileOpen, null)
                        Spacer(Modifier.width(7.dp))
                        Text("导入已有小说")
                    }
                }
            }
            if (projects.isEmpty()) {
                item {
                    EmptyPanel(
                        icon = Icons.AutoMirrored.Outlined.LibraryBooks,
                        title = "这里还没有作品",
                        detail = "可以从零立项，也可以导入 TXT；司命是开源免费的，不需要把正文交给官方服务器。",
                    )
                }
            } else {
                items(projects, key = { it.key }) { project ->
                    ProjectCard(
                        project,
                        localOnly = connection == null,
                        onClick = { onOpenProject(project.projectId) },
                    )
                }
            }
        }
    }
    if (showCreate) {
        CreateProjectDialog(
            onDismiss = { showCreate = false },
            onCreate = { title, description ->
                showCreate = false
                viewModel.createProject(title, description, onOpenProject)
            },
        )
    }
}

@Composable
private fun ProjectCard(project: ReplicaEntity, localOnly: Boolean, onClick: () -> Unit) {
    val title = project.text("title").ifBlank { "未命名作品" }
    val description = project.text("description")
    OutlinedCard(
        onClick = onClick,
        border = BorderStroke(1.dp, if (project.conflicted) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.outlineVariant),
        colors = CardDefaults.outlinedCardColors(containerColor = Color.White),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(15.dp),
        ) {
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.size(52.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(
                        title.take(1),
                        color = SimingCinnabar,
                        fontWeight = FontWeight.Bold,
                        fontSize = 21.sp,
                    )
                }
            }
            Spacer(Modifier.width(13.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (description.isNotBlank()) {
                    Text(
                        description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (project.dirty) MicroTag(if (localOnly) "仅本机" else "待同步", SimingBlue)
                    if (project.conflicted) MicroTag("有分岔", MaterialTheme.colorScheme.error)
                    if (!project.dirty && !project.conflicted) MicroTag("已落库", SimingGreen)
                }
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(18.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProjectScreen(
    viewModel: MainViewModel,
    project: ReplicaEntity,
    onBack: () -> Unit,
    snackbar: SnackbarHostState,
) {
    var section by rememberSaveable(project.projectId) { mutableStateOf("assistant") }
    var editor by remember { mutableStateOf<EditorTarget?>(null) }
    var advanced by remember { mutableStateOf<EditorTarget?>(null) }
    var showChapterOrder by remember { mutableStateOf(false) }
    val currentSection = entitySections.firstOrNull { it.type == section }
    val records by viewModel.entities(project.projectId, section).collectAsStateWithLifecycle(initialValue = emptyList())
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val ui by viewModel.uiState

    if (editor != null) {
        RecordEditorScreen(
            projectId = project.projectId,
            target = requireNotNull(editor),
            viewModel = viewModel,
            onBack = { editor = null },
        )
        return
    }

    Scaffold(
        containerColor = SimingPaper,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                CenterAlignedTopAppBar(
                    title = {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(project.text("title").ifBlank { "未命名作品" }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                if (connection != null) "PC API 一致模式" else "离线资料工作台",
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                    },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回作品库")
                        }
                    },
                    actions = {
                        IconButton(onClick = { editor = EditorTarget("project", project) }) {
                            Icon(Icons.Outlined.Edit, "编辑作品资料")
                        }
                    },
                )
                if (ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        },
        floatingActionButton = {
            if (section != "assistant") {
                FloatingActionButton(onClick = { editor = EditorTarget(section, null) }) {
                    Icon(Icons.Outlined.Add, "新建${requireNotNull(currentSection).label}")
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
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
            if (section == "assistant") {
                AssistantScreen(project.projectId, viewModel)
            } else {
                RecordList(
                    section = requireNotNull(currentSection),
                    records = records,
                    online = connection != null,
                    onOpen = { editor = EditorTarget(section, it) },
                    onAdvanced = if (section in setOf("chapter", "character", "world")) {
                        { record -> advanced = EditorTarget(section, record) }
                    } else {
                        null
                    },
                    onManageChapterOrder = if (section == "chapter") {
                        { showChapterOrder = true }
                    } else {
                        null
                    },
                )
            }
        }
    }

    if (showChapterOrder) {
        ChapterOrderDialog(
            projectId = project.projectId,
            chapters = records,
            online = connection != null,
            viewModel = viewModel,
            onDismiss = { showChapterOrder = false },
        )
    }
    advanced?.let { target ->
        val record = target.record
        if (record != null) {
            when (target.entityType) {
                "chapter" -> ChapterHistoryDialog(
                    projectId = project.projectId,
                    chapter = record,
                    online = connection != null,
                    viewModel = viewModel,
                    onDismiss = { advanced = null },
                )
                "character" -> CharacterAdvancedDialog(
                    projectId = project.projectId,
                    character = record,
                    online = connection != null,
                    viewModel = viewModel,
                    onDismiss = { advanced = null },
                )
                "world" -> WorldAdvancedDialog(
                    projectId = project.projectId,
                    entry = record,
                    online = connection != null,
                    viewModel = viewModel,
                    onDismiss = { advanced = null },
                )
            }
        }
    }
}

@Composable
private fun RecordList(
    section: EntitySection,
    records: List<ReplicaEntity>,
    online: Boolean,
    onOpen: (ReplicaEntity) -> Unit,
    onAdvanced: ((ReplicaEntity) -> Unit)?,
    onManageChapterOrder: (() -> Unit)?,
) {
    LazyColumn(
        contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 96.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ScreenHeading(
                    kicker = section.type.uppercase(),
                    title = section.label,
                    detail = when (section.type) {
                    "chapter" -> if (online) {
                        "在线保存调用 PC 端同一章节 API，快照、目录与校验逻辑完全复用。"
                    } else {
                        "当前离线；正文先保存在手机，恢复连接后进入可靠同步队列。"
                    }
                    "character" -> "字段直接对应 PC 角色卡：别名、外貌、能力、位置、境界、身心状态、目标与冲突共享同一份数据。"
                    "world" -> "规则与设定作为独立实体维护，避免二创时漂移。"
                    else -> if (online) {
                        "在线修改调用 PC 端规范 API，同时维护手机离线副本。"
                    } else {
                        "这里的修改支持离线保存与版本分岔保护。"
                    }
                    },
                )
                if (onManageChapterOrder != null) {
                    OutlinedButton(
                        onClick = onManageChapterOrder,
                        enabled = online && records.size > 1,
                    ) {
                        Text(if (online) "管理章节顺序" else "章节排序需要 PC Gateway")
                    }
                }
            }
        }
        if (records.isEmpty()) {
            item { EmptyPanel(section.icon, section.emptyText, "点击右下角“＋”开始。") }
        } else {
            items(records, key = { it.key }) { record ->
                RecordCard(
                    section.type,
                    record,
                    onClick = { onOpen(record) },
                    onAdvanced = onAdvanced?.let { callback -> { callback(record) } },
                    advancedEnabled = online,
                )
            }
        }
    }
}

@Composable
private fun RecordCard(
    entityType: String,
    record: ReplicaEntity,
    onClick: () -> Unit,
    onAdvanced: (() -> Unit)? = null,
    advancedEnabled: Boolean = false,
) {
    val titleKey = if (entityType == "character") "name" else "title"
    val summaryKey = when (entityType) {
        "chapter" -> "content"
        "outline" -> "summary"
        "character" -> "current_goal"
        "world" -> "content"
        else -> "description"
    }
    OutlinedCard(
        onClick = onClick,
        colors = CardDefaults.outlinedCardColors(containerColor = Color.White),
        border = BorderStroke(
            1.dp,
            when {
                record.conflicted -> MaterialTheme.colorScheme.error
                record.dirty -> MaterialTheme.colorScheme.secondary
                else -> MaterialTheme.colorScheme.outlineVariant
            },
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    record.text(titleKey).ifBlank { "未命名${entitySections.firstOrNull { it.type == entityType }?.label.orEmpty()}" },
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (record.conflicted) Icon(Icons.Outlined.ErrorOutline, "有版本分岔", tint = MaterialTheme.colorScheme.error)
                else if (record.dirty) Icon(Icons.Outlined.CloudQueue, "等待同步", tint = SimingBlue)
                else Icon(Icons.Outlined.CheckCircle, "已同步", tint = SimingGreen)
            }
            val summary = if (entityType == "character") canonicalCharacterSummary(record) else record.text(summaryKey)
            if (summary.isNotBlank()) {
                Text(
                    summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                "修订 ${record.revision} · ${if (record.dirty) "本机有新修改" else "已写入离线库"}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (onAdvanced != null) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                ) {
                    TextButton(
                        onClick = onAdvanced,
                        enabled = advancedEnabled,
                    ) {
                        Text(
                            when (entityType) {
                                "chapter" -> if (advancedEnabled) "版本历史" else "版本需连接 PC"
                                "character" -> if (advancedEnabled) "关系 / AI / 版本" else "高级资料需连接 PC"
                                "world" -> if (advancedEnabled) "版本 / 时间线" else "历史需连接 PC"
                                else -> "高级资料"
                            },
                        )
                    }
                }
            }
        }
    }
}

private data class FormField(
    val key: String,
    val label: String,
    val placeholder: String = "",
    val kind: PcFieldKind,
) {
    val multiline: Boolean
        get() = kind in setOf(
            PcFieldKind.Multiline,
            PcFieldKind.StringArray,
            PcFieldKind.JsonObject,
            PcFieldKind.JsonArray,
        )
}

private fun fieldsFor(type: String): List<FormField> =
    PcAuthoringContract.mobileFields(type).map { spec ->
        FormField(
            key = spec.key,
            label = fieldLabel(type, spec.key),
            placeholder = fieldPlaceholder(type, spec.key),
            kind = spec.kind,
        )
    }

private fun fieldLabel(type: String, key: String): String = when (type) {
    "project" -> when (key) {
        "title" -> "作品名"
        "description" -> "作品简介"
        "tags" -> "标签"
        "narrative_perspective" -> "叙事视角"
        "writing_style" -> "写作文风"
        "forbidden_sentence_patterns" -> "禁用句式"
        "rhetoric_guidelines" -> "修辞规则"
        "short_sentences" -> "短句模式"
        "custom_style_prompt" -> "自定义文风约束"
        "daily_word_goal" -> "每日字数目标"
        else -> key
    }
    "chapter" -> when (key) {
        "title" -> "章节名"
        "outline_node_id" -> "关联大纲节点 ID"
        "content" -> "正文"
        else -> key
    }
    "outline" -> when (key) {
        "title" -> "节点标题"
        "node_type" -> "节点类型"
        "parent_id" -> "父节点 ID"
        "summary" -> "计划内容"
        "status" -> "状态"
        "sort_order" -> "同级顺序"
        "characters" -> "角色与场景职责"
        "metadata" -> "大纲元数据"
        else -> key
    }
    "character" -> when (key) {
        "name" -> "角色名"
        "aliases" -> "别名"
        "role_type" -> "角色定位"
        "age" -> "年龄"
        "appearance" -> "外貌"
        "personality" -> "性格"
        "background" -> "背景"
        "abilities" -> "能力"
        "life_status" -> "生命状态"
        "current_location" -> "当前位置"
        "realm_or_level" -> "境界 / 等级"
        "physical_state" -> "身体状态"
        "mental_state" -> "心理状态"
        "current_goal" -> "当前目标"
        "active_conflict" -> "当前冲突"
        "abilities_state" -> "能力状态"
        "items_or_assets" -> "持有物 / 资产"
        "profile" -> "稳定写作锁"
        "is_evolution_tracked" -> "持续追踪角色变化"
        "change_summary" -> "本次变更摘要"
        else -> key
    }
    "world" -> when (key) {
        "title" -> "设定标题"
        "dimension" -> "维度"
        "content" -> "规则与内容"
        "sort_order" -> "顺序"
        else -> key
    }
    "foreshadowing" -> when (key) {
        "title" -> "伏笔标题"
        "description" -> "埋设与回收计划"
        "status" -> "生命周期状态"
        "importance" -> "重要度"
        "storyline" -> "故事线"
        "source_chapter_id" -> "来源章节 ID"
        "target_chapter_id" -> "计划处理章节 ID"
        "target_chapter_number" -> "计划处理章节号"
        "resolved_chapter_id" -> "实际解决章节 ID"
        "evidence" -> "发现证据"
        "resolution_note" -> "解决说明"
        "resolution_evidence" -> "解决证据"
        "verification_note" -> "复检结论"
        "closed_by" -> "关闭者"
        else -> key
    }
    "governance" -> when (key) {
        "title" -> "叙事债务标题"
        "debt_type" -> "债务类型"
        "description" -> "读者期待与兑现条件"
        "status" -> "生命周期状态"
        "priority" -> "优先级"
        "source_chapter_id" -> "来源章节 ID"
        "target_chapter_id" -> "计划处理章节 ID"
        "target_chapter_number" -> "计划处理章节号"
        "resolved_chapter_id" -> "实际解决章节 ID"
        "linked_foreshadowing_id" -> "关联伏笔 ID"
        "linked_causal_edge_id" -> "关联因果项 ID"
        "evidence" -> "发现证据"
        "resolution_note" -> "解决说明"
        "resolution_evidence" -> "解决证据"
        "verification_note" -> "复检结论"
        "closed_by" -> "关闭者"
        else -> key
    }
    else -> key
}

private fun fieldPlaceholder(type: String, key: String): String = when (type to key) {
    "project" to "tags" -> "一行一个；保存为 PC tags 数组"
    "project" to "narrative_perspective" -> "third_person / first_person"
    "project" to "writing_style" -> "与 PC 项目设置一致"
    "project" to "short_sentences" -> "true / false"
    "chapter" to "outline_node_id" -> "留空表示不关联大纲节点"
    "outline" to "node_type" -> "volume / chapter / section"
    "outline" to "parent_id" -> "留空表示根节点"
    "outline" to "status" -> "pending / in_progress / completed"
    "outline" to "characters" -> "JSON 数组，例如 [{\"character_id\":\"...\",\"role_in_scene\":\"protagonist\"}]"
    "outline" to "metadata" -> "JSON 对象，例如 {\"hook\":\"章末钩子\"}"
    "character" to "aliases" -> "一行一个"
    "character" to "role_type" -> "protagonist / supporting / antagonist / mentor / other"
    "character" to "abilities" -> "一行一个"
    "character" to "life_status" -> "active / deceased / unknown"
    "character" to "profile" -> "JSON 对象；与 PC 稳定写作锁完全同步"
    "character" to "is_evolution_tracked" -> "true / false"
    "world" to "dimension" -> "geography / history / factions / power_system / races / culture"
    "foreshadowing" to "status", "governance" to "status" -> "open / pending_review / deferred / fulfilled / abandoned"
    "foreshadowing" to "importance" -> "low / medium / high / critical"
    "governance" to "priority" -> "low / medium / high / critical"
    "governance" to "debt_type" -> "promise / setup / obligation / question"
    else -> ""
}

private fun requiredIdentityField(type: String): String = if (type == "character") "name" else "title"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RecordEditorScreen(
    projectId: String,
    target: EditorTarget,
    viewModel: MainViewModel,
    onBack: () -> Unit,
) {
    val fields = remember(target.entityType) { fieldsFor(target.entityType) }
    val values = remember(target.record?.key, target.entityType) {
        mutableStateMapOf<String, String>().apply {
            fields.forEach { field -> put(field.key, target.record?.formText(field.key).orEmpty()) }
            fun setDefault(key: String, value: String) {
                if (this[key].isNullOrBlank()) this[key] = value
            }
            when (target.entityType) {
                "project" -> {
                    setDefault("narrative_perspective", "third_person")
                    setDefault("writing_style", "natural")
                    setDefault("short_sentences", "false")
                    setDefault("daily_word_goal", "6000")
                }
                "outline" -> {
                    setDefault("node_type", "chapter")
                    setDefault("status", "pending")
                    setDefault("sort_order", "0")
                    setDefault("characters", "[]")
                    setDefault("metadata", "{}")
                }
                "world" -> {
                    setDefault("dimension", "culture")
                    setDefault("sort_order", "0")
                }
                "character" -> {
                    setDefault("role_type", "supporting")
                    setDefault("life_status", "active")
                    setDefault("profile", "{}")
                    setDefault("is_evolution_tracked", "true")
                }
                "foreshadowing" -> {
                    setDefault("status", "open")
                    setDefault("importance", "medium")
                }
                "governance" -> {
                    setDefault("status", "open")
                    setDefault("priority", "medium")
                    setDefault("debt_type", "promise")
                }
            }
        }
    }
    var showDelete by remember { mutableStateOf(false) }
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val title = if (target.record == null) "新建${entityLabel(target.entityType)}" else "编辑${entityLabel(target.entityType)}"
    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text(title) },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                actions = {
                    if (
                        target.record != null &&
                        target.entityType !in setOf("project", "foreshadowing", "governance")
                    ) {
                        IconButton(onClick = { showDelete = true }) {
                            Icon(Icons.Outlined.DeleteOutline, "删除", tint = MaterialTheme.colorScheme.error)
                        }
                    }
                },
            )
        },
        bottomBar = {
            Surface(tonalElevation = 3.dp, color = SimingPaperWarm) {
                Button(
                    onClick = {
                        val mapped = canonicalFormValues(target.entityType, values)
                        viewModel.saveRecord(
                            projectId,
                            target.entityType,
                            target.record?.entityId ?: if (target.entityType == "project") projectId else null,
                            mapped,
                            target.record?.payload(),
                            onBack,
                        )
                    },
                    enabled = values[requiredIdentityField(target.entityType)].orEmpty().isNotBlank(),
                    modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(14.dp),
                ) {
                    Icon(Icons.Outlined.Save, null)
                    Spacer(Modifier.width(8.dp))
                    Text("保存")
                }
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(13.dp),
        ) {
            if (target.entityType == "character") {
                StatusBanner(
                    Icons.Outlined.Person,
                    "先写清动机，再让 AI 接着写",
                    "当前表单直接编辑 PC Character 字段；能力/别名保持数组结构，profile 保持 JSON 对象结构，与 PC Character 契约一致。",
                )
            }
            fields.forEach { field ->
                OutlinedTextField(
                    value = values[field.key].orEmpty(),
                    onValueChange = { values[field.key] = it },
                    label = { Text(field.label) },
                    placeholder = { if (field.placeholder.isNotBlank()) Text(field.placeholder) },
                    minLines = if (field.multiline) if (field.key == "content") 14 else 4 else 1,
                    maxLines = if (field.multiline) Int.MAX_VALUE else 1,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (field.key == "content" && target.entityType == "chapter") {
                    Text(
                        "${values[field.key].orEmpty().count { !it.isWhitespace() }} 字 · 自动保存需点击下方按钮确认",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text(
                if (connection != null) {
                    "在线保存直接调用 PC 端同一路径与业务逻辑，并同步更新手机副本；不会退化为简化版写入。"
                } else {
                    "当前离线，保存会先写入手机数据库；连接 Gateway 后自动同步。"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(24.dp))
        }
    }
    if (showDelete && target.record != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            icon = { Icon(Icons.Outlined.DeleteOutline, null) },
            title = { Text("删除这条${entityLabel(target.entityType)}？") },
            text = { Text("删除会进入同步队列，并在 Gateway 保留 90 天删除标记；不会静默覆盖其他设备的离线修改。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        showDelete = false
                        viewModel.deleteRecord(projectId, target.entityType, target.record.entityId, onBack)
                    },
                ) { Text("确认删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun AssistantScreen(projectId: String, viewModel: MainViewModel) {
    var prompt by rememberSaveable { mutableStateOf("") }
    var scope by rememberSaveable { mutableStateOf("project") }
    val ui by viewModel.uiState
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val directApi = ui.directApi
    var modelRoute by rememberSaveable { mutableStateOf("pc") }
    LaunchedEffect(connection?.deviceId, directApi?.model) {
        modelRoute = when {
            connection == null && directApi != null -> "mobile"
            modelRoute == "mobile" && directApi == null -> "pc"
            else -> modelRoute
        }
    }
    val standaloneMobile = connection == null && directApi != null
    val gatewayMobile = connection != null && directApi != null && modelRoute == "mobile"
    val canUseAi = connection != null || directApi != null
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            ScreenHeading(
                kicker = when {
                    standaloneMobile -> "FULL PC PROMPT CONTRACT · ON DEVICE"
                    gatewayMobile -> "PC WORKFLOW · MOBILE API KEY"
                    else -> "PC WORKFLOW · PC MODEL ROUTE"
                },
                title = "AI 共创工作台",
                detail = when {
                    standaloneMobile ->
                        "手机内置 PC 工作区的完整提示词契约与结构化工具循环，直接调用本机保存的 API Key。"
                    gatewayMobile ->
                        "PC 执行同一工作区助手与落库工具；本轮模型凭据来自手机，并经端到端加密传递。"
                    else ->
                        "请求由自己的 Gateway 执行，使用 PC 已配置的模型、完整提示词与项目工具。"
                },
            )
        }
        if (connection != null && directApi != null) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    Text("本轮模型线路", style = MaterialTheme.typography.labelMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AssistChip(
                            onClick = { modelRoute = "pc" },
                            label = { Text("PC 已配置线路") },
                            leadingIcon = { Icon(Icons.Outlined.Devices, null, Modifier.size(17.dp)) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (modelRoute == "pc") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                        AssistChip(
                            onClick = { modelRoute = "mobile" },
                            label = { Text("手机私有 Key") },
                            leadingIcon = { Icon(Icons.Outlined.Key, null, Modifier.size(17.dp)) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (modelRoute == "mobile") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                    }
                }
            }
        }
        if (!canUseAi) {
            item {
                StatusBanner(
                    Icons.Outlined.CloudOff,
                    "尚未配置 AI",
                    "项目资料仍可离线编辑；请在“设置”中配置手机直连 API，或连接自己的 Gateway。",
                    warning = true,
                )
            }
        } else if (standaloneMobile || gatewayMobile) {
            item {
                StatusBanner(
                    Icons.Outlined.PhoneAndroid,
                    if (standaloneMobile) {
                        "手机独立执行 ${directApi?.model.orEmpty()}"
                    } else {
                        "PC 工作流使用手机模型 ${directApi?.model.orEmpty()}"
                    },
                    if (standaloneMobile) {
                        "使用内置的 PC 同源提示词、结构化动作和手机副本；生成动作会写入本地实体。"
                    } else {
                        "API Key 只在手机持久化；每次请求加密后临时交给自己的 Gateway，任务结束即释放。"
                    },
                )
            }
        }
        item {
            Row(
                modifier = Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf("project" to "全书", "outline" to "大纲", "characters" to "角色", "worldbuilding" to "世界观").forEach { (value, label) ->
                    AssistChip(
                        onClick = { scope = value },
                        label = { Text(label) },
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = if (scope == value) MaterialTheme.colorScheme.primaryContainer else Color.White,
                        ),
                    )
                }
            }
        }
        item {
            OutlinedTextField(
                value = prompt,
                onValueChange = { prompt = it },
                label = { Text("告诉项目助手要做什么") },
                placeholder = { Text("例如：用质量模式续写下一章，保持周遥的求证动机与温室管理规则，并留下章末钩子") },
                minLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item {
            Button(
                onClick = {
                    viewModel.runAssistant(
                        projectId,
                        scope,
                        prompt,
                        if (modelRoute == "mobile") AssistantModelRoute.MobileKey else AssistantModelRoute.Pc,
                    )
                },
                enabled = canUseAi && prompt.isNotBlank() && !ui.assistantRunning,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (ui.assistantRunning) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Icon(Icons.Outlined.AutoAwesome, null)
                Spacer(Modifier.width(8.dp))
                Text(
                    when {
                        ui.assistantRunning -> "正在生成…"
                        standaloneMobile -> "在手机执行完整工作区流程"
                        gatewayMobile -> "用手机 Key 执行 PC 工作流"
                        else -> "交给自己的 Gateway"
                    },
                )
            }
        }
        if (standaloneMobile && ui.assistantOutput.isNotBlank() && !ui.assistantRunning) {
            item {
                OutlinedButton(
                    onClick = { viewModel.saveAssistantAsChapter(projectId) },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Outlined.Save, null)
                    Spacer(Modifier.width(8.dp))
                    Text("保存为本机新章节")
                }
            }
        }
        if (ui.assistantRunning && ui.assistantActivity.isNotBlank()) {
            item {
                Text(
                    ui.assistantActivity,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = Color.White),
                modifier = Modifier.fillMaxWidth().height(240.dp),
            ) {
                SelectionContainer {
                    Text(
                        ui.assistantOutput.ifBlank {
                            if (standaloneMobile) {
                                "同源工作区流程的最终回复会显示在这里；工具执行进度单独显示，不会混入正文。"
                            } else {
                                "AI 最终回复会显示在这里；工具执行进度单独显示。"
                            }
                        },
                        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),
                        color = if (ui.assistantOutput.isBlank()) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}

@Composable
private fun SyncScreen(
    modifier: Modifier,
    viewModel: MainViewModel,
    connection: GatewayConnection?,
    onScanQr: () -> Unit,
) {
    val pending by viewModel.pendingCount.collectAsStateWithLifecycle()
    val cursor by viewModel.cursor.collectAsStateWithLifecycle()
    val conflicts by viewModel.conflicts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    var disconnectDialog by remember { mutableStateOf(false) }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 96.dp),
        verticalArrangement = Arrangement.spacedBy(13.dp),
    ) {
        item {
            ScreenHeading(
                kicker = "REVISIONED SYNC",
                title = "同步中枢",
                detail = "先上传本机队列，再拉取 Gateway 修订；同一资料两边都改动时保留双方。",
            )
        }
        item {
            if (connection == null) {
                EmptyPanel(
                    Icons.Outlined.CloudOff,
                    "当前没有 Gateway 授权",
                    if (ui.directApi == null) {
                        "本机资料仍可编辑；配置直连 API 后可独立使用 AI。"
                    } else {
                        "手机直连 API 不受影响；跨设备同步仍需 Gateway。"
                    },
                )
                Button(onClick = onScanQr, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Outlined.QrCodeScanner, null)
                    Spacer(Modifier.width(8.dp))
                    Text("扫描新的 Gateway")
                }
            } else {
                GatewayConnectionCard(connection)
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(9.dp), modifier = Modifier.fillMaxWidth()) {
                MetricCard("待上传", pending.toString(), "本机修订", Modifier.weight(1f))
                MetricCard("同步游标", (cursor?.cursor ?: 0).toString(), "全局顺序", Modifier.weight(1f))
                MetricCard("分岔", conflicts.size.toString(), "待选择", Modifier.weight(1f), conflicts.isNotEmpty())
            }
        }
        if (cursor?.lastError != null) {
            item { StatusBanner(Icons.Outlined.ErrorOutline, "上次同步没有完成", cursor?.lastError.orEmpty(), warning = true) }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = viewModel::syncNow,
                    enabled = connection != null && !ui.busy,
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(Icons.Outlined.Sync, null)
                    Spacer(Modifier.width(7.dp))
                    Text("立即同步")
                }
                OutlinedButton(
                    onClick = viewModel::bootstrap,
                    enabled = connection != null && !ui.busy,
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(Icons.Outlined.Refresh, null)
                    Spacer(Modifier.width(7.dp))
                    Text("重新校验")
                }
            }
        }
        item {
            Text("版本分岔", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text("双方原始快照会保留在 Gateway；选择只会追加一个新修订。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (conflicts.isEmpty()) {
            item { StatusBanner(Icons.Outlined.CheckCircle, "没有待处理分岔", "所有设备都沿同一条修订线继续。") }
        } else {
            items(conflicts, key = { it.id }) { conflict -> ConflictCard(conflict, viewModel) }
        }
        if (connection != null) {
            item {
                HorizontalDivider(Modifier.padding(vertical = 5.dp))
                OutlinedButton(onClick = { disconnectDialog = true }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Outlined.CloudOff, null)
                    Spacer(Modifier.width(8.dp))
                    Text("断开这台设备")
                }
            }
        }
    }
    if (disconnectDialog) {
        AlertDialog(
            onDismissRequest = { disconnectDialog = false },
            title = { Text("断开 Gateway？") },
            text = { Text("联网时会同时撤销 Gateway 授权。若 Gateway 暂时不可达，本机会先断开并提示你稍后到管理页补撤销。") },
            confirmButton = {
                TextButton(onClick = { disconnectDialog = false; viewModel.disconnect(false) }) { Text("保留离线作品") }
            },
            dismissButton = {
                TextButton(onClick = { disconnectDialog = false; viewModel.disconnect(true) }) {
                    Text("同时清除本机副本", color = MaterialTheme.colorScheme.error)
                }
            },
        )
    }
}

@Composable
private fun GatewayConnectionCard(connection: GatewayConnection) {
    OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = Color.White)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(color = MaterialTheme.colorScheme.tertiaryContainer, shape = CircleShape, modifier = Modifier.size(42.dp)) {
                    Box(contentAlignment = Alignment.Center) { Icon(Icons.Outlined.Devices, null, tint = SimingGreen) }
                }
                Spacer(Modifier.width(11.dp))
                Column(Modifier.weight(1f)) {
                    Text(connection.gatewayName, fontWeight = FontWeight.SemiBold)
                    Text(connection.baseUrl, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                MicroTag("已授权", SimingGreen)
            }
            HorizontalDivider()
            Text("指纹 ${compactFingerprint(connection.gatewayFingerprint)}", fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall)
            Text("角色 ${connection.deviceRole} · 协议 v${connection.protocolVersion}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ConflictCard(conflict: LocalConflict, viewModel: MainViewModel) {
    var expanded by remember { mutableStateOf(false) }
    OutlinedCard(
        colors = CardDefaults.outlinedCardColors(containerColor = Color(0xFFFFFAF0)),
        border = BorderStroke(1.dp, Color(0xFFD8AD6F)),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.WarningAmber, null, tint = Color(0xFFA66A16))
                Spacer(Modifier.width(8.dp))
                Text("${entityLabel(conflict.entityType)}有两个版本", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Text("修订 ${conflict.serverRevision}", style = MaterialTheme.typography.labelSmall)
            }
            TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起双方快照" else "比较双方快照") }
            if (expanded) {
                SnapshotBox("Gateway 当前版本", conflict.serverPayloadJson)
                SnapshotBox("手机离线版本", conflict.clientPayloadJson)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { viewModel.resolveConflict(conflict, "server") }, modifier = Modifier.weight(1f)) { Text("保留 Gateway") }
                Button(onClick = { viewModel.resolveConflict(conflict, "client") }, modifier = Modifier.weight(1f)) { Text("采用手机") }
            }
        }
    }
}

@Composable
private fun SnapshotBox(label: String, raw: String?) {
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
        SelectionContainer {
            Text(
                raw ?: "（删除记录）",
                fontFamily = FontFamily.Monospace,
                fontSize = 11.sp,
                modifier = Modifier.fillMaxWidth().background(Color.White, RoundedCornerShape(6.dp)).padding(9.dp),
                maxLines = 10,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun AboutScreen(
    modifier: Modifier,
    connection: GatewayConnection?,
    directApi: DirectApiSummary?,
    viewModel: MainViewModel,
    onConfigureApi: () -> Unit,
) {
    val uriHandler = LocalUriHandler.current
    var clearApiDialog by remember { mutableStateOf(false) }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 96.dp),
        verticalArrangement = Arrangement.spacedBy(13.dp),
    ) {
        item {
            ScreenHeading(
                kicker = "OPEN SOURCE · FREE",
                title = "设置与数据边界",
                detail = "手机可以直接连接云端 API，也可以连接自己的 Gateway；作品正文始终保存在你的设备。",
            )
        }
        item {
            Text("手机直连 API", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
        item {
            OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = Color.White)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    if (directApi == null) {
                        Text("尚未配置", fontWeight = FontWeight.SemiBold)
                        Text("配置后无需电脑开机即可使用项目助手。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick = onConfigureApi, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Outlined.Key, null)
                            Spacer(Modifier.width(8.dp))
                            Text("配置云端 API")
                        }
                    } else {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.CheckCircle, null, tint = SimingGreen)
                            Spacer(Modifier.width(8.dp))
                            Column(Modifier.weight(1f)) {
                                Text(directApi.displayName, fontWeight = FontWeight.SemiBold)
                                Text(directApi.model, style = MaterialTheme.typography.bodySmall)
                            }
                            MicroTag("可用", SimingGreen)
                        }
                        Text(directApi.baseUrl, maxLines = 2, overflow = TextOverflow.Ellipsis, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = viewModel::testDirectApi, modifier = Modifier.weight(1f)) { Text("重新测试") }
                            OutlinedButton(onClick = onConfigureApi, modifier = Modifier.weight(1f)) { Text("编辑") }
                        }
                        TextButton(onClick = { clearApiDialog = true }, modifier = Modifier.fillMaxWidth()) {
                            Text("清除本机 API 配置", color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = SimingPaperWarm)) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(11.dp)) {
                    AboutRow(Icons.Outlined.Lock, "官方不转接正文", "设备只连接你配置的 API 或 Gateway")
                    AboutRow(Icons.AutoMirrored.Outlined.LibraryBooks, "新作与二创", "从零建书，或导入已有 TXT 继续创作")
                    AboutRow(Icons.Outlined.Person, "连续性资料", "角色目标、冲突和世界规则独立同步，帮助减少 OOC")
                    AboutRow(Icons.Outlined.CloudOff, "离线仍可写", "Room 本地库 + WorkManager 可靠队列")
                }
            }
        }
        item {
            OutlinedButton(
                onClick = { uriHandler.openUri("https://github.com/teangtang1122/siming-ai") },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Outlined.Info, null)
                Spacer(Modifier.width(8.dp))
                Text("查看开源代码与许可证")
            }
        }
        item {
            Text("版本 ${BuildConfig.VERSION_NAME} · 同步协议 v${BuildConfig.SYNC_PROTOCOL_VERSION}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                when {
                    connection != null -> "当前连接：${connection.gatewayName}"
                    directApi != null -> "当前模式：手机独立 API"
                    else -> "当前为纯离线模式"
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    if (clearApiDialog) {
        AlertDialog(
            onDismissRequest = { clearApiDialog = false },
            title = { Text("清除手机直连 API？") },
            text = { Text("将删除 Android Keystore 加密的 API 配置；本机作品和 Gateway 配对不会受影响。") },
            confirmButton = {
                TextButton(onClick = { clearApiDialog = false; viewModel.clearDirectApi() }) {
                    Text("确认清除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { clearApiDialog = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun AboutRow(icon: ImageVector, title: String, detail: String) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(icon, null, tint = SimingCinnabar, modifier = Modifier.size(21.dp))
        Spacer(Modifier.width(11.dp))
        Column {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun PairingScreen(
    viewModel: MainViewModel,
    allowBack: Boolean,
    onBack: () -> Unit,
    onScanQr: () -> Unit,
    onConfigureApi: () -> Unit,
    snackbar: SnackbarHostState,
) {
    val ui by viewModel.uiState
    var deviceName by rememberSaveable { mutableStateOf("${Build.MANUFACTURER} ${Build.MODEL}".trim()) }
    var manual by rememberSaveable { mutableStateOf(false) }
    var raw by rememberSaveable { mutableStateOf("") }
    Scaffold(
        containerColor = SimingPaper,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            if (allowBack) {
                Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") }
                    Text("连接 Gateway", fontWeight = FontWeight.SemiBold)
                }
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = CircleShape,
                modifier = Modifier.size(76.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text("司命", color = SimingCinnabar, fontWeight = FontWeight.Bold, fontSize = 21.sp)
                }
            }
            Spacer(Modifier.height(18.dp))
            Text("让手机独立工作", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            Text(
                "直接配置云端 API 后，无需连接电脑即可让 AI 完成立项采访、结构生成、正式建档和后续共创。需要跨设备同步时，再连接自己的 Gateway。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 8.dp),
            )
            Spacer(Modifier.height(22.dp))
            if (ui.pairing == null) {
                Button(onClick = onConfigureApi, modifier = Modifier.fillMaxWidth().height(50.dp)) {
                    Icon(Icons.Outlined.Key, null)
                    Spacer(Modifier.width(9.dp))
                    Text("配置云端 API，开启 AI 立项")
                }
                Text(
                    "API Key 仅由 Android Keystore 持久化。选择手机 Key + Gateway 时，只发送端到端加密的一次性请求凭据，PC 不保存。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp),
                ) {
                    HorizontalDivider(Modifier.weight(1f))
                    Text("  或连接自己的 Gateway  ", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    HorizontalDivider(Modifier.weight(1f))
                }
                OutlinedButton(onClick = onScanQr, modifier = Modifier.fillMaxWidth().height(50.dp)) {
                    Icon(Icons.Outlined.QrCodeScanner, null)
                    Spacer(Modifier.width(9.dp))
                    Text("扫描电脑上的二维码")
                }
                TextButton(onClick = { manual = !manual }) { Text(if (manual) "收起手动粘贴" else "相机不可用？手动粘贴配对内容") }
                if (manual) {
                    OutlinedTextField(
                        value = raw,
                        onValueChange = { if (it.length <= 16_384) raw = it },
                        label = { Text("配对 JSON") },
                        minLines = 5,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedButton(
                        onClick = { viewModel.acceptPairingQr(raw) },
                        enabled = raw.isNotBlank(),
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    ) { Text("验证签名") }
                }
            } else {
                val pairing = requireNotNull(ui.pairing)
                OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = Color.White)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.Fingerprint, null, tint = SimingGreen)
                            Spacer(Modifier.width(8.dp))
                            Text("Gateway 签名已验证", color = SimingGreen, fontWeight = FontWeight.SemiBold)
                        }
                        Text(pairing.gatewayName, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        Text(pairing.gatewayUrl, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
                        HorizontalDivider()
                        Text("指纹", style = MaterialTheme.typography.labelSmall)
                        SelectionContainer { Text(pairing.gatewayFingerprint.chunked(4).joinToString(" "), fontFamily = FontFamily.Monospace, fontSize = 11.sp) }
                    }
                }
                OutlinedTextField(
                    value = deviceName,
                    onValueChange = { if (it.length <= 120) deviceName = it },
                    label = { Text("这台设备的名称") },
                    modifier = Modifier.fillMaxWidth().padding(top = 13.dp),
                )
                if (!ui.pairingStatus.isNullOrBlank()) {
                    StatusBanner(
                        Icons.Outlined.Devices,
                        ui.pairingStatus.orEmpty(),
                        if (ui.busy) ui.activity else "只在确认地址和指纹属于你时继续。",
                        warning = ui.busy,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
                Button(
                    onClick = { viewModel.connectPairing(deviceName) },
                    enabled = deviceName.isNotBlank() && !ui.busy,
                    modifier = Modifier.fillMaxWidth().height(50.dp).padding(top = 12.dp),
                ) {
                    if (ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Icon(Icons.Outlined.Link, null)
                    Spacer(Modifier.width(8.dp))
                    Text(if (ui.busy) "等待电脑确认…" else "提交配对申请")
                }
                TextButton(onClick = viewModel::cancelPairing, enabled = !ui.busy) { Text("取消并清除二维码") }
            }
            Spacer(Modifier.height(26.dp))
            StatusBanner(
                Icons.Outlined.Info,
                "开源免费，不托管正文",
                "无 Gateway 时由手机内置 PC 同源提示词直接调用 API；配对令牌和 API Key 均由 Android Keystore 加密。",
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun DirectApiSetupScreen(
    viewModel: MainViewModel,
    existing: DirectApiSummary?,
    onBack: () -> Unit,
    onConfigured: () -> Unit,
    snackbar: SnackbarHostState,
) {
    val ui by viewModel.uiState
    var displayName by rememberSaveable(existing?.baseUrl) {
        mutableStateOf(existing?.displayName ?: "自定义 API")
    }
    var baseUrl by rememberSaveable(existing?.baseUrl) {
        mutableStateOf(existing?.baseUrl ?: "https://api.openai.com/v1")
    }
    // Never place credentials in Android's save-instance-state Bundle.
    var apiKey by remember(existing?.baseUrl) { mutableStateOf("") }
    var model by rememberSaveable(existing?.baseUrl) { mutableStateOf(existing?.model.orEmpty()) }
    var protocol by rememberSaveable(existing?.baseUrl) {
        mutableStateOf(existing?.protocol ?: DirectApiConfig.PROTOCOL_AUTO)
    }

    LaunchedEffect(ui.discoveredModels) {
        if (model.isBlank()) model = ui.discoveredModels.firstOrNull().orEmpty()
    }

    Scaffold(
        containerColor = SimingPaper,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                CenterAlignedTopAppBar(
                    title = { Text(if (existing == null) "配置手机直连 API" else "编辑手机直连 API") },
                    navigationIcon = {
                        IconButton(onClick = onBack, enabled = !ui.busy) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回")
                        }
                    },
                )
                if (ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            ScreenHeading(
                kicker = "STANDALONE · OPENAI COMPATIBLE",
                title = "不连接电脑，也能使用 AI",
                detail = "支持 Responses API 与 Chat Completions。先尝试自动获取模型；失败后仍可手动填写。",
            )
            StatusBanner(
                Icons.Outlined.Lock,
                "凭据只保存在这台手机",
                "API Key 使用 Android Keystore 加密，不进入作品数据库、同步队列或日志。直连地址必须使用 HTTPS。",
            )
            OutlinedTextField(
                value = displayName,
                onValueChange = { displayName = it.take(80) },
                label = { Text("服务名称") },
                placeholder = { Text("例如 OpenAI、硅基流动、自建中转") },
                singleLine = true,
                enabled = !ui.busy,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = baseUrl,
                onValueChange = { baseUrl = it.take(2_000) },
                label = { Text("API 请求地址") },
                placeholder = { Text("https://api.example.com/v1") },
                supportingText = { Text("可填写带或不带 /v1 的 OpenAI 兼容根地址") },
                singleLine = true,
                enabled = !ui.busy,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = apiKey,
                onValueChange = { apiKey = it.take(10_000) },
                label = { Text(if (existing == null) "API Key" else "API Key（留空保留原密钥）") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                enabled = !ui.busy,
                modifier = Modifier.fillMaxWidth(),
            )
            Text("API 协议", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    DirectApiConfig.PROTOCOL_AUTO to "自动识别（推荐）",
                    DirectApiConfig.PROTOCOL_RESPONSES to "Responses",
                    DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS to "Chat Completions",
                ).forEach { (value, label) ->
                    AssistChip(
                        onClick = { protocol = value },
                        label = { Text(label) },
                        enabled = !ui.busy,
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = if (protocol == value) MaterialTheme.colorScheme.primaryContainer else Color.White,
                            labelColor = if (protocol == value) SimingCinnabar else MaterialTheme.colorScheme.onSurface,
                        ),
                    )
                }
            }
            OutlinedTextField(
                value = model,
                onValueChange = { model = it.take(300) },
                label = { Text("模型名") },
                placeholder = { Text("例如 gpt-4.1-mini 或服务商模型名") },
                supportingText = { Text("自动获取失败时可直接手动填写") },
                singleLine = true,
                enabled = !ui.busy,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedButton(
                onClick = { viewModel.discoverDirectModels(baseUrl, apiKey) },
                enabled = baseUrl.isNotBlank() && (apiKey.isNotBlank() || existing != null) && !ui.busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Outlined.Refresh, null)
                Spacer(Modifier.width(8.dp))
                Text("自动获取模型")
            }
            if (ui.discoveredModels.isNotEmpty()) {
                Text("选择已发现模型", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    ui.discoveredModels.take(8).forEach { discovered ->
                        AssistChip(
                            onClick = { model = discovered },
                            label = { Text(discovered, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (model == discovered) MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                    }
                }
                if (ui.discoveredModels.size > 8) {
                    Text(
                        "另有 ${ui.discoveredModels.size - 8} 个模型，可继续手动输入精确名称。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Button(
                onClick = {
                    viewModel.configureDirectApi(
                        displayName,
                        baseUrl,
                        apiKey,
                        model,
                        protocol,
                        onConfigured,
                    )
                },
                enabled = baseUrl.isNotBlank() &&
                    (apiKey.isNotBlank() || existing != null) && !ui.busy,
                modifier = Modifier.fillMaxWidth().height(50.dp),
            ) {
                if (ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Icon(Icons.Outlined.CheckCircle, null)
                Spacer(Modifier.width(8.dp))
                Text(
                    if (ui.busy) {
                        ui.activity.ifBlank { "正在测试…" }
                    } else if (model.isBlank()) {
                        "自动获取模型、测试并保存"
                    } else {
                        "真实对话测试并保存"
                    },
                )
            }
            Text(
                "独立模式只提供云端模型能力，不包含桌面端的本地模型、CLI、MCP 或训练运行时。以后仍可选择连接 Gateway 进行跨设备同步。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun CreateProjectDialog(onDismiss: () -> Unit, onCreate: (String, String) -> Unit) {
    var title by rememberSaveable { mutableStateOf("") }
    var description by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Outlined.Add, null) },
        title = { Text("创作新小说") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(title, { title = it.take(200) }, label = { Text("作品名") }, singleLine = true)
                OutlinedTextField(description, { description = it }, label = { Text("一句话创意（可选）") }, minLines = 3)
                Text("作品立即保存在手机；以后连接 Gateway 时再加入跨设备同步。", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = { onCreate(title, description) }, enabled = title.isNotBlank()) { Text("创建") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun ScreenHeading(kicker: String, title: String, detail: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(kicker, color = SimingCinnabar, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EmptyPanel(icon: ImageVector, title: String, detail: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier.fillMaxWidth().height(230.dp).background(Color.White, RoundedCornerShape(10.dp)).padding(24.dp),
    ) {
        Icon(icon, null, tint = SimingCinnabar, modifier = Modifier.size(36.dp))
        Spacer(Modifier.height(10.dp))
        Text(title, fontWeight = FontWeight.SemiBold)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun StatusBanner(
    icon: ImageVector,
    title: String,
    detail: String,
    modifier: Modifier = Modifier,
    action: String? = null,
    onAction: (() -> Unit)? = null,
    warning: Boolean = false,
) {
    val background = if (warning) Color(0xFFFFF7E8) else MaterialTheme.colorScheme.secondaryContainer
    val foreground = if (warning) Color(0xFF704409) else MaterialTheme.colorScheme.onSecondaryContainer
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier.fillMaxWidth().background(background).padding(13.dp),
    ) {
        Icon(icon, null, tint = foreground)
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = foreground, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            Text(detail, color = foreground.copy(alpha = 0.84f), style = MaterialTheme.typography.bodySmall)
        }
        if (action != null && onAction != null) TextButton(onClick = onAction) { Text(action) }
    }
}

@Composable
private fun MetricCard(label: String, value: String, detail: String, modifier: Modifier, warning: Boolean = false) {
    Card(
        colors = CardDefaults.cardColors(containerColor = if (warning) Color(0xFFFFF7E8) else Color.White),
        modifier = modifier,
    ) {
        Column(Modifier.padding(11.dp)) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontFamily = FontFamily.Monospace, fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = if (warning) Color(0xFFA66A16) else SimingInk)
            Text(detail, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun MicroTag(text: String, color: Color) {
    Text(
        text,
        color = color,
        fontSize = 10.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.background(color.copy(alpha = 0.09f), RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

private fun entityLabel(type: String): String = when (type) {
    "project" -> "作品资料"
    "chapter" -> "章节"
    "outline" -> "大纲"
    "character" -> "角色"
    "world" -> "世界观"
    "foreshadowing" -> "伏笔"
    "governance" -> "叙事治理"
    else -> "资料"
}

private fun compactFingerprint(value: String): String =
    if (value.length <= 20) value else "${value.take(10)}…${value.takeLast(8)}"
