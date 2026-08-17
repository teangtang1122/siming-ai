from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
old = '''from app.services.character_service import (
    character_to_dict,
    create_character_version,
    dumps_list,
    sync_character_aliases,
)
'''
new = old + '''from app.services.character_role_types import (
    append_character_role_description,
    normalize_character_role_type,
)
'''
text = replace_once(text, old, new, "character role imports")
old = '''    if spec.model is Character:
        raw_summary = values.pop("change_summary", None)
        character_change_summary = str(raw_summary or "").strip() or None
        values, character_aliases = _canonical_character_values(values)
'''
new = '''    if spec.model is Character:
        raw_summary = values.pop("change_summary", None)
        character_change_summary = str(raw_summary or "").strip() or None
        if "role_type" in values:
            raw_role_type = values["role_type"]
            values["background"] = append_character_role_description(
                values.get("background", row.background if row is not None else None),
                raw_role_type,
            )
            values["role_type"] = normalize_character_role_type(
                raw_role_type,
                default=(row.role_type or "other") if row is not None else "other",
            )
        values, character_aliases = _canonical_character_values(values)
'''
text = replace_once(text, old, new, "character role normalization")
path.write_text(text, encoding="utf-8")
