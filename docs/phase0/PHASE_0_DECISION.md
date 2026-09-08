# PHASE_0_DECISION.md

Audit of Phase 0 against the explicit exit criteria, at commit `6677e43`.
Evidence for every row is in `SYSTEM_BASELINE.md`, `REPOSITORY_MAP.md`, and
`CURRENT_STATE_MATRIX.md`. "Absence confirmed" is a satisfied criterion, not a
skipped one.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Repository structure inspected | Met | Full `find`; 33 tracked files |
| 2 | Application entry points known | Met | `Program.cs`, `wwwroot/index.html` |
| 3 | Frontend/backend locations identified | Met | Both single files, same project |
| 4 | Current routes identified | Met | `/`, `/health`, `/api/workspaces`; all else 404 |
| 5 | Current APIs/services identified | Met | One service; `/api/workspaces` hard-coded |
| 6 | Agent source-of-truth identified | Met — **absence confirmed** | 0 records; 3 static HTML labels |
| 7 | Governance modules identified | Met — absence confirmed **in this repository** | 0 code occurrences, 0 doc occurrences |
| 8 | Tool Registry located or absence confirmed | Met — absence confirmed **in this repository** | 0 occurrences repo-wide |
| 9 | Execution Gateway located or absence confirmed | Met — absence confirmed **in this repository** | 0 occurrences repo-wide |
| 10 | Model integration points identified | Met — **absence confirmed** | grep: ollama/11434/openai/v1 models → prose only |
| 11 | Database/vector-store usage identified | Met — **absence confirmed** | No client, ORM, or connection string |
| 12 | Config/environment files identified | Met — **absence confirmed** | No appsettings/.env/config dir |
| 13 | Existing tests run | Met | `dotnet test` → MSB1003 (none exist); `validate_workflow.py` → 14/14 |
| 14 | Lint/type/build run where configured | Met | `dotnet build` → 0 warnings, 0 errors. No linter configured. |
| 15 | Application launched | Met | Kestrel on :8080 |
| 16 | Command Center browser-checked | Met | Renders; static panels; no fetch |
| 17 | Local LLMs browser-checked | Met | Renders; 2 static panels |
| 18 | Agent Lab browser-checked | Met | Renders; 3 static labels |
| 19 | Operations browser-checked | Met | Renders; no panels at all |
| 20 | Mock/placeholder/partial/real distinguished | Met | Per-component in `CURRENT_STATE_MATRIX.md` |
| 21 | Existing repository changes identified before new modifications | Met | See below |
| 22 | `SYSTEM_BASELINE.md` exists | Met | `docs/phase0/` + Drive |
| 23 | `REPOSITORY_MAP.md` exists | Met | `docs/phase0/` + Drive |
| 24 | `CURRENT_STATE_MATRIX.md` exists | Met | `docs/phase0/` + Drive |
| 25 | Highest-priority implementation gap known | Met | No client-to-API path exists |

## Criterion 21 — Pre-existing repository state

Established before any Phase 0 modification:

- Working tree **clean**, no stashes, no untracked files at session start.
- `main` is at `07457fc` ("Add PAIOS command center container scaffold").
- Branch `claude/docker-desktop-init-blocked-k7zwoa` carried **four** commits
  ahead of main before the Phase 0 docs were added — all from this session's
  earlier launcher/toolchain work, none touching application logic:
  - `a9a1ce3` Docker-optional launcher
  - `6d0c141` surface engine errors, document `/_ping` 500
  - `ebfbcb1` SessionStart toolchain hook (.NET 8 + PowerShell 7)
  - `18ba1a6` unanchor `bin/`/`obj/` in `.gitignore`
- Phase 0 added exactly one commit, `6677e43`, containing **only** the three
  baseline documents. **No application code was modified during Phase 0.**

## Architecture preserved

Nothing was refactored, renamed, or removed. `Program.cs`, `index.html`, the
`.csproj`, the Dockerfile, and `tier1-remediation/` are byte-identical to their
pre-Phase-0 state.

## Repository evidence conflicting with the shared specifications

Recorded per directive item 8. These are findings, not proposed spec changes —
specification ownership sits outside this repository.

1. **`LOCAL_LLM_ARCHITECTURE.md` says "add to existing API, do not create a
   parallel service layer."** There is no service layer. `Program.cs` is 20
   lines of inline lambdas with no DI registrations beyond `AddHealthChecks()`.
   Implementing the provider endpoints necessarily creates the first service
   layer this project has had.
2. **`LOCAL_LLM_CONFIGURATION.md` says to "pick whichever matches the existing
   repo convention found in Phase 0 inspection."** No configuration convention
   exists — no `appsettings.json`, no `.env`, no `config/`. A convention must be
   established rather than matched.
3. **The `.csproj` declares zero `PackageReference` entries.** Any provider
   adapter introduces the project's first external dependency.
4. **"87 tests passing" (session handoff) is false for this repository.** Zero
   tests exist. Nothing regressed; they were never here.
5. **Workspace identity is contradictory.** UI nav ids are
   `dashboard/llm/agents/ops`; `/api/workspaces` returns
   `dashboard/helpdesk/security/certificates`. Phase 4 aggregation is defined
   over sources that do not share a key.
6. **Phase 2's required proof is not reachable from this repository.** All six
   stages of the governance path are absent *here*. This establishes absence in
   this repository only — historical records describe NeuralGovernance v2.1 in
   the wider PAIOS/DGE estate, so the classification is **BLOCKED — EXISTING
   GOVERNANCE IMPLEMENTATION LOCATION UNRESOLVED**, pending an archaeology pass.
7. **The shipped UI already violates "never fabricate metrics."** `04
   workspaces`, `∞ local sessions`, `GMP/IT/SSL` are literal HTML.

## PHASE DECISION

**Phase 0 — COMPLETE.**

All 25 mandatory exit criteria satisfied. Repository truth is established to the
point that implementation can proceed without guessing: every subsystem named in
the phase plan can be traced to an exact file, or its absence is confirmed by a
recorded command. No global exit rule is violated — the build succeeds, no tests
were broken (none exist), no governance path was bypassed (none exists), no
secrets were introduced, and the changed files are enumerated above.

**Next repository-level action:** implement the Phase 1 provider layer against
`LOCAL_LLM_ARCHITECTURE.md` and `LOCAL_LLM_CONFIGURATION.md`, establishing the
service layer, configuration convention, and first test project that Phase 0
proved absent.
