"""Dependency-free .env loader.

Reads KEY=value lines from a `.env` file in the project root (or the path
given by ABA_DOTENV) and injects them into os.environ WITHOUT overriding
variables that are already set (real env vars always win).

Import this module early (it is imported by core.config, token_manager and
the web UI) so the agent works whether variables come from the shell or a
`.env` file.
"""
import os


def load_env(path=None):
    """Load KEY=value pairs from a .env file. Returns number of vars set."""
    if path is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.environ.get('ABA_DOTENV') or os.path.join(here, '..', '.env')
        path = os.path.abspath(path)
    count = 0
    try:
        with open(path, encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip().removeprefix('export ').strip()
                value = value.strip()
                # strip matching quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
                    count += 1
    except FileNotFoundError:
        pass
    return count


LOADED_PATH = None


def ensure_loaded():
    global LOADED_PATH
    n = load_env()
    if n:
        LOADED_PATH = True
    return n
