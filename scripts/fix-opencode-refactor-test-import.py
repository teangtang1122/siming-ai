from pathlib import Path

path = Path("backend/tests/test_local_cli_cataloging_agent.py")
text = path.read_text(encoding="utf-8")
old = '''from app.services.cataloging.local_cli_agent import (\n    _MAX_NO_SAVE_ATTEMPTS,\n    _build_cataloging_cli_launch,\n'''
new = '''from app.services.cataloging.local_cli_agent import (\n    _build_cataloging_cli_launch,\n'''
if text.count(old) != 1:
    raise RuntimeError("local_cli_agent import block did not match")
text = text.replace(old, new)
anchor = '''from app.services.cataloging.orchestrator import create_cataloging_job\n'''
replacement = '''from app.services.cataloging.local_cli_result import _MAX_NO_SAVE_ATTEMPTS\nfrom app.services.cataloging.orchestrator import create_cataloging_job\n'''
if text.count(anchor) != 1:
    raise RuntimeError("orchestrator import anchor did not match")
path.write_text(text.replace(anchor, replacement), encoding="utf-8", newline="\n")
print("Updated cataloging test constant import")
