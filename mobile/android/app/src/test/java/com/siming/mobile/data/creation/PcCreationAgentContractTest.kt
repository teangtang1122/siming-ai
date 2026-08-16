package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertTrue

class PcCreationAgentContractTest {
    @Test
    fun currentPcCreationAgentPromptRequiresImmediateIncrementalWrites() {
        val contractFile = listOf(
    java.io.File("app/src/main/assets/pc_workspace_prompt_contract.json"),
    java.io.File("src/main/assets/pc_workspace_prompt_contract.json"),
).firstOrNull { it.isFile } ?: error("pc_workspace_prompt_contract.json not found from ${System.getProperty("user.dir")}")
val raw = contractFile.readText()
        val contract = PcCreationAgentContract(raw)
        val prompt = contract.systemPrompt("session-test")
        assertTrue("立即增量写入" in prompt)
        assertTrue("不要把数据积攒到“采访结束”后才生成" in prompt)
        assertTrue("patch_creation_artifact" in contract.toolNames)
        assertTrue("generate_creation_artifact" in contract.toolNames)
    }
}
