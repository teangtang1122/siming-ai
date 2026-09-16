package com.siming.mobile.data.cataloging

import androidx.room.withTransaction
import com.siming.mobile.data.MobileCatalogingProgress
import com.siming.mobile.data.agent.pcExactCharacterArchive
import com.siming.mobile.data.local.LocalCatalogingRun
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.local.SimingDatabase
import com.siming.mobile.data.network.DirectAgentTurn
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

internal class MobileCataloging(
    private val database: SimingDatabase,
    private val contract: CatalogingContract,
    private val model: String,
    private val turn: suspend (List<JsonObject>, JsonArray) -> DirectAgentTurn,
) {
    private val dao = database.dao()

    suspend fun run(projectId: String, chapterIds: List<String>, onProgress: suspend (MobileCatalogingProgress, String?) -> Unit): MobileCatalogingProgress = mutex.withLock {
        require(chapterIds.isNotEmpty() && chapterIds.distinct().size == chapterIds.size) { "请选择唯一的已保存章节 ID" }
        var done = 0
        var latest = MobileCatalogingProgress("", "running", chapterIds.size, 0, 0)
        for (chapterId in chapterIds) {
            val source = catalogRecords(projectId, dao.projectSnapshot(projectId))
            if (source.singleOrNull { it.recordType == "chapter" && it.id == chapterId }?.payload?.text("content").isNullOrBlank()) { done++; continue }
            val plan = CatalogingPlan(contract, projectId, chapterId, source)
            if (plan.chapter["cataloging_required"] == JsonPrimitive(false)) { done++; continue }
            val hash = catalogingHash(plan.chapter.text("content"))
            val previous = dao.catalogingRuns(projectId).firstOrNull { it.chapterId == chapterId && it.chapterVersion == plan.chapter.number("current_version") && it.contentHash == hash }
            val fingerprint = catalogingFingerprint(source)
            val reusable = previous?.takeIf { it.sourceFingerprint == fingerprint && it.status != "completed" }
            val now = Instant.now().toString()
            val job = LocalCatalogingRun(
                id = reusable?.id ?: "local-cat-${UUID.randomUUID()}", projectId = projectId, chapterId = chapterId,
                chapterVersion = plan.chapter.number("current_version"), contentHash = hash,
                sourceFingerprint = fingerprint, sourceJson = JsonArray(source.map(CatalogRecord::toJson)).toString(),
                candidatesJson = reusable?.candidatesJson ?: "[]", transcriptJson = reusable?.transcriptJson ?: "[]",
                model = model, attempt = reusable?.attempt?.plus(1) ?: 1, createdAt = reusable?.createdAt ?: now, updatedAt = now,
            )
            active.add(job.id)
            try {
                database.withTransaction {
                    dao.sealCatalogingSourceMutations(projectId, job.id)
                    dao.saveCatalogingRun(job)
                }
                latest = MobileCatalogingProgress(job.id, "running", chapterIds.size, done, 0)
                onProgress(latest, "正在为《${plan.chapter.text("title")}》建档；正文已保存")
                execute(job) { message -> onProgress(latest, message) }
                done++
                latest = latest.copy(completedChapters = done)
                onProgress(latest, "《${plan.chapter.text("title")}》建档完成，故事资料已保存")
            } catch (e: CancellationException) {
                withContext(NonCancellable) { dao.stopCatalogingRun(job.id, "cancelled", "作者取消或页面任务结束；正文和未应用计划已保留", Instant.now().toString()) }
                throw e
            } catch (e: Exception) {
                withContext(NonCancellable) { dao.stopCatalogingRun(job.id, "failed", e.message.orEmpty().take(8000), Instant.now().toString()) }
                onProgress(latest.copy(status = "failed", failedChapters = 1), e.message)
                throw e
            } finally { active.remove(job.id) }
        }
        latest.copy(status = "completed", completedChapters = done)
    }

    private suspend fun execute(initial: LocalCatalogingRun, onProgress: suspend (String) -> Unit) {
        var job = initial
        val records = Json.parseToJsonElement(job.sourceJson).jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }
        val saved = Json.parseToJsonElement(job.candidatesJson).jsonArray.map { CatalogCandidate.fromJson(it.jsonObject) }
        val plan = CatalogingPlan(contract, job.projectId, job.chapterId, records, saved)
        // A new attempt starts with the controller and persisted candidate IDs. Old tool transcripts
        // are diagnostic only and never manufacture a fresh authorization.
        val messages = mutableListOf(message("system", contract.systemPrompt), message("user", buildJsonObject {
            put("task", "为当前已保存章节建档"); put("project_id", job.projectId); put("job_id", job.id)
            put("chapter_id", job.chapterId); put("chapter_run_id", job.id); put("chapter_version", job.chapterVersion)
            put("resume", buildJsonObject { put("accepted_candidates", JsonArray(saved.map(CatalogCandidate::toJson))) })
        }.toString()))
        var categories = emptyList<String>()
        var selected = false
        var chapterRead = false
        var failures = 0
        for (step in 1..contract.maxSteps) {
            checkActive(job)
            onProgress("正在核对建档计划（步骤 $step）")
            val response = turn(messages.toList(), contract.tools(categories))
            checkActive(job)
            val calls = response.toolCalls
            val allowed = contract.categories.availableToolNames(categories, contract.names)
            // Reject the whole batch before executing any write. The controller always ends its step.
            val batchError = when {
                calls.isEmpty() -> "文字回复不能作为建档完成回执，请继续通过工具修正计划并 finalize"
                calls.any { it.name !in allowed } -> "包含未授权工具，本批未执行"
                !selected && (calls.size != 1 || calls.single().name != "set_tool_categories") -> "新回合只能先调用 set_tool_categories"
                calls.any { it.name == "set_tool_categories" } && calls.size != 1 -> "类别切换必须单独调用，并立即结束当前步骤"
                calls.any { it.name == "save_external_cataloging_candidates" } && calls.size != 1 -> "建档写入必须单独调用"
                calls.map { it.id }.distinct().size != calls.size -> "工具调用 ID 重复"
                else -> null
            }
            messages += response.assistantMessage
            var complete = false
            if (calls.isEmpty()) {
                failures++
                messages += message("user", requireNotNull(batchError) + "；" + plan.diagnostics().joinToString("；"))
            }
            for (call in calls) {
                val result = try {
                    require(batchError == null) { requireNotNull(batchError) }
                    val args = call.arguments
                    contract.validateTool(call.name, args)
                    for ((key, expected) in listOf("project_id" to job.projectId, "job_id" to job.id, "chapter_id" to job.chapterId, "chapter_run_id" to job.id)) {
                        require(key !in args || args.text(key) == expected) { "$key 不属于当前建档任务" }
                    }
                    val data = when (call.name) {
                        "set_tool_categories" -> {
                            categories = contract.categories.normalize(args.strings("enabled_categories")); selected = true
                            buildJsonObject { put("enabled_categories", jsonStrings(categories)); put("step_complete", true) }
                        }
                        "get_next_external_cataloging_chapter" -> {
                            require(args["include_content"] != JsonPrimitive(false)) { "手机建档需要 include_content=true，必须读取完整正文" }
                            chapterRead = true
                            buildJsonObject {
                                put("chapter", plan.chapter); put("chapter_id", job.chapterId); put("chapter_run_id", job.id)
                                put("chapter_version", job.chapterVersion); put("content", plan.chapter.text("content"))
                                put("candidate_count", plan.candidates.size)
                                put("governance", JsonArray(records.filter { it.recordType in setOf("foreshadowing", "narrative_debt", "causal_edge") }.map { it.payload }))
                                put("instruction", "用 read_cataloging_archive 分页读取真实档案索引，再按真实 ID 读取完整卡片。")
                            }
                        }
                        "read_cataloging_archive" -> archive(records, args)
                        "list_cataloging_candidates" -> candidates(plan, args, job)
                        "save_external_cataloging_candidates" -> {
                            require(chapterRead) { "必须先读取本章完整正文，再提交建档计划" }
                            val submitted = plan.submit(args)
                            if (submitted["candidate_set_complete"] == JsonPrimitive(true)) {
                                // Exercise all deterministic writes before a successful finalize receipt.
                                CatalogingProjection(plan, job.id, Instant.now().toString()).apply()
                                complete = true
                            }
                            submitted
                        }
                        else -> error("没有可执行的建档工具：${call.name}")
                    }
                    val bad = data.objects("candidate_errors").isNotEmpty() || data.strings("missing_required_items").isNotEmpty()
                    failures = if (bad && data.number("candidates_saved") == 0) failures + 1 else 0
                    buildJsonObject { put("tool", call.name); put("status", if (bad) "error" else "ok"); put("data", data) }
                } catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    failures++
                    buildJsonObject { put("tool", call.name); put("status", "error"); put("detail", e.message.orEmpty()) }
                }
                messages += buildJsonObject { put("role", "tool"); put("tool_call_id", call.id); put("content", result.toString()) }
                onProgress(if (result.text("status") == "error") "计划校验未通过，已将具体问题交给模型修正" else "已完成建档工具：${call.name}")
            }
            job = job.copy(candidatesJson = JsonArray(plan.candidates.map(CatalogCandidate::toJson)).toString(), transcriptJson = buildJsonObject {
                put("previous_attempt", Json.parseToJsonElement(initial.transcriptJson)); put("attempt", initial.attempt)
                put("messages", JsonArray(messages))
            }.toString(), updatedAt = Instant.now().toString())
            database.withTransaction {
                checkActive(job)
                dao.saveCatalogingRun(job)
            }
            if (complete) { commit(job, plan); return }
            require(failures < contract.maxErrors) {
                "建档连续 ${contract.maxErrors} 次未通过工具校验；正文和候选已保留，可重试。" +
                    messages.lastOrNull()?.text("content").orEmpty().take(5000)
            }
        }
        error("建档已达到 ${contract.maxSteps} 步上限；正文和候选已保留，可重试")
    }

    private suspend fun checkActive(run: LocalCatalogingRun) {
        currentCoroutineContext().ensureActive()
        check(dao.catalogingRun(run.id)?.status == "running") { "建档任务已取消或失效" }
    }

    private suspend fun commit(job: LocalCatalogingRun, plan: CatalogingPlan) = database.withTransaction {
        checkActive(job)
        val current = catalogRecords(job.projectId, dao.projectSnapshot(job.projectId))
        check(catalogingFingerprint(current) == job.sourceFingerprint) { "建档期间正文或故事资料已变化；未写入过期计划，请重试" }
        val changes = CatalogingProjection(plan, job.id, Instant.now().toString()).apply()
        changes.upserts.forEach { row ->
            val key = ReplicaEntity.key(job.projectId, row.entityType, row.id)
            val old = dao.entity(key)
            val encoded = row.payload.toString()
            dao.saveEntity(ReplicaEntity(key, job.projectId, row.entityType, row.id, old?.revision ?: 0, "upsert", encoded,
                catalogingHash(encoded), old?.serverModifiedAt ?: job.createdAt, dirty = true))
        }
        changes.deletes.forEach { row ->
            val key = ReplicaEntity.key(job.projectId, row.entityType, row.id)
            dao.entity(key)?.let { dao.saveEntity(it.copy(operation = "delete", payloadJson = null, contentHash = catalogingHash("null"), dirty = true)) }
        }
        dao.saveCatalogingRun(job.copy(status = "completed", changesJson = changes.toJson().toString(), syncState = "pending", updatedAt = Instant.now().toString()))
    }

    private fun archive(records: List<CatalogRecord>, args: JsonObject): JsonObject {
        val type = when (args.text("kind")) { "character" -> "character"; "worldbuilding" -> "world_entry"; "outline" -> "outline_node"; "relationship" -> "character_relationship"; else -> error("未知档案类型") }
        val rows = records.filter { it.recordType == type && (type != "world_entry" || it.payload.text("status").ifBlank { "active" } == "active") }.sortedBy { it.id }
        val ids = args.strings("ids")
        if (ids.isNotEmpty()) {
            require(ids.all { id -> rows.any { it.id == id } }) { "部分档案 ID 不存在、不属于本作品或已停用" }
            return buildJsonObject { put("items", JsonArray(ids.map { id ->
                val record = rows.first { it.id == id }.payload
                if (type == "character") {
                    val archive = Json.parseToJsonElement(pcExactCharacterArchive(records.map { it.payload }, record)).jsonObject
                    JsonObject(archive.filterKeys { it != "state" } + archive.obj("state"))
                } else record
            })); put("has_more", false) }
        }
        val offset = args.number("cursor")
        val limit = args.number("limit", 20)
        val page = rows.drop(offset).take(limit)
        return buildJsonObject {
            put("items", JsonArray(page.map { row -> JsonObject(row.payload.filterKeys { it in setOf("id", "name", "role_type", "title", "dimension", "node_type", "parent_id", "from", "to", "relationship_type") }) }))
            put("has_more", offset + page.size < rows.size)
            put("next_arguments", if (offset + page.size < rows.size) JsonObject(args + ("cursor" to JsonPrimitive(offset + page.size))) else JsonNull)
        }
    }

    private fun candidates(plan: CatalogingPlan, args: JsonObject, job: LocalCatalogingRun): JsonObject {
        val rows = plan.candidates.filter { args.text("item_type").isBlank() || it.kind == args.text("item_type") }
        val offset = args.number("offset")
        val page = rows.drop(offset).take(args.number("limit", 2))
        return buildJsonObject {
            put("items", JsonArray(page.map(CatalogCandidate::toJson))); put("total", rows.size); put("has_more", offset + page.size < rows.size)
            put("next_arguments", if (offset + page.size < rows.size) JsonObject(args + mapOf("job_id" to JsonPrimitive(job.id), "offset" to JsonPrimitive(offset + page.size))) else JsonNull)
        }
    }

    private fun message(role: String, content: String) = buildJsonObject { put("role", role); put("content", content) }

    companion object {
        private val mutex = Mutex()
        private val active = ConcurrentHashMap.newKeySet<String>()
        suspend fun recoverInterrupted(database: SimingDatabase) {
            val dao = database.dao()
            dao.localProjectIds().forEach { projectId -> dao.catalogingRuns(projectId).filter { it.status == "running" && it.id !in active }.forEach {
                dao.stopCatalogingRun(it.id, "interrupted", "上次建档因应用退出中断；正文和计划已保留，可点击重试", Instant.now().toString())
            } }
        }
    }
}
