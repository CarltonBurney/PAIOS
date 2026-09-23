# Storage of truth and commit protocol

## Authority by role

| Location | Authoritative responsibility |
|---|---|
| Obsidian vault | Human-readable approved architecture, decisions, workflows, AVPB ReadMe and release pointers |
| Dedicated DGE Google Drive area | Matching approved machine contracts; durable operational record bundles, append-only event history, Master Index rows and release manifests |
| OneDrive | Primary original/normalized media, working project assets, large design packets, exports and OCR raw artifacts when needed |
| Local machine | Bounded transient downloads, decode/OCR execution and rebuildable caches only |

Obsidian and DGE are one governed canonical set, not independent last-writer-wins authorities. Each approved release has one release ID and checksums; Obsidian points to the corresponding DGE manifest. A mismatch stops new admissions with CONTRACT_MISMATCH while already admitted jobs retain their pinned release. DGE holds operational records; the Obsidian operational inventory is a generated view of that record set. Do not create one Markdown file per audit event in a locally synced vault as the durability mechanism.

The database and search engine are operational serving projections. They do not supersede canonical DGE commits. Their hosting is deliberately unselected; any coordinator/replay ledger must be durable outside this local machine and recoverable from committed records. Choosing its technology is Claude's proposal, subject to these semantics.

## Three-record invariant

For each asset revision N, publish exactly one immutable Registry snapshot, Audit event and Master Index row in a single logical CommitBundle. All use the same scope/asset ID; registry and index versions equal N; the Registry and Index point to the Audit event. Audit stores the full index metadata snapshot as required by the user. It also contains hashes of the exact UTF-8 Registry and Index payload files. Events cannot be overwritten, patched or deleted through ingestion APIs. Corrections append a new revision with a reason. Version N has predecessor N−1; the first is version 1 with null previous event.

The **Master Image Index** means the latest committed index row per asset, including accepted/failed rows; the full-text search engine is a separate rebuildable projection. Thus a delayed search engine does not waive the mandatory Index update. Registry/Index views advance only through published bundles; partially staged records are invisible.

## Required repository behavior

1. Authorize scope and reserve the idempotency key using a durable, serialized coordinator. Compute request digest from sorted compact UTF-8 JSON with all explicit request fields (no NaN); tenant scope and contract release are part of that digest. Allocate IDs once. An interrupted reservation must be resumable with the same IDs.
2. Build revision 1: accepted Registry, asset_accepted Audit, nonsearchable-text Index. Acceptance is returned only after this bundle is canonically published and the key mapping can be recovered. Source bytes may still be unavailable; SHA, media and OCR can be null at this stage.
3. For processing/terminal transitions, first durably upload necessary original and normalized files to OneDrive and verify size/hash/version. A record must not point to a cache path or a pending upload. Pin provider item IDs and versions; do not persist expiring download URLs or credentials.
4. Stage immutable `registry.json`, `audit.json`, `index.json` in the configured DGE records root, under scope/asset/revision/commit ID. Hash the exact stored payload bytes; JSON serialization for these files is UTF-8, sorted keys, compact separators, no NaN. Hash references do not cover the Audit itself, avoiding a circular hash.
5. Validate the triple; publish a checksummed manifest/commit marker only when all objects are acknowledged durable. The coordinator must atomically enforce expected asset version, unique event/commit IDs, and reservation ownership. Publication is the sole visibility point. The logical commit result includes DGE receipt and revision. A drive folder sync or three independent file writes alone does NOT satisfy this contract.
6. On timeout, query the same commit ID before retrying. Same ID and same bytes returns the original receipt; same ID and different bytes is a conflict. Readers enumerate only published manifests, verify hashes, and never infer commitment from filename or upload time.
7. Update serving projections and acknowledge the worker. Search can lag; direct GET must read the committed head (or return 503), never claim an older version is current. After cache loss, rebuild Registry/Index/jobs/key mappings from DGE commits and reservation metadata.

No distributed atomicity is assumed between cloud services. OneDrive uploads precede DGE publication, so failure can leave unreferenced blobs, never falsely successful assets. A retention job may reclaim verified unreferenced blobs after a configured grace period, never while a reservation/commit is unresolved. No cleanup module is included here.

Claude must prove the selected coordinator and DGE adapter provide single-writer fencing/serialization and crash recovery. Do not pretend Google Drive itself provides a multi-file transaction. Until those capabilities are demonstrated, production writes are blocked; interface/mock implementation can proceed. Coordinator outage or canonical store outage yields 503 before acceptance, or leaves an accepted job pending recovery. A durable retry queue retries terminal failure commits; it must not declare failed/completed only in local memory.

State graph: accepted → processing → completed | partial | failed. Completed/partial/failed are terminal for that ingestion. A new request performs reprocessing as a new asset occurrence linked with `supersedes`; no terminal-to-processing mutation of an old job. Corrections may append terminal revisions without changing ingestion outcome. Worker retries before terminal completion retain the same ingestion ID; each actual state revision appends a new event. A crash can cause repeated computation, but never repeated committed revisions for the same commit ID.

## Cache and retention

Cache paths are process-local capability handles only. They never enter canonical schemas, logs or public API results. Delete temporary bytes after verified remote persistence and release of active readers, under a bounded TTL/size budget. Failed work remains only for the configured diagnostic TTL; unresolved durable records remain remote. Cache cleanup must not delete originals from a synced OneDrive folder. Downloads use version-specific content and verify hashes before reuse. Large persistent media stays in OneDrive; no long-term local-only SQLite or folder is acceptable as truth.

GPS/EXIF and extracted text inherit workspace access policy. Default GPS extraction is disabled unless the configured project policy permits it. Credentials, passwords, raw provider tokens and signed URLs are never schema fields. Audit records preserve provenance without including secrets. Compliance retention/deletion policy is a deployment gate, not an implied promise of permanent personal-data retention.
