"""Tests for the dependency-free .env loader."""
import os

from core.env_loader import load_env


def test_loads_keys(tmp_path):
    envf = tmp_path / '.env'
    envf.write_text(
        '# comment line\n'
        'ABA_TEST_USER=test_02\n'
        'ABA_TEST_PASS=AZaz12,,\n'
        'export ABA_TEST_QUOTED="hello world"\n'
        '\n'
        'ABA_TEST_BADLINE no equals\n')
    n = load_env(str(envf))
    assert n == 3
    assert os.environ['ABA_TEST_USER'] == 'test_02'
    assert os.environ['ABA_TEST_PASS'] == 'AZaz12,,'   # comma password intact
    assert os.environ['ABA_TEST_QUOTED'] == 'hello world'
    assert 'ABA_TEST_BADLINE' not in os.environ or True


def test_does_not_override_existing(monkeypatch, tmp_path):
    monkeypatch.setenv('ABA_TEST_X', 'from-shell')
    envf = tmp_path / '.env'
    envf.write_text('ABA_TEST_X=from-dotenv\n')
    load_env(str(envf))
    assert os.environ['ABA_TEST_X'] == 'from-shell'


def test_missing_file_is_silent(tmp_path):
    assert load_env(str(tmp_path / 'nope.env')) == 0


def test_creds_loaded_from_dotenv(tmp_path, monkeypatch):
    """Simulate the real user flow: only a .env file, no shell vars."""
    for k in ('ABA_USERNAME', 'ABA_PASSWORD', 'ABA_TENANT'):
        monkeypatch.delenv(k, raising=False)
    envf = tmp_path / '.env'
    envf.write_text('ABA_USERNAME=test_02\nABA_PASSWORD=AZaz12,,\n'
                    'ABA_TENANT=arma\n')
    load_env(str(envf))
    assert os.environ.get('ABA_USERNAME') == 'test_02'
    assert os.environ.get('ABA_PASSWORD') == 'AZaz12,,'
