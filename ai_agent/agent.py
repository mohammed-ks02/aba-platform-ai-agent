#!/usr/bin/env python3
"""ABA Fusion multi-platform security agent — main entry point.

The implementation lives in ``core/runner.py``; this script simply forwards
to it so you can run the agent from anywhere:

    python agent.py            # full run: all 9 platforms + fuzz
    python agent.py --quick    # reduced fuzz matrix
    python agent.py --no-fuzz  # discovery only

LLM options (any provider — see core/llm/providers.py):
    python agent.py --provider openai --model gpt-4o-mini
    python agent.py --provider groq
    python agent.py --provider custom --base-url http://my-server:8000/v1 \
                    --model my-model
    ABA_LLM_PROVIDER / ABA_LLM_MODEL / ABA_LLM_API_KEY / ABA_LLM_BASE_URL /
    ABA_LLM_FALLBACKS env vars are honoured too.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.runner import main  # noqa: E402

if __name__ == '__main__':
    main()
