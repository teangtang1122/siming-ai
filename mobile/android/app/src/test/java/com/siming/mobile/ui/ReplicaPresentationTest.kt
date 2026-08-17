package com.siming.mobile.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

class ReplicaPresentationTest {
    @Test
    fun `project form keeps arrays booleans and integers typed`() {
        val values = canonicalFormValues(
            "project",
            mapOf(
                "title" to "测试作品",
                "description" to "简介",
                "tags" to "玄幻\n轻科幻",
                "narrative_perspective" to "third_person",
                "writing_style" to "natural",
                "forbidden_sentence_patterns" to "",
                "rhetoric_guidelines" to "",
                "short_sentences" to "true",
                "custom_style_prompt" to "",
                "daily_word_goal" to "9000",
            ),
        )

        assertEquals(
            JsonArray(listOf(JsonPrimitive("玄幻"), JsonPrimitive("轻科幻"))),
            values["tags"],
        )
        assertEquals(true, values["short_sentences"])
        assertEquals(9000, values["daily_word_goal"])
    }

    @Test
    fun `nullable PC identifiers stay null instead of empty strings`() {
        val values = canonicalFormValues(
            "chapter",
            mapOf(
                "title" to "第一章",
                "outline_node_id" to "",
                "content" to "正文",
            ),
        )

        assertNull(values["outline_node_id"])
    }

    @Test
    fun `outline form parses metadata and character links as JSON`() {
        val values = canonicalFormValues(
            "outline",
            mapOf(
                "title" to "归墟待敌",
                "node_type" to "chapter",
                "parent_id" to "",
                "summary" to "维持困阵",
                "status" to "pending",
                "sort_order" to "2000",
                "characters" to "[{\"character_id\":\"character-1\",\"role_in_scene\":\"protagonist\"}]",
                "metadata" to "{\"hook\":\"裂隙亮起\"}",
            ),
        )

        val characters = values["characters"] as JsonArray
        val first = characters.single() as JsonObject
        assertEquals("character-1", (first["character_id"] as JsonPrimitive).content)
        assertEquals(
            "裂隙亮起",
            ((values["metadata"] as JsonObject)["hook"] as JsonPrimitive).content,
        )
        assertNull(values["parent_id"])
        assertEquals(2000, values["sort_order"])
    }

    @Test
    fun `governance nullable chapter number does not become zero`() {
        val values = canonicalFormValues(
            "foreshadowing",
            mapOf(
                "title" to "传道石伏笔",
                "description" to "后续兑现",
                "status" to "open",
                "importance" to "high",
                "storyline" to "主线",
                "source_chapter_id" to "",
                "target_chapter_id" to "",
                "target_chapter_number" to "",
                "resolved_chapter_id" to "",
                "evidence" to "",
                "resolution_note" to "",
                "resolution_evidence" to "",
                "verification_note" to "",
                "closed_by" to "",
            ),
        )

        assertNull(values["target_chapter_number"])
        assertNull(values["source_chapter_id"])
        assertNull(values["resolved_chapter_id"])
    }
}
