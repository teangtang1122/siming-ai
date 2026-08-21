"""Gitee release mirroring must run after GitHub finishes publishing assets."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_main_push_mirrors_code_without_selecting_the_previous_release():
    workflow = (WORKFLOWS / "sync-gitee.yml").read_text(encoding="utf-8")

    release_start = workflow.index("- name: Check out release sync script")
    release_end = workflow.index("- name: Remove SSH credentials")
    release_steps = workflow[release_start:release_end]

    assert release_steps.count("if: github.event_name != 'push'") == 3
    assert "github.event_name == 'push' && 'latest'" not in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.tag" in workflow
    assert "github.event_name == 'release' && github.event.release.tag_name" in workflow


def test_successful_release_gate_dispatches_the_exact_published_tag():
    workflow = (WORKFLOWS / "sync-gitee-after-release.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_run:" in workflow
    assert 'workflows: ["Release Gate"]' in workflow
    assert "types: [completed]" in workflow
    assert "actions: write" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "git ls-remote --tags" in workflow
    assert 'gh release view "$tag"' in workflow
    assert "gh workflow run sync-gitee.yml" in workflow
    assert "--ref main" in workflow
    assert '-f "tag=$TAG"' in workflow


def test_scheduled_sync_remains_as_a_recovery_path():
    workflow = (WORKFLOWS / "sync-gitee.yml").read_text(encoding="utf-8")

    assert 'cron: "17 2 * * *"' in workflow
    assert "GITEE_RELEASE_RETENTION_COUNT: \"3\"" in workflow
