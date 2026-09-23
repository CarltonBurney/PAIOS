# Packet 2 architecture decision

Reviewed proposal: `apps/image-ingestion/proposals/PACKET-2-STORAGE-PROPOSAL.md` at commit `1a29007b82c5ec8966ccf6024819b00d73d204af`.

**Decision: approve the PostgreSQL-based development foundation under the constraints below. Do not implement the cloud publication protocol as currently written.** This issues concrete architecture decisions so implementation can progress; it is not approval to provision paid hosting, write production records, change canonical specifications or deploy.

## Authorized next development unit

Claude may build PostgreSQL migrations, scoped reservation/intent/head/outbox repositories, the fixture-backed three-record validator, and in-memory Drive/Graph fakes plus crash-injection tests. Use disposable local/CI PostgreSQL strictly as a test fixture, not the deployed source of truth. Preserve the existing interfaces and immutable contract release. No real cloud credentials are required for this unit.

Before building the real marker publisher or cloud writers, revise the proposal's publication/recovery sections and private record formats to satisfy the requirements below and return them for focused review. This restriction does not block the authorized database/fake-adapter work.

## Requested decisions D1–D8

| Decision | Disposition |
|---|---|
| D1: coordinator | **Approve PostgreSQL** with durable transactions, unique scope-aware keys, backup/PITR and an off-machine production deployment. Azure hosting is a candidate, not a purchase/deployment decision. Select a supported version when provisioning. |
| D2: search | **Approve PostgreSQL as the projection store.** The v1 API requires case-insensitive literal substring matching across title/search_text. `tsvector` tokenization/stemming must not replace that behavior. Escape literal `%`/`_` if using LIKE-family operators; preserve scope, filters, sort and cursor semantics. |
| D3: marker | **Revise before publisher implementation.** Marker chaining is suitable; fixed object identity, frozen bytes, coordinator-loss recovery and head freshness are still required below. |
| D4: verification | **Require remote SHA-256 verification for originals AND normalized pages** before issuing their StorageRefs. Download the pinned remote content and hash it when the provider cannot supply the required digest. Size/quickXorHash can be additional checks, not a replacement. |
| D5: forks | **Approve fail-closed quarantine and manual reconciliation.** Never choose by timestamp or last-writer-wins. Quarantine is recovery defense, not the normal substitute for preventing duplicate revision publication. |
| D6: throttling | **Approve bounded per-service concurrency, backoff/jitter, Retry-After and durable retry scheduling.** No throughput or pricing guarantee is accepted from the rough proposal estimates. Measure nonproduction latency/quota behavior before sizing production. |
| D7: retention/erasure | **No automatic deletion/redaction implementation yet.** Append a correction does not remove sensitive data from prior immutable snapshots. Return a policy and full historical-data inventory before implementing erasure; keep retention configurable and production-gated. |
| D8: identity | **Approve separate least-privilege identities and specification/records roots as the design.** Prove the selected Drive/Graph permissions in nonproduction; service-account access to the actual DGE location has not been configured or verified. No broad domain delegation is assumed. |

## Required publication/recovery revisions

### P2-A: stable identity and byte-identical replay

A file/folder name or `{commit_id, role}` search is not a unique create operation. Freeze a single payload object ID per role and a single marker ID in the durable intent before uploading. Use pre-generated Drive IDs where supported and retry the exact IDs; Drive documents this mechanism for safe upload retries. See [Drive uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads) and [file creation](https://developers.google.com/workspace/drive/api/guides/create-file).

Freeze exact serialized Registry/Audit/Index AND marker bytes, including file IDs, publication timestamp and any recorded fencing epoch. A takeover may execute the same immutable intent but must not regenerate marker fields from its new lease/time. Otherwise the proposal's statement that a paused writer can only write identical bytes is unproven. Define a canonical bundle hash without circular self-hashing and store/verify it consistently.

Specify the reservation record itself: format/release, scope, idempotency-key identity/digest, normalized request digest, asset/ingestion IDs, pinned processing profile/context versions, object IDs, intent state and safe replay rules. Never put credentials in it. Complete the crash cases between PostgreSQL reservation, Drive reservation creation, frozen intent, payloads and marker. A reservation filename derived from the key alone is not an atomic uniqueness primitive in Drive.

### P2-B: recovery after PostgreSQL loss must fence old publishers

The proposal permits rebuilding PostgreSQL from Drive after loss, but the not-yet-published intent/allocated object IDs also need durable recovery. A pre-crash publisher could still be running or have a delayed request in flight. Do not allocate a new commit for the same revision slot while an older publication might complete.

Document a fail-closed recovery mode: stop admissions/publication, revoke or fence old publishing authority, recover and reconcile all reservation/intent identities (including uncertain uploads), then resume with a single valid publisher generation. Explain which guarantee is enforced at the publication boundary, rather than by a lease check made before a network call. Add a test where an old writer resumes after database restore and another worker takes over the revision.

### P2-C: direct GET cannot silently serve a stale SQL head

There is a window after a Drive marker is published but before `registry_heads` advances. A periodic reconciler does not make GET current during that window. An API reader may not yet “know” the marker exists. Specify how reads detect a publishing/uncertain intent and reconcile or return 503 instead of presenting the older head as current. Include concurrent GET after marker success/SQL failure and startup/recovery reads. A successful receipt followed by current GET is necessary but not the whole contract.

### P2-D: verify pinned remote bytes and define version semantics

The proposed local SHA-256 says what was sent, not what is durably stored. Graph documents `sha256Hash` as unsupported; require a read-back digest for every artifact covered by a StorageRef. See [Microsoft Graph hashes](https://learn.microsoft.com/en-us/graph/api/resources/hashes?view=graph-rest-1.0).

Define whether `version` is a historical content version ID or a concurrency token. Do not interchange an eTag with a retrievable version ID without a connector rule. A missing/changed historical version must fail rather than silently fetching latest. Apply the same semantics to source reads and verified writes; fake tests must exercise changes between upload, verification and retrieval.

### P2-E: append-only enforcement requires more than a read-only flag

Google explicitly states that content restrictions are mutable and do not create immutable records. Treat them as protection from accidental edits, not the sole append-only guarantee. See [Drive content restrictions](https://developers.google.com/workspace/drive/api/guides/content-restrictions).

Define the trusted publication service boundary, permitted operations, reader verification, role restrictions and administrative break-glass policy. Explain what prevents an ordinary runtime actor from mutating/deleting published history and what is merely detected if an administrator acts outside that trust boundary. Similarly, do not claim a PostgreSQL trigger prevents its privileged owner/superuser from bypassing it. Test with the actual proposed service identities. Production requires an honest, demonstrated guarantee; no new canonical store is approved as an implicit workaround.

## Tests and delivery

Carry forward A01–A08 and A17–A21, and add the five cases above. The repository validator's sample invariants are not an exhaustive production commit validator; implement all normative cross-record/status/page/hash rules, including invariants not covered by the sample tests. Test escaped substring queries separately from any internal full-text ranking.

Return two reviewable units: (1) migrations/repositories/fakes with test evidence and no live writes, and (2) the corrected publication/recovery proposal before the live publisher is built. Report fixture-only versus live evidence explicitly. Live cloud testing, permissions, hosting selection, retention policy, real test media and Obsidian/DGE promotion remain deployment gates.
