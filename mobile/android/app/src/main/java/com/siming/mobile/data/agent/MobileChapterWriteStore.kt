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
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/**
 * Durable journal for Android standalone chapter generation and commit.
 *
 * A generated draft and its full ContextManifest are written atomically before
 * the workspace agent can ask to create/update a chapter.  The deterministic
 * run and entity identifiers make retries after process death coalesce onto the
 * same draft and chapter instead of producing a second chapter.
 */
internal class MobileChapterWriteStore(
    private val directory: File,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    constructor(context: Context) : this(
        File(context.applicationContext.filesDir, DIRECTORY_NAME),
    )

    private val mutex = Mutex()

    suspend fun load(runId: String): MobileChapterWriteRun? = withContext(Dispatchers.IO) {
        mutex.withLock {
            val target = file(runId)
            if (!target.isFile) return@withLock null
            runCatching {
                val root = json.parseToJsonElement(target.readText(Charsets.UTF_8)) as JsonObject
                MobileChapterWriteRun.fromJson(root)
            }.getOrNull()
        }
    }

    suspend fun save(run: MobileChapterWriteRun): MobileChapterWriteRun = withContext(Dispatchers.IO) {
        mutex.withLock {
            directory.mkdirs()
            val now = Instant.now().toString()
            val normalized = run.copy(
                createdAt = run.createdAt.ifBlank { now },
                updatedAt = now,
            )
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

    suspend fun transition(
        run: MobileChapterWriteRun,
        state: String,
        chapterId: String? = run.chapterId,
        error: String? = null,
    ): MobileChapterWriteRun = save(
        run.copy(
            state = state,
            chapterId = chapterId,
            error = error?.take(MAX_ERROR_CHARS),
        ),
    )

    private fun file(runId: String): File {
        require(runId.matches(RUN_ID_PATTERN)) { "无效的手机写章运行 ID" }
        return File(directory, "$runId.json")
    }

    private fun pruneLocked() {
        val files = directory.listFiles { item -> item.isFile && item.extension == "json" }
            ?.sortedByDescending(File::lastModified)
            .orEmpty()
        files.drop(MAX_RUNS).forEach(File::delete)
    }

    companion object {
        private const val DIRECTORY_NAME = "mobile-chapter-write-runs"
        private const val MAX_RUNS = 64
        private const val MAX_ERROR_CHARS = 2_000
        private val RUN_ID_PATTERN = Regex("[a-z0-9-]{8,96}")
    }
}

internal data class MobileChapterWriteRun(
    val id: String,
    val projectId: String,
    val model: String,
    val title: String,
    val content: String,
    val state: String,
    val manifest: MobileContextManifest,
    val chapterId: String? = null,
    val error: String? = null,
    val createdAt: String = "",
    val updatedAt: String = "",
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("schema_version", SCHEMA_VERSION)
        put("id", id)
        put("project_id", projectId)
        put("model", model)
        put("title", title)
        put("content", content)
        put("state", state)
        chapterId?.let { put("chapter_id", it) }
        error?.let { put("error", it) }
        put("created_at", createdAt)
        put("updated_at", updatedAt)
        put("request", manifest.request.toJson())
        put("context_manifest", manifest.toJson(includeContent = true))
    }

    companion object {
        private const val SCHEMA_VERSION = 1

        fun fromJson(root: JsonObject): MobileChapterWriteRun {
            require(root.int("schema_version", SCHEMA_VERSION) == SCHEMA_VERSION) {
                "不支持的手机写章运行版本"
            }
            val request = MobileContextRequest.fromJson(root.objectValue("request"))
            val manifest = MobileContextManifest.fromJson(
                root.objectValue("context_manifest"),
                request,
            )
            return MobileChapterWriteRun(
                id = root.string("id"),
                projectId = root.string("project_id"),
                model = root.string("model"),
                title = root.string("title"),
                content = root.string("content"),
                state = root.string("state"),
                manifest = manifest,
                chapterId = root.string("chapter_id").ifBlank { null },
                error = root.string("error").ifBlank { null },
                createdAt = root.string("created_at"),
                updatedAt = root.string("updated_at"),
            ).also { run ->
                require(run.id.isNotBlank() && run.projectId.isNotBlank()) { "手机写章运行缺少标识" }
                require(run.state in MobileChapterWriteState.ALL) { "手机写章运行状态无效" }
                require(run.manifest.projectId == run.projectId) { "ContextManifest 作品不匹配" }
            }
        }
    }
}

internal object MobileChapterWriteState {
    const val GENERATING = "generating"
    const val GENERATED = "generated"
    const val COMMITTING = "committing"
    const val COMMITTED = "committed"
    const val CANCELLED = "cancelled"
    const val FAILED = "failed"
    val ALL = setOf(GENERATING, GENERATED, COMMITTING, COMMITTED, CANCELLED, FAILED)
}

internal fun mobileChapterWriteRunId(
    projectId: String,
    model: String,
    manifest: MobileContextManifest,
): String = "chapter-${mobileSha256(
    listOf(
        projectId,
        model,
        manifest.requestFingerprint,
        manifest.selectionFingerprint,
        manifest.policySourceHash,
    ).joinToString("\u001f"),
).take(48)}"

internal fun mobileChapterEntityId(projectId: String, runId: String): String =
    UUID.nameUUIDFromBytes("siming-mobile-chapter:$projectId:$runId".toByteArray(Charsets.UTF_8)).toString()

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String, fallback: Int): Int =
    string(name).toIntOrNull() ?: fallback

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
