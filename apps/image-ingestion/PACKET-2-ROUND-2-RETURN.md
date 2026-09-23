# Packet 2 round-2 implementation — ChatGPT takeover

The user authorized ChatGPT to implement the next section after Claude stopped at
`f82f100`. This unit implements B1–B5 and fixture-only checkpoint interfaces. No
cloud writer is enabled and no canonical contract file is changed.

## Changes

- **B1:** migration 0003 adds PostgreSQL server-start time to the defensive database
  fingerprint. `FixtureRuntime` is the composition entrypoint: every instance starts
  closed, independent of copied database rows. It exposes scoped repository adapters
  only after explicit closed-mode reconciliation. Every adapter call rechecks the
  serving epoch. Old adapters stop working after recovery. Earlier migrations are unchanged.
- **B2:** new searches and cursor continuations exclude currently quarantined assets
  by tenant/workspace/asset. Integrity suppression is the one intentional exception
  to stable snapshot membership.
- **B3:** verification includes all published reservations, even before revision 1.
  Recovery binds reservation and intent envelopes to their actual downloaded IDs.
  Wrong IDs quarantine the affected asset; malformed or conflicting evidence leaves
  recovery closed. Missing reservation evidence is no longer silently healthy.
- **B4:** continuation cursors preserve the original indexed_at and absolute expiry.
  Paging does not extend the snapshot beyond its retention lifetime.
- **B5:** `runtime.py` provides the exact IngestionRepository and IndexRepository
  signatures, plus gated AssetRepository and AuditRepository access. Object IDs and
  trusted service context are internal. `expected_version` is the current head:
  revision 1 uses 0. Replay still returns the original receipt. Projection application
  verifies the supplied receipt and row against committed records before using the
  durable outbox; it remains monotonic and idempotent.

The issued Reservation has no first-commit ID. Protocol callers therefore cannot
be required to discover an implementation-only reserved ID before calling commit.
Migration 0003 permits an unallocated first_commit_id in the private reservation
envelope. Protocol reservations use null; the first frozen intent's unique
asset/revision slot binds the caller's UUID. Existing non-null reservations retain
their original binding. This is a private preproduction envelope adjustment, not
a change to the immutable canonical 1.0.0 schemas/interfaces. No live records exist
to migrate; older private-envelope consumers must be upgraded with this unit.

## Recovery rule (supersedes automatic-resume suggestions in the R2 proposal)

The complete intent ledger and termination of old publication authority must be
established before resuming. If either is unknown, stay closed. A canary denial,
quiet window or elapsed timer never establishes those facts. Do not allocate new
revision slots after coordinator loss on the strength of such observations.

`FixtureRuntime.recover(complete_ledger=..., old_authority_terminated=...)` models
that controller decision in tests. These booleans are not cloud-fence evidence or
an approved production operator-signoff mechanism. A future live composition root
must bind recovery to authenticated evidence and keep its fresh serving authority
outside restored database state. Never serve requests by instantiating the internal
CoordinatorRepository directly. Restart/restoration occurs offline through the
composition gate. Real physical restore and provider tests remain deployment gates.

## Checkpoints

`CheckpointStore` defines append/latest; `FakeCheckpointStore` is in-memory only.
Fixture manifests include sequence, time, release and anchored historical marker
hashes, with a digest and keyed authentication. Verification compares the anchored
revision within recovered history, so a legitimate newer head is allowed. Missing,
regressed or different historical evidence is reported. No live backend, signing
key custody, independent administration or retention configuration is implemented.
Checkpoints are evidence; Obsidian/DGE remain canonical truth and OneDrive holds media.

## Validation

Local focused tests: 70 passed; 71 PostgreSQL tests collected and skipped because
this review machine has no configured disposable server. Added database regressions
run in the existing PostgreSQL 16 CI job, covering the same-database fresh-process
gate, quarantine between pages, cross-scope quarantine, reservation delete/edit
before/after acceptance, wrong object IDs, fixed cursor expiry, and protocol-only
reservation/commit/replay/projection calls. The checkpoint authentication/history
test runs in the Ubuntu/Windows matrix. CI results are reported with the delivery
commit; local skips are not database-pass evidence.

### Results on `587f1fd`

CI [run 35864765839](https://github.com/CarltonBurney/PAIOS/actions/runs/35864765839): all three jobs passed.

| Job | Result |
|---|---|
| Contracts and unit tests (ubuntu-latest) | **208 passed, 71 skipped.** All 71 skips are `tests/test_persistence_db.py`, which is skipped without a database. |
| Contracts and unit tests (windows-latest) | **207 passed, 72 skipped.** The 71 database tests plus the existing POSIX-only Packet 1 test. |
| Packet 2 coordinator tests (PostgreSQL 16) | Passed. The count line is not in the retrievable log tail; the same file set passes **140** locally (below). |

A local disposable PostgreSQL 16 run on the line-ending cleanup commit (code identical to `587f1fd`, verified with `git diff --ignore-cr-at-eol`) gives:
- **279 passed, 0 skipped** for the full suite with the database
- **208 passed, 71 skipped** without it
- **140 passed** for the coordinator job's four `tests/test_persistence_*.py` files

These counts overlap and must not be added together. The contract validator reports `contract_package_modified: false`.

The follow-up commit only converts the nine files this unit touched from Windows (CRLF) or mixed line endings to LF, matching the rest of the package. It has no content change. The contract package and ChatGPT's review documents keep their original line endings.

No paid resources, production writes, OCR/API module implementation, PR merge or
canonical promotion is part of this unit.

