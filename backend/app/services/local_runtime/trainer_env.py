"""Managed uv/Python environment for the optional NVIDIA QLoRA trainer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from .paths import downloads_root, runtime_root


UV_RELEASE_API = "https://api.github.com/repos/astral-sh/uv/releases/latest"
TRAINING_PACKAGES = [
    "torch",
    "transformers>=4.51",
    "datasets>=3.0",
    "accelerate>=1.0",
    "peft>=0.15",
    "trl>=0.17",
    "bitsandbytes>=0.45",
    "safetensors",
    "huggingface_hub",
]


def _hidden_kwargs() -> dict:
    return {
        "capture_output": True,
        "text": True,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    }


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "Siming/2.5"})
    with urlopen(request, timeout=60) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def ensure_uv() -> Path:
    existing = runtime_root() / "trainer" / "uv.exe"
    if existing.exists():
        return existing
    request = Request(UV_RELEASE_API, headers={"User-Agent": "Siming/2.5"})
    with urlopen(request, timeout=20) as response:
        release = json.loads(response.read().decode("utf-8"))
    asset = next(
        (
            item
            for item in release.get("assets") or []
            if "x86_64-pc-windows-msvc" in str(item.get("name") or "")
            and str(item.get("name") or "").endswith(".zip")
        ),
        None,
    )
    if not asset:
        raise RuntimeError("未找到 Windows 版 uv 训练环境安装器")
    archive = downloads_root() / str(asset["name"])
    _download(str(asset["browser_download_url"]), archive)
    target = runtime_root() / "trainer"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    found = next(target.rglob("uv.exe"), None)
    if not found:
        raise RuntimeError("uv 安装包中没有 uv.exe")
    if found != existing:
        shutil.copy2(found, existing)
    return existing


def ensure_training_environment(log) -> Path:
    uv = ensure_uv()
    environment = runtime_root() / "trainer" / "venv"
    python = environment / "Scripts" / "python.exe"
    if not python.exists():
        log("正在创建隔离的 Python 3.11 训练环境")
        result = subprocess.run(
            [str(uv), "venv", str(environment), "--python", "3.11"],
            **_hidden_kwargs(),
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "创建训练环境失败")
    marker = environment / ".siming-training-ready"
    if not marker.exists():
        log("首次安装 QLoRA 训练依赖，下载时间取决于网络")
        result = subprocess.run(
            [str(uv), "pip", "install", "--python", str(python), *TRAINING_PACKAGES],
            **_hidden_kwargs(),
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "安装训练依赖失败")
        marker.write_text("2.5.0", encoding="utf-8")
    return python


def ensure_llama_conversion_tools() -> Path:
    """Download llama.cpp sources required by convert_lora_to_gguf.py."""
    target = runtime_root() / "trainer" / "llama_cpp_tools"
    converter = target / "convert_lora_to_gguf.py"
    if converter.exists():
        return converter
    archive = downloads_root() / "llama.cpp-master.zip"
    if not archive.exists():
        _download(
            "https://github.com/ggml-org/llama.cpp/archive/refs/heads/master.zip",
            archive,
        )
    extract_root = runtime_root() / "trainer" / "llama_cpp_source"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extract_root)
    source = next(extract_root.rglob("convert_lora_to_gguf.py"), None)
    if not source:
        raise RuntimeError("llama.cpp 源码中没有 LoRA 转换工具")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source.parent, target)
    return converter
