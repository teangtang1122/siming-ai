from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


path = "backend/app/services/external_agent/mcp_auto_config.py"
replace_once(
    path,
    '    elif provider == "opencode_cli":\n        client = _configure_opencode(server)\n',
    '    elif provider == "opencode_cli":\n        client = _configure_opencode(server, cli_command=cli_command)\n',
)
replace_once(
    path,
    'def _configure_opencode(server: dict[str, Any]) -> dict[str, Any]:\n    opencode = _resolve_command(None, ["opencode.cmd", "opencode", "opencode.exe"])\n',
    'def _configure_opencode(\n    server: dict[str, Any],\n    *,\n    cli_command: str | None = None,\n) -> dict[str, Any]:\n    opencode = _resolve_command(cli_command, ["opencode.cmd", "opencode", "opencode.exe"])\n',
)
replace_once(
    path,
    '    if "opencode" in command_name:\n        return _configure_opencode(server)\n',
    '    if "opencode" in command_name:\n        return _configure_opencode(server, cli_command=cli_command)\n',
)

path = "backend/tests/test_mcp_auto_config.py"
text = Path(path).read_text(encoding="utf-8")
marker = "def test_opencode_configuration_accepts_managed_command_outside_path():"
if marker not in text:
    addition = r'''


def test_opencode_configuration_accepts_managed_command_outside_path():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        managed_command = root / "managed" / "opencode.exe"
        managed_command.parent.mkdir(parents=True)
        managed_command.write_bytes(b"managed-opencode")
        config_path = root / "config" / "opencode.json"
        server = {
            "command": "python",
            "args": ["-m", "app.mcp.server", "--permission-pack", "auto"],
        }
        with patch(
            "app.services.external_agent.mcp_auto_config._opencode_config_path",
            return_value=config_path,
        ), patch(
            "app.services.external_agent.mcp_auto_config.shutil.which",
            return_value=None,
        ):
            result = mcp_auto_config._configure_opencode(
                server,
                cli_command=str(managed_command),
            )

        assert result["status"] == "configured"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["mcp"]["siming"]["enabled"] is True
        assert saved["mcp"]["siming"]["command"][:2] == ["python", "-m"]
'''
    Path(path).write_text(text.rstrip() + addition + "\n", encoding="utf-8", newline="\n")

print("Managed OpenCode MCP command fix applied")
