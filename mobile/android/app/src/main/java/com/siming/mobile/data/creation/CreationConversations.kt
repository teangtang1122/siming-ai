package com.siming.mobile.data.creation

import kotlinx.serialization.json.*

/** Conversation projections never contain business state. Canonical transcripts own history. */
internal object CreationConversations {
    const val ACTIVE = "active_agent_conversation_id"
    const val INDEX = "agent_conversations"
    private val projectionKeys = setOf("agent_turns", "agent_conversation_id", "agent_context_state", "agent_checkpoint_detail")
    private fun draft(session: JsonObject) = session["draft"] as? JsonObject ?: JsonObject(emptyMap())
    fun active(session: JsonObject) = draft(session)[ACTIVE]?.jsonPrimitive?.content.orEmpty()
    fun entries(session: JsonObject) = (draft(session)[INDEX] as? JsonArray).orEmpty().map { it.jsonObject }

    fun capture(session: JsonObject): JsonObject {
        val id = active(session)
        if (id.isBlank()) return session
        val old = entries(session)
        require(old.any { it["id"]?.jsonPrimitive?.content == id }) { "当前立项对话没有登记" }
        val updated = old.map { entry ->
            if (entry["id"]?.jsonPrimitive?.content != id) entry else JsonObject(entry.toMutableMap().apply {
                put("projection", JsonObject(draft(session).filterKeys { it in projectionKeys }))
            })
        }
        return withDraft(session, draft(session).toMutableMap().apply { put(INDEX, JsonArray(updated)) })
    }

    /** Called once after the legacy canonical archive has been durably imported. */
    fun migrate(session: JsonObject, id: String): JsonObject {
        if (active(session).isNotBlank()) return session
        return capture(withDraft(session, draft(session).toMutableMap().apply {
            put(ACTIVE, JsonPrimitive(id))
            put(INDEX, JsonArray(listOf(buildJsonObject {
                put("id", id); put("title", "原有对话"); put("projection", buildJsonObject {})
            })))
        }))
    }

    fun add(session: JsonObject, id: String, gateway: Boolean): JsonObject {
        require(entries(session).none { it["id"]?.jsonPrimitive?.content == id })
        val captured = capture(session)
        val entry = buildJsonObject {
            put("id", id); put("title", "对话 ${entries(session).size + 1}")
            put("projection", buildJsonObject {
                put("agent_turns", JsonArray(emptyList()))
                if (gateway) put("agent_conversation_id", id)
            })
        }
        return select(withDraft(captured, draft(captured).toMutableMap().apply {
            put(INDEX, JsonArray(entries(captured) + entry))
        }), id)
    }

    fun select(session: JsonObject, id: String): JsonObject {
        val captured = capture(session)
        val entry = entries(captured).singleOrNull { it["id"]?.jsonPrimitive?.content == id }
            ?: error("对话不属于当前立项")
        return withDraft(captured, draft(captured).toMutableMap().apply {
            projectionKeys.forEach(::remove)
            putAll(entry["projection"]!!.jsonObject)
            put(ACTIVE, JsonPrimitive(id))
        })
    }

    fun requireWriteTarget(previous: JsonObject, incoming: JsonObject, allowSwitch: Boolean = false) {
        if (!allowSwitch && active(previous).isNotBlank() && active(incoming).isNotBlank() &&
            active(previous) != active(incoming)) {
            throw com.siming.mobile.data.agent.MobileConversationContextException(
                com.siming.mobile.data.agent.MobileConversationContextErrorCode.SOURCE_CHANGED,
                "立项对话已切换，迟到结果未写入当前对话")
        }
    }

    fun preserveIndex(session: JsonObject, previous: JsonObject): JsonObject = withDraft(session,
        draft(session).toMutableMap().apply {
            if (ACTIVE !in this && active(previous).isNotBlank()) {
                put(ACTIVE, draft(previous).getValue(ACTIVE)); put(INDEX, draft(previous).getValue(INDEX))
                projectionKeys.forEach { key -> if (key !in this) draft(previous)[key]?.let { put(key, it) } }
            }
        })

    private fun withDraft(session: JsonObject, value: Map<String, JsonElement>) = JsonObject(
        session.toMutableMap().apply { put("draft", JsonObject(value)) },
    )
}
