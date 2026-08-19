package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ManagementUiWorkspaceTest {
    @Test
    fun `outline labels stay author facing`() {
        assertEquals("进行中", outlineStatusLabel("in_progress"))
        assertEquals("已完成", outlineStatusLabel("completed"))
        assertEquals("待写", outlineStatusLabel("pending"))
        assertEquals("卷", outlineTypeLabel("volume"))
        assertEquals("章", outlineTypeLabel("chapter"))
        assertEquals("节", outlineTypeLabel("section"))
    }

    @Test
    fun `outline parent rules match volume chapter section hierarchy`() {
        assertTrue(validOutlineParentType("volume", null))
        assertFalse(validOutlineParentType("volume", "chapter"))
        assertTrue(validOutlineParentType("chapter", null))
        assertTrue(validOutlineParentType("chapter", "volume"))
        assertFalse(validOutlineParentType("chapter", "chapter"))
        assertTrue(validOutlineParentType("section", "chapter"))
        assertFalse(validOutlineParentType("section", "volume"))
        assertEquals("chapter", outlineSuggestedChildType("volume"))
        assertEquals("section", outlineSuggestedChildType("chapter"))
    }

    @Test
    fun `outline parent picker excludes self descendants and invalid types`() {
        val volume = replica("outline", "v1", "{\"title\":\"第一卷\",\"node_type\":\"volume\",\"parent_id\":null}")
        val chapter = replica("outline", "c1", "{\"title\":\"第一章\",\"node_type\":\"chapter\",\"parent_id\":\"v1\"}")
        val section = replica("outline", "s1", "{\"title\":\"第一节\",\"node_type\":\"section\",\"parent_id\":\"c1\"}")
        val otherChapter = replica("outline", "c2", "{\"title\":\"第二章\",\"node_type\":\"chapter\",\"parent_id\":\"v1\"}")
        val records = listOf(volume, chapter, section, otherChapter)

        val sectionParents = outlineParentOptions(records, "s1", "section").map { it.entityId }
        assertTrue("c1" in sectionParents)
        assertTrue("c2" in sectionParents)
        assertFalse("v1" in sectionParents)
        assertFalse("s1" in sectionParents)

        val chapterParents = outlineParentOptions(records, "c1", "chapter").map { it.entityId }
        assertEquals(listOf("v1"), chapterParents)
    }

    @Test
    fun `narrative labels hide backend enum vocabulary`() {
        assertEquals("待复检", narrativeStatusLabel("pending_review"))
        assertEquals("已兑现", narrativeStatusLabel("fulfilled"))
        assertEquals("关键", narrativePriorityLabel("critical"))
        assertEquals("悬念", narrativeDebtTypeLabel("question"))
    }

    @Test
    fun `sync health favors actionable states`() {
        assertEquals("仅保存在这台手机", syncHealthTitle(false, 3, 1, "boom"))
        assertEquals("有 2 个版本需要选择", syncHealthTitle(true, 4, 2, "boom"))
        assertEquals("上次同步没有完成", syncHealthTitle(true, 4, 0, "boom"))
        assertEquals("有 4 项等待同步", syncHealthTitle(true, 4, 0, null))
        assertEquals("已经同步", syncHealthTitle(true, 0, 0, null))
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
