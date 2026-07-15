"""Tests for using the operating system certificate store."""
from __future__ import annotations

from unittest.mock import patch

from app.core import system_trust


def test_windows_runtime_injects_system_trust_once():
    with patch.object(system_trust, "_configuration_status", None), patch.object(
        system_trust.sys, "platform", "win32"
    ), patch.object(system_trust.truststore, "inject_into_ssl") as inject:
        first = system_trust.configure_system_trust()
        second = system_trust.configure_system_trust()

    assert first.enabled is True
    assert first.backend == "windows-system"
    assert second == first
    inject.assert_called_once_with()


def test_non_windows_runtime_keeps_python_default_trust():
    with patch.object(system_trust, "_configuration_status", None), patch.object(
        system_trust.sys, "platform", "linux"
    ), patch.object(system_trust.truststore, "inject_into_ssl") as inject:
        status = system_trust.configure_system_trust()

    assert status.enabled is False
    assert status.backend == "python-default"
    inject.assert_not_called()
