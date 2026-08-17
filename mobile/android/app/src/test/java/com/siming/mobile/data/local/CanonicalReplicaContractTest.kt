package com.siming.mobile.data.local

import kotlin.test.Test
import kotlin.test.assertEquals

class CanonicalReplicaContractTest {
    @Test
    fun characterHistoryDoesNotBecomeUnnamedCharacterCards() {
        val records = listOf(
            replica("character", "character-main", "character", "\"name\":\"陆糖\""),
            replica("character", "character-version", "character_version", "\"snapshot_json\":{}"),
        )

        assertEquals(
            listOf("character-main"),
            orderReplicaEntities("character", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun worldHistoryDoesNotBecomeUnnamedWorldCards() {
        val records = listOf(
            replica("world", "world-main", "world_entry", "\"title\":\"归墟\""),
            replica("world", "world-version", "world_version", "\"content\":\"旧版本\""),
        )

        assertEquals(
            listOf("world-main"),
            orderReplicaEntities("world", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun governanceListShowsNarrativeDebtInsteadOfInternalCheckpoints() {
        val records = listOf(
            replica("governance", "debt", "narrative_debt", "\"title\":\"回收病毒伏笔\""),
            replica("governance", "checkpoint", "narrative_checkpoint", "\"chapter_id\":\"c1\""),
            replica("governance", "metric", "chapter_quality_metric", "\"score\":80"),
        )

        assertEquals(
            listOf("debt"),
            orderReplicaEntities("governance", records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun standaloneAuthoringSnapshotDropsVersionRowsButKeepsDedicatedContextEntities() {
        val records = listOf(
            replica("character", "character-main", "character", "\"name\":\"陆糖\""),
            replica("character", "character-version", "character_version", "\"snapshot_json\":{}"),
            replica("world", "world-main", "world_entry", "\"title\":\"归墟\""),
            replica("world", "world-version", "world_version", "\"content\":\"旧版本\""),
            replica("summary", "summary-main", "chapter_summary", "\"chapter_id\":\"c1\""),
        )

        assertEquals(
            listOf("character-main", "world-main", "summary-main"),
            primaryAuthoringSnapshot(records).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun legacyPrimaryReplicaWithoutRecordTypeRemainsVisibleWhenWellFormed() {
        val record = replica("foreshadowing", "legacy", null, "\"title\":\"旧伏笔\"")

        assertEquals(
            listOf("legacy"),
            orderReplicaEntities("foreshadowing", listOf(record)).map(ReplicaEntity::entityId),
        )
    }

    @Test
    fun malformedLegacyRowsDoNotBecomeUnnamedCards() {
        val records = listOf(
            replica("character", "bad-character", null, "\"background\":\"旧结构\""),
            replica("world", "bad-world", null, "\"content\":\"旧结构\""),
            replica("foreshadowing", "bad-foreshadowing", null, "\"description\":\"旧结构\""),
        )

        assertEquals(emptyList(), orderReplicaEntities("character", records).map(ReplicaEntity::entityId))
        assertEquals(emptyList(), orderReplicaEntities("world", records).map(ReplicaEntity::entityId))
        assertEquals(emptyList(), orderReplicaEntities("foreshadowing", records).map(ReplicaEntity::entityId))
    }

    private fun replica(
        entityType: String,
        id: String,
        recordType: String?,
        fields: String,
    ): ReplicaEntity {
        val typeField = recordType?.let { "\"_record_type\":\"$it\"," }.orEmpty()
        return ReplicaEntity(
            key = ReplicaEntity.key("project", entityType, id),
            projectId = "project",
            entityType = entityType,
            entityId = id,
            revision = 1,
            operation = "upsert",
            payloadJson = "{$typeField$fields}",
            contentHash = id,
            serverModifiedAt = "2026-08-17T00:00:00Z",
            localModifiedAt = 1,
            dirty = false,
            conflicted = false,
        )
    }
}
