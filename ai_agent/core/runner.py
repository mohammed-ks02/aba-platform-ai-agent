#!/usr/bin/env python3
"""ABA Fusion multi-platform security agent — unified runner.

Replaces the three duplicated variants (agent.py / agent_fast.py /
agent_ultra.py) with one configurable pipeline:

    1. AUTH       -- obtain a valid JWT via token_manager (auto-refresh)
    2. DISCOVERY  -- probe all 9 staging platforms
    3. API FUZZ   -- fuzz the Data Platform manager /connectors endpoint
    4. MEMORY     -- persist platforms / findings / learned patterns (SQLite)
    5. REPORT     -- write a JSON report into data/reports/

LLM integration (optional, provider-agnostic):
    When an LLM is configured (see core/llm/), the agent additionally:
      * AI-triages every >=400 fuzz response to filter false positives and
        re-score severity (falls back to rule-based classification on error)
      * writes a plain-English executive summary into the JSON report
      * with --ai-payloads, asks the model for extra category-specific probes
    With no LLM configured everything above is skipped silently and the
    deterministic pipeline runs unchanged.

Usage:
    python -m core.runner                 # full run (all 9 platforms)
    python -m core.runner --no-fuzz       # discovery only
    python -m core.runner --quick         # 1 connector type, 1 payload/cat
    python -m core.runner --provider groq --model qwen/qwen3.8-27b
    python core/runner.py                 # also works as a plain script
"""
import argparse
import functools
import json
import os
import random
import re
import sys
import time
import urllib.parse as _urlparse
from datetime import datetime

# Allow running both as `python core/runner.py` and `python -m core.runner`
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import CONFIG, PLATFORMS, FUZZ, CONNECTOR_TYPES
    from core.http_client import req, discover, check_abort
    from core.classifier import classify, analyze_response
    from core.memory import Memory
    from core.llm import LLMClient, analyze_finding, generate_payloads, \
        executive_summary
else:
    from .config import CONFIG, PLATFORMS, FUZZ, CONNECTOR_TYPES
    from .http_client import req, discover, check_abort
    from .classifier import classify, analyze_response
    from .memory import Memory
    from .llm import (LLMClient, analyze_finding, generate_payloads,
                      executive_summary)


def get_token(trace_cb=None):
    """Return a valid access token or '' if auth is unavailable."""
    trace = trace_cb or (lambda m: None)
    try:
        from .token_manager import TokenManager
    except ImportError:  # pragma: no cover - direct-script fallback
        from token_manager import TokenManager
    try:
        return TokenManager().ensure_valid()
    except Exception as e:
        trace(f'  Token: FAIL ({str(e)[:120]})')
        print(f'  Token: FAIL ({e})')
        return ''


def run_discovery(mem, trace_cb=None):
    trace = trace_cb or print
    trace('\n[DISCOVERY]')
    results = {}
    for key, info in PLATFORMS.items():
        r = discover(info['base'])
        status = 'alive' if r['alive'] else 'dead'
        mem.save_platform(key, info['name'], info['base'], info['type'], status)
        results[key] = r
        trace(f"  {key}: {status} | {r['title'][:30]}")
    return results


_SEV_RANK = {'info': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}


def _max_sev(a, b):
    """Return the higher-ranked severity (used so the LLM can only raise)."""
    return a if _SEV_RANK.get(a, 0) >= _SEV_RANK.get(b, 0) else b


def _benign_marker():
    """A harmless control value for the differential false-positive re-check."""
    return 'QActl' + str(random.randint(10000, 99999))


def _extract_id(body):
    """Best-effort connector id from a create response, for later cleanup."""
    try:
        d = json.loads(body)
        if isinstance(d, dict):
            return d.get('id') or d.get('_id') or \
                (d.get('data') or {}).get('id')
    except Exception:
        pass
    return None


def _injection_points(mgr, api, dry_run=False):
    """Injection points for one Data Platform target.

    Returns ``[(label, method, build), ...]`` where ``build(payload, cat)``
    yields ``(url, json_body_or_None)``. The POST point writes a connector
    (skipped when ``dry_run``); the GET points are read-only query/path
    injections, so many payloads can be sent safely.
    """
    points = []

    if not dry_run:
        def _post_connectors(p, cat):
            sc = {'f': p}
            if cat in ('nosql', 'xxe') and str(p).strip().startswith(('{', '[')):
                # send structured payloads as REAL nested JSON, not a string,
                # so a NoSQL/document parser actually evaluates them
                try:
                    sc = {'f': json.loads(p)}
                except Exception:
                    sc = {'f': p}
            return (f'{mgr}/connectors',
                    {'name': 'sec-probe', 'type': 'bigquery',
                     'source_config': sc})
        points.append(('POST connectors.source_config', 'POST',
                       _post_connectors))

    if api:
        def _query(base, param):
            def build(p, cat):
                return (f'{base}?{param}=' +
                        _urlparse.quote(str(p), safe=''), None)
            return build
        points.append(('GET connectors?search', 'GET',
                       _query(f'{api}/connectors', 'search')))
        points.append(('GET history?key', 'GET',
                       _query(f'{api}/history', 'key')))

        def _path(p, cat):
            return (f'{api}/connectors/' + _urlparse.quote(str(p), safe=''),
                    None)
        points.append(('GET connectors/{path}', 'GET', _path))
    return points


_SPEC_CACHE = {}
# In the Data Platform the UI "Sources" feature IS the connectors/resources
# surface, so a "test sources" request should cover those paths.
_FOCUS_SYNONYMS = {
    'sources': ('connector', 'source', 'resource'),
    'source': ('connector', 'source', 'resource'),
    'connectors': ('connector',), 'connector': ('connector',),
    'pipelines': ('pipeline',), 'pipeline': ('pipeline',),
    'catalog': ('catalog', 'table'), 'tables': ('table',),
    'ingestions': ('ingestion', 'history', 'realtime'),
    'jobs': ('job',), 'transformations': ('transformation',),
    'query': ('query',), 'notebooks': ('notebook',),
}
_DP_FEATURES = tuple(_FOCUS_SYNONYMS)


def _focus_from_prompt(prompt):
    """Feature words named in a prompt, e.g. 'test sources in dp' -> ['sources'].
    Word-boundary matched so 'source' does not fire inside unrelated tokens."""
    p = (prompt or '').lower()
    return [w for w in _DP_FEATURES
            if re.search(r'\b' + re.escape(w) + r'\b', p)]


def _focus_terms(focus):
    terms = set()
    for f in (focus or []):
        f = str(f).lower().strip()
        if f:
            terms.update(_FOCUS_SYNONYMS.get(f, (f,)))
    return terms


def _get_spec(mgr, token):
    """Fetch + cache the full OpenAPI spec for a manager host ({} on failure)."""
    if mgr not in _SPEC_CACHE:
        try:
            r = req('GET', f'{mgr}/openapi.json', token=token)
            _SPEC_CACHE[mgr] = json.loads(r['body']) \
                if r['status'] == 200 else {}
        except Exception:
            _SPEC_CACHE[mgr] = {}
    return _SPEC_CACHE[mgr]


def _resolve(schema, spec):
    """Resolve a top-level $ref against the spec's component schemas."""
    if isinstance(schema, dict) and '$ref' in schema:
        name = schema['$ref'].split('/')[-1]
        return spec.get('components', {}).get('schemas', {}).get(name, {})
    return schema or {}


def _build_body(schema, spec, payload, depth=0):
    """Build a minimal example body from an OpenAPI schema, injecting ``payload``
    (when not None) into the first string field. Bounded recursion."""
    schema = _resolve(schema, spec)
    if depth > 4 or not isinstance(schema, dict):
        return str(payload) if payload is not None else 'probe'
    t = schema.get('type')
    if t == 'object' or 'properties' in schema:
        props = schema.get('properties', {}) or {}
        fields = schema.get('required') or list(props)[:4]
        obj, injected = {}, False
        for name in fields:
            ps = props.get(name, {'type': 'string'})
            give = payload if (payload is not None and not injected) else None
            obj[name] = _build_body(ps, spec, give, depth + 1)
            if give is not None and isinstance(obj[name], str):
                injected = True
        if payload is not None and not injected:
            obj['q'] = str(payload)
        return obj
    if t == 'array':
        return [_build_body(schema.get('items', {}), spec, payload, depth + 1)]
    if t in ('integer', 'number'):
        return 0
    if t == 'boolean':
        return False
    return str(payload) if payload is not None else 'probe'


def _openapi_write_points(mgr, token, focus=None, limit=8, trace=None):
    """POST body-injection points from the spec (schema-derived bodies) -- the
    generic WRITE-PATH fuzz. Focus-filtered, collection endpoints only. These
    create resources, so callers run them only when writes are permitted and
    clean up via the ids run_fuzz records."""
    spec = _get_spec(mgr, token)
    paths = spec.get('paths', {})
    terms = _focus_terms(focus)
    pts = []
    for path in sorted(paths):
        if '{' in path or 'post' not in paths[path]:
            continue
        if terms and not any(t in path.lower() for t in terms):
            continue
        rb = paths[path]['post'].get('requestBody', {}) or {}
        sch = (rb.get('content', {}).get('application/json', {})
               or {}).get('schema')
        if sch is None:
            continue

        def _mk(pt, s):
            def build(p, cat):
                return (f'{mgr}{pt}', _build_body(s, spec, p))
            return build
        pts.append((f'POST {path} (body)', 'POST', _mk(path, sch)))
        if len(pts) >= limit:
            break
    if trace and pts:
        trace(f'  [openapi] +{len(pts)} WRITE-path POST endpoint(s) '
              '(schema-derived bodies, cleaned up after)')
    return pts


def _lifecycle_probe(mgr, token, focus, hit, trace):
    """Feature LIFECYCLE: create -> get -> delete + idempotency + validation on
    the focus feature's OWN collection endpoint (not always /connectors)."""
    spec = _get_spec(mgr, token)
    paths = spec.get('paths', {})
    terms = _focus_terms(focus) or {'connector'}
    coll = next((p for p in sorted(paths)
                 if '{' not in p and 'post' in paths[p]
                 and any(t in p.lower() for t in terms)), None)
    if not coll:
        return None
    rb = paths[coll]['post'].get('requestBody', {}) or {}
    sch = (rb.get('content', {}).get('application/json', {}) or {}).get('schema')
    body = _build_body(sch, spec, None)
    r1 = hit('POST', f'{mgr}{coll}', body)
    r2 = hit('POST', f'{mgr}{coll}', body)
    cid = _extract_id(r1.get('body', ''))
    out = {'collection': coll, 'create': r1['status'],
           'duplicate': r2['status'], 'idempotency_conclusive': r1['status'] < 400}
    if cid:
        out['get_by_id'] = hit('GET', f'{mgr}{coll}/{cid}')['status']
        out['delete'] = hit('DELETE', f'{mgr}{coll}/{cid}')['status']
    bad = hit('POST', f'{mgr}{coll}', {'name': 12345, 'bogus': ['x']})
    out['validation_4xx'] = 400 <= bad['status'] < 500
    trace(f'  [lifecycle] {coll}: create={r1["status"]} dup={r2["status"]} '
          f"get={out.get('get_by_id')} delete={out.get('delete')} "
          f"validation_4xx={out['validation_4xx']}")
    return out


def _openapi_get_points(mgr, token, focus=None, limit=30, trace=None):
    """Read the DP OpenAPI spec and turn each read-only GET endpoint into an
    injection point (path-param or ``?q=`` query injection). When ``focus`` is
    set (e.g. 'sources') only matching paths are kept. Cached per manager host.
    GET-only, so testing the whole documented surface stays safe."""
    paths = _get_spec(mgr, token).get('paths', {})
    terms = _focus_terms(focus)
    points = []
    for path in sorted(paths):
        if 'get' not in paths[path]:
            continue
        if terms and not any(t in path.lower() for t in terms):
            continue
        if '{' in path:
            def _mk(pt):
                def build(p, cat):
                    return (f'{mgr}' + re.sub(r'\{[^}]+\}',
                            _urlparse.quote(str(p), safe=''), pt, 1), None)
                return build
            points.append((f'GET {path} (path)', 'GET', _mk(path)))
        else:
            def _mkq(pt):
                def build(p, cat):
                    return (f'{mgr}{pt}?q=' +
                            _urlparse.quote(str(p), safe=''), None)
                return build
            points.append((f'GET {path}?q', 'GET', _mkq(path)))
        if len(points) >= limit:
            break
    if trace and (paths or terms):
        trace(f'  [openapi] {len(paths)} documented paths; '
              f'testing {len(points)} GET endpoint(s)'
              + (f" matching focus={list(terms)}" if terms else ''))
    return points


def run_fuzz(mem, token, quick=False, llm=None, trace_cb=None,
             platforms=None, throttle=0.15, dry_run=False,
             focus=None, use_openapi=True):
    """Body-aware, multi-endpoint security probe of the Data Platform API.

    ``throttle`` sleeps between requests (politeness); ``dry_run`` skips the
    connector-writing POST point and tests only the read-only GET endpoints.

    For every fuzzable target it injects each category's payloads into several
    endpoints (one connector POST plus read-only GET query/path points), then:
      (a) covers more payloads across more injection points than the old
          single-endpoint smoke test;
      (b) classifies on the *response body* (reflected payload, evaluated
          template, SQL/NoSQL error strings, file contents, stack traces, 5xx)
          via ``analyze_response`` rather than the status code alone, so a
          plain 422 validation error is no longer reported as a finding;
      (c) confirms every candidate with a differential control re-check -- the
          same request with a benign marker; if the benign input reproduces
          the same signal it is a false positive and dropped. Confirmed
          findings carry an evidence snippet, plus an LLM adversarial second
          opinion + remediation note when a model is configured.

    ``platforms`` optionally restricts which registry keys are probed (only
    entries exposing an ``mgr`` base). Returns the number of findings kept.
    """
    trace = trace_cb or print
    env_t = os.environ.get('ABA_FUZZ_THROTTLE')
    if env_t is not None:
        try:
            throttle = float(env_t)
        except ValueError:
            pass
    trace('\n[API FUZZ]' + ('  (dry-run: no writes)' if dry_run else ''))
    if not token:
        trace('  skipped (no auth token)')
        return 0
    targets = {k: v for k, v in PLATFORMS.items()
               if v.get('mgr') and (not platforms or k in platforms)}
    if not targets:
        trace('  skipped (selected platforms have no fuzzable API)')
        return 0

    def hit(method, url, body=None):
        r = req(method, url, body, token)
        if throttle:
            time.sleep(throttle)          # politeness: don't hammer staging
        return r

    n_findings = 0
    probes = 0                            # total requests sent (coverage proof)
    created = []                          # (mgr, id) connectors to delete after
    for key, info in targets.items():
        mgr = info['mgr']
        points = _injection_points(mgr, info.get('api'), dry_run=dry_run)
        # OpenAPI-driven coverage: add read-only GET endpoints discovered from
        # the spec, focused on the feature the user asked about (e.g. sources).
        if use_openapi:
            oa = _openapi_get_points(mgr, token, focus=focus, trace=trace)
            if focus and not oa:
                trace(f'  [coverage] WARNING: focus={focus} matched 0 '
                      f'documented endpoints -- the result below covers only '
                      f'the default connector probes, NOT that feature')
            points = points + oa
            # (#5) generic WRITE-PATH fuzz: inject into POST request bodies of
            # discovered endpoints (schema-derived), only when writes allowed.
            if not dry_run:
                points = points + _openapi_write_points(mgr, token,
                                                        focus=focus, trace=trace)
        # AI-generated extra payloads: compute ONCE per category and reuse
        # across every injection point (avoids dozens of rate-limited calls).
        ai_extra = {}
        if llm is not None and getattr(llm, 'ai_payloads', False):
            trace('  (ai_payloads on -> generating extra payloads per category)')
            for cat in FUZZ:
                check_abort()
                try:
                    ex = generate_payloads(llm.client, cat, f'{key} DP API')
                except Exception:
                    ex = []
                ai_extra[cat] = [p for p in ex if p not in FUZZ[cat]]
        for label, method, build in points:
            check_abort()
            trace(f'  > {key} :: {label}')
            cap = 1 if quick else (2 if method == 'POST' else 4)
            for cat, payloads in FUZZ.items():
                use = [p for p in payloads if p is not None][:cap]
                use = use + ai_extra.get(cat, [])
                for p in use:
                    check_abort()
                    url, body = build(p, cat)
                    rr = hit(method, url, body)
                    probes += 1
                    if method == 'POST' and rr['status'] < 300:
                        cid = _extract_id(rr.get('body', ''))
                        if cid:
                            created.append((url, cid))  # delete from its own coll
                    res = analyze_response(rr['status'], rr.get('body', ''),
                                           p, cat, headers=rr.get('headers'))
                    if not res['signal']:
                        continue  # (b) status-only noise -> not a finding
                    # (c) differential control re-check with a benign marker.
                    marker = _benign_marker()
                    curl, cbody = build(marker, cat)
                    cr = hit(method, curl, cbody)
                    if method == 'POST' and cr['status'] < 300:
                        cid = _extract_id(cr.get('body', ''))
                        if cid:
                            created.append((curl, cid))
                    cres = analyze_response(cr['status'], cr.get('body', ''),
                                            marker, cat,
                                            headers=cr.get('headers'))
                    if cres['signal'] and cres['type'] == res['type']:
                        trace(f'    ~ {res["type"]} [{cat}] dropped '
                              f'(control also triggered -> false positive)')
                        continue
                    ftype, sev = res['type'], res['severity']
                    # n-of-m: a lone 5xx is flaky. Require a repeat to confirm,
                    # then escalate the (medium) server_error to high.
                    if ftype == 'server_error':
                        rr2 = hit(method, url, body)
                        if rr2['status'] < 500:
                            trace(f'    ~ server_error [{cat}] dropped '
                                  f'(not reproducible: HTTP {rr2["status"]})')
                            continue
                        sev = 'high'
                    details = f'{label} | evidence: {res["evidence"]}'
                    ai = None
                    if (llm is not None and getattr(llm, 'ai_triage', False)
                            and llm.enabled):
                        ai = analyze_finding(llm.client, key, url, cat, p,
                                             rr['status'], rr.get('body', ''))
                        if ai and ai.get('severity'):
                            # the LLM may RAISE severity, never LOWER an
                            # evidence-backed finding
                            sev = _max_sev(sev, ai['severity'])
                    mem.save_finding(key, url, cat, str(p), rr['status'],
                                     ftype, sev, details,
                                     recommendation=(ai or {}).get(
                                         'recommendation', ''))
                    mem.learn_pattern(key, f'{key}:{ftype}', ftype)
                    n_findings += 1
                    tag = ' [ai]' if ai else ''
                    trace(f'    ! {ftype} [{cat}] ({rr["status"]}) [{sev}] '
                          f'CONFIRMED{tag} — {res["evidence"][:50]}')

        # (#6) feature LIFECYCLE on the focus feature's own collection:
        # create -> get -> delete + idempotency + validation.
        if not dry_run and focus:
            lc = _lifecycle_probe(mgr, token, focus, hit, trace)
            if lc and lc['idempotency_conclusive'] and lc['duplicate'] < 400:
                mem.save_finding(key, f'{mgr}{lc["collection"]}', 'logic', '',
                                 lc['duplicate'], 'missing_idempotency',
                                 'medium', 'Duplicate create accepted on '
                                 f'{lc["collection"]}',
                                 recommendation='Enforce uniqueness on create')
                n_findings += 1
            if lc and lc['idempotency_conclusive'] and not lc['validation_4xx']:
                mem.save_finding(key, f'{mgr}{lc["collection"]}', 'logic', '',
                                 0, 'bad_error_semantics', 'low',
                                 f'Malformed create on {lc["collection"]} not '
                                 'rejected with 4xx',
                                 recommendation='Validate request bodies')
                n_findings += 1

    # test-data cleanup: delete every resource this run created
    if created:
        ok = 0
        for base, cid in created:
            try:
                if hit('DELETE', f'{base}/{cid}')['status'] < 400:
                    ok += 1
            except Exception:
                pass
        trace(f'  [cleanup] deleted {ok}/{len(created)} created resource(s)')
    trace(f'  [coverage] {probes} probe(s) sent'
          + (f' (0 findings = tested and clean)' if n_findings == 0
             else f' -> {n_findings} finding(s)'))
    return n_findings


def write_report(mem, llm=None, extra=None, trace_cb=None):
    trace = trace_cb or print
    trace('\n[REPORT]')
    rpt = {'ts': datetime.now().isoformat(), **mem.stats()}
    # full detail lists use distinct keys so counts stay integers
    rpt['platform_details'] = [
        dict(zip(['key', 'name', 'base', 'type', 'status'], p))
        for p in mem.platforms()]
    rpt['finding_details'] = [
        dict(zip(['platform', 'endpoint', 'category', 'payload', 'status',
                  'finding_type', 'severity', 'evidence', 'recommendation'], f))
        for f in mem.findings_detailed()]
    rpt['pattern_details'] = [
        dict(zip(['platform', 'pattern', 'category', 'confidence',
                  'times_seen'], p))
        for p in mem.patterns()]
    if extra:
        rpt.update(extra)
    # (#9) coverage matrix: which dimensions actually ran per platform, so a
    # full-suite run makes clear that limits/logic/security are stg-dp-only and
    # the 8 SPAs only get performance/functionality/ux.
    suite = (extra or {}).get('suite') or {}
    plats = [p[0] for p in mem.platforms()] or \
        [f['key'] for f in rpt.get('platform_details', [])]
    if suite and plats:
        DP_ONLY = {'limits', 'logic', 'security'}
        matrix = {}
        for pk in plats:
            row = {}
            for dim in ('performance', 'functionality', 'ux', 'limits',
                        'logic', 'security'):
                data = suite.get(dim)
                if data is None and dim not in DP_ONLY:
                    row[dim] = 'not run'
                elif dim in DP_ONLY:
                    row[dim] = 'ran' if (pk == 'stg-dp' and data) else 'n/a (DP only)'
                else:
                    ran = isinstance(data, dict) and pk in data
                    row[dim] = 'ran' if ran else ('not run' if data is None else 'n/a')
            matrix[pk] = row
        rpt['coverage'] = matrix
        rpt['coverage_note'] = ('limits, logic and security apply to the Data '
                                'Platform API (stg-dp) only; the other 8 SPAs '
                                'are covered by performance, functionality and '
                                'UX.')
    # (SPA gap) an SPA "works" only when proven: a DISTINCT route (route-diff)
    # OR the UX pass rendered real content while authenticated. Fold both into
    # one verified_working per platform so a catch-all 200 is never "healthy".
    func = suite.get('functionality') if isinstance(suite, dict) else None
    if isinstance(func, dict):
        ux_by = {}
        for u in (extra or {}).get('ux_reviews') or []:
            if isinstance(u, dict):
                ux_by[u.get('platform') or u.get('name')] = u
        for pk, chk in func.items():
            if not isinstance(chk, dict):
                continue
            u = ux_by.get(pk, {})
            ux_ok = bool(u.get('rendered')) and bool(u.get('authenticated'))
            http_ok = bool(chk.get('http_verified'))
            chk['verified_working'] = http_ok or ux_ok
            chk['verified_by'] = ('distinct route' if http_ok else
                                  ('UX hydration + auth' if ux_ok else
                                   'NOT verified (client-routed shell only)'))
    if llm is not None and llm.enabled:
        rpt['llm'] = {'provider': llm.provider_name,
                      'chain': llm.client.status()}
        summary = executive_summary(llm.client, rpt)
        if summary:
            rpt['executive_summary'] = summary
    os.makedirs(CONFIG['reports_dir'], exist_ok=True)
    path = os.path.join(
        CONFIG['reports_dir'],
        f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, 'w') as f:
        json.dump(rpt, f, indent=2, default=str)
    trace(f"  Platforms: {rpt['total']} ({rpt['alive']} alive)")
    trace(f"  Findings: {rpt['findings']} "
          f"({rpt['critical_high']} critical/high)")
    trace(f"  Patterns: {rpt['patterns']}")
    trace(f'  Report: {path}')
    rpt['_report_path'] = path
    return rpt


class _LLMHolder:
    """Small wrapper so run_fuzz can read .enabled/.client/.ai_payloads."""

    def __init__(self, client, ai_payloads=False, ai_triage=False):
        self.client = client
        self.ai_payloads = ai_payloads
        self.ai_triage = ai_triage        # per-finding LLM triage (off default)

    @property
    def enabled(self):
        return bool(self.client and self.client.enabled)

    @property
    def provider_name(self):
        return self.client.provider_name if self.client else None


def _parse_platforms(spec):
    """Resolve a --platforms value into registry keys.

    Accepts comma-separated keys ('stg-dp,stg-mate'), friendly words
    ('analytics forge'), or 'all'.
    """
    if not spec or spec.strip().lower() == 'all':
        return list(PLATFORMS)
    from .planner import keyword_plan  # reuse word map lazily
    p = spec.lower()
    keys = []
    for part in re.split(r'[,;]\s*', p):
        part = part.strip()
        if not part:
            continue
        if part in PLATFORMS and part not in keys:
            keys.append(part)
    if keys:
        return keys
    plan = keyword_plan(p)
    return [k for k in plan['platforms'] if k in PLATFORMS]


def main(argv=None):
    ap = argparse.ArgumentParser(description='ABA Fusion multi-platform agent')
    ap.add_argument('--no-fuzz', action='store_true',
                    help='discovery only, skip API fuzzing')
    ap.add_argument('--quick', action='store_true',
                    help='reduced matrix (fast smoke run)')
    ap.add_argument('--fresh', action='store_true',
                    help='start with an empty memory database')
    ap.add_argument('--provider', default=None,
                    help='LLM provider: openai|anthropic|gemini|groq|deepseek|'
                         'mistral|openrouter|xai|together|fireworks|cerebras|'
                         'sambanova|github|azure|ollama|lmstudio|vllm|'
                         'llamacpp|litellm|custom|auto (default: auto-detect)')
    ap.add_argument('--model', default=None,
                    help='LLM model name override (or ABA_LLM_MODEL)')
    ap.add_argument('--base-url', default=None,
                    help='LLM endpoint override for any OpenAI-compatible '
                         'server (or ABA_LLM_BASE_URL)')
    ap.add_argument('--ai-payloads', action='store_true',
                    help='ask the LLM to generate extra fuzz payloads per '
                         'category (requires a working LLM)')
    ap.add_argument('--ai-triage', action='store_true',
                    help='ask the LLM to review each confirmed finding (off by '
                         'default; slow on rate-limited free tiers, and it can '
                         'only raise severity, never lower a proven one)')
    ap.add_argument('--dry-run', action='store_true',
                    help='security: test only read-only GET endpoints, never '
                         'create connectors on staging')
    ap.add_argument('--throttle', type=float, default=0.15,
                    help='seconds to sleep between requests (politeness; '
                         'default 0.15)')
    ap.add_argument('--suite', default='security', choices=['security', 'full'],
                    help="'security' keeps the classic pipeline; "
                         "'full' adds performance/limits/functionality/logic")
    ap.add_argument('--dims', default=None,
                    help='comma list of suite dimensions (implies --suite full); '
                         'e.g. performance,logic,ux')
    ap.add_argument('--ux', action='store_true',
                    help='add Playwright screenshot + AI UX/UI review pass')
    ap.add_argument('--headed', action='store_true',
                    help='run the UX browser visibly (headed Chromium) instead '
                         'of headless, so you can watch it')
    ap.add_argument('--no-auth', action='store_true',
                    help='skip SSO login for the UX browser pass (inspect only '
                         'public/landing pages instead of the logged-in app)')
    ap.add_argument('--prompt', default=None,
                    help='natural-language test request; the LLM (or keyword '
                         'fallback) turns it into a concrete plan first')
    ap.add_argument('--platforms', default=None,
                    help='restrict targets: comma keys or words '
                         '(e.g. "analytics,forge" or "data platform")')
    args = ap.parse_args(argv)

    print('=' * 50)
    print('AI MULTI-PLATFORM AGENT — unified runner')
    print('=' * 50)

    if args.fresh and os.path.exists(CONFIG['memory_db']):
        os.remove(CONFIG['memory_db'])
    mem = Memory()

    # LLM setup (optional -- everything works without it)
    holder = _LLMHolder(None)
    try:
        client = LLMClient(provider=args.provider, model=args.model,
                           base_url=args.base_url)
        if client.enabled:
            holder = _LLMHolder(client, ai_payloads=args.ai_payloads,
                                ai_triage=args.ai_triage)
            names = ','.join(p['provider'] for p in client.status())
            print(f'\n[LLM] active chain: {names} '
                  f'(model={client.chain[0].model or "default"})')
        else:
            print('\n[LLM] none configured -- running deterministic rules only')
    except Exception as e:
        print(f'\n[LLM] init failed ({e}) -- continuing without AI features')

    # Natural-language prompt -> concrete plan (LLM or keyword fallback)
    platforms = _parse_platforms(args.platforms)
    focus = _focus_from_prompt(args.prompt)   # e.g. "test sources" -> ['sources']
    dims = None
    if args.prompt:
        from .planner import interpret_prompt, ALL_DIMS
        plan = interpret_prompt(args.prompt, llm=holder.client if holder.enabled else None)
        print(f"\n[PROMPT] '{args.prompt}'")
        print(f'  plan source={plan["source"]} dims={plan["dimensions"]} '
              f'platforms={len(plan["platforms"])}')
        print(f'  notes: {plan.get("notes", "")}')
        platforms = [k for k in plan['platforms'] if k in platforms] \
            or platforms
        dims = [d for d in plan['dimensions'] if d in ALL_DIMS]
        if plan.get('focus'):
            print(f'  focus: {plan["focus"]}')
    elif args.dims:
        dims = [d.strip() for d in args.dims.split(',') if d.strip()]

    want_ux = args.ux or (dims and 'ux' in dims)
    want_suite = args.suite == 'full' or args.dims is not None \
        or args.prompt is not None

    print('\n[AUTH]')
    token = get_token()
    if token:
        print('  Token: OK')

    extra = {}
    if want_suite:
        from .suite import run_suite, DIMENSIONS
        sel_dims = [d for d in (dims or list(DIMENSIONS))
                    if d in DIMENSIONS]
        do_security = ('security' in sel_dims) and not args.no_fuzz
        sec_fn = functools.partial(run_fuzz, throttle=args.throttle,
                                   dry_run=args.dry_run,
                                   focus=focus) if do_security else None
        results = run_suite(
            mem, token=token, dims=sel_dims, quick=args.quick,
            keys=platforms, trace_cb=print, llm_holder=holder,
            security_fn=sec_fn)
        extra['suite'] = results
        if not do_security:
            run_discovery(mem, trace_cb=print)
    else:
        run_discovery(mem, trace_cb=print)
        if not args.no_fuzz:
            run_fuzz(mem, token, quick=args.quick, llm=holder,
                     platforms=platforms, throttle=args.throttle,
                     dry_run=args.dry_run, focus=focus)

    if want_ux:
        from .ux_review import review_all
        print('\n[UX REVIEW]' + (' (headed)' if args.headed else ''))
        ux = review_all(llm=holder.client if holder.enabled else None,
                        keys=platforms, headless=not args.headed,
                        slow_mo=350 if args.headed else 0, trace_cb=print,
                        authenticate=not args.no_auth)
        extra['ux_reviews'] = ux

    rpt = write_report(mem, llm=holder, extra=extra)
    if args.prompt:
        rpt['prompt'] = args.prompt
    return rpt


if __name__ == '__main__':
    main()
