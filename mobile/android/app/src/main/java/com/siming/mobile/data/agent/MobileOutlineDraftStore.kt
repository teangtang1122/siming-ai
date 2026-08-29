package com.siming.mobile.data.agent

import android.content.Context
import java.io.File
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** Durable author-review slot for Android standalone outline proposals. */
internal class MobileOutlineDraftStore(
    private val directory: File,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    constructor(context: Context) : this(
        File(context.applicationContext.filesDir, DIRECTORY_NAME),
    )

    private val mutex = Mutex()

    suspend fun load(draftId: String): MobileOutlineDraftRun? = withContext(Dispatchers.IO) {
        mutex.withLock { readLocked(file(draftId)) }
    }

    suspend fun save(run: MobileOutlineDraftRun): MobileOutlineDraftRun = withContext(Dispatchers.IO) {
        mutex.withLock {
            directory.mkdirs()
            val now = Instant.now().toString()
            val normalized = run.copy(
                createdAt = run.createdAt.ifBlank { now },
                updatedAt = now,
            )
            if (normalized.state == MobileOutlineDraftState.PENDING) {
                validatePendingNodes(normalized.nodes)
                val conflicting = directory
                    .listFiles { item -> item.isFile && item.extension == "json" }
                    .orEmpty()
                    .asSequence()
                    .mapNotNull(::readLocked)
                    .firstOrNull {
                        it.projectId == normalized.projectId &&
                            it.id != normalized.id &&
                            it.state == MobileOutlineDraftState.PENDING
                    }
                if (conflicting != null) throw MobilePendingOutlineDraftConflict(conflicting.id)
            }
            val target = file(run.id)
            val temporary = File(directory, ".${target.name}.${UUID.randomUUID()}.tmp")
            temporary.writeText(normalized.toJson().toString(), Charsets.UTF_8)
            if (!temporary.renameTo(target)) {
                target.writeText(temporary.readText(Charsets.UTF_8), Charsets.UTF_8)
                temporary.delete()
            }
            pruneLocked()
            normalized
        }
    }

    suspend fun latestPending(projectId: String): MobileOutlineDraftRun? = withContext(Dispatchers.IO) {
        mutex.withLock {
            directory.listFiles { item -> item.isFile && item.extension == "json" }
                .orEmpty()
                .asSequence()
                .sortedByDescending(File::lastModified)
                .mapNotNull(::readLocked)
                .firstOrNull { it.projectId == projectId && it.state == MobileOutlineDraftState.PENDING }
        }
    }

    suspend fun markConfirmed(draftId: String, savedIds: List<String>): MobileOutlineDraftRun? {
        val run = load(draftId) ?: return null
        return save(run.copy(state = MobileOutlineDraftState.CONFIRMED, savedOutlineNodeIds = savedIds))
    }

    suspend fun markDiscarded(draftId: String): MobileOutlineDraftRun? {
        val run = load(draftId) ?: return null
        return save(run.copy(state = MobileOutlineDraftState.DISCARDED))
    }

    suspend fun markSuperseded(draftId: String): MobileOutlineDraftRun? {
        val run = load(draftId) ?: return null
        return save(run.copy(state = MobileOutlineDraftState.SUPERSEDED))
    }

    private fun readLocked(target: File): MobileOutlineDraftRun? {
        if (!target.isFile) return null
        return runCatching {
            MobileOutlineDraftRun.fromJson(json.parseToJsonElement(target.readText(Charsets.UTF_8)) as JsonObject)
        }.getOrNull()
    }

    private fun file(draftId: String): File {
        require(draftId.matches(DRAFT_ID_PATTERN)) { "无效的手机大纲草稿 ID" }
        return File(directory, "$draftId.json")
    }

    private fun pruneLocked() {
        directory.listFiles { item -> item.isFile && item.extension == "json" }
            ?.sortedByDescending(File::lastModified)
            .orEmpty()
            .drop(MAX_DRAFTS)
            .forEach(File::delete)
    }

    private fun validatePendingNodes(nodes: JsonArray) {
        require(nodes.isNotEmpty()) { "大纲草稿至少需要一个节点" }
        require(nodes.size <= 8) { "单次大纲草稿最多包含 8 个节点" }
        val values = nodes.map { element ->
            element as? JsonObject ?: throw IllegalArgumentException("大纲草稿节点格式无效")
        }
        val titles = values.map { node -> node.string("title").trim() }
        require(titles.all { title -> title.isNotBlank() && title.length <= 200 }) {
            "大纲草稿节点标题必须为 1 至 200 个字符"
        }
        require(titles.distinct().size == titles.size) { "大纲草稿节点标题不能重复" }
        require(values.all { node -> node.string("node_type").ifBlank { "chapter" } in NODE_TYPES }) {
            "大纲草稿节点类型无效"
        }
        val byTitle = values.associateBy { node -> node.string("title").trim() }
        val visiting = mutableSetOf<String>()
        val visited = mutableSetOf<String>()
        fun visit(title: String) {
            if (title in visited) return
            require(visiting.add(title)) { "大纲草稿父子关系形成循环" }
            val parentTitle = byTitle.getValue(title).string("parent_title").trim()
            if (parentTitle.isNotBlank()) {
                require(parentTitle in byTitle) { "大纲草稿引用了不存在的父标题：$parentTitle" }
                visit(parentTitle)
                val parentType = byTitle.getValue(parentTitle).string("node_type").ifBlank { "chapter" }
                val childType = byTitle.getValue(title).string("node_type").ifBlank { "chapter" }
                require(childType in CHILD_TYPES.getValue(parentType)) {
                    "$parentTitle 下不能创建 $childType 类型节点"
                }
            }
            visiting.remove(title)
            visited += title
        }
        titles.forEach(::visit)
    }

    companion object {
        private const val DIRECTORY_NAME = "mobile-outline-drafts"
        private const val MAX_DRAFTS = 64
        private val DRAFT_ID_PATTERN = Regex("[a-z0-9-]{8,96}")
        private val NODE_TYPES = setOf("volume", "chapter", "section")
        private val CHILD_TYPES = mapOf(
            "volume" to setOf("chapter"),
            "chapter" to setOf("section"),
            "section" to emptySet(),
        )
    }
}

internal data class MobileOutlineDraftRun(
    val id: String,
    val projectId: String,
    val model: String,
    val parentId: String,
    val insertAfterId: String,
    val nodes: JsonArray,
    val designNotes: String,
    val state: String,
    val manifest: MobileContextManifest,
    val baseOutlineHash: String,
    val savedOutlineNodeIds: List<String> = emptyList(),
    val createdAt: String = "",
    val updatedAt: String = "",
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("schema_version", SCHEMA_VERSION)
        put("id", id)
        put("project_id", projectId)
        put("model", model)
        put("parent_id", parentId)
        put("insert_after_id", insertAfterId)
        put("nodes", nodes)
        put("design_notes", designNotes)
        put("state", state)
        put("context_selection_digest", manifest.selectionFingerprint)
        put("base_outline_hash", baseOutlineHash)
        put("saved_outline_node_ids", JsonArray(savedOutlineNodeIds.map(::JsonPrimitive)))
        put("created_at", createdAt)
        put("updated_at", updatedAt)
        put("request", manifest.request.toJson())
        put("context_manifest", manifest.toJson(includeContent = true))
    }

    companion object {
        private const val SCHEMA_VERSION = 1

        fun fromJson(root: JsonObject): MobileOutlineDraftRun {
            require(root.int("schema_version", SCHEMA_VERSION) == SCHEMA_VERSION) {
                "不支持的手机大纲草稿版本"
            }
            val request = MobileContextRequest.fromJson(root.objectValue("request"))
            val manifest = MobileContextManifest.fromJson(root.objectValue("context_manifest"), request)
            return MobileOutlineDraftRun(
                id = root.string("id"),
                projectId = root.string("project_id"),
                model = root.string("model"),
                parentId = root.string("parent_id"),
                insertAfterId = root.string("insert_after_id"),
                nodes = root["nodes"] as? JsonArray ?: JsonArray(emptyList()),
                designNotes = root.string("design_notes"),
                state = root.string("state"),
                manifest = manifest,
                baseOutlineHash = root.string("base_outline_hash"),
                savedOutlineNodeIds = (root["saved_outline_node_ids"] as? JsonArray)
                    .orEmpty()
                    .mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
                createdAt = root.string("created_at"),
                updatedAt = root.string("updated_at"),
            ).also { run ->
                require(run.id.isNotBlank() && run.projectId.isNotBlank()) { "手机大纲草稿缺少标识" }
                require(run.state in MobileOutlineDraftState.ALL) { "手机大纲草稿状态无效" }
                require(run.manifest.projectId == run.projectId) { "ContextManifest 作品不匹配" }
                require(run.manifest.request.taskType == "outline_planning") { "ContextManifest 不是大纲规划任务" }
                require(run.baseOutlineHash.length == 64) { "手机大纲草稿缺少正式大纲指纹" }
            }
        }
    }
}

internal object MobileOutlineDraftState {
    const val PENDING = "pending"
    const val CONFIRMED = "confirmed"
    const val DISCARDED = "discarded"
    const val SUPERSEDED = "superseded"
    val ALL = setOf(PENDING, CONFIRMED, DISCARDED, SUPERSEDED)
}

internal class MobilePendingOutlineDraftConflict(val draftId: String) :
    IllegalStateException("作品已有待处理大纲草稿：$draftId")

internal fun mobileOutlineTreeHash(nodes: List<JsonObject>): String = mobileSha256(
    nodes
        .sortedBy { node -> (node["id"] as? JsonPrimitive)?.contentOrNull.orEmpty() }
        .joinToString("\u001e") { node -> canonicalMobileJson(node) },
)

internal fun mobileOutlineDraftId(
    projectId: String,
    model: String,
    manifest: MobileContextManifest,
): String = "outline-${mobileSha256(
    listOf(
        projectId,
        model,
        manifest.requestFingerprint,
        manifest.selectionFingerprint,
        manifest.policySourceHash,
    ).joinToString("\u001f"),
).take(48)}"

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String, fallback: Int): Int =
    string(name).toIntOrNull() ?: fallback

private fun JsonObject.objectValue(name: String): JsonObject =
    get(name) as? JsonObject ?: JsonObject(emptyMap())
