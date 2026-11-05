package com.siming.mobile.data.cataloging

import java.io.File
import java.util.UUID
import com.siming.mobile.data.*
import com.siming.mobile.data.local.ReplicaEntity
import kotlin.test.*
import kotlinx.serialization.json.*

class CatalogingProjectionTest {
    private val fixture = javaClass.classLoader!!.getResourceAsStream("mobile-cataloging-v1.json")!!.bufferedReader().use { Json.parseToJsonElement(it.readText()).jsonObject }
    private val contract = CatalogingContract(Json.parseToJsonElement(File("src/main/assets/pc_workspace_prompt_contract.json").readText()).jsonObject)
    private val ids = fixture.getValue("ids").jsonObject
    private val source = fixture.getValue("records").jsonArray.map { CatalogRecord.fromJson(it.jsonObject) }
    private fun plan() = CatalogingPlan(contract, ids.text("project"), ids.text("chapter"), source)
    private fun filled() = plan().also { plan ->
        val result = plan.submit(buildJsonObject { put("candidates", fixture.getValue("candidates")); put("finalize", true) })
        assertEquals(JsonPrimitive(true), result["candidate_set_complete"], result.toString())
    }

    @Test fun `scene replacement is offered only for staged candidates and rejects formal outline IDs atomically`() {
        val plan = plan()
        plan.submit(buildJsonObject { put("candidates", JsonArray(listOf(fixture.getValue("candidates").jsonArray.first()))) })
        fun choices() = contract.schemasForPlan(false, chapterOutlineId = ids.text("outline"),
            sectionOutlineIds = listOf(ids.text("scene1"), ids.text("scene2")),
            sceneCandidateIds = plan.sceneRepairContext().strings("expected_candidate_ids"))
            .map { it.jsonObject.obj("function") }.single { it.text("name") == "select_cataloging_candidate_types" }
            .obj("parameters").obj("properties").obj("types").obj("items").strings("enum")
        assertFalse("scene_outline_replace" in choices())
        assertTrue("outline_update_section" in choices())
        val scenes = fixture.getValue("candidates").jsonArray.filter {
            it.jsonObject.text("node_type") == "section"
        }
        plan.submit(buildJsonObject { put("candidates", JsonArray(scenes)) })
        assertTrue("scene_outline_replace" in choices())
        val candidateIds = plan.sceneRepairContext().strings("expected_candidate_ids")
        assertEquals(2, candidateIds.size)
        assertFalse(ids.text("scene1") in candidateIds)
        val schema = contract.schemasForPlan(false, "scene_outline_replace", sceneCandidateIds = candidateIds)
            .map { it.jsonObject.obj("function") }.single { it.text("name") == "save_external_cataloging_candidates" }
            .obj("parameters").obj("properties").obj("candidates").obj("items").obj("properties")
            .obj("expected_candidate_ids")
        assertEquals(candidateIds, schema.obj("items").strings("enum"))
        assertEquals(2, schema.number("minItems"))
        val before = plan.candidates.toList()
        fun replace(expected: List<String>, replacements: List<JsonElement> = scenes) = plan.submit(buildJsonObject {
            put("candidates", JsonArray(listOf(buildJsonObject {
                put("type", "scene_outline_replace"); put("expected_candidate_ids", jsonStrings(expected))
                put("sections", JsonArray(replacements))
            })))
        })
        for (bad in listOf(emptyList(), listOf(ids.text("scene1"), ids.text("scene2")), listOf(candidateIds.first(), candidateIds.first()))) {
            val rejected = replace(bad)
            assertTrue(rejected.objects("candidate_errors").isNotEmpty())
            assertEquals(candidateIds, rejected.obj("scene_repair").strings("expected_candidate_ids"))
            assertEquals(before, plan.candidates)
        }
        assertTrue(replace(candidateIds, scenes.take(1)).objects("candidate_errors").isNotEmpty())
        assertEquals(before, plan.candidates)
        assertTrue(replace(candidateIds).objects("candidate_errors").isEmpty())
        assertEquals(3, plan.candidates.size)
        assertEquals(before.first(), plan.candidates.first())
        assertTrue(plan.sceneRepairContext().strings("expected_candidate_ids").none { it in candidateIds })
    }

    @Test fun `narrative object content is rejected before replacing a valid summary`() {
        val plan = filled()
        val summary = plan.candidates.single { it.kind == "chapter_summary" }
        val malformed = JsonObject(summary.payload + ("narrative_state" to buildJsonObject {
            put("foreshadowing_planted", JsonArray(listOf(buildJsonObject { put("content", "线索"); put("scene_number", 1) })))
        }))
        val rejected = plan.submit(buildJsonObject { put("candidates", JsonArray(listOf(malformed))) })
        assertTrue(rejected.objects("candidate_errors").single().text("message").contains("foreshadowing_planted[0].title"))
        assertEquals(summary, plan.candidates.single { it.kind == "chapter_summary" })
        val before = plan.snapshotCandidates()
        assertFailsWith<IllegalStateException> { plan.submit(buildJsonObject {
            put("candidates", JsonArray(emptyList())); put("reject_candidate_ids", jsonStrings(listOf(summary.id))); put("finalize", true)
        }) }
        assertEquals(before, plan.snapshotCandidates())
        assertFailsWith<IllegalArgumentException> { plan.submit(buildJsonObject {
            put("candidates", JsonArray(listOf(malformed))); put("reject_candidate_ids", jsonStrings(listOf(summary.id)))
        }) }
        assertEquals(before, plan.snapshotCandidates())
        val repaired = JsonObject(summary.payload + ("narrative_state" to buildJsonObject {
            put("foreshadowing_planted", JsonArray(listOf(buildJsonObject { put("title", "线索"); put("scene_number", 1) })))
        }))
        assertTrue(plan.submit(buildJsonObject { put("candidates", JsonArray(listOf(repaired))) }).objects("candidate_errors").isEmpty())
        assertEquals(summary.id, plan.candidates.single { it.kind == "chapter_summary" }.id)
        assertFalse(plan.requiresSummary())
        val projection = CatalogingProjection(plan, "local-cat-repaired", "2026-11-04T01:00:00Z")
        projection.apply()
        assertEquals("线索", projection.ofType("foreshadowing").single().payload.text("title"))
    }

    @Test fun `candidate retraction is durable idempotent and isolated from active replacements`() {
        val plan = filled()
        val old = plan.candidates.first { it.kind == "chapter_link" }
        val retract = buildJsonObject { put("candidates", JsonArray(emptyList())); put("reject_candidate_ids", jsonStrings(listOf(old.id))) }
        plan.submit(retract)
        assertEquals("rejected", plan.snapshotCandidates().single { it.id == old.id }.status)
        val restarted = CatalogingPlan(contract, ids.text("project"), ids.text("chapter"), source,
            plan.snapshotCandidates().map { CatalogCandidate.fromJson(it.toJson()) })
        assertTrue(restarted.candidates.none { it.id == old.id })
        restarted.submit(retract)
        restarted.submit(buildJsonObject { put("candidates", JsonArray(listOf(old.payload))) })
        val replacement = restarted.candidates.single { it.kind == "chapter_link" }
        assertNotEquals(old.id, replacement.id)
        restarted.submit(retract)
        assertEquals(replacement, restarted.candidates.single { it.kind == "chapter_link" })
        assertEquals(1, restarted.snapshotCandidates().count { it.id == old.id })
    }

    @Test fun `same PC fixture projects complete character archive and exact outline links`() {
        val projection = CatalogingProjection(filled(), "local-cat-test", "2026-09-16T01:00:00Z")
        projection.apply()
        val hero = projection.get("character", ids.text("hero"))
        assertEquals(4, hero.number("current_version"))
        assertEquals("旧炉工坊学徒。开始核实炉底封印。", hero.text("background"))
        assertEquals("一柄旧锤。新增一枚铜票。", hero.text("items_or_assets"))
        assertEquals("修复炉体", hero.text("current_goal"))
        assertEquals("寡言谨慎", hero.obj("profile").text("voice"))
        assertTrue(projection.ofType("character_ai_config").single().payload.text("custom_system_prompt").contains("不可提前知晓"))
        val version = projection.ofType("character_version").single { it.payload.text("character_id") == ids.text("hero") && it.payload.number("version_number") == 4 }.payload
        assertTrue(Json.parseToJsonElement(version.text("snapshot_data")).jsonObject.obj("ai_config").text("custom_system_prompt").contains("不可提前知晓"))
        val chapter = projection.chapter
        assertEquals(JsonPrimitive(false), chapter["cataloging_required"])
        assertEquals(ids.text("outline"), chapter.text("outline_node_id"))
        val node = projection.get("outline_node", ids.text("outline"))
        assertEquals(ids.text("volume"), node.text("parent_id"))
        assertEquals("预定先修炉", node.text("planned_summary"))
        assertEquals("completed", node.text("status"))
        val characters = setOf(ids.text("hero"), ids.text("mentor"))
        assertEquals(characters, node.objects("linked_characters").map { it.text("id") }.toSet())
        assertEquals(characters, chapter.objects("characters").map { it.text("character_id") }.toSet())
        assertEquals(ids.text("outline"), projection.get("outline_node", ids.text("scene1")).text("parent_id"))
        assertEquals(ids.text("outline"), projection.get("outline_node", ids.text("scene2")).text("parent_id"))
        assertEquals("open", projection.ofType("foreshadowing").single().payload.text("status"))
        assertEquals(1, projection.ofType("narrative_checkpoint").size)
    }

    @Test fun `missing scene and foreign ID cannot finalize or modify source`() {
        val plan = plan()
        val candidates = fixture.getValue("candidates").jsonArray.filter { it.jsonObject.number("scene_number") != 2 }
        val response = plan.submit(buildJsonObject { put("candidates", JsonArray(candidates)); put("finalize", true) })
        assertEquals(JsonPrimitive(false), response["candidate_set_complete"])
        assertFails { CatalogingProjection(plan, "run", "now").apply() }
        val rejected = plan.submit(buildJsonObject { put("candidates", JsonArray(listOf(buildJsonObject {
            put("type", "character_state_update"); put("id", "foreign"); put("mental_state", "changed")
        }))) })
        assertTrue(rejected.objects("candidate_errors").isNotEmpty())
        assertEquals(2, plan.record("character", ids.text("hero")).payload.number("current_version"))
    }

    @Test fun `destructive asset replacement and empty state return repair errors`() {
        val plan = filled()
        val rejected = plan.submit(buildJsonObject { put("candidates", JsonArray(listOf(buildJsonObject {
            put("type", "character_state_update"); put("id", ids.text("hero")); put("items_or_assets", "铜票")
        }))) })
        assertTrue(rejected.objects("candidate_errors").isNotEmpty())
        val empty = plan()
        empty.submit(buildJsonObject { put("candidates", JsonArray(listOf(fixture.getValue("candidates").jsonArray.first()))) })
        assertTrue(empty.submit(buildJsonObject { put("candidates", JsonArray(listOf(buildJsonObject {
            put("type", "character_state_update"); put("id", ids.text("hero"))
        }))) }).objects("candidate_errors").isNotEmpty())
    }

    @Test fun `cataloged package preserves chapter outline character and world links across import and rewrite`() {
        val projection = CatalogingProjection(filled(), "local-cat-test", "2026-09-16T01:00:00Z")
        projection.apply()
        val file = kotlin.io.path.createTempFile("cataloging-", ".siming-project").toFile()
        val rewritten = kotlin.io.path.createTempFile("cataloging-rewrite-", ".siming-project").toFile()
        fun replicas(projectId: String, rows: List<CatalogRecord>) = rows.map { row ->
            ReplicaEntity(ReplicaEntity.key(projectId, row.entityType, row.id), projectId, row.entityType, row.id, 0, "upsert", row.payload.toString(), "fixture", "2026-09-16T01:00:00Z")
        }
        try {
            MobileProjectPackageWriter.write(ids.text("project"), replicas(ids.text("project"), projection.rows.values.toList()), null, "full", file)
            val validated = MobileProjectPackageValidator(file).validate()
            assertEquals(2, validated.coreRows.getValue("chapter_characters").size)
            assertEquals(1, validated.coreRows.getValue("chapter_worldbuilding").size)
            assertEquals(5, validated.coreRows.getValue("outline_characters").size)
            val request = UUID.randomUUID()
            val (copyId, imported) = MobileProjectPackageMaterializer.materialize(validated, request, null)
            val chapter = imported.single { it.entityType == "chapter" }.payload
            assertEquals(2, chapter.objects("characters").size)
            assertEquals(JsonPrimitive(false), chapter["cataloging_required"])
            val copyRows = imported.map { CatalogRecord(it.entityType, it.entityId, it.payload) }
            MobileProjectPackageWriter.rewriteImported(file, sha256File(file), request, copyId, replicas(copyId, copyRows), null, "full", rewritten)
            val checked = MobileProjectPackageValidator(rewritten).validate()
            assertEquals(2, checked.coreRows.getValue("chapter_characters").size)
            assertEquals(1, checked.coreRows.getValue("chapter_worldbuilding").size)
            assertEquals(5, checked.coreRows.getValue("outline_characters").size)
        } finally { file.delete(); rewritten.delete() }
    }
}
