package com.siming.mobile.data.network

import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest

class DirectApiClientTest {
    @Test
    fun `task model without its own capacity profile uses 256k fallback`() {
        val config = DirectApiConfig(
            displayName = "test",
            baseUrl = "https://api.example.test/v1",
            apiKey = "secret",
            model = "general-model",
            availableModels = listOf("general-model", "writer-model"),
            taskModels = mapOf(DirectApiConfig.TASK_WRITING to "writer-model"),
            contextWindowTokens = 128_000,
        )

        val writing = config.forTask(DirectApiConfig.TASK_WRITING)
        assertEquals("writer-model", writing.model)
        assertEquals(DirectApiConfig.DEFAULT_CONTEXT_WINDOW_TOKENS, writing.contextWindowTokens)
        val assistant = config.forTask(DirectApiConfig.TASK_ASSISTANT)
        assertEquals("general-model", assistant.model)
        assertEquals(128_000, assistant.contextWindowTokens)
        assertEquals(config.availableModels, config.summary().availableModels)
        assertEquals("writer-model", config.summary().taskModels[DirectApiConfig.TASK_WRITING])
    }

    @Test
    fun `model discovery falls back to v1 and keeps authorization private`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                assertEquals("Bearer secret-test-key", request.getHeader("Authorization"))
                return if (request.path == "/v1/models") {
                    jsonResponse("""{"data":[{"id":"model-b"},{"id":"model-a"}]}""")
                } else {
                    MockResponse().setResponseCode(405)
                }
            }
        },
    ) { server ->
        val models = runBlocking {
            testClient().discoverModels(server.url("/").toString(), "secret-test-key")
        }
        assertEquals(listOf("model-a", "model-b"), models)
    }

    @Test
    fun `responses API extracts output text`() = withServer(
        pathDispatcher(
            "/responses" to jsonResponse(
                """{"output":[{"type":"message","content":[{"type":"output_text","text":"独立模式可用"}]}]}""",
            ),
        ),
    ) { server ->
        val result = runBlocking {
            testClient().complete(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                "system",
                "user",
            )
        }
        assertEquals("独立模式可用", result)
    }

    @Test
    fun `responses API falls back to v1 after method mismatch at root`() = withServer(
        pathDispatcher(
            "/responses" to MockResponse().setResponseCode(405),
            "/v1/responses" to jsonResponse("""{"output_text":"v1 路径可用"}"""),
        ),
    ) { server ->
        val result = runBlocking {
            testClient().complete(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                "system",
                "user",
            )
        }
        assertEquals("v1 路径可用", result)
    }

    @Test
    fun `automatic protocol falls back to chat completions`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/chat/completions" -> jsonResponse(
                    """{"choices":[{"message":{"role":"assistant","content":"Chat 可用"}}]}""",
                )
                else -> MockResponse().setResponseCode(404)
            }
        },
    ) { server ->
        val result = runBlocking {
            testClient().complete(config(server, DirectApiConfig.PROTOCOL_AUTO), "system", "user")
        }
        assertEquals("Chat 可用", result)
    }

    @Test
    fun `transient upstream errors retry before returning content`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.path != "/responses") return MockResponse().setResponseCode(404)
                    return if (attempts.incrementAndGet() < 3) {
                        jsonResponse(
                            """{"error":{"message":"Upstream request failed","type":"upstream_error"}}""",
                            502,
                        )
                    } else {
                        jsonResponse("""{"output_text":"重试成功"}""")
                    }
                }
            },
        ) { server ->
            val result = runBlocking {
                DirectApiClient(
                    allowCleartextForTests = true,
                    retryDelaysMillis = listOf(0, 0),
                ).complete(config(server, DirectApiConfig.PROTOCOL_RESPONSES), "system", "user")
            }
            assertEquals("重试成功", result)
            assertEquals(3, attempts.get())
        }
    }

    @Test
    fun `standalone text resumes from verified checkpoint after length stop`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    assertEquals("/chat/completions", request.path)
                    val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                    assertTrue(body.getValue("stream").jsonPrimitive.content.toBoolean())
                    return if (attempts.incrementAndGet() == 1) {
                        sseResponse(
                            """{"choices":[{"delta":{"content":"第一段"},"finish_reason":"length"}]}""",
                        )
                    } else {
                        val messages = body.getValue("messages").jsonArray.map { it.jsonObject }
                        val resumeRequest = messages.last().getValue("content").jsonPrimitive.content
                        val expected = resumeRequest.substringAfter("：\n")
                        sseResponse(
                            """{"choices":[{"delta":{"content":"$expected 第二段"},"finish_reason":"stop"}]}""",
                        )
                    }
                }
            },
        ) { server ->
            val checkpoints = mutableListOf<String>()
            val result = runBlocking {
                testClient().completeResumable(
                    config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                    "system",
                    "user",
                    onCheckpoint = { checkpoints += it },
                )
            }
            assertEquals("第一段 第二段", result)
            assertEquals(listOf("第一段", "第一段 第二段"), checkpoints)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun `responses stream uses the same verified checkpoint resume contract`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    assertEquals("/responses", request.path)
                    val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                    return if (attempts.incrementAndGet() == 1) {
                        sseResponse(
                            """{"type":"response.output_text.delta","delta":"第一段"}""",
                            """{"type":"response.incomplete","response":{"status":"incomplete"}}""",
                        )
                    } else {
                        val input = body.getValue("input").jsonArray.map { it.jsonObject }
                        val expected = input.last().getValue("content").jsonPrimitive.content
                            .substringAfter("：\n")
                        sseResponse(
                            """{"type":"response.output_text.delta","delta":"$expected 第二段"}""",
                            """{"type":"response.completed","response":{"status":"completed"}}""",
                        )
                    }
                }
            },
        ) { server ->
            val result = runBlocking {
                testClient().completeResumable(
                    config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                    "system",
                    "user",
                )
            }
            assertEquals("第一段 第二段", result)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun `standalone text rejects an unverified resume seam`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = if (attempts.incrementAndGet() == 1) {
                    sseResponse(
                        """{"choices":[{"delta":{"content":"安全前缀"},"finish_reason":"length"}]}""",
                    )
                } else {
                    sseResponse(
                        """{"choices":[{"delta":{"content":"没有握手的重复前缀"},"finish_reason":"stop"}]}""",
                    )
                }
            },
        ) { server ->
            val checkpoints = mutableListOf<String>()
            assertFailsWith<IllegalArgumentException> {
                runBlocking {
                    testClient().completeResumable(
                        config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                        "system",
                        "user",
                        maxResumeAttempts = 1,
                        onCheckpoint = { checkpoints += it },
                    )
                }
            }
            assertEquals(listOf("安全前缀"), checkpoints)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun `chat agent turn sends PC tools and parses native function calls`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                assertEquals("/chat/completions", request.path)
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertEquals(0.3, body.getValue("temperature").jsonPrimitive.double)
                assertEquals("get_project_info", body.getValue("tools").jsonArray[0]
                    .jsonObject.getValue("function").jsonObject.getValue("name").jsonPrimitive.content)
                return jsonResponse(
                    """{"choices":[{"message":{"role":"assistant","content":null,"reasoning_content":"先读取作品资料","tool_calls":[{"id":"call-1","type":"function","function":{"name":"get_project_info","arguments":"{ \"b\":2, \"id\":\"project-1\", \"a\":1 }"}}]}}],"usage":{"prompt_tokens":37,"completion_tokens":5,"total_tokens":42}}""",
                )
            }
        },
    ) { server ->
        val turn = runBlocking {
            testClient().agentTurn(
                config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                messages = listOf(buildJsonObject { put("role", "user"); put("content", "读取作品") }),
                tools = singleTool("get_project_info"),
            )
        }
        assertEquals("get_project_info", turn.toolCalls.single().name)
        assertEquals("project-1", turn.toolCalls.single().arguments["id"]?.jsonPrimitive?.content)
        assertEquals("call-1", turn.toolCalls.single().id)
        assertEquals("{ \"b\":2, \"id\":\"project-1\", \"a\":1 }", turn.toolCalls.single().rawArgumentsJson)
        val replayedArguments = ((turn.assistantMessage.getValue("tool_calls") as JsonArray).single()
            .jsonObject.getValue("function") as JsonObject).getValue("arguments").jsonPrimitive.content
        assertEquals(turn.toolCalls.single().rawArgumentsJson, replayedArguments)
        assertEquals(37, turn.promptTokens)
        assertEquals("先读取作品资料", turn.reasoningContent)
        assertEquals("先读取作品资料", turn.assistantMessage["reasoning_content"]?.jsonPrimitive?.content)
    }

    @Test
    fun `invalid native arguments fail with stable protocol reason`() = withServer(
        pathDispatcher(
            "/chat/completions" to jsonResponse(
                """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-invalid","type":"function","function":{"name":"get_project_info","arguments":"[1,2]"}}]}}]}""",
            ),
        ),
    ) { server ->
        val error = assertFailsWith<DirectNativeToolProtocolException> {
            runBlocking {
                testClient().agentTurn(
                    config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                    messages = listOf(buildJsonObject { put("role", "user"); put("content", "读取") }),
                    tools = singleTool("get_project_info"),
                )
            }
        }
        assertEquals("native_assistant_transaction_invalid", error.reason)
    }

    @Test
    fun `responses agent turn preserves function call history and parses next call`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                assertEquals("/responses", request.path)
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val input = body.getValue("input").jsonArray.map { it.jsonObject }
                assertTrue(input.any { it["type"]?.jsonPrimitive?.content == "function_call" })
                assertTrue(input.any { it["type"]?.jsonPrimitive?.content == "function_call_output" })
                return jsonResponse(
                    """{"output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"先核对章节列表"}]},{"type":"function_call","call_id":"call-2","name":"list_chapters","arguments":"{}"}],"usage":{"input_tokens":51,"output_tokens":4,"total_tokens":55}}""",
                )
            }
        },
    ) { server ->
        val turn = runBlocking {
            testClient().agentTurn(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                messages = listOf(
                    buildJsonObject { put("role", "system"); put("content", "system") },
                    buildJsonObject { put("role", "user"); put("content", "继续") },
                    buildJsonObject {
                        put("role", "assistant")
                        put("content", "")
                        put("tool_calls", buildJsonArray {
                            add(buildJsonObject {
                                put("id", "call-1")
                                put("type", "function")
                                put("function", buildJsonObject {
                                    put("name", "get_project_info")
                                    put("arguments", "{}")
                                })
                            })
                        })
                    },
                    buildJsonObject {
                        put("role", "tool")
                        put("tool_call_id", "call-1")
                        put("content", "{\"status\":\"ok\"}")
                    },
                ),
                tools = singleTool("list_chapters"),
            )
        }
        assertEquals(51, turn.promptTokens)
        assertEquals("先核对章节列表", turn.reasoningContent)
        assertEquals("list_chapters", turn.toolCalls.single().name)
        assertEquals("call-2", turn.toolCalls.single().id)
    }

    @Test
    fun `chat agent stream emits real deltas and buffers tool arguments until terminal`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertTrue(body.getValue("stream").jsonPrimitive.content.toBoolean())
                return sseResponse(
                    """{"choices":[{"delta":{"reasoning_content":"先读取"},"finish_reason":null}]}""",
                    """{"choices":[{"delta":{"content":"正在处理"},"finish_reason":null}]}""",
                    """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-live","function":{"name":"get_project_","arguments":"{\"id\":"}}]},"finish_reason":null}]}""",
                    """{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"info","arguments":"\"project-1\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":29}}""",
                )
            }
        },
    ) { server ->
        val content = mutableListOf<String>()
        val reasoning = mutableListOf<String>()
        val turn = runBlocking {
            testClient().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                messages = listOf(buildJsonObject { put("role", "user"); put("content", "读取") }),
                tools = singleTool("get_project_info"),
                onContentDelta = { content += it },
                onReasoningDelta = { reasoning += it },
            )
        }
        assertEquals(listOf("正在处理"), content)
        assertEquals(listOf("先读取"), reasoning)
        assertEquals("get_project_info", turn.toolCalls.single().name)
        assertEquals("project-1", turn.toolCalls.single().arguments["id"]?.jsonPrimitive?.content)
        assertEquals("{\"id\":\"project-1\"}", turn.toolCalls.single().rawArgumentsJson)
        assertEquals(29, turn.promptTokens)
    }

    @Test
    fun `responses agent stream emits text and reconstructs function call`() = withServer(
        pathDispatcher(
            "/responses" to sseResponse(
                """{"type":"response.reasoning_summary_text.delta","delta":"核对上下文"}""",
                """{"type":"response.output_text.delta","delta":"准备读取"}""",
                """{"type":"response.output_item.added","item":{"type":"function_call","id":"item-1","call_id":"call-r","name":"list_chapters","arguments":""}}""",
                """{"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"{}"}""",
                """{"type":"response.completed","response":{"status":"completed","output":[],"usage":{"input_tokens":41}}}""",
            ),
        ),
    ) { server ->
        val content = mutableListOf<String>()
        val turn = runBlocking {
            testClient().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                messages = listOf(buildJsonObject { put("role", "user"); put("content", "列出章节") }),
                tools = singleTool("list_chapters"),
                onContentDelta = { content += it },
            )
        }
        assertEquals(listOf("准备读取"), content)
        assertEquals("核对上下文", turn.reasoningContent)
        assertEquals("list_chapters", turn.toolCalls.single().name)
        assertEquals("call-r", turn.toolCalls.single().id)
        assertEquals(41, turn.promptTokens)
    }

    @Test
    fun `deepseek thinking stream omits unsupported tool choice without disabling thinking`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertFalse("tool_choice" in body)
                assertEquals(
                    "enabled",
                    body.getValue("thinking").jsonObject.getValue("type").jsonPrimitive.content,
                )
                return sseResponse(
                    """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-deepseek","function":{"name":"get_project_info","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}""",
                )
            }
        },
    ) { server ->
        val turn = runBlocking {
            testClient().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS).copy(
                    displayName = "DeepSeek",
                    model = "deepseek-v4-pro",
                ),
                messages = listOf(buildJsonObject { put("role", "user"); put("content", "读取") }),
                tools = singleTool("get_project_info"),
                toolChoice = "required",
                extraBody = buildJsonObject {
                    put("thinking", buildJsonObject { put("type", "enabled") })
                },
            )
        }
        assertEquals("get_project_info", turn.toolCalls.single().name)
    }

    @Test
    fun `gemini responses stream omits unsupported tool choice`() = withServer(
        object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertFalse("tool_choice" in body)
                return sseResponse(
                    """{"type":"response.output_item.added","item":{"type":"function_call","id":"item-gemini","call_id":"call-gemini","name":"list_chapters","arguments":"{}"}}""",
                    """{"type":"response.completed","response":{"status":"completed","output":[]}}""",
                )
            }
        },
    ) { server ->
        val turn = runBlocking {
            testClient().streamAgentTurn(
                config(server, DirectApiConfig.PROTOCOL_RESPONSES).copy(
                    displayName = "Google Gemini",
                    model = "gemini-2.5-flash",
                ),
                messages = listOf(buildJsonObject { put("role", "user"); put("content", "列出章节") }),
                tools = singleTool("list_chapters"),
                toolChoice = "auto",
            )
        }
        assertEquals("list_chapters", turn.toolCalls.single().name)
    }

    @Test
    fun `chat agent stream retries once without rejected tool choice`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                    return if (attempts.incrementAndGet() == 1) {
                        assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                        jsonResponse(
                            """{"error":{"message":"thinking mode does not support this tool_choice"}}""",
                            400,
                        )
                    } else {
                        assertFalse("tool_choice" in body)
                        sseResponse(
                            """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-retry","function":{"name":"get_project_info","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}""",
                        )
                    }
                }
            },
        ) { server ->
            val turn = runBlocking {
                testClient().streamAgentTurn(
                    config(server, DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS),
                    messages = listOf(buildJsonObject { put("role", "user"); put("content", "读取") }),
                    tools = singleTool("get_project_info"),
                    toolChoice = "required",
                )
            }
            assertEquals("get_project_info", turn.toolCalls.single().name)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun `responses agent turn retries once without rejected tool choice`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                    return if (attempts.incrementAndGet() == 1) {
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        jsonResponse(
                            """{"error":{"message":"Thinking mode does not support this tool choice"}}""",
                            400,
                        )
                    } else {
                        assertFalse("tool_choice" in body)
                        jsonResponse(
                            """{"output":[{"type":"function_call","call_id":"call-responses-retry","name":"list_chapters","arguments":"{}"}]}""",
                        )
                    }
                }
            },
        ) { server ->
            val turn = runBlocking {
                testClient().agentTurn(
                    config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                    messages = listOf(buildJsonObject { put("role", "user"); put("content", "列出章节") }),
                    tools = singleTool("list_chapters"),
                    toolChoice = "auto",
                )
            }
            assertEquals("list_chapters", turn.toolCalls.single().name)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun `tool choice rejection after visible output is not replayed`() {
        val attempts = AtomicInteger()
        withServer(
            object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    attempts.incrementAndGet()
                    return sseResponse(
                        """{"type":"response.output_text.delta","delta":"已输出片段"}""",
                        """{"type":"response.failed","error":{"message":"thinking mode does not support this tool_choice"}}""",
                    )
                }
            },
        ) { server ->
            val content = mutableListOf<String>()
            assertFailsWith<DirectApiHttpException> {
                runBlocking {
                    testClient().streamAgentTurn(
                        config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                        messages = listOf(buildJsonObject { put("role", "user"); put("content", "继续") }),
                        tools = singleTool("list_chapters"),
                        toolChoice = "required",
                        onContentDelta = { content += it },
                    )
                }
            }
            assertEquals(listOf("已输出片段"), content)
            assertEquals(1, attempts.get())
        }
    }

    @Test
    fun `responses agent stream rejects done sentinel without completed event`() = withServer(
        pathDispatcher(
            "/responses" to sseResponse(
                """{"type":"response.output_text.delta","delta":"未完成内容"}""",
            ),
        ),
    ) { server ->
        assertFailsWith<IOException> {
            runBlocking {
                testClient().streamAgentTurn(
                    config(server, DirectApiConfig.PROTOCOL_RESPONSES),
                    messages = listOf(buildJsonObject { put("role", "user"); put("content", "继续") }),
                    tools = JsonArray(emptyList()),
                )
            }
        }
    }

    @Test
    fun `production client rejects cleartext credential transport`() {
        val error = assertFailsWith<IllegalArgumentException> {
            runBlocking {
                DirectApiClient(retryDelaysMillis = emptyList()).discoverModels(
                    "http://api.example.test/v1",
                    "secret",
                )
            }
        }
        assertTrue(error.message.orEmpty().contains("HTTPS"))
    }

    private fun testClient() = DirectApiClient(
        allowCleartextForTests = true,
        retryDelaysMillis = emptyList(),
    )

    private fun config(server: MockWebServer, protocol: String) = DirectApiConfig(
        displayName = "test",
        baseUrl = server.url("/").toString(),
        apiKey = "secret-test-key",
        model = "model-a",
        protocol = protocol,
    )

    private fun pathDispatcher(vararg routes: Pair<String, MockResponse>) = object : Dispatcher() {
        private val responses = routes.toMap()
        override fun dispatch(request: RecordedRequest): MockResponse =
            responses[request.path] ?: MockResponse().setResponseCode(404)
    }

    private fun singleTool(name: String) = JsonArray(
        listOf(
            buildJsonObject {
                put("type", "function")
                put("function", buildJsonObject {
                    put("name", name)
                    put("description", "test")
                    put("parameters", JsonObject(mapOf("type" to JsonPrimitive("object"))))
                })
            },
        ),
    )

    private fun jsonResponse(body: String, status: Int = 200) = MockResponse()
        .setResponseCode(status)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private fun sseResponse(vararg events: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "text/event-stream")
        .setBody(events.joinToString(separator = "\n\n", postfix = "\n\ndata: [DONE]\n\n") { "data: $it" })

    private fun withServer(dispatcher: Dispatcher, block: (MockWebServer) -> Unit) {
        MockWebServer().use { server ->
            server.dispatcher = dispatcher
            server.start()
            block(server)
        }
    }
}
