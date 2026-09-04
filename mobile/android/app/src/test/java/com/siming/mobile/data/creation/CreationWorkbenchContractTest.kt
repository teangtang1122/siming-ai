package com.siming.mobile.data.creation

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CreationWorkbenchContractTest {
    @Test
    fun visibleStagesPreservePcOrderIncludingCreationConstraints() {
        val order = listOf("constraints", "concepts", "world_style", "final_review")
        val labels = mapOf(
            "constraints" to "创作约束",
            "concepts" to "创意方向",
            "world_style" to "文风与世界观",
            "final_review" to "最终审阅",
        )

        val visible = CreationWorkbenchContract.visibleStages(order, labels)

        assertEquals(order, visible.map { it.first })
        assertEquals("constraints" to "创作约束", visible.first())
    }

    @Test
    fun archiveReadinessMatchesPcFinalizationGate() {
        val stages = mutableMapOf<String, JsonObject>()
        CreationWorkbenchContract.requiredArchiveStages.forEach { stage ->
            stages[stage] = state("confirmed", buildJsonObject { put("value", stage) })
        }
        stages["opening_outline"] = state("pending")
        stages["final_review"] = state(
            "generated",
            buildJsonObject {
                put("ready", true)
                put("blocking", JsonArray(emptyList()))
            },
        )
        val session = session(stages)

        assertTrue(CreationWorkbenchContract.canArchive(session))
        assertTrue(CreationWorkbenchContract.archiveBlockers(session, emptyMap()).isEmpty())

        val stale = session(stages.toMutableMap().apply {
            put("characters", state("stale", buildJsonObject { put("characters", buildJsonArray {}) }))
        })
        assertFalse(CreationWorkbenchContract.canArchive(stale))
        assertEquals(listOf("请先确认characters"), CreationWorkbenchContract.archiveBlockers(stale, emptyMap()))

        val blockedReview = session(stages.toMutableMap().apply {
            put("final_review", state("confirmed", buildJsonObject {
                put("ready", false)
                put("blocking", buildJsonArray { add(JsonPrimitive("作者要求核对尚未校准的原始证据")) })
                put("warnings", buildJsonArray { add(JsonPrimitive("模型超时不能算作审阅完成")) })
            }))
        })
        assertFalse(CreationWorkbenchContract.canArchive(blockedReview))
        assertEquals(
            listOf("作者要求核对尚未校准的原始证据"),
            CreationWorkbenchContract.archiveBlockers(blockedReview, emptyMap()),
        )
    }

    @Test
    fun conceptSelectionIsPersistedInPcArtifactShape() {
        val data = buildJsonObject {
            put("options", buildJsonArray {
                add(buildJsonObject { put("id", "concept-1"); put("title", "方向一") })
                add(buildJsonObject { put("id", "concept-2"); put("title", "方向二") })
            })
        }
        val session = session(mapOf("concepts" to state("generated", data)))

        assertEquals("concept-1", CreationWorkbenchContract.selectedConceptId(session, data))
        val selected = CreationWorkbenchContract.conceptDataWithSelection(data, "concept-2")
        assertEquals("concept-2", (selected["selected_concept_id"] as JsonPrimitive).content)
        assertEquals(data["options"], selected["options"])
    }

    @Test
    fun generatedAndStaleStagesReceiveAttentionBeforePendingStages() {
        val visible = listOf("concepts", "world_style", "characters", "locations")
        val session = session(
            mapOf(
                "concepts" to state("confirmed", buildJsonObject { put("value", 1) }),
                "world_style" to state("pending"),
                "characters" to state("stale", buildJsonObject { put("characters", buildJsonArray {}) }),
                "locations" to state("pending"),
            ),
        )

        assertEquals("characters", CreationWorkbenchContract.recommendedStage(session, visible))
        assertEquals("locations", CreationWorkbenchContract.nextStage(visible, "characters"))
    }

    private fun session(stages: Map<String, JsonObject>): JsonObject = buildJsonObject {
        put("id", "session-1")
        put("revision", 8)
        put("current_stage", "")
        put("draft", buildJsonObject {
            put("selected_concept_id", "")
            put("stages", JsonObject(stages))
        })
    }

    private fun state(
        status: String,
        data: JsonObject = JsonObject(emptyMap()),
    ): JsonObject = buildJsonObject {
        put("status", status)
        if (data.isNotEmpty()) put("data", data)
    }
}
