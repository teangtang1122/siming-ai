"""Gateway pairing, token rotation, and revisioned sync regressions."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bootstrap.app_factory import create_app
from app.core.config import get_settings
from app.core.exceptions import AppException, UnauthorizedError, ValidationError
from app.database import models as _models  # noqa: F401
from app.database.session import Base, get_db
from app.modules.gateway.infrastructure.models import (
    GatewayAccessToken,
    GatewayRefreshToken,
    SyncCaptureJob,
    SyncConflict,
    SyncEntityState,
    SyncTombstone,
)
from app.modules.gateway.infrastructure.service import GatewayService, token_digest
from app.modules.gateway.interfaces.contracts import (
    DeviceCapabilities,
    PairingCompleteRequest,
    SyncConflictResolutionRequest,
    SyncMutation,
)
from app.modules.model_runtime.infrastructure.execution import CloudOnlyGatewayModelExecutor
from app.modules.model_runtime.infrastructure.gateway import LLMGateway
from app.modules.story.infrastructure.entities import Chapter, Project
from app.routers import gateway as gateway_router


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gateway.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_gateway_address_discovery_prefers_default_route_over_virtual_adapters(monkeypatch):
    import psutil

    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "vEthernet (WSL)": [SimpleNamespace(address="172.30.96.1")],
            "VMware Network Adapter VMnet8": [SimpleNamespace(address="192.168.204.1")],
            "WLAN": [SimpleNamespace(address="192.168.31.205")],
        },
    )
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {
            "vEthernet (WSL)": SimpleNamespace(isup=True),
            "VMware Network Adapter VMnet8": SimpleNamespace(isup=True),
            "WLAN": SimpleNamespace(isup=True),
        },
    )
    monkeypatch.setattr(
        gateway_router,
        "_default_route_ipv4",
        lambda: ipaddress.ip_address("192.168.31.205"),
    )

    assert gateway_router._discover_local_gateway_ipv4() == ipaddress.ip_address(
        "192.168.31.205"
    )


def test_gateway_address_discovery_fallback_penalizes_virtual_adapters(monkeypatch):
    import psutil

    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "vEthernet (WSL)": [SimpleNamespace(address="172.30.96.1")],
            "VMware Network Adapter VMnet8": [SimpleNamespace(address="192.168.204.1")],
            "WLAN": [SimpleNamespace(address="192.168.31.205")],
        },
    )
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {
            "vEthernet (WSL)": SimpleNamespace(isup=True),
            "VMware Network Adapter VMnet8": SimpleNamespace(isup=True),
            "WLAN": SimpleNamespace(isup=True),
        },
    )
    monkeypatch.setattr(gateway_router, "_default_route_ipv4", lambda: None)

    assert gateway_router._discover_local_gateway_ipv4() == ipaddress.ip_address(
        "192.168.31.205"
    )


def test_headless_model_executor_rejects_local_cli(monkeypatch):
    monkeypatch.setattr(LLMGateway, "provider_for_model", lambda model=None: "opencode_cli")
    with pytest.raises(ValidationError, match="仅支持云端 API 模型"):
        CloudOnlyGatewayModelExecutor._require_cloud("opencode-cli")


def _paired_owner(service: GatewayService):
    started = service.start_pairing(
        gateway_url="http://192.168.1.20:8000",
        created_from="127.0.0.1",
    )
    request = PairingCompleteRequest(
        pairing_id=started.pairing_id,
        pairing_secret=started.pairing_secret,
        device_name="测试手机",
        platform="android",
        public_key="android-public-key",
        capabilities=DeviceCapabilities(),
    )
    pending = service.complete_pairing(request)
    assert pending.status == "pending_approval"
    approved = service.approve_pairing(started.pairing_id)
    assert approved.device_role == "owner"
    completed = service.complete_pairing(request)
    assert completed.status == "approved"
    assert completed.tokens is not None
    return completed


def test_pairing_secret_and_tokens_are_never_stored_raw(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            service = GatewayService(db)
            completed = _paired_owner(service)
            tokens = completed.tokens
            assert tokens is not None

            access = db.query(GatewayAccessToken).one()
            refresh = db.query(GatewayRefreshToken).one()
            assert access.token_hash == token_digest(tokens.access_token)
            assert refresh.token_hash == token_digest(tokens.refresh_token)
            assert tokens.access_token not in access.token_hash
            assert tokens.refresh_token not in refresh.token_hash

            context = service.authenticate(tokens.access_token, touch=False)
            assert context.device_id == completed.device_id
            assert context.role == "owner"
    finally:
        engine.dispose()


def test_refresh_tokens_rotate_and_reuse_revokes_device_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            service = GatewayService(db)
            completed = _paired_owner(service)
            first = completed.tokens
            assert first is not None
            second = service.refresh_tokens(first.refresh_token)
            assert second.refresh_token != first.refresh_token
            assert service.authenticate(second.access_token, touch=False)

            with pytest.raises(UnauthorizedError):
                service.refresh_tokens(first.refresh_token)
            with pytest.raises(UnauthorizedError):
                service.authenticate(second.access_token, touch=False)
    finally:
        engine.dispose()


def test_sync_is_ordered_idempotent_and_merges_different_entities(tmp_path):
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            project = Project(title="跨设备测试")
            db.add(project)
            db.commit()
            service = GatewayService(db)
            local = service.ensure_local_owner_device()
            service.enable_project(project.id)
            initial_cursor = service.status().cursor

            first = SyncMutation(
                mutation_id="m-1",
                project_id=project.id,
                entity_type="chapter",
                entity_id="chapter-1",
                operation="upsert",
                base_revision=0,
                payload={"title": "第一章", "content": "海边的通知"},
            )
            second = SyncMutation(
                mutation_id="m-2",
                project_id=project.id,
                entity_type="character",
                entity_id="character-1",
                operation="upsert",
                base_revision=0,
                payload={"name": "周遥", "current_goal": "查清花色异常"},
            )
            pushed = service.push(
                [first, second],
                protocol_version=1,
                device_id=local.device_id,
            )
            assert [item.status for item in pushed.results] == ["applied", "applied"]
            assert [item.revision for item in pushed.results] == [
                initial_cursor + 1,
                initial_cursor + 2,
            ]
            stored_chapter = db.get(Chapter, "chapter-1")
            assert stored_chapter is not None
            assert stored_chapter.content == "海边的通知"
            from app.modules.story.infrastructure.entities import Character

            stored_character = db.get(Character, "character-1")
            assert stored_character is not None
            assert stored_character.current_goal == "查清花色异常"

            duplicate = service.push(
                [first],
                protocol_version=1,
                device_id=local.device_id,
            )
            assert duplicate.results[0].status == "duplicate"
            assert duplicate.results[0].revision == initial_cursor + 1

            pulled = service.pull(
                cursor=initial_cursor,
                project_ids=[project.id],
                limit=1,
                protocol_version=1,
            )
            assert [item.revision for item in pulled.changes] == [initial_cursor + 1]
            assert pulled.next_cursor == initial_cursor + 1
            assert pulled.has_more is True
            remainder = service.pull(
                cursor=pulled.next_cursor,
                project_ids=[project.id],
                limit=10,
                protocol_version=1,
            )
            assert [item.revision for item in remainder.changes] == [initial_cursor + 2]
            assert remainder.has_more is False
    finally:
        engine.dispose()


def test_same_entity_divergence_preserves_both_versions(tmp_path):
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            project = Project(title="冲突测试")
            db.add(project)
            db.commit()
            service = GatewayService(db)
            local = service.ensure_local_owner_device()
            service.enable_project(project.id)
            original = SyncMutation(
                mutation_id="chapter-initial",
                project_id=project.id,
                entity_type="chapter",
                entity_id="chapter-1",
                operation="upsert",
                base_revision=0,
                payload={"content": "桌面版本"},
            )
            applied = service.push(
                [original], protocol_version=1, device_id=local.device_id
            )
            server_revision = applied.results[0].revision
            assert server_revision is not None

            stale = SyncMutation(
                mutation_id="chapter-phone-stale",
                project_id=project.id,
                entity_type="chapter",
                entity_id="chapter-1",
                operation="upsert",
                base_revision=0,
                payload={"content": "手机离线版本"},
            )
            result = service.push(
                [stale], protocol_version=1, device_id=local.device_id
            ).results[0]
            assert result.status == "conflict"
            assert result.revision == server_revision
            server_payload = result.server_snapshot["payload"]
            assert server_payload["_record_type"] == "chapter"
            assert server_payload["id"] == "chapter-1"
            assert server_payload["content"] == "桌面版本"
            assert server_payload["current_version"] == 1
            assert server_payload["word_count"] > 0

            conflict = db.query(SyncConflict).one()
            # Client branch keeps the exact stale request for conflict review;
            # server branch is the authoritative PC-shaped domain snapshot.
            assert conflict.client_payload_json == {"content": "手机离线版本"}
            assert conflict.server_payload_json == server_payload
            state = (
                db.query(SyncEntityState)
                .filter(SyncEntityState.entity_type == "chapter")
                .one()
            )
            assert state.payload_json == server_payload

            view = service.resolve_conflict(
                conflict.id,
                SyncConflictResolutionRequest(choice="client"),
                device_id=local.device_id,
            )
            assert view.status == "resolved"
            assert view.resolution["choice"] == "client"
            assert conflict.client_operation == "upsert"
            assert conflict.server_operation == "upsert"
            resolved_chapter = db.get(Chapter, "chapter-1")
            assert resolved_chapter is not None
            assert resolved_chapter.content == "手机离线版本"
            resolved_state = (
                db.query(SyncEntityState)
                .filter(SyncEntityState.entity_type == "chapter")
                .one()
            )
            assert resolved_state.revision > server_revision
            assert resolved_state.payload_json["_record_type"] == "chapter"
            assert resolved_state.payload_json["content"] == "手机离线版本"
            assert resolved_state.payload_json["current_version"] == 2
            assert service.list_conflicts(status="open") == []
            assert service.list_conflicts(status="resolved")[0].id == conflict.id
    finally:
        engine.dispose()


def test_delete_creates_retained_tombstone_and_bootstrap_snapshot(tmp_path):
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            project = Project(title="删除测试")
            db.add(project)
            db.commit()
            service = GatewayService(db, tombstone_retention_days=90)
            local = service.ensure_local_owner_device()
            service.enable_project(project.id)
            created = service.push(
                [
                    SyncMutation(
                        mutation_id="world-create",
                        project_id=project.id,
                        entity_type="world",
                        entity_id="world-1",
                        operation="upsert",
                        base_revision=0,
                        payload={"title": "公共温室管理站"},
                    )
                ],
                protocol_version=1,
                device_id=local.device_id,
            )
            revision = created.results[0].revision
            deleted = service.push(
                [
                    SyncMutation(
                        mutation_id="world-delete",
                        project_id=project.id,
                        entity_type="world",
                        entity_id="world-1",
                        operation="delete",
                        base_revision=revision,
                    )
                ],
                protocol_version=1,
                device_id=local.device_id,
            )
            assert deleted.results[0].status == "applied"
            from app.modules.story.infrastructure.entities import WorldbuildingEntry

            assert db.get(WorldbuildingEntry, "world-1") is None
            tombstone = db.query(SyncTombstone).one()
            assert (tombstone.expires_at - tombstone.deleted_at).days == 90
            bootstrap = service.bootstrap([project.id], protocol_version=1)
            world_snapshot = next(
                item
                for item in bootstrap.entities
                if item.entity_type == "world" and item.entity_id == "world-1"
            )
            assert world_snapshot.operation == "delete"
            assert world_snapshot.payload is None
    finally:
        engine.dispose()


def test_incompatible_protocol_requires_update(tmp_path):
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            service = GatewayService(db)
            with pytest.raises(AppException) as captured:
                service.bootstrap(["project"], protocol_version=2)
            assert captured.value.status_code == 426
    finally:
        engine.dispose()


def test_gateway_canonical_writes_are_captured_after_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_RUNTIME_PROFILE", "gateway")
    get_settings.cache_clear()
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            project = Project(title="事务捕获测试")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章", content="旧正文")
            db.add(chapter)
            db.commit()
            service = GatewayService(db)
            service.enable_project(project.id)
            before = (
                db.query(SyncEntityState)
                .filter(
                    SyncEntityState.entity_type == "chapter",
                    SyncEntityState.entity_id == chapter.id,
                )
                .one()
            )
            before_revision = before.revision

            chapter.content = "桌面端保存后的新正文"
            db.commit()

            captured = (
                db.query(SyncEntityState)
                .filter(
                    SyncEntityState.entity_type == "chapter",
                    SyncEntityState.entity_id == chapter.id,
                )
                .one()
            )
            assert captured.revision > before_revision
            assert captured.payload_json["content"] == "桌面端保存后的新正文"
            job = db.query(SyncCaptureJob).order_by(SyncCaptureJob.created_at.desc()).first()
            assert job is not None
            assert job.status == "completed"
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_project_opt_in_creates_verified_manifest_and_excludes_local_paths(tmp_path):
    engine, Session = _database(tmp_path)
    try:
        with Session() as db:
            project = Project(
                title="迁移校验测试",
                folder_path=str(tmp_path / "private-project-folder"),
            )
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第一章",
                content="正文",
                content_file_path="chapters/private.md",
            )
            db.add(chapter)
            db.commit()

            service = GatewayService(db)
            config = service.enable_project(project.id)
            manifest = config.manifest_json
            initial_hash = manifest["aggregate_hash"]
            assert config.status == "enabled"
            assert config.verified_at is not None
            assert manifest["entity_count"] == 2
            assert manifest["counts"] == {"chapter": 1, "project": 1}
            assert len(manifest["aggregate_hash"]) == 64
            assert Path(manifest["backup_path"]).is_file()

            public_view = GatewayService(db).list_sync_projects()[0]
            assert public_view.status == "enabled"
            assert public_view.entity_count == 2
            assert "backup_path" not in public_view.model_dump()

            states = db.query(SyncEntityState).filter_by(project_id=project.id).all()
            payloads = [state.payload_json for state in states]
            assert all("folder_path" not in payload for payload in payloads)
            assert all("content_file_path" not in payload for payload in payloads)

            owner = service.ensure_local_owner_device()
            mobile_chapter_id = "11111111-1111-4111-8111-111111111111"
            pushed = service.push(
                [
                    SyncMutation(
                        mutation_id="mobile-manifest-refresh",
                        project_id=project.id,
                        entity_type="chapter",
                        entity_id=mobile_chapter_id,
                        operation="upsert",
                        base_revision=0,
                        payload={
                            "_record_type": "chapter",
                            "id": mobile_chapter_id,
                            "project_id": project.id,
                            "title": "手机新章",
                            "content": "离线写作后同步",
                        },
                    )
                ],
                protocol_version=1,
                device_id=owner.device_id,
            )
            assert pushed.results[0].status == "applied"
            refreshed = service.project_view(project.id)
            assert refreshed.entity_count == 3
            assert refreshed.counts == {"chapter": 2, "project": 1}
            assert refreshed.aggregate_hash != initial_hash
    finally:
        engine.dispose()


def test_gateway_http_boundary_pairs_locally_and_denies_unauthorized_remote_clients(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SIMING_RUNTIME_PROFILE", "gateway")
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    engine, Session = _database(tmp_path)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    import app.database.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", Session)
    app = create_app(run_startup=False)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as local_client:
            capabilities = local_client.get("/api/v1/runtime/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["data"]["runtime_profile"] == "gateway"
            assert capabilities.json()["data"]["local_ai"] is True
            assert capabilities.json()["data"]["cli_worker"] is True

            started = local_client.post("/api/v1/pairing/start")
            assert started.status_code == 200
            pairing = started.json()["data"]
            assert len(pairing["gateway_encryption_public_key"]) >= 43
            assert (
                pairing["qr_payload"]["gateway_encryption_public_key"]
                == pairing["gateway_encryption_public_key"]
            )
            application = {
                "pairing_id": pairing["pairing_id"],
                "pairing_secret": pairing["pairing_secret"],
                "device_name": "HTTP 测试手机",
                "platform": "android",
                "public_key": "http-test-public-key",
                "capabilities": {"protocol_version": 1},
            }
            pending = local_client.post("/api/v1/pairing/complete", json=application)
            assert pending.json()["data"]["status"] == "pending_approval"
            pairing_status = local_client.get(
                f"/api/v1/pairing/{pairing['pairing_id']}"
            )
            assert pairing_status.json()["data"]["device_name"] == "HTTP 测试手机"
            approved = local_client.post(
                "/api/v1/pairing/approve",
                json={"pairing_id": pairing["pairing_id"]},
            )
            assert approved.json()["data"]["device_role"] == "member"
            completed = local_client.post("/api/v1/pairing/complete", json=application)
            access_token = completed.json()["data"]["tokens"]["access_token"]

        with Session() as db:
            enabled_project = Project(title="已显式同步")
            private_project = Project(title="仍只在电脑")
            db.add_all([enabled_project, private_project])
            db.commit()
            GatewayService(db).enable_project(enabled_project.id)
            enabled_project_id = enabled_project.id
            private_project_id = private_project.id

        with TestClient(app, client=("203.0.113.9", 41000)) as remote_client:
            denied = remote_client.get("/api/v1/sync/status")
            assert denied.status_code == 401
            assert denied.headers["www-authenticate"] == "Bearer"
            accepted = remote_client.get(
                "/api/v1/sync/status",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert accepted.status_code == 200
            assert accepted.json()["data"]["protocol_version"] == 1

            visible_authoring_api = remote_client.get(
                "/api/v1/projects",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert visible_authoring_api.status_code == 200
            visible_items = visible_authoring_api.json()["data"]["items"]
            assert [item["id"] for item in visible_items] == [enabled_project_id]
            assert visible_items[0]["folder_path"] is None

            private_project_api = remote_client.get(
                f"/api/v1/projects/{private_project_id}",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert private_project_api.status_code == 404
            enabled_project_api = remote_client.get(
                f"/api/v1/projects/{enabled_project_id}",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert enabled_project_api.status_code == 200
            assert enabled_project_api.json()["data"]["folder_path"] is None

            created_chapter = remote_client.post(
                f"/api/v1/projects/{enabled_project_id}/chapters",
                headers={"authorization": f"Bearer {access_token}"},
                json={"title": "手机规范写入", "content": "第一版正文"},
            )
            assert created_chapter.status_code == 200
            chapter_id = created_chapter.json()["data"]["id"]
            updated_chapter = remote_client.put(
                f"/api/v1/projects/{enabled_project_id}/chapters/{chapter_id}",
                headers={"authorization": f"Bearer {access_token}"},
                json={"content": "第二版正文", "trigger_type": "manual_save"},
            )
            assert updated_chapter.status_code == 200
            assert updated_chapter.json()["data"]["content"] == "第二版正文"
            assert updated_chapter.json()["data"]["snapshot_count"] >= 1

            private_assistant = remote_client.head(
                f"/api/v1/projects/{private_project_id}/ai/workspace-assistant/stream",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert private_assistant.status_code == 404
            enabled_assistant = remote_client.head(
                f"/api/v1/projects/{enabled_project_id}/ai/workspace-assistant/stream",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert enabled_assistant.status_code == 405

            oversized = remote_client.post(
                "/api/v1/sync/push",
                headers={
                    "authorization": f"Bearer {access_token}",
                    "content-length": str(8 * 1024 * 1024 + 1),
                },
                content=b"{}",
            )
            assert oversized.status_code == 413

            oversized_assistant = remote_client.post(
                f"/api/v1/projects/{enabled_project_id}/ai/workspace-assistant/stream",
                headers={
                    "authorization": f"Bearer {access_token}",
                    "content-length": str(256 * 1024 + 1),
                },
                content=b"{}",
            )
            assert oversized_assistant.status_code == 413

            revoked = remote_client.delete(
                "/api/v1/devices/me",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert revoked.status_code == 200
            denied_after_revoke = remote_client.get(
                "/api/v1/sync/status",
                headers={"authorization": f"Bearer {access_token}"},
            )
            assert denied_after_revoke.status_code == 401
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        get_settings.cache_clear()


def test_gateway_web_admin_uses_http_only_bootstrap_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMING_RUNTIME_PROFILE", "gateway")
    monkeypatch.setenv("SIMING_GATEWAY_HEADLESS", "true")
    monkeypatch.setenv("SIMING_GATEWAY_BOOTSTRAP_KEY", "test-bootstrap-key-very-secret")
    monkeypatch.setenv("SIMING_HOME", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    engine, Session = _database(tmp_path)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    import app.database.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", Session)
    app = create_app(run_startup=False)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, client=("192.168.1.50", 42000)) as browser:
            launcher = browser.get("/api/v1/config/launcher")
            assert launcher.status_code == 200
            assert launcher.json()["data"]["gateway_runtime_active"] is True
            assert launcher.json()["data"]["gateway_headless"] is True

            capabilities = browser.get("/api/v1/runtime/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["data"]["local_ai"] is False
            assert capabilities.json()["data"]["cli_worker"] is False
            assert capabilities.json()["data"]["mcp"] is False
            assert capabilities.json()["data"]["training"] is False

            first_pairing = browser.post("/api/v1/pairing/start")
            assert first_pairing.status_code == 401
            session = browser.get("/api/v1/auth/admin/session")
            assert session.status_code == 200
            assert session.json()["data"] == {"authenticated": False}
            assert session.headers["cache-control"] == "no-store"

            wrong = browser.post(
                "/api/v1/auth/admin/login",
                json={"bootstrap_key": "incorrect-bootstrap-key"},
            )
            assert wrong.status_code == 401

            logged_in = browser.post(
                "/api/v1/auth/admin/login",
                json={"bootstrap_key": "test-bootstrap-key-very-secret"},
            )
            assert logged_in.status_code == 200
            cookie = logged_in.headers["set-cookie"].lower()
            assert "siming_gateway_session=" in cookie
            assert "httponly" in cookie
            assert "samesite=strict" in cookie
            assert "max-age=43200" in cookie
            session = browser.get("/api/v1/auth/admin/session")
            assert session.status_code == 200
            assert session.json()["data"] == {"authenticated": True}

            projects = browser.get("/api/v1/projects")
            assert projects.status_code == 200
            pairing = browser.post("/api/v1/pairing/start")
            assert pairing.status_code == 200
            assert pairing.json()["data"]["expires_at"].endswith("Z")
            assert pairing.json()["data"]["qr_payload"]["expires_at"].endswith("Z")
            update = browser.post("/api/v1/config/update/check")
            assert update.status_code == 404

            logout = browser.post("/api/v1/auth/admin/logout")
            assert logout.status_code == 200
            assert browser.get("/api/v1/auth/admin/session").json()["data"] == {
                "authenticated": False
            }
            assert browser.get("/api/v1/projects").status_code == 401
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        get_settings.cache_clear()
