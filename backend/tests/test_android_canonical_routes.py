"""The Android allowlist must expose existing PC routes, never a second dialect."""

from app.bootstrap.app_factory import create_app
from app.bootstrap.http_security import (
    REMOTE_ANDROID_AUTHORING_PATHS,
    GatewayAuthenticationMiddleware,
    GatewayRequestLimitMiddleware,
)


def test_android_authoring_allowlist_is_a_subset_of_pc_openapi():
    paths = create_app(run_startup=False).openapi()["paths"]

    for path, methods in REMOTE_ANDROID_AUTHORING_PATHS.items():
        assert path in paths, path
        published = set(paths[path])
        for method in methods - {"HEAD"}:
            assert method.lower() in published, f"{method} {path}"


def test_android_assistant_lifecycle_routes_are_explicitly_allowlisted():
    concrete_paths = (
        "/api/v1/projects/project-1/chapter-drafts/pending",
        "/api/v1/projects/project-1/ai/assistant/conversations",
        "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1/context-state",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1/checkpoints",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1/checkpoints/rebuild",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1/checkpoints/checkpoint-1",
        "/api/v1/projects/project-1/ai/assistant/conversations/conversation-1/checkpoints/checkpoint-1/cancel",
        "/api/v1/projects/project-1/ai/assistant/runs",
        "/api/v1/projects/project-1/ai/assistant/runs/run-1",
        "/api/v1/projects/project-1/ai/assistant/runs/run-1/cancel",
        "/api/v1/novel-creation/sessions/session-1/conversations/conversation-1/context-state",
        "/api/v1/novel-creation/sessions/session-1/conversations/conversation-1/checkpoints",
        (
            "/api/v1/novel-creation/sessions/session-1/conversations/"
            "conversation-1/checkpoints/checkpoint-1"
        ),
    )
    assert all(
        GatewayAuthenticationMiddleware.is_remote_android_authoring_path(path)
        for path in concrete_paths
    )


def test_android_transcript_import_literal_precedes_conversation_detail_template():
    path = "/api/v1/projects/project-1/ai/assistant/conversations/transcript-import"
    first = next(
        (template, methods)
        for template, pattern, methods in
        GatewayAuthenticationMiddleware.REMOTE_ANDROID_AUTHORING_ROUTES
        if pattern.fullmatch(path) is not None
    )
    assert first == (
        "/api/v1/projects/{project_id}/ai/assistant/conversations/transcript-import",
        frozenset({"POST"}),
    )
    assert GatewayRequestLimitMiddleware._body_limit(path) == 2 * 1024 * 1024
    assert GatewayRequestLimitMiddleware._rate_limit(path) == (30, 60.0)
