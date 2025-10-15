"""Command line: `python -m nightjar.cli <command>` (wrapped by the Makefile)."""

from __future__ import annotations

import asyncio
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


@app.command()
def migrate() -> None:
    """Apply database migrations (Alembic upgrade head)."""
    upgrade_head()
    typer.echo("database at head")


@app.command()
def seed() -> None:
    """Seed the Halden Logistics demo (idempotent). Builder A implements nightjar.seed.run()."""
    clock.install_from_settings()
    from nightjar.seed import run

    asyncio.run(run())


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Confirm dropping local data")) -> None:
    """Drop local app data, the hunting store and the stream store, then migrate and seed."""
    if not yes:
        raise typer.BadParameter("pass --yes to drop local data")
    clock.install_from_settings()
    from nightjar.seed import reset as do_reset

    asyncio.run(do_reset())


if __name__ == "__main__":
    app()
