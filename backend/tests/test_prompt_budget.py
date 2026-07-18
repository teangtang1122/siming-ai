"""Fixed prompt-volume regression gate for the four core AI flows."""
from __future__ import annotations

from scripts.check_prompt_budget import prompt_volume_report


def test_core_prompt_volume_is_at_least_twenty_percent_below_2_9() -> None:
    report = prompt_volume_report()

    assert report["source_tag"] == "v2.9.0"
    assert report["reduction_percent"] >= report["minimum_reduction_percent"]
    assert all(
        flow["reduction_percent"] >= report["minimum_reduction_percent"]
        for flow in report["flows"].values()
    )
    assert set(report["flows"]) == {
        "workspace_assistant",
        "chapter_writer",
        "cataloging",
        "novel_creation",
    }
