# Packet 2 Publication and Recovery: Revised Proposal

**Status:** proposal for focused review. It is the second unit requested in [PACKET-2-ARCHITECTURE-DECISION.md](PACKET-2-ARCHITECTURE-DECISION.md). Nothing here is implemented as a live publisher or cloud writer, and nothing here changes the contract.

> **Review outcome (PACKET-2-REVIEW-ROUND-1.md):** R1, R3 and R5 approved. **R2 and R4 were not approved as written; their replacement is [PACKET-2-R2-R4-REVISION.md](PACKET-2-R2-R4-REVISION.md).** The P2-B procedure and the P2-E guarantee below are superseded by that document.

**Replaces:** sections 4–6 of [PACKET-2-STORAGE-PROPOSAL.md](PACKET-2-STORAGE-PROPOSAL.md), for P2-A to P2-E. The database side already exists as unit 1 ([PACKET-2-UNIT-1-RETURN.md](../PACKET-2-UNIT-1-RETURN.md)); this document says how the publisher would use it.

## Guarantees claimed, and their limits

1. **One revision per slot.** At most one set of revision-N bytes is ever published for an asset. This holds while PostgreSQL's uniqueness holds and every recovery completes the credential fence in P2-B before resuming. If an administrator skips the fence, readers detect a fork through the marker chain and quarantine the asset (D5). Nothing picks a winner.
2. **Byte-identical replay.** Any retry or takeover writes exactly the frozen bytes to exactly the frozen object IDs. Nothing is regenerated after freezing.
3. **No stale reads presented as current.** A reader never gets an older head while a newer revision may already be visible; it gets 503 instead.
4. **Remote bytes verified.** Every StorageRef and every DGE object is verified by a SHA-256 computed from the stored remote bytes, not from what was sent.
5. **History is append-only for runtime identities, and tampering is detected for administrators.** This is not WORM storage (P2-E).

## P2-A: Stable identity and byte-identical replay

**Object identity.**
- The publisher pre-generates Drive IDs with `files.generateIds` (`space=drive`, `type=files`) for:
  - the intent record
  - `registry.json`, `audit.json` and `index.json`
  - `commit.json`
  - the reservation record
- The IDs are stored in PostgreSQL *before* any upload: `commit_intents.object_ids` for the five intent objects, and a new `ingestion_reservations.record_object_id` column (migration 0002) for the reservation record.
- Every create passes the pre-generated `id`. If a create with an existing ID fails with 409, the publisher does not retry blindly. It reads the file's `sha256Checksum` and compares it with the frozen SHA-256:
  - equal: the earlier attempt succeeded
  - different: `INTEGRITY_FAILED`, and the asset is quarantined
- Names, folder paths and `appProperties` are for people browsing the Drive. They are never used to find or deduplicate objects.

**Frozen bytes.** Unit 1 already freezes the canonical payload bytes and the complete marker bytes, including `published_at`. That field means "publication time assigned at freeze", so a takeover publishes the original value.

The marker carries no lease or generation field. That is deliberate: fencing is enforced outside the bytes (P2-B), so a new generation can publish the same bytes.

**Bundle identity.** `bundle_sha256` is SHA-256 over canonical JSON of:
- `commit_id`
- `registry_version`
- the contract release
- the three payload SHA-256 values

The marker lists the payload SHA-256 values and the previous marker's SHA-256. Nothing hashes itself: the marker's own SHA-256 is what the next revision chains to, and what the receipt reports.

**Intent record (`intent.json`, new format `paios.intent/1`).** This is the first object uploaded for a commit. It lets a new coordinator finish the intent after losing PostgreSQL (P2-B). It holds:
- scope, asset ID, ingestion ID, `registry_version` and `commit_id`
- `object_ids` for all five objects
- the exact payload bytes and marker bytes, base64-encoded
- `bundle_sha256` and `marker_sha256`
- `frozen_at`

It holds no credentials.

**Reservation record (`reservation.json`, format `paios.reservation/1`).** It holds:
- `format` and `contract_release`
- scope
- `idempotency_key_sha256` (the raw key is not stored)
- `request_digest` and the normalized request
- `ingestion_id` and `asset_id`
- `pinned` (processing profile and context versions)
- `first_commit_id` (the revision-1 commit)
- `created_at`

It holds no credentials. **Uniqueness comes from PostgreSQL**, not the filename, which has no uniqueness in Drive. The record exists so a new coordinator can recover after losing PostgreSQL.

### Crash cases between reservation and marker

| Crash point | State left behind | Recovery |
|---|---|---|
| After the PostgreSQL reservation, before `reservation.json` | Database row only | A retry with the same key replays the same IDs and writes `reservation.json` to its stored ID. |
| After `reservation.json`, before the revision-1 intent | Row plus record | Resume. After PostgreSQL loss, the record restores the row with the same IDs. |
| After the intent is frozen in PostgreSQL, before `intent.json` | Database row only; no Drive object exists | Re-execute. After PostgreSQL loss, nothing exists anywhere, and P2-B guarantees the old writer can no longer upload `intent.json`. |
| After `intent.json`, before or between payloads | Intent record plus some payloads | Re-execute from the frozen intent, from PostgreSQL or from `intent.json`. Existing IDs are verified by SHA-256; missing ones are created with the same bytes. |
| After the marker, before `record_published` | Marker visible in DGE | The intent is `publishing`, so GET returns 503. The reconciler verifies the marker and records it. |
| After `record_published`, before the response | Committed | A retry finds the stored receipt (A05). |

## P2-B: Recovery after PostgreSQL loss fences old publishers

**Where the guarantee is enforced.** The database check in `mark_publishing` is made before a network call, so on its own it cannot stop a paused writer that resumes later. The publication-boundary guarantee therefore comes from **credentials**:

- Each publisher generation writes to Drive with a short-lived token. It is obtained by service-account impersonation (`generateAccessToken`, lifetime ≤ 15 minutes) under a per-generation grant.
- Entering recovery:
  1. Set `publisher_state` to `recovery`. This closes admissions and publication and advances the generation. Unit 1 implements this step, and it waits for in-flight commits.
  2. Revoke the old generation's ability to mint tokens by removing its impersonation grant.
  3. **Wait the maximum token lifetime**, so any token already issued has expired.
  4. Only then reconcile.

After the wait, no pre-crash publisher can create or modify objects in the records root. Enforcement is in Drive's access control, not in the publisher's own checks.

**Reconciliation**, in the new generation:
1. List every `reservation.json`, `intent.json` and `commit.json` under the records root, using Drive queries on `appProperties.role`.
2. Rebuild or verify the reservation and intent rows. Imported intents get the new generation (`adopt_intent`); their bytes and IDs are never changed.
3. For each asset, verify the marker chain and advance heads to the latest verified marker. Two different intents or markers for one slot mean quarantine.
4. Re-execute every unpublished intent with its frozen bytes. **No new commit is allocated for any slot that has an `intent.json`**, even an incomplete one.
5. Resume (`resume(generation)`).

**Tests** (unit 1 covers the database half; the publisher unit adds the rest):
- An old writer resumes after restore and is refused at the database and at Drive. Drive refusal is simulated with a revoked-credential fake.
- A new generation adopts the same intent and publishes identical bytes.
- An `intent.json` that exists without a PostgreSQL row is completed, not reallocated.

## P2-C: Direct GET never serves a stale head as current

Normal operation is already implemented in unit 1. From `mark_publishing` until `record_published`, `get()` returns a retryable 503 for that asset, and the tests cover concurrent GET after the marker upload.

- **Marker published, but the SQL record failed.** The intent stays `publishing`, so the asset keeps returning 503 until the reconciler records it. The reconciler scans `publishing` intents older than N seconds and verifies their markers.
- **Startup and PostgreSQL restore.** While `publisher_state.mode = 'recovery'`, every GET returns 503 until reconciliation (P2-B step 4) has confirmed each head against the latest verified DGE marker. Restored heads are not trusted until then.
- **Staleness from outside.** Someone could add a marker that PostgreSQL never froze. That can't happen through the service, only through a credential compromise or an administrator. Periodic reconciliation detects it (P2-E) and quarantines the asset. GET does not scan Drive on each request.

## P2-D: Pinned remote bytes and version semantics

**Rule.** No StorageRef is created until the SHA-256 of the **stored remote bytes of the pinned version** matches. Graph has no `sha256Hash`, so the publisher downloads the pinned version (`/items/{id}/versions/{versionId}/content`) and hashes it. Size and `quickXorHash` are checked too, as extra checks only.

**`StorageRef.version` meaning.** It is a **historical, retrievable driveItemVersion ID**, never an eTag or cTag. eTags are concurrency tokens and aren't used for retrieval. After an upload:
1. List the versions.
2. Take the newest version whose downloaded SHA-256 equals the expected value.
3. If the newest version doesn't match, someone wrote between the upload and the check: fail with `STORAGE_UNAVAILABLE`, then retry or quarantine. Content-addressed paths plus `conflictBehavior=fail` make this rare.

**Reads.** Reads fetch exactly `item_id@version` and verify the SHA-256.
- A missing version returns `SOURCE_UNAVAILABLE` for sources or `INTEGRITY_FAILED` for stored assets.
- A changed version returns `SOURCE_CHANGED`.
- There is never a fallback to the latest version.

The same rule applies to Packet 4 source pinning. Version retention in the OneDrive library has to keep pinned versions; that setting is a deployment gate.

**Fake tests** already model these cases: a change between upload and verification, a pruned version and a lost response. The writer that runs against the fakes is part of the publisher unit.

## P2-E: Append-only enforcement

| Store | Prevented for runtime identities | Only detected for administrators |
|---|---|---|
| PostgreSQL | UPDATE, DELETE and TRUNCATE on revision and audit tables (grants plus trigger; tested for `paios_app`, `paios_ops` and the owner) | A superuser or owner disabling the trigger |
| DGE (Drive) | The publisher identity only creates files by pre-generated ID. Humans have Viewer access on the records root. | Drive has **no write-once role**: an editor or organizer can change content, and content restrictions can be lifted. |

**Detection** works through two independently administered stores:
- PostgreSQL holds the SHA-256 of every published object and marker.
- DGE holds the chained markers.

A scheduled verifier re-downloads the published objects and checks the hashes and the chain. Any mismatch is `INTEGRITY_FAILED` and the asset is quarantined. Published file revisions are set `keepForever`, so Drive keeps the original bytes even after an edit. Tampering goes undetected only if one actor controls both stores and the verifier.

**Trust boundary.**
- **Publication service identity:** create-only use of the records root; the service never updates or deletes.
- **Break-glass administrator:** a separate account holds the Organizer role and database superuser rights. It isn't used by services, and its use is logged in the Workspace and PostgreSQL audit logs.
- **Nonproduction proof:** the permission tests run with the real proposed identities in a test shared drive.

No WORM store is proposed or assumed.

## Tests for the publisher unit

These add to A01–A08 and A17–A21:

1. A takeover publishes byte-identical intents and markers.
2. An old writer after restore is refused at the database and with revoked credentials.
3. GET returns 503 between the marker upload and the SQL record, and during startup reconciliation.
4. A version changes between upload and verification; a pruned pinned version.
5. A published object is edited, then detected and quarantined.

All five run against the fakes in CI and against nonproduction roots once they're configured. Evidence reports will keep fixture results and live results separate.

## Decisions requested

| # | Decision | Recommendation |
|---|---|---|
| R1 | Accept `intent.json` as the first uploaded object, containing the full frozen bytes | Yes. Without it, a new coordinator can't finish or safely forget an interrupted intent. |
| R2 | Credential fencing: short-lived impersonated tokens plus waiting out the maximum token lifetime in recovery | Yes, with a 15-minute maximum lifetime |
| R3 | `StorageRef.version` means a historical driveItemVersion ID | Yes. It's a connector rule inside the existing field, with no schema change. |
| R4 | Accept "prevented for runtime identities, detected for administrators" as the v1 append-only guarantee | Yes, with keep-forever revisions and a scheduled verifier |
| R5 | Migration 0002: `ingestion_reservations.record_object_id`, and an `intent_record` object in `commit_intents.object_ids` | Yes. Additive, before any live data exists. |
