# Control plane bridge — HTTP over a process boundary

How the .NET Command Center reaches the Python governance kernel, why it was built
this way, and the observed behaviour of every failure mode.

Branch `claude/docker-desktop-init-blocked-k7zwoa`. Written from a live run, not
from the design.

## The decision

The governance kernel is Python. The Command Center is ASP.NET Core 8. Before
this change they had no connection at all, and the Phase 2 archaeology left the
question open as *"expose the control plane over HTTP, or run it as a sidecar?"*

**Chosen: an HTTP surface on the control plane.** The kernel serves
`paios.http_api` and the Command Center is an ordinary HTTP client.

Why, concretely:

- **One deployable, one audit trail.** Every decision the kernel makes emits audit
  events. Embedding the kernel per-caller — a subprocess, an in-process
  interpreter — would scatter that trail across however many callers exist. A
  single long-lived process has one.
- **No cross-runtime process lifecycle in the Command Center.** A sidecar the
  .NET app spawns means .NET code owning Python startup, crash detection, restart
  and zombie reaping. An HTTP client that reports `unavailable` when nothing
  answers is a fraction of the moving parts.
- **The failure mode is already modelled.** Phase 1 established the
  adapter-returns-a-health-state pattern for local model servers. A remote
  governance kernel is the same shape of problem, so it reuses the same contract
  rather than introducing a second one.
- **The boundary is honest about trust.** Two processes talking over a
  credentialed channel makes the authorization question explicit. An in-process
  call would have made it easy to skip.

Rejected alternatives, briefly: a sidecar the Command Center supervises (lifecycle
cost above, and it still needs an IPC contract, so the protocol work is not
avoided); embedding via Python.NET (couples two runtimes' GC and threading for no
gain here); a shared database as the interface (turns a request/response into
polling, and puts governance decisions behind eventual consistency).

## Shape

```
┌──────────────────────────┐          ┌───────────────────────────────┐
│ Command Center (.NET 8)  │          │ Control plane (Python 3.12)   │
│                          │          │                               │
│ GovernanceClient ────────┼─ HTTP ──▶│ GovernanceApi.dispatch        │
│   never throws;          │  bearer  │   ├─ GET  /health             │
│   returns HealthState    │  token   │   ├─ GET  /api/governance/    │
│                          │          │   │        status, tools, audit│
│ GovernanceHealthCheck    │          │   └─ POST /api/governance/    │
│   → Operations           │          │            requests            │
│ DashboardAggregator      │          │                               │
│   → Command Center card  │          │ ControlPlane.handle()          │
└──────────────────────────┘          │   identity → classify → risk   │
                                      │   → policy → route → approve   │
                                      │   → execute → audit            │
                                      └───────────────────────────────┘
```

Transport is separated from dispatch on the Python side: `GovernanceApi.dispatch`
maps method/path/query/body to a status and a payload with no socket involved, and
`paios.serve` wraps it in `ThreadingHTTPServer`. That is why most of the Python
tests need no network.

## Standard library only

The kernel declares `dependencies = []`. The HTTP surface does not change that —
it is built on `http.server`, `json` and `hmac`.

A governance component whose supply chain is one interpreter is easier to vouch
for than one pulling a web framework and its transitive tree. The cost is a
hand-rolled router and no automatic OpenAPI; both are cheap at five endpoints.

## Two properties that must not weaken

### Identity is never read from the request body

The kernel's `authorize()` gates on `identity.authenticated`. If a caller could
set that, the gate would be decorative.

So the surface resolves identity from the presented credential, server-side, and
**ignores** any `identity` in the body rather than merging it — there is no
partial-trust path to reason about. Observed live: a submission claiming
`{"subject": "attacker", "roles": ["platform_admin"], "authenticated": true}`
was processed as subject `command-center` with role `analyst`, the roles its
token was configured with.

The .NET client has no identity parameter at all, so it cannot express the
attempt.

### No automatic approval

`deny_by_default` stays in place behind HTTP. A request that risk or policy routes
to a human is **refused**, not approved by the absence of one.

A policy deny is stronger still: it blocks at routing, before approval is sought,
so an approving handler cannot rescue it. Verified by test — with a handler that
approves everything, a denied request still blocks and no `approval_requested`
event is emitted.

### Fail-closed when unconfigured

With no `PAIOS_HTTP_TOKEN` set, the surface does not fall open. Submissions run
the pipeline and are blocked as `AUTHZ-001` **and audited**. A deployment that
forgot to set a token refuses work rather than running it ungoverned, and leaves a
record either way. `paios.serve` logs a warning at startup saying exactly this.

Binds to `127.0.0.1` by default.

## Observed behaviour — live run

One kernel and one Command Center, real sockets, nothing stubbed. The Command
Center was **not restarted** between states 2–6: propagation is structural,
because the aggregator re-reads on every request and holds no governance state.

| # | Underlying state | Dashboard card | Metrics shown | Alert | Operations records |
|---|---|---|---|---|---|
| 1 | kernel running | `healthy` / implemented | policies 7/7, tools 5/6, risk levels 5 | none | 5, all healthy |
| 2 | **kernel stopped** | `unavailable` / implemented | **none** | 1 critical | 1 (`unavailable`) |
| 3 | kernel restarted | `healthy` / implemented | policies 7/7, tools 5/6, risk levels 5 | none | 5, all healthy |
| 4 | **credential rejected** | `degraded` / implemented | **none** | 1 warning | 5 — components healthy, link `degraded` |
| 5 | **tool registry unreadable** | `degraded` / implemented | policies 7/7, risk levels 5 — **no tools row** | 1 warning | 5 — `tool_registry` `unavailable`, rest healthy |
| 6 | healthy again | `healthy` / implemented | policies 7/7, tools 5/6, risk levels 5 | none | 5, all healthy |
| — | disabled in config | `unknown` / **not_implemented** | none | **none** | 1, `checkable=false` |

What each row is there to prove:

- **2** — an unreachable kernel reports **no metrics**, not zeros. The counts in
  row 1 were measured; there is nothing to report in row 2, and saying `0` would
  invent an empty policy set.
- **2 vs disabled** — the availability/health separation. Governance is now
  *implemented*, so down is `unavailable`. Turned off deliberately is
  `not_implemented` with no alert, because a configuration choice is not an
  incident.
- **4** — `unavailable` and `degraded` are kept apart. Nothing listening versus
  answering-but-refusing-us need different responses ("start it" vs "fix the
  token"), and the state is the only hint an operator gets. The kernel's `/health`
  needs no credential, which is what makes this diagnosable at all.
- **4, Operations column** — the health check probes an authenticated route *as
  well as* `/health`, so a rejected credential shows up in Operations too. Without
  that, Operations read green while the dashboard read amber for the same fault.
- **5** — partial failure stays partial. The policy count is still shown because
  policy still loaded; the tools row disappears because that document did not.

### Request dispositions through the full bridge

Submitted to the Command Center, which forwarded to the kernel:

| Request | Disposition | Delivered | Requires human | Risk |
|---|---|---|---|---|
| `summarise the project roadmap` | `auto_execute` | yes | no | L0 |
| `export all employee salary records` | `human_review` | **no** | yes | L3 financial, privacy |
| `change the governance policy thresholds` | `blocked` | **no** | no | L3 governance, operational |

The third is denied by `GOVERNANCE_ROLE_REQUIRED` — the configured principal holds
`analyst`, not a governance role. Policy deny outranks approval, so it never
reaches an approver.

The kernel's audit trail recorded every stage with `subject=command-center`: the
credential-derived identity, not anything the body asked for.

## Secret handling

`config/governance.json` holds `tokenEnvVar` — the **name** of an environment
variable, never a token. Same rule the provider config follows for API keys, for
the same reason: a config file ends up in version control, a backup and a support
bundle.

The token is resolved from the environment at request time. It is never logged,
never returned by any endpoint, and never written to config. `/api/governance/status`
reports only `authentication: "token" | "none"`. A test asserts the configured
token appears nowhere in a status response.

Governance reads are **proxied** through the Command Center rather than called
from the browser, because the token lives in the server process and must not
reach a client.

## Endpoints

Kernel (`http://127.0.0.1:8081` by default):

| Method | Path | Credential | Notes |
|---|---|---|---|
| GET | `/health` | none | 200 healthy / 503 degraded, per-component detail |
| GET | `/api/governance/status` | bearer | counts from loaded documents; `null` tools when no registry |
| GET | `/api/governance/tools` | bearer | registry with governance fields |
| GET | `/api/governance/audit` | bearer | `correlation_id`, `limit` (clamped to 500) |
| POST | `/api/governance/requests` | bearer | returns the decision; a refusal is 200 |

Command Center: `/api/governance/health`, `/status`, `/tools`, and
`POST /api/governance/requests`. Each returns 200 with a `status` field even when
the kernel is unreachable, so the workspace renders the failure instead of
breaking.

**A refusal is 200.** "You may not do this" is a successful answer to "what does
governance say". A 4xx would conflate it with "you asked wrongly", and an operator
reading logs could not tell a governance decision from a client bug.

## Running it

```bash
# kernel
export PAIOS_HTTP_TOKEN=$(openssl rand -hex 32)
export PAIOS_HTTP_ROLES=analyst          # entitlements for that credential
PYTHONPATH=src python -m paios.serve     # 127.0.0.1:8081

# Command Center, same PAIOS_HTTP_TOKEN in its environment
cd apps/paios-command-center && dotnet run
```

Endpoint and token-variable name are configured in
`apps/paios-command-center/config/governance.json`, read per request so a change
applies without a restart.

## Tests

| Suite | Count | Covers |
|---|---|---|
| `tests/test_http_api.py` | 57 | auth matrix, spoofed identity, both approval paths, deny-is-final, malformed/oversized bodies, audit paging, 6 over a real socket |
| `GovernanceClientTests` | 20 | every transport failure as a state, credential handling, submission shapes |
| `GovernanceSubsystemTests` | 23 | availability vs health, no-fabricated-metrics, alert severities, roll-up, per-component records |

Totals after this change: **201 Python**, **121 .NET**.

## Known limits

- **One principal.** `TokenAuthenticator` resolves a single configured identity.
  Real multi-user identity is Entra ID token validation, which replaces the
  authenticator without changing its interface.
- **Audit reads are per-process.** `/api/governance/audit` answers from the
  in-memory sink, so it reports what *this* kernel process recorded. With
  `PAIOS_AUDIT_PATH` set, the JSONL sink is the record of account and the endpoint
  is a window over the current process only.
- **No approval path over HTTP.** By design for now: the surface refuses what
  needs a human rather than inventing consent. A real approval flow is a separate
  human-facing channel.
- **Agent Lab is still not implemented.** The kernel supplies the Tool Registry
  and Execution Gateway an agent layer would run under, but no agent registry is
  built on them. The dashboard says exactly that, and reports no agent metrics.
