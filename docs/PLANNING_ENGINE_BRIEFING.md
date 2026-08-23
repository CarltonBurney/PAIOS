# Planning Engine Briefing — Repository State

**Purpose:** ground truth for the planning engine writing *PAIOS Master Build
Specification v1.0*, so the specification is written against what this
repository actually contains rather than an assumed starting point.

**Status of this document:** facts, not architecture. It makes no design
decisions. Where it raises a conflict, it states the conflict and leaves the
resolution to the specification.

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

### 2.3 Risk levels — **conflict flagged**

The committed documentation defines five risk levels as *categories of concern*:

| Level | Trigger | Action |
|---|---|---|
| Low | Routine, no sensitive data | Auto-execute |
| Standard | Normal operational | Auto-execute with logging |
| Sensitive | Personal/client data | Human review |
| Compliance | Regulatory or legal | Human review + documentation |
| Security | System/access changes | Admin escalation |

The planning brief proposes five levels as an *impact scale*: L0 informational
/ read-only, L1 low-impact internal, L2 controlled business action, L3
sensitive/high-impact requiring approval, L4 prohibited or executive escalation.

**These are not the same taxonomy renamed.** The committed model classifies by
*what kind of concern* a request raises — a compliance question and a security
change are different in kind, not in magnitude, and can co-occur. The proposed
model classifies by *how much impact* an action has, which is a single ordered
axis.

Consequences either way:

- **Adopt L0–L4:** `architecture/workflows/request-classification-flow.md`,
  `docs/PAIOS_CONTROL_PLANE.md`, and `policies/sample-governance-policies.json`
  all need revision, or the docs will contradict the code.
- **Keep the committed model:** the ordering is ambiguous (is Compliance above
  or below Security?) and needs an explicit precedence rule, since a single
  request can trigger several categories at once.
- **Support both:** treat concern-category and impact-level as two orthogonal
  dimensions. More expressive, more configuration surface.

This is the single most consequential unresolved question in the current
material and should be settled early in the specification.

### 2.4 Governance policy shape (policies/sample-governance-policies.json)

Two policies, `PS-001` (Request Classification) and `PS-002` (Approval
Workflow), each with a `controls` array of natural-language control statements.
The current shape has no machine-readable condition or effect — controls are
prose, and enforcement is bound to them in code by exact string match. **This
does not satisfy the brief's "do not hard-code policy logic" requirement** and
needs a real policy schema (condition/effect/scope) in the specification.

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
  risk.py              risk assignment by escalation
  policy.py            policy loading and enforcement
  routing.py           risk → disposition table
  audit.py             append-only audit trail, correlation IDs
  control_plane.py     the pipeline
  config.py            environment configuration
  providers/
    base.py            ModelProvider protocol
    mock.py            deterministic test provider
    foundry.py         Azure AI Foundry (DefaultAzureCredential)
tests/
  test_control_plane.py  20 tests, all passing
```

Verified state: `ruff check` clean, `pytest` 20/20 passing on Python 3.11
(package targets 3.12+).

### 3.1 How to treat it

This slice was written **before** the specification existed and is explicitly
subordinate to it. It is offered as a reference for how the governance core can
be shaped, not as an architecture to preserve. Known divergences from the brief:

- Risk tiers are pattern-driven and hard-coded; the brief requires five
  configurable tiers.
- Policy enforcement binds to control strings by exact match; the brief
  requires a configurable policy engine.
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

1. Risk taxonomy — resolve the conflict in §2.3. Everything downstream
   (policy schema, routing table, approval matrix, audit fields) depends on it.
2. Policy schema — what replaces prose controls, and what evaluates it.
3. Canonical lifecycle — 10-stage documented vs 14-step proposed (§2.1).
4. Whether the existing `architecture/` and `docs/` files are updated to match
   the specification, or superseded and archived.
