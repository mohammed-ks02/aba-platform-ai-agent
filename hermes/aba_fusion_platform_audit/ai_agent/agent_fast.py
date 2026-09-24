#!/usr/bin/env python3
"""
ABA FUSION MULTI-PLATFORM AI AGENT — Fast Mode
Tests all 9 platforms, quick discovery + targeted API fuzz
"""

import json, os, sqlite3, urllib.request, urllib.error, re
from datetime import datetime

CONFIG = {"data_dir": "ai_agent/data", "memory_db": "ai_agent/data/memory.db",
          "reports_dir": "ai_agent/data/reports", "timeout": 10}

PLATFORMS = {
    "stg-dp": {"name": "Data Platform", "base": "https://stg-dp.abafusion.ai",
               "api": "https://stg-dp.abafusion.ai/api/v1",
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
    "nosql": ['{"$ne": null}', '{"$gt": ""}'],
    "ssrf": ["http://127.0.0.1:8080/admin", "file:///etc/passwd"],
    "redirect": ["//evil.com", "http://evil.com"],
    "ssti": ["{{7*7}}", "{{config}}"],
    "xxe": ["<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"],
    "empty": ["", None, "null"],
    "boundary": ["", "0", "1"],
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
    except Exception as e:
        return {'status': 0, 'body': str(e)[:150]}

def classify(st, body):
    bl = (body or "").lower()
    if st >= 500: return ('server_error', 'critical')
    if st == 401: return ('auth_error', 'high')
    if st == 403: return ('forbidden', 'high')
    if '<script' in bl and 'alert' in bl: return ('xss_reflected', 'critical')
    if 'root:' in bl: return ('info_leak', 'high')
    if 'stack' in bl and 'trace' in bl: return ('info_leak', 'high')
    if 'sql' in bl and 'error' in bl: return ('sql_error', 'medium')
    if st == 404: return ('not_found', 'info')
    return ('other', 'low')

def discover(base):
    try:
        r = urllib.request.Request(base, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='replace')
            title = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            return {'alive': True, 'code': resp.status, 'title': title.group(1).strip() if title else '',
                    'ct': resp.headers.get('Content-Type', '')}
    except:
        return {'alive': False, 'code': 0, 'title': '', 'ct': ''}

class Mem:
    def __init__(self, db):
        self.db = db
        os.makedirs(os.path.dirname(db), exist_ok=True)
        c = sqlite3.connect(db)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS platforms (key TEXT PRIMARY KEY, name TEXT, base TEXT, type TEXT, status TEXT, first TEXT, last TEXT);
            CREATE TABLE IF NOT EXISTS findings (id INTEGER PRIMARY KEY, platform TEXT, endpoint TEXT, category TEXT, payload TEXT, status INTEGER, ftype TEXT, severity TEXT, details TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS patterns (id INTEGER PRIMARY KEY, platform TEXT, pattern TEXT, cat TEXT, conf REAL, times INTEGER, first TEXT, last TEXT);
        """)
        c.commit()
        c.close()

    def save_plat(self, k, n, b, t, s, notes=""):
        c = sqlite3.connect(self.db)
        c.execute("INSERT OR REPLACE INTO platforms VALUES (?,?,?,?,?,?,?,?)",
                   (k, n, b, t, s, datetime.now().isoformat(), datetime.now().isoformat(), notes))
        c.commit()
        c.close()

    def save(self, pl, ep, cat, payload, st, ftype, sev, det, rec=""):
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO findings VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                   (pl, ep, cat, str(payload)[:80], st, ftype, sev, str(det)[:150], rec, datetime.now().isoformat()))
        c.commit()
        c.close()

    def learn(self, pl, pat, cat, conf=0.5):
        c = sqlite3.connect(self.db)
        cur = c.cursor()
        cur.execute("SELECT id, times FROM patterns WHERE platform=? AND pattern=?", (pl, pat))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE patterns SET times=?, last=?, conf=? WHERE id=?",
                        (row[1]+1, datetime.now().isoformat(), min(conf+0.1, 1.0), row[0]))
        else:
            cur.execute("INSERT INTO patterns VALUES (NULL,?,?,?,?,1,?,?)",
                        (pl, pat, cat, conf, datetime.now().isoformat(), datetime.now().isoformat()))
        c.commit()
        c.close()

    def stats(self):
        c = sqlite3.connect(self.db)
        ps = c.execute("SELECT * FROM platforms ORDER BY key").fetchall()
        fnd = c.execute("SELECT * FROM findings ORDER BY id DESC LIMIT 100").fetchall()
        pat = c.execute("SELECT * FROM patterns ORDER BY times DESC").fetchall()
        c.close()
        return {'platforms': [dict(zip(['k','n','b','t','s','f','l','notes'], p)) for p in ps],
                'findings': [dict(zip(['id','pl','ep','cat','pay','st','ft','sv','det','rec','ts'], f)) for f in fnd],
                'patterns': [dict(zip(['id','pl','pat','cat','cf','tm','f','l'], pt)) for pt in pat]}

def main():
    print("=" * 50)
    print("AI MULTI-PLATFORM AGENT — Fast Mode")
    print("=" * 50)

    mem = Mem(CONFIG['memory_db'])

    # Auth
    print("\n[AUTH]")
    try:
        from token_manager import TokenManager
        token = TokenManager().ensure_valid()
        print(f"  Token: OK")
    except Exception as e:
        token = ""
        print(f"  Token: FAIL")

    # Discovery
    print("\n[DISCOVERY]")
    for k, info in PLATFORMS.items():
        r = discover(info['base'])
        s = 'alive' if r['alive'] else 'dead'
        mem.save_plat(k, info['name'], info['base'], info['type'], s, "")
        print(f"  {k}: {s} | {r['title'][:30]} | {r['ct'][:20]}")

    # API Fuzz (stg-dp via mgr)
    print("\n[API FUZZ]")
    if token:
        mgr = PLATFORMS['stg-dp']['mgr']
        for ct in ["bigquery", "mongodb", "slack"]:
            print(f"  ▶ {ct}")
            for cat, payloads in FUZZ.items():
                for p in payloads[:1]:
                    body = {"name": f"fuzz-{ct}", "type": ct, "source_config": {"f": p}}
                    rr = req("POST", f"{mgr}/connectors", body, token)
                    ftype, sev = classify(rr['status'], rr.get('body',''))
                    is_err = rr['status'] >= 400
                    if is_err:
                        mem.save('stg-dp', f"{mgr}/connectors", cat, str(p), rr['status'], ftype, sev, rr.get('body',''), "")
                        mem.learn('stg-dp', f"stg-dp:{ftype}", ftype)
                        print(f"    ⚠ {ftype} ({rr['status']}) [{sev}]")

    # Report
    print("\n[REPORT]")
    s = mem.stats()
    rpt = {'ts': datetime.now().isoformat(), 'total': len(s['platforms']),
           'alive': sum(1 for p in s['platforms'] if p['s']=='alive'),
           'findings': len(s['findings']),
           'patterns': len(s['patterns']), **s}
    os.makedirs(CONFIG['reports_dir'], exist_ok=True)
    rp = f"{CONFIG['reports_dir']}/report_{datetime.now().strftime('%H%M%S')}.json"
    with open(rp, 'w') as f:
        json.dump(rpt, f, indent=2, default=str)
    print(f"\n  Platforms: {rpt['total']} ({rpt['alive']} alive)")
    print(f"  Findings: {rpt['findings']}")
    print(f"  Patterns: {rpt['patterns']}")
    print(f"  Report: {rp}")
    print("=" * 50)

if __name__ == "__main__":
    main()
