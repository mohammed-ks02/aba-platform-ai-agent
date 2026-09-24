"""ABA Fusion Token Manager — auto-refresh every 12 minutes."""
import urllib.request, json, time, base64, os

BASE = 'https://stg-login.abafusion.ai'
DP = 'https://stg-dp.abafusion.ai/api/v1'
UA = {'User-Agent': 'Hermes-Agent/1.0'}
CREDS = {'username': 'test_02', 'password': 'AZaz12,,', 'tenant': 'arma'}

class TokenManager:
    """Manages ABA Fusion auth tokens with auto-refresh.

    - Access token: 15 min TTL (900s)
    - Refresh token: 7 days TTL (604800s) — but server-side refresh doesn't work
    - This manager auto-relogs every 12 min to avoid expiry during long tasks
    """

    def __init__(self, cache_file=None):
        self.cache_file = cache_file or os.path.join(
            os.environ.get('TMP', '/tmp'), 'aba_token_cache.json')
        self.access_token = None
        self.refresh_token = None
        self.tenant_id = None
        self.expires_at = 0
        self.user_info = {}
        self._load()

    def _save(self):
        data = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'tenant_id': self.tenant_id,
            'expires_at': self.expires_at,
            'user_info': self.user_info,
        }
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load(self):
        try:
            with open(self.cache_file) as f:
                data = json.load(f)
            self.access_token = data.get('access_token')
            self.refresh_token = data.get('refresh_token')
            self.tenant_id = data.get('tenant_id')
            self.expires_at = data.get('expires_at', 0)
            self.user_info = data.get('user_info', {})
        except Exception:
            pass

    def _decode_token(self, token):
        try:
            parts = token.split('.')
            if len(parts) == 3:
                payload = base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4))
                return json.loads(payload)
        except Exception:
            pass
        return {}

    def login(self):
        """Login and store tokens."""
        req = urllib.request.Request(
            f'{BASE}/api/v1/auth/login',
            data=json.dumps(CREDS).encode(),
            headers={'Content-Type': 'application/json', **UA})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())

        self.access_token = data.get('accessToken')
        self.refresh_token = data.get('refreshToken')
        self.tenant_id = data.get('tenantId')
        user = data.get('user', {})
        self.user_info = {
            'username': user.get('username'),
            'email': user.get('email'),
            'id': user.get('id'),
            'tenant': user.get('tenant', {}).get('name'),
            'isSuperAdmin': user.get('isSuperAdmin', False),
        }
        # Access TTL = 900s, use 12 min (720s) as refresh threshold
        payload = self._decode_token(self.access_token)
        self.expires_at = payload.get('exp', 0) - 300  # refresh 5 min early
        self._save()
        return self.access_token

    def ensure_valid(self):
        """Return a valid access token, refreshing if needed."""
        now = time.time()
        if self.access_token and now < self.expires_at:
            return self.access_token
        # Token expired or missing — re-login
        print(f'[TokenManager] Re-login (token expired/missing)')
        return self.login()

    def headers(self):
        """Get auth headers for data platform requests."""
        token = self.ensure_valid()
        return {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Hermes-Agent/1.0',
        }


# Demo: show token lifecycle
if __name__ == '__main__':
    tm = TokenManager(cache_file='/tmp/aba_token_cache.json')

    print('=' * 50)
    print('TOKEN MANAGER DEMO')
    print('=' * 50)

    # First login
    print('\n1. FIRST LOGIN')
    token = tm.login()
    print(f'   Access: {token[:50]}... (len={len(token)})')
    print(f'   Refresh: {tm.refresh_token[:50]}... (len={len(tm.refresh_token or "")})')
    print(f'   Tenant: {tm.tenant_id}')
    print(f'   User: {tm.user_info}')
    print(f'   Re-login before: {time.ctime(tm.expires_at)}')

    # Test API access
    print('\n2. DATA PLATFORM ACCESS')
    headers = tm.headers()
    for ep in ['/health', '/groups?limit=3', '/devices?limit=3', '/assets?limit=3']:
        try:
            req = urllib.request.Request(DP + ep, headers=headers)
            r = urllib.request.urlopen(req, timeout=10)
            d = json.loads(r.read().decode())
            items = d.get('results', d if isinstance(d, list) else [])
            print(f'   {ep}: OK ({len(items) if isinstance(items, list) else "dict"})')
        except Exception as e:
            print(f'   {ep}: {e}')

    # Show that token would need refresh in 12 min
    print('\n3. TOKEN LIFECYCLE')
    payload = tm._decode_token(tm.access_token)
    iat = payload.get('iat')
    exp = payload.get('exp')
    print(f'   Issued:   {time.ctime(iat)}')
    print(f'   Expires:  {time.ctime(exp)}')
    print(f'   TTL:      {exp - iat}s = {(exp-iat)/60:.1f} min')
    print(f'   Re-login at: {time.ctime(tm.expires_at)} (5 min before expiry)')

    # Verify cached token works
    print('\n4. CACHED TOKEN VERIFICATION')
    tm2 = TokenManager(cache_file='/tmp/aba_token_cache.json')
    token2 = tm2.ensure_valid()
    print(f'   Cached token valid: {token2[:50] == token[:50]}')
    print(f'   Headers ready: {"Authorization" in tm2.headers()}')

    print('\n' + '=' * 50)
    print('USE IN OTHER SCRIPTS:')
    print('=' * 50)
    print('''
from token_manager import TokenManager
tm = TokenManager()
headers = tm.headers()  # always returns valid token
# Use headers in requests:
req = urllib.request.Request(DP + '/groups', headers=headers)
''')