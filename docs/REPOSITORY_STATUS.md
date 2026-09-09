# Repository Status

Engineering status snapshot for the PAIOS repository. This document tracks what
is merged, what is in flight, and what is blocked. It is scoped to the contents
of this repository — it intentionally records no account, credential, or
personal data.

**Snapshot date:** 2026-09-08
**Default branch:** `main` (`07457fc`)

---

## Merged and on `main`

| Area | State | Notes |
|------|-------|-------|
| Framework documentation | Merged | `docs/` — governance overview, control plane, M365 integration strategy, roadmap, use cases |
| Architecture references | Merged | `architecture/` — system architecture and governance workflow Mermaid diagrams, request classification flow |
| Sample policies | Merged | `policies/sample-governance-policies.json` |
| Tier-1 remediation reference implementation | Merged (PR #1) | `tier1-remediation/` — workflow definition, architecture diagram, validation script |
| PAIOS Command Center | Merged | `apps/paios-command-center/` — .NET 8 ASP.NET Core scaffold; static shell, `/health`, `/api/workspaces` |
| CI | Partial | `.github/workflows/validate-tier1-remediation.yml` validates the Tier-1 workflow only. Control-plane CI exists on PR #2's branch but is not yet on `main`. |

`main` currently contains documentation, one reference workflow and its
validation job, and the Command Center scaffold. The governance control plane
itself is not on `main` — it is still in PR #2.

---

## In flight

### PR #2 — Governance control plane

Branch `claude/control-plane-implementation`. Adds the request lifecycle,
two-axis risk model, declarative policy engine, governed registry framework,
tool registry, and execution gateway: 40 files, ~8,170 added lines, 144 tests.

**Status: clean, no conflicts, ready for review.** The earlier `.gitignore`
add/add conflict was resolved by merging `main` into the branch (`5f34d6f`); the
branch now contains `main` at `07457fc`. Its `.gitignore` carries `main`'s
content plus `*.egg-info/`, `build/` and `*.jsonl`.

The branch also adds `.github/workflows/validate-control-plane.yml` (`4980c53`),
which runs on changes to `src/`, `tests/`, `policies/` or `pyproject.toml`:
ruff over the repository, the full pytest suite on Python 3.12, and a parse
check over every file in `policies/`. That last gate exists because the policy
engine, risk model and tool registry are configuration rather than code — a
malformed JSON file there disables governance at load time rather than failing
review.

Note the PR's own description still carries a "No CI" caveat written before that
workflow was added. The workflow is on the branch; the description is stale.

### PR #4 — Docker-optional launcher and session toolchain

Branch `claude/docker-desktop-init-blocked-k7zwoa`. 6 files, ~379 added lines.
**Status: clean, draft, no conflicts.**

Adds `apps/paios-command-center/scripts/start.ps1`, a launcher that resolves
`docker.exe` when a Docker Desktop restart has dropped it from the shell PATH,
probes engine health under a hard per-call timeout rather than blocking on a
half-started engine, and falls back to the local .NET 8 SDK when the engine
never becomes healthy. Adds a SessionStart hook installing the .NET SDK and
PowerShell 7 so the app can be built and run in a Claude Code web session, and
unanchors `bin/`/`obj/` in `.gitignore`.

No application code changes; both runtimes serve the same `/`, `/health` and
`/api/workspaces` routes.

---

## Known gaps

These are stated so they are not mistaken for oversights.

- **No persistence layer.** Registries and audit records are in memory. Postgres
  exists in the dev compose stack but holds n8n data only.
- **Two runtimes, unreconciled.** The Command Center on `main` is .NET 8 /
  ASP.NET Core. The control plane in PR #2 is Python. Nothing currently
  defines how the shell reaches the governance layer — whether the control
  plane grows an HTTP surface the shell calls, or the enforcement boundary is
  reimplemented in .NET. This decision should be made deliberately rather than
  settled by whichever side ships first.
- **The Command Center shell is not governed.** `/api/workspaces` returns a
  hardcoded list, three of four entries marked `simulation`. No request passes
  through classification, policy, or audit. It is a shell awaiting a backend,
  not an enforcement path.
- **Port binding is inconsistent across compose files.** The Command Center
  compose publishes `"8080:8080"`, which binds all interfaces. PR #2's compose
  deliberately binds every published port to `127.0.0.1`. Worth aligning on
  the stricter form before either stack is run outside a laptop.
- **Agent, Model, and Workflow registries are unbuilt.** Only the Tool registry
  sits on the governed registry substrate.
- **Break-glass is specified, not implemented.** `L4` denial stands on the
  normal control plane.
- **The Docker-healthy path is still unverified.** PR #4 tests the .NET
  fallback, the CLI-resolution path and the unreachable-engine timeout, but no
  environment in that work had a working Docker daemon, so `docker compose up`
  itself remains unexercised for both the Command Center and PR #2's dev stack.
  Windows PowerShell 5.1 is likewise untested; the 5.1-sensitive constructs are
  guarded but unproven.
- **Three data substrates, unreconciled.** PR #2 holds registries and audit
  records in memory. The dev compose stack runs Postgres, but it holds n8n's data
  only. A third substrate, Microsoft Dataverse, is described in project planning
  as the store for assessment, finding, remediation-task and evidence entities. No
  Dataverse schema, binding, or reference exists anywhere in this repository. The
  three have no defined relationship, and nothing states which is authoritative
  for governance records.
- **Two distinct architectures share the name PAIOS.** The recovered
  NeuralGovernance roster describes a *runtime governance architecture* — identity,
  memory, agent orchestration, security, audit. Project planning separately
  describes a *consulting assessment data model* — organization profile,
  assessment, finding, remediation task, evidence, reporting. Neither maps onto
  the other: the roster's `L10-M01 AuditTrail` is not the assessment model's
  evidence entity, and the roster's `L08` governance layer is not assessment
  findings. Which of the two `src/paios/` implements should be stated explicitly
  before either grows further, since both currently claim the same name.
- **Knowledge base integration is absent.** The framework describes an Obsidian
  knowledge base as part of the stack. No integration code, export tooling, or
  vault interface exists in this repository — earlier export material was
  removed prior to public release and was never replaced with a governed
  integration path.

---

## Handoff reconciliation — 2026-09-08

A continuation handoff was supplied describing phase status and integration work.
Its own directive states that repository evidence takes precedence over historical
description where the two conflict. Each claim below was checked against the
repository. `CarltonBurney/PAIOS` is the only PAIOS repository available.

### Confirmed by repository evidence

- **The governance implementation is Python, not TypeScript.** `src/paios/` on
  PR #2's branch is entirely Python.
- **It is a library with no HTTP surface.** No ASP.NET or web framework entry
  point exists in the Python tree; a runtime bridge is genuinely required.
- **PurposeBind, AgentCredential, KillSwitch and SHA-256 audit chaining are
  absent.** A case-insensitive search of PR #2's branch for each name and its
  snake_case form returns zero files. The audit record schema on the design
  branch likewise specifies no hash, chain, or predecessor field.
- **Only the Tool registry sits on the governed registry substrate.** Agent,
  Model and Workflow registries are unbuilt.

### Corrected by repository evidence

- **PR #2 has no collision with `Program.cs` or `wwwroot/index.html`.** It
  touches no file under `apps/` at all. The only conflict it ever carried was an
  add/add on `.gitignore`, already resolved by merging `main` into the branch.
  `mergeable_state` is clean. There is no merge or rebase work outstanding, and
  no Command Center implementation at risk of being reverted by merging it.

- **The Command Center on `main` is a scaffold, not a completed aggregation
  layer.** `apps/paios-command-center/Program.cs` is 17 lines: health checks,
  static files, and one `/api/workspaces` endpoint returning a hardcoded
  four-entry array, three of whose entries are labelled `simulation`.
  `wwwroot/index.html` is 31 lines. There is no telemetry, no aggregation, and no
  provider or workspace implementation on `main` to preserve.

- **Provider/model abstraction exists, but not on `main`.** `src/paios/providers/`
  (base, mock, and an Azure AI Foundry provider) is present only on PR #2's
  branch. It is implemented and unmerged rather than complete.

### Present in the repository but absent from the handoff

Two unmerged branches carry work the handoff does not mention:

- **`claude/design-v77k6t`** — five architecture decision records and four policy
  JSON schemas. This bears directly on the components listed as missing:
  `policies/schemas/agent-policy.schema.json` already specifies the PurposeBind
  data shape. Required: `agent_name`, `purpose`, `owner`, `allowed_users`,
  `allowed_data_sources`, `review_cycle`. Optional: `allowed_actions`,
  `restricted_actions`, `approval_required_for`, `output_target`,
  `audit_required`, `retirement`. `retirement` is a nested object holding
  `status`, `retired_at` and `successor_agent` — those three are **not**
  top-level fields. `additionalProperties` is `false`. And
  ADR-0005 records the enforcement decision behind it — constraints applied at the
  binder as execution-time adapter restrictions rather than as prompt
  instructions, with model output treated as untrusted throughout. Review this
  branch before implementing PurposeBind from scratch.

- **`claude/mlg-deal-intelligence-pipeline-xpnwqc`** — an applied use case with
  governance policies and workflow documentation.

### Classification

| Capability | State |
|---|---|
| Governance kernel (policy, risk, registry, gateway, control plane, audit) | Implemented, unmerged (PR #2) |
| Control-plane CI | Implemented, unmerged (PR #2) |
| Provider/model abstraction | Implemented, unmerged (PR #2) |
| Command Center shell | Implemented, on `main`, ungoverned scaffold |
| Docker-optional launcher | Implemented, unmerged (PR #4) |
| Policy schemas and ADRs | Implemented, unmerged (design branch) |
| PurposeBind | Documented only (schema + ADR, no code) |
| NeuralGovernance module roster | Recovered as identity registry — see [NEURALGOVERNANCE_MODULE_ROSTER.md](NEURALGOVERNANCE_MODULE_ROSTER.md). 54 modules, all recorded `active`, all development fields blank. Recorded status is not implementation evidence. |
| AgentCredential | Missing |
| KillSwitch | Missing |
| SHA-256 audit chaining | Missing |
| Python-to-.NET runtime bridge | Missing |
| Agent / Model / Workflow registries | Missing |
| Persistence layer | Missing |
| Agent roster | Not present in this repository |

---

## Suggested order of work

1. Review and merge PR #2. It is clean and carries its own CI. No merge or
   rebase work is outstanding and nothing on `main` is at risk from it.
2. Review `claude/design-v77k6t` before writing PurposeBind. Its agent-policy
   schema and ADR-0005 already fix the data shape and the enforcement decision.
3. Review and merge PR #4. Both merge orders were verified conflict-free.
4. Decide how the Command Center shell reaches the control plane. The Python
   kernel exposes no HTTP surface, so a service boundary has to be added on one
   side or the other before the shell can show governed state.
5. Implement AgentCredential, KillSwitch and SHA-256 audit chaining. Chaining
   should extend the existing append-only audit trail rather than replace it.
6. Replace the Command Center's hardcoded `/api/workspaces` array with real
   state once a bridge exists — until then it reports nothing the system knows.
7. Pick up persistence or the remaining registries as separate slices.

---

## Bridge ADR and PurposeBind plan — verification, 2026-09-09

Two proposed documents were supplied for reconciliation: a Python service-boundary
ADR and a PurposeBind implementation plan. Both are proposals; neither changes
runtime code. Every checkable claim was verified against the pinned revisions.

### Verified true

All six runtime mismatches the ADR identifies are real:

| Claim | Verification |
|---|---|
| `ControlPlane` calls the provider directly and never invokes `ExecutionGateway` | `control_plane.py:192` is `self.provider.complete(request.content)`; the file contains no gateway reference. Two disconnected execution paths, confirmed. |
| Audit follows execution rather than preceding it | `execution.py:145` types `trail` as optional; `handler(args)` runs at line 285 and `EXECUTION_ALLOWED` is emitted at 296–298, after the side effect. |
| A missing governance context defaults to allow | `execution.py:149` is `decision = governance_context or PolicyDecision()`, and `models.py:230` declares `decision: PolicyOutcome = PolicyOutcome.ALLOW`. Absence of a policy decision is treated as permission. |
| `Approval` carries no decision or bundle binding | `models.py:294` declares only `state`, `approver`, `decided_at` and a note. Nothing ties an approval to the decision it approves. |
| Output review is a placeholder | `control_plane.py:212` emits `OUTPUT_REVIEWED` with `reviewed=True` unconditionally; no review occurs. |
| Contract validation checks unknown keys only | `_contract_violations` at `execution.py:309` returns undeclared argument names. Its own docstring records that a tool with no declared schema accepts anything. Required fields, types and nested properties are unchecked. |

Also verified: the design branch holds exactly 29 files; `audit-record.schema.json`
declares `corrects` and no hash, predecessor or chain field.

### Correction accepted

The PurposeBind plan corrects this document. An earlier revision listed the
agent-policy schema's fields as a flat set including `status` and
`successor_agent`, and omitted `review_cycle`. The schema requires
`review_cycle`, and nests `status`, `retired_at` and `successor_agent` under
`retirement`. The field list above is corrected.

### Consistent with the recovered roster

Both documents hold the roster's separations. The plan states that a
`BoundExecutionContext` "is not an implementation of AgentCredential"
(`L07-M02`), that cancellation of a running operation "must not be advertised as
an implemented KillSwitch" (`L09-M01`), and that SHA-256 chaining must not be
claimed as implemented (`L10-M01`'s recorded tamper-evident property). Each of
the three remaining distinguished modules is explicitly held out rather than
absorbed. `PurposeBind` (`L01-M02`) is the only one the plan advances, and only
to *specified*, not *implemented*.

Two proposed modules map onto roster entries and are worth naming as such:
`output_review.py` corresponds to `L09-M04 OutputGuard`, and `agents.py`
corresponds to `L07-M01 AgentOrchestrator`. The proposed `knowledge_writer.py`
has no roster counterpart.

### Sequencing dependency

The ADR proposes `architecture/decisions/0006-python-service-boundary.md`. That
directory exists only on `claude/design-v77k6t`; ADRs 0001–0005 are not on `main`.
The ADR itself flags that the number must be reserved against the destination
index at integration time. Landing 0006 on `main` before the design branch
integrates would orphan it from the five records it builds on — ADR-0005 in
particular, which the PurposeBind plan depends on directly.
