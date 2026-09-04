package com.siming.mobile.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class AssistantConversationContextDetailTest {
    @Test
    fun `standalone checkpoint state exposes the complete user visible detail contract`() {
        val state = buildJsonObject {
            put("status", "ready")
            put("active_checkpoint_id", "checkpoint-2")
            put("latest_checkpoint_id", "checkpoint-2")
            put("recent_exact_turn_count", 4)
            put("original_history_tokens", 8_000)
            put("active_history_tokens", 2_000)
            put("trigger", "projected_next_step_over_capacity")
            put("capacity_assurance", "conservative")
            put("provider", "android_direct_api")
            put("model", "model-a")
            put("covered_sequence_ranges", buildJsonArray {
                add(buildJsonObject {
                    put("first_sequence", 1)
                    put("last_sequence", 8)
                    put("message_count", 8)
                })
                add(buildJsonObject {
                    put("first_sequence", 11)
                    put("last_sequence", 16)
                    put("message_count", 6)
                })
            })
        }
        val detail = buildJsonObject {
            put("id", "checkpoint-2")
            put("status", "ready")
            put("policy_version", 1)
            put("schema_version", "conversation_checkpoint.v1")
            put("checkpoint_tokens", 600)
            put("source_range", buildJsonObject {
                put("first_sequence", 11)
                put("last_sequence", 16)
                put("message_count", 6)
            })
            put("warnings", JsonArray(listOf(JsonPrimitive("保守计数"))))
            put("author_quotes", buildJsonArray {
                add(buildJsonObject {
                    put("message_id", "message-1")
                    put("exact_quote", "不要改主角名字")
                    put("purpose", "author_constraint")
                    put("superseded", false)
                })
            })
            put("execution_ledger", buildJsonArray {
                add(buildJsonObject {
                    put("step_id", "step-1")
                    put("tool", "update_character")
                    put("status", "ok")
                    put("summary", "角色已更新")
                    put("resource_ids", JsonArray(listOf(JsonPrimitive("character-1"))))
                })
            })
        }

        val parsed = mobileAssistantContextStateFromJson(state, detail)

        assertEquals("checkpoint-2", parsed.activeCheckpointId)
        assertEquals(2, parsed.coveredSequenceRanges.size)
        assertEquals(600, parsed.checkpointTokens)
        assertEquals("不要改主角名字", parsed.authorQuotes.single().exactQuote)
        assertEquals("角色已更新", parsed.executionLedger.single().detail)
        assertEquals(listOf("character-1"), parsed.executionLedger.single().resourceIds)
        assertEquals("消息序号 1–8（8 条）；消息序号 11–16（6 条）", conversationContextRangeLabel(parsed))
        assertEquals("下一模型步骤预计超过当前模型容量", conversationContextTriggerLabel(parsed.trigger))
        assertEquals("保守上界", conversationContextAssuranceLabel(parsed.capacityAssurance))
        assertTrue(parsed.checkpointDetailLoaded)
        assertTrue(hasConversationContextDetails(parsed))
    }

    @Test
    fun `failed context keeps explicit retry policy and supports a new conversation action`() {
        val state = mobileAssistantContextStateFromJson(buildJsonObject {
            put("status", "failed")
            put("error_code", "conversation_checkpoint_failed")
            put("error_detail", "整理失败")
            put("retryable", false)
        })

        assertFalse(state.retryable)
        assertEquals("失败", conversationContextStatusLabel(state.status))
        assertTrue(hasConversationContextDetails(state))
    }

    @Test
    fun `missing checkpoint detail is not interpreted as an empty quote and ledger set`() {
        val state = mobileAssistantContextStateFromJson(buildJsonObject {
            put("status", "ready")
            put("active_checkpoint_id", "checkpoint-1")
            put("original_history_tokens", 1_000)
        })

        assertFalse(state.checkpointDetailLoaded)
        assertTrue(state.authorQuotes.isEmpty())
        assertTrue(state.executionLedger.isEmpty())
    }
}
