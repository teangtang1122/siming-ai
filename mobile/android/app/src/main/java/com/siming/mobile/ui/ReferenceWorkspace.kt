package com.siming.mobile.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Save
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
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

private val roleOptions = listOf(
    "protagonist" to "主角",
    "supporting" to "配角",
    "antagonist" to "对手",
    "mentor" to "导师",
    "other" to "其他",
)

private val lifeOptions = listOf(
    "active" to "在场",
    "deceased" to "已故",
    "unknown" to "未知",
)

private val worldDimensionOptions = listOf(
    "geography" to "地理",
    "history" to "历史",
    "factions" to "势力",
    "power_system" to "力量体系",
    "races" to "种族",
    "culture" to "文化",
)

@Composable
internal fun CharacterWorkspace(
    records: List<ReplicaEntity>,
    onOpen: (ReplicaEntity) -> Unit,
) {
    val protagonists = records.count { it.formText("role_type") == "protagonist" }
    val tracked = records.count { characterTracked(it) }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, top = 16.dp, end = 16.dp, bottom = 104.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("角色", style = MaterialTheme.typography.headlineSmall)
                Text(
                    if (records.isEmpty()) "把人物当成正在变化的人，而不是一张字段表。" else "${records.size} 人 · $protagonists 位主角 · $tracked 人持续追踪",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (records.isEmpty()) {
            item {
                EmptyPanel(
                    icon = Icons.Outlined.Person,
                    title = "还没有角色",
                    detail = "点击右下角“＋”创建人物；先写清目标、冲突和当下状态。",
                )
            }
        } else {
            items(records, key = { it.key }) { character ->
                CharacterDirectoryCard(character = character, onClick = { onOpen(character) })
            }
        }
    }
}

@Composable
private fun CharacterDirectoryCard(character: ReplicaEntity, onClick: () -> Unit) {
    val name = character.formText("name").ifBlank { "未命名角色" }
    val role = characterRoleLabel(character.formText("role_type"))
    val realm = character.formText("realm_or_level")
    val location = character.formText("current_location")
    val summary = characterPrimarySummary(character)
    OutlinedCard(
        onClick = onClick,
        colors = CardDefaults.outlinedCardColors(containerColor = Color.White),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.primaryContainer,
                modifier = Modifier.size(44.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(characterInitial(name), color = SimingCinnabar, fontWeight = FontWeight.Bold)
                }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    ReferenceSyncIcon(character)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    MicroTag(role, SimingCinnabar)
                    if (realm.isNotBlank()) MicroTag(realm, SimingBlue)
                }
                if (summary.isNotBlank()) {
                    Text(
                        summary,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (location.isNotBlank()) {
                    Text("现在：$location", style = MaterialTheme.typography.labelSmall, color = SimingInkMuted)
                }
            }
        }
    }
}

@Composable
internal fun WorldWorkspace(
    records: List<ReplicaEntity>,
    onOpen: (ReplicaEntity) -> Unit,
) {
    val grouped = records.groupBy { it.formText("dimension").ifBlank { "culture" } }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, top = 16.dp, end = 16.dp, bottom = 104.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("世界", style = MaterialTheme.typography.headlineSmall)
                Text(
                    if (records.isEmpty()) "把规则写成可检索、可复用的设定，而不是埋在正文里。" else "${records.size} 条设定 · ${grouped.size} 个维度",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (records.isEmpty()) {
            item {
                EmptyPanel(
                    icon = Icons.Outlined.Hub,
                    title = "还没有世界观设定",
                    detail = "点击右下角“＋”创建一条规则、地点、历史或力量体系。",
                )
            }
        } else {
            worldDimensionOptions.forEach { (dimension, label) ->
                val entries = grouped[dimension].orEmpty()
                if (entries.isNotEmpty()) {
                    item {
                        Text(label, style = MaterialTheme.typography.labelLarge, color = SimingCinnabar, modifier = Modifier.padding(start = 4.dp, top = 6.dp))
                    }
                    items(entries, key = { it.key }) { entry ->
                        WorldDirectoryCard(entry = entry, onClick = { onOpen(entry) })
                    }
                }
            }
            val known = worldDimensionOptions.map { it.first }.toSet()
            val otherEntries = records.filter { it.formText("dimension").ifBlank { "culture" } !in known }
            if (otherEntries.isNotEmpty()) {
                item { Text("其他", style = MaterialTheme.typography.labelLarge, color = SimingCinnabar, modifier = Modifier.padding(start = 4.dp, top = 6.dp)) }
                items(otherEntries, key = { it.key }) { entry -> WorldDirectoryCard(entry, onClick = { onOpen(entry) }) }
            }
        }
    }
}

@Composable
private fun WorldDirectoryCard(entry: ReplicaEntity, onClick: () -> Unit) {
    val title = entry.formText("title").ifBlank { "未命名设定" }
    val content = entry.formText("content")
    OutlinedCard(onClick = onClick, colors = CardDefaults.outlinedCardColors(containerColor = Color.White), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                ReferenceSyncIcon(entry)
            }
            Text(worldDimensionLabel(entry.formText("dimension")), style = MaterialTheme.typography.labelSmall, color = SimingCinnabar)
            if (content.isNotBlank()) {
                Text(
                    referenceSnippet(content),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CharacterDetailScreen(
    projectId: String,
    character: ReplicaEntity?,
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onAdvanced: (() -> Unit)?,
) {
    val creating = character == null
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    var editing by rememberSaveable(character?.key) { mutableStateOf(creating) }
    var showMore by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }

    var name by rememberSaveable(character?.key) { mutableStateOf(character?.formText("name").orEmpty()) }
    var aliases by rememberSaveable(character?.key) { mutableStateOf(character?.formText("aliases").orEmpty()) }
    var roleType by rememberSaveable(character?.key) { mutableStateOf(character?.formText("role_type").orEmpty().ifBlank { "supporting" }) }
    var age by rememberSaveable(character?.key) { mutableStateOf(character?.formText("age").orEmpty()) }
    var appearance by rememberSaveable(character?.key) { mutableStateOf(character?.formText("appearance").orEmpty()) }
    var personality by rememberSaveable(character?.key) { mutableStateOf(character?.formText("personality").orEmpty()) }
    var background by rememberSaveable(character?.key) { mutableStateOf(character?.formText("background").orEmpty()) }
    var abilities by rememberSaveable(character?.key) { mutableStateOf(character?.formText("abilities").orEmpty()) }
    var lifeStatus by rememberSaveable(character?.key) { mutableStateOf(character?.formText("life_status").orEmpty().ifBlank { "active" }) }
    var location by rememberSaveable(character?.key) { mutableStateOf(character?.formText("current_location").orEmpty()) }
    var realm by rememberSaveable(character?.key) { mutableStateOf(character?.formText("realm_or_level").orEmpty()) }
    var physical by rememberSaveable(character?.key) { mutableStateOf(character?.formText("physical_state").orEmpty()) }
    var mental by rememberSaveable(character?.key) { mutableStateOf(character?.formText("mental_state").orEmpty()) }
    var goal by rememberSaveable(character?.key) { mutableStateOf(character?.formText("current_goal").orEmpty()) }
    var conflict by rememberSaveable(character?.key) { mutableStateOf(character?.formText("active_conflict").orEmpty()) }
    var abilityState by rememberSaveable(character?.key) { mutableStateOf(character?.formText("abilities_state").orEmpty()) }
    var assets by rememberSaveable(character?.key) { mutableStateOf(character?.formText("items_or_assets").orEmpty()) }
    var tracked by rememberSaveable(character?.key) { mutableStateOf(character?.let(::characterTracked) ?: true) }
    var profileJson by rememberSaveable(character?.key) { mutableStateOf(character?.formText("profile").orEmpty().ifBlank { "{}" }) }
    var showProfile by rememberSaveable(character?.key) { mutableStateOf(false) }

    fun reset() {
        if (creating) {
            onBack()
            return
        }
        name = character?.formText("name").orEmpty()
        aliases = character?.formText("aliases").orEmpty()
        roleType = character?.formText("role_type").orEmpty().ifBlank { "supporting" }
        age = character?.formText("age").orEmpty()
        appearance = character?.formText("appearance").orEmpty()
        personality = character?.formText("personality").orEmpty()
        background = character?.formText("background").orEmpty()
        abilities = character?.formText("abilities").orEmpty()
        lifeStatus = character?.formText("life_status").orEmpty().ifBlank { "active" }
        location = character?.formText("current_location").orEmpty()
        realm = character?.formText("realm_or_level").orEmpty()
        physical = character?.formText("physical_state").orEmpty()
        mental = character?.formText("mental_state").orEmpty()
        goal = character?.formText("current_goal").orEmpty()
        conflict = character?.formText("active_conflict").orEmpty()
        abilityState = character?.formText("abilities_state").orEmpty()
        assets = character?.formText("items_or_assets").orEmpty()
        tracked = character?.let(::characterTracked) ?: true
        profileJson = character?.formText("profile").orEmpty().ifBlank { "{}" }
        showProfile = false
        editing = false
    }

    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text(if (creating) "新角色" else if (editing) "编辑角色" else name.ifBlank { "未命名角色" }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = { IconButton(onClick = if (editing) ::reset else onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                actions = {
                    if (!creating && !editing) {
                        IconButton(onClick = { showMore = true }) { Icon(Icons.Outlined.MoreHoriz, "更多角色操作") }
                    }
                },
            )
        },
        bottomBar = {
            Surface(color = SimingPaperWarm, tonalElevation = 3.dp) {
                if (editing) {
                    Row(
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 14.dp, vertical = 10.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        OutlinedButton(onClick = ::reset, modifier = Modifier.weight(1f)) { Text(if (creating) "取消" else "放弃") }
                        Button(
                            enabled = name.isNotBlank(),
                            onClick = {
                                val fields = linkedMapOf<String, Any?>(
                                    "name" to name.trim(),
                                    "aliases" to aliases,
                                    "role_type" to roleType,
                                    "age" to age,
                                    "appearance" to appearance,
                                    "personality" to personality,
                                    "background" to background,
                                    "abilities" to abilities,
                                    "life_status" to lifeStatus,
                                    "current_location" to location,
                                    "realm_or_level" to realm,
                                    "physical_state" to physical,
                                    "mental_state" to mental,
                                    "current_goal" to goal,
                                    "active_conflict" to conflict,
                                    "abilities_state" to abilityState,
                                    "items_or_assets" to assets,
                                    "is_evolution_tracked" to tracked,
                                )
                                if (showProfile) fields["profile"] = profileJson
                                viewModel.saveRecord(
                                    projectId = projectId,
                                    entityType = "character",
                                    entityId = character?.entityId,
                                    fields = fields,
                                    basePayload = character?.payload(),
                                    onSaved = { if (creating) onBack() else editing = false },
                                )
                            },
                            modifier = Modifier.weight(1.35f),
                        ) {
                            Icon(Icons.Outlined.Save, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("保存")
                        }
                    }
                } else {
                    Row(
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 14.dp, vertical = 10.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        OutlinedButton(onClick = { showMore = true }, modifier = Modifier.weight(1f)) { Text("关系 / AI") }
                        Button(onClick = { editing = true }, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Outlined.Edit, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("编辑")
                        }
                    }
                }
            }
        },
    ) { padding ->
        if (editing) {
            CharacterEditorContent(
                modifier = Modifier.padding(padding),
                name = name,
                onName = { name = it },
                aliases = aliases,
                onAliases = { aliases = it },
                roleType = roleType,
                onRoleType = { roleType = it },
                age = age,
                onAge = { age = it },
                lifeStatus = lifeStatus,
                onLifeStatus = { lifeStatus = it },
                goal = goal,
                onGoal = { goal = it },
                conflict = conflict,
                onConflict = { conflict = it },
                location = location,
                onLocation = { location = it },
                realm = realm,
                onRealm = { realm = it },
                physical = physical,
                onPhysical = { physical = it },
                mental = mental,
                onMental = { mental = it },
                appearance = appearance,
                onAppearance = { appearance = it },
                personality = personality,
                onPersonality = { personality = it },
                background = background,
                onBackground = { background = it },
                abilities = abilities,
                onAbilities = { abilities = it },
                abilityState = abilityState,
                onAbilityState = { abilityState = it },
                assets = assets,
                onAssets = { assets = it },
                tracked = tracked,
                onTracked = { tracked = it },
                showProfile = showProfile,
                onToggleProfile = { showProfile = !showProfile },
                profileJson = profileJson,
                onProfileJson = { profileJson = it },
            )
        } else {
            CharacterReadingContent(character = requireNotNull(character), modifier = Modifier.padding(padding))
        }
    }

    if (showMore && character != null) {
        ModalBottomSheet(onDismissRequest = { showMore = false }) {
            Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 18.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("角色操作", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(vertical = 8.dp))
                TextButton(
                    enabled = connection != null && onAdvanced != null,
                    onClick = { showMore = false; onAdvanced?.invoke() },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(if (connection != null) "关系 / AI 配置 / 版本历史" else "高级资料需要连接 PC")
                    Spacer(Modifier.weight(1f))
                }
                TextButton(onClick = { showMore = false; showDelete = true }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(8.dp))
                    Text("删除角色", color = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.weight(1f))
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }

    if (showDelete && character != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("删除“${name.ifBlank { "未命名角色" }}”？") },
            text = { Text("删除继续使用现有可靠同步与冲突保护，不会绕过 PC 的权威版本。") },
            confirmButton = {
                TextButton(onClick = { showDelete = false; viewModel.deleteRecord(projectId, "character", character.entityId, onBack) }) {
                    Text("确认删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun CharacterEditorContent(
    modifier: Modifier,
    name: String,
    onName: (String) -> Unit,
    aliases: String,
    onAliases: (String) -> Unit,
    roleType: String,
    onRoleType: (String) -> Unit,
    age: String,
    onAge: (String) -> Unit,
    lifeStatus: String,
    onLifeStatus: (String) -> Unit,
    goal: String,
    onGoal: (String) -> Unit,
    conflict: String,
    onConflict: (String) -> Unit,
    location: String,
    onLocation: (String) -> Unit,
    realm: String,
    onRealm: (String) -> Unit,
    physical: String,
    onPhysical: (String) -> Unit,
    mental: String,
    onMental: (String) -> Unit,
    appearance: String,
    onAppearance: (String) -> Unit,
    personality: String,
    onPersonality: (String) -> Unit,
    background: String,
    onBackground: (String) -> Unit,
    abilities: String,
    onAbilities: (String) -> Unit,
    abilityState: String,
    onAbilityState: (String) -> Unit,
    assets: String,
    onAssets: (String) -> Unit,
    tracked: Boolean,
    onTracked: (Boolean) -> Unit,
    showProfile: Boolean,
    onToggleProfile: () -> Unit,
    profileJson: String,
    onProfileJson: (String) -> Unit,
) {
    Column(
        modifier = modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("身份", style = MaterialTheme.typography.titleSmall, color = SimingCinnabar)
        OutlinedTextField(name, onName, label = { Text("角色名") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        ChoiceRow("角色定位", roleOptions, roleType, onRoleType)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(age, onAge, label = { Text("年龄") }, singleLine = true, modifier = Modifier.weight(1f))
            Column(Modifier.weight(1.4f)) { ChoiceRow("生命状态", lifeOptions, lifeStatus, onLifeStatus) }
        }
        OutlinedTextField(aliases, onAliases, label = { Text("别名") }, placeholder = { Text("一行一个") }, minLines = 2, modifier = Modifier.fillMaxWidth())

        Text("现在正在发生什么", style = MaterialTheme.typography.titleSmall, color = SimingCinnabar)
        OutlinedTextField(goal, onGoal, label = { Text("当前目标") }, minLines = 2, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(conflict, onConflict, label = { Text("当前冲突") }, minLines = 2, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(location, onLocation, label = { Text("当前位置") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(realm, onRealm, label = { Text("境界 / 等级") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(physical, onPhysical, label = { Text("身体状态") }, minLines = 2, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(mental, onMental, label = { Text("心理状态") }, minLines = 2, modifier = Modifier.fillMaxWidth())

        Text("人物底色", style = MaterialTheme.typography.titleSmall, color = SimingCinnabar)
        OutlinedTextField(personality, onPersonality, label = { Text("性格") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(appearance, onAppearance, label = { Text("外貌") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(background, onBackground, label = { Text("背景") }, minLines = 4, modifier = Modifier.fillMaxWidth())

        Text("能力与资产", style = MaterialTheme.typography.titleSmall, color = SimingCinnabar)
        OutlinedTextField(abilities, onAbilities, label = { Text("能力") }, placeholder = { Text("一行一个") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(abilityState, onAbilityState, label = { Text("能力状态") }, minLines = 2, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(assets, onAssets, label = { Text("持有物 / 资产") }, minLines = 2, modifier = Modifier.fillMaxWidth())

        OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = SimingPaperWarm), modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("连续性追踪", fontWeight = FontWeight.SemiBold)
                Text("开启后，写章后的角色变化会继续进入 PC/手机共用的角色档案。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                AssistChip(
                    onClick = { onTracked(!tracked) },
                    label = { Text(if (tracked) "已开启持续追踪" else "未开启持续追踪") },
                    colors = AssistChipDefaults.assistChipColors(containerColor = if (tracked) MaterialTheme.colorScheme.primaryContainer else Color.White),
                )
            }
        }

        TextButton(onClick = onToggleProfile, modifier = Modifier.fillMaxWidth()) {
            Text(if (showProfile) "收起高级写作锁" else "高级：编辑稳定写作锁 JSON")
        }
        if (showProfile) {
            OutlinedTextField(
                value = profileJson,
                onValueChange = onProfileJson,
                label = { Text("稳定写作锁（JSON 对象）") },
                minLines = 5,
                modifier = Modifier.fillMaxWidth(),
            )
            Text("只有主动展开并修改时才覆盖 profile；日常编辑会原样保留 PC 数据。", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun CharacterReadingContent(character: ReplicaEntity, modifier: Modifier) {
    val name = character.formText("name").ifBlank { "未命名角色" }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 18.dp, top = 16.dp, end = 18.dp, bottom = 100.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(colors = CardDefaults.cardColors(containerColor = SimingPaperWarm), modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.size(58.dp)) {
                        Box(contentAlignment = Alignment.Center) { Text(characterInitial(name), fontSize = 22.sp, fontWeight = FontWeight.Bold, color = SimingCinnabar) }
                    }
                    Spacer(Modifier.width(13.dp))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                        Text(name, style = MaterialTheme.typography.headlineSmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            MicroTag(characterRoleLabel(character.formText("role_type")), SimingCinnabar)
                            val realm = character.formText("realm_or_level")
                            if (realm.isNotBlank()) MicroTag(realm, SimingBlue)
                        }
                        val aliases = character.formText("aliases")
                        if (aliases.isNotBlank()) Text("别名：${compactReferenceList(aliases)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        item {
            ReferenceSectionCard(
                title = "现在",
                rows = listOf(
                    "目标" to character.formText("current_goal"),
                    "冲突" to character.formText("active_conflict"),
                    "位置" to character.formText("current_location"),
                    "身体" to character.formText("physical_state"),
                    "心理" to character.formText("mental_state"),
                ),
            )
        }
        item {
            ReferenceSectionCard(
                title = "人物底色",
                rows = listOf(
                    "性格" to character.formText("personality"),
                    "外貌" to character.formText("appearance"),
                    "背景" to character.formText("background"),
                ),
            )
        }
        item {
            ReferenceSectionCard(
                title = "能力与持有物",
                rows = listOf(
                    "能力" to compactReferenceList(character.formText("abilities")),
                    "能力状态" to character.formText("abilities_state"),
                    "资产" to character.formText("items_or_assets"),
                ),
            )
        }
        item {
            OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = Color.White), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("连续性", fontWeight = FontWeight.SemiBold)
                    Text(
                        if (characterTracked(character)) "写章后持续追踪角色变化" else "当前未开启自动变化追踪",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    val profile = character.formText("profile")
                    if (profile.isNotBlank() && profile != "{}") Text("稳定写作锁已配置", style = MaterialTheme.typography.labelSmall, color = SimingGreen)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WorldDetailScreen(
    projectId: String,
    entry: ReplicaEntity?,
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onAdvanced: (() -> Unit)?,
) {
    val creating = entry == null
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    var editing by rememberSaveable(entry?.key) { mutableStateOf(creating) }
    var title by rememberSaveable(entry?.key) { mutableStateOf(entry?.formText("title").orEmpty()) }
    var dimension by rememberSaveable(entry?.key) { mutableStateOf(entry?.formText("dimension").orEmpty().ifBlank { "culture" }) }
    var content by rememberSaveable(entry?.key) { mutableStateOf(entry?.formText("content").orEmpty()) }
    var showMore by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }

    fun reset() {
        if (creating) {
            onBack()
            return
        }
        title = entry?.formText("title").orEmpty()
        dimension = entry?.formText("dimension").orEmpty().ifBlank { "culture" }
        content = entry?.formText("content").orEmpty()
        editing = false
    }

    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text(if (creating) "新设定" else if (editing) "编辑设定" else title.ifBlank { "未命名设定" }, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = { IconButton(onClick = if (editing) ::reset else onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                actions = { if (!creating && !editing) IconButton(onClick = { showMore = true }) { Icon(Icons.Outlined.MoreHoriz, "更多设定操作") } },
            )
        },
        bottomBar = {
            Surface(color = SimingPaperWarm, tonalElevation = 3.dp) {
                Row(
                    modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 14.dp, vertical = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (editing) {
                        OutlinedButton(onClick = ::reset, modifier = Modifier.weight(1f)) { Text(if (creating) "取消" else "放弃") }
                        Button(
                            enabled = title.isNotBlank() && content.isNotBlank(),
                            onClick = {
                                viewModel.saveRecord(
                                    projectId = projectId,
                                    entityType = "world",
                                    entityId = entry?.entityId,
                                    fields = linkedMapOf("title" to title.trim(), "dimension" to dimension, "content" to content),
                                    basePayload = entry?.payload(),
                                    onSaved = { if (creating) onBack() else editing = false },
                                )
                            },
                            modifier = Modifier.weight(1.35f),
                        ) {
                            Icon(Icons.Outlined.Save, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("保存")
                        }
                    } else {
                        OutlinedButton(onClick = { showMore = true }, modifier = Modifier.weight(1f)) { Text("历史") }
                        Button(onClick = { editing = true }, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Outlined.Edit, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("编辑")
                        }
                    }
                }
            }
        },
    ) { padding ->
        if (editing) {
            Column(
                modifier = Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedTextField(title, { title = it }, label = { Text("设定标题") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("归类", style = MaterialTheme.typography.labelMedium)
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    worldDimensionOptions.forEach { (value, label) ->
                        AssistChip(
                            onClick = { dimension = value },
                            label = { Text(label) },
                            colors = AssistChipDefaults.assistChipColors(containerColor = if (dimension == value) MaterialTheme.colorScheme.primaryContainer else Color.White),
                        )
                    }
                }
                OutlinedTextField(
                    value = content,
                    onValueChange = { content = it },
                    label = { Text("规则与内容") },
                    placeholder = { Text("写清楚什么成立、什么不成立，以及例外条件。") },
                    minLines = 18,
                    modifier = Modifier.fillMaxWidth(),
                    textStyle = MaterialTheme.typography.bodyLarge.copy(lineHeight = 27.sp),
                )
                Text("顺序字段由现有 canonical 数据保留，不在日常编辑中暴露。", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            LazyColumn(
                modifier = Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(start = 20.dp, top = 18.dp, end = 20.dp, bottom = 100.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Surface(shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.size(44.dp)) {
                            Box(contentAlignment = Alignment.Center) { Icon(Icons.Outlined.Hub, null, tint = SimingCinnabar) }
                        }
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text(title.ifBlank { "未命名设定" }, style = MaterialTheme.typography.headlineSmall)
                            Text(worldDimensionLabel(dimension), style = MaterialTheme.typography.labelMedium, color = SimingCinnabar)
                        }
                    }
                }
                item { HorizontalDivider() }
                item {
                    Text(
                        content,
                        style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 29.sp),
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }

    if (showMore && entry != null) {
        ModalBottomSheet(onDismissRequest = { showMore = false }) {
            Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 18.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("设定操作", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(vertical = 8.dp))
                TextButton(
                    enabled = connection != null && onAdvanced != null,
                    onClick = { showMore = false; onAdvanced?.invoke() },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(if (connection != null) "版本历史 / 时间线" else "历史需要连接 PC")
                    Spacer(Modifier.weight(1f))
                }
                TextButton(onClick = { showMore = false; showDelete = true }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(8.dp))
                    Text("删除设定", color = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.weight(1f))
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }

    if (showDelete && entry != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("删除“${title.ifBlank { "未命名设定" }}”？") },
            text = { Text("删除继续使用现有可靠同步与冲突保护。") },
            confirmButton = {
                TextButton(onClick = { showDelete = false; viewModel.deleteRecord(projectId, "world", entry.entityId, onBack) }) {
                    Text("确认删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun ChoiceRow(
    label: String,
    options: List<Pair<String, String>>,
    selected: String,
    onSelected: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            options.forEach { (value, text) ->
                AssistChip(
                    onClick = { onSelected(value) },
                    label = { Text(text) },
                    colors = AssistChipDefaults.assistChipColors(containerColor = if (selected == value) MaterialTheme.colorScheme.primaryContainer else Color.White),
                )
            }
        }
    }
}

@Composable
private fun ReferenceSectionCard(title: String, rows: List<Pair<String, String>>) {
    val visible = rows.filter { it.second.isNotBlank() }
    if (visible.isEmpty()) return
    OutlinedCard(colors = CardDefaults.outlinedCardColors(containerColor = Color.White), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            visible.forEachIndexed { index, (label, value) ->
                if (index > 0) HorizontalDivider()
                Text(label, style = MaterialTheme.typography.labelSmall, color = SimingInkMuted)
                Text(value, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun ReferenceSyncIcon(record: ReplicaEntity) {
    when {
        record.conflicted -> Icon(Icons.Outlined.ErrorOutline, "存在版本分岔", tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(18.dp))
        record.dirty -> Icon(Icons.Outlined.CloudQueue, "等待同步", tint = SimingBlue, modifier = Modifier.size(18.dp))
        else -> Icon(Icons.Outlined.CheckCircle, "已同步", tint = SimingGreen, modifier = Modifier.size(18.dp))
    }
}

internal fun characterRoleLabel(raw: String): String = roleOptions.firstOrNull { it.first == raw }?.second ?: when {
    raw.isBlank() -> "配角"
    else -> raw
}

internal fun worldDimensionLabel(raw: String): String = worldDimensionOptions.firstOrNull { it.first == raw }?.second ?: when {
    raw.isBlank() -> "文化"
    else -> raw
}

internal fun characterPrimarySummary(character: ReplicaEntity): String = listOf(
    character.formText("current_goal"),
    character.formText("active_conflict"),
    character.formText("personality"),
    character.formText("background"),
).firstOrNull { it.isNotBlank() }.orEmpty()

internal fun characterTracked(character: ReplicaEntity): Boolean =
    character.formText("is_evolution_tracked").trim().lowercase() !in setOf("false", "0", "no", "off", "否")

internal fun compactReferenceList(raw: String): String = raw
    .replace("[", "")
    .replace("]", "")
    .replace("\"", "")
    .split('\n', ',', '，', '、')
    .map(String::trim)
    .filter(String::isNotBlank)
    .joinToString(" · ")

internal fun referenceSnippet(raw: String, limit: Int = 120): String {
    val normalized = raw.replace(Regex("\\s+"), " ").trim()
    return if (normalized.length <= limit) normalized else normalized.take(limit).trimEnd() + "…"
}

private fun characterInitial(name: String): String = name.trim().take(1).ifBlank { "角" }
