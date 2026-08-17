package com.siming.mobile.data.local

import kotlin.test.Test
import kotlin.test.assertEquals

class ReplicaOrderingTest {
    @Test
    fun unnumberedChaptersUseCanonicalCreationTimeInsteadOfLocalBootstrapTime() {
        val records = listOf(
            chapter("closing", "收束", "2026-08-16T10:00:03.000000Z", 9_999),
            chapter("opening", "开端", "2026-08-16T10:00:01.000000Z", 9_997),
            chapter("turning", "转折", "2026-08-16T10:00:02.000000Z", 9_998),
        )

        assertEquals(
            listOf("opening", "turning", "closing"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun syncedNumberedChaptersPreferSemanticNumberOverCreationTime() {
        val records = listOf(
            chapter(
                "thirty-four",
                "第34章 新朋友·壹（小七）",
                "2026-08-16T10:00:01.000000Z",
                4_000,
            ),
            chapter("eleven", "第十一章 日常", "2026-08-16T10:00:02.000000Z", 3_000),
            chapter("four", "第四章 暗流", "2026-08-16T10:00:03.000000Z", 2_000),
            chapter("three", "第三章 打回去", "2026-08-16T10:00:04.000000Z", 1_000),
        )

        assertEquals(
            listOf("three", "four", "eleven", "thirty-four"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun semanticSortingKeepsUnnumberedChapterSlots() {
        val records = listOf(
            chapter("epilogue", "尾声", "2026-08-16T10:00:05.000000Z", 5_000),
            chapter("three", "第三章", "2026-08-16T10:00:04.000000Z", 4_000),
            chapter("interlude", "间章", "2026-08-16T10:00:03.000000Z", 3_000),
            chapter("thirty-four", "第34章", "2026-08-16T10:00:02.000000Z", 2_000),
            chapter("prologue", "序章", "2026-08-16T10:00:01.000000Z", 1_000),
        )

        assertEquals(
            listOf("prologue", "three", "interlude", "thirty-four", "epilogue"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun locallyImportedChaptersFallBackToNumberThenCreationTime() {
        val records = listOf(
            chapter("ten", "第10章", null, 3_000),
            chapter("two", "第2章", null, 2_000),
            chapter("one", "第1章", null, 1_000),
        )

        assertEquals(
            listOf("one", "two", "ten"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun legacyChineseChapterTitlesFallBackToSemanticNumber() {
        val records = listOf(
            chapter("thirty-four", "第34章 新朋友·壹（小七）", null, 4_000),
            chapter("eleven", "第十一章 日常", null, 3_000),
            chapter("four", "第四章 暗流", null, 2_000),
            chapter("three", "第三章 打回去", null, 1_000),
        )

        assertEquals(
            listOf("three", "four", "eleven", "thirty-four"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun chapterNumberFallbackAcceptsFullWidthAndPositionalChineseDigits() {
        val records = listOf(
            chapter("one-hundred-three", "第一〇三章", null, 3_000),
            chapter("twenty-five", "二十五章", null, 2_000),
            chapter("seven", "第 〇 七 章", null, 1_000),
        )

        assertEquals(
            listOf("seven", "twenty-five", "one-hundred-three"),
            orderReplicaEntities("chapter", records).map(ReplicaEntity::entityId),
        )
    }

    private fun chapter(id: String, title: String, createdAt: String?, localModifiedAt: Long): ReplicaEntity {
        val createdAtField = createdAt?.let { "\"created_at\":\"$it\"," } ?: ""
        return ReplicaEntity(
            key = ReplicaEntity.key("project", "chapter", id),
            projectId = "project",
            entityType = "chapter",
            entityId = id,
            revision = 1,
            operation = "upsert",
            payloadJson = "{$createdAtField\"title\":\"$title\"}",
            contentHash = id,
            serverModifiedAt = "2026-08-16T10:00:00Z",
            localModifiedAt = localModifiedAt,
        )
    }
}
