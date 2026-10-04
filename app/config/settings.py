"""Environment-based configuration (pydantic-settings). Secrets are SecretStr and never printed."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    app_env: Literal["development", "demo", "production"] = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    db_log_level: str = Field("INFO", alias="DB_LOG_LEVEL")

    telegram_bot_token: SecretStr = Field(SecretStr(""), alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_ids_raw: str = Field("", alias="ADMIN_TELEGRAM_IDS")

    database_url: str = Field("sqlite+aiosqlite:///./.runtime/app.db", alias="DATABASE_URL")
    encryption_key: SecretStr = Field(SecretStr(""), alias="ENCRYPTION_KEY")

    ctrader_client_id: str = Field("", alias="CTRADER_CLIENT_ID")
    ctrader_client_secret: SecretStr = Field(SecretStr(""), alias="CTRADER_CLIENT_SECRET")
    ctrader_redirect_uri: str = Field("", alias="CTRADER_REDIRECT_URI")
    ctrader_environment: Literal["demo", "live"] = Field("demo", alias="CTRADER_ENVIRONMENT")
    ctrader_scope: Literal["accounts", "trading"] = Field("trading", alias="CTRADER_SCOPE")
    ctrader_auth_url: str = Field("https://id.ctrader.com/my/settings/openapi/grantingaccess/", alias="CTRADER_AUTH_URL")
    ctrader_token_url: str = Field("https://openapi.ctrader.com/apps/token", alias="CTRADER_TOKEN_URL")
    ctrader_demo_host: str = Field("demo.ctraderapi.com", alias="CTRADER_DEMO_HOST")
    ctrader_live_host: str = Field("live.ctraderapi.com", alias="CTRADER_LIVE_HOST")
    ctrader_port: int = Field(5035, alias="CTRADER_PORT")
    ctrader_request_timeout: float = Field(15.0, alias="CTRADER_REQUEST_TIMEOUT")
    token_refresh_margin_seconds: int = Field(900, alias="TOKEN_REFRESH_MARGIN_SECONDS")

    live_trading_enabled: bool = Field(False, alias="LIVE_TRADING_ENABLED")

    web_host: str = Field("0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(8080, alias="PORT")

    strategies_dir: str = Field("strategies", alias="STRATEGIES_DIR")
    strategy_sandbox_mode: Literal["process", "docker"] = Field("process", alias="STRATEGY_SANDBOX_MODE")
    strategy_docker_image: str = Field("ctrader-tg-platform:latest", alias="STRATEGY_DOCKER_IMAGE")
    strategy_max_file_bytes: int = Field(200_000, alias="STRATEGY_MAX_FILE_BYTES")
    strategy_allowed_imports_raw: str = Field(
        "math,statistics,collections,dataclasses,datetime,decimal,typing,enum,functools,itertools,"
        "fractions,heapq,bisect,random,json,numbers,operator",
        alias="STRATEGY_ALLOWED_IMPORTS")
    strategy_allowed_dependencies_raw: str = Field("", alias="STRATEGY_ALLOWED_DEPENDENCIES")
    strategy_max_dependencies: int = Field(5, alias="STRATEGY_MAX_DEPENDENCIES")
    strategy_call_timeout: float = Field(5.0, alias="STRATEGY_CALL_TIMEOUT_SECONDS")
    strategy_memory_mb: int = Field(256, alias="STRATEGY_MEMORY_MB")
    strategy_cpu_seconds: int = Field(86400, alias="STRATEGY_CPU_SECONDS")

    market_data_stale_seconds: int = Field(30, alias="MARKET_DATA_STALE_SECONDS")
    reconcile_interval_seconds: int = Field(120, alias="RECONCILE_INTERVAL_SECONDS")
    heartbeat_interval_seconds: int = Field(15, alias="HEARTBEAT_INTERVAL_SECONDS")
    watchdog_interval_seconds: int = Field(20, alias="WATCHDOG_INTERVAL_SECONDS")
    notification_throttle_seconds: int = Field(300, alias="NOTIFICATION_THROTTLE_SECONDS")
    crash_loop_max_restarts: int = Field(4, alias="CRASH_LOOP_MAX_RESTARTS")
    crash_loop_window_seconds: int = Field(600, alias="CRASH_LOOP_WINDOW_SECONDS")

    @field_validator("log_level", "db_log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ---- derived ----
    @property
    def admin_ids(self) -> list[int]:
        out: list[int] = []
        for part in self.admin_telegram_ids_raw.replace(";", ",").split(","):
            part = part.strip()
            if part:
                if not part.lstrip("-").isdigit():
                    raise ConfigError("ADMIN_TELEGRAM_IDS must be numeric Telegram user IDs")
                out.append(int(part))
        return out

    @property
    def strategy_allowed_imports(self) -> set[str]:
        return {p.strip() for p in self.strategy_allowed_imports_raw.split(",") if p.strip()}

    @property
    def strategy_allowed_dependencies(self) -> set[str]:
        return {p.strip() for p in self.strategy_allowed_dependencies_raw.split(",") if p.strip()}

    @property
    def async_database_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        elif url.startswith("sqlite://") and "aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.async_database_url.startswith("sqlite")

    @property
    def redirect_path(self) -> str:
        return urlparse(self.ctrader_redirect_uri).path or "/auth/ctrader/callback"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def secret_values(self) -> list[str]:
        """Exact secret strings that the log masker must scrub."""
        vals = [self.telegram_bot_token.get_secret_value(), self.encryption_key.get_secret_value(),
                self.ctrader_client_secret.get_secret_value()]
        try:
            from sqlalchemy.engine import make_url
            pw = make_url(self.async_database_url).password
            if pw:
                vals.append(pw)
        except Exception:  # pragma: no cover - sqlalchemy missing in unit contexts
            pass
        return [v for v in vals if v and len(v) >= 4]

    def validate_runtime(self) -> None:
        """Fail fast at startup with *non-secret* error messages."""
        problems: list[str] = []
        if not self.telegram_bot_token.get_secret_value():
            problems.append("TELEGRAM_BOT_TOKEN is required")
        if not self.admin_ids:
            problems.append("ADMIN_TELEGRAM_IDS must contain at least one numeric Telegram ID")
        key = self.encryption_key.get_secret_value()
        if not key:
            problems.append("ENCRYPTION_KEY is required (python scripts/generate_key.py)")
        else:
            try:
                from cryptography.fernet import Fernet
                Fernet(key.encode())
            except Exception:
                problems.append("ENCRYPTION_KEY is not a valid Fernet key (python scripts/generate_key.py)")
        if self.is_production and self.is_sqlite:
            problems.append("Production requires PostgreSQL DATABASE_URL (SQLite is for dev/Termux only)")
        if self.live_trading_enabled and not self.is_production:
            problems.append("LIVE_TRADING_ENABLED=true is only allowed with APP_ENV=production")
        if self.ctrader_client_id and not self.ctrader_client_secret.get_secret_value():
            problems.append("CTRADER_CLIENT_SECRET missing while CTRADER_CLIENT_ID is set")
        if self.ctrader_client_id and not self.ctrader_redirect_uri:
            problems.append("CTRADER_REDIRECT_URI is required for the OAuth flow")
        if problems:
            raise ConfigError("Invalid configuration: " + "; ".join(problems))

    @property
    def ctrader_configured(self) -> bool:
        return bool(self.ctrader_client_id and self.ctrader_client_secret.get_secret_value() and self.ctrader_redirect_uri)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_settings() -> Settings:
    return load_settings()
