"""Classify HTTP responses into (finding_type, severity) pairs."""


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
