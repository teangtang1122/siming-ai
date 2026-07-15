"""Configure Python HTTPS clients to use the operating system trust store."""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass

import truststore


logger = logging.getLogger(__name__)
_configuration_lock = threading.Lock()
_configuration_status: "SystemTrustStatus | None" = None


@dataclass(frozen=True)
class SystemTrustStatus:
    enabled: bool
    backend: str
    error: str | None = None


def configure_system_trust() -> SystemTrustStatus:
    """Use the Windows certificate store without weakening TLS verification."""
    global _configuration_status
    if _configuration_status is not None:
        return _configuration_status
    with _configuration_lock:
        if _configuration_status is not None:
            return _configuration_status
        if sys.platform != "win32":
            _configuration_status = SystemTrustStatus(enabled=False, backend="python-default")
            return _configuration_status
        try:
            truststore.inject_into_ssl()
            _configuration_status = SystemTrustStatus(enabled=True, backend="windows-system")
        except Exception as exc:  # pragma: no cover - depends on the host certificate APIs
            logger.warning("Unable to enable the Windows system trust store: %s", exc)
            _configuration_status = SystemTrustStatus(
                enabled=False,
                backend="python-default",
                error=str(exc),
            )
        return _configuration_status
