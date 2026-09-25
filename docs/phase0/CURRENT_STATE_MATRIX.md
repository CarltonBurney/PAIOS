# CURRENT_STATE_MATRIX.md

Filled by inspection on 2026-09-07 at commit `18ba1a6`. Status reflects verified
findings. "Absent" means the subsystem has no representation in this repository —
not code, not config, not stub.

| Component | Status | Real/Mock/Partial | Dependency | Blocker | Next Action |
|---|---|---|---|---|---|
| Command Center shell | Renders | **Partial** | None | Makes zero HTTP calls | Add a fetch layer to `index.html` before any data work |
| Local LLMs — Ollama detection | Absent | — | HTTP client | No `PackageReference` in csproj; no HTTP client exists | Add adapter + first project dependency |
| Local LLMs — OpenAI-compat endpoint | Absent | — | Config system | No config file/convention exists to hold endpoints | Create `config/providers.json` + binding |
| Local LLMs — model enumeration | Absent | — | Both above | Nothing to enumerate from | After adapters land |
| Local LLMs — health states | Absent | — | Adapters | No health model beyond ASP.NET `/health` | Implement 4-state model per spec |
| Agent Lab — agent registry | Absent | **Mock** | Persistence | 3 hard-coded HTML labels (`GMP`/`IT`/`SSL`); 0 records | Decide store (none exists) before schema |
| Agent Lab — execution path | Absent | — | Governance | No execution code of any kind | Blocked behind governance decision |
| Agent Lab — governance integration | **Not present here** | — | Governance Core | Governance Core not present in this repository — 0 code, 0 docs. Implementation location unresolved (see NeuralGovernance v2.1) | Archaeology pass across remaining PAIOS/DGE repos before any build |
| Agent Lab — run history | Absent | — | Persistence | No datastore | After persistence decision |
| Operations — service health checks | Absent | — | Services to check | Only 1 service exists (this app) | Scope down: most listed services aren't present |
| Operations — error surfacing | Absent | — | Health checks | Workspace has no panels at all | After health model |
| Command Center — aggregation | Absent | **Mock** | Phases 1–3 | Panels are literal HTML (`04`, `∞`) | Blocked until real sources exist |
| Governance Core | **Not present here** | — | — | 0 occurrences in code **and** 0 in this repo's docs | Locate NeuralGovernance v2.1 before deciding to build |
| Tool Registry | **Not present here** | — | Governance Core | 0 occurrences in this repository | Same archaeology pass |
| Execution Gateway | **Not present here** | — | Governance Core | 0 occurrences in this repository | Same archaeology pass |
| Data stores (Postgres/SQLite/vector) | **Absent** | — | — | No connection strings, no ORM, no client library, no persistence | Choose a store; everything stateful blocks on this |

## Verification Method

| Claim | How verified |
|---|---|
| Zero tests | `dotnet test` → `MSB1003`; `find` for test/spec files → empty |
| Build clean | `dotnet build` → 0 warnings, 0 errors |
| No provider code | repo-wide grep: `ollama`, `11434`, `openai`, `/v1/models`, `lmstudio`, `localai`, `vllm` → 1 hit, prose in HTML |
| No governance code | grep per term across `.cs`/`.py`/`.ts`/`.js` → 0 for all |
| UI makes no calls | grep `fetch(`/`XMLHttpRequest`/`axios` in `index.html` → 0 |
| Policy file inert | grep for `sample-governance-policies` in code → 0 references |
| Workspace mismatch | `GET /api/workspaces` vs `data-workspace` attributes — disjoint sets |
| Routes | live server: `/health` 200, `/api/workspaces` 200, `/api/providers` 404 |
| tier1 validator | `python3 validate_workflow.py` → 14/14 passed |

## Phase Gate Assessment

**Phase 0 exit criterion** — "can name the exact file/service behind every
subsystem": **met.** For most subsystems the honest answer is *nothing exists*,
which is itself the finding.

**Three gate-level problems for the phases ahead:**

1. **Phase 2 is BLOCKED — EXISTING GOVERNANCE IMPLEMENTATION LOCATION
   UNRESOLVED.** Its exit requires tracing an agent through "the REAL governance
   path (Identity/RBAC → Purpose Binding → Policy Eval → Tool Registry →
   Execution Gateway → Result/Audit)". None of those six stages are present in
   *this* repository. Historical records describe NeuralGovernance v2.1 (51
   modules, 11 layers, TypeScript, ~65 files; AuditTrail v2.0 with SHA-256
   chaining, KillSwitch, AgentCredential, PurposeBind) associated with this
   account's PAIOS/DGE repositories. **Do not conclude the kernel must be built
   from scratch.** The recovery operation is an archaeology and reconciliation
   pass across the remaining repositories and historical artifacts to locate
   that implementation and determine which components genuinely exist.

2. **Phase 3's service list assumes infrastructure that isn't here.** It names
   n8n, Postgres/SQLite, Chroma/Qdrant, Tool Registry, Execution Gateway. Exactly
   one checkable service exists: this ASP.NET app. Health-checking absent
   services returns `unknown` forever, which is honest but not useful.

3. **The API/UI workspace taxonomies must be reconciled first.** `dashboard,
   helpdesk, security, certificates` (API) vs `dashboard, llm, agents, ops` (UI)
   is a schema-level contradiction. Phase 4 aggregation cannot be built over two
   disjoint id sets, and picking one silently would discard the other's intent.
