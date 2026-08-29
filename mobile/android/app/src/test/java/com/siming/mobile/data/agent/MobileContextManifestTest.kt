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
    private val policyRoot: JsonObject by lazy {
        val file = listOf(
            File("app/src/main/assets/pc_context_manifest_policy.json"),
            File("src/main/assets/pc_context_manifest_policy.json"),
        ).first(File::isFile)
        json.parseToJsonElement(file.readText()) as JsonObject
    }
    @Test
    fun `manifest keeps style and target outline as required anchors`() {
        val manifest = engine().prepare(inputs())

        assertEquals("ready", manifest.status)
        assertEquals(listOf("style", "target_outline"), manifest.items.take(2).map { it.category })
        assertEquals("covered", manifest.coverage.getValue("style").status)
        assertEquals("covered", manifest.coverage.getValue("target_outline").status)
        assertTrue(manifest.inputBudgetTokens > manifest.estimatedInputTokens)
        assertEquals(32_000, manifest.softInputTargetTokens)
        assertTrue(manifest.inputBudgetTokens > manifest.softInputTargetTokens)
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
    fun `outline planning baseline contains position and style but no story collections`() {
        val planningRequest = MobileContextRequest(
            taskType = "outline_planning",
            parentId = "",
            insertAfterId = "o1",
            batchCount = 2,
            requirements = "规划后续两章",
        )

        val manifest = engine("outline_planning").prepare(inputs(request = planningRequest))

        assertEquals("ready", manifest.status)
        assertEquals("covered", manifest.coverage.getValue("style").status)
        assertEquals("covered", manifest.coverage.getValue("outline_position").status)
        assertEquals(
            setOf("style", "outline_position", "user_requirement"),
            manifest.generationItems.mapTo(linkedSetOf()) { it.category },
        )
        assertTrue(manifest.items.none { it.sourceType in setOf("character", "worldbuilding", "chapter") })
    }

    @Test
    fun `final selection has no fixed twenty four source limit`() {
        val extras = (0 until 25).map { index ->
            world(
                id = "proof-$index",
                title = "proof-${index.toString().padStart(2, '0')}",
                content = if (index < 20) {
                    "commonproof detail $index"
                } else {
                    "commonproof supplementproof detail $index"
                },
                updatedAt = "2026-08-18T00:00:00Z",
            )
        }
        val base = inputs()
        val expanded = base.copy(
            primaryRecords = base.primaryRecords + extras,
            rawRecords = base.rawRecords + extras,
        )
        val engine = engine()
        val first = engine.search(
            engine.prepare(expanded),
            expanded,
            "commonproof",
            setOf("worldbuilding"),
            limit = 20,
        )
        val second = engine.search(
            first.manifest,
            expanded,
            "supplementproof",
            setOf("worldbuilding"),
            limit = 20,
        )
        val selectedIds = second.manifest.items
            .filter { it.category == "agent_search" && it.sourceId.orEmpty().startsWith("proof-") }
            .map { it.itemId }

        val selection = engine.select(second.manifest, expanded, selectedIds)

        assertTrue(selection.ready)
        assertEquals(25, selection.accepted.size)
    }

    @Test
    fun `volume target cannot satisfy the chapter writing anchor`() {
        val base = inputs()
        fun asVolume(row: JsonObject): JsonObject = if (row.string("id") == "o1") {
            buildJsonObject {
                row.forEach { (key, value) -> put(key, value) }
                put("node_type", "volume")
            }
        } else {
            row
        }
        val manifest = engine().prepare(
            base.copy(
                primaryRecords = base.primaryRecords.map(::asVolume),
                rawRecords = base.rawRecords.map(::asVolume),
            ),
        )

        assertEquals("needs_confirmation", manifest.status)
        assertEquals("missing", manifest.coverage.getValue("target_outline").status)
        assertTrue(manifest.warnings.any { "target_outline" in it })
    }

    @Test
    fun `local lexical search returns candidates without injecting them`() {
        val engine = engine()
        val inputs = inputs(request = request(requirements = "这一章要切断病毒网络并防止尸潮扩散"))
        val manifest = engine.prepare(inputs)
        val searched = engine.search(manifest, inputs, "病毒网络 尸潮", setOf("worldbuilding"))

        assertTrue(manifest.items.none { it.sourceType == "worldbuilding" })
        val world = searched.items.filter { it.sourceType == "worldbuilding" }
        assertTrue(world.isNotEmpty())
        assertEquals("病毒网络", world.first().title)
        assertTrue((world.first().lexicalScore ?: 0.0) > 0.0)
        assertEquals("agent_search", world.first().category)
        assertTrue(searched.manifest.selectionToken.isNullOrBlank())
    }

    @Test
    fun `model selected character expands exact card and relationships`() {
        val engine = engine()
        val inputs = inputs()
        val manifest = engine.prepare(inputs)
        val searched = engine.search(manifest, inputs, "陆承宇 父亲", setOf("character"))
        val candidate = searched.items.first { it.title == "陆承宇" }
        val selection = engine.select(searched.manifest, inputs, listOf(candidate.itemId))

        assertTrue(selection.ready)
        val character = selection.accepted.single()
        assertEquals("agent_selected", character.category)
        assertEquals("陆承宇", character.title)
        assertTrue("陆糖: 父女" in character.content)
        assertEquals(64, character.sourceHash.length)
    }

    @Test
    fun `selected exact source keeps content beyond old fixed character limit`() {
        val marker = "世界观尾部不可丢失标记"
        val base = inputs()
        val longWorld = world(
            "w-long",
            "长篇世界观",
            "长篇资料".repeat(2_100) + marker,
            "2026-08-19T00:00:00Z",
        )
        val expanded = base.copy(
            primaryRecords = base.primaryRecords + longWorld,
            rawRecords = base.rawRecords + longWorld,
        )
        val engine = engine()
        val searched = engine.search(
            engine.prepare(expanded),
            expanded,
            marker,
            setOf("worldbuilding"),
        )
        val candidate = searched.items.single { it.sourceId == "w-long" }

        val selection = engine.select(searched.manifest, expanded, listOf(candidate.itemId))

        assertTrue(selection.ready)
        assertTrue(selection.accepted.single().content.length > 8_000)
        assertTrue(marker in selection.accepted.single().content)
    }

    @Test
    fun `default search can select the full governance ledger`() {
        val marker = "第十三条低优先级治理项"
        val governance = (1..13).map { index ->
            buildJsonObject {
                put("_record_type", "narrative_debt")
                put("id", "governance-$index")
                put("title", if (index == 13) marker else "高优先级债务$index")
                put("status", "open")
                put("priority", if (index == 13) "low" else "critical")
            }
        }
        val base = inputs()
        val expanded = base.copy(
            primaryRecords = base.primaryRecords + governance,
            rawRecords = base.rawRecords + governance,
        )
        val engine = engine()
        val searched = engine.search(engine.prepare(expanded), expanded, marker)
        val candidate = searched.items.single {
            it.sourceType == "narrative_governance"
        }

        val selection = engine.select(
            searched.manifest,
            expanded,
            listOf(candidate.itemId),
        )

        assertTrue(selection.ready)
        assertTrue(marker in selection.accepted.single().content)
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
        assertEquals(440, manifest.softInputTargetTokens)
        assertEquals("needs_confirmation", manifest.status)
        assertEquals("missing", manifest.coverage.getValue("style").status)
        assertTrue(manifest.estimatedInputTokens <= manifest.inputBudgetTokens)
    }

    @Test
    fun `soft target warns without rejecting model approved context`() {
        val engine = engine()
        val inputs = inputs(styleText = "风".repeat(35_000))
        val manifest = engine.prepare(inputs)

        val selection = engine.select(manifest, inputs, emptyList())

        assertTrue(selection.ready)
        assertTrue(selection.manifest.estimatedInputTokens > 32_000)
        assertTrue(selection.manifest.warnings.any { "超过软目标" in it })
    }

    @Test
    fun `selected source changes make a cached manifest stale`() {
        val engine = engine()
        val beforeInputs = inputs(request = request(requirements = "病毒网络"))
        val baseline = engine.prepare(beforeInputs)
        val searched = engine.search(baseline, beforeInputs, "病毒网络", setOf("worldbuilding"))
        val candidate = searched.items.first { it.title == "病毒网络" }
        val before = engine.select(searched.manifest, beforeInputs, listOf(candidate.itemId)).manifest
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
    fun `new search invalidates a finalized selection token`() {
        val engine = engine()
        val inputs = inputs()
        val firstSearch = engine.search(engine.prepare(inputs), inputs, "病毒网络", setOf("worldbuilding"))
        val selected = engine.select(firstSearch.manifest, inputs, listOf(firstSearch.items.first().itemId)).manifest

        assertTrue(!selected.selectionToken.isNullOrBlank())
        val nextSearch = engine.search(selected, inputs, "陆承宇", setOf("character"))

        assertTrue(nextSearch.manifest.selectionToken.isNullOrBlank())
        assertTrue(nextSearch.manifest.generationItems.none { it.category == "agent_selected" })
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

    private fun policy(taskType: String): MobileContextPolicy =
        MobileContextPolicy.fromJson(policyRoot, taskType)

    private fun engine(taskType: String = "writing") = MobileContextManifestEngine(policy(taskType))

    private fun request(
        outlineNodeId: String = "o1",
        requirements: String = "寻找记忆城中的病毒线索",
    ) = MobileContextRequest(
        outlineNodeId = outlineNodeId,
        targetChapterId = "",
        requirements = requirements,
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
