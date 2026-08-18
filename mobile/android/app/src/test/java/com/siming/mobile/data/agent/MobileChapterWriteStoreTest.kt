package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import kotlin.io.path.createTempDirectory
import kotlinx.coroutines.test.runTest

class MobileChapterWriteStoreTest {
    @Test
    fun `generated draft and full manifest survive a new store instance`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val first = MobileChapterWriteStore(directory).save(
                MobileChapterWriteRun(
                    id = runId,
                    projectId = "p1",
                    model = "deepseek-chat",
                    title = "断线重连",
                    content = "陆糖切断了病毒网络。",
                    state = MobileChapterWriteState.GENERATED,
                    manifest = manifest,
                ),
            )

            val recovered = MobileChapterWriteStore(directory).load(runId)

            assertNotNull(recovered)
            assertEquals(first.content, recovered.content)
            assertEquals(first.manifest.selectionFingerprint, recovered.manifest.selectionFingerprint)
            assertEquals("Outline: 断线重连", recovered.manifest.items.first().content)
            assertTrue(recovered.updatedAt.isNotBlank())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `commit transition and deterministic entity id make retries coalesce`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val store = MobileChapterWriteStore(directory)
            val generated = store.save(
                MobileChapterWriteRun(
                    id = runId,
                    projectId = "p1",
                    model = "deepseek-chat",
                    title = "断线重连",
                    content = "正文",
                    state = MobileChapterWriteState.GENERATED,
                    manifest = manifest,
                ),
            )
            val entityId = mobileChapterEntityId("p1", runId)
            store.transition(generated, MobileChapterWriteState.COMMITTING, chapterId = entityId)
            store.transition(generated, MobileChapterWriteState.COMMITTED, chapterId = entityId)

            val recovered = store.load(runId)
            assertNotNull(recovered)
            assertEquals(MobileChapterWriteState.COMMITTED, recovered.state)
            assertEquals(entityId, recovered.chapterId)
            assertEquals(entityId, mobileChapterEntityId("p1", runId))
            assertNotEquals(entityId, mobileChapterEntityId("p2", runId))
        } finally {
            directory.deleteRecursively()
        }
    }

    private fun manifest(): MobileContextManifest {
        val request = MobileContextRequest(
            outlineNodeId = "o1",
            targetChapterId = "",
            requirements = "切断病毒网络",
            involvedCharacters = listOf("陆糖"),
            characterLimit = 8,
            recentLimit = 3,
        )
        val item = MobileContextManifestItem(
            category = "target_outline",
            sourceType = "outline",
            sourceId = "o1",
            chunkId = null,
            title = "断线重连",
            content = "Outline: 断线重连",
            required = true,
            tier = 1,
            lexicalScore = null,
            recencyScore = null,
            structuralScore = 1.0,
            finalScore = 1.0,
            selectionReason = "required",
        )
        return MobileContextManifest(
            id = "manifest-1",
            projectId = "p1",
            model = "deepseek-chat",
            policyVersion = 1,
            indexVersion = 1,
            policySourceHash = "a".repeat(64),
            status = "ready",
            request = request,
            requestFingerprint = request.fingerprint("p1"),
            selectionFingerprint = mobileSha256(item.sourceHash),
            contextWindowTokens = 16_384,
            inputBudgetTokens = 8_000,
            outputReserveTokens = 4_000,
            safetyMarginTokens = 512,
            items = listOf(item),
            coverage = mapOf(
                "target_outline" to MobileContextCoverage(true, "covered", 1),
            ),
            warnings = listOf("lexical fallback"),
        )
    }
}
