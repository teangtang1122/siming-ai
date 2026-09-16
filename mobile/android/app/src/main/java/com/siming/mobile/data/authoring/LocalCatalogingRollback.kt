package com.siming.mobile.data.authoring

import com.siming.mobile.data.cataloging.CatalogRecord
import com.siming.mobile.data.cataloging.catalogingHash
import com.siming.mobile.data.local.*
import java.time.Instant
import kotlinx.serialization.json.*

/** Runs inside the caller's Room transaction, just like the PC chapter boundary rollback. */
internal class LocalCatalogingRollback(private val database: SimingDatabase) {
    private val dao = database.dao()

    suspend fun rollback(projectId: String, chapterId: String): List<String> {
        val original = dao.projectSnapshot(projectId).associateBy { it.key }
        val chapters = orderReplicaEntities("chapter", original.values.filter { it.entityType == "chapter" })
        val start = chapters.indexOfFirst { it.entityId == chapterId }
        require(start >= 0) { "章节不属于当前作品" }
        val affected = chapters.drop(start).map { it.entityId }.toSet()
        val now = Instant.now().toString()
        val rows = original.toMutableMap()
        fun put(row: ReplicaEntity, payload: JsonObject) {
            val raw = payload.toString()
            rows[row.key] = row.copy(payloadJson = raw, contentHash = catalogingHash(raw), dirty = true)
        }
        fun remove(row: ReplicaEntity) { rows.remove(row.key) }
        fun key(row: CatalogRecord) = ReplicaEntity.key(projectId, row.entityType, row.id)
        // Undo completed mobile projections in reverse application order. An author's
        // subsequent edit is never overwritten with the older generated value.
        for (run in dao.catalogingRuns(projectId).filter { it.chapterId in affected }) {
            if (run.status == "completed" && run.changesJson != null) {
                val before = Json.parseToJsonElement(run.sourceJson).jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }.associateBy(::key)
                val changes = Json.parseToJsonElement(run.changesJson).jsonObject
                for (value in changes.getValue("upserts").jsonArray.reversed()) {
                    val applied = CatalogRecord.fromJson(value.jsonObject)
                    if (applied.entityType == "chapter" || applied.recordType == "chapter_snapshot") continue
                    val current = rows[key(applied)] ?: continue
                    if (current.payload() != applied.payload) continue
                    val prior = before[current.key]
                    if (prior != null) put(current, prior.payload)
                    else if (!hasExternalReference(current.entityId, affected, rows.values)) remove(current)
                }
                for (value in changes.getValue("deletes").jsonArray) {
                    val deleted = CatalogRecord.fromJson(value.jsonObject)
                    if (key(deleted) !in rows) {
                        val raw = deleted.payload.toString()
                        rows[key(deleted)] = ReplicaEntity(key(deleted), projectId, deleted.entityType, deleted.id, 0, "upsert", raw, catalogingHash(raw), now, true)
                    }
                }
            }
            if (run.status in setOf("running", "completed")) dao.saveCatalogingRun(run.copy(
                status = "invalidated", error = "正文已修改，建档结果已失效，请重新建档", updatedAt = now,
            ))
        }
        val owned = setOf("chapter_summary", "chapter_character", "chapter_worldbuilding", "character_change_log",
            "character_timeline", "world_timeline", "character_narrative_state", "chapter_quality_metric", "narrative_checkpoint")
        val sourceOwned = setOf("character_alias", "character_version", "world_version")
        // Imported archives carry these source IDs too, even when they predate local journals.
        for (row in rows.values.toList()) {
            val p = row.payload(); val type = p.text("_record_type")
            when {
                type in owned && p.text("chapter_id") in affected -> remove(row)
                type in sourceOwned && p.text("source_chapter_id") in affected -> remove(row)
                type == "cataloging_fact" && p.text("chapter_id") in affected -> put(row, JsonObject(p + ("status" to JsonPrimitive("superseded"))))
                type == "chapter_governance_review" && p.text("chapter_id") in affected ->
                    put(row, JsonObject(p + mapOf("status" to JsonPrimitive("stale"), "reviewed_at" to JsonNull)))
                type in setOf("foreshadowing", "causal_edge", "narrative_debt") &&
                    listOf("source_chapter_id", "target_chapter_id", "resolved_chapter_id").any { p.text(it) in affected } -> {
                    val events = rows.values.filter { it.payload().let { event -> event.text("_record_type") == "narrative_governance_event" && event.text("item_id") == row.entityId } }
                    if (p.text("source") == "cataloging" && p.text("source_chapter_id") in affected && events.all { it.payload().text("chapter_id") in affected }) {
                        remove(row); events.forEach(::remove)
                    } else put(row, JsonObject(p + mapOf("status" to JsonPrimitive("stale"), "stale_reason" to JsonPrimitive("正文已修改，需重新核对"), "verified_at" to JsonNull, "verification_note" to JsonNull, "closed_by" to JsonNull)))
                }
                type == "outline_node" && p.text("source_chapter_id") in affected && p.text("cataloging_status") == "cataloged" -> {
                    val bound = chapters.any { it.payload().text("outline_node_id") == row.entityId }
                    if (bound || hasExternalReference(row.entityId, affected, rows.values)) {
                        put(row, JsonObject(p + mapOf("source_chapter_id" to JsonNull, "actual_summary" to JsonNull,
                            "summary" to JsonPrimitive(p.text("planned_summary")), "cataloging_status" to JsonNull)))
                    } else remove(row)
                }
            }
        }
        val ledger = rows.values.filter { row -> row.payload().let { it.text("_record_type") == "cataloging_fact" && it.text("fact_type") == "narrative_ledger_entry" && it.text("chapter_id") !in affected } }
            .sortedWith(compareBy<ReplicaEntity> { it.payload().text("created_at") }.thenBy { it.entityId })
            .groupBy { row -> row.payload().parsedObject("raw_payload").let { it.text("ledger_type").ifBlank { "event" } to it.text("ledger_key") } }
        ledger.filterKeys { it.second.isNotBlank() }.values.forEach { facts -> facts.forEachIndexed { index, row ->
            put(row, JsonObject(row.payload() + ("status" to JsonPrimitive(if (index == facts.lastIndex) "active" else "superseded"))))
        } }
        // Refresh provenance from surviving versions instead of keeping references to invalid chapters.
        for (row in rows.values.toList().filter { it.payload().text("_record_type") in setOf("character", "world_entry") }) {
            val p = row.payload()
            val ownerKey = if (row.entityType == "character") "character_id" else "entry_id"
            val versions = rows.values.map { it.payload() }.filter { it.text(ownerKey) == row.entityId && it.text("_record_type") in setOf("character_version", "world_version") }
            val latest = versions.maxByOrNull { it.number("version_number") }
            val fields = mutableMapOf<String, JsonElement>()
            if (p.text("last_updated_chapter_id") in affected) fields["last_updated_chapter_id"] = latest?.get("source_chapter_id") ?: JsonNull
            if (p.text("last_seen_chapter_id") in affected) fields["last_seen_chapter_id"] = chapters.lastOrNull { ch -> ch.entityId !in affected && rows.values.any { link -> link.payload().let { it.text("chapter_id") == ch.entityId && it.text(ownerKey) == row.entityId } } }?.entityId?.let(::JsonPrimitive) ?: JsonNull
            if (row.entityType == "character") fields["current_version"] = JsonPrimitive(latest?.number("version_number", 1) ?: 1)
            if (fields.isNotEmpty()) put(row, JsonObject(p + fields))
        }
        affected.forEach { id ->
            val row = rows.getValue(ReplicaEntity.key(projectId, "chapter", id))
            put(row, JsonObject(row.payload() + ("cataloging_required" to JsonPrimitive(row.payload().text("content").isNotBlank()))))
        }
        for ((key, old) in original) if (key !in rows) dao.saveEntity(old.copy(operation = "delete", payloadJson = null, contentHash = catalogingHash("null"), dirty = true))
        for ((key, row) in rows) if (original[key] != row) dao.saveEntity(row)
        return affected.toList()
    }

    private fun hasExternalReference(id: String, affected: Set<String>, rows: Collection<ReplicaEntity>): Boolean = rows.any { row ->
        if (row.entityId == id) false else {
            val p = row.payload()
            val chapter = p.text("chapter_id").ifBlank { p.text("source_chapter_id") }
            chapter !in affected && listOf("character_id", "entry_id", "from", "to", "character_a_id", "character_b_id", "source_entry_id", "target_entry_id", "parent_id", "outline_node_id").any { p.text(it) == id }
        }
    }
}
