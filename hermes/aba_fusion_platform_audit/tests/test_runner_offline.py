"""Offline end-to-end tests of the runner pipeline (all I/O mocked)."""
import json
import os

import pytest

import core.runner as runner
from core.config import PLATFORMS


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Point CONFIG at a temp dir; stub auth/discovery/fuzz HTTP calls."""
    # Memory()/write_report() read CONFIG at call time; patch the single
    # source-of-truth dict they share.
    from core import config as cfg_mod
    monkeypatch.setitem(cfg_mod.CONFIG, 'data_dir', str(tmp_path))
    monkeypatch.setitem(cfg_mod.CONFIG, 'memory_db',
                        str(tmp_path / 'memory.db'))
    monkeypatch.setitem(cfg_mod.CONFIG, 'reports_dir',
                        str(tmp_path / 'reports'))
    monkeypatch.setattr(runner, 'get_token', lambda: 'fake-token')
    # make sure no ambient LLM keys leak into offline tests
    for var in ('ABA_LLM_PROVIDER', 'ABA_LLM_API_KEY', 'OPENAI_API_KEY',
                'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY',
                'GROQ_API_KEY', 'DEEPSEEK_API_KEY', 'MISTRAL_API_KEY',
                'OPENROUTER_API_KEY', 'XAI_API_KEY', 'TOGETHER_API_KEY',
                'FIREWORKS_API_KEY', 'CEREBRAS_API_KEY',
                'AZURE_OPENAI_API_KEY', 'SAMBANOVA_API_KEY', 'GITHUB_TOKEN',
                'GH_TOKEN', 'ABA_LLM_BASE_URL', 'OPENAI_BASE_URL',
                'ABA_LLM_MODEL', 'ABA_LLM_FALLBACKS'):
        monkeypatch.delenv(var, raising=False)

    def fake_discover(base, timeout=5):
        alive = 'fusionforge' not in base  # forge is the "dead" fixture case
        return {'alive': alive, 'code': 200 if alive else 0,
                'title': 'App' if alive else '', 'ct': 'text/html'}

    fuzz_calls = []

    def fake_req(method, url, body=None, token='', timeout=None):
        fuzz_calls.append((method, url, body, token))
        # simulate a 500 on xss payloads, 400 elsewhere
        status = 500 if 'script' in json.dumps(body) else 400
        return {'status': status, 'body': 'err'}

    monkeypatch.setattr(runner, 'discover', fake_discover)
    monkeypatch.setattr(runner, 'req', fake_req)
    return tmp_path, fuzz_calls


def test_full_pipeline(isolated):
    tmp_path, fuzz_calls = isolated
    rpt = runner.main(['--fresh'])
    assert rpt['total'] == 9
    assert rpt['alive'] == 8
    assert rpt['findings'] > 0
    assert rpt['critical_high'] > 0
    assert rpt['patterns'] > 0


def test_report_written(isolated):
    tmp_path, _ = isolated
    runner.main([])
    reports = os.listdir(tmp_path / 'reports')
    assert len(reports) == 1 and reports[0].endswith('.json')
    with open(tmp_path / 'reports' / reports[0]) as f:
        data = json.load(f)
    assert {p['key'] for p in data['platform_details']} == set(PLATFORMS)


def test_quick_mode_reduces_matrix(isolated):
    _, calls = isolated
    runner.main(['--quick'])
    types = {c[2]['type'] for c in calls}
    assert len(types) == 1  # single connector type in quick mode


def test_no_fuzz_skips_requests(isolated):
    _, calls = isolated
    runner.main(['--no-fuzz'])
    assert calls == []


def test_no_token_skips_fuzz_gracefully(monkeypatch, isolated):
    tmp_path, calls = isolated
    monkeypatch.setattr(runner, 'get_token', lambda: '')
    rpt = runner.main([])
    assert calls == []
    assert rpt['total'] == 9       # discovery still ran
    assert rpt['findings'] == 0


def test_fuzz_sends_auth_and_payload(isolated):
    _, calls = isolated
    runner.main(['--quick'])
    method, url, body, token = calls[0]
    assert method == 'POST'
    assert url.endswith('/connectors')
    assert token == 'fake-token'
    assert 'source_config' in body
