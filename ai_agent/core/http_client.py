"""Stdlib-only HTTP helpers shared by all agent variants."""
import json
import re
import urllib.error
import urllib.request

from .config import CONFIG


def req(method, url, body=None, token='', timeout=None):
    """Perform an HTTP request; never raises.

    Returns ``{'status': int, 'body': str}`` where status 0 means a
    connection-level failure (DNS, timeout, TLS, refused...).
    """
    h = {'User-Agent': 'AIAgent/1.0'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    elif isinstance(body, str):
        body = body.encode()
    try:
        r = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(
                r, timeout=timeout or CONFIG['timeout']) as resp:
            return {'status': resp.status,
                    'body': resp.read().decode('utf-8', errors='replace')}
    except urllib.error.HTTPError as e:
        return {'status': e.code,
                'body': e.read().decode('utf-8', errors='replace')}
    except Exception as e:
        return {'status': 0, 'body': str(e)[:150]}


def discover(base, timeout=5):
    """GET a platform root and report liveness / title / content-type."""
    try:
        r = urllib.request.Request(base, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            html = resp.read().decode('utf-8', errors='replace')
            title = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            return {'alive': True, 'code': resp.status,
                    'title': title.group(1).strip() if title else '',
                    'ct': resp.headers.get('Content-Type', '')}
    except Exception:
        return {'alive': False, 'code': 0, 'title': '', 'ct': ''}
