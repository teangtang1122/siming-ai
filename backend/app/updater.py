"""GitHub Release updater for the packaged Windows executable."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .version import APP_VERSION, DEFAULT_UPDATE_REPO


USER_AGENT = f"NovelWritingAgent/{APP_VERSION}"
EXE_NAME = "NovelWritingAgent.exe"


def _version_tuple(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lower()
    text = text.removeprefix("v")
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts) if parts else (0,)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    latest_parts = _version_tuple(latest)
    current_parts = _version_tuple(current)
    width = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (width - len(latest_parts)) > current_parts + (0,) * (width - len(current_parts))


def _github_token() -> str | None:
    return os.environ.get("NOVEL_AGENT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _request(url: str, timeout: float = 8.0) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
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


def _manifest_from_github_release(repo: str) -> dict[str, Any] | None:
    release = _request_json(f"https://api.github.com/repos/{repo}/releases/latest")
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
        elif name.lower() in {"sha256.txt", f"{EXE_NAME.lower()}.sha256"}:
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


def find_latest_update() -> dict[str, Any] | None:
    if os.environ.get("NOVEL_AGENT_DISABLE_UPDATE") == "1":
        return None
    manifest_url = os.environ.get("NOVEL_AGENT_UPDATE_MANIFEST_URL", "").strip()
    repo = os.environ.get("NOVEL_AGENT_UPDATE_REPO", DEFAULT_UPDATE_REPO).strip()
    try:
        manifest = _manifest_from_url(manifest_url) if manifest_url else _manifest_from_github_release(repo)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None
    if not manifest or not manifest.get("download_url"):
        return None
    if not is_newer_version(str(manifest.get("version") or "")):
        return None
    return manifest


def _download_update(manifest: dict[str, Any], updates_dir: Path) -> Path:
    updates_dir.mkdir(parents=True, exist_ok=True)
    version = str(manifest["version"]).strip().removeprefix("v")
    target = updates_dir / f"NovelWritingAgent-{version}.exe"
    data = _request(str(manifest["download_url"]), timeout=120)
    digest = hashlib.sha256(data).hexdigest().lower()
    expected = str(manifest.get("sha256") or "").strip().lower()
    if expected and digest != expected:
        raise RuntimeError("Downloaded update checksum does not match the release manifest.")
    target.write_bytes(data)
    return target


def _quote_ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_apply_script(script_path: Path, update_exe: Path, current_exe: Path, current_pid: int) -> None:
    content = f"""
$ErrorActionPreference = "Stop"
$UpdateExe = {_quote_ps(update_exe)}
$CurrentExe = {_quote_ps(current_exe)}
$CurrentPid = {current_pid}
try {{
  Wait-Process -Id $CurrentPid -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 800
  Copy-Item -LiteralPath $UpdateExe -Destination $CurrentExe -Force
  Start-Process -FilePath $CurrentExe
}} catch {{
  Add-Content -LiteralPath ({_quote_ps(script_path.parent)} + "\\update-error.log") -Value $_.Exception.Message
}}
"""
    script_path.write_text(content.strip() + "\n", encoding="utf-8")


def apply_update_if_available(app_home: Path) -> bool:
    """Return True when an update was scheduled and the current process should exit."""
    if not getattr(sys, "frozen", False):
        return False
    current_exe = Path(sys.executable).resolve()
    if current_exe.name.lower() != EXE_NAME.lower():
        return False
    manifest = find_latest_update()
    if not manifest:
        return False
    updates_dir = app_home / "updates"
    update_exe = _download_update(manifest, updates_dir)
    script_path = updates_dir / "apply-update.ps1"
    _write_apply_script(script_path, update_exe, current_exe, os.getpid())
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"Update {manifest['version']} downloaded. Restarting with the new version...")
    return True
