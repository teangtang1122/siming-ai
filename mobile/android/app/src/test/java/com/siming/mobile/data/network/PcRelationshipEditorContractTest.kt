package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class PcRelationshipEditorContractTest {
    @Test
    fun `editing from target endpoint preserves original direction`() {
        val network = network(
            nodes = listOf("parent" to "陆承宇", "child" to "陆糖"),
            edges = listOf(edge("parent", "child", "父女")),
        )

        val relation = pcEditableRelationships(network, "child").single()
        val payload = pcRelationshipMutationPayload(relation)

        assertEquals("parent", relation.sourceId)
        assertEquals("child", relation.targetId)
        assertEquals("陆承宇", relation.counterpartName)
        assertEquals("parent", payload.getValue("source_character_id").jsonPrimitive.content)
        assertEquals("child", payload.getValue("target_character_id").jsonPrimitive.content)
        assertTrue("陆承宇 → 当前角色" in relation.directionLabel("child"))
    }

    @Test
    fun `new relationship starts at current character`() {
        val relation = pcNewRelationship("child", "friend", "小七")
        val payload = pcRelationshipMutationPayload(relation)

        assertEquals("child", payload.getValue("source_character_id").jsonPrimitive.content)
        assertEquals("friend", payload.getValue("target_character_id").jsonPrimitive.content)
        assertEquals("friend", relation.counterpartId("child"))
    }

    @Test
    fun `unconnected edges are excluded from current editor`() {
        val network = network(
            nodes = listOf("a" to "甲", "b" to "乙", "c" to "丙"),
            edges = listOf(edge("a", "b", "同门")),
        )

        assertTrue(pcEditableRelationships(network, "c").isEmpty())
    }

    private fun network(
        nodes: List<Pair<String, String>>,
        edges: List<JsonObject>,
    ): JsonObject = buildJsonObject {
        put(
            "nodes",
            JsonArray(
                nodes.map { (id, name) ->
                    buildJsonObject {
                        put("id", id)
                        put("name", name)
                    }
                },
            ),
        )
        put("edges", JsonArray(edges))
    }

    private fun edge(source: String, target: String, type: String): JsonObject =
        buildJsonObject {
            put("from", source)
            put("to", target)
            put("relationship_type", type)
            put("description", JsonPrimitive(""))
        }
}
