package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileContextParityTest {
    @Test
    fun `character resolution follows outline then name then alias`() {
        val protagonist = character("c1", "陆糖", aliases = listOf("糖糖"))
        val father = character("c2", "陆承宇", aliases = listOf("父亲"))
        val outline = buildJsonObject {
            put("_record_type", "outline_node")
            put("id", "o1")
            put(
                "linked_characters",
                JsonArray(
                    listOf(
                        buildJsonObject {
                            put("character_id", "c1")
                            put("role_in_scene", "protagonist")
                        },
                    ),
                ),
            )
        }

        val resolved = resolvePcCharacters(
            listOf(protagonist, father, outline),
            outlineNodeId = "o1",
            involvedNames = listOf("父亲"),
            limit = 8,
        )

        assertEquals(listOf("陆糖", "陆承宇"), resolved.characters.map { it["name"]?.toString()?.trim('"') })
        assertEquals("陆承宇", resolved.resolvedAliases["父亲"])
    }

    @Test
    fun `relationship preview uses PC names and selected endpoint semantics`() {
        val protagonist = character("c1", "陆糖")
        val father = character("c2", "陆承宇")
        val unrelated = character("c3", "小七")
        val relation = buildJsonObject {
            put("_record_type", "character_relationship")
            put("id", "r1")
            put("from", "c1")
            put("to", "c2")
            put("relationship_type", "父女")
            put("description", "互相信任")
        }
        val unrelatedRelation = buildJsonObject {
            put("_record_type", "character_relationship")
            put("id", "r2")
            put("from", "c2")
            put("to", "c3")
            put("relationship_type", "认识")
        }

        val payloads = pcRelationshipPayloads(
            listOf(protagonist, father, unrelated, relation, unrelatedRelation),
            selectedCharacters = listOf(protagonist),
        )

        assertEquals(1, payloads.size)
        val item = payloads.first() as JsonObject
        assertEquals(JsonPrimitive("陆糖"), item["source"])
        assertEquals(JsonPrimitive("陆承宇"), item["target"])
        assertEquals(JsonPrimitive("父女"), item["relationship_type"])

        val details = pcCharacterDetails(
            listOf(protagonist, father, relation),
            selectedCharacters = listOf(protagonist),
        )
        assertTrue("关系" in details)
        assertTrue("陆承宇: 父女" in details)
    }

    @Test
    fun `exact character archive keeps full fields ai config and every relationship`() {
        val marker = "最后一条关系不可丢失"
        val protagonist = buildJsonObject {
            character("c1", "陆糖").forEach { (key, value) -> put(key, value) }
            put("background", "长背景".repeat(1_000) + "背景尾部标记")
            put("profile", buildJsonObject { put("core_motivation", "守住记忆城") })
        }
        val others = (1..12).map { index -> character("other-$index", "关系人$index") }
        val relationships = (1..12).map { index ->
            buildJsonObject {
                put("_record_type", "character_relationship")
                put("id", "relation-$index")
                put("from", "c1")
                put("to", "other-$index")
                put("relationship_type", "关系$index")
                put("description", if (index == 12) marker else "关系说明$index")
            }
        }
        val aiConfig = buildJsonObject {
            put("_record_type", "character_ai_config")
            put("id", "config-c1")
            put("character_id", "c1")
            put("tone_style", "克制")
            put("catchphrases", JsonArray(listOf(JsonPrimitive("先看证据"))))
        }

        val archive = pcExactCharacterArchive(
            listOf(protagonist, aiConfig) + others + relationships,
            protagonist,
        )

        assertTrue(archive.length > 4_000)
        assertTrue("背景尾部标记" in archive)
        assertTrue("先看证据" in archive)
        assertTrue(marker in archive)
        assertEquals(12, Regex("relationship_type").findAll(archive).count())
    }

    @Test
    fun `governance context mirrors PC open statuses and priority ordering`() {
        val fulfilled = buildJsonObject {
            put("_record_type", "foreshadowing")
            put("id", "f0")
            put("title", "已经兑现")
            put("status", "fulfilled")
            put("importance", "critical")
        }
        val debt = buildJsonObject {
            put("_record_type", "narrative_debt")
            put("id", "d1")
            put("title", "必须交代病毒来源")
            put("status", "open")
            put("priority", "critical")
        }
        val foreshadow = buildJsonObject {
            put("_record_type", "foreshadowing")
            put("id", "f1")
            put("title", "传道石")
            put("status", "pending_review")
            put("importance", "high")
            put("target_chapter_number", 150)
        }
        val state = buildJsonObject {
            put("_record_type", "character_narrative_state")
            put("id", "s1")
            put("character_id", "c1")
            put("current_goal", "切断网络")
            put("behavior_boundaries", "不能牺牲凡人")
        }

        val context = pcGovernanceContext(listOf(fulfilled, debt, foreshadow, state))

        assertTrue(context.startsWith("叙事治理锁："))
        assertTrue("必须交代病毒来源" in context)
        assertTrue("目标第150章" in context)
        assertTrue("切断网络" in context)
        assertFalse("已经兑现" in context)
        assertTrue(context.indexOf("必须交代病毒来源") < context.indexOf("传道石"))
    }

    @Test
    fun `governance context keeps only latest state per character`() {
        val oldState = buildJsonObject {
            put("_record_type", "character_narrative_state")
            put("id", "state-old")
            put("character_id", "c1")
            put("current_goal", "逃离青云宗")
            put("created_at", "2026-08-01T00:00:00Z")
        }
        val newState = buildJsonObject {
            put("_record_type", "character_narrative_state")
            put("id", "state-new")
            put("character_id", "c1")
            put("current_goal", "切断病毒网络")
            put("created_at", "2026-08-17T00:00:00Z")
        }

        val context = pcGovernanceContext(listOf(oldState, newState))

        assertTrue("切断病毒网络" in context)
        assertFalse("逃离青云宗" in context)
        assertEquals(1, Regex("角色动态/c1").findAll(context).count())
    }

    @Test
    fun `exact governance context can include items beyond compact preview`() {
        val marker = "第十三条低优先级治理项"
        val records = (1..13).map { index ->
            buildJsonObject {
                put("_record_type", "narrative_debt")
                put("id", "debt-$index")
                put("title", if (index == 13) marker else "高优先级债务$index")
                put("status", "open")
                put("priority", if (index == 13) "low" else "critical")
            }
        }

        assertFalse(marker in pcGovernanceContext(records))
        assertTrue(marker in pcGovernanceContext(records, limit = null))
    }

    private fun character(id: String, name: String, aliases: List<String> = emptyList()): JsonObject =
        buildJsonObject {
            put("_record_type", "character")
            put("id", id)
            put("name", name)
            put("role_type", "supporting")
            put("personality", "稳定")
            put("background", "背景")
            put("appearance", "外貌")
            put("abilities", JsonArray(listOf(JsonPrimitive("能力"))))
            put("aliases", JsonArray(aliases.map(::JsonPrimitive)))
        }
}
