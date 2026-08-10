# Development

## Setup and local runs

Nothing is required beyond the repo. With no API key, the deterministic **`stub`** judgment
runs and everything except the agents' prose executes for real — the Flow, the routers, the
saga, the tool gateway, the Reg Z policy control, retrieval, and citation verification.

```bash
uv sync
uv run pytest tests/                                # Python test suite, no LLM
uv run python run_cli.py --scenario 3               # saga compensation, in reverse
uv run python evals/run_evals.py                    # five headline measures plus diagnostics
```

For the eight agents, set an API key (see [`.env.example`](../.env.example)) and add
`--judgment crew`.

```bash
uv run python run_cli.py --scenario 1               # value acceptance exercised — the $600 never happens
uv run python run_cli.py --scenario 2               # DU says eligible; the overlay is stricter and binds
uv run python run_cli.py --scenario 3               # low appraisal → LTV breach → DENY → compensation
uv run python run_cli.py --scenario 4 --twice       # idempotency: bureau counter stays at 1
uv run python run_cli.py --scenario 1 --policy-attack   # the Reg Z gate under prompt injection

uv run python run_cli.py --scenario 5 --kill-after submit_to_aus   # kill mid-flow…
uv run python run_cli.py --scenario 5                             # …resume from the ledger alone
uv run python run_cli.py --ledger MER-1003          # reconstruct a loan by hand from saga_log
```

## Layout

```
meridian/
├── run_cli.py                          ← consumes core/events.py; no orchestration logic here
├── evals/  run_evals.py golden_set.json report.md
├── tests/  test_core.py test_calc.py test_crews.py test_rag.py test_api.py ...  ← Python test suite, no network
├── data/   scenarios/ guidelines/ cassettes/
├── frontend/                           ← Next.js 16 App Router dashboard
└── src/meridian/
    ├── core/       ← ZERO CrewAI imports. db · idempotency · saga · policy · events · replay
    ├── calc/       ← income · dti · ltv. Deterministic, tested, not agents
    ├── vendors/    ← credit_bureau · aus · amc · pricing · voe
    ├── rag/        ← store.py (numpy cosine, not chromadb) + deterministic retrieval loop
    ├── application/← framework-neutral dashboard/run/evaluation services, shared by CLI and API
    ├── api/        ← thin FastAPI adapter: app.py, models.py, routes/*.py — no orchestration logic
    ├── models.py   ← typed state the routers branch on
    ├── tools.py    ← gatewayed tool calls and conditional menus; saga, policy, and persistence have their own layers
    ├── judgment.py ← the seam: StubJudgment (rules) vs CrewJudgment (agents)
    ├── crews/      ← 4 crews, prompts in config/*.yaml
    └── flow.py     ← OriginationFlow, the deterministic spine
```

`core/`, `calc/`, `vendors/` and `rag/` have **zero CrewAI imports** — stdlib, numpy, openai.
Build order was risk order: if the framework had fought the install, there was still a working
system.

## Dashboard (two processes)

The API and the UI run as two separate local processes; nothing proxies between them in
development, so both ports are reachable directly.

```bash
uv run uvicorn meridian.api.app:app --host 127.0.0.1 --port 8000   # terminal 1 — API on :8000

cd frontend
npm ci               # first time only
npm run dev          # terminal 2 — UI on :3000
```

Or start both processes from one terminal and stop both with `Ctrl+C`:

```bash
cd frontend
npm run dev:stack
npm run dev:stack -- --demo  # also enable local auto-approval controls
```

- **Ports:** API `:8000`, UI `:3000`. Both are configurable — pass `--port` to `uvicorn`, and
  set `NEXT_PUBLIC_MERIDIAN_API_ORIGIN` (browser) / `MERIDIAN_API_ORIGIN` (server components) if
  the API is not on `:8000`.
- **CORS:** the API only allows `http://localhost:3000` and `http://127.0.0.1:3000` by default;
  set `MERIDIAN_CORS_ORIGINS` (comma-separated) for any other origin, including a same-origin
  reverse proxy in front of both processes.
- **Database:** set `MERIDIAN_DB` to point the API at a specific SQLite file; otherwise it uses
  the same default `meridian.db` as `run_cli.py`.
- **Shutdown:** each run/evaluation executes on a daemon worker thread inside the API process,
  independent of any browser connection or SSE stream — closing the browser tab or restarting
  the Next.js dev server does not interrupt or corrupt an in-flight run; only stopping the API
  process itself does.

## Frontend checks

```bash
cd frontend
npm run typecheck
npm run lint
npm test                # Vitest unit/component tests
npm run build           # production build
npm run test:e2e        # cross-stack Playwright suite (starts both processes itself)
npm run test:e2e:smoke  # Firefox/WebKit smoke pass over the stateless specs
```

`npm run test:e2e` and `test:e2e:smoke` start their own isolated API + Next.js dev server pair
against a scratch database (see `frontend/scripts/run_e2e.py`) and tear both down afterward, so
they do not require the two-process setup above to already be running.

## Local PDF intake and OCR

The dashboard accepts native-text and scanned PDFs without sending document content to an LLM or
cloud OCR service. Native text is extracted with PyMuPDF. Image-only pages use a locally installed
Tesseract executable through `pytesseract`; when Tesseract is absent, the review screen reports OCR
as unavailable instead of treating the page as empty.

Set `MERIDIAN_DOCUMENT_DIR` to a private writable directory for encrypted uploaded originals and
draft manifests. The default is `.meridian-documents/` under the process working directory and is
ignored by Git. Originals and manifests use AES-256-GCM. Complete extracted page text is transient,
but selected proposed-field `source_text` values and short provenance excerpts are retained in the
encrypted manifest. For local development, Meridian creates a private `.document-key` beside the
encrypted data. Production deployments must set `MERIDIAN_DOCUMENT_KEY` to a URL-safe base64-encoded
32-byte key supplied by a secrets manager or KMS-backed deployment mechanism; do not copy the local
key into source control or container images. Generate a suitable value with
`python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.

This local feature is still not complete production document management: malware scanning,
authentication, managed key rotation, role-based access, access audit logging, and an enforced
retention/deletion policy are required before using real borrower documents outside a controlled
environment.

← [Back to README](../README.md)
