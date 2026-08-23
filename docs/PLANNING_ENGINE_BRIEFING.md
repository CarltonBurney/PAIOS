# Planning Engine Briefing — Repository State

**Purpose:** ground truth for the planning engine writing *PAIOS Master Build
Specification v1.0*, so the specification is written against what this
repository actually contains rather than an assumed starting point.

**Status of this document:** facts, not architecture. Where it raises a
conflict, it states the conflict. Sections marked **RESOLVED** record decisions
supplied by the project owner and now implemented; everything else remains open
for the specification.

Generated from commit state on branch `claude/control-plane-implementation`.

---

## 1. What the repository contained before this branch

`main` was **documentation only — no application code of any kind.** A prior
commit ("Refactor repo to professional technical documentation standard")
removed `src/README.md`, and an earlier one removed a private `obsidian-pack/`
directory. There is no build system, no dependency manifest, no CI, no
infrastructure-as-code, no container definition, and no test suite in `main`.

Full inventory of `main`:

```
README.md
docs/
  README.md
  PAIOS_CONTROL_PLANE.md
  AI_NEURAL_GOVERNANCE_OVERVIEW.md
  ROADMAP_AGENT_GOVERNANCE.md
  VALIDATED_USE_CASE_DOCUMENTATION.md
  MICROSOFT_365_INTEGRATION_STRATEGY.md
  ENTERPRISE_USE_CASES.md
  GOVERNANCE_WORKFLOW_EXAMPLE.md
architecture/
  README.md
  diagrams/     (system-architecture.md, governance-workflow.md)
  workflows/    (request-classification-flow.md)
policies/
  sample-governance-policies.json
```

Total: 807 lines of Markdown plus a 26-line JSON policy sample.

**Implication for the specification:** this is a greenfield build. Nothing in
the specification needs to accommodate legacy code, because there is none. The
only existing assets are the documented concepts below.

---

## 2. Concepts already committed to documentation

The specification should either adopt these or explicitly supersede them. Right
now they are the only published definition of PAIOS behaviour, and the sample
policy file is written against them.

### 2.1 Request lifecycle (docs/PAIOS_CONTROL_PLANE.md)

```
Request → Identity/Context Review → Request Classification → Risk Level
Assignment → Routing Decision → Human Approval When Required → Model/Agent
Execution → Output Review → Audit/Documentation → Knowledge Capture
```

This is a 10-stage flow. The planning brief specifies a 14-step lifecycle. They
are compatible in spirit; the specification should state which is canonical and
update the other so the documentation and the implementation do not diverge.

### 2.2 Classification categories (architecture/workflows/request-classification-flow.md)

| Category | Default routing |
|---|---|
| `project` | Project Agent |
| `technical` | Technical Agent |
| `logical` | Logical Agent |
| `core` | Core Agent |
| `governance_change` | Human Approval Gate |

### 2.3 Risk levels — **RESOLVED**

Previously flagged as a conflict between the committed taxonomy (Low/Standard/
Sensitive/Compliance/Security) and the L0–L4 scale in the build brief. Resolved
in favour of **both axes, kept orthogonal**:

```json
{ "risk_level": "L3", "risk_domains": ["security", "compliance"] }
```

- **`risk_level`** — an ordered impact scale, L0 to L4. Answers *how much
  damage could this do*. Exactly one per request.
- **`risk_domains`** — non-exclusive kinds of concern (security, compliance,
  privacy, financial, operational, governance). Answers *what kind of concern
  is this*. Zero or more per request.

This resolves the ambiguity the single-axis model had: "is Compliance above or
below Security?" was unanswerable because they are not on the same line. A
request can now be L3 in both the security and compliance domains at once,
which the committed model could not express and the pure L0–L4 scale could not
either.

Level assignment ratchets: detectors escalate the level and accumulate domains,
and nothing lowers a level once raised.

Implemented in `policies/risk-model.json` — detectors, level definitions, and
the level→disposition mapping are all configuration, satisfying the brief's
"do not hard-code policy logic" requirement for the risk layer.

**Documentation still to reconcile:** `architecture/workflows/request-classification-flow.md`
and `docs/PAIOS_CONTROL_PLANE.md` still describe the old single-axis taxonomy.
They should be updated to the two-axis model, or the specification should state
that they are superseded.

### 2.4 Policy schema — **RESOLVED**

The prose-control format in `policies/sample-governance-policies.json` is
replaced by a declarative scoped/conditioned/effected schema:

```json
{
  "policy_id": "PAIOS-SEC-001",
  "enabled": true,
  "priority": 100,
  "scope":      { "departments": ["*"], "agents": ["*"], "environments": ["prod"] },
  "conditions": { "risk_level": ["L3", "L4"], "risk_domains": ["security"] },
  "effects":    { "require_approval": true,
                  "allowed_tools": ["security_read"],
                  "denied_tools": ["security_write"],
                  "audit_level": "full" }
}
```

Implemented in `policies/policy-rules.json`, which `config.py` now loads by
default. Merge semantics across all matching policies, chosen for
least-privilege and order-independence:

| Effect | Merge rule | Rationale |
|---|---|---|
| `require_approval` | logical OR | any policy demanding approval wins |
| `denied_tools` | union | a denial anywhere is a denial everywhere |
| `allowed_tools` | intersection of specified lists | adding a policy can never *widen* permissions |
| `audit_level` | maximum (minimal < standard < full) | the strictest observer wins |

`priority` orders evaluation for deterministic reporting, but the merge is
order-independent by construction — two policies at equal priority cannot
produce different results depending on file order.

**Decision made during implementation, flagged for confirmation:** `risk_domains`
in `conditions` matches on **overlap**, not exact set equality. A policy scoped
to `["security"]` fires on a request carrying `["security", "compliance"]`. The
alternative — requiring all listed domains — would make multi-domain policies
progressively harder to trigger as detectors improve, which is the wrong
direction for a governance control.

**Not yet resolved:** the schema has no `deny` effect. Outright refusal is
currently handled separately, as authorization (see §3.3), not as policy. If
policy should be able to deny outright, the schema needs that effect.

**Superseded:** `policies/sample-governance-policies.json` remains in the tree
as the documented artifact from `main` but is no longer loaded.

### 2.5 Domain matching — **CONFIRMED**

Overlap semantics, by default and formally:

```
policy_domains ∩ request_domains ≠ ∅
```

The bare array always means *any_of*. A future schema revision may add explicit
`any_of` / `all_of` / `none_of` operators; until then the array must never be
silently reinterpreted as exact equality.

### 2.6 Authorization vs Policy — **CONFIRMED, separate layers**

| Layer | Question | Runs |
|---|---|---|
| **Authorization** | Is this identity entitled to attempt this class of operation? | Before classification |
| **Policy** | May this specific, otherwise-authorized request proceed under current governance conditions? | After risk assessment |

Authorization is now identity-only and runs first, per the canonical pipeline.
The governance-role check that previously lived there has moved to policy as a
`deny` (`PAIOS-GOV-002`), because it is contextual: authorization cannot know a
request is a governance change before classification has run.

Canonical pipeline, now implemented:

```
Authentication → Identity Resolution → Authorization → Classification →
Risk Assessment → Policy Evaluation → Approval Gate → Tool/Workflow Execution →
Output Validation → Audit
```

### 2.7 Policy decision — **ADDED**

`effects.decision` is a constrained enum, resolved across every matching policy
by precedence:

```
deny  >  require_approval  >  allow_with_controls  >  allow
```

Any applicable `deny` wins; approval can never override it. Effects also carry
`reason_code`, surfaced on the decision and in the audit record.

The legacy boolean `require_approval` is still honoured for existing policy
documents, but it can only ever *raise* the outcome, never lower it.

**Extension made during implementation, flagged for confirmation:** policy
conditions gained `request_types` and `principal_roles_none_of`. The latter
fires only when the principal holds *none* of the listed roles, and is what
lets a role-based refusal be expressed as policy rather than authorization. It
is beyond the supplied schema and is called out here rather than assumed.

### 2.8 Tool Registry — **ADDED**

`policies/tool-registry.json`, loaded by `paios.tools.ToolRegistry`. All
specified minimum fields are implemented. Extension points are parsed and
carried under `constraints` — environments, data classifications, network
destinations, timeout, retries, rate limits, idempotency, caller types, allowed
agents, allowed workflows — of which environments and caller types are enforced
today; the remainder are declared so registry documents can express them
without a later schema migration.

Agents may reference registered tools. Agents cannot create executable tool
identities: an unregistered `tool_id` is not a tool, and the gateway rejects it.

The five entries exist to prove the enforcement path. They are **not** a
business tool catalog, which is deliberately out of scope for this slice.

### 2.9 Execution Gateway — **IMPLEMENTED (minimum enforcement)**

`paios.execution.ExecutionGateway` is now the only path to a handler. It
re-validates independently rather than trusting that a caller consulted policy:
a caller who never called `permits_tool()` gets the same answer as one who did.

Rejects, each with a distinct `RejectionReason` and an audit event: unknown
tool, disabled tool, policy `deny`, tool denied by policy, approval required,
approval not granted, missing role, missing scope, environment not permitted,
caller type not permitted, arguments outside the registered contract, no
handler, handler error.

Every attempt — allowed or rejected — emits an audit event carrying the
correlation ID. `permits_tool()` remains a policy *decision*, documented as
such at the call site; the gateway is the security boundary.

### 2.10 Documentation migration — **DONE**

`architecture/workflows/request-classification-flow.md` and
`docs/PAIOS_CONTROL_PLANE.md` now describe the two-axis model, the decision
precedence, the authorization/policy split, and the canonical pipeline. Both
carry the migration note. The old taxonomy survives in Git history only.

### 2.11 Locked design rule — risk never authorizes execution

A request being `L0` or `L1` does not make a tool permissible. Risk, identity
and authorization, policy, tool registration, and approval are separate inputs
to the final execution decision. `if risk_level <= L1: execute()` is
specifically prohibited; the rule is stated in `policy.py`, `execution.py`, and
both reconciled documents, and is covered by tests.


---

## 3. What exists on this branch

A **pre-specification reference slice** — working, tested code that implements
the documented lifecycle end to end with no cloud dependency.

```
pyproject.toml
.env.example
src/paios/
  __init__.py          package exports
  models.py            domain types (Identity, Request, Classification, …)
  classification.py    rule-based classifier + optional model assist
  risk.py              two-axis risk assessment, config-driven
  policy.py            declarative policy engine
  routing.py           authorization + disposition
  audit.py             append-only audit trail, correlation IDs
  control_plane.py     the pipeline
  config.py            environment configuration
  providers/
    base.py            ModelProvider protocol
    mock.py            deterministic test provider
    foundry.py         Azure AI Foundry (DefaultAzureCredential)
tests/
  audit.py             append-only audit trail, correlation IDs
  tools.py             Tool Registry
  execution.py         Execution Gateway (enforcement boundary)
  control_plane.py     the pipeline
  config.py            environment configuration
  providers/
tests/
  test_control_plane.py      pipeline
  test_policy_decisions.py   precedence + domain matching
  test_execution_gateway.py  registry + enforcement
                             87 tests total, all passing
```

Verified state: `ruff check` clean, `pytest` 87/87 passing on Python 3.11
(package targets 3.12+).

### 3.1 How to treat it

This slice was written **before** the specification existed and is explicitly
subordinate to it. It is offered as a reference for how the governance core can
be shaped, not as an architecture to preserve. Known divergences from the brief:

- ~~Risk tiers hard-coded~~ — now configurable via `policies/risk-model.json`.
- ~~Policy bound to control strings~~ — now a declarative schema (§2.4).
- Tool permissions are *decided and audited* but not yet *enforced* — there is
  no tool execution layer for `permits_tool()` to gate. The decision is
  computed and recorded; a future execution gateway consumes it.
- No FastAPI surface, no Pydantic, no persistence, no Graph adapter, no
  retrieval layer, no registries, no telemetry — none of the subsystems the
  brief enumerates.
- Package layout is flat; the brief specifies a much wider module tree.

Whatever survives should survive because the specification chose it, not
because it was written first.

### 3.2 What may be worth carrying forward

Three behaviours in the slice are governance properties the specification will
likely want regardless of how the architecture is drawn, because each one is a
concrete answer to "how does this fail safely":

1. **Escalation-only risk.** Detectors can raise a risk level, never lower it.
   A request touching both client PII and a permission change resolves to the
   higher level, not an average.
2. **One-way governance classification.** A request classified as
   `governance_change` cannot be reclassified downward by a model. This is the
   defence against a prompt-injected request talking its way out of the
   approval gate.
3. **Deny by default on approval.** With no approval handler wired up, requests
   requiring human review resolve to `TIMED_OUT`, not to execution. Absent
   governance means no action, not unrestricted action.

---

## 4. Environment facts

- **Repository:** `github.com/CarltonBurney/PAIOS`, **public**.
  Note: private material has been removed from this repository three separate
  times in its history. Tenant identifiers, resource names, and internal
  topology should be treated as configuration, never committed.
- **Default branch:** `main`.
- **Language/runtime in the dev container:** Python 3.11.15.
- **CI:** none configured.
- **Infrastructure-as-code:** none present.
- **Container definition:** none present.

---

## 5. Tenant values the implementation will need

None of these exist in the repository. All are configuration, supplied at
deploy time. **No secret, key, or credential belongs in any of these slots** —
authentication uses `DefaultAzureCredential` (managed identity in Azure,
`az login` locally).

| Value | Consumed by |
|---|---|
| Azure tenant ID | Entra authentication |
| Subscription ID | Infrastructure provisioning |
| Preferred region | All resource deployment |
| Resource group naming convention | Infrastructure provisioning |
| Foundry project + resource name | Model provider |
| Foundry model deployment name(s) | Model provider, model registry |
| Entra app registration (client ID) | API authentication |
| Entra app roles / group object IDs | Role resolution, policy scoping |
| SharePoint site URLs participating | Retrieval, document sync |
| Teams / Copilot publishing targets | End-user surfaces |
| PostgreSQL server + database name | Durable operational data |
| Key Vault name | Secret resolution |
| Storage account + container names | Artifacts, log archive |
| Application Insights connection | Telemetry |
| Domain / naming conventions | Everything |

---

## 6. Recommended first questions for the specification to answer

1. ~~Risk taxonomy~~ — **resolved**, two orthogonal axes (§2.3).
2. ~~Policy schema~~ — **resolved**, scoped/conditioned/effected records (§2.4).
3. Canonical lifecycle — 10-stage documented vs 14-step proposed (§2.1). Still open.
4. Whether the existing `architecture/` and `docs/` files are updated to match
   the specification, or superseded and archived. Still open, and now overdue:
   they describe a risk taxonomy the code no longer implements.
5. ~~Whether policy needs a `deny` effect~~ — **resolved**, added (§2.7).
6. ~~The tool namespace~~ — **resolved**, Tool Registry is first-class (§2.8).
7. **Next slice:** Agent Registry, Model Registry, and Workflow Registry, plus
   the common registry lifecycle they should all share — registration,
   versioning, enable/disable, ownership, promotion, retirement, audit. The
   Tool Registry is currently the only registry and was built standalone; the
   lifecycle pattern should be defined once and applied to all four rather than
   letting each evolve its own.
8. Confirm the `principal_roles_none_of` / `request_types` condition extension
   (§2.7), which was added during implementation and is not in the supplied
   schema.
