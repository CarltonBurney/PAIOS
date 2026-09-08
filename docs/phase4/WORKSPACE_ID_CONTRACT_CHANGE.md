# WORKSPACE_ID_CONTRACT_CHANGE.md

Breaking change to `GET /api/workspaces`, closed 2026-09-07.

## The change

| | Before | After |
|---|---|---|
| ids | `dashboard`, `helpdesk`, `security`, `certificates` | `dashboard`, `llm`, `agents`, `ops` |
| `kind` | `local`, `simulation` ×3 | `aggregate`, `providers`, `not_implemented` ×2 |

## Why

Phase 0 established that the API and the UI used **disjoint id sets** sharing
one member (`dashboard`). The nav rail's `data-workspace` attributes were
`dashboard/llm/agents/ops`; the API returned
`dashboard/helpdesk/security/certificates`. Nothing reconciled them because
nothing consumed the API at all — the frontend made no HTTP requests.

Phase 4 aggregation is defined over subsystem ids that must match the workspace
a card drills into. Two disjoint sets cannot both be that key. The nav ids won
because they name the actual architecture — Local LLMs, Agent Lab, Operations —
while `helpdesk`/`security`/`certificates` named three *simulations* that were
never built and had no backing data.

## Consumer search

Searched before closing, across `.cs .html .js .json .py .md`, the full working
tree, and every remote branch.

| Consumer | Result |
|---|---|
| `wwwroot/index.html` | uses the **new** ids (`data-workspace`), unchanged by this |
| `Program.cs` | the definition itself |
| `README.md` | describes the endpoint generically, no ids |
| `docs/phase0/*` | historical records that *document* the old ids as a finding — correct as-is, not consumers |
| Literal `"helpdesk"` / `"security"` / `"certificates"` in code | **zero occurrences** |
| Other repositories in the estate | zero references to `/api/workspaces` |

**No broken consumers. Nothing required migration or a compatibility shim.**

## One real collision, on another branch

`origin/claude/control-plane-implementation` (PR #2) still carries the
**pre-Phase-1** `Program.cs` and `index.html`, which contain the old ids. This
is not a broken consumer — it is a **merge conflict waiting to happen**.

Merging PR #2 after PR #4 without care would revert the provider layer and
restore the retired ids. Either merge PR #4 first and rebase PR #2 onto it, or
resolve both files deliberately in favour of the Phase 1–4 versions.

Recorded in `docs/PAIOS_OPERATIONAL_CORE_STATUS.md` as a continuation blocker.

## Status

**CLOSED.** Contract change documented, no consumers to migrate, one
cross-branch collision recorded for the merge.
