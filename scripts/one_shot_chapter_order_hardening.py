from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected 1 occurrence, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


def replace_n(path: str, old: str, new: str, expected: int) -> None:
    content = read(path)
    count = content.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} occurrences, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new))


def regex_replace(path: str, pattern: str, repl: str, expected: int) -> None:
    content = read(path)
    updated, count = re.subn(pattern, repl, content, flags=re.MULTILINE)
    if count != expected:
        raise RuntimeError(f"{path}: regex expected {expected}, found {count}: {pattern!r}")
    write(path, updated)


# Central append/order helpers. Missed constructors still get a safe tail sentinel
# from the model default rather than jumping to the beginning of a novel.
write(
    "backend/app/services/chapter_ordering.py",
    '''"""Canonical chapter reading-order helpers.\n\nChapter.sort_order is the authoritative narrative sequence. outline_node_id is\nplanning metadata only and must never be used to infer chapter order.\n"""\nfrom __future__ import annotations\n\nfrom sqlalchemy import func\nfrom sqlalchemy.orm import Session\n\nfrom ..database.models import Chapter\n\nCHAPTER_ORDER_STEP = 1000\nUNASSIGNED_CHAPTER_SORT_ORDER = 1_000_000_000\n\n\ndef next_chapter_sort_order(db: Session, project_id: str) -> int:\n    highest = (\n        db.query(func.max(Chapter.sort_order))\n        .filter(Chapter.project_id == project_id)\n        .scalar()\n        or 0\n    )\n    return int(highest) + CHAPTER_ORDER_STEP\n\n\ndef chapter_order_asc():\n    return (Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())\n\n\ndef chapter_order_desc():\n    return (Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())\n\n\n__all__ = [\n    "CHAPTER_ORDER_STEP",\n    "UNASSIGNED_CHAPTER_SORT_ORDER",\n    "chapter_order_asc",\n    "chapter_order_desc",\n    "next_chapter_sort_order",\n]\n''',
)

replace_once(
    "backend/app/modules/story/infrastructure/entities.py",
    "    sort_order = Column(Integer, nullable=False, default=0)\n",
    "    sort_order = Column(Integer, nullable=False, default=1_000_000_000)\n",
)
replace_once(
    "backend/alembic/versions/300a17_chapter_sort_order.py",
    '        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),\n',
    '        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1000000000"),\n',
)

# Canonical CRUD uses the shared append helper; reorder also rebuilds the readable
# project mirror so numbered chapter files follow the same order.
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "from ....services.chapter_service import (\n",
    "from ....services.chapter_ordering import next_chapter_sort_order\nfrom ....services.chapter_service import (\n",
)
old_method = '''    def _next_sort_order(self, project_id: str) -> int:\n        last = (\n            self._session.query(Chapter)\n            .filter(Chapter.project_id == project_id)\n            .order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())\n            .first()\n        )\n        return ((last.sort_order or 0) if last else 0) + 1000\n\n'''
replace_once("backend/app/modules/story/infrastructure/chapters.py", old_method, "")
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "            sort_order=self._next_sort_order(project_id),\n",
    "            sort_order=next_chapter_sort_order(self._session, project_id),\n",
)
replace_once(
    "backend/app/modules/story/infrastructure/chapters.py",
    "        return StoryMutation(data=self.list(project_id), sync_intents=[])\n",
    '''        return StoryMutation(\n            data=self.list(project_id),\n            sync_intents=[\n                ContentSyncIntent(\n                    project_id=project_id,\n                    target=ContentSyncTarget.PROJECT,\n                    source="chapter_reorder",\n                )\n            ],\n        )\n''',
)

# Gateway old-client creation shares the same append rule.
replace_once(
    "backend/app/services/gateway_legacy_replication.py",
    "from sqlalchemy import Date, DateTime, func\n",
    "from sqlalchemy import Date, DateTime\n",
)
replace_once(
    "backend/app/services/gateway_legacy_replication.py",
    "from app.core.exceptions import ValidationError\n",
    "from app.core.exceptions import ValidationError\nfrom app.services.chapter_ordering import next_chapter_sort_order\n",
)
old_gateway = '''    if spec.model is Chapter and row is None and "sort_order" not in allowed:\n        highest = (\n            db.query(func.max(Chapter.sort_order))\n            .filter(Chapter.project_id == project_id)\n            .scalar()\n            or 0\n        )\n        allowed["sort_order"] = int(highest) + 1000\n'''
replace_once(
    "backend/app/services/gateway_legacy_replication.py",
    old_gateway,
    '''    if spec.model is Chapter and row is None and "sort_order" not in allowed:\n        allowed["sort_order"] = next_chapter_sort_order(db, project_id)\n''',
)

# Workspace/AI chapter writer must append by canonical reading order.
replace_once(
    "backend/app/services/workspace/tools/chapters.py",
    "from ....services.chapter_service import (\n",
    "from ....services.chapter_ordering import next_chapter_sort_order\nfrom ....services.chapter_service import (\n",
)
replace_once(
    "backend/app/services/workspace/tools/chapters.py",
    "            current_version=1,\n            context_manifest_id=context_manifest_id,\n",
    "            current_version=1,\n            sort_order=next_chapter_sort_order(db, project_id),\n            context_manifest_id=context_manifest_id,\n",
)
replace_once(
    "backend/app/services/workspace/tools/chapters.py",
    "    return query.order_by(Chapter.created_at.asc()).all()\n",
    "    return query.order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc()).all()\n",
)

# Assistant persistence adapter is a generic escape hatch; make its default safe.
replace_once(
    "backend/app/services/persistence/assistant_workspace.py",
    "from app.database.models import (\n",
    "from app.database.models import (\n",
)
replace_once(
    "backend/app/services/persistence/assistant_workspace.py",
    ")\n\n\nclass SqlAlchemyAssistantWorkspace:\n",
    ")\nfrom app.services.chapter_ordering import next_chapter_sort_order\n\n\nclass SqlAlchemyAssistantWorkspace:\n",
)
replace_once(
    "backend/app/services/persistence/assistant_workspace.py",
    '''    def create_chapter(self, **values: Any):\n        chapter = Chapter(**values)\n''',
    '''    def create_chapter(self, **values: Any):\n        project_id = str(values.get("project_id") or "").strip()\n        if project_id and values.get("sort_order") is None:\n            values["sort_order"] = next_chapter_sort_order(self.db, project_id)\n        chapter = Chapter(**values)\n''',
)

# Import preserves split order and appends the imported block after existing正文.
replace_once(
    "backend/app/services/import_service.py",
    "from ..database.models import Chapter\n",
    "from ..database.models import Chapter\nfrom .chapter_ordering import CHAPTER_ORDER_STEP, next_chapter_sort_order\n",
)
replace_once(
    "backend/app/services/import_service.py",
    '''    created_chapters: list[Chapter] = []\n    if splits:\n''',
    '''    created_chapters: list[Chapter] = []\n    next_sort_order = next_chapter_sort_order(db, project_id)\n    if splits:\n''',
)
replace_n(
    "backend/app/services/import_service.py",
    "                current_version=1,\n",
    "                current_version=1,\n                sort_order=next_sort_order + len(created_chapters) * CHAPTER_ORDER_STEP,\n",
    1,
)
replace_once(
    "backend/app/services/import_service.py",
    "            current_version=1,\n        )\n        db.add(chapter)\n        created_chapters.append(chapter)\n",
    "            current_version=1,\n            sort_order=next_sort_order,\n        )\n        db.add(chapter)\n        created_chapters.append(chapter)\n",
)

# Legacy assistant helper has two creation paths (final and placeholder).
replace_once(
    "backend/app/services/assistant_chapter.py",
    "from .workspace import execute_workspace_action\n",
    "from .chapter_ordering import next_chapter_sort_order\nfrom .workspace import execute_workspace_action\n",
)
replace_n(
    "backend/app/services/assistant_chapter.py",
    "        current_version=1,\n",
    "        current_version=1,\n        sort_order=next_chapter_sort_order(db, project_id),\n",
    2,
)

# Human-readable project mirror carries and reflects canonical order. Full project
# rebuilds rename numbered chapter files after a drag/reorder.
replace_once(
    "backend/app/services/content_store.py",
    "from .chapter_service import create_snapshot, ensure_current_snapshot\n",
    "from .chapter_ordering import CHAPTER_ORDER_STEP, next_chapter_sort_order\nfrom .chapter_service import create_snapshot, ensure_current_snapshot\n",
)
replace_once(
    "backend/app/services/content_store.py",
    '        "current_version": chapter.current_version or 1,\n',
    '        "current_version": chapter.current_version or 1,\n        "sort_order": chapter.sort_order,\n',
)
old_sync = '''def sync_chapter_to_file(db: Session, project: Project, chapter: Chapter, index: int = 0) -> None:\n    folder = ensure_project_folder(db, project)\n    old_rel = getattr(chapter, "content_file_path", None)\n    path = folder / old_rel if old_rel else _chapter_path(folder, chapter, index)\n    if old_rel and not path.exists():\n        path = _chapter_path(folder, chapter, index)\n    digest = _write_text(path, chapter_markdown(chapter))\n    chapter.content_file_path = _rel(path, folder)\n    chapter.content_hash = digest\n    invalidate_project(project.id)\n'''
new_sync = '''def sync_chapter_to_file(db: Session, project: Project, chapter: Chapter, index: int = 0) -> None:\n    folder = ensure_project_folder(db, project)\n    old_rel = getattr(chapter, "content_file_path", None)\n    old_path = (folder / old_rel) if old_rel else None\n    if index:\n        path = _chapter_path(folder, chapter, index)\n    else:\n        path = old_path if old_path and old_path.exists() else _chapter_path(folder, chapter, index)\n    digest = _write_text(path, chapter_markdown(chapter))\n    if old_path and old_path.resolve() != path.resolve() and old_path.exists():\n        old_path.unlink()\n    chapter.content_file_path = _rel(path, folder)\n    chapter.content_hash = digest\n    invalidate_project(project.id)\n'''
replace_once("backend/app/services/content_store.py", old_sync, new_sync)
replace_once(
    "backend/app/services/content_store.py",
    "        .order_by(Chapter.created_at.asc())\n",
    "        .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())\n",
)
replace_once(
    "backend/app/services/content_store.py",
    '''    chapters_by_path = {\n        str(chapter.content_file_path or ""): chapter\n        for chapter in chapters_by_id.values()\n        if chapter.content_file_path\n    }\n    for path in sorted((folder / "chapters").glob("*.md")):\n''',
    '''    chapters_by_path = {\n        str(chapter.content_file_path or ""): chapter\n        for chapter in chapters_by_id.values()\n        if chapter.content_file_path\n    }\n    next_sort_order = next_chapter_sort_order(db, project.id)\n    for path in sorted((folder / "chapters").glob("*.md")):\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''                current_version=int(meta.get("current_version") or 1),\n            )\n''',
    '''                current_version=int(meta.get("current_version") or 1),\n                sort_order=(\n                    int(meta.get("sort_order") or 0)\n                    if int(meta.get("sort_order") or 0) > 0\n                    else next_sort_order\n                ),\n            )\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''            chapters_by_id[chapter.id] = chapter\n            chapters_by_path[rel_path] = chapter\n            continue\n''',
    '''            chapters_by_id[chapter.id] = chapter\n            chapters_by_path[rel_path] = chapter\n            next_sort_order = max(\n                next_sort_order + CHAPTER_ORDER_STEP,\n                int(chapter.sort_order or 0) + CHAPTER_ORDER_STEP,\n            )\n            continue\n''',
)
replace_once(
    "backend/app/services/content_store.py",
    '''        chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id\n        chapter.content = content\n''',
    '''        chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id\n        file_sort_order = int(meta.get("sort_order") or 0)\n        if file_sort_order > 0:\n            chapter.sort_order = file_sort_order\n        chapter.content = content\n''',
)

# Cataloging is chapter-sequence-sensitive: recent summaries must follow the
# reading order, never the planning tree.
replace_once(
    "backend/app/services/cataloging/context.py",
    "from ...services.outline_service import load_outline_nodes, outline_sort_context\n",
    "",
)
old_catalog_order = '''def ordered_chapters(db: Session, project_id: str, chapter_ids: list[str] | None = None) -> list[Chapter]:\n    outline_context = outline_sort_context(load_outline_nodes(db, project_id))\n    query = db.query(Chapter).filter(Chapter.project_id == project_id)\n    chapters = query.all()\n    by_id = {chapter.id: chapter for chapter in chapters}\n    if chapter_ids:\n        return [by_id[item] for item in chapter_ids if item in by_id]\n\n    def sort_key(chapter: Chapter):\n        outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)\n        if outline_key is None:\n            return (1, (999999,), chapter.created_at)\n        return (0, outline_key, chapter.created_at)\n\n    return sorted(chapters, key=sort_key)\n'''
new_catalog_order = '''def ordered_chapters(db: Session, project_id: str, chapter_ids: list[str] | None = None) -> list[Chapter]:\n    query = db.query(Chapter).filter(Chapter.project_id == project_id)\n    chapters = query.order_by(\n        Chapter.sort_order.asc(),\n        Chapter.created_at.asc(),\n        Chapter.id.asc(),\n    ).all()\n    by_id = {chapter.id: chapter for chapter in chapters}\n    if chapter_ids:\n        return [by_id[item] for item in chapter_ids if item in by_id]\n    return chapters\n'''
replace_once("backend/app/services/cataloging/context.py", old_catalog_order, new_catalog_order)

# Narrative-order consumers. Search/revision-time queries are deliberately not
# changed; only readers that interpret chapter sequence are migrated.
asc_targets = {
    "backend/app/modules/story/infrastructure/deconstruct.py": 1,
    "backend/app/services/workspace/tools/story_granularity.py": 2,
    "backend/app/services/workspace/tools/plot.py": 1,
    "backend/app/services/workspace/tools/project_status.py": 1,
    "backend/app/services/workspace/tools/deconstruct.py": 1,
    "backend/app/services/deconstruct/pipeline.py": 2,
    "backend/app/services/deconstruct/import_helpers.py": 1,
    "backend/app/services/local_runtime/datasets.py": 1,
}
for path, expected in asc_targets.items():
    replace_n(
        path,
        ".order_by(Chapter.created_at.asc())",
        ".order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())",
        expected,
    )

plain_asc_targets = {
    "backend/app/services/workspace/tools/external_cataloging.py": 2,
    "backend/app/mcp/resources.py": 1,
}
for path, expected in plain_asc_targets.items():
    replace_n(
        path,
        ".order_by(Chapter.created_at)",
        ".order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())",
        expected,
    )

desc_targets = {
    "backend/app/services/workspace/tools/context_preview.py": 1,
    "backend/app/services/workspace/tools/external_writing.py": 1,
    "backend/app/services/context_orchestrator.py": 1,
    "backend/app/services/context_builders.py": 1,
    "backend/app/mcp/prompts.py": 1,
}
for path, expected in desc_targets.items():
    replace_n(
        path,
        ".order_by(Chapter.created_at.desc())",
        ".order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())",
        expected,
    )

replace_n(
    "backend/app/services/narrative_governance.py",
    ".order_by(Chapter.created_at, Chapter.id)",
    ".order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())",
    2,
)

# Imported chapter regression asserts persisted split order explicitly.
replace_once(
    "backend/tests/test_importer.py",
    "            stored = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.title.asc()).all()\n",
    '''            stored = (\n                db.query(Chapter)\n                .filter(Chapter.project_id == project_id)\n                .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())\n                .all()\n            )\n''',
)
replace_once(
    "backend/tests/test_importer.py",
    "            self.assertEqual([chapter.word_count for chapter in stored], expected_counts)\n",
    "            self.assertEqual([chapter.word_count for chapter in stored], expected_counts)\n            self.assertEqual([chapter.sort_order for chapter in stored], [1000, 2000])\n",
)

# Migration readability/formatter cleanup.
replace_once(
    "backend/alembic/versions/300a17_chapter_sort_order.py",
    '''        def old_pc_sort_key(row):\n            outline_key = outline_keys.get(str(row["outline_node_id"])) if row["outline_node_id"] else None\n''',
    '''        def old_pc_sort_key(row):\n            outline_key = (\n                outline_keys.get(str(row["outline_node_id"]))\n                if row["outline_node_id"]\n                else None\n            )\n''',
)

# Temporary audit machinery must never land in the PR diff.
for transient in [
    ROOT / ".github/workflows/one-shot-chapter-order-audit.yml",
    ROOT / ".github/workflows/one-shot-chapter-order-hardening.yml",
    ROOT / "scripts/one_shot_chapter_order_hardening.py",
]:
    transient.unlink(missing_ok=True)

print("Chapter ordering hardening applied.")
