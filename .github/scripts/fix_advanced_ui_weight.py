from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/AdvancedAuthoringDialogs.kt")
text = path.read_text(encoding="utf-8")
text = text.replace("import androidx.compose.foundation.layout.weight\n", "")
old = '''                            Text(\n                                chapter.text("title").ifBlank { "未命名章节" },\n                                modifier = Modifier.weight(1f),\n                            )\n'''
new = '''                            Text(\n                                chapter.text("title").ifBlank { "未命名章节" },\n                                modifier = Modifier.fillMaxWidth(0.58f),\n                            )\n'''
if text.count(old) != 1:
    raise RuntimeError(f"chapter title weight anchor mismatch: {text.count(old)}")
text = text.replace(old, new, 1)
old = '''                Row(verticalAlignment = Alignment.CenterVertically) {\n                    Text(relation.targetName.ifBlank { relation.targetId }, Modifier.weight(1f), fontWeight = FontWeight.SemiBold)\n                    TextButton(onClick = {\n                        onRelationsChanged(relations.filterIndexed { itemIndex, _ -> itemIndex != index })\n                    }) { Text("移除") }\n                }\n'''
new = '''                Text(\n                    relation.targetName.ifBlank { relation.targetId },\n                    fontWeight = FontWeight.SemiBold,\n                )\n                TextButton(onClick = {\n                    onRelationsChanged(relations.filterIndexed { itemIndex, _ -> itemIndex != index })\n                }) { Text("移除") }\n'''
if text.count(old) != 1:
    raise RuntimeError(f"relationship weight anchor mismatch: {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
