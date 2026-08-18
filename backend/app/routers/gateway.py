"""User-owned Gateway pairing, device, runtime, and sync HTTP API."""

from __future__ import annotations

import hmac
import ipaddress
import socket
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..bootstrap.http_security import is_loopback_client
from ..core.config import Settings, get_settings
from ..core.exceptions import AppException, UnauthorizedError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.gateway.infrastructure.service import GatewayAuthContext, GatewayService
from ..modules.gateway.interfaces.contracts import (
    DeviceView,
    GatewayAdminLoginRequest,
    GatewayAdminSessionStatusView,
    GatewayAdminSessionView,
    PairingApproveRequest,
    PairingCompleteRequest,
    PairingCompleteResponse,
    PairingStartResponse,
    PairingStatusResponse,
    RefreshTokenRequest,
    RuntimeCapabilities,
    SyncBootstrapRequest,
    SyncBootstrapResponse,
    SyncConflictResolutionRequest,
    SyncConflictView,
    SyncProjectView,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncStatusResponse,
    TokenPair,
)
from ..services.cataloging.launcher import (
    AUTO_CHAPTER_WRITE_SOURCE,
    create_and_queue_cataloging_job,
)

router = APIRouter(tags=["gateway"])
ADMIN_SESSION_COOKIE = "siming_gateway_session"


def get_gateway_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GatewayService:
    return GatewayService(
        db,
        pairing_ttl_minutes=settings.gateway_pairing_ttl_minutes,
        access_ttl_minutes=settings.gateway_access_ttl_minutes,
        refresh_ttl_days=settings.gateway_refresh_ttl_days,
        tombstone_retention_days=settings.sync_tombstone_retention_days,
    )


def _require_gateway(settings: Settings) -> None:
    if not settings.gateway_enabled:
        raise AppException(
            code=409,
            status_code=409,
            message="当前为单机模式，请先在设置中启用 Gateway。",
        )


def _client_is_private(request: Request) -> bool:
    host = str(request.client.host if request.client else "").split("%", 1)[0]
    if host == "testclient":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    tailscale_v4 = ipaddress.ip_network("100.64.0.0/10")
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or (address.version == 4 and address in tailscale_v4)
    )


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return None


def _request_auth(
    request: Request,
    service: GatewayService,
    *,
    allow_local: bool,
) -> GatewayAuthContext:
    device_id = getattr(request.state, "gateway_device_id", None)
    if device_id:
        return GatewayAuthContext(
            device_id=device_id,
            role=getattr(request.state, "gateway_device_role", "member"),
            platform=getattr(request.state, "gateway_device_platform", "unknown"),
        )
    token = _bearer_token(request)
    if token:
        return service.authenticate(token)
    if allow_local and is_loopback_client(request.scope):
        return service.ensure_local_owner_device()
    raise UnauthorizedError("请先连接并授权此设备")


def _require_admin(
    request: Request,
    service: GatewayService,
    settings: Settings,
) -> GatewayAuthContext | None:
    if is_loopback_client(request.scope):
        return service.ensure_local_owner_device()
    device_id = getattr(request.state, "gateway_device_id", None)
    if device_id:
        context = GatewayAuthContext(
            device_id=device_id,
            role=getattr(request.state, "gateway_device_role", "member"),
            platform=getattr(request.state, "gateway_device_platform", "unknown"),
        )
        if context.role != "owner":
            raise AppException(code=403, status_code=403, message="仅所有者设备可执行此操作")
        return context
    token = _bearer_token(request)
    if token:
        context = service.authenticate(token)
        if context.role != "owner":
            raise AppException(code=403, status_code=403, message="仅所有者设备可执行此操作")
        return context
    if service.has_owner():
        raise UnauthorizedError("请在已授权的所有者设备上确认")
    supplied_bootstrap = request.headers.get("x-siming-bootstrap-key", "")
    if settings.gateway_bootstrap_key:
        if hmac.compare_digest(supplied_bootstrap, settings.gateway_bootstrap_key):
            return None
        raise UnauthorizedError("请先使用 Gateway 管理口令解锁管理页面")
    if _client_is_private(request):
        # First-owner bootstrap is restricted to a directly reachable private
        # network. Public deployments must set SIMING_GATEWAY_BOOTSTRAP_KEY.
        return None
    raise UnauthorizedError("公网首次配对需要 Gateway 启动密钥")


_VIRTUAL_INTERFACE_MARKERS = (
    "bluetooth",
    "docker",
    "hyper-v",
    "loopback",
    "podman",
    "radmin",
    "tap",
    "tun",
    "vbox",
    "virtual",
    "vmnet",
    "vmware",
    "vpn",
    "wsl",
)
_PHYSICAL_INTERFACE_MARKERS = (
    "ethernet",
    "wi-fi",
    "wifi",
    "wlan",
    "wireless",
    "以太网",
    "无线",
)
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _usable_ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None
    if (
        address.version != 4
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    ):
        return None
    return address


def _default_route_ipv4() -> ipaddress.IPv4Address | None:
    """Return the source address selected by the OS default IPv4 route.

    Connecting a UDP socket does not send traffic. It only asks the routing
    table which local interface would be used, avoiding WSL/VMware adapters
    that often sort ahead of the Wi-Fi or Ethernet address.
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return _usable_ipv4(probe.getsockname()[0])
    except OSError:
        return None


def _interface_priority(
    candidate: tuple[str, ipaddress.IPv4Address],
) -> tuple[int, int, int, int]:
    interface_name, address = candidate
    normalized_name = interface_name.casefold()
    virtual = any(marker in normalized_name for marker in _VIRTUAL_INTERFACE_MARKERS)
    physical = any(marker in normalized_name for marker in _PHYSICAL_INTERFACE_MARKERS)
    if any(address in network for network in _RFC1918_NETWORKS):
        address_scope = 0
    elif address in _CGNAT_NETWORK:
        address_scope = 1
    else:
        address_scope = 2
    return (int(virtual), int(not physical), address_scope, int(address))


def _discover_local_gateway_ipv4() -> ipaddress.IPv4Address | None:
    candidates: list[tuple[str, ipaddress.IPv4Address]] = []
    try:
        import psutil

        interface_stats = psutil.net_if_stats()
        for interface_name, addresses in psutil.net_if_addrs().items():
            stats = interface_stats.get(interface_name)
            if stats is not None and not stats.isup:
                continue
            for item in addresses:
                address = _usable_ipv4(str(item.address))
                if address is not None:
                    candidates.append((interface_name, address))
    except Exception:
        # The default-route probe below still works in slim builds without
        # psutil. If that also fails, the caller retains the loopback URL.
        pass

    default_route = _default_route_ipv4()
    if default_route is not None:
        return default_route
    if not candidates:
        return None
    return min(candidates, key=_interface_priority)[1]


def _discover_gateway_url(request: Request, settings: Settings) -> str:
    if settings.gateway_advertised_url:
        parsed = urlsplit(settings.gateway_advertised_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValidationError("Gateway 公布地址必须是有效的 HTTP 或 HTTPS 地址")
        return settings.gateway_advertised_url.rstrip("/")

    parsed = urlsplit(str(request.base_url))
    hostname = parsed.hostname or "127.0.0.1"
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        return str(request.base_url).rstrip("/")

    selected = _discover_local_gateway_ipv4()
    if selected is None:
        return str(request.base_url).rstrip("/")
    port = parsed.port
    netloc = str(selected) if port is None else f"{selected}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")


@router.get(
    "/runtime/capabilities",
    response_model=ApiResponse[RuntimeCapabilities],
)
def runtime_capabilities(
    settings: Annotated[Settings, Depends(get_settings)],
):
    local_runtime = settings.local_runtime_enabled
    data = RuntimeCapabilities(
        runtime_profile=settings.runtime_profile,
        gateway_authoritative=settings.gateway_enabled,
        pairing_enabled=settings.gateway_enabled,
        local_ai=local_runtime,
        cli_worker=local_runtime,
        mcp=local_runtime,
        training=local_runtime,
        requires_gateway_for_ai=False,
    )
    return ApiResponse.success(data=data)


@router.post(
    "/pairing/start",
    response_model=ApiResponse[PairingStartResponse],
)
def start_pairing(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    context = _require_admin(request, service, settings)
    data = service.start_pairing(
        gateway_url=_discover_gateway_url(request, settings),
        created_from=request.client.host if request.client else None,
        created_by_device_id=context.device_id if context else None,
        display_name=settings.gateway_name,
    )
    return ApiResponse.success(data=data, message="配对二维码已生成，10 分钟内有效")


@router.get(
    "/pairing/{pairing_id}",
    response_model=ApiResponse[PairingStatusResponse],
)
def get_pairing_status(
    pairing_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    return ApiResponse.success(data=service.pairing_status(pairing_id))


@router.post(
    "/pairing/complete",
    response_model=ApiResponse[PairingCompleteResponse],
)
def complete_pairing(
    payload: PairingCompleteRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    return ApiResponse.success(data=service.complete_pairing(payload))


@router.post(
    "/pairing/approve",
    response_model=ApiResponse[PairingCompleteResponse],
)
def approve_pairing(
    payload: PairingApproveRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    return ApiResponse.success(
        data=service.approve_pairing(payload.pairing_id),
        message="设备已批准，请在手机上完成连接",
    )


@router.post("/auth/refresh", response_model=ApiResponse[TokenPair])
def refresh_tokens(
    payload: RefreshTokenRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    return ApiResponse.success(data=service.refresh_tokens(payload.refresh_token))


@router.post(
    "/auth/admin/login",
    response_model=ApiResponse[GatewayAdminSessionView],
)
def login_gateway_admin(
    payload: GatewayAdminLoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    if not settings.gateway_bootstrap_key:
        raise AppException(
            code=409,
            status_code=409,
            message="管理网页未启用口令，请先设置 SIMING_GATEWAY_BOOTSTRAP_KEY。",
        )
    if not hmac.compare_digest(payload.bootstrap_key, settings.gateway_bootstrap_key):
        raise UnauthorizedError("Gateway 管理口令不正确")
    token, expires_at = service.issue_web_admin_session()
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="strict",
        path="/",
    )
    return ApiResponse.success(
        data=GatewayAdminSessionView(expires_at=expires_at),
        message="管理网页已解锁，本次会话 12 小时内有效。",
    )


@router.get(
    "/auth/admin/session",
    response_model=ApiResponse[GatewayAdminSessionStatusView],
)
def get_gateway_admin_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    """Probe the HttpOnly admin cookie without using an expected 401 response."""

    _require_gateway(settings)
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    authenticated = False
    if token:
        try:
            context = service.authenticate(token, touch=False)
            authenticated = context.role == "owner" and context.platform == "web"
        except UnauthorizedError:
            authenticated = False
    return ApiResponse.success(
        data=GatewayAdminSessionStatusView(authenticated=authenticated)
    )


@router.post("/auth/admin/logout", response_model=ApiResponse[None])
def logout_gateway_admin(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        service.revoke_access_token(token)
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", samesite="strict")
    return ApiResponse.success(message="管理网页会话已退出")


@router.get("/devices", response_model=ApiResponse[list[DeviceView]])
def list_devices(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    return ApiResponse.success(data=service.list_devices())


@router.delete("/devices/me", response_model=ApiResponse[None])
def revoke_current_device(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    context = _request_auth(request, service, allow_local=False)
    service.revoke_device(context.device_id, actor_device_id=context.device_id)
    return ApiResponse.success(message="当前设备授权已撤销")


@router.get("/sync/projects", response_model=ApiResponse[list[SyncProjectView]])
def list_sync_projects(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _request_auth(request, service, allow_local=True)
    return ApiResponse.success(data=service.list_sync_projects())


@router.delete("/devices/{device_id}", response_model=ApiResponse[None])
def revoke_device(
    device_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    context = _require_admin(request, service, settings)
    service.revoke_device(device_id, actor_device_id=context.device_id if context else None)
    return ApiResponse.success(message="设备授权已撤销")


@router.post(
    "/sync/projects/{project_id}/enable",
    response_model=ApiResponse[SyncProjectView],
)
def enable_project_sync(
    project_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    service.enable_project(project_id)
    return ApiResponse.success(
        data=service.project_view(project_id),
        message="作品已显式启用跨设备同步",
    )


@router.delete("/sync/projects/{project_id}", response_model=ApiResponse[None])
def disable_project_sync(
    project_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _require_admin(request, service, settings)
    service.disable_project(project_id)
    return ApiResponse.success(message="作品已停止跨设备同步，本地数据保持不变")


@router.post(
    "/sync/bootstrap",
    response_model=ApiResponse[SyncBootstrapResponse],
)
def sync_bootstrap(
    payload: SyncBootstrapRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _request_auth(request, service, allow_local=True)
    return ApiResponse.success(
        data=service.bootstrap(
            payload.project_ids,
            protocol_version=payload.protocol_version,
        )
    )


def _launch_deferred_chapter_cataloging(
    service: GatewayService,
) -> dict[str, tuple[bool, str]]:
    """Start canonical post-write cataloging after the sync transaction commits."""

    outcomes: dict[str, tuple[bool, str]] = {}
    for mutation_id, project_id, chapter_id in service.take_deferred_chapter_cataloging():
        try:
            _job, launch = create_and_queue_cataloging_job(
                service.db,
                project_id,
                [chapter_id],
                execution_mode="auto",
                trigger_source=AUTO_CHAPTER_WRITE_SOURCE,
                run_now=True,
            )
            message = (
                "章节已按 PC 领域语义同步，并启动正式建档"
                if launch.get("worker_queued")
                else "章节已按 PC 领域语义同步，正式建档任务已创建"
            )
            outcomes[mutation_id] = (True, message)
        except Exception as exc:
            # The chapter and sync revision are already committed.  Preserve an
            # accurate retryable outcome instead of rolling back or claiming the
            # post-write lifecycle completed.
            outcomes[mutation_id] = (
                False,
                "章节已同步；正式建档启动失败，可在任务列表重试："
                f"{str(exc)[:500]}",
            )
    return outcomes


@router.post("/sync/push", response_model=ApiResponse[SyncPushResponse])
async def sync_push(
    payload: SyncPushRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    context = _request_auth(request, service, allow_local=True)
    data = service.push(
        payload.mutations,
        protocol_version=payload.protocol_version,
        device_id=context.device_id,
    )
    results_by_mutation = {item.mutation_id: item for item in data.results}
    for mutation_id, (_started, message) in _launch_deferred_chapter_cataloging(service).items():
        result = results_by_mutation.get(mutation_id)
        if result is not None:
            result.message = message
    return ApiResponse.success(data=data)


@router.get("/sync/pull", response_model=ApiResponse[SyncPullResponse])
def sync_pull(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
    project_id: Annotated[list[str], Query(min_length=1, max_length=20)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    protocol_version: Annotated[int, Query(ge=1)] = 1,
):
    _require_gateway(settings)
    _request_auth(request, service, allow_local=True)
    return ApiResponse.success(
        data=service.pull(
            cursor=cursor,
            project_ids=project_id,
            limit=limit,
            protocol_version=protocol_version,
        )
    )


@router.get("/sync/status", response_model=ApiResponse[SyncStatusResponse])
def sync_status(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    _request_auth(request, service, allow_local=True)
    return ApiResponse.success(data=service.status())


@router.get("/sync/conflicts", response_model=ApiResponse[list[SyncConflictView]])
def list_sync_conflicts(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
    status: Annotated[Literal["open", "resolved"], Query()] = "open",
    project_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    _require_gateway(settings)
    _request_auth(request, service, allow_local=True)
    return ApiResponse.success(
        data=service.list_conflicts(
            status=status,
            project_id=project_id,
            limit=limit,
        )
    )


@router.post(
    "/sync/conflicts/{conflict_id}/resolve",
    response_model=ApiResponse[SyncConflictView],
)
async def resolve_sync_conflict(
    conflict_id: str,
    payload: SyncConflictResolutionRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
):
    _require_gateway(settings)
    context = _request_auth(request, service, allow_local=True)
    data = service.resolve_conflict(
        conflict_id,
        payload,
        device_id=context.device_id,
    )
    outcomes = _launch_deferred_chapter_cataloging(service)
    failed = [message for started, message in outcomes.values() if not started]
    message = "同步冲突已处理，双方原始版本仍保留在记录中"
    if failed:
        message += "；章节已写入，但正式建档需要在任务列表重试"
    return ApiResponse.success(data=data, message=message)


__all__ = ["router"]
