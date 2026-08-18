package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import kotlin.test.Test
import kotlin.test.assertEquals

class OutlineTreeModelTest {
    private fun record(id: String, payload: String) = ReplicaEntity(
        key = ReplicaEntity.key("p1", "outline", id),
        projectId = "p1",
        entityType = "outline",
        entityId = id,
        revision = 1,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "hash-$id",
        serverModifiedAt = "2026-08-18T00:00:00Z",
    )

    @Test
    fun `builds volume chapter section hierarchy by parent and sort order`() {
        val tree = buildOutlineTree(
            listOf(
                record("s1", """{"title":"第一节","node_type":"section","parent_id":"c1","sort_order":0}"""),
                record("c2", """{"title":"第二章","node_type":"chapter","parent_id":"v1","sort_order":1}"""),
                record("v1", """{"title":"第一卷","node_type":"volume","sort_order":0}"""),
                record("c1", """{"title":"第一章","node_type":"chapter","parent_id":"v1","sort_order":0}"""),
            ),
        )

        assertEquals(listOf("v1"), tree.map { it.record.entityId })
        assertEquals(listOf("c1", "c2"), tree.single().children.map { it.record.entityId })
        assertEquals(listOf("s1"), tree.single().children.first().children.map { it.record.entityId })
    }

    @Test
    fun `moves only inside the current sibling group`() {
        val siblings = buildOutlineTree(
            listOf(
                record("a", """{"title":"A","node_type":"volume","sort_order":0}"""),
                record("b", """{"title":"B","node_type":"volume","sort_order":1}"""),
                record("c", """{"title":"C","node_type":"volume","sort_order":2}"""),
            ),
        )

        assertEquals(listOf("b", "a", "c"), moveOutlineSiblingIds(siblings, "a", 1))
        assertEquals(listOf("a", "c", "b"), moveOutlineSiblingIds(siblings, "c", -1))
    }
}
