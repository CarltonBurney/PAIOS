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
