# Documentation Index — File-by-File Map

The project was executed as **three working branches/sessions** on top of one repo:

| Branch / session | Focus |
|---|---|
| **Branch 1 — `main` (legacy layout)** | Original duplicated scripts (`agent.py`, `agent_fast.py`, `agent_ultra.py`) under `hermes/aba_fusion_platform_audit/`; hardcoded creds; nested data-dir bug. Superseded — kept only for history. |
| **Branch 2 — `ai-agent-v2` (Day 1 build-out)** | Everything below marked *Day 1*: core/ library, tests, LLM providers, multi-dimensional suite, Web UI, .env support, bug-fix waves §11/§14, flattening & GitHub sync. |
| **Branch 3 — current session (hardening + model picker)** | Offline-hang fix in `core/llm/client.py`, offline-test hardening, **provider → available-models list** feature (`base.list_models`, `providers.list_provider_models`, `/api/models`, UI model dropdown), and this per-file day documentation. |

Almost every file was born on **Day 1**; the "Notes" column records later-session
changes ("TODAY" = branch 3). See also `docs/days/day1.md` … `day3.md`.

| File | Day | What it does / history |
|---|---|---|
| `ai_agent/agent.py` | Day 1 | Single CLI entry point for the fuzz agent (v1 refactor; deprecated shims agent_fast.py / agent_ultra.py forward to it). |
| `ai_agent/agent_fast.py` | Day 1 | Deprecated shim -> `agent.py --quick`. |
| `ai_agent/agent_ultra.py` | Day 1 | Deprecated shim -> `agent.py --quick`. |
| `ai_agent/core/config.py` | Day 1 | Platform registry (9 ABA Fusion staging targets) + absolute paths; later (Day 1 §13) wired to load `.env` first. |
| `ai_agent/core/http_client.py` | Day 1 | Stdlib HTTP client with discovery probe; Day 1 §11 added cooperative abort (`set_abort_check`/`check_abort`) powering the UI Stop button. |
| `ai_agent/core/classifier.py` | Day 1 | Response classifier decision table (vuln/info/false-positive/noise). |
| `ai_agent/core/memory.py` | Day 1 | SQLite memory: platforms / findings / patterns with confidence caps. |
| `ai_agent/core/token_manager.py` | Day 1 | JWT login, expiry parsing, cached token + auto re-login. |
| `ai_agent/token_manager.py` | Day 1 | Legacy top-level token manager kept for import compatibility. |
| `ai_agent/core/runner.py` | Day 1 | Unified pipeline (auth -> discovery -> fuzz -> report) replacing the three script variants; Day 1 §6 hooked in optional LLM triage. |
| `ai_agent/core/__init__.py` | Day 1 | Package marker. |
| `ai_agent/core/llm/__init__.py` | Day 1 | LLM package exports (`LLMClient`, helpers). |
| `ai_agent/core/llm/base.py` | Day 1 | BaseProvider plumbing: retries, auth styles, User-Agent fix for Groq/Cloudflare (§7), SSE `_post_sse` for NIM reasoning models (§11). TODAY: added generic `list_models()` (GET /models) so the UI can show per-provider model catalogs. |
| `ai_agent/core/llm/providers.py` | Day 1 | 19+ provider registry (OpenAI-compatible factory + Anthropic/Gemini natives + local servers). TODAY: added Anthropic/Gemini model-list overrides, `CURATED_MODELS` fallbacks and `list_provider_models()` used by `/api/models`. |
| `ai_agent/core/llm/client.py` | Day 1 | LLMClient facade: provider selection, fallback chain, chat/chat_json/status. Day-2 session: fixed offline hang — keyless local providers are no longer auto-detected unless an explicit base URL is configured. |
| `ai_agent/core/planner.py` | Day 1 | Prompt interpretation: LLM planner with keyword fallback (§9 Web UI). |
| `ai_agent/core/suite.py` | Day 1 | Multi-dimensional suite: performance / limits / functionality / logic / security (§8). §14 fixed `_timed_request` signature crash. |
| `ai_agent/core/ux_review.py` | Day 1 | Playwright screenshots + DOM heuristics sent to a vision LLM for UX commentary (§8). |
| `ai_agent/core/live.py` | Day 1 | Headed-Chromium "watch the agent work" mode (`--live`, `--slowmo`) (§5). |
| `ai_agent/KNOWLEDGE_FUZZ.md` | Day 1 | Fuzzing knowledge base: payload categories & expectations. |
| `ai_agent/KNOWLEDGE_MEMORY.md` | Day 1 | Memory/schema knowledge notes. |
| `ai_agent/KNOWLEDGE_PLATFORMS.md` | Day 1 | Per-platform notes (routes, auth quirks, Forge path fix §11). |
| `tests/conftest.py` | Day 1 | Pytest fixtures; keeps ambient env from leaking into offline tests. |
| `tests/test_config.py` | Day 1 | Registry/path integrity tests. |
| `tests/test_classifier.py` | Day 1 | Parametrized classifier decision-table tests. |
| `tests/test_memory.py` | Day 1 | Memory CRUD/upsert/confidence-cap tests. |
| `tests/test_token_manager.py` | Day 1 | JWT lifecycle tests with mocked urlopen. |
| `tests/test_llm.py` | Day 1 | LLM layer unit tests (providers, client chain, graceful degradation). |
| `tests/test_runner_offline.py` | Day 1 | End-to-end runner test with all I/O mocked. Day-2 session: pinned `ABA_LLM_PROVIDER=auto` so `.env` cannot enable live network calls in offline CI. |
| `tests/test_platforms_playwright.py` | Day 1 | Real headless-Chromium render checks on all 9 platforms (§4); pass criteria = HTTP<500 AND non-empty DOM. |
| `tests/test_platforms_live.py` | Day 1 | Opt-in live integration tests (`ABA_LIVE_TESTS=1`). |
| `tools/llm_provider_test.py` | Day 1 | Provider smoke tool: chat + AI features + `--full-session` (§7). |
| `webui/app.py` | Day 1 | FastAPI Web UI server (§9): runs, SSE trace stream with replay, stop endpoint, reports/screenshots. TODAY: added `/api/models?provider=...` endpoint (5-min cache, `refresh=1` bypass). |
| `webui/static/index.html` | Day 1 | Dashboard SPA (§9, §14 fixes). TODAY: provider picker now loads a live model dropdown on provider change (was free-text override only). |
| `run.sh / run.bat` | Day 1 | One-command start: venv sync + deps + launch Web UI (§12). |
| `setup.sh / setup.bat` | Day 1 | One-time setup: venv, requirements, Playwright Chromium, copies `env.example` -> `.env` (§12). |
| `requirements.txt` | Day 1 | Pinned deps (fastapi, uvicorn, playwright, pytest...). |
| `env.example` | Day 1 | Documented template of every env var (§13). |
| `pytest.ini` | Day 1 | Test paths/markers (live tests excluded by default). |
| `README.md` | Day 1 | Project overview & quick start. |
| `PROGRESS_DAY_BY_DAY.md` | Day 1 | Day-by-day narrative report. |

---
*Generated files (not tracked): `ai_agent/data/*.db`, `reports/*.json`, screenshots.*
