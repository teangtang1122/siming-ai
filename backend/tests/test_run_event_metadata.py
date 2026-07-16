import json

from app.services.observability.run_events import classify_failure, merge_event_metadata


def test_classify_quota_error_from_cli_text():
    assert classify_failure("Free usage exceeded, subscribe to Go [retrying in 9h]") == "quota_or_rate_limit"


def test_classify_network_unavailability():
    assert classify_failure("Cannot connect to OpenAI") == "network"


def test_classify_empty_and_invalid_model_responses():
    assert classify_failure("没有收到模型的文字回复") == "empty_response"
    assert classify_failure("模型返回的新选项格式无法解析") == "invalid_response"


def test_merge_event_metadata_adds_failure_class_and_next_action():
    payload_json = merge_event_metadata(
        json.dumps({"tool": "opencode"}),
        event_type="error",
        status="error",
        message="请求超时（180秒）",
        model_source="opencode_cli:deepseek-free",
        tool_mode="siming_mcp_task_file",
        next_action="test_local_cli_or_switch_provider",
    )

    payload = json.loads(payload_json)
    assert payload["tool"] == "opencode"
    assert payload["failure_class"] == "timeout"
    assert payload["model_source"] == "opencode_cli:deepseek-free"
    assert payload["tool_mode"] == "siming_mcp_task_file"
    assert payload["next_action"] == "test_local_cli_or_switch_provider"
