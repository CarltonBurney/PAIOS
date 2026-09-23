# Packet 2, Unit 1 Return: Coordinator Database Foundation and Fakes

This is the first of the two reviewable units requested in [PACKET-2-ARCHITECTURE-DECISION.md](proposals/PACKET-2-ARCHITECTURE-DECISION.md). The second unit, the revised publication and recovery proposal, is [proposals/PACKET-2-PUBLICATION-RECOVERY-REVISION.md](proposals/PACKET-2-PUBLICATION-RECOVERY-REVISION.md). No live publisher is built until it is reviewed.

| Item | Value |
|---|---|
| Contract release | 1.0.0. The package is unchanged. |
| Implementation commit | The commit that adds this file on `claude/modest-hawking-qx0faa` (PR #6) |
| Evidence type | **Fixture only.** Tests use a disposable PostgreSQL 16 plus in-memory Drive and Graph fakes. No cloud resources, credentials, live writes or production records. |
| Result | 207 tests pass locally with a database: 138 from Packet 1 plus 69 new. Without `PAIOS_TEST_DATABASE_URL`, the 36 database tests are skipped. A new CI job runs them against a `postgres:16` service container. |

## What was built

| Component | Location |
|---|---|
| Migration: scope-keyed tables, append-only triggers, row-level security, publisher generation | `src/paios_ingestion/persistence/migrations/0001_foundation.sql` |
| Least-privilege grants for `paios_app` and `paios_ops` (roles are created at deployment) | `src/paios_ingestion/persistence/migrations/grants.sql` |
| Migration runner with checksums, so an edited migration is refused | `src/paios_ingestion/persistence/migrate.py` |
| Canonical JSON, bundle identity hash, request digest | `src/paios_ingestion/persistence/canonical.py` |
| Three-record validator enforcing all the normative invariants | `src/paios_ingestion/persistence/validation.py` |
| Repositories: reservations, frozen intents, heads, revisions, jobs, content lookup, outbox, search, recovery fencing | `src/paios_ingestion/persistence/repository.py` |
| Drive and Graph fakes with fault injection | `src/paios_ingestion/persistence/fakes.py` |

**Not built:** the DGE marker publisher, the OneDrive writer, any cloud client, the reconciler, erasure and retention jobs. `tests/persistence_helpers.TestPublisher` is a test-only driver for the Drive fake, used to exercise the database side end to end. It is not the production publisher.

## How the decisions and review items are reflected

| Item | Implementation | Tests (`tests/test_persistence_*.py`) |
|---|---|---|
| D1 PostgreSQL coordinator | Unique scope-aware keys and transactions. Compare-and-set on `registry_heads`, moving N−1 to N. | `test_one_commit_per_revision_slot`, `test_stale_expected_version_is_rejected` |
| D2 search | Case-insensitive **literal** substring via `strpos(lower(..))`, so `%` and `_` are never wildcards and there is no tokenizing. Exact tag and status filters; predicates combine with AND. Sort is `updated_at` descending then `asset_id` ascending. The HMAC-signed cursor pins the snapshot, query and scope, and expires. | `test_search_semantics`, `test_search_pagination_pins_the_snapshot`, `test_bad_cursors_are_invalid_requests` |
| D5 forks | A revision slot can hold only one commit (the primary key), and a slot is never reallocated. | `test_one_commit_per_revision_slot`; the takeover test ends by trying a rival commit |
| D6 retries | The outbox uses durable retry with exponential backoff and jitter, a bounded number of attempts, then a `dead` state. Expired claims are reclaimed. | `test_failed_projection_backs_off_and_goes_dead`, `test_crashed_projector_claim_is_reclaimed` |
| D8 identities | `paios_app` has row-level security, no DELETE or TRUNCATE, and no UPDATE on history tables. `paios_ops` has BYPASSRLS with the same write limits. Every scoped query also filters on tenant and workspace explicitly, so the BYPASSRLS role cannot read across scopes either. | `test_other_scopes_cannot_see_or_reuse` (both roles, plus raw SQL with no scope set) |
| P2-A frozen intents | `freeze_intent` stores the exact canonical payload bytes, the four pre-generated object IDs and the exact marker bytes (including `published_at`). It checks every marker field against the bundle and the previous marker's SHA. Re-freezing identical bytes is a no-op; different bytes for the same commit give `INTEGRITY_FAILED`. `bundle_sha256` covers the payload digests and slot and excludes the marker, so nothing hashes itself. | `test_identical_freeze_replays_and_changed_bytes_conflict`, `test_marker_must_match_the_frozen_bundle` ×4, `test_non_canonical_payload_bytes_are_rejected`, `test_drive_lost_response_retries_with_the_same_ids` |
| P2-B recovery fencing | `enter_recovery` closes admissions and publication and advances the generation. Work from an older generation can no longer mark publishing or record a publication. `adopt_intent` moves only ownership; the bytes and IDs never change. `record_published` holds a share lock on `publisher_state` until it commits, so recovery can't start between an old writer's check and its commit. | `test_recovery_fences_an_old_writer_and_the_new_generation_takes_over`, `test_recovery_waits_for_an_in_flight_commit` |
| P2-C stale head | `mark_publishing` is the last database step before the marker upload. While an intent is `publishing`, `get()` returns a retryable `PERSISTENCE_UNAVAILABLE` (503) instead of the older head. | `test_get_refuses_a_possibly_stale_head_while_publishing`; the crash tests also check that `get()` gives 503, not 404 |
| P2-D verification | The fakes model its requirements: Graph reports `quickXorHash` only, versions are pinned, a pruned version gives NotFound (never a fallback to latest), and content can change between upload and verification. Drive reports `sha256Checksum`. The writer that applies these rules is **not** built; it belongs to the publisher unit that follows the revised proposal. | `test_graph_reports_no_sha256_and_pins_versions`, `test_graph_conflict_behavior_and_faults` |
| P2-E append-only | Grants mean runtime roles can't update, delete or truncate history. A trigger also rejects changes from the table owner and superusers. The Drive fake shows that content restrictions can be lifted by any editor, so they only support detection. | `test_runtime_roles_cannot_rewrite_history` ×5, `test_append_only_trigger_also_stops_the_owner`, `test_drive_content_restriction_is_not_immutability` |

### Acceptance rows covered at the database level

| Row | Coverage |
|---|---|
| A01 | Publish chain and reads: `test_chain_publishes_and_reads_latest` |
| A02 | Replay, conflict, per-scope keys and concurrent reservation: `test_reservation_replay_and_conflict`, `test_concurrent_reservations_allocate_ids_once` |
| A03 | Revision-slot race and stale writer |
| A04 | Crash injection at four points inside the visibility transaction leaves nothing visible, and recovery completes with exactly one audit event. |
| A05 | Receipt replay; a replay with a different marker gives `INTEGRITY_FAILED` |
| A07 | GET stays current while search lags; projection is monotonic |
| A16 | Reuse lookup by SHA plus fingerprint; a different fingerprint gives no reuse |
| A17 | Scope isolation |
| A20 | Append-only enforcement |
| A21 | The validator: 26 rule tests, including hashes, chain, status, pages, OCR, search text and relationships |

A06, A08, A18 and A19 need the publisher and cloud adapters, so they stay open.

## Honest limits

- **Privileged administrators can still bypass.** Triggers and grants stop runtime roles and accidents. A superuser or table owner can disable a trigger. That is an administrative trust boundary, not immutability, and the P2-E section of the revised proposal defines it for production.
- **Fencing stops at the database.** A stale writer can't *record* a publication, but nothing here prevents it from *uploading* its (identical, frozen) marker bytes to Drive. Enforcing the fence at the Drive boundary is part of the P2-B revision.
- **`paios_app` can update heads, intents, reservations and jobs.** It needs to, to record publication. Moving these writes behind `SECURITY DEFINER` functions would narrow that further, and it can be done if you want it.
- **Search does a scan.** `strpos` search scans the scope's rows. That's fine at archive scale; a `pg_trgm` index could keep the literal-substring behaviour at larger scale.
- **`quickXorHash` is only partly verified.** It follows Microsoft's published algorithm but is checked only against the empty-input vector. It's an extra check; D4 requires the remote SHA-256 read-back anyway.
- **The cursor secret needs a home.** It has to come from the deployment's secret store, and nothing here defines that.

## Reproduction

```bash
python tools/validate_image_ingestion_contracts.py
cd apps/image-ingestion && python -m pip install -e '.[test]'
python -m pytest -q                      # database tests skip without a database
PAIOS_TEST_DATABASE_URL='host=localhost port=5432 user=postgres password=postgres dbname=postgres' \
  python -m pytest -q tests/test_persistence_db.py
```

The admin connection must be allowed to create databases and roles. Each run creates, and then drops, a throwaway database called `paios_test_<random>`.

---

# Round 1 resubmission (responds to PACKET-2-REVIEW-ROUND-1.md)

| Item | Value |
|---|---|
| Contract release | 1.0.0. The package is unchanged; the validator reports `contract_package_modified: false`. |
| Commits | `8256bf7` U4/U5 · `491e878` strict envelopes · `cfb1110` U1–U3, migration 0002, test-adapter publisher and reconciler · the commit that adds this section (R2/R4 revision, multipart guard, docs) |
| Evidence type | **Fixture only**: PostgreSQL 16 and the in-memory Drive fake. No provider test has been run and there is no live-cloud evidence. |
| Packet 1 | Accepted by the review. These changes don't touch Packet 1 code, and its tests are unchanged and still pass. |

## U1–U5 disposition

| Finding | Disposition | Regression tests |
|---|---|---|
| **U1** Recovery didn't block reads | **Fixed.** All authoritative reads and admissions go through `serving_ready()`. It requires `mode = 'open'` **and** a stored fingerprint that equals `cluster_fingerprint()` (system identifier, current WAL timeline and database OID). The gated reads are `get`, `get_job`, `list_events`, `lookup_commit`, `find_reusable`, `search` and `reservation_pins`. `enter_recovery` clears the fingerprint. Migration 0002 starts every install closed. A restored copy still says `open` but has a different fingerprint, so it serves nothing until it is reconciled. Scope predicates are unchanged: a closed coordinator answers 503 before scope is considered, and an open one still answers 404 for other scopes. | `test_recovery_closes_reads_of_existing_assets_and_jobs`, `test_restored_database_starts_closed_until_reconciled`, `test_fresh_install_starts_closed`, `test_resume_is_refused_until_uncertain_intents_are_resolved`, `test_other_scopes_cannot_see_or_reuse` |
| **U2** Separate reads could return an obsolete head | **Fixed.** `get` and `get_job` run in one read-only REPEATABLE READ transaction, so readiness, quarantine, head and uncertainty all come from one snapshot. A test barrier sits between the head read and the uncertainty read. | `test_publication_between_reads_never_yields_the_obsolete_head[get]` and `[get_job]` place `record_published(N)` at the barrier, with N's marker already in the DGE, and accept N or a retryable 503. **Mutation check:** with REPEATABLE READ removed, both fail with "an obsolete head was returned". |
| **U3** Search cursor lost members when assets updated | **Fixed.** Search rows are versioned: `created_xid` and `superseded_xid` are stamped by the projecting transaction. Page 1 captures `pg_current_snapshot()`. Every page filters with `pg_visible_in_snapshot`, so visibility follows commit semantics rather than wall-clock time. The cursor is bound to the generation and the fingerprint. Superseded versions are pruned only below a horizon that no unexpired cursor can see; an older cursor is refused, never answered with missing rows. | `test_search_snapshot_survives_updates_to_unseen_assets[title/status/tag]`, `test_projection_in_flight_at_snapshot_creation_stays_invisible` (projection open when page 1 is taken, committed before page 2), `test_pruning_keeps_versions_for_live_cursors_and_rejects_pruned_snapshots`, `test_cursors_do_not_survive_recovery`, `test_search_pagination_pins_the_snapshot`. **Mutation check:** with the superseded-snapshot predicate removed, six tests fail. |
| **U4** Completed ingestion could omit OCR | **Fixed.** A `completed` status requires a non-null OCR result that is `completed`, `skipped` or `reused`. Disabled OCR is a `skipped` result. | `test_completed_ingestion_without_ocr_result_is_rejected` (the exact review case; it also confirms the schema alone accepts it), `test_disabled_ocr_completes_with_a_skipped_result` |
| **U5** Timestamps were compared as strings | **Fixed.** `validation.instant()` parses RFC 3339 `Z` timestamps into (whole seconds, exact decimal fraction). Numeric offsets, lowercase `z` and invalid dates are still rejected. I also added "OCR `completed_at` ≥ `started_at`" (Q5). | `test_fractional_seconds_compare_chronologically` ×4, `test_differing_fractional_precision_compares_by_value`, `test_real_backwards_movement_is_rejected`, `test_non_utc_timestamps_are_still_rejected`, `test_instant_parser` |

Before the fix, the U4/U5 tests failed on the old validator: 6 failed, 30 passed.

## Approved additional development

| Item | Implementation | Tests |
|---|---|---|
| Migration 0002 (R5) | `ingestion_reservations`: `first_commit_id`, `record_object_id`, `record_bytes`, `record_published_at`. `commit_intents`: `intent_bytes` and `intent_sha256`, plus a CHECK requiring all five object roles. New tables `quarantined_assets` and versioned `search_rows`, plus the readiness functions. Applied by checksum, like 0001. | `test_migrations_are_idempotent_and_tamper_evident` (both migrations), `test_intent_rows_require_all_five_object_ids` |
| Strict private envelopes (R1) | `persistence/envelopes.py`: `paios.reservation/1`, `paios.intent/1`, `paios.commit/1`. Parsing checks canonical bytes and the exact key set. Unknown format or release is `CONTRACT_MISMATCH`. Every embedded digest and binding is re-derived. Replays keep the originally frozen `intent.json` bytes. | `tests/test_persistence_envelopes.py` (26 tests), `test_identical_freeze_replays_and_changed_bytes_conflict` |
| Reservation before acceptance (R1) | `reservation.json` is published to its pre-generated ID and verified by SHA-256 before revision 1 can be frozen. Revision 1 must use the reserved asset, ingestion and first-commit IDs. | `test_reservation_record_is_published_before_acceptance` |
| Publisher, reconciler and verifier against test adapters | `persistence/publisher.py`. Object order is `intent.json`, the payloads, then (after `mark_publishing`) `commit.json`. A 409 is resolved by the stored SHA-256, never by overwriting. Records are single-request uploads only. The publisher refuses any adapter without `FIXTURE_ONLY`. | `test_intent_json_is_first_and_holds_the_frozen_bytes`, `test_drive_lost_response_retries_with_the_same_ids`, `test_drive_object_with_other_bytes_is_integrity_failure`, `test_publisher_refuses_any_adapter_that_is_not_a_test_adapter`, `test_records_are_never_uploaded_through_a_resumable_session` |
| Recovery completes frozen intents while closed | A privileged reconciliation path requires recovery mode, the current generation, and `rolbypassrls` or superuser. It imports DGE reservations and intents, adopts and completes intents byte-identically, quarantines forks and stray markers, verifies every object, and then resumes. `resume` is refused while anything is unresolved. | `test_recovery_fences_an_old_writer_and_completes_its_intent_while_closed` (no early resume; modes observed during reconciliation are all `recovery`), `test_reconciliation_path_needs_recovery_mode_and_the_privileged_role`, `test_marker_published_but_record_lost_is_completed_by_reconciliation`, `test_coordinator_loss_rebuilds_from_dge_without_reallocating`, `test_second_dge_intent_for_a_slot_quarantines_the_asset`, `test_marker_without_an_intent_quarantines_the_asset`, `test_recovery_waits_for_an_in_flight_commit` |
| R4 detection | The verifier checks every recorded object's SHA-256. Missing, deleted or edited objects quarantine the asset; its reads return `INTEGRITY_FAILED`, and it is excluded from reuse. | `test_verifier_detects_edited_and_deleted_objects[edit/delete]` |
| R4 boundary | `PublicationService` exposes exactly `admit` and `commit`. It checks the worker credential's scope, the bundle's scope and the expected version, and has no update or delete operation. | `test_publication_service_is_the_only_writer_and_checks_scope` |

The revised R2/R4 design, including the fence procedure, the provider tests still required, and the prevented-versus-detected table, is in [proposals/PACKET-2-R2-R4-REVISION.md](proposals/PACKET-2-R2-R4-REVISION.md).

## Test results

**CI, run on `cfb1110`** ([run 35860120243](https://github.com/CarltonBurney/PAIOS/actions/runs/35860120243)): all three jobs passed.

| Job | Result |
|---|---|
| Contracts and unit tests (ubuntu-latest) | **206 passed, 62 skipped.** All 62 skips are `tests/test_persistence_db.py`, which is skipped without a database. |
| Contracts and unit tests (windows-latest) | **205 passed, 63 skipped.** The 62 database tests plus the existing POSIX-only Packet 1 test. |
| Packet 2 coordinator tests (PostgreSQL 16) | Passed. The job runs the four `tests/test_persistence_*.py` files. |

**Locally, on this commit,** which adds the multipart-guard test:

| Run | Result |
|---|---|
| Full suite with PostgreSQL 16 | **269 passed, 0 skipped** |
| Full suite without a database | **207 passed, 62 skipped** |
| Coordinator-job file set | **131 passed** |

The CI counts in these tables overlap and must not be added together.

## Honest limits (updated)

- **No provider evidence.** Any Google Drive behaviour the fence depends on is untested. That includes revocation propagation, in-flight requests, change-feed latency and 409 on create by ID. §2.5 of the R2/R4 revision lists the provider tests. The fake assumes create-by-ID returns 409.
- **Fork after coordinator loss is detected, not prevented** (R2/R4 revision §2.3). The settle hold, the continuous change-feed watcher and the independent anchor (§3.4) are proposed and not built.
- **The restore fingerprint** detects logical restores, template or file copies into a new database, point-in-time recovery and replica promotion, because each changes the database OID or the timeline. A cold copy of the whole data directory, started as the same cluster on the same timeline, is not detected. Restore runbooks must call `enter_recovery` explicitly.
- **Search keeps superseded versions** until `prune_search_versions` runs, so storage grows with the number of revisions until pruning.
- **`WorkerCredentials` is an HMAC test stand-in** for workload identity. It is not a production authentication design.

## Remaining questions

See the R2/R4 revision, §4 Q1–Q7: quarantine and recovery error codes, cursor refusal after recovery, the `reserve` signature extension, the U4 scope for duplicates, the anchor store, and who runs the provider tests.
