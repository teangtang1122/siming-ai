from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
anchor = '''def apply_domain_mutation(\n'''
helper = '''def _prepare_domain_mutation_values(\n    spec: RecordSpec,\n    row: Any | None,\n    payload: dict[str, Any] | None,\n) -> tuple[\n    dict[str, Any],\n    list[str] | None,\n    str | None,\n    list[tuple[str, str | None]] | None,\n    dict[str, Any] | None,\n]:\n    values = dict(payload or {})\n    values.pop("_record_type", None)\n    character_aliases: list[str] | None = None\n    character_change_summary: str | None = None\n    outline_links: list[tuple[str, str | None]] | None = None\n    governance_status_values: dict[str, Any] | None = None\n    if spec.model in {Foreshadowing, NarrativeDebt}:\n        governance_status_values = {\n            key: values.pop(key)\n            for key in STATUS_UPDATE_FIELDS\n            if key in values\n        }\n    if spec.model is Project:\n        values = _canonical_project_values(values)\n    elif spec.model is Character:\n        values, character_aliases, character_change_summary = _prepare_character_mutation_values(\n            values,\n            row,\n        )\n    elif spec.model is CharacterRelationship:\n        values = _canonical_character_relation_values(values)\n    elif spec.model is CharacterAIConfig:\n        values = _canonical_character_ai_config_values(values)\n        if row is not None:\n            values.setdefault("character_id", row.character_id)\n    elif spec.model is OutlineNode:\n        values, outline_links = _canonical_outline_values(values)\n    return (\n        values,\n        character_aliases,\n        character_change_summary,\n        outline_links,\n        governance_status_values,\n    )\n\n\n'''
if anchor not in text:
    raise RuntimeError("apply mutation anchor missing")
text = text.replace(anchor, helper + anchor, 1)
old = '''    values = dict(payload or {})\n    values.pop("_record_type", None)\n    character_aliases: list[str] | None = None\n    character_change_summary: str | None = None\n    outline_links: list[tuple[str, str | None]] | None = None\n    governance_status_values: dict[str, Any] | None = None\n    if spec.model in {Foreshadowing, NarrativeDebt}:\n        governance_status_values = {\n            key: values.pop(key)\n            for key in STATUS_UPDATE_FIELDS\n            if key in values\n        }\n    if spec.model is Project:\n        values = _canonical_project_values(values)\n    if spec.model is Character:\n        values, character_aliases, character_change_summary = _prepare_character_mutation_values(\n            values,\n            row,\n        )\n    if spec.model is CharacterRelationship:\n        values = _canonical_character_relation_values(values)\n    if spec.model is CharacterAIConfig:\n        values = _canonical_character_ai_config_values(values)\n        if row is not None:\n            values.setdefault("character_id", row.character_id)\n    if spec.model is OutlineNode:\n        values, outline_links = _canonical_outline_values(values)\n'''
new = '''    (\n        values,\n        character_aliases,\n        character_change_summary,\n        outline_links,\n        governance_status_values,\n    ) = _prepare_domain_mutation_values(spec, row, payload)\n'''
text = replace_once(text, old, new, "mutation preparation block")
path.write_text(text, encoding="utf-8")
