"""Unit tests for the LLM layer (core/llm): providers, client chain, helpers.

All HTTP is mocked -- no network, no real API keys required.
"""
import json

import pytest

from core.llm import (LLMClient, LLMError, PROVIDERS, analyze_finding,
                      available_providers, executive_summary,
                      extract_json, generate_payloads)
from core.llm.base import BaseProvider
from core.llm.providers import AnthropicProvider, GeminiProvider


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
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
    """Patch urllib.request.urlopen used by base.py; record calls."""
    calls = []

    def _install(responder):
        import core.llm.base as base_mod

        def fake(req, timeout=None):
            calls.append({'url': req.full_url,
                          'headers': dict(req.headers),
                          'body': json.loads(req.data.decode())})
            return FakeResp(responder(req))
        monkeypatch.setattr(base_mod.urllib.request, 'urlopen', fake)
        return calls
    return _install


@pytest.fixture()
def clean_env(monkeypatch):
    for var in list(__import__('os').environ):
        if var.startswith('ABA_LLM') or var.endswith('_API_KEY') \
                or var in ('OPENAI_BASE_URL', 'GITHUB_TOKEN', 'GH_TOKEN',
                           'GOOGLE_API_KEY'):
            monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------
# registry / selection
# --------------------------------------------------------------------------
def test_registry_has_all_major_providers():
    expected = {'openai', 'anthropic', 'gemini', 'groq', 'deepseek',
                'mistral', 'openrouter', 'xai', 'together', 'fireworks',
                'cerebras', 'sambanova', 'github', 'nvidia', 'azure',
                'ollama', 'lmstudio', 'vllm', 'llamacpp', 'litellm'}
    assert expected <= set(PROVIDERS)


def test_every_provider_subclasses_base():
    for name, cls in PROVIDERS.items():
        assert issubclass(cls, BaseProvider), name


def test_openai_key_detected(clean_env):
    clean_env.setenv('OPENAI_API_KEY', 'sk-test')
    assert 'openai' in available_providers()


def test_custom_provider_requires_url_and_model(clean_env):
    from core.llm.providers import CustomProvider
    with pytest.raises(LLMError):
        CustomProvider()
    p = CustomProvider(base_url='http://x/v1', model='m')
    assert p.base_url == 'http://x/v1' and p.model == 'm'


def test_client_disabled_without_keys(clean_env):
    # local providers (requires_key=False) may auto-detect; force none
    clean_env.setenv('ABA_LLM_PROVIDER', 'nonexistent-provider')
    llm = LLMClient(verbose=False)
    assert not llm.enabled


def test_client_explicit_provider_and_fallback_chain(clean_env):
    clean_env.setenv('OPENAI_API_KEY', 'sk-a')
    clean_env.setenv('GROQ_API_KEY', 'gsk-b')
    llm = LLMClient(provider='openai,groq', verbose=False)
    assert [p.name for p in llm.chain] == ['openai', 'groq']
    assert llm.provider_name == 'openai'


def test_fallback_when_first_provider_fails(clean_env, fake_urlopen):
    clean_env.setenv('OPENAI_API_KEY', 'sk-a')
    clean_env.setenv('GROQ_API_KEY', 'gsk-b')

    def responder(req):
        if 'openai.com' in req.full_url:
            raise Exception('boom')  # connection-level failure
        return {'choices': [{'message': {'content': 'from groq'}}]}

    fake_urlopen(responder)
    llm = LLMClient(provider='openai,groq', verbose=False)
    llm.chain[0].max_retries = 0
    out = llm.chat([{'role': 'user', 'content': 'hi'}])
    assert out == 'from groq'


# --------------------------------------------------------------------------
# wire formats
# --------------------------------------------------------------------------
def test_openai_wire_format(clean_env, fake_urlopen):
    clean_env.setenv('OPENAI_API_KEY', 'sk-test')
    calls = fake_urlopen(
        lambda r: {'choices': [{'message': {'content': 'hello'}}]})
    llm = LLMClient(provider='openai', model='gpt-4o-mini', verbose=False)
    assert llm.chat([{'role': 'user', 'content': 'x'}]) == 'hello'
    c = calls[-1]
    assert c['url'].endswith('/chat/completions')
    assert c['headers']['Authorization'] == 'Bearer sk-test'
    assert c['body']['model'] == 'gpt-4o-mini'


def test_anthropic_wire_format(clean_env, fake_urlopen):
    clean_env.setenv('ANTHROPIC_API_KEY', 'ant-x')
    calls = fake_urlopen(
        lambda r: {'content': [{'type': 'text', 'text': 'claude says hi'}]})
    llm = LLMClient(provider='anthropic', verbose=False)
    msgs = [{'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': 'q'}]
    assert llm.chat(msgs) == 'claude says hi'
    c = calls[-1]
    assert c['url'].endswith('/messages')
    hdrs = {k.lower(): v for k, v in c['headers'].items()}
    assert hdrs.get('x-api-key') == 'ant-x'
    assert hdrs.get('anthropic-version') == '2023-06-01'
    assert c['body']['system'] == 'sys'
    assert c['body']['messages'] == [{'role': 'user', 'content': 'q'}]
    assert c['body']['max_tokens'] == 1024


def test_gemini_wire_format(clean_env, fake_urlopen):
    clean_env.setenv('GEMINI_API_KEY', 'g-x')
    calls = fake_urlopen(
        lambda r: {'candidates': [{'content':
                                   {'parts': [{'text': 'gm'}]}}]})
    llm = LLMClient(provider='gemini', model='gemini-2.0-flash',
                    verbose=False)
    assert llm.chat([{'role': 'user', 'content': 'q'}]) == 'gm'
    c = calls[-1]
    assert ':generateContent' in c['url'] and 'key=g-x' in c['url']
    assert c['body']['contents'][0]['role'] == 'user'


def test_ollama_no_key_needed(clean_env, fake_urlopen):
    fake_urlopen(lambda r: {'choices': [{'message': {'content': 'local'}}]})
    llm = LLMClient(provider='ollama', verbose=False)
    assert llm.enabled
    assert llm.chat([{'role': 'user', 'content': 'hi'}]) == 'local'


def test_any_openai_compatible_via_base_url(clean_env, fake_urlopen):
    clean_env.setenv('ABA_LLM_API_KEY', 'k')
    calls = fake_urlopen(
        lambda r: {'choices': [{'message': {'content': 'ok'}}]})
    llm = LLMClient(provider='custom', base_url='http://my-box:8000/v1',
                    model='whatever', verbose=False)
    assert llm.chat([{'role': 'user', 'content': 'x'}]) == 'ok'
    assert calls[-1]['url'] == 'http://my-box:8000/v1/chat/completions'


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------
@pytest.mark.parametrize('text,expected', [
    ('{"a": 1}', {'a': 1}),
    ('```json\n{"a": 1}\n```', {'a': 1}),
    ('prose before {"a": [1,2]} prose after', {'a': [1, 2]}),
    ('Sure! Here you go:\n```\n[1, 2, 3]\n```', [1, 2, 3]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_failure():
    with pytest.raises(ValueError):
        extract_json('no json here at all')


# --------------------------------------------------------------------------
# agent-level AI helpers (mocked LLMClient)
# --------------------------------------------------------------------------
class StubClient:
    enabled = True

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply

    def chat_json(self, system, user, **kw):
        return extract_json(self.reply)

    def status(self):
        return [{'provider': 'stub', 'model': 'm', 'base_url': '-'}]


class StubHolder:
    enabled = True
    ai_payloads = True
    provider_name = 'stub'

    def __init__(self, client):
        self.client = client


def test_analyze_finding_parses_severity():
    holder = StubHolder(StubClient(json.dumps(
        {'is_real': True, 'severity': 'HIGH', 'analysis': 'leak',
         'recommendation': 'fix it'})))
    out = analyze_finding(holder.client, 'stg-dp', '/e', 'sql', 'p', 500,
                          'body')
    assert out['severity'] == 'high' and out['is_real']


def test_analyze_finding_bad_severity_defaults_info():
    holder = StubHolder(StubClient('{"is_real": true, "severity": "wow"}'))
    out = analyze_finding(holder.client, 'p', '/e', 'c', 'x', 500, '')
    assert out['severity'] == 'info'


def test_analyze_finding_garbage_returns_none():
    holder = StubHolder(StubClient('not json at all'))
    assert analyze_finding(holder.client, 'p', '/e', 'c', 'x', 500,
                           '') is None


def test_analyze_finding_no_llm_returns_none():
    assert analyze_finding(None, 'p', '/e', 'c', 'x', 500, '') is None


def test_generate_payloads():
    holder = StubHolder(StubClient('{"payloads": ["a", "", "b"]}'))
    assert generate_payloads(holder.client, 'xss', 't') == ['a', 'b']


def test_executive_summary():
    holder = StubHolder(StubClient('All good.'))
    assert executive_summary(holder.client, {'total': 9}) == 'All good.'


def test_helpers_tolerate_unsupported_severity_strings():
    holder = StubHolder(StubClient('{"is_real": false}'))
    out = analyze_finding(holder.client, 'p', '/e', 'c', 'x', 400, '')
    assert out['severity'] == 'info' and not out['is_real']


# --------------------------------------------------------------------------
# streaming (SSE) providers -- e.g. NVIDIA NIM reasoning models
# --------------------------------------------------------------------------
class FakeSSEResp:
    """Minimal iterable response yielding SSE lines."""

    def __init__(self, lines):
        self._lines = [l.encode() for l in lines]

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse_lines(text='PONG', reasoning=None):
    lines = []
    if reasoning:
        lines.append('data: ' + json.dumps(
            {'choices': [{'delta': {'reasoning': reasoning}}]}))
    lines.append('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}))
    lines.append('data: ' + json.dumps(
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}))
    lines.append('data: [DONE]')
    return lines


def test_stream_provider_uses_sse(monkeypatch):
    import core.llm.base as base_mod
    captured = {}

    def fake(req, timeout=None):
        captured['body'] = json.loads(req.data.decode())
        captured['accept'] = req.headers.get('Accept')
        return FakeSSEResp(_sse_lines('hello stream'))
    monkeypatch.setattr(base_mod.urllib.request, 'urlopen', fake)
    p = PROVIDERS['nvidia'](api_key='nv-test')
    out = p.chat([{'role': 'user', 'content': 'hi'}])
    assert out == 'hello stream'
    assert captured['body'].get('stream') is True
    assert captured['accept'] == 'text/event-stream'


def test_stream_reasoning_fallback(monkeypatch):
    """If the model only emits reasoning tokens, fall back to its tail."""
    import core.llm.base as base_mod

    def fake(req, timeout=None):
        return FakeSSEResp(_sse_lines('', reasoning='deep thoughts...'))
    monkeypatch.setattr(base_mod.urllib.request, 'urlopen', fake)
    p = PROVIDERS['nvidia'](api_key='nv-test')
    out = p.chat([{'role': 'user', 'content': 'hi'}])
    assert 'deep thoughts' in out


def test_non_stream_providers_unaffected(monkeypatch):
    import core.llm.base as base_mod
    captured = {}

    def fake(req, timeout=None):
        captured['body'] = json.loads(req.data.decode())
        return FakeResp({'choices': [{'message': {'content': 'plain'}}]})
    monkeypatch.setattr(base_mod.urllib.request, 'urlopen', fake)
    p = PROVIDERS['groq'](api_key='gsk-test')
    out = p.chat([{'role': 'user', 'content': 'hi'}])
    assert out == 'plain'
    assert 'stream' not in captured['body']


def test_groq_default_model_is_qwen38():
    assert PROVIDERS['groq'].default_model_name == 'qwen/qwen3.8-27b'


def test_nvidia_default_model_works_and_streams():
    # Default was changed from z-ai/glm-5.3-flash (times out on every call) to
    # a model verified to respond on the free NIM endpoint. It must still use
    # SSE streaming, which NIM requires for reasonable latency.
    assert PROVIDERS['nvidia'].default_model_name == \
        'nvidia/nemotron-3-super-120b-a12b'
    assert PROVIDERS['nvidia'].stream is True
