"""Legacy environment names remain accepted only through one boundary."""

from app.core.legacy_env import (
    compatible_env_enabled,
    get_compatible_env,
    set_compatible_env,
)


def test_canonical_environment_value_wins(monkeypatch) -> None:
    monkeypatch.setenv("MOSHU_HOME", "legacy")
    monkeypatch.setenv("SIMING_HOME", "canonical")

    assert get_compatible_env("SIMING_HOME") == "canonical"


def test_legacy_environment_value_is_still_read(monkeypatch) -> None:
    monkeypatch.delenv("SIMING_DISABLE_UPDATE", raising=False)
    monkeypatch.setenv("MOSHU_DISABLE_UPDATE", "1")

    assert compatible_env_enabled("SIMING_DISABLE_UPDATE") is True


def test_child_process_environment_receives_compatible_managed_names() -> None:
    environment: dict[str, str] = {}

    set_compatible_env(
        "SIMING_MANAGED_CATALOGING_JOB_ID",
        "job-1",
        target=environment,
    )

    assert environment == {
        "SIMING_MANAGED_CATALOGING_JOB_ID": "job-1",
        "MOSHU_MANAGED_CATALOGING_JOB_ID": "job-1",
    }
