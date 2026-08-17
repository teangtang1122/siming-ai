from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
old = '''from app.modules.continuity.infrastructure.models import (
'''
new = '''from app.modules.continuity.infrastructure.governance import (
    STATUS_UPDATE_FIELDS,
    apply_governance_status_update,
)
from app.modules.continuity.infrastructure.models import (
'''
text = replace_once(text, old, new, "governance lifecycle import")
old = '''from app.modules.story.infrastructure.entities import (
'''
new = '''from app.modules.story.infrastructure.chapter_evidence import SqlAlchemyChapterEvidenceReader
from app.modules.story.infrastructure.entities import (
'''
text = replace_once(text, old, new, "chapter evidence import")
old = '''    values = dict(payload or {})
    values.pop("_record_type", None)
    character_aliases: list[str] | None = None
    outline_links: list[tuple[str, str | None]] | None = None
'''
new = '''    values = dict(payload or {})
    values.pop("_record_type", None)
    character_aliases: list[str] | None = None
    outline_links: list[tuple[str, str | None]] | None = None
    governance_status_values: dict[str, Any] | None = None
    if spec.model in {Foreshadowing, NarrativeDebt}:
        governance_status_values = {
            key: values.pop(key)
            for key in STATUS_UPDATE_FIELDS
            if key in values
        }
'''
text = replace_once(text, old, new, "extract governance lifecycle values")
old = '''    db.flush()
    if spec.model is Chapter and "content" in allowed:
'''
new = '''    db.flush()
    if governance_status_values:
        item_type = "foreshadowing" if spec.model is Foreshadowing else "narrative_debt"
        updated = apply_governance_status_update(
            db,
            SqlAlchemyChapterEvidenceReader(),
            project_id,
            item_type,
            row.id,
            governance_status_values,
            commit=False,
        )
        if updated is None:
            raise ValidationError("治理项不存在")
    if spec.model is Chapter and "content" in allowed:
'''
text = replace_once(text, old, new, "apply shared governance lifecycle")
path.write_text(text, encoding="utf-8")
