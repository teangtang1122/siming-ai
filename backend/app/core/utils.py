"""Shared pure utility functions."""
from __future__ import annotations

import re
from datetime import UTC, datetime


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize a database UTC timestamp with an explicit UTC offset.

    SQLite returns timezone-naive ``datetime`` values even when the stored
    convention is UTC.  Sending their bare ``isoformat()`` value makes a web
    client interpret UTC as local time.  Preserve aware inputs and make the
    database convention explicit for naive values.
    """

    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(UTC).isoformat()


def count_words(text: str) -> int:
    """Count characters excluding spaces and newlines, matching mainstream novel platforms."""
    return len(re.sub(r"[\s]", "", text))


def count_han_characters(text: str) -> int:
    """Count CJK unified ideographs in chapter prose.

    This is a deterministic format metric used only after the Agent has
    translated an author's natural-language length request into a structured
    tool field.  It deliberately does not infer intent from prose.
    """
    ranges = (
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
        (0xF900, 0xFAFF),
        (0x20000, 0x2FA1F),
        (0x30000, 0x323AF),
    )
    return sum(
        1
        for character in text or ""
        if any(start <= ord(character) <= end for start, end in ranges)
    )
