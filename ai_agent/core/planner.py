#!/usr/bin/env python3
"""Natural-language test planning.

Lets a human type things like:

    "check login page UX and how fast analytics loads, then fuzz search"

and turns it into a structured plan that both the CLI and web UI execute:

    {
      "platforms": ["stg-analytics", ...],     # subset of PLATFORMS keys
      "dimensions": ["performance", "ux"],     # suite dims + 'ux'/'security'
      "notes": "...",                          # what the model will do
      "focus": ["login page", "search fuzzing"]
    }

``interpret_prompt`` uses the configured LLM when available; without an LLM
(or on parse failure) ``keyword_plan`` provides deterministic fallback so the
feature always works offline.
"""
import re

from .config import PLATFORMS
from .suite import DIMENSIONS

ALL_DIMS = list(DIMENSIONS) + ['ux']

_PLAT_WORDS = {
    'dp': 'stg-dp', 'data platform': 'stg-dp', 'connector': 'stg-dp',
    'analytics': 'stg-analytics', 'pulse': 'stg-pulse', 'orbit': 'stg-orbit',
    'mate': 'stg-mate', 'perf': 'stg-perf', 'performance app': 'stg-perf',
    'agentic': 'stg-agentic', 'agent ai': 'stg-agentic',
    'orch': 'stg-orch', 'orchestration': 'stg-orch', 'automation': 'stg-orch',
    'forge': 'stg-forge', 'fusion forge': 'stg-forge',
}

_DIM_HINTS = [
    (('slow', 'fast', 'latency', 'performance', 'speed', 'load time', 'ttfb'),
     'performance'),
    (('limit', 'rate', 'burst', 'oversized', 'big payload', 'stress',
      'max size', 'throttl'), 'limits'),
    (('security', 'vuln', 'inject', 'xss', 'sql', 'fuzz', 'attack', 'pentest',
      'exploit', 'ssti', 'ssrf'), 'security'),
    (('work', 'works', 'functional', 'broken', 'button', 'link', 'page load',
      'reachable', 'render', '404', 'blank'), 'functionality'),
    (('logic', 'flow', 'consisten', 'idempoten', 'duplicate', 'edge case',
      'workflow', 'validation'), 'logic'),
    (('ux', 'ui', 'design', 'usability', 'look', 'feel', 'user experience',
      'interface', 'accessib', 'screenshot', 'visual'), 'ux'),
]


def keyword_plan(prompt):
    """Deterministic fallback planner from keywords."""
    p = (prompt or '').lower()
    dims = []
    for hints, dim in _DIM_HINTS:
        if any(h in p for h in hints) and dim not in dims:
            dims.append(dim)
    plats = []
    for word, key in _PLAT_WORDS.items():
        if word in p and key not in plats:
            plats.append(key)
    focus = re.findall(r'"([^"]+)"|\'([^\']+)\'', p)
    focus = [a or b for a, b in focus]
    return {
        'platforms': plats or list(PLATFORMS),
        'dimensions': dims or ['functionality'],
        'focus': focus,
        'source': 'keywords',
        'notes': ('Matched dimensions ' + ','.join(dims or ['functionality'])
                  + (' on ' + ','.join(plats) if plats else ' on all 9 platforms')),
    }


PLAN_SYSTEM = (
    "You are the planning brain of a web-platform test agent. Convert the "
    "user's request into a JSON test plan. Valid dimensions: performance, "
    "limits, security, functionality, logic, ux. Valid platform keys: "
    "{keys}. Reply ONLY with JSON of shape: "
    '{{"platforms": ["..."], "dimensions": ["..."], '
    '"focus": ["short phrases"], "notes": "one sentence describing what you '
    'will do"}}. Use ALL platform keys when the user does not name one.')


def interpret_prompt(prompt, llm=None):
    """Return a structured plan dict; never raises."""
    if llm is None:
        return keyword_plan(prompt)
    try:
        raw = llm.chat(
            [{'role': 'system',
              'content': PLAN_SYSTEM.format(keys=list(PLATFORMS))},
             {'role': 'user', 'content': prompt}],
            temperature=0.1, max_tokens=500)
        from .llm.base import extract_json
        data = extract_json(raw)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise ValueError('model did not return a JSON object')
        # sanitise
        valid_p = set(PLATFORMS)
        plats = [k for k in data.get('platforms', []) if k in valid_p] \
            or list(PLATFORMS)
        dims = [d for d in data.get('dimensions', []) if d in ALL_DIMS] \
            or ['functionality']
        return {'platforms': plats, 'dimensions': dims,
                'focus': [str(f)[:80] for f in data.get('focus', [])][:6],
                'notes': str(data.get('notes', ''))[:300],
                'source': 'llm'}
    except Exception as e:
        plan = keyword_plan(prompt)
        plan['notes'] += f' (LLM planning unavailable: {str(e)[:60]})'
        plan['source'] = 'keywords-fallback'
        return plan


def plan_to_argv(plan):
    """Map a plan onto runner CLI flags (used by the web UI launcher)."""
    argv = []
    dims = plan.get('dimensions', [])
    if 'security' not in dims:
        argv.append('--no-fuzz')
    if len(plan.get('platforms', [])) < len(PLATFORMS):
        pass  # --platforms handled natively by runner
    return argv
