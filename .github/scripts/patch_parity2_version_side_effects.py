from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from app.core.exceptions import ValidationError\n",
    "from app.core.db_helpers import get_outline_node_or_404\nfrom app.core.exceptions import ValidationError\n",
    "outline validation import",
)
text = replace_once(
    text,
    "from app.services.chapter_service import chapter_to_detail\n",
    "from app.services.chapter_service import chapter_to_detail, create_snapshot\n",
    "chapter snapshot import",
)
text = replace_once(
    text,
    "from app.services.character_service import character_to_dict, dumps_list, sync_character_aliases\n",
    "from app.services.character_service import (\n    character_to_dict,\n    create_character_version,\n    dumps_list,\n    sync_character_aliases,\n)\n",
    "character version import",
)
text = replace_once(
    text,
    "from app.modules.story.infrastructure.chapter_evidence import SqlAlchemyChapterEvidenceReader\n",
    "from app.modules.story.infrastructure.chapter_evidence import SqlAlchemyChapterEvidenceReader\nfrom app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace\n",
    "chapter workspace import",
)
text = replace_once(
    text,
    "from app.modules.story.infrastructure.entities import (\n",
    "from app.services.narrative_governance import create_narrative_checkpoint\nfrom app.modules.story.infrastructure.entities import (\n",
    "checkpoint import",
)
old = '''    row = db.get(spec.model, entity_id)
    if operation == "delete":'''
new = '''    row = db.get(spec.model, entity_id)
    row_existed = row is not None
    if operation == "delete":'''
text = replace_once(text, old, new, "row existence tracking")
old = '''    character_aliases: list[str] | None = None
    outline_links: list[tuple[str, str | None]] | None = None
    governance_status_values: dict[str, Any] | None = None
'''
new = '''    character_aliases: list[str] | None = None
    character_change_summary: str | None = None
    outline_links: list[tuple[str, str | None]] | None = None
    governance_status_values: dict[str, Any] | None = None
'''
text = replace_once(text, old, new, "character summary state")
old = '''    if spec.model is Character:
        values, character_aliases = _canonical_character_values(values)
'''
new = '''    if spec.model is Character:
        raw_summary = values.pop("change_summary", None)
        character_change_summary = str(raw_summary or "").strip() or None
        values, character_aliases = _canonical_character_values(values)
'''
text = replace_once(text, old, new, "character summary extraction")
old = '''    if spec.model is OutlineNode and "parent_id" in values:
        ensure_no_cycle(db, project_id, entity_id, values.get("parent_id"))

    columns = {column.key: column for column in sa_inspect(spec.model).columns}
'''
new = '''    if spec.model is Chapter and row_existed:
        chapter_values = {
            key: value
            for key, value in values.items()
            if key in {"title", "outline_node_id", "content", "context_manifest_id"}
        }
        SqlAlchemyChapterWorkspace(db).save(project_id, entity_id, chapter_values)
        return

    if spec.model is Chapter and values.get("outline_node_id"):
        get_outline_node_or_404(db, project_id, values.get("outline_node_id"))
    if spec.model is OutlineNode and "parent_id" in values:
        ensure_no_cycle(db, project_id, entity_id, values.get("parent_id"))

    columns = {column.key: column for column in sa_inspect(spec.model).columns}
'''
text = replace_once(text, old, new, "chapter PC save semantics")
old = '''    if spec.model is Chapter and "content" in allowed:
        row.word_count = count_words(row.content or "")
        db.flush()
'''
new = '''    if spec.model is Chapter and "content" in allowed:
        row.word_count = count_words(row.content or "")
        db.flush()
    if spec.model is Chapter and not row_existed:
        db.add(create_snapshot(row, "manual_save"))
        create_narrative_checkpoint(
            db,
            project_id,
            chapter=row,
            label=f"{row.title} 创建",
            trigger_type="chapter_create",
        )
        db.flush()
'''
text = replace_once(text, old, new, "chapter create side effects")
old = '''    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()
'''
new = '''    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()
    if spec.model is Character and row_existed:
        create_character_version(
            db,
            row,
            character_change_summary or "手动更新角色档案",
        )
        db.flush()
'''
text = replace_once(text, old, new, "character version side effects")
path.write_text(text, encoding="utf-8")
