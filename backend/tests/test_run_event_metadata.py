import json

from app.services.observability.run_events import classify_failure, merge_event_metadata


def test_classify_quota_error_from_cli_text():
    assert classify_failure("Free usage exceeded, subscribe to Go [retrying in 9h]") == "quota_or_rate_limit"


def test_classify_network_unavailability():
    assert classify_failure("Cannot connect to OpenAI") == "network"
    assert classify_failure(
        "Error code: 502 - {'error': {'message': 'Upstream request failed', 'type': 'upstream_error'}}"
    ) == "network"


def test_classify_explicit_provider_overload_before_generic_502_network_error():
    assert classify_failure(
        "Streaming response failed: [502] Upstream error from Nvidia: "
        "Service temporarily overloaded"
    ) == "unavailable"


def test_classify_empty_and_invalid_model_responses():
    assert classify_failure("没有收到模型的文字回复") == "empty_response"
    assert classify_failure("模型返回的新选项格式无法解析") == "invalid_response"


def test_classify_tool_schema_errors_without_treating_cataloging_as_login():
    detail = (
        "save_external_cataloging_facts: 工具参数不符合当前 JSON Schema，本次未执行。"
        "请核对必填字段及类型；对象和数组必须直接传入。"
    )

    assert classify_failure(detail) == "invalid_arguments"
    assert classify_failure("Required tool argument facts is missing") == "invalid_arguments"
    assert classify_failure("cataloging failed for an unknown reason") == "unknown"
    assert classify_failure("HTTP 401 Unauthorized") == "auth"
    assert classify_failure("Login required") == "auth"

    payload = json.loads(merge_event_metadata(
        json.dumps({"tool": "save_external_cataloging_facts"}),
        event_type="tool_result",
        status="error",
        message=detail,
    ))
    assert payload["failure_class"] == "invalid_arguments"


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
