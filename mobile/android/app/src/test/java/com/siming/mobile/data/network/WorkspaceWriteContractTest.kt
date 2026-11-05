package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertFailsWith
import com.siming.mobile.data.agent.characterToolPayload
import com.siming.mobile.data.agent.characterToolAiConfig
import com.siming.mobile.data.agent.outlineToolPayload
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

class WorkspaceWriteContractTest {
    @Test fun `outline tool character IDs reach authoring links`() {
        val args = Json.parseToJsonElement("""{"id":"o1","title":"chapter","node_type":"chapter","status":"pending","character_ids":["person-id"]}""").jsonObject
        val old = Json.parseToJsonElement("""{"characters":[{"character_id":"previous"}]}""").jsonObject
        val saved = PcApiPayloads.syncMutation("outline", JsonObject(old + outlineToolPayload(args)), "p1", "o1")
        assertFalse("character_names" in saved)
        assertEquals("person-id", saved.getValue("characters").jsonArray.single().jsonObject.getValue("character_id").jsonPrimitive.content)
        assertFalse("character_ids" in saved)
        val clear = outlineToolPayload(buildJsonObject { put("character_ids", JsonArray(emptyList())) })
        val cleared = PcApiPayloads.syncMutation("outline", JsonObject(saved + clear), "p1", "o1")
        assertEquals(0, cleared.getValue("characters").jsonArray.size)
    }

    @Test fun `character tool speech style and config map to formal records`() {
        val args = Json.parseToJsonElement("""{"name":"Alice","role_type":"protagonist","speech_style":"always whispers","ai_config":{"tone_style":"calm","custom_system_prompt":"whisper"}}""").jsonObject
        val saved = PcApiPayloads.authoring("character", characterToolPayload(args), create = true)
        assertFalse("speech_style" in saved)
        assertFalse("ai_config" in saved)
        assertEquals("说话风格：always whispers", saved.getValue("background").jsonPrimitive.content)
        val config = requireNotNull(characterToolAiConfig(args, "person-id"))
        assertEquals("whisper", config.getValue("custom_system_prompt").jsonPrimitive.content)
        assertEquals("person-id", config.getValue("character_id").jsonPrimitive.content)
    }

    @Test fun `non stream responses reject incomplete native tools`() = runBlocking {
        listOf(
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS to """{"choices":[{"finish_reason":"length","message":{"role":"assistant","tool_calls":[{"id":"c1","type":"function","function":{"name":"create_character","arguments":"{\"name\":\"Alice\"}"}}]}}]}""",
            DirectApiConfig.PROTOCOL_RESPONSES to """{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"output":[{"type":"function_call","call_id":"c1","name":"create_character","arguments":"{\"name\":\"Alice\"}"}]}""",
        ).forEach { (protocol, body) ->
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody(body))
                server.start()
                val api = DirectApiClient(allowCleartextForTests = true)
                val config = DirectApiConfig("audit", server.url("/").toString(), "test-only", "audit", protocol = protocol)
                val error = assertFailsWith<DirectIncompleteResponseException> {
                    api.agentTurn(config, emptyList(), JsonArray(emptyList()))
                }
                assertEquals(
                    if (protocol == DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS) "length" else "max_output_tokens",
                    error.finishReason,
                )
                assertEquals(true, error.outputLimited)
                assertEquals(1, server.requestCount)
            }
        }
    }
}
