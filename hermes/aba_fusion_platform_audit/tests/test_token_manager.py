"""Unit tests for token_manager.TokenManager (fully mocked, no network)."""
import base64
import json
import time

import pytest

import token_manager as tm_mod
from token_manager import TokenManager


def make_jwt(iat=None, exp=None):
    """Build an unsigned but structurally valid JWT with iat/exp claims."""
    iat = iat or int(time.time())
    exp = exp or iat + 900
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b'=')
    payload = base64.urlsafe_b64encode(
        json.dumps({'iat': iat, 'exp': exp}).encode()).rstrip(b'=')
    sig = base64.urlsafe_b64encode(b'sig').rstrip(b'=')
    return b'.'.join([header, payload, sig]).decode()


class FakeResp:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fake_urlopen(monkeypatch):
    calls = []

    def _install(login_response):
        def fake(req, timeout=None):
            calls.append(json.loads(req.data.decode()))
            return FakeResp(login_response)
        monkeypatch.setattr(tm_mod.urllib.request, 'urlopen', fake)
        return calls

    return _install


@pytest.fixture()
def cache(tmp_path):
    return str(tmp_path / 'token_cache.json')


LOGIN_OK = {
    'accessToken': make_jwt(),
    'refreshToken': 'refresh-abc',
    'tenantId': 'tenant-1',
    'user': {'username': 'test_02', 'email': 't@x.ai', 'id': 7,
             'tenant': {'name': 'arma'}, 'isSuperAdmin': False},
}


def test_login_stores_fields(fake_urlopen, cache):
    fake_urlopen(LOGIN_OK)
    tm = TokenManager(cache_file=cache)
    token = tm.login()
    assert token == LOGIN_OK['accessToken']
    assert tm.tenant_id == 'tenant-1'
    assert tm.user_info['username'] == 'test_02'
    assert tm.user_info['tenant'] == 'arma'


def test_login_sends_creds(fake_urlopen, cache):
    calls = fake_urlopen(LOGIN_OK)
    TokenManager(cache_file=cache).login()
    assert calls[0]['username'] == tm_mod.CREDS['username']


def test_expires_at_is_5min_before_exp(fake_urlopen, cache):
    iat = int(time.time())
    jwt = make_jwt(iat=iat, exp=iat + 900)
    fake_urlopen({**LOGIN_OK, 'accessToken': jwt})
    tm = TokenManager(cache_file=cache)
    tm.login()
    assert tm.expires_at == iat + 900 - 300


def test_ensure_valid_reuses_fresh_token(fake_urlopen, cache):
    calls = fake_urlopen(LOGIN_OK)
    tm = TokenManager(cache_file=cache)
    t1 = tm.login()
    assert tm.ensure_valid() == t1
    assert len(calls) == 1  # no re-login


def test_ensure_valid_relogins_when_expired(fake_urlopen, cache):
    calls = fake_urlopen(LOGIN_OK)
    tm = TokenManager(cache_file=cache)
    tm.login()
    tm.expires_at = time.time() - 1  # force expiry
    tm.ensure_valid()
    assert len(calls) == 2


def test_headers_shape(fake_urlopen, cache):
    fake_urlopen(LOGIN_OK)
    tm = TokenManager(cache_file=cache)
    h = tm.headers()
    assert h['Authorization'].startswith('Bearer ')
    assert h['Content-Type'] == 'application/json'


def test_cache_roundtrip(fake_urlopen, cache):
    calls = fake_urlopen(LOGIN_OK)
    tm1 = TokenManager(cache_file=cache)
    token = tm1.login()
    tm2 = TokenManager(cache_file=cache)          # loads from disk
    assert tm2.ensure_valid() == token
    assert len(calls) == 1                          # no extra login


def test_decode_token_garbage():
    tm = TokenManager(cache_file='/nonexistent/x.json')
    assert tm._decode_token('not-a-jwt') == {}
    assert tm._decode_token(None if False else 'a.b') == {}
