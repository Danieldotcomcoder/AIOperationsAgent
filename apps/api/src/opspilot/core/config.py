"""Single source of configuration.

Nothing else in the application reads ``os.environ``. Settings are resolved once at
import of :func:`get_settings` and injected from there, so that a misconfiguration is
a startup failure with a clear message rather than an ``AttributeError`` on the first
request that happens to need the value.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The repository root, derived from this file's location rather than the working
#: directory. There is one .env for the whole repo, and it must resolve identically
#: whether the process was started from the root (docker compose), from apps/api
#: (alembic, uvicorn) or from a test runner.
REPO_ROOT = Path(__file__).resolve().parents[5]


class AppEnv(StrEnum):
    development = "development"
    test = "test"
    staging = "staging"
    production = "production"

    @property
    def is_local(self) -> bool:
        return self in (AppEnv.development, AppEnv.test)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later entries win, so a container-local .env can still override the root one.
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # -- environment --------------------------------------------------------
    app_env: AppEnv = AppEnv.development
    app_name: str = "opspilot"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    api_base_url: str = "http://127.0.0.1:8000"
    web_origin: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- datastores ---------------------------------------------------------
    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"
    database_pool_size: int = 5
    database_max_overflow: int = 5
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # -- identity (ADR 0008) ------------------------------------------------
    auth_mode: Literal["dev", "auth0"] = "dev"
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_issuer: str = ""
    dev_auth_signing_key_path: str = ".data/dev-auth-key.pem"
    dev_auth_token_ttl_seconds: int = 28_800

    # -- model providers (ADR 0005) -----------------------------------------
    llm_provider: Literal["fake", "openai", "anthropic", "openrouter"] = "fake"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_free_only: bool = True
    # Explicit estimates required for paid OpenRouter routes; never assume unknown = free.
    openrouter_input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    openrouter_output_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    embedding_provider: Literal["fake", "openai", "openrouter"] = "fake"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # -- agent bounds; retries count toward every one of these --------------
    run_max_turns: int = Field(default=3, ge=1, le=10)
    run_max_tool_calls: int = Field(default=6, ge=1, le=20)
    run_deadline_seconds: float = Field(default=90.0, gt=0)
    provider_call_deadline_seconds: float = Field(default=25.0, gt=0)
    run_max_input_tokens: int = Field(default=20_000, gt=0)
    run_max_output_tokens: int = Field(default=3_000, gt=0)
    retrieval_candidates: int = Field(default=8, ge=1, le=50)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    chunk_target_tokens: int = Field(default=500, ge=100)
    chunk_overlap_tokens: int = Field(default=75, ge=0)
    retrieval_token_budget: int = Field(default=2500, ge=100, le=10000)

    # -- cost guard ---------------------------------------------------------
    budget_monthly_usd: float = 15.0
    budget_daily_usd: float = 2.0
    budget_per_user_daily_runs: int = 50
    feature_writes_enabled: bool = True
    feature_agent_enabled: bool = True

    # -- business date ------------------------------------------------------
    #: The day "overdue" and "effective" are evaluated on; blank means today (UTC).
    #: Pin it to the seed's frozen date for the synthetic dataset, so the inbox, the
    #: agent and the evaluation cases all describe the same day. Real time still
    #: drives expiry, retention and budget windows.
    operations_as_of: date | None = None

    # -- lifecycle ----------------------------------------------------------
    proposal_expiry_hours: int = 24
    trace_retention_days: int = Field(default=14, ge=1)
    audit_retention_days: int = Field(default=90, ge=1)

    # -- simulated ERP boundary --------------------------------------------
    ops_api_base_url: str = "http://127.0.0.1:8000/ops"
    ops_api_service_token: SecretStr = SecretStr("change-me-locally")
    ops_api_timeout_seconds: float = 10.0

    # ------------------------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        """Exact origins only. A wildcard is never correct for a credentialed API."""
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @field_validator("operations_as_of", mode="before")
    @classmethod
    def _blank_means_today(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _refuse_unsafe_deployments(self) -> Settings:
        """Fail at startup rather than serve a dangerous configuration.

        Every check here has been a real incident somewhere. Keep them loud.
        """
        if not self.app_env.is_local:
            if self.auth_mode != "auth0":
                raise ValueError(
                    "AUTH_MODE must be 'auth0' outside development/test; "
                    "the dev token issuer is not a deployable identity provider."
                )
            if not (self.auth0_domain and self.auth0_audience and self.auth0_issuer):
                raise ValueError("AUTH0_DOMAIN, AUTH0_AUDIENCE and AUTH0_ISSUER are required.")
            if self.ops_api_service_token.get_secret_value() in ("", "change-me-locally"):
                raise ValueError("OPS_API_SERVICE_TOKEN must be set to a real secret.")
            if self.llm_provider == "fake":
                raise ValueError("LLM_PROVIDER=fake is a development fixture, not a provider.")
        if self.llm_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key.get_secret_value():
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.")
        if self.llm_provider == "openrouter":
            if not self.openrouter_api_key.get_secret_value():
                raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter.")
            free = self.llm_model == "openrouter/free" or (
                self.llm_model.endswith(":free") and not self.llm_model.startswith("openrouter/")
            )
            if self.openrouter_free_only and not free:
                raise ValueError(
                    "Free-only mode requires openrouter/free or a model's :free variant."
                )
            if not self.openrouter_free_only and (
                self.openrouter_input_usd_per_million is None
                or self.openrouter_output_usd_per_million is None
            ):
                raise ValueError(
                    "Paid OpenRouter routes require explicit input/output price estimates."
                )
        if self.embedding_dim != 1536:
            raise ValueError("EMBEDDING_DIM must match the database vector dimension (1536).")
        if self.embedding_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings.")
        if self.embedding_provider == "openrouter":
            if not self.openrouter_api_key.get_secret_value():
                raise ValueError("OPENROUTER_API_KEY is required for OpenRouter embeddings.")
            if self.embedding_model != "liquid/lfm-2.5-embedding-350m:free":
                raise ValueError("Only liquid/lfm-2.5-embedding-350m:free is supported here.")
            if self.chunk_target_tokens > 500:
                raise ValueError("Liquid embeddings require CHUNK_TARGET_TOKENS <= 500 bytes.")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TARGET_TOKENS.")
        if self.retrieval_top_k > self.retrieval_candidates:
            raise ValueError("RETRIEVAL_TOP_K cannot exceed RETRIEVAL_CANDIDATES.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached; call it, do not re-instantiate ``Settings``."""
    return Settings()
