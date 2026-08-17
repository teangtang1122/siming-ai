from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt")
text = path.read_text(encoding="utf-8")
old = "当前表单直接编辑 PC Character 字段；能力/别名保持数组结构，未展示的稳定写作 profile 会原样保留。"
new = "当前表单直接编辑 PC Character 字段；能力/别名保持数组结构，profile 保持 JSON 对象结构，与 PC Character 契约一致。"
if text.count(old) != 1:
    raise RuntimeError(f"character banner anchor changed: {text.count(old)} matches")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
