package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class MobileWorkspaceAgentOutlinePaginationTest {
    @Test
    fun `outline child pages report the full total and stable continuation`() {
        val rows = listOf(
            outline("child-b", 1),
            outline("child-a", 1),
            outline("child-c", 2),
        )

        val first = mobileOutlinePage(rows, cursor = 0, limit = 20)

        assertEquals(listOf("child-a", "child-b"), first.items.map(::id))
        assertEquals(3, first.totalItems)
        assertEquals(2, first.limit)
        assertEquals(2, first.nextCursor)

        val result = mobileOutlineSearchResult(
            detail = "大纲节点 第一章：子节点共 3 个，本页返回 2 个",
            data = JsonArray(emptyList()),
            page = first,
            args = buildJsonObject {
                put("summary_chars", 60)
                put("linked_limit", 1)
            },
            nodeId = "parent",
        )
        val page = result.getValue("page").jsonObject
        assertEquals(3, page.getValue("total_items").jsonPrimitive.int)
        assertEquals(2, page.getValue("returned_items").jsonPrimitive.int)
        assertTrue(page.getValue("has_more").jsonPrimitive.content.toBoolean())
        val next = result.getValue("next_arguments").jsonObject
        assertEquals("parent", next.getValue("node_id").jsonPrimitive.content)
        assertEquals(2, next.getValue("cursor").jsonPrimitive.int)
        assertEquals(60, next.getValue("summary_chars").jsonPrimitive.int)
        assertEquals(1, next.getValue("linked_limit").jsonPrimitive.int)

        val second = mobileOutlinePage(rows, cursor = next.getValue("cursor").jsonPrimitive.int, limit = 2)
        assertEquals(listOf("child-c"), second.items.map(::id))
        assertNull(second.nextCursor)
        val lastResult = mobileOutlineSearchResult(
            detail = "大纲节点 第一章：子节点共 3 个，本页返回 1 个",
            data = JsonArray(emptyList()),
            page = second,
            args = JsonObject(emptyMap()),
            nodeId = "parent",
        )
        assertFalse("next_arguments" in lastResult)
    }

    @Test
    fun `outline summaries and linked characters honor the shared range contract`() {
        val payload = buildJsonObject {
            put("id", "outline-1")
            put("node_type", "chapter")
            put("title", "第一章")
            put("summary", "0123456789")
            put("actual_summary", "abcdefghij")
            put("planned_summary", "甲乙丙丁戊己庚辛壬癸")
            put("linked_characters", JsonArray(listOf(
                buildJsonObject {
                    put("character_id", "c1")
                    put("role_in_scene", "protagonist")
                },
                buildJsonObject { put("character_id", "c2") },
                buildJsonObject { put("character_id", "c3") },
            )))
        }
        val item = mobileOutlineSearchItem(
            payload = payload,
            args = buildJsonObject {
                put("summary_offset_chars", 3)
                put("summary_chars", 4)
                put("linked_cursor", 1)
                put("linked_limit", 1)
            },
            characterNamesById = mapOf("c1" to "甲", "c2" to "乙", "c3" to "丙"),
        )

        assertEquals("3456", item.getValue("summary").jsonPrimitive.content)
        val range = item.getValue("summary_range").jsonObject
        assertEquals(10, range.getValue("total_chars").jsonPrimitive.int)
        assertEquals(7, range.getValue("next_offset_chars").jsonPrimitive.int)
        val linked = item.getValue("linked_characters") as JsonArray
        assertEquals("c2", linked.single().jsonObject.getValue("id").jsonPrimitive.content)
        assertEquals("乙", linked.single().jsonObject.getValue("name").jsonPrimitive.content)
        val linkedPage = item.getValue("linked_page").jsonObject
        assertEquals(1, linkedPage.getValue("cursor").jsonPrimitive.int)
        assertEquals(1, linkedPage.getValue("limit").jsonPrimitive.int)
        assertEquals(1, linkedPage.getValue("returned_items").jsonPrimitive.int)
        assertEquals(3, linkedPage.getValue("total_items").jsonPrimitive.int)
        assertEquals(2, linkedPage.getValue("next_cursor").jsonPrimitive.int)
        assertTrue(linkedPage.getValue("has_more").jsonPrimitive.content.toBoolean())
    }

    private fun outline(id: String, order: Int) = buildJsonObject {
        put("id", id)
        put("sort_order", order)
    }

    private fun id(value: JsonObject): String = value.getValue("id").jsonPrimitive.content
}
