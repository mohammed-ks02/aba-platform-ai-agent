"""Re-export of the top-level token_manager module for ``core`` users."""
import os
import sys

_agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _agent_root not in sys.path:
    sys.path.insert(0, _agent_root)

from token_manager import TokenManager, BASE, DP, CREDS, UA  # noqa: E402,F401
