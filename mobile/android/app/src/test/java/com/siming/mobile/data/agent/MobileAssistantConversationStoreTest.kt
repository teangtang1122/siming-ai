package com.siming.mobile.data.agent

import com.siming.mobile.data.creation.CreationAgentTurnRecords
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNotEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileAssistantConversationStoreTest {
    @Test
    fun `lost transcript response keeps old snapshot key while title and project change identity`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "　第一\r\n章 ")
            store.finishTurn("project-1", turn, "已完成", "completed", emptyList())

            val beforeResponse = assertNotNull(
                store.snapshot("project-1", turn.conversationId),
            )
            val original = assertNotNull(beforeResponse.nextTranscriptImportRequest())
            val restored = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot(
                    "project-1",
                    turn.conversationId,
                ),
            )
            val recovered = assertNotNull(restored.nextTranscriptImportRequest())
            val renamed = assertNotNull(
                restored.copy(title = "第二章").nextTranscriptImportRequest(),
            )
            val foreignProject = assertNotNull(
                restored.copy(projectId = "project-2").nextTranscriptImportRequest(),
            )

            assertEquals(original.idempotencyKey, recovered.idempotencyKey)
            assertEquals(original.messages, recovered.messages)
            assertNotEquals(original.idempotencyKey, renamed.idempotencyKey)
            assertNotEquals(original.idempotencyKey, foreignProject.idempotencyKey)
            assertEquals(0L, restored.replicaState.confirmedSourceRevision)
        }
    }

    @Test
    fun `standalone transcript and tool log survive a new store instance`() = runBlocking {
        withTemporaryDirectory { directory ->
            val firstStore = MobileAssistantConversationStore(directory)
            val first = firstStore.beginTurn("project-1", null, "检查人物动机")
            firstStore.finishTurn(
                projectId = "project-1",
                turnContext = first,
                content = "已检查主要角色。",
                status = "completed",
                toolLogs = listOf("已读取 3 个角色"),
            )

            val restored = MobileAssistantConversationStore(directory)
            val conversations = restored.conversations("project-1")
            val messages = restored.messages("project-1", first.conversationId)
            val snapshot = assertNotNull(restored.snapshot("project-1", first.conversationId))

            assertEquals(1, conversations.size)
            assertEquals(2, conversations.single().messageCount)
            assertEquals(listOf("user", "assistant"), messages.map { it.role })
            assertEquals(listOf("已读取 3 个角色"), messages.last().toolLogs)
            assertEquals(listOf(1L, 2L), snapshot.messages.map { it.sequenceNo })
            assertEquals(2L, snapshot.transcriptRevision)
            val persistedRoot = Json.parseToJsonElement(
                directory.resolve("project-1.json").readText(),
            ) as JsonObject
            val persistedConversation = (persistedRoot["conversations"] as JsonArray)
                .single() as JsonObject
            assertEquals(
                2L,
                (persistedConversation["transcript_revision"] as JsonPrimitive).content.toLong(),
            )
            assertEquals(
                3L,
                (persistedConversation["next_sequence_no"] as JsonPrimitive).content.toLong(),
            )

            val next = restored.beginTurn("project-1", first.conversationId, "继续")
            val nextSnapshot = assertNotNull(restored.snapshot("project-1", first.conversationId))
            val history = nextSnapshot.historicalTurns(next).flatMap(MobileConversationTurn::messages)
            assertEquals(2, history.size)
            assertEquals(3L, next.userSequence)
            assertTrue(history.last().content.contains("已检查"))
        }
    }

    @Test
    fun `delivered tool transaction and compactable receipt persist atomically`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "读取作品")
            val delivered = MobileToolTransaction(
                transactionId = "transaction-1",
                assistantMessageId = "assistant-1",
                assistantContent = "先读取",
                assistantReasoningContent = "需要核对项目资料",
                state = MobileToolTransactionState.DELIVERED,
                calls = listOf(
                    MobileToolCallRecord(
                        id = "call-1",
                        name = "get_project_info",
                        argumentsJson = "{ \"project_id\": \"project-1\" }",
                    ),
                ),
                results = listOf(
                    MobileToolResultRecord(
                        toolCallId = "call-1",
                        content = "{\"tool\":\"get_project_info\",\"status\":\"ok\",\"detail\":\"已读取\",\"data\":{}}",
                    ),
                ),
            )
            store.recordDeliveredToolTransaction("project-1", turn, delivered)

            val deliveredBeforeConsumption = assertNotNull(
                store.snapshot("project-1", turn.conversationId),
            ).toolRuntimeState(turn.turnId)
            assertEquals(
                listOf("transaction-1"),
                deliveredBeforeConsumption?.deliveredTransactions?.map { it.transactionId },
            )
            assertEquals(
                "{ \"project_id\": \"project-1\" }",
                deliveredBeforeConsumption?.deliveredTransactions?.single()?.calls?.single()?.argumentsJson,
            )

            store.markDeliveredToolTransactionsConsumed("project-1", turn)
            val compactedAfterRestart = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot("project-1", turn.conversationId),
            ).toolRuntimeState(turn.turnId)
            assertTrue(compactedAfterRestart?.deliveredTransactions.orEmpty().isEmpty())
            assertEquals(MobileToolTransactionState.COMPACTABLE, compactedAfterRestart?.transactions?.single()?.state)
            assertEquals(1, compactedAfterRestart?.executionLedger?.size)
            assertNotNull(compactedAfterRestart?.transactions?.single()?.results?.single()?.resultRef)
            assertNotNull(compactedAfterRestart?.transactions?.single()?.results?.single()?.persistedStepId)
        }
    }

    @Test
    fun `oversized workspace rejection survives restart as delivered without a ledger`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "读取作品")
            val rejected = MobileToolTransaction(
                transactionId = "workspace-rejected-transaction",
                assistantMessageId = "workspace-rejected-assistant",
                assistantContent = "x".repeat(17_000),
                state = MobileToolTransactionState.DELIVERED,
                calls = listOf(
                    MobileToolCallRecord(
                        id = "call-rejected",
                        name = "set_tool_categories",
                        argumentsJson = "{\"enabled_categories\":[]}",
                    ),
                ),
                results = listOf(
                    MobileToolResultRecord(
                        toolCallId = "call-rejected",
                        content = """{"tool":"set_tool_categories","status":"denied","detail":"整批未执行","data":{}}""",
                    ),
                ),
            )
            val admission = MobileNativeToolBatchAdmission(
                accepted = false,
                reason = MobileNativeToolBudgetContract.NATIVE_ASSISTANT_TRANSACTION_OVER_CAPACITY,
                declaredJsonBytes = 17_000,
                maxJsonBytes = MobileNativeToolBudgetContract.MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
                callCount = 1,
            )

            val error = assertFailsWith<MobileConversationContextException> {
                persistRejectedMobileNativeToolBatch(
                    conversationStore = store,
                    projectId = "project-1",
                    turnContext = turn,
                    transaction = rejected,
                    admission = admission,
                    overCapacityDetail = "workspace transaction exceeds the protocol",
                    afterPersist = {},
                )
            }
            assertEquals(MobileConversationContextErrorCode.PROTOCOL_INVALID, error.code)

            val restarted = MobileAssistantConversationStore(directory)
            val snapshot = assertNotNull(restarted.snapshot("project-1", turn.conversationId))
            val runtime = assertNotNull(snapshot.toolRuntimeState(turn.turnId))
            assertEquals(MobileToolTransactionState.DELIVERED, runtime.transactions.single().state)
            assertEquals(runtime.transactions, runtime.deliveredTransactions)
            assertTrue(runtime.executionLedger.isEmpty())
            assertNull(runtime.transactions.single().results.single().resultRef)
            assertNull(runtime.transactions.single().results.single().persistedStepId)
        }
    }

    @Test
    fun `turn completion is idempotent and rejects a conflicting retry`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "继续")
            store.finishTurn("project-1", turn, "完成", "completed", emptyList())
            store.finishTurn("project-1", turn, "完成", "completed", emptyList())

            assertEquals(2, store.messages("project-1", turn.conversationId).size)
            assertFailsWith<MobileConversationStorageException> {
                store.finishTurn("project-1", turn, "另一个结果", "completed", emptyList())
            }
        }
    }

    @Test
    fun `newer user message prevents stale turn from being closed`() = runBlocking {
        withTemporaryDirectory { directory ->
            val original = MobileAssistantConversationStore(directory)
            val old = original.beginTurn("project-1", null, "旧任务")
            val restarted = MobileAssistantConversationStore(directory)
            val newest = restarted.beginTurn("project-1", old.conversationId, "新任务")

            val error = assertFailsWith<MobileConversationContextException> {
                original.finishTurn("project-1", old, "旧任务完成", "completed", emptyList())
            }
            assertEquals(MobileConversationContextErrorCode.SOURCE_CHANGED, error.code)
            val snapshot = assertNotNull(restarted.snapshot("project-1", newest.conversationId))
            val history = snapshot.historicalTurns(newest).flatMap(MobileConversationTurn::messages)
            assertEquals(listOf("user", "assistant"), history.map { it.role })
            assertEquals("aborted", history.last().status)
        }
    }

    @Test
    fun `full transcript is not silently capped at two hundred messages`() = runBlocking {
        withTemporaryDirectory { directory ->
            directory.resolve("project-1.json").writeText(legacyArchive(conversationCount = 1, messageCount = 202))
            val restored = MobileAssistantConversationStore(directory)
            val snapshot = assertNotNull(restored.snapshot("project-1", "conversation-0"))
            assertEquals(202, snapshot.messages.size)
            assertEquals((1L..202L).toList(), snapshot.messages.map { it.sequenceNo })
            assertEquals("消息 0", snapshot.messages.first().content)
        }
    }

    @Test
    fun `creation archive beyond twenty turns restores checkpoint and exact replay tail`() = runBlocking {
        withTemporaryDirectory { directory ->
            val archived = (1..25).map { index ->
                MobileArchivedConversationTurn(
                    turnId = "creation-turn-$index",
                    userContent = "立项问题 $index",
                    assistantContent = "立项回答 $index",
                    status = "completed",
                    createdAt = "2026-02-01T00:00:${index.toString().padStart(2, '0')}Z",
                    updatedAt = "2026-02-01T00:01:${index.toString().padStart(2, '0')}Z",
                )
            }
            val storageId = "creation-session-archive"
            val conversationId = storageId
            val store = MobileAssistantConversationStore(directory)
            val imported = store.ensureConversationArchive(
                projectId = storageId,
                conversationId = conversationId,
                conversationKind = "creation",
                creationSessionId = "session-archive",
                title = "长期立项",
                archivedTurns = archived,
            )
            assertEquals(50, imported.messages.size)
            assertEquals((1L..50L).toList(), imported.messages.map { it.sequenceNo })
            val transcriptImport = assertNotNull(imported.nextTranscriptImportRequest())
            assertEquals(50, transcriptImport.messages.size)
            assertTrue(transcriptImport.messages.all { it.id.length <= 36 })

            val attempt = store.beginCheckpoint(
                projectId = storageId,
                conversationId = conversationId,
                sourceFirstSequence = 1L,
                sourceLastSequence = 40L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "9".repeat(64),
                expectedContextStateRevision = imported.contextState.revision,
                originalTokens = 2_000,
            )
            store.publishCheckpoint(
                projectId = storageId,
                conversationId = conversationId,
                checkpointId = attempt.checkpoint.id,
                expectedContextStateRevision = attempt.contextStateRevision,
                semanticDraft = MobileCheckpointSemanticDraft(
                    semanticNavigation = MobileConversationCheckpoint.emptySemanticNavigation(),
                    quoteSelections = emptyList(),
                ),
                deterministicExecutionLedger = emptyList(),
                projectRefs = emptyList(),
                validation = buildJsonObject { put("capacity_assurance", "exact") },
                checkpointTokens = 200,
            )

            val restarted = MobileAssistantConversationStore(directory)
            val restored = assertNotNull(restarted.snapshot(storageId, conversationId))
            assertEquals("creation", restored.conversationKind)
            assertEquals("session-archive", restored.creationSessionId)
            assertEquals("creation", restored.activeCheckpoint?.scope)
            assertEquals(50, restored.messages.size)

            val currentTurn = restarted.beginTurn(
                projectId = storageId,
                conversationId = conversationId,
                prompt = "继续完善立项",
                conversationKind = "creation",
                creationSessionId = "session-archive",
            )
            val current = assertNotNull(restarted.snapshot(storageId, conversationId))
            val binding = MobileGenerationModelBinding(
                taskType = "planning",
                provider = "android_direct_api",
                modelName = "test-model",
                normalizedModel = "test-model",
                protocol = "chat_completions",
                contextWindowTokens = 100_000,
                maxOutputTokens = 6_000,
                tokenCounterId = MobileUtf8ByteTokenCounter.counterId,
                capacityAssurance = MobileUtf8ByteTokenCounter.assurance,
                promptContractHash = "prompt",
                toolSchemaHash = "tools",
                configFingerprint = "config",
            )
            val budget = buildMobileRequestBudget(
                binding = binding,
                counter = MobileUtf8ByteTokenCounter,
                components = MobileRequestTokenComponents(currentUserTokens = 10),
                safetyMarginTokens = 1_000,
            )
            val recent = current.planRecentTurns(currentTurn, MobileUtf8ByteTokenCounter, budget)
            assertEquals(
                (21..25).map { "立项问题 $it" },
                recent.recentExactTurns.map { it.messages.first().content },
            )
        }
    }

    @Test
    fun `creation restart closes an interrupted canonical turn before the next task`() = runBlocking {
        withTemporaryDirectory { directory ->
            val storageId = "creation-interrupted"
            val first = MobileAssistantConversationStore(directory)
            first.ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = "session-interrupted",
                title = "立项",
                archivedTurns = emptyList(),
            )
            val turn = first.beginTurn(
                projectId = storageId,
                conversationId = storageId,
                prompt = "未完成问题",
                conversationKind = "creation",
                creationSessionId = "session-interrupted",
            )

            val restarted = MobileAssistantConversationStore(directory)
            val closed = assertNotNull(restarted.snapshot(storageId, storageId))
            assertEquals("aborted", closed.turns.single().status)
            assertEquals("上一轮任务未完成，已在新任务开始前安全终止。", closed.messages.last().content)
            assertEquals(turn.turnId, closed.messages.last().turnId)

            val reconciled = restarted.ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = "session-interrupted",
                title = "立项",
                archivedTurns = listOf(
                    MobileArchivedConversationTurn(
                        turnId = turn.turnId,
                        userContent = "未完成问题",
                        assistantContent = "上一轮任务未完成，已在新任务开始前安全终止。",
                        status = "aborted",
                        createdAt = closed.messages.first().createdAt,
                        updatedAt = closed.messages.last().createdAt,
                    ),
                ),
            )
            assertEquals(2, reconciled.messages.size)
            val next = restarted.beginTurn(
                projectId = storageId,
                conversationId = storageId,
                prompt = "继续立项",
                conversationKind = "creation",
                creationSessionId = "session-interrupted",
            )
            assertEquals(3L, next.userSequence)
        }
    }

    @Test
    fun `trailing legacy creation user imports once and remains idempotent after restart`() = runBlocking {
        withTemporaryDirectory { directory ->
            val sessionId = "session-legacy-trailing-user"
            val storageId = "creation-legacy-trailing-user"
            val legacy = buildJsonObject {
                put("id", sessionId)
                put("draft", buildJsonObject {
                    put("agent_history", buildJsonArray {
                        add(buildJsonObject {
                            put("id", "legacy-user-1")
                            put("role", "user")
                            put("content", "先前问题")
                            put("created_at", "2026-01-01T00:00:00Z")
                        })
                        add(buildJsonObject {
                            put("id", "legacy-assistant-1")
                            put("role", "assistant")
                            put("content", "先前回答")
                            put("created_at", "2026-01-01T00:00:01Z")
                        })
                        add(buildJsonObject {
                            put("id", "legacy-user-2")
                            put("role", "user")
                            put("content", "进程退出前的作者原文")
                            put("created_at", "2026-01-01T00:00:02Z")
                        })
                    })
                })
            }
            val firstMigration = CreationAgentTurnRecords.migrateLegacyHistory(legacy)
            val repeatedMigration = CreationAgentTurnRecords.migrateLegacyHistory(legacy)
            assertEquals(
                CreationAgentTurnRecords.turns(firstMigration),
                CreationAgentTurnRecords.turns(repeatedMigration),
            )
            val archived = CreationAgentTurnRecords.archivedTurns(firstMigration)
            assertEquals(2, archived.size)
            assertEquals("进程退出前的作者原文", archived.last().userContent)
            assertEquals("aborted", archived.last().status)
            assertEquals(
                "上一轮任务未完成，已在新任务开始前安全终止。",
                archived.last().assistantContent,
            )

            val first = MobileAssistantConversationStore(directory).ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = sessionId,
                title = "旧立项",
                archivedTurns = archived,
            )
            val restarted = MobileAssistantConversationStore(directory)
            val repeated = restarted.ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = sessionId,
                title = "旧立项",
                archivedTurns = CreationAgentTurnRecords.archivedTurns(repeatedMigration),
            )
            assertEquals(first.messages, repeated.messages)
            assertEquals((1L..4L).toList(), repeated.messages.map { it.sequenceNo })
            assertEquals(4L, repeated.transcriptRevision)
            assertEquals("进程退出前的作者原文", repeated.messages[2].content)
            assertEquals("aborted", repeated.messages[3].status)
            assertEquals(4, assertNotNull(repeated.nextTranscriptImportRequest()).messages.size)
            assertTrue(repeated.messages.all { it.id.length <= 36 })
        }
    }

    @Test
    fun `completed canonical creation turn repairs stale running audit after restart`() = runBlocking {
        withTemporaryDirectory { directory ->
            val storageId = "creation-finish-before-audit"
            val sessionId = "session-finish-before-audit"
            val first = MobileAssistantConversationStore(directory)
            first.ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = sessionId,
                title = "立项",
                archivedTurns = emptyList(),
            )
            val turn = first.beginTurn(
                projectId = storageId,
                conversationId = storageId,
                prompt = "已提交问题",
                conversationKind = "creation",
                creationSessionId = sessionId,
            )
            val staleAudit = CreationAgentTurnRecords.withTurns(
                session = buildJsonObject {
                    put("id", sessionId)
                    put("draft", buildJsonObject {})
                },
                turns = listOf(CreationAgentTurnRecords.pending("已提交问题", id = turn.turnId)),
            )

            // Canonical completion commits, then the process exits before the audit save.
            first.finishTurn(
                projectId = storageId,
                turnContext = turn,
                content = "canonical 已完成",
                status = "completed",
                toolLogs = listOf("{\"tool\":\"record\",\"status\":\"ok\"}"),
            )
            val incorrectlyAbortedAudit = CreationAgentTurnRecords.recoverInterruptedTurns(staleAudit)
            assertEquals("aborted", CreationAgentTurnRecords.archivedTurns(incorrectlyAbortedAudit).single().status)

            val restarted = MobileAssistantConversationStore(directory)
            val canonical = assertNotNull(restarted.snapshot(storageId, storageId))
            assertFailsWith<IllegalArgumentException> {
                restarted.ensureConversationArchive(
                    projectId = storageId,
                    conversationId = storageId,
                    conversationKind = "creation",
                    creationSessionId = sessionId,
                    title = "立项",
                    archivedTurns = CreationAgentTurnRecords.archivedTurns(incorrectlyAbortedAudit),
                )
            }

            val reconciledAudit = CreationAgentTurnRecords.reconcileWithCanonicalConversation(
                staleAudit,
                canonical,
            )
            assertEquals(
                reconciledAudit,
                CreationAgentTurnRecords.reconcileWithCanonicalConversation(
                    incorrectlyAbortedAudit,
                    canonical,
                ),
            )
            assertEquals(
                reconciledAudit,
                CreationAgentTurnRecords.reconcileWithCanonicalConversation(
                    reconciledAudit,
                    canonical,
                ),
            )
            val recoveredTurn = CreationAgentTurnRecords.archivedTurns(reconciledAudit).single()
            assertEquals(turn.turnId, recoveredTurn.turnId)
            assertEquals("canonical 已完成", recoveredTurn.assistantContent)
            assertEquals("completed", recoveredTurn.status)
            val recoveredAuditRecord = CreationAgentTurnRecords.turns(reconciledAudit).single()
            assertEquals(1, (recoveredAuditRecord["tool_results"] as JsonArray).size)

            val conflictingClosedAudit = CreationAgentTurnRecords.withTurns(
                staleAudit,
                listOf(CreationAgentTurnRecords.complete(
                    pending = CreationAgentTurnRecords.turns(staleAudit).single(),
                    reply = "另一个已闭合结果",
                    modelMessages = JsonArray(emptyList()),
                    toolResults = JsonArray(emptyList()),
                    replayable = false,
                    executionRoute = "device",
                )),
            )
            assertFailsWith<IllegalStateException> {
                CreationAgentTurnRecords.reconcileWithCanonicalConversation(
                    conflictingClosedAudit,
                    canonical,
                )
            }

            val consistent = restarted.ensureConversationArchive(
                projectId = storageId,
                conversationId = storageId,
                conversationKind = "creation",
                creationSessionId = sessionId,
                title = "立项",
                archivedTurns = CreationAgentTurnRecords.archivedTurns(reconciledAudit),
            )
            assertEquals(2, consistent.messages.size)
            assertEquals("canonical 已完成", consistent.messages.last().content)
            assertEquals("completed", consistent.messages.last().status)

            val next = restarted.beginTurn(
                projectId = storageId,
                conversationId = storageId,
                prompt = "继续立项",
                conversationKind = "creation",
                creationSessionId = sessionId,
            )
            assertEquals(3L, next.userSequence)
        }
    }

    @Test
    fun `conversation archive is not silently capped at forty conversations`() = runBlocking {
        withTemporaryDirectory { directory ->
            directory.resolve("project-1.json").writeText(legacyArchive(conversationCount = 41, messageCount = 2))
            assertEquals(41, MobileAssistantConversationStore(directory).conversations("project-1").size)
        }
    }

    @Test
    fun `schema one transcript is migrated in array order with stable sequences`() = runBlocking {
        withTemporaryDirectory { directory ->
            val legacy = directory.resolve("project-1.json")
            legacy.writeText(
                """{
                    "schema_version":1,
                    "conversations":[{
                        "id":"conversation-1",
                        "title":"旧会话",
                        "created_at":"2026-01-01T00:00:00Z",
                        "updated_at":"2026-01-01T00:00:02Z",
                        "messages":[
                            {"id":"u1","role":"user","content":"先检查","status":"completed","created_at":"2026-01-01T00:00:00Z","tool_logs":[]},
                            {"id":"a1","role":"assistant","content":"已检查","status":"completed","created_at":"2026-01-01T00:00:01Z","tool_logs":[]},
                            {"id":"u2","role":"user","content":"继续","status":"completed","created_at":"2026-01-01T00:00:02Z","tool_logs":[]}
                        ]
                    }]
                }""".trimIndent(),
            )

            val store = MobileAssistantConversationStore(directory)
            val first = assertNotNull(store.snapshot("project-1", "conversation-1"))
            val second = assertNotNull(MobileAssistantConversationStore(directory).snapshot("project-1", "conversation-1"))

            assertEquals(listOf(1L, 2L, 3L, 4L), first.messages.map { it.sequenceNo })
            assertEquals(first.messages.map { it.turnId }, second.messages.map { it.turnId })
            assertEquals(2, first.turns.size)
            assertEquals("继续", first.messages[2].content)
            assertEquals("aborted", first.messages[3].status)
            val migratedRoot = Json.parseToJsonElement(legacy.readText()) as JsonObject
            assertEquals(2, migratedRoot["schema_version"]?.toString()?.toInt())
        }
    }

    @Test
    fun `invalid author quote cannot publish or replace the active checkpoint`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            var conversationId: String? = null
            repeat(2) { index ->
                val turn = store.beginTurn("project-1", conversationId, "作者要求 $index")
                conversationId = turn.conversationId
                store.finishTurn("project-1", turn, "确认 $index", "completed", emptyList())
            }
            val initial = assertNotNull(store.snapshot("project-1", conversationId!!))
            val fingerprint = "a".repeat(64)
            val pending = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = conversationId!!,
                sourceFirstSequence = 1L,
                sourceLastSequence = 4L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = fingerprint,
                expectedContextStateRevision = initial.contextState.revision,
                originalTokens = 120,
            )
            val compressing = store.markCheckpointCompressing(
                "project-1",
                conversationId!!,
                pending.checkpoint.id,
                pending.contextStateRevision,
            )
            // A newer message does not mutate the immutable old source range.
            store.beginTurn("project-1", conversationId, "当前任务")
            assertFailsWith<MobileConversationContextException> {
                store.publishCheckpoint(
                    projectId = "project-1",
                    conversationId = conversationId!!,
                    checkpointId = pending.checkpoint.id,
                    expectedContextStateRevision = compressing.contextStateRevision,
                    semanticDraft = MobileCheckpointSemanticDraft(
                        semanticNavigation = MobileConversationCheckpoint.emptySemanticNavigation(),
                        quoteSelections = listOf(
                            MobileCheckpointQuoteSelection("u-does-not-exist", 0, 1, "active_constraint"),
                        ),
                    ),
                    deterministicExecutionLedger = emptyList(),
                    projectRefs = emptyList(),
                    validation = buildJsonObject { put("capacity_assurance", "exact") },
                    checkpointTokens = 30,
                )
            }
            val unchanged = assertNotNull(store.snapshot("project-1", conversationId!!))
            assertEquals(null, unchanged.contextState.activeCheckpointId)
            assertEquals(
                MobileConversationCheckpointStatus.COMPRESSING,
                unchanged.checkpoints.single().status,
            )
        }
    }

    @Test
    fun `valid checkpoint is restored and interrupted attempt becomes failed`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "必须保持称呼")
            store.finishTurn("project-1", turn, "已记录", "completed", emptyList())
            val snapshot = assertNotNull(store.snapshot("project-1", turn.conversationId))
            val attempt = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = turn.conversationId,
                sourceFirstSequence = 1L,
                sourceLastSequence = 2L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "b".repeat(64),
                expectedContextStateRevision = snapshot.contextState.revision,
            )
            val compressing = store.markCheckpointCompressing(
                "project-1",
                turn.conversationId,
                attempt.checkpoint.id,
                attempt.contextStateRevision,
            )
            val generationRequest = assertNotNull(store.snapshot("project-1", turn.conversationId))
                .checkpointGenerationRequest(attempt.checkpoint.id, emptyList())
            assertEquals(listOf(1L, 2L), generationRequest.sourceMessages.map { it.sequenceNo })
            assertTrue(generationRequest.priorSegments.isEmpty())
            val sourceUser = snapshot.messages.first()
            val ready = store.publishCheckpoint(
                projectId = "project-1",
                conversationId = turn.conversationId,
                checkpointId = attempt.checkpoint.id,
                expectedContextStateRevision = compressing.contextStateRevision,
                semanticDraft = semanticDraft(
                    MobileCheckpointQuoteSelection(
                        sourceUser.id,
                        0,
                        sourceUser.content.length,
                        "active_constraint",
                    ),
                ),
                deterministicExecutionLedger = emptyList(),
                projectRefs = emptyList(),
                validation = buildJsonObject { put("capacity_assurance", "exact") },
                checkpointTokens = 12,
            )
            assertEquals(MobileConversationCheckpointStatus.READY, ready.checkpoint.status)

            val restored = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot(
                    "project-1",
                    snapshot.conversationId,
                ),
            )
            assertEquals(ready.checkpoint.id, restored.contextState.activeCheckpointId)
            assertEquals("必须保持称呼", restored.activeCheckpoint?.authorQuotes?.single()?.exactQuote)

            val cancelledTurn = store.beginTurn("project-1", turn.conversationId, "取消的一轮")
            store.finishTurn("project-1", cancelledTurn, "已取消", "cancelled", emptyList())
            val nextTurn = store.beginTurn("project-1", turn.conversationId, "新一轮")
            store.finishTurn("project-1", nextTurn, "完成", "completed", emptyList())
            val nextSnapshot = assertNotNull(store.snapshot("project-1", turn.conversationId))
            val secondAttempt = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = turn.conversationId,
                sourceFirstSequence = 5L,
                sourceLastSequence = 6L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "c".repeat(64),
                expectedContextStateRevision = nextSnapshot.contextState.revision,
            )
            val secondSourceUser = nextSnapshot.messages.first { it.sequenceNo == 5L }
            val secondReady = store.publishCheckpoint(
                projectId = "project-1",
                conversationId = turn.conversationId,
                checkpointId = secondAttempt.checkpoint.id,
                expectedContextStateRevision = secondAttempt.contextStateRevision,
                semanticDraft = semanticDraft(
                    MobileCheckpointQuoteSelection(
                        secondSourceUser.id,
                        0,
                        secondSourceUser.content.length,
                        "active_constraint",
                    ),
                ).copy(
                    priorAuthorQuoteStates = ready.checkpoint.authorQuotes.map { quote ->
                        MobilePriorAuthorQuoteDecision(
                            messageId = quote.messageId,
                            startChar = quote.startChar,
                            endChar = quote.endChar,
                            quoteSha256 = quote.quoteSha256,
                            status = "active",
                        )
                    },
                ),
                deterministicExecutionLedger = emptyList(),
                projectRefs = emptyList(),
                validation = buildJsonObject { put("capacity_assurance", "exact") },
                checkpointTokens = 20,
            )
            assertEquals(5L, secondReady.checkpoint.sourceRange.firstSequence)
            assertEquals(6L, secondReady.checkpoint.sourceRange.lastSequence)
            assertEquals(listOf(ready.checkpoint.id), secondReady.checkpoint.segmentIds)
            assertEquals(2, secondReady.checkpoint.authorQuotes.size)
            val segmentedSnapshot = assertNotNull(store.snapshot("project-1", turn.conversationId))
            assertEquals(
                listOf(1L to 2L, 5L to 6L),
                segmentedSnapshot.activeCheckpointSegments.map {
                    it.sourceRange.firstSequence to it.sourceRange.lastSequence
                },
            )
            assertEquals(
                listOf("cancelled"),
                segmentedSnapshot.turns
                    .filter { it.firstSequence in 3L..4L }
                    .map(MobileConversationTurn::status),
            )

            val interruptedTurn = store.beginTurn("project-1", turn.conversationId, "再一轮")
            store.finishTurn("project-1", interruptedTurn, "完成", "completed", emptyList())
            val interruptedSnapshot = assertNotNull(store.snapshot("project-1", turn.conversationId))
            val interrupted = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = turn.conversationId,
                sourceFirstSequence = 7L,
                sourceLastSequence = 8L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "d".repeat(64),
                expectedContextStateRevision = interruptedSnapshot.contextState.revision,
            )
            val afterRestart = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot("project-1", turn.conversationId),
            )
            assertEquals(
                MobileConversationCheckpointStatus.FAILED,
                afterRestart.checkpoints.first { it.id == interrupted.checkpoint.id }.status,
            )
            assertEquals(secondReady.checkpoint.id, afterRestart.contextState.activeCheckpointId)
        }
    }

    @Test
    fun `author quote rollup keeps a tombstone in audit but removes it from active rendering`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val firstTurn = store.beginTurn("project-1", null, "永远称呼我为船长")
            store.finishTurn("project-1", firstTurn, "已记录", "completed", emptyList())
            val firstSnapshot = assertNotNull(store.snapshot("project-1", firstTurn.conversationId))
            val firstAttempt = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = firstTurn.conversationId,
                sourceFirstSequence = 1L,
                sourceLastSequence = 2L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "e".repeat(64),
                expectedContextStateRevision = firstSnapshot.contextState.revision,
            )
            val sourceUser = firstSnapshot.messages.first()
            val firstReady = store.publishCheckpoint(
                projectId = "project-1",
                conversationId = firstTurn.conversationId,
                checkpointId = firstAttempt.checkpoint.id,
                expectedContextStateRevision = firstAttempt.contextStateRevision,
                semanticDraft = semanticDraft(
                    MobileCheckpointQuoteSelection(
                        sourceUser.id,
                        0,
                        sourceUser.content.length,
                        "active_constraint",
                    ),
                ),
                deterministicExecutionLedger = emptyList(),
                projectRefs = emptyList(),
                validation = buildJsonObject { put("capacity_assurance", "exact") },
                checkpointTokens = 10,
            )
            val activeQuote = firstReady.checkpoint.authorQuotes.single()
            assertFalse(activeQuote.superseded)

            val secondTurn = store.beginTurn("project-1", firstTurn.conversationId, "取消之前的称呼要求")
            store.finishTurn("project-1", secondTurn, "已取消", "completed", emptyList())
            val secondSnapshot = assertNotNull(store.snapshot("project-1", firstTurn.conversationId))
            val secondAttempt = store.beginCheckpoint(
                projectId = "project-1",
                conversationId = firstTurn.conversationId,
                sourceFirstSequence = 3L,
                sourceLastSequence = 4L,
                modelBinding = buildJsonObject { put("model_name", "test-model") },
                modelBindingFingerprint = "f".repeat(64),
                expectedContextStateRevision = secondSnapshot.contextState.revision,
            )
            store.publishCheckpoint(
                projectId = "project-1",
                conversationId = firstTurn.conversationId,
                checkpointId = secondAttempt.checkpoint.id,
                expectedContextStateRevision = secondAttempt.contextStateRevision,
                semanticDraft = MobileCheckpointSemanticDraft(
                    semanticNavigation = MobileConversationCheckpoint.emptySemanticNavigation(),
                    quoteSelections = emptyList(),
                    priorAuthorQuoteStates = listOf(
                        MobilePriorAuthorQuoteDecision(
                            messageId = activeQuote.messageId,
                            startChar = activeQuote.startChar,
                            endChar = activeQuote.endChar,
                            quoteSha256 = activeQuote.quoteSha256,
                            status = "superseded",
                        ),
                    ),
                ),
                deterministicExecutionLedger = emptyList(),
                projectRefs = emptyList(),
                validation = buildJsonObject { put("capacity_assurance", "exact") },
                checkpointTokens = 10,
            )
            val currentTurn = store.beginTurn("project-1", firstTurn.conversationId, "继续")
            val current = assertNotNull(store.snapshot("project-1", firstTurn.conversationId))
            val segments = current.activeCheckpointSegments
            assertEquals(listOf(false, true), segments.map { it.authorQuotes.single().superseded })

            val binding = MobileGenerationModelBinding(
                taskType = "assistant",
                provider = "android_direct_api",
                modelName = "test-model",
                normalizedModel = "test-model",
                protocol = "chat_completions",
                contextWindowTokens = 100_000,
                maxOutputTokens = 2_000,
                tokenCounterId = MobileUtf8ByteTokenCounter.counterId,
                capacityAssurance = MobileUtf8ByteTokenCounter.assurance,
                promptContractHash = "prompt",
                toolSchemaHash = "tools",
                configFingerprint = "config",
            )
            val budget = buildMobileRequestBudget(
                binding = binding,
                counter = MobileUtf8ByteTokenCounter,
                components = MobileRequestTokenComponents(currentUserTokens = 10),
                safetyMarginTokens = 1_000,
            )
            val frame = current.assembleContextFrame(
                turnContext = currentTurn,
                modelBinding = binding,
                systemContract = MobileSystemContract("a".repeat(64), "b".repeat(64)),
                recentExactTurns = emptyList(),
                currentTurnLedger = emptyList(),
                pendingToolTransactions = emptyList(),
                budget = budget,
            )
            val rendered = renderMobileContextFrame(frame, "system")
            assertFalse(rendered.messages.joinToString().contains("永远称呼我为船长"))
        }
    }

    @Test
    fun `gateway transcript sync batches one hundred turns and persists its exact cursor`() = runBlocking {
        withTemporaryDirectory { directory ->
            directory.resolve("project-1.json").writeText(
                legacyArchive(conversationCount = 1, messageCount = 202),
            )
            val store = MobileAssistantConversationStore(directory)
            val firstSnapshot = assertNotNull(store.prepareTranscriptSync("project-1", "conversation-0"))
            val first = assertNotNull(firstSnapshot.nextTranscriptImportRequest())

            assertEquals(200, first.messages.size)
            assertEquals(1L, first.messages.first().sequenceNo)
            assertEquals(200L, first.transcriptRevision)
            assertEquals("conversation-0", first.clientConversationId)
            assertNull(first.serverConversationId)
            assertEquals("completed", first.messages.first().status)

            val firstUpdate = store.recordTranscriptImportReceipt(
                projectId = "project-1",
                conversationId = firstSnapshot.conversationId,
                request = first,
                receipt = MobileTranscriptImportReceipt(
                    conversationId = "server-conversation-1",
                    transcriptRevision = 200L,
                    appliedRevision = 200L,
                    importedMessageCount = 200,
                    idempotent = false,
                ),
                expectedReplicaRevision = firstSnapshot.replicaState.revision,
            )
            assertEquals(200L, firstUpdate.replicaState.confirmedSourceRevision)

            val restored = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot(
                    "project-1",
                    firstSnapshot.conversationId,
                ),
            )
            val second = assertNotNull(restored.nextTranscriptImportRequest())
            assertEquals(listOf(201L, 202L), second.messages.map { it.sequenceNo })
            assertNull(second.clientConversationId)
            assertEquals("server-conversation-1", second.serverConversationId)

            store.recordTranscriptImportReceipt(
                projectId = "project-1",
                conversationId = firstSnapshot.conversationId,
                request = second,
                receipt = MobileTranscriptImportReceipt(
                    conversationId = "server-conversation-1",
                    transcriptRevision = 202L,
                    appliedRevision = 202L,
                    importedMessageCount = 2,
                    idempotent = false,
                ),
                expectedReplicaRevision = restored.replicaState.revision,
            )
            val finished = assertNotNull(store.snapshot("project-1", firstSnapshot.conversationId))
            assertNull(finished.nextTranscriptImportRequest())
            assertEquals(202, finished.messages.size)
        }
    }

    @Test
    fun `corrupt transcript is reported and never treated as an empty archive`() = runBlocking {
        withTemporaryDirectory { directory ->
            val target = directory.resolve("project-1.json")
            target.writeText("{broken")
            val store = MobileAssistantConversationStore(directory)

            assertFailsWith<MobileConversationStorageException> { store.conversations("project-1") }
            assertEquals("{broken", target.readText())
        }
    }

    @Test
    fun `schema two rejects sequence gaps duplicates ordering and revision cursor drift`() = runBlocking {
        withTemporaryDirectory { directory ->
            val store = MobileAssistantConversationStore(directory)
            val turn = store.beginTurn("project-1", null, "检查完整性")
            store.finishTurn("project-1", turn, "已完成", "completed", emptyList())
            val target = directory.resolve("project-1.json")
            val valid = target.readText()
            val corruptions: List<Pair<String, (JsonObject) -> JsonObject>> = listOf(
                "sequence gap" to { conversation ->
                    conversation.withMessageSequences(listOf(1L, 3L))
                },
                "duplicate sequence" to { conversation ->
                    conversation.withMessageSequences(listOf(1L, 1L))
                },
                "out of order sequence" to { conversation ->
                    conversation.withMessageSequences(listOf(2L, 1L))
                },
                "stale transcript revision" to { conversation ->
                    JsonObject(conversation.toMutableMap().apply {
                        put("transcript_revision", JsonPrimitive(1L))
                    })
                },
                "incorrect next sequence" to { conversation ->
                    JsonObject(conversation.toMutableMap().apply {
                        put("next_sequence_no", JsonPrimitive(4L))
                    })
                },
            )

            corruptions.forEach { (label, corrupt) ->
                target.writeText(rewriteFirstConversation(valid, corrupt))
                assertFailsWith<MobileConversationStorageException>(label) {
                    MobileAssistantConversationStore(directory).snapshot(
                        "project-1",
                        turn.conversationId,
                    )
                }
                target.writeText(valid)
            }

            val roundTripped = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot("project-1", turn.conversationId),
            )
            assertEquals(listOf(1L, 2L), roundTripped.messages.map { it.sequenceNo })
            assertEquals(2L, roundTripped.transcriptRevision)
        }
    }

    @Test
    fun `empty schema two conversation requires initial revision and sequence cursor`() = runBlocking {
        withTemporaryDirectory { directory ->
            val target = directory.resolve("project-1.json")
            val valid = buildJsonObject {
                put("schema_version", MobileConversationContextSchema.STORAGE_VERSION)
                put("project_id", "project-1")
                put("conversations", buildJsonArray {
                    add(buildJsonObject {
                        put("id", "empty-conversation")
                        put("conversation_kind", "workspace")
                        put("title", "空会话")
                        put("created_at", "2026-01-01T00:00:00Z")
                        put("updated_at", "2026-01-01T00:00:00Z")
                        put("transcript_revision", 0L)
                        put("next_sequence_no", 1L)
                        put("messages", JsonArray(emptyList()))
                        put("context_state", buildJsonObject {})
                        put("checkpoints", JsonArray(emptyList()))
                        put("replica_state", buildJsonObject {})
                        put("tool_runtime_states", JsonArray(emptyList()))
                    })
                })
            }.toString()
            target.writeText(valid)
            val restored = assertNotNull(
                MobileAssistantConversationStore(directory).snapshot("project-1", "empty-conversation"),
            )
            assertTrue(restored.messages.isEmpty())
            assertEquals(0L, restored.transcriptRevision)

            listOf(
                "nonzero empty revision" to { conversation: JsonObject ->
                    JsonObject(conversation.toMutableMap().apply {
                        put("transcript_revision", JsonPrimitive(1L))
                    })
                },
                "advanced empty next sequence" to { conversation: JsonObject ->
                    JsonObject(conversation.toMutableMap().apply {
                        put("next_sequence_no", JsonPrimitive(2L))
                    })
                },
            ).forEach { (label, corrupt) ->
                target.writeText(rewriteFirstConversation(valid, corrupt))
                assertFailsWith<MobileConversationStorageException>(label) {
                    MobileAssistantConversationStore(directory).snapshot(
                        "project-1",
                        "empty-conversation",
                    )
                }
                target.writeText(valid)
            }
        }
    }

    private fun semanticDraft(vararg quotes: MobileCheckpointQuoteSelection) =
        MobileCheckpointSemanticDraft(
            semanticNavigation = MobileConversationCheckpoint.emptySemanticNavigation(),
            quoteSelections = quotes.toList(),
        )

    private fun legacyArchive(conversationCount: Int, messageCount: Int): String = buildJsonObject {
        put("schema_version", 1)
        put("conversations", buildJsonArray {
            repeat(conversationCount) { conversationIndex ->
                add(buildJsonObject {
                    put("id", "conversation-$conversationIndex")
                    put("title", "会话 $conversationIndex")
                    put("created_at", "2026-01-01T00:00:00Z")
                    put("updated_at", "2026-01-01T00:00:00Z")
                    put("messages", buildJsonArray {
                        repeat(messageCount) { messageIndex ->
                            add(buildJsonObject {
                                put("id", "message-$conversationIndex-$messageIndex")
                                put("role", if (messageIndex % 2 == 0) "user" else "assistant")
                                put("content", "消息 $messageIndex")
                                put("status", "completed")
                                put("created_at", "2026-01-01T00:00:00Z")
                                put("tool_logs", kotlinx.serialization.json.JsonArray(emptyList()))
                            })
                        }
                    })
                })
            }
        })
    }.toString()

    private fun rewriteFirstConversation(
        raw: String,
        transform: (JsonObject) -> JsonObject,
    ): String {
        val root = Json.parseToJsonElement(raw) as JsonObject
        val conversations = root["conversations"] as JsonArray
        val first = conversations.first() as JsonObject
        return JsonObject(root.toMutableMap().apply {
            put(
                "conversations",
                JsonArray(listOf(transform(first)) + conversations.drop(1)),
            )
        }).toString()
    }

    private fun JsonObject.withMessageSequences(sequenceNumbers: List<Long>): JsonObject {
        val messages = this["messages"] as JsonArray
        require(sequenceNumbers.size == messages.size)
        return JsonObject(toMutableMap().apply {
            put("messages", JsonArray(messages.mapIndexed { index, raw ->
                JsonObject((raw as JsonObject).toMutableMap().apply {
                    put("sequence_no", JsonPrimitive(sequenceNumbers[index]))
                })
            }))
        })
    }

    private inline fun withTemporaryDirectory(block: (java.io.File) -> Unit) {
        val directory = Files.createTempDirectory("siming-mobile-assistant").toFile()
        try {
            block(directory)
        } finally {
            directory.deleteRecursively()
        }
    }
}
