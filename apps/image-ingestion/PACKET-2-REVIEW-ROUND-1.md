# Packet 1 acceptance and Packet 2 review

Reviewed PR #6 at `af2c14d1dd2549bb3de4ea2f7b92743dc78be863`. Working architecture/QA decision; no canonical promotion, merge or production deployment is implied.

## Packet 1: accepted within its issued scope

The R3 correction separates nonmutating reader probes from gate-held stale-marker removal. Registration takes a blocking marker lock while holding the gate. This closes the POSIX creation-to-lock race described in round 2. The new regression exercises the requested interval. Local Windows cache tests: **15 passed, 1 skipped**. The prior R1, R2, R4, R5, R6 and ICC dispositions stand. Packet 1 is accepted as the pre-OCR foundation, with its documented resource envelope. Real iPhone HEIC/scanned-PDF integration testing remains a final pipeline gate.

## Packet 2 unit 1: changes required

The scoped coordinator, frozen payloads, literal substring search and fake adapters are useful progress. Acceptance is pending the five corrections below. Continue corrections within the already authorized development scope; do not rewrite the immutable contract package.

Evidence: [CI run 35822129001](https://github.com/CarltonBurney/PAIOS/actions/runs/35822129001) passed. Its logs show the PostgreSQL job **69 passed**, Ubuntu **171 passed / 36 skipped**, and Windows **170 passed / 37 skipped**. The skips include the database tests outside the dedicated database job. These counts overlap and must not be added as distinct tests. This review did not rerun PostgreSQL locally; the repository findings below are source analysis, and the validator findings were independently reproduced locally.

### U1 — P1: recovery mode does not block reads

`persistence/repository.py`, `CoordinatorRepository.get()` never reads `publisher_state`. After `enter_recovery()`, a previously committed head with no publishing intent still returns successfully. This contradicts P2-C in the revised proposal and exposes untrusted restored heads before reconciliation. `get_job()` also has no recovery gate.

Require a coherent recovery/readiness check for authoritative asset and job reads. Restored state must start closed before serving requests, rather than trusting an old `mode=open` backup. Add tests for an existing asset and job during recovery, startup after restore, and successful reads only after reconciliation permits resume. Keep authorization/scoping behavior explicit.

### U2 — P1: separate head and uncertainty queries can return an obsolete head

`get()` first reads the head, then checks publishing intents in a separate statement. With the default READ COMMITTED transaction, the statements can see different database states. Reproduction schedule: revision N's DGE marker already exists and its intent is publishing; GET reads head N-1; `record_published(N)` commits; GET's second statement now sees no publishing intent and returns its saved N-1. This returns an old revision even though N was published before the read began.

Read recovery state, head and publication uncertainty coherently (for example in a single statement snapshot, or a correctly locked/snapshotted transaction). Add a deterministic barrier test between the existing two reads with N already remotely visible. Accept N or retryable 503, never N-1. A recovery check alone does not fix this interleaving.

### U3 — P1: the search cursor does not preserve a snapshot across updates

`apply_search_projection()` overwrites the only `search_rows` record for an asset and assigns a new `projected_at`. `search()` filters that table by `projected_at <= cursor.snapshot`. If an unseen asset is updated after page 1, the original snapshot row is gone and its replacement is excluded, so that asset disappears from subsequent pages. The existing test covers a newly inserted asset, not an update to an existing one.

Preserve the versions needed for the cursor lifetime, or materialize a stable result snapshot. Use a visibility watermark with defined commit semantics, not merely wall-clock assignment before transaction commit. Add tests updating an unseen asset between pages (including title/status/filter changes), and a projection transaction crossing snapshot creation. Return each original snapshot member once in its original snapshot form.

### U4 — P1: completed ingestion can omit its OCR result

`persistence/validation.py`, `_check_status()` checks completed OCR only when `ocr is not None`. Starting with the completed example, setting Registry OCR to null, all three `ocr_status` values to pending, text hashes to null and Index search text to empty, then recomputing Audit hashes, is accepted by `validate_bundle()`.

Completed ingestion must contain the appropriate completed/skipped/reused OCR result; disabled OCR produces a skipped result, not null. Enforce this and add the exact negative case plus a positive disabled-OCR/skipped case. Do not describe schema shape validation as sufficient for this invariant.

### U5 — P2: timestamps are compared lexically instead of chronologically

`validate_bundle()` and `_check_chain()` compare RFC 3339 strings directly. `2026-09-22T18:00:00.100Z` sorts before `2026-09-22T18:00:00Z`, despite being later. A locally reproduced valid later fractional timestamp is rejected as preceding creation/predecessor. Parse timezone-aware timestamps and compare instants. Keep UTC/Z validation; test zero versus fractional seconds, differing fractional precision, equality, and real backwards movement.

## Revised publication proposal: decisions R1–R5

| Request | Decision |
|---|---|
| R1: frozen `intent.json` first | Approved as the recovery design. Persist its object ID and exact envelope bytes before upload; preserve them on replay. Add format/release validation and define recovery enumeration and conflicts. Reservation publication must precede durable acceptance. |
| R2: credential fencing and a 15-minute wait | Not approved as a proven publication fence. Use the corrections below before any live writer is enabled. Short-lived credentials remain appropriate defense in depth. |
| R3: historical OneDrive version ID | Approved connector semantics. Resolve and verify the newest version for the upload; if it differs, fail rather than selecting an older matching version. Read exactly the pinned version thereafter. No fallback to latest. |
| R4: append-only guarantee | Not approved as currently phrased. Create-only behavior is not create-only permission. Adopt the trusted publication-service boundary below and state its limits accurately. |
| R5: migration 0002 | Approved additive development change for reservation and intent object IDs, including validators/fakes and migration tests. No canonical schema edits are needed for these private coordinator fields. |

### R2: account for revocation propagation and in-flight writes

Removing a token-minting grant is not immediately effective everywhere. Google documents IAM access changes as eventually consistent: [access-change propagation](https://docs.cloud.google.com/iam/docs/access-change-propagation). A worker could mint another token during propagation, so waiting 15 minutes from the removal request does not prove all tokens expired. A requested 15-minute lifetime also is not proof that every credential path enforces that maximum. An already-authorized upload may be in flight when a token expires.

Specify an enforceable publisher shutdown/fence and fail-closed recovery procedure. Account for all credential-minting paths, outstanding uploads/resumable sessions and delayed acknowledgments; reconcile only after old publication authority and uncertain requests are resolved. Do not infer that session authority ends with an access token: [Drive's upload documentation](https://developers.google.com/workspace/drive/api/guides/manage-uploads) describes resumable sessions separately. The exact cancellation/authorization behavior needs a provider-specific test. A fake that immediately rejects revoked credentials cannot prove this cloud guarantee.

Also fix the recovery state-machine contradiction: the proposal completes/adopts intents while mode is recovery, but `adopt_intent`, `mark_publishing` and `record_published` require mode open, and the test resumes before adoption. Define a privileged reconciliation path in the current generation that can repair/complete frozen intents while public admission and reads remain closed. Test recovery without an early public resume.

### R4: isolate the privileged publisher and narrow the claim

Approve a dedicated trusted publication service as the enforcement boundary: ordinary ingestion/OCR/API workers receive no raw Drive write credentials and call an authenticated append-only operation. The service validates scope, frozen intent identity, bytes and object IDs and exposes no update/delete operation for published content. Its credential remains privileged; compromise of that service or a Drive administrator is a detection/recovery risk, not something Drive permissions prevent.

Document and test that separation. The current statement that the publisher only calls create is not sufficient to claim prevention for every runtime identity. Google explicitly says content restrictions are mutable and do not create immutable records: [Drive content restrictions](https://developers.google.com/workspace/drive/api/guides/content-restrictions). Keep-forever revisions are retention support, not permission enforcement or protection from deletion of the file. Test missing/deleted objects as well as edited bytes, and define an independent baseline for verification after coordinator loss.

## Authorized next handoff

Claude should deliver U1–U5 with targeted regressions and update the return document. In parallel, migration 0002, strict private-envelope validation, and the publisher/reconciler state machine against fakes may proceed under the R1/R3/R5 decisions and the boundaries above. This authorizes test-only publisher development; it does not authorize enabling a live cloud writer.

Return a focused R2/R4 design update showing the actual fence and trusted service boundary, including the closed-mode reconciliation path. Keep fixtures and live evidence separate. No paid resources, production writes or changes to the Obsidian/DGE canonical specification are authorized by this review. Packet 1 need not be resubmitted unless these changes affect it.

