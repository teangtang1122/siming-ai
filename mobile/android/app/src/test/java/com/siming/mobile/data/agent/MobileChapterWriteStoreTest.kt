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
    fun `draft journal marks explicit author save without losing audit record`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val store = MobileChapterWriteStore(directory)
            store.save(
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

            val recovered = store.load(runId)
            assertNotNull(recovered)
            assertEquals(MobileChapterWriteState.GENERATED, recovered.state)
            assertEquals(
                setOf(
                    "generating",
                    "generated",
                    "cancelled",
                    "failed",
                    "saved",
                    "discarded",
                    "superseded",
                ),
                MobileChapterWriteState.ALL,
            )
            assertEquals(runId, store.latestGenerated("p1")?.id)
            assertEquals(MobileChapterWriteState.SAVED, store.markSaved(runId)?.state)
            assertEquals(null, store.latestGenerated("p1"))
            assertEquals(runId, mobileChapterWriteRunId("p1", "deepseek-chat", manifest))
            assertNotEquals(runId, mobileChapterWriteRunId("p2", "deepseek-chat", manifest))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `author discard releases standalone draft without deleting audit record`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val store = MobileChapterWriteStore(directory)
            store.save(
                MobileChapterWriteRun(
                    id = runId,
                    projectId = "p1",
                    model = "deepseek-chat",
                    title = "断线重连",
                    content = "作者决定不用的正文。",
                    state = MobileChapterWriteState.GENERATED,
                    manifest = manifest,
                ),
            )

            val discarded = store.markDiscarded(runId)

            assertEquals(MobileChapterWriteState.DISCARDED, discarded?.state)
            assertEquals(MobileChapterWriteState.DISCARDED, store.markDiscarded(runId)?.state)
            assertEquals(null, store.latestGenerated("p1"))
            assertEquals(MobileChapterWriteState.DISCARDED, store.load(runId)?.state)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `stale standalone draft can be superseded without blocking a new write`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val store = MobileChapterWriteStore(directory)
            store.save(
                MobileChapterWriteRun(
                    id = runId,
                    projectId = "p1",
                    model = "deepseek-chat",
                    title = "断线重连",
                    content = "已经存在的未保存草稿。",
                    state = MobileChapterWriteState.GENERATED,
                    manifest = manifest,
                ),
            )

            assertEquals(runId, store.latestGenerated("p1")?.id)
            val released = store.markSuperseded(runId, "对应大纲已有正式章节")

            assertEquals(MobileChapterWriteState.SUPERSEDED, released?.state)
            assertEquals("对应大纲已有正式章节", released?.error)
            assertEquals(null, store.latestGenerated("p1"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun `cancelled partial text remains a resumable checkpoint`() = runTest {
        val directory = createTempDirectory("siming-mobile-write-").toFile()
        try {
            val manifest = manifest()
            val runId = mobileChapterWriteRunId("p1", "deepseek-chat", manifest)
            val store = MobileChapterWriteStore(directory)
            store.save(
                MobileChapterWriteRun(
                    id = runId,
                    projectId = "p1",
                    model = "deepseek-chat",
                    title = "断线重连",
                    content = "已经确认的前半段",
                    state = MobileChapterWriteState.CANCELLED,
                    manifest = manifest,
                    error = "用户停止",
                ),
            )

            val checkpoint = MobileChapterWriteStore(directory).load(runId)

            assertNotNull(checkpoint)
            assertEquals(MobileChapterWriteState.CANCELLED, checkpoint.state)
            assertEquals("已经确认的前半段", checkpoint.content)
            val completed = store.save(
                checkpoint.copy(
                    content = checkpoint.content + "，随后从检查点继续。",
                    state = MobileChapterWriteState.GENERATED,
                    error = null,
                ),
            )
            assertEquals(runId, completed.id)
            assertEquals("已经确认的前半段，随后从检查点继续。", completed.content)
        } finally {
            directory.deleteRecursively()
        }
    }

    private fun manifest(): MobileContextManifest {
        val request = MobileContextRequest(
            outlineNodeId = "o1",
            targetChapterId = "",
            requirements = "切断病毒网络",
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
            softInputTargetTokens = 8_000,
            outputReserveTokens = 4_000,
            safetyMarginTokens = 512,
            items = listOf(item),
            coverage = mapOf(
                "target_outline" to MobileContextCoverage(true, "covered", 1),
            ),
            warnings = listOf("lexical fallback"),
            selectionToken = "selection-token-1",
        )
    }
}
