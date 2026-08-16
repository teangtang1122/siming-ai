"""Installer-aware Windows update flow.

New installed builds prefer the signed Inno Setup package so a complete onedir
runtime can be replaced safely.  The legacy Siming.exe release asset remains a
compatibility bridge for older clients whose updater only understands the
single-file executable.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error as urllib_error

from . import updater as legacy
from .core.legacy_env import compatible_env_enabled, get_compatible_env
from .version import APP_VERSION, DEFAULT_UPDATE_REPO

INSTALLER_NAME = "Siming-Setup.exe"
PORTABLE_NAME = legacy.EXE_NAME
INSTALL_MARKER = ".siming-installed"
INSTALLER_CHECKSUM_ASSET_NAMES = {
    "siming-setup.sha256",
    "siming-setup.exe.sha256",
}


def _valid_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[a-f0-9]{64}", text) else ""


def _asset_sha256(asset: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    digest = str(asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        value = _valid_sha256(digest.removeprefix("sha256:"))
        if value:
            return value

    checksum_asset = next(
        (
            candidate
            for candidate in assets
            if str(candidate.get("name") or "").lower()
            in INSTALLER_CHECKSUM_ASSET_NAMES
        ),
        None,
    )
    if checksum_asset and checksum_asset.get("browser_download_url"):
        try:
            text = legacy._request(
                str(checksum_asset["browser_download_url"]),
                timeout=6,
            ).decode("utf-8", errors="ignore")
        except Exception:
            return ""
        match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
        return match.group(1).lower() if match else ""
    return ""


def _manifest_from_release_payload(
    repo: str,
    release: dict[str, Any],
) -> dict[str, Any] | None:
    tag = str(release.get("tag_name") or release.get("name") or "").strip()
    version = tag.removeprefix("v")
    raw_assets = release.get("assets")
    assets = [asset for asset in raw_assets if isinstance(asset, dict)] if isinstance(raw_assets, list) else []
    installer_asset = next(
        (
            asset
            for asset in assets
            if str(asset.get("name") or "").lower() == INSTALLER_NAME.lower()
        ),
        None,
    )
    if version and installer_asset and installer_asset.get("browser_download_url"):
        return {
            "version": version,
            "download_url": str(installer_asset["browser_download_url"]),
            "sha256": _asset_sha256(installer_asset, assets),
            "source": release.get("html_url")
            or f"https://github.com/{repo}/releases/latest",
            "asset_name": INSTALLER_NAME,
            "install_mode": "installer",
        }

    portable = legacy._manifest_from_release_payload(repo, release)
    if not portable:
        return None
    return {
        **portable,
        "asset_name": PORTABLE_NAME,
        "install_mode": "portable",
    }


def _release_has_update_asset(release: dict[str, Any]) -> bool:
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    supported = {INSTALLER_NAME.lower(), PORTABLE_NAME.lower()}
    return any(
        isinstance(asset, dict)
        and str(asset.get("name") or "").lower() in supported
        for asset in assets
    )


def _manifest_from_github_release(
    repo: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    selected_channel = legacy.resolve_update_channel(channel)
    if selected_channel == "stable":
        release = legacy._request_json(
            f"https://api.github.com/repos/{repo}/releases/latest"
        )
        return (
            _manifest_from_release_payload(repo, release)
            if isinstance(release, dict)
            else None
        )

    releases = legacy._request_json(
        f"https://api.github.com/repos/{repo}/releases?per_page=30"
    )
    if not isinstance(releases, list):
        return None
    eligible = [
        release
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and _release_has_update_asset(release)
    ]
    if not eligible:
        return None
    latest = eligible[0]
    for candidate in eligible[1:]:
        candidate_version = str(
            candidate.get("tag_name") or candidate.get("name") or ""
        )
        latest_version = str(latest.get("tag_name") or latest.get("name") or "")
        if legacy.is_newer_version(candidate_version, latest_version):
            latest = candidate
    return _manifest_from_release_payload(repo, latest)


def _manifest_from_url(url: str) -> dict[str, Any] | None:
    data = legacy._request_json(url)
    if not isinstance(data, dict):
        return None
    version = str(data.get("version") or data.get("tag_name") or "").strip().removeprefix("v")
    download_url = str(data.get("download_url") or data.get("url") or "").strip()
    if not version or not download_url:
        return None
    asset_name = str(data.get("asset_name") or Path(download_url).name or "").strip()
    install_mode = str(data.get("install_mode") or "").strip().lower()
    if install_mode not in {"installer", "portable"}:
        install_mode = (
            "installer"
            if asset_name.lower() == INSTALLER_NAME.lower()
            else "portable"
        )
    return {
        "version": version,
        "download_url": download_url,
        "sha256": _valid_sha256(data.get("sha256")),
        "source": url,
        "asset_name": asset_name or (INSTALLER_NAME if install_mode == "installer" else PORTABLE_NAME),
        "install_mode": install_mode,
    }


def _running_install_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    current = Path(sys.executable).resolve()
    if current.name.lower() != PORTABLE_NAME.lower():
        return None
    return current.parent if (current.parent / INSTALL_MARKER).is_file() else None


def _portable_migration_needed(manifest: dict[str, Any]) -> bool:
    if str(manifest.get("install_mode") or "") != "installer":
        return False
    if not getattr(sys, "frozen", False):
        return False
    current = Path(sys.executable).resolve()
    if current.name.lower() != PORTABLE_NAME.lower():
        return False
    if (current.parent / INSTALL_MARKER).is_file():
        return False
    latest = str(manifest.get("version") or "").strip().removeprefix("v")
    current_version = str(APP_VERSION).strip().removeprefix("v")
    return latest == current_version


def find_latest_update(channel: str | None = None) -> dict[str, Any] | None:
    """Return the preferred installer update, falling back to legacy portable."""
    if compatible_env_enabled("SIMING_DISABLE_UPDATE"):
        return None
    manifest_url = get_compatible_env("SIMING_UPDATE_MANIFEST_URL").strip()
    repo = get_compatible_env(
        "SIMING_UPDATE_REPO",
        default=DEFAULT_UPDATE_REPO,
    ).strip()
    selected_channel = legacy.resolve_update_channel(channel)
    try:
        manifest = (
            _manifest_from_url(manifest_url)
            if manifest_url
            else _manifest_from_github_release(repo, selected_channel)
        )
    except (
        OSError,
        urllib_error.URLError,
        urllib_error.HTTPError,
        json.JSONDecodeError,
    ):
        return None
    if not manifest or not manifest.get("download_url"):
        return None
    manifest["channel"] = selected_channel
    manifest["migration"] = _portable_migration_needed(manifest)
    if legacy.is_newer_version(str(manifest.get("version") or "")):
        return manifest
    return manifest if manifest["migration"] else None


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": str(manifest.get("version") or ""),
        "channel": str(manifest.get("channel") or ""),
        "source": str(manifest.get("source") or ""),
        "download_url": str(manifest.get("download_url") or ""),
        "asset_name": str(manifest.get("asset_name") or ""),
        "install_mode": str(manifest.get("install_mode") or ""),
        "migration": bool(manifest.get("migration")),
        "sha256_available": bool(_valid_sha256(manifest.get("sha256"))),
    }


def get_update_status(
    app_home: Path,
    channel: str | None = None,
) -> dict[str, Any]:
    selected_channel = legacy.resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    staged = legacy._read_staged_update(app_home)
    staged_payload = None
    if staged:
        staged_payload = {
            "version": str(staged.get("version") or ""),
            "sha256": str(staged.get("sha256") or ""),
            "signature": staged.get("signature") if isinstance(staged.get("signature"), dict) else None,
            "install_mode": str(staged.get("install_mode") or "portable"),
            "migration": bool(staged.get("migration")),
            "ready_to_install": False,
        }
        try:
            legacy._validate_staged_update(app_home)
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
        "installed_layout": _running_install_root() is not None,
    }


def download_and_stage_update(
    app_home: Path,
    channel: str | None = None,
) -> dict[str, Any]:
    selected_channel = legacy.resolve_update_channel(channel)
    manifest = find_latest_update(selected_channel)
    if not manifest:
        return get_update_status(app_home, selected_channel)
    expected_sha256 = legacy._expected_sha256(manifest)
    updates_dir = legacy._updates_dir(app_home)
    updates_dir.mkdir(parents=True, exist_ok=True)
    version = str(manifest["version"]).strip().removeprefix("v")
    install_mode = str(manifest.get("install_mode") or "portable")
    target_name = (
        f"Siming-Setup-{version}.exe"
        if install_mode == "installer"
        else f"Siming-{version}.exe"
    )
    target = updates_dir / target_name
    partial = target.with_name(target.name + ".part")
    try:
        if target.exists() and legacy._sha256_file(target) != expected_sha256:
            target.unlink(missing_ok=True)
        if not target.exists():
            partial.unlink(missing_ok=True)
            legacy._download_to_file(str(manifest["download_url"]), partial)
            actual_sha256 = legacy._sha256_file(partial)
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    "Downloaded update checksum does not match the release manifest."
                )
            partial.replace(target)
        actual_sha256 = legacy._sha256_file(target)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                "Downloaded update checksum does not match the release manifest."
            )
        signature = legacy._require_valid_signature(target)
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    staged = {
        "version": version,
        "path": str(target.resolve()),
        "sha256": actual_sha256,
        "source": str(manifest.get("source") or ""),
        "asset_name": str(manifest.get("asset_name") or ""),
        "install_mode": install_mode,
        "migration": bool(manifest.get("migration")),
        "signature": signature,
    }
    legacy._write_staged_update(app_home, staged)
    result = get_update_status(app_home, selected_channel)
    result["downloaded"] = True
    result["staged_update"] = {
        "version": version,
        "sha256": actual_sha256,
        "signature": signature,
        "install_mode": install_mode,
        "migration": bool(manifest.get("migration")),
        "ready_to_install": True,
    }
    return result


def schedule_staged_update_install(app_home: Path) -> dict[str, Any]:
    staged = legacy._validate_staged_update(app_home)
    if str(staged.get("install_mode") or "portable") != "installer":
        return legacy.schedule_staged_update_install(app_home)

    current_exe = legacy._current_packaged_executable()
    installer = Path(str(staged["path"])).resolve()
    installed_layout = (current_exe.parent / INSTALL_MARKER).is_file()
    command = [str(installer)]
    if installed_layout:
        command.extend(
            [
                "/SP-",
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={current_exe.parent}",
            ]
        )
    else:
        # First migration from the legacy one-file build remains interactive so
        # the user can choose the install directory and desktop shortcut.
        command.append("/SP-")

    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(installer.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "version": str(staged.get("version") or ""),
        "signature": staged.get("signature"),
        "install_mode": "installer",
        "migration": not installed_layout,
        "restart_scheduled": True,
    }


def apply_update_if_available(app_home: Path) -> bool:
    return legacy.apply_update_if_available(app_home)


__all__ = [
    "apply_update_if_available",
    "download_and_stage_update",
    "find_latest_update",
    "get_update_status",
    "schedule_staged_update_install",
]
