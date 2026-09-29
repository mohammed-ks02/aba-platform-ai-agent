"""Classify HTTP responses into findings.

Two entry points:
  * ``classify(status, body)`` -- the original status/body -> (type, severity)
    map, kept unchanged for existing callers and tests.
  * ``analyze_response(status, body, payload, category)`` -- body-aware
    analysis that looks for concrete exploitation *evidence* (reflected
    payload, evaluated template, DB error strings, file contents, stack
    traces, cloud-metadata) and reports whether a real *signal* was seen, so
    the runner can drop status-only noise (e.g. a 422 validation error).
"""

# Evidence signatures grouped by finding type. Each entry:
#   (finding_type, severity, [lowercased substrings that prove it])
_BODY_SIGNATURES = [
    ('file_read', 'critical',
     ['root:x:0:0', 'root:*:0:0', '[extensions]', '16-bit app support',
      'daemon:x:', '/bin/bash\n']),
    # SSRF must be proven by metadata RESPONSE CONTENT the attacker did not
    # send (an echoed request URL is reflection, not a leak -- see the
    # reflection guard in the loop below).
    ('ssrf_metadata', 'critical',
     ['ami-launch-index', 'instance-action', 'ami-manifest-path',
      '"accesskeyid"', '"secretaccesskey"', 'iam/security-credentials/\n']),
    ('sql_error', 'high',
     ['sql syntax', 'sqlstate', 'you have an error in your sql',
      'unclosed quotation', 'ora-0', 'psqlexception', 'pg::',
      'syntax error at or near', 'sqlite3.', 'mysql_fetch', 'odbc driver']),
    ('nosql_error', 'high',
     ['mongoerror', 'e11000', 'bsontype', 'unknown operator',
      'cast to objectid failed', '$where', 'mongoservererror']),
    ('info_leak', 'medium',
     ['traceback (most recent call last)', 'exception in thread',
      '\tat java.', 'system.web', 'werkzeug', '.py", line',
      'stacktrace', 'com.mongodb', 'org.springframework']),
]


def _evidence(text, needle, span=70):
    """Return a short window of ``text`` around ``needle`` for the report."""
    i = text.lower().find(needle.lower())
    if i < 0:
        return (text or '')[:span].replace('\n', ' ')
    a = max(0, i - 20)
    return text[a:i + len(needle) + span].replace('\n', ' ').strip()


def analyze_response(status, body, payload='', category='', headers=None):
    """Body-aware verdict for one probe.

    Returns a dict ``{type, severity, evidence, signal}`` where ``signal`` is
    True only when a concrete exploitation indicator was found (not merely a
    suggestive status code). Callers use ``signal`` to gate reporting and to
    trigger the differential control re-check. ``headers`` (response headers,
    lowercase-keyed) makes reflected-XSS context-aware.
    """
    b = body or ''
    bl = b.lower()
    p = str(payload or '')
    cat = (category or '').lower()
    ct = (headers or {}).get('content-type', '').lower()

    # 1. Reflected XSS: the payload comes back unescaped AND the response is
    #    served as HTML (a JSON API echoing input in an error is not XSS).
    if (cat == 'xss' and p and p in b and ('html' in ct or not ct)
            and any(m in p.lower()
                    for m in ('<script', 'onerror', '<svg', 'onload'))):
        return {'type': 'xss_reflected', 'severity': 'high',
                'evidence': _evidence(b, p), 'signal': True}

    # 2. SSTI: the expression was evaluated (1327*1331 -> 1766237) and the
    #    literal template is NOT echoed back (that would just be reflection).
    #    A rare product avoids the '49' coincidental-match false positive.
    if cat == 'ssti' and '1766237' in b and '1327*1331' not in b:
        return {'type': 'ssti_evaluated', 'severity': 'critical',
                'evidence': _evidence(b, '1766237'), 'signal': True}

    # 3. Body signatures (file read, metadata, SQL/NoSQL errors, stack traces).
    #    Reflection guard: if the matched string is part of the payload we
    #    sent, the server merely echoed our input (e.g. a 404 error page
    #    repeating the URL) -- that is NOT a finding. This is what turned a
    #    reflected SSRF URL in a 404 into a false CRITICAL.
    pl = p.lower()
    for ftype, sev, sigs in _BODY_SIGNATURES:
        for s in sigs:
            if s in bl and s not in pl:
                return {'type': ftype, 'severity': sev,
                        'evidence': _evidence(b, s), 'signal': True}

    # 4. Server error: a 5xx triggered by the payload is a candidate, but
    #    flaky staging looks identical, so it is only MEDIUM until the caller's
    #    control + repeat re-checks prove it stable and payload-correlated.
    if status >= 500:
        return {'type': 'server_error', 'severity': 'medium',
                'evidence': f'HTTP {status}: ' + bl[:60], 'signal': True}

    # 5. Nothing concrete -- status-only, treated as noise (not reported).
    return {'type': 'no_signal', 'severity': 'info',
            'evidence': f'HTTP {status}', 'signal': False}


def passive_findings(status, headers, body, url=''):
    """Passive security review of ANY response (all 9 platforms, no payloads).

    Flags missing/weak security headers, unsafe cookies, permissive CORS, and
    open redirects. Returns a list of ``{finding_type, severity, category,
    evidence, recommendation}`` dicts. Zero risk -- it only reads what the
    server already returned.
    """
    h = headers or {}
    out = []

    def add(ftype, sev, evidence, rec):
        out.append({'finding_type': ftype, 'severity': sev,
                    'category': 'headers', 'evidence': evidence,
                    'recommendation': rec})

    # --- missing security headers (only meaningful on an HTML document) ------
    ct = h.get('content-type', '').lower()
    if 'html' in ct:
        checks = [
            ('content-security-policy', 'missing_csp', 'medium',
             'Add a Content-Security-Policy header'),
            ('x-frame-options', 'missing_x_frame_options', 'low',
             'Add X-Frame-Options: DENY (or CSP frame-ancestors) to stop '
             'clickjacking'),
            ('x-content-type-options', 'missing_x_content_type', 'low',
             'Add X-Content-Type-Options: nosniff'),
        ]
        for hdr, ftype, sev, rec in checks:
            if hdr not in h:
                add(ftype, sev, f'no {hdr} on {ct}', rec)
    if 'strict-transport-security' not in h and url.startswith('https'):
        add('missing_hsts', 'low', 'no Strict-Transport-Security on HTTPS',
            'Add HSTS: Strict-Transport-Security: max-age=31536000')

    # --- weak cookies -------------------------------------------------------
    setc = h.get('set-cookie', '')
    if setc:
        low = setc.lower()
        if 'httponly' not in low:
            add('cookie_no_httponly', 'medium', 'Set-Cookie without HttpOnly',
                'Set HttpOnly on session cookies to block JS theft')
        if 'secure' not in low:
            add('cookie_no_secure', 'medium', 'Set-Cookie without Secure',
                'Set Secure so cookies are HTTPS-only')
        if 'samesite' not in low:
            add('cookie_no_samesite', 'low', 'Set-Cookie without SameSite',
                'Set SameSite=Lax/Strict to reduce CSRF exposure')

    # --- permissive CORS ----------------------------------------------------
    acao = h.get('access-control-allow-origin', '')
    if acao == '*' and h.get('access-control-allow-credentials',
                             '').lower() == 'true':
        add('cors_wildcard_with_creds', 'high',
            'ACAO:* together with Allow-Credentials:true',
            'Never combine wildcard origin with credentials; echo an '
            'allow-listed origin instead')

    # --- version/tech leak --------------------------------------------------
    for hdr in ('server', 'x-powered-by', 'x-aspnet-version'):
        v = h.get(hdr)
        if v and any(ch.isdigit() for ch in v):
            add('version_disclosure', 'low', f'{hdr}: {v}',
                f'Remove or genericise the {hdr} header (version fingerprint)')

    # --- open redirect ------------------------------------------------------
    if 300 <= status < 400:
        loc = h.get('location', '')
        if loc.startswith('//') or loc.startswith('http://evil') \
                or 'evil.com' in loc:
            add('open_redirect', 'high', f'3xx Location: {loc[:80]}',
                'Validate/allow-list redirect targets')
    return out


def classify(status, body):
    """Map an HTTP status + response body to a finding type and severity.

    Severity scale: critical > high > medium > low > info.
    """
    bl = (body or '').lower()
    if status >= 500:
        return ('server_error', 'critical')
    if status == 401:
        return ('auth_error', 'high')
    if status == 403:
        return ('forbidden', 'high')
    if '<script' in bl and 'alert' in bl:
        return ('xss_reflected', 'critical')
    if 'root:' in bl:
        return ('info_leak', 'high')
    if 'stack' in bl and 'trace' in bl:
        return ('info_leak', 'high')
    if 'sql' in bl and 'error' in bl:
        return ('sql_error', 'medium')
    if status == 404:
        return ('not_found', 'info')
    return ('other', 'low')
