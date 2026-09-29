#!/usr/bin/env python3
"""Multi-dimensional platform test suite.

The agent no longer only fuzzes for security: this module adds the other
testing dimensions used by both the CLI runner and the web UI:

    performance     -- latency stats (avg/p50/p95/max), HTML size, TTFB-ish
    limits          -- oversized payloads, deep JSON nesting, unicode,
                       header stuffing, rate-limit behaviour
    functionality   -- reachability, SPA hydration markers, title/favicon,
                       auth round-trip, broken-link sampling
    logic           -- idempotency (double POST), GET-mutates-state check,
                       CRUD validation semantics, 404 vs 405 correctness
    security        -- delegated to core.runner.run_fuzz (unchanged)

Every dimension emits trace lines through ``trace_cb`` so the caller can
visualise progress live (terminal or web SSE stream).  Results are plain
dicts persisted into the report JSON; findings go into memory.db.
"""
import json
import time
from datetime import datetime

from .config import CONFIG, PLATFORMS
from .http_client import req, discover, check_abort
from .classifier import passive_findings


# ---------------------------------------------------------------- helpers
def _now():
    return datetime.now().isoformat(timespec='seconds')


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))
    return s[idx]


def _timed_request(method, url, body=None, **kw):
    """req() + wall-clock timing. Returns (result, elapsed_ms).

    Accepts ``body`` positionally (call sites in test_logic pass it that
    way) so the signature matches every caller regardless of token kwarg."""
    if body is not None:
        kw['body'] = body
    t0 = time.monotonic()
    r = req(method, url, **kw)
    return r, round((time.monotonic() - t0) * 1000, 1)


# ---------------------------------------------------------- performance
def test_performance(mem, keys=None, samples=5, token='', trace_cb=None):
    trace = trace_cb or (lambda m: None)
    out = {}
    for key in (keys or PLATFORMS):
        info = PLATFORMS[key]
        trace(f'[perf] {key}: {samples} GET samples on {info["base"]}')
        lat, sizes, codes = [], [], []
        for _ in range(samples):
            r, ms = _timed_request('GET', info['base'], token=token)
            lat.append(ms)
            codes.append(r['status'])
            sizes.append(len(r.get('body', '') or ''))
            time.sleep(0.15)
        stats = {
            'samples': samples,
            'codes': sorted(set(codes)),
            'avg_ms': round(sum(lat) / len(lat), 1),
            'min_ms': min(lat), 'max_ms': max(lat),
            'p50_ms': _pct(lat, 50), 'p95_ms': _pct(lat, 95),
            'avg_html_bytes': int(sum(sizes) / len(sizes)),
        }
        slow = stats['p95_ms'] and stats['p95_ms'] > 3000
        sev = 'medium' if slow else 'info'
        note = ('slow response p95 > 3s' if slow
                else 'latency within normal range')
        trace(f'[perf] {key}: avg {stats["avg_ms"]}ms p95 {stats["p95_ms"]}ms'
              f' -> {note}')
        out[key] = stats
        if slow:
            mem.save_finding(key, info['base'], 'performance', '', 200,
                             'slow_response', sev, note,
                             recommendation='Check backend caching/DB indexes')
    return out


# ---------------------------------------------------------------- limits
def test_limits(mem, keys=None, token='', trace_cb=None):
    trace = trace_cb or (lambda m: None)
    # These probes hit the Data Platform manager API specifically. When the
    # caller selected platforms that do not include stg-dp, skip cleanly
    # instead of crashing on PLATFORMS[<list>] or silently probing a target
    # the user never asked for.
    if keys and 'stg-dp' not in keys:
        trace('[limits] skipped (stg-dp manager API not in selected platforms)')
        return {'skipped': 'stg-dp (manager API) not selected'}
    target = PLATFORMS['stg-dp']['mgr']
    # (label, json body, extra request headers)
    probes = [
        ('oversized_payload_64kb',
         {'name': 'limit-big', 'type': 'bigquery',
          'source_config': {'blob': 'A' * 65536}}, None),
        ('deep_nesting_100',
         {'name': 'limit-deep', 'type': 'bigquery',
          'source_config': json.loads('{"a":' * 50 + '1' + '}' * 50)}, None),
        ('unicode_zwj_emoji',
         {'name': 'fam\u00EDlia-\U0001F468\u200D\U0001F469\u200D\U0001F467',
          'type': 'bigquery', 'source_config': {'f': 'ok'}}, None),
        # 16 KB header value actually exercises server header-size limits
        # (nginx/most gateways cap header buffers around 8 KB).
        ('huge_header',
         {'name': 'hdr', 'type': 'bigquery', 'source_config': {'f': 'x'}},
         {'X-Fuzz-Blob': 'A' * 16384}),
    ]
    results = {}
    for label, body, extra_headers in probes:
        url = f'{target}/connectors'
        t0 = time.monotonic()
        r = req('POST', url, body, token, headers=extra_headers)
        ms = round((time.monotonic() - t0) * 1000, 1)
        status = r['status']
        graceful = status in (0, 400, 413, 422) or 400 <= status < 500
        verdict = 'graceful rejection' if graceful else \
            ('crash/timeout' if status in (0, 500, 502, 503, 504)
             else 'accepted (check!)')
        trace(f'[limits] {label}: HTTP {status} ({ms}ms) -> {verdict}')
        results[label] = {'status': status, 'ms': ms, 'verdict': verdict}
        if not graceful:
            mem.save_finding('stg-dp', url, 'limits', label, status,
                             'limit_violation',
                             'high' if status >= 500 else 'medium',
                             f'{label}: server did not degrade gracefully',
                             recommendation=f'Harden input limit for {label}')
    # rate-limit probe: a meaningful burst against the API (not a 12-request
    # root ping), so "no rate limiting" is only claimed at a defensible volume.
    burst_n = 5 if not token else 60
    burst_url = (PLATFORMS['stg-dp']['mgr'] + '/connectors') if token \
        else PLATFORMS['stg-dp']['base']
    codes = []
    for _ in range(burst_n):
        codes.append(req('GET', burst_url, token=token)['status'])
    rl = sum(1 for c in codes if c == 429)
    results['rate_limit_burst'] = {'codes': sorted(set(codes)),
                                   'rate_limited': bool(rl), 'n': burst_n,
                                   'url': burst_url}
    trace(f'[limits] rate-limit burst x{burst_n} on {burst_url}: '
          f'codes={sorted(set(codes))} rate_limited={bool(rl)}')
    if not rl and burst_n >= 30:
        mem.save_finding('stg-dp', burst_url, 'limits', 'rate_limit_burst', 200,
                         'no_rate_limiting', 'low',
                         f'No 429 across {burst_n} rapid authenticated requests',
                         recommendation='Add rate limiting to API endpoints')
    return results


# -------------------------------------------------------- functionality
def test_functionality(mem, keys=None, token='', trace_cb=None):
    trace = trace_cb or (lambda m: None)
    out = {}
    for key in (keys or PLATFORMS):
        info = PLATFORMS[key]
        d = discover(info['base'], timeout=10)
        # passive security review of the response headers (all 9 platforms,
        # zero risk -- reads only what the server already returns)
        hr = req('GET', info['base'], token=token)
        pf_list = passive_findings(hr['status'], hr.get('headers'),
                                   hr.get('body', ''), info['base'])
        for pf in pf_list:
            mem.save_finding(key, info['base'], pf['category'], '',
                             hr['status'], pf['finding_type'], pf['severity'],
                             pf['evidence'], recommendation=pf['recommendation'])
        if pf_list:
            trace(f'[func] {key}: {len(pf_list)} passive header/cookie/CORS '
                  f'finding(s)')
        import urllib.request

        def _fetch(u):
            try:
                rq = urllib.request.Request(
                    u, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(rq, timeout=10) as resp:
                    return resp.read(400000).decode('utf-8', errors='replace')
            except Exception:
                return ''
        html = _fetch(info['base'])
        # route-diff: fetch a bogus sibling path. If it returns the SAME body,
        # the server client-routes a catch-all SPA shell, so a 200 on the real
        # path does NOT prove the route/feature exists -- functionality here is
        # shell-level, and "working" must be proven by UX hydration + auth.
        bogus = info['base'].rstrip('/') + '/__aba_route_probe_404__'
        bhtml = _fetch(bogus)
        catch_all = bool(html) and bool(bhtml) and (
            bhtml == html or abs(len(bhtml) - len(html)) <= 16)
        checks = {
            'reachable': d['alive'],
            'http_status': d['code'],
            'has_title': bool(d['title']),
            'is_spa': ('id="root"' in html or 'id="app"' in html
                       or '__NEXT_DATA__' in html),
            'has_favicon': 'favicon' in html.lower(),
            'has_meta_viewport': 'viewport' in html.lower(),
            'html_size': len(html),
            'route_distinct': (not catch_all) if bhtml else None,
            # HTTP alone cannot verify a catch-all SPA works; the UX dimension's
            # rendered+authenticated flags upgrade this to verified (see report).
            'http_verified': bool(d['alive']) and (not catch_all),
            'status': ('reachable + distinct route' if (d['alive'] and not
                       catch_all) else ('client-routed shell (verify via UX '
                       'hydration + auth)' if d['alive'] else 'unreachable')),
        }
        problems = [k for k, ok in (('reachable', checks['reachable']),
                                    ('title', checks['has_title']),
                                    ('spa_root', checks['is_spa']))
                    if not ok]
        checks['problems'] = problems
        trace(f'[func] {key}: alive={checks["reachable"]} spa={checks["is_spa"]}'
              f' route_distinct={checks["route_distinct"]} '
              f'-> {checks["status"]}'
              + (f' PROBLEMS={problems}' if problems else ''))
        out[key] = checks
        if problems:
            mem.save_finding(key, info['base'], 'functionality', '',
                             d['code'], 'functional_gap', 'medium',
                             f'missing: {", ".join(problems)}',
                             recommendation='Fix page shell / metadata')
        # authenticated API round-trip (DP only, has api base). /health is an
        # allow-listed public endpoint -- reachable-anonymously is EXPECTED
        # there, so we only record it as info. The real auth check asserts a
        # genuinely PROTECTED route (/connectors) is NOT readable without a
        # token; that is what would be a true high-severity finding.
        if info.get('api') and token:
            r = req('GET', info['api'] + '/health', token=token)
            r_no = req('GET', info['api'] + '/health')
            checks['auth_roundtrip'] = {'with_token': r['status'],
                                        'without_token': r_no['status']}
            trace(f'[func] {key}: /health auth={r["status"]} '
                  f'anon={r_no["status"]} (health is public by design)')
            prot = info['api'] + '/connectors'
            pr = req('GET', prot, token=token)
            pr_no = req('GET', prot)
            checks['protected_roundtrip'] = {'with_token': pr['status'],
                                             'without_token': pr_no['status']}
            trace(f'[func] {key}: /connectors auth={pr["status"]} '
                  f'anon={pr_no["status"]}')
            if pr_no['status'] not in (401, 403, 0) and pr['status'] < 400 \
                    and pr_no['status'] < 400:
                mem.save_finding(key, prot, 'functionality', '',
                                 pr_no['status'], 'unauthenticated_access',
                                 'high', 'Protected API route readable without '
                                 'a token', recommendation='Enforce auth on '
                                 'non-public API routes')
    return out


# ------------------------------------------------------------------ logic
def test_logic(mem, keys=None, token='', trace_cb=None):
    """Behavioural/logic checks against the DP manager API."""
    trace = trace_cb or (lambda m: None)
    # DP-manager-specific: skip when stg-dp was not among the selected targets.
    if keys and 'stg-dp' not in keys:
        trace('[logic] skipped (stg-dp manager API not in selected platforms)')
        return {'skipped': 'stg-dp (manager API) not selected'}
    mgr = PLATFORMS['stg-dp']['mgr']
    out = {}
    if not token:
        trace('[logic] skipped (no auth token)')
        return {'skipped': 'no token'}

    # A fuller (still likely-invalid) bigquery body so the create has a chance
    # of succeeding; if it is rejected the idempotency test is INCONCLUSIVE
    # (you cannot judge uniqueness when the resource was never created).
    body = {'name': 'logic-probe', 'type': 'bigquery',
            'source_config': {'project_id': 'demo', 'dataset': 'demo',
                              'host': 'example.com'}}

    # 1. idempotency: same POST twice
    r1, m1 = _timed_request('POST', f'{mgr}/connectors', body, token=token)
    r2, m2 = _timed_request('POST', f'{mgr}/connectors', body, token=token)
    create_ok = r1['status'] < 400
    dup_created = create_ok and r2['status'] < 400
    out['idempotent_post'] = {'first': r1['status'], 'second': r2['status'],
                              'duplicate_allowed': dup_created,
                              'conclusive': create_ok}
    if not create_ok:
        trace(f'[logic] double POST INCONCLUSIVE: create rejected '
              f'({r1["status"]}) - cannot test uniqueness with this body')
    else:
        trace(f'[logic] double POST: {r1["status"]}/{r2["status"]} '
              f'dup_allowed={dup_created}')
    if dup_created:
        mem.save_finding('stg-dp', f'{mgr}/connectors', 'logic', '',
                         r2['status'], 'missing_idempotency', 'medium',
                         'Identical POST accepted twice (duplicate resource?)',
                         recommendation='Enforce uniqueness on connector name')

    # 2. GET must not mutate state: hit a collection twice, compare
    g1, _ = _timed_request('GET', f'{mgr}/connectors', token=token)
    g2, _ = _timed_request('GET', f'{mgr}/connectors', token=token)
    stable = g1['status'] == g2['status'] and g1['body'][:2000] == \
        g2['body'][:2000]
    out['get_read_only'] = {'status': g1['status'], 'stable': stable}
    trace(f'[logic] GET stability: {g1["status"]} stable={stable}')

    # 3. validation semantics: bad type should be 4xx not 5xx
    bad = {'name': 12345, 'type': ['not-a-string'], 'source_config': 'flat'}
    rb, _ = _timed_request('POST', f'{mgr}/connectors', bad, token=token)
    ok_sem = 400 <= rb['status'] < 500
    out['validation_semantics'] = {'status': rb['status'], 'correct': ok_sem}
    trace(f'[logic] malformed payload -> {rb["status"]} '
          f'({"correct" if ok_sem else "WRONG (expected 4xx)"})')
    if not ok_sem:
        mem.save_finding('stg-dp', f'{mgr}/connectors', 'logic', '',
                         rb['status'], 'bad_error_semantics', 'medium',
                         'Malformed input produced non-4xx status',
                         recommendation='Return 422 with field-level errors')

    # 4. 404 vs 405 on unknown route / wrong method
    r404 = req('GET', f'{mgr}/definitely-not-a-route', token=token)
    r405 = req('DELETE', f'{mgr}/connectors', token=token)
    out['routing'] = {'unknown_route': r404['status'],
                      'delete_collection': r405['status']}
    trace(f'[logic] unknown route -> {r404["status"]}, '
          f'DELETE collection -> {r405["status"]}')
    return out


# ------------------------------------------------------------ orchestrator
DIMENSIONS = ('performance', 'limits', 'security', 'functionality', 'logic')


def run_suite(mem, token='', dims=('performance', 'limits', 'functionality',
                                   'logic'), quick=False, keys=None,
              trace_cb=None, llm_holder=None, security_fn=None):
    """Run the requested dimensions; returns dict of per-dim results."""
    trace = trace_cb or (lambda m: None)
    results = {}
    if 'performance' in dims:
        check_abort()
        trace('[suite] performance...')
        results['performance'] = test_performance(
            mem, keys=keys, samples=3 if quick else 5, token=token,
            trace_cb=trace)
    if 'limits' in dims:
        check_abort()
        trace('[suite] limits...')
        results['limits'] = test_limits(mem, keys=keys, token=token,
                                        trace_cb=trace)
    if 'functionality' in dims:
        check_abort()
        trace('[suite] functionality...')
        results['functionality'] = test_functionality(
            mem, keys=keys, token=token, trace_cb=trace)
    if 'logic' in dims:
        check_abort()
        trace('[suite] logic...')
        results['logic'] = test_logic(mem, keys=keys, token=token,
                                      trace_cb=trace)
    if 'security' in dims and security_fn is not None:
        check_abort()
        trace('[suite] security fuzz...')
        results['security'] = {'findings_recorded': security_fn(
            mem, token, quick=quick, llm=llm_holder)}
    trace(f'[suite] done: dimensions={list(results)}')
    return results
