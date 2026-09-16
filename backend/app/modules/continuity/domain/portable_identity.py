"""Stable generated container/governance IDs shared with the Android storage adapter."""

from __future__ import annotations

from hashlib import md5
from uuid import UUID


def portable_cataloging_id(*parts: str) -> str:
    # Java UUID.nameUUIDFromBytes uses an unnamespaced version-3 UUID.
    return str(
        UUID(bytes=md5("|".join(parts).encode("utf-8"), usedforsecurity=False).digest(), version=3)
    )
