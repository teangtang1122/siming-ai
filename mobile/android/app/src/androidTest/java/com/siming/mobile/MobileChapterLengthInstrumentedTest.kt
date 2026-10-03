package com.siming.mobile

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.siming.mobile.data.agent.MobileAssistantConversationStore
import com.siming.mobile.data.agent.MobileWorkspaceAgent
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.File
import java.net.Proxy
import java.util.UUID
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MobileChapterLengthInstrumentedTest {
    @Test fun independentPhoneKeepsCompleteShortDraftAndStopsWithoutSaving() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val projectId = "length-test-${UUID.randomUUID()}"
        val outlineId = "outline-${UUID.randomUUID()}"
        val content = "山门风起，少年拾起断剑。\n".repeat(180).trim()
        fun entity(type: String, id: String, payload: JsonObject) = ReplicaEntity(
            ReplicaEntity.key(projectId, type, id), projectId, type, id,
            0, "upsert", payload.toString(), "test", "2026-10-01T00:00:00Z",
        )
        val snapshot = listOf(
            entity("project", projectId, buildJsonObject {
                put("_record_type", "project")
                put("id", projectId); put("title", "手机独立篇幅测试")
                put("writing_style", "natural"); put("narrative_perspective", "third_person")
                put("forbidden_sentence_patterns", "")
            }),
            entity("outline", outlineId, buildJsonObject {
                put("_record_type", "outline_node")
                put("id", outlineId); put("project_id", projectId); put("title", "第一章 山门")
                put("node_type", "chapter"); put("summary", "少年在山门拾起断剑。")
                put("sort_order", 1)
            }),
        )
        val server = MockWebServer()
        var assistantRequests = 0
        var proseRequests = 0
        fun toolReply(name: String, arguments: JsonObject): MockResponse {
            val delta = buildJsonObject {
                put("role", "assistant")
                put("tool_calls", buildJsonArray { add(buildJsonObject {
                    put("index", 0); put("id", "call-${UUID.randomUUID()}"); put("type", "function")
                    put("function", buildJsonObject {
                        put("name", name); put("arguments", arguments.toString())
                    })
                }) })
            }
            return streamReply(delta, "tool_calls")
        }
        fun projectFieldsReply(): MockResponse = streamReply(buildJsonObject {
            put("role", "assistant")
            put("tool_calls", JsonArray(listOf("writing_style", "forbidden_sentence_patterns").mapIndexed { index, field ->
                buildJsonObject {
                    put("index", index); put("id", "field-$index"); put("type", "function")
                    put("function", buildJsonObject {
                        put("name", "get_project_info")
                        put("arguments", buildJsonObject { put("field", field) }.toString())
                    })
                }
            }))
        }, "tool_calls")
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val messages = body.getValue("messages").jsonArray
                if (body["tools"] == null) {
                    proseRequests++
                    assertTrue(messages.last().jsonObject.getValue("content").jsonPrimitive.content.contains("3000"))
                    return streamReply(buildJsonObject { put("content", content) }, "stop")
                }
                val lastTool = messages.lastOrNull { it.jsonObject["role"] == JsonPrimitive("tool") }
                    ?.jsonObject?.get("content")?.jsonPrimitive?.content
                    ?.let { Json.parseToJsonElement(it).jsonObject }
                val lastData = lastTool?.get("data")?.jsonObject
                val manifestId = lastData?.get("context_manifest_id") ?: lastData?.get("manifest_id")
                if (assistantRequests in 4..5 && manifestId == null) {
                    android.util.Log.e("ChapterLengthTest", "Missing manifest step=$assistantRequests lastTool=$lastTool")
                    return MockResponse().setResponseCode(500).setBody("Missing context manifest: $lastTool")
                }
                return when (assistantRequests++) {
                    0 -> toolReply("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf("project_info", "writing_context").map(::JsonPrimitive)))
                    })
                    1 -> projectFieldsReply()
                    2 -> toolReply("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf("writing_context", "chapter_writing").map(::JsonPrimitive)))
                    })
                    3 -> toolReply("prepare_task_context", buildJsonObject {
                        put("task_type", "writing"); put("outline_node_id", outlineId)
                        put("minimum_han_characters", 3000)
                    })
                    4 -> toolReply("submit_context_evidence", buildJsonObject {
                        put("context_manifest_id", manifestId!!)
                        put("sources", JsonArray(emptyList()))
                    })
                    5 -> toolReply("chapter_writer", buildJsonObject {
                        put("outline_node_id", outlineId)
                        put("context_manifest_id", manifestId!!)
                        put("context_selection_token", lastData!!.getValue("context_selection_token"))
                    })
                    else -> MockResponse().setResponseCode(400).setBody("Unexpected model call after draft")
                }
            }
        }
        server.start()
        val store = MobileAssistantConversationStore(File(context.cacheDir, projectId))
        val api = DirectApiClient(OkHttpClient.Builder().proxy(Proxy.NO_PROXY).build(), allowCleartextForTests = true)
        val agent = MobileWorkspaceAgent(context, api, store, loadSnapshot = { snapshot }, saveEntity = { _, _, _, _ ->
            error("Draft generation must not write formal entities")
        })
        try {
            val prompt = "写第一章，希望正文有3000个汉字。"
            val turn = store.beginTurn(projectId, null, prompt)
            val conversation = store.snapshot(projectId, turn.conversationId)!!
            val events = mutableListOf<JsonObject>()
            agent.run(projectId, prompt, DirectApiConfig(
                displayName = "test", baseUrl = server.url("/v1").toString(), apiKey = "test",
                model = "test-model", protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
                contextWindowTokens = 40_000,
            ), conversation, turn) { events += Json.parseToJsonElement(it).jsonObject }
            assertEquals(6, assistantRequests)
            assertEquals(1, proseRequests)
            val draft = agent.pendingChapterDraft(projectId)!!
            assertEquals(content, draft.getValue("content").jsonPrimitive.content)
            assertEquals("pending", draft.getValue("draft_status").jsonPrimitive.content)
            assertTrue(events.any { it["type"] == JsonPrimitive("chapter_draft") })
            val done = events.single { it["type"] == JsonPrimitive("done") }
            assertTrue(done.getValue("detail").jsonPrimitive.content.contains("完整草稿已保留"))
            assertFalse(snapshot.any { it.entityType == "chapter" })
        } finally {
            agent.pendingChapterDraft(projectId)?.get("draft_id")?.jsonPrimitive?.content?.let {
                agent.discardChapterDraft(it)
            }
            server.shutdown()
        }
    }

    private fun streamReply(delta: JsonObject, finish: String): MockResponse {
        fun chunk(value: JsonObject, reason: JsonElement) = buildJsonObject {
            put("choices", buildJsonArray { add(buildJsonObject {
                put("index", 0); put("delta", value); put("finish_reason", reason)
            }) })
        }
        return MockResponse().setHeader("Content-Type", "text/event-stream").setBody(
            "data: ${chunk(delta, JsonNull)}\n\n" +
                "data: ${chunk(buildJsonObject {}, JsonPrimitive(finish))}\n\n" +
                "data: [DONE]\n\n",
        )
    }
}
