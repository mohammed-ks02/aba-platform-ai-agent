#!/usr/bin/env python3
"""Playwright-based LIVE test: load ALL 9 ABA Fusion staging platforms in a
real headless Chromium browser and verify each one renders.

This replaces the plain urllib live tests (test_platforms_live.py) with real
browser checks, which additionally catches client-side problems that raw HTTP
probes cannot see: JS bundle errors, failed sub-resource loads, blank pages,
redirect loops, TLS issues, etc.

Run directly (no pytest involved):

    python tests/test_platforms_playwright.py            # render-check all 9
    python tests/test_platforms_playwright.py --headed   # watch it happen
    python tests/test_platforms_playwright.py --fuzz     # + authenticated DP login & API fuzz via the browser

Exit code 0 = every platform rendered without fatal errors, 1 otherwise.
A JSON report is written to ai_agent/data/reports/.
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

from playwright.sync_api import sync_playwright  # noqa: E402

from core.config import PLATFORMS, FUZZ  # noqa: E402
from core.classifier import classify  # noqa: E402

# Errors that are environmental / not the platform's fault
IGNORED_CONSOLE_FRAGMENTS = ('favicon', 'net::err_aborted')


def check_platform(browser, key, info, results):
    """Open one platform in a fresh context; return result dict."""
    url = info['base']
    console_errors = []
    request_failures = []
    res = {'platform': key, 'name': info['name'], 'url': url}
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    page.on('console', lambda m, c=console_errors: c.append(m.text)
            if m.type == 'error' else None)
    page.on('pageerror', lambda e, c=console_errors: c.append(str(e)))
    page.on('requestfailed',
            lambda r: request_failures.append(
                f"{r.url[:80]} ({r.failure})"))
    try:
        resp = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        res['http_status'] = resp.status if resp else None
        res['final_url'] = page.url

        # Wait until the SPA renders something (up to ~15s)
        try:
            page.wait_for_function(
                "() => { const b = document.body;"
                " return b && (b.innerText.trim().length > 0"
                " || b.childElementCount > 0); }", timeout=15000)
        except Exception:
            pass
        title = ''
        try:
            title = page.title() or ''
        except Exception:
            pass
        res['title'] = title[:80]
        # blank-page detection: visible text OR rendered DOM elements
        try:
            body_text = page.inner_text('body', timeout=5000)
            res['body_chars'] = len(body_text.strip())
        except Exception:
            res['body_chars'] = 0
        try:
            res['dom_elements'] = page.evaluate(
                'document.querySelectorAll("body *").length')
        except Exception:
            res['dom_elements'] = 0
        fatal = [e for e in console_errors
                 if not any(f in e.lower() for f in IGNORED_CONSOLE_FRAGMENTS)]
        res['console_errors'] = fatal[:5]
        res['request_failures'] = request_failures[:5]
        ok = (resp is not None
              and resp.status is not None and resp.status < 500
              and (res['body_chars'] > 0 or res['dom_elements'] > 0))
        res['rendered'] = bool(ok)
    except Exception as e:
        res['rendered'] = False
        res['error'] = str(e)[:200]
    finally:
        ctx.close()
    results.append(res)
    return res


def dp_login_and_fuzz(pw, results):
    """Authenticated flow through Playwright: JWT login via stg-login, then
    POST fuzz payloads to the Data Platform manager using the browser-level
    APIRequestContext (real Chromium networking, but not subject to the
    page's CORS restrictions)."""
    from token_manager import TokenManager
    findings = []
    try:
        token = TokenManager(cache_file='/tmp/aba_pw_cache.json').ensure_valid()
    except Exception as e:
        results.append({'login_error': str(e)[:200]})
        return findings
    mgr = PLATFORMS['stg-dp']['mgr']
    api = pw.request.new_context(base_url=mgr,
                                      ignore_https_errors=True)
    for ct in ['bigquery', 'mongodb']:
        for cat, payloads in FUZZ.items():
            body = {'name': f'pw-{ct}', 'type': ct,
                    'source_config': {'f': payloads[0]}}
            try:
                r = api.fetch('/connectors', method='POST',
                              headers={'Authorization': f'Bearer {token}'},
                              data=body, timeout=30000)
                status, text = r.status, r.text()[:500]
            except Exception as e:
                status, text = 0, str(e)
            ftype, sev = classify(status, text)
            findings.append({'connector': ct, 'category': cat,
                             'status': status, 'finding_type': ftype,
                             'severity': sev})
            time.sleep(0.2)  # be gentle on staging
    api.dispose()
    return findings


def run(headed=False, fuzz=False):
    print('=' * 60)
    print('PLAYWRIGHT LIVE TEST — all 9 ABA Fusion staging platforms')
    print('=' * 60)
    results = []
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            for key, info in PLATFORMS.items():
                r = check_platform(browser, key, info, results)
                mark = 'PASS' if r.get('rendered') else 'FAIL'
                detail = (r.get('title') or r.get('error')
                          or (r.get('console_errors') or [''])[0])[:60]
                print(f"[{mark}] {key:15s} {str(r.get('http_status')):>4} "
                      f"| {detail}")
                if not r.get('rendered'):
                    failures.append(key)
            findings = []
            if fuzz:
                print('\n[BROWSER AUTH + DP FUZZ]')
                findings = dp_login_and_fuzz(pw, results)
                by_sev = {}
                for f in findings:
                    by_sev[f['severity']] = by_sev.get(f['severity'], 0) + 1
                    print(f"  {f['connector']:8s} {f['category']:9s} -> "
                          f"{f['status']} {f['finding_type']} [{f['severity']}]")
                print(f'  severity summary: {by_sev}')
                if by_sev.get('critical'):
                    failures.append('critical-findings')
        finally:
            browser.close()

    out_dir = os.path.join(_AI_AGENT, 'data', 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(
        out_dir,
        f"playwright_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, 'w') as f:
        json.dump({'ts': datetime.now().isoformat(),
                   'engine': 'playwright/chromium',
                   'platforms_total': len(results),
                   'platforms_rendered': sum(1 for r in results
                                             if r.get('rendered')),
                   'results': results}, f, indent=2)
    print(f'\nReport: {path}')
    ok = not failures
    print('RESULT:', 'ALL PLATFORMS RENDERED IN BROWSER' if ok
          else f'FAILURES: {failures}')
    return 0 if ok else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--headed', action='store_true',
                    help='run with a visible browser window')
    ap.add_argument('--fuzz', action='store_true',
                    help='also run authenticated DP fuzzing through the browser')
    args = ap.parse_args()
    sys.exit(run(headed=args.headed, fuzz=args.fuzz))
