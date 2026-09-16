package com.siming.mobile.data.authoring

import androidx.room.withTransaction
import com.siming.mobile.data.agent.mobileChapterPayloadForSave
import com.siming.mobile.data.cataloging.catalogingHash
import com.siming.mobile.data.cataloging.catalogingId
import com.siming.mobile.data.local.*
import com.siming.mobile.data.network.PcApiPayloads
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.*

/** The sole mobile authoring write boundary. Domain state and pending sync commit together. */
internal class LocalAuthoringStore(private val database: SimingDatabase) {
    private val dao = database.dao()
    val read = LocalAuthoringRead(dao)

    suspend fun save(
        projectId: String, type: String, id: String, input: JsonObject,
        trigger: String = "manual_save", expectedVersion: Int? = null,
    ): String = database.withTransaction {
        val before = dao.projectPackageSnapshot(projectId).associateBy { it.key }
        if (type != "project") read.requireEntity(projectId, "project", projectId)
        require(input.text("project_id").let { it.isEmpty() || it == projectId }) { "不能把资料移动到其他作品" }
        require(input.text("id").let { it.isEmpty() || it == id }) { "资料 ID 不一致" }
        val current = dao.entity(ReplicaEntity.key(projectId, type, id))
        require(current?.conflicted != true) { "请先处理这条资料的版本分岔" }
        val old = current?.takeIf { it.operation == "upsert" }?.payload()
        if (expectedVersion != null) require(old?.number("current_version", 1) == expectedVersion) { "正式章节版本已变化，请重新审阅修订候选" }
        if (old != null && type in setOf("chapter", "character", "world", "outline")) read.requireEntity(projectId, type, id)
        val now = Instant.now().toString()
        if (type == "world" && input.containsKey("dimension")) {
            require(input.text("dimension") in setOf("geography", "history", "factions", "power_system", "races", "culture")) { "世界观维度必须使用契约枚举" }
        }
        val source = if (old == null && type in setOf("project", "chapter", "outline", "character", "world")) PcApiPayloads.authoring(type, input, create = true) else input
        val mutation = PcApiPayloads.syncMutation(type, source, projectId, id)
        val merged = JsonObject(old.orEmpty() + mutation + mapOf("created_at" to (old?.get("created_at") ?: JsonPrimitive(now)), "updated_at" to JsonPrimitive(now)))
        if (type == "character_ai_config" && old != null) require(old.text("character_id") == merged.text("character_id")) { "角色 AI 配置不能移动到其他角色" }
        validate(projectId, type, id, merged)
        var payload = when (type) {
            "chapter" -> mobileChapterPayloadForSave(old, merged)
            "character" -> JsonObject(merged + ("current_version" to JsonPrimitive(if (old == null) 1 else old.number("current_version", 1) + 1)))
            "foreshadowing", "governance" -> localGovernanceTransition(old?.text("status")?.ifBlank { "open" } ?: "open", merged, now)
            else -> merged
        }
        if (type == "chapter") {
            if (old == null) {
                val chapters = dao.projectSnapshot(projectId).filter { it.entityType == "chapter" }
                payload = JsonObject(payload + ("sort_order" to JsonPrimitive((chapters.maxOfOrNull { it.payload().number("sort_order") } ?: 0) + 1000)))
            } else {
                snapshot(projectId, id, old, "manual_save", now)
                if (listOf("content", "title", "outline_node_id").any { old.text(it) != payload.text(it) }) LocalCatalogingRollback(database).rollback(projectId, id)
            }
        }
        payload = LocalLinkedRecords(dao, this).apply(projectId, type, id, payload, mutation.keys)
        put(projectId, type, id, payload, dirty = true)
        if (type in setOf("foreshadowing", "governance")) {
            val chapterId = payload.text("resolved_chapter_id")
            val chapter = if (chapterId.isNotBlank()) read.requireEntity(projectId, "chapter", chapterId) else null
            if (chapter != null) {
                payload = JsonObject(payload + ("resolved_chapter_version" to JsonPrimitive(chapter.number("current_version", 1))))
                put(projectId, type, id, payload, true)
            }
            val eventId = UUID.randomUUID().toString()
            put(projectId, "governance", eventId, buildJsonObject {
                put("id", eventId); put("project_id", projectId); put("_record_type", "narrative_governance_event")
                put("item_id", id); put("item_type", if (type == "foreshadowing") "foreshadowings" else "narrative-debts")
                put("from_status", old?.text("status")?.ifBlank { "open" } ?: "open"); put("to_status", payload.text("status")); put("created_at", now)
                put("chapter_id", chapterId.takeIf(String::isNotBlank)?.let(::JsonPrimitive) ?: JsonNull)
                put("chapter_version", chapter?.get("current_version") ?: JsonNull)
                put("note", listOf("verification_note", "resolution_evidence", "resolution_note", "evidence").firstNotNullOfOrNull { payload.text(it).takeIf(String::isNotBlank) }?.take(4000)?.let(::JsonPrimitive) ?: JsonNull)
                put("actor", payload.text("closed_by").ifBlank { "user" }.take(50))
            }, true)
        }
        when (type) {
            "chapter" -> {
                snapshot(projectId, id, payload, trigger, now)
                checkpoint(projectId, id, payload, if (old == null) "chapter_create" else trigger, now)
            }
            "character" -> if (old != null) version(projectId, "character", "character_version", "character_id", id, payload, now)
        }
        val mutationId = enqueue(projectId, type, id, PcApiPayloads.syncMutation(type, payload, projectId, id).toString(), current?.revision ?: 0)
        receipt(projectId, mutationId, dao.projectPackageSnapshot(projectId).filter { it != before[it.key] })
        id
    }

    private suspend fun validate(projectId: String, type: String, id: String, payload: JsonObject) {
        validateAuthoringFields(type, payload)
        if (type == "project") require(id == projectId) { "作品 ID 不一致" }
        if (type in setOf("project", "chapter", "outline", "world")) require(payload.text("title").isNotBlank()) { "标题不能为空" }
        if (type == "character") require(payload.text("name").isNotBlank()) { "角色名称不能为空" }
        if (type == "character") require(payload.text("role_type").let { it.isBlank() || it in setOf("protagonist", "supporting", "antagonist", "mentor", "other", "merged_alias") }) { "角色类型必须使用契约枚举" }
        if (type in setOf("foreshadowing", "governance")) {
            require(payload.text("title").isNotBlank()) { "标题不能为空" }
            listOf("source_chapter_id", "target_chapter_id", "resolved_chapter_id").forEach { field -> payload.text(field).takeIf(String::isNotBlank)?.let { read.requireEntity(projectId, "chapter", it) } }
            payload.text("linked_foreshadowing_id").takeIf(String::isNotBlank)?.let { read.requireEntity(projectId, "foreshadowing", it) }
            payload.text("linked_causal_edge_id").takeIf(String::isNotBlank)?.let { causalId -> require(read.records(projectId, "causal_edge").any { it.text("id") == causalId }) { "因果关系不属于当前作品" } }
        }
        if (type == "chapter" && payload.text("outline_node_id").isNotEmpty()) {
            val outline = read.requireEntity(projectId, "outline", payload.text("outline_node_id"))
            require(outline.text("node_type") == "chapter") { "章节只能绑定章级大纲" }
        }
        if (type == "outline") {
            var parent = payload.text("parent_id")
            val seen = mutableSetOf(id)
            while (parent.isNotBlank()) {
                require(seen.add(parent)) { "大纲不能形成循环" }
                parent = read.requireEntity(projectId, "outline", parent).text("parent_id")
            }
            (payload["characters"] as? JsonArray).orEmpty().forEach { link ->
                read.requireEntity(projectId, "character", link.jsonObject.text("character_id"))
            }
        }
        if (type == "character_ai_config") read.requireEntity(projectId, "character", payload.text("character_id"))
        if (type == "character_relation") {
            val from = payload.text("from"); val to = payload.text("to")
            require(from != to) { "角色不能与自身建立关系" }
            read.requireEntity(projectId, "character", from); read.requireEntity(projectId, "character", to)
        }
        if (type == "world_relation") {
            val from = payload.text("source_entry_id"); val to = payload.text("target_entry_id")
            require(from != to) { "世界观条目不能与自身建立关系" }
            read.requireEntity(projectId, "world", from); read.requireEntity(projectId, "world", to)
        }
        require(payload.toString().toByteArray().size <= 1024 * 1024) { "单条资料不能超过 1 MiB" }
    }

    internal suspend fun put(projectId: String, type: String, id: String, payload: JsonObject, dirty: Boolean = false) {
        val key = ReplicaEntity.key(projectId, type, id)
        val old = dao.entity(key)
        val raw = payload.toString()
        dao.saveEntity(ReplicaEntity(key, projectId, type, id, old?.revision ?: 0, "upsert", raw,
            catalogingHash(raw), old?.serverModifiedAt ?: Instant.now().toString(), dirty = dirty))
    }

    internal suspend fun snapshot(projectId: String, chapterId: String, payload: JsonObject, trigger: String, now: String) {
        val version = payload.number("current_version", 1)
        if (read.records(projectId, "chapter_snapshot").any { it.text("chapter_id") == chapterId && it.number("version_number") == version && it.text("content") == payload.text("content") }) return
        val id = catalogingId("chapter_snapshot", chapterId, version.toString(), catalogingHash(payload.text("content")))
        put(projectId, "chapter_version", id, buildJsonObject {
            put("id", id); put("project_id", projectId); put("chapter_id", chapterId); put("_record_type", "chapter_snapshot")
            put("version_number", version); put("content", payload.text("content")); put("word_count", payload.number("word_count"))
            put("trigger_type", trigger); put("created_at", now)
        }, dirty = true)
    }

    private suspend fun version(projectId: String, entityType: String, recordType: String, ownerKey: String, ownerId: String, payload: JsonObject, now: String) {
        val number = if (entityType == "character") payload.number("current_version", 1) else
            (read.records(projectId, recordType).filter { it.text(ownerKey) == ownerId }.maxOfOrNull { it.number("version_number") } ?: 0) + 1
        val id = catalogingId(recordType, ownerId, number.toString(), catalogingHash(payload.toString()))
        put(projectId, entityType, id, buildJsonObject {
            put("id", id); put("project_id", projectId); put("_record_type", recordType); put(ownerKey, ownerId)
            put("version_number", number); put("snapshot_data", payload.toString()); put("created_at", now)
            put("change_summary", payload.text("change_summary").ifBlank { "手动更新资料" })
        }, dirty = true)
    }

    private suspend fun enqueue(projectId: String, type: String, id: String, raw: String?, revision: Long, operation: String = "upsert"): String {
        val existing = dao.pendingMutation(projectId, type, id)?.takeIf { pending ->
            pending.catalogingBarrierId == null && type !in setOf("chapter", "character", "foreshadowing", "governance")
        }
        val mutation = (existing ?: OutboxMutation(if (type == "authoring_command") id else UUID.randomUUID().toString(), projectId, type, id,
            operation, revision, raw, Instant.now().toString())).copy(payloadJson = raw, operation = operation,
            clientModifiedAt = Instant.now().toString(), state = "pending", lastError = null)
        dao.saveMutation(mutation)
        return mutation.mutationId
    }

    private suspend fun checkpoint(projectId: String, chapterId: String, chapter: JsonObject, trigger: String, now: String) {
        val snapshot = read.records(projectId, "chapter_snapshot").first { it.text("chapter_id") == chapterId && it.number("version_number") == chapter.number("current_version", 1) }
        val checkpointId = UUID.randomUUID().toString()
        put(projectId, "governance", checkpointId, buildJsonObject {
            put("id", checkpointId); put("project_id", projectId); put("_record_type", "narrative_checkpoint")
            put("chapter_id", chapterId); put("chapter_snapshot_id", snapshot.text("id")); put("trigger_type", trigger)
            put("sequence", (read.records(projectId, "narrative_checkpoint").maxOfOrNull { it.number("sequence") } ?: 0) + 1)
            put("created_at", now); put("label", "${chapter.text("title")} 写后状态")
            put("state_json", buildJsonObject {
                mapOf("foreshadowings" to "foreshadowing", "causal_edges" to "causal_edge", "narrative_debts" to "narrative_debt", "character_states" to "character_narrative_state", "quality_metrics" to "chapter_quality_metric", "chapter_reviews" to "chapter_governance_review").forEach { (key, type) -> put(key, JsonArray(read.records(projectId, type))) }
            })
        }, true)
    }

    suspend fun delete(projectId: String, type: String, id: String) = database.withTransaction {
        val row = dao.entity(ReplicaEntity.key(projectId, type, id)) ?: return@withTransaction
        if (row.operation == "delete") return@withTransaction
        require(!row.conflicted) { "请先处理这条资料的版本分岔" }
        read.requireEntity(projectId, type, id)
        val before = dao.projectPackageSnapshot(projectId).associateBy { it.key }
        if (type == "chapter") LocalCatalogingRollback(database).rollback(projectId, id)
        val deletedIds = mutableSetOf(id)
        if (type == "outline") {
            do {
                val added = read.records(projectId, "outline_node").filter { it.text("parent_id") in deletedIds }.map { it.text("id") }.filter { it !in deletedIds }
                deletedIds.addAll(added)
            } while (added.isNotEmpty())
        }
        for (candidate in dao.projectSnapshot(projectId)) {
            val p = candidate.payload()
            val remove = candidate.key == row.key || when (type) {
                "chapter" -> p.text("chapter_id") == id && p.text("_record_type") !in setOf("narrative_governance_event", "narrative_checkpoint")
                "character" -> p.text("character_id") == id || p.text("from") == id || p.text("to") == id || p.text("character_a_id") == id || p.text("character_b_id") == id
                "world" -> p.text("entry_id") == id || p.text("worldbuilding_entry_id") == id || p.text("source_entry_id") == id || p.text("target_entry_id") == id
                "outline" -> candidate.entityType == "outline" && candidate.entityId in deletedIds || p.text("outline_node_id") in deletedIds && candidate.entityType != "chapter"
                else -> false
            }
            if (remove) dao.saveEntity(candidate.copy(operation = "delete", payloadJson = null, contentHash = catalogingHash("null"), dirty = true))
            else if (type == "outline" && candidate.entityType == "chapter" && p.text("outline_node_id") in deletedIds) put(projectId, "chapter", candidate.entityId, JsonObject(p + ("outline_node_id" to JsonNull)), true)
            else if (type == "character" && candidate.entityType == "outline" && p.text("_record_type").ifBlank { "outline_node" } == "outline_node") {
                val links = (p["linked_characters"] as? JsonArray).orEmpty().filter { it.jsonObject.text("character_id").ifBlank { it.jsonObject.text("id") } != id }
                val characters = (p["characters"] as? JsonArray).orEmpty().filter { it.jsonObject.text("character_id") != id }
                if (JsonArray(links) != p["linked_characters"] || JsonArray(characters) != p["characters"]) {
                    put(projectId, "outline", candidate.entityId, JsonObject(p + mapOf("linked_characters" to JsonArray(links), "characters" to JsonArray(characters))), true)
                }
            } else if (type == "chapter") {
                val detached = p.toMutableMap()
                for (field in listOf("chapter_id", "source_chapter_id", "target_chapter_id", "resolved_chapter_id", "last_seen_chapter_id", "last_updated_chapter_id")) {
                    if (p.text(field) == id) detached[field] = JsonNull
                }
                if (JsonObject(detached) != p) put(projectId, candidate.entityType, candidate.entityId, JsonObject(detached), true)
            }
        }
        val mutation = enqueue(projectId, type, id, null, row.revision, "delete")
        receipt(projectId, mutation, dao.projectPackageSnapshot(projectId).filter { it != before[it.key] })
    }

    private suspend fun receipt(projectId: String, id: String, changes: List<ReplicaEntity>) {
        val previous = dao.entity(ReplicaEntity.key(projectId, "local_receipt", id))?.payload()?.get("local_changes") as? JsonArray
        val all = previous.orEmpty().map { it.jsonObject }.associateBy { it.text("key") }.toMutableMap()
        changes.forEach { row -> all[row.key] = buildJsonObject { put("key", row.key); put("operation", row.operation); put("hash", row.contentHash) } }
        put(projectId, "local_receipt", id, buildJsonObject { put("local_changes", JsonArray(all.values.toList())) }, true)
    }

    suspend fun coveredKeys(mutations: List<OutboxMutation>): Set<String> = mutations.flatMap { mutation ->
        val receipt = dao.entity(ReplicaEntity.key(mutation.projectId, "local_receipt", mutation.mutationId))?.payload()
        (receipt?.get("local_changes") as? JsonArray).orEmpty().map { it.jsonObject.text("key") }
    }.toSet()

    suspend fun acknowledge(mutation: OutboxMutation) {
        val key = ReplicaEntity.key(mutation.projectId, "local_receipt", mutation.mutationId)
        val receipt = dao.entity(key) ?: return
        (receipt.payload()["local_changes"] as? JsonArray).orEmpty().forEach { value ->
            val change = value.jsonObject
            val current = dao.entity(change.text("key"))
            if (current != null && current.operation == change.text("operation") && current.contentHash == change.text("hash")) dao.saveEntity(current.copy(dirty = false))
        }
        dao.saveEntity(receipt.copy(dirty = false, operation = "delete"))
    }

    internal suspend fun command(projectId: String, name: String, arguments: JsonObject, expected: JsonObject, changes: List<ReplicaEntity>) {
        val id = UUID.randomUUID().toString()
        dao.sealCatalogingSourceMutations(projectId, id)
        val request = buildJsonObject { put("command", name); put("arguments", arguments); put("expected", expected) }
        val local = JsonObject(request + ("local_changes" to JsonArray(changes.map { row -> buildJsonObject {
            put("key", row.key); put("operation", row.operation); put("hash", row.contentHash)
        } })))
        // Receipts live separately from authoring records so a command is never mistaken for a story entity.
        put(projectId, "authoring_command", id, local, dirty = true)
        enqueue(projectId, "authoring_command", id, request.toString(), 0)
        receipt(projectId, id, changes)
    }

    suspend fun editReplicaDraft(projectId: String, draftId: String, title: String, content: String): JsonObject = database.withTransaction {
        val old = read.requireEntity(projectId, "chapter_draft", draftId)
        require(dao.entity(ReplicaEntity.key(projectId, "chapter_draft", draftId))?.conflicted == false) { "请先处理草稿的版本分岔" }
        require(old.text("status") in setOf("pending", "generated")) { "章节草稿已经处理或失效" }
        require(title.isNotBlank() && title.length <= 200) { "章节标题必须为 1 至 200 个字符" }
        val updated = JsonObject(old + mapOf("title" to JsonPrimitive(title), "content" to JsonPrimitive(content), "updated_at" to JsonPrimitive(Instant.now().toString())))
        require(updated.toString().toByteArray().size <= 1024 * 1024) { "单条草稿不能超过 1 MiB" }
        put(projectId, "chapter_draft", draftId, updated, true)
        command(projectId, "chapter_draft_edit", buildJsonObject {
            put("draft_id", draftId); put("title", title); put("content", content); put("outline_node_id", old["outline_node_id"] ?: JsonNull)
        }, buildJsonObject { put("content_hash", catalogingHash(old.text("content"))) }, listOf(requireNotNull(dao.entity(ReplicaEntity.key(projectId, "chapter_draft", draftId)))))
        updated
    }

    suspend fun closeReplicaDraft(projectId: String, draftId: String, status: String, chapterId: String? = null): Boolean = database.withTransaction {
        val row = dao.entity(ReplicaEntity.key(projectId, "chapter_draft", draftId)) ?: return@withTransaction false
        val old = read.requireEntity(projectId, "chapter_draft", draftId)
        require(!row.conflicted) { "请先处理草稿的版本分岔" }
        if (old.text("status") == status) return@withTransaction true
        require(old.text("status") in setOf("pending", "generated")) { "章节草稿已经处理或失效" }
        require(status in setOf("saved", "discarded")) { "草稿状态无效" }
        val chapter = if (status == "saved") read.requireEntity(projectId, "chapter", requireNotNull(chapterId)) else null
        val updated = JsonObject(old + buildJsonObject { put("status", status); put("updated_at", Instant.now().toString()); if (chapterId != null) put("saved_chapter_id", chapterId) })
        put(projectId, "chapter_draft", draftId, updated, true)
        command(projectId, "chapter_draft_status", buildJsonObject {
            put("draft_id", draftId); put("status", status)
            if (chapter != null) { put("saved_chapter_id", chapterId); put("saved_content_hash", catalogingHash(chapter.text("content"))) }
        }, buildJsonObject { put("content_hash", catalogingHash(old.text("content"))) }, listOf(requireNotNull(dao.entity(row.key))))
        true
    }

    suspend fun restoreChapter(projectId: String, chapterId: String, snapshotId: String): JsonObject = database.withTransaction {
        val before = dao.projectPackageSnapshot(projectId).associateBy { it.key }
        val chapter = read.requireEntity(projectId, "chapter", chapterId)
        require(before.getValue(ReplicaEntity.key(projectId, "chapter", chapterId)).conflicted.not()) { "请先处理章节的版本分岔" }
        val selected = read.snapshot(projectId, chapterId, snapshotId)
        val now = Instant.now().toString()
        snapshot(projectId, chapterId, chapter, "manual_save", now)
        val affected = LocalCatalogingRollback(database).rollback(projectId, chapterId)
        val restored = JsonObject(chapter + mapOf(
            "content" to JsonPrimitive(selected.text("content")),
            "word_count" to JsonPrimitive(selected.text("content").count { !it.isWhitespace() }),
            "current_version" to JsonPrimitive(chapter.number("current_version", 1) + 1),
            "cataloging_required" to JsonPrimitive(selected.text("content").isNotBlank()),
            "updated_at" to JsonPrimitive(now),
        ))
        put(projectId, "chapter", chapterId, restored, true)
        snapshot(projectId, chapterId, restored, "restore", now)
        checkpoint(projectId, chapterId, restored, "restore", now)
        command(projectId, "chapter_restore", buildJsonObject {
            put("chapter_id", chapterId); put("snapshot_version", selected.number("version_number")); put("snapshot_content", selected.text("content"))
        }, buildJsonObject { put("current_version", chapter.number("current_version", 1)); put("content_hash", catalogingHash(chapter.text("content"))) },
            dao.projectPackageSnapshot(projectId).filter { it != before[it.key] })
        JsonObject(restored + ("recatalog_required_chapter_ids" to JsonArray(affected.map(::JsonPrimitive))))
    }

    suspend fun reorderChapters(projectId: String, ids: List<String>): JsonObject = database.withTransaction {
        read.requireEntity(projectId, "project", projectId)
        val rows = orderReplicaEntities("chapter", dao.projectSnapshot(projectId).filter { it.entityType == "chapter" })
        require(ids.size == ids.distinct().size && ids.toSet() == rows.map { it.entityId }.toSet()) { "章节排序必须包含当前作品的全部章节，且不能重复" }
        val expected = buildJsonObject { put("chapter_ids", JsonArray(rows.map { JsonPrimitive(it.entityId) })) }
        ids.forEachIndexed { index, id ->
            val row = rows.first { it.entityId == id }
            require(!row.conflicted) { "请先处理章节的版本分岔" }
            put(projectId, "chapter", id, JsonObject(row.payload() + ("sort_order" to JsonPrimitive((index + 1) * 1000))), true)
        }
        command(projectId, "chapter_reorder", buildJsonObject { put("chapter_ids", JsonArray(ids.map(::JsonPrimitive))) }, expected,
            ids.map { requireNotNull(dao.entity(ReplicaEntity.key(projectId, "chapter", it))) })
        items(ids.map { read.requireEntity(projectId, "chapter", it) })
    }

    suspend fun replaceRelationships(projectId: String, characterId: String, relations: JsonArray): JsonObject = database.withTransaction {
        read.requireEntity(projectId, "character", characterId)
        val pairs = mutableSetOf<Pair<String, String>>()
        val normalized = relations.map { raw ->
            val row = raw.jsonObject
            val from = row.text("source_character_id").ifBlank { characterId }; val to = row.text("target_character_id")
            require(characterId == from || characterId == to) { "提交的关系必须连接当前角色" }
            require(from != to && pairs.add(from to to)) { "不能提交自关联或重复的有向关系" }
            read.requireEntity(projectId, "character", from); read.requireEntity(projectId, "character", to)
            buildJsonObject { put("id", catalogingId("character_relationship", projectId, from, to)); put("from", from); put("to", to); put("relationship_type", row.text("relationship_type").ifBlank { "related" }); put("description", row.text("description")) }
        }
        val before = relationshipGuard(read.relationshipNetwork(projectId), characterId)
        val old = dao.projectSnapshot(projectId).filter { it.entityType == "character_relation" &&
            it.payload().let { p -> characterId in listOf(p.text("from"), p.text("to"), p.text("character_a_id"), p.text("character_b_id")) } }
        val changed = mutableListOf<ReplicaEntity>()
        old.forEach { row ->
            val deleted = row.copy(operation = "delete", payloadJson = null, contentHash = catalogingHash("null"), dirty = true)
            dao.saveEntity(deleted); changed += deleted
        }
        normalized.forEach { edge ->
            val id = catalogingId("character_relationship", projectId, edge.text("from"), edge.text("to"))
            put(projectId, "character_relation", id, JsonObject(edge + mapOf("id" to JsonPrimitive(id), "project_id" to JsonPrimitive(projectId), "_record_type" to JsonPrimitive("character_relationship"))), true)
            changed += requireNotNull(dao.entity(ReplicaEntity.key(projectId, "character_relation", id)))
        }
        command(projectId, "character_relationships", buildJsonObject { put("character_id", characterId); put("relationships", JsonArray(normalized)) },
            buildJsonObject { put("relationships", before) }, changed.distinctBy { it.key }.map { requireNotNull(dao.entity(it.key)) })
        read.relationshipNetwork(projectId)
    }
}

internal fun relationshipGuard(network: JsonObject, characterId: String): JsonArray = JsonArray(
    network.getValue("edges").jsonArray.map { it.jsonObject }.filter { characterId in listOf(it.text("from"), it.text("to")) }
        .map { row -> buildJsonObject { for (key in listOf("from", "to", "relationship_type", "description")) put(key, row.text(key)) } }
        .sortedWith(compareBy<JsonObject> { it.text("from") }.thenBy { it.text("to") }),
)
