# COMMAND_CENTER_DATA_MAP.md — as implemented

Every figure on the Command Center, and the subsystem that owns it. The
dashboard holds **no state of its own** — `DashboardAggregator` reads the same
registries the individual workspaces read, so there is no second copy of truth
that could drift.

## Panel → source

| Card | Metric | Read from | Owning workspace |
|---|---|---|---|
| Local LLMs | `providers` — `healthy/total` | `ProviderRegistry.GetProvidersAsync` → `/api/providers` | Local LLMs (`llm`) |
| Local LLMs | `models available` | `ProviderRegistry.GetModelsAsync`, **healthy providers only** | Local LLMs (`llm`) |
| Agent Lab | *(none)* | nothing — no agent registry exists | Agent Lab (`agents`) |
| Operations | `services` — `healthy/total` | `OperationsRegistry.GetSnapshotAsync` → `/api/operations/services` | Operations (`ops`) |
| Operations | `alerts` | derived: services in `degraded` or `unavailable` | Operations (`ops`) |
| System banner | `overall` | roll-up across implemented subsystems | — |
| Alert list | each entry | a specific provider or service record | the alert names it |

Every card renders a `source:` line naming the API its numbers came from, so a
reader can trace any figure without reading code. `Agent Lab` renders
`source: none — no agent registry exists`, which is the honest answer.

## Alerts

Alerts are **derived**, never authored. One is emitted for each underlying
record in a non-healthy state, and each carries `subsystemId` and `sourceId`
identifying the exact provider or service it came from.

| Condition | Severity |
|---|---|
| Provider or service `unavailable` | `critical` |
| Provider or service `degraded` | `warning` |
| Service declared with no implemented probe | `info` |
| A subsystem could not be read at all | `critical` |

Ordered most severe first. An empty alert list means nothing is wrong, not that
alerting is unimplemented.

## The agent subsystem

Agent Lab is reported with `availability: "not_implemented"`, `status:
"unknown"`, and **zero metrics**. Three deliberate choices:

1. **`availability` is separate from `status`.** A subsystem that does not exist
   is not "unhealthy". Collapsing the two would let an unbuilt feature read as a
   broken one, or worse, let a fabricated green imply it works.
2. **No metrics at all**, rather than `0 agents`. Zero is a measurement, and
   nothing measured it.
3. **Excluded from the roll-up.** An unbuilt subsystem must not drag the system
   banner to `unknown`, and must not be counted as healthy either. If *nothing*
   is implemented the roll-up is `unknown`, never `healthy`.

Its `detail` states that Phase 2 is blocked pending resolution of the governance
implementation location.

## Drill-down

Each card carries a `workspace` id matching the nav's `data-workspace`
attribute; selecting a card opens the workspace that owns its data. Asserted by
`Every_subsystem_drills_into_a_real_workspace_id`, so a renamed workspace breaks
a test rather than a link. The not-implemented card is `aria-disabled` — it
renders but does not navigate, because there is nothing to navigate to.

## Partial failure

Subsystems are read concurrently and independently. A subsystem that throws
becomes a `degraded` card carrying the error plus a `critical` alert; the others
render normally. Asserted by `A_failing_subsystem_degrades_only_its_own_card`.
