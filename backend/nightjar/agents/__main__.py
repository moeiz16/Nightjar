"""`python -m nightjar.agents serve [triage|enrichment|response|all]`: triage :8031, enrichment :8032, response
:8033. `all` (the default) hosts the three agents in one process on their separate ports; name one agent to run it
on its own."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import typer

from nightjar import clock

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Nightjar SOC agents (A2A)")


@app.callback()
def main() -> None:
    """Keeps `serve` a named subcommand (a one-command Typer app would otherwise take `serve` as the argument)."""


@app.command()
def serve(which: str = typer.Argument("all")) -> None:
    """Serve the triage, enrichment and response agents (`all`) or one of them."""
    import uvicorn

    from nightjar.agents.a2a import agent_url
    from nightjar.agents.service import build_agent_app

    clock.install_from_settings()
    names = ["triage", "enrichment", "response"] if which == "all" else [which]

    async def go() -> None:
        servers = []
        for n in names:
            u = urlparse(agent_url(n))
            cfg = uvicorn.Config(
                build_agent_app(n), host=u.hostname or "127.0.0.1", port=u.port or 8031, log_level="warning"
            )
            servers.append(uvicorn.Server(cfg))
        typer.echo("agents: " + ", ".join(f"{n} {agent_url(n)}" for n in names))
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(go())


if __name__ == "__main__":
    app()
