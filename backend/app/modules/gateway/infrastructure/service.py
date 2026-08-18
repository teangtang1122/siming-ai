"""Secure pairing, opaque-token authentication, and revisioned sync services."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.core.crypto import decrypt, encrypt
from app.core.exceptions import AppException, NotFoundError, UnauthorizedError, ValidationError
from app.database.backup import backup_sqlite_database
from app.modules.gateway.application.contracts import (
    SYNC_PROTOCOL_VERSION,
    DeviceView,
    MutationResult,
    PairingCompleteRequest,
    PairingCompleteResponse,
    PairingStartResponse,
    PairingStatusResponse,
    SyncBootstrapResponse,
    SyncChangeView,
    SyncConflictResolutionRequest,
    SyncConflictView,
    SyncEntitySnapshot,
    SyncMutation,
    SyncProjectView,
    SyncPullResponse,
    SyncPushResponse,
    SyncStatusResponse,
)
from app.modules.gateway.infrastructure.models import (
    GatewayAccessToken,
    GatewayDevice,
    GatewayIdentity,
    GatewayPairingSession,
    GatewayRefreshToken,
    SyncChange,
    SyncConflict,
    SyncEntityState,
    SyncProject,
)
from app.services.gateway_legacy_replication import (
    Project,
    apply_domain_mutation,
    project_snapshots,
)

from .mobile_provider_crypto import gateway_encryption_public_key
from .mutation_service import GatewayMutationApplier
from .support import (
    MAX_ENTITY_PAYLOAD_BYTES,
    GatewayAuthContext,
    canonical_payload,
    payload_hash,
    require_protocol,
    token_digest,
    utcnow,
)
from .token_service import GatewayTokenMixin


class GatewayService(GatewayTokenMixin):
    def __init__(
        self,
        db: Session,
        *,
        pairing_ttl_minutes: int = 10,
        access_ttl_minutes: int = 15,
        refresh_ttl_days: int = 30,
        tombstone_retention_days: int = 90,
    ) -> None:
        self.db = db
        self.pairing_ttl_minutes = pairing_ttl_minutes
        self.access_ttl_minutes = access_ttl_minutes
        self.refresh_ttl_days = refresh_ttl_days
        self.tombstone_retention_days = tombstone_retention_days

    def ensure_identity(self, *, display_name: str = "司命 Gateway") -> GatewayIdentity:
        identity = self.db.query(GatewayIdentity).order_by(GatewayIdentity.created_at).first()
        if identity is not None:
            return identity

        private_key = Ed25519PrivateKey.generate()
        private_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_text = base64.urlsafe_b64encode(public_raw).decode("ascii")
        identity = GatewayIdentity(
            instance_id=secrets.token_hex(16),
            display_name=display_name,
            public_key=public_text,
            private_key_encrypted=encrypt(base64.urlsafe_b64encode(private_raw).decode("ascii")),
            fingerprint=hashlib.sha256(public_raw).hexdigest(),
        )
        self.db.add(identity)
        self.db.flush()
        return identity

    def _sign_pairing_payload(self, identity: GatewayIdentity, payload: dict[str, Any]) -> str:
        private_raw = base64.urlsafe_b64decode(decrypt(identity.private_key_encrypted))
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
        signature = private_key.sign(canonical_payload(payload))
        return base64.urlsafe_b64encode(signature).decode("ascii")

    def start_pairing(
        self,
        *,
        gateway_url: str,
        created_from: str | None,
        created_by_device_id: str | None = None,
        display_name: str = "司命 Gateway",
    ) -> PairingStartResponse:
        identity = self.ensure_identity(display_name=display_name)
        now = utcnow()
        self.db.query(GatewayPairingSession).filter(
            GatewayPairingSession.expires_at <= now,
            GatewayPairingSession.status.in_(["created", "pending"]),
        ).update({"status": "expired"}, synchronize_session=False)

        raw_secret = "smp_" + secrets.token_urlsafe(32)
        session = GatewayPairingSession(
            secret_hash=token_digest(raw_secret),
            created_by_device_id=created_by_device_id,
            created_from=(created_from or "")[:80] or None,
            expires_at=now + timedelta(minutes=self.pairing_ttl_minutes),
        )
        self.db.add(session)
        self.db.flush()
        unsigned_payload = {
            "type": "siming-gateway-pairing",
            "protocol_version": SYNC_PROTOCOL_VERSION,
            "gateway_url": gateway_url.rstrip("/"),
            "gateway_name": identity.display_name,
            "gateway_public_key": identity.public_key,
            "gateway_encryption_public_key": gateway_encryption_public_key(identity),
            "gateway_fingerprint": identity.fingerprint,
            "pairing_id": session.id,
            "pairing_secret": raw_secret,
            "expires_at": session.expires_at.isoformat(timespec="seconds") + "Z",
        }
        qr_payload = {
            **unsigned_payload,
            "signature": self._sign_pairing_payload(identity, unsigned_payload),
        }
        commit_session(self.db)
        return PairingStartResponse(
            pairing_id=session.id,
            pairing_secret=raw_secret,
            gateway_url=gateway_url.rstrip("/"),
            gateway_name=identity.display_name,
            gateway_public_key=identity.public_key,
            gateway_encryption_public_key=gateway_encryption_public_key(identity),
            gateway_fingerprint=identity.fingerprint,
            expires_at=session.expires_at,
            qr_payload=qr_payload,
        )

    def complete_pairing(self, payload: PairingCompleteRequest) -> PairingCompleteResponse:
        session = self.db.get(GatewayPairingSession, payload.pairing_id)
        if session is None or not hmac.compare_digest(
            session.secret_hash,
            token_digest(payload.pairing_secret),
        ):
            raise UnauthorizedError("配对信息无效或已失效")

        now = utcnow()
        if session.expires_at <= now or session.status == "expired":
            session.status = "expired"
            commit_session(self.db)
            return PairingCompleteResponse(status="expired", pairing_id=session.id)
        if session.status == "consumed":
            return PairingCompleteResponse(
                status="consumed",
                pairing_id=session.id,
                device_id=session.requested_device_id,
            )

        device: GatewayDevice | None = None
        if session.requested_device_id:
            device = self.db.get(GatewayDevice, session.requested_device_id)
        key_fingerprint = (
            hashlib.sha256(payload.public_key.encode("utf-8")).hexdigest()
            if payload.public_key
            else None
        )
        if device is None:
            device = GatewayDevice(
                name=payload.device_name,
                platform=payload.platform,
                role="compute" if payload.capabilities.cli_worker else "member",
                status="pending",
                public_key=payload.public_key,
                public_key_fingerprint=key_fingerprint,
                capabilities_json=payload.capabilities.model_dump(),
                protocol_version=payload.capabilities.protocol_version,
            )
            self.db.add(device)
            self.db.flush()
            session.requested_device_id = device.id
            session.status = "pending"
            commit_session(self.db)
            return PairingCompleteResponse(
                status="pending_approval",
                pairing_id=session.id,
                device_id=device.id,
                device_role=device.role,
            )

        if (
            device.name != payload.device_name
            or device.platform != payload.platform
            or device.public_key_fingerprint != key_fingerprint
        ):
            raise UnauthorizedError("配对设备信息与首次申请不一致")

        if session.status != "approved" or device.status != "approved":
            return PairingCompleteResponse(
                status="pending_approval",
                pairing_id=session.id,
                device_id=device.id,
                device_role=device.role,
            )

        tokens = self._issue_token_pair(device)
        session.status = "consumed"
        session.consumed_at = now
        commit_session(self.db)
        return PairingCompleteResponse(
            status="approved",
            pairing_id=session.id,
            device_id=device.id,
            device_role=device.role,
            tokens=tokens,
        )

    def approve_pairing(self, pairing_id: str) -> PairingCompleteResponse:
        session = self.db.get(GatewayPairingSession, pairing_id)
        if session is None:
            raise NotFoundError("未找到配对申请")
        now = utcnow()
        if session.expires_at <= now:
            session.status = "expired"
            commit_session(self.db)
            raise ValidationError("配对申请已过期，请重新生成二维码")
        if not session.requested_device_id:
            raise ValidationError("手机尚未提交配对申请")
        device = self.db.get(GatewayDevice, session.requested_device_id)
        if device is None or device.status == "revoked":
            raise ValidationError("配对设备已不存在或已撤销")

        owner_exists = (
            self.db.query(GatewayDevice)
            .filter(GatewayDevice.role == "owner", GatewayDevice.status == "approved")
            .first()
            is not None
        )
        if not owner_exists:
            device.role = "owner"
        device.status = "approved"
        device.approved_at = device.approved_at or now
        session.status = "approved"
        session.approved_at = now
        commit_session(self.db)
        return PairingCompleteResponse(
            status="approved",
            pairing_id=session.id,
            device_id=device.id,
            device_role=device.role,
        )

    def pairing_status(self, pairing_id: str) -> PairingStatusResponse:
        session = self.db.get(GatewayPairingSession, pairing_id)
        if session is None:
            raise NotFoundError("未找到配对申请")
        now = utcnow()
        if session.expires_at <= now and session.status not in {"consumed", "expired"}:
            session.status = "expired"
            commit_session(self.db)
        device = (
            self.db.get(GatewayDevice, session.requested_device_id)
            if session.requested_device_id
            else None
        )
        status = "pending_approval" if session.status == "pending" else session.status
        return PairingStatusResponse(
            pairing_id=session.id,
            status=status,
            expires_at=session.expires_at,
            device_id=device.id if device else None,
            device_name=device.name if device else None,
            device_platform=device.platform if device else None,
        )

    def has_owner(self) -> bool:
        return (
            self.db.query(GatewayDevice)
            .filter(GatewayDevice.role == "owner", GatewayDevice.status == "approved")
            .first()
            is not None
        )

    def ensure_local_owner_device(self) -> GatewayAuthContext:
        """Represent the built-in desktop process without issuing a bearer token."""

        device = (
            self.db.query(GatewayDevice)
            .filter(
                GatewayDevice.platform == "windows",
                GatewayDevice.public_key_fingerprint == "local-desktop-process",
            )
            .first()
        )
        now = utcnow()
        if device is None:
            owner_exists = self.has_owner()
            device = GatewayDevice(
                name="本机司命桌面端",
                platform="windows",
                role="member" if owner_exists else "owner",
                status="approved",
                public_key_fingerprint="local-desktop-process",
                capabilities_json={
                    "protocol_version": SYNC_PROTOCOL_VERSION,
                    "offline_read": True,
                    "offline_write": True,
                    "cloud_ai": True,
                    "local_ai": True,
                    "cli_worker": True,
                    "mcp": True,
                    "training": True,
                },
                protocol_version=SYNC_PROTOCOL_VERSION,
                approved_at=now,
                last_seen_at=now,
            )
            self.db.add(device)
        else:
            device.last_seen_at = now
        commit_session(self.db)
        return GatewayAuthContext(
            device_id=device.id,
            role=device.role,
            platform=device.platform,
        )

    def list_devices(self) -> list[DeviceView]:
        devices = self.db.query(GatewayDevice).order_by(GatewayDevice.created_at).all()
        return [
            DeviceView(
                id=device.id,
                name=device.name,
                platform=device.platform,
                role=device.role,
                status=device.status,
                public_key_fingerprint=device.public_key_fingerprint,
                capabilities=device.capabilities_json or {},
                protocol_version=device.protocol_version,
                created_at=device.created_at,
                approved_at=device.approved_at,
                revoked_at=device.revoked_at,
                last_seen_at=device.last_seen_at,
            )
            for device in devices
        ]

    @staticmethod
    def _project_view(project: Project, config: SyncProject | None) -> SyncProjectView:
        manifest = config.manifest_json if config and config.manifest_json else {}
        return SyncProjectView(
            project_id=project.id,
            title=project.title,
            status=config.status if config is not None else "not_enabled",
            entity_count=int(manifest.get("entity_count") or 0),
            counts={
                str(key): int(value) for key, value in dict(manifest.get("counts") or {}).items()
            },
            aggregate_hash=manifest.get("aggregate_hash"),
            initial_revision=int(config.initial_revision or 0) if config else 0,
            enabled_at=config.enabled_at if config else None,
            verified_at=config.verified_at if config else None,
            last_error=config.last_error if config else None,
        )

    def list_sync_projects(self) -> list[SyncProjectView]:
        projects = self.db.query(Project).order_by(Project.updated_at.desc()).all()
        configs = {config.project_id: config for config in self.db.query(SyncProject).all()}
        return [self._project_view(project, configs.get(project.id)) for project in projects]

    def enabled_project_ids(self) -> set[str]:
        """Return projects explicitly enabled for Gateway synchronization."""

        return {
            row.project_id
            for row in self.db.query(SyncProject.project_id)
            .filter(SyncProject.status == "enabled")
            .all()
        }

    def _refresh_project_manifest(self, project_id: str) -> None:
        """Keep public verification counts and hashes aligned with live sync state."""

        config = self.db.get(SyncProject, project_id)
        if config is None or config.status != "enabled":
            return
        rows = (
            self.db.query(SyncEntityState)
            .filter(
                SyncEntityState.project_id == project_id,
                SyncEntityState.is_deleted.is_(False),
            )
            .order_by(SyncEntityState.entity_type, SyncEntityState.entity_id)
            .all()
        )
        counts: dict[str, int] = {}
        hashes: list[str] = []
        for row in rows:
            counts[row.entity_type] = counts.get(row.entity_type, 0) + 1
            hashes.append(f"{row.entity_type}:{row.entity_id}:{row.content_hash}")
        previous = dict(config.manifest_json or {})
        config.manifest_json = {
            **previous,
            "sync_protocol_version": SYNC_PROTOCOL_VERSION,
            "project_id": project_id,
            "entity_count": len(rows),
            "counts": counts,
            "aggregate_hash": hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest(),
            "last_changed_at": utcnow().isoformat(timespec="seconds") + "Z",
        }

    def revoke_device(self, device_id: str, *, actor_device_id: str | None = None) -> None:
        device = self.db.get(GatewayDevice, device_id)
        if device is None:
            raise NotFoundError("未找到设备")
        if actor_device_id and actor_device_id == device_id and device.role == "owner":
            other_owner = (
                self.db.query(GatewayDevice)
                .filter(
                    GatewayDevice.id != device_id,
                    GatewayDevice.role == "owner",
                    GatewayDevice.status == "approved",
                )
                .first()
            )
            if other_owner is None:
                raise ValidationError("不能撤销唯一的所有者设备")
        now = utcnow()
        device.status = "revoked"
        device.revoked_at = now
        self.db.query(GatewayAccessToken).filter(
            GatewayAccessToken.device_id == device_id,
            GatewayAccessToken.revoked_at.is_(None),
        ).update({"revoked_at": now}, synchronize_session=False)
        self.db.query(GatewayRefreshToken).filter(
            GatewayRefreshToken.device_id == device_id,
            GatewayRefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": now}, synchronize_session=False)
        commit_session(self.db)

    def enable_project(self, project_id: str) -> SyncProject:
        if self.db.get(Project, project_id) is None:
            raise NotFoundError("未找到作品")
        bind = self.db.get_bind()
        target_engine = getattr(bind, "engine", bind)
        backup_path = backup_sqlite_database(
            str(target_engine.url),
            reason=f"pre-sync-{project_id[:8]}",
        )
        config = self.db.get(SyncProject, project_id)
        if config is None:
            config = SyncProject(project_id=project_id)
            self.db.add(config)
        config.status = "migrating"
        config.last_error = None
        self.db.flush()

        snapshots = project_snapshots(self.db, project_id)
        counts: dict[str, int] = {}
        hashes: list[str] = []
        present_keys: set[tuple[str, str]] = set()
        for spec, row, payload in snapshots:
            entity_id = str(row.id)
            present_keys.add((spec.entity_type, entity_id))
            counts[spec.entity_type] = counts.get(spec.entity_type, 0) + 1
            digest = payload_hash(payload)
            hashes.append(f"{spec.entity_type}:{entity_id}:{digest}")
            state = (
                self.db.query(SyncEntityState)
                .filter(
                    SyncEntityState.project_id == project_id,
                    SyncEntityState.entity_type == spec.entity_type,
                    SyncEntityState.entity_id == entity_id,
                )
                .first()
            )
            if state is not None and not state.is_deleted and state.content_hash == digest:
                continue
            base_revision = state.revision if state is not None else 0
            seed_id = (
                "seed-"
                + hashlib.sha256(
                    f"{project_id}:{spec.entity_type}:{entity_id}:{digest}".encode()
                ).hexdigest()[:48]
            )
            result = self._apply_mutation(
                SyncMutation(
                    mutation_id=seed_id,
                    project_id=project_id,
                    entity_type=spec.entity_type,
                    entity_id=entity_id,
                    operation="upsert",
                    base_revision=base_revision,
                    payload=payload,
                ),
                device_id=None,
                project_domain=False,
            )
            if result.status not in {"applied", "duplicate"}:
                raise ValidationError(result.message or "作品初始同步失败")

        existing_active = (
            self.db.query(SyncEntityState)
            .filter(
                SyncEntityState.project_id == project_id,
                SyncEntityState.is_deleted.is_(False),
            )
            .all()
        )
        for state in existing_active:
            if (state.entity_type, state.entity_id) in present_keys:
                continue
            seed_id = (
                "seed-"
                + hashlib.sha256(
                    f"{project_id}:{state.entity_type}:{state.entity_id}:deleted".encode()
                ).hexdigest()[:48]
            )
            result = self._apply_mutation(
                SyncMutation(
                    mutation_id=seed_id,
                    project_id=project_id,
                    entity_type=state.entity_type,
                    entity_id=state.entity_id,
                    operation="delete",
                    base_revision=state.revision,
                ),
                device_id=None,
                project_domain=False,
            )
            if result.status not in {"applied", "duplicate"}:
                raise ValidationError(result.message or "作品删除标记同步失败")

        aggregate_hash = hashlib.sha256("\n".join(sorted(hashes)).encode("utf-8")).hexdigest()
        config.manifest_json = {
            "sync_protocol_version": SYNC_PROTOCOL_VERSION,
            "project_id": project_id,
            "entity_count": len(snapshots),
            "counts": counts,
            "aggregate_hash": aggregate_hash,
            "backup_path": str(backup_path) if backup_path else None,
        }
        config.initial_revision = self._current_cursor()
        config.status = "enabled"
        config.enabled_at = config.enabled_at or utcnow()
        config.verified_at = utcnow()
        config.last_error = None
        commit_session(self.db)
        return config

    def project_view(self, project_id: str) -> SyncProjectView:
        project = self.db.get(Project, project_id)
        if project is None:
            raise NotFoundError("未找到作品")
        return self._project_view(project, self.db.get(SyncProject, project_id))

    def disable_project(self, project_id: str) -> None:
        config = self.db.get(SyncProject, project_id)
        if config is None:
            raise NotFoundError("该作品尚未启用跨设备同步")
        config.status = "disabled"
        commit_session(self.db)

    def _enabled_projects(self, project_ids: list[str]) -> set[str]:
        if not project_ids:
            return set()
        return {
            row.project_id
            for row in self.db.query(SyncProject)
            .filter(
                SyncProject.project_id.in_(project_ids),
                SyncProject.status == "enabled",
            )
            .all()
        }

    def _current_cursor(self) -> int:
        return int(self.db.query(func.max(SyncChange.revision)).scalar() or 0)

    def bootstrap(self, project_ids: list[str], *, protocol_version: int) -> SyncBootstrapResponse:
        require_protocol(protocol_version)
        enabled = self._enabled_projects(project_ids)
        missing = sorted(set(project_ids) - enabled)
        if missing:
            raise ValidationError("以下作品尚未在 Gateway 中显式启用同步：" + "、".join(missing))
        rows = (
            self.db.query(SyncEntityState)
            .filter(SyncEntityState.project_id.in_(project_ids))
            .order_by(SyncEntityState.revision)
            .all()
        )
        entities = [
            SyncEntitySnapshot(
                project_id=row.project_id,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                revision=row.revision,
                operation="delete" if row.is_deleted else "upsert",
                payload=None if row.is_deleted else row.payload_json,
                content_hash=row.content_hash,
                server_modified_at=row.server_modified_at,
            )
            for row in rows
        ]
        return SyncBootstrapResponse(
            cursor=self._current_cursor(),
            projects=project_ids,
            entities=entities,
        )

    def push(
        self,
        mutations: list[SyncMutation],
        *,
        protocol_version: int,
        device_id: str,
    ) -> SyncPushResponse:
        require_protocol(protocol_version)
        enabled = self._enabled_projects(sorted({item.project_id for item in mutations}))
        results: list[MutationResult] = []
        for mutation in mutations:
            if mutation.project_id not in enabled:
                if (
                    mutation.entity_type == "project"
                    and mutation.operation == "upsert"
                    and mutation.base_revision == 0
                    and self.db.get(Project, mutation.project_id) is None
                ):
                    try:
                        with self.db.begin_nested():
                            self.db.info["siming_sync_projection"] = True
                            apply_domain_mutation(
                                self.db,
                                project_id=mutation.project_id,
                                entity_type=mutation.entity_type,
                                entity_id=mutation.entity_id,
                                operation=mutation.operation,
                                payload=mutation.payload,
                            )
                            self.db.add(
                                SyncProject(
                                    project_id=mutation.project_id,
                                    status="enabled",
                                    enabled_at=utcnow(),
                                    verified_at=utcnow(),
                                    manifest_json={
                                        "sync_protocol_version": SYNC_PROTOCOL_VERSION,
                                        "created_from_device": device_id,
                                        "entity_count": 1,
                                    },
                                )
                            )
                            self.db.flush()
                    except (AppException, SQLAlchemyError, ValueError) as exc:
                        results.append(
                            MutationResult(
                                mutation_id=mutation.mutation_id,
                                status="rejected",
                                message=f"无法创建同步作品：{exc}",
                            )
                        )
                        continue
                    finally:
                        self.db.info.pop("siming_sync_projection", None)
                    enabled.add(mutation.project_id)
                    results.append(
                        self._apply_mutation(
                            mutation,
                            device_id=device_id,
                            project_domain=False,
                        )
                    )
                    continue
                results.append(
                    MutationResult(
                        mutation_id=mutation.mutation_id,
                        status="rejected",
                        message="作品尚未显式启用跨设备同步",
                    )
                )
                continue
            results.append(self._apply_mutation(mutation, device_id=device_id))
        commit_session(self.db)
        return SyncPushResponse(cursor=self._current_cursor(), results=results)

    def take_deferred_chapter_cataloging(self) -> list[tuple[str, str, str]]:
        """Return and clear post-commit chapter cataloging requests.

        Each tuple is ``(mutation_id, project_id, chapter_id)``.  Multiple
        writes to the same chapter within one push collapse to the latest
        mutation because only the final canonical chapter state needs a job.
        """

        pending = self.db.info.pop("siming_deferred_chapter_cataloging", {})
        return [
            (mutation_id, project_id, chapter_id)
            for (project_id, chapter_id), mutation_id in pending.items()
        ]

    def _apply_mutation(
        self,
        mutation: SyncMutation,
        *,
        device_id: str | None,
        project_domain: bool = True,
    ) -> MutationResult:
        return GatewayMutationApplier(
            self.db,
            tombstone_retention_days=self.tombstone_retention_days,
            refresh_project_manifest=self._refresh_project_manifest,
        ).apply(
            mutation,
            device_id=device_id,
            project_domain=project_domain,
        )

    def pull(
        self,
        *,
        cursor: int,
        project_ids: list[str],
        limit: int,
        protocol_version: int,
    ) -> SyncPullResponse:
        require_protocol(protocol_version)
        enabled = self._enabled_projects(project_ids)
        missing = sorted(set(project_ids) - enabled)
        if missing:
            raise ValidationError("以下作品尚未在 Gateway 中显式启用同步：" + "、".join(missing))
        rows = (
            self.db.query(SyncChange)
            .filter(
                SyncChange.revision > cursor,
                SyncChange.project_id.in_(project_ids),
            )
            .order_by(SyncChange.revision)
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        changes = [
            SyncChangeView(
                revision=row.revision,
                mutation_id=row.mutation_id,
                project_id=row.project_id,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                operation=row.operation,
                base_revision=row.base_revision,
                payload=row.payload_json,
                content_hash=row.content_hash,
                changed_at=row.changed_at,
            )
            for row in visible
        ]
        next_cursor = visible[-1].revision if visible else cursor
        return SyncPullResponse(
            from_cursor=cursor,
            next_cursor=next_cursor,
            has_more=has_more,
            changes=changes,
        )

    def _conflict_view(self, conflict: SyncConflict) -> SyncConflictView:
        project = self.db.get(Project, conflict.project_id)
        device = self.db.get(GatewayDevice, conflict.device_id) if conflict.device_id else None
        return SyncConflictView(
            id=conflict.id,
            mutation_id=conflict.mutation_id,
            project_id=conflict.project_id,
            project_title=project.title if project else "已删除作品",
            entity_type=conflict.entity_type,
            entity_id=conflict.entity_id,
            device_id=conflict.device_id,
            device_name=device.name if device else None,
            client_base_revision=conflict.client_base_revision,
            server_revision=conflict.server_revision,
            client_operation=conflict.client_operation,
            server_operation=conflict.server_operation,
            client_payload=conflict.client_payload_json,
            server_payload=conflict.server_payload_json,
            status=conflict.status,
            resolution=conflict.resolution_json,
            created_at=conflict.created_at,
            resolved_at=conflict.resolved_at,
        )

    def list_conflicts(
        self,
        *,
        status: str = "open",
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[SyncConflictView]:
        query = self.db.query(SyncConflict).filter(SyncConflict.status == status)
        if project_id:
            query = query.filter(SyncConflict.project_id == project_id)
        conflicts = query.order_by(SyncConflict.created_at.desc()).limit(limit).all()
        return [self._conflict_view(conflict) for conflict in conflicts]

    def resolve_conflict(
        self,
        conflict_id: str,
        request: SyncConflictResolutionRequest,
        *,
        device_id: str | None,
    ) -> SyncConflictView:
        conflict = self.db.get(SyncConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("未找到同步冲突")
        if conflict.status != "open":
            raise ValidationError("该同步冲突已经处理")

        state = (
            self.db.query(SyncEntityState)
            .filter(
                SyncEntityState.project_id == conflict.project_id,
                SyncEntityState.entity_type == conflict.entity_type,
                SyncEntityState.entity_id == conflict.entity_id,
            )
            .first()
        )
        current_revision = state.revision if state else 0
        applied_revision = current_revision
        if request.choice != "server":
            if request.choice == "client":
                operation = conflict.client_operation
                payload = conflict.client_payload_json
            else:
                operation = request.custom_operation or "upsert"
                payload = request.custom_payload
            mutation = SyncMutation(
                mutation_id=f"resolve:{conflict.id}:{secrets.token_hex(4)}",
                project_id=conflict.project_id,
                entity_type=conflict.entity_type,
                entity_id=conflict.entity_id,
                operation=operation,
                base_revision=current_revision,
                payload=payload,
            )
            result = self._apply_mutation(mutation, device_id=device_id)
            if result.status != "applied" or result.revision is None:
                raise ValidationError(result.message or "同步冲突处理失败")
            applied_revision = result.revision

        conflict.status = "resolved"
        conflict.resolved_at = utcnow()
        conflict.resolution_json = {
            "choice": request.choice,
            "applied_revision": applied_revision,
            "resolved_by_device_id": device_id,
        }
        commit_session(self.db)
        return self._conflict_view(conflict)

    def status(self) -> SyncStatusResponse:
        return SyncStatusResponse(
            cursor=self._current_cursor(),
            enabled_projects=self.db.query(SyncProject)
            .filter(SyncProject.status == "enabled")
            .count(),
            open_conflicts=self.db.query(SyncConflict)
            .filter(SyncConflict.status == "open")
            .count(),
            active_devices=self.db.query(GatewayDevice)
            .filter(GatewayDevice.status == "approved")
            .count(),
            tombstone_retention_days=self.tombstone_retention_days,
        )


__all__ = [
    "GatewayAuthContext",
    "GatewayService",
    "MAX_ENTITY_PAYLOAD_BYTES",
    "canonical_payload",
    "payload_hash",
    "require_protocol",
    "token_digest",
]
