package com.siming.mobile

import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.MobilePendingChapterDraft
import com.siming.mobile.data.SimingRepository
import com.siming.mobile.data.authoring.LocalAuthoringStore
import com.siming.mobile.data.local.*
import java.io.File
import java.util.zip.ZipInputStream
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LocalAuthoringInstrumentedTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun database() = Room.inMemoryDatabaseBuilder(context, SimingDatabase::class.java).build()
    private fun obj(vararg pairs: Pair<String, String>) = buildJsonObject { pairs.forEach { (key, value) -> put(key, value) } }
    private suspend fun unavailableGateway(db: SimingDatabase) = db.dao().saveConnection(GatewayConnection(
        baseUrl = "http://127.0.0.1:1", gatewayName = "Unavailable test peer", gatewayFingerprint = "test", deviceId = "test", deviceRole = "owner", protocolVersion = 1,
    ))

    @Test fun chapterHistoryReorderDiffAndRestoreWorkWithOrWithoutSavedGateway() = runBlocking {
        for (paired in listOf(false, true)) {
            val db = database()
            try {
                if (paired) unavailableGateway(db)
                val repo = SimingRepository(context, db)
                withTimeout(5_000) {
                    val project = repo.createProject("独立作品")
                    val first = repo.saveEntity(project, "chapter", "first", obj("title" to "第一章", "content" to "旧正文\n留存"))
                    repo.saveEntity(project, "chapter", "second", obj("title" to "第二章", "content" to "第二章"))
                    val selected = repo.listChapterSnapshots(project, first).getValue("items").jsonArray.single().jsonObject
                    repo.saveEntity(project, "chapter", first, obj("content" to "新正文"))
                    val snapshots = repo.listChapterSnapshots(project, first).getValue("items").jsonArray
                    assertEquals(2, snapshots.size)
                    val latest = snapshots.first().jsonObject
                    val diff = repo.diffChapterSnapshots(project, first, selected.getValue("id").jsonPrimitive.content, latest.getValue("id").jsonPrimitive.content)
                    assertEquals(1, diff.getValue("total_changes").jsonPrimitive.int)
                    assertTrue(runCatching { repo.reorderChapters(project, listOf(first)) }.isFailure)
                    repo.reorderChapters(project, listOf("second", first))
                    assertEquals(2000, LocalAuthoringStore(db).read.requireEntity(project, "chapter", first).getValue("sort_order").jsonPrimitive.int)
                    repo.reorderChapters(project, listOf(first, "second"))
                    val restored = repo.restoreChapterSnapshot(project, first, selected.getValue("id").jsonPrimitive.content)
                    assertEquals("旧正文\n留存", restored.getValue("content").jsonPrimitive.content)
                    assertEquals(3, restored.getValue("current_version").jsonPrimitive.int)
                    assertEquals(listOf(first, "second"), restored.getValue("recatalog_required_chapter_ids").jsonArray.map { it.jsonPrimitive.content })
                    assertEquals(3, repo.listChapterSnapshots(project, first).getValue("total").jsonPrimitive.int)
                    assertEquals(3, db.dao().pendingMutations(100).count { it.entityType == "authoring_command" })
                    assertTrue(db.dao().projectSnapshot(project).none { it.entityType in setOf("local_receipt", "authoring_command") })
                }
            } finally { db.close() }
        }
    }

    @Test fun relationshipsConfigAndCharacterVersionsAreAtomicAndIndependent() = runBlocking {
        val db = database()
        try {
            unavailableGateway(db)
            val repo = SimingRepository(context, db)
            withTimeout(5_000) {
                val project = repo.createProject("角色")
                repo.saveEntity(project, "character", "a", obj("name" to "甲", "role_type" to "protagonist"))
                repo.saveEntity(project, "character", "b", obj("name" to "乙"))
                val edge = obj("source_character_id" to "b", "target_character_id" to "a", "relationship_type" to "师徒", "description" to "乙教甲")
                repo.replaceCharacterRelationships(project, "a", JsonArray(listOf(edge)))
                val network = repo.characterRelationshipNetwork(project)
                assertEquals("b", network.getValue("edges").jsonArray.single().jsonObject.getValue("from").jsonPrimitive.content)
                assertTrue(runCatching { repo.replaceCharacterRelationships(project, "a", JsonArray(listOf(edge, edge))) }.isFailure)
                assertEquals(network, repo.characterRelationshipNetwork(project))
                assertTrue(runCatching { repo.replaceCharacterRelationships(project, "a", JsonArray(listOf(obj("target_character_id" to "foreign")))) }.isFailure)
                val config = repo.updateCharacterAiConfig(project, "a", buildJsonObject { put("tone_style", "克制"); put("catchphrases", JsonArray(listOf(JsonPrimitive("等一等")))) })
                assertEquals("克制", config.getValue("tone_style").jsonPrimitive.content)
                assertEquals("moderate", config.getValue("verbosity").jsonPrimitive.content)
                repo.saveEntity(project, "character", "a", obj("background" to "新经历"))
                assertEquals(1, repo.characterVersions(project, "a").getValue("total").jsonPrimitive.int)
                assertEquals(2, LocalAuthoringStore(db).read.requireEntity(project, "character", "a").getValue("current_version").jsonPrimitive.int)
            }
        } finally { db.close() }
    }

    @Test fun localRevisionChecksTheActualSavedVersionAndPreservesHistory() = runBlocking {
        val db = database()
        try {
            val repo = SimingRepository(context, db)
            val project = repo.createProject("修订")
            repo.saveEntity(project, "chapter", "chapter", obj("title" to "原章", "content" to "原文"))
            unavailableGateway(db)
            val draft = MobilePendingChapterDraft(draftId = "revision-fixture", projectId = project, title = "原章", content = "候选", executionRoute = "project_package", draftKind = "revision", targetChapterId = "chapter", baseChapterVersion = 1, targetChapterCurrentVersion = 1)
            withTimeout(5_000) { assertEquals("chapter", repo.savePendingChapterDraft(draft, "修订章", "修订正文", "save_only")) }
            val after = LocalAuthoringStore(db).read.requireEntity(project, "chapter", "chapter")
            assertEquals("修订正文", after.getValue("content").jsonPrimitive.content)
            assertEquals(2, after.getValue("current_version").jsonPrimitive.int)
            assertTrue(runCatching { repo.savePendingChapterDraft(draft, "过期", "不能覆盖", "save_only") }.isFailure)
            assertEquals(after, LocalAuthoringStore(db).read.requireEntity(project, "chapter", "chapter"))
        } finally { db.close() }
    }

    @Test fun wordAndPaginatedPdfAreGeneratedOnDeviceWithAnUnavailableGateway() = runBlocking {
        val db = database()
        val pdfFile = File(context.cacheDir, "independent-export-test.pdf")
        try {
            val repo = SimingRepository(context, db)
            val project = repo.createProject("焚天 & 铸骨 <测试>")
            val prose = (1..150).joinToString("\n") { "第 $it 段：陆沉走进铸剑坊，残碑上刻着这一段独有的文字。" }
            repo.saveEntity(project, "chapter", "chapter", obj("title" to "没人要的料", "content" to prose))
            unavailableGateway(db)
            val docx = withTimeout(10_000) { repo.exportProject(project, "docx") }
            var xml = ""
            ZipInputStream(docx.bytes!!.inputStream()).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    if (entry.name == "word/document.xml") xml = zip.readBytes().toString(Charsets.UTF_8)
                }
            }
            assertTrue(xml.contains("焚天 &amp; 铸骨 &lt;测试&gt;"))
            assertTrue(xml.contains("第 1 段")); assertTrue(xml.contains("第 150 段"))
            pdfFile.writeBytes(withTimeout(15_000) { repo.exportProject(project, "pdf").bytes!! })
            val descriptor = ParcelFileDescriptor.open(pdfFile, ParcelFileDescriptor.MODE_READ_ONLY)
            val renderer = PdfRenderer(descriptor)
            try {
                assertTrue(renderer.pageCount >= 3)
                for (index in 0 until renderer.pageCount) {
                    val page = renderer.openPage(index)
                    assertEquals(595, page.width)
                    assertEquals(842, page.height)
                    page.close()
                }
            } finally { renderer.close() }
            // Keep a review copy in the isolated debug test package for host text extraction.
            pdfFile.copyTo(File(context.filesDir, "independent-export-test.pdf"), overwrite = true)
            Unit
        } finally { pdfFile.delete(); db.close() }
    }

    @Test fun acknowledgedOlderMutationCannotClearNewerAuthorEdits() = runBlocking {
        val db = database()
        try {
            val store = LocalAuthoringStore(db)
            store.save("p", "project", "p", obj("title" to "事务"))
            store.save("p", "chapter", "c", obj("title" to "章节", "content" to "第一版"))
            val earlier = db.dao().pendingMutations(100).last()
            store.save("p", "chapter", "c", obj("title" to "章节", "content" to "第二版"))
            store.acknowledge(earlier)
            assertTrue(db.dao().entity(ReplicaEntity.key("p", "chapter", "c"))!!.dirty)
            assertEquals(2, store.read.history("p", "chapter", "c", "chapter_snapshot", "chapter_id").getValue("total").jsonPrimitive.int)
            assertEquals(2, db.dao().pendingMutations(100).count { it.entityType == "chapter" })
        } finally { db.close() }
    }

    @Test fun outlineLinksAndGovernanceTransitionsStayAtomicWithoutPc() = runBlocking {
        val db = database()
        try {
            val store = LocalAuthoringStore(db)
            store.save("p", "project", "p", obj("title" to "资料"))
            store.save("p", "character", "a", obj("name" to "甲"))
            store.save("p", "chapter", "c", obj("title" to "章节", "content" to "实际修订正文"))
            val outline = buildJsonObject { put("title", "章纲"); put("node_type", "chapter"); put("character_ids", JsonArray(listOf(JsonPrimitive("a")))) }
            store.save("p", "outline", "o", outline)
            store.save("p", "outline", "o", outline)
            assertEquals(1, store.read.records("p", "outline_node_character").size)
            SimingRepository(context, db).reorderOutline("p", null, listOf("o"))
            assertTrue(runCatching { store.save("p", "outline", "o", buildJsonObject { put("character_ids", JsonArray(listOf(JsonPrimitive("foreign")))) }) }.isFailure)
            assertEquals(1, store.read.records("p", "outline_node_character").size)
            store.save("p", "foreshadowing", "f", obj("title" to "旧炉谜题", "status" to "open"))
            assertTrue(runCatching { store.save("p", "foreshadowing", "f", obj("status" to "fulfilled")) }.isFailure)
            store.save("p", "foreshadowing", "f", obj("status" to "pending_review", "resolved_chapter_id" to "c", "resolution_note" to "正文已经解释"))
            store.save("p", "foreshadowing", "f", obj("status" to "fulfilled", "verification_note" to "复核正文属实"))
            assertEquals(3, db.dao().pendingMutations(100).count { it.entityType == "foreshadowing" })
            val events = store.read.records("p", "narrative_governance_event")
            assertEquals(3, events.size)
            assertEquals("fulfilled", store.read.requireEntity("p", "foreshadowing", "f").getValue("status").jsonPrimitive.content)
            store.delete("p", "character", "a")
            assertTrue(store.read.records("p", "outline_node_character").isEmpty())
            assertTrue(store.read.requireEntity("p", "outline", "o").getValue("linked_characters").jsonArray.isEmpty())
            store.delete("p", "chapter", "c")
            assertEquals(JsonNull, store.read.requireEntity("p", "foreshadowing", "f")["resolved_chapter_id"])
            assertEquals(3, store.read.records("p", "narrative_governance_event").size)
        } finally { db.close() }
    }

    @Test fun aDraftBroughtFromPcCanBeEditedAndDiscardedWithPcUnavailable() = runBlocking {
        val db = database()
        try {
            val repo = SimingRepository(context, db)
            val project = repo.createProject("跨设备草稿")
            unavailableGateway(db)
            val draft = MobilePendingChapterDraft("from-pc", project, "旧标题", "未保存正文", executionRoute = "gateway")
            withTimeout(5_000) { assertTrue(repo.assistantConversations(project).isEmpty()) }
            val edited = withTimeout(5_000) { repo.updatePendingChapterDraft(draft, "手机改稿", "手机修改的正文") }
            assertEquals("手机修改的正文", edited.content)
            assertEquals("手机修改的正文", repo.pendingChapterDraft(project)?.content)
            assertTrue(db.dao().projectSnapshot(project).none { it.entityType == "chapter" })
            withTimeout(5_000) { repo.discardPendingChapterDraft(edited) }
            assertNull(repo.pendingChapterDraft(project))
            assertEquals(2, db.dao().pendingMutations(100).count { it.entityType == "authoring_command" })
            val creationKey = ReplicaEntity.key("__novel_creation__", "creation_session", "pc-creation")
            db.dao().saveEntity(ReplicaEntity(creationKey, "__novel_creation__", "creation_session", "pc-creation", 0,
                "upsert", """{"id":"pc-creation","draft":{"execution_route":"pc","execution_host":"gateway","agent_conversation_id":"pc-context","form":{"title":"保留创意"}}}""", "hash", "2026-09-16T00:00:00Z"))
            val localCreation = withTimeout(5_000) { repo.continueCreationOnPhone("pc-creation") }.getValue("draft").jsonObject
            assertEquals(JsonPrimitive("device"), localCreation["execution_host"])
            assertEquals(JsonPrimitive("mobile"), localCreation["execution_route"])
            assertEquals(JsonPrimitive("保留创意"), localCreation.getValue("form").jsonObject["title"])
            assertFalse(localCreation.containsKey("agent_conversation_id"))
            withTimeout(5_000) { repo.discardCreation("pc-creation") }
            assertEquals("delete", db.dao().entity(creationKey)!!.operation)
            assertTrue(runCatching { repo.getCreationSession("pc-creation") }.isFailure)
        } finally { db.close() }
    }
}
