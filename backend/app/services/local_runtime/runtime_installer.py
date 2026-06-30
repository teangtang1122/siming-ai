"""Download and install the latest supported llama.cpp Windows runtime."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from .downloads import download_with_fallback
from .hardware import HardwareProfile
from .paths import downloads_root, runtime_root


GITHUB_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


def _release() -> dict:
    request = Request(GITHUB_RELEASE_API, headers={"User-Agent": "Siming/2.5"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _asset_score(name: str, hardware: HardwareProfile) -> int:
    lowered = name.lower()
    if not lowered.endswith(".zip") or "win" not in lowered or "x64" not in lowered:
        return -1000
    score = 0
    if "bin" in lowered:
        score += 20
    if hardware.nvidia_available and "cuda" in lowered:
        score += 50
    elif not hardware.nvidia_available and ("avx2" in lowered or "cpu" in lowered):
        score += 30
    if "vulkan" in lowered:
        score += 10
    if "cudart" in lowered:
        score -= 20
    return score


def install_llama_cpp(task_id: str, hardware: HardwareProfile) -> dict:
    release = _release()
    assets = release.get("assets") or []
    candidates = sorted(
        assets,
        key=lambda asset: _asset_score(str(asset.get("name") or ""), hardware),
        reverse=True,
    )
    if not candidates or _asset_score(str(candidates[0].get("name") or ""), hardware) < 0:
        raise RuntimeError("未找到兼容的 llama.cpp Windows 运行时")
    asset = candidates[0]
    version = str(release.get("tag_name") or "latest")
    archive = downloads_root() / str(asset["name"])
    download_with_fallback(task_id, [str(asset["browser_download_url"])], archive)
    target = runtime_root() / "llama_cpp" / version
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    if hardware.nvidia_available and "cuda" in str(asset["name"]).lower():
        cudart = next(
            (
                item
                for item in assets
                if "cudart" in str(item.get("name") or "").lower()
                and str(item.get("name") or "").lower().endswith(".zip")
            ),
            None,
        )
        if cudart:
            cudart_archive = downloads_root() / str(cudart["name"])
            download_with_fallback(
                task_id,
                [str(cudart["browser_download_url"])],
                cudart_archive,
            )
            with zipfile.ZipFile(cudart_archive) as bundle:
                bundle.extractall(target)
    executables = list(target.rglob("llama-server.exe"))
    if not executables:
        raise RuntimeError("运行时压缩包中没有 llama-server.exe")
    return {
        "version": version,
        "backend": "cuda" if "cuda" in str(asset["name"]).lower() else "cpu",
        "install_path": str(target),
        "executable_path": str(executables[0]),
        "asset_name": asset["name"],
    }
