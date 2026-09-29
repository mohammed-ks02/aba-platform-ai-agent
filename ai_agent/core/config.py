"""Configuration: platform registry, fuzz payloads, data paths.

All paths are resolved relative to the ``ai_agent`` package root (the parent
of this ``core`` package), so agents work no matter what the current working
directory is.  Override the data directory with the ``ABA_AGENT_DATA_DIR``
environment variable.
"""
import os

from .env_loader import ensure_loaded as _ensure_dotenv
_ensure_dotenv()

# ai_agent/ package root
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.environ.get('ABA_AGENT_DATA_DIR',
                          os.path.join(AGENT_ROOT, 'data'))

CONFIG = {
    'data_dir': DATA_DIR,
    'memory_db': os.path.join(DATA_DIR, 'memory.db'),
    'reports_dir': os.path.join(DATA_DIR, 'reports'),
    'timeout': 8,
}

PLATFORMS = {
    'stg-dp': {'name': 'Data Platform', 'base': 'https://stg-dp.abafusion.ai',
               'api': 'https://stg-dp.abafusion.ai/api/v1',
               'mgr': 'https://stg-dp-mgr.abafusion.ai',
               'type': 'data_platform'},
    'stg-analytics': {'name': 'Analytics',
                      'base': 'https://stg-analytics.abafusion.ai',
                      'type': 'analytics'},
    # These three SPAs are served under a sub-path, not the bare host -- probe
    # the app, not the redirect shell (see the original platform spec).
    'stg-pulse': {'name': 'Pulse',
                  'base': 'https://stg-pulse.abafusion.ai/project',
                  'type': 'pulse'},
    'stg-orbit': {'name': 'Orbit', 'base': 'https://stg-orbit.abafusion.ai',
                  'type': 'orbit'},
    'stg-mate': {'name': 'Mate', 'base': 'https://stg-mate.abafusion.ai/mate',
                 'type': 'mate'},
    'stg-perf': {'name': 'Performance', 'base': 'https://stg-perf.abafusion.ai',
                 'type': 'perf'},
    'stg-agentic': {'name': 'Agentic AI',
                    'base': 'https://stg-agentic.abafusion.ai',
                    'type': 'agentic'},
    'stg-orch': {'name': 'Orchestration',
                 'base': 'https://stg-orch.abafusion.ai/automation',
                 'type': 'orchestration'},
    # NOTE: the forge root URL is unreachable; the app lives under /fusionforge/
    'stg-forge': {'name': 'Fusion Forge',
                  'base': 'https://stg-forge.abafusion.ai/fusionforge/',
                  'type': 'forge'},
}

FUZZ = {
    'xss': ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>'],
    'sql': ["' OR '1'='1", "'; DROP TABLE users; --"],
    'path': ['../../../etc/passwd', '..%252f..%252fetc/passwd'],
    'cmd': ['; ls -la', '$(whoami)'],
    'nosql': ['{"$ne": null}', '{"$gt": ""}'],
    'ssrf': ['http://127.0.0.1:8080/admin', 'file:///etc/passwd'],
    'redirect': ['//evil.com', 'http://evil.com'],
    'ssti': ['{{1327*1331}}', '${1327*1331}'],
    'xxe': ["<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"],
}
# Note: 'empty'/'boundary' categories were removed -- analyze_response has no
# signal for them, so they only added HTTP churn with no possible finding.

# Connector types used when fuzzing the Data Platform manager API
CONNECTOR_TYPES = ['bigquery', 'mongodb', 'slack']

SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']
