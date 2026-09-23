# Packet 2 Storage Proposal

**Status:** proposal for ChatGPT review. Nothing described here has been implemented. Contract release 1.0.0.

**Answers:** the deployment gate in [`storage-and-consistency.md`](../../../contracts/image-ingestion/docs/storage-and-consistency.md) and [`persistence-model.md`](../../../contracts/image-ingestion/docs/persistence-model.md). Those documents require Claude to propose the coordinator/database technology and the formats for publication and reservation records, and then prove fencing and crash recovery before any production write.

**Scope:** this covers the six items [PACKET-1-REVIEW.md](../PACKET-1-REVIEW.md) requires for this proposal:

| Required item | Section |
|---|---|
| 1. Durable coordinator | 2–3 |
| 2. DGE publication | 4 |
| 3. OneDrive assets | 5 |
| 4. Projections and replay | 6–7 |
| 5. Isolation, identities, secrets, retention, observability and cost | 9–12 |
| 6. Failure matrix | 13 |

This proposal changes no contract field. The items that need an architecture decision are listed in [Decisions requested](#decisions-requested).

## 1. Summary

| Concern | Proposal |
|---|---|
| Durable coordinator | Managed **PostgreSQL**, hosted off the local machine. It holds reservations, commit intents, fencing epochs, heads and the projection queue. |
| Canonical records | **DGE (Google Drive)** holds immutable `registry.json`, `audit.json` and `index.json` files plus a `commit.json` marker. The marker is written last and is the only point at which a revision becomes visible. |
| Working media | **OneDrive** through Microsoft Graph. Paths are set by the issued routing rule. Each upload is verified by size, `quickXorHash` and a local SHA-256, and pinned to a driveItem ID and version. |
| Serving projections | Heads, the Master Index and full-text search live in the same PostgreSQL (`tsvector` for v1). All of it can be rebuilt from the DGE. |
| Queue | A transactional outbox in PostgreSQL, claimed with `FOR UPDATE SKIP LOCKED`. No local queue. |
| Local machine | Packet 1's cache only. Nothing on it is authoritative. |

The core idea is that **PostgreSQL decides who may write revision N, and the DGE decides what revision N is.** A revision is committed only once its DGE marker exists and every hash checks out. The coordinator enforces uniqueness and fencing so that only one writer can ever produce that marker.

## 2. Why PostgreSQL for the coordinator

The contract requires the coordinator to do four things:

- serialize writers atomically
- enforce the expected version, unique event IDs and unique commit IDs
- survive loss of the local machine
- be rebuildable from committed records

PostgreSQL provides the first three with ordinary unique constraints, row locks and `SERIALIZABLE` transactions. Recovery is designed for the fourth (section 6).

| Option | Why not chosen |
|---|---|
| Google Drive alone | No multi-file transaction and no compare-and-set on file names; the contract says not to pretend otherwise. |
| SQLite or local files | The contract rules out a long-term local-only store. |
| Azure Table Storage or Cosmos DB | Workable (ETag compare-and-set), but there are no multi-row transactions across the tables this model needs. That makes reservation plus intent plus outbox harder to prove correct. |
| Redis or ZooKeeper-style locks | Leases alone don't give a durable ledger, and the ledger is still needed. |

**Hosting** is a decision for you (D1). Azure Database for PostgreSQL Flexible Server fits the repo's Microsoft 365 direction. Any managed PostgreSQL 15+ with point-in-time recovery meets the semantics. The adapter depends only on standard SQL, so tests run against a disposable PostgreSQL in CI.

## 3. Coordinator data model

This implements the logical collections in `persistence-model.md`. Every key includes `(tenant_id, workspace_id)`, and row-level security enforces scope (section 9).

| Table | Key and constraints | Purpose |
|---|---|---|
| `ingestion_reservations` | Unique (scope, idempotency_key). Stores request_digest, ingestion_id, asset_id, pinned profile and context versions, the DGE reservation file ID, and `state` (reserving → accepted). | Idempotency (A02). IDs are allocated exactly once. |
| `commit_intents` | Primary key (scope, asset_id, version). Unique commit_id. Also stores fencing_epoch, bundle_sha256, `state` (staged → published → projected) and the marker's file ID. | **One commit ID per revision slot.** The loser of a race gets `VERSION_CONFLICT` (A03). |
| `writer_leases` | Primary key (scope, asset_id). Stores holder, fencing_epoch (monotonic) and expiry. | Fencing, so a paused or stale worker cannot publish. |
| `registry_heads` | Primary key (scope, asset_id). Stores version and marker ID, updated by compare-and-set from N−1 to N. | Direct GET reads the committed head (A07). |
| `master_index_heads` | Primary key (scope, asset_id). Stores the index row JSON and a `tsvector`, updated monotonically. | Search projection (A23). |
| `audit_events` | Unique event_id and unique (scope, asset_id, registry_version). **Insert only.** | Local copy of the event history for fast reads. |
| `content_lookup` | Non-unique (scope, original_sha256, processing_fingerprint). Only committed, successful candidates. | Duplicate lookup and reuse (A16, A17, A28). |
| `jobs` | Unique ingestion_id. | Polling view derived from heads, never authoritative. |
| `projection_queue` | Unique (commit_id, projection_name). Tracks attempts and the next retry time. | Durable outbox (A07). |

**Fencing.** A worker takes `writer_leases` for an asset, which increments `fencing_epoch`. It then inserts `commit_intents(asset, N, commit_id, epoch)`, then stages files in the DGE, then writes the marker. The marker includes the epoch. Immediately before writing the marker, the worker re-checks in one transaction that its lease epoch is still current and that the intent is still `staged`. If either check fails, it stops.

A worker that paused between that check and the marker write can only ever write the marker for **its own commit ID**. That's the one intent for slot N, and the bytes are identical, so the write is harmless. This is why the unique `(asset, version)` intent, not the lease, is the real safety guarantee. The lease exists for liveness.

## 4. DGE commit publication

**Layout.** Everything sits under the configured DGE records root. It is separate from the root holding approved specifications, and the implementation never writes there.

```
records/
  {tenant}/{workspace}/
    reservations/{sha256(idempotency_key)}.json
    assets/{asset_id}/rev-{N:08d}/{commit_id}/
        registry.json   audit.json   index.json   commit.json   <- marker, written last
```

Tenant and workspace segments are percent-encoded. Names are never taken from untrusted filenames. Folder names are for people browsing the Drive. Readers never infer that a revision is committed from names or times. They enumerate markers and verify them.

**Payloads.** UTF-8 with sorted keys, compact separators and no NaN, as the contract specifies. `audit.json` carries `registry_sha256` and `index_sha256` over the exact stored bytes.

**Marker (`commit.json`)**, an adapter-private format:

```json
{
  "format": "paios.commit/1", "contract_release": "1.0.0",
  "scope": {...}, "asset_id": "...", "ingestion_id": "...",
  "registry_version": 3, "commit_id": "...", "event_id": "...", "fencing_epoch": 17,
  "files": {"registry.json": {"file_id": "...", "sha256": "...", "bytes": 1234},
            "audit.json":    {...}, "index.json": {...}},
  "previous_marker_sha256": "... hash of rev-2 commit.json, or null for rev 1 ...",
  "published_at": "2026-09-23T01:02:03Z"
}
```

`previous_marker_sha256` chains each revision to the one before it. A gap, a fork or a tampered earlier revision fails verification with `INTEGRITY_FAILED` (A20, A21).

**Publication steps**, which map to steps 4–6 of `storage-and-consistency.md`:

1. Upload the three payloads with `appProperties` set to `{commit_id, role}`.
2. Read back each file's Drive `sha256Checksum` and size, and compare them with the local values. Any mismatch fails the commit without retry until the cause is known.
3. Validate the triple against every invariant in `validate_contracts.py`'s bundle rules. The same checks move into the adapter.
4. Run the fencing check (section 3).
5. Upload `commit.json`, then read back and verify its `sha256Checksum`. **This upload is the visibility point.**
6. Lock all four files with Drive `contentRestrictions.readOnly`, so a human editor can't change them by accident.
7. In one PostgreSQL transaction, mark the intent `published`, compare-and-set `registry_heads` from N−1 to N, insert the `audit_events` row, and enqueue projections.

**Timeouts.** If step 5 times out, the worker queries for a marker with that `commit_id` before retrying (contract step 6). The same bytes mean success: return the original receipt. Different bytes are `INTEGRITY_FAILED`. The `CommitReceipt` is `{commit_id, registry_version, canonical_manifest_id = the marker's file ID, manifest_sha256, committed_at}`.

**The 202 response.** It is returned only after revision 1 (`asset_accepted`) has been published through these same steps **and** the reservation file exists in the DGE. That way the idempotency mapping can be recovered even if PostgreSQL is lost (A01, A08).

**Latency.** Drive writes take roughly 0.3–1 s each, so one revision costs about 5–8 Drive calls. Throughput is bounded by Drive API quotas. That's fine for a personal archive; batch or bulk imports need their own rate limiting (D6).

## 5. OneDrive working-media verification

- **Routing.** The issued rule, under the configured OneDrive root:
  - `{tenant}/{workspace}/ingestion/{asset_id}/original/{original_sha256}`
  - `.../normalized/png-rgb-white-v1/page-{n:06d}/{page_sha256}.png`
- **Upload.** Graph upload sessions with `@microsoft.graph.conflictBehavior=fail`. If the path already exists, the existing item is verified and reused, so the operation is idempotent by content address. No overwrite is ever issued.
- **Verification before a `StorageRef` is created** (A19):
  1. The size matches.
  2. `quickXorHash` from Graph matches one computed locally. It's the only hash OneDrive for Business guarantees; SHA-1 and SHA-256 aren't available there.
  3. The SHA-256 in `StorageRef.sha256` is computed locally from the bytes that were uploaded.
  4. Optionally, the file is read back and re-hashed with SHA-256. I propose this on by default for originals and off for normalized pages (D4).
- **Pinning.** `StorageRef` = {`connector: onedrive`, `root_id`, `item_id` = driveItem ID, `version` = the version ID from `/versions` or the eTag, `sha256`, `byte_size`}. No download URLs or tokens are stored.
- **Source pinning** (A18) is part of the `SourceConnector` work in Packet 4. It uses the same item ID and version pinning, returning `SOURCE_CHANGED` or `SOURCE_UNAVAILABLE`.
- **Synced folders.** Uploads go through Graph, never through a locally synced OneDrive folder, so cache cleanup can never touch a synced original (A22). Packet 1's `WorkCache` already refuses to overlap a protected root.

## 6. Recovery

| Failure point (`persistence-model.md`) | Behaviour |
|---|---|
| Before the reservation | Nothing is durable, and the client gets 503 with retry-after (A06). |
| After the reservation, before revision 1 is published | The reservation row and DGE file exist but no marker does. A retry with the same key resumes with the **same IDs** and re-stages. |
| Between payload uploads | The staged files are invisible because there's no marker. A retry re-uploads idempotently: the same commit_id folder and same bytes. Orphans are handled by retention (section 10). |
| Marker written, PostgreSQL not updated | On startup and periodically, a reconciler lists markers newer than the head, verifies them, and advances heads (A04, A05). |
| After commit, before the response | The retry sees `commit_intents.state = published` and returns the stored receipt. No duplicate OCR is scheduled (A05). |
| Search projection down | The outbox retries. GET reads `registry_heads`, which never lags. Projections apply revisions in order and never go backwards (A07). |
| **All local cache lost** | No effect on truth. The DGE and PostgreSQL are both off the machine (A08). |
| **PostgreSQL lost** | Restore from point-in-time recovery, then reconcile forward from DGE markers. If point-in-time recovery isn't possible, a full rebuild enumerates every reservation file and marker, verifies each hash chain, and rebuilds every table. |

In a full rebuild, two different markers for the same (asset, N) can only appear if the coordinator's uniqueness guarantee was lost. If that happens, the asset is **quarantined** (reads and writes return `INTEGRITY_FAILED`) and reported for manual resolution. There is no automatic tie-break (D5).

**Crash-injection evidence.** Fault hooks at each point in the table run against in-memory Drive and Graph fakes with injected timeouts and partial writes, plus a disposable PostgreSQL in CI. Production acceptance additionally repeats the A04–A08 runs against **nonproduction** DGE, OneDrive and PostgreSQL instances, as `promotion.md` requires.

## 7. Serving projections, read-after-write and ordered replay

- **Read-after-write.** `GET /images/{asset_id}` reads `registry_heads`, which advances in the same PostgreSQL transaction that records the publication (section 4, step 7). A client that got a receipt for revision N will read N or later. If the head row is missing or older than a known marker, the API returns 503 rather than claim an older version is current.
- **Master Index vs search.** The Master Index is the canonical `index.json` of each published revision; `master_index_heads` is its serving copy. Full-text search is a projection that may lag, and the lag is measured (section 11).
- **Ordered, idempotent projection.** Each `projection_queue` row is (commit_id, projection). A projector applies revision N only if the stored version is N−1. Otherwise it waits or requeues, so projections never regress and never skip. Replaying an applied commit is a no-op.
- **Rebuild.** Every projection can be dropped and rebuilt by replaying verified DGE markers in version order per asset. The rebuild tool compares the result with the live heads and reports differences before switching over.

## 8. Integrity checks

- The commit is rejected in full if the Registry, Audit and Index disagree on scope, version, event, metadata, source or hash (A21).
- Readers verify the marker's hash, each file's SHA-256 and the chain link before trusting a revision. Anything else fails with `INTEGRITY_FAILED` and is never repaired automatically.
- Corrections append a new revision with a reason and a predecessor link. There is no update path for events (A20).

## 9. Access controls

| Identity | DGE | OneDrive | PostgreSQL |
|---|---|---|---|
| `ingest-writer` (worker) | Create files and apply read-only locks in `records/` only. No delete. **No access to the specification root.** | Read/write under the configured ingestion root only (Graph `Sites.Selected` / folder-scoped grant) | Execute `commit_revision()`, `reserve()` and `acquire_lease()` only. No direct UPDATE or DELETE. |
| `projector` | Read `records/` | None | Update `master_index_heads`, `jobs` and `projection_queue` |
| `api-reader` | Read `records/` for verification | Read-only | SELECT, with row-level security by (tenant, workspace) |
| `maintenance` | Read. Retention deletes of **unreferenced media only**, logged. | Delete unreferenced blobs after the grace period | Cannot UPDATE or DELETE `audit_events`: a trigger rejects it for every role, including the owner outside a migration. |
| Human architects | Read/write the **specification** root. Read-only on `records/`. | As configured | None |

- **Secrets.** Service credentials live in a secret store (for example Azure Key Vault or Google Secret Manager). They never appear in the contract, config templates, logs, the Audit log or errors.
- **Scope.** Every repository call takes a `Scope`, and row-level security plus scoped keys make cross-tenant reads return `NOT_FOUND` (A17). A matching hash never bypasses authorization.
- **GPS and EXIF** stay off by default, as in Packet 1. Extracted text and metadata inherit the workspace's access policy.

## 10. Retention

| Data | Rule |
|---|---|
| Local cache | Packet 1 rules: 24 h diagnostic TTL and 5 GiB budget. Deleted after verified persistence and reader release. |
| Staged DGE files without a marker | After a grace period (proposed 7 days), deleted by `maintenance`, and **only if** no `commit_intents` row in `staged` references the commit_id. |
| Unreferenced OneDrive blobs | Same grace period, and only if no published revision, open reservation or staged intent references the item. Content-addressed paths make that check exact. |
| Published revisions and audit events | Kept for the configured `retention_policy_id`. Never deleted by ingestion or maintenance code. |
| Personal-data erasure | Not decided by this proposal (D7). My suggestion: append a redaction revision, delete the media blobs and the OCR text from future heads, and have the Audit log keep hashes and provenance rather than content. That needs a contract decision, because today's Audit events embed the index metadata snapshot. |

All deletions are recorded in an operations log that is separate from asset Audit history, so cleanup never appears to rewrite history.

## 11. Recovery observability

Metrics follow the list in the contract's acceptance definition of done:

| Metric | Alert when |
|---|---|
| Accepted and terminal counts per scope | — (dashboard only) |
| Oldest `commit_intents` still `staged` | Older than 15 min |
| Markers found by the reconciler ahead of their heads | Any, sustained for 5 min |
| Projection lag: newest published commit vs newest projected commit | Over `max_search_lag_seconds` (60 s in the config template) |
| Commit retries and `VERSION_CONFLICT` rate | Sudden increase |
| Integrity failures (hash, chain, triple mismatch) | Any |
| Quarantined assets | Any |
| Local cache use against budget | Over 80 % |
| DGE and Graph throttling responses | Sustained |

Logs carry commit, ingestion and asset IDs. They never carry paths, tokens or signed URLs. The alert destination is a deployment setting (`promotion.md`, step 4).

## 12. Operating costs (rough)

| Item | Estimate for a personal archive (about 10k assets a year) |
|---|---|
| Managed PostgreSQL (smallest burstable tier, 32 GB storage, 7-day PITR) | About US$15–30/month on Azure Flexible Server B1ms. Comparable elsewhere. |
| DGE (Google Drive) | About 4 small files per revision and 3 revisions per asset. Fits in existing Drive storage; API use stays within free quotas at this volume. |
| OneDrive | Originals plus normalized PNGs, roughly 1.5–3× the original size, in existing OneDrive storage |
| Compute | One worker. Decode costs about 0.3–1 s of process start plus decode time per asset. |

These are order-of-magnitude figures for choosing hosting (D1), not quotes.

## 13. Failure matrix (A01–A08, A17–A21)

"Fake" means an in-memory Drive or Graph fake with fault injection, plus a disposable PostgreSQL in CI. "Live" means configured **nonproduction** DGE, OneDrive and PostgreSQL roots, which `promotion.md` requires for production acceptance.

| ID | Scenario | Mechanism | Fake (CI) | Live (nonproduction) |
|---|---|---|---|---|
| A01 | 202 only after the accepted triple is readable | Revision 1 is published and the reservation file written before 202 | ✓ | ✓ |
| A02 | Retry and replay by key; changed body gives 409 | Unique reservation plus request digest | ✓ | ✓ |
| A03 | Two workers race the same revision | Unique `commit_intents(asset, N)` plus lease epoch | ✓ (threads and processes) | ✓ |
| A04 | Crash after each payload write and before the marker | No marker means invisible; resume with stable IDs | ✓ (fault hooks) | ✓ (killed worker) |
| A05 | Timeout after the marker, before the response | Look up the commit ID, then return the stored receipt | ✓ | ✓ |
| A06 | DGE down before or after acceptance | 503 before; pending recovery after | ✓ | ✓ (revoked credentials or blocked network) |
| A07 | Search projection down | Outbox retries; GET stays current | ✓ | ✓ |
| A08 | Local cache wiped | Rebuild from DGE plus PostgreSQL | ✓ | ✓ |
| A17 | Cross-tenant reads and reuse | Row-level security plus scoped keys give `NOT_FOUND` | ✓ | ✓ |
| A18 | Source version changes or disappears | Item ID and version pinning (Packet 4 connector) | ✓ | ✓ |
| A19 | OneDrive upload or verification fails | No `StorageRef` before verification; retry or durable failure | ✓ | ✓ (corrupted-upload injection only on fakes) |
| A20 | Audit mutation, deletion or stale index write | Insert-only table, trigger, read-only Drive locks, marker chain | ✓ | ✓ (attempted edits with the writer identity) |
| A21 | Triple disagreement or tampered hashes | Validation before the marker; verification on read | ✓ | ✓ |

**Requested schema or interface changes: none.** The marker and reservation formats are adapter-private, as `persistence-model.md` allows. D7 (erasure) may need a contract change later, and that would come to you as a separate change request.

## 14. What Packet 2 will deliver once approved

- PostgreSQL migrations and a DGE adapter implementing `IngestionRepository`, `AuditRepository`, `IndexRepository` and `AssetRepository`.
- A `WorkingAssetStore` for OneDrive.
- The reconciler and rebuild tooling.
- Crash-injection tests and A01–A08 and A17–A21 evidence, run against fakes in CI and against nonproduction roots for acceptance.
- Documented durability guarantees and operations recovery instructions.

Production writes stay blocked until the deployment gates in `promotion.md` are configured.

## Decisions requested

| # | Decision | My recommendation |
|---|---|---|
| D1 | Coordinator technology and hosting | Managed PostgreSQL 15+ with point-in-time recovery, for example Azure Database for PostgreSQL Flexible Server |
| D2 | Search projection for v1 | PostgreSQL full-text search inside the same database. A separate engine can come later, since it's a rebuildable projection. |
| D3 | Marker format and chain (section 4) | Accept `paios.commit/1` as described |
| D4 | OneDrive verification strength | Size plus `quickXorHash` always. Read back and re-hash with SHA-256 for originals by default. |
| D5 | Conflicting markers found during a full rebuild | Quarantine plus manual resolution, with no automatic tie-break |
| D6 | Throughput and rate limits | Accept Drive quotas for v1, with rate limiting on bulk imports |
| D7 | Personal-data erasure and retention | Needs a policy decision; see the suggestion in section 9 |
| D8 | DGE and Google identity | One Google service account for `ingest-writer`, domain-restricted, with access only to `records/` |
