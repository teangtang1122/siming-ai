package com.siming.mobile.data.creation

import com.siming.mobile.data.agent.*
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import kotlin.test.*
import kotlinx.serialization.json.*
import org.junit.Test

class CreationConversationsTest {
    @Test fun `canonical histories and checkpoints survive restart independently`() = runBlocking {
        val directory = Files.createTempDirectory("creation-conversations").toFile()
        try {
            val store = MobileAssistantConversationStore(directory)
            val imported = store.ensureConversationArchive("book", "old", "creation", "book", "old",
                listOf(MobileArchivedConversationTurn("t1", "author", "answer", "completed", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                    MobileArchivedConversationTurn("t2", "later", "reply", "completed", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")))
            val attempt = store.beginCheckpoint("book", "old", 1, 2, buildJsonObject { put("model", "test") },
                "a".repeat(64), imported.contextState.revision, 100)
            store.publishCheckpoint("book", "old", attempt.checkpoint.id, attempt.contextStateRevision,
                MobileCheckpointSemanticDraft(MobileConversationCheckpoint.emptySemanticNavigation(), emptyList()),
                emptyList(), emptyList(), buildJsonObject {}, 50)
            store.ensureConversationArchive("book", "new", "creation", "book", "new", emptyList())
            val reopened = MobileAssistantConversationStore(directory)
            val old = assertNotNull(reopened.snapshot("book", "old"))
            val fresh = assertNotNull(reopened.snapshot("book", "new"))
            assertEquals(4, old.messages.size)
            assertEquals(attempt.checkpoint.id, old.activeCheckpoint?.id)
            assertTrue(fresh.messages.isEmpty())
            assertNull(fresh.activeCheckpoint)
            val failed = reopened.beginCheckpoint("book", "old", 3, 4, buildJsonObject { put("model", "test") },
                "b".repeat(64), old.contextState.revision, 100)
            reopened.failCheckpoint("book", "old", failed.checkpoint.id, failed.contextStateRevision, errorDetail = "历史整理输出超限")
            val preserved = assertNotNull(MobileAssistantConversationStore(directory).snapshot("book", "old"))
            assertEquals(old.messages, preserved.messages)
            assertEquals(old.activeCheckpoint?.id, preserved.activeCheckpoint?.id)
        } finally { directory.deleteRecursively() }
    }
    private fun session() = buildJsonObject {
        put("id", "book")
        put("draft", buildJsonObject {
            put("constraints", buildJsonObject { put("status", "confirmed") })
            put("agent_turns", JsonArray(listOf(CreationAgentTurnRecords.pending("old", "turn-old"))))
            put("agent_context_state", buildJsonObject { put("checkpoint_id", "checkpoint-old") })
        })
    }

    @Test fun `migration is idempotent and conversations isolate projections while sharing business data`() {
        val migrated = CreationConversations.migrate(session(), "legacy")
        assertEquals(migrated, CreationConversations.migrate(migrated, "ignored"))
        val fresh = CreationConversations.add(migrated, "new", false)
        assertTrue(CreationAgentTurnRecords.turns(fresh).isEmpty())
        assertNull(CreationAgentTurnRecords.contextState(fresh))
        val visible = CreationAgentTurnRecords.agentVisibleDraft(fresh)
        assertFalse(visible.toString().contains("checkpoint-old"))
        assertFalse(visible.containsKey(CreationConversations.INDEX))
        assertFalse(visible.containsKey(CreationConversations.ACTIVE))
        assertEquals("confirmed", fresh["draft"]!!.jsonObject["constraints"]!!.jsonObject["status"]!!.jsonPrimitive.content)
        val persisted = Json.parseToJsonElement(CreationConversations.capture(fresh).toString()).jsonObject
        val restored = CreationConversations.select(persisted, "legacy")
        assertEquals("old", CreationAgentTurnRecords.turns(restored).single()["user_content"]!!.jsonPrimitive.content)
        assertEquals("checkpoint-old", CreationAgentTurnRecords.contextState(restored)!!["checkpoint_id"]!!.jsonPrimitive.content)
        assertTrue(CreationAgentTurnRecords.turns(CreationConversations.select(restored, "new")).isEmpty())
        assertFailsWith<IllegalStateException> { CreationConversations.select(restored, "foreign") }
    }
    @Test fun `late result cannot overwrite a different active conversation`() {
        val old = CreationConversations.migrate(session(), "old")
        val current = CreationConversations.add(old, "new", false)
        assertFailsWith<MobileConversationContextException> { CreationConversations.requireWriteTarget(current, old) }
        CreationConversations.requireWriteTarget(old, current, allowSwitch = true)
        val refreshed = CreationConversations.preserveIndex(buildJsonObject {
            put("id", "book"); put("draft", buildJsonObject { put("business_revision", 2) })
        }, current)
        assertEquals("new", CreationConversations.active(refreshed))
        assertEquals(2, CreationConversations.entries(refreshed).size)
        assertTrue(CreationAgentTurnRecords.turns(refreshed).isEmpty())
    }

}
