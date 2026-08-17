package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
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
                put("short_sentences", "true")
                put("daily_word_goal", 8000)
            },
            create = true,
        )

        assertEquals("测试作品", payload.getValue("title").jsonPrimitive.content)
        assertEquals(
            listOf("悬疑", "科幻"),
            payload.getValue("tags").jsonArray.map { it.jsonPrimitive.content },
        )
        assertTrue(payload.getValue("short_sentences").jsonPrimitive.boolean)
        assertEquals(8000, payload.getValue("daily_word_goal").jsonPrimitive.content.toInt())
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
    fun `outline response aliases become PC update request fields`() {
        val source = buildJsonObject {
            put("id", "outline-1")
            put("title", "归墟待敌")
            put("node_type", "chapter")
            put("status", "pending")
            put("sort_order", 3000)
            put("metadata_json", buildJsonObject { put("hook", "裂隙亮起") })
            put(
                "linked_characters",
                JsonArray(
                    listOf(
                        buildJsonObject {
                            put("id", "character-1")
                            put("name", "陆糖")
                            put("role_in_scene", "protagonist")
                        },
                    ),
                ),
            )
            put("created_at", "2026-08-17T00:00:00")
        }

        val payload = PcApiPayloads.authoring("outline", source, create = false)

        assertEquals(
            "裂隙亮起",
            payload.getValue("metadata").jsonObject.getValue("hook").jsonPrimitive.content,
        )
        val link = payload.getValue("characters").jsonArray.single().jsonObject
        assertEquals("character-1", link.getValue("character_id").jsonPrimitive.content)
        assertEquals("protagonist", link.getValue("role_in_scene").jsonPrimitive.content)
        assertFalse("metadata_json" in payload)
        assertFalse("linked_characters" in payload)
        assertFalse("created_at" in payload)
    }

    @Test
    fun `offline chapter mutation strips every PC response only field`() {
        val payload = PcApiPayloads.syncMutation(
            entityType = "chapter",
            source = buildJsonObject {
                put("id", "chapter-1")
                put("project_id", "project-1")
                put("title", "第一章")
                put("outline_node_id", "outline-1")
                put("content", "正文")
                put("word_count", 999)
                put("current_version", 88)
                put("sort_order", 9000)
                put("snapshot_count", 12)
                put("quality_score", 100)
                put("created_at", "2000-01-01T00:00:00Z")
                put("updated_at", "2000-01-01T00:00:00Z")
            },
            projectId = "project-1",
            entityId = "chapter-1",
        )

        assertEquals("chapter", payload.getValue("_record_type").jsonPrimitive.content)
        assertEquals("第一章", payload.getValue("title").jsonPrimitive.content)
        assertEquals("outline-1", payload.getValue("outline_node_id").jsonPrimitive.content)
        assertEquals("正文", payload.getValue("content").jsonPrimitive.content)
        listOf(
            "word_count",
            "current_version",
            "sort_order",
            "snapshot_count",
            "quality_score",
            "created_at",
            "updated_at",
        ).forEach { assertFalse(it in payload) }
    }

    @Test
    fun `governance content and lifecycle use separate PC endpoints`() {
        val source = buildJsonObject {
            put("title", "回收传道石")
            put("description", "在后续章节兑现")
            put("importance", "high")
            put("status", "pending_review")
            put("target_chapter_number", 150)
            put("resolved_chapter_id", "chapter-150")
            put("resolution_note", "已在第150章兑现")
            put("verification_note", "人工复检通过")
            put("closed_by", "user")
        }

        val content = PcApiPayloads.governanceContent(
            "foreshadowing",
            source,
            entityId = "foreshadowing-1",
            create = false,
        )
        val lifecycle = PcApiPayloads.governanceStatus("foreshadowing", source)

        val data = content.getValue("data").jsonObject
        assertEquals("foreshadowing-1", data.getValue("item_id").jsonPrimitive.content)
        assertEquals("回收传道石", data.getValue("title").jsonPrimitive.content)
        assertFalse("status" in data)
        assertFalse("verification_note" in data)

        requireNotNull(lifecycle)
        assertEquals("pending_review", lifecycle.getValue("status").jsonPrimitive.content)
        assertEquals(150, lifecycle.getValue("target_chapter_number").jsonPrimitive.content.toInt())
        assertEquals("人工复检通过", lifecycle.getValue("verification_note").jsonPrimitive.content)
    }

    @Test
    fun `blank governance status does not invent a lifecycle transition`() {
        val lifecycle = PcApiPayloads.governanceStatus(
            "governance",
            buildJsonObject { put("status", "") },
        )

        assertNull(lifecycle)
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

    @Test
    fun `shared PC authoring contract exposes the missing core desktop fields`() {
        assertTrue("tags" in PcAuthoringContract.writableKeys("project"))
        assertTrue("daily_word_goal" in PcAuthoringContract.writableKeys("project"))
        assertTrue("outline_node_id" in PcAuthoringContract.writableKeys("chapter"))
        assertTrue("characters" in PcAuthoringContract.writableKeys("outline"))
        assertTrue("metadata" in PcAuthoringContract.writableKeys("outline"))
        assertTrue("profile" in PcAuthoringContract.writableKeys("character"))
        assertTrue("verification_note" in PcAuthoringContract.writableKeys("foreshadowing"))
        assertTrue("linked_foreshadowing_id" in PcAuthoringContract.writableKeys("governance"))
    }
}
