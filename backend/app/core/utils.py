"""Shared pure utility functions."""
from __future__ import annotations

import re


def count_words(text: str) -> int:
    """Count CJK characters and Latin words in a practical writing-friendly way."""
    cjk_chars = re.findall(r"[一-鿿]", text)
    without_cjk = re.sub(r"[一-鿿]", " ", text)
    latin_words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", without_cjk)
    return len(cjk_chars) + len(latin_words)
