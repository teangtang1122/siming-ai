package com.siming.mobile

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.agent.mobileCatalogingBlockReason
import com.siming.mobile.data.agent.mobileChapterWritingState
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.Dispatcher
import com.siming.mobile.data.cataloging.*
import com.siming.mobile.data.local.*
import com.siming.mobile.data.network.DirectAgentToolCall
import com.siming.mobile.data.network.DirectAgentTurn
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import com.siming.mobile.data.network.DirectApiTimeoutException
import okhttp3.OkHttpClient
import java.net.Proxy
import java.util.concurrent.TimeUnit
import java.time.Instant
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MobileCatalogingInstrumentedTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val fixture = instrumentation.context.assets.open("mobile-cataloging-v1.json").bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject }
    private val ids = fixture.getValue("ids").jsonObject
    private val projectId = ids.text("project")
    private val chapterId = ids.text("chapter")
    private val contract = CatalogingContract(instrumentation.targetContext)
    private fun database() = Room.inMemoryDatabaseBuilder(instrumentation.targetContext, SimingDatabase::class.java).build()
    private suspend fun seed(db: SimingDatabase) {
        fixture.getValue("records").jsonArray.forEach { value ->
            val record = CatalogRecord.fromJson(value.jsonObject)
            db.dao().saveEntity(ReplicaEntity(ReplicaEntity.key(projectId, record.entityType, record.id), projectId, record.entityType,
                record.id, 0, "upsert", record.payload.toString(), "fixture", "2026-09-16T00:00:00Z"))
        }
    }
    private fun call(name: String, args: JsonObject): DirectAgentTurn {
        val id = "call-${java.util.UUID.randomUUID()}"
        return DirectAgentTurn("", "", listOf(DirectAgentToolCall(id, name, args, args.toString())), buildJsonObject {
            put("role", "assistant"); put("content", ""); put("tool_calls", JsonArray(listOf(buildJsonObject {
                put("id", id); put("type", "function"); put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
            })))
        })
    }
    private fun scripted(db: SimingDatabase, intercept: suspend (Int) -> Unit = {}): MobileCataloging {
        var step = 0
        val batches = listOf(listOf(fixture.getValue("candidates").jsonArray.first())) + fixture.getValue("candidates").jsonArray.drop(1).chunked(3)
        return MobileCataloging(db, contract, "mock/deepseek-contract") { messages, tools, _ ->
            val index = step++
            intercept(index)
            val jobId = Json.parseToJsonElement(messages[1].text("content")).jsonObject.text("job_id")
            when (index) {
                0 -> {
                    assertEquals(listOf("set_tool_categories"), tools.map { it.jsonObject.obj("function").text("name") })
                    call("set_tool_categories", buildJsonObject { put("enabled_categories", jsonStrings(listOf("cataloging"))) })
                }
                1 -> call("get_next_external_cataloging_chapter", buildJsonObject { put("job_id", jobId); put("include_content", true) })
                else -> {
                    assertTrue("unexpected model call after finalize: ${messages.lastOrNull()}", index - 2 < batches.size)
                    call("save_external_cataloging_candidates", buildJsonObject {
                        put("job_id", jobId); put("chapter_id", chapterId); put("candidates", JsonArray(batches[index - 2]))
                        put("finalize", index - 2 == batches.lastIndex)
                    })
                }
            }
        }
    }

    @Test fun independentPhoneCommitsOnceAndUnlocksNextChapterWithNoGateway() = runBlocking {
        val db = database()
        try {
            seed(db)
            db.dao().saveMutation(OutboxMutation("save", projectId, "chapter", chapterId, "upsert", 0, "{}", "now"))
            assertNull(db.dao().connection())
            val runtime = scripted(db)
            assertEquals("completed", runtime.run(projectId, listOf(chapterId)) { _, _ -> }.status)
            val run = db.dao().catalogingRuns(projectId).single()
            assertEquals("completed", run.status)
            assertEquals("pending", run.syncState)
            assertNotNull(run.changesJson)
            val after = db.dao().projectSnapshot(projectId)
            assertNull(mobileCatalogingBlockReason(mobileChapterWritingState(projectId, after, null), ""))
            assertEquals(JsonPrimitive(4), Json.parseToJsonElement(after.single { it.entityId == ids.text("hero") }.payloadJson!!).jsonObject["current_version"])
            assertEquals(run.id, db.dao().pendingMutations(10).single().catalogingBarrierId)
            assertNull(db.dao().pendingMutation(projectId, "chapter", chapterId))
            assertEquals("completed", runtime.run(projectId, listOf(chapterId)) { _, _ -> }.status)
            assertEquals(1, db.dao().catalogingRuns(projectId).size)
        } finally { db.close() }
    }

    @Test fun providerFailureRetainsPlanAndExplicitRetryResumesIt() = runBlocking {
        val db = database()
        try {
            seed(db)
            assertTrue(runCatching { scripted(db) { if (it == 3) error("模拟网络中断") }.run(projectId, listOf(chapterId)) { _, _ -> } }.isFailure)
            val failed = db.dao().catalogingRuns(projectId).single()
            assertEquals("failed", failed.status)
            assertTrue(failed.error!!.contains("网络中断"))
            assertEquals(1, Json.parseToJsonElement(failed.candidatesJson).jsonArray.size)
            assertTrue(db.dao().projectSnapshot(projectId).none { it.entityType == "summary" })
            scripted(db).run(projectId, listOf(chapterId)) { _, _ -> }
            val resumed = db.dao().catalogingRuns(projectId).single()
            assertEquals(failed.id, resumed.id)
            assertEquals(2, resumed.attempt)
            assertEquals("completed", resumed.status)
        } finally { db.close() }
    }

    @Test fun restoringChapterRollsBackGeneratedArchivesButKeepsLaterAuthorEdits() = runBlocking {
        for (manualEdit in listOf(false, true)) {
            val db = database()
            try {
                seed(db)
                val store = com.siming.mobile.data.authoring.LocalAuthoringStore(db)
                val original = store.read.requireEntity(projectId, "character", ids.text("hero"))
                val snapshotId = store.read.records(projectId, "chapter_snapshot").single().text("id")
                scripted(db).run(projectId, listOf(chapterId)) { _, _ -> }
                val generated = store.read.requireEntity(projectId, "character", ids.text("hero"))
                assertNotEquals(original["background"], generated["background"])
                if (manualEdit) store.save(projectId, "character", ids.text("hero"), buildJsonObject { put("background", "作者亲自确认的新经历") })
                store.restoreChapter(projectId, chapterId, snapshotId)
                val restored = store.read.requireEntity(projectId, "character", ids.text("hero"))
                assertEquals(if (manualEdit) JsonPrimitive("作者亲自确认的新经历") else original["background"], restored["background"])
                assertTrue(store.read.records(projectId, "chapter_summary").isEmpty())
                assertEquals("invalidated", db.dao().catalogingRuns(projectId).single().status)
                assertEquals(JsonPrimitive(true), store.read.requireEntity(projectId, "chapter", chapterId)["cataloging_required"])
                assertTrue(store.read.records(projectId, "narrative_checkpoint").any { it.text("trigger_type") == "restore" })
            } finally { db.close() }
        }
    }

    @Test fun fifthChapterTimeoutPreservesCompletedChaptersAndRetryKeepsItsPlan() = runBlocking {
        val db = database()
        val server = MockWebServer()
        try {
            seed(db)
            val source = db.dao().entity(ReplicaEntity.key(projectId, "chapter", chapterId))!!
            val payload = Json.parseToJsonElement(source.payloadJson!!).jsonObject
            val outline = db.dao().entity(ReplicaEntity.key(projectId, "outline", ids.text("outline")))!!
            val outlinePayload = Json.parseToJsonElement(outline.payloadJson!!).jsonObject
            val completedIds = (1..4).map { index -> "00000000-0000-0000-0000-00000000010$index" }
            completedIds.forEachIndexed { index, id ->
                val outlineId = "00000000-0000-0000-0000-00000000020${index + 1}"
                db.dao().saveEntity(source.copy(
                    key = ReplicaEntity.key(projectId, "chapter", id), entityId = id,
                    payloadJson = JsonObject(payload + mapOf(
                        "id" to JsonPrimitive(id), "cataloging_required" to JsonPrimitive(false),
                        "outline_node_id" to JsonPrimitive(outlineId), "sort_order" to JsonPrimitive(index),
                    )).toString(),
                ))
                db.dao().saveEntity(outline.copy(
                    key = ReplicaEntity.key(projectId, "outline", outlineId), entityId = outlineId,
                    payloadJson = JsonObject(outlinePayload + mapOf(
                        "id" to JsonPrimitive(outlineId), "source_chapter_id" to JsonPrimitive(id),
                        "sort_order" to JsonPrimitive(index),
                    )).toString(),
                ))
            }
            db.dao().saveEntity(source.copy(payloadJson = JsonObject(payload + ("sort_order" to JsonPrimitive(4))).toString()))
            val before = db.dao().projectSnapshot(projectId).associateBy { it.key }
            // MockWebServer throttles request reads too; let the whole request arrive first.
            val partial = ": ${" ".repeat(4_096)}\n\ndata: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"partial\",\"function\":{\"name\":\"read_cataloging_archive\",\"arguments\":\"{}\"}}]},\"finish_reason\":null}]}\n\n"
            server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody(partial + "data: [DONE]\n\n").throttleBody(partial.toByteArray().size.toLong(), 2, TimeUnit.SECONDS))
            server.start()
            val api = DirectApiClient(OkHttpClient.Builder().proxy(Proxy.NO_PROXY).build(), allowCleartextForTests = true)
            val config = DirectApiConfig("fixture", server.url("/").newBuilder().host("127.0.0.1").build().toString().trimEnd('/'), "fixture-key", "fixture-model")
            val progress = mutableListOf<com.siming.mobile.data.MobileCatalogingProgress>()
            val failed = runCatching {
                scripted(db) { index -> if (index == 3) {
                    api.streamAgentTurn(config, listOf(buildJsonObject { put("role", "user"); put("content", "fixture") }),
                        JsonArray(emptyList()), streamIdleTimeoutMillis = 400)
                } }.run(projectId, completedIds + chapterId) { state, _ -> progress += state }
            }
            assertTrue(failed.exceptionOrNull() is DirectApiTimeoutException)
            assertEquals(4, progress.last().completedChapters)
            assertEquals("failed", progress.last().status)
            val retained = db.dao().catalogingRuns(projectId).single()
            val error = retained.error.orEmpty()
            assertTrue(error.contains("未收到模型有效输出"))
            assertFalse(error.contains("检查网络"))
            assertEquals(1, Json.parseToJsonElement(retained.candidatesJson).jsonArray.size)
            assertEquals(before, db.dao().projectSnapshot(projectId).associateBy { it.key })
            assertEquals(1, server.requestCount)
            scripted(db).run(projectId, completedIds + chapterId) { _, _ -> }
            val resumed = db.dao().catalogingRuns(projectId).single()
            assertEquals(retained.id, resumed.id)
            assertEquals(2, resumed.attempt)
            assertEquals("completed", resumed.status)
            completedIds.forEach { id ->
                val key = ReplicaEntity.key(projectId, "chapter", id)
                assertEquals(before[key], db.dao().entity(key))
            }
        } finally { server.shutdown(); db.close() }
    }

    @Test fun editingDuringGenerationRejectsEntireProjection() = runBlocking {
        val db = database()
        try {
            seed(db)
            val result = runCatching { scripted(db) { step ->
                if (step == 4) {
                    val key = ReplicaEntity.key(projectId, "chapter", chapterId)
                    val old = db.dao().entity(key)!!
                    val payload = Json.parseToJsonElement(old.payloadJson!!).jsonObject
                    db.dao().saveEntity(old.copy(payloadJson = JsonObject(payload + mapOf("current_version" to JsonPrimitive(2), "content" to JsonPrimitive("作者新改的正文"))).toString(), dirty = true))
                }
            }.run(projectId, listOf(chapterId)) { _, _ -> } }
            assertTrue(result.exceptionOrNull()?.message.orEmpty().contains("已变化"))
            val after = db.dao().projectSnapshot(projectId)
            assertEquals(2, Json.parseToJsonElement(after.single { it.entityId == ids.text("hero") }.payloadJson!!).jsonObject.number("current_version"))
            assertTrue(after.none { it.entityType == "summary" })
            assertEquals("failed", db.dao().catalogingRuns(projectId).single().status)
        } finally { db.close() }
    }

    @Test fun cancellationAndRestartRecoveryKeepSavedProseAndNeverMarkCompleted() = runBlocking {
        val db = database()
        try {
            seed(db)
            val entered = CompletableDeferred<Unit>()
            val work = launch { scripted(db) { if (it == 3) { entered.complete(Unit); awaitCancellation() } }.run(projectId, listOf(chapterId)) { _, _ -> } }
            entered.await()
            work.cancelAndJoin()
            val run = db.dao().catalogingRuns(projectId).single()
            assertEquals("cancelled", run.status)
            assertTrue(db.dao().projectSnapshot(projectId).none { it.entityType == "summary" })
            db.dao().saveCatalogingRun(run.copy(status = "running", updatedAt = Instant.now().toString()))
            MobileCataloging.recoverInterrupted(db)
            assertEquals("interrupted", db.dao().catalogingRuns(projectId).single().status)
        } finally { db.close() }
    }

    @Test fun syncPreservesBodyVersionsAndReplaysCatalogingBeforeLaterAuthorEdits() = runBlocking {
        val db = database()
        val server = MockWebServer()
        val tokenStore = SecureTokenStore(instrumentation.targetContext)
        val previousToken = tokenStore.read()
        try {
            seed(db)
            val repository = SimingRepository(instrumentation.targetContext, db)
            val sourceChapter = fixture.getValue("records").jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }.single { it.id == chapterId }.payload
            repository.saveEntity(projectId, "chapter", chapterId, JsonObject(sourceChapter + ("content" to JsonPrimitive("第一次修改"))))
            repository.saveEntity(projectId, "chapter", chapterId, sourceChapter)
            assertEquals(2, db.dao().pendingMutations(10).size)
            scripted(db).run(projectId, listOf(chapterId)) { _, _ -> }
            val run = db.dao().catalogingRuns(projectId).single()
            assertEquals(3, run.chapterVersion)
            val key = ReplicaEntity.key(projectId, "chapter", chapterId)
            val cataloged = Json.parseToJsonElement(db.dao().entity(key)!!.payloadJson!!).jsonObject
            repository.saveEntity(projectId, "chapter", chapterId, JsonObject(cataloged + ("title" to JsonPrimitive("作者后改标题"))))
            val events = java.util.Collections.synchronizedList(mutableListOf<String>())
            val problems = java.util.Collections.synchronizedList(mutableListOf<String>())
            val remote = fixture.getValue("records").jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }.associateBy { it.entityType to it.id }.toMutableMap()
            val revisions = mutableMapOf<Pair<String, String>, Int>()
            var revision = 0
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    val data = when {
                        path.endsWith("/sync/push") -> {
                            events += "push"
                            val mutations = Json.parseToJsonElement(request.body.readUtf8()).jsonObject.objects("mutations")
                            buildJsonObject { put("protocol_version", 1); put("cursor", revision); put("results", JsonArray(mutations.map { mutation ->
                                val recordKey = mutation.text("entity_type") to mutation.text("entity_id")
                                if (mutation.number("base_revision") != (revisions[recordKey] ?: 0)) problems += "wrong base revision"
                                val old = remote.getValue(recordKey)
                                val update = mutation.obj("payload")
                                val version = old.payload.number("current_version") + 1
                                remote[recordKey] = old.copy(payload = JsonObject(old.payload + update + ("current_version" to JsonPrimitive(version))))
                                revisions[recordKey] = ++revision
                                buildJsonObject { put("mutation_id", mutation.text("mutation_id")); put("status", "applied"); put("revision", revision) }
                            })) }
                        }
                        path.endsWith("/cataloging/mobile-commit") -> {
                            events += "commit"
                            val commit = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                            if (remote.getValue("chapter" to chapterId).payload.number("current_version") != commit.number("chapter_version")) problems += "wrong chapter version"
                            if (remote.getValue("chapter" to chapterId).payload.text("title") != "旧炉") problems += "later edit sent before cataloging"
                            Json.parseToJsonElement(run.changesJson!!).jsonObject.objects("upserts").forEach { value ->
                                val row = CatalogRecord.fromJson(value)
                                remote[row.entityType to row.id] = row
                                revisions[row.entityType to row.id] = ++revision
                            }
                            buildJsonObject { put("status", "completed"); put("job_id", "server-job") }
                        }
                        path.endsWith("/sync/bootstrap") -> {
                            events += "bootstrap"
                            buildJsonObject { put("protocol_version", 1); put("cursor", revision); put("projects", jsonStrings(listOf(projectId))); put("entities", JsonArray(remote.values.map { row ->
                                buildJsonObject { put("project_id", projectId); put("entity_type", row.entityType); put("entity_id", row.id)
                                    put("revision", revisions[row.entityType to row.id] ?: 0); put("operation", "upsert"); put("payload", row.payload)
                                    put("content_hash", "server"); put("server_modified_at", "2026-09-16T01:00:00Z") }
                            })) }
                        }
                        path.endsWith("/sync/conflicts") -> JsonArray(emptyList())
                        path.endsWith("/sync/pull") -> buildJsonObject {
                            put("protocol_version", 1); put("from_cursor", revision); put("next_cursor", revision); put("has_more", false); put("changes", JsonArray(emptyList()))
                        }
                        else -> { problems += "unexpected path $path"; JsonObject(emptyMap()) }
                    }
                    return MockResponse().setHeader("Content-Type", "application/json").setBody(buildJsonObject { put("code", 200); put("message", "ok"); put("data", data) }.toString())
                }
            }
            server.start()
            tokenStore.save(StoredTokenPair("fixture-token", "2099-01-01T00:00:00Z", "fixture-refresh", "2099-01-01T00:00:00Z"))
            db.dao().saveConnection(GatewayConnection(baseUrl = server.url("/").toString().trimEnd('/'), gatewayName = "fixture", gatewayFingerprint = "fixture", deviceId = "fixture", deviceRole = "editor", protocolVersion = 1))
            repository.syncNow()
            assertEquals(emptyList<String>(), problems)
            assertEquals(listOf("push", "push", "commit", "bootstrap", "push", "bootstrap"), events)
            assertEquals("作者后改标题", remote.getValue("chapter" to chapterId).payload.text("title"))
            assertEquals(0, db.dao().pendingMutationCount())
            assertEquals("synced", db.dao().catalogingRuns(projectId).single().syncState)
            assertFalse(db.dao().entity(key)!!.dirty)
        } finally {
            server.shutdown()
            if (previousToken == null) tokenStore.clear() else tokenStore.save(previousToken)
            db.close()
        }
    }
}
