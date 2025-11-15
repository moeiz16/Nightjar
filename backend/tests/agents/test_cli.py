"""The process entry points parse the commands the Makefile and docs use."""

from __future__ import annotations

from typer.testing import CliRunner


def test_agents_serve_is_a_subcommand() -> None:
    from nightjar.agents.__main__ import app

    r = CliRunner().invoke(app, ["serve", "--help"])
    assert r.exit_code == 0 and "triage, enrichment and response" in r.output


def test_tool_servers_and_workers_expose_their_commands() -> None:
    from nightjar.mcp_servers.__main__ import app as mcp
    from nightjar.workers.__main__ import app as workers

    assert CliRunner().invoke(mcp, ["serve", "--help"]).exit_code == 0
    assert CliRunner().invoke(workers, ["run", "--help"]).exit_code == 0
