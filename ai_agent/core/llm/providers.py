"""Concrete LLM providers.

Two families:
  * OpenAI-compatible chat/completions servers (the majority): OpenAI,
    Azure OpenAI, Groq, Together, Fireworks, DeepSeek, Mistral, OpenRouter,
    xAI/Grok, Cerebras, SambaNova, GitHub Models, Ollama, LM Studio, vLLM,
    llama.cpp server, LiteLLM proxy... -- these are pure registry entries.
  * Providers with a different wire format: Anthropic Claude, Google Gemini.
    These subclass BaseProvider and translate messages themselves.
"""
from .base import BaseProvider, LLMError


# --------------------------------------------------------------------------
# OpenAI-compatible family (one shared implementation)
# --------------------------------------------------------------------------
class OpenAICompatibleProvider(BaseProvider):
    """POST {base}/chat/completions  -- works for dozens of providers."""

    name = 'openai'
    default_base_url = 'https://api.openai.com/v1'
    auth_style = 'bearer'
    api_key_env = ('ABA_LLM_API_KEY', 'OPENAI_API_KEY')
    base_url_env = ('ABA_LLM_BASE_URL', 'OPENAI_BASE_URL')
    default_model_name = 'gpt-4o-mini'
    # some gateways (NVIDIA NIM reasoning models) only deliver responses in
    # reasonable time over SSE; force streaming for those providers
    stream = False

    def chat(self, messages, temperature=0.2, max_tokens=1024):
        payload = {'model': self.model, 'messages': messages,
                   'temperature': temperature, 'max_tokens': max_tokens}
        if self.stream:
            payload['stream'] = True
            return self._post_sse(f'{self.base_url}/chat/completions',
                                  payload)
        data = self._post_json(f'{self.base_url}/chat/completions', payload)
        try:
            return data['choices'][0]['message']['content'] or ''
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f'{self.name}: unexpected response shape: '
                           f'{str(data)[:200]}') from e


def make_compatible(name, base_url, key_envs, model, base_url_envs=(),
                    requires_key=True, extra_headers=None, stream=False):
    """Factory: declaratively register an OpenAI-compatible provider."""

    class _P(OpenAICompatibleProvider):
        pass

    _P.name = name
    _P.default_base_url = base_url
    _P.api_key_env = tuple(key_envs)
    _P.base_url_env = tuple(base_url_envs) + ('ABA_LLM_BASE_URL',)
    _P.default_model_name = model
    _P.requires_key = requires_key
    _P.stream = stream
    if extra_headers:
        orig = _P._headers

        def _h(self, _extra=extra_headers, _orig=orig):
            h = _orig(self)
            h.update(_extra)
            return h
        _P._headers = _h
    _P.__name__ = f'{name.capitalize()}Provider'
    return _P


# --------------------------------------------------------------------------
# Anthropic (Messages API -- different schema + auth header)
# --------------------------------------------------------------------------
class AnthropicProvider(BaseProvider):
    name = 'anthropic'
    default_base_url = 'https://api.anthropic.com/v1'
    auth_style = 'header-x-api-key'
    api_key_env = ('ANTHROPIC_API_KEY', 'ABA_LLM_API_KEY')
    base_url_env = ('ABA_LLM_BASE_URL',)
    default_model_name = 'claude-sonnet-4-20250514'

    def _headers(self):
        h = super()._headers()
        h['anthropic-version'] = '2023-06-01'
        return h

    def chat(self, messages, temperature=0.2, max_tokens=1024):
        system = '\n'.join(m['content'] for m in messages
                           if m.get('role') == 'system')
        rest = [m for m in messages if m.get('role') != 'system']
        payload = {'model': self.model, 'max_tokens': max_tokens,
                   'temperature': temperature, 'messages': rest}
        if system:
            payload['system'] = system
        data = self._post_json(f'{self.base_url}/messages', payload)
        try:
            blocks = data.get('content', [])
            return ''.join(b.get('text', '') for b in blocks
                           if b.get('type') == 'text')
        except Exception as e:
            raise LLMError(f'anthropic: bad response: {str(data)[:200]}') from e

    def list_models(self):
        import urllib.request
        url = f'{self.base_url}/models'
        req = urllib.request.Request(url, headers=self._headers(),
                                     method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as e:
            raise LLMError(f'anthropic: models: {e}') from e
        return sorted({m['id'] for m in data.get('data', [])
                       if isinstance(m, dict) and m.get('id')})


# --------------------------------------------------------------------------
# Google Gemini (generateContent -- auth via query param)
# --------------------------------------------------------------------------
class GeminiProvider(BaseProvider):
    name = 'gemini'
    default_base_url = ('https://generativelanguage.googleapis.com'
                        '/v1beta')
    auth_style = 'none'  # key goes in the URL
    api_key_env = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ABA_LLM_API_KEY')
    base_url_env = ('ABA_LLM_BASE_URL',)
    default_model_name = 'gemini-2.0-flash'

    def chat(self, messages, temperature=0.2, max_tokens=1024):
        system = '\n'.join(m['content'] for m in messages
                           if m.get('role') == 'system')
        contents = [{'role': 'user' if m['role'] == 'user' else 'model',
                     'parts': [{'text': m['content']}]}
                    for m in messages if m.get('role') != 'system']
        payload = {'contents': contents,
                   'generationConfig': {'temperature': temperature,
                                        'maxOutputTokens': max_tokens}}
        if system:
            payload['systemInstruction'] = {'parts': [{'text': system}]}
        url = (f'{self.base_url}/models/{self.model}:generateContent'
               f'?key={self.api_key}')
        data = self._post_json(url, payload)
        try:
            parts = data['candidates'][0]['content']['parts']
            return ''.join(p.get('text', '') for p in parts)
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f'gemini: bad response: {str(data)[:200]}') from e

    def list_models(self):
        import urllib.request
        url = f'{self.base_url}/models?key={self.api_key}'
        req = urllib.request.Request(url, headers=self._headers(),
                                     method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
        except Exception as e:
            raise LLMError(f'gemini: models: {e}') from e
        out = []
        for m in data.get('models', []) or data.get('sdkModels', []):
            name = m.get('name', '')
            out.append(name[len('models/'):] if name.startswith('models/')
                       else name)
        return sorted(x for x in out if x)


# --------------------------------------------------------------------------
# Registry: every provider the agent knows about out of the box
# --------------------------------------------------------------------------
PROVIDERS = {
    # hosted clouds (all OpenAI-compatible endpoints)
    'openai': OpenAICompatibleProvider,
    'groq': make_compatible('groq', 'https://api.groq.com/openai/v1',
                            ('GROQ_API_KEY', 'ABA_LLM_API_KEY'),
                            'qwen/qwen3.8-27b'),
    'together': make_compatible(
        'together', 'https://api.together.xyz/v1',
        ('TOGETHER_API_KEY', 'ABA_LLM_API_KEY'),
        'meta-llama/Llama-3.3-70B-Instruct-Turbo-Free'),
    'fireworks': make_compatible(
        'fireworks', 'https://api.fireworks.ai/inference/v1',
        ('FIREWORKS_API_KEY', 'ABA_LLM_API_KEY'),
        'accounts/fireworks/models/llama-v3p1-70b-instruct'),
    'deepseek': make_compatible(
        'deepseek', 'https://api.deepseek.com/v1',
        ('DEEPSEEK_API_KEY', 'ABA_LLM_API_KEY'), 'deepseek-chat'),
    'mistral': make_compatible(
        'mistral', 'https://api.mistral.ai/v1',
        ('MISTRAL_API_KEY', 'ABA_LLM_API_KEY'), 'mistral-large-latest'),
    'openrouter': make_compatible(
        'openrouter', 'https://openrouter.ai/api/v1',
        ('OPENROUTER_API_KEY', 'ABA_LLM_API_KEY'),
        'openrouter/auto'),
    'xai': make_compatible('xai', 'https://api.x.ai/v1',
                           ('XAI_API_KEY', 'ABA_LLM_API_KEY'),
                           'grok-4-fast-non-reasoning'),
    'cerebras': make_compatible(
        'cerebras', 'https://api.cerebras.ai/v1',
        ('CEREBRAS_API_KEY', 'ABA_LLM_API_KEY'), 'llama3.1-70b'),
    'sambanova': make_compatible(
        'sambanova', 'https://api.sambanova.ai/v1',
        ('SAMBANOVA_API_KEY', 'ABA_LLM_API_KEY'), 'Meta-Llama-3.3-70B-Instruct'),
    'github': make_compatible(
        'github', 'https://models.inference.ai.azure.com',
        ('GITHUB_TOKEN', 'GH_TOKEN', 'ABA_LLM_API_KEY'), 'gpt-4o'),
    'nvidia': make_compatible(
        'nvidia', 'https://integrate.api.nvidia.com/v1',
        ('NVIDIA_API_KEY', 'NIM_API_KEY', 'ABA_LLM_API_KEY'),
        # Default must be a model that actually responds on the free NIM
        # endpoint. Verified 2026-09-29: nemotron-3-super answers in ~2s over
        # SSE, whereas the previous default (z-ai/glm-5.3-flash) and other
        # flagship reasoning models time out (>50s) on every call.
        'nvidia/nemotron-3-super-120b-a12b', stream=True),
    'azure': make_compatible(
        'azure', 'https://YOUR_RESOURCE.openai.azure.com/openai/deployments/'
                 'YOUR_DEPLOYMENT',
        ('AZURE_OPENAI_API_KEY', 'ABA_LLM_API_KEY'), 'gpt-4o-mini'),
    # native-format APIs
    'anthropic': AnthropicProvider,
    'gemini': GeminiProvider,
    # local / self-hosted servers (no key needed by default)
    'ollama': make_compatible('ollama', 'http://localhost:11434/v1',
                              ('OLLAMA_API_KEY',), 'llama3.1',
                              requires_key=False),
    'lmstudio': make_compatible('lmstudio', 'http://localhost:1234/v1',
                                ('LMSTUDIO_API_KEY',), '',
                                requires_key=False),
    'vllm': make_compatible('vllm', 'http://localhost:8000/v1',
                            ('VLLM_API_KEY',), '', requires_key=False),
    'llamacpp': make_compatible('llamacpp', 'http://localhost:8080/v1',
                                ('LLAMACPP_API_KEY',), 'local-model',
                                requires_key=False),
    'litellm': make_compatible('litellm', 'http://localhost:4000/v1',
                               ('LITELLM_API_KEY', 'ABA_LLM_API_KEY'), '',
                               requires_key=False),
}


class CustomProvider(OpenAICompatibleProvider):
    """User-defined provider: ABA_LLM_PROVIDER=custom with explicit
    ABA_LLM_BASE_URL / ABA_LLM_API_KEY / ABA_LLM_MODEL -- talks to any
    OpenAI-compatible endpoint (covers everything not in PROVIDERS)."""
    name = 'custom'
    default_base_url = ''
    requires_key = False

    def __init__(self, **kw):
        super().__init__(**kw)
        if not self.base_url:
            raise LLMError('custom provider needs ABA_LLM_BASE_URL')
        if not self.model:
            raise LLMError('custom provider needs ABA_LLM_MODEL')


# --------------------------------------------------------------------------
# Model discovery (used by the Web UI provider picker)
# --------------------------------------------------------------------------
# Curated fallbacks used when the live GET /models endpoint is unreachable
# or returns nothing useful (e.g. Azure deployments are not listable).
CURATED_MODELS = {
    # Verified working on the free tiers 2026-09-29 (see tools/llm_provider_test
    # or the model-probe report); ordered fastest/most-reliable first.
    'groq': ['qwen/qwen3.8-27b', 'allam-2-7b',
             'openai/gpt-oss-20b', 'openai/gpt-oss-120b'],
    'nvidia': ['nvidia/nemotron-3-super-120b-a12b',
               'nvidia/nemotron-3-ultra-550b-a55b',
               'openai/gpt-oss-20b', 'meta/llama-3.2-11b-vision-instruct'],
    'openai': ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'o3-mini'],
    'anthropic': ['claude-sonnet-4-20250514', 'claude-3-5-haiku-latest'],
    'gemini': ['gemini-2.0-flash', 'gemini-1.5-pro'],
    'ollama': ['llama3.1', 'qwen2.5-coder', 'deepseek-r1'],
    'custom': [],
}


def list_provider_models(name, base_url=None, api_key=None, timeout=15):
    """Best-effort model catalogue for one provider.

    Tries the provider's live ``GET /models`` endpoint first; on any error
    falls back to a small curated list so the UI always has something to
    show.  Returns {'provider', 'source': 'live'|'fallback'|'none',
    'models': [...], 'error': str|None}.
    """
    cls = PROVIDERS.get(name)
    if cls is None and name != 'custom':
        return {'provider': name, 'source': 'none', 'models': [],
                'error': f'unknown provider: {name}'}
    try:
        prov = (cls if name == 'custom' else cls)(base_url=base_url,
                                                  api_key=api_key,
                                                  timeout=timeout)
        models = prov.list_models()
        if models:
            return {'provider': name, 'source': 'live', 'models': models,
                    'error': None}
        err = None
    except Exception as e:
        models, err = [], str(e)[:200]
    fallback = CURATED_MODELS.get(name, [])
    if fallback:
        return {'provider': name, 'source': 'fallback', 'models': fallback,
                'error': err}
    dflt = getattr(cls, 'default_model_name', '') if cls else ''
    return {'provider': name, 'source': 'none',
            'models': [dflt] if dflt else [], 'error': err}
