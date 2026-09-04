package com.siming.mobile.ui

import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import com.siming.mobile.data.creation.CreationAgentProgressEvent
import com.siming.mobile.data.creation.CreationAgentTurnRecords
import kotlin.test.assertEquals
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.put

class ReferenceCreationConversationWorkspaceTest {
    @Test
    fun `creation capacity failure exposes the direct api configuration action`() {
        val capacityFailure = CreationAgentProgressEvent(
            type = "conversation_context",
            message = "容量未知",
            status = "failed",
            data = buildJsonObject {
                put("error_code", MobileConversationContextErrorCode.CAPACITY_UNKNOWN)
            },
        )
        val unrelated = CreationAgentProgressEvent(
            type = "conversation_context",
            message = "其他失败",
            status = "failed",
            data = buildJsonObject { put("error_code", "conversation_source_changed") },
        )

        assertTrue(creationNeedsCapacityConfiguration(listOf(capacityFailure)))
        assertFalse(creationNeedsCapacityConfiguration(listOf(capacityFailure, unrelated)))
    }

    @Test
    fun `gateway creation reopen restores checkpoint range quotes and ledger from persisted detail`() {
        val state = buildJsonObject {
            put("status", "ready")
            put("active_checkpoint_id", "checkpoint-1")
            put("latest_checkpoint_id", "checkpoint-1")
            put("original_history_tokens", 5_000)
            put("active_history_tokens", 1_200)
            put("recent_exact_turn_count", 3)
        }
        val detail = buildJsonObject {
            put("id", "checkpoint-1")
            put("source_range", buildJsonObject {
                put("first_sequence", 1)
                put("last_sequence", 18)
                put("message_count", 18)
            })
            put("author_quotes", buildJsonArray {
                add(buildJsonObject {
                    put("message_id", "message-1")
                    put("exact_quote", "主角必须姓林")
                    put("superseded", false)
                })
            })
            put("execution_ledger", buildJsonArray {
                add(buildJsonObject {
                    put("step_id", "step-1")
                    put("tool", "save_creation_stage")
                    put("status", "ok")
                    put("summary", "世界观已保存")
                })
            })
        }
        val session = CreationAgentTurnRecords.withConversationContext(
            buildJsonObject {
                put("id", "session-1")
                put("draft", buildJsonObject {})
            },
            state,
            detail,
        )

        val restored = creationConversationContextState(session, emptyList())

        assertEquals(1L, restored?.sourceRange?.firstSequence)
        assertEquals("主角必须姓林", restored?.authorQuotes?.single()?.exactQuote)
        assertEquals("世界观已保存", restored?.executionLedger?.single()?.detail)
        assertTrue(restored?.checkpointDetailLoaded == true)
    }
}
