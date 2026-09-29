# ABA Fusion AI Agent — Complete Guide

An AI-driven, multi-platform **testing agent** for the 9 ABA Fusion staging
platforms (`*.abafusion.ai`). It authenticates, tests each platform across six
dimensions (performance, limits, functionality, logic, security, UX), and
reports findings — from a CLI or a live web dashboard.

---

## 1. What it is

- **Automated black-box testing** of web platforms. The security dimension is
  **DAST** (Dynamic Application Security Testing) + fuzzing; the rest is smoke /
  E2E / non-functional / behavioural testing.
- **Authenticated** — logs in through the central SSO and tests *inside* the app.
- **Feature-aware** — reads the Data Platform's OpenAPI spec and, when you name a
  feature ("test sources"), tests that feature's endpoints.
- **Trustworthy by design** — differential false-positive checks, body-aware
  classification, route-diff + hydration gating, coverage counts, test-data
  cleanup.

### Testing technology / stack
| Layer | Tech |
|---|---|
| HTTP probing engine | Python **stdlib `urllib`** (zero deps) |
| Security fuzzing | custom payload dictionary (XSS/SQLi/path/cmd/NoSQL/SSRF/redirect/SSTI/XXE) |
| Real-browser + UX | **Playwright** (headless or headed Chromium) |
| Own unit tests | **pytest** (98 offline tests) |
| Web UI / live view | **FastAPI + uvicorn + SSE** (vanilla JS frontend) |
| Storage / auth / AI | **SQLite** · **JWT** · optional provider-agnostic **LLM** (20 providers) |

---

## 2. The 9 platforms

| Key | Name | URL | API |
|-----|------|-----|-----|
| `stg-dp` | Data Platform | `https://stg-dp.abafusion.ai` | **Yes** — `/api/v1` + manager `stg-dp-mgr` (118-path OpenAPI) |
| `stg-analytics` | Analytics | `https://stg-analytics.abafusion.ai` | SPA |
| `stg-pulse` | Pulse | `https://stg-pulse.abafusion.ai/project` | SPA |
| `stg-orbit` | Orbit | `https://stg-orbit.abafusion.ai` | SPA |
| `stg-mate` | Mate | `https://stg-mate.abafusion.ai/mate` | SPA |
| `stg-perf` | Performance | `https://stg-perf.abafusion.ai` | SPA |
| `stg-agentic` | Agentic AI | `https://stg-agentic.abafusion.ai` | SPA |
| `stg-orch` | Orchestration | `https://stg-orch.abafusion.ai/automation` | SPA |
| `stg-forge` | Fusion Forge | `https://stg-forge.abafusion.ai/fusionforge/` | SPA |

Only `stg-dp` exposes an API, so **limits / logic / security apply to `stg-dp` only**; the 8 SPAs are covered by **performance / functionality / UX**.

---

## 3. The six dimensions

| Dimension | What it checks |
|---|---|
| **performance** | latency avg/p50/p95/max + HTML size (read-only GET) |
| **limits** | oversized / deep-nested / unicode / huge-header payloads + a 60-request rate-limit burst (writes to DP) |
| **functionality** | reachability, SPA markers, title/favicon, **route-diff** (catch-all shell detection), passive security-header/cookie/CORS scan (all 9), protected-route auth check |
| **logic** | idempotency (double POST), GET-read-only, validation semantics, 404-vs-405, **feature lifecycle** create→get→delete when focused (writes to DP) |
| **security** | body-aware DAST across multiple endpoints + OpenAPI-discovered ones, differential FP re-check, **write-path** POST body injection, n-of-m 5xx confirmation (writes to DP) |
| **ux** | Playwright screenshot + hydration check + optional AI "real user" review; authenticated by default |

---

## 4. Install & setup

```bash
# 1. Windows one-command (creates .venv, installs deps + Playwright Chromium, launches UI)
setup.bat
run.bat

# Linux/macOS
./setup.sh
./run.sh
```

Manual:
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # fastapi uvicorn pydantic playwright pytest
.venv\Scripts\python -m playwright install chromium
```

**Credentials & keys** — put them in a `.env` in the project root (it is
**gitignored**; never commit it). Copy `.example` and fill in:
```ini
ABA_USERNAME=test_02
ABA_PASSWORD=<staging password>
ABA_TENANT=arma
GROQ_API_KEY=gsk_...
NVIDIA_API_KEY=nvapi-...
GROQ_API_KEY=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
NVIDIA_API_KEY=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
ABA_LLM_PROVIDER=groq
ABA_LLM_MODEL=qwen/qwen3.8-27b
```
The agent auto-loads `.env` (no `set`/`export` needed). Working LLM defaults:
**groq / `qwen/qwen3.8-27b`** or **nvidia / `nemotron-3-super-120b-a12b`**.

---

## 5. How to run

### Web UI (recommended)
```bash
run.bat            # -> http://127.0.0.1:8787
```
Pick platforms (chips), dimensions, toggles (quick / login&test-inside /
show-browser / dry-run / throttle), press **Run test**, watch the live trace,
then browse Findings (with filters + evidence), Performance, Limits,
Functionality, Logic, UX reviews, and Reports (Read / PDF / JSON).

### CLI
```bash
python ai_agent/agent.py [options]
```
| Flag | Effect |
|---|---|
| `--platforms "dp,analytics"` | restrict targets (keys or words; default all 9) |
| `--dims performance,security` | pick dimensions (implies full suite) |
| `--prompt "test sources in dp"` | natural-language → plan (LLM or keyword fallback) |
| `--quick` | reduced matrix (fast smoke run) |
| `--ux` / `--headed` | Playwright UX pass / visible browser |
| `--no-auth` | UX without SSO login (public pages only) |
| `--dry-run` | security: read-only GETs, never create connectors |
| `--throttle 0.2` | seconds between requests (politeness) |
| `--ai-payloads` / `--ai-triage` | LLM extra payloads / per-finding triage (slow, opt-in) |
| `--provider groq --model qwen/qwen3.8-27b` | LLM selection |
| `--no-fuzz` | discovery only · `--fresh` reset memory |

---

## 6. Tutorial — your first run

1. **Set up:** `setup.bat`, then edit `.env` with your creds + a working LLM.
2. **Launch the UI:** `run.bat` → open `http://127.0.0.1:8787`.
3. **A safe first test:** select platform **dp**, tick **Functionality** +
   **Performance**, leave **login & test inside** on, press **Run test**. These
   are read-only. Watch the live trace; then open the **Findings** tab.
4. **See it work authenticated:** tick **UX/UI** + **👁 show browser**, run again
   from your own terminal (`python ai_agent/agent.py --ux --headed --platforms
   stg-dp`) — a Chromium window logs in via SSO and walks the app.
5. **Export:** Reports tab → **📄 Read** (in-app), **🖨 PDF**, or **⬇ JSON**.

---

## 7. Example tests

| Goal | How (UI prompt or CLI) | What happens | Typical result |
|---|---|---|---|
| Quick health of all 9 | dims: performance + functionality | GET each platform, latency + render + passive headers | all alive; ~8/9 missing CSP (medium) |
| Test the Sources feature | prompt **"test sources in dp"** | reads OpenAPI, tests 9 connector/source GET endpoints | 0 findings (API validates) + coverage count |
| Safe security scan | `--dims security --platforms stg-dp --dry-run` | read-only injection across DP GET endpoints | 0 findings, no writes, no false SSRF |
| Full security (writes) | `--dims security --platforms stg-dp` | + write-path POST body injection + cleanup | findings if any; connectors it creates are DELETEd |
| Watch an authenticated UX | `--ux --headed --platforms stg-dp` | logs in, renders inside the app, AI UX rating | rating /5 + screenshot |
| Rate-limit check | `--dims limits --platforms stg-dp` | 60-request burst on the API | `no_rate_limiting` (low) if no 429 |
| Logic / lifecycle | prompt **"lifecycle test on dp pipelines: create, validate, clean up"** | create→get→delete + idempotency + validation | inconclusive if create rejected (honest) |
| Full suite, one platform | `--dims performance,limits,functionality,logic,security,ux --platforms stg-dp` | everything on the DP | full report + coverage matrix |
| Compare speed across all | prompt **"how fast does every platform load"** | performance on all 9 | latency ranking |

---

## 8. Interpreting results

- **Severity:** critical > high > medium > low > info. Findings carry an
  **evidence** snippet + a **recommendation**.
- **Coverage:** every security run prints `[coverage] N probe(s)` so **"0
  findings" means tested-and-clean, not skipped**; a focus that matches 0
  endpoints **warns**. The report includes a per-platform × per-dimension matrix.
- **verified_working (SPAs):** a `200` on a SPA route isn't proof — the agent
  route-diffs vs a bogus path and only calls an app "working" with a **distinct
  route** OR **UX rendered content while authenticated**.
- **False-positive controls:** reflected payloads never count (SSRF-on-404 is
  filtered); a lone 5xx needs an n-of-m repeat; `/health` is allow-listed.

---

## 9. Safety

- Targets are **staging only**; credentials live in `.env` (gitignored) and the
  agent logs in **programmatically** (never typed into a form by a human).
- `--dry-run` for read-only security; `--throttle` for politeness; the security
  dimension **cleans up** every connector it creates.
- Runs are cancellable (Stop) and each web run uses its own isolated DB.

---

## 10. Known limits

- The 8 SPAs get performance/functionality/UX only (no API to fuzz).
- Perf p95 uses few samples (liveness/latency check, not a load test).
- UX reviews the app's landing/home, not a deep feature view.
- The feature lifecycle needs a valid create body to fully exercise get/delete.
- LLM features (ai-payloads/ai-triage) are opt-in and slow on rate-limited free
  tiers; the deterministic engine is the source of truth.

---

## 11. Tests & troubleshooting

```bash
python -m pytest                                   # 98 offline tests (mocked)
python tests/test_platforms_playwright.py          # live render-check all 9
python tools/llm_provider_test.py --provider groq  # validate an LLM key/model
```
- **"LLM failed":** check the model name — use a verified one (groq
  `qwen/qwen3.8-27b`, nvidia `nemotron-3-super-120b-a12b`).
- **Headed browser not visible:** run from your own terminal (a window from the
  app-spawned server may render on a non-visible session).
- **UI looks stale after an update:** hard-refresh (the page is served
  `no-store`, so this is rare).

---

_See also: `SESSION_REPORT_2026-09-29.md` (change log), `TEST_MATRIX_30_2026-09-29.md` (30 example tests reviewed for quality), `PROGRESS_DAY_BY_DAY.md` (dev history)._
