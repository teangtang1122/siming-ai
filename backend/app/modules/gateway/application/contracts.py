"""Versioned public contracts shared by desktop, Gateway, and Android."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

SYNC_PROTOCOL_VERSION = 1
SYNC_ENTITY_TYPES = (
    "authoring_command",
    "project",
    "chapter",
    "chapter_version",
    "outline",
    "character",
    "character_ai_config",
    "character_alias",
    "character_relation",
    "world",
    "world_relation",
    "summary",
    "timeline",
    "foreshadowing",
    "governance",
)

EntityType = Literal[
    "authoring_command",
    "project",
    "chapter",
    "chapter_version",
    "outline",
    "character",
    "character_ai_config",
    "character_alias",
    "character_relation",
    "world",
    "world_relation",
    "summary",
    "timeline",
    "foreshadowing",
    "governance",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_serializer("*", when_used="json")
    def serialize_utc_datetime(self, value: Any) -> Any:
        """Make every public protocol timestamp unambiguous across time zones."""

        if not isinstance(value, datetime):
            return value
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class DeviceCapabilities(StrictContract):
    protocol_version: int = Field(default=SYNC_PROTOCOL_VERSION, ge=1)
    offline_read: bool = True
    offline_write: bool = True
    cloud_ai: bool = True
    local_ai: bool = False
    cli_worker: bool = False
    mcp: bool = False
    training: bool = False


class RuntimeCapabilities(StrictContract):
    runtime_profile: Literal["desktop-standalone", "gateway"]
    sync_protocol_version: int = SYNC_PROTOCOL_VERSION
    minimum_sync_protocol_version: int = SYNC_PROTOCOL_VERSION
    gateway_authoritative: bool
    pairing_enabled: bool
    offline_replica_supported: bool = True
    local_ai: bool
    cli_worker: bool
    mcp: bool
    training: bool
    requires_gateway_for_ai: bool
    supported_entity_types: list[str] = Field(default_factory=lambda: list(SYNC_ENTITY_TYPES))


class PairingStartResponse(StrictContract):
    pairing_id: str
    pairing_secret: str
    gateway_url: str
    gateway_name: str
    gateway_public_key: str
    gateway_encryption_public_key: str
    gateway_fingerprint: str
    expires_at: datetime
    qr_payload: dict[str, Any]


class PairingCompleteRequest(StrictContract):
    pairing_id: str = Field(min_length=36, max_length=36)
    pairing_secret: str = Field(min_length=24, max_length=200)
    device_name: str = Field(min_length=1, max_length=120)
    platform: Literal["android", "windows", "linux", "macos", "ios", "web", "node"]
    public_key: str | None = Field(default=None, max_length=4096)
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)


class TokenPair(StrictContract):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class PairingCompleteResponse(StrictContract):
    status: Literal["pending_approval", "approved", "consumed", "expired"]
    pairing_id: str
    device_id: str | None = None
    device_role: Literal["owner", "member", "compute"] | None = None
    tokens: TokenPair | None = None


class PairingApproveRequest(StrictContract):
    pairing_id: str = Field(min_length=36, max_length=36)


class PairingStatusResponse(StrictContract):
    pairing_id: str
    status: Literal["created", "pending_approval", "approved", "consumed", "expired"]
    expires_at: datetime
    device_id: str | None = None
    device_name: str | None = None
    device_platform: str | None = None


class RefreshTokenRequest(StrictContract):
    refresh_token: str = Field(min_length=24, max_length=200)


class GatewayAdminLoginRequest(StrictContract):
    bootstrap_key: str = Field(min_length=12, max_length=512)


class GatewayAdminSessionView(StrictContract):
    device_role: Literal["owner"] = "owner"
    expires_at: datetime


class GatewayAdminSessionStatusView(StrictContract):
    authenticated: bool


class DeviceView(StrictContract):
    id: str
    name: str
    platform: str
    role: str
    status: str
    public_key_fingerprint: str | None = None
    capabilities: dict[str, Any]
    protocol_version: int
    created_at: datetime
    approved_at: datetime | None = None
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None


class SyncProjectView(StrictContract):
    project_id: str
    title: str
    status: Literal["not_enabled", "migrating", "enabled", "disabled", "error"]
    entity_count: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    aggregate_hash: str | None = None
    initial_revision: int = 0
    enabled_at: datetime | None = None
    verified_at: datetime | None = None
    last_error: str | None = None


class SyncConflictView(StrictContract):
    id: str
    mutation_id: str
    project_id: str
    project_title: str
    entity_type: str
    entity_id: str
    device_id: str | None = None
    device_name: str | None = None
    client_base_revision: int
    server_revision: int
    client_operation: Literal["upsert", "delete"]
    server_operation: Literal["upsert", "delete"]
    client_payload: dict[str, Any] | None = None
    server_payload: dict[str, Any] | None = None
    status: Literal["open", "resolved"]
    resolution: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class SyncConflictResolutionRequest(StrictContract):
    choice: Literal["server", "client", "custom"]
    custom_operation: Literal["upsert", "delete"] | None = None
    custom_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_custom_resolution(self) -> SyncConflictResolutionRequest:
        if self.choice != "custom":
            if self.custom_operation is not None or self.custom_payload is not None:
                raise ValueError("custom fields are only valid for a custom resolution")
            return self
        if self.custom_operation is None:
            raise ValueError("custom_operation is required for a custom resolution")
        if self.custom_operation == "upsert" and self.custom_payload is None:
            raise ValueError("custom_payload is required for a custom upsert")
        if self.custom_operation == "delete" and self.custom_payload is not None:
            raise ValueError("custom_payload must be omitted for a custom delete")
        return self


class SyncMutation(StrictContract):
    mutation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")
    project_id: str = Field(min_length=1, max_length=64)
    entity_type: EntityType
    entity_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")
    operation: Literal["upsert", "delete"]
    base_revision: int = Field(ge=0)
    payload: dict[str, Any] | None = None
    client_modified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_payload_for_operation(self) -> SyncMutation:
        if self.operation == "upsert" and self.payload is None:
            raise ValueError("payload is required for an upsert mutation")
        if self.operation == "delete" and self.payload is not None:
            raise ValueError("payload must be omitted for a delete mutation")
        return self


class SyncPushRequest(StrictContract):
    protocol_version: int = Field(default=SYNC_PROTOCOL_VERSION, ge=1)
    mutations: list[SyncMutation] = Field(min_length=1, max_length=100)


class MutationResult(StrictContract):
    mutation_id: str
    status: Literal["applied", "duplicate", "conflict", "rejected"]
    revision: int | None = None
    conflict_id: str | None = None
    message: str | None = None
    server_snapshot: dict[str, Any] | None = None


class SyncPushResponse(StrictContract):
    protocol_version: int = SYNC_PROTOCOL_VERSION
    cursor: int
    results: list[MutationResult]


class SyncBootstrapRequest(StrictContract):
    protocol_version: int = Field(default=SYNC_PROTOCOL_VERSION, ge=1)
    project_ids: list[str] = Field(min_length=1, max_length=20)


class SyncEntitySnapshot(StrictContract):
    project_id: str
    entity_type: str
    entity_id: str
    revision: int
    operation: Literal["upsert", "delete"]
    payload: dict[str, Any] | None = None
    content_hash: str
    server_modified_at: datetime


class SyncBootstrapResponse(StrictContract):
    protocol_version: int = SYNC_PROTOCOL_VERSION
    cursor: int
    projects: list[str]
    entities: list[SyncEntitySnapshot]


class SyncChangeView(StrictContract):
    revision: int
    mutation_id: str
    project_id: str
    entity_type: str
    entity_id: str
    operation: Literal["upsert", "delete"]
    base_revision: int
    payload: dict[str, Any] | None = None
    content_hash: str
    changed_at: datetime


class SyncPullResponse(StrictContract):
    protocol_version: int = SYNC_PROTOCOL_VERSION
    from_cursor: int
    next_cursor: int
    has_more: bool
    changes: list[SyncChangeView]


class SyncStatusResponse(StrictContract):
    protocol_version: int = SYNC_PROTOCOL_VERSION
    cursor: int
    enabled_projects: int
    open_conflicts: int
    active_devices: int
    tombstone_retention_days: int


__all__ = [
    "DeviceCapabilities",
    "DeviceView",
    "GatewayAdminLoginRequest",
    "GatewayAdminSessionView",
    "GatewayAdminSessionStatusView",
    "MutationResult",
    "PairingApproveRequest",
    "PairingCompleteRequest",
    "PairingCompleteResponse",
    "PairingStartResponse",
    "PairingStatusResponse",
    "RefreshTokenRequest",
    "RuntimeCapabilities",
    "SYNC_ENTITY_TYPES",
    "SYNC_PROTOCOL_VERSION",
    "SyncBootstrapRequest",
    "SyncBootstrapResponse",
    "SyncChangeView",
    "SyncConflictResolutionRequest",
    "SyncConflictView",
    "SyncEntitySnapshot",
    "SyncMutation",
    "SyncPullResponse",
    "SyncProjectView",
    "SyncPushRequest",
    "SyncPushResponse",
    "SyncStatusResponse",
    "TokenPair",
]
