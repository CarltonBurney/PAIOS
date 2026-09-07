# SYSTEM_BASELINE.md

Filled by repo inspection on 2026-09-07 against commit `18ba1a6`, branch
`claude/docker-desktop-init-blocked-k7zwoa`. Every field reflects an actual
command run or file read. Where the answer is "none", that is a finding, not a
gap in the inspection.

## Application Entry Points

- Frontend entry: `apps/paios-command-center/wwwroot/index.html` (single static
  file, served by `UseStaticFiles`/`UseDefaultFiles`)
- Backend entry: `apps/paios-command-center/Program.cs` (20 lines, minimal API)
- Start command(s): `dotnet run --project Paios.CommandCenter.csproj`, or
  `scripts/start.ps1`, or `docker compose up --build`
- Port(s): 8080 (`ASPNETCORE_URLS`; container `EXPOSE 8080`). No other ports.

## Frontend

- Framework/version: **none** — hand-written HTML + inline `<style>` + one
  inline `<script>`. No React/Vue/Angular, no build step, no bundler, no
  package.json anywhere in the repo.
- Location: `apps/paios-command-center/wwwroot/index.html` (single file)
- Routing mechanism: client-side show/hide only. A click toggles
  `.workspace.active` and swaps `#page-title` innerHTML. No URL routing, no
  history, no deep links — reloading always returns to `dashboard`.
- Workspace route files: none exist. All four workspaces are `<section>`
  elements in the one file:
  - Command Center: `#workspace-dashboard`
  - Local LLMs: `#workspace-llm`
  - Agent Lab: `#workspace-agents`
  - Operations: `#workspace-ops`

## Backend / API

- Framework/version: ASP.NET Core minimal API, .NET 8 (`net8.0`)
- Location: `apps/paios-command-center/Program.cs`
- Route/controller files: none — all routes are inline lambdas in `Program.cs`
- Current exposed endpoints:
  - `GET /health` — ASP.NET health check, returns `Healthy` (text/plain)
  - `GET /api/workspaces` — returns a **hard-coded array of 4 anonymous
    objects**, no data source
  - `GET /` — static `index.html`
  - Everything else → 404 (verified: `GET /api/providers` → 404)

## Model Integrations Found

- Ollama references: **none found** (repo-wide grep for `ollama`, `11434`)
- OpenAI-compatible client references: **none found** — the only match for
  "OpenAI-compatible" is prose inside `index.html` describing an intention
- Other providers: none. `Paios.CommandCenter.csproj` declares **zero
  `PackageReference` entries** — no HTTP client library, no JSON client, nothing.

## Agent Definitions

- Source of truth location: **none**
- Schema (as found): none
- Count of existing agent records: **0**. The Agent Lab workspace shows three
  hard-coded labels (`GMP suite`, `IT helpdesk`, `SSL monitor`) that are literal
  HTML text, not records.

## Governance Modules

Repo-wide grep, code files vs markdown, counting occurrences:

| Term | Occurrences in code | Occurrences in this repo's docs |
|---|---|---|
| Governance Core | 0 | 0 |
| Tool Registry | 0 | 0 |
| Execution Gateway | 0 | 0 |
| Purpose Binding | 0 | 0 |
| Kill Switch | 0 | 0 |
| deny precedence | 0 | 0 |
| RBAC | 0 | 1 |

- Governance Core location: **does not exist**
- RBAC/Identity: does not exist
- Purpose Binding: does not exist
- Tool Registry: **not found**
- Execution Gateway: **not found**
- Policy evaluation / deny precedence: does not exist
- Audit trail mechanism: does not exist. "audit" appears 25 times in markdown,
  0 times in code — entirely aspirational prose.
- Only governance artifact present: `policies/sample-governance-policies.json`
  (2 policies, PS-001/PS-002, prose controls). **Not referenced by any code** —
  verified by grep. It is an inert document.

## Data Stores

- PostgreSQL: none (no connection string, no Npgsql, no EF Core)
- SQLite: none
- ChromaDB/Qdrant: none
- Other: **no persistence of any kind.** The application holds no state.

## Config / Environment

- .env or config file locations: **none.** No `appsettings.json`, no
  `appsettings.Development.json`, no `.env`, no `config/` directory.
  Configuration is entirely environment-variable-driven at the ASP.NET level
  (`ASPNETCORE_URLS`, `ASPNETCORE_ENVIRONMENT`).
- Secrets handling: not implemented (nothing to hold). `.gitignore` is
  well-prepared for secrets that do not yet exist.

## Identified Mocks / Placeholders

- **Entire frontend** — makes zero network calls. Verified: 0 matches for
  `fetch(`, `XMLHttpRequest`, `axios` in `index.html`. The UI cannot display
  live data by construction.
- **Command Center panels** — `04 workspaces`, `01 container`, `∞ local
  sessions` are literal HTML, not computed.
- **Local LLMs panels** — `01 chat workspace`, `02 context vault`: static text.
- **Agent Lab panels** — `GMP`, `IT`, `SSL`: static text.
- **Operations workspace** — no panels at all, just a "NEXT WIRING POINT" notice.
- **`GET /api/workspaces`** — hard-coded array; three of its four entries are
  explicitly labelled `"kind": "simulation"`.

## Test / Build Baseline

- Test command: **none exists.** No test project, no test file, no
  `package.json`, no `pytest.ini`. `dotnet test` fails: `MSB1003: Specify a
  project or solution file`.
- Pass count: **0** / Fail count: **0** (no tests to run)
- Lint result: no linter configured for C# or HTML in this repo
- Type-check result: n/a (covered by compilation)
- Build result: **`dotnet build` succeeds — 0 warnings, 0 errors**
- Nearest thing to a test suite: `tier1-remediation/scripts/validate_workflow.py`
  — a structural validator for the Logic Apps workflow JSON. Run: **14/14 checks
  passed.** It validates `tier1-remediation/`, which is unrelated to the
  Command Center.
- CI: `.github/workflows/validate-tier1-remediation.yml` is path-filtered to
  `tier1-remediation/**`. **No CI covers `apps/paios-command-center` at all.**

## Browser Validation (as of this inspection)

Server launched on `:8080`, routes exercised with curl.

- Command Center: renders. Static panels only. Shows a notice reading
  `BACKEND: /api/workspaces` — but never calls it.
- Local LLMs: renders. Static prose + 2 static panels. No provider state.
- Agent Lab: renders. 3 static labels. No agent records.
- Operations: renders. No panels, no health data — a placeholder notice only.
- Nav switching works (client-side class toggle). Page title swaps correctly.
- `GET /health` → `200 Healthy`. `GET /api/workspaces` → `200` + hard-coded JSON.

## Contradictions Between the Handoff Doc and the Repo

These must be resolved before Phase 1, because downstream specs assume them:

1. **"Preserve existing state: 87 tests passing"** — there are **zero tests** in
   this repository. Nothing was lost; they never existed here. If 87 passing
   tests exist, they are in a different repository or working copy.
2. **Workspace identity mismatch.** The UI nav uses `dashboard / llm / agents /
   ops`. `GET /api/workspaces` returns `dashboard / helpdesk / security /
   certificates`. These are **two disjoint taxonomies** sharing one id
   (`dashboard`). The API cannot back the UI as written — Phase 4's
   "aggregation" premise is broken at the schema level.
3. **"4 workspaces exist ... not wired to real data"** is accurate but
   understates it: they are not wired to *any* data. There is no client.
4. **Governance Core / Tool Registry / Execution Gateway** are treated by the
   handoff as existing subsystems to integrate with ("never bypass governance").
   They do not exist in this repository in any form — not code, not even
   documentation. Phase 2's exit criterion (trace one agent through the real
   governance path) is currently unreachable: there is no path to trace.
5. **The "never fabricate metrics" rule is already violated** by the shipped UI —
   `04 workspaces`, `∞ local sessions`, `GMP/IT/SSL` are fabricated display
   values. Fixing this is in-scope for Phase 4 and arguably Phase 0.

## Highest-Priority Gap Identified

The frontend has no HTTP client and the backend has no data layer, so **no
workspace can show real state until a client-to-API path exists at all** —
building provider detection before that would produce data nothing can render.
