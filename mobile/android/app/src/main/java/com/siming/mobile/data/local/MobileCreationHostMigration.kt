package com.siming.mobile.data.local

import androidx.sqlite.db.SupportSQLiteDatabase
import com.siming.mobile.data.cataloging.catalogingHash
import kotlinx.serialization.json.*

/** Schema 5 -> 6 only: retained phone-key creation drafts become device-owned. */
internal fun migrateMobileCreationHosts(database: SupportSQLiteDatabase) {
    database.query("SELECT key, payloadJson FROM replica_entities WHERE entityType = 'creation_session' AND operation = 'upsert'").use { cursor ->
        while (cursor.moveToNext()) {
            val session = runCatching { Json.parseToJsonElement(cursor.getString(1)).jsonObject }.getOrNull() ?: continue
            val draft = session["draft"] as? JsonObject ?: continue
            if ((draft["execution_route"] as? JsonPrimitive)?.contentOrNull != "mobile" ||
                (draft["execution_host"] as? JsonPrimitive)?.contentOrNull != "gateway") continue
            val updated = JsonObject(session + ("draft" to JsonObject(draft + ("execution_host" to JsonPrimitive("device")))))
            val raw = updated.toString()
            database.execSQL("UPDATE replica_entities SET payloadJson = ?, contentHash = ? WHERE key = ?", arrayOf(raw, catalogingHash(raw), cursor.getString(0)))
        }
    }
}
