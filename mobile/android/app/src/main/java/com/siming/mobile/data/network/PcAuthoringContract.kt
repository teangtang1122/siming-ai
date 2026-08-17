package com.siming.mobile.data.network

/**
 * Writable field contract published by the PC authoring APIs.
 *
 * Android UI, online requests, and offline mutation projection must all use
 * this table instead of inventing independent mobile entity shapes.
 */
internal enum class PcFieldKind {
    Text,
    Multiline,
    Integer,
    Boolean,
    StringArray,
    JsonObject,
    JsonArray,
}

internal data class PcFieldSpec(
    val key: String,
    val kind: PcFieldKind = PcFieldKind.Text,
    val mobileEditable: Boolean = true,
)

internal object PcAuthoringContract {
    private val specs = mapOf(
        "project" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("description", PcFieldKind.Multiline),
            PcFieldSpec("tags", PcFieldKind.StringArray),
            PcFieldSpec("narrative_perspective"),
            PcFieldSpec("writing_style"),
            PcFieldSpec("forbidden_sentence_patterns", PcFieldKind.Multiline),
            PcFieldSpec("rhetoric_guidelines", PcFieldKind.Multiline),
            PcFieldSpec("short_sentences", PcFieldKind.Boolean),
            PcFieldSpec("custom_style_prompt", PcFieldKind.Multiline),
            PcFieldSpec("daily_word_goal", PcFieldKind.Integer),
        ),
        "chapter" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("outline_node_id"),
            PcFieldSpec("content", PcFieldKind.Multiline),
            // Auditable AI context is preserved across mobile edits, but it is
            // not a free-form authoring field in the Android editor.
            PcFieldSpec("context_manifest_id", mobileEditable = false),
        ),
        "outline" to listOf(
            PcFieldSpec("parent_id"),
            PcFieldSpec("node_type"),
            PcFieldSpec("title"),
            PcFieldSpec("summary", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("sort_order", PcFieldKind.Integer),
            PcFieldSpec("character_ids", PcFieldKind.StringArray, mobileEditable = false),
            PcFieldSpec("characters", PcFieldKind.JsonArray),
            PcFieldSpec("metadata", PcFieldKind.JsonObject),
        ),
        "character" to listOf(
            PcFieldSpec("name"),
            PcFieldSpec("appearance", PcFieldKind.Multiline),
            PcFieldSpec("role_type"),
            PcFieldSpec("personality", PcFieldKind.Multiline),
            PcFieldSpec("background", PcFieldKind.Multiline),
            PcFieldSpec("abilities", PcFieldKind.StringArray),
            PcFieldSpec("aliases", PcFieldKind.StringArray),
            PcFieldSpec("age"),
            PcFieldSpec("life_status"),
            PcFieldSpec("current_location"),
            PcFieldSpec("realm_or_level"),
            PcFieldSpec("physical_state", PcFieldKind.Multiline),
            PcFieldSpec("mental_state", PcFieldKind.Multiline),
            PcFieldSpec("current_goal", PcFieldKind.Multiline),
            PcFieldSpec("active_conflict", PcFieldKind.Multiline),
            PcFieldSpec("abilities_state", PcFieldKind.Multiline),
            PcFieldSpec("items_or_assets", PcFieldKind.Multiline),
            PcFieldSpec("profile", PcFieldKind.JsonObject),
            PcFieldSpec("is_evolution_tracked", PcFieldKind.Boolean),
            PcFieldSpec("change_summary", PcFieldKind.Multiline),
        ),
        "world" to listOf(
            PcFieldSpec("dimension"),
            PcFieldSpec("title"),
            PcFieldSpec("content", PcFieldKind.Multiline),
            PcFieldSpec("sort_order", PcFieldKind.Integer),
        ),
        "foreshadowing" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("description", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("importance"),
            PcFieldSpec("storyline"),
            PcFieldSpec("source_chapter_id"),
            PcFieldSpec("target_chapter_id"),
            PcFieldSpec("target_chapter_number", PcFieldKind.Integer),
            PcFieldSpec("resolved_chapter_id"),
            PcFieldSpec("evidence", PcFieldKind.Multiline),
            PcFieldSpec("resolution_note", PcFieldKind.Multiline),
            PcFieldSpec("resolution_evidence", PcFieldKind.Multiline),
            PcFieldSpec("verification_note", PcFieldKind.Multiline),
            PcFieldSpec("closed_by"),
            PcFieldSpec("dedupe_key", mobileEditable = false),
            PcFieldSpec("source", mobileEditable = false),
        ),
        "governance" to listOf(
            PcFieldSpec("debt_type"),
            PcFieldSpec("title"),
            PcFieldSpec("description", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("priority"),
            PcFieldSpec("source_chapter_id"),
            PcFieldSpec("target_chapter_id"),
            PcFieldSpec("target_chapter_number", PcFieldKind.Integer),
            PcFieldSpec("resolved_chapter_id"),
            PcFieldSpec("linked_foreshadowing_id"),
            PcFieldSpec("linked_causal_edge_id"),
            PcFieldSpec("evidence", PcFieldKind.Multiline),
            PcFieldSpec("resolution_note", PcFieldKind.Multiline),
            PcFieldSpec("resolution_evidence", PcFieldKind.Multiline),
            PcFieldSpec("verification_note", PcFieldKind.Multiline),
            PcFieldSpec("closed_by"),
            PcFieldSpec("dedupe_key", mobileEditable = false),
            PcFieldSpec("source", mobileEditable = false),
        ),
    )

    fun fields(entityType: String): List<PcFieldSpec> =
        specs[entityType] ?: error("PC API 暂不支持资料类型：$entityType")

    fun writableKeys(entityType: String): Set<String> = fields(entityType).mapTo(linkedSetOf()) { it.key }

    fun mobileFields(entityType: String): List<PcFieldSpec> = fields(entityType).filter(PcFieldSpec::mobileEditable)
}
