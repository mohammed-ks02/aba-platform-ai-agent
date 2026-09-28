"""Unit tests for core.memory.Memory (SQLite persistence)."""
from core.memory import Memory


def test_creates_schema(memory):
    tables = {r[0] for r in __import__('sqlite3').connect(memory.db)
              .execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'platforms', 'findings', 'patterns'} <= tables


def test_save_and_list_platforms(memory):
    memory.save_platform('stg-dp', 'Data Platform',
                         'https://stg-dp.abafusion.ai', 'data_platform',
                         'alive')
    memory.save_platform('stg-mate', 'Mate', 'https://stg-mate.abafusion.ai',
                         'mate', 'dead')
    plats = dict((p[0], p[4]) for p in memory.platforms())
    assert plats == {'stg-dp': 'alive', 'stg-mate': 'dead'}


def test_platform_upsert_no_duplicates(memory):
    for status in ('alive', 'dead', 'alive'):
        memory.save_platform('stg-dp', 'DP', 'https://x', 't', status)
    rows = memory.platforms()
    assert len(rows) == 1 and rows[0][4] == 'alive'


def test_save_finding(memory):
    memory.save_finding('stg-dp', 'https://mgr/connectors', 'xss',
                        '<script>alert(1)</script>', 500,
                        'server_error', 'critical', 'boom')
    fnd = memory.findings()
    assert len(fnd) == 1
    plat, ep, cat, payload, status, ftype, sev = fnd[0]
    assert (plat, cat, status, ftype, sev) == \
        ('stg-dp', 'xss', 500, 'server_error', 'critical')


def test_payload_truncation(memory):
    memory.save_finding('p', 'e', 'c', 'x' * 500, 400, 'other', 'low')
    assert len(memory.findings()[0][3]) <= 80


def test_learn_pattern_increments_confidence(memory):
    memory.learn_pattern('stg-dp', 'stg-dp:server_error', 'server_error')
    first = memory.patterns()[0]
    assert first[4] == 1                      # times_seen
    assert abs(first[3] - 0.5) < 1e-9         # confidence
    memory.learn_pattern('stg-dp', 'stg-dp:server_error', 'server_error')
    second = memory.patterns()[0]
    assert second[4] == 2
    assert abs(second[3] - 0.6) < 1e-9
    # capped at 1.0
    for _ in range(10):
        memory.learn_pattern('stg-dp', 'stg-dp:server_error', 'server_error')
    assert memory.patterns()[0][3] <= 1.0
    assert len(memory.patterns()) == 1


def test_stats(memory):
    memory.save_platform('a', 'A', 'https://a', 't', 'alive')
    memory.save_platform('b', 'B', 'https://b', 't', 'dead')
    memory.save_finding('a', '/x', 'sql', "p", 500, 'server_error', 'critical')
    memory.save_finding('a', '/x', 'sql', "p", 400, 'other', 'low')
    s = memory.stats()
    assert s == {'total': 2, 'alive': 1, 'findings': 2,
                 'critical_high': 1, 'patterns': 0}


def test_reopen_existing_db(tmp_db):
    m1 = Memory(db=tmp_db)
    m1.save_platform('a', 'A', 'https://a', 't', 'alive')
    m2 = Memory(db=tmp_db)  # must not fail on existing schema
    assert m2.platforms()[0][0] == 'a'
