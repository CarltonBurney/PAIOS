# Packet 2 review — round 2 and Q1–Q7 decisions

Reviewed PR #6 at `8ee72ba58d428d557cd9ccc7c8f374489a837bb0`. Contract baseline remains 1.0.0. This is a working review and handoff, not canonical promotion or live deployment approval.

## Verdict and evidence

Packet 1 remains accepted. U2, U4 and U5 are closed. U1's recovery-mode read checks and U3's versioned snapshot mechanism address the original examples, but their restore/lifetime guarantees need the bounded follow-ups below. Packet 2 acceptance remains pending these corrections and interface conformance. The test-only publisher/reconciler scope remains authorized.

[CI run 35861308513](https://github.com/CarltonBurney/PAIOS/actions/runs/35861308513) passed on the reviewed head. Exact log counts: Ubuntu **207 passed / 62 skipped**; Windows **206 passed / 63 skipped**; PostgreSQL job **131 passed**. These overlapping suites must not be added as unique tests. The database job's count is now verified, replacing the previously unavailable count in the return narrative.

Local independent review: **69 tests passed** across validation, envelope and fake-storage tests. The previous probes now reject completed ingestion with null OCR and accept correctly ordered fractional timestamps. PostgreSQL concurrency/restore evidence comes from CI and source review; this reviewer did not run a local PostgreSQL server. No live-cloud evidence exists.

| Original finding | Disposition |
|---|---|
| U1 recovery reads | Readiness checks now cover asset/job/search/admission paths. Keep the restore guarantee open under B1. |
| U2 mixed read snapshots | Closed: get/get_job use a read-only REPEATABLE READ transaction, with the requested publication-between-reads regression. |
| U3 updated assets disappear from pagination | The original overwrite bug is addressed by version rows and transaction visibility. Complete B4 for cursor lifetime and pruning. |
| U4 completed asset without OCR | Closed: completed requires completed/skipped/reused OCR, including duplicate occurrences. |
| U5 lexical timestamp ordering | Closed: parsed whole seconds and exact fractional values replace string ordering. |

## Remaining implementation corrections

### B1 — P1: a database fingerprint is not a universal restore fence

`migrations/0002_recovery_and_snapshots.sql` derives readiness from system identifier, WAL timeline and database OID. The restore regression uses `CREATE DATABASE ... TEMPLATE`, which changes the OID. It does not prove the claim for a restored physical cluster. A consistent cold filesystem copy can preserve all three values and the stored open flag; ordinary restart of that copy need not create a new timeline. Thus readiness may remain true even though the restored coordinator is behind DGE.

This is a source/design finding, not a locally executed physical-restore experiment. PostgreSQL documents that physical backups copy cluster files, while specifically archive recovery creates a new timeline; those are not evidence that every restored copy gets a new identity. See [physical backup](https://www.postgresql.org/docs/16/app-pgbasebackup.html) and [archive recovery timelines](https://www.postgresql.org/docs/16/continuous-archiving.html).

Keep the fingerprint as defense in depth. Add an explicit startup/restore gate outside the copied database state: the service must not expose public operations until the recovery controller establishes a fresh serving epoch and reconciles. Wire the gate into the composition entrypoint, not just a README instruction. Add a same-fingerprint copied-state regression plus the existing changed-OID test. Document supported restore procedures and distinguish template cloning from physical restore evidence. No production server must be started directly against restored `mode=open` state.

### B2 — P1: search still serves quarantined assets

`CoordinatorRepository.search()` filters scope, version visibility and query predicates, but never excludes `quarantined_assets`. After the verifier quarantines an already-indexed asset, direct GET fails while search continues returning its text and metadata, including through pre-quarantine cursors.

Exclude currently quarantined assets from new and continued search results. Integrity suppression takes precedence over the otherwise stable result snapshot; document that narrow exception. Add tests for quarantine before a new search and between pages. Verify that cross-scope quarantine cannot suppress another scope. Do not return the quarantined text in an error message.

### B3 — P1: the verifier omits reservation records

`published_objects()` returns only the five intent/commit objects. `Verifier.run()` never checks `ingestion_reservations.record_object_id` or the digest of `record_bytes`. Deleting or editing `reservation.json` can therefore leave verification green although a later coordinator rebuild cannot recover the idempotency mapping. `import_reservation()` also receives bytes without verifying that the downloaded file's actual ID equals the envelope's declared record ID.

Include every published reservation in the verification baseline, including reservations without a committed asset revision. Bind downloaded reservation/intent envelopes to the actual object IDs used to retrieve them. Missing, edited or relocated-to-a-different-ID reservation data must fail closed with a scoped integrity finding. Add delete/edit/wrong-ID tests and a recovery test showing the same key cannot allocate a second occurrence after such loss. Keep canonical records unchanged; this is coordinator/recovery validation.

### B4 — P2: pagination renews an old snapshot beyond its retention lifetime

Each new cursor sets `expires = now + CURSOR_TTL_SECONDS`, even when continuing an old snapshot. Pruning assumes cursors issued before an old mark have expired. A slow client can keep receiving freshly extended cursors for the same old snapshot, then have a still-unexpired token rejected after pruning removes that snapshot's rows. The implementation explicitly promises that pruning will not break a live cursor.

Pin snapshot creation time and absolute expiration on page 1; preserve both on every continuation. Alternatively track renewable snapshot leases and make pruning honor them, but fixed expiry is sufficient for v1. Test a multi-page walk near the original expiry, continued paging after that boundary, and pruning concurrent with a continuation. Expiration must be a clean INVALID_REQUEST, never silent omission. Keep generation/fingerprint binding.

### B5 — P1 integration gate: provide actual 1.0.0 protocol adapters

Q4's parameter is only syntactically optional: `reserve()` immediately rejects its omission, so a valid call through `IngestionRepository.reserve` cannot work. `CoordinatorRepository` also lacks the protocol's `commit`, and `PublicationService.commit` requires different credentials/scope arguments; the outbox does not itself implement `IndexRepository.apply_committed`.

Keep these implementation objects internal. Add thin adapters with the exact issued method signatures, obtaining object IDs and authorized service context internally and delegating to the frozen publication/outbox mechanisms. A caller implementing the issued protocol must not know about record_object_id or the service credential parameter. Test through the protocol-facing adapters, including reservation replay, commit replay/version conflict and monotonic apply_committed. State expected_version semantics explicitly and consistently in adapter tests; propose any desired semantic change before changing the release. No silent interface change or schema-version bump is approved.

## Architecture answers Q1–Q7

| Question | Decision |
|---|---|
| Q1 quarantined reads | Approve INTEGRITY_FAILED with retryable=false for authorized direct reads; Packet 4 maps it to the existing contract's 503. It requires repair, not blind retry. Foreign IDs remain undisclosed. Search suppresses quarantined rows under B2. |
| Q2 recovery reads | Approve PERSISTENCE_UNAVAILABLE, retryable=true, Retry-After 30 seconds as the current configurable default, HTTP 503. |
| Q3 old search cursor | Approve INVALID_REQUEST after recovery/restore; client starts a new search. Preserve absolute expiry under B4. |
| Q4 interface differences | Internal helpers may have extra arguments. They are not substitutes for the issued protocols. Implement the adapters in B5; keep release 1.0.0 unchanged. |
| Q5 duplicate OCR/time | Confirm both. Every completed occurrence has a completed/skipped/reused result. Reused results follow the existing same-scope, successful-source and matching-fingerprint rules. completed_at must not precede started_at. |
| Q6 independent verification record | Approve the checkpoint format/interface and fake tests, not a new storage provider. It is evidence, never a replacement for Obsidian/DGE truth. A live backend needs independently controlled write authority and retention verified at deployment; no paid bucket or new canonical store is authorized. Backend selection remains a deployment configuration decision. |
| Q7 provider tests | ChatGPT owns the protocol, evidence assessment and release gate; Claude builds/runs the reviewed harness when a dedicated nonproduction drive and least-privilege identities are configured. The user supplies/authorizes that environment. No production-root tests. Provider observations inform operations but do not prove a universal maximum delay. |

## R2/R4 disposition and concrete v1 recovery boundary

**R4: accept the trusted-service design for continued test-only implementation.** It now correctly distinguishes worker prevention from a privileged service/administrator compromise. The HMAC credentials are fixtures, not approved production authentication. Retain that distinction in all return documents.

**R2: do not enable automatic resumption after coordinator loss on the strength of canary refusals or a quiet window.** A different canary principal being denied does not prove the actual old publisher is denied everywhere. A measured delay distribution or 50 successful trials does not establish a provider-wide upper bound. The proposal itself acknowledges a late intent can still fork a slot; treating that as normal detected-and-quarantined behavior weakens the existing single-writer contract and is not approved implicitly.

Adopt this bounded v1 operating rule: when the complete durable intent ledger survives, resume only under the reviewed fence/reconciliation procedure with the same frozen identities. If the ledger is lost or its completeness cannot be established, keep admissions/publication closed and require recovery of a complete ledger plus verified termination of old publication authority before allocating any new slot. A quiet window is supplementary evidence, not the condition that proves completeness. If the chosen deployment cannot establish this, propose an explicit contract/architecture change for review; do not hide it behind a settle timer. The fake recovery flow remains useful and authorized, but is not evidence for live safety.

For Q6, a checkpoint must verify the anchored historical revision in the recovered chain, not compare a legitimate newer head directly with an older checkpoint hash. Include checkpoint sequence, time, release, manifest digest, and authenticated provenance; resolve equal/newer/missing/regressed heads in tests. An unsigned digest stored beside the object does not authenticate the baseline. If independence cannot be configured, state that compromised-DGE detection after coordinator loss remains unsupported and keep that release gate open.

## Next Claude handoff

Implement B1–B5 in focused commits with targeted regressions, update the return document, and revise R2 to the fail-closed v1 operating rule above. Build only checkpoint interfaces/fakes until a backend is selected. No new decoder work, real cloud writer, paid resources, production writes, OCR/API implementation or canonical promotion is requested in this handoff. Keep fixture and provider evidence separate. Return commit IDs and exact three-job CI counts; do not add overlapping counts.

