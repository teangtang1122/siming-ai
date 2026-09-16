package com.siming.mobile

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.MobileProjectPackageFile
import com.siming.mobile.data.MobileProjectPackageWriter
import com.siming.mobile.data.sha256File
import com.siming.mobile.data.authoring.LocalAuthoringStore
import com.siming.mobile.data.authoring.number
import com.siming.mobile.data.authoring.text
import com.siming.mobile.data.local.*
import com.siming.mobile.security.SecureTokenStore
import com.siming.mobile.security.StoredTokenPair
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class LocalAuthoringSyncInstrumentedTest {
    @Test fun importSeedRebasesOfflineEditsButNeverOverwritesLaterPcEditsAfterALostReceipt() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val tokens = SecureTokenStore(context)
        val previous = tokens.read()
        try {
            for (pcEdited in listOf(false, true)) {
                val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
                val server = MockWebServer()
                val source = File.createTempFile("sync-import-", ".siming-project", context.cacheDir)
                var retained: File? = null
                try {
                    val sourceRow = ReplicaEntity(ReplicaEntity.key("source", "project", "source"), "source", "project", "source", 0,
                        "upsert", """{"id":"source","_record_type":"project","title":"原包"}""", "hash", "2026-09-16T00:00:00Z")
                    MobileProjectPackageWriter.write("source", listOf(sourceRow), null, "full", source)
                    val repository = SimingRepository(context, db)
                    val imported = repository.importProjectPackage(MobileProjectPackageFile(source.name, source, source.length(), sha256File(source)))
                    val id = imported.projectId
                    val stored = db.dao().projectPackage(id)!!
                    retained = File(stored.localFilePath)
                    val store = LocalAuthoringStore(db)
                    var remote = store.read.requireEntity(id, "project", id)
                    store.save(id, "project", id, buildJsonObject { put("title", "手机修改") })
                    var lost = false
                    var revision = 5
                    val pushedBases = mutableListOf<Int>()
                    var conflictPayload: JsonObject? = null
                    server.dispatcher = object : Dispatcher() {
                        override fun dispatch(request: RecordedRequest): MockResponse {
                            val path = request.requestUrl!!.encodedPath
                            val data: JsonElement = when {
                                path.endsWith("/project-package/import") -> {
                                    assertEquals(stored.idempotencyKey, request.getHeader("Idempotency-Key"))
                                    if (!lost) {
                                        lost = true
                                        if (pcEdited) { revision = 6; remote = JsonObject(remote + ("title" to JsonPrimitive("PC 修改"))) }
                                        return MockResponse().setResponseCode(503).setBody("receipt lost")
                                    }
                                    buildJsonObject { put("project_id", id); put("package_id", stored.packageId); put("sync_import_cursor", 5); put("replayed", true) }
                                }
                                path.endsWith("/sync/bootstrap") -> buildJsonObject {
                                    put("protocol_version", 1); put("cursor", revision); put("projects", JsonArray(listOf(JsonPrimitive(id))))
                                    put("entities", buildJsonArray { add(buildJsonObject {
                                        put("project_id", id); put("entity_type", "project"); put("entity_id", id); put("payload", remote)
                                        put("revision", revision); put("operation", "upsert"); put("content_hash", "server"); put("server_modified_at", "2026-09-16T00:00:00Z")
                                    }) })
                                }
                                path.endsWith("/sync/push") -> {
                                    val mutation = Json.parseToJsonElement(request.body.readUtf8()).jsonObject.getValue("mutations").jsonArray.single().jsonObject
                                    val base = mutation.number("base_revision"); pushedBases += base
                                    if (pcEdited) conflictPayload = mutation.getValue("payload").jsonObject
                                    if (!pcEdited) { remote = mutation.getValue("payload").jsonObject; revision++ }
                                    buildJsonObject {
                                        put("protocol_version", 1); put("cursor", revision)
                                        put("results", buildJsonArray { add(buildJsonObject {
                                            put("mutation_id", mutation.getValue("mutation_id")); put("status", if (pcEdited) "conflict" else "applied"); put("revision", revision)
                                            if (pcEdited) { put("conflict_id", "concurrent"); put("server_snapshot", buildJsonObject { put("payload", remote) }) }
                                        }) })
                                    }
                                }
                                path.endsWith("/sync/conflicts") -> buildJsonArray {
                                    conflictPayload?.let { client -> add(buildJsonObject {
                                        put("id", "concurrent"); put("project_id", id); put("entity_type", "project"); put("entity_id", id)
                                        put("client_payload", client); put("server_payload", remote); put("server_revision", revision); put("status", "open")
                                    }) }
                                }
                                path.endsWith("/sync/pull") -> buildJsonObject { put("protocol_version", 1); put("from_cursor", 0); put("next_cursor", revision); put("has_more", false); put("changes", JsonArray(emptyList())) }
                                else -> error("Unexpected remote operation $path")
                            }
                            return MockResponse().setHeader("Content-Type", "application/json").setBody(buildJsonObject { put("code", 200); put("message", "ok"); put("data", data) }.toString())
                        }
                    }
                    server.start()
                    tokens.save(StoredTokenPair("fixture", "2099-01-01T00:00:00Z", "refresh", "2099-01-01T00:00:00Z"))
                    db.dao().saveConnection(GatewayConnection(baseUrl = server.url("/").toString().trimEnd('/'), gatewayName = "test", gatewayFingerprint = "test", deviceId = "test", deviceRole = "owner", protocolVersion = 1))
                    assertTrue(runCatching { repository.syncNow() }.isFailure)
                    assertEquals("手机修改", store.read.requireEntity(id, "project", id).text("title"))
                    repository.syncNow()
                    assertEquals(listOf(if (pcEdited) 0 else 5), pushedBases)
                    val local = db.dao().entity(ReplicaEntity.key(id, "project", id))!!
                    assertEquals(pcEdited, local.conflicted)
                    assertEquals("手机修改", store.read.requireEntity(id, "project", id).text("title"))
                    assertEquals(if (pcEdited) "PC 修改" else "手机修改", remote.text("title"))
                    assertEquals(if (pcEdited) 1 else 0, db.dao().openConflictsSnapshot().size)
                } finally { server.shutdown(); db.close(); source.delete(); retained?.delete() }
            }
        } finally { if (previous == null) tokens.clear() else tokens.save(previous) }
    }

    @Test fun uncertainRestoreIsRetriedOnceBeforeLaterEditsAndCanonicalHistoryReplacesLocalReceipts() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
        val tokens = SecureTokenStore(context)
        val previous = tokens.read()
        val server = MockWebServer()
        try {
            val store = LocalAuthoringStore(db)
            fun body(title: String, content: String) = buildJsonObject { put("title", title); put("content", content) }
            store.save("p", "project", "p", buildJsonObject { put("title", "本机写作") })
            store.save("p", "chapter", "c", body("一", "第一版"))
            val first = store.read.records("p", "chapter_snapshot").single().text("id")
            store.save("p", "chapter", "d", body("二", "第二章"))
            store.save("p", "chapter", "c", body("一", "第二版"))
            store.reorderChapters("p", listOf("d", "c"))
            store.restoreChapter("p", "c", first)
            val remote = linkedMapOf<Pair<String, String>, JsonObject>()
            val revisions = mutableMapOf<Pair<String, String>, Int>()
            val receipts = mutableMapOf<String, Int>()
            val events = mutableListOf<String>()
            var revision = 0
            var interrupted = false
            fun set(type: String, id: String, payload: JsonObject) { remote[type to id] = payload; revisions[type to id] = ++revision }
            fun saveChapter(id: String, payload: JsonObject) {
                val old = remote["chapter" to id]
                val version = (old?.number("current_version") ?: 0) + 1
                val data = JsonObject(old.orEmpty() + payload + buildJsonObject { put("id", id); put("project_id", "p"); put("_record_type", "chapter"); put("current_version", version); put("cataloging_required", true); put("sort_order", old?.number("sort_order") ?: ((remote.keys.count { it.first == "chapter" } + 1) * 1000)) })
                set("chapter", id, data)
                val snapshotId = "server-$id-$version"
                set("chapter_version", snapshotId, buildJsonObject { put("id", snapshotId); put("project_id", "p"); put("_record_type", "chapter_snapshot"); put("chapter_id", id); put("version_number", version); put("content", data.text("content")); put("created_at", "2026-09-16T00:00:00Z") })
            }
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    val data: JsonElement = when {
                        path.endsWith("/sync/push") -> {
                            var loseReceipt = false
                            val mutations = Json.parseToJsonElement(request.body.readUtf8()).jsonObject.getValue("mutations").jsonArray
                            val results = mutations.map { raw ->
                                val m = raw.jsonObject; val mutationId = m.text("mutation_id"); val type = m.text("entity_type"); val id = m.text("entity_id")
                                val previousRevision = receipts[mutationId]
                                if (previousRevision == null) {
                                    val payload = m.getValue("payload").jsonObject
                                    if (type == "authoring_command") {
                                        val name = payload.text("command"); val args = payload.getValue("arguments").jsonObject
                                        events += name
                                        when (name) {
                                            "chapter_reorder" -> args.getValue("chapter_ids").jsonArray.forEachIndexed { index, chapter ->
                                                val cid = chapter.jsonPrimitive.content
                                                set("chapter", cid, JsonObject(remote.getValue("chapter" to cid) + ("sort_order" to JsonPrimitive((index + 1) * 1000))))
                                            }
                                            "chapter_restore" -> {
                                                saveChapter(args.text("chapter_id"), buildJsonObject { put("content", args.text("snapshot_content")) })
                                                if (!interrupted) { interrupted = true; loseReceipt = true }
                                            }
                                        }
                                    } else if (type == "chapter") { events += "save:${payload.text("content")}"; saveChapter(id, payload) }
                                    else set(type, id, payload)
                                    receipts[mutationId] = revisions[type to id] ?: ++revision
                                } else events += "duplicate"
                                buildJsonObject { put("mutation_id", mutationId); put("status", if (previousRevision == null) "applied" else "duplicate"); put("revision", receipts.getValue(mutationId)) }
                            }
                            if (loseReceipt) return MockResponse().setResponseCode(503).setBody("receipt unavailable after commit")
                            buildJsonObject { put("protocol_version", 1); put("cursor", revision); put("results", JsonArray(results)) }
                        }
                        path.endsWith("/sync/bootstrap") -> buildJsonObject {
                            put("protocol_version", 1); put("cursor", revision); put("projects", JsonArray(listOf(JsonPrimitive("p"))))
                            put("entities", JsonArray(remote.map { (key, payload) -> buildJsonObject {
                                put("project_id", "p"); put("entity_type", key.first); put("entity_id", key.second); put("payload", payload)
                                put("revision", revisions.getValue(key)); put("operation", "upsert"); put("content_hash", "server"); put("server_modified_at", "2026-09-16T00:00:00Z")
                            } }))
                        }
                        path.endsWith("/sync/conflicts") -> JsonArray(emptyList())
                        path.endsWith("/sync/pull") -> buildJsonObject { put("protocol_version", 1); put("from_cursor", 0); put("next_cursor", revision); put("has_more", false); put("changes", JsonArray(emptyList())) }
                        else -> error("Unexpected remote operation $path")
                    }
                    return MockResponse().setHeader("Content-Type", "application/json").setBody(buildJsonObject { put("code", 200); put("message", "ok"); put("data", data) }.toString())
                }
            }
            server.start()
            tokens.save(StoredTokenPair("fixture", "2099-01-01T00:00:00Z", "refresh", "2099-01-01T00:00:00Z"))
            db.dao().saveConnection(GatewayConnection(baseUrl = server.url("/").toString().trimEnd('/'), gatewayName = "test", gatewayFingerprint = "test", deviceId = "test", deviceRole = "owner", protocolVersion = 1))
            val repository = SimingRepository(context, db)
            assertTrue(runCatching { repository.syncNow() }.isFailure)
            assertEquals(3, store.read.requireEntity("p", "chapter", "c").number("current_version"))
            store.save("p", "chapter", "c", body("一", "恢复后又修改"))
            repository.syncNow()
            assertEquals(listOf("save:第一版", "save:第二章", "save:第二版", "chapter_reorder", "chapter_restore", "duplicate", "save:恢复后又修改"), events)
            assertEquals(0, db.dao().pendingMutationCount())
            val chapter = store.read.requireEntity("p", "chapter", "c")
            assertEquals(4, chapter.number("current_version")); assertEquals("恢复后又修改", chapter.text("content"))
            val history = store.read.records("p", "chapter_snapshot").filter { it.text("chapter_id") == "c" }
            assertEquals(4, history.size)
            assertTrue(history.all { it.text("id").startsWith("server-") })
            assertFalse(db.dao().entity(ReplicaEntity.key("p", "chapter", "c"))!!.dirty)
        } finally {
            server.shutdown()
            if (previous == null) tokens.clear() else tokens.save(previous)
            db.close()
        }
    }
}
