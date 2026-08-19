package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChapterWorkspaceTest {
    @Test
    fun `word count prefers canonical stored value and falls back to content`() {
        assertEquals(
            321,
            chapterWordCount(replica("chapter", "c1", "{\"title\":\"第一章\",\"content\":\"很短\",\"word_count\":321}")),
        )
        assertEquals(
            4,
            chapterWordCount(replica("chapter", "c2", "{\"title\":\"第二章\",\"content\":\"天 地\\n玄黄\"}")),
        )
    }

    @Test
    fun `chapter resolves parent volume from linked outline hierarchy`() {
        val volume = replica(
            "outline",
            "v1",
            "{\"title\":\"第一卷 山雨欲来\",\"node_type\":\"volume\",\"parent_id\":null}",
        )
        val outlineChapter = replica(
            "outline",
            "o1",
            "{\"title\":\"第一章 穿越\",\"node_type\":\"chapter\",\"parent_id\":\"v1\"}",
        )
        val chapter = replica(
            "chapter",
            "c1",
            "{\"title\":\"第一章 穿越\",\"content\":\"正文\",\"outline_node_id\":\"o1\"}",
        )

        assertEquals("第一卷 山雨欲来", chapterVolumeLabel(chapter, listOf(volume, outlineChapter)))
    }

    @Test
    fun `unlinked or cyclic outline does not invent a volume`() {
        val unlinked = replica("chapter", "c1", "{\"title\":\"第一章\",\"content\":\"正文\"}")
        val a = replica("outline", "a", "{\"title\":\"A\",\"node_type\":\"chapter\",\"parent_id\":\"b\"}")
        val b = replica("outline", "b", "{\"title\":\"B\",\"node_type\":\"section\",\"parent_id\":\"a\"}")
        val linked = replica(
            "chapter",
            "c2",
            "{\"title\":\"第二章\",\"content\":\"正文\",\"outline_node_id\":\"a\"}",
        )

        assertNull(chapterVolumeLabel(unlinked, listOf(a, b)))
        assertNull(chapterVolumeLabel(linked, listOf(a, b)))
    }

    @Test
    fun `reader keeps paragraph boundaries without blank blocks`() {
        assertEquals(
            listOf("第一段。", "第二段。\n仍属于第二段。", "第三段。"),
            chapterParagraphs("第一段。\n\n 第二段。\n仍属于第二段。 \n\n\n第三段。"),
        )
    }

    private fun replica(entityType: String, entityId: String, payload: String) = ReplicaEntity(
        key = ReplicaEntity.key("p1", entityType, entityId),
        projectId = "p1",
        entityType = entityType,
        entityId = entityId,
        revision = 1,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "hash-$entityId",
        serverModifiedAt = "2026-08-19T00:00:00Z",
    )
}
