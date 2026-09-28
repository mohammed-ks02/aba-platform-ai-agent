# Day 1 — Core build-out (branch `ai-agent-v2`)

Full narrative lives in [`PROGRESS_DAY_BY_DAY.md`](../PROGRESS_DAY_BY_DAY.md).
Files delivered on Day 1:

## Analysis & entry points
- `ai_agent/agent.py`, `ai_agent/agent_fast.py`, `ai_agent/agent_ultra.py` (shims)
- Knowledge bases: `ai_agent/KNOWLEDGE_FUZZ.md`, `KNOWLEDGE_MEMORY.md`, `KNOWLEDGE_PLATFORMS.md`

## Core library (`ai_agent/core/`)
- `config.py` — platform registry (9 targets) + paths
- `http_client.py` — stdlib HTTP, discovery probe, later abort support
- `classifier.py` — response classification decision table
- `memory.py` — SQLite findings/patterns store
- `token_manager.py` — JWT auth, caching, auto re-login
- `runner.py` — unified pipeline replacing the 3 script variants
- `live.py` — headed-browser real-time visualization mode
- `suite.py` — performance / limits / functionality / logic / security dimensions
- `ux_review.py` — screenshot + vision-LLM UX review
- `planner.py` — natural-language prompt → test plan

## LLM layer (`ai_agent/core/llm/`)
- `base.py` — BaseProvider (retries, auth styles, UA fix, SSE streaming)
- `providers.py` — 19+ providers incl. Groq, NVIDIA NIM, Anthropic, Gemini, Ollama…
- `client.py` — LLMClient facade + fallback chain + AI triage/summaries
- `tools/llm_provider_test.py` — provider smoke/full-session tester

## Tests (`tests/`)
- offline: `test_config`, `test_classifier`, `test_memory`, `test_token_manager`,
  `test_llm`, `test_runner_offline` (55 passed / 10 skipped at end of Day 1)
- live/opt-in: `test_platforms_playwright`, `test_platforms_live`

## Web UI (`webui/`)
- `app.py` — FastAPI server, runs registry, SSE traces with replay, stop, reports
- `static/index.html` — dashboard: prompt box, provider/model pickers, dimension
  checkboxes, live view, results tabs (Findings/Performance/Limits/etc.)

## Ops & docs
- `setup.sh/.bat`, `run.sh/.bat`, `requirements.txt`, `env.example`, `pytest.ini`,
  `.gitignore`, `README.md`, `PROGRESS_DAY_BY_DAY.md`
