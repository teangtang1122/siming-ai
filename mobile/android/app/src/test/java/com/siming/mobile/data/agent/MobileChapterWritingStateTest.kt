package com.siming.mobile.data.agent

import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.PcApiPayloads
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class MobileChapterWritingStateTest {
    @Test
    fun `saving and cataloging clear an old imported pending draft in standalone runtime`() {
        val pending = entity("chapter_draft", "draft-1", """{"status":"pending","outline_node_id":"outline-1","content":"正文"}""")
        val draftSnapshot = listOf(pending)
        assertEquals(JsonPrimitive("draft-1"), state(draftSnapshot)["pending_draft"]!!.jsonObject["id"])

        val saved = entity("chapter", "chapter-1", """{"outline_node_id":"outline-1","content":"正文","current_version":1,"cataloging_required":true}""")
        val savedState = state(listOf(pending, saved))
        assertEquals(JsonNull, savedState["pending_draft"])
        assertEquals(JsonPrimitive("chapter-1"), savedState["cataloging_required_chapter"]!!.jsonObject["id"])
        assertTrue(mobileCatalogingBlockReason(savedState, "")!!.contains("尚未完成建档"))
        assertNull(mobileCatalogingBlockReason(savedState, "draft-being-revised"))

        val cataloged = saved.copy(payloadJson = saved.payloadJson!!.replace("true", "false"))
        val completedState = state(listOf(pending, cataloged))
        assertTrue(completedState.values.all { it == JsonNull })
        assertNull(mobileCatalogingBlockReason(completedState, ""))
        val runtime = mobileWorkspaceRuntimeData(obj("""{"id":"p1","title":"作品"}"""), completedState)
        assertEquals(JsonNull, runtime["active_chapter_draft"])
        assertEquals(completedState, runtime["chapter_writing_state"])
        assertFalse(runtime.toString().contains("draft-1"))
    }

    @Test
    fun `changed local prose and title edits invalidate cataloging with a new PC compatible version`() {
        val completed = obj("""{"id":"chapter-1","content":"原文","title":"旧标题","cataloging_required":false,"current_version":2}""")
        val renamed = mobileChapterPayloadForSave(completed, obj("""{"title":"新标题"}"""))
        assertEquals(JsonPrimitive(true), renamed["cataloging_required"])
        val edited = mobileChapterPayloadForSave(completed, obj("""{"content":"修改后的正文","cataloging_required":false}"""))
        assertEquals(JsonPrimitive(true), edited["cataloging_required"])
        assertEquals(JsonPrimitive(3), edited["current_version"])
        assertEquals(JsonPrimitive(6), edited["word_count"])
        assertEquals(JsonPrimitive(3), renamed["current_version"])
        assertEquals(JsonPrimitive(true), mobileChapterPayloadForSave(edited, obj("""{"title":"另一个标题","cataloging_required":false}"""))["cataloging_required"])
        assertEquals(JsonPrimitive(false), mobileChapterPayloadForSave(edited, obj("""{"content":""}"""))["cataloging_required"])
        // Derived mobile state must not become an author-supplied override on the canonical API.
        assertFalse(PcApiPayloads.syncMutation("chapter", edited, "p1", "chapter-1").containsKey("cataloging_required"))
    }

    @Test
    fun `new local chapter requires cataloging and old data without a flag remains explicitly unknown`() {
        val saved = mobileChapterPayloadForSave(null, obj("""{"content":"正文","cataloging_required":false}"""))
        assertEquals(JsonPrimitive(true), saved["cataloging_required"])
        val legacy = entity("chapter", "chapter-1", """{"content":"旧包正文"}""")
        val state = state(listOf(legacy))
        assertEquals(JsonNull, state["cataloging_required_chapter"])
        assertEquals(JsonPrimitive("chapter-1"), state["cataloging_state_unknown_chapter"]!!.jsonObject["id"])
        assertTrue(mobileCatalogingBlockReason(state, "")!!.contains("缺少"))
        val unchanged = mobileChapterPayloadForSave(obj(legacy.payloadJson!!), obj("""{"title":"重命名"}"""))
        assertEquals(JsonPrimitive(true), unchanged["cataloging_required"])
    }

    @Test
    fun `only current project live chapter records can create a cataloging gate`() {
        val entries = listOf(
            entity("chapter", "foreign", """{"content":"其他作品","cataloging_required":true}""", projectId = "p2"),
            entity("chapter", "mismatch", """{"project_id":"p2","content":"归属不匹配","cataloging_required":true}"""),
            entity("outline", "volume", """{"content":"卷","cataloging_required":true}"""),
            entity("chapter", "deleted", """{"content":"已删除","cataloging_required":true}""").copy(operation = "delete"),
            entity("chapter", "version", """{"_record_type":"chapter_version","content":"历史版本","cataloging_required":true}"""),
            entity("chapter", "empty", """{"content":"","cataloging_required":true}"""),
        )
        assertTrue(state(entries).values.all { it == JsonNull })
    }

    @Test
    fun `handled drafts cannot reappear but a pending revision remains independent from formal prose`() {
        for (status in listOf("saved", "discarded", "superseded")) {
            assertNull(mobileImportedChapterDraft("p1", listOf(entity("chapter_draft", "draft-1", """{"status":"$status"}"""))))
        }
        val consumed = entity("chapter_draft", "draft-1", """{"status":"pending","saved_chapter_id":"chapter-1"}""")
        assertNull(mobileImportedChapterDraft("p1", listOf(consumed)))
        val revision = entity("chapter_draft", "revision-1", """{"status":"pending","draft_kind":"revision","target_chapter_id":"chapter-1","outline_node_id":"outline-1"}""")
        val formal = entity("chapter", "chapter-1", """{"outline_node_id":"outline-1","content":"原文","cataloging_required":false}""")
        assertEquals(JsonPrimitive("revision-1"), state(listOf(revision, formal))["pending_draft"]!!.jsonObject["id"])
    }

    @Test
    fun `editing a selected imported draft never returns another pending draft`() {
        val older = entity("chapter_draft", "older", """{"status":"pending","draft_kind":"revision","target_chapter_id":"chapter-1","created_at":"2026-09-01"}""")
        val newer = entity("chapter_draft", "newer", """{"status":"pending","created_at":"2026-09-02"}""")
        val formal = entity("chapter", "chapter-1", """{"title":"原章","content":"原文","current_version":3}""")
        val selected = mobileImportedChapterDraft("p1", listOf(older, newer, formal), "older")!!
        assertEquals(JsonPrimitive("older"), selected["draft_id"])
        assertEquals(JsonPrimitive(3), selected["target_chapter_current_version"])
        assertEquals(JsonPrimitive("原文"), selected["target_chapter_content"])
        assertNull(mobileImportedChapterDraft("p1", listOf(older, newer, formal), "missing"))
    }

    private fun state(snapshot: List<ReplicaEntity>) =
        mobileChapterWritingState("p1", snapshot, mobileImportedChapterDraft("p1", snapshot))

    private fun obj(value: String) = Json.parseToJsonElement(value).jsonObject

    private fun entity(type: String, id: String, payload: String, projectId: String = "p1") = ReplicaEntity(
        key = ReplicaEntity.key(projectId, type, id), projectId = projectId, entityType = type,
        entityId = id, revision = 1, operation = "upsert",
        payloadJson = JsonObject(mapOf("title" to JsonPrimitive(id)) + obj(payload)).toString(),
        contentHash = "hash", serverModifiedAt = "2026-09-10T03:04:00Z",
    )
}
