from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/network/PcApiPayloads.kt")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''        val values = if (entityType in coreAuthoringTypes) {\n            canonicalFields(entityType, source).also { normalizeCore(entityType, source, it) }\n        } else {\n            linkedMapOf<String, JsonElement>().apply {\n                PcAuthoringContract.writableKeys(entityType).forEach { key ->\n                    source[key]?.let { put(key, it) }\n                }\n            }\n        }\n''',
    '''        val values = if (entityType in coreAuthoringTypes) {\n            canonicalFields(entityType, source).also { normalizeCore(entityType, source, it) }\n        } else {\n            linkedMapOf<String, JsonElement>().apply {\n                PcAuthoringContract.writableKeys(entityType).forEach { key ->\n                    source[key]?.let { put(key, it) }\n                }\n            }.also { normalizeAuxiliary(entityType, source, it) }\n        }\n''',
    "auxiliary normalization hook",
)
anchor = '''    private fun normalizeProject(values: MutableMap<String, JsonElement>) {\n'''
insert = '''    private fun normalizeAuxiliary(\n        entityType: String,\n        source: JsonObject,\n        values: MutableMap<String, JsonElement>,\n    ) {\n        when (entityType) {\n            "character_relation" -> {\n                if (values["from"] == null) source["character_a_id"]?.let { values["from"] = it }\n                if (values["to"] == null) source["character_b_id"]?.let { values["to"] = it }\n            }\n            "character_ai_config" -> values.normalizeStringArray("catchphrases")\n            "world_relation" -> values.normalizeJsonObject("metadata_json", "世界观关系 metadata")\n        }\n    }\n\n'''
if anchor not in text:
    raise RuntimeError("normalizeProject anchor missing")
text = text.replace(anchor, insert + anchor, 1)
text = replace_once(
    text,
    '''        "character" -> "character"\n        "world" -> "world_entry"\n''',
    '''        "character" -> "character"\n        "character_relation" -> "character_relationship"\n        "character_ai_config" -> "character_ai_config"\n        "world" -> "world_entry"\n        "world_relation" -> "world_relationship"\n''',
    "auxiliary record types",
)

path.write_text(text, encoding="utf-8")
