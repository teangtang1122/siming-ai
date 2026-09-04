package com.siming.mobile.data

import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalQueries
import java.util.Locale

private val compactOffset = Regex("([+-]\\d{2})(\\d{2})$")
private val recordTimeFormat = DateTimeFormatter.ofPattern("yyyy/MM/dd HH:mm:ss", Locale.SIMPLIFIED_CHINESE)

/** Database timestamps without an offset are UTC, just like the PC API contract. */
internal fun parseApiDateTime(value: String?): Instant? {
    val text = value?.trim()?.takeIf(String::isNotEmpty) ?: return null
    return runCatching {
        val normalized = text.replace(' ', 'T').replace(compactOffset, "$1:$2")
        val parsed = DateTimeFormatter.ISO_DATE_TIME.parse(normalized)
        val offset = parsed.query(TemporalQueries.offset()) ?: ZoneOffset.UTC
        LocalDateTime.from(parsed).toInstant(offset)
    }.getOrNull()
}

internal fun formatApiDateTime(value: String?, zone: ZoneId = ZoneId.systemDefault()): String? =
    parseApiDateTime(value)?.let { recordTimeFormat.withZone(zone).format(it) }
