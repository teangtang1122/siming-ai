package com.siming.mobile.data.agent

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileConversationContextTest {
    @Test
    fun `task model cannot inherit the default model capacity profile`() {
        val config = DirectApiConfig(
            displayName = "test",
            baseUrl = "https://example.com/v1",
            apiKey = "secret",
            model = "general-model",
            taskModels = mapOf(DirectApiConfig.TASK_WRITING to "writer-model"),
            contextWindowTokens = 128_000,
            maxOutputTokens = 6_000,
            safetyMarginTokens = 4_096,
        )

        val error = assertFailsWith<MobileConversationContextException> {
            mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_WRITING)
        }
        assertEquals(MobileConversationContextErrorCode.CAPACITY_UNKNOWN, error.code)
        assertTrue(error.message.orEmpty().contains("writer-model"))

        val defaultTask = mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_ASSISTANT)
        assertEquals("general-model", defaultTask.model)
        assertEquals(128_000, defaultTask.contextWindowTokens)
    }

    @Test
    fun `legacy official config and exact task model recover documented capacity`() {
        val config = DirectApiConfig(
            displayName = "OpenAI",
            baseUrl = "https://api.openai.com/v1",
            apiKey = "secret",
            model = "gpt-4o",
            taskModels = mapOf(DirectApiConfig.TASK_WRITING to "gpt-4.1-mini"),
            contextWindowTokens = null,
            maxOutputTokens = 6_000,
            safetyMarginTokens = 4_096,
        )

        val assistant = mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_ASSISTANT)
        assertEquals("gpt-4o", assistant.model)
        assertEquals(128_000, assistant.contextWindowTokens)
        assertEquals(6_000, assistant.maxOutputTokens)

        val writing = mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_WRITING)
        assertEquals("gpt-4.1-mini", writing.model)
        assertEquals(1_047_576, writing.contextWindowTokens)
        assertEquals(6_000, writing.maxOutputTokens)
    }

    @Test
    fun `context state payload tolerates a missing cached budget`() {
        val conversation = MobileConversationSnapshot(
            conversationId = "conversation-empty-budget",
            projectId = "project-1",
            conversationKind = "workspace",
            title = "新对话",
            transcriptRevision = 0L,
            messages = emptyList(),
            contextState = MobileConversationContextState(lastBudget = null),
            checkpoints = emptyList(),
            replicaState = MobileTranscriptReplicaState(),
            toolRuntimeStates = emptyList(),
        )

        val payload = mobileConversationContextStatePayload(
            status = "ready",
            detail = "尚无缓存预算",
            conversation = conversation,
        )

        assertEquals("ready", (payload["status"] as JsonPrimitive).content)
        assertEquals(0, payload["original_history_tokens"].toString().toInt())
        assertEquals("within_capacity", (payload["trigger"] as JsonPrimitive).content)
        assertFalse("active_history_tokens" in payload)
    }

    @Test
    fun `recent history is a dynamic complete-turn suffix rather than a fixed count`() {
        val turns = (1..5).map(::completedTurn)
        val plan = MobileRecentTurnPlanner.plan(
            turns = turns,
            coveredSequenceRanges = emptyList(),
            tokenCountByTurnId = turns.associate { it.turnId to 10 },
            budget = MobileRecentTurnBudget(
                requestInputLimitTokens = 130,
                systemAndToolsTokens = 90,
                providerWrapperTokens = 0,
                checkpointTokens = 0,
                currentUserTokens = 10,
                currentTurnLedgerTokens = 0,
                pendingToolTransactionTokens = 0,
            ),
        )

        assertEquals(listOf("turn-3", "turn-4", "turn-5"), plan.recentExactTurns.map { it.turnId })
        assertEquals(listOf("turn-1", "turn-2"), plan.checkpointTurns.map { it.turnId })
        assertEquals(1, plan.checkpointRanges.size)
        assertEquals(0, plan.remainingInputTokens)
        assertTrue(plan.requiresCheckpoint)
    }

    @Test
    fun `checkpoint eligibility requires adjacent nonblank user assistant messages`() {
        val gap = MobileConversationTurn(
            turnId = "turn-gap",
            status = "completed",
            messages = listOf(
                message(1, "turn-gap", "user", "问题", "completed"),
                message(3, "turn-gap", "assistant", "回答", "completed"),
            ),
        )
        val blank = MobileConversationTurn(
            turnId = "turn-blank",
            status = "completed",
            messages = listOf(
                message(4, "turn-blank", "user", "问题", "completed"),
                message(5, "turn-blank", "assistant", "", "completed"),
            ),
        )

        assertFalse(gap.isCheckpointEligible)
        assertFalse(blank.isCheckpointEligible)
        assertTrue(completedTurn(1).isCheckpointEligible)
    }

    @Test
    fun `planner never skips a newer large turn to retain an older small one`() {
        val turns = (1..4).map(::completedTurn)
        val plan = MobileRecentTurnPlanner.plan(
            turns = turns,
            coveredSequenceRanges = emptyList(),
            tokenCountByTurnId = mapOf(
                "turn-1" to 1,
                "turn-2" to 1,
                "turn-3" to 30,
                "turn-4" to 10,
            ),
            budget = MobileRecentTurnBudget(
                requestInputLimitTokens = 125,
                systemAndToolsTokens = 90,
                providerWrapperTokens = 0,
                checkpointTokens = 0,
                currentUserTokens = 10,
                currentTurnLedgerTokens = 0,
                pendingToolTransactionTokens = 0,
            ),
        )

        assertEquals(listOf("turn-4"), plan.recentExactTurns.map { it.turnId })
        assertEquals(listOf("turn-1", "turn-2", "turn-3"), plan.checkpointTurns.map { it.turnId })
    }

    @Test
    fun `segmented checkpoint planning preserves exceptional turns in exact transcript order`() {
        val turns = listOf(
            closedTurn(1, "completed"),
            closedTurn(2, "cancelled"),
            closedTurn(3, "completed"),
            closedTurn(4, "error"),
            closedTurn(5, "completed"),
        )
        val firstPlan = MobileRecentTurnPlanner.plan(
            turns = turns,
            coveredSequenceRanges = emptyList(),
            tokenCountByTurnId = turns.associate { it.turnId to 10 },
            budget = budget(limit = 130),
        )

        assertEquals(listOf("turn-2", "turn-4", "turn-5"), firstPlan.recentExactTurns.map { it.turnId })
        assertEquals(listOf("turn-1", "turn-3"), firstPlan.checkpointTurns.map { it.turnId })
        assertEquals(
            listOf(listOf("turn-1"), listOf("turn-3")),
            firstPlan.checkpointRanges.map { range -> range.map { it.turnId } },
        )

        val covered = firstPlan.checkpointRanges.map { range ->
            val messages = range.flatMap(MobileConversationTurn::messages)
            MobileConversationSourceRange(
                firstSequence = messages.first().sequenceNo,
                lastSequence = messages.last().sequenceNo,
                messageCount = messages.size,
                sourceHash = mobileConversationSourceHash(messages),
            )
        }
        val afterSegments = MobileRecentTurnPlanner.plan(
            turns = turns,
            coveredSequenceRanges = covered,
            tokenCountByTurnId = turns.associate { it.turnId to 10 },
            budget = budget(limit = 130),
        )

        assertEquals(listOf("turn-2", "turn-4", "turn-5"), afterSegments.recentExactTurns.map { it.turnId })
        assertTrue(afterSegments.checkpointTurns.isEmpty())
    }

    @Test
    fun `unknown token cost and oversized required state fail without truncation`() {
        val complete = completedTurn(1)
        val unknown = assertFailsWith<MobileConversationContextException> {
            MobileRecentTurnPlanner.plan(
                turns = listOf(complete),
                coveredSequenceRanges = emptyList(),
                tokenCountByTurnId = emptyMap(),
                budget = budget(limit = 200),
            )
        }
        assertEquals(MobileConversationContextErrorCode.CAPACITY_UNKNOWN, unknown.code)

        val failed = MobileConversationTurn(
            turnId = "failed",
            status = "error",
            messages = listOf(
                message(1L, "failed", "user", "执行任务", "error"),
                message(2L, "failed", "assistant", "执行失败", "error"),
            ),
        )
        val over = assertFailsWith<MobileConversationContextException> {
            MobileRecentTurnPlanner.plan(
                turns = listOf(failed),
                coveredSequenceRanges = emptyList(),
                tokenCountByTurnId = mapOf("failed" to 101),
                budget = budget(limit = 200),
            )
        }
        assertEquals(MobileConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY, over.code)
    }

    @Test
    fun `source hash requires a contiguous exact sequence and canonical json is stable`() {
        val turn = completedTurn(1)
        val hash = mobileConversationSourceHash(turn.messages)
        assertEquals("6ba732213ce62d143efc4cb3fef8e54b321ddad911092e5e2b7b53fc3a6f60db", hash)
        val sourceFixture = interopFixture()["checkpoint_source"] as JsonObject
        assertEquals((sourceFixture["source_hash"] as JsonPrimitive).content, hash)
        assertEquals(
            sourceFixture["messages"],
            JsonArray(turn.messages.map(MobileTranscriptMessage::toCheckpointSourceJson)),
        )
        assertEquals(hash, mobileConversationSourceHash(turn.messages.reversed().sortedBy { it.sequenceNo }))
        assertFailsWith<IllegalArgumentException> {
            mobileConversationSourceHash(
                listOf(
                    turn.messages.first(),
                    turn.messages.last().copy(sequenceNo = 3L),
                ),
            )
        }
        val left = buildJsonObject {
            put("中文", "原文")
            put("a", JsonNull)
        }
        val right = JsonObject(linkedMapOf("a" to JsonNull, "中文" to JsonPrimitive("原文")))
        assertEquals(mobileCanonicalJson(left), mobileCanonicalJson(right))
        assertEquals(mobileCanonicalSha256(left), mobileCanonicalSha256(right))
        assertTrue(mobileCanonicalJson(left).contains("原文"))
    }

    @Test
    fun `gateway transcript message hash matches the shared canonical fixture`() {
        val expected = resourceFixture("assistant-transcript-import-v1-interop.json")["message"] as JsonObject
        val message = MobileTranscriptImportMessage(
            id = "message-1",
            sequenceNo = 1L,
            role = "user",
            content = "问题 1",
            status = "completed",
        )

        assertEquals(expected, message.toJson())
        assertEquals(
            "d3983296d8a9dc591aa2a7f57ca11544977c6d5d4120dabc1665886123194d49",
            message.messageHash,
        )
    }

    @Test
    fun `gateway transcript import key matches normalized server identity fixture`() {
        val fixture = resourceFixture("assistant-transcript-import-v1-interop.json")
            .getValue("import_key") as JsonObject
        val payload = fixture.getValue("payload") as JsonObject
        val sourceTitle = (fixture.getValue("source_title") as JsonPrimitive).content
        val messages = listOf(
            message(1L, "turn-1", "user", "问题 1", "completed"),
            message(2L, "turn-1", "assistant", "回答 1", "completed"),
        )
        val request = requireNotNull(
            buildMobileTranscriptImportRequest(
                projectId = "project-1",
                clientConversationId = "conversation-1",
                serverConversationId = null,
                title = sourceTitle,
                closedMessages = messages,
                confirmedSourceRevision = 0L,
            ),
        )

        assertEquals("章节 一", request.title)
        assertEquals(
            (fixture.getValue("sha256") as JsonPrimitive).content,
            mobileCanonicalSha256(payload),
        )
        assertEquals(
            (fixture.getValue("idempotency_key") as JsonPrimitive).content,
            request.idempotencyKey,
        )

        val normalizedEquivalent = requireNotNull(
            buildMobileTranscriptImportRequest(
                projectId = "project-1",
                clientConversationId = "conversation-1",
                serverConversationId = null,
                title = "章节\r\n一",
                closedMessages = messages,
                confirmedSourceRevision = 0L,
            ),
        )
        val renamed = requireNotNull(
            buildMobileTranscriptImportRequest(
                projectId = "project-1",
                clientConversationId = "conversation-1",
                serverConversationId = null,
                title = "章节 二",
                closedMessages = messages,
                confirmedSourceRevision = 0L,
            ),
        )
        val foreignProject = requireNotNull(
            buildMobileTranscriptImportRequest(
                projectId = "project-2",
                clientConversationId = "conversation-1",
                serverConversationId = null,
                title = sourceTitle,
                closedMessages = messages,
                confirmedSourceRevision = 0L,
            ),
        )

        assertEquals(request.idempotencyKey, normalizedEquivalent.idempotencyKey)
        assertNotEquals(request.idempotencyKey, renamed.idempotencyKey)
        assertNotEquals(request.idempotencyKey, foreignProject.idempotencyKey)
    }

    @Test
    fun `author quote offsets use unicode code points like the python contract`() {
        val source = MobileTranscriptMessage(
            id = "author-1",
            sequenceNo = 1L,
            turnId = "turn-1",
            role = "user",
            content = "A😀中文",
            status = "completed",
            createdAt = "2026-01-01T00:00:00Z",
        )
        val (_, quotes) = validateAndMaterializeCheckpointDraft(
            MobileCheckpointSemanticDraft(
                MobileConversationCheckpoint.emptySemanticNavigation(),
                listOf(MobileCheckpointQuoteSelection("author-1", 1, 3, "active_constraint")),
            ),
            listOf(source),
        )

        assertEquals("😀中", quotes.single().exactQuote)
        assertEquals(mobileConversationSha256("😀中"), quotes.single().quoteSha256)
    }

    @Test
    fun `tool protocol accepts a native closed batch`() {
        MobileToolProtocolValidator.validate(
            messages = listOf(
                messageJson("system", "契约"),
                messageJson("user", "检查作品"),
                assistantToolCall("call-1", "get_project_info"),
                buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", "call-1")
                    put("content", "{\"status\":\"ok\"}")
                },
            ),
            supportsNativeToolCalling = true,
            toolsOffered = true,
        )
    }

    @Test
    fun `tool protocol blocks orphan split duplicate and unsupported calls`() {
        val orphan = assertFailsWith<MobileConversationContextException> {
            MobileToolProtocolValidator.validate(
                listOf(
                    messageJson("system", "契约"),
                    buildJsonObject {
                        put("role", "tool")
                        put("tool_call_id", "missing")
                        put("content", "{}")
                    },
                ),
                supportsNativeToolCalling = true,
                toolsOffered = true,
            )
        }
        assertEquals(MobileConversationContextErrorCode.ORPHAN_TOOL_RESULT, orphan.code)

        val split = assertFailsWith<MobileConversationContextException> {
            MobileToolProtocolValidator.validate(
                listOf(
                    messageJson("system", "契约"),
                    assistantToolCall("call-1", "search_chapters"),
                    messageJson("assistant", "我先继续说文字"),
                ),
                supportsNativeToolCalling = true,
                toolsOffered = true,
            )
        }
        assertEquals(MobileConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION, split.code)

        val duplicate = assertFailsWith<MobileConversationContextException> {
            MobileToolProtocolValidator.validate(
                listOf(
                    messageJson("system", "契约"),
                    assistantToolCall("same", "search_chapters"),
                    toolResult("same"),
                    assistantToolCall("same", "search_outline"),
                    toolResult("same"),
                ),
                supportsNativeToolCalling = true,
                toolsOffered = true,
            )
        }
        assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, duplicate.code)

        val unsupported = assertFailsWith<MobileConversationContextException> {
            MobileToolProtocolValidator.validate(
                listOf(messageJson("system", "契约"), messageJson("user", "执行")),
                supportsNativeToolCalling = false,
                toolsOffered = true,
            )
        }
        assertEquals(MobileConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE, unsupported.code)
    }

    @Test
    fun `native tool batch limits match golden and reject the whole response before handlers`() {
        val fixture = interopFixture()["native_tool_budget"] as JsonObject
        assertEquals(
            fixture["max_native_assistant_transaction_json_bytes"]?.toString()?.toInt(),
            MobileNativeToolBudgetContract.MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
        )
        assertEquals(
            fixture["max_model_visible_tool_result_batch_json_bytes"]?.toString()?.toInt(),
            MobileNativeToolBudgetContract.MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
        )
        assertEquals(
            fixture["max_native_tool_calls_per_step"]?.toString()?.toInt(),
            MobileNativeToolBudgetContract.MAX_NATIVE_TOOL_CALLS_PER_STEP,
        )
        assertEquals(
            fixture["next_step_wrapper_tokens"]?.toString()?.toInt(),
            MobileNativeToolBudgetContract.NEXT_STEP_WRAPPER_TOKENS,
        )
        val resultContracts = fixture["standalone_result_json_bytes_by_tool"] as JsonObject
        resultContracts.forEach { (tool, bytes) ->
            assertEquals(
                bytes.toString().toInt(),
                MobileNativeToolBudgetContract.declaredResultJsonBytes(tool),
                tool,
            )
        }
        val errors = fixture["errors"] as JsonObject
        assertEquals(
            (errors["assistant_invalid"] as JsonPrimitive).content,
            MobileNativeToolBudgetContract.NATIVE_ASSISTANT_TRANSACTION_INVALID,
        )

        val accepted = MobileNativeToolBudgetContract.admitExactAssistantTransaction(
            assistantPayload = nativeAssistantPayload(listOf("set_tool_categories")),
            orderedToolNames = listOf("set_tool_categories"),
        )
        assertTrue(accepted.accepted)

        val resultBatch = MobileNativeToolBudgetContract.admitExactAssistantTransaction(
            assistantPayload = nativeAssistantPayload(
                listOf("search_chapters", "search_outline", "search_characters"),
            ),
            orderedToolNames = listOf("search_chapters", "search_outline", "search_characters"),
        )
        assertFalse(resultBatch.accepted)
        assertEquals(
            MobileNativeToolBudgetContract.TOOL_RESULT_BATCH_OVER_CAPACITY,
            resultBatch.reason,
        )

        val assistantBatch = MobileNativeToolBudgetContract.admitExactAssistantTransaction(
            assistantPayload = nativeAssistantPayload(
                listOf("set_tool_categories"),
                content = "x".repeat(17_000),
            ),
            orderedToolNames = listOf("set_tool_categories"),
        )
        assertFalse(assistantBatch.accepted)
        assertEquals(
            MobileNativeToolBudgetContract.NATIVE_ASSISTANT_TRANSACTION_OVER_CAPACITY,
            assistantBatch.reason,
        )

        val duplicateIdPayload = JsonObject(
            nativeAssistantPayload(listOf("set_tool_categories", "set_tool_categories")).toMutableMap().apply {
                put("tool_calls", buildJsonArray {
                    repeat(2) {
                        add(buildJsonObject {
                            put("id", "duplicate-call")
                            put("type", "function")
                            put("function", buildJsonObject {
                                put("name", "set_tool_categories")
                                put("arguments", "{}")
                            })
                        })
                    }
                })
            },
        )
        val duplicate = assertFailsWith<MobileConversationContextException> {
            MobileNativeToolBudgetContract.admitExactAssistantTransaction(
                assistantPayload = duplicateIdPayload,
                orderedToolNames = listOf("set_tool_categories", "set_tool_categories"),
            )
        }
        assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, duplicate.code)

        listOf("not-json", "[]", "42").forEach { arguments ->
            val payload = nativeAssistantPayload(
                listOf("set_tool_categories"),
                arguments = arguments,
            )
            val admissionError = assertFailsWith<MobileConversationContextException> {
                MobileNativeToolBudgetContract.admitExactAssistantTransaction(
                    assistantPayload = payload,
                    orderedToolNames = listOf("set_tool_categories"),
                )
            }
            assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, admissionError.code)

            val validatorError = assertFailsWith<MobileConversationContextException> {
                MobileToolProtocolValidator.validate(
                    messages = listOf(messageJson("system", "契约"), payload, toolResult("call-0")),
                    supportsNativeToolCalling = true,
                    toolsOffered = true,
                )
            }
            assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, validatorError.code)
        }
    }

    @Test
    fun `conversation context error codes match the shared golden`() {
        val expected = (interopFixture()["conversation_context_error_codes"] as JsonArray)
            .map { (it as JsonPrimitive).content }
            .toSet()

        assertEquals(expected, MobileConversationContextErrorCode.ALL)
    }

    @Test
    fun `native transaction retains reasoning and provider state exactly`() {
        val providerState = buildJsonObject {
            put("type", "reasoning")
            put("id", "reasoning-1")
            put("encrypted_content", "opaque")
        }
        val transaction = MobileToolTransaction(
            transactionId = "transaction-exact",
            assistantMessageId = "assistant-exact",
            assistantContent = "先查询",
            assistantReasoningContent = "需要读取章节",
            assistantProviderState = listOf(providerState),
            state = MobileToolTransactionState.DELIVERED,
            calls = listOf(MobileToolCallRecord("call-1", "search_chapters", "{\"limit\":1}")),
            results = listOf(MobileToolResultRecord("call-1", "{\"status\":\"ok\"}")),
        )

        val assistant = transaction.nativeMessages().first()
        assertEquals("需要读取章节", (assistant["reasoning_content"] as JsonPrimitive).content)
        assertEquals(JsonArray(listOf(providerState)), assistant["provider_state"])
        assertEquals(JsonArray(listOf(providerState)), transaction.toFrameJson()["assistant_provider_state"])
    }

    @Test
    fun `only consumed persisted transaction can become compactable`() {
        val delivered = MobileToolTransaction(
            transactionId = "transaction-1",
            assistantMessageId = "assistant-1",
            state = MobileToolTransactionState.DELIVERED,
            calls = listOf(MobileToolCallRecord("call-1", "search_chapters")),
            results = listOf(MobileToolResultRecord("call-1")),
        )
        assertFalse(delivered.canCompact)
        val consumed = delivered.copy(
            state = MobileToolTransactionState.CONSUMED,
            results = listOf(
                MobileToolResultRecord(
                    "call-1",
                    resultRef = "assistant_run_step:step-1",
                    persistedStepId = "step-1",
                ),
            ),
        )
        val compactable = consumed.markCompactable()
        assertTrue(compactable.canCompact)
        assertFailsWith<IllegalArgumentException> {
            delivered.copy(state = MobileToolTransactionState.COMPACTABLE)
        }
    }

    @Test
    fun `three tool rounds replay only the newest delivered batch and keep a bounded receipt ledger`() {
        var runtime = MobileTurnToolRuntimeState(turnId = "turn-current")

        repeat(3) { index ->
            val transaction = deliveredTransaction(index)
            runtime = runtime.recordDelivered(transaction)

            assertEquals(
                listOf(transaction.transactionId),
                runtime.deliveredTransactions.map(MobileToolTransaction::transactionId),
            )

            runtime = runtime.markDeliveredConsumed()
            assertTrue(runtime.deliveredTransactions.isEmpty())
            assertEquals(index + 1, runtime.executionLedger.size)
            assertEquals(index + 1, runtime.transactions.count(MobileToolTransaction::canCompact))
        }

        assertEquals(3, runtime.executionLedger.size)
        assertEquals(3, runtime.executionLedger.map(MobileToolExecutionReceipt::stepId).distinct().size)
        assertTrue(runtime.transactions.all(MobileToolTransaction::canCompact))
    }

    @Test
    fun `context frame keeps current user separate and seals a deterministic hash`() {
        val user = message(5L, "current", "user", "现在只修改标题", "running")
        val binding = testBinding()
        val budget = buildMobileRequestBudget(
            binding = binding,
            counter = MobileUtf8ByteTokenCounter,
            components = MobileRequestTokenComponents(currentUserTokens = 20),
            safetyMarginTokens = 100,
        )
        val frame = MobileConversationContextFrame(
            conversation = MobileConversationIdentity(
                kind = "workspace",
                id = "conversation-1",
                revision = 5L,
                projectId = "project-1",
            ),
            modelBinding = binding,
            systemContract = MobileSystemContract("a".repeat(64), "b".repeat(64)),
            checkpoint = null,
            recentTurns = listOf(completedTurn(1)),
            currentUserMessage = user,
            currentTurnLedger = emptyList(),
            pendingToolTransactions = emptyList(),
            budget = budget,
            transcriptRevision = 5L,
        )

        val sealed = frame.toJson()
        val current = sealed["current_user_message"] as JsonObject
        val integrity = sealed["integrity"] as JsonObject
        assertEquals("现在只修改标题", (current["content"] as JsonPrimitive).content)
        assertEquals(
            "71e1726eb23add6db52ea7f721fdcbed49622050b06a42d0234cdac65801581e",
            (integrity["frame_hash"] as JsonPrimitive).content,
        )
        assertTrue(sealed["checkpoint"] is JsonNull)
        assertEquals(interopFixture()["frame"], sealed)
    }

    @Test
    fun `multi segment frame golden interleaves checkpoints and exceptional exact turns`() {
        val firstTurn = closedTurn(1, "completed")
        val thirdTurn = closedTurn(3, "completed")
        val firstCheckpoint = checkpoint(
            id = "checkpoint-1",
            sourceTurn = firstTurn,
        )
        val activeCheckpoint = checkpoint(
            id = "checkpoint-2",
            sourceTurn = thirdTurn,
            parent = firstCheckpoint,
        )
        val binding = testBinding()
        val frame = MobileConversationContextFrame(
            conversation = MobileConversationIdentity(
                kind = "workspace",
                id = "conversation-1",
                revision = 11L,
                projectId = "project-1",
            ),
            modelBinding = binding,
            systemContract = MobileSystemContract("a".repeat(64), "b".repeat(64)),
            checkpoint = activeCheckpoint,
            checkpointSegments = listOf(firstCheckpoint, activeCheckpoint),
            recentTurns = listOf(
                closedTurn(2, "cancelled"),
                closedTurn(4, "error"),
                closedTurn(5, "completed"),
            ),
            currentUserMessage = message(11L, "current", "user", "继续", "running"),
            currentTurnLedger = emptyList(),
            pendingToolTransactions = emptyList(),
            budget = buildMobileRequestBudget(
                binding = binding,
                counter = MobileUtf8ByteTokenCounter,
                components = MobileRequestTokenComponents(currentUserTokens = 20),
                safetyMarginTokens = 100,
            ),
            transcriptRevision = 11L,
        )

        assertEquals(
            listOf(1L, 3L, 5L, 7L, 9L),
            frame.historicalEvents().map(MobileHistoricalContextEvent::firstSequence),
        )
        assertEquals(listOf(false, true), frame.historicalEvents()
            .filter { it.checkpointSegment != null }
            .map(MobileHistoricalContextEvent::isActiveCheckpoint))
        assertEquals(interopFixture()["segmented_frame"], frame.toJson())
    }

    @Test
    fun `provider rendering sends only the latest aggregate checkpoint regardless of segment count`() {
        var parent: MobileConversationCheckpoint? = null
        val segments = (1..24).map { index ->
            checkpoint(
                id = "checkpoint-$index",
                sourceTurn = closedTurn(index, "completed"),
                parent = parent,
            ).also { parent = it }
        }
        val binding = testBinding().copy(contextWindowTokens = 100_000)
        val frame = MobileConversationContextFrame(
            conversation = MobileConversationIdentity(
                kind = "workspace",
                id = "conversation-1",
                revision = 49L,
                projectId = "project-1",
            ),
            modelBinding = binding,
            systemContract = MobileSystemContract("a".repeat(64), "b".repeat(64)),
            checkpoint = segments.last(),
            checkpointSegments = segments,
            recentTurns = emptyList(),
            currentUserMessage = message(49L, "current", "user", "继续", "running"),
            currentTurnLedger = emptyList(),
            pendingToolTransactions = emptyList(),
            budget = buildMobileRequestBudget(
                binding = binding,
                counter = MobileUtf8ByteTokenCounter,
                components = MobileRequestTokenComponents(currentUserTokens = 20),
                safetyMarginTokens = 100,
            ),
            transcriptRevision = 49L,
        )

        val rendered = renderMobileContextFrame(frame, "system")
        assertEquals(3, rendered.messages.size)
        assertEquals(
            1,
            rendered.messages.count { message ->
                (message["content"] as? JsonPrimitive)?.content?.contains("[HISTORICAL_REFERENCE_DATA]") == true
            },
        )
        assertFalse(rendered.messages.joinToString().contains("HISTORICAL_CHECKPOINT_SEGMENT"))
    }

    @Test
    fun `checkpoint preflight counts exact chat and responses envelopes at zero safety margin`() {
        val directApi = DirectApiClient()
        val sourceMessages = completedTurn(1).messages
        val request = MobileCheckpointGenerationRequest(
            scope = "workspace",
            conversationId = "conversation-1",
            transcriptRevision = 2L,
            sourceRange = MobileConversationSourceRange(
                firstSequence = sourceMessages.first().sequenceNo,
                lastSequence = sourceMessages.last().sequenceNo,
                messageCount = sourceMessages.size,
                sourceHash = mobileConversationSourceHash(sourceMessages),
            ),
            sourceMessages = sourceMessages,
            priorSegments = emptyList(),
            deterministicExecutionLedger = emptyList(),
            modelBinding = JsonObject(emptyMap()),
        )
        val maxOutputTokens = 1_000
        listOf(
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
            DirectApiConfig.PROTOCOL_RESPONSES,
        ).forEach { protocol ->
            val config = DirectApiConfig(
                displayName = "test",
                baseUrl = "https://example.com/v1",
                apiKey = "secret",
                model = "checkpoint-model",
                protocol = protocol,
                contextWindowTokens = 100_000,
                maxOutputTokens = maxOutputTokens,
                safetyMarginTokens = 0,
            )
            fun generator(contextWindowTokens: Int) = MobileDirectCheckpointGenerator(
                directApi = directApi,
                config = config,
                counter = MobileUtf8ByteTokenCounter,
                contextWindowTokens = contextWindowTokens,
                maxOutputTokens = maxOutputTokens,
                safetyMarginTokens = 0,
            )

            val probe = generator(100_000)
            val initialInputTokens = probe.requestInputTokens(request)
            assertTrue(generator(initialInputTokens + maxOutputTokens).promptFits(request), protocol)
            assertFalse(generator(initialInputTokens + maxOutputTokens - 1).promptFits(request), protocol)

            val repairPrompt = "repair\n" + "无效输出".repeat(256)
            val repairInputTokens = probe.requestInputTokens(repairPrompt)
            assertTrue(generator(repairInputTokens + maxOutputTokens).promptFits(repairPrompt), protocol)
            assertFalse(generator(repairInputTokens + maxOutputTokens - 1).promptFits(repairPrompt), protocol)

            val payload = directApi.completeRequestPayload(
                config = config,
                protocol = protocol,
                systemPrompt = "system",
                userPrompt = "user",
                maxOutputTokens = maxOutputTokens,
                temperature = 0.0,
                extraBody = buildJsonObject { put("response_option", true) },
            )
            assertEquals("checkpoint-model", (payload["model"] as JsonPrimitive).content)
            assertEquals("false", (payload["stream"] as JsonPrimitive).content)
            assertEquals(0.0, (payload["temperature"] as JsonPrimitive).content.toDouble())
            assertTrue("response_option" in payload)
            if (protocol == DirectApiConfig.PROTOCOL_RESPONSES) {
                assertEquals("system", (payload["instructions"] as JsonPrimitive).content)
                assertEquals("user", (payload["input"] as JsonPrimitive).content)
                assertEquals(maxOutputTokens, payload["max_output_tokens"].toString().toInt())
            } else {
                assertEquals(2, (payload["messages"] as JsonArray).size)
                assertEquals(maxOutputTokens, payload["max_tokens"].toString().toInt())
            }
        }
    }

    @Test
    fun `request budget counts every section and reserves the next tool result`() {
        val binding = testBinding()
        val envelope = buildMobileRequestBudget(
            binding = binding,
            counter = MobileUtf8ByteTokenCounter,
            components = MobileRequestTokenComponents(
                systemPromptTokens = 100,
                generatorTemplateTokens = 10,
                toolSchemaTokens = 50,
                messageWrapperTokens = 20,
                providerProtocolTokens = 5,
                checkpointTokens = 30,
                recentExactTurnTokens = 100,
                currentUserTokens = 20,
                currentTurnLedgerTokens = 10,
                pendingToolTransactionTokens = 5,
                providerStateTokens = 5,
                extraRuntimeInstructionTokens = 5,
                maxModelVisibleResultTokensForOpenTools = 350,
                nextStepWrapperTokens = 25,
            ),
            safetyMarginTokens = 100,
        )

        assertEquals(360, envelope.currentInputTokens)
        assertEquals(700, envelope.requestInputLimit)
        assertEquals(735, envelope.projectedNextStepTokens)
        assertTrue(envelope.fitsCurrent)
        assertFalse(envelope.fitsProjected)
        envelope.requireSendable()
        assertEquals(binding.fingerprint, envelope.toJson()["model_binding_fingerprint"]?.toString()?.trim('"'))
    }

    @Test
    fun `rendered request budget covers chat and responses payloads without empty tool reserves`() {
        val directApi = DirectApiClient()
        val systemPrompt = "system\n[SERVER_RUNTIME_INSTRUCTION]\n只依据真实回执回复"
        val scopedTools = buildJsonArray {
            add(buildJsonObject {
                put("type", "function")
                put("function", buildJsonObject {
                    put("name", "get_project_info")
                    put("description", "读取项目信息")
                    put("parameters", buildJsonObject { put("type", "object") })
                })
            })
        }
        val transaction = deliveredTransaction(1).copy(
            assistantReasoningContent = "provider reasoning",
            assistantProviderState = listOf(buildJsonObject {
                put("type", "reasoning")
                put("id", "reasoning-1")
            }),
        )
        val ledger = listOf(MobileToolExecutionReceipt(
            stepId = "step-1",
            tool = "get_project_info",
            status = "ok",
            summary = "已读取项目",
            resourceIds = listOf("project-1"),
            resultRef = "mobile-tool-result:abc",
            reread = null,
            writeCommitted = false,
        ))

        listOf(
            DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
            DirectApiConfig.PROTOCOL_RESPONSES,
        ).forEach { protocol ->
            val binding = testBinding().copy(
                protocol = protocol,
                contextWindowTokens = 100_000,
                maxOutputTokens = 1_200,
            )
            val frame = MobileConversationContextFrame(
                conversation = MobileConversationIdentity(
                    kind = "workspace",
                    id = "conversation-1",
                    revision = 3L,
                    projectId = "project-1",
                ),
                modelBinding = binding,
                systemContract = MobileSystemContract("a".repeat(64), "b".repeat(64)),
                checkpoint = null,
                recentTurns = listOf(completedTurn(1)),
                currentUserMessage = message(3L, "current", "user", "继续", "running"),
                currentTurnLedger = ledger,
                pendingToolTransactions = listOf(transaction),
                budget = buildMobileRequestBudget(
                    binding = binding,
                    counter = MobileUtf8ByteTokenCounter,
                    components = MobileRequestTokenComponents(),
                    safetyMarginTokens = 100,
                ),
                transcriptRevision = 3L,
            )
            val rendered = renderMobileContextFrameUnchecked(frame, systemPrompt)
            val config = DirectApiConfig(
                displayName = "test",
                baseUrl = "https://example.com/v1",
                apiKey = "secret",
                model = "test-model",
                protocol = protocol,
                contextWindowTokens = 100_000,
                maxOutputTokens = 1_200,
                safetyMarginTokens = 100,
            )
            val components = countMobileRenderedRequestComponents(
                directApi = directApi,
                config = config,
                frame = frame,
                rendered = rendered,
                scopedTools = scopedTools,
                maxOutputTokens = 1_200,
                toolChoice = "auto",
                temperature = 0.25,
                extraBody = buildJsonObject { put("runtime_option", true) },
            )
            val actualPayload = directApi.agentRequestPayload(
                config = config,
                messages = providerMessages(rendered.messages),
                tools = scopedTools,
                toolChoice = "auto",
                maxOutputTokens = 1_200,
                temperature = 0.25,
                extraBody = buildJsonObject { put("runtime_option", true) },
                stream = true,
            )

            assertTrue(components.messageWrapperTokens > 0, protocol)
            assertTrue(components.providerProtocolTokens > 0, protocol)
            assertTrue(components.providerStateTokens > 0, protocol)
            assertEquals(MobileUtf8ByteTokenCounter.countText(systemPrompt), components.systemPromptTokens)
            assertTrue(
                components.currentInputTokens >= MobileUtf8ByteTokenCounter.countValue(actualPayload),
                protocol,
            )

            val withoutTools = countMobileRenderedRequestComponents(
                directApi = directApi,
                config = config,
                frame = frame,
                rendered = rendered,
                scopedTools = JsonArray(emptyList()),
                maxOutputTokens = 1_200,
                toolChoice = null,
                temperature = 0.2,
                extraBody = null,
            )
            assertEquals(0, withoutTools.maxModelVisibleResultTokensForOpenTools, protocol)
            assertEquals(0, withoutTools.nextStepWrapperTokens, protocol)
        }
        assertEquals(
            MobileNativeToolBudgetContract.NEXT_STEP_WRAPPER_TOKENS,
            MobileNativeToolBudgetContract.nextStepWrapperTokens(toolsOffered = true),
        )
        assertEquals(0, MobileNativeToolBudgetContract.nextStepWrapperTokens(toolsOffered = false))
    }

    private fun completedTurn(index: Int): MobileConversationTurn = closedTurn(index, "completed")

    private fun nativeAssistantPayload(
        toolNames: List<String>,
        content: String = "",
        arguments: String = "{}",
    ): JsonObject = buildJsonObject {
        put("role", "assistant")
        put("content", content)
        put("tool_calls", buildJsonArray {
            toolNames.forEachIndexed { index, tool ->
                add(buildJsonObject {
                    put("id", "call-$index")
                    put("type", "function")
                    put("function", buildJsonObject {
                        put("name", tool)
                        put("arguments", arguments)
                    })
                })
            }
        })
    }

    private fun deliveredTransaction(index: Int): MobileToolTransaction {
        val callId = "call-$index"
        return MobileToolTransaction(
            transactionId = "transaction-$index",
            assistantMessageId = "assistant-$index",
            state = MobileToolTransactionState.DELIVERED,
            calls = listOf(
                MobileToolCallRecord(
                    id = callId,
                    name = "get_project_info",
                    argumentsJson = "{\"round\":$index}",
                ),
            ),
            results = listOf(
                MobileToolResultRecord(
                    toolCallId = callId,
                    content = "{\"tool\":\"get_project_info\",\"status\":\"ok\",\"detail\":\"round $index\",\"data\":{}}",
                ),
            ),
        )
    }

    private fun closedTurn(index: Int, status: String): MobileConversationTurn {
        val first = index.toLong() * 2L - 1L
        val turnId = "turn-$index"
        return MobileConversationTurn(
            turnId = turnId,
            status = status,
            messages = listOf(
                message(first, turnId, "user", "问题 $index", status),
                message(first + 1L, turnId, "assistant", "回答 $index", status),
            ),
        )
    }

    private fun checkpoint(
        id: String,
        sourceTurn: MobileConversationTurn,
        parent: MobileConversationCheckpoint? = null,
    ): MobileConversationCheckpoint {
        val messages = sourceTurn.messages
        val quote = MobileCheckpointAuthorQuote(
            messageId = "message-1",
            startChar = 0,
            endChar = 4,
            exactQuote = "问题 1",
            quoteSha256 = mobileConversationSha256("问题 1"),
            purpose = "active_constraint",
            superseded = false,
        )
        return MobileConversationCheckpoint(
            id = id,
            conversationId = "conversation-1",
            parentCheckpointId = parent?.id,
            status = MobileConversationCheckpointStatus.READY,
            sourceRange = MobileConversationSourceRange(
                firstSequence = messages.first().sequenceNo,
                lastSequence = messages.last().sequenceNo,
                messageCount = messages.size,
                sourceHash = mobileConversationSourceHash(messages),
            ),
            transcriptRevision = 11L,
            idempotencyKey = "idempotency-$id",
            semanticNavigation = MobileConversationCheckpoint.emptySemanticNavigation(),
            authorQuotes = parent?.authorQuotes ?: listOf(quote),
            segmentIds = parent?.let { it.segmentIds + it.id }.orEmpty(),
            createdAt = "2026-01-01T00:00:00Z",
            updatedAt = "2026-01-01T00:00:00Z",
            completedAt = "2026-01-01T00:00:00Z",
        )
    }

    private fun message(
        sequence: Long,
        turnId: String,
        role: String,
        content: String,
        status: String,
    ) = MobileTranscriptMessage(
        id = "message-$sequence",
        sequenceNo = sequence,
        turnId = turnId,
        role = role,
        content = content,
        status = status,
        createdAt = "2026-01-01T00:00:${sequence.toString().padStart(2, '0')}Z",
    )

    private fun budget(limit: Int) = MobileRecentTurnBudget(
        requestInputLimitTokens = limit,
        systemAndToolsTokens = 90,
        providerWrapperTokens = 0,
        checkpointTokens = 0,
        currentUserTokens = 10,
        currentTurnLedgerTokens = 0,
        pendingToolTransactionTokens = 0,
    )

    private fun testBinding() = MobileGenerationModelBinding(
        taskType = "assistant",
        provider = "openai",
        modelName = "test-model",
        normalizedModel = "openai:test-model",
        protocol = "chat_completions",
        contextWindowTokens = 1_000,
        maxOutputTokens = 200,
        tokenCounterId = MobileUtf8ByteTokenCounter.counterId,
        capacityAssurance = MobileUtf8ByteTokenCounter.assurance,
        promptContractHash = "prompt-hash",
        toolSchemaHash = "tool-hash",
        configFingerprint = "config-hash",
    )

    private fun messageJson(role: String, content: String): JsonObject = buildJsonObject {
        put("role", role)
        put("content", content)
    }

    private fun assistantToolCall(id: String, name: String): JsonObject = buildJsonObject {
        put("role", "assistant")
        put("content", "")
        put("tool_calls", buildJsonArray {
            add(buildJsonObject {
                put("id", id)
                put("type", "function")
                put("function", buildJsonObject {
                    put("name", name)
                    put("arguments", "{}")
                })
            })
        })
    }

    private fun toolResult(id: String): JsonObject = buildJsonObject {
        put("role", "tool")
        put("tool_call_id", id)
        put("content", "{}")
    }

    private fun interopFixture(): JsonObject = resourceFixture("conversation-context-v1-interop.json")

    private fun resourceFixture(name: String): JsonObject {
        val stream = requireNotNull(
            javaClass.classLoader?.getResourceAsStream(name),
        ) { "共享会话上下文互操作夹具不存在" }
        return stream.bufferedReader(Charsets.UTF_8).use { reader ->
            Json.parseToJsonElement(reader.readText()) as JsonObject
        }
    }
}
