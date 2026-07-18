"""Explicit, verified updater for the packaged Windows executable.

The updater is deliberately user-driven.  Startup never downloads or replaces the
application.  A user must first check, then download and verify, and finally
confirm installation from the Settings page.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .core.legacy_env import compatible_env_enabled, get_compatible_env
from .version import APP_VERSION, DEFAULT_UPDATE_REPO

USER_AGENT = f"Siming/{APP_VERSION}"
EXE_NAME = "Siming.exe"
COMPATIBLE_EXE_NAMES = {EXE_NAME.lower()}
CHECKSUM_ASSET_NAMES = {"sha256.txt", f"{EXE_NAME.lower()}.sha256"}
STAGED_UPDATE_FILENAME = "pending-update.json"
UPDATE_CHANNELS = {"stable", "preview"}


def _version_tuple(value: str) -> tuple[int, ...]:
    core, _prerelease = _parse_semver(value)
    return core


def _parse_semver(value: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    text = str(value or "").strip().lower().removeprefix("v")
    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9a-z.-]+))?",
        text,
    )
    if not match:
        return (0, 0, 0), None
    core = tuple(int(part or 0) for part in match.groups()[:3])
    prerelease = (
        tuple(part for part in match.group(4).split(".") if part)
        if match.group(4)
        else None
    )
    return core, prerelease


def _compare_prerelease(
    left: tuple[str, ...] | None,
    right: tuple[str, ...] | None,
) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return 1
    if right is None:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_part) > int(right_part) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1
    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    latest_core, latest_prerelease = _parse_semver(latest)
    current_core, current_prerelease = _parse_semver(current)
    if latest_core != current_core:
        return latest_core > current_core
    return _compare_prerelease(latest_prerelease, current_prerelease) > 0


def default_update_channel(version: str = APP_VERSION) -> str:
    _core, prerelease = _parse_semver(version)
    return "preview" if prerelease is not None else "stable"


def resolve_update_channel(channel: str | None = None) -> str:
    selected = str(
        channel
        or get_compatible_env("SIMING_UPDATE_CHANNEL")
        or ""
    ).strip().lower()
    return selected if selected in UPDATE_CHANNELS else default_update_channel()


def _github_token() -> str | None:
    return (
        get_compatible_env("SIMING_GITHUB_TOKEN", "GITHUB_TOKEN")
    )


def _request_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(url: str, timeout: float = 8.0) -> bytes:
    request = urllib_request.Request(url, headers=_request_headers())
    with urllib_request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_json(url: str, timeout: float = 8.0) -> Any:
    return json.loads(_request(url, timeout=timeout).decode("utf-8-sig"))


def _manifest_from_url(url: str) -> dict[str, Any] | None:
    data = _request_json(url)
    version = str(data.get("version") or data.get("tag_name") or "").strip().removeprefix("v")
    download_url = str(data.get("download_url") or data.get("url") or "").strip()
    if not version or not download_url:
        return None
    return {
        "version": version,
        "download_url": download_url,
        "sha256": str(data.get("sha256") or "").strip().lower(),
        "source": url,
    }


def _manifest_from_release_payload(
    repo: str,
    release: dict[str, Any],
) -> dict[str, Any] | None:
    tag = str(release.get("tag_name") or release.get("name") or "").strip()
    version = tag.removeprefix("v")
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    exe_asset = None
    checksum_asset = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if name.lower() == EXE_NAME.lower():
            exe_asset = asset
        elif name.lower() in CHECKSUM_ASSET_NAMES:
            checksum_asset = asset
    if not version or not exe_asset:
        return None
    sha256 = ""
    if checksum_asset and checksum_asset.get("browser_download_url"):
        try:
            checksum_text = _request(str(checksum_asset["browser_download_url"]), timeout=6).decode("utf-8", errors="ignore")
            match = re.search(r"\b[a-fA-F0-9]{64}\b", checksum_text)
            sha256 = match.group(0).lower() if match else ""
        except Exception:
            sha256 = ""
    return {
        "version": version,
        "download_url": str(exe_asset.get("browser_download_url") or ""),
        "sha256": sha256,
        "source": release.get("html_url") or f"https://github.com/{repo}/releases/latest",
    }


def _release_has_executable(release: dict[str, Any]) -> bool:
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    return any(
        isinstance(asset, dict)
        and str(asset.get("name") or "").lower() == EXE_NAME.lower()
        for asset in assets
    )


def _manifest_from_github_release(
    repo: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    selected_channel = resolve_update_channel(channel)
    if selected_channel == "stable":
        release = _request_json(
            f"https://api.github.com/repos/{repo}/releases/latest"
        )
        if not isinstance(release, dict):
            return None
        return _manifest_from_release_payload(repo, release)

    releases = _request_json(
        f"https://api.github.com/repos/{repo}/releases?per_page=30"
    )
    if not isinstance(releases, list):
        return None
    eligible = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and _release_has_executable(release)
    ]
    if not eligible:
        return None
    latest = eligible[0]
    for candidate in eligible[1:]:
        candidate_version = str(
            candidate.get("tag_name") or candidate.get("name") or ""
        )
        latest_version = str(latest.get("tag_name") or latest.get("name") or "")
        if is_newer_version(candidate_version, latest_version):
            latest = candidate
    return _manifest_from_release_payload(repo, latest)


def find_latest_update(channel: str | None = None) -> dict[str, Any] | None:
    """Return metadata only.  This function never downloads an update."""
    if compatible_env_enabled("SIMING_DISABLE_UPDATE"):
        return None
    manifest_url = (
        get_compatible_env("SIMING_UPDATE_MANIFEST_URL")
    ).strip()
    repo = (
        get_compatible_env("SIMING_UPDATE_REPO", default=DEFAULT_UPDATE_REPO)
    ).strip()
    selected_channel = resolve_update_channel(channel)
    try:
        manifest = (
            _manifest_from_url(manifest_url)
            if manifest_url
            else _manifest_from_github_release(repo, selected_channel)
        )
    except (OSError, urllib_error.URLError, urllib_error.HTTPError, json.JSONDecodeError):
        return None
    if not manifest or not manifest.get("download_url"):
        return None
    manifest["channel"] = selected_channel
    return manifest if is_newer_version(str(manifest.get("version") or "")) else None


def _updates_dir(app_home: Path) -> Path:
    return app_home / "updates"


def _stage_path(app_home: Path) -> Path:
    return _updates_dir(app_home) / STAGED_UPDATE_FILENAME


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def _expected_sha256(manifest: dict[str, Any]) -> str:
    expected = str(manifest.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise RuntimeError("The release does not provide a valid SHA-256 checksum.")
    return expected


def _download_to_file(url: str, target: Path, timeout: float = 120) -> None:
    request = urllib_request.Request(url, headers=_request_headers())
    with urllib_request.urlopen(request, timeout=timeout) as response, target.open("wb") as file:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)


def _verify_authenticode_signature(path: Path) -> dict[str, Any]:
    """Verify Authenticode directly through Windows without launching a shell."""
    if os.name != "nt":
        return {
            "valid": False,
            "status": "unsupported_platform",
            "subject": "",
            "thumbprint": "",
        }
    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class WinTrustFileInfo(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.POINTER(Guid)),
        ]

    class WinTrustData(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.c_void_p),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    # WINTRUST_ACTION_GENERIC_VERIFY_V2 and the values documented by WinVerifyTrust.
    action = Guid(
        0xAAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = WinTrustFileInfo(
        cbStruct=ctypes.sizeof(WinTrustFileInfo),
        pcwszFilePath=str(path),
        hFile=None,
        pgKnownSubject=None,
    )
    data = WinTrustData(
        cbStruct=ctypes.sizeof(WinTrustData),
        pPolicyCallbackData=None,
        pSIPClientData=None,
        dwUIChoice=2,  # WTD_UI_NONE
        fdwRevocationChecks=0,  # WTD_REVOKE_NONE
        dwUnionChoice=1,  # WTD_CHOICE_FILE
        pFile=ctypes.cast(ctypes.pointer(file_info), ctypes.c_void_p),
        dwStateAction=1,  # WTD_STATEACTION_VERIFY
        hWVTStateData=None,
        pwszURLReference=None,
        dwProvFlags=0,
        dwUIContext=0,
        pSignatureSettings=None,
    )
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        verify = wintrust.WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(Guid), ctypes.c_void_p]
        verify.restype = ctypes.c_long
        result = int(verify(None, ctypes.byref(action), ctypes.byref(data)))
        status_code = result & 0xFFFFFFFF
        status_labels = {
            0x800B0100: "no_signature",
            0x800B0109: "untrusted_root",
            0x800B010C: "certificate_revoked",
            0x80096010: "bad_digest",
        }
        return {
            "valid": result == 0,
            "status": "Valid" if result == 0 else status_labels.get(status_code, f"wintrust_0x{status_code:08x}"),
            "subject": "",
            "thumbprint": "",
        }
    except Exception as exc:
        return {
            "valid": False,
            "status": "verification_failed",
            "subject": "",
            "thumbprint": "",
            "error": str(exc),
        }
    finally:
        try:
            data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
            verify(None, ctypes.byref(action), ctypes.byref(data))
        except Exception:
            pass


def _require_valid_signature(path: Path) -> dict[str, Any]:
    signature = _verify_authenticode_signature(path)
    if not signature.get("valid"):
        detail = str(signature.get("status") or "invalid")
        raise RuntimeError(f"The update signature is not trusted ({detail}).")
    return signature


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": str(manifest.get("version") or ""),
        "channel": str(manifest.get("channel") or ""),
        "source": str(manifest.get("source") or ""),
        "download_url": str(manifest.get("download_url") or ""),
        "sha256_available": bool(re.fullmatch(r"[a-fA-F0-9]{64}", str(manifest.get("sha256") or ""))),
    }


def _read_staged_update(app_home: Path) -> dict[str, Any] | None:
    path = _stage_path(app_home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_staged_update(app_home: Path, payload: dict[str, Any]) -> None:
    path = _stage_path(app_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _validate_staged_update(app_home: Path) -> dict[str, Any]:
    staged = _read_staged_update(app_home)
    if not staged:
        raise RuntimeError("No downloaded update is waiting to be installed.")
    update_path = Path(str(staged.get("path") or "")).expanduser()
    expected_sha256 = str(staged.get("sha256") or "").strip().lower()
    if not update_path.is_file() or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise RuntimeError("The downloaded update is incomplete. Please download it again.")
    actual_sha256 = _sha256_file(update_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError("The downloaded update checksum no longer matches the verified SHA-256 value.")
    signature = _require_valid_signature(update_path)
    staged["signature"] = signature
    staged["sha256"] = actual_sha256
    return staged


def get_update_status(
    app_home: Path,
    channel: str | None = None,
) -> dict[str, Any]:
    """Check only metadata and surface any already verified staged update."""
    selected_channel = resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    staged = _read_staged_update(app_home)
    staged_payload = None
    if staged:
        staged_payload = {
            "version": str(staged.get("version") or ""),
            "sha256": str(staged.get("sha256") or ""),
            "signature": staged.get("signature") if isinstance(staged.get("signature"), dict) else None,
            "ready_to_install": False,
        }
        try:
            _validate_staged_update(app_home)
            staged_payload["ready_to_install"] = True
        except Exception as exc:
            staged_payload["error"] = str(exc)
    return {
        "current_version": APP_VERSION,
        "update_channel": selected_channel,
        "update_available": bool(manifest),
        "update": _public_manifest(manifest) if manifest else None,
        "staged_update": staged_payload,
        "automatic_updates": False,
    }


def download_and_stage_update(
    app_home: Path,
    channel: str | None = None,
) -> dict[str, Any]:
    """Download a user-confirmed update and require both hash and signature checks."""
    selected_channel = resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    if not manifest:
        return get_update_status(app_home, selected_channel)
    expected_sha256 = _expected_sha256(manifest)
    updates_dir = _updates_dir(app_home)
    updates_dir.mkdir(parents=True, exist_ok=True)
    version = str(manifest["version"]).strip().removeprefix("v")
    target = updates_dir / f"Siming-{version}.exe"
    partial = target.with_name(target.name + ".part")
    try:
        if target.exists() and _sha256_file(target) != expected_sha256:
            target.unlink(missing_ok=True)
        if not target.exists():
            partial.unlink(missing_ok=True)
            _download_to_file(str(manifest["download_url"]), partial)
            actual_sha256 = _sha256_file(partial)
            if actual_sha256 != expected_sha256:
                raise RuntimeError("Downloaded update checksum does not match the release manifest.")
            partial.replace(target)
        actual_sha256 = _sha256_file(target)
        if actual_sha256 != expected_sha256:
            raise RuntimeError("Downloaded update checksum does not match the release manifest.")
        signature = _require_valid_signature(target)
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    staged = {
        "version": version,
        "path": str(target.resolve()),
        "sha256": actual_sha256,
        "source": str(manifest.get("source") or ""),
        "signature": signature,
    }
    _write_staged_update(app_home, staged)
    return {
        **get_update_status(app_home, selected_channel),
        "downloaded": True,
        "staged_update": {
            "version": version,
            "sha256": actual_sha256,
            "signature": signature,
            "ready_to_install": True,
        },
    }


def _current_packaged_executable() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Updates can only be installed from the packaged Siming.exe application.")
    current_exe = Path(sys.executable).resolve()
    if current_exe.name.lower() not in COMPATIBLE_EXE_NAMES:
        raise RuntimeError("The running executable is not a supported Siming release.")
    return current_exe


def schedule_staged_update_install(app_home: Path) -> dict[str, Any]:
    """Start the verified new executable as a small update helper.

    The new executable waits for the current application to exit, replaces the
    old file itself, and restarts.  This avoids PowerShell and policy bypasses.
    """
    current_exe = _current_packaged_executable()
    staged = _validate_staged_update(app_home)
    update_exe = Path(str(staged["path"])).resolve()
    if update_exe == current_exe:
        raise RuntimeError("The staged update cannot replace itself.")
    subprocess.Popen(
        [
            str(update_exe),
            "--apply-staged-update",
            "--update-target",
            str(current_exe),
            "--wait-pid",
            str(os.getpid()),
            "--expected-sha256",
            str(staged["sha256"]),
            "--update-metadata",
            str(_stage_path(app_home)),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(current_exe.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "version": str(staged.get("version") or ""),
        "signature": staged.get("signature"),
        "restart_scheduled": True,
    }


def apply_update_if_available(app_home: Path) -> bool:
    """Compatibility shim: silent updates are permanently disabled."""
    del app_home
    return False
