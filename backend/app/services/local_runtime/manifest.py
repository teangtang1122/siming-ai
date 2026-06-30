"""Signed-manifest-ready catalog for local models and runtimes.

The embedded catalog is always available offline. A remote manifest can replace
it after signature verification is added by the release pipeline.
"""
from __future__ import annotations

from copy import deepcopy
import base64
import json
import os
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .paths import moshu_home


MODEL_CATALOG = [
    {
        "model_key": "qwen3-4b-q4",
        "display_name": "Qwen3 4B Q4_K_M",
        "family": "qwen3",
        "parameter_size": "4B",
        "quantization": "Q4_K_M",
        "context_length": 32768,
        "file_name": "Qwen3-4B-Q4_K_M.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 8,
        "recommended_vram_gb": 4,
        "sources": [
            "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
            "https://www.modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/Qwen3-4B-Q4_K_M.gguf",
        ],
    },
    {
        "model_key": "qwen3-8b-q4",
        "display_name": "Qwen3 8B Q4_K_M",
        "family": "qwen3",
        "parameter_size": "8B",
        "quantization": "Q4_K_M",
        "context_length": 32768,
        "file_name": "Qwen3-8B-Q4_K_M.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 12,
        "recommended_vram_gb": 8,
        "sources": [
            "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
            "https://www.modelscope.cn/models/Qwen/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf",
        ],
    },
    {
        "model_key": "qwen3-14b-q4",
        "display_name": "Qwen3 14B Q4_K_M",
        "family": "qwen3",
        "parameter_size": "14B",
        "quantization": "Q4_K_M",
        "context_length": 32768,
        "file_name": "Qwen3-14B-Q4_K_M.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 20,
        "recommended_vram_gb": 14,
        "sources": [
            "https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf",
            "https://www.modelscope.cn/models/Qwen/Qwen3-14B-GGUF/resolve/master/Qwen3-14B-Q4_K_M.gguf",
        ],
    },
]


def model_catalog() -> list[dict]:
    remote = _load_verified_remote_manifest()
    models = remote.get("models") if isinstance(remote, dict) else None
    return deepcopy(models if isinstance(models, list) and models else MODEL_CATALOG)


def model_spec(model_key: str) -> dict | None:
    return next((item for item in model_catalog() if item["model_key"] == model_key), None)


def _load_verified_remote_manifest() -> dict | None:
    """Load an optional signed manifest without weakening the offline catalog."""
    url = (os.environ.get("SIMING_MODEL_MANIFEST_URL") or os.environ.get("MOSHU_MODEL_MANIFEST_URL", "")).strip()
    public_key_b64 = (
        os.environ.get("SIMING_MODEL_MANIFEST_PUBLIC_KEY")
        or os.environ.get("MOSHU_MODEL_MANIFEST_PUBLIC_KEY", "")
    ).strip()
    cache = moshu_home() / "model-manifest.json"
    if url and public_key_b64:
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True)
            response.raise_for_status()
            envelope = response.json()
            payload = envelope["payload"]
            signature = base64.b64decode(envelope["signature"])
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
            public_key.verify(signature, canonical)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            return payload
        except Exception:
            pass
    if cache.exists() and public_key_b64:
        try:
            envelope = json.loads(cache.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
            public_key.verify(base64.b64decode(envelope["signature"]), canonical)
            return payload
        except Exception:
            return None
    return None
