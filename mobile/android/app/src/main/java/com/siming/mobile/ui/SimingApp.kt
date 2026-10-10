package com.siming.mobile.ui

import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.TopAppBar
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.consumeWindowInsets
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
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
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
import com.siming.mobile.data.local.ProjectSyncRecord
import com.siming.mobile.data.local.ProjectSyncStatus
import com.siming.mobile.data.local.syncStatus
import com.siming.mobile.data.AssistantModelRoute
import com.siming.mobile.data.MobileExportFile
import com.siming.mobile.data.MobileNovelImportFile
import com.siming.mobile.data.MobileProjectPackageFile
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.DirectApiSummary
import com.siming.mobile.data.network.MobileKnownModelCapacityCatalog
import com.siming.mobile.data.network.PcAuthoringContract
import com.siming.mobile.data.network.PcFieldKind
import com.siming.mobile.R

private enum class RootTab(val label: String, val icon: ImageVector) {
    Library("书架", Icons.AutoMirrored.Outlined.LibraryBooks),
    Create("立项", Icons.Outlined.AutoAwesome),
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
    EntitySection("tools", "工具", Icons.Outlined.Settings, ""),
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SimingApp(
    viewModel: MainViewModel,
    onScanQr: () -> Unit,
    onPickText: (((MobileNovelImportFile) -> Unit) -> Unit),
    onPickProjectPackage: (((MobileProjectPackageFile) -> Unit) -> Unit),
    onSaveExport: (MobileExportFile) -> Unit,
) {
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val libraryProjects by viewModel.projects.collectAsStateWithLifecycle()
    val projects = libraryProjects.map { it.project }
    val creationDrafts by viewModel.creationDrafts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    val snackbar = remember { SnackbarHostState() }
    val screenState = rememberSaveableStateHolder()
    var rootTab by rememberSaveable { mutableStateOf(RootTab.Library) }
    var selectedProjectId by rememberSaveable { mutableStateOf<String?>(null) }
    var showDirectApiSetup by rememberSaveable { mutableStateOf(false) }
    var showAbout by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(ui.notice, ui.error) {
        val message = ui.error ?: ui.notice ?: return@LaunchedEffect
        snackbar.showSnackbar(message)
        viewModel.clearNotice()
    }

    BackHandler(enabled = showDirectApiSetup || showAbout || selectedProjectId != null || rootTab != RootTab.Library) {
        when {
            showDirectApiSetup -> showDirectApiSetup = false
            showAbout -> showAbout = false
            selectedProjectId != null -> selectedProjectId = null
            else -> rootTab = RootTab.Library
        }
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

    if (showAbout) {
        MobileAboutWorkspace(onBack = { showAbout = false })
        return
    }

    val setupRequired = !ui.connectionSetupDeferred && connection == null &&
        projects.isEmpty() && creationDrafts.isEmpty() && ui.directApi == null
    if (setupRequired || ui.pairing != null) {
        PairingScreen(
            viewModel = viewModel,
            allowBack = !setupRequired,
            onBack = viewModel::cancelPairing,
            onScanQr = onScanQr,
            onConfigureApi = { showDirectApiSetup = true },
            onDeferSetup = if (setupRequired) viewModel::deferConnectionSetup else null,
            snackbar = snackbar,
        )
        return
    }

    val selectedProject = projects.firstOrNull { it.projectId == selectedProjectId }
    if (selectedProject != null) {
        screenState.SaveableStateProvider("project:${selectedProject.projectId}") {
        ProjectScreen(
            viewModel = viewModel,
            project = selectedProject,
            onBack = { selectedProjectId = null },
            snackbar = snackbar,
            onSaveExport = onSaveExport,
            onConfigureDirectApi = { showDirectApiSetup = true },
        )
        }
        return
    }

    Scaffold(
        containerColor = SimingPaper,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                if (!(rootTab == RootTab.Create && ui.activeCreationId != null)) SimingTopBar(connection, ui.directApi)
                if (ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        },
        bottomBar = {
            if (!(rootTab == RootTab.Create && ui.activeCreationId != null) && WindowInsets.ime.getBottom(LocalDensity.current) <= WindowInsets.navigationBars.getBottom(LocalDensity.current)) NavigationBar(
                containerColor = SimingPaperWarm,
                tonalElevation = 0.dp,
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
        screenState.SaveableStateProvider(rootTab.name) {
        when (rootTab) {
            RootTab.Create -> CreationScreen(
                modifier = Modifier.padding(padding).consumeWindowInsets(padding),
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
                projects = libraryProjects,
                viewModel = viewModel,
                onOpenProject = { selectedProjectId = it },
                onPickText = onPickText,
                onPickProjectPackage = onPickProjectPackage,
                onStartAiCreation = { rootTab = RootTab.Create },
            )
            RootTab.Sync -> MobileSyncWorkspace(
                modifier = Modifier.padding(padding),
                viewModel = viewModel,
                connection = connection,
                onScanQr = onScanQr,
            )
            RootTab.Settings -> MobileSettingsWorkspace(
                modifier = Modifier.padding(padding),
                connection = connection,
                directApi = ui.directApi,
                viewModel = viewModel,
                onConfigureApi = { showDirectApiSetup = true },
                onOpenSync = { rootTab = RootTab.Sync },
                onOpenAbout = { showAbout = true },
            )
        }
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
                        connection != null -> "已连接电脑"
                        directApi != null -> "手机 AI 已配置"
                        else -> "离线创作"
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        navigationIcon = {
            Image(
                painter = painterResource(R.drawable.ic_siming_pc),
                contentDescription = "司命应用图标",
                modifier = Modifier
                    .padding(start = 12.dp)
                    .size(36.dp)
                    .clip(RoundedCornerShape(9.dp)),
            )
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LibraryScreen(
    modifier: Modifier,
    projects: List<ProjectSyncRecord>,
    viewModel: MainViewModel,
    onOpenProject: (String) -> Unit,
    onPickText: (((MobileNovelImportFile) -> Unit) -> Unit),
    onPickProjectPackage: (((MobileProjectPackageFile) -> Unit) -> Unit),
    onStartAiCreation: () -> Unit,
) {
    var showCreate by rememberSaveable { mutableStateOf(false) }
    var showActions by rememberSaveable { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    var deleteTarget by remember { mutableStateOf<String?>(null) }
    val visibleProjects = projects.filter {
        query.isBlank() || it.project.formText("title").contains(query, ignoreCase = true)
    }
    LazyColumn(
        modifier = modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.weight(1f)) {
                    Text("我的书架", style = MaterialTheme.typography.headlineSmall)
                    Text("${projects.size} 部作品", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Button(onClick = { showActions = true }) {
                    Icon(Icons.Outlined.Add, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("新建")
                }
            }
        }
        if (projects.isNotEmpty()) {
            item {
                OutlinedTextField(query, { query = it }, placeholder = { Text("搜索作品") },
                    leadingIcon = { Icon(Icons.Outlined.Search, null) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.medium)
            }
        }
        if (projects.isEmpty()) {
            item {
                OutlinedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(22.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Icon(Icons.AutoMirrored.Outlined.MenuBook, null, tint = SimingCinnabar, modifier = Modifier.size(36.dp))
                        Text("故事，从这里开始", style = MaterialTheme.typography.titleLarge)
                        Text("和 AI 一起构思新书，或导入已有作品继续写。", style = MaterialTheme.typography.bodyMedium)
                        Button(onClick = onStartAiCreation, modifier = Modifier.fillMaxWidth()) { Text("开始立项") }
                        TextButton(onClick = { showActions = true }, modifier = Modifier.fillMaxWidth()) { Text("导入或手动创建") }
                    }
                }
            }
        } else if (visibleProjects.isEmpty()) {
            item { EmptyPanel(Icons.Outlined.Search, "没有找到作品", "换个书名再试试。") }
        } else {
            items(visibleProjects, key = { it.project.key }) { record ->
                MobileProjectCard(record, onClick = { onOpenProject(record.project.projectId) },
                    onDelete = { deleteTarget = record.project.projectId })
            }
        }
    }
    if (showActions) {
        ModalBottomSheet(onDismissRequest = { showActions = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            Text("添加作品", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(20.dp))
            LibraryActionPanel(
                onStartAiCreation = { showActions = false; onStartAiCreation() },
                onCreateBlank = { showActions = false; showCreate = true },
                onImportNovel = {
                    showActions = false
                    onPickText { file -> viewModel.importNovel(file, onOpenProject) }
                },
                onImportProjectPackage = {
                    showActions = false
                    onPickProjectPackage { file -> viewModel.importProjectPackage(file, onOpenProject) }
                },
            )
            Spacer(Modifier.height(20.dp))
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
    projects.firstOrNull { it.project.projectId == deleteTarget }?.let { record ->
        val target = record.project
        val title = target.text("title").ifBlank { "未命名作品" }
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("删除《$title》？") },
            text = {
                Text(
                    "将从当前手机移除作品并取消待同步记录。其他设备上的副本保留，后续同步不会自动把作品重新下载。此操作不可撤销。",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.deleteProject(target.projectId) { deleteTarget = null }
                    },
                ) {
                    Text("确认删除")
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) { Text("取消") }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ProjectScreen(
    viewModel: MainViewModel,
    project: ReplicaEntity,
    onBack: () -> Unit,
    snackbar: SnackbarHostState,
    onSaveExport: (MobileExportFile) -> Unit,
    onConfigureDirectApi: () -> Unit,
) {
    var section by rememberSaveable(project.projectId) { mutableStateOf("chapter") }
    var editor by remember { mutableStateOf<EditorTarget?>(null) }
    var advanced by remember { mutableStateOf<EditorTarget?>(null) }
    var chapterEditor by remember { mutableStateOf<ReplicaEntity?>(null) }
    var referenceTarget by remember { mutableStateOf<EditorTarget?>(null) }
    var outlineTarget by remember { mutableStateOf<OutlineEditorTarget?>(null) }
    var narrativeTarget by remember { mutableStateOf<EditorTarget?>(null) }
    var creatingChapter by remember { mutableStateOf(false) }
    var showChapterOrder by remember { mutableStateOf(false) }
    val currentSection = entitySections.firstOrNull { it.type == section }
    val records by viewModel.entities(project.projectId, section).collectAsStateWithLifecycle(initialValue = emptyList())
    val outlineRecords by viewModel.entities(project.projectId, "outline").collectAsStateWithLifecycle(initialValue = emptyList())
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val ui by viewModel.uiState

    LaunchedEffect(project.projectId, connection?.deviceId) {
        viewModel.restorePendingChapterDraft(project.projectId)
        viewModel.restorePendingOutlineDraft(project.projectId)
    }

    fun navigateBack() {
        when {
            outlineTarget != null -> outlineTarget = null
            narrativeTarget != null -> narrativeTarget = null
            referenceTarget != null -> referenceTarget = null
            editor != null -> editor = null
            chapterEditor != null || creatingChapter -> { chapterEditor = null; creatingChapter = false }
            section in projectReferenceSections.map { it.first } || section == "tools" -> section = "reference"
            section != "chapter" -> section = "chapter"
            else -> onBack()
        }
    }
    BackHandler(onBack = ::navigateBack)

    val pendingChapterDraft = ui.pendingChapterDraft
        ?.takeIf { it.projectId == project.projectId }
    if (pendingChapterDraft != null) {
        PendingChapterDraftEditorScreen(
            draft = pendingChapterDraft,
            online = connection != null,
            busy = ui.busy,
            viewModel = viewModel,
            onBack = {
                viewModel.hidePendingChapterDraft()
                section = "assistant"
            },
            onSaved = {
                viewModel.hidePendingChapterDraft()
                section = "chapter"
            },
        )
        return
    }

    if (creatingChapter || chapterEditor != null) {
        val activeChapter = chapterEditor
        ChapterEditorScreen(
            projectId = project.projectId,
            chapter = activeChapter,
            suggestedTitle = "第 ${records.size + 1} 章",
            viewModel = viewModel,
            onBack = {
                creatingChapter = false
                chapterEditor = null
            },
            onOpenAi = {
                creatingChapter = false
                chapterEditor = null
                section = "assistant"
            },
            onOpenHistory = activeChapter?.let { record ->
                {
                    chapterEditor = null
                    advanced = EditorTarget("chapter", record)
                }
            },
        )
        return
    }

    if (outlineTarget != null) {
    val activeOutlineTarget = requireNotNull(outlineTarget)
    OutlineDetailScreen(
        projectId = project.projectId,
        target = activeOutlineTarget,
        records = outlineRecords,
        viewModel = viewModel,
        onBack = { outlineTarget = null },
        onAddChild = { parent -> outlineTarget = OutlineEditorTarget(null, parent.entityId) },
    )
    return
}

if (narrativeTarget != null) {
    val activeNarrativeTarget = requireNotNull(narrativeTarget)
    NarrativeDetailScreen(
        projectId = project.projectId,
        entityType = activeNarrativeTarget.entityType,
        record = activeNarrativeTarget.record,
        viewModel = viewModel,
        onBack = { narrativeTarget = null },
    )
    return
}

if (referenceTarget != null) {
    val target = requireNotNull(referenceTarget)
    when (target.entityType) {
        "character" -> CharacterDetailScreen(
            projectId = project.projectId,
            character = target.record,
            viewModel = viewModel,
            onBack = { referenceTarget = null },
            onAdvanced = target.record?.let { record ->
                { advanced = EditorTarget("character", record) }
            },
        )
        "world" -> WorldDetailScreen(
            projectId = project.projectId,
            entry = target.record,
            viewModel = viewModel,
            onBack = { referenceTarget = null },
            onAdvanced = target.record?.let { record ->
                { advanced = EditorTarget("world", record) }
            },
        )
    }
    advanced?.let { extra ->
        val record = extra.record
        if (record != null) {
            when (extra.entityType) {
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
    return
}

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
                                currentSection?.label ?: if (section == "assistant") "项目助手" else "故事资料",
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                    },
                    navigationIcon = {
                        IconButton(onClick = ::navigateBack) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, if (section == "chapter") "返回作品库" else "返回")
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
        bottomBar = {
            if (WindowInsets.ime.getBottom(LocalDensity.current) <= WindowInsets.navigationBars.getBottom(LocalDensity.current)) ProjectPrimaryNavigation(
                selected = section,
                onSelected = { section = it },
            )
        },
        floatingActionButton = {
            if (section !in setOf("assistant", "tools", "reference", "chapter")) {
                FloatingActionButton(
            onClick = {
                if (section == "chapter") creatingChapter = true
                else if (section == "outline") outlineTarget = OutlineEditorTarget(null)
                else if (section in setOf("character", "world")) referenceTarget = EditorTarget(section, null)
                else if (section in setOf("foreshadowing", "governance")) narrativeTarget = EditorTarget(section, null)
                else editor = EditorTarget(section, null)
            },
        ) {
                    Icon(Icons.Outlined.Add, "新建${requireNotNull(currentSection).label}")
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).consumeWindowInsets(padding).fillMaxSize()) {
            when (section) {
                "reference" -> ProjectReferenceHome(onSelected = { section = it },
                    onEditProject = { editor = EditorTarget("project", project) })
                "chapter" -> ChapterWorkspace(
                    chapters = records,
                    outlines = outlineRecords,
                    onOpen = { chapterEditor = it },
                    onManageOrder = { showChapterOrder = true },
                    onCreate = { creatingChapter = true },
                    onOpenAssistant = { section = "assistant" },
                    onOpenCataloging = { section = "tools" },
                )
                "assistant" -> AssistantScreen(
                    projectId = project.projectId,
                    viewModel = viewModel,
                    onConfigureDirectApi = onConfigureDirectApi,
                )
                "tools" -> ProjectToolsPanel(
                    project = project,
                    online = connection != null,
                    ui = ui,
                    viewModel = viewModel,
                    onExportReady = onSaveExport,
                )
                "outline" -> MobileOutlineWorkspace(
                    projectId = project.projectId,
                    records = records,
                    online = connection != null,
                    pendingDraft = ui.pendingOutlineDraft?.takeIf { it.projectId == project.projectId },
                    busy = ui.busy,
                    onOpen = { outlineTarget = OutlineEditorTarget(it) },
                    onAddChild = { parent -> outlineTarget = OutlineEditorTarget(null, parent.entityId) },
                    onReorder = { parentId, nodeIds ->
                        viewModel.reorderOutline(project.projectId, parentId, nodeIds)
                    },
                    onUpdateDraft = viewModel::updatePendingOutlineDraft,
                    onConfirmDraft = { draft, nodes, notes, writeAfterConfirm ->
                        viewModel.confirmPendingOutlineDraft(
                            draft,
                            nodes,
                            notes,
                            writeAfterConfirm,
                            onConfirmed = {
                                if (writeAfterConfirm) section = "assistant"
                            },
                        )
                    },
                    onRegenerateDraft = { draft ->
                        viewModel.regeneratePendingOutlineDraft(draft) { section = "assistant" }
                    },
                    onDiscardDraft = viewModel::discardPendingOutlineDraft,
                )
                "foreshadowing", "governance" -> NarrativeWorkspace(
                    entityType = section,
                    records = records,
                    onOpen = { narrativeTarget = EditorTarget(section, it) },
                )
                "character" -> CharacterWorkspace(
                    records = records,
                    onOpen = { referenceTarget = EditorTarget("character", it) },
                )
                "world" -> WorldWorkspace(
                    records = records,
                    onOpen = { referenceTarget = EditorTarget("world", it) },
                )
                else -> RecordList(
                    section = requireNotNull(currentSection),
                    records = records,
                    online = connection != null,
                    onOpen = { editor = EditorTarget(section, it) },
                    onAdvanced = if (section in setOf("character", "world")) {
                        { record -> advanced = EditorTarget(section, record) }
                    } else {
                        null
                    },
                    onManageChapterOrder = null,
                )
            }
        }
    }


if (ui.pendingCatalogingProjectId == project.projectId) {
    AlertDialog(
        onDismissRequest = viewModel::dismissImportCatalogingPrompt,
        title = { Text("导入完成 · ${ui.importedChapterCount} 章") },
        text = {
            Text(
                "正文已经保存在手机。配置手机 API 后，可到“工具”独立完成章节建档；也可以先阅读和编辑。",
            )
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (ui.directApi != null) viewModel.startCataloging(project.projectId)
                    section = "tools"
                    viewModel.dismissImportCatalogingPrompt()
                },
            ) {
                Text(if (ui.directApi != null) "开始建档" else "打开作品工具")
            }
        },
        dismissButton = {
            TextButton(onClick = viewModel::dismissImportCatalogingPrompt) { Text("稍后") }
        },
    )
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
                    "chapter" -> "正文、历史版本和章节顺序保存在手机，可独立编辑和恢复。"
                    "character" -> "字段直接对应 PC 角色卡：别名、外貌、能力、位置、境界、身心状态、目标与冲突共享同一份数据。"
                    "world" -> "规则与设定作为独立实体维护，避免二创时漂移。"
                    else -> "修改保存在手机，跨设备同步时保留版本分岔保护。"
                    },
                )
                if (onManageChapterOrder != null) {
                    OutlinedButton(
                        onClick = onManageChapterOrder,
                        enabled = records.size > 1,
                    ) {
                        Text("管理章节顺序")
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
                    advancedEnabled = true,
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
                                "chapter" -> "版本历史"
                                "character" -> "关系 / AI / 版本"
                                "world" -> "版本 / 时间线"
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
                "保存到手机后即可继续使用；跨设备同步会在 Gateway 可用时进行。",
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
private fun AssistantScreen(
    projectId: String,
    viewModel: MainViewModel,
    onConfigureDirectApi: () -> Unit,
) {
    AssistantWorkspace(projectId, viewModel, onConfigureDirectApi)
}

@Composable
private fun PairingScreen(
    viewModel: MainViewModel,
    allowBack: Boolean,
    onBack: () -> Unit,
    onScanQr: () -> Unit,
    onConfigureApi: () -> Unit,
    onDeferSetup: (() -> Unit)?,
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
            Text("随时，写下你的故事", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            Text(
                "可以先写作，也可以连接 AI 一起构思。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 8.dp),
            )
            Spacer(Modifier.height(22.dp))
            if (ui.pairing == null) {
                Button(onClick = onConfigureApi, modifier = Modifier.fillMaxWidth().height(50.dp)) {
                    Icon(Icons.Outlined.Key, null)
                    Spacer(Modifier.width(9.dp))
                    Text("配置手机 AI")
                }
                Text(
                    "使用自己的 API，手机可独立完成创作。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
                if (onDeferSetup != null) {
                    OutlinedButton(
                        onClick = onDeferSetup,
                        enabled = !ui.busy,
                        modifier = Modifier.fillMaxWidth().padding(top = 12.dp).height(50.dp),
                    ) { Text("稍后配置") }
                    Text(
                        "先进入书架，导入或手动写作。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp),
                ) {
                    HorizontalDivider(Modifier.weight(1f))
                    Text("  使用电脑模型 / 同步  ", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    HorizontalDivider(Modifier.weight(1f))
                }
                OutlinedButton(onClick = onScanQr, modifier = Modifier.fillMaxWidth().height(50.dp)) {
                    Icon(Icons.Outlined.QrCodeScanner, null)
                    Spacer(Modifier.width(9.dp))
                    Text("扫描电脑上的二维码")
                }
                TextButton(onClick = { manual = !manual }) { Text(if (manual) "收起手动粘贴" else "粘贴配对内容") }
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
            Spacer(Modifier.height(16.dp))
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
    var contextWindowTokens by rememberSaveable(existing?.baseUrl) {
        mutableStateOf(
            existing
                ?.takeIf {
                    it.contextCapacitySource != DirectApiConfig.CONTEXT_CAPACITY_FALLBACK
                }
                ?.contextWindowTokens
                ?.toString()
                .orEmpty(),
        )
    }
    var maxOutputTokens by rememberSaveable(existing?.baseUrl) {
        mutableStateOf((existing?.maxOutputTokens ?: DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS).toString())
    }
    var safetyMarginTokens by rememberSaveable(existing?.baseUrl) {
        mutableStateOf((existing?.safetyMarginTokens ?: DirectApiConfig.DEFAULT_SAFETY_MARGIN_TOKENS).toString())
    }
    var contextProfileIdentity by rememberSaveable(existing?.baseUrl) {
        mutableStateOf(
            existing?.takeIf {
                it.contextWindowTokens != null &&
                    it.contextCapacitySource != DirectApiConfig.CONTEXT_CAPACITY_FALLBACK
            }?.let { saved ->
                "${saved.baseUrl.trim()}\u001f${saved.model.trim()}"
            },
        )
    }
    val taskModels = remember(existing?.baseUrl) {
        mutableStateMapOf<String, String>().apply {
            putAll(existing?.taskModels.orEmpty())
        }
    }
    var taskModelPicker by rememberSaveable(existing?.baseUrl) { mutableStateOf<String?>(null) }
    val modelChoices = (
        listOf(model) +
            ui.discoveredModels +
            existing?.availableModels.orEmpty() +
            taskModels.values
        )
        .map(String::trim)
        .filter(String::isNotBlank)
        .distinct()
    val documentedCapacity = remember(baseUrl, model) {
        MobileKnownModelCapacityCatalog.resolve(baseUrl, model)
    }
    val selectedProfileIdentity = remember(baseUrl, model) {
        "${baseUrl.trim()}\u001f${model.trim()}"
    }

    LaunchedEffect(ui.discoveredModels) {
        if (model.isBlank()) model = ui.discoveredModels.firstOrNull().orEmpty()
    }
    LaunchedEffect(selectedProfileIdentity, documentedCapacity) {
        if (documentedCapacity != null) {
            val capacity = documentedCapacity
            contextWindowTokens = capacity.contextWindowTokens.toString()
            val currentOutput = maxOutputTokens.toIntOrNull()
                ?: DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS
            maxOutputTokens = minOf(currentOutput, capacity.maxOutputTokens).toString()
            contextProfileIdentity = selectedProfileIdentity
        } else if (contextProfileIdentity != selectedProfileIdentity) {
            // A capacity profile belongs to one exact endpoint/model pair. Do
            // not carry an old official or author-entered window to a newly
            // selected custom deployment.
            contextWindowTokens = ""
            maxOutputTokens = DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS.toString()
            contextProfileIdentity = null
        }
    }

    taskModelPicker?.let { taskType ->
        AlertDialog(
            onDismissRequest = { taskModelPicker = null },
            title = { Text("选择${DirectApiConfig.taskModelLabels[taskType] ?: taskType}模型") },
            text = {
                LazyColumn(
                    modifier = Modifier.fillMaxWidth().height(360.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    item {
                        TextButton(
                            onClick = {
                                taskModels.remove(taskType)
                                taskModelPicker = null
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("跟随 API 默认模型 · ${model.ifBlank { "尚未选择" }}")
                        }
                    }
                    items(modelChoices, key = { it }) { candidate ->
                        TextButton(
                            onClick = {
                                if (candidate == model) taskModels.remove(taskType)
                                else taskModels[taskType] = candidate
                                taskModelPicker = null
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(candidate, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { taskModelPicker = null }) { Text("取消") }
            },
        )
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
            Text("项目助手容量档案", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(
                if (documentedCapacity != null) {
                    "已按官方 API 端点和精确模型 ID 验证容量；切换模型时会同步更新。"
                } else {
                    "未取得官方或作者配置时，司命临时按未验证的 256K 上下文兜底，并使用 UTF-8 保守计数；服务商实际窗口更小时请填写档案。"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = contextWindowTokens,
                onValueChange = {
                    contextWindowTokens = it.filter(Char::isDigit).take(9)
                    contextProfileIdentity = selectedProfileIdentity.takeIf {
                        contextWindowTokens.isNotBlank()
                    }
                },
                label = { Text("上下文窗口（tokens，可留空）") },
                placeholder = { Text("留空则临时使用 256000") },
                supportingText = {
                    Text(
                        if (documentedCapacity != null) {
                            "已由官方模型规格自动填写"
                        } else {
                            "留空时使用未验证的 256K 兜底；填写后该模型档案优先"
                        },
                    )
                },
                singleLine = true,
                enabled = !ui.busy && documentedCapacity == null,
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = maxOutputTokens,
                    onValueChange = { maxOutputTokens = it.filter(Char::isDigit).take(8) },
                    label = { Text("输出预留") },
                    singleLine = true,
                    enabled = !ui.busy,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = safetyMarginTokens,
                    onValueChange = { safetyMarginTokens = it.filter(Char::isDigit).take(8) },
                    label = { Text("安全余量") },
                    singleLine = true,
                    enabled = !ui.busy,
                    modifier = Modifier.weight(1f),
                )
            }
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
            HorizontalDivider()
            Text("按任务选择模型", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(
                "显式选择优先于任务默认；没有单独设置的任务会使用上面的 API 默认模型。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DirectApiConfig.taskModelLabels.forEach { (taskType, label) ->
                OutlinedButton(
                    onClick = { taskModelPicker = taskType },
                    enabled = modelChoices.isNotEmpty() && !ui.busy,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.fillMaxWidth()) {
                        Text(label, fontWeight = FontWeight.SemiBold)
                        Text(
                            taskModels[taskType]?.let { "单独使用 · $it" }
                                ?: "跟随默认 · ${model.ifBlank { "尚未选择" }}",
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
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
                        modelChoices,
                        taskModels.toMap(),
                        contextWindowTokens.toIntOrNull(),
                        maxOutputTokens.toIntOrNull() ?: 0,
                        safetyMarginTokens.toIntOrNull() ?: -1,
                        onConfigured,
                    )
                },
                enabled = baseUrl.isNotBlank() &&
                    (apiKey.isNotBlank() || existing != null) &&
                    (contextWindowTokens.isBlank() ||
                        (contextWindowTokens.toIntOrNull() ?: 0) > 0) &&
                    (maxOutputTokens.toIntOrNull() ?: 0) > 0 &&
                    (safetyMarginTokens.toIntOrNull() ?: -1) >= 0 &&
                    ((maxOutputTokens.toIntOrNull() ?: 0) + (safetyMarginTokens.toIntOrNull() ?: 0) <
                        (contextWindowTokens.toIntOrNull()
                            ?: DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS)) &&
                    !ui.busy,
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

@Suppress("UNUSED_PARAMETER")
@Composable
internal fun ScreenHeading(kicker: String, title: String, detail: String) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        if (detail.isNotBlank()) {
            Text(
                detail,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
internal fun EmptyPanel(icon: ImageVector, title: String, detail: String) {
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
internal fun StatusBanner(
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
internal fun MicroTag(text: String, color: Color) {
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
