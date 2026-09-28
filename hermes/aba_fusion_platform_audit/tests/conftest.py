"""Pytest fixtures / sys.path setup for the aba_fusion_platform_audit tests."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)                      # aba_fusion_platform_audit/
_AI_AGENT = os.path.join(_PKG_ROOT, 'ai_agent')         # .../ai_agent

# Make `core.*` and `token_manager` importable from tests.
for p in (_AI_AGENT, _PKG_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path):
    """Path to a throwaway SQLite database file."""
    return str(tmp_path / 'memory_test.db')


@pytest.fixture()
def memory(tmp_db):
    from core.memory import Memory
    return Memory(db=tmp_db)
