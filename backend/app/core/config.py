"""Application settings.

Every value is sourced from the environment (or a local .env file).  Nothing
secret is ever hard-coded; see docs/COMPLIANCE.md section 5.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_env_means_default(cls, data):
        """Treat an empty-string env var as "unset" so the default applies.

        Hosts like Vercel and other dashboards inject a variable as "" when its
        box is left blank, rather than not setting it at all. Pydantic then tries
        to parse "" as an int/float/bool for the typed fields and the whole
        process exits on startup (the crash that produced the Vercel 500s). For a
        non-string field a blank value carries no information, so we drop it and
        let the field default stand.
        """
        if not isinstance(data, dict):
            return data
        string_fields = {
            name for name, field in cls.model_fields.items() if field.annotation is str
        }
        return {
            key: value
            for key, value in data.items()
            # keep blanks only for genuine string fields (an empty string is a
            # valid value there); drop them everywhere else.
            if not (isinstance(value, str) and value == "" and key.lower() not in string_fields)
        }

    # --- core -------------------------------------------------------------
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "AI Job Application Agent"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    api_v1_prefix: str = "/api/v1"
    frontend_origin: str = "http://localhost:3000"

    # --- secrets ----------------------------------------------------------
    secret_key: str = Field(default="dev-only-insecure-secret-key-change-me")
    encryption_key: str = Field(default="")
    encryption_key_previous: str = ""
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql+psycopg://jobagent:jobagent@localhost:5432/jobagent"

    # --- redis / queue ----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- storage ----------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./storage"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""

    # --- llm --------------------------------------------------------------
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 16000
    llm_enabled: bool = True

    # --- discovery --------------------------------------------------------
    discovery_interval_minutes: int = 180
    discovery_http_timeout_seconds: float = 20.0
    discovery_user_agent: str = "JobAgent/1.0 (+personal job search)"
    discovery_max_concurrency: int = 4
    discovery_per_host_rps: float = 0.5
    respect_robots_txt: bool = True

    # --- partner credentials (opt-in, user supplied) ----------------------
    linkedin_partner_api_token: str = ""
    indeed_publisher_api_token: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    usajobs_api_key: str = ""
    usajobs_user_agent: str = ""

    # --- automation safety ------------------------------------------------
    automation_global_enabled: bool = False
    auto_submit_min_score: int = 85
    daily_application_limit: int = 10
    job_max_age_hours: int = 48
    browser_assistant_token: str = ""

    # --- notifications ----------------------------------------------------
    notify_email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "Job Agent <jobagent@example.com>"
    smtp_starttls: bool = True
    notify_digest_hour_local: int = 8
    notify_timezone: str = "UTC"

    # --- rate limiting ----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    rate_limit_auth_per_minute: int = 10

    @field_validator("secret_key")
    @classmethod
    def _secret_must_be_real_in_prod(cls, v: str, info: ValidationInfo) -> str:
        env = (info.data or {}).get("app_env")
        if env in {"staging", "production"} and (len(v) < 32 or v.startswith("dev-only")):
            raise ValueError(
                "SECRET_KEY must be a unique random value of >=32 chars outside development"
            )
        return v

    @field_validator("encryption_key")
    @classmethod
    def _encryption_key_required_in_prod(cls, v: str, info: ValidationInfo) -> str:
        """Refuse the derived dev key outside development, in EVERY process.

        `main.py:_production_preflight` only guards the API: a Celery worker, an
        alembic run or a maintenance script never executes that lifespan hook, so
        the guard has to live where the setting is parsed.
        """
        env = (info.data or {}).get("app_env")
        if env in {"staging", "production"} and not v.strip():
            raise ValueError(
                "ENCRYPTION_KEY is required when APP_ENV is staging/production; the "
                "derived development key is never used there."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key) and self.llm_enabled

    @property
    def storage_root(self) -> Path:
        p = Path(self.storage_local_path)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
