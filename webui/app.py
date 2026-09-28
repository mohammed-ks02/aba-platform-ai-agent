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
        self.report = None                # filled when done
        self.error = None
        self.stop_requested = threading.Event()

    def request_stop(self):
        """Mark the run for cancellation; helpers raise Aborted soon after."""
        self.stop_requested.set()
        self.trace('[web] STOP requested - cancelling at next step...')

    def trace(self, msg):
        self.q.put({'ts': datetime.now().strftime('%H:%M:%S'),
                    'line': str(msg).strip()})

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


def _apply_llm_env(spec):
    """Configure provider selection via env so LLMClient picks it up."""
    if spec.get('provider'):
        os.environ['ABA_LLM_PROVIDER'] = spec['provider']
    if spec.get('model'):
        os.environ['ABA_LLM_MODEL'] = spec['model']
    if spec.get('base_url'):
        os.environ['ABA_LLM_BASE_URL'] = spec['base_url']
    if spec.get('api_key'):
        os.environ['ABA_LLM_API_KEY'] = spec['api_key']


def _execute(run):
    """Run the requested dimensions inside a worker thread."""
    spec = run.spec
    from core.http_client import Aborted, set_abort_check
    set_abort_check(run.stop_requested.is_set)
    try:
        run.status = 'running'
        run.trace(f'[web] run {run.id} started: {json.dumps({k: v for k, v in spec.items() if k != "prompt"})[:200]}')
        mem = Memory()

        holder = R._LLMHolder(None)
        try:
            from core.llm import LLMClient
            client = LLMClient(provider=spec.get('provider') or None,
                               model=spec.get('model') or None,
                               base_url=spec.get('base_url') or None)
            if client.enabled:
                holder = R._LLMHolder(client,
                                      ai_payloads=bool(spec.get('ai_payloads')))
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
        if suite_dims:
            res = run_suite(mem, token=token, dims=suite_dims,
                            quick=bool(spec.get('quick')), keys=plats,
                            trace_cb=run.trace, llm_holder=holder,
                            security_fn=R.run_fuzz if do_sec else None)
            extra['suite'] = res
        elif do_sec:
            res = R.run_fuzz(mem, token, quick=bool(spec.get('quick')),
                             llm=holder, trace_cb=run.trace, platforms=plats)
            extra['suite'] = {'security': {'findings_recorded': res}}
        if 'ux' in dims:
            try:
                from core.ux_review import review_all
                extra['ux_reviews'] = review_all(
                    llm=holder.client if holder.enabled else None,
                    keys=plats, headless=True, trace_cb=run.trace)
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
        run.q.put({'ts': datetime.now().strftime('%H:%M:%S'),
                   'line': '__DONE__', 'status': run.status})


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


# -------------------------------------------------------------------- routes
@app.get('/', response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(_HERE, 'static', 'index.html'))


@app.get('/api/platforms')
def api_platforms():
    return [{'key': k, **{f: v[f] for f in ('name', 'type')}}
            for k, v in PLATFORMS.items()]


@app.get('/api/providers')
def api_providers():
    from core.llm.providers import PROVIDERS
    out = []
    for name, cls in sorted(PROVIDERS.items()):
        try:
            avail = cls.available()
        except Exception:
            avail = False
        out.append({'name': name,
                    'default_model': getattr(cls, 'default_model_name', None),
                    'configured': bool(avail)})
    return out


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
    _apply_llm_env(spec)
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
def api_findings(limit: int = 200):
    cols = ['platform', 'endpoint', 'category', 'payload', 'status',
            'finding_type', 'severity']
    return [dict(zip(cols, f)) for f in Memory().findings(limit)]


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


if __name__ == '__main__':
    import uvicorn
    print('=' * 60)
    print(' ABA Fusion AI Agent — Web UI')
    print(' Open:  http://127.0.0.1:8787')
    print('=' * 60)
    uvicorn.run(app, host=os.environ.get('ABA_UI_HOST', '127.0.0.1'),
                port=int(os.environ.get('ABA_UI_PORT', '8787')),
                log_level='warning')
