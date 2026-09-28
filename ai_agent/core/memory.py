"""SQLite-backed agent memory: platforms, findings, learned patterns.

Schema (stable across agent variants, documented in KNOWLEDGE_MEMORY.md):

    platforms(key PK, name, base, type, status, first_seen, last_seen, notes)
    findings(id PK, platform, endpoint, category, payload, status,
             finding_type, severity, details, recommendation, ts)
    patterns(id PK, platform, pattern, category, confidence, times_seen,
             first_seen, last_seen)
"""
import os
import sqlite3
from datetime import datetime

from .config import CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS platforms (
    key TEXT PRIMARY KEY, name TEXT, base TEXT, type TEXT,
    status TEXT, first_seen TEXT, last_seen TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY, platform TEXT, endpoint TEXT, category TEXT,
    payload TEXT, status INTEGER, finding_type TEXT, severity TEXT,
    details TEXT, recommendation TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY, platform TEXT, pattern TEXT, category TEXT,
    confidence REAL, times_seen INTEGER, first_seen TEXT, last_seen TEXT);
"""


class Memory:
    def __init__(self, db=None):
        self.db = db or CONFIG['memory_db']
        parent = os.path.dirname(self.db)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with sqlite3.connect(self.db) as c:
            c.executescript(SCHEMA)

    # -- write ---------------------------------------------------------
    def save_platform(self, key, name, base, ptype, status, notes=''):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute(
                'INSERT OR REPLACE INTO platforms VALUES (?,?,?,?,?,?,?,?)',
                (key, name, base, ptype, status, now, now, notes))

    def save_finding(self, platform, endpoint, category, payload, status,
                     finding_type, severity, details='', recommendation=''):
        with sqlite3.connect(self.db) as c:
            cur = c.execute(
                'INSERT INTO findings VALUES (NULL,?,?,?,?,?,?,?,?,?,?)',
                (platform, endpoint, category, str(payload)[:80], status,
                 finding_type, severity, str(details)[:150],
                 recommendation, datetime.now().isoformat()))
            return cur.lastrowid

    def learn_pattern(self, platform, pattern, category, conf=0.5):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db) as c:
            cur = c.cursor()
            cur.execute('SELECT id, times_seen FROM patterns '
                        'WHERE platform=? AND pattern=?',
                        (platform, pattern))
            row = cur.fetchone()
            if row:
                cur.execute(
                    'UPDATE patterns SET times_seen=?, last_seen=?, '
                    'confidence=? WHERE id=?',
                    (row[1] + 1, now, min(conf + 0.1, 1.0), row[0]))
            else:
                cur.execute(
                    'INSERT INTO patterns VALUES (NULL,?,?,?,?,1,?,?)',
                    (platform, pattern, category, conf, now, now))

    # -- read ----------------------------------------------------------
    def platforms(self):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                'SELECT key, name, base, type, status FROM platforms '
                'ORDER BY key').fetchall()

    def findings(self, limit=100):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                'SELECT platform, endpoint, category, payload, status, '
                'finding_type, severity FROM findings '
                'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()

    def finding_by_id(self, fid):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                'SELECT platform, endpoint, category, payload, status, '
                'finding_type, severity, details, recommendation, ts '
                'FROM findings WHERE id=?', (fid,)).fetchone()

    def patterns(self):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                'SELECT platform, pattern, category, confidence, '
                'times_seen FROM patterns ORDER BY times_seen DESC').fetchall()

    def stats(self):
        plats = self.platforms()
        with sqlite3.connect(self.db) as c:
            n_findings = c.execute(
                'SELECT COUNT(*) FROM findings').fetchone()[0]
            n_critical_high = c.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE severity IN ('critical','high')").fetchone()[0]
        return {
            'total': len(plats),
            'alive': sum(1 for p in plats if p[4] == 'alive'),
            'findings': n_findings,
            'critical_high': n_critical_high,
            'patterns': len(self.patterns()),
        }
