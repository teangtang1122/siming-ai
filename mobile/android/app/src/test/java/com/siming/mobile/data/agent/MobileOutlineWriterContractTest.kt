package com.siming.mobile.data.agent

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class MobileOutlineWriterContractTest {
    @Test
    fun `outline generator shares PC inactivity and local reasoning policy`() {
        val asset = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .map { File(it, "mobile/android/app/src/main/assets/pc_workspace_prompt_contract.json") }
            .first(File::isFile)
        val contract = Json.parseToJsonElement(asset.readText()).jsonObject
        val names = contract.getValue("tool_names").toString()
        assertFalse(names.contains("create_outline_node"))
        assertTrue(names.contains("outline_writer"))
        val execution = contract.getValue("outline_generation").jsonObject
        assertEquals(1, execution.getValue("default_batch_count").jsonPrimitive.int)
        assertEquals(180_000, execution.getValue("stream_idle_timeout_ms").jsonPrimitive.int)
        assertEquals("false", outlineGenerationExtraBody(execution, "local_llama_cpp:qwen3.8-27b-q3")!!
            .getValue("chat_template_kwargs").jsonObject.getValue("enable_thinking").toString())
        assertEquals(outlineGenerationExtraBody(execution, "qwen3.8-27b-q3"),
            outlineGenerationExtraBody(execution, "local_llama_cpp:qwen3.8-27b-q3"))
        assertNull(outlineGenerationExtraBody(execution, "deepseek-flash"))
        assertEquals(4 * 1024, MobileNativeToolBudgetContract.declaredResultJsonBytes("outline_writer"))
        assertTrue(contract.getValue("workspace_system_template").jsonPrimitive.content
            .contains("“下一章”只规划一章，batch_count=1"))
    }

    @Test
    fun `writer uses the reviewed count without inheriting cataloging scene expansion`() {
        val asset = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .map { File(it, "mobile/android/app/src/main/assets/pc_workspace_prompt_contract.json") }
            .first(File::isFile)
        val contract = Json.parseToJsonElement(asset.readText()).jsonObject
        val template = JsonArray(listOf(contract.getValue("writer_output_tools").jsonObject.getValue("outline")))
        val original = template.toString()
        for (count in listOf(1, 6, 11)) {
            val tool = bindOutlineOutputNodeCount(template, count).single().jsonObject
            val nodes = tool.getValue("function").jsonObject.getValue("parameters").jsonObject
                .getValue("properties").jsonObject.getValue("nodes").jsonObject
            assertEquals(count, nodes.getValue("minItems").jsonPrimitive.int)
            assertEquals(count, nodes.getValue("maxItems").jsonPrimitive.int)
        }
        assertEquals(original, template.toString())
        assertFailsWith<IllegalArgumentException> { bindOutlineOutputNodeCount(template, 0) }
        assertFailsWith<IllegalArgumentException> { bindOutlineOutputNodeCount(template, 13) }
        val system = contract.getValue("writer_systems").jsonObject.getValue("outline").jsonPrimitive.content
        assertTrue(system.contains("精确总数"))
        assertTrue(system.contains("章 summary"))
        assertFalse(system.contains("必须额外输出"))
    }
}
