"""`python -m nightjar.mcp_servers serve`: the five tool servers on :8034 at /mcp/<server>."""

from __future__ import annotations

from urllib.parse import urlparse

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Nightjar SOC tool servers (MCP)")


@app.command()
def serve() -> None:
    import uvicorn

    from nightjar import clock
    from nightjar.core.config import get_settings
    from nightjar.mcp_servers.host import ToolHost

    clock.install_from_settings()
    url = get_settings().mcp_url
    u = urlparse(url)
    typer.echo(f"tool servers on {url}/mcp/{{intel,assets,identity,ticketing,response}}")
    uvicorn.run(ToolHost(url), host=u.hostname or "127.0.0.1", port=u.port or 8034, log_level="warning")


@app.command()
def tools() -> None:
    """Print each server's tools and the scope they need."""
    from nightjar.mcp_servers.servers import build_all

    for name, srv in build_all("http://127.0.0.1:8034").items():
        typer.echo(f"{name}: " + ", ".join(f"{t} ({s})" for t, s in srv.tool_scopes.items()))


if __name__ == "__main__":
    app()
