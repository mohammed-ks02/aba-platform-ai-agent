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


def review_platform(key, llm=None, headless=True, slow_mo=0):
    """Open one platform in Chromium, screenshot, collect facts+comments."""
    from playwright.sync_api import sync_playwright
    info = PLATFORMS[key]
    shot = os.path.join(_screenshot_dir(), f'{key}_{datetime.now():%H%M%S}.png')
    result = {'platform': key, 'name': info['name'], 'url': info['base'],
              'screenshot': os.path.basename(shot)}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)[:120]))
        try:
            resp = page.goto(info['base'], wait_until='domcontentloaded',
                             timeout=30000)
            page.wait_for_timeout(2500)  # let the SPA hydrate
            result['http_status'] = resp.status if resp else None
            result['final_url'] = page.url
            facts = _dom_facts(page)
            facts['console_errors'] = errors[:5]
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


def review_all(llm=None, keys=None, headless=True, slow_mo=0, trace_cb=None):
    from .http_client import check_abort
    trace = trace_cb or (lambda m: None)
    out = []
    for key in (keys or PLATFORMS):
        check_abort()
        trace(f'[ux] reviewing {key} in Chromium ...')
        r = review_platform(key, llm=llm, headless=headless, slow_mo=slow_mo)
        rv = r.get('ux_review', {})
        trace(f"[ux] {key}: rating {rv.get('overall_rating_1to5')}/5 "
              f"({rv.get('source', 'llm')}) "
              f"- {str(rv.get('first_impression'))[:80]}")
        out.append(r)
    return out
