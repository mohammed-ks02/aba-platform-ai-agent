#!/usr/bin/env python3
"""
ABA FUSION MULTI-PLATFORM AI AGENT — Ultra Fast Mode
Tests all 9 platforms in under 30 seconds
"""

import json, os, sqlite3, urllib.request, urllib.error, re
from datetime import datetime

CONFIG = {"data_dir": "ai_agent/data", "memory_db": "ai_agent/data/memory.db",
          "reports_dir": "ai_agent/data/reports", "timeout": 8}

PLATFORMS = {
    "stg-dp": {"name": "Data Platform", "base": "https://stg-dp.abafusion.ai",
               "mgr": "https://stg-dp-mgr.abafusion.ai", "type": "data_platform"},
    "stg-analytics": {"name": "Analytics", "base": "https://stg-analytics.abafusion.ai", "type": "analytics"},
    "stg-pulse": {"name": "Pulse", "base": "https://stg-pulse.abafusion.ai", "type": "pulse"},
    "stg-orbit": {"name": "Orbit", "base": "https://stg-orbit.abafusion.ai", "type": "orbit"},
    "stg-mate": {"name": "Mate", "base": "https://stg-mate.abafusion.ai", "type": "mate"},
    "stg-perf": {"name": "Performance", "base": "https://stg-perf.abafusion.ai", "type": "perf"},
    "stg-agentic": {"name": "Agentic AI", "base": "https://stg-agentic.abafusion.ai", "type": "agentic"},
    "stg-orch": {"name": "Orchestration", "base": "https://stg-orch.abafusion.ai", "type": "orchestration"},
    "stg-forge": {"name": "Fusion Forge", "base": "https://stg-forge.abafusion.ai", "type": "forge"},
}

FUZZ = {
    "xss": ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>"],
    "sql": ["' OR '1'='1", "'; DROP TABLE users; --"],
    "path": ["../../../etc/passwd", "..%252f..%252fetc/passwd"],
    "cmd": ["; ls -la", "$(whoami)"],
    "nosql": ['{"$ne": null}'],
    "ssrf": ["http://127.0.0.1:8080/admin", "file:///etc/passwd"],
    "redirect": ["//evil.com"],
    "ssti": ["{{7*7}}"],
}

def req(method, url, body=None, token=""):
    h = {'User-Agent': 'AIAgent/1.0'}
    if token: h['Authorization'] = f'Bearer {token}'
    if isinstance(body, dict): body = json.dumps(body).encode()
    elif isinstance(body, str): body = body.encode()
    try:
        r = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(r, timeout=CONFIG['timeout']) as resp:
            return {'status': resp.status, 'body': resp.read().decode('utf-8', errors='replace')}
    except urllib.error.HTTPError as e:
        return {'status': e.code, 'body': e.read().decode('utf-8', errors='replace')}
    except:
        return {'status': 0, 'body': ''}

def classify(st, body):
    bl = (body or "").lower()
    if st >= 500: return ('server_error', 'critical')
    if st == 401: return ('auth_error', 'high')
    if st == 403: return ('forbidden', 'high')
    if '<script' in bl and 'alert' in bl: return ('xss_reflected', 'critical')
    if 'root:' in bl: return ('info_leak', 'high')
    if st == 404: return ('not_found', 'info')
    return ('other', 'low')

def discover(base):
    try:
        r = urllib.request.Request(base, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='replace')
            title = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            return {'alive': True, 'code': resp.status,
                    'title': (title.group(1).strip() if title else '')}
    except:
        return {'alive': False, 'code': 0, 'title': ''}

class M:
    def __init__(self, db):
        self.db = db
        os.makedirs(os.path.dirname(db), exist_ok=True)
        c = sqlite3.connect(db)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS p (k TEXT PRIMARY KEY, n TEXT, b TEXT, t TEXT, s TEXT, f TEXT, l TEXT, notes TEXT);
            CREATE TABLE IF NOT EXISTS f (id INTEGER PRIMARY KEY, pl TEXT, ep TEXT, cat TEXT, pay TEXT, st INTEGER, ft TEXT, sv TEXT, det TEXT, rec TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS pt (id INTEGER PRIMARY KEY, pl TEXT, pat TEXT, cat TEXT, cf REAL, tm INTEGER, f TEXT, l TEXT);
        """)
        c.commit()
        c.close()

    def sp(self, k, n, b, t, s, notes=""):
        c = sqlite3.connect(self.db)
        c.execute("INSERT OR REPLACE INTO p VALUES (?,?,?,?,?,?,?,?)",
                   (k, n, b, t, s, datetime.now().isoformat(), datetime.now().isoformat(), notes))
        c.commit()
        c.close()

    def sf(self, pl, ep, cat, pay, st, ft, sv, det, rec=""):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO f VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)",
                   (pl, ep, cat, str(pay)[:60], st, ft, sv, str(det)[:100], rec, datetime.now().isoformat()))
        c.commit()
        c.close()

    def lp(self, pl, pat, cat, conf=0.5):
        c = sqlite3.connect(self.db)
        cur = c.cursor()
        cur.execute("SELECT id, tm FROM pt WHERE pl=? AND pat=?", (pl, pat))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE pt SET tm=?, l=?, cf=? WHERE id=?",
                        (row[1]+1, datetime.now().isoformat(), min(conf+0.1, 1.0), row[0]))
        else:
            cur.execute("INSERT INTO pt VALUES (NULL,?,?,?,?,1,?,?)",
                        (pl, pat, cat, conf, datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()
        c.close()

def main():
    print("AI MULTI-PLATFORM AGENT — Ultra Fast")
    mem = M(CONFIG['memory_db'])

    # Auth
    try:
        from token_manager import TokenManager
        token = TokenManager().ensure_valid()
    except:
        token = ""

    # Discovery (all 9)
    for k, info in PLATFORMS.items():
        r = discover(info['base'])
        mem.sp(k, info['name'], info['base'], info['type'],
               'alive' if r['alive'] else 'dead')
        print(f"  {k}: {'✓' if r['alive'] else '✗'} {r['title'][:20]}")

    # API fuzz (stg-dp via mgr, 3 types x 1 payload per category)
    if token:
        mgr = PLATFORMS['stg-dp']['mgr']
        for ct in ["bigquery", "mongodb"]:
            for cat, payloads in FUZZ.items():
                for p in payloads[:1]:
                    body = {"name": f"f-{ct}", "type": ct, "source_config": {"x": p}}
                    rr = req("POST", f"{mgr}/connectors", body, token)
                    ft, sv = classify(rr['status'], rr.get('body',''))
                    if rr['status'] >= 400:
                        mem.sf('stg-dp', f"{mgr}/connectors", cat, str(p),
                               rr['status'], ft, sv, rr.get('body',''))
                        mem.lp('stg-dp', f"stg-dp:{ft}", ft)
                        print(f"  ⚠ {ft} ({rr['status']})")

    # Report
    c = sqlite3.connect(mem.db)
    ps = c.execute("SELECT * FROM p ORDER BY k").fetchall()
    fnd = c.execute("SELECT * FROM f ORDER BY id DESC LIMIT 50").fetchall()
    pat = c.execute("SELECT * FROM pt ORDER BY tm DESC").fetchall()
    c.close()

    rpt = {'ts': datetime.now().isoformat(), 'total': len(ps),
           'alive': sum(1 for p in ps if p[4]=='alive'),
           'findings': len(fnd), 'patterns': len(pat)}
    os.makedirs(CONFIG['reports_dir'], exist_ok=True)
    rp = f"{CONFIG['reports_dir']}/rpt_{datetime.now().strftime('%H%M%S')}.json"
    with open(rp, 'w') as f:
        json.dump(rpt, f, indent=2)
    print(f"\n  Platforms: {rpt['total']} ({rpt['alive']} alive)")
    print(f"  Findings: {rpt['findings']} | Patterns: {rpt['patterns']}")
    print(f"  Report: {rp}")

if __name__ == "__main__":
    main()
