"""Unit tests for core.classifier.classify()."""
import pytest

from core.classifier import classify


@pytest.mark.parametrize('status,body,expected', [
    (500, 'Internal Server Error', ('server_error', 'critical')),
    (503, 'Service Unavailable', ('server_error', 'critical')),
    (401, '{"detail":"token expired"}', ('auth_error', 'high')),
    (403, '{"detail":"forbidden"}', ('forbidden', 'high')),
    (200, '<script>alert(1)</script>', ('xss_reflected', 'critical')),
    (200, '<SCRIPT>ALERT(1)</SCRIPT>', ('xss_reflected', 'critical')),
    (200, 'root:x:0:0:/bin/sh', ('info_leak', 'high')),
    (200, 'Traceback... stack trace here', ('info_leak', 'high')),
    (400, 'SQL syntax error near', ('sql_error', 'medium')),
    (404, 'Not Found', ('not_found', 'info')),
    (200, 'ok', ('other', 'low')),
    (400, 'bad request', ('other', 'low')),
])
def test_classify(status, body, expected):
    assert classify(status, body) == expected


def test_classify_none_body():
    assert classify(200, None) == ('other', 'low')


def test_status_priority_over_body():
    """5xx wins even if body contains an XSS echo."""
    ft, sev = classify(500, '<script>alert(1)</script>')
    assert (ft, sev) == ('server_error', 'critical')


def test_severity_always_known():
    from core.config import SEVERITIES
    for st in (0, 200, 400, 401, 403, 404, 500, 599):
        _, sev = classify(st, 'anything')
        assert sev in SEVERITIES
