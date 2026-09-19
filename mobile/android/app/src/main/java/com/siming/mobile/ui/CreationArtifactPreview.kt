package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

@Composable
internal fun EmptyArtifact(label: String) {
    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        Text("$label 还没有内容", fontWeight = FontWeight.SemiBold)
        Text(
            "可以直接生成，也可以回到对话中先补充这一部分的要求。生成结果不会自动确认。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
internal fun ConceptSelector(
    data: JsonObject,
    selectedId: String,
    onSelect: (String) -> Unit,
    enabled: Boolean,
) {
    val options = (data["options"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        options.forEachIndexed { index, concept ->
            val id = concept.string("id").ifBlank { "concept-${index + 1}" }
            OutlinedCard(
                onClick = { onSelect(id) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(17.dp),
                border = BorderStroke(
                    if (id == selectedId) 1.8.dp else 1.dp,
                    if (id == selectedId) SimingCinnabar else MaterialTheme.colorScheme.outlineVariant,
                ),
                colors = CardDefaults.outlinedCardColors(
                    containerColor = if (id == selectedId) Color(0xFFFFF4EF) else Color.White,
                ),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(concept.string("title").ifBlank { "创意方向 ${index + 1}" }, fontWeight = FontWeight.Bold, fontSize = 17.sp, modifier = Modifier.weight(1f))
                        if (id == selectedId) {
                            Surface(color = SimingCinnabar.copy(alpha = 0.12f), shape = RoundedCornerShape(10.dp)) {
                                Text("已选择", color = SimingCinnabar, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
                            }
                        }
                    }
                    concept.string("subtitle").takeIf(String::isNotBlank)?.let {
                        Text(it, color = SimingCinnabar, style = MaterialTheme.typography.labelMedium)
                    }
                    Text(concept.string("logline").ifBlank { "暂无一句话梗概" }, lineHeight = 21.sp)
                    val protagonist = concept.objectValue("protagonist_seed")
                    if (protagonist.isNotEmpty()) {
                        Text(
                            "主角：${protagonist.string("name").ifBlank { "待定" }} · ${protagonist.string("identity").ifBlank { "身份待定" }}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    concept.string("core_conflict").takeIf(String::isNotBlank)?.let {
                        Text("核心冲突：$it", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        if (options.isEmpty()) EmptyArtifact("创意方向")
    }
}

@Composable
internal fun ArtifactPreview(data: JsonObject) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        data.entries.forEach { (key, value) ->
            if (key !in setOf("selected_concept_id")) {
                ArtifactField(fieldLabel(key), value, depth = 0)
            }
        }
    }
}

@Composable
private fun ArtifactField(label: String, value: JsonElement, depth: Int) {
    when (value) {
        JsonNull -> Unit
        is JsonPrimitive -> {
            val text = value.contentOrNull.orEmpty()
            if (text.isNotBlank()) {
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                    SelectionContainer { Text(text, lineHeight = 21.sp) }
                }
            }
        }
        is JsonArray -> {
            var visibleCount by rememberSaveable(label, value.size) { mutableStateOf(24) }
            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("$label（${value.size}）", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                if (value.isEmpty()) {
                    Text("暂无内容", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    value.take(visibleCount).forEachIndexed { index, item ->
                        when (item) {
                            is JsonObject -> ObjectPreviewCard(item, index, depth + 1)
                            is JsonPrimitive -> Text("• ${item.contentOrNull.orEmpty()}", lineHeight = 21.sp)
                            else -> Text(item.toString(), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (value.size > visibleCount) {
                        TextButton(onClick = { visibleCount += 24 }) {
                            Text("继续查看（还有 ${value.size - visibleCount} 项）")
                        }
                    }
                }
            }
        }
        is JsonObject -> ObjectPreviewCard(value, 0, depth + 1, label)
    }
}

@Composable
private fun ObjectPreviewCard(
    value: JsonObject,
    index: Int,
    depth: Int,
    fallbackLabel: String = "",
) {
    val title = listOf("title", "name", "label", "id")
        .firstNotNullOfOrNull { key -> value.string(key).takeIf(String::isNotBlank) }
        ?: fallbackLabel.ifBlank { "条目 ${index + 1}" }
    var showAll by rememberSaveable(title, depth) { mutableStateOf(false) }
    val fields = value.entries.filterNot { (key, child) ->
        key in setOf("title", "name", "label", "id") || child == JsonNull ||
            (child is JsonPrimitive && child.contentOrNull.isNullOrBlank())
    }
    OutlinedCard(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            fields.take(if (showAll) fields.size else 3)
                .forEach { (key, child) ->
                    when (child) {
                        JsonNull -> Unit
                        is JsonPrimitive -> child.contentOrNull?.takeIf(String::isNotBlank)?.let {
                            Text("${fieldLabel(key)}：${fieldValueLabel(key, it)}", style = MaterialTheme.typography.bodySmall, lineHeight = 19.sp)
                        }
                        else -> {
                            var expanded by rememberSaveable(title, key) { mutableStateOf(false) }
                            TextButton(onClick = { expanded = !expanded }) {
                                Text("${if (expanded) "收起" else "查看"}${fieldLabel(key)}")
                            }
                            if (expanded) {
                                if (depth < 4) ArtifactField(fieldLabel(key), child, depth + 1)
                                else SelectionContainer { Text(child.toString(), style = MaterialTheme.typography.bodySmall) }
                            }
                        }
                    }
                }
            if (fields.size > 3) {
                TextButton(onClick = { showAll = !showAll }) {
                    Text(if (showAll) "收起资料" else "展开完整资料（${fields.size} 项）")
                }
            }
        }
    }
}


private fun fieldLabel(key: String): String = mapOf(
    "brief" to "创作核心",
    "genre" to "作品类型",
    "target_audience" to "目标读者",
    "platform" to "发布平台",
    "target_words" to "目标字数",
    "target_chapters" to "目标章节数",
    "world_tone" to "世界基调",
    "story_structure" to "故事结构",
    "pacing" to "叙事节奏",
    "special_requirements" to "必须遵守",
    "avoid" to "必须避免",
    "author_overrides" to "作者覆盖项",
    "options" to "创意方案",
    "title" to "标题",
    "subtitle" to "差异化定位",
    "logline" to "一句话梗概",
    "protagonist_seed" to "主角种子",
    "world_hook" to "世界钩子",
    "core_conflict" to "核心冲突",
    "story_engine" to "持续推进机制",
    "opening_hook" to "开篇钩子",
    "writing_style" to "正文风格",
    "style_rules" to "文风规则",
    "forbidden_patterns" to "禁用表达",
    "worldbuilding" to "世界观设定",
    "characters" to "角色",
    "relationships" to "人物关系",
    "entries" to "地点与势力",
    "relations" to "结构关系",
    "volumes" to "分卷规划",
    "chapters" to "章节细纲",
    "sections" to "场景事件",
    "summary" to "摘要",
    "planned_summary" to "计划摘要",
    "ready" to "是否可建档",
    "warnings" to "提醒",
    "blocking" to "阻断项",
    "counts" to "对象统计",
    "goal" to "目标",
    "current_goal" to "当前目标",
    "weakness" to "弱点",
    "conflict" to "矛盾",
    "background" to "背景经历",
    "current_location" to "当前位置",
    "role_type" to "角色定位",
    "profile" to "人物设定",
    "age" to "年龄",
    "appearance" to "外貌",
    "status" to "当前状态",
    "core_motivation" to "核心动机",
    "inner_lack" to "内在缺口",
    "core_belief" to "核心信念",
    "public_persona" to "外在人设",
    "hidden_persona" to "隐藏面貌",
    "reveal_chapter" to "揭示章节",
    "moral_taboo" to "道德底线",
    "voice" to "说话方式",
    "action_habit" to "行为习惯",
    "trauma_trigger" to "创伤触发点",
    "source" to "关系起点",
    "target" to "关系对象",
    "type" to "类型",
    "description" to "说明",
    "dimension" to "设定类别",
    "content" to "内容",
    "story_overview" to "全书主线",
    "volume_id" to "所属卷",
    "volume_key" to "所属卷标识",
    "volume_title" to "所属卷名",
    "chapter_number" to "章节序号",
    "chapter_start" to "起始章节",
    "chapter_end" to "结束章节",
    "character_ids" to "关联角色",
    "section_type" to "场景类型",
    "plot_points" to "剧情要点",
    "writing_constraints" to "写作约束",
)[key] ?: key.replace('_', ' ')

private fun fieldValueLabel(key: String, value: String): String = when (key) {
    "role_type" -> mapOf("protagonist" to "主角", "supporting" to "配角", "antagonist" to "对立角色", "minor" to "次要角色")[value] ?: value
    else -> value
}

private fun JsonObject.objectValue(name: String): JsonObject =
    get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
