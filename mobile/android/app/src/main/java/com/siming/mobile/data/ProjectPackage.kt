@file:OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)

package com.siming.mobile.data

import com.siming.mobile.BuildConfig
import com.siming.mobile.data.local.ReplicaEntity
import java.io.File
import java.io.InputStream
import java.io.InputStreamReader
import java.nio.charset.CodingErrorAction
import java.security.DigestInputStream
import java.security.MessageDigest
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.util.UUID
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import org.apache.commons.compress.archivers.zip.ZipArchiveEntry
import org.apache.commons.compress.archivers.zip.ZipFile as ApacheZipFile

const val PROJECT_PACKAGE_EXTENSION = ".siming-project"
const val PROJECT_PACKAGE_MEDIA_TYPE = "application/vnd.siming.project+zip"
const val MAX_PROJECT_PACKAGE_BYTES: Long = 512L * 1024L * 1024L

private const val PROJECT_PACKAGE_FORMAT = "siming-project-package"
private const val PROJECT_PACKAGE_VERSION = 1
private const val MAX_PROJECT_PACKAGE_ENTRIES = 10_000
private const val MAX_PROJECT_PACKAGE_UNCOMPRESSED_BYTES = 2L * 1024L * 1024L * 1024L
private const val MAX_PROJECT_PACKAGE_MANIFEST_BYTES = 1024L * 1024L
private const val MAX_PROJECT_PACKAGE_DATA_BYTES = 128L * 1024L * 1024L
private const val MAX_PROJECT_PACKAGE_MATERIAL_BYTES = 25L * 1024L * 1024L
private const val MAX_PROJECT_PACKAGE_COMPRESSION_RATIO = 100L

private val PROJECT_PACKAGE_ID_NAMESPACE: UUID =
    UUID.fromString("8a746db1-9153-5a74-977c-4ad4dc1f6cb7")

data class MobileProjectPackageFile(
    val filename: String,
    val file: File,
    val sizeBytes: Long,
    val sha256: String,
)

data class MobileProjectPackageImportResult(
    val projectId: String,
    val projectTitle: String,
    val profile: String,
    val remote: Boolean,
    val replayed: Boolean = false,
    val refreshWarning: String? = null,
)

internal data class MobilePackageReplica(
    val projectId: String,
    val entityType: String,
    val entityId: String,
    val payload: JsonObject,
)

internal data class ValidatedMobileProjectPackage(
    val packageId: String,
    val profile: String,
    val sourceProjectId: String,
    val sourceProjectTitle: String,
    val packageSha256: String,
    val coreRows: Map<String, List<JsonObject>>,
    val sourceIdCollections: Map<String, String>,
)

private data class MobileCollectionSpec(
    val key: String,
    val fields: Set<String>,
    val profiles: Set<String>,
    val localEntityType: String? = null,
) {
    val path: String = "data/$key.jsonl"
}

private fun spec(
    key: String,
    profiles: Set<String>,
    fields: String,
    localEntityType: String? = null,
) = MobileCollectionSpec(key, fields.split(' ').filter(String::isNotBlank).toSet(), profiles, localEntityType)

private val COMMON_PROFILES = setOf("full", "structure")
private val FULL_PROFILE = setOf("full")

private val MOBILE_COLLECTION_SPECS = listOf(
    spec(
        "project",
        COMMON_PROFILES,
        "id title description tags narrative_perspective writing_style forbidden_sentence_patterns " +
            "rhetoric_guidelines short_sentences custom_style_prompt daily_word_goal created_at updated_at",
        "project",
    ),
    spec(
        "creation_sessions",
        COMMON_PROFILES,
        "id source_project_id created_project_id status mode user_brief target_audience genre platform " +
            "schema_version current_stage revision review_json draft_json checkpoints_json created_at updated_at completed_at",
    ),
    spec(
        "creation_entities",
        COMMON_PROFILES,
        "id session_id artifact_key entity_type entity_key position status revision source data_json " +
            "provenance_json created_at updated_at deleted_at",
    ),
    spec(
        "outline_nodes",
        COMMON_PROFILES,
        "id project_id parent_id node_type title summary status source_chapter_id actual_summary " +
            "planned_summary metadata_json sort_order created_at updated_at",
        "outline",
    ),
    spec(
        "characters",
        COMMON_PROFILES,
        "id project_id name appearance personality background abilities role_type age current_version " +
            "is_evolution_tracked life_status current_location realm_or_level physical_state mental_state " +
            "current_goal active_conflict abilities_state items_or_assets profile_json last_seen_chapter_id " +
            "last_updated_chapter_id created_at updated_at",
        "character",
    ),
    spec(
        "character_ai_configs",
        COMMON_PROFILES,
        "id character_id tone_style catchphrases verbosity emotion_tendency created_at updated_at",
    ),
    spec(
        "character_aliases",
        COMMON_PROFILES,
        "id project_id character_id alias alias_type description confidence source_chapter_id " +
            "merged_character_id created_at updated_at",
    ),
    spec(
        "character_relationships",
        COMMON_PROFILES,
        "id project_id character_a_id character_b_id relationship_type description created_at",
    ),
    spec(
        "worldbuilding_entries",
        COMMON_PROFILES,
        "id project_id dimension title content first_seen_chapter_id last_updated_chapter_id status " +
            "confidence sort_order created_at updated_at",
        "world",
    ),
    spec(
        "worldbuilding_relations",
        COMMON_PROFILES,
        "id project_id source_entry_id target_entry_id relation_type description metadata_json created_at updated_at",
    ),
    spec(
        "outline_characters",
        COMMON_PROFILES,
        "id outline_node_id character_id role_in_scene created_at",
    ),
    spec(
        "chapters",
        FULL_PROFILE,
        "id project_id outline_node_id title content word_count current_version sort_order created_at updated_at",
        "chapter",
    ),
    spec(
        "chapter_snapshots",
        FULL_PROFILE,
        "id chapter_id version_number content word_count trigger_type created_at",
    ),
    spec(
        "chapter_summaries",
        FULL_PROFILE,
        "id chapter_id summary_text key_events token_count created_at updated_at",
    ),
    spec(
        "chapter_characters",
        FULL_PROFILE,
        "id chapter_id character_id appearance_type description created_at",
    ),
    spec(
        "chapter_worldbuilding",
        FULL_PROFILE,
        "id chapter_id worldbuilding_entry_id description created_at",
    ),
    spec(
        "chapter_drafts",
        FULL_PROFILE,
        "id project_id title outline_node_id saved_chapter_id status content created_at updated_at",
        "chapter_draft",
    ),
    spec(
        "character_versions",
        FULL_PROFILE,
        "id character_id version_number snapshot_data change_summary source_chapter_id created_at",
    ),
    spec(
        "character_timelines",
        FULL_PROFILE,
        "id character_id chapter_id event_description event_type emotional_state_change sort_order created_at",
    ),
    spec(
        "character_change_logs",
        FULL_PROFILE,
        "id character_id chapter_id chapter_version change_type field_name old_value new_value confirmed created_at",
    ),
    spec(
        "worldbuilding_versions",
        FULL_PROFILE,
        "id entry_id version_number snapshot_data change_summary source_chapter_id created_at",
    ),
    spec(
        "worldbuilding_timelines",
        FULL_PROFILE,
        "id entry_id chapter_id event_description event_type evidence sort_order created_at",
    ),
    spec(
        "foreshadowings",
        FULL_PROFILE,
        "id project_id title description status importance source_chapter_id target_chapter_id " +
            "target_chapter_number resolved_chapter_id evidence source_chapter_version resolved_chapter_version " +
            "resolution_note resolution_evidence verification_note verified_at last_checked_at stale_reason closed_by " +
            "storyline dedupe_key source created_at updated_at",
    ),
    spec(
        "causal_edges",
        FULL_PROFILE,
        "id project_id cause effect causal_type strength status character_ids source_chapter_id resolved_chapter_id " +
            "evidence source_chapter_version resolved_chapter_version resolution_note resolution_evidence verification_note " +
            "verified_at last_checked_at stale_reason closed_by dedupe_key source created_at updated_at",
    ),
    spec(
        "narrative_debts",
        FULL_PROFILE,
        "id project_id debt_type title description status priority source_chapter_id target_chapter_id " +
            "target_chapter_number resolved_chapter_id linked_foreshadowing_id linked_causal_edge_id evidence " +
            "source_chapter_version resolved_chapter_version resolution_note resolution_evidence verification_note " +
            "verified_at last_checked_at stale_reason closed_by dedupe_key source created_at updated_at",
    ),
    spec(
        "character_narrative_states",
        FULL_PROFILE,
        "id project_id character_id chapter_id current_goal public_stance hidden_intent emotional_residue " +
            "relationship_tension behavior_boundaries evidence source created_at",
    ),
    spec(
        "narrative_checkpoints",
        FULL_PROFILE,
        "id project_id chapter_id chapter_snapshot_id sequence label trigger_type state_json created_at",
    ),
    spec(
        "chapter_governance_reviews",
        FULL_PROFILE,
        "id project_id chapter_id chapter_version status source findings_count confidence evidence reviewed_at " +
            "created_at updated_at",
    ),
    spec(
        "creation_artifact_versions",
        FULL_PROFILE,
        "id session_id artifact_key revision status source change_type snapshot_json change_summary_json " +
            "parent_version_id restored_from_version_id created_at",
    ),
    spec(
        "creation_materials",
        FULL_PROFILE,
        "id session_id filename media_type file_sha256 size_bytes input_revision text_length selection_json " +
            "created_at updated_at completed_at asset_path",
    ),
)

private val SPECS_BY_PATH = MOBILE_COLLECTION_SPECS.associateBy(MobileCollectionSpec::path)
private val SPECS_BY_KEY = MOBILE_COLLECTION_SPECS.associateBy(MobileCollectionSpec::key)
private val SPECS_BY_LOCAL_ENTITY_TYPE = MOBILE_COLLECTION_SPECS
    .filter { it.localEntityType != null }
    .associateBy { requireNotNull(it.localEntityType) }

private val MANIFEST_FIELDS = setOf(
    "format", "format_version", "package_id", "profile", "producer", "exported_at",
    "source_project", "entries",
)
private val ENTRY_FIELDS = setOf("path", "media_type", "size", "sha256", "records")
private val PRODUCER_FIELDS = setOf("name", "app_version")
private val SOURCE_PROJECT_FIELDS = setOf("id", "title")

private val JSON_FIELDS = setOf(
    "review_json", "draft_json", "checkpoints_json", "data_json", "provenance_json",
    "metadata_json", "profile_json", "character_ids", "state_json", "snapshot_json",
    "change_summary_json", "selection_json",
)
private val BOOLEAN_FIELDS = setOf("short_sentences", "is_evolution_tracked", "confirmed")
private val INTEGER_FIELDS = setOf(
    "daily_word_goal", "schema_version", "revision", "position", "sort_order", "current_version",
    "word_count", "version_number", "token_count", "chapter_version",
    "target_chapter_number", "source_chapter_version", "resolved_chapter_version", "sequence",
    "findings_count", "size_bytes", "input_revision", "text_length",
)
private val NUMBER_FIELDS = setOf("confidence", "strength")
private val DATETIME_FIELDS = setOf(
    "created_at", "updated_at", "completed_at", "deleted_at", "verified_at", "last_checked_at", "reviewed_at",
)

private fun required(fields: String): Set<String> = fields.split(' ').filter(String::isNotBlank).toSet()

private val REQUIRED_FIELDS = mapOf(
    "project" to required("id title created_at updated_at"),
    "creation_sessions" to required("id status mode schema_version revision created_at"),
    "creation_entities" to required(
        "id session_id artifact_key entity_type entity_key position status revision source data_json created_at updated_at",
    ),
    "outline_nodes" to required("id project_id node_type title created_at updated_at"),
    "characters" to required("id project_id name created_at updated_at"),
    "character_ai_configs" to required("id character_id created_at updated_at"),
    "character_aliases" to required("id project_id character_id alias alias_type created_at updated_at"),
    "character_relationships" to required(
        "id project_id character_a_id character_b_id relationship_type created_at",
    ),
    "worldbuilding_entries" to required("id project_id dimension title content created_at updated_at"),
    "worldbuilding_relations" to required(
        "id project_id source_entry_id target_entry_id relation_type created_at updated_at",
    ),
    "outline_characters" to required("id outline_node_id character_id created_at"),
    "chapters" to required("id project_id title content sort_order created_at updated_at"),
    "chapter_snapshots" to required("id chapter_id version_number content trigger_type created_at"),
    "chapter_summaries" to required("id chapter_id summary_text created_at updated_at"),
    "chapter_characters" to required("id chapter_id character_id appearance_type created_at"),
    "chapter_worldbuilding" to required("id chapter_id worldbuilding_entry_id created_at"),
    "chapter_drafts" to required("id project_id title status content created_at updated_at"),
    "character_versions" to required("id character_id version_number snapshot_data created_at"),
    "character_timelines" to required(
        "id character_id chapter_id event_description event_type created_at",
    ),
    "character_change_logs" to required("id character_id chapter_id change_type field_name created_at"),
    "worldbuilding_versions" to required("id entry_id version_number snapshot_data created_at"),
    "worldbuilding_timelines" to required("id entry_id chapter_id event_description event_type created_at"),
    "foreshadowings" to required(
        "id project_id title status importance dedupe_key source created_at",
    ),
    "causal_edges" to required(
        "id project_id cause effect causal_type strength status character_ids dedupe_key source created_at",
    ),
    "narrative_debts" to required(
        "id project_id debt_type title status priority dedupe_key source created_at",
    ),
    "character_narrative_states" to required("id project_id character_id source created_at"),
    "narrative_checkpoints" to required(
        "id project_id sequence label trigger_type state_json created_at",
    ),
    "chapter_governance_reviews" to required(
        "id project_id chapter_id chapter_version status source findings_count created_at updated_at",
    ),
    "creation_artifact_versions" to required(
        "id session_id artifact_key revision status source change_type snapshot_json created_at",
    ),
    "creation_materials" to required(
        "id session_id filename file_sha256 size_bytes input_revision text_length created_at updated_at asset_path",
    ),
)

private val REFERENCE_TARGETS: Map<String, Map<String, String>> = mapOf(
    "creation_sessions" to mapOf("source_project_id" to "project", "created_project_id" to "project"),
    "creation_entities" to mapOf("session_id" to "creation_sessions"),
    "outline_nodes" to mapOf(
        "project_id" to "project", "parent_id" to "outline_nodes", "source_chapter_id" to "chapters",
    ),
    "characters" to mapOf(
        "project_id" to "project", "last_seen_chapter_id" to "chapters",
        "last_updated_chapter_id" to "chapters",
    ),
    "character_ai_configs" to mapOf("character_id" to "characters"),
    "character_aliases" to mapOf(
        "project_id" to "project", "character_id" to "characters", "source_chapter_id" to "chapters",
        "merged_character_id" to "characters",
    ),
    "character_relationships" to mapOf(
        "project_id" to "project", "character_a_id" to "characters", "character_b_id" to "characters",
    ),
    "worldbuilding_entries" to mapOf(
        "project_id" to "project", "first_seen_chapter_id" to "chapters",
        "last_updated_chapter_id" to "chapters",
    ),
    "worldbuilding_relations" to mapOf(
        "project_id" to "project", "source_entry_id" to "worldbuilding_entries",
        "target_entry_id" to "worldbuilding_entries",
    ),
    "outline_characters" to mapOf("outline_node_id" to "outline_nodes", "character_id" to "characters"),
    "chapters" to mapOf("project_id" to "project", "outline_node_id" to "outline_nodes"),
    "chapter_snapshots" to mapOf("chapter_id" to "chapters"),
    "chapter_summaries" to mapOf("chapter_id" to "chapters"),
    "chapter_characters" to mapOf("chapter_id" to "chapters", "character_id" to "characters"),
    "chapter_worldbuilding" to mapOf(
        "chapter_id" to "chapters", "worldbuilding_entry_id" to "worldbuilding_entries",
    ),
    "chapter_drafts" to mapOf(
        "project_id" to "project", "outline_node_id" to "outline_nodes", "saved_chapter_id" to "chapters",
    ),
    "character_versions" to mapOf("character_id" to "characters", "source_chapter_id" to "chapters"),
    "character_timelines" to mapOf("character_id" to "characters", "chapter_id" to "chapters"),
    "character_change_logs" to mapOf("character_id" to "characters", "chapter_id" to "chapters"),
    "worldbuilding_versions" to mapOf("entry_id" to "worldbuilding_entries", "source_chapter_id" to "chapters"),
    "worldbuilding_timelines" to mapOf("entry_id" to "worldbuilding_entries", "chapter_id" to "chapters"),
    "foreshadowings" to mapOf(
        "project_id" to "project", "source_chapter_id" to "chapters", "target_chapter_id" to "chapters",
        "resolved_chapter_id" to "chapters",
    ),
    "causal_edges" to mapOf(
        "project_id" to "project", "source_chapter_id" to "chapters", "resolved_chapter_id" to "chapters",
    ),
    "narrative_debts" to mapOf(
        "project_id" to "project", "source_chapter_id" to "chapters", "target_chapter_id" to "chapters",
        "resolved_chapter_id" to "chapters", "linked_foreshadowing_id" to "foreshadowings",
        "linked_causal_edge_id" to "causal_edges",
    ),
    "character_narrative_states" to mapOf(
        "project_id" to "project", "character_id" to "characters", "chapter_id" to "chapters",
    ),
    "narrative_checkpoints" to mapOf(
        "project_id" to "project", "chapter_id" to "chapters", "chapter_snapshot_id" to "chapter_snapshots",
    ),
    "chapter_governance_reviews" to mapOf("project_id" to "project", "chapter_id" to "chapters"),
    "creation_artifact_versions" to mapOf(
        "session_id" to "creation_sessions", "parent_version_id" to "creation_artifact_versions",
        "restored_from_version_id" to "creation_artifact_versions",
    ),
    "creation_materials" to mapOf("session_id" to "creation_sessions"),
)

private val REFERENCE_FIELDS = REFERENCE_TARGETS.values.flatMap { it.keys }.toSet()

internal class MobileProjectPackageValidator(
    private val source: File,
    private val expectedSha256: String? = null,
) {
    private val json = Json { ignoreUnknownKeys = false; explicitNulls = true }

    fun validate(): ValidatedMobileProjectPackage {
        require(source.isFile && source.length() > 0L) { "上传的司命项目包为空" }
        require(source.length() <= MAX_PROJECT_PACKAGE_BYTES) { "项目包超过 512MiB 上限" }
        val packageSha256 = sha256File(source)
        require(expectedSha256 == null || packageSha256 == expectedSha256) { "项目包文件哈希在读取期间发生变化" }

        ApacheZipFile(source).use { archive ->
            val zipEntries = archive.entries.toListStrict()
            val byName = validateZipStructure(archive, zipEntries)
            val manifest = parseManifest(archive, requireNotNull(byName["manifest.json"]) {
                "文件缺少 manifest.json，不是司命项目包"
            })
            val declared = validateManifest(manifest, byName)
            val identities = linkedMapOf<String, String>()
            val identifiersByCollection = mutableMapOf<String, MutableSet<String>>()
            val coreRows = mutableMapOf<String, MutableList<JsonObject>>()
            val materialRows = mutableListOf<JsonObject>()

            declared.values.forEach { declaration ->
                val path = declaration.string("path")
                val entry = requireNotNull(byName[path])
                val digest = MessageDigest.getInstance("SHA-256")
                var records = 0
                archive.getInputStream(entry).use { raw ->
                    DigestInputStream(raw, digest).use { checked ->
                        if (path.startsWith("data/")) {
                            val spec = requireNotNull(SPECS_BY_PATH[path])
                            records = readRows(checked, spec) { row, lineNumber ->
                                validateRow(spec, row, lineNumber)
                                val sourceId = row.string("id")
                                require(sourceId.isNotBlank()) { "${spec.path} 第 $lineNumber 行包含无效 ID" }
                                val previous = identities.putIfAbsent(sourceId, spec.key)
                                require(previous == null) {
                                    "项目包 ID 在 $previous 与 ${spec.key} 中重复：$sourceId"
                                }
                                identifiersByCollection.getOrPut(spec.key, ::linkedSetOf).add(sourceId)
                                if (spec.localEntityType != null) {
                                    coreRows.getOrPut(spec.key, ::mutableListOf).add(row)
                                }
                                if (spec.key == "creation_materials") materialRows += row
                            }
                        } else {
                            checked.copyTo(DiscardingOutputStream, 1024 * 1024)
                            records = 1
                        }
                    }
                }
                require(digest.hex() == declaration.string("sha256")) { "项目包条目校验失败：$path" }
                require(records == declaration.int("records")) { "$path 记录数与 manifest 不一致" }
            }

            val projectRows = coreRows["project"].orEmpty()
            require(projectRows.size == 1) { "项目包必须且只能包含一条 project 记录" }
            val sourceProject = manifest.objectValue("source_project")
            require(projectRows.single().string("id") == sourceProject.string("id")) { "项目包来源作品 ID 不一致" }
            validateMaterialLinks(declared, materialRows)
            validateReferences(archive, declared, identifiersByCollection)
            return ValidatedMobileProjectPackage(
                packageId = manifest.string("package_id"),
                profile = manifest.string("profile"),
                sourceProjectId = sourceProject.string("id"),
                sourceProjectTitle = sourceProject.string("title"),
                packageSha256 = packageSha256,
                coreRows = coreRows,
                sourceIdCollections = identities,
            )
        }
    }

    private fun validateZipStructure(
        archive: ApacheZipFile,
        entries: List<ZipArchiveEntry>,
    ): Map<String, ZipArchiveEntry> {
        require(entries.size <= MAX_PROJECT_PACKAGE_ENTRIES) { "项目包条目数量超过 10000" }
        val byName = linkedMapOf<String, ZipArchiveEntry>()
        var total = 0L
        entries.forEach { entry ->
            val name = entry.name
            validateArchiveName(name)
            require(!entry.isDirectory) { "项目包不得包含目录占位条目" }
            require(byName.putIfAbsent(name, entry) == null) { "项目包包含重复条目：$name" }
            require(!entry.generalPurposeBit.usesEncryption()) { "项目包不得加密" }
            require(!entry.isUnixSymlink) { "项目包不得包含符号链接" }
            val limit = entryLimit(name)
            require(limit > 0L) { "项目包包含未知条目：$name" }
            require(entry.size in 0..limit) { "项目包条目过大：$name" }
            require(entry.compressedSize >= 0L) { "项目包压缩信息无效：$name" }
            if (entry.size > 0L) {
                require(entry.compressedSize > 0L) { "项目包压缩比异常：$name" }
                require(entry.size / entry.compressedSize <= MAX_PROJECT_PACKAGE_COMPRESSION_RATIO) {
                    "项目包压缩比超过 100:1：$name"
                }
            }
            total += entry.size
            require(total <= MAX_PROJECT_PACKAGE_UNCOMPRESSED_BYTES) { "项目包解压总量超过 2GiB" }
            require(archive.canReadEntryData(entry)) { "项目包条目使用了不支持的压缩或加密方式：$name" }
        }
        return byName
    }

    private fun parseManifest(archive: ApacheZipFile, entry: ZipArchiveEntry): JsonObject {
        require(entry.size <= MAX_PROJECT_PACKAGE_MANIFEST_BYTES) { "manifest.json 超过安全上限" }
        val raw = archive.getInputStream(entry).use { it.readBytesLimited(MAX_PROJECT_PACKAGE_MANIFEST_BYTES) }
        val text = decodeUtf8(raw, "manifest.json")
        return runCatching { json.parseToJsonElement(text) as? JsonObject }
            .getOrNull() ?: error("manifest.json 不是有效 UTF-8 JSON 对象")
    }

    private fun validateManifest(
        manifest: JsonObject,
        zipEntries: Map<String, ZipArchiveEntry>,
    ): Map<String, JsonObject> {
        requireExactFields(manifest, MANIFEST_FIELDS, "manifest.json")
        require(manifest.string("format") == PROJECT_PACKAGE_FORMAT) {
            "该文件不是司命项目包；TXT/Markdown/DOCX 请使用“导入外部小说”"
        }
        require(manifest.int("format_version") == PROJECT_PACKAGE_VERSION) { "不支持的司命项目包版本" }
        runCatching { UUID.fromString(manifest.string("package_id")) }
            .getOrElse { error("项目包 package_id 无效") }
        val profile = manifest.string("profile")
        require(profile in COMMON_PROFILES) { "项目包 profile 无效" }
        val producer = manifest.objectValue("producer")
        val sourceProject = manifest.objectValue("source_project")
        requireExactFields(producer, PRODUCER_FIELDS, "producer")
        requireExactFields(sourceProject, SOURCE_PROJECT_FIELDS, "source_project")
        require(producer.string("name") == "siming") { "项目包生产者不是司命" }
        require(producer.string("app_version").isNotBlank()) { "项目包导出版本无效" }
        require(sourceProject.string("id").isNotBlank()) { "项目包来源作品 ID 无效" }
        require((manifest["exported_at"] as? JsonPrimitive)?.contentOrNull?.isNotBlank() == true) {
            "项目包导出时间无效"
        }
        validateDateTime(manifest.string("exported_at"), "项目包导出时间无效")
        val entries = manifest["entries"] as? JsonArray ?: error("项目包 entries 必须是数组")
        val expectedData = MOBILE_COLLECTION_SPECS.filter { profile in it.profiles }.mapTo(linkedSetOf()) { it.path }
        val declared = linkedMapOf<String, JsonObject>()
        entries.forEach { raw ->
            val declaration = raw as? JsonObject ?: error("项目包 entry 必须是对象")
            requireExactFields(declaration, ENTRY_FIELDS, "entry")
            val path = declaration.string("path")
            require(path != "manifest.json") { "项目包 entry 路径无效" }
            validateArchiveName(path)
            require(declared.putIfAbsent(path, declaration) == null) { "manifest 重复声明：$path" }
            if (path.startsWith("data/")) {
                require(path in expectedData) { "项目包包含档位不允许的数据：$path" }
                require(declaration.string("media_type") == "application/x-ndjson") {
                    "项目包数据媒体类型无效：$path"
                }
            } else {
                require(path.startsWith("assets/materials/") && profile == "full") {
                    if (profile == "structure") "结构项目包不得包含素材文件" else "项目包包含未知路径：$path"
                }
                require(declaration.int("records") == 1) { "项目包素材记录数无效：$path" }
            }
            val entry = zipEntries[path] ?: error("项目包缺少已声明条目：$path")
            require(declaration.long("size") == entry.size) { "项目包条目大小不一致：$path" }
            require(declaration.int("records") >= 0) { "项目包记录数无效：$path" }
            val digest = declaration.string("sha256")
            require(digest.length == 64 && digest.all { it in "0123456789abcdef" }) { "项目包哈希无效：$path" }
            require(declaration.string("media_type").isNotBlank()) { "项目包媒体类型无效：$path" }
        }
        require(declared.keys.containsAll(expectedData)) {
            "项目包缺少数据集合：${(expectedData - declared.keys).sorted().joinToString()}"
        }
        require(zipEntries.keys - "manifest.json" == declared.keys) { "ZIP 条目与 manifest 声明不一致" }
        return declared
    }

    private fun readRows(
        input: InputStream,
        spec: MobileCollectionSpec,
        consume: (JsonObject, Int) -> Unit,
    ): Int {
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        var count = 0
        try {
            InputStreamReader(input, decoder).buffered().use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) continue
                    count += 1
                    val row = runCatching { json.parseToJsonElement(line) as? JsonObject }.getOrNull()
                        ?: error("${spec.path} 第 $count 行必须是 JSON 对象")
                    consume(row, count)
                }
            }
        } catch (error: java.nio.charset.CharacterCodingException) {
            throw IllegalArgumentException("${spec.path} 不是 UTF-8", error)
        }
        return count
    }

    private fun validateRow(spec: MobileCollectionSpec, row: JsonObject, lineNumber: Int) {
        requireExactFields(row, spec.fields, "${spec.path} 第 $lineNumber 行")
        row.forEach { (field, value) ->
            if (value is JsonNull) {
                require(field !in REQUIRED_FIELDS[spec.key].orEmpty()) {
                    "${spec.path} 第 $lineNumber 行的 $field 不能为空"
                }
                return@forEach
            }
            if (field in JSON_FIELDS) return@forEach
            val primitive = value as? JsonPrimitive
                ?: error("${spec.path} 第 $lineNumber 行的 $field 类型无效")
            when (field) {
                in BOOLEAN_FIELDS -> require(primitive.booleanOrNull != null) {
                    "${spec.path} 的 $field 必须是布尔值"
                }
                in INTEGER_FIELDS -> require(!primitive.isString && primitive.intOrNull != null) {
                    "${spec.path} 的 $field 必须是整数"
                }
                in NUMBER_FIELDS -> require(!primitive.isString && primitive.doubleOrNull != null) {
                    "${spec.path} 的 $field 必须是数字"
                }
                in DATETIME_FIELDS -> {
                    require(primitive.isString) { "${spec.path} 的 $field 必须是时间字符串" }
                    validateDateTime(primitive.content, "${spec.path} 的 $field 时间格式无效")
                }
                else -> require(primitive.isString) { "${spec.path} 的 $field 必须是字符串" }
            }
        }
    }

    private fun validateMaterialLinks(
        declared: Map<String, JsonObject>,
        materialRows: List<JsonObject>,
    ) {
        val referenced = linkedSetOf<String>()
        materialRows.forEach { row ->
            val path = row.string("asset_path")
            require(path.startsWith("assets/materials/${row.string("id")}/")) {
                "素材条目引用无效：${row.string("filename")}"
            }
            val entry = declared[path] ?: error("素材条目引用无效：${row.string("filename")}")
            require(entry.string("sha256") == row.string("file_sha256") && entry.long("size") == row.long("size_bytes")) {
                "素材元数据不一致：${row.string("filename") }"
            }
            referenced += path
        }
        val materials = declared.keys.filterTo(linkedSetOf()) { it.startsWith("assets/materials/") }
        require(materials == referenced) { "项目包包含未关联的素材文件" }
    }

    private fun validateReferences(
        archive: ApacheZipFile,
        declared: Map<String, JsonObject>,
        identifiers: Map<String, Set<String>>,
    ) {
        MOBILE_COLLECTION_SPECS.forEach { spec ->
            val declaration = declared[spec.path] ?: return@forEach
            val entry = archive.getEntry(declaration.string("path")) ?: error("项目包条目缺失：${spec.path}")
            archive.getInputStream(entry).use { input ->
                readRows(input, spec) { row, _ ->
                    REFERENCE_TARGETS[spec.key].orEmpty().forEach reference@{ (field, target) ->
                        val value = row[field]
                        if (value == null || value is JsonNull) return@reference
                        val sourceId = (value as? JsonPrimitive)?.contentOrNull
                        require(sourceId != null && sourceId in identifiers[target].orEmpty()) {
                            "${spec.key}.$field 引用了项目包外的实体"
                        }
                    }
                    if (spec.key == "causal_edges") {
                        val characterIds = row["character_ids"] as? JsonArray
                            ?: error("causal_edges.character_ids 包含项目包外的角色")
                        require(characterIds.all { value ->
                            (value as? JsonPrimitive)?.contentOrNull in identifiers["characters"].orEmpty()
                        }) { "causal_edges.character_ids 包含项目包外的角色" }
                    }
                }
            }
        }
    }
}

internal object MobileProjectPackageMaterializer {
    fun materialize(
        validated: ValidatedMobileProjectPackage,
        idempotencyKey: UUID,
        requestedTitle: String?,
    ): Pair<String, List<MobilePackageReplica>> {
        val identifierMap = validated.sourceIdCollections.mapValues { (sourceId, collection) ->
            projectPackageUuid5(idempotencyKey, collection, sourceId).toString()
        }
        val projectId = requireNotNull(identifierMap[validated.sourceProjectId])
        val replicas = mutableListOf<MobilePackageReplica>()
        validated.coreRows.forEach { (collection, rows) ->
            val spec = requireNotNull(SPECS_BY_KEY[collection])
            val entityType = requireNotNull(spec.localEntityType)
            rows.forEach { source ->
                val mapped = mapRow(source, identifierMap).toMutableMap()
                val entityId = requireNotNull(identifierMap[source.string("id")])
                if (collection == "project" && !requestedTitle.isNullOrBlank()) {
                    mapped["title"] = JsonPrimitive(requestedTitle.trim().take(200))
                }
                mapped["_record_type"] = JsonPrimitive(entityType)
                replicas += MobilePackageReplica(
                    projectId = projectId,
                    entityType = entityType,
                    entityId = entityId,
                    payload = JsonObject(mapped),
                )
            }
        }
        return projectId to replicas
    }

    internal fun mapRow(row: JsonObject, identifierMap: Map<String, String>): JsonObject = JsonObject(
        row.mapValues { (field, value) ->
            when {
                field == "id" || field in REFERENCE_FIELDS -> mapReference(value, identifierMap)
                field in JSON_FIELDS -> mapNested(value, identifierMap)
                else -> value
            }
        },
    )

    private fun mapReference(value: JsonElement, identifierMap: Map<String, String>): JsonElement {
        if (value is JsonNull) return value
        val sourceId = (value as? JsonPrimitive)?.contentOrNull ?: return value
        return JsonPrimitive(identifierMap[sourceId] ?: sourceId)
    }

    private fun mapNested(value: JsonElement, identifierMap: Map<String, String>): JsonElement = when (value) {
        is JsonPrimitive -> value.contentOrNull?.let { identifierMap[it] }?.let(::JsonPrimitive) ?: value
        is JsonArray -> JsonArray(value.map { mapNested(it, identifierMap) })
        is JsonObject -> JsonObject(value.mapValues { mapNested(it.value, identifierMap) })
    }
}

internal object MobileProjectPackageWriter {
    private val writerJson = Json { explicitNulls = true; encodeDefaults = true }

    /**
     * Rebuild an imported package from its retained, validated archive while
     * overlaying the current editable mobile replicas. Collections and
     * materials that Android does not render remain byte-for-byte available;
     * IDs are rewritten with the same UUIDv5 rule used by the backend import.
     */
    fun rewriteImported(
        source: File,
        expectedSha256: String,
        idempotencyKey: UUID,
        projectId: String,
        snapshot: List<ReplicaEntity>,
        pendingDraft: MobilePendingChapterDraft?,
        profile: String,
        destination: File,
    ) {
        require(profile in COMMON_PROFILES) { "项目包档位只能是完整或结构" }
        val validated = MobileProjectPackageValidator(source, expectedSha256).validate()
        require(validated.profile != "structure" || profile == "structure") {
            "本机仅保存了结构项目包，无法离线补出原包中未包含的章节和素材"
        }
        val identifierMap = validated.sourceIdCollections.mapValues { (sourceId, collection) ->
            projectPackageUuid5(idempotencyKey, collection, sourceId).toString()
        }
        require(identifierMap[validated.sourceProjectId] == projectId) { "本机项目包与作品副本不匹配" }
        val project = snapshot.singleOrNull {
            it.entityType == "project" && it.entityId == projectId && it.operation == "upsert"
        } ?: error("作品不存在")
        val finalIdentifiers = finalIdentifiers(validated, identifierMap, snapshot, pendingDraft)
        val now = java.time.Instant.now().toString()
        val staging = kotlin.io.path.createTempDirectory("mobile-project-package-rewrite-").toFile()
        try {
            ApacheZipFile(source).use { sourceArchive ->
                val sourceManifest = sourceArchive.getEntry("manifest.json")?.let { entry ->
                    sourceArchive.getInputStream(entry).use { input ->
                        writerJson.parseToJsonElement(
                            decodeUtf8(input.readBytesLimited(MAX_PROJECT_PACKAGE_MANIFEST_BYTES), "manifest.json"),
                        ) as? JsonObject
                    }
                } ?: error("文件缺少 manifest.json，不是司命项目包")
                val sourceDeclarations = (sourceManifest["entries"] as? JsonArray)
                    .orEmpty()
                    .map { it as JsonObject }
                    .associateBy { it.string("path") }
                val materialPaths = materialAssetPaths(sourceArchive, sourceDeclarations, identifierMap)
                val referencedAssets = linkedSetOf<String>()
                val manifestEntries = mutableListOf<JsonObject>()

                MOBILE_COLLECTION_SPECS.filter { profile in it.profiles }.forEach { spec ->
                    val dataFile = File(staging, spec.path).apply { parentFile?.mkdirs() }
                    val records = writeRewrittenRows(
                        archive = sourceArchive,
                        sourceDeclarations = sourceDeclarations,
                        spec = spec,
                        identifierMap = identifierMap,
                        materialPaths = materialPaths,
                        projectId = projectId,
                        snapshot = snapshot,
                        pendingDraft = pendingDraft,
                        profile = profile,
                        now = now,
                        finalIdentifiers = finalIdentifiers,
                        destination = dataFile,
                        referencedAssets = referencedAssets,
                    )
                    manifestEntries += dataDeclaration(spec.path, dataFile, records)
                }

                val sourcePathByRewrittenPath = materialPaths.entries.associate { (sourcePath, rewrittenPath) ->
                    rewrittenPath to sourcePath
                }
                val retainedAssets = referencedAssets.sorted().map { rewrittenPath ->
                    val sourcePath = sourcePathByRewrittenPath[rewrittenPath]
                        ?: error("素材条目引用无效：$rewrittenPath")
                    val declaration = sourceDeclarations[sourcePath]
                        ?: error("素材条目引用无效：$sourcePath")
                    val rewritten = JsonObject(
                        declaration.toMutableMap().apply { put("path", JsonPrimitive(rewrittenPath)) },
                    )
                    manifestEntries += rewritten
                    RetainedAsset(sourcePath, rewrittenPath)
                }

                val title = project.payloadObject().string("title").ifBlank { "未命名作品" }
                val manifest = packageManifest(projectId, title, profile, now, manifestEntries)
                destination.parentFile?.mkdirs()
                ZipOutputStream(destination.outputStream().buffered()).use { output ->
                    output.setLevel(Deflater.NO_COMPRESSION)
                    manifestEntries.filter { it.string("path").startsWith("data/") }.forEach { declaration ->
                        val path = declaration.string("path")
                        output.putNextEntry(ZipEntry(path))
                        File(staging, path).inputStream().buffered().use { input ->
                            input.copyTo(output, 1024 * 1024)
                        }
                        output.closeEntry()
                    }
                    retainedAssets.forEach { asset ->
                        val sourceEntry = sourceArchive.getEntry(asset.sourcePath)
                            ?: error("素材条目缺失：${asset.sourcePath}")
                        output.putNextEntry(ZipEntry(asset.rewrittenPath))
                        sourceArchive.getInputStream(sourceEntry).use { input ->
                            input.copyTo(output, 1024 * 1024)
                        }
                        output.closeEntry()
                    }
                    output.putNextEntry(ZipEntry("manifest.json"))
                    output.write((writerJson.encodeToString(JsonObject.serializer(), manifest) + "\n").toByteArray())
                    output.closeEntry()
                }
            }
            MobileProjectPackageValidator(destination).validate()
        } catch (error: Exception) {
            destination.delete()
            throw error
        } finally {
            staging.deleteRecursively()
        }
    }

    fun write(
        projectId: String,
        snapshot: List<ReplicaEntity>,
        pendingDraft: MobilePendingChapterDraft?,
        profile: String,
        destination: File,
    ) {
        require(profile in COMMON_PROFILES) { "项目包档位只能是完整或结构" }
        val project = snapshot.singleOrNull { it.entityType == "project" && it.entityId == projectId }
            ?: error("作品不存在")
        val now = java.time.Instant.now().toString()
        val staging = kotlin.io.path.createTempDirectory("mobile-project-package-").toFile()
        try {
            val manifestEntries = mutableListOf<JsonObject>()
            MOBILE_COLLECTION_SPECS.filter { profile in it.profiles }.forEach { spec ->
                val dataFile = File(staging, spec.path).apply { parentFile?.mkdirs() }
                var records = 0
                dataFile.bufferedWriter(Charsets.UTF_8).use { output ->
                    localRows(spec, projectId, snapshot, pendingDraft, profile, now).forEach { row ->
                        output.append(writerJson.encodeToString(JsonObject.serializer(), row)).append('\n')
                        records += 1
                    }
                }
                manifestEntries += JsonObject(
                    mapOf(
                        "path" to JsonPrimitive(spec.path),
                        "media_type" to JsonPrimitive("application/x-ndjson"),
                        "size" to JsonPrimitive(dataFile.length()),
                        "sha256" to JsonPrimitive(sha256File(dataFile)),
                        "records" to JsonPrimitive(records),
                    ),
                )
            }
            val projectPayload = project.payloadObject()
            val title = projectPayload.string("title").ifBlank { "未命名作品" }
            val manifest = JsonObject(
                mapOf(
                    "format" to JsonPrimitive(PROJECT_PACKAGE_FORMAT),
                    "format_version" to JsonPrimitive(PROJECT_PACKAGE_VERSION),
                    "package_id" to JsonPrimitive(UUID.randomUUID().toString()),
                    "profile" to JsonPrimitive(profile),
                    "producer" to JsonObject(
                        mapOf(
                            "name" to JsonPrimitive("siming"),
                            "app_version" to JsonPrimitive(BuildConfig.VERSION_NAME),
                        ),
                    ),
                    "exported_at" to JsonPrimitive(now),
                    "source_project" to JsonObject(
                        mapOf(
                            "id" to JsonPrimitive(projectId),
                            "title" to JsonPrimitive(title),
                        ),
                    ),
                    "entries" to JsonArray(manifestEntries),
                ),
            )
            destination.parentFile?.mkdirs()
            ZipOutputStream(destination.outputStream().buffered()).use { archive ->
                archive.setLevel(Deflater.NO_COMPRESSION)
                manifestEntries.forEach { declaration ->
                    val path = declaration.string("path")
                    archive.putNextEntry(ZipEntry(path))
                    File(staging, path).inputStream().buffered().use { input -> input.copyTo(archive, 1024 * 1024) }
                    archive.closeEntry()
                }
                archive.putNextEntry(ZipEntry("manifest.json"))
                archive.write((writerJson.encodeToString(JsonObject.serializer(), manifest) + "\n").toByteArray())
                archive.closeEntry()
            }
            MobileProjectPackageValidator(destination).validate()
        } catch (error: Exception) {
            destination.delete()
            throw error
        } finally {
            staging.deleteRecursively()
        }
    }

    private fun writeRewrittenRows(
        archive: ApacheZipFile,
        sourceDeclarations: Map<String, JsonObject>,
        spec: MobileCollectionSpec,
        identifierMap: Map<String, String>,
        materialPaths: Map<String, String>,
        projectId: String,
        snapshot: List<ReplicaEntity>,
        pendingDraft: MobilePendingChapterDraft?,
        profile: String,
        now: String,
        finalIdentifiers: Map<String, Set<String>>,
        destination: File,
        referencedAssets: MutableSet<String>,
    ): Int {
        val overrides = spec.localEntityType?.let { entityType ->
            snapshot.filter { it.entityType == entityType }.associateBy(ReplicaEntity::entityId).toMutableMap()
        } ?: mutableMapOf()
        val pendingRow = if (spec.key == "chapter_drafts" && pendingDraft != null) {
            localRows(spec, projectId, emptyList(), pendingDraft, profile, now).single()
        } else {
            null
        }
        val writtenIds = linkedSetOf<String>()
        var records = 0
        destination.bufferedWriter(Charsets.UTF_8).use { output ->
            fun emit(candidate: JsonObject?) {
                if (candidate == null) return
                if (spec.key == "chapter_drafts" && !candidate.isPendingDraft()) return
                val sanitized = sanitizeReferences(spec, candidate, finalIdentifiers) ?: return
                val id = sanitized.string("id")
                if (!writtenIds.add(id)) return
                output.append(writerJson.encodeToString(JsonObject.serializer(), sanitized)).append('\n')
                if (spec.key == "creation_materials") referencedAssets += sanitized.string("asset_path")
                records += 1
            }

            sourceDeclarations[spec.path]?.let { declaration ->
                val entry = archive.getEntry(declaration.string("path"))
                    ?: error("项目包条目缺失：${spec.path}")
                archive.getInputStream(entry).bufferedReader(Charsets.UTF_8).useLines { lines ->
                    lines.filter(String::isNotBlank).forEach { line ->
                        val sourceRow = writerJson.parseToJsonElement(line) as JsonObject
                        var mapped = MobileProjectPackageMaterializer.mapRow(sourceRow, identifierMap)
                        if (spec.key == "creation_materials") {
                            val sourcePath = sourceRow.string("asset_path")
                            mapped = JsonObject(
                                mapped.toMutableMap().apply {
                                    put(
                                        "asset_path",
                                        JsonPrimitive(materialPaths[sourcePath] ?: error("素材条目引用无效：$sourcePath")),
                                    )
                                },
                            )
                        }
                        val id = mapped.string("id")
                        val local = overrides.remove(id)
                        val candidate = when {
                            pendingRow?.string("id") == id -> pendingRow
                            local == null -> mapped
                            local.operation == "upsert" -> replicaRow(spec, local, projectId, profile, now, records)
                            else -> null
                        }
                        emit(candidate)
                    }
                }
            }
            overrides.values.sortedBy(ReplicaEntity::entityId).forEach { local ->
                if (local.operation == "upsert") {
                    emit(replicaRow(spec, local, projectId, profile, now, records))
                }
            }
            if (pendingRow != null && pendingRow.string("id") !in writtenIds) emit(pendingRow)
        }
        return records
    }

    private fun replicaRow(
        spec: MobileCollectionSpec,
        entity: ReplicaEntity,
        projectId: String,
        profile: String,
        now: String,
        index: Int,
    ): JsonObject {
        val payload = entity.payloadObject().toMutableMap().apply {
            put("id", JsonPrimitive(entity.entityId))
            if (entity.entityType != "project") put("project_id", JsonPrimitive(projectId))
        }
        return normalizeRow(spec, JsonObject(payload), projectId, index, profile, now)
    }

    private fun finalIdentifiers(
        validated: ValidatedMobileProjectPackage,
        identifierMap: Map<String, String>,
        snapshot: List<ReplicaEntity>,
        pendingDraft: MobilePendingChapterDraft?,
    ): Map<String, Set<String>> {
        val identifiers = mutableMapOf<String, MutableSet<String>>()
        validated.sourceIdCollections.forEach { (sourceId, collection) ->
            identifiers.getOrPut(collection, ::linkedSetOf) += requireNotNull(identifierMap[sourceId])
        }
        snapshot.forEach { entity ->
            val collection = SPECS_BY_LOCAL_ENTITY_TYPE[entity.entityType]?.key ?: return@forEach
            val active = entity.operation == "upsert" && (
                collection != "chapter_drafts" || entity.payloadObject().isPendingDraft()
                )
            if (active) {
                identifiers.getOrPut(collection, ::linkedSetOf) += entity.entityId
            } else {
                identifiers[collection]?.remove(entity.entityId)
            }
        }
        pendingDraft?.let { identifiers.getOrPut("chapter_drafts", ::linkedSetOf) += it.draftId }
        return identifiers
    }

    private fun sanitizeReferences(
        spec: MobileCollectionSpec,
        row: JsonObject,
        identifiers: Map<String, Set<String>>,
    ): JsonObject? {
        val mutable = row.toMutableMap()
        REFERENCE_TARGETS[spec.key].orEmpty().forEach { (field, targetCollection) ->
            val value = mutable[field]
            if (value == null || value is JsonNull) return@forEach
            val id = (value as? JsonPrimitive)?.contentOrNull
            if (id == null || id !in identifiers[targetCollection].orEmpty()) {
                if (field in REQUIRED_FIELDS[spec.key].orEmpty()) return null
                mutable[field] = JsonNull
            }
        }
        if (spec.key == "causal_edges") {
            val validCharacters = identifiers["characters"].orEmpty()
            val characterIds = (mutable["character_ids"] as? JsonArray).orEmpty().filter { value ->
                (value as? JsonPrimitive)?.contentOrNull in validCharacters
            }
            mutable["character_ids"] = JsonArray(characterIds)
        }
        return JsonObject(mutable)
    }

    private fun materialAssetPaths(
        archive: ApacheZipFile,
        declarations: Map<String, JsonObject>,
        identifierMap: Map<String, String>,
    ): Map<String, String> {
        val declaration = declarations["data/creation_materials.jsonl"] ?: return emptyMap()
        val entry = archive.getEntry(declaration.string("path")) ?: return emptyMap()
        val paths = linkedMapOf<String, String>()
        archive.getInputStream(entry).bufferedReader(Charsets.UTF_8).useLines { lines ->
            lines.filter(String::isNotBlank).forEach { line ->
                val row = writerJson.parseToJsonElement(line) as JsonObject
                val sourcePath = row.string("asset_path")
                val materialId = identifierMap[row.string("id")] ?: error("素材 ID 无效")
                val filename = sourcePath.substringAfterLast('/')
                val rewrittenPath = "assets/materials/$materialId/$filename"
                require(paths.putIfAbsent(sourcePath, rewrittenPath) == null) { "素材条目路径重复：$sourcePath" }
            }
        }
        return paths
    }

    private fun dataDeclaration(path: String, file: File, records: Int): JsonObject = JsonObject(
        mapOf(
            "path" to JsonPrimitive(path),
            "media_type" to JsonPrimitive("application/x-ndjson"),
            "size" to JsonPrimitive(file.length()),
            "sha256" to JsonPrimitive(sha256File(file)),
            "records" to JsonPrimitive(records),
        ),
    )

    private fun packageManifest(
        projectId: String,
        title: String,
        profile: String,
        exportedAt: String,
        entries: List<JsonObject>,
    ): JsonObject = JsonObject(
        mapOf(
            "format" to JsonPrimitive(PROJECT_PACKAGE_FORMAT),
            "format_version" to JsonPrimitive(PROJECT_PACKAGE_VERSION),
            "package_id" to JsonPrimitive(UUID.randomUUID().toString()),
            "profile" to JsonPrimitive(profile),
            "producer" to JsonObject(
                mapOf(
                    "name" to JsonPrimitive("siming"),
                    "app_version" to JsonPrimitive(BuildConfig.VERSION_NAME),
                ),
            ),
            "exported_at" to JsonPrimitive(exportedAt),
            "source_project" to JsonObject(
                mapOf(
                    "id" to JsonPrimitive(projectId),
                    "title" to JsonPrimitive(title),
                ),
            ),
            "entries" to JsonArray(entries),
        ),
    )

    private fun JsonObject.isPendingDraft(): Boolean = string("status") in setOf(
        "pending",
        "generated",
        "generating",
    )

    private data class RetainedAsset(val sourcePath: String, val rewrittenPath: String)

    private fun localRows(
        spec: MobileCollectionSpec,
        projectId: String,
        snapshot: List<ReplicaEntity>,
        pendingDraft: MobilePendingChapterDraft?,
        profile: String,
        now: String,
    ): List<JsonObject> {
        if (spec.key == "chapter_drafts") {
            val draft = pendingDraft ?: return emptyList()
            return listOf(
                normalizeRow(
                    spec,
                    JsonObject(
                        mapOf(
                            "id" to JsonPrimitive(draft.draftId),
                            "project_id" to JsonPrimitive(projectId),
                            "title" to JsonPrimitive(draft.title),
                            "outline_node_id" to (draft.outlineNodeId?.let(::JsonPrimitive) ?: JsonNull),
                            "saved_chapter_id" to JsonNull,
                            "status" to JsonPrimitive("pending"),
                            "content" to JsonPrimitive(draft.content),
                            "created_at" to JsonPrimitive(now),
                            "updated_at" to JsonPrimitive(now),
                        ),
                    ),
                    projectId,
                    0,
                    profile,
                    now,
                ),
            )
        }
        val entityType = spec.localEntityType ?: return emptyList()
        val records = snapshot.filter { it.entityType == entityType && it.operation == "upsert" }
        return records.mapIndexed { index, entity ->
            val payload = entity.payloadObject().toMutableMap().apply {
                put("id", JsonPrimitive(entity.entityId))
                if (entityType != "project") put("project_id", JsonPrimitive(projectId))
            }
            normalizeRow(spec, JsonObject(payload), projectId, index, profile, now)
        }
    }

    private fun normalizeRow(
        spec: MobileCollectionSpec,
        payload: JsonObject,
        projectId: String,
        index: Int,
        profile: String,
        now: String,
    ): JsonObject = JsonObject(
        spec.fields.associateWith { field ->
            val sanitized = if (profile == "structure" && field in STRUCTURE_CLEARED_FIELDS) JsonNull else payload[field]
            normalizeValue(spec.key, field, sanitized, payload, projectId, index, now)
        },
    )

    private fun normalizeValue(
        collection: String,
        field: String,
        value: JsonElement?,
        payload: JsonObject,
        projectId: String,
        index: Int,
        now: String,
    ): JsonElement {
        if (value != null && value !is JsonNull) {
            return when {
                field in JSON_FIELDS -> value
                field in BOOLEAN_FIELDS -> (value as? JsonPrimitive)?.booleanOrNull?.let(::JsonPrimitive)
                    ?: defaultValue(field, payload, projectId, index, now)
                field in INTEGER_FIELDS -> (value as? JsonPrimitive)?.intOrNull?.let(::JsonPrimitive)
                    ?: defaultValue(field, payload, projectId, index, now)
                field in NUMBER_FIELDS -> (value as? JsonPrimitive)?.doubleOrNull?.let(::JsonPrimitive)
                    ?: defaultValue(field, payload, projectId, index, now)
                else -> (value as? JsonPrimitive)?.takeIf { it.isString }
                    ?: JsonPrimitive(value.toString())
            }
        }
        if (field !in REQUIRED_FIELDS[collection].orEmpty()) return JsonNull
        return defaultValue(field, payload, projectId, index, now)
    }

    private fun defaultValue(
        field: String,
        payload: JsonObject,
        projectId: String,
        index: Int,
        now: String,
    ): JsonElement = when (field) {
        "id" -> payload["id"] ?: JsonPrimitive(UUID.randomUUID().toString())
        "project_id" -> JsonPrimitive(projectId)
        "created_at", "updated_at" -> JsonPrimitive(now)
        "title" -> JsonPrimitive(payload.string("title").ifBlank { "未命名" })
        "name" -> JsonPrimitive(payload.string("name").ifBlank { "未命名角色" })
        "node_type" -> JsonPrimitive(payload.string("node_type").ifBlank { "chapter" })
        "dimension" -> JsonPrimitive(payload.string("dimension").ifBlank { "other" })
        "content" -> JsonPrimitive(payload.string("content"))
        "sort_order" -> JsonPrimitive(
            (payload["sort_order"] as? JsonPrimitive)?.intOrNull ?: (index + 1) * 1000,
        )
        "status" -> JsonPrimitive(payload.string("status").ifBlank { "active" })
        "current_version", "version_number", "schema_version", "revision" -> JsonPrimitive(1)
        "word_count" -> JsonPrimitive(payload.string("content").count { !it.isWhitespace() })
        "source", "mode", "artifact_key", "entity_type", "entity_key", "trigger_type",
        "appearance_type", "event_description", "event_type", "change_type", "field_name",
        "cause", "effect", "causal_type", "debt_type", "priority", "importance", "dedupe_key",
        "label", "filename", "file_sha256", "asset_path" -> JsonPrimitive(
            payload.string(field).ifBlank { "mobile" },
        )
        "position", "chapter_version", "findings_count", "sequence", "size_bytes", "input_revision",
        "text_length" -> JsonPrimitive(0)
        "strength" -> JsonPrimitive(1.0)
        "character_ids" -> JsonArray(emptyList())
        "data_json", "state_json", "snapshot_json" -> JsonObject(emptyMap())
        else -> JsonPrimitive(payload.string(field))
    }

    private val STRUCTURE_CLEARED_FIELDS = setOf(
        "source_chapter_id", "last_seen_chapter_id", "last_updated_chapter_id", "first_seen_chapter_id",
        "target_chapter_id", "resolved_chapter_id", "actual_summary", "checkpoints_json",
    )
}

private fun ReplicaEntity.payloadObject(): JsonObject = payloadJson
    ?.let { runCatching { Json.parseToJsonElement(it) as? JsonObject }.getOrNull() }
    ?: JsonObject(emptyMap())

internal fun projectPackageUuid5(
    idempotencyKey: UUID,
    collection: String,
    sourceId: String,
): UUID = uuid5(PROJECT_PACKAGE_ID_NAMESPACE, "$idempotencyKey:$collection:$sourceId")

private fun uuid5(namespace: UUID, name: String): UUID {
    val digest = MessageDigest.getInstance("SHA-1")
    digest.update(namespace.toBytes())
    val bytes = digest.digest(name.toByteArray(Charsets.UTF_8)).copyOf(16)
    bytes[6] = ((bytes[6].toInt() and 0x0f) or 0x50).toByte()
    bytes[8] = ((bytes[8].toInt() and 0x3f) or 0x80).toByte()
    val high = bytes.take(8).fold(0L) { value, byte -> (value shl 8) or (byte.toLong() and 0xff) }
    val low = bytes.drop(8).fold(0L) { value, byte -> (value shl 8) or (byte.toLong() and 0xff) }
    return UUID(high, low)
}

private fun UUID.toBytes(): ByteArray = ByteArray(16).also { bytes ->
    var high = mostSignificantBits
    var low = leastSignificantBits
    for (index in 7 downTo 0) {
        bytes[index] = high.toByte()
        high = high ushr 8
    }
    for (index in 15 downTo 8) {
        bytes[index] = low.toByte()
        low = low ushr 8
    }
}

internal fun sha256File(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().buffered().use { input ->
        val buffer = ByteArray(1024 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.hex()
}

private fun java.util.Enumeration<ZipArchiveEntry>.toListStrict(): List<ZipArchiveEntry> = buildList {
    while (hasMoreElements()) add(nextElement())
}

private fun entryLimit(name: String): Long = when {
    name == "manifest.json" -> MAX_PROJECT_PACKAGE_MANIFEST_BYTES
    name.startsWith("data/") -> MAX_PROJECT_PACKAGE_DATA_BYTES
    name.startsWith("assets/materials/") -> MAX_PROJECT_PACKAGE_MATERIAL_BYTES
    else -> 0L
}

private fun validateArchiveName(name: String) {
    require(name.isNotBlank() && '\\' !in name && '\u0000' !in name) { "项目包包含非法路径" }
    require(!name.startsWith('/') && !Regex("^[A-Za-z]:").containsMatchIn(name)) {
        "项目包包含不安全路径：$name"
    }
    require(name.split('/').none { it.isBlank() || it == "." || it == ".." }) {
        "项目包包含不安全路径：$name"
    }
}

private fun requireExactFields(value: JsonObject, fields: Set<String>, label: String) {
    require(value.keys == fields) {
        val missing = (fields - value.keys).sorted().joinToString()
        val unknown = (value.keys - fields).sorted().joinToString()
        buildString {
            append("$label 字段无效")
            if (missing.isNotBlank()) append("：缺少 $missing")
            if (unknown.isNotBlank()) append("：包含未知字段 $unknown")
        }
    }
}

private fun validateDateTime(value: String, message: String) {
    require(
        runCatching { OffsetDateTime.parse(value) }.isSuccess ||
            runCatching { LocalDateTime.parse(value.removeSuffix("Z")) }.isSuccess,
    ) { message }
}

private fun decodeUtf8(raw: ByteArray, label: String): String {
    val decoder = Charsets.UTF_8.newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
    return runCatching { decoder.decode(java.nio.ByteBuffer.wrap(raw)).toString() }
        .getOrElse { throw IllegalArgumentException("$label 不是有效 UTF-8", it) }
}

private fun InputStream.readBytesLimited(limit: Long): ByteArray {
    val output = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(64 * 1024)
    var total = 0L
    while (true) {
        val count = read(buffer)
        if (count < 0) break
        total += count
        require(total <= limit) { "项目包条目解压超限" }
        output.write(buffer, 0, count)
    }
    return output.toByteArray()
}

private fun MessageDigest.hex(): String = digest().joinToString("") { "%02x".format(it) }

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String): Int =
    (get(name) as? JsonPrimitive)?.takeUnless { it.isString }?.intOrNull
        ?: error("$name 必须是整数")

private fun JsonObject.long(name: String): Long =
    (get(name) as? JsonPrimitive)?.takeUnless { it.isString }?.contentOrNull?.toLongOrNull()
        ?: error("$name 必须是整数")

private fun JsonObject.objectValue(name: String): JsonObject =
    get(name) as? JsonObject ?: error("$name 必须是对象")

private object DiscardingOutputStream : java.io.OutputStream() {
    override fun write(value: Int) = Unit
    override fun write(buffer: ByteArray, offset: Int, length: Int) = Unit
}
