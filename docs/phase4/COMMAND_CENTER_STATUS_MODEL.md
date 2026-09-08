# COMMAND_CENTER_STATUS_MODEL.md — as implemented

## Two independent axes

The dashboard tracks **availability** (does this subsystem exist?) separately
from **health** (is it working?). Conflating them is how dashboards end up
showing green for things that were never built.

```ts
type SubsystemAvailability = "implemented" | "not_implemented";
type HealthState = "healthy" | "degraded" | "unavailable" | "unknown";
```

| Availability | Health | Meaning |
|---|---|---|
| `implemented` | `healthy` | built, checked, working |
| `implemented` | `degraded` | built, reachable, misbehaving |
| `implemented` | `unavailable` | built, not reachable |
| `implemented` | `unknown` | built, state could not be determined |
| `not_implemented` | `unknown` | **not built** — the only legal pairing |

`not_implemented` never pairs with `healthy`. A subsystem that does not exist
cannot be working.

## Roll-up

Worst state wins across **implemented** subsystems only:

```
unavailable > degraded > unknown > healthy
```

- Not-implemented subsystems are **excluded** — an unbuilt feature neither drags
  the banner down nor counts as healthy.
- If no subsystem is implemented, the result is `unknown`, never `healthy`.
- Same ordering as the Phase 3 operations roll-up, deliberately: one vocabulary
  across the system.

## The five UI states

| State | Trigger | Rendered as |
|---|---|---|
| **Loading** | before the first response | "Loading subsystems…" |
| **Empty** | zero subsystems, or zero alerts | explicit empty text, never a blank region |
| **Unknown** | subsystem state indeterminate | grey `unknown` chip + reason |
| **Error** | `/api/dashboard` unreachable or non-2xx | red error block naming the failure |
| **Partial** | one subsystem unreadable | that card degraded, others render normally |

The error state is a rendered failure, not a swallowed one — a dashboard that
cannot reach its API says so rather than showing stale or blank cards.

## Why propagation is structural

The phase requires that changing an underlying service changes the dashboard
without editing dashboard data. This holds **by construction**: the aggregator
stores nothing. Each request re-reads `ProviderRegistry` and
`OperationsRegistry`, the same objects the Local LLMs and Operations workspaces
use. There is no cache to invalidate and no dashboard-side copy to update.

`Dashboard_holds_no_state_between_reads` asserts this, and it was demonstrated
live:

| Underlying change | Local LLMs | Command Center | Alerts |
|---|---|---|---|
| stub server running | `lmstudio-local: healthy` | `providers 1/2`, `models 2` | 2 |
| **stub server stopped** | `lmstudio-local: unavailable` | `providers 0/2`, `models 0` | 4 |
| **stub server restarted** | `lmstudio-local: healthy` | `providers 1/2`, `models 2` | 2 |

No dashboard code, config, or data was touched between those reads — only the
real service was stopped and started.

## Traceability

Every card names its `source`. Every alert names `subsystemId` and `sourceId`.
Any figure on the dashboard can be traced to the record that produced it, which
is what makes "no fabricated metrics" checkable rather than merely asserted.
