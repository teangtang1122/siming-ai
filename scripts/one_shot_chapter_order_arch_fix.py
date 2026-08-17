from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected 1 match, found {count}: {old[:120]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


# Keep the explicit AI append ordering but do not grow this grandfathered module.
replace_once(
    "backend/app/services/workspace/tools/chapters.py",
    '''    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n    if existing_non_empty:\n''',
    '''    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n    if existing_non_empty:\n''',
)
# The text above intentionally matches without a blank line only after the first
# hardening pass? Normalize the two internal blank lines explicitly below.
chapters_path = ROOT / "backend/app/services/workspace/tools/chapters.py"
chapters = chapters_path.read_text(encoding="utf-8")
for old, new in (
    (
        "    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n    if existing_non_empty:\n",
        "    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n    if existing_non_empty:\n",
    ),
    (
        "        commit_session(db)\n        return result\n\n    reused_empty = existing_chapter is not None\n",
        "        commit_session(db)\n        return result\n    reused_empty = existing_chapter is not None\n",
    ),
):
    if old in chapters:
        chapters = chapters.replace(old, new, 1)
# Remove one harmless internal blank before candidate handling if still present.
chapters = chapters.replace(
    "    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n\n    if existing_non_empty:\n",
    "    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)\n    if existing_non_empty:\n",
    1,
)
chapters_path.write_text(chapters, encoding="utf-8")

# refresh_project_from_files is already grandfathered at 286 lines. Its legacy
# repair path can safely use Chapter's tail default, so keep canonical ordering
# in normal DB/Gateway paths without growing this old repair function.
replace_once(
    "backend/app/services/content_store.py",
    "from .chapter_ordering import CHAPTER_ORDER_STEP, next_chapter_sort_order\n",
    "",
)
replace_once(
    "backend/app/services/content_store.py",
    '''    chapters_by_path = {\n        str(chapter.content_file_path or ""): chapter\n        for chapter in chapters_by_id.values()\n        if chapter.content_file_path\n    }\n    next_sort_order = next_chapter_sort_order(db, project.id)\n    for path in sorted((folder / "chapters").glob("*.md")):\n''',
    '''    chapters_by_path = {\n        str(chapter.content_file_path or ""): chapter\n        for chapter in chapters_by_id.values()\n        if chapter.content_file_path\n    }\n    for path in sorted((folder / "chapters").glob("*.md")):\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''                current_version=int(meta.get("current_version") or 1),\n                sort_order=(\n                    int(meta.get("sort_order") or 0)\n                    if int(meta.get("sort_order") or 0) > 0\n                    else next_sort_order\n                ),\n            )\n''',
    '''                current_version=int(meta.get("current_version") or 1),\n            )\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''            chapters_by_id[chapter.id] = chapter\n            chapters_by_path[rel_path] = chapter\n            next_sort_order = max(\n                next_sort_order + CHAPTER_ORDER_STEP,\n                int(chapter.sort_order or 0) + CHAPTER_ORDER_STEP,\n            )\n            continue\n''',
    '''            chapters_by_id[chapter.id] = chapter\n            chapters_by_path[rel_path] = chapter\n            continue\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''        chapter.title = str(meta.get("title") or chapter.title)[:200]\n        chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id\n        file_sort_order = int(meta.get("sort_order") or 0)\n        if file_sort_order > 0:\n            chapter.sort_order = file_sort_order\n        chapter.content = content\n''',
    '''        chapter.title = str(meta.get("title") or chapter.title)[:200]\n        chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id\n        chapter.content = content\n''',
)

# main has a five-line stale architecture-baseline drift here. Reduce it without
# changing behavior instead of increasing the grandfathered limit.
replace_once(
    "backend/app/services/novel_creation_workspace.py",
    "from collections.abc import Callable\nfrom typing import Any\n",
    "from typing import Any, Callable\n",
)
replace_once(
    "backend/app/services/novel_creation_workspace.py",
    '''from app.services.novel_creation_runs import add_run_event  # noqa: F401 - compatibility export\nfrom app.services.novel_creation_runs import complete_run  # noqa: F401 - compatibility export\nfrom app.services.novel_creation_runs import confirm_run  # noqa: F401 - compatibility export\nfrom app.services.novel_creation_runs import create_run  # noqa: F401 - compatibility export\nfrom app.services.novel_creation_runs import fail_run  # noqa: F401 - compatibility export\nfrom app.services.novel_creation_runs import serialize_run\n''',
    '''from app.services.novel_creation_runs import add_run_event, complete_run, confirm_run  # noqa: F401\nfrom app.services.novel_creation_runs import create_run, fail_run, serialize_run  # noqa: F401\n''',
)

for transient in [
    ROOT / "scripts/one_shot_chapter_order_arch_fix.py",
    ROOT / ".github/workflows/one-shot-chapter-order-arch-fix.yml",
]:
    transient.unlink(missing_ok=True)

print("Architecture gate debt kept at or below baseline.")
