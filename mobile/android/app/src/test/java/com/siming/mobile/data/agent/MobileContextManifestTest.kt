package com.siming.mobile.data.agent

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileContextManifestTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val policy: MobileContextPolicy by lazy {
        val file = listOf(
            File("app/src/main/assets/pc_context_manifest_policy.json"),
            File("src/main/assets/pc_context_manifest_policy.json"),
        ).first(File::isFile)
        MobileContextPolicy.fromJson(json.parseToJsonElement(file.readText()) as JsonObject)
    }

    @Test
    fun `manifest keeps style and target outline as required anchors`() {
        val manifest = engine().prepare(inputs())

        assertEquals("ready", manifest.status)
        assertEquals(listOf("style", "target_outline"), manifest.items.take(2).map { it.category })
        assertEquals("covered", manifest.coverage.getValue("style").status)
        assertEquals("covered", manifest.coverage.getValue("target_outline").status)
        assertTrue(manifest.inputBudgetTokens > manifest.estimatedInputTokens)
    }

    @Test
    fun `missing target outline requires confirmation instead of writing blindly`() {
        val manifest = engine().prepare(
            inputs(request = request(outlineNodeId = "missing")),
        )

        assertEquals("needs_confirmation", manifest.status)
        assertEquals("missing", manifest.coverage.getValue("target_outline").status)
        assertTrue(manifest.warnings.any { "target_outline" in it })
    }

    @Test
    fun `local lexical fallback selects relevant worldbuilding`() {
        val manifest = engine().prepare(
            inputs(request = request(requirements = "这一章要切断病毒网络并防止尸潮扩散")),
        )

        val world = manifest.items.filter { it.sourceType == "worldbuilding" }
        assertTrue(world.isNotEmpty())
        assertEquals("病毒网络", world.first().title)
        assertTrue((world.first().lexicalScore ?: 0.0) > 0.0)
        assertTrue("Lexical fallback" in world.first().selectionReason)
    }

    @Test
    fun `alias resolution and directed relationships enter character source hash`() {
        val manifest = engine().prepare(
            inputs(request = request(involved = listOf("父亲"))),
        )

        val character = manifest.items.first { it.category == "scene_character" && it.title == "陆承宇" }
        assertEquals("陆承宇", character.title)
        assertTrue("陆糖: 父女" in character.content)
        assertEquals(64, character.sourceHash.length)
    }

    @Test
    fun `required anchors that exceed the hard budget are rejected`() {
        val manifest = engine().prepare(
            inputs(
                styleText = "风".repeat(2_000),
                contextWindowTokens = 3_000,
            ),
        )

        assertEquals(440, manifest.inputBudgetTokens)
        assertEquals("needs_confirmation", manifest.status)
        assertEquals("missing", manifest.coverage.getValue("style").status)
        assertTrue(manifest.estimatedInputTokens <= manifest.inputBudgetTokens)
    }

    @Test
    fun `selected source changes make a cached manifest stale`() {
        val engine = engine()
        val beforeInputs = inputs(request = request(requirements = "病毒网络"))
        val before = engine.prepare(beforeInputs)
        val changedWorld = beforeInputs.rawRecords.map { row ->
            if (row.string("id") == "w-virus") {
                buildJsonObject {
                    row.forEach { (key, value) -> put(key, value) }
                    put("content", "病毒网络已经变异为会伪造角色记忆的新形态。")
                }
            } else {
                row
            }
        }
        val validation = engine.validate(
            before,
            beforeInputs.copy(rawRecords = changedWorld),
        )

        assertEquals("stale", validation.status)
        assertNotEquals(before.selectionFingerprint, validation.current.selectionFingerprint)
        assertTrue("来源发生变化" in validation.detail)
    }

    @Test
    fun `model changes make a cached manifest stale`() {
        val engine = engine()
        val beforeInputs = inputs()
        val before = engine.prepare(beforeInputs)
        val validation = engine.validate(before, beforeInputs.copy(model = "another-model"))

        assertEquals("stale", validation.status)
        assertTrue("模型已从" in validation.detail)
    }

    @Test
    fun `default manifest payload does not expose selected content`() {
        val manifest = engine().prepare(inputs())
        val compact = manifest.toJson()
        val encoded = compact.toString()

        assertFalse("rendered_context" in compact)
        assertFalse("Character archive" in encoded)
        assertFalse("会吞噬记忆" in encoded)
        val items = compact["items"] as JsonArray
        assertTrue(items.all { "content" !in (it as JsonObject) })
    }

    @Test
    fun `token estimator mirrors PC CJK and non CJK heuristic`() {
        assertEquals(5, estimateMobileTokens("天地玄黄"))
        assertEquals(2, estimateMobileTokens("abcdefgh"))
        assertEquals(4, estimateMobileTokens("天地abcdefgh"))
    }

    private fun engine() = MobileContextManifestEngine(policy)

    private fun request(
        outlineNodeId: String = "o1",
        requirements: String = "寻找记忆城中的病毒线索",
        involved: List<String> = listOf("陆糖"),
    ) = MobileContextRequest(
        outlineNodeId = outlineNodeId,
        targetChapterId = "",
        requirements = requirements,
        involvedCharacters = involved,
        characterLimit = 8,
        recentLimit = 3,
    )

    private fun inputs(
        request: MobileContextRequest = request(),
        model: String = "deepseek-chat",
        styleText: String = "第三人称；自然文风；不得改写角色专名。",
        contextWindowTokens: Int? = null,
    ): MobileContextInputs {
        val project = buildJsonObject {
            put("_record_type", "project")
            put("id", "p1")
            put("title", "记忆城")
        }
        val outline = buildJsonObject {
            put("_record_type", "outline_node")
            put("id", "o1")
            put("title", "断线重连")
            put("node_type", "chapter")
            put("summary", "陆糖尝试切断病毒网络。")
            put("status", "pending")
            put("linked_characters", JsonArray(listOf(buildJsonObject { put("character_id", "c1") })))
        }
        val protagonist = character("c1", "陆糖", aliases = listOf("糖糖"))
        val father = character("c2", "陆承宇", aliases = listOf("父亲"))
        val relation = buildJsonObject {
            put("_record_type", "character_relationship")
            put("id", "r1")
            put("from", "c2")
            put("to", "c1")
            put("relationship_type", "父女")
            put("description", "互相信任")
        }
        val virus = world(
            "w-virus",
            "病毒网络",
            "病毒会学习并通过凡人节点传播，切断网络后会引发尸潮。",
            "2026-08-17T00:00:00Z",
        )
        val market = world(
            "w-market",
            "灵石市场",
            "城内使用灵石交易，商会负责定价。",
            "2026-08-16T00:00:00Z",
        )
        val chapter1 = chapter("ch1", "第一章", "陆糖进入记忆城。", 0)
        val chapter2 = chapter("ch2", "第二章", "她发现病毒灰痕。", 1)
        val raw = listOf(project, outline, protagonist, father, relation, virus, market, chapter1, chapter2)
        return MobileContextInputs(
            projectId = "p1",
            model = model,
            request = request,
            project = project,
            styleText = styleText,
            primaryRecords = listOf(project, outline, protagonist, father, virus, market, chapter1, chapter2),
            rawRecords = raw,
            orderedChapters = listOf(chapter1, chapter2),
            contextWindowTokens = contextWindowTokens,
        )
    }

    private fun character(id: String, name: String, aliases: List<String>): JsonObject = buildJsonObject {
        put("_record_type", "character")
        put("id", id)
        put("name", name)
        put("role_type", "supporting")
        put("personality", "稳定")
        put("background", "记忆城居民")
        put("appearance", "黑发")
        put("abilities", JsonArray(listOf(JsonPrimitive("阵法"))))
        put("aliases", JsonArray(aliases.map(::JsonPrimitive)))
    }

    private fun world(id: String, title: String, content: String, updatedAt: String): JsonObject = buildJsonObject {
        put("_record_type", "world_entry")
        put("id", id)
        put("dimension", "culture")
        put("title", title)
        put("content", content)
        put("updated_at", updatedAt)
    }

    private fun chapter(id: String, title: String, summary: String, sortOrder: Int): JsonObject = buildJsonObject {
        put("_record_type", "chapter")
        put("id", id)
        put("title", title)
        put("summary", summary)
        put("sort_order", sortOrder)
    }

    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.content.orEmpty()
}
