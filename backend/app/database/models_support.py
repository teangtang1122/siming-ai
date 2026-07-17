"""Small model helpers shared by domain-owned persistence modules."""
from __future__ import annotations

import uuid


def generate_uuid() -> str:
    """Generate a UUID string suitable for existing Siming primary keys."""

    return str(uuid.uuid4())


__all__ = ["generate_uuid"]
