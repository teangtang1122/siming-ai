"""Project cataloging pipeline."""

from .runtime_repair import install_cataloging_runtime_repairs

install_cataloging_runtime_repairs()

__all__ = ["install_cataloging_runtime_repairs"]
