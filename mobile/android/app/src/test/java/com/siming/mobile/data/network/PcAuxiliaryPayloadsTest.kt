package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class PcAuxiliaryPayloadsTest {
    @Test
    fun `legacy character relation aliases become canonical from and to`() {
        val payload = PcApiPayloads.syncMutation(
            entityType = "character_relation",
            source = buildJsonObject {
                put("character_a_id", "character-a")
                put("character_b_id", "character-b")
                put("relationship_type", "父女")
                put("description", "互相信任")
                put("created_at", "2000-01-01T00:00:00Z")
            },
            projectId = "project-1",
            entityId = "relation-1",
        )

        assertEquals("character_relationship", payload.getValue("_record_type").jsonPrimitive.content)
        assertEquals("character-a", payload.getValue("from").jsonPrimitive.content)
        assertEquals("character-b", payload.getValue("to").jsonPrimitive.content)
        assertEquals("父女", payload.getValue("relationship_type").jsonPrimitive.content)
        assertFalse("character_a_id" in payload)
        assertFalse("character_b_id" in payload)
        assertFalse("created_at" in payload)
    }

    @Test
    fun `character AI config keeps catchphrases as PC string array`() {
        val payload = PcApiPayloads.syncMutation(
            entityType = "character_ai_config",
            source = JsonObject(
                mapOf(
                    "character_id" to JsonPrimitive("character-1"),
                    "tone_style" to JsonPrimitive("冷静"),
                    "catchphrases" to JsonPrimitive("先验证\n数据说话，别猜"),
                    "verbosity" to JsonPrimitive("brief"),
                    "updated_at" to JsonPrimitive("2000-01-01T00:00:00Z"),
                ),
            ),
            projectId = "project-1",
            entityId = "ai-config-1",
        )

        assertEquals("character_ai_config", payload.getValue("_record_type").jsonPrimitive.content)
        assertEquals(
            listOf("先验证", "数据说话", "别猜"),
            payload.getValue("catchphrases").jsonArray.map { it.jsonPrimitive.content },
        )
        assertFalse("updated_at" in payload)
    }

    @Test
    fun `world relation metadata stays a JSON object`() {
        val payload = PcApiPayloads.syncMutation(
            entityType = "world_relation",
            source = buildJsonObject {
                put("source_entry_id", "world-a")
                put("target_entry_id", "world-b")
                put("relation_type", "constrained_by")
                put("description", "宗门维持阵法")
                put("metadata_json", JsonPrimitive("{\"strength\":\"high\"}"))
            },
            projectId = "project-1",
            entityId = "world-relation-1",
        )

        assertEquals("world_relationship", payload.getValue("_record_type").jsonPrimitive.content)
        assertEquals(
            "high",
            payload.getValue("metadata_json").jsonObject.getValue("strength").jsonPrimitive.content,
        )
    }

    @Test
    fun `auxiliary contract keys remain explicit`() {
        assertEquals(
            setOf("from", "to", "relationship_type", "description"),
            PcAuthoringContract.writableKeys("character_relation"),
        )
        assertEquals(
            setOf(
                "character_id",
                "tone_style",
                "catchphrases",
                "verbosity",
                "emotion_tendency",
                "model_override",
                "custom_system_prompt",
            ),
            PcAuthoringContract.writableKeys("character_ai_config"),
        )
        assertEquals(
            setOf(
                "source_entry_id",
                "target_entry_id",
                "relation_type",
                "description",
                "metadata_json",
            ),
            PcAuthoringContract.writableKeys("world_relation"),
        )
    }
}
