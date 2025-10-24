"""Central configuration (12-factor). Every setting is overridable via env / `.env`.

Add new settings to the relevant section; never rename existing ones (the web app, Makefile and CI read them).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(BACKEND_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "Nightjar SOC"
    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    public_app_url: str = "http://localhost:3030"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3030"])
    log_level: str = "INFO"
    log_json: bool = False
    # Demo clock (see nightjar/clock.py): "today" for walkthroughs and screenshots, e.g. 2025-11-20.
    nightjar_demo_today: date | None = None

    # ── Security ───────────────────────────────────────────────────────────
    secret_key: SecretStr = SecretStr("dev-insecure-change-me-0123456789abcdef")
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    password_min_length: int = 12
    cookie_secure: bool = False
    cookie_domain: str | None = None
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    rate_limit_per_minute: int = 300
    auth_rate_limit_per_minute: int = 10
    ingest_rate_limit_per_minute: int = 600
    login_lockout_failures: int = 5
    login_lockout_minutes: int = 15
    invitation_ttl_days: int = 7
    max_upload_mb: int = 25
    # Ingest API keys (webhook/syslog senders) are per org and stored hashed; this is the header name.
    ingest_key_header: str = "X-Nightjar-Key"

    # ── App database (SQLAlchemy 2; SQLite default, Postgres-ready) ────────
    database_url: str = "sqlite+aiosqlite:///./nightjar-dev.db"
    database_echo: bool = False

    # ── Event backbone (ADR 0001) ─────────────────────────────────────────
    # "nats": JetStream via .tools/nats-server · "memory": in-process fallback (tests, single-process demo)
    bus: Literal["nats", "memory"] = "nats"
    nats_url: str = "nats://127.0.0.1:4230"
    nats_store_dir: Path = REPO_ROOT / "data" / "jetstream"
    stream_max_bytes_raw: int = 256 * 1024 * 1024
    consumer_ack_wait_s: float = 30.0
    consumer_max_deliver: int = 5
    consumer_max_ack_pending: int = 1000

    # ── Hunting store (ADR 0004) ──────────────────────────────────────────
    duckdb_path: Path = REPO_ROOT / "data" / "hunting.duckdb"
    hunting_query_timeout_s: float = 10.0
    hunting_max_rows: int = 5000
    # The API's store-writer consumer and housekeeping loop (clock transitions, approval expiry). Off in tests.
    background_tasks: bool = True

    # ── Agents and tool servers (ADR 0002, ADR 0006) ──────────────────────
    agent_triage_url: str = "http://127.0.0.1:8031"
    agent_enrichment_url: str = "http://127.0.0.1:8032"
    agent_response_url: str = "http://127.0.0.1:8033"
    mcp_url: str = "http://127.0.0.1:8034"
    # "http": agents are separate A2A services · "inline": in-process ASGI transport (tests, single-process demo)
    agents_transport: Literal["http", "inline"] = "http"
    approval_ttl_minutes: int = 60

    # ── AI narration (LiteLLM; offline fake by default) ───────────────────
    llm_fake: bool = True
    llm_model: str = "groq/llama-3.3-70b-versatile"
    llm_fallback_models: list[str] = Field(default_factory=lambda: ["gemini/gemini-2.0-flash"])
    groq_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    llm_timeout_s: float = 20.0

    # ── Data, models, intel ───────────────────────────────────────────────
    data_dir: Path = REPO_ROOT / "data"
    # "bundled": small offline subsets shipped in the package · "downloaded": data/ (make data)
    intel_source: Literal["bundled", "downloaded"] = "bundled"
    rules_source: Literal["bundled", "downloaded"] = "bundled"
    models_dir: Path = BACKEND_ROOT / "nightjar" / "ml" / "artifacts"
    mlflow_tracking_uri: str = f"file:{REPO_ROOT / 'mlruns'}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
