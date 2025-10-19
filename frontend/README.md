# Nightjar SOC web app (SvelteKit)

Owner: builder C. Scaffold only; the design concept lives in `DESIGN.md` (to be written before any UI code, per
the playbook) and must follow `~/Work/_playbook/DESIGN_DIRECTIONS.md` §Nightjar.

- Node 21.7 (`.nvmrc`), exact pins (`.npmrc save-exact`), Tailwind 4.1 via `@tailwindcss/vite`.
- `npm run dev` → http://localhost:3030 with `/api` proxied to the API on :8030 (`NIGHTJAR_API_URL` overrides).
- `npm run build && npm run preview` → production build served on :3030 with the same proxy;
  `npm run start` runs the adapter-node server (put a reverse proxy in front for `/api`).
- `npm run check` → svelte-check (types).
- API: `docs/API_CONTRACT.md` Part 1; `src/lib/api/client.ts` handles CSRF and RFC 7807 errors.
