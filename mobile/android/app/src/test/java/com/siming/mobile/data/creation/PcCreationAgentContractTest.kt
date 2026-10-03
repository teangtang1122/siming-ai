package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class PcCreationAgentContractTest {
    @Test
    fun `generated entities match shared PC validation fixtures`() {
        val file = listOf(
            java.io.File("app/src/main/assets/pc_workspace_prompt_contract.json"),
            java.io.File("src/main/assets/pc_workspace_prompt_contract.json"),
        ).first { it.isFile }
        val contract = PcCreationAgentContract(file.readText(Charsets.UTF_8)).entities
        val fixture = checkNotNull(javaClass.classLoader?.getResourceAsStream("creation_entity_generation.json"))
            .bufferedReader(Charsets.UTF_8).use { Json.parseToJsonElement(it.readText()).jsonObject }
        fixture.getValue("cases").jsonArray.forEach { value ->
            val case = value.jsonObject
            val data = case.getValue("data").jsonObject
            val original = data.toString()
            val stage = (case.getValue("stage") as JsonPrimitive).content
            val target = case["target"] as? JsonObject
            val expected = case["error"] as? JsonObject
            if (expected == null) {
                contract.validateGenerated(stage, data, target)
            } else {
                val error = kotlin.test.assertFailsWith<CreationGenerationException> {
                    contract.validateGenerated(stage, data, target)
                }
                val diagnostic = error.diagnostic.getValue("data").jsonObject
                assertEquals(expected["reason"], diagnostic["reason"])
                assertEquals(expected["path"], diagnostic["path"])
            }
            assertEquals(original, data.toString())
        }
        assertEquals("faction", contract.entityType("place", Json.parseToJsonElement("""{"dimension":"factions"}""").jsonObject))
        assertEquals("location", contract.entityType("place", Json.parseToJsonElement("""{"dimension":"geography"}""").jsonObject))
    }

    @Test
    fun `large write receipts match PC fixtures and preserve exact metadata`() {
        val contractFile = listOf(
            java.io.File("app/src/main/assets/pc_workspace_prompt_contract.json"),
            java.io.File("src/main/assets/pc_workspace_prompt_contract.json"),
        ).first { it.isFile }
        val contract = PcCreationAgentContract(contractFile.readText(Charsets.UTF_8))
        assertEquals(2 * 1024, contract.writeResultMaxBytesFor("patch_creation_artifact"))
        val fixture = checkNotNull(javaClass.classLoader?.getResourceAsStream("creation_write_receipts.json"))
            .bufferedReader(Charsets.UTF_8).use { it.readText() }
            .replace("DOCUMENT_BODY", "完整角色背景。".repeat(5_000))
        Json.parseToJsonElement(fixture).jsonArray.forEach { value ->
            val case = value.jsonObject
            val source = case.getValue("data").jsonObject
            val original = source.toString()
            val projected = contract.projectWriteResultData(source)

            assertEquals(case["expected_data"], projected)
            assertTrue(projected.toString().toByteArray(Charsets.UTF_8).size < contract.writeResultMaxBytes)
            assertTrue(original.toByteArray(Charsets.UTF_8).size > contract.writeResultMaxBytes)
            assertEquals(original, source.toString())
        }
    }

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
    fun `mobile uses the same scoped small category replacement as PC`() {
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
        val entityNames = contract.toolSchemas(listOf("creation_entities")).mapNotNull { schema ->
            (((schema as? JsonObject)?.get("function") as? JsonObject)?.get("name") as? JsonPrimitive)
                ?.contentOrNull
        }.toSet()
        val flowNames = contract.toolSchemas(listOf("creation_completion")).mapNotNull { schema ->
            (((schema as? JsonObject)?.get("function") as? JsonObject)?.get("name") as? JsonPrimitive)
                ?.contentOrNull
        }.toSet()
        assertTrue("patch_creation_entity" in entityNames)
        assertFalse("patch_creation_session" in entityNames)
        assertFalse("finalize_creation_session" in entityNames)
        assertTrue("finalize_creation_session" in flowNames)
        assertFalse("patch_creation_entity" in flowNames)
        assertEquals(5, entityNames.size)
        assertTrue(runCatching { contract.normalizeCategories(listOf("chapter_writing")) }.isFailure)
        assertTrue(runCatching { contract.normalizeCategories(listOf("creation_setup")) }.isFailure)
        assertTrue(runCatching {
            contract.normalizeCategories(listOf("creation_session", "creation_artifacts", "creation_entities"))
        }.isFailure)
        val controller = contract.toolSchemas(emptyList()).single().jsonObject.getValue("function").jsonObject
        val menu = controller.getValue("parameters").jsonObject.getValue("properties").jsonObject
            .getValue("enabled_categories").jsonObject.getValue("items").jsonObject.getValue("enum").jsonArray
        assertTrue(menu.all { it.jsonPrimitive.content.startsWith("creation_") })
        assertFalse(menu.any { it.jsonPrimitive.content == "creation_setup" })
    }
}
