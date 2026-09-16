package com.siming.mobile.data.local

import androidx.room.Embedded

/** A single snapshot of the evidence used by the library and deletion boundary. */
data class ProjectSyncRecord(
    @Embedded val project: ReplicaEntity,
    val packageSyncState: String?,
    val packageLastError: String?,
    val packageUploadedAt: Long?,
    val hasUnsentCreation: Boolean,
    val creationWasSent: Boolean,
)

enum class ProjectSyncStatus {
    LOCAL_ONLY,
    SYNC_PENDING,
    SYNCED,
    UNCONFIRMED,
}

val ProjectSyncRecord.syncStatus: ProjectSyncStatus
    get() = when {
        project.revision > 0 || packageUploadedAt != null || packageSyncState == "succeeded" ->
            if (project.dirty || packageSyncState in setOf("pending", "uploading")) {
                ProjectSyncStatus.SYNC_PENDING
            } else {
                ProjectSyncStatus.SYNCED
            }
        // An interrupted request may already have committed on the Gateway.
        creationWasSent -> ProjectSyncStatus.UNCONFIRMED
        packageSyncState != null ->
            if (packageSyncState == "pending" && packageLastError == null) {
                ProjectSyncStatus.LOCAL_ONLY
            } else {
                ProjectSyncStatus.UNCONFIRMED
            }
        hasUnsentCreation -> ProjectSyncStatus.LOCAL_ONLY
        else -> ProjectSyncStatus.UNCONFIRMED
    }

internal const val PROJECT_SYNC_RECORDS_QUERY = """
    SELECT project.*,
        imported.syncState AS packageSyncState,
        imported.lastError AS packageLastError,
        imported.uploadedAt AS packageUploadedAt,
        EXISTS(SELECT 1 FROM sync_outbox AS mutation
            WHERE mutation.projectId = project.projectId AND mutation.entityType = 'project'
                AND mutation.entityId = project.entityId AND mutation.operation = 'upsert'
                AND mutation.baseRevision = 0 AND mutation.state = 'pending'
                AND mutation.sentPayloadHash IS NULL) AS hasUnsentCreation,
        EXISTS(SELECT 1 FROM sync_outbox AS mutation
            WHERE mutation.projectId = project.projectId AND mutation.entityType = 'project'
                AND mutation.entityId = project.entityId
                AND (mutation.sentPayloadHash IS NOT NULL OR mutation.state = 'sending'
                    OR mutation.baseRevision > 0)) AS creationWasSent
    FROM replica_entities AS project
    LEFT JOIN project_packages AS imported ON imported.projectId = project.projectId
    WHERE project.entityType = 'project' AND project.operation = 'upsert'
"""

enum class ProjectDeletionResult {
    LOCAL_ONLY,
    ALREADY_ABSENT,
}
