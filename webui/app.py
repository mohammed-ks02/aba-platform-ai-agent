#!/usr/bin/env python3
"""ABA Fusion AI Agent -- local web interface (FastAPI).

Start with exactly ONE terminal command:

    python webui/app.py

then open http://127.0.0.1:8787 in a browser.  Everything else -- starting
runs, watching them live, browsing results, screenshots and reports -- is
done in the UI.

Features
    * Prompt box: natural-language "what to test" requests -> LLM plan
      (keyword fallback when no LLM configured), then execution.
    * Live visualised testing: SSE trace stream + per-step progress +
      Playwright screenshots served from /screenshots/.
    * Tabs: Findings, Performance charts, Limits, Functionality, Logic,
      UX reviews (rendered screenshots), Reports download.
    * Provider/model picker wired into core.llm env configuration.
"""
import glob
import html as _html
import json
import os
import queue
import sys
import threading
import time
import uuid
from datetime import datetime

# --- make the ai_agent package importable regardless of cwd ---------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                           # repo root
_AI_AGENT = os.path.join(_ROOT, 'ai_agent')              # .../ai_agent
for _p in (_AI_AGENT, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from core.config import CONFIG, PLATFORMS
from core.memory import Memory
from core.planner import interpret_prompt, keyword_plan
from core import runner as R
from core.suite import run_suite, DIMENSIONS

app = FastAPI(title='ABA Fusion AI Agent')

_SCREENSHOTS = os.path.join(CONFIG['data_dir'], 'screenshots')
os.makedirs(_SCREENSHOTS, exist_ok=True)


# ------------------------------------------------------------- run registry
class Run:
    """One agent execution; threads push trace lines into .q for SSE."""

    def __init__(self, rid, spec):
        self.id = rid
        self.spec = spec                  # what the user asked for
        self.status = 'queued'            # queued|running|done|error|stopped
        self.started = datetime.now().isoformat(timespec='seconds')
        self.finished = None
        self.q = queue.Queue()
        self.buffer = []                  # full trace history (survives reload)
        self.report = None                # filled when done
        self.error = None
        self.stop_requested = threading.Event()

    def request_stop(self):
        """Mark the run for cancellation; helpers raise Aborted soon after."""
        self.stop_requested.set()
        self.trace('[web] STOP requested - cancelling at next step...')

    def trace(self, msg):
        item = {'ts': datetime.now().strftime('%H:%M:%S'),
                'line': str(msg).strip()}
        self.buffer.append(item)
        self.q.put(item)

    def brief(self):
        return {'id': self.id, 'status': self.status, 'started': self.started,
                'finished': self.finished, 'spec': self.spec,
                'summary': ({k: v for k, v in self.report.items()
                             if k in ('total', 'alive', 'findings',
                                      'critical_high')}
                            if isinstance(self.report, dict) else None),
                'error': self.error}


RUNS = {}                 # rid -> Run
RUNS_ORDER = []           # newest first
_LOCK = threading.Lock()


def _execute(run):
    """Run the requested dimensions inside a worker thread."""
    spec = run.spec
    from core.http_client import Aborted, set_abort_check
    set_abort_check(run.stop_requested.is_set)
    try:
        run.status = 'running'
        run.trace(f'[web] run {run.id} started: {json.dumps({k: v for k, v in spec.items() if k != "prompt"})[:200]}')
        # Each web run gets its own memory DB so the Findings tab shows only
        # this run's results rather than every past run's findings piled up.
        run_db = os.path.join(CONFIG['data_dir'], 'runs', f'{run.id}.db')
        mem = Memory(db=run_db)

        holder = R._LLMHolder(None)
        try:
            from core.llm import LLMClient
            client = LLMClient(provider=spec.get('provider') or None,
                               model=spec.get('model') or None,
                               base_url=spec.get('base_url') or None,
                               api_key=spec.get('api_key') or None)
            if client.enabled:
                holder = R._LLMHolder(client,
                                      ai_payloads=bool(spec.get('ai_payloads')),
                                      ai_triage=bool(spec.get('ai_triage')))
                run.trace(f'[llm] chain: '
                          f'{",".join(p["provider"] for p in client.status())}')
            else:
                run.trace('[llm] none configured - deterministic mode')
        except Exception as e:
            run.trace(f'[llm] init failed: {e}')

        run.trace('[auth] obtaining JWT (timeout 20s)...')
        token = R.get_token(trace_cb=run.trace)
        run.trace(f'[auth] {"OK" if token else "no token (public tests only)"}')

        dims = [d for d in spec.get('dims', []) if d]
        plats = [k for k in spec.get('platforms', list(PLATFORMS))
                 if k in PLATFORMS] or list(PLATFORMS)

        extra = {}
        suite_dims = [d for d in dims if d in DIMENSIONS]
        do_sec = 'security' in suite_dims
        import functools
        dry = bool(spec.get('dry_run'))
        thr = spec.get('throttle')
        thr = float(thr) if thr is not None else 0.15
        focus = R._focus_from_prompt(spec.get('prompt') or '')
        if focus:
            run.trace(f'[focus] targeting feature(s): {", ".join(focus)}')
        sec_fn = functools.partial(R.run_fuzz, throttle=thr, dry_run=dry,
                                   focus=focus)
        if suite_dims:
            res = run_suite(mem, token=token, dims=suite_dims,
                            quick=bool(spec.get('quick')), keys=plats,
                            trace_cb=run.trace, llm_holder=holder,
                            security_fn=sec_fn if do_sec else None)
            extra['suite'] = res
        elif do_sec:
            res = sec_fn(mem, token, quick=bool(spec.get('quick')),
                         llm=holder, trace_cb=run.trace, platforms=plats)
            extra['suite'] = {'security': {'findings_recorded': res}}
        if 'ux' in dims:
            try:
                from core.ux_review import review_all
                headed = bool(spec.get('headed'))
                if headed:
                    run.trace('[ux] HEADED mode - a real Chromium window will '
                              'open on the machine running this server')
                auth = spec.get('auth', True)
                extra['ux_reviews'] = review_all(
                    llm=holder.client if holder.enabled else None,
                    keys=plats, headless=not headed,
                    slow_mo=350 if headed else 0, trace_cb=run.trace,
                    authenticate=auth)
            except Exception as e:
                run.trace(f'[ux] unavailable: {str(e)[:120]}')
        if 'discovery' in dims or not dims:
            extra.setdefault('suite', {})
            disc = R.run_discovery(mem, trace_cb=run.trace)
            extra['discovery'] = {k: {'alive': v['alive'], 'title': v['title']}
                                  for k, v in disc.items()}

        rpt = R.write_report(mem, llm=holder, extra=extra,
                             trace_cb=run.trace)
        rpt.pop('_report_path', None)
        if spec.get('prompt'):
            rpt['prompt'] = spec['prompt']
        run.report = rpt
        run.status = 'done'
        run.trace(f'[web] finished: {rpt["findings"]} findings, '
                  f'report saved')
    except Aborted:
        run.status = 'stopped'
        run.trace('[web] run stopped by user')
    except Exception as e:
        run.status = 'error'
        run.error = str(e)[:300]
        run.trace(f'[web] ERROR: {e}')
    finally:
        set_abort_check(None)
        run.finished = datetime.now().isoformat(timespec='seconds')
        run.buffer.append({'ts': datetime.now().strftime('%H:%M:%S'),
                           'line': '__DONE__', 'status': run.status})
        run.q.put(run.buffer[-1])


def _start_run(spec):
    rid = uuid.uuid4().hex[:8]
    run = Run(rid, spec)
    with _LOCK:
        RUNS[rid] = run
        RUNS_ORDER.insert(0, rid)
    t = threading.Thread(target=_execute, args=(run,), daemon=True)
    t.start()
    return run


# ----------------------------------------------------------------- API models
class PlanIn(BaseModel):
    prompt: str
    provider: str | None = None
    model: str | None = None


class RunIn(BaseModel):
    prompt: str | None = None
    dims: list[str] | None = None
    platforms: list[str] | None = None
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    quick: bool = False
    ai_payloads: bool = False
    headed: bool = False           # show the real Chromium window during UX runs
    auth: bool = True              # SSO-login the UX browser to test inside apps
    dry_run: bool = False          # security: read-only GETs, never write
    throttle: float | None = None  # seconds between requests (politeness)


# -------------------------------------------------------------------- routes
@app.get('/', response_class=HTMLResponse)
def index():
    # no-store so a UI update is never masked by a stale cached page (features
    # like the report Read/PDF buttons appearing "removed" after an update).
    return FileResponse(os.path.join(_HERE, 'static', 'index.html'),
                        headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/platforms')
def api_platforms():
    return [{'key': k, **{f: v[f] for f in ('name', 'type')}}
            for k, v in PLATFORMS.items()]


@app.get('/api/providers')
def api_providers():
    from core.llm.providers import PROVIDERS
    from core.llm.client import available_providers
    # A provider is "configured" only when it is actually usable now: a keyed
    # cloud provider with its API key present, or a keyless local server
    # (ollama/lmstudio/vllm/llamacpp/litellm) with an explicit base URL set.
    # Using available_providers() (not raw cls.available(), which returns True
    # for EVERY keyless provider regardless of whether anything is running)
    # stops the picker from advertising local servers that are not up.
    usable = set(available_providers())
    out = []
    for name, cls in sorted(PROVIDERS.items()):
        out.append({'name': name,
                    'default_model': getattr(cls, 'default_model_name', None),
                    'configured': name in usable})
    return out


_MODELS_CACHE = {}          # (provider, base_url) -> (timestamp, result)
_MODELS_TTL = 300.0         # seconds -- live catalogs change rarely


@app.get('/api/models')
def api_models(provider: str, base_url: str | None = None,
               refresh: bool = False):
    """List models available for a provider (live /models endpoint with
    curated fallback).  Used by the Web UI model picker."""
    from core.llm.providers import list_provider_models
    key = (provider, base_url or '')
    hit = _MODELS_CACHE.get(key)
    if not refresh and hit and time.time() - hit[0] < _MODELS_TTL:
        return hit[1]
    result = list_provider_models(provider, base_url=base_url)
    _MODELS_CACHE[key] = (time.time(), result)
    return result


@app.post('/api/plan')
def api_plan(body: PlanIn):
    llm = None
    try:
        from core.llm import LLMClient
        c = LLMClient(provider=body.provider, model=body.model)
        if c.enabled:
            llm = c
    except Exception:
        pass
    return interpret_prompt(body.prompt, llm=llm)


@app.post('/api/run')
def api_run(body: RunIn):
    spec = body.model_dump(exclude_none=True)
    if body.prompt:
        # LLM planning when a provider is configured; keyword fallback otherwise
        llm = None
        try:
            from core.llm import LLMClient
            c = LLMClient(provider=spec.get('provider'),
                          model=spec.get('model'))
            if c.enabled:
                llm = c
        except Exception:
            llm = None
        plan = interpret_prompt(body.prompt, llm=llm)
        if not spec.get('dims'):
            spec['dims'] = plan['dimensions']
        if not spec.get('platforms'):
            spec['platforms'] = plan['platforms']
        spec['_plan'] = plan
        spec['_plan_source'] = plan['source']
    if not spec.get('dims'):
        spec['dims'] = ['functionality']
    run = _start_run(spec)
    return {'run_id': run.id, 'spec': spec}


@app.get('/api/runs')
def api_runs():
    with _LOCK:
        return [RUNS[r].brief() for r in RUNS_ORDER]


@app.get('/api/run/{rid}')
def api_run_get(rid: str):
    run = RUNS.get(rid)
    if not run:
        raise HTTPException(404, 'unknown run')
    return {**run.brief(), 'report': run.report}


@app.post('/api/run/{rid}/stop')
def api_run_stop(rid: str):
    run = RUNS.get(rid)
    if not run:
        raise HTTPException(404, 'unknown run')
    if run.status in ('done', 'error', 'stopped'):
        return {'ok': False, 'status': run.status}
    run.request_stop()
    return {'ok': True, 'status': run.status}


@app.get('/api/run/{rid}/stream')
def api_stream(rid: str):
    run = RUNS.get(rid)
    if not run:
        raise HTTPException(404, 'unknown run')

    def gen():
        # 1. replay buffered history so reopening "view" (or refreshing the
    #    page) always shows the full trace, even if it already finished.
        for item in list(run.buffer):
            yield 'data: ' + json.dumps(item, default=str) + '\n\n'
        if run.status in ('done', 'error', 'stopped'):
            yield 'data: ' + json.dumps(
                {'ts': datetime.now().strftime('%H:%M:%S'),
                 'line': '__DONE__', 'status': run.status},
                default=str) + '\n\n'
            return
        # 2. then stream live lines from the queue
        while True:
            try:
                item = run.q.get(timeout=25)
                yield 'data: ' + json.dumps(item, default=str) + '\n\n'
                if item.get('line') == '__DONE__':
                    break
            except queue.Empty:
                yield ': keep-alive\n\n'
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


@app.get('/api/findings')
def api_findings(limit: int = 200, rid: str | None = None):
    """Findings for a single run (default: the most recent run).

    Runs now write to per-run memory DBs, so findings come from the run's
    report (which holds only that run's findings) instead of a global table
    that mixed every run together.
    """
    run = RUNS.get(rid) if rid else None
    if run is None:
        for r in RUNS_ORDER:
            if isinstance(RUNS[r].report, dict):
                run = RUNS[r]
                break
    if run is not None and isinstance(run.report, dict):
        return run.report.get('finding_details', [])[:limit]
    # fallback: newest report file on disk (survives a server restart)
    files = sorted(glob.glob(os.path.join(CONFIG['reports_dir'], '*.json')),
                   key=os.path.getmtime, reverse=True)
    if files:
        try:
            with open(files[0], encoding='utf-8') as f:
                return json.load(f).get('finding_details', [])[:limit]
        except Exception:
            pass
    return []


@app.get('/api/screenshots')
def api_screenshots():
    files = sorted(glob.glob(os.path.join(_SCREENSHOTS, '*.png')),
                   key=os.path.getmtime, reverse=True)[:60]
    return [{'name': os.path.basename(f),
             'platform': os.path.basename(f).rsplit('_', 1)[0],
             'modified': datetime.fromtimestamp(
                 os.path.getmtime(f)).isoformat(timespec='seconds')}
            for f in files]


@app.get('/screenshots/{name}')
def api_screenshot(name: str):
    safe = os.path.basename(name)
    path = os.path.join(_SCREENSHOTS, safe)
    if not safe.endswith('.png') or not os.path.isfile(path):
        raise HTTPException(404, 'no such screenshot')
    return FileResponse(path, media_type='image/png')


@app.get('/api/reports')
def api_reports():
    files = sorted(glob.glob(os.path.join(CONFIG['reports_dir'], '*.json')),
                   key=os.path.getmtime, reverse=True)[:50]
    return [{'name': os.path.basename(f),
             'size': os.path.getsize(f),
             'modified': datetime.fromtimestamp(
                 os.path.getmtime(f)).isoformat(timespec='seconds')}
            for f in files]


@app.get('/api/report/{name}')
def api_report(name: str):
    safe = os.path.basename(name)
    path = os.path.join(CONFIG['reports_dir'], safe)
    if not safe.endswith('.json') or not os.path.isfile(path):
        raise HTTPException(404, 'no such report')
    return FileResponse(path, media_type='application/json',
                        filename=safe)


# --------------------------------------------------------- readable report view
_SEV_COLOR = {'critical': '#b3123a', 'high': '#c0392b', 'medium': '#b8860b',
              'low': '#2b6cb0', 'info': '#5a6577'}


def _e(x):
    return _html.escape(str(x if x is not None else ''))


def render_report_html(rpt, name):
    """Render one report JSON as a standalone, print-to-PDF-friendly page."""
    p, A = [], None
    out = []
    A = out.append
    suite = rpt.get('suite') or {}
    prov = (rpt.get('llm') or {}).get('provider')
    A('<!doctype html><html lang="en"><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width,initial-scale=1">'
      f'<title>ABA Report — {_e(name)}</title><style>'
      ':root{color-scheme:light}*{box-sizing:border-box}'
      'body{font:14px/1.55 system-ui,Segoe UI,Roboto,sans-serif;color:#1a2233;'
      'background:#eef1f7;margin:0;padding:20px}'
      '.wrap{max-width:900px;margin:0 auto;background:#fff;border:1px solid #dde3ee;'
      'border-radius:12px;padding:26px}'
      'h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:22px 0 8px;'
      'border-bottom:2px solid #eef1f7;padding-bottom:4px}'
      '.muted{color:#66738c;font-size:12px}'
      '.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}'
      '.card{flex:1;min-width:104px;border:1px solid #e3e8f2;border-radius:10px;'
      'padding:10px;text-align:center}.card .n{font-size:22px;font-weight:800}'
      '.card .l{font-size:11px;color:#66738c;text-transform:uppercase}'
      'table{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}'
      'th{text-align:left;background:#f4f6fb;padding:6px 8px;border:1px solid #e3e8f2;'
      'font-size:11px;text-transform:uppercase;color:#55617a}'
      'td{padding:6px 8px;border:1px solid #eef1f7;vertical-align:top}'
      '.sev{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;'
      'font-size:11px;font-weight:700;text-transform:uppercase}'
      '.exec{background:#f0f5ff;border:1px solid #cfe0ff;border-radius:8px;padding:12px 14px}'
      '.toolbar{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}'
      '.btn{background:#2b6cb0;color:#fff;border:0;border-radius:8px;padding:8px 14px;'
      'font:600 13px inherit;cursor:pointer;text-decoration:none;display:inline-block}'
      '.btn.sec{background:#eef1f7;color:#1a2233;border:1px solid #dde3ee}'
      'pre{background:#f4f6fb;border:1px solid #e3e8f2;border-radius:8px;padding:10px;'
      'overflow:auto;font-size:12px;white-space:pre-wrap}'
      '@media print{body{background:#fff;padding:0}.wrap{border:0;max-width:none;padding:0}'
      '.toolbar{display:none}}'
      '</style></head><body><div class="wrap">')
    A('<div class="toolbar">'
      '<button class="btn" onclick="window.print()">\U0001F5A8 Save as PDF</button>'
      f'<a class="btn sec" href="/api/report/{_e(name)}" download>⬇ Download JSON</a>'
      '</div>')
    A('<h1>ABA Fusion — Test Report</h1>'
      f'<div class="muted">{_e(name)} · {_e(rpt.get("ts",""))}'
      + (f' · LLM: {_e(prov)}' if prov else '') + '</div>')
    if rpt.get('prompt'):
        A(f'<div class="muted">Prompt: “{_e(rpt["prompt"])}”</div>')
    A('<div class="cards">')
    for n, lbl in [('total', 'Platforms'), ('alive', 'Alive'),
                   ('findings', 'Findings'), ('critical_high', 'Crit/High'),
                   ('patterns', 'Patterns')]:
        A(f'<div class="card"><div class="n">{_e(rpt.get(n, "–"))}</div>'
          f'<div class="l">{lbl}</div></div>')
    A('</div>')
    if rpt.get('executive_summary'):
        A('<h2>Executive summary</h2>'
          f'<div class="exec">{_e(rpt["executive_summary"])}</div>')
    cov = rpt.get('coverage')
    if cov:
        A('<h2>Coverage</h2>')
        if rpt.get('coverage_note'):
            A(f'<div class="muted">{_e(rpt["coverage_note"])}</div>')
        dims = ['performance', 'functionality', 'ux', 'limits', 'logic',
                'security']
        tick = '✓'
        A('<table><tr><th>Platform</th>'
          + ''.join('<th>' + d[:4] + '</th>' for d in dims) + '</tr>')
        for pk, row in cov.items():
            cells = ''.join(
                '<td>' + (tick if row.get(d) == 'ran' else _e(row.get(d, '-')))
                + '</td>' for d in dims)
            A('<tr><td>' + _e(pk) + '</td>' + cells + '</tr>')
        A('</table>')
    pd = rpt.get('platform_details') or []
    if pd:
        A('<h2>Platforms</h2><table><tr><th>Key</th><th>Name</th><th>Type</th>'
          '<th>Status</th><th>Base URL</th></tr>')
        for x in pd:
            A(f'<tr><td>{_e(x.get("key"))}</td><td>{_e(x.get("name"))}</td>'
              f'<td>{_e(x.get("type"))}</td><td>{_e(x.get("status"))}</td>'
              f'<td class="muted">{_e(x.get("base"))}</td></tr>')
        A('</table>')
    fd = rpt.get('finding_details') or []
    A(f'<h2>Findings ({len(fd)})</h2>')
    if fd:
        A('<table><tr><th>Platform</th><th>Category</th><th>Type</th>'
          '<th>Status</th><th>Severity</th><th>Payload</th></tr>')
        for f in fd:
            sev = str(f.get('severity', 'info'))
            col = _SEV_COLOR.get(sev, '#5a6577')
            A(f'<tr><td>{_e(f.get("platform"))}</td><td>{_e(f.get("category"))}</td>'
              f'<td>{_e(f.get("finding_type"))}</td><td>{_e(f.get("status"))}</td>'
              f'<td><span class="sev" style="background:{col}">{_e(sev)}</span></td>'
              f'<td class="muted">{_e(str(f.get("payload", ""))[:70])}</td></tr>')
        A('</table>')
    else:
        A('<div class="muted">No findings recorded in this run.</div>')
    perf = suite.get('performance') or {}
    if perf:
        A('<h2>Performance</h2><table><tr><th>Platform</th><th>Avg ms</th>'
          '<th>p95 ms</th><th>Max ms</th><th>HTML KB</th></tr>')
        for k, v in perf.items():
            kb = round((v.get('avg_html_bytes', 0) or 0) / 1024, 1)
            A(f'<tr><td>{_e(k)}</td><td>{_e(v.get("avg_ms"))}</td>'
              f'<td>{_e(v.get("p95_ms"))}</td><td>{_e(v.get("max_ms"))}</td>'
              f'<td>{_e(kb)}</td></tr>')
        A('</table>')
    lim = suite.get('limits') or {}
    if lim:
        A('<h2>Limits</h2><table><tr><th>Probe</th><th>HTTP</th><th>Time</th>'
          '<th>Verdict</th></tr>')
        for k, v in lim.items():
            if isinstance(v, dict) and 'status' in v:
                A(f'<tr><td>{_e(k)}</td><td>{_e(v.get("status"))}</td>'
                  f'<td>{_e(v.get("ms"))}ms</td><td>{_e(v.get("verdict"))}</td></tr>')
        A('</table>')
        if isinstance(lim.get('rate_limit_burst'), dict):
            A('<div class="muted">Rate-limit burst: '
              f'{_e(json.dumps(lim["rate_limit_burst"]))}</div>')
    fn = suite.get('functionality') or {}
    if fn:
        A('<h2>Functionality</h2><table><tr><th>Platform</th><th>HTTP</th>'
          '<th>SPA</th><th>Title</th><th>Route distinct</th>'
          '<th>Verified working</th></tr>')
        for k, v in fn.items():
            tick = lambda b: '✓' if b else '✗'  # noqa: E731
            rd = v.get('route_distinct')
            rd_txt = '?' if rd is None else ('✓' if rd else '✗ catch-all')
            vw = v.get('verified_working')
            vw_txt = ('✓ working' if vw else 'shell only') \
                if vw is not None else '?'
            A(f'<tr><td>{_e(k)}</td><td>{_e(v.get("http_status"))}</td>'
              f'<td>{tick(v.get("is_spa"))}</td><td>{tick(v.get("has_title"))}</td>'
              f'<td>{rd_txt}</td><td title="{_e(v.get("verified_by", ""))}">'
              f'{vw_txt}</td></tr>')
        A('</table><div class="muted">"Verified working" = a distinct route '
          '(route-diff) OR the UX pass rendered real content while '
          'authenticated; a catch-all 200 alone is not "working".</div>')
    if suite.get('logic'):
        A('<h2>Logic</h2><pre>' + _e(json.dumps(suite['logic'], indent=2)) + '</pre>')
    pat = rpt.get('pattern_details') or []
    if pat:
        A('<h2>Learned patterns</h2><table><tr><th>Platform</th><th>Pattern</th>'
          '<th>Confidence</th><th>Times seen</th></tr>')
        for x in pat:
            A(f'<tr><td>{_e(x.get("platform"))}</td><td>{_e(x.get("pattern"))}</td>'
              f'<td>{_e(x.get("confidence"))}</td><td>{_e(x.get("times_seen"))}</td></tr>')
        A('</table>')
    A('</div></body></html>')
    return ''.join(out)


@app.get('/report-view/{name}', response_class=HTMLResponse)
def report_view(name: str):
    """A human-readable, print-to-PDF-friendly rendering of a saved report."""
    safe = os.path.basename(name)
    path = os.path.join(CONFIG['reports_dir'], safe)
    if not safe.endswith('.json') or not os.path.isfile(path):
        raise HTTPException(404, 'no such report')
    with open(path, encoding='utf-8') as f:
        rpt = json.load(f)
    return HTMLResponse(render_report_html(rpt, safe))


def _free_port(host, start, tries=20):
    """Return the first free TCP port at/after ``start`` (so a second launch or
    a leftover server never crashes run.bat with 'address already in use')."""
    import socket
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start  # give up -> let uvicorn surface the error


if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('ABA_UI_HOST', '127.0.0.1')
    want = int(os.environ.get('ABA_UI_PORT', '8787'))
    port = _free_port(host, want)
    print('=' * 60)
    print(' ABA Fusion AI Agent — Web UI')
    if port != want:
        print(f' NOTE: port {want} was busy -> using {port} instead')
    print(f' Open:  http://{host}:{port}')
    print('=' * 60)
    try:
        uvicorn.run(app, host=host, port=port, log_level='warning')
    except OSError as e:
        print(f'\nERROR: could not start the web server on {host}:{port} ({e}).')
        print('Another copy may be running. Close it, or set a different port:')
        print('   set ABA_UI_PORT=8790  &  run.bat')
