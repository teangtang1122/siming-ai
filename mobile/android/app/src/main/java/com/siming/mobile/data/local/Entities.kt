package com.siming.mobile.data.local

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "replica_entities",
    indices = [
        Index(value = ["projectId", "entityType"]),
        Index(value = ["projectId", "dirty"]),
    ],
)
data class ReplicaEntity(
    @PrimaryKey val key: String,
    val projectId: String,
    val entityType: String,
    val entityId: String,
    val revision: Long,
    val operation: String,
    val payloadJson: String?,
    val contentHash: String,
    val serverModifiedAt: String,
    val dirty: Boolean = false,
    val conflicted: Boolean = false,
    val localModifiedAt: Long = System.currentTimeMillis(),
) {
    companion object {
        fun key(projectId: String, entityType: String, entityId: String) =
            "$projectId|$entityType|$entityId"
    }
}

@Entity(
    tableName = "sync_outbox",
    indices = [
        Index(value = ["state", "createdAt"]),
        Index(value = ["projectId", "entityType", "entityId", "state"]),
    ],
)
data class OutboxMutation(
    @PrimaryKey val mutationId: String,
    val projectId: String,
    val entityType: String,
    val entityId: String,
    val operation: String,
    val baseRevision: Long,
    val payloadJson: String?,
    val clientModifiedAt: String,
    val state: String = "pending",
    val sentPayloadHash: String? = null,
    val lastError: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)

@Entity(tableName = "gateway_connection")
data class GatewayConnection(
    @PrimaryKey val id: Int = 1,
    val baseUrl: String,
    val gatewayName: String,
    val gatewayFingerprint: String,
    val gatewayEncryptionPublicKey: String = "",
    val deviceId: String,
    val deviceRole: String,
    val protocolVersion: Int,
    val connectedAt: Long = System.currentTimeMillis(),
)

@Entity(tableName = "sync_cursor")
data class SyncCursor(
    @PrimaryKey val id: Int = 1,
    val cursor: Long = 0,
    val lastSuccessfulSyncAt: Long? = null,
    val lastError: String? = null,
)

@Entity(
    tableName = "local_conflicts",
    indices = [Index(value = ["projectId", "status"])],
)
data class LocalConflict(
    @PrimaryKey val id: String,
    val projectId: String,
    val entityType: String,
    val entityId: String,
    val clientPayloadJson: String?,
    val serverPayloadJson: String?,
    val serverRevision: Long,
    val status: String = "open",
    val createdAt: Long = System.currentTimeMillis(),
)

@Entity(
    tableName = "project_packages",
    indices = [
        Index(value = ["projectId"], unique = true),
        Index(value = ["syncState", "createdAt"]),
    ],
)
data class StoredProjectPackage(
    @PrimaryKey val idempotencyKey: String,
    val packageId: String,
    val projectId: String,
    val originalFilename: String,
    val localFilePath: String,
    val packageSha256: String,
    val profile: String,
    val requestedTitle: String?,
    val syncState: String = "pending",
    val lastError: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
    val uploadedAt: Long? = null,
)
