"""Signed-manifest-ready catalog for local models and runtimes.

The embedded catalog is always available offline. A remote manifest can replace
it after signature verification is added by the release pipeline.
"""
from __future__ import annotations

import base64
import json
import re
from copy import deepcopy

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ...core.legacy_env import get_compatible_env
from .paths import siming_home

MODEL_CATALOG = [
    {
        "model_key": "qwen3.6-27b-q4",
        "display_name": "Qwen3.6 27B UD-Q4_K_XL",
        "family": "qwen3.6",
        "parameter_size": "27B",
        "quantization": "UD-Q4_K_XL",
        "context_length": 262144,
        "file_name": "Qwen3.6-27B-UD-Q4_K_XL.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 32,
        "recommended_vram_gb": 24,
        "sources": [
            "https://huggingface.co/unsloth/Qwen3.6-27B-GGUF/resolve/main/Qwen3.6-27B-UD-Q4_K_XL.gguf",
        ],
    },
    {
        "model_key": "qwen3.8-27b-q3",
        "display_name": "Qwen3.8 27B UD-Q3_K_XL（16GB / 文本）",
        "family": "qwen3.8",
        "parameter_size": "27B",
        "quantization": "UD-Q3_K_XL",
        "context_length": 262144,
        "file_name": "Qwen3.8-27B-UD-Q3_K_XL.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 32,
        "recommended_vram_gb": 16,
        "sha256": "8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e",
        "sources": [
            "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q3_K_XL.gguf",
            "https://modelscope.cn/models/unsloth/Qwen3.8-27B-GGUF/resolve/master/Qwen3.8-27B-UD-Q3_K_XL.gguf",
        ],
    },
    {
        "model_key": "qwen3.8-27b-q4",
        "display_name": "Qwen3.8 27B UD-Q4_K_XL",
        "family": "qwen3.8",
        "parameter_size": "27B",
        "quantization": "UD-Q4_K_XL",
        "context_length": 262144,
        "file_name": "Qwen3.8-27B-UD-Q4_K_XL.gguf",
        "license_name": "Apache-2.0",
        "min_ram_gb": 32,
        "recommended_vram_gb": 24,
        "sources": [
            "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q4_K_XL.gguf",
        ],
    },
]


def _is_curated_size(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if (
        str(item.get("family", "")).lower() == "qwen3.5"
        or str(item.get("model_key", "")).lower().startswith("qwen3.5-")
    ):
        return False
    match = re.fullmatch(r"(\d+(?:\.\d+)?)B", str(item.get("parameter_size", "")).upper())
    return bool(match and float(match.group(1)) >= 27)


def model_catalog() -> list[dict]:
    remote = _load_verified_remote_manifest()
    models = remote.get("models") if isinstance(remote, dict) else None
    curated = (
        [item for item in models if _is_curated_size(item)]
        if isinstance(models, list)
        else []
    )
    return deepcopy(curated or MODEL_CATALOG)


def model_spec(model_key: str) -> dict | None:
    return next((item for item in model_catalog() if item["model_key"] == model_key), None)


def _load_verified_remote_manifest() -> dict | None:
    """Load an optional signed manifest without weakening the offline catalog."""
    url = get_compatible_env("SIMING_MODEL_MANIFEST_URL").strip()
    public_key_b64 = (
        get_compatible_env("SIMING_MODEL_MANIFEST_PUBLIC_KEY")
    ).strip()
    cache = siming_home() / "model-manifest.json"
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
