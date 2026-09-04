package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

class CreationAgentTurnRecordsTest {
    @Test
    fun `legacy bubbles migrate once for display but never enter model replay`() {
        val legacySession = buildJsonObject {
            put("id", "session-1")
            put("draft", buildJsonObject {
                put("agent_history", buildJsonArray {
                    add(buildJsonObject {
                        put("id", "user-old")
                        put("role", "user")
                        put("content", "旧问题")
                    })
                    add(buildJsonObject {
                        put("id", "assistant-old")
                        put("role", "assistant")
                        put("content", "旧回答")
                    })
                })
            })
        }

        val migrated = CreationAgentTurnRecords.migrateLegacyHistory(legacySession)
        val draft = migrated["draft"] as JsonObject

        assertNull(draft["agent_history"])
        assertEquals(1, CreationAgentTurnRecords.turns(migrated).size)
        assertEquals(listOf("旧问题", "旧回答"), CreationAgentTurnRecords.displayMessages(migrated).map { it.string("content") })
        assertFalse(CreationAgentTurnRecords.turns(migrated).single().boolean("replayable"))
    }

    @Test
    fun `creation audit keeps every closed turn beyond the legacy twenty turn cap`() {
        val initial = buildJsonObject {
            put("id", "session-long")
            put("draft", buildJsonObject {})
        }
        val turns = (1..25).map { index ->
            CreationAgentTurnRecords.complete(
                pending = CreationAgentTurnRecords.pending(
                    userContent = "问题 $index",
                    id = "turn-$index",
                    createdAt = "2026-01-${index.toString().padStart(2, '0')}T00:00:00Z",
                ),
                reply = "回答 $index",
                modelMessages = JsonArray(emptyList()),
                toolResults = JsonArray(emptyList()),
                replayable = false,
                executionRoute = "device",
            )
        }

        val stored = CreationAgentTurnRecords.withTurns(initial, turns)

        assertEquals(25, CreationAgentTurnRecords.turns(stored).size)
        assertEquals(25, CreationAgentTurnRecords.archivedTurns(stored).size)
        assertEquals("问题 1", CreationAgentTurnRecords.archivedTurns(stored).first().userContent)
        assertEquals("回答 25", CreationAgentTurnRecords.archivedTurns(stored).last().assistantContent)
    }

    @Test
    fun `interrupted creation audit closes with an exact aborted receipt`() {
        val pending = CreationAgentTurnRecords.pending("未完成问题", id = "turn-interrupted")
        val session = buildJsonObject {
            put("id", "session-interrupted")
            put("draft", buildJsonObject {
                put(CreationAgentTurnRecords.STORAGE_KEY, JsonArray(listOf(pending)))
            })
        }

        val recovered = CreationAgentTurnRecords.recoverInterruptedTurns(session)
        val turn = CreationAgentTurnRecords.turns(recovered).single()

        assertEquals("aborted", turn.string("status"))
        assertEquals("上一轮任务未完成，已在新任务开始前安全终止。", turn.string("reply"))
        assertEquals("aborted", CreationAgentTurnRecords.archivedTurns(recovered).single().status)
    }

    @Test
    fun `gateway context detail survives turn updates but never enters the model visible draft`() {
        val base = buildJsonObject {
            put("id", "session-context")
            put("draft", buildJsonObject {})
        }
        val withContext = CreationAgentTurnRecords.withConversationContext(
            base,
            buildJsonObject {
                put("status", "ready")
                put("active_checkpoint_id", "checkpoint-1")
            },
            buildJsonObject {
                put("id", "checkpoint-1")
                put("author_quotes", JsonArray(emptyList()))
                put("execution_ledger", JsonArray(emptyList()))
            },
        )
        val updated = CreationAgentTurnRecords.withTurns(
            withContext,
            listOf(CreationAgentTurnRecords.pending("继续", id = "turn-1")),
            gatewayConversationId = "conversation-1",
        )
        val reopened = CreationAgentTurnRecords.mergeRemoteSession(
            remote = buildJsonObject {
                put("id", "session-context")
                put("revision", 2)
                put("draft", buildJsonObject { put("genre", "悬疑") })
            },
            local = updated,
        )

        assertNotNull(CreationAgentTurnRecords.contextState(reopened))
        assertNotNull(CreationAgentTurnRecords.checkpointDetail(reopened))
        assertEquals(1, CreationAgentTurnRecords.turns(reopened).size)
        assertEquals("conversation-1", CreationAgentTurnRecords.gatewayConversationId(reopened))
        val visible = CreationAgentTurnRecords.agentVisibleDraft(reopened)
        assertEquals("悬疑", visible.string("genre"))
        assertFalse(visible.containsKey(CreationAgentTurnRecords.STORAGE_KEY))
        assertFalse(visible.values.any { it.toString().contains("checkpoint-1") })
    }

    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.boolean(name: String): Boolean = string(name).toBooleanStrictOrNull() ?: false
}
