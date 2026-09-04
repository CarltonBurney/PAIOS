# Repository Status

Engineering status snapshot for the PAIOS repository. This document tracks what
is merged, what is in flight, and what is blocked. It is scoped to the contents
of this repository — it intentionally records no account, credential, or
personal data.

**Snapshot date:** 2026-09-04
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
| CI | Partial | `.github/workflows/validate-tier1-remediation.yml` validates the Tier-1 workflow only. Control-plane CI exists on PR #2's branch but is not yet on `main`. |

`main` currently contains documentation, one reference workflow and its
validation job, and the Command Center scaffold. The governance control plane
itself is not on `main` — it is still in PR #2.

---

## In flight

### PR #2 — Governance control plane

Branch `claude/control-plane-implementation`. Adds the request lifecycle,
two-axis risk model, declarative policy engine, governed registry framework,
tool registry, and execution gateway: 40 files, ~8,170 added lines, 144 tests.

**Status: clean, no conflicts, ready for review.** The earlier `.gitignore`
add/add conflict was resolved by merging `main` into the branch (`5f34d6f`); the
branch now contains `main` at `07457fc`. Its `.gitignore` carries `main`'s
content plus `*.egg-info/`, `build/` and `*.jsonl`.

The branch also adds `.github/workflows/validate-control-plane.yml` (`4980c53`),
which runs on changes to `src/`, `tests/`, `policies/` or `pyproject.toml`:
ruff over the repository, the full pytest suite on Python 3.12, and a parse
check over every file in `policies/`. That last gate exists because the policy
engine, risk model and tool registry are configuration rather than code — a
malformed JSON file there disables governance at load time rather than failing
review.

Note the PR's own description still carries a "No CI" caveat written before that
workflow was added. The workflow is on the branch; the description is stale.

### PR #4 — Docker-optional launcher and session toolchain

Branch `claude/docker-desktop-init-blocked-k7zwoa`. 6 files, ~379 added lines.
**Status: clean, draft, no conflicts.**

Adds `apps/paios-command-center/scripts/start.ps1`, a launcher that resolves
`docker.exe` when a Docker Desktop restart has dropped it from the shell PATH,
probes engine health under a hard per-call timeout rather than blocking on a
half-started engine, and falls back to the local .NET 8 SDK when the engine
never becomes healthy. Adds a SessionStart hook installing the .NET SDK and
PowerShell 7 so the app can be built and run in a Claude Code web session, and
unanchors `bin/`/`obj/` in `.gitignore`.

No application code changes; both runtimes serve the same `/`, `/health` and
`/api/workspaces` routes.

---

## Known gaps

These are stated so they are not mistaken for oversights.

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
- **The Docker-healthy path is still unverified.** PR #4 tests the .NET
  fallback, the CLI-resolution path and the unreachable-engine timeout, but no
  environment in that work had a working Docker daemon, so `docker compose up`
  itself remains unexercised for both the Command Center and PR #2's dev stack.
  Windows PowerShell 5.1 is likewise untested; the 5.1-sensitive constructs are
  guarded but unproven.
- **Knowledge base integration is absent.** The framework describes an Obsidian
  knowledge base as part of the stack. No integration code, export tooling, or
  vault interface exists in this repository — earlier export material was
  removed prior to public release and was never replaced with a governed
  integration path.

---

## Suggested order of work

1. Review and merge PR #2. It is clean and carries its own CI; merging it is
   what puts the control plane and its enforcement on `main`.
2. Review and merge PR #4. Both merge orders were verified conflict-free.
3. Verify the Docker-healthy path once a working daemon is available.
4. Decide how the Command Center shell and the control plane connect, before
   either grows further in its own direction.
5. Pick up persistence or the remaining registries as separate slices.
