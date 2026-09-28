#!/usr/bin/env python3
"""Live LLM provider test: run a full agent testing session through each
configured provider/model and verify the model's reasoning quality.

Providers are defined by env vars (comma-separated lists, positionally paired):

    ABA_LLM_PROVIDER  = groq,nvidia
    ABA_LLM_MODEL     = qwen/qwen3.8-27b,z-ai/glm-5.3-flash
    GROQ_API_KEY      = gsk_...
    NVIDIA_API_KEY    = nvapi-...

Usage:
    python tools/llm_provider_test.py                 # connectivity + AI features only
    python tools/llm_provider_test.py --full-session  # + live discovery & fuzz via core.runner
    python tools/llm_provider_test.py --provider groq --model qwen/qwen3.8-27b ...
"""
import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
_AI_AGENT = os.path.join(_PKG_ROOT, 'ai_agent')
for p in (_AI_AGENT, _PKG_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.llm.client import LLMClient, analyze_finding, generate_payloads, executive_summary  # noqa: E402
from core.llm.base import LLMError  # noqa: E402


def smoke(client, label):
    """Basic chat round-trip; returns latency + answer snippet."""
    import time
    t0 = time.time()
    out = client.chat([{'role': 'user',
                        'content': 'Reply with exactly: PONG'}],
                      max_tokens=64, temperature=0)
    dt = time.time() - t0
    ok = 'pong' in out.lower()
    return {'check': 'smoke_chat', 'ok': ok, 'latency_s': round(dt, 2),
            'reply': out[:80]}


def ai_features(client):
    """Exercise the three agent-level AI features against real payloads."""
    res = {}
    an = analyze_finding(
        client, 'stg-dp', 'https://stg-dp-mgr.abafusion.ai/connectors',
        'xss', '<script>alert(1)</script>', 422,
        '{"code":"validation_error","detail":"Request validation failed",'
        '"errors":[{"type":"model_attributes_type"}]}')
    if isinstance(an, dict):
        res['analyze_finding'] = {
            'ok': True,
            'is_real': an.get('is_real'),
            'severity': an.get('severity'),
            'analysis': (an.get('analysis') or '')[:160]}
    else:
        res['analyze_finding'] = {'ok': False,
                                  'error': f'unexpected shape: {an!r}'[:200]}
    pl = generate_payloads(client, 'ssti',
                           'FastAPI JSON endpoint /connectors (pydantic)', 3)
    res['generate_payloads'] = {'ok': len(pl) > 0, 'payloads': pl[:3]}
    summ = executive_summary(client, {'ts': datetime.now().isoformat(),
                                      'total': 9, 'alive': 9, 'findings': 44,
                                      'critical_high': 0,
                                      'top_findings': []})
    res['executive_summary'] = {'ok': bool(summ.strip()),
                                'text': summ[:200]}
    return res


def full_session(provider, model):
    """Run the actual agent pipeline (auth + discovery + DP fuzz + report)
    with this provider active for finding analysis / summary."""
    from core import runner
    argv = ['--fresh', '--provider', provider, '--quick']
    if model:
        argv += ['--model', model]
    return runner.main(argv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', default=os.environ.get('ABA_LLM_PROVIDER'))
    ap.add_argument('--model', default=os.environ.get('ABA_LLM_MODEL'))
    ap.add_argument('--full-session', action='store_true',
                    help='also run the complete agent pipeline per provider')
    args = ap.parse_args()

    providers = [p.strip() for p in (args.provider or '').split(',') if p.strip()]
    models = [m.strip() for m in (args.model or '').split(',')]
    if not providers:
        print('set ABA_LLM_PROVIDER (or --provider), e.g. "groq,nvidia"')
        return 2

    all_results = {}
    exit_code = 0
    for i, prov in enumerate(providers):
        model = models[i] if i < len(models) else ''
        print('=' * 64)
        print(f'PROVIDER: {prov}   MODEL: {model or "(default)"}')
        print('=' * 64)
        r = {'provider': prov, 'model': model}
        try:
            client = LLMClient(provider=prov, model=model or None, timeout=240)
            if not client.enabled:
                raise LLMError('provider not usable (missing key?)')
            st = client.status()[0]
            print(f'  base_url: {st["base_url"]}')
            for fn, label in ((lambda: smoke(client, prov), 'smoke'),
                              (lambda: ai_features(client), 'ai_features')):
                out = fn()
                r[label] = out
                if isinstance(out, dict) and 'ok' in out:
                    ok = bool(out['ok'])
                elif isinstance(out, dict):
                    vals = [v.get('ok') for v in out.values()
                            if isinstance(v, dict)]
                    ok = bool(vals) and all(vals)
                else:
                    ok = False
                print(f'  [{"PASS" if ok else "CHECK"}] {label}: '
                      f'{json.dumps(out, default=str)[:220]}')
        except Exception as e:
            r['error'] = str(e)[:300]
            exit_code = 1
            print(f'  [FAIL] {e}')
        if args.full_session and 'error' not in r:
            print('\n  --- FULL AGENT SESSION ---')
            try:
                rpt = full_session(prov, model or None)
                r['session'] = {'platforms': rpt['total'],
                                'alive': rpt['alive'],
                                'findings': rpt['findings']}
            except Exception as e:
                r['session'] = {'error': str(e)[:200]}
                exit_code = 1
        all_results[f'{prov}:{model or "default"}'] = r
        print()

    out_dir = os.path.join(_AI_AGENT, 'data', 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'llm_provider_test_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    with open(path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f'Report: {path}')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
