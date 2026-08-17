package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class PcApiPayloadsTest {
    @Test
    fun `project payload converts stored tags and strips replica fields`() {
        val payload = PcApiPayloads.authoring(
            entityType = "project",
            source = buildJsonObject {
                put("id", "project-1")
                put("folder_path", "D:/private/workspace")
                put("title", "测试作品")
                put("tags", "[\"悬疑\",\"科幻\"]")
            },
            create = true,
        )

        assertEquals("测试作品", payload.getValue("title").jsonPrimitive.content)
        assertEquals(
            listOf("悬疑", "科幻"),
            payload.getValue("tags").jsonArray.map { it.jsonPrimitive.content },
        )
        assertFalse("id" in payload)
        assertFalse("folder_path" in payload)
    }

    @Test
    fun `character create and update use their exact PC fields`() {
        val source = buildJsonObject {
            put("id", "character-1")
            put("name", "周遥")
            put("abilities", JsonArray(listOf(JsonPrimitive("观察"))))
            put("profile", buildJsonObject { put("core_drive", "求证") })
            put("change_summary", "进入温室")
            put("current_version", 7)
        }

        val createPayload = PcApiPayloads.authoring("character", source, create = true)
        val updatePayload = PcApiPayloads.authoring("character", source, create = false)

        assertTrue("abilities" in createPayload)
        assertTrue("profile" in createPayload)
        assertFalse("change_summary" in createPayload)
        assertFalse("current_version" in createPayload)
        assertEquals("进入温室", updatePayload.getValue("change_summary").jsonPrimitive.content)
    }

    @Test
    fun `character editor strings normalize to PC arrays object and boolean`() {
        val source = JsonObject(
            mapOf(
                "name" to JsonPrimitive("陆糖"),
                "abilities" to JsonPrimitive("阵法\n推演，炼丹"),
                "aliases" to JsonPrimitive("糖糖、特昂糖"),
                "profile" to JsonPrimitive("{\"core_motivation\":\"保护家人\"}"),
                "is_evolution_tracked" to JsonPrimitive("false"),
            ),
        )

        val payload = PcApiPayloads.authoring("character", source, create = false)

        assertEquals(
            listOf("阵法", "推演", "炼丹"),
            payload.getValue("abilities").jsonArray.map { it.jsonPrimitive.content },
        )
        assertEquals(
            listOf("糖糖", "特昂糖"),
            payload.getValue("aliases").jsonArray.map { it.jsonPrimitive.content },
        )
        assertEquals(
            "保护家人",
            payload.getValue("profile").jsonObject.getValue("core_motivation").jsonPrimitive.content,
        )
        assertFalse(payload.getValue("is_evolution_tracked").jsonPrimitive.boolean)
    }

    @Test
    fun `character create defaults keep PC collection shapes`() {
        val payload = PcApiPayloads.authoring(
            "character",
            JsonObject(mapOf("name" to JsonPrimitive("陆景珩"))),
            create = true,
        )

        assertEquals(JsonArray(emptyList()), payload["abilities"])
        assertEquals(JsonArray(emptyList()), payload["aliases"])
        assertEquals(JsonObject(emptyMap()), payload["profile"])
    }

    @Test
    fun `chapter update and world create match PC request defaults`() {
        val chapter = PcApiPayloads.authoring(
            "chapter",
            buildJsonObject {
                put("title", "第一章")
                put("content", "正文")
                put("snapshot_count", 9)
            },
            create = false,
        )
        val world = PcApiPayloads.authoring(
            "world",
            buildJsonObject {
                put("dimension", "invalid")
                put("title", "温室")
                put("content", "封闭生态区")
            },
            create = true,
        )

        assertEquals("manual_save", chapter.getValue("trigger_type").jsonPrimitive.content)
        assertFalse("snapshot_count" in chapter)
        assertEquals("culture", world.getValue("dimension").jsonPrimitive.content)
        assertEquals(0, world.getValue("sort_order").jsonPrimitive.content.toInt())
    }
}
