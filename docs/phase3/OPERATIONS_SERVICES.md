# OPERATIONS_SERVICES.md — as implemented

What Operations checks, why that list is short, and how to extend it.

## Checked today

| Service | Category | Probe | Source |
|---|---|---|---|
| Command Center API | `application` | self-report with measured uptime | `ApplicationHealthCheck` |
| Each model provider | `provider` | delegates to the Phase 1 registry | `ProviderHealthCheck` |
| Each declared dependency | `infrastructure` | TCP connect or HTTP GET | `InfrastructureHealthCheck` |

Provider health is **not reimplemented here.** `ProviderHealthCheck` calls the
same `ProviderRegistry` the Local LLMs workspace uses, so the two views cannot
report different states for the same provider.

## Why the list is short

The phase plan names n8n, Postgres/SQLite, Chroma/Qdrant, Tool Registry, and
Execution Gateway. Phase 0 confirmed **none of them exist in this repository** —
no client library, no connection string, no code. Health-checking a service that
was never deployed would produce a permanently red or permanently unknown card
that teaches nothing.

So `config/services.json` ships **empty**. Operations reports only what actually
exists. Declaring a dependency is a deliberate act, which keeps the workspace
honest about the difference between "down" and "never built".

## Declaring a dependency

```json
{
  "monitored": [
    { "serviceId": "postgres", "displayName": "PostgreSQL",
      "probe": "tcp",  "host": "127.0.0.1", "port": 5432 },
    { "serviceId": "n8n", "displayName": "n8n",
      "probe": "http", "url": "http://localhost:5678/healthz" }
  ]
}
```

- `tcp` — connects to `host:port`. Proves a listener is accepting connections.
  Correct for Postgres, Redis, and anything without an HTTP health route.
- `http` — `GET url`. 2xx is healthy, non-2xx is degraded with the status
  attached.
- Any other kind — reported as `unknown` with `checkable: false`. Adding a
  service before its probe exists is safe: it appears honestly as unmonitored
  rather than silently passing.

Read per request, so edits apply without a restart. A malformed file logs an
error and monitors nothing, rather than failing startup.

## Verified behaviour

All four states produced simultaneously from real probes, no simulation:

| Service | State | Cause |
|---|---|---|
| Command Center API | `healthy` | self-report, real uptime |
| `lmstudio-local` | `healthy` | real HTTP server on :1234 |
| Stub model server | `healthy` | real HTTP 200 |
| Stub server, bad route | `degraded` | real HTTP 404 |
| `ollama-local` | `unavailable` | real connection refused on :11434 |
| PostgreSQL | `unavailable` | real TCP refusal on :5432 |
| Qdrant vector store | `unknown` | declared with an unimplemented `grpc` probe |

Roll-up returned `unavailable`, correctly dominated by the worst state.

## Not covered

- **Tool Registry / Execution Gateway** — absent from the repository. Not
  declared, because a probe would be aimed at nothing.
- **Historical telemetry** — status is point-in-time. No time series is stored;
  there is no datastore to store one in.
- **Push/streaming updates** — the workspace fetches on load. Live refresh is a
  Phase 11 concern, not a Phase 3 exit criterion.
