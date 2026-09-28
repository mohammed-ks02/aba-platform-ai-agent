"""Shared core library for the ABA Fusion multi-platform AI agent.

Modules:
    config        -- platform registry, fuzz payloads, runtime configuration
    http_client   -- stdlib HTTP request helper + discovery probe
    classifier    -- response -> (finding_type, severity) classification
    memory        -- SQLite persistence (platforms / findings / patterns)
    token_manager -- JWT login + auto-refresh (re-export of top-level module)
"""

from . import config, http_client, classifier, memory  # noqa: F401
