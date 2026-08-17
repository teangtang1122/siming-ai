package com.siming.mobile.data.local

import java.text.Normalizer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Return chapter replicas in canonical reading order.
 *
 * New PC/Gateway snapshots carry Chapter.sort_order, which is the only
 * authoritative cross-device ordering signal. Title parsing remains strictly
 * as a compatibility fallback for old/offline replicas that predate that field.
 */
fun orderReplicaEntities(entityType: String, records: List<ReplicaEntity>): List<ReplicaEntity> {
    if (entityType != "chapter" || records.size < 2) return records

    val items = records.map { record -> ChapterOrder(record, payload(record)) }
    val canonical = items.filter { it.sortOrder != null }
    if (canonical.isNotEmpty()) {
        val legacy = legacyOrder(items.filter { it.sortOrder == null })
        return canonical.sortedWith(canonicalChapterOrder).map(ChapterOrder::record) +
            legacy.map(ChapterOrder::record)
    }
    return legacyOrder(items).map(ChapterOrder::record)
}

private data class ChapterOrder(
    val record: ReplicaEntity,
    val payload: JsonObject?,
) {
    val sortOrder = payload?.get("sort_order")?.jsonPrimitive?.let { value ->
        value.intOrNull ?: value.contentOrNull?.toIntOrNull()
    }
    val createdAt = payload?.string("created_at")?.takeIf(String::isNotBlank)
    val titleNumber = payload?.string("title")?.let(::chapterNumber)
}

private val canonicalChapterOrder =
    compareBy<ChapterOrder> { it.sortOrder ?: Int.MAX_VALUE }
        .thenBy { it.createdAt == null }
        .thenBy { it.createdAt.orEmpty() }
        .thenBy { it.record.entityId }

private fun legacyOrder(items: List<ChapterOrder>): List<ChapterOrder> {
    val fallbackOrdered = items.sortedWith(chapterFallbackOrder)
    if (fallbackOrdered.count { it.titleNumber != null } < 2) return fallbackOrdered
    val numbered = fallbackOrdered
        .filter { it.titleNumber != null }
        .sortedWith(numberedChapterOrder)
        .iterator()
    return fallbackOrdered.map { item ->
        if (item.titleNumber == null) item else numbered.next()
    }
}

private val chapterFallbackOrder =
    compareBy<ChapterOrder> { it.createdAt == null }
        .thenBy { it.createdAt.orEmpty() }
        .thenBy { it.record.localModifiedAt }
        .thenBy { it.record.entityId }

private val numberedChapterOrder =
    compareBy<ChapterOrder> { it.titleNumber ?: Int.MAX_VALUE }
        .thenBy { it.createdAt == null }
        .thenBy { it.createdAt.orEmpty() }
        .thenBy { it.record.localModifiedAt }
        .thenBy { it.record.entityId }

private const val MAX_CHAPTER_NUMBER = 99_999
private const val CHINESE_NUMBER_CHARS = "零〇○一二两三四五六七八九十百千万"
private val chineseDigits = mapOf(
    '零' to 0,
    '〇' to 0,
    '○' to 0,
    '一' to 1,
    '二' to 2,
    '两' to 2,
    '三' to 3,
    '四' to 4,
    '五' to 5,
    '六' to 6,
    '七' to 7,
    '八' to 8,
    '九' to 9,
)
private val chineseUnits = mapOf(
    '十' to 10,
    '百' to 100,
    '千' to 1_000,
    '万' to 10_000,
)
private val chapterNumberToken =
    """[0-9０-９$CHINESE_NUMBER_CHARS](?:[0-9０-９$CHINESE_NUMBER_CHARS]|\s)*?"""
private val chapterNumberPatterns = listOf(
    Regex("""第\s*($chapterNumberToken)\s*[章节回]"""),
    Regex("""(?:^|\s)($chapterNumberToken)\s*[章节回]"""),
    Regex("""(?:chapter|chap\.?)\s*(\d{1,5})""", RegexOption.IGNORE_CASE),
    Regex("""^\s*(\d{1,5})\b"""),
)

private fun chapterNumber(title: String): Int? {
    val normalized = Normalizer.normalize(title, Normalizer.Form.NFKC)
    return chapterNumberPatterns.firstNotNullOfOrNull { pattern ->
        pattern.find(normalized)
            ?.groupValues
            ?.getOrNull(1)
            ?.let(::parseChapterNumber)
    }
}

private fun parseChapterNumber(text: String): Int? {
    val number = chineseNumberToInt(text) ?: return null
    return number.takeIf { it in 1..MAX_CHAPTER_NUMBER }
}

private fun chineseNumberToInt(text: String): Int? {
    val value = Regex("""\s+""").replace(
        Normalizer.normalize(text, Normalizer.Form.NFKC),
        "",
    )
    if (value.isBlank() || value.length > 32) return null
    value.toIntOrNull()?.let { return it }
    if (value.any { it !in chineseDigits && it !in chineseUnits }) return null

    if (value.none { it in chineseUnits }) {
        return value
            .map { chineseDigits[it] ?: return null }
            .joinToString("")
            .toIntOrNull()
    }

    var total = 0L
    var section = 0L
    var number: Int? = null
    var lastSmallUnit = 10_000
    var seenWan = false
    for (char in value) {
        val digit = chineseDigits[char]
        if (digit != null) {
            if (number != null && (number != 0 || digit == 0)) return null
            if (digit == 0 && total == 0L && section == 0L && number == null) return null
            number = digit
            continue
        }

        val unit = chineseUnits[char] ?: return null
        if (unit == 10_000) {
            if (seenWan) return null
            var base = section + (number ?: 0)
            if (base == 0L) base = 1L
            total += base * unit
            section = 0L
            number = null
            lastSmallUnit = 10_000
            seenWan = true
        } else {
            if (unit >= lastSmallUnit || number == 0) return null
            section += (number ?: 1) * unit
            number = null
            lastSmallUnit = unit
        }
    }
    if (number == 0) return null
    val result = total + section + (number ?: 0)
    return result.takeIf { it <= Int.MAX_VALUE }?.toInt()
}

private fun payload(record: ReplicaEntity): JsonObject? = record.payloadJson?.let { raw ->
    runCatching { replicaJson.parseToJsonElement(raw) as? JsonObject }.getOrNull()
}

private fun JsonObject.string(name: String): String = get(name)?.jsonPrimitive?.contentOrNull.orEmpty()

private val replicaJson = Json {
    ignoreUnknownKeys = true
}
