from pathlib import Path

path = Path("backend/app/services/gateway_legacy_replication.py")
text = path.read_text(encoding="utf-8")
old = '''    raw_summary = values.pop("change_summary", None)
    change_summary = str(raw_summary or "").strip() or None
    if "role_type" in values:
'''
new = '''    raw_summary = values.pop("change_summary", None)
    change_summary = str(raw_summary or "").strip() or None
    if row is None and "role_type" not in values:
        values["role_type"] = normalize_character_role_type(None)
    if "role_type" in values:
'''
if text.count(old) != 1:
    raise RuntimeError(f"character default role anchor changed: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
