#!/usr/bin/env python3
"""LIVE integration test: probe ALL 9 ABA Fusion staging platforms.

Not part of the default pytest run (they are network-dependent).  Run with:

    python tests/test_platforms_live.py            # discovery only
    python tests/test_platforms_live.py --fuzz     # + authenticated API fuzz
    python -m pytest tests/test_platforms_live.py -v   # or via pytest

Exit code 0 = all checks passed, 1 = at least one platform unreachable
or a critical finding appeared.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_AI_AGENT = os.path.join(os.path.dirname(_HERE), 'ai_agent')
for p in (_AI_AGENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import PLATFORMS, FUZZ          # noqa: E402
from core.http_client import req, discover       # noqa: E402
from core.classifier import classify             # noqa: E402


def check_platform(key, info):
    """Return result dict for one platform."""
    r = discover(info['base'], timeout=10)
    out = {'platform': key, 'name': info['name'], 'base': info['base'],
           'alive': r['alive'], 'http_status': r['code'],
           'title': r['title'][:60], 'content_type': r['ct'][:40]}
    # Data Platform: also probe its API and manager hosts
    for extra_key, url in (('api', info.get('api')), ('mgr', info.get('mgr'))):
        if url:
            e = discover(url, timeout=10)
            out[extra_key] = {'alive': e['alive'], 'status': e['code']}
    return out


def fuzz_dp(token):
    """Authenticated fuzz of the Data Platform manager; returns findings."""
    mgr = PLATFORMS['stg-dp']['mgr']
    findings = []
    for ct in ['bigquery', 'mongodb']:
        for cat, payloads in FUZZ.items():
            body = {'name': f'itest-{ct}', 'type': ct,
                    'source_config': {'f': payloads[0]}}
            rr = req('POST', f'{mgr}/connectors', body, token)
            ftype, sev = classify(rr['status'], rr.get('body', ''))
            findings.append({'connector': ct, 'category': cat,
                             'status': rr['status'], 'finding_type': ftype,
                             'severity': sev})
            time.sleep(0.2)  # be gentle on staging
    return findings


def run(fuzz=False):
    print('=' * 60)
    print('LIVE TEST — all 9 ABA Fusion staging platforms')
    print('=' * 60)
    results = []
    failures = []
    for key, info in PLATFORMS.items():
        r = check_platform(key, info)
        results.append(r)
        mark = 'PASS' if r['alive'] else 'FAIL'
        print(f"[{mark}] {key:15s} {r['http_status']:>3} | {r['title']}")
        if not r['alive']:
            failures.append(key)

    findings = []
    if fuzz:
        print('\n[AUTH + DP FUZZ]')
        try:
            from token_manager import TokenManager
            token = TokenManager().ensure_valid()
            print(f'  login: OK (user={TokenManager().user_info.get("username")})')
            findings = fuzz_dp(token)
            by_sev = {}
            for f in findings:
                by_sev[f['severity']] = by_sev.get(f['severity'], 0) + 1
                print(f"  {f['connector']:8s} {f['category']:9s} -> "
                      f"{f['status']} {f['finding_type']} [{f['severity']}]")
            print(f'  severity summary: {by_sev}')
            if by_sev.get('critical'):
                failures.append('critical-findings')
        except Exception as e:
            print(f'  fuzz skipped/failed: {e}')
            failures.append('fuzz-error')

    out_dir = os.path.join(_AI_AGENT, 'data', 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(
        out_dir, f"live_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, 'w') as f:
        json.dump({'ts': datetime.now().isoformat(),
                   'platforms_total': len(results),
                   'platforms_alive': sum(1 for r in results if r['alive']),
                   'results': results, 'findings': findings}, f, indent=2)
    print(f'\nReport: {path}')
    ok = not failures
    print('RESULT:', 'ALL PLATFORMS REACHABLE' if ok
          else f'FAILURES: {failures}')
    return 0 if ok else 1


# ---- pytest entry points (marked live; deselected by default) -------------
import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get('ABA_LIVE_TESTS') != '1',
    reason='set ABA_LIVE_TESTS=1 to run live platform tests')


@pytest.mark.parametrize('key', list(PLATFORMS))
def test_platform_reachable(key):
    r = discover(PLATFORMS[key]['base'], timeout=10)
    assert r['alive'], f"{key} ({PLATFORMS[key]['base']}) unreachable"


def test_login_live():
    from token_manager import TokenManager
    token = TokenManager(cache_file='/tmp/aba_itest_cache.json').ensure_valid()
    assert token and token.count('.') == 2


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuzz', action='store_true',
                    help='also run authenticated DP API fuzzing')
    sys.exit(run(fuzz=ap.parse_args().fuzz))
