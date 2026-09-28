# AI Agent — Platform Knowledge

## Platforms (9 total)

| Key | Name | URL | Type | API |
|-----|------|-----|------|-----|
| stg-dp | Data Platform | https://stg-dp.abafusion.ai | data_platform | Yes (OpenAPI, 154 paths) |
| stg-analytics | Analytics | https://stg-analytics.abafusion.ai | analytics | No |
| stg-pulse | Pulse | https://stg-pulse.abafusion.ai | pulse | No |
| stg-orbit | Orbit | https://stg-orbit.abafusion.ai | orbit | No |
| stg-mate | Mate | https://stg-mate.abafusion.ai | mate | No |
| stg-perf | Performance | https://stg-perf.abafusion.ai | perf | No |
| stg-agentic | Agentic AI | https://stg-agentic.abafusion.ai | agentic | No |
| stg-orch | Orchestration | https://stg-orch.abafusion.ai | orchestration | No |
| stg-forge | Fusion Forge | https://stg-forge.abafusion.ai | forge | No |

## stg-dp (Data Platform) — Detailed

**API Base**: `https://stg-dp.abafusion.ai/api/v1`
**Manager API**: `https://stg-dp-mgr.abafusion.ai`
**Login**: `https://stg-login.abafusion.ai/api/v1/auth/login`

### Auth
- TokenManager: `token_manager.py`
- Credentials: username=`test_02`, tenant=`arma`
- Token TTL: ~15 min
- 153 connector types registered (11 createable)

### OpenAPI Spec
- GET `/api/v1/docs` → Swagger UI
- Embedded in `/api/v1/docs/swagger-ui-init.js`
- 154 paths documented

### Known Working Endpoints
- POST `/connectors` — create connector
- GET `/connector-types` — list types
- GET `/connectors` — list connectors
- POST `/pipelines` — create pipeline
- GET `/pipelines` — list pipelines
- POST `/history` — ingest (returns 400 but persists)
- GET `/history/latest/{uuid}` — verify ingestion
- GET `/history/keys` — list telemetry keys
- GET `/history` — get history data

### Known Issues
- POST /history returns 400 "Something went wrong!" but data persists
- Cloudflare R2 connector (ID: `7d4cbc3c-c351-4ee4-930c-bab4e0909d63`) in error status — SSL handshake failure on schema discovery
- Pipeline `baaf6fb3-18e5-4d0e-909d-62caf14cc671` stuck at "ready" — orchestrator 502

### Connector Types (11 createable)
bigquery, cloudflare_r2, github, google_drive, jira, mongodb, mqtt, rabbitmq, slack, twitter_x

## Other Platforms — Brief

- **stg-analytics**: Analytics dashboard platform. Previously at 16.171.135.9:8080. Returns HTML. No documented API.
- **stg-pulse**: Pulse/project platform. `/project` returns HTML. No documented API.
- **stg-orbit**: Orbit platform. Returns HTML. No documented API.
- **stg-mate**: Mate platform. `/mate` returns HTML. No documented API.
- **stg-perf**: Performance monitoring. Returns HTML. No documented API.
- **stg-agentic**: Agentic AI platform. Returns HTML. No documented API.
- **stg-orch**: Orchestration platform. `/automation/` returns HTML. Previously returned 404. Orchestrator service at `orchestrator-shared-infra-stg-api-server.shared-infra-stg.svc.cluster.local:8080`.
- **stg-forge**: Fusion Forge AI (AGPL-3.0 license). `/fusionforge/` returns HTML. No documented API.
