# Nightjar SOC developer tasks. Everything runs offline: no Docker, no accounts, no keys.
# Ports: web 3030 · API 8030 · agents 8031–8033 · tool servers 8034 · NATS 4230 (monitor 8230).
# On small machines run heavy targets (setup, test, train, eval, data, dev) one at a time.
PY      := backend/.venv/bin/python
BPY     := .venv/bin/python
NPM     := npm --prefix frontend
SCENARIO ?= halden-night
SPEED    ?= 60
LINT     := backend scripts $(wildcard eval)
export MLFLOW_DISABLE_AGENT_HINT := 1

.PHONY: help setup setup-backend setup-ai setup-frontend check-omp lock tools nats api workers agents mcp web web-dev dev \
        migrate seed reset replay test test-backend test-frontend lint format typecheck eval train data smoke stop clean

help:            ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: setup-backend setup-frontend tools  ## Install API + web app + nats-server, create .env, migrate and seed
	@test -f .env || cp .env.example .env
	$(MAKE) migrate seed

setup-backend: check-omp  ## Python 3.13 venv with the pinned lock (+ the package, editable)
	test -d backend/.venv || python3.13 -m venv backend/.venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r backend/requirements.lock
	$(PY) -m pip install -q --no-deps -e "backend[dev]"

setup-ai:        ## Optional: LiteLLM for live cloud narration (LLM_FAKE=false + a free-tier key)
	$(PY) -m pip install -q "litellm==1.109.1"

setup-frontend:  ## Install the web app (exact versions; package-lock.json once it exists)
	@if [ -f frontend/package-lock.json ]; then $(NPM) ci; else $(NPM) install; fi

check-omp:       ## LightGBM needs the OpenMP runtime on macOS
	@if [ "$$(uname -s)" = "Darwin" ] && [ ! -f /opt/homebrew/opt/libomp/lib/libomp.dylib ] && [ ! -f /usr/local/opt/libomp/lib/libomp.dylib ]; then \
	  echo "LightGBM needs libomp on macOS: run 'brew install libomp' and re-run make setup."; exit 1; fi

lock:            ## Re-resolve core + dev dependencies into backend/requirements.lock
	cd backend && $(BPY) -m pip install -q --dry-run --ignore-installed --report /tmp/nightjar-resolve.json -e ".[dev]"
	$(PY) scripts/write_lock.py /tmp/nightjar-resolve.json backend/requirements.lock

tools:           ## Download the pinned nats-server binary into .tools/ (checksum verified)
	$(PY) scripts/fetch_nats.py

nats:            ## NATS with JetStream on :4230 (store in data/jetstream)
	@test -x .tools/nats-server || $(MAKE) tools
	@mkdir -p data/jetstream
	.tools/nats-server -js -sd data/jetstream -a 127.0.0.1 -p 4230 -m 8230

api:             ## API on :8030 (owns the hunting store; runs the store writer)
	cd backend && $(BPY) -m uvicorn nightjar.main:app --port 8030 --no-access-log

workers:         ## Pipeline workers: normalise, detect, UEBA, score, correlate, dispatch, orchestrate, jobs
	cd backend && $(BPY) -m nightjar.workers run

agents:          ## Triage :8031, enrichment :8032, response :8033 in one process (serve triage|enrichment|response for one agent)
	cd backend && $(BPY) -m nightjar.agents serve all

mcp:             ## Tool servers (intel, assets, identity, ticketing, response) on :8034
	cd backend && $(BPY) -m nightjar.mcp_servers serve

web-dev:         ## Vite dev server on :3030 (proxies /api to :8030)
	$(NPM) run dev

web:             ## Production build of the web app, served on :3030
	$(NPM) run build && $(NPM) run preview

dev:             ## Everything: NATS + API + workers + agents + tool servers + web (Ctrl-C stops all)
	@$(MAKE) -j6 nats api workers agents mcp web-dev

migrate:         ## Apply database migrations
	cd backend && $(BPY) -m nightjar.cli migrate

seed:            ## Seed Halden Logistics + Brightline Couriers (idempotent; respects NIGHTJAR_DEMO_TODAY)
	cd backend && $(BPY) -m nightjar.cli seed

reset:           ## Drop app DB, hunting store and stream store, then migrate and seed
	cd backend && $(BPY) -m nightjar.cli reset --yes

replay:          ## Replay a scenario into the running system (SCENARIO=halden-night SPEED=60)
	cd backend && $(BPY) -m nightjar.workers replay $(SCENARIO) --speed $(SPEED)

test: test-backend test-frontend  ## All tests

test-backend:    ## API, platform, pipeline, agent and ML tests (in-process bus; no servers needed)
	cd backend && $(BPY) -m pytest

test-frontend:   ## Web app type checks, lint, unit and flow tests, production build
	$(NPM) run check && $(NPM) run lint && $(NPM) test && $(NPM) run build

lint:            ## Lint and format checks (ruff)
	$(PY) -m ruff check $(LINT)
	$(PY) -m ruff format --check $(LINT)

format:          ## Apply ruff formatting and safe fixes
	$(PY) -m ruff format $(LINT)
	$(PY) -m ruff check --fix $(LINT)

typecheck:       ## mypy (backend) + svelte-check (web)
	cd backend && $(BPY) -m mypy nightjar
	$(NPM) run check

eval:            ## Evaluation gates → eval/RESULTS.md (detection, UEBA, triage, agents, latency, safety)
	cd backend && $(BPY) ../eval/run.py

train:           ## Train the triage classifier and UEBA models (CPU; tracked in mlruns/)
	cd backend && $(BPY) -m nightjar.ml.train all

data:            ## Download public datasets into data/ (~56 MB transferred, ~152 MB on disk)
	$(PY) scripts/fetch_data.py

smoke:           ## Re-verify the toolchain (NATS, A2A, MCP, Sigma, DuckDB + ML); see docs/adr
	@test -x .tools/nats-server || $(MAKE) tools
	$(PY) scripts/smoke/nats_smoke.py
	$(PY) scripts/smoke/a2a_smoke.py
	$(PY) scripts/smoke/mcp_smoke.py
	$(PY) scripts/smoke/sigma_smoke.py
	$(PY) scripts/smoke/store_ml_smoke.py

stop:            ## Stop every Nightjar SOC process started from this repo
	-pkill -f 'uvicorn nightjar.main:app' ; pkill -f 'nightjar.workers' ; pkill -f 'nightjar.agents' ; \
	 pkill -f 'nightjar.mcp_servers' ; pkill -f '.tools/nats-server' ; pkill -f 'vite.*3030' ; true
	@for p in 3030 8030 8031 8032 8033 8034 4230; do lsof -ti :$$p >/dev/null 2>&1 && echo "port $$p still in use" || true; done

clean:           ## Remove caches and build output (keeps data/, .tools/ and the venv)
	find $(LINT) -path '*/.venv' -prune -o -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .mypy_cache .pytest_cache backend/.coverage backend/htmlcov frontend/.svelte-kit frontend/build eval/out
