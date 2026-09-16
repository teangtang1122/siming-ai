package com.siming.mobile.data.local

import androidx.sqlite.db.SupportSQLiteDatabase
import java.security.MessageDigest
import kotlinx.serialization.json.*

/** Room 4 -> 5: old offline editors coalesced body changes without advancing versions.
 * Only repair a queued body whose exact base-version snapshot proves it changed.
 * New local chapters and title-only edits retain their existing version.
 */
internal fun advanceQueuedLegacyChapterVersions(db: SupportSQLiteDatabase) {
    data class Row(val key: String, val projectId: String, val chapterId: String, val payload: JsonObject)
    val rows = buildList {
        db.query("SELECT r.`key`, r.projectId, r.entityId, r.payloadJson FROM replica_entities r " +
            "WHERE r.entityType = 'chapter' AND r.operation = 'upsert' AND r.dirty = 1 " +
            "AND EXISTS (SELECT 1 FROM sync_outbox m WHERE m.projectId = r.projectId AND m.entityId = r.entityId " +
            "AND m.entityType = 'chapter' AND m.operation = 'upsert' AND m.state IN ('pending', 'sending'))").use { cursor ->
            while (cursor.moveToNext()) {
                val value = runCatching { Json.parseToJsonElement(cursor.getString(3)).jsonObject }.getOrNull() ?: continue
                add(Row(cursor.getString(0), cursor.getString(1), cursor.getString(2), value))
            }
        }
    }
    for (row in rows) {
        val version = (row.payload["current_version"] as? JsonPrimitive)?.intOrNull ?: continue
        val content = (row.payload["content"] as? JsonPrimitive)?.contentOrNull ?: continue
        val originals = buildList {
            db.query("SELECT payloadJson FROM replica_entities WHERE projectId = ? AND entityType = 'chapter_version' AND operation = 'upsert'", arrayOf(row.projectId)).use { cursor ->
                while (cursor.moveToNext()) {
                    val value = runCatching { Json.parseToJsonElement(cursor.getString(0)).jsonObject }.getOrNull() ?: continue
                    if (value["chapter_id"] == JsonPrimitive(row.chapterId) && value["version_number"] == JsonPrimitive(version) &&
                        value["_record_type"] == JsonPrimitive("chapter_snapshot")) add(value["content"])
                }
            }
        }
        if (originals.isEmpty() || JsonPrimitive(content) in originals) continue
        val payload = JsonObject(row.payload + mapOf("current_version" to JsonPrimitive(version + 1),
            "cataloging_required" to JsonPrimitive(content.isNotBlank()))).toString()
        val hash = MessageDigest.getInstance("SHA-256").digest(payload.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        db.execSQL("UPDATE replica_entities SET payloadJson = ?, contentHash = ? WHERE `key` = ?", arrayOf(payload, hash, row.key))
    }
}
