package com.siming.mobile.ui

import com.siming.mobile.data.agent.mobileCapacityBoundTaskConfig
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.DirectApiConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReferenceAssistantWorkspaceTest {
    @Test
    fun `character summary prefers live goal over conflict and profile text`() {
        val character = replica(
            "character",
            "c1",
            "{\"name\":\"陆糖\",\"current_goal\":\"查清病毒来源\",\"active_conflict\":\"不能暴露真实来历\",\"personality\":\"冷静\"}",
        )
        assertEquals("查清病毒来源", characterPrimarySummary(character))
    }

    @Test
    fun `character tracking defaults on and respects explicit false`() {
        assertTrue(characterTracked(replica("character", "c1", "{\"name\":\"甲\"}")))
        assertFalse(characterTracked(replica("character", "c2", "{\"name\":\"乙\",\"is_evolution_tracked\":false}")))
    }

    @Test
    fun `mobile labels hide backend enum vocabulary`() {
        assertEquals("主角", characterRoleLabel("protagonist"))
        assertEquals("力量体系", worldDimensionLabel("power_system"))
        assertEquals("文化", worldDimensionLabel(""))
    }

    @Test
    fun `reference list and snippet are compact for mobile cards`() {
        assertEquals("剑术 · 阵法 · 炼丹", compactReferenceList("[\"剑术\", \"阵法\", \"炼丹\"]"))
        assertEquals("天地 玄黄 宇宙…", referenceSnippet("天地  玄黄\n宇宙洪荒", 8))
    }

    @Test
    fun `assistant quick actions only provide user messages without app routing`() {
        assertTrue(assistantQuickActions.any { it.label == "续写下一章" && "下一章" in it.prompt })
        assertTrue(assistantQuickActions.any { it.label == "检查世界观冲突" && "世界观" in it.prompt })
    }

    @Test
    fun `unknown direct model capacity exposes the explicit configuration action`() {
        assertTrue(
            requiresDirectContextCapacityConfiguration(
                MobileAssistantContextState(
                    status = "failed",
                    errorCode = "conversation_capacity_unknown",
                ),
            ),
        )
        assertFalse(
            requiresDirectContextCapacityConfiguration(
                MobileAssistantContextState(
                    status = "failed",
                    errorCode = "conversation_checkpoint_failed",
                ),
            ),
        )
    }

    @Test
    fun `assistant task model override uses bounded 256k fallback`() {
        val config = DirectApiConfig(
            displayName = "test",
            baseUrl = "https://example.test/v1",
            apiKey = "secret",
            model = "general-model",
            taskModels = mapOf(DirectApiConfig.TASK_ASSISTANT to "assistant-model"),
            contextWindowTokens = 128_000,
        )

        val resolved = mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_ASSISTANT)

        assertEquals("assistant-model", resolved.model)
        assertEquals(DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS, resolved.contextWindowTokens)
        assertEquals(DirectApiConfig.DEFAULT_AGENT_OUTPUT_TOKENS, resolved.maxOutputTokens)
        assertEquals(128_000, config.contextWindowTokens)
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
