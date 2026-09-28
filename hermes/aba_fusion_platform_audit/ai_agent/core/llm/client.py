"""LLM client facade: provider selection, fallback chain, agent helpers.

Selection order (first match wins):
    1. explicit ``provider=`` argument
    2. ABA_LLM_PROVIDER env var (name from PROVIDERS registry, or 'custom',
       or 'auto')
    3. 'auto' -> first registry provider whose API key is present in env
    4. none configured -> LLM disabled; the agent keeps working with its
       deterministic rules and simply skips AI features.

Fallback: if a call fails and ABA_LLM_FALLBACKS='groq,openrouter,...' is set,
the next provider in that list is tried automatically.
"""
import json
import os

from .base import LLMError, extract_json  # noqa: F401  (re-export)
from .providers import PROVIDERS, CustomProvider


def _all_registry():
    reg = dict(PROVIDERS)
    reg['custom'] = CustomProvider
    return reg


def available_providers():
    """Registry names usable right now (key present / no key required)."""
    reg = _all_registry()
    out = {}
    for name, cls in reg.items():
        try:
            if cls.available():
                out[name] = cls
        except Exception:
            continue
    return out


class LLMClient:
    """Thin multi-provider client used by the agent's AI features."""

    def __init__(self, provider=None, model=None, api_key=None,
                 base_url=None, timeout=180, stream_timeout=None,
                 verbose=True):
        self.verbose = verbose
        self.stream_timeout = stream_timeout
        self.chain = self._build_chain(provider, model, api_key, base_url,
                                       timeout)

    def _build_chain(self, provider, model, api_key, base_url, timeout):
        reg = _all_registry()
        names = []
        if provider and provider != 'auto':
            names = [p.strip() for p in provider.split(',') if p.strip()]
        else:
            sel = provider or os.environ.get('ABA_LLM_PROVIDER', '')
            fb = os.environ.get('ABA_LLM_FALLBACKS', '')
            if sel and sel != 'auto':
                names = [sel]
            elif fb:
                names = [p.strip() for p in fb.split(',') if p.strip()]
            if not names:
                # auto-detect from environment keys
                names = sorted(available_providers(),
                               key=lambda n: 0 if n == 'custom' else 1)
        chain = []
        for n in names:
            cls = reg.get(n)
            if cls is None:
                if self.verbose:
                    print(f'[llm] unknown provider "{n}" -- skipped')
                continue
            try:
                chain.append(cls(model=model, api_key=api_key,
                                 base_url=base_url, timeout=timeout,
                                 stream_timeout=self.stream_timeout))
            except LLMError as e:
                if self.verbose:
                    print(f'[llm] {n}: unusable ({e})')
        return chain

    @property
    def enabled(self):
        return bool(self.chain)

    @property
    def provider_name(self):
        return self.chain[0].name if self.chain else None

    def chat(self, messages, temperature=0.2, max_tokens=1024):
        """Try each provider in the chain; return text from the first OK."""
        if not self.chain:
            raise LLMError('no LLM provider configured '
                           '(set an API key or ABA_LLM_PROVIDER)')
        errors = []
        for p in self.chain:
            try:
                return p.chat(messages, temperature=temperature,
                              max_tokens=max_tokens)
            except LLMError as e:
                errors.append(str(e)[:150])
                if self.verbose:
                    print(f'[llm] {p.name} failed, trying next...')
        raise LLMError('all providers failed: ' + ' | '.join(errors))

    def chat_json(self, system, user, **kw):
        """Ask for JSON and parse it robustly (fences, prose...).

        Always returns a dict: reasoning models sometimes answer with a
        bare JSON array or scalar, which callers would otherwise crash on
        when calling .get().
        """
        text = self.chat([{'role': 'system',
                           'content': system + '\nAnswer with valid JSON only.'},
                          {'role': 'user', 'content': user}], **kw)
        data = extract_json(text)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {'result': data}
        return {'result': data}

    def status(self):
        rows = []
        for p in self.chain:
            rows.append({'provider': p.name, 'model': p.model,
                         'base_url': p.base_url or '-'})
        return rows


# ---------------------------------------------------------------------------
# Agent-level AI features (all degrade gracefully when no LLM is configured)
# ---------------------------------------------------------------------------
SECURITY_SYSTEM = (
    'You are a senior application-security engineer auditing ABA Fusion '
    'staging platforms. Be precise, avoid false positives, and always '
    'justify severity with evidence from the response body.')


def analyze_finding(llm, platform, endpoint, category, payload, status, body):
    """Return {'is_real': bool, 'severity': str, 'analysis': str,
    'recommendation': str} using the LLM, or None if unavailable."""
    if not llm or not llm.enabled:
        return None
    user = (
        f'Platform: {platform}\nEndpoint: {endpoint}\nFuzz category: '
        f'{category}\nPayload sent: {payload!r}\nHTTP status: {status}\n'
        f'Response body (truncated):\n{(body or "")[:800]}\n\n'
        'Decide whether this response indicates a REAL vulnerability '
        '(vs. normal validation/error behaviour). Respond in JSON: '
        '{"is_real": true|false, "severity": "critical|high|medium|low|info",'
        ' "analysis": "...", "recommendation": "..."}')
    try:
        try:
            data = llm.chat_json(SECURITY_SYSTEM, user, max_tokens=500)
        except ValueError:
            # first attempt produced no JSON (e.g. a long reasoning trace
            # truncated before the answer) -- retry with an explicit
            # "no thinking, JSON only" instruction
            data = llm.chat_json(
                SECURITY_SYSTEM + ' Do not explain your reasoning. Output '
                'only one JSON object.', user, max_tokens=800)
        if not isinstance(data, dict):
            data = {}
        sev = str(data.get('severity', 'info')).lower()
        if sev not in ('critical', 'high', 'medium', 'low', 'info'):
            sev = 'info'
        return {'is_real': bool(data.get('is_real')),
                'severity': sev,
                'analysis': str(data.get('analysis', ''))[:500],
                'recommendation': str(data.get('recommendation', ''))[:300]}
    except (LLMError, ValueError):
        return None


def generate_payloads(llm, category, target_desc, count=5):
    """Ask the LLM for extra category-specific payloads; [] if unavailable."""
    if not llm or not llm.enabled:
        return []
    user = (f'Target: {target_desc}\nVulnerability class: {category}\n'
            f'Produce up to {count} realistic {category.upper()} probe '
            'payloads for authorised security testing of a JSON REST API. '
            'Respond JSON: {"payloads": ["...", ...]}')
    try:
        data = llm.chat_json(SECURITY_SYSTEM, user, max_tokens=800)
        items = data.get('payloads') if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            inner = data.get('result')
            items = inner if isinstance(inner, list) else []
        if not isinstance(items, list):
            items = []
        out = [str(p) for p in items if str(p).strip()]
        return out[:count]
    except (LLMError, ValueError):
        return []


def executive_summary(llm, report_dict):
    """One-paragraph plain-English summary of a run; '' if no LLM."""
    if not llm or not llm.enabled:
        return ''
    compact = {k: report_dict.get(k) for k in
               ('ts', 'total', 'alive', 'findings', 'critical_high')}
    compact['top_findings'] = report_dict.get('finding_details', [])[:10]
    try:
        return llm.chat(
            [{'role': 'system', 'content': SECURITY_SYSTEM},
             {'role': 'user',
              'content': 'Summarise this security scan for management in '
                         'max 6 sentences:\n'
                         + json.dumps(compact, default=str)[:2000]}],
            max_tokens=350)
    except LLMError:
        return ''
