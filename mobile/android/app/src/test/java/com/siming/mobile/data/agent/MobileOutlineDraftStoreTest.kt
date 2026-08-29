package com.siming.mobile.data.agent

import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotEquals
import kotlin.test.assertNotNull
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileOutlineDraftStoreTest {
    @Test
    fun `pending outline proposal survives restart and keeps audit context`() = runTest {
        val directory = createTempDirectory("siming-mobile-outline-").toFile()
        try {
            val manifest = manifest()
            val baseline = listOf(outline("o1", "第一章"))
            val run = MobileOutlineDraftRun(
                id = mobileOutlineDraftId("p1", "deepseek-chat", manifest),
                projectId = "p1",
                model = "deepseek-chat",
                parentId = "",
                insertAfterId = "o1",
                nodes = nodes(),
                designNotes = "推进冲突",
                state = MobileOutlineDraftState.PENDING,
                manifest = manifest,
                baseOutlineHash = mobileOutlineTreeHash(baseline),
            )

            val stored = MobileOutlineDraftStore(directory).save(run)
            val recovered = MobileOutlineDraftStore(directory).latestPending("p1")

            assertNotNull(recovered)
            assertEquals(stored.id, recovered.id)
            assertEquals(stored.baseOutlineHash, recovered.baseOutlineHash)
            assertEquals(
                manifest.selectionFingerprint,
                (stored.toJson()["context_selection_digest"] as JsonPrimitive).content,
            )
            assertEquals("outline_planning", recovered.manifest.request.taskType)
            assertEquals("第二章", recovered.nodes.first().let { it as kotlinx.serialization.json.JsonObject }
                .let { it["title"] as JsonPrimitive }.content)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `store enforces one pending proposal per project and releases slot on discard`() = runTest {
        val directory = createTempDirectory("siming-mobile-outline-").toFile()
        try {
            val manifest = manifest()
            val store = MobileOutlineDraftStore(directory)
            val first = draft("outline-first", manifest)
            val second = draft("outline-second", manifest)

            store.save(first)
            val conflict = assertFailsWith<MobilePendingOutlineDraftConflict> {
                store.save(second)
            }
            assertEquals(first.id, conflict.draftId)

            assertEquals(MobileOutlineDraftState.DISCARDED, store.markDiscarded(first.id)?.state)
            assertEquals(second.id, store.save(second).id)
            assertEquals(MobileOutlineDraftState.CONFIRMED, store.markConfirmed(second.id, listOf("real-2"))?.state)
            assertEquals(null, store.latestPending("p1"))

            val third = draft("outline-third", manifest())
            store.save(third)
            assertEquals(MobileOutlineDraftState.SUPERSEDED, store.markSuperseded(third.id)?.state)
            assertEquals(null, store.latestPending("p1"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `store rejects oversized proposals instead of truncating them`() = runTest {
        val directory = createTempDirectory("siming-mobile-outline-").toFile()
        try {
            val oversized = JsonArray(
                (1..9).map { index ->
                    buildJsonObject {
                        put("node_type", "chapter")
                        put("title", "第${index}章")
                    }
                },
            )
            val invalid = draft("outline-oversized", manifest()).copy(nodes = oversized)

            assertFailsWith<IllegalArgumentException> {
                MobileOutlineDraftStore(directory).save(invalid)
            }
            assertEquals(null, MobileOutlineDraftStore(directory).latestPending("p1"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `store rejects invalid nested node hierarchy`() = runTest {
        val directory = createTempDirectory("siming-mobile-outline-").toFile()
        try {
            val invalidNodes = JsonArray(
                listOf(
                    buildJsonObject {
                        put("node_type", "chapter")
                        put("title", "父章")
                    },
                    buildJsonObject {
                        put("node_type", "chapter")
                        put("title", "错误子章")
                        put("parent_title", "父章")
                    },
                ),
            )
            val invalid = draft("outline-invalid-tree", manifest()).copy(nodes = invalidNodes)

            assertFailsWith<IllegalArgumentException> {
                MobileOutlineDraftStore(directory).save(invalid)
            }
            assertEquals(null, MobileOutlineDraftStore(directory).latestPending("p1"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `outline tree fingerprint changes when formal outline changes`() {
        val original = mobileOutlineTreeHash(listOf(outline("o1", "第一章")))
        val reorderedInput = mobileOutlineTreeHash(
            listOf(outline("o2", "第二章"), outline("o1", "第一章")),
        )
        val sameDifferentOrder = mobileOutlineTreeHash(
            listOf(outline("o1", "第一章"), outline("o2", "第二章")),
        )

        assertNotEquals(original, reorderedInput)
        assertEquals(reorderedInput, sameDifferentOrder)
    }

    private fun draft(id: String, manifest: MobileContextManifest) = MobileOutlineDraftRun(
        id = id,
        projectId = "p1",
        model = "deepseek-chat",
        parentId = "",
        insertAfterId = "o1",
        nodes = nodes(),
        designNotes = "",
        state = MobileOutlineDraftState.PENDING,
        manifest = manifest,
        baseOutlineHash = mobileOutlineTreeHash(listOf(outline("o1", "第一章"))),
    )

    private fun nodes() = JsonArray(
        listOf(
            buildJsonObject {
                put("node_type", "chapter")
                put("title", "第二章")
                put("summary", "冲突升级")
                put("character_names", JsonArray(emptyList()))
                put("status", "pending")
            },
        ),
    )

    private fun outline(id: String, title: String) = buildJsonObject {
        put("_record_type", "outline_node")
        put("id", id)
        put("project_id", "p1")
        put("node_type", "chapter")
        put("title", title)
        put("sort_order", id.removePrefix("o").toIntOrNull() ?: 0)
    }

    private fun manifest(): MobileContextManifest {
        val request = MobileContextRequest(
            taskType = "outline_planning",
            insertAfterId = "o1",
            batchCount = 1,
            requirements = "规划下一章",
        )
        val item = MobileContextManifestItem(
            category = "outline_position",
            sourceType = "inline",
            sourceId = "outline-position",
            chunkId = null,
            title = "Outline position",
            content = "{\"parent_id\":null,\"insert_after_id\":\"o1\"}",
            required = true,
            tier = 1,
            lexicalScore = null,
            recencyScore = null,
            structuralScore = 1.0,
            finalScore = 1.0,
            selectionReason = "required",
        )
        return MobileContextManifest(
            id = "manifest-outline-1",
            projectId = "p1",
            model = "deepseek-chat",
            policyVersion = 4,
            indexVersion = 1,
            policySourceHash = "a".repeat(64),
            status = "ready",
            request = request,
            requestFingerprint = request.fingerprint("p1"),
            selectionFingerprint = mobileSha256(item.sourceHash),
            contextWindowTokens = 100_000,
            inputBudgetTokens = 60_000,
            softInputTargetTokens = 32_000,
            outputReserveTokens = 30_000,
            safetyMarginTokens = 512,
            items = listOf(item),
            coverage = mapOf(
                "outline_position" to MobileContextCoverage(true, "covered", 1),
            ),
            warnings = emptyList(),
            selectionToken = "selection-token-outline",
        )
    }
}
