#!/usr/bin/env python3
"""UX/UI reviewer: real-browser screenshots + LLM "real user" commentary.

For every platform the agent opens the page in Chromium (Playwright), takes
a full-page screenshot, extracts DOM facts (headings, forms, buttons, alt
coverage, console errors) and then asks the configured vision-capable LLM to
comment on the interface *like a real user would* -- first impressions,
clarity, navigation, accessibility issues.  If no LLM is available (or the
call fails) deterministic heuristics still produce honest UX comments so the
feature never silently disappears.

Screenshots are written under ``CONFIG['data_dir']/screenshots/`` and served
by the web UI for visualised testing.
"""
import base64
import json
import os
from datetime import datetime

from .config import CONFIG, PLATFORMS


def _screenshot_dir():
    d = os.path.join(CONFIG['data_dir'], 'screenshots')
    os.makedirs(d, exist_ok=True)
    return d


def _dom_facts(page):
    """Extract simple UX-relevant facts from the live DOM."""
    try:
        return page.evaluate("""() => {
            const q = s => Array.from(document.querySelectorAll(s));
            const imgs = q('img');
            const inputs = q('input, textarea, select');
            const labelled = inputs.filter(i =>
                i.id && document.querySelector(`label[for="${i.id}"]`)
                || i.getAttribute('aria-label') || i.closest('label')).length;
            const h1 = q('h1').map(h => h.innerText.trim()).filter(Boolean);
            return {
                title: document.title,
                url: location.href,
                headings_h1: h1.slice(0, 3),
                n_buttons: q('button, [role=button]').length,
                n_links: q('a').length,
                n_inputs: inputs.length,
                inputs_labelled_pct: inputs.length
                    ? Math.round(100 * labelled / inputs.length) : null,
                imgs_missing_alt: imgs.filter(i => !i.alt).length,
                body_text_chars: (document.body &&
                    document.body.innerText || '').trim().length,
                nav_present: !!q('nav, [role=navigation]').length,
            };
        }""")
    except Exception as e:
        return {'error': str(e)[:120]}


SYSTEM_PROMPT = (
    "You are a picky but fair everyday user reviewing a web app you just "
    "opened for the first time. Give short, concrete comments about what you "
    "see: first impression, whether it is obvious what to do next, "
    "navigation clarity, visible errors or blank areas, and accessibility "
    "problems. Reply ONLY with JSON: "
    '{"first_impression": "...", "confusions": ["..."], '
    '"praise": ["..."], "accessibility_issues": ["..."], '
    '"overall_rating_1to5": N}')


def _llm_comment(llm, facts, png_path):
    """Ask the LLM for user-style comments; fall back to None on failure."""
    if llm is None:
        return None
    try:
        # Vision attempt when the provider supports images
        img_b64 = None
        if png_path and os.path.exists(png_path):
            with open(png_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode()
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': json.dumps(facts)}]
        if img_b64:
            messages[1]['content'] = [
                {'type': 'text', 'text': json.dumps(facts)},
                {'type': 'image_url', 'image_url': {'url':
                    f'data:image/png;base64,{img_b64}'}}]
        raw = llm.chat(messages, temperature=0.4, max_tokens=700)
        from .llm.base import extract_json
        data = extract_json(raw)
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _heuristic_comments(facts):
    """Deterministic 'user voice' comments when no LLM answered."""
    conf, praise, acc = [], [], []
    if facts.get('body_text_chars', 0) < 80:
        conf.append('The page looks almost empty - I cannot tell what this '
                    'app does.')
    if not facts.get('nav_present'):
        conf.append('No visible navigation - I would not know where to go.')
    if not facts.get('headings_h1'):
        conf.append('There is no clear page title/heading telling me where '
                    'I am.')
    if facts.get('n_buttons', 0) == 0 and facts.get('n_links', 0) == 0:
        conf.append('Nothing appears clickable - I am stuck at the start.')
    ia = facts.get('inputs_labelled_pct')
    if ia is not None and ia < 70:
        acc.append(f'Only {ia}% of form fields have labels - screen-reader '
                   'users will struggle.')
    if facts.get('imgs_missing_alt', 0) > 0:
        acc.append(f"{facts['imgs_missing_alt']} image(s) missing alt text.")
    if not conf and not acc:
        praise.append('Page renders with content, navigation and labelled '
                      'forms - good first impression.')
    rating = 5 - min(3, len(conf) + len(acc))
    return {'first_impression': (f"Heading: {facts.get('headings_h1')} | "
                                 f"{facts.get('n_buttons', 0)} buttons, "
                                 f"{facts.get('n_links', 0)} links visible"),
            'confusions': conf, 'praise': praise,
            'accessibility_issues': acc, 'overall_rating_1to5': rating,
            'source': 'heuristics'}


def _banner(page, step, total, text, sub=''):
    """Show a fixed on-page banner naming the current test step (headed runs)."""
    try:
        page.evaluate(
            """([step, total, text, sub]) => {
                let el = document.getElementById('__aba_banner');
                if (!el) {
                    el = document.createElement('div');
                    el.id = '__aba_banner';
                    el.style.cssText = 'position:fixed;top:0;left:0;right:0;'
                      + 'z-index:2147483647;background:#0e1420;color:#dbe3f0;'
                      + 'font:600 15px system-ui,Segoe UI,sans-serif;'
                      + 'padding:10px 16px;border-bottom:3px solid #4f8cff;'
                      + 'box-shadow:0 2px 14px rgba(0,0,0,.55)';
                    document.documentElement.appendChild(el);
                }
                el.innerHTML = '<span style="color:#4f8cff">ABA test agent</span>'
                  + ' · step ' + step + '/' + total + ' · ' + text
                  + (sub ? '<div style="font-weight:400;font-size:12px;'
                    + 'color:#8593ab;margin-top:2px">' + sub + '</div>' : '');
            }""", [step, total, text, sub])
    except Exception:
        pass


def _highlight(page, selector, color):
    """Outline elements matching selector so a watcher sees what is inspected."""
    try:
        return page.evaluate(
            """([sel, color]) => {
                const els = Array.from(document.querySelectorAll(sel));
                els.forEach(e => { e.style.outline = '3px solid ' + color;
                                   e.style.outlineOffset = '1px'; });
                return els.length;
            }""", [selector, color])
    except Exception:
        return 0


def _clean_overlays(page):
    """Remove the banner + all injected outlines before the report screenshot."""
    try:
        page.evaluate("""() => {
            const b = document.getElementById('__aba_banner'); if (b) b.remove();
            document.querySelectorAll('*').forEach(e => {
                if (e.style && e.style.outline) e.style.outline = ''; });
        }""")
    except Exception:
        pass


def _login(page, trace):
    """Authenticate through the central SSO (stg-login.abafusion.ai) using the
    agent's OWN test account from the environment (ABA_USERNAME/ABA_PASSWORD) --
    the same account and credentials the API TokenManager already uses. Lands
    the browser INSIDE the authenticated app. Returns True on success."""
    user = os.environ.get('ABA_USERNAME', '')
    pw = os.environ.get('ABA_PASSWORD', '')
    if not (user and pw):
        trace('[auth] ABA_USERNAME/ABA_PASSWORD not set - staying anonymous')
        return False
    try:
        page.wait_for_selector('#username, input[name="username"]',
                               timeout=15000)
        page.fill('#username', user)
        page.fill('#password', pw)
        try:
            page.click('button:has-text("Sign In")', timeout=5000)
        except Exception:
            page.click('button[type="submit"]')
        # SSO bounces through stg-login and redirects back to the platform;
        # wait until the URL actually leaves the login host before judging.
        try:
            page.wait_for_url(lambda u: 'stg-login' not in u, timeout=25000)
        except Exception:
            page.wait_for_load_state('networkidle', timeout=12000)
        page.wait_for_timeout(1500)
        ok = 'stg-login' not in page.url and '/login' not in page.url
        trace(f'[auth] SSO login {"OK" if ok else "did not complete"} '
              f'-> {page.url[:70]}')
        return ok
    except Exception as e:
        trace(f'[auth] login error: {str(e)[:130]}')
        return False


def _headed_walkthrough(page, key, trace, authenticated=False):
    """Visibly narrate the checks. When not authenticated it stays on the
    public landing page; when authenticated it walks the logged-in app."""
    total = 5

    def hold(ms):
        try:
            page.wait_for_timeout(ms)
        except Exception:
            pass

    if authenticated:
        _banner(page, 1, total, 'Inside the authenticated app',
                'Logged in via the agent test account — testing what is inside')
        trace(f'[ux:show] {key}: authenticated app loaded')
    else:
        _banner(page, 1, total, 'Loaded public page',
                'No login — inspecting the public/landing page only')
        trace(f'[ux:show] {key}: public page loaded (no login)')
    hold(1800)

    _banner(page, 2, total, 'Structure: navigation & headings')
    _highlight(page, 'nav, [role=navigation], header', '#4fd1a5')
    _highlight(page, 'h1, h2', '#4f8cff')
    hold(1800); _clean_overlays(page)

    _banner(page, 3, total, 'Forms & input labels (accessibility)')
    n_f = _highlight(page, 'form', '#ff6b6b')
    n_in = _highlight(page, 'input, textarea, select', '#ffb454')
    trace(f'[ux:show] {key}: {n_f} form(s), {n_in} field(s) checked for labels')
    hold(1900); _clean_overlays(page)

    _banner(page, 4, total, 'Interactive controls: links & buttons')
    n_b = _highlight(page, 'button, [role=button]', '#7cc4ff')
    n_l = _highlight(page, 'a[href]', '#b18cff')
    trace(f'[ux:show] {key}: {n_b} button(s), {n_l} link(s) outlined')
    hold(1700); _clean_overlays(page)

    _banner(page, 5, total, 'Images alt-text + scrolling through the page')
    n_alt = _highlight(page, 'img:not([alt]), img[alt=""]', '#ff4d6d')
    trace(f'[ux:show] {key}: {n_alt} image(s) missing alt-text; scrolling page')
    try:
        for frac in (0.3, 0.6, 1.0):
            page.evaluate("f => window.scrollTo({top: document.body."
                          "scrollHeight*f, behavior:'smooth'})", frac)
            hold(750)
        page.evaluate("() => window.scrollTo({top:0, behavior:'smooth'})")
    except Exception:
        pass
    hold(1200)


def review_platform(key, llm=None, headless=True, slow_mo=0, trace_cb=None,
                    authenticate=False):
    """Open one platform in Chromium, screenshot, collect facts+comments.

    ``authenticate=True`` logs in through the SSO with the agent's test account
    so the checks run INSIDE the app; otherwise it stays on the public page and
    never logs in."""
    from playwright.sync_api import sync_playwright
    info = PLATFORMS[key]
    shot = os.path.join(_screenshot_dir(), f'{key}_{datetime.now():%H%M%S}.png')
    result = {'platform': key, 'name': info['name'], 'url': info['base'],
              'screenshot': os.path.basename(shot)}
    with sync_playwright() as pw:
        # When headed (visible), open a big maximized window and dwell longer
        # so a human can actually watch it; headless stays lean and fast.
        launch_kw = {'headless': headless, 'slow_mo': slow_mo}
        if not headless:
            launch_kw['args'] = ['--start-maximized']
        browser = pw.chromium.launch(**launch_kw)
        ctx_kw = {'ignore_https_errors': True}
        if not headless:
            ctx_kw['no_viewport'] = True   # let the maximized window drive size
        ctx = browser.new_context(**ctx_kw)
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)[:120]))
        trace = trace_cb or (lambda m: None)
        try:
            resp = page.goto(info['base'], wait_until='domcontentloaded',
                             timeout=30000)
            page.wait_for_timeout(2000)
            authed = False
            if authenticate:
                authed = _login(page, trace)
                page.wait_for_timeout(2500)  # let the app boot post-redirect
                result['authenticated'] = authed
            # (#7) hydration wait instead of a blind sleep: wait for the
            # network to go idle and for real content to render, so we never
            # rate an un-hydrated / white-screen shell as if it worked.
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_function(
                    "() => (document.body && document.body.innerText || '')"
                    ".trim().length > 60", timeout=6000)
                result['rendered'] = True
            except Exception:
                result['rendered'] = False
                trace(f'[ux] {key}: WARNING little/no rendered content '
                      '(possible blank/white screen) - not just a shell')
            if not headless:
                page.wait_for_timeout(1200)            # settle for watching
                _headed_walkthrough(page, key, trace, authenticated=authed)
                _clean_overlays(page)                  # clean report screenshot
            result['http_status'] = resp.status if resp else None
            result['final_url'] = page.url
            facts = _dom_facts(page)
            facts['console_errors'] = errors[:5]
            facts['rendered'] = result.get('rendered')
            # (#10) light scripted interaction for interactive / AI products:
            # type into the primary input, submit, and note whether a response
            # region appears. Best-effort with test input; never fails review.
            if info.get('type') == 'agentic' and authed:
                try:
                    box = page.query_selector(
                        'textarea, input[type="text"], [contenteditable="true"]')
                    if box:
                        before = len(page.inner_text('body'))
                        box.click()
                        page.keyboard.type('test')
                        page.keyboard.press('Enter')
                        page.wait_for_timeout(3000)
                        after = len(page.inner_text('body'))
                        facts['interaction'] = {'sent': True,
                                                'responded': after > before + 20}
                        trace(f'[ux] {key}: scripted interaction sent -> '
                              f'responded={facts["interaction"]["responded"]}')
                except Exception as e:
                    trace(f'[ux] {key}: interaction skipped ({str(e)[:60]})')
            result['facts'] = facts
            try:
                page.screenshot(path=shot, full_page=True)
            except Exception:
                page.screenshot(path=shot)
        except Exception as e:
            result['error'] = str(e)[:200]
            facts = {'error': result['error']}
            result['facts'] = facts
        finally:
            browser.close()
    ai = _llm_comment(llm, facts, shot)
    result['ux_review'] = ai or _heuristic_comments(facts)
    if ai:
        result['ux_review']['source'] = 'llm'
    return result


def review_all(llm=None, keys=None, headless=True, slow_mo=0, trace_cb=None,
               authenticate=False):
    from .http_client import check_abort
    trace = trace_cb or (lambda m: None)
    out = []
    for key in (keys or PLATFORMS):
        check_abort()
        trace(f'[ux] reviewing {key} in Chromium '
              f'({"authenticated" if authenticate else "public"}) ...')
        r = review_platform(key, llm=llm, headless=headless, slow_mo=slow_mo,
                            trace_cb=trace, authenticate=authenticate)
        rv = r.get('ux_review', {})
        trace(f"[ux] {key}: rating {rv.get('overall_rating_1to5')}/5 "
              f"({rv.get('source', 'llm')}) "
              f"- {str(rv.get('first_impression'))[:80]}")
        out.append(r)
    return out
