#!/usr/bin/env python3
"""Fast Mode — thin wrapper around the unified runner.

Equivalent to:  python agent.py --quick
Kept for backwards compatibility with previous invocations.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.runner import main  # noqa: E402

if __name__ == '__main__':
    print('(deprecated: use `python agent.py --quick` instead)')
    main(['--quick'])
