# Day 2 — Reliability hardening (current session, part 1)

Focus: make the offline stack actually deterministic and unblock CI-style runs.

## Bugs found & fixed
1. **Offline test-suite hang** — keyless local LLM providers (`ollama`,
   `lmstudio`, `vllm`, `llamacpp`, `litellm`, `custom`) were treated as always
   *available* by `BaseProvider.available()`, so `LLMClient(provider="auto")`
   put them in the fallback chain and the runner/tests attempted live HTTP to
   localhost endpoints (long connect/retry stalls inside pytest and webui runs).
   - **Fix** (`ai_agent/core/llm/client.py`): keyless providers are only added
     to the auto chain when their endpoint is explicitly configured via
     `ABA_LLM_BASE_URL` / `OPENAI_BASE_URL` (or their own base-url env).
     Explicitly selecting such a provider still works.
2. **Ambient-env leakage into offline tests** — `.env` / exported vars could
   silently switch tests to live mode.
   - **Fix** (`tests/test_runner_offline.py`): pins `ABA_LLM_PROVIDER=auto`
     and clears `ABA_LLM_BASE_URL`/`OPENAI_BASE_URL` for the offline suite.
3. Restored `.gitignore` content that had been accidentally emptied during
   debugging.

## Verification
- Full offline suite: **88 passed, 10 skipped** (live tests opt-in via
  `ABA_LIVE_TESTS=1`) in ~1.5 s — previously it hung indefinitely.

## Files touched
- `ai_agent/core/llm/client.py` (fix)
- `tests/test_runner_offline.py` (hardening)
- `.gitignore` (restore)
