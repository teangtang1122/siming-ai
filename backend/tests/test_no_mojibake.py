"""Scan source files for mojibake (corrupted encoding) patterns.

Run with:  pytest backend/tests/test_no_mojibake.py -v

This test walks the backend and frontend source trees and fails if any
known mojibake characters are found.  It is meant to be included in CI
to prevent encoding-corrupted text from being committed.

All mojibake characters are expressed as Unicode escapes so that this
file itself never contains corrupted text.
"""
import pathlib
import re

# Root of the repository (two levels up from this test file).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# ── Mojibake character class ──────────────────────────────────────────
# These characters appear when UTF-8 Chinese text is misread as GBK and
# then re-saved as UTF-8.  Each one is a strong signal of corruption.
# Using \uXXXX escapes so the test file itself stays clean.
_MOJIBAKE_CHARS = re.compile(
    "["
    "鈥"  # em-dash mojibake lead
    "鈹"
    "鍐"
    "澶"
    "瑙"
    "绔"
    "浣"
    "鎶"
    "椗"
    "璁"
    "]"
)

# U+FFFD replacement character (appears when decoding was lossy).
_REPLACEMENT_CHAR = re.compile("�")

# Double-encoded UTF-8 patterns (Western European mojibake).
_DOUBLE_ENCODED = re.compile(r"[ÃÂ][\x80-\xbf]")

# Directories to skip.
_SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".build",
    "dist",
    "release",
    "backups",
    # Runtime/user artifacts are not repository source. They may preserve raw
    # model audit text verbatim and are validated by project-data audits rather
    # than this source-encoding guard.
    "artifacts",
    "siming-projects",
    "video",
    ".venv",
    "venv",
}

# File extensions to scan.
_SCAN_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml", ".md"}


def _iter_source_files():
    """Yield all source files under the repo root, skipping irrelevant dirs."""
    for path in _REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in _SCAN_EXTENSIONS:
            continue
        # Skip this test file itself (it contains the pattern by design).
        if path.name == "test_no_mojibake.py":
            continue
        # Skip package-lock.json — npm generates it and may contain ? bytes.
        if path.name == "package-lock.json":
            continue
        yield path


def test_no_mojibake_characters():
    """Fail if any source file contains known mojibake characters."""
    violations: list[str] = []

    for fpath in _iter_source_files():
        try:
            text = fpath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — skip

        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in (_MOJIBAKE_CHARS, _REPLACEMENT_CHAR, _DOUBLE_ENCODED):
                match = pattern.search(line)
                if match:
                    rel = fpath.relative_to(_REPO_ROOT)
                    violations.append(
                        f"{rel}:{lineno} — found U+{ord(match.group()):04X}"
                    )

    assert not violations, (
        "Mojibake detected in source files:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\nFix the corrupted text and re-run the test."
    )
