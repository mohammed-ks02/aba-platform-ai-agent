# Day 3 — Provider → model catalog picker + per-day documentation (current session, part 2)

Requested: *"after choosing a provider, see a list of available models"* and
*"documentation of each day … each file has a day"*.

## Feature: dynamic model list per provider
Backend:
- `ai_agent/core/llm/base.py` — new `BaseProvider.list_models()`: generic
  `GET {base}/models` query (works for every OpenAI-compatible server:
  Groq, NVIDIA NIM, OpenAI, Ollama, vLLM, LM Studio, llama.cpp, LiteLLM…).
- `ai_agent/core/llm/providers.py` —
  - `AnthropicProvider.list_models()` (native `/v1/models`)
  - `GeminiProvider.list_models()` (native `listModels`, strips `models/` prefix)
  - `CURATED_MODELS` static fallback lists (used when the live catalog is
    unreachable/unkeyed)
  - `list_provider_models(name, …)` → `{'provider','source':'live|fallback|none','models',[…],'error'}`
- `webui/app.py` — new endpoint `GET /api/models?provider=<name>[&refresh=1]`
  with a 5-minute TTL cache per (provider, base_url).

Frontend (`webui/static/index.html`):
- The free-text "model override" input became a **model dropdown**.
- Choosing a provider in the LLM select immediately fetches its catalog
  (race-guarded), shows the provider default highlighted, plus a hint line:
  `N model(s) · live catalog` / `curated list (live catalog unreachable)`.
- ⟳ button forces a refresh bypassing the server cache.
- Providers without any discoverable catalog fall back to a manual
  "type model id" field.
- Selected model flows into both `/api/plan` and `/api/run` payloads.

## Verified live (this sandbox, with real Groq + NVIDIA API keys)
- Web UI server started; `/api/providers` shows `groq` and `nvidia` as
  `configured: true`.
- `GET /api/models?provider=groq&refresh=1` → `source:"live"`, **11 real
  models** (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`,
  `allam-2-7b`, `whisper-large-v3`, …).
- `GET /api/models?provider=nvidia&refresh=1` → `source:"live"`, **81 real
  models** (`meta/llama-*`, `google/gemma-*`, `ibm/granite-*`,
  `deepseek-ai/*`, …).
- End-to-end chat with a model chosen from the fetched list:
  - Groq `openai/gpt-oss-20b` → replied correctly (empty selection falls back
    to provider default `qwen/qwen3.8-27b`).
  - NVIDIA default `z-ai/glm-5.3-flash` → replied correctly.
  - Stale/unsupported catalog entries surface clean errors (e.g. HTTP 410
    "end of life" for `meta/llama-3.1-8b-instruct`) — expected behavior; the
    live catalog reflects what the account can list, per-model access can
    differ.
- `GET /api/models?provider=ollama` (server not running) → `source:"fallback"`.
- Page serves the new controls; offline pytest suite still green.

## Documentation structure (new)
- `docs/DOCUMENTATION_INDEX.md` — every file mapped to its day + history.
- `docs/days/day1.md` — core build-out summary (links full narrative).
- `docs/days/day2.md` — reliability hardening (offline hang fix).
- `docs/days/day3.md` — this file: model picker + docs.
- Existing full narrative remains `PROGRESS_DAY_BY_DAY.md`.

## Files touched today
- `ai_agent/core/llm/base.py`, `ai_agent/core/llm/providers.py`
- `webui/app.py`, `webui/static/index.html`
- `docs/**` (new)
