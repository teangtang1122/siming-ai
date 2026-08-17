from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "backend/tests/test_database_bootstrap.py"
text = path.read_text(encoding="utf-8")
old_revision = '"300a16_character_role_type_enum"'
new_revision = '"300a17_chapter_sort_order"'
count = text.count(old_revision)
if count != 6:
    raise RuntimeError(f"expected 6 head-revision assertions, found {count}")
text = text.replace(old_revision, new_revision)
old = '''            assert {\n                "projects",\n                "chapters",\n                "operation_runs",\n                "content_sync_jobs",\n                "gateway_devices",\n                "sync_changes",\n            } <= tables\n'''
new = '''            assert {\n                "projects",\n                "chapters",\n                "operation_runs",\n                "content_sync_jobs",\n                "gateway_devices",\n                "sync_changes",\n            } <= tables\n            assert "sort_order" in {\n                column["name"] for column in inspect(engine).get_columns("chapters")\n            }\n'''
if old not in text:
    raise RuntimeError("fresh database table assertion not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

for transient in [
    root / "scripts/one_shot_chapter_order_bootstrap_tests.py",
    root / ".github/workflows/one-shot-chapter-order-bootstrap-tests.yml",
]:
    transient.unlink(missing_ok=True)

print("Bootstrap migration expectations updated.")
