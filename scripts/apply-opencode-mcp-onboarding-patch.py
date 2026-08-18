from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"pattern is not unique in {path}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def append_once(path: str, marker: str, addition: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if marker in text:
        return
    file_path.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8", newline="\n")


# 1. MCP configuration + real preflight.
path = "backend/app/services/external_agent/mcp_auto_config.py"
replace_once(
    path,
    'MCP_SERVER_NAME = "siming"\nLEGACY_MCP_SERVER_NAMES = ("moshu",)\n',
    '''MCP_SERVER_NAME = "siming"\nLEGACY_MCP_SERVER_NAMES = ("moshu",)\nCATALOGING_MCP_TOOL_NAMES = (\n    "report_agent_plan",\n    "report_agent_progress",\n    "report_context_selected",\n    "get_next_external_cataloging_chapter",\n    "save_external_cataloging_facts",\n    "save_external_cataloging_candidates",\n    "verify_external_cataloging_progress",\n    "get_cataloging_control_state",\n    "list_cataloging_facts",\n    "apply_pending_cataloging",\n)\n''',
)
insert_marker = '''def configure_cli_integration(\n    provider: str,\n    *,\n    cli_command: str | None = None,\n    permission_pack: str = DEFAULT_PERMISSION_PACK,\n) -> dict[str, Any]:\n'''
preflight_code = r'''
def _cli_argv(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def _probe_siming_mcp_tools(
    *,
    permission_pack: str,
    timeout: int = 20,
) -> tuple[set[str], str]:
    """Start Siming MCP directly and verify the exact permission-pack tool surface."""

    server = resolve_siming_mcp_server(permission_pack=permission_pack)
    requests = "\n".join([
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "siming-preflight", "version": "1"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        "",
    ])
    env = os.environ.copy()
    if permission_pack == "cataloging_worker":
        env["SIMING_MANAGED_AGENT_KIND"] = "cataloging"
    try:
        completed = subprocess.run(
            _cli_argv(str(server["command"]), [str(item) for item in server.get("args") or []]),
            input=requests,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=server.get("cwd") or str(app_home()),
            env=env,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        return set(), f"Siming MCP 启动检查失败：{exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "MCP process exited").strip()[-800:]
        return set(), f"Siming MCP 启动失败：{detail}"
    for raw_line in completed.stdout.splitlines():
        try:
            payload = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("id") != 2:
            continue
        tools = ((payload.get("result") or {}).get("tools") or [])
        names = {
            str(item.get("name") or "").strip()
            for item in tools
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        return names, ""
    detail = (completed.stderr or completed.stdout or "tools/list did not return a result").strip()[-800:]
    return set(), f"Siming MCP 未返回工具列表：{detail}"


def preflight_cli_integration(
    provider: str,
    *,
    cli_command: str | None = None,
    permission_pack: str = "cataloging_worker",
    required_tools: tuple[str, ...] | list[str] | set[str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Verify the client connection and Siming MCP tool surface without model calls."""

    provider = str(provider or "").strip()
    required = set(required_tools or CATALOGING_MCP_TOOL_NAMES)
    if provider != "opencode_cli":
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": "当前仅对 OpenCode 建档执行自动 MCP 启动检查",
        }
    command = _resolve_command(cli_command, CLI_INTEGRATION_COMMANDS["opencode_cli"])
    if not command:
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": "没有找到可运行的 OpenCode，无法检查 Siming MCP",
        }

    env = os.environ.copy()
    if permission_pack == "cataloging_worker":
        # The persistent OpenCode entry uses permission_pack=auto. Managed
        # cataloging turns narrow that dynamically to cataloging_worker.
        env["SIMING_MANAGED_AGENT_KIND"] = "cataloging"
    try:
        completed = subprocess.run(
            _cli_argv(command, ["mcp", "list"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(app_home()),
            env=env,
            **hidden_subprocess_kwargs(),
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    except Exception as exc:
        return {
            "provider": provider,
            "ready": False,
            "configured": False,
            "connected": False,
            "tool_surface_ready": False,
            "missing_tools": sorted(required),
            "detail": f"OpenCode MCP 连接检查失败：{exc}",
        }

    siming_lines = [line.strip() for line in output.splitlines() if MCP_SERVER_NAME in line.lower()]
    configured = bool(siming_lines)
    negative_markers = ("failed", "error", "disconnected", "disabled", "offline", "unavailable")
    connection_failed = any(
        any(marker in line.lower() for marker in negative_markers)
        for line in siming_lines
    )
    connected = completed.returncode == 0 and configured and not connection_failed
    tool_names, tool_error = _probe_siming_mcp_tools(
        permission_pack=permission_pack,
        timeout=timeout,
    )
    missing = sorted(required - tool_names)
    tool_surface_ready = not tool_error and not missing
    ready = connected and tool_surface_ready

    if not configured:
        detail = "OpenCode 尚未配置 Siming MCP；请先在快速开始或系统设置中授权配置"
    elif not connected:
        detail = "OpenCode 已配置 Siming MCP，但连接状态异常；请重新配置后再试"
    elif tool_error:
        detail = tool_error
    elif missing:
        detail = "Siming MCP 已连接，但缺少建档工具：" + ", ".join(missing)
    else:
        detail = "OpenCode 与 Siming MCP 已连接，建档写入工具可用"

    return {
        "provider": provider,
        "ready": ready,
        "configured": configured,
        "connected": connected,
        "tool_surface_ready": tool_surface_ready,
        "missing_tools": missing,
        "available_tools": sorted(tool_names & required),
        "detail": detail,
        "mcp_list_output": output[-1000:],
    }


'''
replace_once(path, insert_marker, preflight_code + insert_marker)

# 2. Quick Start: surface consent, configure, and verify MCP.
path = "backend/app/routers/getting_started.py"
replace_once(
    path,
    '''from ..services.opencode_onboarding import (\n''',
    '''from ..services.external_agent.mcp_auto_config import (\n    configure_cli_integration,\n    preflight_cli_integration,\n    scan_cli_integrations,\n)\nfrom ..services.opencode_onboarding import (\n''',
)
replace_once(
    path,
    '''    activation_job: OpenCodeActivationStatus | None = None\n    official_links: dict[str, str] = Field(default_factory=dict)\n''',
    '''    activation_job: OpenCodeActivationStatus | None = None\n    opencode_mcp_configured: bool = False\n    official_links: dict[str, str] = Field(default_factory=dict)\n''',
)
replace_once(
    path,
    '''def _getting_started_summary(db: Session) -> dict:\n    state = get_getting_started_configuration().state(db)\n    has_usable_model = bool(\n''',
    '''def _getting_started_summary(db: Session) -> dict:\n    state = get_getting_started_configuration().state(db)\n    opencode_mcp_configured = False\n    if state.opencode_command:\n        try:\n            scan = scan_cli_integrations()\n            opencode_mcp_configured = any(\n                item.get("provider") == "opencode_cli" and bool(item.get("configured"))\n                for item in scan.get("clients") or []\n            )\n        except Exception:\n            opencode_mcp_configured = False\n    has_usable_model = bool(\n''',
)
replace_once(
    path,
    '''        "activation_job": None if has_usable_model else get_latest_opencode_activation_job(db),\n        "official_links": {\n''',
    '''        "activation_job": None if has_usable_model else get_latest_opencode_activation_job(db),\n        "opencode_mcp_configured": opencode_mcp_configured,\n        "official_links": {\n''',
)
endpoint_marker = '''@router.get("/config/getting-started/opencode/jobs/{job_id}")\ndef get_activation_status(job_id: str):\n'''
endpoint_code = r'''
@router.post("/config/getting-started/opencode/mcp/configure")
def configure_getting_started_opencode_mcp(db: Session = Depends(get_db)):
    """Explicitly configure and verify Siming MCP after OpenCode is usable."""

    state = get_getting_started_configuration().state(db)
    command = resolve_opencode_command(state.opencode_command)
    if not command:
        raise ValidationError("还没有可运行的 OpenCode，请先完成快速开始")
    configured = configure_cli_integration(
        "opencode_cli",
        cli_command=command,
        permission_pack="auto",
    )
    preflight = preflight_cli_integration(
        "opencode_cli",
        cli_command=command,
        permission_pack="cataloging_worker",
    )
    return ApiResponse.success(
        data={
            **configured,
            "ready": bool(preflight.get("ready")),
            "preflight": preflight,
        },
        message=(
            "OpenCode 与 Siming MCP 已配置并验证"
            if preflight.get("ready")
            else preflight.get("detail") or configured.get("detail") or "MCP 配置未完成"
        ),
    )


'''
replace_once(path, endpoint_marker, endpoint_code + endpoint_marker)

# 3. Quick Start frontend consent screen.
path = "frontend/src/pages/GettingStartedPage.tsx"
replace_once(
    path,
    '''  activation_job?: ActivationJob | null\n  official_links?: { model_docs?: string }\n}\n\ninterface ApiEnvelope<T> {\n''',
    '''  activation_job?: ActivationJob | null\n  opencode_mcp_configured?: boolean\n  official_links?: { model_docs?: string }\n}\n\ninterface McpSetupResult {\n  ready: boolean\n  detail?: string\n  preflight?: { ready?: boolean; detail?: string; missing_tools?: string[] }\n}\n\ninterface ApiEnvelope<T> {\n''',
)
replace_once(
    path,
    '''  const [setupError, setSetupError] = useState('')\n  const [authCredential, setAuthCredential] = useState('')\n  const downloadRate = useDownloadRate({\n''',
    '''  const [setupError, setSetupError] = useState('')\n  const [authCredential, setAuthCredential] = useState('')\n  const [mcpSetupRunning, setMcpSetupRunning] = useState(false)\n  const [mcpSetupError, setMcpSetupError] = useState('')\n  const [mcpConfigured, setMcpConfigured] = useState(false)\n  const [mcpDeferred, setMcpDeferred] = useState(\n    () => localStorage.getItem('siming_getting_started_mcp_deferred') === '1',\n  )\n  const downloadRate = useDownloadRate({\n''',
)
replace_once(
    path,
    '''  useEffect(() => {\n    if (!status) return\n    if (status.has_usable_models) {\n''',
    '''  useEffect(() => {\n    if (status?.opencode_mcp_configured) {\n      setMcpConfigured(true)\n      setMcpDeferred(false)\n      localStorage.removeItem('siming_getting_started_mcp_deferred')\n    }\n  }, [status?.opencode_mcp_configured])\n\n  useEffect(() => {\n    if (!status) return\n    if (status.has_usable_models) {\n''',
)
configure_callback_marker = '''  const currentStep = useMemo(() => {\n'''
configure_callback = r'''  const configureMcp = async () => {
    setMcpSetupRunning(true)
    setMcpSetupError('')
    try {
      const response = await apiClient.post<ApiEnvelope<McpSetupResult>>(
        '/config/getting-started/opencode/mcp/configure',
      )
      const result = response.data.data
      if (!result.ready) {
        setMcpSetupError(result.preflight?.detail || result.detail || 'MCP 配置检查未通过')
        return
      }
      setMcpConfigured(true)
      setMcpDeferred(false)
      localStorage.removeItem('siming_getting_started_mcp_deferred')
      message.success('OpenCode 与 Siming MCP 已配置并验证')
      await fetchStatus(false)
    } catch (error) {
      setMcpSetupError(errorText(error))
    } finally {
      setMcpSetupRunning(false)
    }
  }

  const deferMcpSetup = () => {
    localStorage.setItem('siming_getting_started_mcp_deferred', '1')
    setMcpDeferred(true)
  }

'''
replace_once(path, configure_callback_marker, configure_callback + configure_callback_marker)
replace_once(
    path,
    '''  if (ready) return <FirstIdea modelReady model={activeModel} />\n\n  const running = Boolean(job && ['pending', 'running'].includes(job.status))\n''',
    '''  const shouldOfferMcp = Boolean(\n    ready\n      && activeModel?.startsWith('opencode_cli:')\n      && !status.opencode_mcp_configured\n      && !mcpConfigured\n      && !mcpDeferred,\n  )\n  if (shouldOfferMcp) {\n    return (\n      <div className="getting-started-panel">\n        <div className="getting-started-layout">\n          <section className="getting-started-work" aria-live="polite">\n            <CheckCircleOutlined className="getting-started-ready-icon" />\n            <Title level={3}>OpenCode 已可用，再完成一步即可启用完整 Agent</Title>\n            <Paragraph>\n              配置 Siming MCP 后，OpenCode 才能在作品建档等任务中把结构化结果正式写回司命。\n              司命只为托管建档回合开放读取作品镜像和专用建档工具，不会给 OpenCode 任意文件写入或命令执行权限。\n            </Paragraph>\n            <Alert\n              type="info"\n              showIcon\n              message="推荐完成配置"\n              description="司命会先写入 OpenCode 的 siming MCP 配置，再实际检查 MCP 连接和建档工具列表。你也可以暂时跳过，之后在系统设置中补配。"\n            />\n            {mcpSetupError && <Alert type="error" showIcon message="MCP 配置未完成" description={mcpSetupError} />}\n            <Space wrap>\n              <Button type="primary" loading={mcpSetupRunning} onClick={() => void configureMcp()}>\n                推荐：配置并验证 MCP\n              </Button>\n              <Button disabled={mcpSetupRunning} onClick={deferMcpSetup}>暂时跳过</Button>\n            </Space>\n          </section>\n        </div>\n      </div>\n    )\n  }\n  if (ready) return <FirstIdea modelReady model={activeModel} />\n\n  const running = Boolean(job && ['pending', 'running'].includes(job.status))\n''',
)

# 4. Cataloging: mandatory preflight, narrow per-process OpenCode permissions,
#    and clearer no-save diagnostics.
path = "backend/app/services/cataloging/local_cli_agent.py"
replace_once(
    path,
    '''from app.services.external_agent.run_service import add_event, create_run, update_run_status\n''',
    '''from app.services.external_agent.mcp_auto_config import (\n    CATALOGING_MCP_TOOL_NAMES,\n    preflight_cli_integration,\n)\nfrom app.services.external_agent.run_service import add_event, create_run, update_run_status\n''',
)
replace_once(
    path,
    '''def _latest_agent_event_at(agent_run_id: str) -> datetime | None:\n''',
    '''def _opencode_cataloging_permission_env() -> str:\n    permission: dict[str, Any] = {\n        "*": "deny",\n        "read": {\n            "*": "allow",\n            "*.env": "deny",\n            "*.env.*": "deny",\n            "*.env.example": "allow",\n        },\n        "glob": "allow",\n        "grep": "allow",\n        "edit": "deny",\n        "bash": "deny",\n        "question": "deny",\n        "task": "deny",\n        "skill": "deny",\n        "lsp": "deny",\n        "webfetch": "deny",\n        "websearch": "deny",\n        "external_directory": "deny",\n        "doom_loop": "allow",\n    }\n    for tool_name in CATALOGING_MCP_TOOL_NAMES:\n        permission[f"siming_{tool_name}"] = "allow"\n    return json.dumps(permission, ensure_ascii=False, separators=(",", ":"))\n\n\ndef _agent_tool_event_count(agent_run_id: str) -> int:\n    db = SessionLocal()\n    try:\n        return (\n            db.query(AgentRunEvent.id)\n            .filter(\n                AgentRunEvent.run_id == agent_run_id,\n                AgentRunEvent.event_type == "tool_start",\n            )\n            .count()\n        )\n    finally:\n        db.close()\n\n\ndef _latest_agent_event_at(agent_run_id: str) -> datetime | None:\n''',
)
replace_once(
    path,
    '''    provider = config.provider\n    run = _active_agent_run(db, job, provider)\n''',
    '''    provider = config.provider\n    mcp_preflight = None\n    if provider == "opencode_cli":\n        mcp_preflight = preflight_cli_integration(\n            provider,\n            cli_command=config.cli_command,\n            permission_pack="cataloging_worker",\n        )\n        if not mcp_preflight.get("ready"):\n            raise RuntimeError(\n                "OpenCode 无法开始 MCP 建档："\n                + str(mcp_preflight.get("detail") or "MCP 启动检查未通过")\n            )\n    run = _active_agent_run(db, job, provider)\n''',
)
replace_once(
    path,
    '''        "provider": provider,\n        "job_id": job.id,\n    }\n''',
    '''        "provider": provider,\n        "job_id": job.id,\n        "mcp_preflight": mcp_preflight,\n    }\n''',
)
replace_once(
    path,
    '''            "message": "本机 CLI Agent 已连接，将直接读取作品文件并通过 Siming MCP 写入",\n''',
    '''            "message": "本机 CLI Agent 已连接，Siming MCP 建档工具已通过启动检查",\n''',
)
replace_once(
    path,
    '''    for suffix, value in managed_env.items():\n        set_compatible_env(f"SIMING_{suffix}", value, target=env)\n    process = await asyncio.create_subprocess_exec(\n''',
    '''    for suffix, value in managed_env.items():\n        set_compatible_env(f"SIMING_{suffix}", value, target=env)\n    if config.provider == "opencode_cli":\n        # OpenCode supports a runtime-only permission override. Keep the managed\n        # cataloging child read-only except for the ten Siming cataloging tools.\n        env["OPENCODE_PERMISSION"] = _opencode_cataloging_permission_env()\n    process = await asyncio.create_subprocess_exec(\n''',
)
replace_once(
    path,
    '''            external_activity_probe=lambda: _latest_agent_event_at(agent_run_id),\n            poll_seconds=poll_seconds,\n        )\n''',
    '''            external_activity_probe=lambda: _latest_agent_event_at(agent_run_id),\n            poll_seconds=poll_seconds,\n            stop_on_permission_request=True,\n        )\n''',
)
replace_once(
    path,
    '''    stderr_tail: str = "",\n) -> tuple[bool, str]:\n''',
    '''    stderr_tail: str = "",\n    failure_reason: str = "",\n) -> tuple[bool, str]:\n''',
)
replace_once(
    path,
    '''        message="本机 CLI 未通过 MCP 保存，改用同一模型的直连 JSONL 建档兜底",\n''',
    '''        message=(\n            failure_reason\n            or "本机 CLI 未通过 MCP 保存，改用同一模型的直连 JSONL 建档兜底"\n        ),\n''',
)
replace_once(
    path,
    '''            try:\n                returncode, stdout, stderr = await _run_cli_turn(\n''',
    '''            tool_events_before = _agent_tool_event_count(agent_run_id)\n            try:\n                returncode, stdout, stderr = await _run_cli_turn(\n''',
)
replace_once(
    path,
    '''                no_saved_progress = returncode == 0 and _turn_has_no_saved_progress(stage, run.status)\n                if no_saved_progress:\n                    attempt = no_save_attempts.get(run.id, 0) + 1\n''',
    '''                tool_activity = _agent_tool_event_count(agent_run_id) > tool_events_before\n                no_saved_progress = returncode == 0 and _turn_has_no_saved_progress(stage, run.status)\n                if no_saved_progress:\n                    attempt = no_save_attempts.get(run.id, 0) + 1\n''',
)
replace_once(
    path,
    '''                            message=(\n                                f"本机 CLI 未保存第 {run.chapter_order + 1} 章，"\n                                f"正在自动重试 {attempt + 1}/{_MAX_NO_SAVE_ATTEMPTS}"\n                            ),\n''',
    '''                            message=(\n                                (\n                                    "MCP 已连接，但模型本轮未调用任何 Siming 工具；"\n                                    if not tool_activity\n                                    else "模型调用了 Siming MCP，但未完成本章保存；"\n                                )\n                                + f"正在自动重试 {attempt + 1}/{_MAX_NO_SAVE_ATTEMPTS}"\n                            ),\n''',
)
replace_once(
    path,
    '''                        stderr_tail=stderr,\n                    )\n''',
    '''                        stderr_tail=stderr,\n                        failure_reason=(\n                            "MCP 已连接，但模型连续重试后仍未调用建档写入工具；"\n                            "改用同一模型的直连 JSONL 建档兜底"\n                            if not tool_activity\n                            else "模型调用了 Siming MCP，但连续重试后仍未完成本章保存；"\n                            "改用同一模型的直连 JSONL 建档兜底"\n                        ),\n                    )\n''',
)

# 5. Regression tests.
append_once(
    "backend/tests/test_mcp_auto_config.py",
    "test_opencode_preflight_requires_configured_connected_siming",
    r'''

def test_opencode_preflight_requires_configured_connected_siming():
    connected = MagicMock(returncode=0, stdout="siming connected\n", stderr="")
    with patch(
        "app.services.external_agent.mcp_auto_config._resolve_command",
        return_value="opencode",
    ), patch(
        "app.services.external_agent.mcp_auto_config.subprocess.run",
        return_value=connected,
    ), patch(
        "app.services.external_agent.mcp_auto_config._probe_siming_mcp_tools",
        return_value=(set(mcp_auto_config.CATALOGING_MCP_TOOL_NAMES), ""),
    ):
        result = mcp_auto_config.preflight_cli_integration(
            "opencode_cli",
            cli_command="opencode",
        )

    assert result["ready"] is True
    assert result["connected"] is True
    assert result["missing_tools"] == []


def test_opencode_preflight_reports_missing_mcp_configuration():
    listed = MagicMock(returncode=0, stdout="No MCP servers configured\n", stderr="")
    with patch(
        "app.services.external_agent.mcp_auto_config._resolve_command",
        return_value="opencode",
    ), patch(
        "app.services.external_agent.mcp_auto_config.subprocess.run",
        return_value=listed,
    ), patch(
        "app.services.external_agent.mcp_auto_config._probe_siming_mcp_tools",
        return_value=(set(mcp_auto_config.CATALOGING_MCP_TOOL_NAMES), ""),
    ):
        result = mcp_auto_config.preflight_cli_integration("opencode_cli")

    assert result["ready"] is False
    assert result["configured"] is False
    assert "尚未配置" in result["detail"]
''',
)
append_once(
    "backend/tests/test_getting_started.py",
    "test_quick_start_can_explicitly_configure_and_preflight_opencode_mcp",
    r'''

def test_quick_start_can_explicitly_configure_and_preflight_opencode_mcp():
    from app.routers.getting_started import configure_getting_started_opencode_mcp

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(APIConfig(
            provider="opencode_cli",
            provider_type="local_cli",
            api_key_encrypted="test",
            default_model="opencode/big-pickle",
            cli_command=r"C:\\managed\\opencode.exe",
            readiness_status="ready",
        ))
        db.commit()
        with patch(
            "app.routers.getting_started.resolve_opencode_command",
            return_value=r"C:\\managed\\opencode.exe",
        ), patch(
            "app.routers.getting_started.configure_cli_integration",
            return_value={"status": "configured", "configured": True, "detail": "configured"},
        ) as configure, patch(
            "app.routers.getting_started.preflight_cli_integration",
            return_value={"ready": True, "detail": "ready", "missing_tools": []},
        ) as preflight:
            result = configure_getting_started_opencode_mcp(db)

    assert result.data["ready"] is True
    configure.assert_called_once()
    preflight.assert_called_once()
''',
)
append_once(
    "backend/tests/test_local_cli_cataloging_agent.py",
    "test_opencode_cataloging_permission_env_is_read_only_except_cataloging_mcp",
    r'''

def test_opencode_cataloging_permission_env_is_read_only_except_cataloging_mcp():
    from app.services.cataloging.local_cli_agent import _opencode_cataloging_permission_env
    from app.services.external_agent.mcp_auto_config import CATALOGING_MCP_TOOL_NAMES

    permissions = json.loads(_opencode_cataloging_permission_env())
    assert permissions["edit"] == "deny"
    assert permissions["bash"] == "deny"
    assert permissions["external_directory"] == "deny"
    assert permissions["read"]["*"] == "allow"
    for tool_name in CATALOGING_MCP_TOOL_NAMES:
        assert permissions[f"siming_{tool_name}"] == "allow"
''',
)

print("OpenCode MCP onboarding patch applied")
