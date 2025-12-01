# Nightjar SOC web app (SvelteKit)

The console, the landing page, sign-in and the CISO brief, built to `DESIGN.md` §0 ("the shift chart"): a shift
axis across the top of every screen with the regulator deadlines folded at its end, full-width bands, response stages
as lanes with one stepped line per incident, the turnover (the handover sheet) as home, a tmux-style watch bar at the
bottom, the baton timeline with Replay, Hold to authorise, and the dawn morning brief.

- Node 21.7 (`.nvmrc`), exact pins (`.npmrc save-exact`), Tailwind 4.1 via `@tailwindcss/vite`.
- Component system: **Skeleton** (`@skeletonlabs/skeleton` 3.2.2 + `@skeletonlabs/skeleton-svelte` 1.5.3) on the
  custom `nightjar` theme in `src/lib/theme/nightjar.css`; Tailwind's `dark` variant follows `<html data-theme>`.
- Type: **Recursive** bundled offline (`@fontsource-variable/recursive`, the weight + MONO axis file): Sans Linear
  for reading, Mono Linear for logs, indicators, timestamps and clocks. Motion uses Svelte built-ins only (`Tween`,
  `Spring`, `crossfade`, `flip`, `prefersReducedMotion`).
- `npm run dev` → http://localhost:3030 with `/api` proxied to the API on :8030 (`NIGHTJAR_API_URL` overrides). The
  **real API is the default**.
- `npm run dev:mock` / `npm run preview:mock` → the same app against an in-browser mock of the API
  (`PUBLIC_API_MOCK=1`): Halden Logistics sample data from contract Part 6, the same permissions, conflicts and SSE
  events. Sign in with any demo role on the sign-in page (password `Nightjar-demo-2025!`).
- `npm run build && npm run preview` → production build on :3030 with the same proxy; `npm run start` runs the
  adapter-node server (put a reverse proxy in front for `/api`).
- `npm run check` (svelte-check), `npm run lint` (ESLint), `npm test` (Vitest: client, clock maths, hold, untrusted
  text, palette, hunting, rules, brief, the shift chart's layout, the shell model, the band primitives and end-to-end
  flows through the mock API).

## Layout

| Path | What |
|---|---|
| `src/lib/api/` | `client.ts` (CSRF, RFC 7807, refresh, server clock offset), `types.ts` (contract Part 1), `endpoints.ts`, `stream.ts` (SSE), `mock/` (dev and test only) |
| `src/lib/time/clock.ts` | every deadline and countdown: NIS2, SEC 8-K Item 1.05 (US federal holidays), SOCI, states, labels |
| `src/lib/theme/nightjar.css` | the Skeleton theme: surface, primary (teal), secondary (lavender), warning, error, success and tertiary scales from the night and day tokens |
| `src/lib/util/shift.ts`, `src/lib/stores/shift.svelte.ts` | the shift window, the scale, each incident's path through the lanes, intake bars and axis marks; the store that feeds them from the shift log |
| `src/lib/components/ui/` | `Band` (the one container), `Choice` (Skeleton Segment), `Sheet` (Skeleton Modal: bottom sheet or dialog) |
| `src/lib/components/shell/` | `ShiftAxis`, `WatchBar` (Skeleton Navigation bar), `Tick`/`Digit`, `CommandPalette`, `SettingsNav` |
| `src/lib/components/console/` | `ShiftChart`, `Turnover`, `ClockList`, `EventRiver`, `IncidentHeader`, `BatonTimeline`, `StepDrawer`, `ApprovalCard`, `HoldButton`, `DecisionSheet`, `HuntPanel`, `BehaviourPanel`, `ActivityPanel`, `Untrusted` |
| `src/routes/` | `/` landing, `/sign-in`, `/invite/[token]`, `(app)/…` the console, `(read)/brief…` the brief and weekly report |

## Contract notes

- Assets and identities come from `GET /assets` and `GET /identities` (contract §1.8a).
- Least privilege for evidence: people with `detections:read` see the detections' event copies; the CISO (and
  anyone else) reads `GET /incidents/{id}/evidence`, a summary without matched fields or log text.
- "Disagree" posts `/investigations/{runId}/steps/{seq}/feedback`; "add to incident" from a hunt posts
  `/incidents/{id}/evidence`; "Not yet: decide by…" posts `/incidents/{id}/reporting/{regime}/defer` and the
  deadline gutter shows "decide by …"; "Lower the noise" and "Mark as expected" create per-org exceptions (`/suppressions`).
- The hunting parser emits the normaliser's action names (`logon`, `logon-failed`, `mfa-push-denied`,
  `inbox-rule-created`, `s3-get-object`, …), the same names saved searches and suggestions use.
- River minute totals are exact: `GET /events/river` returns `minutes`, and `river.batch` counts add to them.
- The business clock is the server's: `Me.clock.now` and the `X-Nightjar-Now` header set the offset, so a demo
  date (and time) reads the same in the browser as on the server.
- Step details are read defensively: `lookedAt`/`why` when present, otherwise the preceding lookups and the verdict
  summary.
