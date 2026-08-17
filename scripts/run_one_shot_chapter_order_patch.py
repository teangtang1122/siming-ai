from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
patcher = root / "scripts/one_shot_chapter_order_patch.py"
source = patcher.read_text(encoding="utf-8")
old = '''    "        current_version=chapter.current_version or 1,\\n        outline_title=outline_title,\\n",\n    "        current_version=chapter.current_version or 1,\\n        sort_order=chapter.sort_order or 0,\\n        outline_title=outline_title,\\n",\n'''
new = '''    "        current_version=chapter.current_version or 1,\\n        outline_title=outline_node.title if outline_node else None,\\n",\n    "        current_version=chapter.current_version or 1,\\n        sort_order=chapter.sort_order or 0,\\n        outline_title=outline_node.title if outline_node else None,\\n",\n'''
if old not in source:
    raise RuntimeError("expected chapter_service patch snippet not found in patcher")
source = source.replace(old, new, 1)
exec(compile(source, str(patcher), "exec"), {"__name__": "__main__", "__file__": str(patcher)})
Path(__file__).unlink(missing_ok=True)
