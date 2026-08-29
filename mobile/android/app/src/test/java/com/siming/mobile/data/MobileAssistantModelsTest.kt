package com.siming.mobile.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class MobileAssistantModelsTest {
    @Test
    fun `outline draft character names resolve to canonical link objects`() {
        val links = mobileOutlineCharacterLinks(
            listOf("陆糖", "陆承宇", "陆糖"),
            mapOf(
                "陆糖" to "character-1",
                "character-1" to "character-1",
                "陆承宇" to "character-2",
            ),
        )

        assertEquals(2, links.size)
        assertEquals(
            listOf("character-1", "character-2"),
            links.map { it.jsonObject.getValue("character_id").jsonPrimitive.content },
        )
        assertEquals(
            listOf("AI关联", "AI关联"),
            links.map { it.jsonObject.getValue("role_in_scene").jsonPrimitive.content },
        )
        assertEquals(JsonArray(emptyList()), mobileOutlineCharacterLinks(emptyList(), emptyMap()))
    }

    @Test
    fun `outline draft rejects an unknown character before formal writes`() {
        val error = assertFailsWith<IllegalArgumentException> {
            mobileOutlineCharacterLinks(
                listOf("陆糖", "未知角色"),
                mapOf("陆糖" to "character-1"),
            )
        }

        assertEquals(true, error.message?.contains("未知角色"))
    }

    @Test
    fun `chapter tool result becomes an editor draft instead of assistant text`() {
        val draft = MobilePendingChapterDraft.fromJson(
            "project-1",
            buildJsonObject {
                put("draft_id", "draft-1")
                put("title", "雨夜追踪")
                put("content", "雨幕压住了街灯。")
                put("outline_node_id", "outline-1")
                put("draft_status", "pending")
                put("context_snapshot", buildJsonObject {
                    put("context_manifest_id", "manifest-1")
                    put("execution_route", "android_standalone")
                })
            },
        )

        assertNotNull(draft)
        assertEquals("draft-1", draft.draftId)
        assertEquals("雨幕压住了街灯。", draft.content)
        assertEquals("manifest-1", draft.contextManifestId)
        assertEquals("android_standalone", draft.executionRoute)
    }
}
