"""Command line: `python -m nightjar.cli <command>` (wrapped by the Makefile)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from alembic.config import Config

from nightjar import clock

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Nightjar SOC operations")
BACKEND = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade_head() -> None:
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def downgrade_base() -> None:
    from alembic import command

    command.downgrade(_alembic_config(), "base")


@app.command()
def migrate() -> None:
    """Apply database migrations (Alembic upgrade head)."""
    upgrade_head()
    typer.echo("database at head")


@app.command()
def seed(
    fixtures: bool = typer.Option(
        False,
        "--fixtures",
        help="Insert last night's incidents as fixtures instead of replaying them through the pipeline",
    ),
) -> None:
    """Seed Halden Logistics and Brightline Couriers (idempotent; respects NIGHTJAR_DEMO_TODAY). Stop the API
    first: the seed writes the hunting store, which has a single writer."""
    clock.install_from_settings()
    upgrade_head()
    from nightjar.seed import run

    out = asyncio.run(run(use_pipeline=not fixtures))
    typer.echo(json.dumps(out, indent=2))


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="Confirm dropping local data"),
    fixtures: bool = typer.Option(False, "--fixtures", help="Seed last night from fixtures"),
) -> None:
    """Drop local app data, the hunting store and the stream store, then migrate and seed."""
    if not yes:
        raise typer.BadParameter("pass --yes to drop local data")
    clock.install_from_settings()
    from nightjar.seed import reset as do_reset

    out = asyncio.run(do_reset(use_pipeline=not fixtures))
    typer.echo(json.dumps(out, indent=2))


@app.command()
def replay(
    scenario: str = typer.Argument("halden-night"),
    speed: float = typer.Option(60.0, "--speed", min=1, max=600, help="Replay speed (60 = one hour per minute)"),
    org: str = typer.Option("halden-logistics", "--org", help="Organisation slug"),
    inline: bool = typer.Option(False, "--inline", help="Run the pipeline in this process (stop the API first)"),
) -> None:
    """Replay a scenario: through the running workers (default) or in-process with --inline."""
    clock.install_from_settings()

    async def _go() -> dict:
        import uuid

        from nightjar.db.session import session_scope
        from nightjar.seed.builders import org_by_slug
        from nightjar.services.jobs import create_job, job_out

        async with session_scope() as db:
            o = await org_by_slug(db, org)
            if o is None:
                raise typer.BadParameter(f"no organisation {org!r}; run `seed` first")
            org_id = o.id
            job = (
                None
                if inline
                else await create_job(
                    db, org_id=org_id, kind="replay", payload={"scenarioId": scenario, "speed": speed}, created_by=None
                )
            )
            job_doc = job_out(job) if job else None
        if inline:
            from nightjar.workers.inline import run_scenario

            refs = await run_scenario(org_id, scenario, end_at=None, auto_investigate=True)
            return {"incidents": [getattr(r, "reference", str(r)) for r in refs or []]}
        from nightjar.stream import api as stream

        await stream.start_replay(org_id, scenario, speed=speed, job_id=uuid.UUID(job_doc["id"]))  # type: ignore[index]
        return {"job": job_doc}

    typer.echo(json.dumps(asyncio.run(_go()), indent=2, default=str))


@app.command()
def serve(
    port: int = typer.Option(8030, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Run the API (owns the hunting store and runs its store writer)."""
    import uvicorn

    uvicorn.run("nightjar.main:app", host=host, port=port, proxy_headers=True, access_log=False)


if __name__ == "__main__":
    app()
