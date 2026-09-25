# PAIOS_OPERATIONAL_CORE_STATUS.md

Status of the operational core (Phases 0–4) as of 2026-09-26, branch
`claude/docker-desktop-init-blocked-k7zwoa`.

> **Milestone A is NOT complete.** The governance kernel is now merged and
> bridged, but Phase 2 needs Agent Lab, which does not exist.

## Phase status

| Phase | Status | Evidence |
|---|---|---|
| **Phase 0** — Discovery and baseline | **COMPLETE** | `docs/phase0/` — 25/25 criteria, each with the command that established it |
| **Phase 1** — Local model infrastructure | **COMPLETE** | `docs/phase1/` — 19/19 criteria; both proof branches demonstrated |
| **Phase 2** — Agent Lab and execution | **INCOMPLETE — governance merged and bridged; no agent layer** | see below |
| **Phase 3** — Operations and telemetry | **COMPLETE** | `docs/phase3/` — 19/19 criteria; four states from real probes |
| **Phase 4** — Command Center aggregation | **COMPLETE WITH NON-BLOCKING DEBT** | `docs/phase4/` — 18/19; debt is the absent agent source |
| **Milestone A** | **GATED BY PHASE 2** | — |

### Phase 2 — status change

Two of the three blockers are gone.

1. **Location — resolved.** The archaeology pass
   (`docs/phase2/GOVERNANCE_ARCHAEOLOGY.md`) located the kernel: PR #2, branch
   `claude/control-plane-implementation`.
2. **Merge — done.** That branch is merged into this one. The merge was
   textually clean and both suites pass in the merged tree.
3. **Bridge — built.** The kernel now serves HTTP and the Command Center reads
   it. See `docs/phase2/CONTROL_PLANE_BRIDGE.md` for the decision, the six
   observed states, and the limits.

Phase 2 is **INCOMPLETE** rather than blocked: nothing is unresolved, but its
subject — an agent lab with governed execution — has no agent layer yet. The
kernel supplies the Tool Registry and Execution Gateway such a layer would run
under; no agent registry is built on them.

The merge-order warning recorded during the archaeology was **wrong** and is
withdrawn. PR #2 never modified `Program.cs` or `wwwroot/index.html` relative to
the merge base `07457fc` — it carried the pre-Phase-1 copies it branched from.
Only this side changed those files, so git keeps this side's version in either
merge order. Verified after merging: provider, operations and dashboard endpoints
all still served; `/api/workspaces` unchanged except for the deliberate addition
of `governance`.

## Verified baseline

| Check | Result | When |
|---|---|---|
| `dotnet build Paios.sln` Debug + Release | 0 warnings, 0 errors | this session |
| `dotnet test Paios.sln` | **78 passed, 0 failed** | this session |
| CI "Build and test" @ `1c0239e` | **green** | 2026-09-07 19:10Z |
| `tier1-remediation/scripts/validate_workflow.py` | 14/14 | this session |
| PR #2 `pytest` (governance kernel) | **144 passed** | this session |
| PR #4 mergeability | clean, no review threads | 2026-09-07 |

The repository had **zero tests** at Phase 0. It now has **121 .NET** and
**201 Python** in one tree.

## Actual operational capabilities

What the system genuinely does today, verified by running it:

**Local model providers** — auto-detects Ollama; registers OpenAI-compatible
endpoints (LM Studio, LocalAI, vLLM) at runtime via `POST /api/providers`,
persisted to `config/providers.json`. Enumerates real models. Reports
`healthy` / `degraded` / `unavailable` / `unknown` from real probes under a 5s
timeout. Only environment-variable *names* are stored, never keys.

**Operations health** — normalized `ServiceHealth` across three check types:
the application (measured uptime), every provider (delegated to the provider
registry, so there is one implementation), and declared infrastructure over TCP
or HTTP. A declared dependency with no implemented probe reports `unknown` with
`checkable: false` rather than passing silently.

**Command Center aggregation** — live roll-up holding no state of its own.
Verified: stopping a real provider moved `models available` 2 → 0 and alerts
2 → 4; restarting returned both, with no dashboard edits.

**Governance control plane** — the Python kernel serves HTTP; the Command
Center reads it over a bearer-token channel and renders it as its own subsystem
in both the dashboard and Operations. Requests can be submitted for a real
decision: verified `auto_execute`, `human_review` and `blocked` end to end, with
the kernel's audit trail recording the credential-derived identity rather than
anything the request body claimed. Identity is never taken from the body and
nothing requiring a human is auto-approved.

**Not operational**: agent registry, agent execution, persistence of any kind,
authentication of the Command Center's own callers.

## Architecture decisions (locked)

1. **Availability is separate from health.** `not_implemented` is not
   `unhealthy`. `not_implemented` never pairs with `healthy`; a not-implemented
   subsystem is excluded from roll-ups so it neither drags the banner down nor
   counts as healthy. **This model is permanent and must be preserved when Agent
   Lab comes online** — the agent card starts reporting real health the moment a
   source exists, and until then reports nothing rather than zeros.
2. **An unmeasured subsystem contributes no metrics.** Not `0 agents` — zero is
   a measurement, and nothing measured it.
3. **One health vocabulary.** `HealthState` is shared by providers, operations
   and the dashboard, so the views cannot drift.
4. **Failures are states, not exceptions.** Adapters and probes never throw for
   network or protocol failure.
5. **Failure isolation at every layer.** One broken probe degrades one card.
6. **Traceability.** Every dashboard figure names its source; every alert names
   the record it came from.
7. **Config over code.** Providers and monitored services are declared in
   `config/*.json`, read per request, malformed files degrade to defaults rather
   than failing startup.

## Known debt

| Item | Impact | Resolves when |
|---|---|---|
| Agent Lab has no data source | Phase 4 debt; dashboard aggregates 3 of 4 subsystems | an agent registry is built on the merged Tool Registry / Execution Gateway |
| No persistence | Providers survive restart via config; health and audit do not | a datastore is chosen |
| Command Center endpoints unauthenticated | anyone reaching :8080 can submit a governance request as its principal | an identity layer on the .NET side |
| Audit reads are per-process | `/api/governance/audit` reports only what the current kernel process recorded | a durable queryable sink |
| Audit not hash-chained | `audit.py` is append-only but not tamper-evident | small additive build |
| One governance principal | `TokenAuthenticator` resolves a single configured identity | Entra ID token validation replaces the authenticator |
| No approval path over HTTP | anything needing a human is refused, never approved | a human-facing approval channel |
| Docker-healthy path untested | no daemon in the authoring environment | verified on a machine with Docker |

## Cross-language bridge — decided and built

**Decision: an HTTP surface on the control plane.** The kernel serves
`paios.http_api` on loopback and the Command Center is an ordinary HTTP client.
A sidecar the .NET app supervises was rejected: it would put Python process
lifecycle in .NET code and still need an IPC contract, so the protocol work is
not avoided.

Rationale, the six observed failure states, the security boundary and the known
limits are in `docs/phase2/CONTROL_PLANE_BRIDGE.md`. The kernel keeps
`dependencies = []` — the surface is standard library only.

## Milestone A — remaining criteria

| Criterion | Status |
|---|---|
| Phase 0 COMPLETE | ✅ |
| Phase 1 COMPLETE | ✅ |
| Phase 2 COMPLETE, or execution dependency resolved to agreed minimum | ❌ **blocking** — governance merged and bridged, but no agent layer |
| Phase 3 COMPLETE | ✅ |
| Phase 4 COMPLETE | ⚠️ complete with non-blocking debt |
| Application starts successfully | ✅ |
| Build succeeds | ✅ |
| Required tests pass | ✅ 121 .NET + 201 Python |
| No known critical regression | ✅ |
| Workspaces browser-accessible | ✅ five, including Governance |
| Real system state visible | ✅ |
| Governance not bypassed | ✅ substantively — identity is never read from a request body, and nothing requiring a human is auto-approved |
| Remaining mock data removed or labelled | ✅ all fabricated values removed |
| Root documentation reflects implementation | ✅ |
| `PAIOS_OPERATIONAL_CORE_STATUS.md` exists | ✅ this document |
| Precise continuation checkpoint exists | ✅ below |

**One criterion stands between here and Milestone A**: Phase 2, and the Phase 4
debt that resolves with it. Both now reduce to the same missing piece — an agent
layer.

## Continuation checkpoint

The kernel is merged, bridged and verified. The next repository-level action is
**an agent registry built on the merged Tool Registry and Execution Gateway**,
which closes Phase 2 and the Phase 4 debt together: the dashboard's Agent Lab
card starts reporting measured health the moment a source exists.

Beyond that, build only what the archaeology confirmed genuinely absent:
KillSwitch, AgentCredential, PurposeBind, and SHA-256 audit chaining.

Architecture decision 1 stays binding through that work: when Agent Lab comes
online its card moves from `not_implemented` to `implemented`, and an unreachable
agent layer must then read `unavailable` — exactly as Governance does now — never
`not_implemented`, and never with fabricated zeros.
