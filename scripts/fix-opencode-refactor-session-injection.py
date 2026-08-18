from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:160]!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


result_path = "backend/app/services/cataloging/local_cli_result.py"
replace_once(
    result_path,
    '''def agent_tool_event_count(agent_run_id: str) -> int:\n    db = SessionLocal()\n''',
    '''def agent_tool_event_count(\n    agent_run_id: str,\n    *,\n    session_factory=SessionLocal,\n) -> int:\n    db = session_factory()\n''',
)
replace_once(
    result_path,
    '''    stage: str,\n    exc: Exception,\n) -> None:\n    db = SessionLocal()\n''',
    '''    stage: str,\n    exc: Exception,\n    session_factory=SessionLocal,\n) -> None:\n    db = session_factory()\n''',
)
replace_once(
    result_path,
    '''    tool_events_before: int,\n    no_save_attempts: dict[str, int],\n) -> TurnAction:\n    db = SessionLocal()\n''',
    '''    tool_events_before: int,\n    no_save_attempts: dict[str, int],\n    session_factory=SessionLocal,\n) -> TurnAction:\n    db = session_factory()\n''',
)
replace_once(
    result_path,
    '''        tool_activity = agent_tool_event_count(agent_run_id) > tool_events_before\n''',
    '''        tool_activity = (\n            agent_tool_event_count(\n                agent_run_id,\n                session_factory=session_factory,\n            )\n            > tool_events_before\n        )\n''',
)

agent_path = "backend/app/services/cataloging/local_cli_agent.py"
replace_once(
    agent_path,
    '''            tool_events_before = agent_tool_event_count(agent_run_id)\n''',
    '''            tool_events_before = agent_tool_event_count(\n                agent_run_id,\n                session_factory=SessionLocal,\n            )\n''',
)
replace_once(
    agent_path,
    '''                    stage=stage,\n                    exc=exc,\n                )\n''',
    '''                    stage=stage,\n                    exc=exc,\n                    session_factory=SessionLocal,\n                )\n''',
)
replace_once(
    agent_path,
    '''                tool_events_before=tool_events_before,\n                no_save_attempts=no_save_attempts,\n            )\n''',
    '''                tool_events_before=tool_events_before,\n                no_save_attempts=no_save_attempts,\n                session_factory=SessionLocal,\n            )\n''',
)

test_path = "backend/tests/test_local_cli_cataloging_agent.py"
replace_once(
    test_path,
    '''"app.services.cataloging.local_cli_agent._run_direct_jsonl_cataloging_fallback"''',
    '''"app.services.cataloging.local_cli_result._run_direct_jsonl_cataloging_fallback"''',
)

print("Preserved cataloging SessionLocal injection across extracted result helpers")
