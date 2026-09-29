"""Structural tests: project layout, package imports, config integrity."""
import ast
import importlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
_AI_AGENT = os.path.join(_PKG_ROOT, 'ai_agent')
sys_path_note = None  # paths handled by conftest
import sys  # noqa: E402


def test_project_layout():
    expected = [
        'ai_agent/agent.py',
        'ai_agent/token_manager.py',
        'ai_agent/core/__init__.py',
        'ai_agent/core/config.py',
        'ai_agent/core/http_client.py',
        'ai_agent/core/classifier.py',
        'ai_agent/core/memory.py',
        'ai_agent/core/runner.py',
        'ai_agent/KNOWLEDGE_PLATFORMS.md',
        'ai_agent/KNOWLEDGE_MEMORY.md',
        'ai_agent/KNOWLEDGE_FUZZ.md',
        'tests/test_config.py',
        'tests/test_classifier.py',
        'tests/test_memory.py',
        'tests/test_token_manager.py',
        'tests/test_runner_offline.py',
        'tests/test_platforms_live.py',
    ]
    missing = [p for p in expected
               if not os.path.isfile(os.path.join(_PKG_ROOT, p))]
    assert not missing, f'missing files: {missing}'


def test_no_duplicate_nested_data_dir():
    """The old buggy run created ai_agent/ai_agent/data — must be gone."""
    nested = os.path.join(_AI_AGENT, 'ai_agent')
    assert not os.path.exists(nested)


def test_core_modules_importable():
    for mod in ('core.config', 'core.http_client', 'core.classifier',
                'core.memory', 'core.runner', 'core.token_manager'):
        importlib.import_module(mod)


_SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git', '.venv', 'venv', 'env',
              'site-packages', 'node_modules', 'build', 'dist', '.mypy_cache',
              '.ruff_cache', '.eggs'}


def test_all_python_files_parse():
    # Only walk the project's own source trees, never a virtualenv or any
    # installed site-packages (they contain files this test should not judge,
    # and non-UTF-8/py2 files there would break it). Always decode UTF-8 so
    # the test behaves identically on Windows (cp1252) and POSIX.
    roots = [_AI_AGENT, _HERE,
             os.path.join(_PKG_ROOT, 'tools'),
             os.path.join(_PKG_ROOT, 'webui')]
    for base in roots:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                if fn.endswith('.py'):
                    path = os.path.join(root, fn)
                    with open(path, encoding='utf-8') as f:
                        try:
                            ast.parse(f.read(), filename=path)
                        except SyntaxError as e:
                            pytest.fail(f'syntax error in {path}: {e}')


class TestConfig:
    def test_nine_platforms(self):
        from core.config import PLATFORMS
        assert len(PLATFORMS) == 9

    @pytest.mark.parametrize('key', [
        'stg-dp', 'stg-analytics', 'stg-pulse', 'stg-orbit', 'stg-mate',
        'stg-perf', 'stg-agentic', 'stg-orch', 'stg-forge'])
    def test_platform_entry_shape(self, key):
        from core.config import PLATFORMS
        info = PLATFORMS[key]
        assert info['name'] and info['base'].startswith('https://')
        assert info['type']

    def test_dp_has_api_and_mgr(self):
        from core.config import PLATFORMS
        dp = PLATFORMS['stg-dp']
        assert 'mgr' in dp and 'api' in dp

    def test_fuzz_categories(self):
        from core.config import FUZZ
        for cat in ('xss', 'sql', 'ssti', 'ssrf', 'path', 'cmd',
                    'nosql', 'redirect'):
            assert cat in FUZZ
            assert len(FUZZ[cat]) >= 1

    def test_paths_absolute(self):
        from core.config import CONFIG
        for k in ('data_dir', 'memory_db', 'reports_dir'):
            assert os.path.isabs(CONFIG[k]), f'{k} must be absolute'

    def test_severities(self):
        from core.config import SEVERITIES
        assert set(SEVERITIES) == {'critical', 'high', 'medium', 'low', 'info'}
