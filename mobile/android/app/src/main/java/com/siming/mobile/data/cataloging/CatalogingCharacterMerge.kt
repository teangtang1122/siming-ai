package com.siming.mobile.data.cataloging

import kotlinx.serialization.json.*

/** Entity identities are supplied by the model's bindings, never detected by name similarity. */
internal fun CatalogingProjection.mergeCharacter(p: JsonObject) {
    val primaryId = plan.binding(p.text("primary_name")).text("id")
    val secondaryId = plan.binding(p.text("secondary_name")).text("id")
    val primary = get("character", primaryId)
    val secondary = get("character", secondaryId)
    val reason = p.text("reason").ifBlank { p.text("evidence") }
    val aliases = (primary.strings("aliases") + secondary.strings("aliases") + secondary.text("name") + p.strings("aliases")).distinct().filter { it != primary.text("name") }
    val fields = mutableMapOf<String, JsonElement>()
    listOf("appearance", "personality", "background", "items_or_assets", "abilities_state", "active_conflict").forEach { field ->
        fields[field] = JsonPrimitive(mergeCatalogText(primary.text(field), secondary.text(field), chapter.text("title"), if (field in setOf("appearance", "personality")) 8000 else 4000))
    }
    val note = p.text("background_append").ifBlank { "身份合并记录：${secondary.text("name")} 被确认或高度疑似为 ${primary.text("name")} 的另一身份。已知称呼：${aliases.joinToString(", ")}。依据：${reason.ifBlank { "用户确认" }}。" }
    fields["background"] = JsonPrimitive(mergeCatalogText((fields["background"] as JsonPrimitive).content, note, chapter.text("title"), 4000))
    fields["aliases"] = jsonStrings(aliases)
    fields["abilities"] = jsonStrings((primary.strings("abilities") + secondary.strings("abilities")).distinct())
    listOf("life_status", "current_location", "realm_or_level", "physical_state", "mental_state", "current_goal").forEach { field ->
        if (primary.text(field).isBlank() && secondary.text(field).isNotBlank()) fields[field] = secondary.getValue(field)
    }
    put("character", "character", primaryId, JsonObject(fields))
    put("character", "character", secondaryId, buildJsonObject {
        put("role_type", "merged_alias"); put("current_goal", ""); put("active_conflict", "")
        put("background", mergeCatalogText(secondary.text("background"), "该角色卡已作为重复身份合并到“${primary.text("name")}”。合并依据：$reason", chapter.text("title"), 3000))
    })
    aliases.forEach { alias ->
        val old = find("character_alias") { it.text("character_id") == primaryId && it.text("alias") == alias }
        put("character_alias", "character_alias", old?.id ?: id("merge_alias", primaryId, alias), buildJsonObject {
            put("character_id", primaryId); put("alias", alias); put("alias_type", if (alias == secondary.text("name")) "merged_identity" else "alias")
            put("source_chapter_id", chapterId); put("description", reason)
        })
    }
    ofType("outline_node").toList().forEach { outline ->
        val links = outline.payload.objects("linked_characters")
        if (links.any { it.text("id") == secondaryId }) {
            val mapped = links.map { if (it.text("id") == secondaryId) JsonObject(it + mapOf("id" to JsonPrimitive(primaryId), "name" to JsonPrimitive(primary.text("name")))) else it }.distinctBy { it.text("id") }
            put("outline", "outline_node", outline.id, buildJsonObject { put("linked_characters", JsonArray(mapped)) })
        }
    }
    ofType("chapter").toList().forEach { source ->
        val links = source.payload.objects("characters")
        if (links.any { it.text("character_id") == secondaryId }) {
            val mapped = links.map { if (it.text("character_id") == secondaryId) JsonObject(it + ("character_id" to JsonPrimitive(primaryId))) else it }.distinctBy { it.text("character_id") }
            put("chapter", "chapter", source.id, buildJsonObject { put("characters", JsonArray(mapped)) })
        }
    }
    ofType("character_relationship").toList().forEach { row ->
        val from = row.payload.text("from").let { if (it == secondaryId) primaryId else it }
        val to = row.payload.text("to").let { if (it == secondaryId) primaryId else it }
        if (from == to) rows.remove(row.entityType to row.id)
        else if (from != row.payload.text("from") || to != row.payload.text("to")) {
            val duplicate = find("character_relationship") { it.text("id") != row.id && it.text("from") == from && it.text("to") == to && it.text("relationship_type") == row.payload.text("relationship_type") }
            if (duplicate != null) {
                put("character_relation", "character_relationship", duplicate.id, buildJsonObject {
                    put("description", mergeCatalogText(duplicate.payload.text("description"), row.payload.text("description"), chapter.text("title"), 4000))
                })
                rows.remove(row.entityType to row.id)
            } else put(row.entityType, row.recordType, row.id, buildJsonObject { put("from", from); put("to", to) })
        }
    }
    rows.values.filter { it.recordType in setOf("character_timeline", "character_change", "character_alias") && it.payload.text("character_id") == secondaryId }.toList().forEach {
        put(it.entityType, it.recordType, it.id, buildJsonObject { put("character_id", primaryId) })
    }
    val secondaryConfig = find("character_ai_config") { it.text("character_id") == secondaryId }
    val old = find("character_ai_config") { it.text("character_id") == primaryId }
    if (secondaryConfig != null || old != null) {
        val config = secondaryConfig?.payload.orEmpty().filterKeys { it in CatalogingProjection.CONFIG_FIELDS || it == "model_override" }.toMutableMap()
        old?.payload?.filterKeys { it in CatalogingProjection.CONFIG_FIELDS }?.filterValues { it != JsonNull && it != JsonPrimitive("") }?.let(config::putAll)
        val prompt = mergeCatalogText(old?.payload?.text("custom_system_prompt").orEmpty(), secondaryConfig?.payload?.text("custom_system_prompt").orEmpty(), chapter.text("title"), 12000)
        config["custom_system_prompt"] = JsonPrimitive(mergeCatalogText(prompt, "身份合并：该角色可能使用过这些称呼或身份：${aliases.joinToString(", ")}。扮演时必须保持这些经历的一致性。", chapter.text("title"), 12000))
        config["catchphrases"] = jsonStrings((old?.payload?.strings("catchphrases").orEmpty() + secondaryConfig?.payload?.strings("catchphrases").orEmpty()).distinct())
        config["character_id"] = JsonPrimitive(primaryId)
        put("character_ai_config", "character_ai_config", old?.id ?: id("merged_config", primaryId), JsonObject(config))
    }
    for (identity in listOf(primaryId, secondaryId)) {
        val old = get("character", identity)
        val saved = put("character", "character", identity, buildJsonObject {
            put("current_version", old.number("current_version", 1) + 1); put("last_updated_chapter_id", chapterId); put("last_seen_chapter_id", chapterId)
        }).payload
        put("character", "character_version", id("merge_version", identity), buildJsonObject {
            put("character_id", identity); put("version_number", saved.number("current_version")); put("snapshot_data", characterSnapshot(saved))
            put("source_chapter_id", chapterId); put("change_summary", "身份合并：${secondary.text("name")} → ${primary.text("name")}")
        })
    }
}
