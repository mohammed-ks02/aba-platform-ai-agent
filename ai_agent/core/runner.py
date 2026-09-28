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
import json
import os
import re
import sys
from datetime import datetime

# Allow running both as `python core/runner.py` and `python -m core.runner`
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import CONFIG, PLATFORMS, FUZZ, CONNECTOR_TYPES
    from core.http_client import req, discover, check_abort
    from core.classifier import classify
    from core.memory import Memory
    from core.llm import LLMClient, analyze_finding, generate_payloads, \
        executive_summary
else:
    from .config import CONFIG, PLATFORMS, FUZZ, CONNECTOR_TYPES
    from .http_client import req, discover, check_abort
    from .classifier import classify
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


def run_fuzz(mem, token, quick=False, llm=None, trace_cb=None,
             platforms=None):
    """Fuzz the Data Platform manager /connectors endpoint.

    ``platforms`` optionally restricts which registry keys are fuzzed; only
    entries exposing an ``mgr`` base are supported today (stg-dp).
    Returns the number of findings recorded.
    """
    trace = trace_cb or print
    trace('\n[API FUZZ]')
    if not token:
        trace('  skipped (no auth token)')
        return 0
    targets = {k: v for k, v in PLATFORMS.items()
               if v.get('mgr') and (not platforms or k in platforms)}
    if not targets:
        trace('  skipped (selected platforms have no fuzzable API)')
        return 0
    n_findings = 0
    for key, info in targets.items():
        mgr = info['mgr']
        types = CONNECTOR_TYPES[:1] if quick else ['bigquery', 'mongodb', 'slack']
        for ct in types:
            check_abort()
            trace(f'  > {key}/{ct}')
            for cat, payloads in FUZZ.items():
                use = payloads[:1]
                if llm is not None and getattr(llm, 'ai_payloads', False):
                    extra = generate_payloads(llm.client, cat,
                                              f'{key} /connectors ({ct})')
                    use = use + [p for p in extra if p not in payloads]
                for p in use:
                    body = {'name': f'fuzz-{ct}', 'type': ct,
                            'source_config': {'f': p}}
                    rr = req('POST', f'{mgr}/connectors', body, token)
                    ftype, sev = classify(rr['status'], rr.get('body', ''))
                    if rr['status'] >= 400:
                        ai = None
                        if llm is not None and llm.enabled:
                            ai = analyze_finding(llm.client, key,
                                                 f'{mgr}/connectors', cat, p,
                                                 rr['status'],
                                                 rr.get('body', ''))
                            if ai:
                                sev = ai['severity']
                                ftype = ai['analysis'][:60] or ftype
                        mem.save_finding(key, f'{mgr}/connectors', cat,
                                         str(p), rr['status'], ftype, sev,
                                         rr.get('body', ''),
                                         recommendation=(ai or {}).get(
                                             'recommendation', ''))
                        mem.learn_pattern(key, f'{key}:{ftype}', ftype)
                        n_findings += 1
                        tag = ' [ai]' if ai else ''
                        trace(f'    ! {ftype} ({rr["status"]}) [{sev}]{tag}')
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
                  'finding_type', 'severity'], f))
        for f in mem.findings()]
    rpt['pattern_details'] = [
        dict(zip(['platform', 'pattern', 'category', 'confidence',
                  'times_seen'], p))
        for p in mem.patterns()]
    if extra:
        rpt.update(extra)
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

    def __init__(self, client, ai_payloads=False):
        self.client = client
        self.ai_payloads = ai_payloads

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
    ap.add_argument('--suite', default='security', choices=['security', 'full'],
                    help="'security' keeps the classic pipeline; "
                         "'full' adds performance/limits/functionality/logic")
    ap.add_argument('--dims', default=None,
                    help='comma list of suite dimensions (implies --suite full); '
                         'e.g. performance,logic,ux')
    ap.add_argument('--ux', action='store_true',
                    help='add Playwright screenshot + AI UX/UI review pass')
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
            holder = _LLMHolder(client, ai_payloads=args.ai_payloads)
            names = ','.join(p['provider'] for p in client.status())
            print(f'\n[LLM] active chain: {names} '
                  f'(model={client.chain[0].model or "default"})')
        else:
            print('\n[LLM] none configured -- running deterministic rules only')
    except Exception as e:
        print(f'\n[LLM] init failed ({e}) -- continuing without AI features')

    # Natural-language prompt -> concrete plan (LLM or keyword fallback)
    platforms = _parse_platforms(args.platforms)
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
        results = run_suite(
            mem, token=token, dims=sel_dims, quick=args.quick,
            keys=platforms, trace_cb=print, llm_holder=holder,
            security_fn=(run_fuzz if do_security else None))
        extra['suite'] = results
        if not do_security:
            run_discovery(mem, trace_cb=print)
    else:
        run_discovery(mem, trace_cb=print)
        if not args.no_fuzz:
            run_fuzz(mem, token, quick=args.quick, llm=holder,
                     platforms=platforms)

    if want_ux:
        from .ux_review import review_all
        print('\n[UX REVIEW]')
        ux = review_all(llm=holder.client if holder.enabled else None,
                        keys=platforms, trace_cb=print)
        extra['ux_reviews'] = ux

    rpt = write_report(mem, llm=holder, extra=extra)
    if args.prompt:
        rpt['prompt'] = args.prompt
    return rpt


if __name__ == '__main__':
    main()
