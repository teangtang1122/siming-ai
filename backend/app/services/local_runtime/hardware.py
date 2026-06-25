"""Hardware detection and conservative local-model recommendations."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass
class HardwareProfile:
    os: str
    arch: str
    cpu_count: int
    ram_gb: float
    gpu_name: str | None
    vram_gb: float
    nvidia_available: bool
    profile: str
    recommended_model: str
    recommended_context: int
    training_supported: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("avail_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.total_phys / (1024**3), 1)
    except Exception:
        pass
    return 0.0


def _nvidia_gpu() -> tuple[str | None, float]:
    command = shutil.which("nvidia-smi")
    if not command:
        return None, 0.0
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = result.stdout.strip().splitlines()[0]
        name, memory_mb = [part.strip() for part in first.rsplit(",", 1)]
        return name, round(float(memory_mb) / 1024, 1)
    except Exception:
        return None, 0.0


def detect_hardware() -> HardwareProfile:
    gpu_name, vram_gb = _nvidia_gpu()
    ram_gb = _ram_gb()
    if vram_gb >= 24 and ram_gb >= 32:
        profile, model, context = "quality", "qwen3-14b-q4", 32768
    elif vram_gb >= 12 or ram_gb >= 32:
        profile, model, context = "standard", "qwen3-8b-q4", 16384
    else:
        profile, model, context = "light", "qwen3-4b-q4", 8192
    return HardwareProfile(
        os=platform.system(),
        arch=platform.machine(),
        cpu_count=os.cpu_count() or 1,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        nvidia_available=bool(gpu_name),
        profile=profile,
        recommended_model=model,
        recommended_context=context,
        training_supported=bool(gpu_name and vram_gb >= 8),
    )


def hardware_json() -> str:
    return json.dumps(detect_hardware().to_dict(), ensure_ascii=False)
