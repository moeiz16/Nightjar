"""Nightjar SOC API: application factory (port 8030)."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nightjar import clock
from nightjar.core.config import get_settings
from nightjar.core.errors import install_error_handlers
from nightjar.core.logging import configure_logging
from nightjar.core.middleware import CachePolicyMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from nightjar.core.ratelimit import RateLimitMiddleware

log = structlog.get_logger("nightjar")

# Routers under /api/v1, in contract order.
ROUTERS = (
    "auth",
    "org",
    "invitations",
    "ingest",
    "events",
    "detections",
    "incidents",
    "investigations",
    "actions",
    "reporting",
    "hunting",
    "executive",
    "ueba",
    "settings",
    "audit",
    "system",
)

OPENAPI_TAGS = [
    {"name": "auth", "description": "Sign-in, sessions and organisation switching"},
    {"name": "organisation", "description": "Organisation profile, members, invitations and event-source keys"},
    {"name": "ingest", "description": "Event intake: webhook, syslog-style JSON and file replay"},
    {"name": "events", "description": "Normalised event search"},
    {"name": "detections", "description": "Rule and behaviour detections, detection rules"},
    {"name": "incidents", "description": "Incidents, timelines, affected assets and identities"},
    {"name": "investigations", "description": "Agent investigation runs and their replayable step log"},
    {"name": "actions", "description": "Proposed response actions and human approvals"},
    {"name": "reporting", "description": "Regulatory incident clocks, notices and submissions"},
    {"name": "hunting", "description": "Saved searches and hunting suggestions"},
    {"name": "executive", "description": "Posture, response times and the weekly brief"},
    {"name": "ueba", "description": "Behaviour anomalies and triage scores"},
    {"name": "settings", "description": "Regulator contacts and organisation settings"},
    {"name": "audit", "description": "Tamper-evident audit trail"},
    {"name": "live", "description": "Server-Sent Events"},
    {"name": "health", "description": "Liveness, readiness and metrics"},
]


async def bootstrap_schema() -> None:
    """Local SQLite convenience: dev runs the migrations, tests create the tables directly."""
    import asyncio

    import nightjar.db.models  # noqa: F401  (register all tables)
    from nightjar.db.base import Base
    from nightjar.db.session import get_engine

    if get_settings().environment == "dev":
        from nightjar.cli import upgrade_head

        await asyncio.to_thread(upgrade_head)
        return
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    offset = clock.install_from_settings()
    if s.is_sqlite and s.environment in ("dev", "test"):
        await bootstrap_schema()
    log.info(
        "nightjar_api_started",
        environment=s.environment,
        db="sqlite" if s.is_sqlite else "postgresql",
        bus=s.bus,
        demo_offset_days=offset.days,
    )
    writer = sweeper_task = None
    if s.background_tasks and s.environment != "test":
        from nightjar.hunting.service import start_store_writer
        from nightjar.services import sweeper

        writer = await start_store_writer()
        sweeper_task = sweeper.start()
    try:
        yield
    finally:
        from nightjar.db.session import get_engine
        from nightjar.hunting.api import close_store
        from nightjar.services import sweeper

        await sweeper.stop(sweeper_task)
        if writer is not None:
            await writer.stop()
        close_store()
        await get_engine().dispose()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="Nightjar SOC API",
        version="1.0.0",
        description=(
            "Security operations for mid-size companies: event intake, detection and correlation, agent "
            "investigations with human-approved response, and regulatory incident clocks (NIS2, SEC Form 8-K "
            "Item 1.05, SOCI)."
        ),
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/docs" if not s.is_prod else None,
        redoc_url=None,
    )
    install_error_handlers(app)

    # Starlette applies middleware in reverse order of addition: the last added is outermost.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CachePolicyMiddleware, api_prefix=s.api_prefix)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Last-Event-ID", s.ingest_key_header],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    from nightjar.api.v1.health import router as health_router

    app.include_router(health_router)  # /health, /health/ready, /metrics at the root for probes
    app.include_router(health_router, prefix=s.api_prefix, include_in_schema=False)
    for name in ROUTERS:
        app.include_router(importlib.import_module(f"nightjar.api.v1.{name}").router, prefix=s.api_prefix)
    return app


app = create_app()
