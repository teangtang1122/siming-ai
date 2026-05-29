"""Name normalization helpers for cataloging identity matching."""
from __future__ import annotations

import re


_ALIAS_SEPARATOR_RE = re.compile(r"\s*(?:[／/、,，;；|｜]+|又名|别名|化名|也叫|又叫|本名|原名)\s*")
_PAREN_RE = re.compile(r"[（(]([^（）()]{1,40})[）)]")


def split_character_name(value: str | None) -> list[str]:
    """Split a model-provided character name into possible identity labels."""
    text = str(value or "").strip()
    if not text:
        return []
    expanded = _PAREN_RE.sub(r"|\1", text)
    parts = [part.strip() for part in _ALIAS_SEPARATOR_RE.split(expanded) if part.strip()]
    return list(dict.fromkeys(parts))


def normalize_name_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s:：，,。.!！?？（）()\[\]【】《》<>\"'“”‘’]+", "", text)


def derived_character_aliases(name: str | None) -> list[str]:
    """Derive conservative aliases for common Chinese kinship/title labels."""
    text = str(name or "").strip()
    aliases: list[str] = []
    if not text:
        return aliases
    if text.endswith("老爷子"):
        surname = text[:-3]
        aliases.extend(["老爷子", "爷爷"])
        if surname:
            aliases.append(f"{surname}爷爷")
    elif text.endswith("爷爷") and text != "爷爷":
        surname = text[:-2]
        aliases.append("爷爷")
        if surname:
            aliases.append(f"{surname}老爷子")
    return list(dict.fromkeys(alias for alias in aliases if alias and alias != text))
