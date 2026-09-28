# Hermes — ABA Fusion Platform Audit

AI-driven multi-platform security testing agent for the ABA Fusion staging
platforms (9 targets under `*.abafusion.ai`).

## Layout

```

├── ai_agent/
│   ├── agent.py            # main entry point (forwards to core.runner)
│   ├── agent_fast.py       # deprecated wrapper -> `agent.py --quick`
│   ├── agent_ultra.py      # deprecated wrapper -> `agent.py --quick`
│   ├── token_manager.py    # JWT login + auto-refresh (stg-login.abafusion.ai)
│   ├── core/               # shared library (stdlib only)
│   │   ├── config.py       #   platform registry, fuzz payloads, paths
│   │   ├── http_client.py  #   request helper + discovery probe
│   │   ├── classifier.py   #   response -> (finding_type, severity)
│   │   ├── memory.py       #   SQLite persistence (platforms/findings/patterns)
│   │   ├── runner.py       #   unified pipeline: auth -> discover -> fuzz -> report
│   │   └── token_manager.py#   re-export of top-level module
│   ├── llm/                # (inside core/) provider-agnostic LLM layer
│   ├── data/               # runtime artifacts (memory.db, reports/, screenshots/) — gitignored
│   └── KNOWLEDGE_*.md      # documentation of platforms, memory schema, payloads
├── tests/                  # pytest suite (offline) + live platform tests
├── tools/                  # llm_provider_test.py — live provider/model validation
└── webui/                  # FastAPI web interface (app.py + static/index.html)
                            #   run: python webui/app.py  -> http://127.0.0.1:8787

## Quick start (one command)

Windows CMD:
```bat
setup.bat        :: creates .venv, installs requirements + Playwright Chromium
run.bat          :: starts the Web UI -> http://127.0.0.1:8787
```

Linux/macOS:
```bash
./setup.sh       # creates .venv, installs requirements + Playwright Chromium
./run.sh         # starts the Web UI -> http://127.0.0.1:8787
```

`run.bat` / `run.sh` re-syncs dependencies from `requirements.txt` on every
start, so after pulling new code you never need to reinstall manually — just
run it again and the venv stays up to date automatically.

Before running tests that hit staging, configure credentials/env vars.
**Recommended: a `.env` file in the project root** — the agent auto-loads it
on startup (no `set`/`export` needed). `setup.bat`/`setup.sh` create it for
you from `env.example`; edit it with your real values:

```ini
ABA_USERNAME=test_02
ABA_PASSWORD=<staging password>
ABA_TENANT=arma
GROQ_API_KEY=gsk_...
NVIDIA_API_KEY=nvapi-...
ABA_LLM_PROVIDER=groq
ABA_LLM_MODEL=qwen/qwen3.8-27b
```

Alternatively you can still set them as environment variables in CMD:
```bat
set ABA_USERNAME=test_02
set ABA_PASSWORD=<staging password>
set GROQ_API_KEY=gsk_...        &  set NVIDIA_API_KEY=nvapi-...
set ABA_LLM_PROVIDER=groq
```
(.env never overrides already-set environment variables; `.env` is gitignored.)

## Usage (CLI shortcuts, optional)

```bash
python ai_agent/agent.py            # full run
python ai_agent/agent.py --quick    # reduced matrix
python ai_agent/agent.py --no-fuzz  # discovery only
python ai_agent/agent.py --fresh    # reset memory DB
```

Data paths are absolute (resolved from the package root) and can be
overridden with `ABA_AGENT_DATA_DIR`.

## Tests

```bash
pip install -r requirements.txt
playwright install chromium              # one-time browser download
python -m pytest                       # offline unit + integration tests (mocked I/O)

# Live platform tests — Playwright is the runner for these (NOT pytest):
python tests/test_platforms_playwright.py           # render-check all 9 in headless Chromium + JSON report
python tests/test_platforms_playwright.py --fuzz    # + authenticated DP API fuzzing via Playwright request context
python tests/test_platforms_playwright.py --headed  # watch the browser

# Legacy urllib-based live sweep (kept as a no-browser fallback):
ABA_LIVE_TESTS=1 python -m pytest tests/test_platforms_live.py -v
```

## Notes

- Runtime has zero third-party dependencies (Python stdlib only).
- Only staging environments are targeted; keep credentials in env/config if
  you fork this — `token_manager.CREDS` currently holds a hardcoded test account.
