"""Base classes and shared helpers for all LLM providers.

Design goals:
  * Zero third-party dependencies -- everything uses urllib from the stdlib.
  * Uniform ``chat()`` interface across every provider so the agent can be
    pointed at ANY model/provider with just an env var or config entry.
  * Providers are described declaratively (base URL + auth style + env keys),
    which makes adding a new provider a one-line registry entry when it is
    OpenAI-compatible, or a small subclass otherwise.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    """Raised when a provider call fails (network, auth, quota, bad model)."""


class BaseProvider:
    """Common plumbing: HTTP POST with JSON, retries, error normalisation."""

    name = 'base'
    default_base_url = ''
    # how we authenticate: 'bearer' | 'header-x-api-key' | 'none'
    auth_style = 'bearer'
    # environment variables checked (in order) for the API key
    api_key_env = ()
    # environment variables checked (in order) for a base-url override
    base_url_env = ()
    # whether this provider needs an API key at all (local servers don't)
    requires_key = True
    # per-provider default for streaming calls; reasoning models (NIM) can
    # spend minutes emitting tokens -- give SSE a much larger budget
    stream_timeout = 300

    def __init__(self, api_key=None, base_url=None, model=None, timeout=60,
                 max_retries=2, stream_timeout=None):
        self.api_key = api_key or self._env_first(self.api_key_env) or ''
        self.base_url = (base_url or self._env_first(self.base_url_env)
                         or self.default_base_url).rstrip('/')
        self.model = model or self.default_model()
        self.timeout = timeout
        self.stream_timeout = stream_timeout or getattr(
            self.__class__, 'stream_timeout', 300)
        self.max_retries = max_retries

    # -- helpers ---------------------------------------------------------
    @classmethod
    def available(cls):
        """True if this provider is usable in the current environment."""
        if not cls.requires_key:
            return True
        return bool(cls._env_first(cls.api_key_env))

    @staticmethod
    def _env_first(names):
        for n in names:
            v = os.environ.get(n)
            if v:
                return v.strip()
        return None

    def default_model(self):
        return os.environ.get('ABA_LLM_MODEL') or getattr(
            self.__class__, 'default_model_name', '')

    def _headers(self):
        h = {'Content-Type': 'application/json',
             'Accept': 'application/json',
             # some gateways (e.g. Cloudflare in front of Groq) block the
             # default "Python-urllib" UA with HTTP 403 / error 1010
             'User-Agent': getattr(self.__class__, 'user_agent',
                                   'ABAFusionAgent/1.0')}
        if self.api_key:
            if self.auth_style == 'bearer':
                h['Authorization'] = f'Bearer {self.api_key}'
            elif self.auth_style == 'header-x-api-key':
                h['x-api-key'] = self.api_key
        return h

    def _post_json(self, url, payload, extra_headers=None):
        headers = dict(self._headers())
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(payload).encode()
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers,
                                             method='POST')
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', 'replace')[:400]
                # retry only on 429 / 5xx
                if e.code in (429,) or e.code >= 500:
                    last_err = LLMError(f'{self.name}: HTTP {e.code}: {body}')
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(f'{self.name}: HTTP {e.code}: {body}') from e
            except Exception as e:  # URLError, timeout, JSON decode...
                last_err = LLMError(f'{self.name}: {e}')
                time.sleep(1.0 * (attempt + 1))
        raise last_err

    def _post_sse(self, url, payload, extra_headers=None):
        """POST a streaming (SSE) request and reassemble the assistant text.

        Used by providers whose non-streaming mode hangs behind slow
        reasoning gateways (e.g. NVIDIA NIM): tokens arrive incrementally
        so the connection stays alive.  Returns the concatenated
        ``delta.content`` (plus any ``reasoning_content`` fallback).
        """
        headers = dict(self._headers())
        headers['Accept'] = 'text/event-stream'
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST')
        last_err = None
        for attempt in range(2):  # one silent retry: NIM cold starts stall
            content, reason = [], []
            got_stop = False
            try:
                with urllib.request.urlopen(
                        req, timeout=self.stream_timeout) as resp:
                    for raw in resp:
                        line = raw.decode('utf-8', 'replace').strip()
                        if not line.startswith('data:'):
                            continue
                        chunk = line[5:].strip()
                        if chunk == '[DONE]':
                            break
                        try:
                            d = json.loads(chunk)
                        except ValueError:
                            continue
                        for ch in d.get('choices') or []:
                            delta = ch.get('delta') or {}
                            if delta.get('content'):
                                content.append(delta['content'])
                            rc = delta.get('reasoning') or \
                                delta.get('reasoning_content')
                            if rc:
                                reason.append(rc)
                            fr = ch.get('finish_reason')
                            if fr == 'stop' and content:
                                got_stop = True
                        if got_stop:
                            break
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', 'replace')[:400]
                raise LLMError(f'{self.name}: HTTP {e.code}: {body}') from e
            except Exception as e:  # timeout mid-stream -> retry once
                last_err = e
                time.sleep(1.0)
                continue
            out = ''.join(content)
            if not out and reason:
                # model spent its whole budget thinking; take the tail of
                # the reasoning trace so JSON extraction can still work
                out = ''.join(reason)[-1500:]
            if out and got_stop:
                return out
            if out:
                return out  # truncated by [DONE]; still usable
        if last_err is not None:
            raise LLMError(f'{self.name}: stream error: {last_err}')
        return ''

    # -- public API ------------------------------------------------------
    def chat(self, messages, temperature=0.2, max_tokens=1024):
        """Send an OpenAI-style message list; return assistant text.

        messages: [{'role': 'system'|'user'|'assistant', 'content': str}, ...]
        """
        raise NotImplementedError

    def list_models(self):
        """Return the model ids this provider can serve.

        Default implementation queries ``GET {base_url}/models`` (works for
        every OpenAI-compatible server: Groq, NVIDIA, Ollama, vLLM...).
        Providers with a different wire format override this.  Always
        returns a plain list of strings; raises LLMError on failure so
        callers can fall back to curated/static lists.
        """
        url = f'{self.base_url}/models'
        headers = dict(self._headers())
        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            raise LLMError(f'{self.name}: HTTP {e.code}: {body}') from e
        except Exception as e:
            raise LLMError(f'{self.name}: {e}') from e
        items = data.get('data') or data.get('models') or []
        out = []
        for m in items:
            if isinstance(m, str):
                out.append(m)
            elif isinstance(m, dict):
                mid = m.get('id') or m.get('name') or m.get('model')
                if mid:
                    out.append(str(mid))
        return sorted(set(out))

    def ping(self):
        """Cheap availability probe. Returns (ok, detail)."""
        try:
            saved = self.stream_timeout
            self.stream_timeout = min(saved, 45)
            try:
                out = self.chat([{'role': 'user',
                                  'content': 'Reply with exactly: OK'}],
                                max_tokens=8, temperature=0)
            finally:
                self.stream_timeout = saved
            return True, (out or '')[:40]
        except Exception as e:
            return False, str(e)[:200]


def extract_json(text):
    """Best-effort extraction of a JSON object/array from LLM output.

    Handles markdown code fences and leading/trailing prose.
    Raises ValueError when nothing parseable is found.
    """
    if not text:
        raise ValueError('empty response')
    # fenced block first
    fence = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)
    # first {...} or [...] balanced-ish span
    for opener, closer in (('{', '}'), ('[', ']')):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    raise ValueError(f'no JSON found in LLM output: {text[:120]!r}')
