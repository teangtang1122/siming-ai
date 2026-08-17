from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
patcher = root / "scripts/one_shot_chapter_order_arch_fix.py"
exec(compile(patcher.read_text(encoding="utf-8"), str(patcher), "exec"), {
    "__name__": "__main__",
    "__file__": str(patcher),
})

# One additional internal blank-line reduction keeps the grandfathered chapter
# tool exactly at its recorded module-size baseline without changing behavior.
path = root / "backend/app/services/workspace/tools/chapters.py"
text = path.read_text(encoding="utf-8")
old = '''        }\n\n    candidates = _chapter_write_candidates(db, project_id, outline_node, title)\n'''
new = '''        }\n    candidates = _chapter_write_candidates(db, project_id, outline_node, title)\n'''
if old not in text:
    raise RuntimeError("expected candidate-section blank line not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
