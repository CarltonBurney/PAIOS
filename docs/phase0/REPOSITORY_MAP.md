# REPOSITORY_MAP.md

Filled by inspection on 2026-09-07 at commit `18ba1a6`. The template's assumed
directories (`/frontend`, `/backend`, `/agents`, `/governance`, `/tools`,
`/execution`, `/db`, `/tests`, `/config`) are listed with their real status —
**nine of the ten do not exist.**

| Path | Responsibility | Real / Mock / Partial | Notes |
|---|---|---|---|
| `apps/paios-command-center/` | The entire application | **Partial** | 1 source file + 1 HTML file. Builds and runs. |
| `apps/paios-command-center/Program.cs` | Backend, all routes | **Partial** | 20 lines. `/health` real; `/api/workspaces` hard-coded. |
| `apps/paios-command-center/wwwroot/index.html` | Entire frontend | **Mock** | Static shell. Zero network calls. |
| `apps/paios-command-center/scripts/` | Launcher + runbook | **Real** | `start.ps1`, verified working (see PR #4). |
| `tier1-remediation/` | Logic Apps reference impl | **Real (as a spec)** | Workflow JSON + validator. Placeholder tenant GUIDs by design. |
| `tier1-remediation/scripts/validate_workflow.py` | Structural validator | **Real** | 14/14 checks pass. Only executable verification in the repo. |
| `policies/sample-governance-policies.json` | Governance policy sample | **Mock** | 2 prose policies. Not read by any code. |
| `docs/` | Concept documentation | **Real (prose)** | 8 documents. Describes intent, not implementation. |
| `architecture/` | Diagrams + workflows | **Real (prose)** | Markdown/Mermaid only. |
| `.github/workflows/` | CI | **Partial** | One workflow, path-filtered to `tier1-remediation/**`. |
| `.claude/` | Session toolchain hook | **Real** | Installs .NET 8 + PowerShell 7 on web sessions. |
| `/frontend` or `/app` | — | **Does not exist** | Frontend is one file under `wwwroot/`. |
| `/backend` or `/api` | — | **Does not exist** | Backend is one file, `Program.cs`. |
| `/agents` | — | **Does not exist** | No agent records anywhere. |
| `/governance` | — | **Does not exist** | No governance code anywhere. |
| `/tools` (Tool Registry) | — | **Does not exist** | — |
| `/execution` (Execution Gateway) | — | **Does not exist** | — |
| `/db` or `/models` | — | **Does not exist** | No persistence of any kind. |
| `/tests` | — | **Does not exist** | Zero tests repo-wide. |
| `/config` | — | **Does not exist** | No appsettings, no .env. |

## Critical Files

- [x] `apps/paios-command-center/Program.cs` — the only backend. Every API
      change starts here. Currently has no service layer, no DI registrations
      beyond `AddHealthChecks()`, and no configuration binding.
- [x] `apps/paios-command-center/wwwroot/index.html` — the only frontend.
      Any real data display requires adding a fetch layer here first.
- [x] `apps/paios-command-center/Paios.CommandCenter.csproj` — **zero
      PackageReference entries.** Any HTTP-calling provider adapter will be the
      first dependency the project has ever had.
- [x] `tier1-remediation/workflows/tier1-remediation.json` — 35-action Logic
      Apps definition, guarded by CI. Do not hand-edit; the validator enforces
      name uniqueness, `runAfter` resolution, and secret hygiene.

## Test Locations

- [x] **None for the Command Center.** No test project exists.
- [x] `tier1-remediation/scripts/validate_workflow.py` — structural validation
      of the workflow JSON only. Run with `python3`, no dependencies.

## Documentation Locations

- [x] `docs/` — 8 concept documents (governance overview, control plane,
      roadmap, M365 integration strategy, use cases)
- [x] `architecture/` — system architecture and governance workflow diagrams
- [x] `apps/paios-command-center/README.md` — run instructions
- [x] `apps/paios-command-center/scripts/README.md` — Docker Desktop recovery
- [x] `docs/phase0/` — this baseline set

## Directories NOT to touch without explicit reason

- [x] `tier1-remediation/` — the only CI-guarded area. Edits trigger the
      validation workflow and can break a working reference implementation.
- [x] `.github/workflows/` — changing path filters silently changes what CI
      covers.
- [x] `docs/` and `architecture/` — narrative documents that appear to be
      portfolio/reference material; they describe intent and should not be
      rewritten to match implementation without a decision to do so.

## Structural Observation for Phase 1

The repository has **two executable files total** (`Program.cs`,
`validate_workflow.py`). There is no service layer, no DI structure beyond the
default builder, no configuration system, and no HTTP client dependency. The
Local LLM spec (`LOCAL_LLM_ARCHITECTURE.md`) assumes an existing API to "add to"
and an existing config convention to match — **neither exists.** Phase 1 will be
creating those foundations, not extending them.
