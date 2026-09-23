# Packet 2 R2/R4 Revision: Publication Fence and Trusted Publication Service

**Status:** proposal for focused review. It answers the R2 and R4 findings in [PACKET-2-REVIEW-ROUND-1.md](../PACKET-2-REVIEW-ROUND-1.md) and replaces the R2 (P2-B credential fencing) and R4 (P2-E append-only) sections of [PACKET-2-PUBLICATION-RECOVERY-REVISION.md](PACKET-2-PUBLICATION-RECOVERY-REVISION.md). R1, R3 and R5 were approved and are implemented against test adapters. No live cloud writer is enabled. No contract change is proposed.

**Evidence rule:** every "tested" claim below is **fixture evidence** from PostgreSQL 16 plus the in-memory Drive fake. Rows marked **provider test** need a nonproduction Google Workspace shared drive and cannot be proven with fakes. The review is right that a fake which rejects revoked credentials immediately proves nothing about Google's behaviour.

## 1. What changed from the rejected R2

The rejected design fenced old publishers by removing their token-minting grant and then waiting 15 minutes. That design had three gaps:

- IAM changes propagate eventually, so an old writer can mint a new token after the removal request.
- A requested token lifetime is not proof that every credential path enforces it.
- An upload authorized before expiry can still be in flight.

The revision makes three changes:

1. **Recovery never relies on elapsed time alone.** It completes only after these are true: the old authority is removed at the Drive resource, the removal is observed, every known outstanding request is resolved, and a quiet window is observed on the Drive change feed.
2. **Nothing an old writer can still send is dangerous.**
   - Old writers can send only frozen, byte-identical objects to pre-generated IDs.
   - Records are uploaded in a single request, never as resumable sessions, so no upload session outlives the request.
   - The one remaining hazard is a late object that creates a fork after coordinator loss. It is **detected and quarantined**, not prevented. This document says so rather than claiming a proof.
3. **The recovery state machine no longer contradicts itself.** Reconciliation runs in a privileged, closed mode in the current generation. There is no early public resume. This is implemented and tested.

## 2. R2: How recovery stops old publishers

### 2.1 Publication authority and credential paths

Publication authority belongs to exactly one principal per generation: service account `paios-publisher-g<N>`. Section 3 explains why ordinary workers have none. Every path by which that principal's authority could be exercised is closed as follows:

| Credential path | Control | Evidence |
|---|---|---|
| Service-account keys | Org policy `iam.disableServiceAccountKeyCreation`; no keys exist. The recovery checklist lists keys and requires zero. | Provider test (policy check) |
| Token minting by impersonation (`generateAccessToken`, `generateIdToken`, `signJwt`) | Only the publication service's runtime identity holds `roles/iam.serviceAccountTokenCreator` on `paios-publisher-g<N>`. Workers and humans do not. The recovery controller holds it on **new** generations only. | Provider test (IAM policy analysis) |
| Attached runtime identity (metadata server) | The publisher service account is attached only to the publication service's instances. Stopping those instances ends that path. | Provider test |
| Domain-wide delegation | None configured for publisher accounts; the checklist verifies this. | Provider test |
| Drive access itself | The publisher account is a **member of the records shared drive**, and this membership is the enforcement point. Removing the membership withdraws authority from every token already issued, whatever its remaining lifetime. Whether Google enforces this per request and how fast it propagates must be measured (§2.4). | Provider test |

Short token lifetimes, at most 15 minutes, remain as **defense in depth only**. Recovery never relies on them.

### 2.2 No resumable sessions, bounded requests

- Records objects (`reservation.json`, `intent.json`, the three payloads and `commit.json`) are small canonical JSON. The publication service uploads them only as **single-request multipart creates** with a pre-generated ID. It never opens a resumable session for the records root, so no session URI exists that could outlive a revoked credential.
- Drive documents resumable sessions separately from access tokens, and the review notes that session authority must not be assumed to end with the token. The publisher therefore refuses any record larger than the multipart limit instead of switching to a resumable upload (`test_records_are_never_uploaded_through_a_resumable_session`). Large media goes to OneDrive (R3), not the records root.
- Every create has a client deadline. After the deadline, the service treats the outcome as unknown. It never assumes the request failed; it resolves it by ID as described in §2.3.

### 2.3 Outstanding-request ledger and delayed acknowledgements

Every object the service may create is known in advance by its pre-generated ID, and the coordinator records it before any upload:
- `ingestion_reservations.record_object_id`
- `commit_intents.object_ids`, all five IDs

After the fence, recovery resolves each unpublished object ID by reading it:

| Observation for a frozen ID | Meaning | Action |
|---|---|---|
| Absent | Never created, or not yet applied | Stays unresolved until the quiet window (§2.4) ends; then the current generation creates it with the frozen bytes. |
| Present, SHA-256 equals the frozen value | A delayed request was applied | Accept it. It is the object the new generation would write. |
| Present, different SHA-256 | Impossible through the service | `INTEGRITY_FAILED`; quarantine the asset |

**Why a delayed acknowledgement is harmless while PostgreSQL survives:**
- A late create writes the exact frozen bytes to the exact frozen ID, and a later create by the new generation gets a 409 and then verifies the SHA-256. The fixture test `test_drive_lost_response_retries_with_the_same_ids` covers this.
- The database fence stops the old generation from freezing a new slot or recording a publication. Tests: `test_recovery_fences_an_old_writer_and_completes_its_intent_while_closed` and `test_recovery_waits_for_an_in_flight_commit`.

**The one hazard, after coordinator loss:** an old writer may hold an intent that the lost database knew about and the DGE does not yet show, because its `intent.json` has not landed. If that `intent.json` lands after reconciliation has listed the DGE, and the new generation has already frozen a different commit for the slot, the result is a fork. This can't be prevented without a provider guarantee we don't have. It is handled in two ways:
- **Settle hold (proposed, not implemented):** after a coordinator-loss recovery, no new revision is frozen for an asset whose latest DGE object is newer than `recovery_started_at − settle_window` until the settle window has passed.
- **Fork detection:** the import-conflict check and the stray-marker scan are implemented. A continuous change-feed watcher after resume is proposed, not implemented; it would run alongside the scheduled verifier. Any records-root object that the coordinator did not create in the current generation quarantines its asset. The fixture tests are `test_second_dge_intent_for_a_slot_quarantines_the_asset` and `test_marker_without_an_intent_quarantines_the_asset`. Nothing picks a winner (D5).

### 2.4 Fail-closed recovery procedure

| Step | Action | Completion criterion (no fixed wait alone) |
|---|---|---|
| 1 | `enter_recovery()`: mode `recovery`, generation N+1, fingerprint cleared. It waits for in-flight `record_published` transactions. | Returns. Public admissions, publication and reads are now closed (U1). |
| 2 | Stop every old publication-service instance. | The orchestrator reports zero old instances. Instances that cannot be reached count as still running, so later steps must not depend on this one. |
| 3 | Remove the old publisher's token-creator bindings **and** its records-shared-drive membership. | Both removals are acknowledged. |
| 4 | **Observe the fence.** The recovery controller cannot mint old-generation tokens because of step 3, so it uses a separate **canary identity** that has the same Drive membership as the publisher and is revoked at the same moment in step 3. It probes a create in a canary folder until the create is refused on K consecutive attempts spread across at least P minutes. | K and P come from the propagation measurements (§2.5). If the canary is not refused within the bound, recovery escalates to an operator and stays closed. |
| 5 | Resolve the outstanding-request ledger (§2.3) and **observe a quiet window.** The records-root change feed shows no change by an old principal for Q minutes after step 4. | Q is at least the measured maximum delay between request receipt and change visibility, plus margin. Every change after the step-3 timestamp is listed in the recovery report. |
| 6 | Reconcile in closed mode (§2.6). | `Reconciler.run(..., resume=False)` returns without transient errors. |
| 7 | `resume(N+1)`. | Refused while any intent is still `publishing` or unadopted, unless it is quarantined. Binds the open state to this database's fingerprint. |

If any step can't reach its criterion, the coordinator **stays closed**. Resuming with an incomplete fence is a break-glass action by a named administrator, logged in both the Workspace audit log and the PostgreSQL audit log.

### 2.5 Provider tests required before any live writer

These run against a nonproduction shared drive. The results set K, P and Q, and they are reported separately from fixture evidence.

1. **Drive membership removal:** time until a create with an **already-issued, unexpired** token is refused. Measure the distribution over at least 50 trials, and across regions if clients are distributed.
2. **In-flight request:** start a create, remove the membership while the request is outstanding, and record whether it applies. Include a large payload close to the multipart limit to widen the window.
3. **IAM propagation:** time from removing the token-creator binding until `generateAccessToken` is refused. This informs steps 3 and 4 but is not relied on.
4. **Change-feed latency:** delay between a create's acknowledgement and its appearance in `changes.list`. This sets Q.
5. **Token lifetime:** confirm that the requested 900-second lifetime appears in the token metadata. Also confirm the org policy has no lifetime-extension constraint for publisher accounts.
6. **Create-by-ID conflict:** confirm that a second create with the same ID returns 409 and does not overwrite. The fake assumes this behaviour.

### 2.6 Closed-mode reconciliation path (implemented)

The review found a contradiction: the proposal completed intents while in recovery, but `adopt_intent`, `mark_publishing` and `record_published` required mode `open`. The implementation now has two authorization paths in `CoordinatorRepository._authorize`:

- **Public path.** Requires all of:
  - `mode = 'open'`
  - the stored fingerprint equals `cluster_fingerprint()` (the system identifier, current WAL timeline and database OID)
  - the caller's generation is current
- **Reconciliation path.** Requires all of:
  - `mode = 'recovery'`
  - the caller's generation is current
  - a privileged database role (`rolbypassrls` or superuser)

  `adopt_intent`, `import_reservation`, `import_intent` and `quarantine` exist only on this path. `mark_publishing`, `record_published` and `mark_reservation_published` take it with `reconcile=True`.

`Reconciler.run(generation)` takes these steps:
1. Import `reservation.json` records, then `intent.json` records in version order. Each is strictly parsed. A conflicting or unchainable intent quarantines its asset.
2. Quarantine the asset of any `commit.json` that has no intent.
3. Publish reservation records the DGE never received.
4. Adopt and complete every unpublished intent with its frozen bytes.
5. Verify every recorded object.
6. Resume, which is optional; tests run with `resume=False` to prove completion while closed.

| Behaviour | Fixture test |
|---|---|
| Completes an old writer's intent with no public resume, byte-identical objects and intent | `test_recovery_fences_an_old_writer_and_completes_its_intent_while_closed` |
| Ordinary role or open mode cannot use the reconciliation path | `test_reconciliation_path_needs_recovery_mode_and_the_privileged_role` |
| Resume is refused while an intent is still publishing | `test_resume_is_refused_until_uncertain_intents_are_resolved` |
| Marker uploaded but not recorded: completed in closed mode | `test_marker_published_but_record_lost_is_completed_by_reconciliation` |
| Coordinator lost: rebuilt from the DGE with the same commit IDs, and an incomplete intent is completed, not reallocated | `test_coordinator_loss_rebuilds_from_dge_without_reallocating` |
| A restored copy that says `open` starts closed | `test_restored_database_starts_closed_until_reconciled` |

## 3. R4: Trusted publication service boundary

### 3.1 Boundary

- **The publication service** is the only component with Drive write access to the records root, through `paios-publisher-g<N>`.
- **Ordinary ingestion, OCR and API workers** hold **no** Drive credential and no token-creator binding on publisher accounts. They authenticate to the service with their workload identity, which in fixtures is a short-lived scoped token (`WorkerCredentials`).
- **The service's whole API** is two append-only operations:
  - `admit`: reserve, then publish `reservation.json` before acceptance.
  - `commit`: freeze through the coordinator's full validation, then publish exactly the frozen bytes to the frozen IDs.
- **For each call, the service:**
  - checks the caller's scope against the bundle's scope
  - checks the expected version
  - relies on the coordinator for bundle validity, marker and chain checks, and slot uniqueness
- **The service never offers:**
  - an update or delete operation
  - a raw Drive passthrough
  - a way to supply object IDs or bytes that are not frozen

  `test_publication_service_is_the_only_writer_and_checks_scope` asserts the service's public surface is exactly `admit` and `commit`. It also covers wrong-scope, tampered and expired credentials, a bundle-scope mismatch and a version mismatch.

- `Publisher` refuses any Drive adapter that does not declare `FIXTURE_ONLY` (`test_publisher_refuses_any_adapter_that_is_not_a_test_adapter`). Enabling a live adapter is a separate, reviewable change.

### 3.2 What Drive permissions can and cannot do

Drive has no create-only role. A shared-drive Contributor can edit the files it can see, and Content managers and Managers can also delete and trash them. The service's create-only behaviour is therefore **code behaviour inside a privileged identity**, not a permission guarantee. Two related limits:
- Content restrictions can be lifted, so they are not immutability (`test_drive_content_restriction_is_not_immutability`).
- Keep-forever revisions are **retention support** only. They do not stop a user with enough permission from deleting or trashing the file.

### 3.3 Prevented versus detected (stated exactly)

| Mutation or actor | Prevented, and by what | Detected, and by what |
|---|---|---|
| Ordinary worker writes, edits or deletes a DGE object | **Prevented**: the worker holds no Drive credential or token-creator binding. *Provider test: IAM and Drive membership audit.* | Also caught by the verifier |
| Worker publishes into another scope, or publishes bytes that are not frozen or are invalid | **Prevented**: service scope check, coordinator validation, exact frozen bytes and IDs. *Fixture tested.* | n/a |
| A second commit for a revision slot, through the service | **Prevented**: coordinator unique slot plus compare-and-set head. *Fixture tested.* | n/a |
| A stale-generation writer freezes or records while PostgreSQL survives | **Prevented**: generation fence and closed mode. *Fixture tested.* | n/a |
| A stale-generation writer's late `intent.json` or marker after **coordinator loss** | **Not prevented** | **Detected**: import conflict, stray-marker scan, post-resume change-feed watcher; the asset is quarantined. *Fixture tested (import and stray marker); watcher is proposed.* |
| Runtime database roles update, delete or truncate history, or delete quarantine records | **Prevented**: grants plus triggers. *Fixture tested.* | n/a |
| Publication-service compromise (it holds edit rights) | **Not prevented** | **Detected**: verifier compares every object with the coordinator's SHA-256; a missing or edited object quarantines the asset. *Fixture tested for edit and delete.* |
| Drive administrator, Manager or Content manager edits, deletes or trashes | **Not prevented** | **Detected**: same verifier |
| Database superuser or owner disables triggers and rewrites history | **Not prevented** | **Detected** only against an independent baseline (§3.4) |
| One actor controlling the DGE, the coordinator and the verifier | **Not prevented** | **Not detected** without the independent anchor (§3.4). This limit is stated, not solved. |

### 3.4 Independent baseline after coordinator loss

After coordinator loss, the DGE marker chain is self-consistent by construction, so on its own it can't reveal tampering by someone who controls the DGE. The proposed baseline:

- The verifier periodically writes a **checkpoint anchor**: a canonical list of `(scope, asset_id, registry_version, marker_sha256)` for every head, plus its SHA-256.
- The anchor is stored in a store administered separately from both the records shared drive and the coordinator. Examples are an append-only bucket with a retention lock, in a separate project with separate administrators.
- After a coordinator-loss rebuild, the reconciler compares the rebuilt heads with the latest anchor. Any head that is missing, regressed or different quarantines its asset.

This is **proposed, not implemented**. It needs a storage decision (see Q6).

## 4. Remaining contract and architecture questions

| # | Question | Current implementation |
|---|---|---|
| Q1 | Should a quarantined asset's reads return `INTEGRITY_FAILED` (not retryable)? The contract has no dedicated quarantine code. | `INTEGRITY_FAILED`, `retryable=false` |
| Q2 | Should recovery-mode reads use `PERSISTENCE_UNAVAILABLE` (retryable, 30 s)? Packet 4 will map this to HTTP 503. | As stated |
| Q3 | A search cursor from before a recovery or restore is refused as `INVALID_REQUEST`, and the client restarts the search. Acceptable? | As stated (`test_cursors_do_not_survive_recovery`) |
| Q4 | `IngestionRepository.reserve` in the contract has no `record_object_id`. The implementation adds an optional keyword, and admission goes through `PublicationService.admit`. Is this acceptable as an implementation extension, or do you want a protocol revision? Also, `IngestionRepository.commit` and `IndexRepository.apply_committed` are realised as `PublicationService.commit` and the projection outbox, not as methods with those names. | Extension; the contract is unchanged |
| Q5 | U4 now requires completed, skipped or reused OCR for **every** completed ingestion, including `exact_duplicate_of` occurrences. I also added "OCR `completed_at` must not precede `started_at`". Please confirm both. | Enforced |
| Q6 | The anchor store for §3.4 (provider and administrative separation) | Not built |
| Q7 | Settle window and quiet-window values (§2.3, §2.4) come from the provider tests. Who owns running them in the nonproduction shared drive? | Not run |
