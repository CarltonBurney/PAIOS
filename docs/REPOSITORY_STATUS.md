# Repository Status

Engineering status snapshot for the PAIOS repository. This document tracks what
is merged, what is in flight, and what is blocked. It is scoped to the contents
of this repository — it intentionally records no account, credential, or
personal data.

**Snapshot date:** 2026-09-03
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
| CI | Partial | `.github/workflows/validate-tier1-remediation.yml` validates the Tier-1 workflow only |

`main` currently contains documentation, one reference workflow and its
validation job, and the Command Center scaffold. The governance control plane
itself is not on `main` — it is still in PR #2.

---

## In flight

### PR #2 — Governance control plane

Branch `claude/control-plane-implementation`. Adds the request lifecycle,
two-axis risk model, declarative policy engine, governed registry framework,
tool registry, and execution gateway: 38 files, ~8,100 added lines, 144 tests.

**Status: blocked on a merge conflict.** The branch was cut from `a4cd573`;
`main` has since advanced to `d078b60` via the Tier-1 merge.

**Conflict scope: one file.** `.gitignore`, an add/add conflict. No source file,
policy file, or document conflicts.

Resolution: take the `main` version as the base — it already covers
`__pycache__/`, `*.py[cod]`, `.venv/`, `venv/`, `.pytest_cache/`, `.ruff_cache/`
and `dist/` — and append the three entries it does not carry:

```
*.egg-info/
build/
*.jsonl
```

Merging `main` into the branch and resolving that single file restores
mergeability. No history rewrite is required.

---

## Known gaps

These are stated so they are not mistaken for oversights.

- **No CI for the control plane.** The only workflow validates the Tier-1
  remediation JSON. PR #2's 144 tests and lint run locally but no check
  executes them on a pull request. A Python test/lint workflow is needed before
  the control plane merges, or the suite becomes unverified on `main`.
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
- **Dev stack is unverified.** `docker compose config` validates, but first
  bring-up has never been run against a live Docker daemon.
- **Knowledge base integration is absent.** The framework describes an Obsidian
  knowledge base as part of the stack. No integration code, export tooling, or
  vault interface exists in this repository — earlier export material was
  removed prior to public release and was never replaced with a governed
  integration path.

---

## Suggested order of work

1. Resolve the `.gitignore` conflict on PR #2 and get it mergeable.
2. Add a Python CI workflow (ruff + pytest) so PR #2's suite is enforced.
3. Merge PR #2.
4. Decide how the Command Center shell and the control plane connect, before
   either grows further in its own direction.
5. Pick up persistence or the remaining registries as separate slices.
