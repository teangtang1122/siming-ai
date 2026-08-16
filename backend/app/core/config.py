"""Application configuration."""

import sys
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Source checkouts may retain deprecated keys in their development .env.
    # They must not prevent the application from starting; validation remains
    # strict for every setting that is still part of this model.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    database_url: str = "sqlite:///./novel_agent.db"
    secret_key: str = "change-me-in-production"
    cors_origins: str = "http://localhost:5371"
    runtime_profile: Literal["desktop-standalone", "gateway"] = Field(
        default="desktop-standalone",
        validation_alias=AliasChoices("SIMING_RUNTIME_PROFILE", "RUNTIME_PROFILE"),
    )
    gateway_advertised_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_ADVERTISED_URL", "GATEWAY_ADVERTISED_URL"
        ),
    )
    gateway_name: str = Field(
        default="司命 Gateway",
        validation_alias=AliasChoices("SIMING_GATEWAY_NAME", "GATEWAY_NAME"),
    )
    gateway_allowed_hosts: str = Field(
        default="localhost,127.0.0.1,testserver,*.local,*.ts.net",
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_ALLOWED_HOSTS", "GATEWAY_ALLOWED_HOSTS"
        ),
    )
    gateway_cors_origins: str = Field(
        default="http://localhost,https://localhost,capacitor://localhost",
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_CORS_ORIGINS", "GATEWAY_CORS_ORIGINS"
        ),
    )
    gateway_bootstrap_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_BOOTSTRAP_KEY", "GATEWAY_BOOTSTRAP_KEY"
        ),
    )
    gateway_headless: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_HEADLESS", "GATEWAY_HEADLESS"
        ),
    )
    gateway_pairing_ttl_minutes: int = Field(
        default=10,
        ge=2,
        le=30,
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_PAIRING_TTL_MINUTES", "GATEWAY_PAIRING_TTL_MINUTES"
        ),
    )
    gateway_access_ttl_minutes: int = Field(
        default=15,
        ge=5,
        le=60,
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_ACCESS_TTL_MINUTES", "GATEWAY_ACCESS_TTL_MINUTES"
        ),
    )
    gateway_refresh_ttl_days: int = Field(
        default=30,
        ge=1,
        le=90,
        validation_alias=AliasChoices(
            "SIMING_GATEWAY_REFRESH_TTL_DAYS", "GATEWAY_REFRESH_TTL_DAYS"
        ),
    )
    sync_tombstone_retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        validation_alias=AliasChoices(
            "SIMING_SYNC_TOMBSTONE_RETENTION_DAYS", "SYNC_TOMBSTONE_RETENTION_DAYS"
        ),
    )
    
    def get_cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        values = [origin.strip().rstrip("/") for origin in self.cors_origins.split(",")]
        if self.runtime_profile == "gateway":
            values.extend(
                origin.strip().rstrip("/")
                for origin in self.gateway_cors_origins.split(",")
            )
        return sorted({origin for origin in values if origin})

    def get_trusted_hosts(self) -> list[str]:
        """Return configured hosts plus addresses owned by this machine."""

        hosts = {
            host.strip()
            for host in self.gateway_allowed_hosts.split(",")
            if host.strip()
        }
        hosts.update({"127.0.0.1", "localhost", "testserver"})
        if self.runtime_profile == "gateway":
            try:
                import psutil

                for addresses in psutil.net_if_addrs().values():
                    for address in addresses:
                        value = str(address.address).split("%", 1)[0]
                        if value:
                            hosts.add(value)
            except Exception:
                # Explicit configuration remains authoritative if interface
                # enumeration is unavailable in a slim container.
                pass
        return sorted(hosts)

    @property
    def gateway_enabled(self) -> bool:
        return self.runtime_profile == "gateway"

    @property
    def local_runtime_enabled(self) -> bool:
        return not (self.gateway_enabled and self.gateway_headless)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    if getattr(sys, "frozen", False):
        # A packaged executable can be launched by another CLI from that CLI's
        # working directory.  Never interpret the caller's .env as Siming
        # configuration; packaged builds only accept the real process
        # environment prepared by launcher.py.
        return Settings(_env_file=None)
    return Settings()
