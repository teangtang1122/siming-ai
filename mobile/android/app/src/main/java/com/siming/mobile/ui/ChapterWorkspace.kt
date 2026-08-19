package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.MenuBook
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.SwapVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.data.local.ReplicaEntity

@Composable
internal fun ChapterWorkspace(
    chapters: List<ReplicaEntity>,
    outlines: List<ReplicaEntity>,
    online: Boolean,
    onOpen: (ReplicaEntity) -> Unit,
    onManageOrder: () -> Unit,
) {
    val totalWords = chapters.sumOf(::chapterWordCount)
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, top = 16.dp, end = 16.dp, bottom = 104.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text("正文", style = MaterialTheme.typography.headlineSmall)
                        Text(
                            "${chapters.size} 章 · ${formatWordCount(totalWords)}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    OutlinedButton(
                        onClick = onManageOrder,
                        enabled = online && chapters.size > 1,
                    ) {
                        Icon(Icons.Outlined.SwapVert, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("排序")
                    }
                }
                if (!online && chapters.size > 1) {
                    Text(
                        "当前可离线阅读和编辑；调整全书章节顺序需要连接 PC Gateway。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        if (chapters.isEmpty()) {
            item {
                EmptyPanel(
                    icon = Icons.Outlined.MenuBook,
                    title = "还没有正文",
                    detail = "点击右下角“＋”创建第一章。",
                )
            }
        } else {
            itemsIndexed(chapters, key = { _, chapter -> chapter.key }) { index, chapter ->
                val volume = chapterVolumeLabel(chapter, outlines)
                val previousVolume = chapters.getOrNull(index - 1)?.let { chapterVolumeLabel(it, outlines) }
                Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    if (!volume.isNullOrBlank() && volume != previousVolume) {
                        Text(
                            volume,
                            style = MaterialTheme.typography.labelLarge,
                            color = SimingCinnabar,
                            modifier = Modifier.padding(start = 4.dp, top = if (index == 0) 2.dp else 8.dp),
                        )
                    }
                    ChapterDirectoryCard(
                        index = index + 1,
                        chapter = chapter,
                        onClick = { onOpen(chapter) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ChapterDirectoryCard(
    index: Int,
    chapter: ReplicaEntity,
    onClick: () -> Unit,
) {
    val title = chapter.formText("title").ifBlank { "未命名章节" }
    val content = chapter.formText("content")
    Card(
        onClick = onClick,
        colors = CardDefaults.cardColors(containerColor = SimingSurfaceRaised),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.surfaceVariant,
                modifier = Modifier.size(38.dp),
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Text(
                        index.toString().padStart(2, '0'),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        title,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    when {
                        chapter.conflicted -> Icon(
                            Icons.Outlined.ErrorOutline,
                            contentDescription = "存在版本分岔",
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(17.dp),
                        )
                        chapter.dirty -> Icon(
                            Icons.Outlined.CloudQueue,
                            contentDescription = "等待同步",
                            tint = SimingBlue,
                            modifier = Modifier.size(17.dp),
                        )
                        else -> Icon(
                            Icons.Outlined.CheckCircle,
                            contentDescription = "已同步",
                            tint = SimingGreen,
                            modifier = Modifier.size(17.dp),
                        )
                    }
                }
                val snippet = chapterSnippet(content)
                if (snippet.isNotBlank()) {
                    Text(
                        snippet,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Text(
                    formatWordCount(chapterWordCount(chapter)),
                    style = MaterialTheme.typography.labelSmall,
                    color = SimingInkMuted,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ChapterEditorScreen(
    projectId: String,
    chapter: ReplicaEntity?,
    suggestedTitle: String,
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onOpenAi: () -> Unit,
    onOpenHistory: (() -> Unit)?,
) {
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val originalTitle = chapter?.formText("title").orEmpty()
    val originalContent = chapter?.formText("content").orEmpty()
    var title by rememberSaveable(chapter?.key) {
        mutableStateOf(originalTitle.ifBlank { suggestedTitle })
    }
    var content by rememberSaveable(chapter?.key) { mutableStateOf(originalContent) }
    var editing by rememberSaveable(chapter?.key) { mutableStateOf(chapter == null) }
    var showMore by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }

    fun cancelEditing() {
        if (chapter == null) {
            onBack()
        } else {
            title = originalTitle
            content = originalContent
            editing = false
        }
    }

    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        when {
                            chapter == null -> "新章节"
                            editing -> "编辑章节"
                            else -> title.ifBlank { "未命名章节" }
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = if (editing) ::cancelEditing else onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    if (chapter != null && !editing) {
                        IconButton(onClick = { showMore = true }) {
                            Icon(Icons.Outlined.MoreHoriz, contentDescription = "更多章节操作")
                        }
                    }
                },
            )
        },
        bottomBar = {
            if (editing) {
                Surface(color = SimingPaperWarm, tonalElevation = 3.dp) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 14.dp, vertical = 10.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        OutlinedButton(
                            onClick = ::cancelEditing,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text(if (chapter == null) "取消" else "放弃修改")
                        }
                        Button(
                            onClick = {
                                val fields = linkedMapOf<String, Any?>(
                                    "title" to title.trim(),
                                    "content" to content,
                                    "word_count" to content.count { !it.isWhitespace() },
                                )
                                if (chapter == null) fields["current_version"] = 1
                                viewModel.saveRecord(
                                    projectId = projectId,
                                    entityType = "chapter",
                                    entityId = chapter?.entityId,
                                    fields = fields,
                                    basePayload = chapter?.payload(),
                                    onSaved = {
                                        if (chapter == null) onBack() else editing = false
                                    },
                                )
                            },
                            enabled = title.isNotBlank(),
                            modifier = Modifier.weight(1.4f),
                        ) {
                            Text("保存")
                        }
                    }
                }
            } else {
                Surface(color = SimingPaperWarm, tonalElevation = 3.dp) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 12.dp, vertical = 9.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        OutlinedButton(onClick = onOpenAi, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(5.dp))
                            Text("AI 共创")
                        }
                        OutlinedButton(onClick = { showMore = true }, modifier = Modifier.weight(0.82f)) {
                            Icon(Icons.Outlined.MoreHoriz, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(5.dp))
                            Text("更多")
                        }
                        Button(onClick = { editing = true }, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Outlined.Edit, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(5.dp))
                            Text("编辑")
                        }
                    }
                }
            }
        },
    ) { padding ->
        if (editing) {
            Column(
                modifier = Modifier
                    .padding(padding)
                    .fillMaxSize()
                    .imePadding()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                OutlinedTextField(
                    value = title,
                    onValueChange = { title = it },
                    placeholder = { Text("章节标题") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = content,
                    onValueChange = { content = it },
                    placeholder = { Text("开始写正文…") },
                    minLines = 16,
                    maxLines = Int.MAX_VALUE,
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    textStyle = MaterialTheme.typography.bodyLarge.copy(lineHeight = 28.sp),
                )
                Text(
                    "${content.count { !it.isWhitespace() }} 字 · ${if (connection != null) "保存后同步到 PC" else "离线保存在本机"}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            ChapterReadingView(
                title = title,
                content = content,
                chapter = requireNotNull(chapter),
                modifier = Modifier.padding(padding),
            )
        }
    }

    if (showMore && chapter != null) {
        ModalBottomSheet(onDismissRequest = { showMore = false }) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(horizontal = 18.dp, vertical = 4.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text("章节操作", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(vertical = 8.dp))
                TextButton(
                    onClick = {
                        showMore = false
                        onOpenHistory?.invoke()
                    },
                    enabled = connection != null && onOpenHistory != null,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Outlined.History, null)
                    Spacer(Modifier.width(9.dp))
                    Text(if (connection != null) "版本历史" else "版本历史需要连接 PC")
                    Spacer(Modifier.weight(1f))
                }
                TextButton(
                    onClick = {
                        showMore = false
                        showDelete = true
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(9.dp))
                    Text("删除章节", color = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.weight(1f))
                }
                Spacer(Modifier.height(14.dp))
            }
        }
    }

    if (showDelete && chapter != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("删除《${title.ifBlank { "未命名章节" }}》？") },
            text = { Text("删除会进入现有可靠同步流程；已同步作品不会绕过 PC 的版本与删除保护。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        showDelete = false
                        viewModel.deleteRecord(projectId, "chapter", chapter.entityId, onBack)
                    },
                ) {
                    Text("确认删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun ChapterReadingView(
    title: String,
    content: String,
    chapter: ReplicaEntity,
    modifier: Modifier = Modifier,
) {
    val paragraphs = remember(content) { chapterParagraphs(content) }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, top = 18.dp, end = 20.dp, bottom = 36.dp),
        verticalArrangement = Arrangement.spacedBy(15.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    title.ifBlank { "未命名章节" },
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        formatWordCount(chapterWordCount(chapter)),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text("·", color = SimingInkMuted)
                    Text(
                        when {
                            chapter.conflicted -> "存在版本分岔"
                            chapter.dirty -> "等待同步"
                            else -> "已同步"
                        },
                        style = MaterialTheme.typography.labelMedium,
                        color = when {
                            chapter.conflicted -> MaterialTheme.colorScheme.error
                            chapter.dirty -> SimingBlue
                            else -> SimingGreen
                        },
                    )
                }
            }
        }
        if (paragraphs.isEmpty()) {
            item {
                Text(
                    "这一章还没有正文。点击下方“编辑”开始写作。",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            itemsIndexed(paragraphs) { _, paragraph ->
                SelectionContainer {
                    Text(
                        paragraph,
                        style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 30.sp),
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}

internal fun chapterWordCount(chapter: ReplicaEntity): Int {
    val stored = chapter.formText("word_count").toIntOrNull()
    if (stored != null && stored >= 0) return stored
    return chapter.formText("content").count { !it.isWhitespace() }
}

internal fun chapterVolumeLabel(chapter: ReplicaEntity, outlines: List<ReplicaEntity>): String? {
    val outlineId = chapter.formText("outline_node_id").trim().takeIf(String::isNotBlank) ?: return null
    val byId = outlines.associateBy { it.entityId }
    var current = byId[outlineId] ?: return null
    val visited = mutableSetOf<String>()
    repeat(16) {
        if (!visited.add(current.entityId)) return null
        val title = current.formText("title").trim()
        if (current.formText("node_type") == "volume") return title.takeIf(String::isNotBlank)
        val parentId = current.formText("parent_id").trim().takeIf(String::isNotBlank) ?: return null
        current = byId[parentId] ?: return null
    }
    return null
}

internal fun chapterParagraphs(content: String): List<String> = content
    .replace("\r\n", "\n")
    .replace('\r', '\n')
    .split(Regex("\\n\\s*\\n"))
    .map(String::trim)
    .filter(String::isNotBlank)

private fun chapterSnippet(content: String): String = content
    .replace(Regex("\\s+"), " ")
    .trim()

private fun formatWordCount(count: Int): String = when {
    count >= 100_000 -> "%.1f 万字".format(count / 10_000.0)
    count >= 10_000 -> "%.1f 万字".format(count / 10_000.0)
    else -> "$count 字"
}
