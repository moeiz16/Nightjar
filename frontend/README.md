# Nightjar SOC web app (SvelteKit)

The console, the landing page, sign-in and the CISO brief, built to `DESIGN.md` ("the night watch"): a ⌘K-first
tiled console with the regulator clock bar on top and a mono status line, the baton timeline with Replay, Hold to
authorise, and a separate dawn-washed morning brief.

- Node 21.7 (`.nvmrc`), exact pins (`.npmrc save-exact`), Tailwind 4.1 via `@tailwindcss/vite`, Geist and Geist Mono
  bundled offline (`@fontsource-variable/*`). Motion uses Svelte built-ins only (`Tween`, `Spring`, `crossfade`,
  `flip`, `prefersReducedMotion`).
- `npm run dev` → http://localhost:3030 with `/api` proxied to the API on :8030 (`NIGHTJAR_API_URL` overrides). The
  **real API is the default**.
- `npm run dev:mock` / `npm run preview:mock` → the same app against an in-browser mock of the API
  (`PUBLIC_API_MOCK=1`): Halden Logistics sample data from contract Part 6, the same permissions, conflicts and SSE
  events. Sign in with any demo role on the sign-in page (password `Nightjar-demo-2025!`).
- `npm run build && npm run preview` → production build on :3030 with the same proxy; `npm run start` runs the
  adapter-node server (put a reverse proxy in front for `/api`).
- `npm run check` (svelte-check), `npm run lint` (ESLint), `npm test` (Vitest: client, clock maths, hold, untrusted
  text, palette, hunting, rules, brief and end-to-end flows through the mock API).

## Layout

| Path | What |
|---|---|
| `src/lib/api/` | `client.ts` (CSRF, RFC 7807, refresh, server clock offset), `types.ts` (contract Part 1), `endpoints.ts`, `stream.ts` (SSE), `mock/` (dev and test only) |
| `src/lib/time/clock.ts` | every deadline and countdown: NIS2, SEC 8-K Item 1.05 (US federal holidays), SOCI, states, labels |
| `src/lib/components/shell/` | `ClockBar`, `ClockCell`, `Tick`/`Digit`, `StatusLine`, `CommandPalette`, `Pane` |
| `src/lib/components/console/` | `EventRiver`, `IncidentHeader`, `BatonTimeline`, `StepDrawer`, `ApprovalCard`, `HoldButton`, `DecisionSheet`, `HuntPanel`, `BehaviourPanel`, `ActivityPanel`, `Untrusted` |
| `src/routes/` | `/` landing, `/sign-in`, `/invite/[token]`, `(app)/…` the console, `(read)/brief…` the brief and weekly report |

## Contract notes

- No inventory list routes in v1: `/assets` and `/identities` are tried first; on 404 the app assembles them from
  incidents' `assetsDetail` / `identitiesDetail`.
- The executive cannot read `/incidents/{id}/detections`, so the notice page cites the incident timeline as evidence
  for the CISO.
- "Disagree" with a teammate's step, "Add to incident" from a hunt and "Not yet: decide by…" are recorded as
  incident notes; there are no dedicated endpoints. "Mark as expected" and "Lower the noise" have no endpoint yet.
- River minute totals are estimated from `eventsPerMinute` plus `river.batch` counts (no per-minute totals).
- Step details are read defensively: `lookedAt`/`why` when present, otherwise the preceding lookups and the verdict
  summary.
