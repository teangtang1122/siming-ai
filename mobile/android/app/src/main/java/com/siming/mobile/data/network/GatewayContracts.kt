package com.siming.mobile.data.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class ApiEnvelope<T>(
    val code: Int,
    val message: String = "",
    val data: T,
)

@Serializable
data class DeviceCapabilities(
    @SerialName("protocol_version") val protocolVersion: Int = 1,
    @SerialName("offline_read") val offlineRead: Boolean = true,
    @SerialName("offline_write") val offlineWrite: Boolean = true,
    @SerialName("cloud_ai") val cloudAi: Boolean = true,
    @SerialName("local_ai") val localAi: Boolean = false,
    @SerialName("cli_worker") val cliWorker: Boolean = false,
    val mcp: Boolean = false,
    val training: Boolean = false,
)

@Serializable
data class PairingCompleteRequest(
    @SerialName("pairing_id") val pairingId: String,
    @SerialName("pairing_secret") val pairingSecret: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerialName("public_key") val publicKey: String,
    val capabilities: DeviceCapabilities = DeviceCapabilities(),
)

@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("access_expires_at") val accessExpiresAt: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("refresh_expires_at") val refreshExpiresAt: String,
)

@Serializable
data class PairingCompleteResponse(
    val status: String,
    @SerialName("pairing_id") val pairingId: String,
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("device_role") val deviceRole: String? = null,
    val tokens: TokenPair? = null,
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class RemoteSyncProject(
    @SerialName("project_id") val projectId: String,
    val title: String,
    val status: String,
    @SerialName("entity_count") val entityCount: Int = 0,
    val counts: Map<String, Int> = emptyMap(),
    @SerialName("aggregate_hash") val aggregateHash: String? = null,
    @SerialName("initial_revision") val initialRevision: Long = 0,
    @SerialName("verified_at") val verifiedAt: String? = null,
)

@Serializable
data class SyncBootstrapRequest(
    @SerialName("protocol_version") val protocolVersion: Int = 1,
    @SerialName("project_ids") val projectIds: List<String>,
)

@Serializable
data class SyncSnapshot(
    @SerialName("project_id") val projectId: String,
    @SerialName("entity_type") val entityType: String,
    @SerialName("entity_id") val entityId: String,
    val revision: Long,
    val operation: String,
    val payload: JsonObject? = null,
    @SerialName("content_hash") val contentHash: String,
    @SerialName("server_modified_at") val serverModifiedAt: String,
)

@Serializable
data class SyncBootstrapResponse(
    @SerialName("protocol_version") val protocolVersion: Int,
    val cursor: Long,
    val projects: List<String>,
    val entities: List<SyncSnapshot>,
)

@Serializable
data class SyncMutationRequest(
    @SerialName("mutation_id") val mutationId: String,
    @SerialName("project_id") val projectId: String,
    @SerialName("entity_type") val entityType: String,
    @SerialName("entity_id") val entityId: String,
    val operation: String,
    @SerialName("base_revision") val baseRevision: Long,
    val payload: JsonObject? = null,
    @SerialName("client_modified_at") val clientModifiedAt: String,
)

@Serializable
data class SyncPushRequest(
    @SerialName("protocol_version") val protocolVersion: Int = 1,
    val mutations: List<SyncMutationRequest>,
)

@Serializable
data class MutationResult(
    @SerialName("mutation_id") val mutationId: String,
    val status: String,
    val revision: Long? = null,
    @SerialName("conflict_id") val conflictId: String? = null,
    val message: String? = null,
    @SerialName("server_snapshot") val serverSnapshot: JsonObject? = null,
)

@Serializable
data class SyncPushResponse(
    @SerialName("protocol_version") val protocolVersion: Int,
    val cursor: Long,
    val results: List<MutationResult>,
)

@Serializable
data class SyncChange(
    val revision: Long,
    @SerialName("mutation_id") val mutationId: String,
    @SerialName("project_id") val projectId: String,
    @SerialName("entity_type") val entityType: String,
    @SerialName("entity_id") val entityId: String,
    val operation: String,
    @SerialName("base_revision") val baseRevision: Long,
    val payload: JsonObject? = null,
    @SerialName("content_hash") val contentHash: String,
    @SerialName("changed_at") val changedAt: String,
)

@Serializable
data class SyncPullResponse(
    @SerialName("protocol_version") val protocolVersion: Int,
    @SerialName("from_cursor") val fromCursor: Long,
    @SerialName("next_cursor") val nextCursor: Long,
    @SerialName("has_more") val hasMore: Boolean,
    val changes: List<SyncChange>,
)

@Serializable
data class RemoteConflict(
    val id: String,
    @SerialName("project_id") val projectId: String,
    @SerialName("entity_type") val entityType: String,
    @SerialName("entity_id") val entityId: String,
    @SerialName("client_payload") val clientPayload: JsonObject? = null,
    @SerialName("server_payload") val serverPayload: JsonObject? = null,
    @SerialName("server_revision") val serverRevision: Long,
    val status: String,
)

@Serializable
data class ConflictResolutionRequest(val choice: String)

@Serializable
data class MobileProviderEnvelope(
    val version: Int = 1,
    @SerialName("ephemeral_public_key") val ephemeralPublicKey: String,
    val nonce: String,
    val ciphertext: String,
)

@Serializable
data class WorkspaceAssistantRequest(
    val message: String,
    @SerialName("conversation_id") val conversationId: String? = null,
    @SerialName("active_chapter_draft_id") val activeChapterDraftId: String? = null,
    @SerialName("model_route") val modelRoute: String = "pc",
    @SerialName("mobile_provider") val mobileProvider: MobileProviderEnvelope? = null,
)
