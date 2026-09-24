#!/usr/bin/env python3
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

FUZZ = {"xss": ["<script>alert(1)</script>","<img src=x onerror=alert(1)>"],
        "sql": ["' OR '1'='1","'; DROP TABLE users; --"],
        "path": ["../../../etc/passwd","..%252f..%252fetc/passwd"],
        "cmd": ["; ls -la","$(whoami)"],
        "nosql": ['{"$ne": null}'],
        "ssrf": ["http://127.0.0.1:8080/admin","file:///etc/passwd"],
        "redirect": ["//evil.com"],
        "ssti": ["{{7*7}}"]}

def req(method, url, body=None, token=""):
    h = {'User-Agent':'AIAgent/1.0'}
    if token: h['Authorization'] = f'Bearer {token}'
    if isinstance(body, dict): body = json.dumps(body).encode()
    elif isinstance(body, str): body = body.encode()
    try:
        r = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(r, timeout=CONFIG['timeout']) as resp:
            return {'status': resp.status, 'body': resp.read().decode('utf-8','replace')}
    except urllib.error.HTTPError as e:
        return {'status': e.code, 'body': e.read().decode('utf-8','replace')}
    except: return {'status': 0, 'body': ''}

def classify(st, body):
    bl = (body or "").lower()
    if st>=500: return ('server_error','critical')
    if st==401: return ('auth_error','high')
    if st==403: return ('forbidden','high')
    if '<script' in bl and 'alert' in bl: return ('xss_reflected','critical')
    if 'root:' in bl: return ('info_leak','high')
    if st==404: return ('not_found','info')
    return ('other','low')

def disc(base):
    try:
        r = urllib.request.Request(base, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(r, timeout=5) as resp:
            html = resp.read().decode('utf-8','replace')
            t = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            return {'alive':True,'code':resp.status,'title':(t.group(1).strip() if t else '')}
    except: return {'alive':False,'code':0,'title':''}

# Fresh DB
db = CONFIG['memory_db']
if os.path.exists(db): os.remove(db)
os.makedirs(os.path.dirname(db), exist_ok=True)
c = sqlite3.connect(db)
c.executescript("""
CREATE TABLE p(k TEXT PRIMARY KEY,n TEXT,b TEXT,t TEXT,s TEXT);
CREATE TABLE f(id INTEGER PRIMARY KEY,pl TEXT,ep TEXT,cat TEXT,pay TEXT,st INTEGER,ft TEXT,sv TEXT,det TEXT,ts TEXT);
CREATE TABLE pt(id INTEGER PRIMARY KEY,pl TEXT,pat TEXT,cat TEXT,cf REAL,tm INTEGER,f TEXT,l TEXT);
""")
c.close()

print("AI MULTI-PLATFORM AGENT — v3")
mem=db

# Auth
try:
    from token_manager import TokenManager
    token=TokenManager().ensure_valid()
    print(f"Token: OK")
except Exception as e:
    token=""
    print(f"Token: FAIL")

# Discovery
for k,info in PLATFORMS.items():
    r=disc(info['base'])
    st='alive' if r['alive'] else 'dead'
    c=sqlite3.connect(mem)
    c.execute("INSERT INTO p VALUES (?,?,?,?,?)",(k,info['name'],info['base'],info['type'],st))
    c.commit(); c.close()
    print(f"  {k}: {'✓' if r['alive'] else '✗'} {r['title'][:25]}")

# API fuzz
if token:
    mgr=PLATFORMS['stg-dp']['mgr']
    for ct in ["bigquery","mongodb"]:
        for cat,payloads in FUZZ.items():
            for p in payloads[:1]:
                body={"name":f"f-{ct}","type":ct,"source_config":{"x":p}}
                rr=req("POST",f"{mgr}/connectors",body,token)
                ft,sv=classify(rr['status'],rr.get('body',''))
                if rr['status']>=400:
                    c=sqlite3.connect(mem)
                    c.execute("INSERT INTO f VALUES (NULL,?,?,?,?,?,?,?,?,?)",
                               ('stg-dp',f"{mgr}/connectors",cat,str(p),rr['status'],ft,sv,rr.get('body',''),datetime.now().isoformat()))
                    c.commit(); c.close()
                    c=sqlite3.connect(mem); cur=c.cursor()
                    cur.execute("SELECT id,tm FROM pt WHERE pl=? AND pat=?",('stg-dp',f"stg-dp:{ft}"))
                    row=cur.fetchone()
                    if row: cur.execute("UPDATE pt SET tm=?,l=?,cf=? WHERE id=?",(row[1]+1,datetime.now().isoformat(),min(0.6,1.0),row[0]))
                    else: cur.execute("INSERT INTO pt VALUES (NULL,?,?,?,?,1,?,?)",('stg-dp',f"stg-dp:{ft}",ft,0.5,datetime.now().isoformat(),datetime.now().isoformat()))
                    c.commit(); c.close()
                    print(f"  ⚠ {ft} ({rr['status']})")

# Report
c=sqlite3.connect(mem)
ps=c.execute("SELECT COUNT(*),SUM(CASE WHEN s='alive' THEN 1 ELSE 0 END) FROM p").fetchone()
fnd=c.execute("SELECT COUNT(*),COUNT(CASE WHEN sv IN ('critical','high') THEN 1 END) FROM f").fetchone()
pat=c.execute("SELECT COUNT(*) FROM pt").fetchone()
c.close()
rpt={'ts':datetime.now().isoformat(),'platforms':ps[0],'alive':ps[1],
     'findings':fnd[0],'critical_high':fnd[1],'patterns':pat[0]}
os.makedirs(CONFIG['reports_dir'],exist_ok=True)
rp=f"{CONFIG['reports_dir']}/rpt_{datetime.now().strftime('%H%M%S')}.json"
with open(rp,'w') as f: json.dump(rpt,f,indent=2)
print(f"\nPlatforms: {rpt['platforms']} ({rpt['alive']} alive)")
print(f"Findings: {rpt['findings']} ({rpt['critical_high']} critical/high)")
print(f"Patterns: {rpt['patterns']}")
print(f"Report: {rp}")
