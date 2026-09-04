package com.siming.mobile.data.creation

import com.siming.mobile.data.agent.MobileArchivedConversationTurn
import com.siming.mobile.data.agent.MobileConversationSnapshot
import com.siming.mobile.data.agent.MobileTranscriptMessage
import com.siming.mobile.data.agent.mobileCanonicalSha256
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** UI/audit projection; standalone recovery and model history live in the canonical transcript store. */
internal object CreationAgentTurnRecords {
    const val SCHEMA = "creation_agent_turn.v1"
    const val STORAGE_KEY = "agent_turns"
    private const val GATEWAY_CONVERSATION_KEY = "agent_conversation_id"
    private const val CONTEXT_STATE_KEY = "agent_context_state"
    private const val CHECKPOINT_DETAIL_KEY = "agent_checkpoint_detail"

    fun turns(session: JsonObject): List<JsonObject> =
        (session.objectValue("draft")[STORAGE_KEY] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }

    fun gatewayConversationId(session: JsonObject): String =
        session.objectValue("draft").string(GATEWAY_CONVERSATION_KEY)

    fun contextState(session: JsonObject): JsonObject? =
        session.objectValue("draft")[CONTEXT_STATE_KEY] as? JsonObject

    fun checkpointDetail(session: JsonObject): JsonObject? =
        session.objectValue("draft")[CHECKPOINT_DETAIL_KEY] as? JsonObject

    /** Keep local routing/conversation storage out of the business snapshot sent to the model. */
    fun agentVisibleDraft(session: JsonObject): JsonObject = JsonObject(
        session.objectValue("draft").filterKeys { it !in INTERNAL_DRAFT_KEYS },
    )

    fun pending(
        userContent: String,
        id: String = UUID.randomUUID().toString(),
        createdAt: String = Instant.now().toString(),
    ): JsonObject = buildJsonObject {
        put("schema", SCHEMA)
        put("id", id)
        put("created_at", createdAt)
        put("updated_at", createdAt)
        put("status", "running")
        put("user_content", userContent)
        put("reply", "")
        put("replayable", false)
        put("model_messages", JsonArray(emptyList()))
        put("tool_results", JsonArray(emptyList()))
    }

    fun complete(
        pending: JsonObject,
        reply: String,
        modelMessages: JsonArray,
        toolResults: JsonArray,
        replayable: Boolean,
        status: String = "completed",
        executionRoute: String,
        createdProjectId: String? = null,
        progressEvents: JsonArray = JsonArray(emptyList()),
        promptMetrics: JsonArray = JsonArray(emptyList()),
    ): JsonObject = JsonObject(pending.toMutableMap().apply {
        put("updated_at", JsonPrimitive(Instant.now().toString()))
        put("status", JsonPrimitive(status))
        put("reply", JsonPrimitive(reply))
        put("replayable", JsonPrimitive(replayable && status == "completed"))
        put("model_messages", modelMessages)
        put("tool_results", toolResults)
        put("execution_route", JsonPrimitive(executionRoute))
        put("progress_events", progressEvents)
        put("prompt_metrics", promptMetrics)
        createdProjectId?.takeIf(String::isNotBlank)?.let {
            put("created_project_id", JsonPrimitive(it))
        }
    })

    fun fail(
        pending: JsonObject,
        detail: String,
        status: String = "error",
    ): JsonObject = complete(
        pending = pending,
        reply = detail.ifBlank { "本轮立项处理失败" },
        modelMessages = JsonArray(emptyList()),
        toolResults = JsonArray(emptyList()),
        replayable = false,
        status = status,
        executionRoute = "error",
    )

    fun replace(turns: List<JsonObject>, replacement: JsonObject): List<JsonObject> {
        val id = replacement.string("id")
        val found = turns.any { it.string("id") == id }
        val updated = if (found) {
            turns.map { if (it.string("id") == id) replacement else it }
        } else {
            turns + replacement
        }
        return updated
    }

    fun withTurns(
        session: JsonObject,
        turns: List<JsonObject>,
        gatewayConversationId: String? = null,
    ): JsonObject {
        val draft = session.objectValue("draft").toMutableMap()
        draft.remove("agent_history")
        draft[STORAGE_KEY] = JsonArray(turns)
        gatewayConversationId?.takeIf(String::isNotBlank)?.let {
            draft[GATEWAY_CONVERSATION_KEY] = JsonPrimitive(it)
        }
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

    /**
     * Persists the safe public Gateway projection so reopening a creation chat
     * can show the exact source range, author quotes, and execution receipts.
     * A missing detail object remains distinguishable from an empty validated
     * detail and is never rendered as "no quotes/receipts".
     */
    fun withConversationContext(
        session: JsonObject,
        state: JsonObject,
        checkpointDetail: JsonObject?,
    ): JsonObject {
        val draft = session.objectValue("draft").toMutableMap().apply {
            put(CONTEXT_STATE_KEY, state)
            if (checkpointDetail == null) remove(CHECKPOINT_DETAIL_KEY)
            else put(CHECKPOINT_DETAIL_KEY, checkpointDetail)
        }
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

    /** Merge a refreshed PC business snapshot without losing Android audit cursors. */
    fun mergeRemoteSession(remote: JsonObject, local: JsonObject): JsonObject {
        val conversationId = gatewayConversationId(local)
        var merged = withTurns(
            remote,
            turns(local),
            gatewayConversationId = conversationId,
        )
        contextState(local)?.let { state ->
            merged = withConversationContext(merged, state, checkpointDetail(local))
        }
        return merged
    }

    fun displayMessages(session: JsonObject): List<JsonObject> = turns(session).flatMap { turn ->
        buildList {
            val turnId = turn.string("id")
            val createdAt = turn.string("created_at")
            val userContent = turn.string("user_content")
            if (userContent.isNotBlank()) add(displayMessage("$turnId:user", "user", userContent, createdAt))
            val reply = turn.string("reply")
            if (reply.isNotBlank()) add(displayMessage(
                "$turnId:assistant",
                "assistant",
                reply,
                turn.string("updated_at"),
                turn["progress_events"] as? JsonArray ?: JsonArray(emptyList()),
            ))
        }
    }

    /**
     * Projects the complete closed creation audit into the shared canonical
     * transcript. No fixed turn count is authoritative; checkpoint planning
     * decides the dynamic exact tail at request time.
     */
    fun archivedTurns(session: JsonObject): List<MobileArchivedConversationTurn> = turns(session)
        .mapNotNull { turn ->
            val status = turn.string("status")
            val user = turn.string("user_content")
            val reply = turn.string("reply")
            if (turn.string("schema") != SCHEMA ||
                status !in setOf("completed", "error", "aborted", "cancelled") ||
                user.isBlank() || reply.isBlank()
            ) {
                return@mapNotNull null
            }
            MobileArchivedConversationTurn(
                turnId = turn.string("id"),
                userContent = user,
                assistantContent = reply,
                status = status,
                createdAt = turn.string("created_at").ifBlank { Instant.EPOCH.toString() },
                updatedAt = turn.string("updated_at").ifBlank {
                    turn.string("created_at").ifBlank { Instant.EPOCH.toString() }
                },
            )
        }

    /**
     * Rebuilds the standalone creation audit as a projection of the canonical transcript.
     * The shared transcript owns recovery; the session copy only retains richer audit data
     * when its exact turn id, author input, outcome, and status already agree.
     */
    fun reconcileWithCanonicalConversation(
        session: JsonObject,
        conversation: MobileConversationSnapshot,
    ): JsonObject {
        val sessionId = session.string("id")
        require(conversation.conversationKind == "creation" &&
            conversation.creationSessionId == sessionId
        ) { "canonical creation conversation 归属不匹配" }
        val existingTurns = turns(session)
        require(existingTurns.all { it.string("id").isNotBlank() } &&
            existingTurns.map { it.string("id") }.distinct().size == existingTurns.size
        ) { "creation audit 回合 ID 无效或重复" }
        val existingById = existingTurns.associateBy { it.string("id") }
        val canonicalTurnIds = conversation.turns.mapTo(linkedSetOf()) { it.turnId }
        require(existingTurns.all { it.string("id") in canonicalTurnIds }) {
            "creation audit 包含 canonical transcript 不存在的回合"
        }
        val reconciled = conversation.turns.map { canonicalTurn ->
            val user = canonicalTurn.messages.singleOrNull { it.role == "user" }
                ?: error("canonical creation 回合缺少唯一作者消息")
            val assistant = canonicalTurn.messages.singleOrNull { it.role == "assistant" }
            require(canonicalTurn.messages.size == if (assistant == null) 1 else 2) {
                "canonical creation 回合消息结构无效"
            }
            val existing = existingById[canonicalTurn.turnId]
            when {
                assistant == null && existing == null -> pending(
                    userContent = user.content,
                    id = canonicalTurn.turnId,
                    createdAt = user.createdAt,
                )
                assistant == null && existing.matchesCanonicalOpenTurn(user.content) ->
                    requireNotNull(existing)
                assistant == null -> error("开放 canonical 回合与 creation audit 冲突")
                existing.matchesCanonicalClosedTurn(
                    userContent = user.content,
                    assistantContent = assistant.content,
                    status = assistant.status,
                ) -> requireNotNull(existing)
                existing != null &&
                    !existing.matchesCanonicalRecoverableTurn(user.content) -> error(
                    "已闭合 creation audit 与 canonical transcript 冲突",
                )
                else -> canonicalAuditTurn(canonicalTurn.turnId, user, assistant)
            }
        }
        return if (existingTurns == reconciled) session else withTurns(session, reconciled)
    }

    private fun canonicalAuditTurn(
        turnId: String,
        user: MobileTranscriptMessage,
        assistant: MobileTranscriptMessage,
    ): JsonObject = buildJsonObject {
        put("schema", SCHEMA)
        put("id", turnId)
        put("created_at", user.createdAt)
        put("updated_at", assistant.createdAt)
        put("status", assistant.status)
        put("user_content", user.content)
        put("reply", assistant.content)
        put("replayable", false)
        put("model_messages", JsonArray(emptyList()))
        put("tool_results", JsonArray(assistant.toolLogs.map(::decodeCanonicalToolLog)))
        put("execution_route", "device")
        put("progress_events", JsonArray(emptyList()))
        put("prompt_metrics", JsonArray(emptyList()))
    }

    /** Fallback recovery when no standalone canonical transcript is available. */
    fun recoverInterruptedTurns(session: JsonObject): JsonObject {
        val existing = turns(session)
        if (existing.none { it.string("status") == "running" }) return session
        return withTurns(
            session,
            existing.map { turn ->
                if (turn.string("status") != "running") turn else complete(
                    pending = turn,
                    reply = INTERRUPTED_TURN_RECEIPT,
                    modelMessages = JsonArray(emptyList()),
                    toolResults = JsonArray(emptyList()),
                    replayable = false,
                    status = "aborted",
                    executionRoute = "interrupted",
                )
            },
        )
    }

    /** One-time data migration; old text bubbles never enter model replay. */
    fun migrateLegacyHistory(session: JsonObject): JsonObject {
        val draft = session.objectValue("draft")
        if (draft[STORAGE_KEY] is JsonArray) {
            if (draft["agent_history"] !is JsonArray) return session
            val cleaned = draft.toMutableMap().apply { remove("agent_history") }
            return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(cleaned)) })
        }
        if (draft["agent_history"] !is JsonArray) return session
        val legacy = (draft["agent_history"] as JsonArray).mapNotNull { it as? JsonObject }
        val migrated = mutableListOf<JsonObject>()
        val sessionId = session.string("id")
        var pendingUser: Pair<Int, JsonObject>? = null
        legacy.forEachIndexed { index, message ->
            when (message.string("role")) {
                "user" -> {
                    pendingUser?.let { (userIndex, user) ->
                        migrated += migratedTurn(sessionId, userIndex, user, null, null)
                    }
                    pendingUser = index to message
                }
                "assistant" -> {
                    pendingUser?.let { (userIndex, user) ->
                        migrated += migratedTurn(sessionId, userIndex, user, index, message)
                    }
                    pendingUser = null
                }
            }
        }
        pendingUser?.let { (userIndex, user) ->
            migrated += migratedTurn(sessionId, userIndex, user, null, null)
        }
        return withTurns(session, migrated)
    }

    private fun migratedTurn(
        sessionId: String,
        userIndex: Int,
        user: JsonObject,
        assistantIndex: Int?,
        assistant: JsonObject?,
    ): JsonObject {
        val interrupted = assistant == null
        val createdAt = user.string("created_at").ifBlank { Instant.EPOCH.toString() }
        val updatedAt = assistant?.string("created_at").orEmpty().ifBlank { createdAt }
        val reply = assistant?.string("content") ?: INTERRUPTED_TURN_RECEIPT
        val turnId = "legacy-turn-${mobileCanonicalSha256(buildJsonObject {
            put("schema", LEGACY_TURN_ID_SCHEMA)
            put("session_id", sessionId)
            put("user_index", userIndex)
            put("user_id", user.string("id"))
            put("user_content", user.string("content"))
            put("assistant_index", assistantIndex ?: -1)
            put("assistant_id", assistant?.string("id").orEmpty())
            put("assistant_content", assistant?.string("content").orEmpty())
        }).take(24)}"
        return buildJsonObject {
            put("schema", SCHEMA)
            put("id", turnId)
            put("created_at", createdAt)
            put("updated_at", updatedAt)
            put("status", if (interrupted) "aborted" else "completed")
            put("user_content", user.string("content"))
            put("reply", reply)
            put("replayable", false)
            put("model_messages", JsonArray(emptyList()))
            put("tool_results", JsonArray(emptyList()))
            put("execution_route", if (interrupted) "interrupted" else "migrated")
            put("progress_events", JsonArray(emptyList()))
            put("prompt_metrics", JsonArray(emptyList()))
        }
    }

    private fun displayMessage(
        id: String,
        role: String,
        content: String,
        createdAt: String,
        progressEvents: JsonArray = JsonArray(emptyList()),
    ) = buildJsonObject {
        put("id", id)
        put("role", role)
        put("content", content)
        put("created_at", createdAt)
        if (progressEvents.isNotEmpty()) put("progress_events", progressEvents)
    }

    private fun JsonObject?.matchesCanonicalOpenTurn(userContent: String): Boolean =
        this != null && string("schema") == SCHEMA && string("status") == "running" &&
            string("user_content") == userContent && string("reply").isBlank()

    private fun JsonObject?.matchesCanonicalClosedTurn(
        userContent: String,
        assistantContent: String,
        status: String,
    ): Boolean = this != null && string("schema") == SCHEMA &&
        string("user_content") == userContent && string("reply") == assistantContent &&
        string("status") == status

    private fun JsonObject.matchesCanonicalRecoverableTurn(userContent: String): Boolean =
        string("schema") == SCHEMA && string("user_content") == userContent && (
            string("status") == "running" && string("reply").isBlank() ||
                string("status") == "aborted" &&
                string("reply") == INTERRUPTED_TURN_RECEIPT &&
                string("execution_route") == "interrupted" &&
                get("replayable") == JsonPrimitive(false) &&
                (get("model_messages") as? JsonArray)?.isEmpty() == true &&
                (get("tool_results") as? JsonArray)?.isEmpty() == true &&
                (get("progress_events") as? JsonArray)?.isEmpty() == true &&
                (get("prompt_metrics") as? JsonArray)?.isEmpty() == true
            )

    private fun decodeCanonicalToolLog(value: String) = Json.parseToJsonElement(value)

    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

    private val INTERNAL_DRAFT_KEYS = setOf(
        STORAGE_KEY,
        GATEWAY_CONVERSATION_KEY,
        CONTEXT_STATE_KEY,
        CHECKPOINT_DETAIL_KEY,
        "agent_history",
        "execution_route",
        "execution_host",
    )
    private const val INTERRUPTED_TURN_RECEIPT =
        "上一轮任务未完成，已在新任务开始前安全终止。"
    private const val LEGACY_TURN_ID_SCHEMA = "creation_agent_legacy_turn_id.v1"
}
