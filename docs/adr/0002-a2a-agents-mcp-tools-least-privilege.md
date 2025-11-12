# ADR 0002: Three A2A agents, MCP tool servers and least privilege

**Status:** accepted

## Context
The product needs independent triage, enrichment and response agents that cooperate over an open protocol and use
tools through servers we implement, with least-privilege access and a replayable record of every decision. A
single in-process "agent framework" would hide the boundaries we need to show and to enforce.

## Decision
- **Agents** are three services built on the official **`a2a-sdk` 1.3** (A2A protocol 1.0, JSON-RPC binding), each
  with an Agent Card at `/.well-known/agent-card.json` and skills `triage_incident`, `enrich_incident`,
  `plan_response` / `execute_action`. Payloads are DataParts carrying the contract docs. A small orchestrator in
  the workers process hands incident tasks to triage; triage delegates to enrichment and response over A2A.
- **Peer authentication**: the RPC endpoint requires a caller token; each agent accepts only its listed callers.
  Cards stay public.
- **Tools** are five servers on the official **`mcp` SDK 2.3** (`MCPServer`): `intel`, `assets`, `identity`,
  `ticketing`, `response`, served by one host process over Streamable HTTP (tests connect in-process). The SDK's
  bearer `TokenVerifier` maps each agent's token to scopes; every tool checks its scope inside the server, and tool
  lists are filtered per caller. Scopes: triage and enrichment read only (`intel:read`, `assets:read`,
  `identity:read`); response adds `tickets:*`, `response:propose`, `response:execute`.
- **Records**: every agent appends its own steps (tool calls, results, hand-offs, verdicts, guardrail events) to
  the run log through the platform interface, with the A2A task id and an input hash.

Verified on Python 3.13, macOS arm64:
- `scripts/smoke/a2a_smoke.py`: two agents on real ports, each serving a card (camelCase JSON, parsed by
  `A2ACardResolver`); a task reaches `TASK_STATE_COMPLETED`; triage delegates to enrichment over A2A and returns
  its structured result; the peer-token gate returns 401 to unknown callers; the same apps work over an in-process
  ASGI transport (used by the test suite).
- `scripts/smoke/mcp_smoke.py`: tool listing with JSON schemas and annotations (`destructive_hint`), structured
  output; Streamable HTTP with `TokenVerifier` → 401 without a token, a triage token can read intel but is refused
  the destructive tool (`ToolError` from the scope check), a responder token without an approval token is not
  executed, with one it is; the stdio transport also works.

## Consequences
- Implementation notes: a2a-sdk 1.x uses protobuf types (`TaskState.TASK_STATE_COMPLETED`, `Role.ROLE_USER`);
  DataPart numbers come back as floats; `create_client(card, ClientConfig(httpx_client=...))` injects auth headers
  and test transports. mcp 2.x renamed `FastMCP` to `MCPServer`, uses `httpx2` for its HTTP client, snake_case
  model fields (`structured_content`, `destructive_hint`), and `list_tools()` returns a result object (`.tools`).
- Five processes for the agent tier is too heavy for 8 GB, so the MCP servers share one host and, by default
  (`make agents`, i.e. `serve all`), the three agents share one process while keeping separate ports, cards and
  tokens. `serve triage|enrichment|response` runs each in its own process.
- No agent framework dependency: policies are plain Python, which keeps tool choice deterministic (ADR 0006).
