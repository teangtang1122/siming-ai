package com.siming.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.serialization.json.*

/** Edits the existing creation payload without translating or inventing business fields. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CreationArtifactEditor(
    title: String, initial: JsonObject, busy: Boolean,
    onDismiss: () -> Unit, onSave: (JsonObject) -> Unit,
) {
    var value by rememberSaveable { mutableStateOf(initial.toString()) }
    var rawMode by rememberSaveable { mutableStateOf(false) }
    var raw by rememberSaveable { mutableStateOf("") }
    var discard by rememberSaveable { mutableStateOf(false) }
    val errors = remember { mutableStateMapOf<String, Boolean>() }
    val inputs = remember { mutableStateMapOf<String, String>() }
    val data = remember(value) { Json.parseToJsonElement(value).jsonObject }
    val rawData = remember(raw) { runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull() }
    val dirty = value != initial.toString() || (rawMode && rawData != data)
    fun leave() { if (!busy) { if (dirty) discard = true else onDismiss() } }
    Dialog(onDismissRequest = ::leave, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        BackHandler(onBack = ::leave)
        Scaffold(
            modifier = Modifier.fillMaxSize().safeDrawingPadding().imePadding(),
            containerColor = SimingPaper,
            topBar = {
                TopAppBar(title = { Text("编辑$title", maxLines = 1) },
                    navigationIcon = { IconButton(onClick = ::leave) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回资料") } })
            },
            bottomBar = {
                Surface {
                    Button(onClick = { onSave(if (rawMode) requireNotNull(rawData) else data) },
                        enabled = !busy && (if (rawMode) rawData != null else errors.values.none { it }),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) { Text("保存修改") }
                }
            },
        ) { padding ->
            Column(Modifier.padding(padding).consumeWindowInsets(padding).fillMaxSize()
                .verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("保存修改后仍需确认；受影响的后续资料会提示复核。", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (rawMode) {
                    OutlinedTextField(raw, { raw = it }, modifier = Modifier.fillMaxWidth(), minLines = 8,
                        label = { Text("高级结构编辑") }, isError = rawData == null,
                        supportingText = { if (rawData == null) Text("请输入有效的 JSON 对象。") }, enabled = !busy)
                } else {
                    CreationFormObject(data, "root", !busy, inputs,
                        onChange = { value = it.toString() }, onError = { path, invalid -> errors[path] = invalid })
                }
                TextButton(onClick = {
                    if (rawMode) {
                        rawData?.let { value = it.toString(); errors.clear(); inputs.clear(); rawMode = false }
                    } else {
                        raw = Json { prettyPrint = true }.encodeToString(JsonObject.serializer(), data)
                        rawMode = true
                    }
                }, enabled = !busy && (rawMode || errors.values.none { it })) {
                    Text(if (rawMode) "返回表单" else "高级：编辑原始结构")
                }
            }
        }
        if (discard) AlertDialog(onDismissRequest = { discard = false },
            title = { Text("放弃未保存的修改？") },
            confirmButton = { TextButton(onClick = onDismiss) { Text("放弃修改") } },
            dismissButton = { TextButton(onClick = { discard = false }) { Text("继续编辑") } })
    }
}

@Composable
private fun CreationFormObject(
    value: JsonObject, path: String, enabled: Boolean, inputs: MutableMap<String, String>,
    onChange: (JsonObject) -> Unit, onError: (String, Boolean) -> Unit,
) {
    value.forEach { (key, child) ->
        key("$path.$key") {
            CreationFormField(key, child, "$path.$key", enabled, inputs,
                onChange = { replacement -> onChange(JsonObject(value + (key to replacement))) }, onError = onError)
        }
    }
}

@Composable
private fun CreationFormField(
    field: String, value: JsonElement, path: String, enabled: Boolean, inputs: MutableMap<String, String>,
    onChange: (JsonElement) -> Unit, onError: (String, Boolean) -> Unit,
) {
    val label = creationFieldLabel(field)
    when {
        value == JsonNull -> Text("$label：未设置", style = MaterialTheme.typography.bodySmall)
        value is JsonObject -> {
            FormDisclosure(label) {
                CreationFormObject(value, path, enabled, inputs, onChange = onChange, onError = onError)
            }
        }
        value is JsonArray && value.isNotEmpty() && value.all { it is JsonPrimitive && it.isString } -> {
            val text = inputs[path] ?: value.joinToString("\n") { it.jsonPrimitive.content }
            OutlinedTextField(text, { next ->
                inputs[path] = next
                onChange(JsonArray(next.lines().filter(String::isNotBlank).map(::JsonPrimitive)))
            }, label = { Text(label) }, supportingText = { Text("每行一项") }, minLines = 2,
                modifier = Modifier.fillMaxWidth(), enabled = enabled)
        }
        value is JsonArray -> FormDisclosure("$label · ${value.size} 项") {
            value.forEachIndexed { index, item ->
                CreationFormField("${index + 1}", item, "$path.$index", enabled, inputs,
                    onChange = { replacement -> onChange(JsonArray(value.toMutableList().also { it[index] = replacement })) },
                    onError = onError)
            }
        }
        value is JsonPrimitive && !value.isString && value.booleanOrNull != null -> {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                Switch(checked = value.boolean, onCheckedChange = { onChange(JsonPrimitive(it)) }, enabled = enabled)
            }
        }
        value is JsonPrimitive -> {
            val text = inputs[path] ?: value.content
            val parsed = if (value.isString) JsonPrimitive(text) else parseCreationNumber(text)
            val identity = field == "id" || field.endsWith("_id") || field.endsWith("_key")
            OutlinedTextField(text, { next ->
                inputs[path] = next
                val nextValue = if (value.isString) JsonPrimitive(next) else parseCreationNumber(next)
                onError(path, nextValue == null)
                nextValue?.let(onChange)
            }, label = { Text(label) }, modifier = Modifier.fillMaxWidth(), enabled = enabled,
                readOnly = identity, isError = parsed == null, minLines = if (text.length > 80) 3 else 1,
                supportingText = if (parsed == null) ({ Text("请输入数字") }) else null)
        }
    }
}

internal fun parseCreationNumber(text: String): JsonPrimitive? =
    runCatching { Json.parseToJsonElement(text) as? JsonPrimitive }.getOrNull()
        ?.takeIf { !it.isString && it.doubleOrNull?.isFinite() == true }

@Composable
private fun FormDisclosure(title: String, content: @Composable ColumnScope.() -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    OutlinedCard(Modifier.fillMaxWidth()) {
        TextButton(onClick = { expanded = !expanded }, modifier = Modifier.fillMaxWidth()) {
            Text(title, modifier = Modifier.weight(1f))
            Text(if (expanded) "收起" else "展开")
        }
        if (expanded) Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}
