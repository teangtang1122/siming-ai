"""Runtime tracking stays local while CLI correlation and useful errors survive."""
import sys
from unittest.mock import patch

import httpx
from openai import BadRequestError

from app.ai.local_cli_adapter import LocalCLIAdapter
from app.core.exceptions import LLMError
from app.modules.model_runtime.infrastructure.gateway import LLMGateway
from app.services.creation_agent_errors import safe_creation_agent_error
from app.services.workspace.assistant_public_errors import public_model_failure


def test_cli_retains_operation_correlation_under_internal_namespace(tmp_path):
    with patch("app.modules.operations.interfaces.runtime.current_operation_id",return_value="fixture-operation"):
        body,_=LLMGateway._local_cli_timeout_body(LocalCLIAdapter,None,30)
    assert body["moshu_operation_id"]=="fixture-operation"
    assert "operation_id" not in body
    adapter=LocalCLIAdapter(api_key="fixture",base_url="custom_cli",cli_command=sys.executable,cli_args='["-c", "{prompt}"]')
    with patch.object(adapter,"_runtime_cwd",return_value=str(tmp_path)):
        context=adapter._prepare_run_context("fixture","fixture",body)
    assert context.operation_id=="fixture-operation"


def test_rejected_parameter_is_not_unknown_and_does_not_expose_parameter_values():
    request=httpx.Request("POST","https://api.openai.com/v1/responses")
    upstream=BadRequestError("Unknown parameter: operation_id",response=httpx.Response(400,request=request),
        body={"error":{"message":"private value must not be public","param":"operation_id","code":"unknown_parameter","type":"invalid_request_error"}})
    error=LLMError("OpenAI API 错误: Error code: 400 - Unknown parameter: operation_id")
    error.__cause__=upstream
    failure=public_model_failure(error)
    message,creation=safe_creation_agent_error(error)
    assert failure.code=="model_request_parameters_rejected"
    assert failure.failure_class==creation["failure_class"]=="request_parameters"
    assert failure.details["provider_status"]==400
    assert failure.details["parameter"]=="operation_id"
    assert failure.details["provider_error_code"]=="unknown_parameter"
    assert creation["message"]==message==failure.message
    assert "private" not in message
    assert failure.details["retryable"] is False
