# PAIOS_OPERATIONAL_CORE_STATUS.md

Status of the operational core (Phases 0–4) as of 2026-09-07, branch
`claude/docker-desktop-init-blocked-k7zwoa` @ `1c0239e`.

> **Milestone A is NOT complete.** It is gated by Phase 2.
> Update this document when Phase 2 resolves.

## Phase status

| Phase | Status | Evidence |
|---|---|---|
| **Phase 0** — Discovery and baseline | **COMPLETE** | `docs/phase0/` — 25/25 criteria, each with the command that established it |
| **Phase 1** — Local model infrastructure | **COMPLETE** | `docs/phase1/` — 19/19 criteria; both proof branches demonstrated |
| **Phase 2** — Agent Lab and execution | **BLOCKED — EXISTING GOVERNANCE IMPLEMENTATION LOCATION UNRESOLVED → NOW RESOLVED, PENDING MERGE** | see below |
| **Phase 3** — Operations and telemetry | **COMPLETE** | `docs/phase3/` — 19/19 criteria; four states from real probes |
| **Phase 4** — Command Center aggregation | **COMPLETE WITH NON-BLOCKING DEBT** | `docs/phase4/` — 18/19; debt is the absent agent source |
| **Milestone A** | **GATED BY PHASE 2** | — |

### Phase 2 — status change

The blocker is **resolved as a location question**. The archaeology pass
(`docs/phase2/GOVERNANCE_ARCHAEOLOGY.md`) located the governance kernel: it is
**PR #2**, branch `claude/control-plane-implementation`, unmerged since
2026-08-23. Its suite was executed in this session: **144 passed**.

Phase 2 remains **BLOCKED**, but the blocker is now merge-and-integrate rather
than discovery. No ground-up governance build is warranted.

## Verified baseline

| Check | Result | When |
|---|---|---|
| `dotnet build Paios.sln` Debug + Release | 0 warnings, 0 errors | this session |
| `dotnet test Paios.sln` | **78 passed, 0 failed** | this session |
| CI "Build and test" @ `1c0239e` | **green** | 2026-09-07 19:10Z |
| `tier1-remediation/scripts/validate_workflow.py` | 14/14 | this session |
| PR #2 `pytest` (governance kernel) | **144 passed** | this session |
| PR #4 mergeability | clean, no review threads | 2026-09-07 |

The repository had **zero tests** at Phase 0. It now has 78 in the .NET tree,
with 144 more waiting in PR #2.

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

**Not operational**: agent registry, agent execution, governance enforcement
(pending PR #2 merge), persistence of any kind, authentication.

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
| Agent Lab has no data source | Phase 4 debt; dashboard aggregates 2 of 3 subsystems | PR #2 merges and Agent Lab is wired |
| No persistence | Providers survive restart via config; health and audit do not | a datastore is chosen |
| No authentication | Every endpoint is unauthenticated | out of scope for the operational core |
| No HTTP surface on the kernel | PR #2 is a library; .NET cannot call it yet | bridge decision (see below) |
| Audit not hash-chained | `audit.py` is append-only but not tamper-evident | small additive build |
| Docker-healthy path untested | no daemon in the authoring environment | verified on a machine with Docker |
| `Program.cs` collision | PR #2 and PR #4 both modify it | merge order resolved deliberately |

## Cross-language bridge — open decision

The governance kernel is **Python**; the Command Center is **.NET 8**. PR #2
states PAIOS "is a library" with "no HTTP surface". Connecting them requires an
owner decision: expose the control plane over HTTP for the .NET app to call, or
run it as a sidecar process. This is the single largest unresolved architecture
question in the operational core.

## Milestone A — remaining criteria

| Criterion | Status |
|---|---|
| Phase 0 COMPLETE | ✅ |
| Phase 1 COMPLETE | ✅ |
| Phase 2 COMPLETE, or execution dependency resolved to agreed minimum | ❌ **blocking** |
| Phase 3 COMPLETE | ✅ |
| Phase 4 COMPLETE | ⚠️ complete with non-blocking debt |
| Application starts successfully | ✅ |
| Build succeeds | ✅ |
| Required tests pass | ✅ 78 (+144 pending merge) |
| No known critical regression | ✅ |
| Four workspaces browser-accessible | ✅ |
| Real system state visible | ✅ |
| Governance not bypassed | ✅ vacuously — no governance path exists to bypass yet |
| Remaining mock data removed or labelled | ✅ all fabricated values removed |
| Root documentation reflects implementation | ✅ |
| `PAIOS_OPERATIONAL_CORE_STATUS.md` exists | ✅ this document |
| Precise continuation checkpoint exists | ✅ below |

**Two criteria stand between here and Milestone A**: Phase 2, and the Phase 4
debt that resolves with it.

## Continuation checkpoint

**Next repository-level action:** merge PR #2 (`claude/control-plane-implementation`),
resolving the `apps/paios-command-center/Program.cs` and `wwwroot/index.html`
collision with PR #4 — PR #2 carries the pre-Phase-1 versions of both, so a
careless merge order reverts the provider layer and restores the retired
workspace ids.

Then decide the Python↔.NET bridge, wire Agent Lab to the merged control plane,
and re-assess Phase 2 against its exit criteria. Build only KillSwitch,
AgentCredential, PurposeBind and SHA-256 audit chaining, which the archaeology
confirmed genuinely absent.
