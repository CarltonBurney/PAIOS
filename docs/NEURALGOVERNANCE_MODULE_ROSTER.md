# NeuralGovernance Module Roster — Recovered

Recovered module registry for the PAIOS NeuralGovernance architecture, transcribed
from the **PAIOS Encyclopedia**, a static HTML reference set found in the project
owner's Google Drive. Every page carries the footer *"Generated from PAIOS
NeuralGovernance v2.1 reproduction package."* Pages are dated 2026-04-28.

**Recovered:** 2026-09-08
**Source:** 54 HTML pages, `l<NN>-m<NN>-<name>.html`, held in two Drive folders
(the set is duplicated across both; contents are identical where they overlap).

---

## What this document is, and is not

This roster records **module identity** — what the architecture declares exists,
under what identifier, in which layer. It is deliberately separated from two
other things it must not be confused with:

- **It is not a specification.** Every one of the 54 pages carries a
  *Development Notes Template* with the fields `Purpose`, `Inputs`, `Outputs`,
  `Dependencies`, `Security considerations`, `Audit events`, `Validation test`,
  `Open questions`. **On all 54 pages every one of those fields is blank.** They
  are recorded below as **unspecified**.

- **It is not evidence of implementation.** Every page records `Status: active`.
  That is a status recorded in a reference document generated in April 2026. It
  says nothing about whether code exists. Four modules on this roster —
  PurposeBind, AgentCredential, KillSwitch and the tamper-evident property of
  AuditTrail — are recorded here as `active` and are **absent from the codebase**,
  confirmed by search of PR #2's branch. **A recorded status of `active` must not
  be read as implemented.**

Verified implementation state is tracked separately in
[`REPOSITORY_STATUS.md`](REPOSITORY_STATUS.md).

---

## Count discrepancy

Historical descriptions of NeuralGovernance v2.1 refer to **51 modules across 11
layers**. The recovered set contains **54 modules across 11 layers**. The layer
count matches; the module count does not. No module numbering gaps remain after
both Drive folders were merged, so the surplus is not an artifact of missing
pages. The discrepancy is recorded rather than resolved — the "51" figure and
this roster disagree, and repository evidence exists for neither.

---

## Rationale templates

Each page carries a one-sentence rationale, repeated verbatim in its "Why It
Matters" card. These sentences are **templated per layer, not authored per
module**, and several are mis-assigned to modules in other layers. The templates
are listed once here and referenced by code in the tables below.

| Code | Sentence |
|---|---|
| **T-ID** | Defines the system's declared identity so every downstream action knows what PAIOS is and is not. |
| **T-OWN** | Maintains the human owner profile and preferences that guide personalization without replacing human authority. |
| **T-MIS** | Locks the system to its intended mission and reduces drift away from the owner-approved operating mandate. |
| **T-COM** | Controls how the system communicates so output remains consistent, usable, and aligned to the intended user experience. |
| **T-BEH** | Interprets user patterns carefully so the system can adapt while avoiding unsupported assumptions. |
| **T-HUM** | Provides the human-interaction layer: emotional awareness, tone calibration, values alignment, and relationship-sensitive response behavior. |
| **T-COG** | Processes requests into structured intent, reasoning steps, decisions, and tasks that can be executed or handed off. |
| **T-LRN** | Improves future responses using validated feedback, learning signals, and user-approved adaptation. |
| **T-MEM** | Manages what the system can remember, retrieve, forget, and apply across sessions. |
| **T-AGT** | Coordinates agents, routes work to the correct component, and controls how agent capabilities are exposed. |
| **T-GOV** | Applies rules, approval gates, and enterprise policy controls before action is taken. |
| **T-SEC** | Protects the system from unsafe inputs, unauthorized execution, secret leakage, and runaway behavior. |
| **T-OBS** | Creates operational visibility so behavior can be reviewed, measured, debugged, and rolled back. |
| **T-INT** | Connects PAIOS to tools, devices, agents, and external systems while preserving governance boundaries. |
| **T-GEN** | Serves as one controlled component inside the PAIOS governance stack. *(generic filler)* |

Every rationale is followed on the page by the same closing clause: *"In
enterprise terms, this makes PAIOS easier to audit, explain, test, and deploy
because each function has a defined boundary."*

A **⚠** in the tables marks a rationale belonging to a different layer's
template — a transcription defect in the source, not a design statement.

---

## Roster

All modules record `Status: active` and version `2.0` unless noted. All
development fields are **unspecified**. Source page is
`<id-lowercase>-<name-lowercase>.html`.

### L01 — Identity & Purpose
*Establishes who the system is, what it serves, and its operating mandate*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L01-M01 | CoreIdentity | 2.0 | T-ID | — |
| L01-M02 | **PurposeBind** | 2.0 | T-MIS | **Infrastructure layer — prevents purpose drift** |
| L01-M03 | OwnerProfile | 2.0 | T-OWN | — |
| L01-M04 | MissionAlignment | 2.0 | T-MIS | — |

### L02 — Personality & Behavioral Matrix
*Governs tone, style, adaptive personality modeling, and relationship intelligence*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L02-M01 | PersonalityMatrix | **3.0** | T-COM | — |
| L02-M02 | ToneEngine | 2.0 | T-COM | — |
| L02-M03 | BehavioralInference | 2.0 | T-BEH | — |
| L02-M04 | RelationshipProgressionModel | 2.0 | T-GEN | — |
| L02-M05 | AdaptiveCommunicationStyle | 2.0 | T-LRN ⚠ | — |

### L03 — Humanistic Intelligence
*Emotional intelligence, empathy modeling, ethical reasoning, and human-centered values*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L03-M01 | HIM_Core | 2.0 | T-HUM | — |
| L03-M02 | EmotionalStateDetection | 2.0 | T-HUM | — |
| L03-M03 | EmpathyModeling | 2.0 | T-HUM | — |
| L03-M04 | EthicsLayer | 2.0 | T-GEN | — |
| L03-M05 | WellnessAwareness | 2.0 | T-GEN | — |

### L04 — Cognitive Processing
*Reasoning frameworks, wish processing, intent resolution, and decision logic*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L04-M01 | GenieMethod | 2.0 | T-COG | — |
| L04-M02 | IntentResolver | 2.0 | T-COG | — |
| L04-M03 | ReasoningOrchestrator | 2.0 | T-COG | — |
| L04-M04 | AmbiguityHandler | 2.0 | T-GEN | — |

### L05 — Adaptive Learning
*Learning node management, neurodivergent support, teaching adaptation, knowledge retention*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L05-M01 | AdaptiveLearningNode | 2.0 | T-LRN | — |
| L05-M02 | NeurodivergentProfileSupport | 2.0 | T-GEN | — |
| L05-M03 | MBTIAdaptiveTeaching | 2.0 | T-LRN | — |
| L05-M04 | KnowledgeRetentionEngine | 2.0 | T-GEN | — |

### L06 — Memory & Persistence
*Short-term context, long-term memory indexing, recall strategy, session continuity*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L06-M01 | ShortTermContext | 2.0 | T-MEM | — |
| L06-M02 | LongTermMemoryIndex | 2.0 | T-MEM | — |
| L06-M03 | RecallStrategy | 2.0 | T-MEM | — |
| L06-M04 | SessionContinuity | 2.0 | T-GEN | — |
| L06-M05 | MemoryPrivacyGuard | 2.0 | T-MEM | — |

### L07 — Agent Orchestration
*Multi-agent coordination, WTU bot class management, task routing, inter-agent communication*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L07-M01 | AgentOrchestrator | 2.0 | T-AGT | — |
| L07-M02 | **AgentCredential** | 2.0 | T-AGT | **Infrastructure layer — identity verification for all agents** |
| L07-M03 | WTU_BotClassRouter | 2.0 | T-AGT | — |
| L07-M04 | TaskDelegationEngine | 2.0 | T-COG ⚠ | — |
| L07-M05 | InterAgentProtocol | 2.0 | T-AGT | — |

### L08 — Global & Local Governance
*System-wide rules, domain-specific overrides, policy hierarchy enforcement*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L08-M01 | GlobalGovernance | 2.0 | T-GOV | — |
| L08-M02 | LocalGovernance | 2.0 | T-GOV | — |
| L08-M03 | PolicyHierarchy | 2.0 | T-GOV | — |
| L08-M04 | DomainContextSwitcher | 2.0 | T-MEM ⚠ | — |
| L08-M05 | OverrideAuthority | 2.0 | T-GEN | — |

### L09 — Security & Safety
*Input/output validation, injection defense, kill switch, rate limiting, secrets management*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L09-M01 | **KillSwitch** | 2.0 | T-SEC | **Infrastructure layer — immediate shutdown capability** |
| L09-M02 | InjectionDefense | 2.0 | T-SEC | — |
| L09-M03 | InputValidator | 2.0 | T-SEC | — |
| L09-M04 | OutputGuard | 2.0 | T-INT ⚠ | — |
| L09-M05 | SecretsManager | 2.0 | T-SEC | — |
| L09-M06 | RateLimiter | 2.0 | T-GEN | — |

### L10 — Audit & Observability
*Immutable audit trail, telemetry, performance metrics, error tracking, compliance logging*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L10-M01 | **AuditTrail** | 2.0 | T-OBS | **Infrastructure layer — append-only, tamper-evident log** |
| L10-M02 | TelemetryEngine | 2.0 | T-OBS | — |
| L10-M03 | PerformanceMonitor | 2.0 | T-OBS | — |
| L10-M04 | ErrorTracker | 2.0 | T-GEN | — |
| L10-M05 | ComplianceLogger | 2.0 | T-GOV ⚠ | — |

### L11 — Integration & Output
*API routing, MCP protocol, device sync, output formatting, external system connectors*

| ID | Name | Ver | Rationale | Source Note |
|---|---|---|---|---|
| L11-M01 | APIRouter | 2.0 | T-AGT ⚠ | — |
| L11-M02 | MCPProtocol | 2.0 | T-INT | — |
| L11-M03 | DeviceSyncManager | 2.0 | T-INT | — |
| L11-M04 | OutputFormatter | 2.0 | T-INT | — |
| L11-M05 | ExternalConnectorRegistry | 2.0 | T-INT | — |
| L11-M06 | A2A_AgentCardLayer | 2.0 | T-AGT ⚠ | — |

---

## The four distinguished modules

Fifty of the 54 pages record the source note *"No special note supplied in the
master JSON."* Exactly **four** carry a substantive note, and those four are the
same four components independently identified as missing from the codebase:

| ID | Module | Recorded source note |
|---|---|---|
| L01-M02 | PurposeBind | Infrastructure layer — prevents purpose drift |
| L07-M02 | AgentCredential | Infrastructure layer — identity verification for all agents |
| L09-M01 | KillSwitch | Infrastructure layer — immediate shutdown capability |
| L10-M01 | AuditTrail | Infrastructure layer — append-only, tamper-evident log |

The AuditTrail note is worth isolating: **"append-only, tamper-evident"** is the
SHA-256 chaining requirement stated in prose. The implemented audit trail in
PR #2 is append-only but not tamper-evident, so the recovered note names exactly
the property the implementation lacks.

This alignment is a corroboration, not a specification. It confirms the four
components were architecturally intended and gives each a one-line statement of
intent. It supplies no inputs, outputs, dependencies, or validation criteria —
those remain unspecified everywhere in this source.

---

## Where specification actually exists

For **PurposeBind** only, a real specification exists in this repository, on the
unmerged branch `claude/design-v77k6t`:

- `policies/schemas/agent-policy.schema.json` — the data shape: `agent_name`,
  `purpose`, `allowed_actions`, `allowed_data_sources`, `allowed_users`,
  `restricted_actions`, `approval_required_for`, `audit_required`, `owner`,
  `status`, `retirement`, `successor_agent`.
- `architecture/decisions/0005-binder-enforcement.md` — the enforcement decision:
  constraints applied at the binder as execution-time adapter restrictions rather
  than prompt instructions, with model output treated as untrusted throughout.

AgentCredential, KillSwitch and audit chaining have no equivalent. For those
three, this roster plus its one-line source note is the whole of the recovered
intent.

---

## Not recovered

- The **PAIOS Encyclopedia index** (`index.html`) and the eleven **layer pages**
  (`layers/l<NN>-*.html`) are referenced by every module page's navigation but
  were not located in Drive. Layer names and descriptions above were reconstructed
  from the module pages themselves, where both appear verbatim.
- The **agent roster** is a separate artifact and is **not** in this set. These 54
  entries are architecture modules, not agents. Nothing here bears on the
  historical claim of an approximately 50-agent registry, and nothing here should
  be substituted for it.
