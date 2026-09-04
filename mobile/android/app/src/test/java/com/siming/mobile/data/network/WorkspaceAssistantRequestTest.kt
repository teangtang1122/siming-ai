package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class WorkspaceAssistantRequestTest {
    @Test
    fun `mobile assistant sends only latest message and canonical conversation id`() {
        val encoded = Json.encodeToString(
            WorkspaceAssistantRequest(
                message = "继续检查",
                conversationId = "conversation-1",
                activeChapterDraftId = "draft-1",
            ),
        )
        val root = Json.parseToJsonElement(encoded).jsonObject
        assertEquals("conversation-1", root.getValue("conversation_id").jsonPrimitive.content)
        assertEquals("继续检查", root.getValue("message").jsonPrimitive.content)
        assertEquals("draft-1", root.getValue("active_chapter_draft_id").jsonPrimitive.content)
        assertEquals(false, "history" in root)
    }
}
