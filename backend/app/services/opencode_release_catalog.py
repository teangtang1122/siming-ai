"""Verified OpenCode releases used by Siming's managed Windows installer."""
from __future__ import annotations

import ctypes
import os
import platform
from typing import Any

PINNED_OPENCODE_VERSION = "v1.18.4"
_GITHUB_RELEASE_ROOT = (
    "https://github.com/anomalyco/opencode/releases/download/"
    f"{PINNED_OPENCODE_VERSION}"
)
_WINDOWS_ASSETS: dict[str, tuple[int, str]] = {
    "opencode-windows-arm64.zip": (
        57_547_484,
        "4b4d2b48afdf1432a697bccabe230c3d614cf8ed34f5bc0acf9ffd89bb9cfb25",
    ),
    "opencode-windows-x64-baseline.zip": (
        59_388_430,
        "3bfb70c41d0278221d1fbc58efe77f79615491252498ff3f5a82db64266234e0",
    ),
    "opencode-windows-x64.zip": (
        59_388_435,
        "814dae5724dfa396a43b6408703d0929625483e2fac135623f10f0fa8db04a96",
    ),
}


def windows_supports_avx2() -> bool:
    """Match OpenCode's Windows installer feature check, preferring baseline on doubt."""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        probe = kernel32.IsProcessorFeaturePresent
        probe.argtypes = [ctypes.c_uint]
        probe.restype = ctypes.c_bool
        return bool(probe(40))
    except (AttributeError, OSError, TypeError):
        return False


def windows_asset_name(
    *,
    machine: str | None = None,
    avx2_supported: bool | None = None,
) -> str:
    architecture = (machine or platform.machine()).lower()
    if architecture in {"arm64", "aarch64"}:
        return "opencode-windows-arm64.zip"
    if architecture not in {"amd64", "x86_64"}:
        detected = machine or platform.machine() or "unknown"
        raise RuntimeError(f"暂不支持当前 Windows 架构：{detected}")
    supports_avx2 = windows_supports_avx2() if avx2_supported is None else avx2_supported
    suffix = "" if supports_avx2 else "-baseline"
    return f"opencode-windows-x64{suffix}.zip"


def managed_windows_release(
    *,
    machine: str | None = None,
    avx2_supported: bool | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return a release whose URL and digest were verified when Siming was built."""
    asset_name = windows_asset_name(
        machine=machine,
        avx2_supported=avx2_supported,
    )
    size, sha256 = _WINDOWS_ASSETS[asset_name]
    return PINNED_OPENCODE_VERSION, {
        "name": asset_name,
        "size": size,
        "digest": f"sha256:{sha256}",
        "browser_download_url": f"{_GITHUB_RELEASE_ROOT}/{asset_name}",
    }
