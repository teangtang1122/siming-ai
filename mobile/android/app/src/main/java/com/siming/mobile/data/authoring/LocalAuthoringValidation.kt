package com.siming.mobile.data.authoring

import com.siming.mobile.data.network.PcAuthoringContract
import com.siming.mobile.data.network.PcFieldKind
import kotlinx.serialization.json.*

/** Structural authoring limits from the shared desktop request schemas. */
internal fun validateAuthoringFields(type: String, payload: JsonObject) {
    for (field in PcAuthoringContract.fields(type)) {
        val value = payload[field.key]?.takeUnless { it == JsonNull } ?: continue
        val valid = when (field.kind) {
            PcFieldKind.Integer, PcFieldKind.NullableInteger -> value is JsonPrimitive && !value.isString && value.intOrNull != null
            PcFieldKind.Boolean -> value is JsonPrimitive && !value.isString && value.booleanOrNull != null
            PcFieldKind.StringArray -> value is JsonArray && value.all { it is JsonPrimitive && it.isString }
            PcFieldKind.JsonArray -> value is JsonArray
            PcFieldKind.JsonObject -> value is JsonObject
            else -> value is JsonPrimitive && value.isString
        }
        require(valid) { "${field.key} 的数据格式不符合资料契约" }
    }
    fun length(field: String, maximum: Int, minimum: Int = 0) {
        val value = payload[field]?.takeUnless { it == JsonNull } ?: return
        val text = value.jsonPrimitive.content
        require(text.codePointCount(0, text.length) in minimum..maximum) { "$field 必须为 $minimum 至 $maximum 个字符" }
    }
    fun nonnegative(field: String) {
        val value = payload[field]?.takeUnless { it == JsonNull } ?: return
        require(value.jsonPrimitive.int >= 0) { "$field 不能为负数" }
    }
    if (type in setOf("project", "chapter", "outline", "world")) length("title", 200, 1)
    when (type) {
        "project" -> nonnegative("daily_word_goal")
        "character" -> {
            length("name", 100, 1); length("role_type", 50); length("age", 100)
            length("life_status", 50); length("current_location", 200); length("realm_or_level", 200)
        }
        "character_ai_config" -> {
            length("tone_style", 100); length("verbosity", 50); length("emotion_tendency", 100); length("model_override", 512)
        }
        "character_relation" -> length("relationship_type", 100, 1)
        "outline" -> {
            require(payload.text("node_type") in setOf("volume", "chapter", "section")) { "大纲节点类型无效" }
            require(payload.text("status") in setOf("pending", "in_progress", "completed")) { "大纲状态无效" }
            nonnegative("sort_order")
            (payload["characters"] as? JsonArray).orEmpty().forEach { value ->
                require(value is JsonObject && value["character_id"] is JsonPrimitive && value.getValue("character_id").jsonPrimitive.isString) { "大纲角色关联必须提供真实角色 ID" }
                val role = (value as JsonObject)["role_in_scene"]?.takeUnless { it == JsonNull }
                require(role == null || role is JsonPrimitive && role.isString && role.content.codePointCount(0, role.content.length) <= 50) { "场景角色说明不能超过 50 个字符" }
            }
        }
        "world" -> {
            require(payload.text("dimension") in setOf("geography", "history", "factions", "power_system", "races", "culture")) { "世界观维度无效" }
            require(payload.text("content").isNotEmpty()) { "世界观内容不能为空" }
            nonnegative("sort_order")
        }
    }
}
