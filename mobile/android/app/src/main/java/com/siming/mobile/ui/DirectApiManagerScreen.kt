package com.siming.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.siming.mobile.data.network.DirectApiConfig

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
internal fun DirectApiManagerScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onConfigured: () -> Unit,
    snackbar: SnackbarHostState,
) {
    val ui by viewModel.uiState
    var editing by rememberSaveable { mutableStateOf<String?>(null) }
    var adding by rememberSaveable { mutableStateOf(false) }
    var deleting by rememberSaveable { mutableStateOf<String?>(null) }
    var taskPicker by rememberSaveable { mutableStateOf<String?>(null) }
    val profiles = ui.directApiProfiles
    if (editing != null || adding) {
        key(editing ?: "new-api") {
            DirectApiProfileEditor(viewModel, profiles.firstOrNull { it.id == editing },
                onBack = { editing = null; adding = false },
                onConfigured = { editing = null; adding = false }, snackbar = snackbar)
        }
        return
    }
    deleting?.let { id ->
        val entry = profiles.firstOrNull { it.id == id }
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text("删除 API 配置？") },
            text = { Text("删除 ${entry?.displayName.orEmpty()} 的地址和加密凭据。作品及对话保留；被任务使用的 API 需要先解除绑定。") },
            confirmButton = { TextButton(onClick = { viewModel.deleteDirectApi(id); deleting = null }) { Text("删除") } },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("取消") } })
    }
    taskPicker?.let { task ->
        AlertDialog(onDismissRequest = { taskPicker = null }, title = { Text("选择${DirectApiConfig.taskModelLabels[task]}的 API 和模型") },
            text = {
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 420.dp)) {
                    item { TextButton(onClick = { viewModel.bindDirectApiTask(task, null, null); taskPicker = null }) { Text("跟随默认 API 和默认模型") } }
                    profiles.forEach { profile ->
                        item(key = "api:${profile.id}") { Text(profile.displayName, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp)) }
                        items((listOf(profile.model) + profile.availableModels).distinct(), key = { "${profile.id}:$it" }) { model ->
                            TextButton(onClick = { viewModel.bindDirectApiTask(task, profile.id, model); taskPicker = null }) {
                                Text(model, maxLines = 2)
                            }
                        }
                    }
                }
            }, confirmButton = { TextButton(onClick = { taskPicker = null }) { Text("取消") } })
    }
    Scaffold(topBar = {
        CenterAlignedTopAppBar(title = { Text("手机 API 管理") }, navigationIcon = {
            IconButton(onClick = onBack, enabled = !ui.busy) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") }
        })
    }, snackbarHost = { SnackbarHost(snackbar) }) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Text("每组 API 独立保存地址、密钥、协议、模型和容量。默认 API 用于未单独指定的任务。")
                Button(onClick = { viewModel.resetDirectModelDiscovery(); adding = true }, enabled = !ui.busy,
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) { Text("新增 API") }
            }
            if (profiles.isEmpty()) item { Text("尚未保存 API，点击上方新增。") }
            items(profiles, key = { it.id }) { profile ->
                OutlinedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(profile.displayName + if (profile.id == ui.directApi?.id) " · 默认" else "", style = MaterialTheme.typography.titleMedium)
                        Text(profile.model)
                        Text(profile.baseUrl, style = MaterialTheme.typography.bodySmall)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            TextButton(onClick = { viewModel.resetDirectModelDiscovery(); editing = profile.id }, enabled = !ui.busy) { Text("编辑") }
                            TextButton(onClick = { viewModel.testDirectApiProfile(profile.id) }, enabled = !ui.busy) { Text("测试") }
                            if (profile.id != ui.directApi?.id) TextButton(onClick = { viewModel.selectDirectApi(profile.id) }, enabled = !ui.busy) { Text("设为默认") }
                            TextButton(onClick = { deleting = profile.id }, enabled = !ui.busy) { Text("删除") }
                        }
                    }
                }
            }
            if (profiles.isNotEmpty()) {
                item { HorizontalDivider(); Text("按任务选择 API 和模型", style = MaterialTheme.typography.titleMedium) }
                DirectApiConfig.taskModelLabels.forEach { (task, label) ->
                    item(key = "task:$task") {
                        val selected = profiles.firstOrNull { it.id == ui.directApiTaskProfiles[task] }
                        OutlinedButton(onClick = { taskPicker = task }, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) {
                            Column(Modifier.fillMaxWidth()) {
                                Text(label)
                                Text(selected?.let { "${it.displayName} · ${it.taskModels[task] ?: it.model}" }
                                    ?: "跟随默认 · ${ui.directApi?.displayName.orEmpty()}", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
                item { Button(onClick = onConfigured, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) { Text("完成") } }
            }
            if (ui.busy) item { LinearProgressIndicator(Modifier.fillMaxWidth()); Text(ui.activity) }
            ui.error?.let { message -> item { Text(message, color = MaterialTheme.colorScheme.error) } }
        }
    }
}
