package com.siming.mobile.data.creation

import com.siming.mobile.data.agent.MobileAssistantConversationStore
import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import com.siming.mobile.data.agent.MobileConversationContextException
import com.siming.mobile.data.agent.MobileToolTransactionState
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.File
import java.nio.file.Files
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
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest

class MobileCreationConversationAgentTest {
    @Test
    fun `review data rejects absent or string readiness before replacement`() {
        val stageAgent = MobileCreationAgent(contractJson(), DirectApiClient())
        listOf(
            buildJsonObject { put("readiness", "ready") },
            buildJsonObject { put("ready", "true") },
            buildJsonObject { put("ready", 1) },
        ).forEach { invalid ->
            assertFailsWith<IllegalArgumentException> {
                stageAgent.replaceArtifact(session(), "final_review", invalid, "assistant")
            }
        }
    }

    @Test
    fun `standalone final review admits actual chapter and character read batch without saving`() {
        val requests = AtomicInteger()
        val writes = AtomicInteger()
        val source = JsonObject(session().toMutableMap().apply {
            put("draft", JsonObject(session().getValue("draft").jsonObject.toMutableMap().apply {
                put("stages", buildJsonObject {
                    put("characters", buildJsonObject {
                        put("status", "confirmed")
                        put("data", buildJsonObject {
                            put("characters", JsonArray(listOf(buildJsonObject {
                                put("name", "林七"); put("role_type", "protagonist")
                                put("goal", "追出感染源")
                            })))
                            put("relationships", JsonArray(emptyList()))
                        })
                    })
                    put("opening_outline", buildJsonObject {
                        put("status", "confirmed")
                        put("data", buildJsonObject {
                            put("chapters", JsonArray((1..3).map { index -> buildJsonObject {
                                put("client_id", "chapter-$index"); put("title", "第${index}章")
                                put("summary", "追查线索，确定下一步行动。")
                            } }))
                            put("sections", JsonArray(emptyList()))
                        })
                    })
                })
            }))
        })
        fun tool(id: String, name: String, args: JsonObject) = buildJsonObject {
            put("id", id); put("type", "function")
            put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
        }
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val message = when (requests.getAndIncrement()) {
                    0 -> buildJsonObject {
                        put("role", "assistant")
                        put("tool_calls", JsonArray(listOf(tool("categories", "set_tool_categories", buildJsonObject {
                            put("enabled_categories", JsonArray(listOf(JsonPrimitive("creation_entities"))))
                        }))))
                    }
                    1 -> buildJsonObject {
                        put("role", "assistant")
                        put("tool_calls", JsonArray(listOf("opening_outline", "characters").map { artifact ->
                            tool(artifact, "list_creation_entities", buildJsonObject {
                                put("artifact", artifact); put("limit", 10)
                            })
                        }))
                    }
                    else -> {
                        val receipts = body.getValue("messages").jsonArray.map { it.jsonObject }
                            .filter { it.string("role") == "tool" && it.string("tool_call_id") in setOf("opening_outline", "characters") }
                        assertEquals(2, receipts.size)
                        receipts.forEach { receipt ->
                            val result = Json.parseToJsonElement(receipt.string("content")).jsonObject
                            assertEquals("ok", result.string("status"))
                            assertTrue(result.getValue("data").jsonObject.getValue("entities").jsonArray.isNotEmpty())
                        }
                        buildJsonObject { put("role", "assistant"); put("content", "已读取前3章和角色资料，可以继续最终审阅。") }
                    }
                }
                return chatStreamResponse(buildJsonObject {
                    put("choices", JsonArray(listOf(buildJsonObject { put("message", message) })))
                }.toString())
            }
        }) { server ->
            val outcome = runBlocking {
                agent { writes.incrementAndGet() }.run(source, "最终审阅", config(server).copy(contextWindowTokens = 100_000))
            }
            assertEquals(3, requests.get())
            assertEquals(0, writes.get())
            assertEquals(source, outcome.session)
            assertEquals("read_only", outcome.replyStatus)
            assertTrue(outcome.reply.startsWith(PcCreationAgentContract(contractJson()).readOnlyNotice))
            assertEquals(2, outcome.toolResults.count { it.jsonObject.string("tool") == "list_creation_entities" })
            assertTrue(outcome.toolResults.all { it.jsonObject.string("status") == "ok" })
        }
    }

    @Test
    fun `standalone review saves a structured report before reporting generation`() {
        val requests = AtomicInteger()
        val writes = AtomicInteger()
        val contract = PcCreationAgentContract(contractJson())
        val review = buildJsonObject {
            put("ready", true)
            put("blocking", JsonArray(emptyList()))
            put("warnings", JsonArray(emptyList()))
            put("counts", buildJsonObject { put("reviewed_stages", 7) })
        }
        fun tool(name: String, args: JsonObject) = buildJsonObject {
            put("role", "assistant")
            put("tool_calls", JsonArray(listOf(buildJsonObject {
                put("id", "call-$name"); put("type", "function")
                put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
            })))
        }
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val message = when (requests.getAndIncrement()) {
                    0 -> tool("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf("creation_artifacts", "creation_generation").map(::JsonPrimitive)))
                    })
                    1 -> tool("get_creation_artifact", buildJsonObject { put("artifact", "final_review") })
                    2 -> {
                        assertTrue(body.getValue("messages").jsonArray.first().jsonObject.string("content")
                            .contains(contract.readOnlyCompletionInstruction))
                        tool("generate_creation_artifact", buildJsonObject {
                            put("artifact", "final_review"); put("expected_revision", 1)
                            put("instruction", "完成末轮审查并保留报告")
                        })
                    }
                    3 -> buildJsonObject {
                        put("role", "assistant")
                        put("content", buildJsonObject { put("data", review) }.toString())
                    }
                    else -> buildJsonObject {
                        put("role", "assistant"); put("content", "最终审阅已保存，等待作者确认。")
                    }
                }
                val payload = buildJsonObject {
                    put("choices", JsonArray(listOf(buildJsonObject { put("message", message) })))
                }.toString()
                return if (body["stream"]?.jsonPrimitive?.content == "true") chatStreamResponse(payload)
                else MockResponse().setHeader("Content-Type", "application/json").setBody(payload)
            }
        }) { server ->
            val outcome = runBlocking {
                agent { writes.incrementAndGet() }.run(session(), "请完成末轮审查并保留报告", config(server))
            }
            assertEquals(5, requests.get())
            assertEquals(1, writes.get())
            assertFalse(outcome.reply.startsWith(contract.readOnlyNotice))
            val saved = outcome.session.getValue("draft").jsonObject.getValue("stages").jsonObject
                .getValue("final_review").jsonObject
            assertEquals("generated", saved.string("status"))
            assertEquals(review, saved["data"])
        }
    }

    @Test
    fun `standalone patch accepts both declared forms and rejects missing operation fields`() {
        val mobile = agent().agent
        val source = Json.parseToJsonElement("""{"genre":"玄幻","tags":["冒险"]}""").jsonObject
        fun change(json: String) = Json.parseToJsonElement(json).jsonObject

        val patched = mobile.applyChanges(source, listOf(
            change("""{"path":"/genre","action":"set","value":"悬疑"}"""),
            change("""{"path":"/tags/-","op":"add","value":"推理"}"""),
        ))
        assertEquals("悬疑", patched.getValue("genre").jsonPrimitive.content)
        assertEquals(2, patched.getValue("tags").jsonArray.size)
        listOf(
            """{"action":"set","value":"无路径"}""",
            """{"path":"/genre","value":"无动作"}""",
            """{"path":"/genre","action":"set"}""",
            """{"path":"/genre","action":"set","op":"replace","value":"双动作"}""",
            """{"path":"/tags","action":"resize"}""",
            """{"path":"/genre","action":"set","value":"未知字段","unexpected":true}""",
        ).forEach { invalid ->
            assertFailsWith<IllegalArgumentException> {
                mobile.applyChanges(source, listOf(change(invalid)))
            }
        }
    }

    @Test
    fun `standalone reply contract rejects literal tool markup`() {
        val contract = PcCreationAgentContract(contractJson())
        assertEquals(
            "tool_protocol_text",
            contract.replyError("<tool_call><function=patch_creation_artifact></function></tool_call>"),
        )
    }

    @Test
    fun `standalone artifact read rejects unknown stage id`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (requests.getAndIncrement()) {
                0 -> chatStreamResponse(
                    """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_artifacts\"]}"}}]}}]}""",
                )
                1 -> chatStreamResponse(
                    """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-artifact","type":"function","function":{"name":"get_creation_artifact","arguments":"{\"artifact\":\"outline\"}"}}]}}]}""",
                )
                else -> chatStreamResponse(
                    """{"choices":[{"message":{"role":"assistant","content":"请使用真实阶段 ID。"}}]}""",
                )
            }
        }) { server ->
            val source = session()
            val result = runBlocking { agent().run(source, "读取卷纲", config(server)) }
            val failure = result.toolResults.last().jsonObject
            assertEquals(3, requests.get())
            assertEquals("error", failure.string("status"))
            assertEquals("creation_unknown_artifact", failure.getValue("data").jsonObject.string("reason"))
            assertEquals(source["revision"], result.session["revision"])
        }
    }

    @Test
    fun `standalone first outline generation reports five saved volumes`() {
        listOf("whole", "new").forEach(::exerciseOutlineGeneration)
    }

    @Test
    fun `standalone initial volumes repair missing stage fields or report a diagnostic`() {
        listOf("repair", "invalid").forEach(::exerciseOutlineGeneration)
    }

    @Test
    fun `standalone volume refinement preserves the existing main story and other volumes`() {
        exerciseOutlineGeneration("existing")
    }

    @Test
    fun `standalone whole artifact tools save new collections after an existing generation`() {
        listOf("whole_generate", "whole_refine", "whole_regenerate", "whole_locked").forEach(::exerciseOutlineGeneration)
    }

    private fun exerciseOutlineGeneration(mode: String) {
        val calls = AtomicInteger()
        val writes = AtomicInteger()
        val wholeExisting = mode.startsWith("whole_")
        val locked = mode == "whole_locked"
        val initial = mode != "existing" && !wholeExisting
        val repairs = mode == "repair" || mode == "invalid" || locked
        val succeeds = mode != "invalid" && !locked
        val volumes = JsonArray((0..4).map { index -> buildJsonObject {
            put("title", "第${index + 1}卷"); put("start_chapter", index * 48 + 1)
            put("end_chapter", (index + 1) * 48); put("summary", "调查取得新证据。")
        } })
        val outline = buildJsonObject {
            put("story_overview", "追索旧城秘密。"); put("core_conflict", "争夺旧档。")
            put("ending_direction", "公开旧档并承担代价。"); put("target_chapters", 240)
            put("volumes", volumes); put("stage_plan", JsonArray(emptyList()))
        }
        val replacement = JsonObject(volumes.first().jsonObject + ("summary" to JsonPrimitive("修订后的卷摘要。")))
        val base = session()
        val sourceBase = if (initial) base else JsonObject(base + ("draft" to JsonObject(
            base.getValue("draft").jsonObject + ("stages" to buildJsonObject {
                put("macro_outline", buildJsonObject { put("status", "generated"); put("data", outline) })
            }),
        )))
        val source = if (locked) JsonObject(sourceBase + ("draft" to JsonObject(
            sourceBase.getValue("draft").jsonObject + ("artifact_locks" to buildJsonObject {
                put("macro_outline", JsonArray(listOf(JsonPrimitive("/volumes/0/summary"))))
            }),
        ))) else sourceBase
        fun tool(name: String, args: JsonObject) = buildJsonObject {
            put("role", "assistant")
            put("tool_calls", JsonArray(listOf(buildJsonObject {
                put("id", "call-$name"); put("type", "function")
                put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
            })))
        }
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val index = calls.getAndIncrement()
                val message = when {
                    index == 0 -> tool("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf("creation_session", "creation_generation").map(::JsonPrimitive)))
                    })
                    index == 1 -> tool("get_creation_snapshot", buildJsonObject {})
                    index == 2 -> tool(if (locked) "refine_creation_artifact" else if (wholeExisting) "${mode.removePrefix("whole_")}_creation_artifact" else if (initial) "generate_creation_artifact" else "refine_creation_artifact", buildJsonObject {
                        put("artifact", "macro_outline"); put("expected_revision", 1)
                        put("instruction", if (initial) "生成首版全书主线与五卷卷纲" else "只修订第一卷摘要")
                        if (!initial && !wholeExisting) put("entity_id", MobileCreationAgent(contractJson(), DirectApiClient()).openingContract.volumeId(source, volumes.first().jsonObject))
                        else if (mode != "whole" && !wholeExisting) put("entity_type", "volume")
                    })
                    index == 3 || (repairs && index == 4) -> {
                        val prompt = body.getValue("messages").jsonArray.last().jsonObject.string("content")
                        if (mode != "whole" && !wholeExisting) assertTrue("initialize_stage=$initial" in prompt, prompt)
                        if (wholeExisting) assertTrue("调查取得新证据。" in prompt, prompt)
                        if (index == 4) assertTrue((if (locked) "/volumes/0/summary" else "$.data.story_overview") in prompt, prompt)
                        val data = when {
                            wholeExisting -> JsonObject(outline + ("volumes" to JsonArray(listOf(replacement) + volumes.drop(1))))
                            !initial -> buildJsonObject { put("volumes", JsonArray(listOf(replacement))) }
                            repairs && (index == 3 || !succeeds) -> buildJsonObject { put("volumes", volumes) }
                            else -> outline
                        }
                        buildJsonObject { put("role", "assistant"); put("content", buildJsonObject { put("data", data) }.toString()) }
                    }
                    else -> {
                        check(index == if (repairs) 5 else 4)
                        val wire = body.getValue("messages").jsonArray.map { it.jsonObject }
                            .last { it.string("role") == "tool" }.string("content")
                        val result = Json.parseToJsonElement(wire).jsonObject
                        val data = result.getValue("data").jsonObject
                        if (succeeds) {
                            assertEquals("ok", result.string("status"))
                            assertEquals("generated", data.string("status"))
                            assertEquals("true", data.string("saved"))
                            assertEquals("true", data.string("requires_confirmation"))
                            assertEquals("2", data.string("revision"))
                            assertEquals("5", data.getValue("collection_counts").jsonObject.string("volumes"))
                            assertFalse("data" in data)
                            assertTrue(wire.toByteArray(Charsets.UTF_8).size <= 4_096)
                        } else {
                            assertEquals("error", result.string("status"))
                            assertEquals(if (locked) "creation_artifact_locked_changed" else "creation_generated_stage_fields_missing", data.string("reason"))
                            assertEquals(if (locked) "/volumes/0/summary" else "$.data.story_overview", data.string("path"))
                        }
                        buildJsonObject {
                            put("role", "assistant")
                            put("content", if (succeeds) "五卷卷纲已保存，等待审阅确认。" else "生成失败，资料未改动。")
                        }
                    }
                }
                val payload = buildJsonObject { put("choices", JsonArray(listOf(buildJsonObject { put("message", message) }))) }
                return if (body["stream"]?.jsonPrimitive?.content == "true") chatStreamResponse(payload.toString())
                else MockResponse().setHeader("Content-Type", "application/json").setBody(payload.toString())
            }
        }) { server ->
            val outcome = runBlocking { agent { writes.incrementAndGet() }.run(source, "处理卷纲", config(server)) }
            assertEquals(if (repairs) 6 else 5, calls.get())
            assertEquals(if (succeeds) 1 else 0, writes.get())
            assertEquals(if (succeeds) "2" else "1", outcome.session.string("revision"))
            val stages = outcome.session.getValue("draft").jsonObject.getValue("stages").jsonObject
            if (succeeds) {
                val data = stages.getValue("macro_outline").jsonObject.getValue("data").jsonObject
                listOf("story_overview", "core_conflict", "ending_direction").forEach { assertEquals(outline[it], data[it]) }
                assertEquals(if (initial) volumes else JsonArray(listOf(replacement) + volumes.drop(1)), data["volumes"])
            } else {
                assertEquals(source.getValue("draft").jsonObject.getValue("stages"), stages)
            }
        }
    }

    @Test
    fun `standalone factions repair their dimension or report the exact error without changing locations`() {
        listOf(true, false).forEach { repairSucceeds ->
            val calls = AtomicInteger()
            val writes = AtomicInteger()
            val oldEntries = JsonArray(listOf("旧坊", "黑市", "东巷").map { title -> buildJsonObject {
                put("title", title); put("dimension", "geography"); put("content", "保留原有地点资料。")
            } })
            val factions = JsonArray(listOf("黑市会", "巡夜队", "纸业行", "驿站脚行").map { title -> buildJsonObject {
                put("title", title); put("dimension", "factions"); put("content", "凡人组成的本地组织。")
            } })
            val malformed = JsonArray(factions.map { JsonObject(it.jsonObject - "dimension") })
            val base = session()
            val source = JsonObject(base.toMutableMap().apply {
                put("draft", JsonObject(base.getValue("draft").jsonObject.toMutableMap().apply {
                    put("stages", buildJsonObject {
                        put("locations", buildJsonObject {
                            put("status", "generated")
                            put("data", buildJsonObject {
                                put("entries", oldEntries); put("relations", JsonArray(emptyList()))
                            })
                        })
                    })
                }))
            })
            fun tool(name: String, args: JsonObject) = buildJsonObject {
                put("role", "assistant")
                put("tool_calls", JsonArray(listOf(buildJsonObject {
                    put("id", "call-$name"); put("type", "function")
                    put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
                })))
            }
            withServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                    val index = calls.getAndIncrement()
                    val message = when (index) {
                        0 -> tool("set_tool_categories", buildJsonObject {
                            put("enabled_categories", JsonArray(listOf("creation_entities", "creation_generation").map(::JsonPrimitive)))
                        })
                        1 -> tool("list_creation_entities", buildJsonObject {
                            put("artifact", "locations"); put("entity_type", "location")
                        })
                        2 -> tool("generate_creation_artifact", buildJsonObject {
                            put("artifact", "locations"); put("entity_type", "faction"); put("expected_revision", 1)
                            put("instruction", "新增四个本地势力，保留已有地点。")
                            put("context_entity_ids", JsonArray((0..2).map { JsonPrimitive("locations:entries:$it") }))
                        })
                        3, 4 -> {
                            val prompt = body.getValue("messages").jsonArray.last().jsonObject.string("content")
                            assertTrue(prompt.contains("\"required_values\": {\"dimension\": \"factions\"}"), prompt)
                            assertTrue(prompt.contains("目标模式：new"), prompt)
                            if (index == 4) assertTrue(prompt.contains("$.data.entries[0].dimension"), prompt)
                            buildJsonObject {
                                put("role", "assistant")
                                put("content", buildJsonObject {
                                    put("data", buildJsonObject {
                                        put("entries", if (index == 4 && repairSucceeds) factions else malformed)
                                        put("relations", JsonArray(emptyList()))
                                    })
                                }.toString())
                            }
                        }
                        else -> {
                            check(index == 5)
                            val receipt = body.getValue("messages").jsonArray.map { it.jsonObject }
                                .last { it.string("role") == "tool" }.string("content")
                            val result = Json.parseToJsonElement(receipt).jsonObject
                            assertEquals(if (repairSucceeds) "ok" else "error", result.string("status"))
                            if (!repairSucceeds) {
                                assertTrue(receipt.contains("creation_generated_dimension_invalid"), receipt)
                                assertTrue(receipt.contains("$.data.entries[0].dimension"), receipt)
                                assertTrue(receipt.contains("factions"), receipt)
                            }
                            buildJsonObject { put("role", "assistant"); put("content", "本轮处理已结束。") }
                        }
                    }
                    val payload = buildJsonObject { put("choices", JsonArray(listOf(buildJsonObject { put("message", message) }))) }
                    return if (body["stream"]?.jsonPrimitive?.content == "true") chatStreamResponse(payload.toString())
                    else MockResponse().setHeader("Content-Type", "application/json").setBody(payload.toString())
                }
            }) { server ->
                val result = runBlocking { agent { writes.incrementAndGet() }.run(source, "新增本地势力网", config(server)) }
                val entries = result.session.getValue("draft").jsonObject.getValue("stages").jsonObject
                    .getValue("locations").jsonObject.getValue("data").jsonObject.getValue("entries").jsonArray
                assertEquals(6, calls.get())
                assertEquals(if (repairSucceeds) 1 else 0, writes.get())
                assertEquals(if (repairSucceeds) "2" else "1", result.session.getValue("revision").jsonPrimitive.content)
                assertEquals(oldEntries.toList(), entries.take(3))
                assertEquals(if (repairSucceeds) 7 else 3, entries.size)
                if (repairSucceeds) assertEquals(factions.toList(), entries.drop(3))
            }
        }
    }

    @Test
    fun `standalone large entity patch delivers success to summary and persists once`() {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        val background = "完整角色背景。".repeat(500)
        val reply = "角色姓名已更新为林遥，原有背景已保留。"
        val deliveredReceipt = java.util.concurrent.atomic.AtomicReference<JsonObject>()
        val captures = java.util.Collections.synchronizedList(mutableListOf<Pair<JsonObject, String>>())
        val previousSink = com.siming.mobile.data.observability.MobileTrace.sink
        com.siming.mobile.data.observability.MobileTrace.sink = object : com.siming.mobile.data.observability.ContextTraceSink {
            override fun mode() = "full"
            override fun submit(event: JsonObject, content: String?): Boolean {
                if (content != null) captures += event to content
                return true
            }
        }
        try {
        val base = session()
        val source = JsonObject(base.toMutableMap().apply {
            put("draft", JsonObject(base.getValue("draft").jsonObject.toMutableMap().apply {
                put("stages", buildJsonObject {
                    put("characters", buildJsonObject {
                        put("status", "generated")
                        put("data", buildJsonObject {
                            put("characters", JsonArray(listOf(buildJsonObject {
                                put("name", "林七"); put("role_type", "protagonist")
                                put("goal", "找到母亲"); put("background", background)
                            })))
                            put("relationships", JsonArray(emptyList()))
                        })
                    })
                })
            }))
        })
        val openingContract = PcCreationEntityContract(Json.parseToJsonElement(contractJson()).jsonObject.getValue("creation").jsonObject).opening
        val entityId = openingContract.characterIndex(source).first().jsonObject.getValue("id").jsonPrimitive.content
        var savedEntityId = ""
        fun response(content: String = "", name: String? = null, arguments: String = "{}") = chatStreamResponse(
            buildJsonObject {
                put("choices", JsonArray(listOf(buildJsonObject {
                    put("message", buildJsonObject {
                        put("role", "assistant"); put("content", content)
                        if (name != null) put("tool_calls", JsonArray(listOf(buildJsonObject {
                            put("id", name); put("type", "function")
                            put("function", buildJsonObject { put("name", name); put("arguments", arguments) })
                        })))
                    })
                })))
            }.toString(),
        )
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (requests.getAndIncrement()) {
                    0 -> response(name = "set_tool_categories", arguments = """{"enabled_categories":["creation_entities"]}""")
                    1 -> response(name = "get_creation_entity", arguments = """{"entity_id":"$entityId"}""")
                    2 -> response(name = "patch_creation_entity", arguments = """{"entity_id":"$entityId","expected_revision":1,"changes":[{"action":"set","path":"/name","value":"林遥"}]}""")
                    else -> {
                        check(requests.get() == 4)
                        assertTrue(body.getValue("tools").jsonArray.isEmpty())
                        val message = body.getValue("messages").jsonArray.last().jsonObject
                        val receipt = Json.parseToJsonElement(message.string("content")).jsonObject
                        deliveredReceipt.set(receipt)
                        assertEquals("ok", receipt.string("status"))
                        val data = receipt.getValue("data").jsonObject
                        savedEntityId = data.string("id")
                        assertTrue(savedEntityId.isNotBlank())
                        assertEquals("林遥", data.string("entity_key"))
                        assertEquals("characters", data.string("artifact"))
                        assertFalse("data" in data)
                        assertTrue(message.string("content").toByteArray(Charsets.UTF_8).size <= 4_096)
                        response(reply)
                    }
                }
            }
        }) { server ->
            val outcome = runBlocking { agent { persisted.incrementAndGet() }.run(
                source = source, message = "把主角改名为林遥", config = config(server),
            ) }
            assertEquals(reply, outcome.reply)
            assertEquals("model", outcome.replyStatus)
            assertTrue(outcome.replyDiagnostics.isEmpty())
            assertEquals(4, requests.get())
            assertEquals(1, persisted.get())
            assertEquals("2", outcome.session.getValue("revision").jsonPrimitive.content)
            assertEquals(openingContract.characterIndex(outcome.session).first().jsonObject.getValue("id").jsonPrimitive.content, savedEntityId)
            val character = outcome.session.getValue("draft").jsonObject.getValue("stages").jsonObject
                .getValue("characters").jsonObject.getValue("data").jsonObject.getValue("characters").jsonArray.first().jsonObject
            assertEquals("林遥", character.string("name"))
            assertEquals(background, character.string("background"))
            fun captured(layer: String) = captures.single { (event, content) ->
                event.getValue("data").jsonObject.string("layer") == layer &&
                    Json.parseToJsonElement(content).jsonObject.string("tool") == "patch_creation_entity"
            }
            val raw = captured("tool_receipt")
            val projected = captured("model_visible_tool_result")
            assertTrue(raw.second.contains(background))
            assertFalse(projected.second.contains(background))
            assertEquals(deliveredReceipt.get(), Json.parseToJsonElement(projected.second))
            assertEquals(raw.first["span_id"], projected.first["span_id"])
        }
        } finally { com.siming.mobile.data.observability.MobileTrace.sink = previousSink }
    }

    @Test
    fun `standalone generation returns reference diagnostics without calling the generator`() {
        val cases = listOf(
            Triple("context_entity_ids", JsonArray(listOf(JsonPrimitive("world_style:worldbuilding:99"))), "creation_context_entity_unavailable"),
            Triple("context_artifacts", JsonArray(listOf(JsonPrimitive("unknown_artifact"))), "creation_context_artifact_invalid"),
            Triple("entity_type", JsonPrimitive("characters"), "creation_entity_type_invalid"),
        )
        cases.forEach { (field, value, reason) ->
            val requests = AtomicInteger()
            var modelReceipt = ""
            fun tool(name: String, args: JsonObject): MockResponse = chatStreamResponse(buildJsonObject {
                put("choices", JsonArray(listOf(buildJsonObject { put("message", buildJsonObject {
                    put("role", "assistant")
                    put("tool_calls", JsonArray(listOf(buildJsonObject {
                        put("id", "call-$name")
                        put("type", "function")
                        put("function", buildJsonObject { put("name", name); put("arguments", args.toString()) })
                    })))
                }) })))
            }.toString())
            withServer(object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (requests.getAndIncrement()) {
                    0 -> tool("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf(JsonPrimitive("creation_generation"))))
                    })
                    1 -> tool("generate_creation_artifact", buildJsonObject {
                        put("artifact", "characters"); put("expected_revision", 1); put(field, value)
                    })
                    else -> {
                        check(requests.get() == 3)
                        val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                        modelReceipt = body.getValue("messages").jsonArray.map { it.jsonObject }
                            .last { it.string("role") == "tool" }.string("content")
                        chatStreamResponse("""{"choices":[{"message":{"role":"assistant","content":"引用无效，本轮未写入。"}}]}""")
                    }
                }
            }) { server ->
                val initial = session()
                val outcome = runBlocking { agent().run(initial, "生成主角档案", config(server)) }
                assertEquals(3, requests.get())
                assertTrue(modelReceipt.contains(reason), modelReceipt)
                assertTrue(modelReceipt.contains("$.$field"), modelReceipt)
                assertEquals(1, outcome.session.getValue("revision").jsonPrimitive.content.toInt())
                assertEquals(initial.getValue("draft"), outcome.session.getValue("draft"))
                val receipt = outcome.toolResults.map { it.jsonObject }.last()
                assertEquals(reason, receipt.getValue("data").jsonObject.string("reason"))
            }
        }
    }

    @Test
    fun `standalone entity refinement replaces only the selected second entity`() {
        exerciseEntityRefinement(valid = true)
    }

    @Test
    fun `standalone entity refinement cannot use the old baseline as a generated result`() {
        exerciseEntityRefinement(valid = false)
    }

    @Test
    fun `standalone generation passes explicitly selected context entities to the provider`() {
        exerciseEntityRefinement(valid = true, includeContext = true)
    }

    private fun exerciseEntityRefinement(valid: Boolean, includeContext: Boolean = false) {
        val calls = AtomicInteger()
        val first = buildJsonObject {
            put("title", "不应变动的条目")
            put("dimension", "culture")
            put("content", "保持这条原始内容")
        }
        val second = buildJsonObject {
            put("title", "待修订条目")
            put("dimension", "culture")
            put("content", "原始第二条")
        }
        val replacement = JsonObject(second.toMutableMap().apply { put("content", JsonPrimitive("经过核验的新内容")) })
        val world = buildJsonObject {
            put("writing_style", "克制")
            put("world_tone", "现实")
            put("story_structure", "线性")
            put("pacing", "稳健")
            put("style_rules", "保持限知")
            put("forbidden_patterns", "不使用巧合")
            put("worldbuilding", JsonArray(listOf(first, second)))
        }
        val initial = session()
        val source = JsonObject(initial.toMutableMap().apply {
            put("draft", JsonObject(initial["draft"]!!.jsonObject.toMutableMap().apply {
                put("stages", buildJsonObject {
                    put("world_style", buildJsonObject { put("status", "generated"); put("data", world) })
                })
            }))
        })
        fun response(message: JsonObject, streaming: Boolean): MockResponse {
            val body = buildJsonObject {
                put("choices", JsonArray(listOf(buildJsonObject { put("message", message) })))
                put("usage", buildJsonObject { put("prompt_tokens", 100); put("completion_tokens", 50) })
            }.toString()
            return if (streaming) chatStreamResponse(body) else
                MockResponse().setHeader("Content-Type", "application/json").setBody(body)
        }
        fun toolMessage(name: String, arguments: JsonObject) = buildJsonObject {
            put("role", "assistant")
            put("tool_calls", JsonArray(listOf(buildJsonObject {
                put("id", "call-" + name)
                put("type", "function")
                put("function", buildJsonObject { put("name", name); put("arguments", arguments.toString()) })
            })))
        }
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                val index = calls.getAndIncrement()
                val message = when (index) {
                    0 -> toolMessage("set_tool_categories", buildJsonObject {
                        put("enabled_categories", JsonArray(listOf(JsonPrimitive("creation_generation"))))
                    })
                    1 -> toolMessage("refine_creation_artifact", buildJsonObject {
                        put("artifact", "world_style")
                        put("entity_id", "world_style:worldbuilding:1")
                        put("instruction", "修订所选实体")
                        put("expected_revision", 1)
                        if (includeContext) put("context_entity_ids", JsonArray(listOf(JsonPrimitive("world_style:worldbuilding:0"))))
                    })
                    2, 3 -> if (index == 2 || !valid) {
                        if (index == 2) {
                            val prompt = body.getValue("messages").jsonArray.last().jsonObject.string("content")
                            assertTrue(prompt.contains("world_style:worldbuilding:1"))
                            assertEquals(includeContext, prompt.contains("保持这条原始内容"))
                            if (includeContext) assertTrue(prompt.contains("retrieved_entities"))
                        }
                        buildJsonObject {
                            put("role", "assistant")
                            put("content", buildJsonObject {
                                put("data", if (valid) buildJsonObject {
                                    put("worldbuilding", JsonArray(listOf(replacement)))
                                } else buildJsonObject {
                                    put("world_style", buildJsonObject { put("worldbuilding", JsonArray(listOf(replacement))) })
                                })
                            }.toString())
                        }
                    } else buildJsonObject { put("role", "assistant"); put("content", "已完成本轮") }
                    else -> buildJsonObject { put("role", "assistant"); put("content", "模型格式无效，原资料未修改") }
                }
                return response(message, body["stream"]?.jsonPrimitive?.content == "true")
            }
        }) { server ->
            val outcome = runBlocking { agent().run(source, "修订所选实体", config(server)) }
            val rows = outcome.session["draft"]!!.jsonObject["stages"]!!.jsonObject["world_style"]!!
                .jsonObject["data"]!!.jsonObject["worldbuilding"]!!.jsonArray
            assertEquals(first, rows[0])
            assertEquals(if (valid) replacement else second, rows[1])
            assertEquals(if (valid) 4 else 5, calls.get())
            val receipt = outcome.toolResults.map { it.jsonObject }.first { it.string("tool") == "refine_creation_artifact" }
            assertEquals(if (valid) "ok" else "error", receipt.string("status"))
            assertEquals(if (valid) 2 else 1, outcome.session["revision"]!!.jsonPrimitive.content.toInt())
        }
    }

    @Test
    fun `standalone agent selects categories before reading and preserves complete rounds`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertTrue(body.getValue("stream").jsonPrimitive.content.toBoolean())
                return when (requests.getAndIncrement()) {
                    0 -> {
                        assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}],"usage":{"prompt_tokens":88}}""",
                        )
                    }
                    1 -> {
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-read","type":"function","function":{"name":"get_creation_snapshot","arguments":"{}"}}]}}],"usage":{"prompt_tokens":100}}""",
                        )
                    }
                    else -> {
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        val messages = body.getValue("messages").jsonArray.map { it.jsonObject }
                        assertTrue(messages.any { (it["tool_calls"] as? JsonArray)?.isNotEmpty() == true })
                        val toolMessage = messages.first {
                            it.string("role") == "tool" && it.string("tool_call_id") == "call-read"
                        }
                        val toolResult = Json.parseToJsonElement(toolMessage.string("content")).jsonObject
                        val visibleDraft = toolResult.getValue("data").jsonObject.getValue("draft").jsonObject
                        assertFalse("agent_turns" in visibleDraft)
                        assertFalse("agent_conversation_id" in visibleDraft)
                        assertFalse("execution_route" in visibleDraft)
                        assertFalse("execution_host" in visibleDraft)
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"已读取当前立项资料，没有修改数据。"}}],"usage":{"prompt_tokens":144}}""",
                        )
                    }
                }
            }
        }) { server ->
            val result = runBlocking { agent().run(
                source = session(),
                message = "先看看当前资料",
                config = config(server),
            ) }

            assertEquals(3, requests.get())
            assertEquals("completed", result.status)
            assertTrue(result.replayable)
            assertEquals(PcCreationAgentContract(contractJson()).readOnlyNotice + "\n\n已读取当前立项资料，没有修改数据。", result.reply)
            assertEquals(
                listOf("user", "assistant", "tool", "assistant", "tool", "assistant"),
                result.modelMessages.map { (it as JsonObject).string("role") },
            )
            assertEquals(88, result.promptMetrics[0].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
            assertEquals(100, result.promptMetrics[1].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
            assertEquals(144, result.promptMetrics[2].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
        }
    }

    @Test
    fun `standalone agent can select no business tools and reply without writing`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertEquals(
                    listOf("set_tool_categories"),
                    body.getValue("tools").jsonArray.map {
                        it.jsonObject.getValue("function").jsonObject.string("name")
                    },
                )
                return when (requests.getAndIncrement()) {
                    0 -> {
                        assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-empty-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[]}"}}]}}]}""",
                        )
                    }
                    else -> {
                        assertEquals(2, requests.get())
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        val receipt = body.getValue("messages").jsonArray.map { it.jsonObject }.first {
                            it.string("role") == "tool" && it.string("tool_call_id") == "call-empty-categories"
                        }
                        assertEquals("ok", Json.parseToJsonElement(receipt.string("content")).jsonObject.string("status"))
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"你好！你想讨论怎样的故事？"}}]}""",
                        )
                    }
                }
            }
        }) { server ->
            val source = session()
            val result = runBlocking { agent().run(source, "你好？", config(server)) }
            assertEquals(2, requests.get())
            assertEquals("completed", result.status)
            assertEquals("你好！你想讨论怎样的故事？", result.reply)
            assertEquals(source["revision"], result.session["revision"])
            assertEquals(listOf("set_tool_categories"), result.toolResults.map { it.jsonObject.string("tool") })
            assertEquals("ok", result.toolResults.single().jsonObject.string("status"))
        }
    }

    @Test
    fun `standalone agent continues past the old six step limit`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (val step = requests.getAndIncrement()) {
                    0 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                    in 1..6 -> {
                        assertTrue(body.getValue("tools").jsonArray.isNotEmpty())
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-read-$step","type":"function","function":{"name":"get_creation_snapshot","arguments":"{}"}}]}}]}""",
                        )
                    }
                    else -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":"已在超过旧上限后完成检查。"}}]}""",
                    )
                }
            }
        }) { server ->
            val result = runBlocking { agent().run(
                source = session(),
                message = "连续检查多轮",
                config = config(server),
            ) }

            assertEquals(8, requests.get())
            assertEquals("completed", result.status)
            assertEquals(PcCreationAgentContract(contractJson()).readOnlyNotice + "\n\n已在超过旧上限后完成检查。", result.reply)
        }
    }

    @Test
    fun `deepseek standalone conversation preserves thinking and omits unsupported tool choice`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertFalse("tool_choice" in body)
                assertFalse("thinking" in body)
                return if (requests.getAndIncrement() == 0) {
                    chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                } else {
                    chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":"思考模式与工具调用可以共同工作。"}}]}""",
                    )
                }
            }
        }) { server ->
            val result = runBlocking {
                agent().run(
                    source = session(),
                    message = "先检查当前资料",
                    config = config(server).copy(
                        displayName = "DeepSeek",
                        model = "deepseek-v4-pro",
                    ),
                )
            }

            assertEquals(2, requests.get())
            assertEquals("思考模式与工具调用可以共同工作。", result.reply)
        }
    }

    @Test
    fun `standalone agent rejects text before selecting tool categories`() {
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                assertTrue(body.getValue("stream").jsonPrimitive.content.toBoolean())
                return chatStreamResponse(
                    """{"choices":[{"message":{"role":"assistant","content":"我已经读取并保存了设定。"}}]}""",
                )
            }
        }) { server ->
            val error = assertFailsWith<IllegalStateException> {
                runBlocking { agent().run(
                    source = session(),
                    message = "加入一个新设定",
                    config = config(server),
                ) }
            }
            assertTrue(error.message.orEmpty().contains("set_tool_categories"))
        }
    }

    @Test
    fun `standalone agent persists only one successful write per user message`() {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertTrue(body.getValue("stream").jsonPrimitive.content.toBoolean())
                return when (requests.getAndIncrement()) {
                    0 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                    1 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-write-one","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"form\":{\"genre\":\"玄幻\"}}}"}},{"id":"call-write-two","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"form\":{\"target_chapters\":1000}}}"}}]}}]}""",
                    )
                    else -> {
                        assertTrue(body.getValue("tools").jsonArray.isEmpty())
                        assertFalse("tool_choice" in body)
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"本轮只记录了题材。下一步想补充什么？"}}]}""",
                        )
                    }
                }
            }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-agent-test").toFile())
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )
            val result = runBlocking { AgentHarness(standalone, store).run(
                source = session(),
                message = "继续",
                config = config(server),
            ) }

            assertEquals(3, requests.get())
            assertEquals(1, persisted.get())
            assertEquals(2, result.session.getValue("revision").jsonPrimitive.content.toInt())
            assertEquals("玄幻", result.session.getValue("genre").jsonPrimitive.content)
            val draft = result.session.getValue("draft").jsonObject
            assertEquals("玄幻", draft.getValue("form").jsonObject.getValue("genre").jsonPrimitive.content)
            assertEquals("玄幻", draft.getValue("stages").jsonObject.getValue("constraints").jsonObject.getValue("data").jsonObject.getValue("genre").jsonPrimitive.content)
            val businessResults = result.toolResults.map { it.jsonObject }
                .filter { it.string("tool") == "patch_creation_session" }
            assertEquals(listOf("ok", "denied"), businessResults.map { it.string("status") })
            assertEquals("本轮只记录了题材。下一步想补充什么？", result.reply)
        }
    }

    @Test
    fun `small standalone creation reads use exact results when declarations exceed capacity`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (requests.getAndIncrement()) {
                    0 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                    1 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"session","type":"function","function":{"name":"get_creation_session","arguments":"{}"}},{"id":"snapshot","type":"function","function":{"name":"get_creation_snapshot","arguments":"{}"}}]}}]}""",
                    )
                    else -> {
                        val toolMessages = body.getValue("messages").jsonArray.map { it.jsonObject }
                            .filter { it.string("role") == "tool" }
                        assertEquals(2, toolMessages.count {
                            it.string("tool_call_id") in setOf("session", "snapshot")
                        })
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"已读取立项资料，尚未修改。"}}]}""",
                        )
                    }
                }
            }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-read-budget").toFile())
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { error("纯读取不应持久化修改") },
                finalizeSession = { source -> source to "project-1" },
            )

            val result = runBlocking {
                AgentHarness(standalone, store).run(
                    source = session(),
                    message = "查看当前立项资料",
                    config = config(server).copy(contextWindowTokens = 100_000),
                )
            }

            assertEquals(3, requests.get())
            assertEquals(PcCreationAgentContract(contractJson()).readOnlyNotice + "\n\n已读取立项资料，尚未修改。", result.reply)
            assertEquals(2, result.toolResults.count {
                it.jsonObject.string("status") == "ok" &&
                    it.jsonObject.string("tool") in setOf("get_creation_session", "get_creation_snapshot")
            })
        }
    }

    @Test
    fun `oversized declared creation results reject the whole batch before any handler`() {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (requests.getAndIncrement()) {
                    0 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                    1 -> {
                        val calls = (1..60).joinToString(",") { index ->
                            """{"id":"call-write-$index","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"form\":{\"genre\":\"类型$index\"}}}"}}"""
                        }
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[$calls]}}]}""",
                        )
                    }
                    else -> {
                        val messages = body.getValue("messages").jsonArray.map { it.jsonObject }
                        assertEquals(
                            60,
                            messages.count { it.string("role") == "tool" && it.string("tool_call_id").startsWith("call-write-") },
                        )
                        chatStreamResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"批次过大，未修改立项资料。"}}]}""",
                        )
                    }
                }
            }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-agent-test").toFile())
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )

            val result = runBlocking {
                AgentHarness(standalone, store).run(
                    source = session(),
                    message = "一次改很多字段",
                    config = config(server),
                )
            }

            assertEquals(3, requests.get())
            assertEquals(0, persisted.get())
            assertEquals(1, result.session.getValue("revision").jsonPrimitive.content.toInt())
            assertEquals(60, result.toolResults.count {
                it.jsonObject.string("status") == "error" &&
                    it.jsonObject["data"]?.jsonObject?.string("reason") == "tool_result_batch_over_capacity"
            })
        }
    }

    @Test
    fun `oversized creation rejection remains delivered without a ledger after restart`() {
        val oversizedContent = "x".repeat(250_000)
        val persisted = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = chatStreamResponse(
                """{"choices":[{"message":{"role":"assistant","content":"$oversizedContent","tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
            )
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val directory = Files.createTempDirectory("creation-oversized-restart-test").toFile()
            val store = MobileAssistantConversationStore(directory)
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )

            val error = assertFailsWith<MobileConversationContextException> {
                runBlocking {
                    AgentHarness(standalone, store).run(
                        source = session(),
                        message = "选择立项能力",
                        config = config(server),
                    )
                }
            }
            assertEquals(MobileConversationContextErrorCode.TOOL_TRANSACTION_OVER_CAPACITY, error.code)
            assertEquals(0, persisted.get())

            val restarted = MobileAssistantConversationStore(directory)
            val snapshot = runBlocking {
                checkNotNull(restarted.snapshot("creation-session-1", "creation-session-1"))
            }
            val runtime = snapshot.toolRuntimeStates.single()
            assertEquals(MobileToolTransactionState.DELIVERED, runtime.transactions.single().state)
            assertEquals(runtime.transactions, runtime.activeTransactions)
            assertTrue(runtime.executionLedger.isEmpty())
            assertTrue(runtime.transactions.single().results.all { result ->
                result.resultRef == null && result.persistedStepId == null
            })
        }
    }

    @Test
    fun `duplicate creation call ids reject the whole batch before any handler`() {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (requests.getAndIncrement() == 0) {
                    chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                } else {
                    chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"duplicate","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"form\":{\"genre\":\"玄幻\"}}}"}},{"id":"duplicate","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"form\":{\"genre\":\"科幻\"}}}"}}]}}]}""",
                    )
                }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-agent-test").toFile())
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )

            val error = assertFailsWith<MobileConversationContextException> {
                runBlocking {
                    AgentHarness(standalone, store).run(
                        source = session(),
                        message = "重复调用不得执行",
                        config = config(server),
                    )
                }
            }

            assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, error.code)
            assertEquals(0, persisted.get())
            assertEquals(2, requests.get())
        }
    }

    @Test
    fun `standalone summary rejects DSML before displaying any provider delta`() {
        listOf(
            DSML,
            "已经保存。\n$DSML",
            DSML.replace("｜｜", "｜"),
            DSML.replace("｜", "|"),
            DSML.replace("<", "&lt;"),
            "<｜｜DSML  ",
        ).forEach { invalid -> exerciseSummary(invalid, persistentFailure = false) }
    }

    @Test
    fun `standalone summary stops after one correction and retains committed data`() {
        exerciseSummary(DSML, persistentFailure = true)
    }

    @Test
    fun `standalone summary never executes an unexpected native call`() {
        exerciseSummary("", persistentFailure = false, nativeCall = true)
    }

    @Test
    fun `standalone empty summary is corrected without replaying a write`() {
        exerciseSummary("", persistentFailure = false)
    }

    @Test
    fun `standalone summary request failure preserves the successful write`() {
        exerciseSummary("", persistentFailure = false, transportFailure = true)
    }

    @Test
    fun `standalone failed write limit reports verified no-write receipt`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                return when (requests.getAndIncrement()) {
                    0 -> chatStreamResponse(
                        """{"choices":[{"message":{"role":"assistant","tool_calls":[{"id":"categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_session\"]}"}}]}}]}""",
                    )
                    1 -> {
                        val calls = (1..4).joinToString(",") { index ->
                            """{"id":"write-$index","type":"function","function":{"name":"patch_creation_session","arguments":"{\"expected_revision\":999,\"changes\":{\"form\":{\"genre\":\"玄幻\"}}}"}}"""
                        }
                        chatStreamResponse("""{"choices":[{"message":{"role":"assistant","tool_calls":[$calls]}}]}""")
                    }
                    else -> error("失败写入后不得再请求模型总结")
                }
            }
        }) { server ->
            val outcome = runBlocking { agent().run(session(), "把题材设为玄幻", config(server)) }
            assertEquals(2, requests.get())
            assertEquals(1, outcome.session.getValue("revision").jsonPrimitive.content.toInt())
            assertTrue(outcome.reply.startsWith("本轮没有保存任何修改"))
            assertTrue(outcome.reply.contains("本轮写入失败已达上限"))
            assertEquals(listOf("error", "error", "error", "denied"), outcome.toolResults.map { it.jsonObject }
                .filter { it.string("tool") == "patch_creation_session" }.map { it.string("status") })
            assertEquals("receipt_only", outcome.replyStatus)
        }
    }

    private fun exerciseSummary(
        invalid: String,
        persistentFailure: Boolean,
        nativeCall: Boolean = false,
        transportFailure: Boolean = false,
    ) {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        val progress = mutableListOf<CreationAgentProgressEvent>()
        val expectedReply = "题材已更新为玄幻。主角有什么目标？"
        fun response(content: String, call: String? = null): MockResponse = chatStreamResponse(
            buildJsonObject {
                put("choices", JsonArray(listOf(buildJsonObject {
                    put("message", buildJsonObject {
                        put("role", "assistant")
                        put("content", content)
                        if (call != null) put("tool_calls", JsonArray(listOf(Json.parseToJsonElement(call))))
                    })
                })))
            }.toString(),
        )
        fun call(id: String, name: String, arguments: String): String = buildJsonObject {
            put("id", id)
            put("type", "function")
            put("function", buildJsonObject { put("name", name); put("arguments", arguments) })
        }.toString()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                assertEquals("/chat/completions", request.path)
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertEquals("deepseek-flash", body.string("model"))
                return when (val step = requests.getAndIncrement()) {
                    0 -> response("", call("categories", "set_tool_categories", """{"enabled_categories":["creation_session"]}"""))
                    1 -> response("", call("read", "get_creation_snapshot", "{}"))
                    2 -> response("", call("write", "patch_creation_session", """{"changes":{"form":{"genre":"玄幻"}}}"""))
                    else -> {
                        check(step in 3..4) { "Summary correction must be bounded" }
                        assertTrue(body.getValue("tools").jsonArray.isEmpty())
                        assertFalse("tool_choice" in body)
                        val messages = body.getValue("messages").jsonArray.map { it.jsonObject }
                        val system = messages.first().string("content")
                        assertTrue(system.contains("[SERVER_RUNTIME_INSTRUCTION]"))
                        assertTrue(system.contains("工具已关闭"))
                        assertEquals("把题材设为玄幻", messages.last { it.string("role") == "user" }.string("content"))
                        assertTrue(messages.any { it.string("role") == "tool" && it.string("tool_call_id") == "write" })
                        assertFalse(progress.any { it.type == "reply_delta" })
                        if (step == 4) assertTrue(system.contains("上一条总结未通过输出协议校验"))
                        if (transportFailure) {
                            MockResponse().setResponseCode(401).setBody("""{"error":{"message":"summary request unavailable"}}""")
                        } else if (step == 3 && nativeCall) {
                            response("", call("unoffered-write", "patch_creation_session", """{"changes":{"form":{"genre":"科幻"}}}"""))
                        } else response(if (step == 3 || persistentFailure) invalid else expectedReply)
                    }
                }
            }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-summary-test").toFile())
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )
            val outcome = runBlocking { AgentHarness(standalone, store).run(
                source = session(), message = "把题材设为玄幻",
                config = config(server).copy(model = "deepseek-flash"),
                onProgress = { progress += it },
            ) }
            assertEquals(if (transportFailure) 4 else 5, requests.get())
            assertEquals(1, persisted.get())
            assertEquals(2, outcome.session.getValue("revision").jsonPrimitive.content.toInt())
            assertEquals(1, outcome.toolResults.count { it.jsonObject.string("tool") == "patch_creation_session" })
            if (!transportFailure) {
                assertEquals(listOf("summary", "summary"), outcome.promptMetrics.takeLast(2).map { it.jsonObject.string("phase") })
            }
            assertEquals(if (persistentFailure || transportFailure) "receipt_only" else "model", outcome.replyStatus)
            assertEquals(if (persistentFailure) 2 else 1, outcome.replyDiagnostics.size)
            if (persistentFailure || transportFailure) {
                assertTrue(outcome.reply.contains("已保存"))
                assertTrue(outcome.reply.contains("模型未能生成有效总结"))
            } else assertEquals(expectedReply, outcome.reply)
            assertFalse(outcome.reply.contains("DSML"))
            assertFalse(outcome.modelMessages.toString().contains("DSML"))
            assertEquals(outcome.reply, progress.filter { it.type == "reply_delta" }.joinToString("") { it.data.string("delta") })
            val record = CreationAgentTurnRecords.complete(
                pending = CreationAgentTurnRecords.pending("把题材设为玄幻"),
                reply = outcome.reply,
                modelMessages = outcome.modelMessages,
                toolResults = outcome.toolResults,
                replayable = outcome.replayable,
                executionRoute = "mobile",
                replyStatus = outcome.replyStatus,
                replyDiagnostics = outcome.replyDiagnostics,
            )
            assertEquals(outcome.replyStatus, record.string("reply_status"))
            assertEquals(outcome.replyDiagnostics, record["reply_diagnostics"])
        }
    }

    private data class AgentHarness(
        val agent: MobileCreationConversationAgent,
        val store: MobileAssistantConversationStore,
    )

    private fun agent(persistSession: suspend (JsonObject) -> Unit = {}): AgentHarness {
        val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
        val contract = contractJson()
        val store = MobileAssistantConversationStore(Files.createTempDirectory("creation-agent-test").toFile())
        return AgentHarness(
            agent = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                conversationStore = store,
                persistSession = persistSession,
                finalizeSession = { source -> source to "project-1" },
            ),
            store = store,
        )
    }

    private suspend fun AgentHarness.run(
        source: JsonObject,
        message: String,
        config: DirectApiConfig,
        onProgress: suspend (CreationAgentProgressEvent) -> Unit = {},
    ): MobileCreationConversationResult {
        val storageId = "creation-session-1"
        val conversationId = storageId
        store.ensureConversationArchive(
            projectId = storageId,
            conversationId = conversationId,
            conversationKind = "creation",
            creationSessionId = source.string("id"),
            title = "测试立项",
            archivedTurns = CreationAgentTurnRecords.archivedTurns(source),
        )
        val turnContext = store.beginTurn(
            projectId = storageId,
            conversationId = conversationId,
            prompt = message,
            conversationKind = "creation",
            creationSessionId = source.string("id"),
        )
        val conversation = checkNotNull(store.snapshot(storageId, conversationId))
        return agent.run(
            source = source,
            message = message,
            storageId = storageId,
            conversation = conversation,
            turnContext = turnContext,
            config = config,
            onProgress = onProgress,
        )
    }

    private fun config(server: MockWebServer) = DirectApiConfig(
        displayName = "test",
        baseUrl = server.url("/").toString(),
        apiKey = "secret",
        model = "test-model",
        protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
        contextWindowTokens = 200_000,
        maxOutputTokens = 6_000,
        safetyMarginTokens = 4_096,
    )

    private fun session() = buildJsonObject {
        put("id", "session-1")
        put("revision", 1)
        put("display_title", "测试立项")
        put("draft", buildJsonObject {
            put("form", buildJsonObject {})
            put("stages", buildJsonObject {})
            put("agent_turns", JsonArray(listOf(buildJsonObject {
                put("schema", CreationAgentTurnRecords.SCHEMA)
                put("user_content", "不应递归进入工具快照")
            })))
            put("agent_conversation_id", "conversation-1")
            put("execution_route", "mobile")
            put("execution_host", "device")
        })
    }

    private fun contractJson(): String {
        val candidates = listOf(
            File("src/main/assets/pc_workspace_prompt_contract.json"),
            File("app/src/main/assets/pc_workspace_prompt_contract.json"),
        )
        return candidates.first(File::isFile).readText(Charsets.UTF_8)
    }

    private companion object {
        const val DSML = "<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name=\"get_creation_entity\">" +
            "<｜｜DSML｜｜ parameter name=\"entity_id\" string=\"true\">entity-1" +
            "</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>"
    }

    private fun chatStreamResponse(body: String): MockResponse {
        val root = Json.parseToJsonElement(body).jsonObject
        val message = root.getValue("choices").jsonArray.first().jsonObject.getValue("message").jsonObject
        val finishReason = if ((message["tool_calls"] as? JsonArray).orEmpty().isNotEmpty()) {
            "tool_calls"
        } else {
            "stop"
        }
        val event = buildJsonObject {
            put("choices", JsonArray(listOf(buildJsonObject {
                put("delta", message)
                put("finish_reason", finishReason)
            })))
            root["usage"]?.let { put("usage", it) }
        }
        return MockResponse()
            .setResponseCode(200)
            .setHeader("Content-Type", "text/event-stream")
            .setBody("data: $event\n\ndata: [DONE]\n\n")
    }

    private fun withServer(dispatcher: Dispatcher, block: (MockWebServer) -> Unit) {
        MockWebServer().use { server ->
            server.dispatcher = dispatcher
            server.start()
            block(server)
        }
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
