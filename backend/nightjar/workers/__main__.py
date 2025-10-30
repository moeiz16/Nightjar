"""`python -m nightjar.workers setup|run|replay|dlq` (API contract Part 4)."""

from __future__ import annotations

import asyncio
import signal
import uuid

import typer

from nightjar import clock

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Nightjar SOC pipeline workers")


@app.command()
def setup() -> None:
    """Create the streams and durable consumers (idempotent)."""

    async def go() -> None:
        from nightjar.stream.runtime import get_bus, reset_bus

        bus = await get_bus()
        stats = await bus.stats()
        typer.echo("streams: " + ", ".join(f"{s.name} ({len(s.consumers)} consumers)" for s in stats))
        await reset_bus()

    asyncio.run(go())


@app.command()
def run(only: str = typer.Option("", help="Comma-separated consumers to run (default: all)")) -> None:
    """Run the pipeline stages until interrupted."""
    clock.install_from_settings()

    async def go() -> None:
        from nightjar.core.config import get_settings
        from nightjar.core.logging import configure_logging
        from nightjar.stream.runtime import get_bus, reset_bus
        from nightjar.workers.pipeline import Pipeline, run_workers

        s = get_settings()
        configure_logging(s.log_level, s.log_json)
        bus = await get_bus()
        watch = getattr(bus, "watch_max_deliveries", None)
        if watch:
            await watch()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        pipeline = Pipeline(bus)
        names = [n.strip() for n in only.split(",") if n.strip()] or None
        typer.echo(f"workers running on {bus.mode} bus ({'all stages' if not names else ', '.join(names)})")
        try:
            await run_workers(pipeline, stop=stop, consumers=names)
        finally:
            await reset_bus()

    asyncio.run(go())


@app.command()
def replay(
    scenario: str = typer.Argument("halden-night"),
    speed: float = typer.Option(60.0),
    org: str = typer.Option("", help="Organisation id (default: the first sample organisation)"),
) -> None:
    """Replay a scenario into the running system (publishes to the raw stream)."""
    clock.install_from_settings()

    async def go() -> None:
        from nightjar.stream.runtime import reset_bus
        from nightjar.workers.jobs import replay_scenario

        org_id = uuid.UUID(org) if org else await _sample_org()
        result = await replay_scenario(org_id, scenario, speed=speed)
        typer.echo(f"replayed {scenario}: {result}")
        await reset_bus()

    asyncio.run(go())


async def _sample_org() -> uuid.UUID:
    from sqlalchemy import select

    from nightjar.db.models.platform import Organization
    from nightjar.db.session import session_scope

    async with session_scope() as db:
        org = (
            await db.execute(select(Organization.id).where(Organization.is_sample.is_(True)).limit(1))
        ).scalar_one_or_none()
    if org is None:
        raise typer.BadParameter("no sample organisation: run make seed or pass --org")
    return org


@app.command()
def dlq() -> None:
    """List dead-lettered messages."""

    async def go() -> None:
        from nightjar.stream.api import list_dlq

        items, total = await list_dlq(page=1, page_size=50)
        typer.echo(f"{total} dead letters")
        for i in items:
            typer.echo(
                f"  #{i['seq']} {i['stream']}/{i['consumer']} {i['subject']} x{i['deliveries']}: {i['reason'][:100]}"
            )

    asyncio.run(go())


if __name__ == "__main__":
    app()
