# AI Agent — Memory & Learning

## Memory Schema (SQLite)

```
ai_agent/data/memory.db
```

### Table: platforms
Stores discovered platform info.

| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PRIMARY KEY | Platform key (e.g. "stg-dp") |
| name | TEXT | Human-readable name |
| base | TEXT | Base URL |
| type | TEXT | Platform type |
| status | TEXT | "alive" or "dead" |
| first | TEXT | First discovery timestamp |
| last | TEXT | Last check timestamp |
| notes | TEXT | Additional notes |

### Table: tests
Every API test performed.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| platform | TEXT | Platform key |
| endpoint | TEXT | Full endpoint URL |
| method | TEXT | HTTP method |
| category | TEXT | Fuzz category |
| payload | TEXT | Test payload |
| status | INTEGER | HTTP status code |
| is_err | INTEGER | 1 if error, 0 if OK |
| ftype | TEXT | Classified finding type |
| severity | TEXT | critical/high/medium/low/info |
| details | TEXT | Response body (truncated) |
| ts | TEXT | Test timestamp |

### Table: findings
Significant findings (errors, vulnerabilities).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| platform | TEXT | Platform key |
| endpoint | TEXT | Endpoint URL |
| category | TEXT | Fuzz category |
| payload | TEXT | Test payload |
| status | INTEGER | HTTP status |
| ftype | TEXT | Finding type |
| severity | TEXT | Severity level |
| details | TEXT | Response details |
| rec | TEXT | Recommendation |
| ts | TEXT | Discovery timestamp |

### Table: patterns
Learned patterns with confidence scoring.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment ID |
| platform | TEXT | Platform key |
| pattern | TEXT | Pattern string (e.g. "stg-dp:server_error") |
| cat | TEXT | Category |
| conf | REAL | Confidence 0.0-1.0 |
| times | INTEGER | Times observed |
| first | TEXT | First seen |
| last | TEXT | Last seen |

## Learning Logic

```
Each finding → save to findings table
Each unique (platform, error_type) → learn pattern

Pattern confidence:
  - New pattern: conf = 0.5
  - Each observation: conf = min(conf + 0.1, 1.0)
  - Cap: 1.0

times_seen increments each run where pattern observed.

Pattern states:
  - times >= 3  → "confirmed" (reproducible)
  - times >= 5  → "established" (likely real issue)
  - times >= 10 → "well-known" (certain)
```

## Finding Classification

| Status/Response | Finding Type | Severity |
|-----------------|--------------|----------|
| status >= 500 | server_error | critical |
| status == 401 | auth_error | high |
| status == 403 | forbidden | high |
| XSS payload in response body | xss_reflected | critical |
| System file content in body | info_leak | high |
| Stack trace in body | info_leak | high |
| SQL error in body | sql_error | medium |
| status == 404 | not_found | info |
| status == 0 | connection_error | medium |

## Report Format

```json
{
  "timestamp": "2026-09-24T17:30:00",
  "total": 9,
  "alive": 9,
  "findings": 42,
  "critical_high": 5,
  "patterns": 8,
  "platforms": [...],
  "findings": [...],
  "patterns": [...]
}
```
