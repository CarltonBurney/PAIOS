# GOVERNANCE_ARCHAEOLOGY.md

Phase 2 recovery pass, 2026-09-07. Searches every repository reachable on the
GitHub account plus the full commit history of each. Repository evidence wins
over historical description throughout.

**Headline: the governance kernel exists. It is `CarltonBurney/PAIOS` PR #2,
branch `claude/control-plane-implementation` — unmerged since 2026-08-23.
144 tests, verified passing in this session. It is Python, not TypeScript.**

## A. Reachable repository inventory

Enumerated authoritatively; no names guessed.

| # | Repository | Visibility | Last push | Files | TS | Py | Governance code? |
|---|---|---|---|---|---|---|---|
| 1 | `CarltonBurney/PAIOS` | public | 2026-09-07 | 33 on `main` | 0 | 1 | **yes — on an unmerged branch** |
| 2 | `CarltonBurney/Design-Group` | public | 2026-09-03 | 14 | 0 | 0 | no |
| 3 | `CarltonBurney/CarltonBurney` | public | 2026-06-02 | 1 | 0 | 0 | no (profile readme) |
| 4 | `CarltonBurney/AI-Neural-Governance` | public | 2026-06-02 | 7 | 0 | 0 | **no — docs only** |
| 5 | `CarltonBurney/AI-Neural-Governance-v1` | public | 2026-05-30 | 4 | 0 | 0 | **no — docs only** |
| 6 | `CarltonBurney/ssl-certificate-monitor` | public | 2026-05-29 | 4 | 0 | 1 | no |
| 7 | `CarltonBurney/enterprise-documentation-portfolio` | public | 2026-05-29 | 4 | 0 | 0 | no |
| 8 | `CarltonBurney/power-platform-automation-framework` | public | 2026-05-29 | 1 | 0 | 0 | no |
| 9 | `CarltonBurney/Free_Session_Data_Repository` | **private** | 2025-11-02 | 30 | 0 | 0 | no — `governance/` holds only `.gitkeep` |

Historical documentation said "PAIOS + 5 other repos". The account actually
holds **9** repositories. All were cloned and searched.

### Two findings that reframe the search

1. **The two repositories named for the governance framework contain no code
   at all.** `AI-Neural-Governance` (7 files) and `AI-Neural-Governance-v1`
   (4 files) are entirely Markdown plus one policy JSON sample. Their names are
   the strongest false lead in the estate.
2. **No TypeScript file has ever existed in any commit of any repository.**
   Verified by fetching history to depth 200 on every candidate and running
   `git log --all --diff-filter=D --name-only` for `.ts`/`.tsx` — zero results,
   including deleted paths. The historical "~65-file TypeScript project" is not
   recoverable from GitHub because it was never pushed there.

**The "~65 files" figure does match something exactly**: the
`claude/control-plane-implementation` branch tree contains **exactly 65 files**.
The count carried forward accurately; the language attribution did not.

## B. Component inventory

Every component is located in `CarltonBurney/PAIOS`, branch
`claude/control-plane-implementation` @ `4980c53`, unless stated otherwise.

| Component | Path | Lang | Impl status | Tests | Currency | Dependencies | Reusable as-is | Needs migration | Deprecated | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| **Policy engine / declarative merge** | `src/paios/policy.py` (424 ln) | Python | Complete | `tests/test_policy_decisions.py` (279 ln) | current | `models`, `audit` | **yes** | no | no | 57 deny/precedence refs; docstring states `deny > require_approval > allow_with_controls > allow` |
| **Deny precedence** | `src/paios/policy.py:9-10, 296` | Python | Complete | covered | current | — | **yes** | no | no | precedence comparison implemented at line 296 |
| **Risk classification L0–L4** | `src/paios/risk.py` (214 ln), `policies/risk-model.json` | Python + JSON | Complete | covered | current | `models` | **yes** | no | no | 104 L0–L4 refs; two-axis `risk_level` + `risk_domains` |
| **Risk domains** | `src/paios/risk.py`, `policies/risk-model.json` | Python + JSON | Complete | covered | current | — | **yes** | no | no | 43 refs; non-exclusive domain set |
| **Tool Registry** | `src/paios/tools.py` (386 ln), `policies/tool-registry.json` | Python + JSON | Complete | `test_registry_lifecycle.py` (581 ln) | current | `registry` | **yes** | no | no | 44 refs |
| **Execution Gateway** | `src/paios/execution.py` (319 ln) | Python | Complete | `test_execution_gateway.py` (388 ln) | current | `tools`, `policy`, `audit` | **yes** | no | no | 19 refs; independent re-validation |
| **Governance Core (control plane)** | `src/paios/control_plane.py` (225 ln) | Python | Complete | `test_control_plane.py` (280 ln) | current | all | **yes** | no | no | full pipeline auth→identity→authz→classify→risk→policy→approval→execute→audit |
| **Governed registry substrate** | `src/paios/registry.py` (1065 ln) | Python | Complete | `test_registry_lifecycle.py` | current | `models` | **yes** | no | no | lifecycle states, immutable versions, burned ids |
| **Registry governance / mutation gate** | `src/paios/registry_governance.py` (174 ln), `policies/registry-policies.json` | Python + JSON | Complete | `test_registry_governance.py` (356 ln) | current | `policy` | **yes** | no | no | separation of duties enforced post-policy |
| **AuditTrail** | `src/paios/audit.py` (131 ln) | Python | **Partial** | covered | current | — | mostly | **yes — no hash chaining** | no | append-only, correlation ids, 17 stages, pluggable sinks. **No SHA-256 chain.** |
| **Classification** | `src/paios/classification.py` (188 ln) | Python | Complete | covered | current | — | **yes** | no | no | deterministic + optional model assist |
| **Routing** | `src/paios/routing.py` (140 ln) | Python | Complete | covered | current | `providers` | **yes** | no | no | — |
| **Model providers** | `src/paios/providers/` (192 ln) | Python | Complete | covered | current | Azure SDK (optional) | **yes** | no | no | mock + Azure AI Foundry |
| **SHA-256 audit chaining** | — | — | **absent** | — | — | — | no | — | — | 0 occurrences of `sha256`/`SHA-256` in any repository |
| **KillSwitch** | — | — | **absent** | — | — | — | no | — | — | 0 code occurrences estate-wide |
| **AgentCredential** | — | — | **absent** | — | — | — | no | — | — | 0 code occurrences estate-wide |
| **PurposeBind** | — | — | **absent** | — | — | — | no | — | — | 1 incidental word match, no construct |
| **Governance docs** | `AI-Neural-Governance/docs/*.md` | Markdown | Docs only | n/a | 2026-06 | — | reference only | — | no | 7 files, all prose |

## C. Target classification

| Target | Classification | Location |
|---|---|---|
| Governance Core / kernel | **FOUND — CURRENT** | PR #2 `control_plane.py` |
| Tool Registry | **FOUND — CURRENT** | PR #2 `tools.py` + `policies/tool-registry.json` |
| Execution Gateway | **FOUND — CURRENT** | PR #2 `execution.py` |
| Current policy engine | **FOUND — CURRENT** | PR #2 `policy.py` + `policies/policy-rules.json` |
| L0–L4 / risk domains | **FOUND — CURRENT** | PR #2 `risk.py` + `policies/risk-model.json` |
| Deny precedence | **FOUND — CURRENT** | PR #2 `policy.py` |
| Declarative policy merge | **FOUND — CURRENT** | PR #2 `policy.py` (least-privilege union/intersect) |
| AuditTrail | **FOUND — REQUIRES MIGRATION** | PR #2 `audit.py` — present but not hash-chained |
| AuditTrail v2.0 / SHA-256 chaining | **NOT FOUND** | 0 occurrences estate-wide |
| KillSwitch | **NOT FOUND** | 0 occurrences estate-wide |
| AgentCredential | **NOT FOUND** | 0 occurrences estate-wide |
| PurposeBind | **NOT FOUND** | 0 code occurrences estate-wide |
| NeuralGovernance v2.1 (as named) | **DOCUMENTED ONLY** | name appears only in prose |
| ~65-file TypeScript project | **NOT FOUND** | no `.ts` in any commit of any repo, ever |
| 51 modules / 11 layers | **NOT FOUND** | PR #2 has 17 modules; no layer taxonomy |
| `paios_router.py` | **NOT FOUND** | nearest is `src/paios/routing.py` |
| `AI_ROUTING_RULES.json` | **NOT FOUND** | nearest are `policies/*.json` |
| PAIOS MCP server implementations | **NOT FOUND** | 0 occurrences |
| AXIS / LEDGER / RELAY | **NOT FOUND** | 0 occurrences, code or docs |
| SIGNAL / WIRE | **NOT FOUND** | only incidental English usage |

## D. Reconciling the historical record

The historical description is **partly accurate and partly wrong**, and the
inaccurate parts are what made the kernel hard to find:

| Historical claim | Reality | Verdict |
|---|---|---|
| ~65-file project | branch tree is exactly 65 files | **accurate** |
| Governance framework exists | it does, and passes 144 tests | **accurate** |
| Tool Registry, Execution Gateway, policy engine, L0–L4, deny precedence | all present and tested | **accurate** |
| Written in TypeScript | it is Python; no `.ts` ever committed | **wrong** |
| 51 modules, 11 layers | 17 modules, no layer taxonomy | **wrong** |
| Named "NeuralGovernance v2.1" | name appears in no code | **wrong** |
| AuditTrail v2.0 with SHA-256 chaining | audit exists, chaining does not | **partly wrong** |
| KillSwitch, AgentCredential, PurposeBind | none implemented | **wrong** |
| Associated with `PAIOS` + other repos | governance lives only in `PAIOS` | **partly wrong** |

The likeliest explanation is that the historical note conflates a *design
intent* — plausibly the prose in `AI-Neural-Governance/docs/` — with the
implementation that was actually built. Names, language and module counts came
from the aspirational document; the file count came from the real branch.

## E. Recommendation

**WIRE EXISTING**, then a small targeted build.

A ground-up build is not justified and would discard ~4,000 lines of tested,
reviewed governance code that already satisfies most of Phase 2's exit criteria.

Ordered:

1. **Merge PR #2.** It is green, mergeable, not a draft, and has waited since
   2026-08-23. It is the single highest-value action available in this project.
2. **Resolve the `Program.cs` collision first.** PR #2 and PR #4 both modify
   `apps/paios-command-center/Program.cs` and `wwwroot/index.html`. PR #2 carries
   the *pre-Phase-1* versions — merging it after PR #4 without care would revert
   the provider layer and restore the old workspace ids. Merge order matters,
   or PR #2 needs a rebase onto PR #4.
3. **Wire Agent Lab to the merged control plane.** Phase 2's exit — trace one
   agent through identity → purpose → policy → tool registry → execution gateway
   → audit — becomes reachable: `control_plane.py` already implements that exact
   pipeline. The remaining work is an HTTP surface and a UI, not a kernel.
4. **Then build only what is genuinely missing**, each small and additive:
   KillSwitch, AgentCredential, PurposeBind as a named construct, and SHA-256
   chaining over the existing `AuditEvent` stream.

**Cross-language note.** The kernel is Python; the Command Center is .NET. PR #2
states PAIOS "is a library" with "no HTTP surface". Bridging them needs a
decision: expose the Python control plane over HTTP for the .NET app to call, or
run it as a sidecar. That is an architecture decision for the owner, not
something to assume.

## F. Search method

- All 9 repositories cloned; every one searched, not sampled.
- 29 target terms searched across `.ts .tsx .js .py .cs .json .sql .ps1 .sh`
  (code) and `.md .mmd` (docs), separately, so documentation could never be
  mistaken for implementation.
- History fetched to depth 200 on every candidate; `--diff-filter=D` used to
  find deleted TypeScript. None existed.
- All PAIOS remote branches enumerated — this is what surfaced the kernel, which
  a default-branch-only search would have missed entirely.
- **PR #2's suite was executed, not trusted**: `144 passed in 0.18s`.

## G. Incidental finding, outside Phase 2 scope

`CarltonBurney/Free_Session_Data_Repository` tracks `db/docker/.env` alongside
`.env.example`. A committed `.env` in a private repository is still a credential
in version control. Worth reviewing separately; not touched by this pass.
