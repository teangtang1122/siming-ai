from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
anchor = '''def apply_domain_mutation(
'''
helper = '''def _prepare_character_mutation_values(
    values: dict[str, Any],
    row: Character | None,
) -> tuple[dict[str, Any], list[str] | None, str | None]:
    raw_summary = values.pop("change_summary", None)
    change_summary = str(raw_summary or "").strip() or None
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
    values, aliases = _canonical_character_values(values)
    return values, aliases, change_summary


''' + anchor
text = replace_once(text, anchor, helper, "character mutation helper insertion")
old = '''    if spec.model is Character:
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
new = '''    if spec.model is Character:
        values, character_aliases, character_change_summary = _prepare_character_mutation_values(
            values,
            row,
        )
'''
text = replace_once(text, old, new, "character mutation helper call")
path.write_text(text, encoding="utf-8")
