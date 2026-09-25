# HEALTH_CHECK_SCHEMA.md — as implemented

`apps/paios-command-center/Operations/ServiceHealth.cs`

## The normalized record

Every checkable component in the system reports through one shape. It reuses
`HealthState` from the Phase 1 provider layer rather than defining a parallel
vocabulary, so Operations and Local LLMs cannot drift into disagreeing about
what "degraded" means.

```ts
type ServiceCategory = "application" | "provider" | "infrastructure";
type HealthState     = "healthy" | "degraded" | "unavailable" | "unknown";

interface ServiceHealth {
  serviceId:     string;          // stable; providers are namespaced "provider:<id>"
  displayName:   string;
  category:      ServiceCategory;
  status:        HealthState;     // defaults to "unknown", never "healthy"
  target:        string | null;   // what was probed — endpoint, host:port, or description
  latencyMs:     number | null;
  errorMessage:  string | null;   // populated for every non-healthy state
  lastCheckedAt: string;          // ISO-8601, required
  checkable:     boolean;         // false = declared but no probe implemented
}
```

`checkable: false` is the honest answer for a dependency someone declared but
that has no implemented probe. It reports `unknown` and says why, rather than
being drawn as healthy or quietly omitted.

## The snapshot

```ts
interface OperationsSnapshot {
  overall:     HealthState;
  generatedAt: string;                    // ISO-8601
  counts:      Record<HealthState, number>;
  services:    ServiceHealth[];           // ordered by category, then name
}
```

**Roll-up rule.** Worst observed state wins, with one deliberate ordering choice:
`unknown` ranks *below* `unavailable`, so a system whose state cannot be
determined never reads as merely degraded. An empty system is `unknown`, never
`healthy` — nothing to check is not the same as everything being fine.

## State mapping

| Condition | State |
|---|---|
| Probe succeeded | `healthy` |
| Reachable but erroring (non-2xx) | `degraded` |
| Connection refused, DNS failure, socket error | `unavailable` |
| Timeout | `unavailable`, message `"Request timed out."` / `"Connection timed out after Ns."` |
| Declared with an unimplemented probe kind | `unknown`, `checkable: false` |
| Invalid declaration (no host, bad port, bad URL) | `unknown`, `checkable: false` |
| Health check itself threw | `degraded`, message carries the exception |

Reachable-but-erroring is `degraded` rather than `unavailable` on purpose: the
distinction decides whether you restart a service or investigate it.

## Failure isolation

`OperationsRegistry.RunSafelyAsync` wraps every check in a deliberately broad
`catch`. An unforeseen probe bug degrades one card and is logged; it cannot blank
the workspace. This is asserted by
`A_throwing_check_becomes_a_degraded_card_and_does_not_break_the_snapshot`.

## Endpoints

```
GET /api/operations/services       -> OperationsSnapshot
GET /api/operations/services/{id}  -> ServiceHealth | 404 { error }
```

Checks run in parallel; TCP probes use a 3s timeout and HTTP probes the shared
5s provider client timeout, so a hung dependency cannot hang the request.
