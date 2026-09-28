"""LLM integration for the ABA Fusion security agent.

Modules:
    base      -- BaseProvider, LLMError, JSON extraction helpers
    providers -- concrete providers (OpenAI-compatible family + Anthropic,
                 Gemini) and the PROVIDERS registry
    client    -- LLMClient facade (selection / fallback chain) and the
                 agent-level AI features (analyze_finding, generate_payloads,
                 executive_summary)

Configure via environment:
    ABA_LLM_PROVIDER   openai|anthropic|gemini|groq|ollama|custom|... ('auto')
    ABA_LLM_MODEL      model name override
    ABA_LLM_API_KEY    key override (provider-specific vars also honoured)
    ABA_LLM_BASE_URL   endpoint override (any OpenAI-compatible server)
    ABA_LLM_FALLBACKS  comma-separated provider fallback chain
"""
from .base import BaseProvider, LLMError, extract_json
from .client import (LLMClient, analyze_finding, available_providers,
                     executive_summary, generate_payloads)
from .providers import (PROVIDERS, AnthropicProvider, GeminiProvider,
                        OpenAICompatibleProvider)

__all__ = ['BaseProvider', 'LLMError', 'extract_json', 'LLMClient',
           'PROVIDERS', 'OpenAICompatibleProvider', 'AnthropicProvider',
           'GeminiProvider', 'available_providers', 'analyze_finding',
           'generate_payloads', 'executive_summary']
