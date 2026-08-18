from pathlib import Path

path = Path("backend/app/services/external_agent/mcp_auto_config.py")
text = path.read_text(encoding="utf-8")
needle = "\n\n\ndef configure_cli_integration("
if needle not in text:
    raise RuntimeError("expected refactor boundary was not found")
path.write_text(
    text.replace(needle, "\n\ndef configure_cli_integration(", 1),
    encoding="utf-8",
    newline="\n",
)
print("Removed one refactor-only blank line from mcp_auto_config.py")
