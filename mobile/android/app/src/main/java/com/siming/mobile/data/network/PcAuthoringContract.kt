package com.siming.mobile.data.network

/**
 * Writable field contract published by the PC authoring APIs.
 *
 * Android UI, online requests, and offline mutation projection must all use
 * this table instead of inventing independent mobile entity shapes. Room may
 * cache richer PC response snapshots for offline reading; the outbox must still
 * project those snapshots back to this writable contract before synchronization.
 * Conflict review may retain an exact stale client request, while the accepted
 * server branch is always represented by the canonical PC domain snapshot.
 */
internal enum class PcFieldKind {
    Text,
    NullableText,
    Multiline,
    Integer,
    NullableInteger,
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
            PcFieldSpec("outline_node_id", PcFieldKind.NullableText),
            PcFieldSpec("content", PcFieldKind.Multiline),
            // Auditable AI context is preserved across mobile edits, but it is
            // not a free-form authoring field in the Android editor.
            PcFieldSpec("context_manifest_id", PcFieldKind.NullableText, mobileEditable = false),
        ),
        "outline" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("node_type"),
            PcFieldSpec("parent_id", PcFieldKind.NullableText),
            PcFieldSpec("summary", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("sort_order", PcFieldKind.Integer),
            PcFieldSpec("character_ids", PcFieldKind.StringArray, mobileEditable = false),
            PcFieldSpec("characters", PcFieldKind.JsonArray),
            PcFieldSpec("metadata", PcFieldKind.JsonObject),
        ),
        "character" to listOf(
            PcFieldSpec("name"),
            PcFieldSpec("aliases", PcFieldKind.StringArray),
            PcFieldSpec("role_type", PcFieldKind.NullableText),
            PcFieldSpec("age", PcFieldKind.NullableText),
            PcFieldSpec("appearance", PcFieldKind.Multiline),
            PcFieldSpec("personality", PcFieldKind.Multiline),
            PcFieldSpec("background", PcFieldKind.Multiline),
            PcFieldSpec("abilities", PcFieldKind.StringArray),
            PcFieldSpec("life_status", PcFieldKind.NullableText),
            PcFieldSpec("current_location", PcFieldKind.NullableText),
            PcFieldSpec("realm_or_level", PcFieldKind.NullableText),
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
            PcFieldSpec("title"),
            PcFieldSpec("dimension"),
            PcFieldSpec("content", PcFieldKind.Multiline),
            PcFieldSpec("sort_order", PcFieldKind.Integer),
        ),
        "foreshadowing" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("description", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("importance"),
            PcFieldSpec("storyline", PcFieldKind.NullableText),
            PcFieldSpec("source_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("target_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("target_chapter_number", PcFieldKind.NullableInteger),
            PcFieldSpec("resolved_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("evidence", PcFieldKind.Multiline),
            PcFieldSpec("resolution_note", PcFieldKind.Multiline),
            PcFieldSpec("resolution_evidence", PcFieldKind.Multiline),
            PcFieldSpec("verification_note", PcFieldKind.Multiline),
            PcFieldSpec("closed_by", PcFieldKind.NullableText),
            PcFieldSpec("dedupe_key", PcFieldKind.NullableText, mobileEditable = false),
            PcFieldSpec("source", PcFieldKind.NullableText, mobileEditable = false),
        ),
        "governance" to listOf(
            PcFieldSpec("title"),
            PcFieldSpec("debt_type"),
            PcFieldSpec("description", PcFieldKind.Multiline),
            PcFieldSpec("status"),
            PcFieldSpec("priority"),
            PcFieldSpec("source_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("target_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("target_chapter_number", PcFieldKind.NullableInteger),
            PcFieldSpec("resolved_chapter_id", PcFieldKind.NullableText),
            PcFieldSpec("linked_foreshadowing_id", PcFieldKind.NullableText),
            PcFieldSpec("linked_causal_edge_id", PcFieldKind.NullableText),
            PcFieldSpec("evidence", PcFieldKind.Multiline),
            PcFieldSpec("resolution_note", PcFieldKind.Multiline),
            PcFieldSpec("resolution_evidence", PcFieldKind.Multiline),
            PcFieldSpec("verification_note", PcFieldKind.Multiline),
            PcFieldSpec("closed_by", PcFieldKind.NullableText),
            PcFieldSpec("dedupe_key", PcFieldKind.NullableText, mobileEditable = false),
            PcFieldSpec("source", PcFieldKind.NullableText, mobileEditable = false),
        ),
    )

    fun fields(entityType: String): List<PcFieldSpec> =
        specs[entityType] ?: error("PC API 暂不支持资料类型：$entityType")

    fun writableKeys(entityType: String): Set<String> = fields(entityType).mapTo(linkedSetOf()) { it.key }

    fun mobileFields(entityType: String): List<PcFieldSpec> = fields(entityType).filter(PcFieldSpec::mobileEditable)
}
