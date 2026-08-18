package com.siming.mobile.data

import com.siming.mobile.data.local.ReplicaEntity
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

data class MobileExportFile(
    val filename: String,
    val mimeType: String,
    val bytes: ByteArray,
)

data class MobileCatalogingProgress(
    val jobId: String,
    val status: String,
    val totalChapters: Int,
    val completedChapters: Int,
    val failedChapters: Int,
)

private val projectToolsJson = Json { ignoreUnknownKeys = true }

internal fun JsonObject.toMobileCatalogingProgress(): MobileCatalogingProgress =
    MobileCatalogingProgress(
        jobId = (get("id") as? JsonPrimitive)?.contentOrNull.orEmpty(),
        status = (get("status") as? JsonPrimitive)?.contentOrNull.orEmpty(),
        totalChapters = (get("total_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
        completedChapters = (get("completed_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
        failedChapters = (get("failed_chapters") as? JsonPrimitive)?.intOrNull ?: 0,
    )

internal fun exportMimeType(format: String): String = when (format.lowercase()) {
    "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    "pdf" -> "application/pdf"
    else -> "text/plain"
}

internal fun buildLocalNovelExport(
    project: ReplicaEntity,
    chapters: List<ReplicaEntity>,
): MobileExportFile {
    val title = project.payloadText("title").ifBlank { "未命名作品" }
    val safeTitle = title
        .replace(Regex("[\\\\/:*?\"<>|]"), "_")
        .trim()
        .ifBlank { "司命导出" }
        .take(80)
    val content = buildString {
        append(title).append("\n\n")
        chapters.forEachIndexed { index, chapter ->
            val chapterTitle = chapter.payloadText("title").ifBlank { "第 ${index + 1} 章" }
            append(chapterTitle).append("\n\n")
            append(chapter.payloadText("content").trim()).append("\n\n")
        }
    }.trimEnd() + "\n"
    return MobileExportFile(
        filename = "$safeTitle.txt",
        mimeType = "text/plain",
        bytes = content.toByteArray(Charsets.UTF_8),
    )
}

private fun ReplicaEntity.payloadText(key: String): String {
    val payload = payloadJson
        ?.let { runCatching { projectToolsJson.parseToJsonElement(it) as? JsonObject }.getOrNull() }
        ?: return ""
    return (payload[key] as? JsonPrimitive)?.contentOrNull.orEmpty()
}
