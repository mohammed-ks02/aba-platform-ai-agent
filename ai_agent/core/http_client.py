"""Stdlib-only HTTP helpers shared by all agent variants."""
import json
import re
import socket
import urllib.error
import urllib.request

from .config import CONFIG


class Aborted(Exception):
    """Raised when the current test run was cancelled by the user."""


# Cooperative cancellation: webui registers a callable that returns True once
# the user presses "Stop".  All network helpers check it before/while issuing
# requests so a stuck run can be killed quickly instead of hanging forever.
_ABORT = None


def set_abort_check(fn):
    """Register (or clear with None) a callable -> bool abort predicate."""
    global _ABORT
    _ABORT = fn


def check_abort():
    if _ABORT is not None and _ABORT():
        raise Aborted('run cancelled by user')


def _lower_headers(hdrs):
    """Response headers as a lowercase-keyed dict (last value wins)."""
    try:
        return {k.lower(): v for k, v in hdrs.items()}
    except Exception:
        return {}


def req(method, url, body=None, token='', timeout=None, headers=None):
    """Perform an HTTP request; never raises (except on user abort).

    ``headers`` optionally supplies extra request headers (e.g. an oversized
    header used by the limits probe). Returns
    ``{'status': int, 'headers': dict, 'body': str}`` where status 0 means a
    connection-level failure (DNS, timeout, TLS, refused...). ``headers`` are
    the RESPONSE headers, lowercase-keyed (empty on a connection failure).
    """
    check_abort()
    h = {'User-Agent': 'AIAgent/1.0'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    if headers:
        h.update(headers)
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    elif isinstance(body, str):
        body = body.encode()
    eff_timeout = timeout or CONFIG['timeout']
    try:
        r = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(r, timeout=eff_timeout) as resp:
            return {'status': resp.status,
                    'headers': _lower_headers(resp.headers),
                    'body': resp.read().decode('utf-8', errors='replace')}
    except urllib.error.HTTPError as e:
        return {'status': e.code,
                'headers': _lower_headers(e.headers) if e.headers else {},
                'body': e.read().decode('utf-8', errors='replace')}
    except socket.timeout:
        return {'status': 0, 'headers': {},
                'body': f'timed out after {eff_timeout}s'}
    except TimeoutError:
        return {'status': 0, 'headers': {},
                'body': f'timed out after {eff_timeout}s'}
    except Exception as e:
        return {'status': 0, 'headers': {}, 'body': str(e)[:150]}


def discover(base, timeout=5):
    """GET a platform root and report liveness / title / content-type."""
    check_abort()
    try:
        r = urllib.request.Request(base, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            html = resp.read().decode('utf-8', errors='replace')
            title = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            return {'alive': True, 'code': resp.status,
                    'title': title.group(1).strip() if title else '',
                    'ct': resp.headers.get('Content-Type', '')}
    except Aborted:
        raise
    except Exception:
        return {'alive': False, 'code': 0, 'title': '', 'ct': ''}
