from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything is env-driven. Secrets are never logged or serialized."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Urja Meter Ops API"
    environment: str = "dev"
    log_level: str = "INFO"
    admin_api_key: SecretStr = SecretStr("dev-admin-key")
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    max_page_size: int = 100
    default_page_size: int = 20
    warm_cache_on_startup: bool = False

    urja_base_url: str = "http://127.0.0.1:9000"
    urja_username: str = "operator@urja.example"
    urja_password: SecretStr = SecretStr("urja-demo-pass")
    urja_timezone: str = "Asia/Kolkata"
    urja_user_agent: str = "urja-meter-ops-api/1.0 (+read-only)"

    portal_connect_timeout_s: float = 3.0
    portal_read_timeout_s: float = 10.0
    portal_total_timeout_s: float = 15.0
    portal_max_concurrency: int = 5
    portal_max_retries: int = 2
    portal_retry_base_delay_s: float = 0.25
    breaker_failure_threshold: int = 5
    breaker_reset_timeout_s: float = 20.0
    readiness_probe_ttl_s: float = 10.0

    cache_meters_fresh_s: float = 600.0
    cache_meters_stale_s: float = 3600.0
    cache_meter_fresh_s: float = 300.0
    cache_meter_stale_s: float = 1800.0
    cache_consumption_fresh_s: float = 120.0
    cache_consumption_stale_s: float = 3600.0
    cache_consumption_closed_fresh_s: float = 3600.0
    cache_consumption_closed_stale_s: float = 86400.0
    cache_hierarchy_fresh_s: float = 600.0
    cache_hierarchy_stale_s: float = 3600.0
    cache_jitter_ratio: float = 0.05

    @property
    def base_url(self) -> str:
        return self.urja_base_url.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
