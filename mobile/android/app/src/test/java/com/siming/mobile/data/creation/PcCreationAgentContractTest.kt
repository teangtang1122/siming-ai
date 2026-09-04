package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

class PcCreationAgentContractTest {
    @Test
    fun currentPcCreationAgentPromptRequiresImmediateIncrementalWrites() {
        val contractFile = listOf(
            java.io.File("app/src/main/assets/pc_workspace_prompt_contract.json"),
            java.io.File("src/main/assets/pc_workspace_prompt_contract.json"),
        ).firstOrNull { it.isFile }
            ?: error("pc_workspace_prompt_contract.json not found from ${System.getProperty("user.dir")}")
        val raw = contractFile.readText()
        val contract = PcCreationAgentContract(raw)
        val prompt = contract.systemPrompt("session-test")
        assertTrue("立即增量写入" in prompt)
        assertTrue("不得积攒到采访结束" in prompt)
        assertTrue("最多完成一次成功的写工具调用" in prompt)
        assertTrue("patch_creation_artifact" in contract.toolNames)
        assertTrue("generate_creation_artifact" in contract.toolNames)
        assertEquals(
            setOf(
                "get_creation_operation",
                "cancel_creation_operation",
                "pause_creation_operation",
                "resume_creation_operation",
                "retry_creation_operation",
                "undo_creation_artifact",
                "list_creation_artifact_versions",
                "get_creation_artifact_diff",
                "restore_creation_artifact_version",
                "preview_creation_import",
                "apply_creation_import",
            ),
            contract.excludedPcToolNames,
        )
        assertTrue(contract.excludedPcToolNames.none(contract.toolNames::contains))
        assertTrue("confirm_creation_artifact" in contract.writeToolNames)
        assertTrue("patch_creation_artifact" in contract.revisionToolNames)
        assertEquals(1, contract.maxSuccessfulWritesPerTurn)
        assertEquals(3, contract.maxFailedWritesPerTurn)
    }

    @Test
    fun `mobile uses the same global category replacement as PC`() {
        val contractFile = listOf(
            java.io.File("app/src/main/assets/pc_workspace_prompt_contract.json"),
            java.io.File("src/main/assets/pc_workspace_prompt_contract.json"),
        ).firstOrNull { it.isFile } ?: error("pc_workspace_prompt_contract.json not found")
        val contract = PcCreationAgentContract(contractFile.readText())

        assertEquals(
            setOf("set_tool_categories"),
            contract.toolSchemas(emptyList()).mapNotNull { schema ->
                (((schema as? JsonObject)?.get("function") as? JsonObject)?.get("name") as? JsonPrimitive)
                    ?.contentOrNull
            }.toSet(),
        )
        val entityNames = contract.toolSchemas(listOf("creation_data")).mapNotNull { schema ->
            (((schema as? JsonObject)?.get("function") as? JsonObject)?.get("name") as? JsonPrimitive)
                ?.contentOrNull
        }.toSet()
        val flowNames = contract.toolSchemas(listOf("creation_flow")).mapNotNull { schema ->
            (((schema as? JsonObject)?.get("function") as? JsonObject)?.get("name") as? JsonPrimitive)
                ?.contentOrNull
        }.toSet()
        assertTrue("patch_creation_entity" in entityNames)
        assertTrue("patch_creation_session" in entityNames)
        assertFalse("finalize_creation_session" in entityNames)
        assertTrue("finalize_creation_session" in flowNames)
        assertFalse("patch_creation_entity" in flowNames)
    }
}
