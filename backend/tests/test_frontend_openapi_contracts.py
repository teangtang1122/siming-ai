from app.main import create_app


def _response_schema(openapi: dict, path: str, method: str) -> dict:
    return openapi["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]


def test_core_frontend_queries_have_generated_response_contracts():
    openapi = create_app().openapi()

    expected_refs = {
        ("/api/v1/projects", "get"): "ApiResponse_ProjectListData_",
        ("/api/v1/projects", "post"): "ApiResponse_ProjectResponse_",
        ("/api/v1/projects/{project_id}", "get"): "ApiResponse_ProjectResponse_",
        ("/api/v1/operations", "get"): "ApiResponse_OperationListData_",
        ("/api/v1/operations/{operation_id}", "get"): "ApiResponse_OperationResponse_",
        ("/api/v1/config/getting-started", "get"): "ApiResponse_GettingStartedStatus_",
    }

    for (path, method), schema_name in expected_refs.items():
        schema = _response_schema(openapi, path, method)
        assert schema["$ref"].endswith(f"/{schema_name}")


def test_operation_contract_exports_lifecycle_and_health_enums():
    openapi = create_app().openapi()
    operation = openapi["components"]["schemas"]["OperationResponse"]

    assert set(operation["properties"]["status"]["enum"]) == {
        "draft",
        "queued",
        "running",
        "waiting_user",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }
    assert set(operation["properties"]["health_status"]["enum"]) == {
        "active",
        "quiet",
        "suspected_stall",
        "stalled",
        "disconnected",
    }
