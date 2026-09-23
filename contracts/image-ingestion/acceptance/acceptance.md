# Acceptance criteria and evidence matrix

Contract validation in this package is a preflight only. Claude executes the following against implementations; ChatGPT reviews evidence. Unless a criterion explicitly allows partial status, all steps must pass. Use generated/synthetic fixtures without private content; record original and normalized byte hashes, provider/decoder versions and expected outputs.

| ID | Scenario / required outcome |
|---|---|
| A01 | New connector image: POST returns 202 only after accepted triple is readable in DGE; terminal GET has completed Registry, appended terminal Audit and matching Index revision/metadata. |
| A02 | Retry same request/key before, during and after processing: same asset/ingestion; no duplicate reservation or audit revision. Changed body with same key: 409. |
| A03 | Two workers race same revision: exactly one distinct commit wins; loser reloads after VERSION_CONFLICT; identical commit replay returns same receipt. |
| A04 | Inject crash after each of three payload writes and before marker: no partial visible revision; recovery resumes stable IDs without audit duplication. |
| A05 | Inject timeout after marker before response: lookup finds committed bundle; retry returns existing result; no duplicate OCR is scheduled solely from response loss. |
| A06 | DGE unavailable before acceptance: 503 and no accepted claim. After acceptance: durable recovery pending; no local-only completion. |
| A07 | Search projection unavailable: canonical index row still committed; direct GET current; replay catches search up monotonically without regressing newer rows. |
| A08 | Delete entire local cache/workspace during accepted/terminal states: recover keys, jobs, Registry/Audit/Index from durable canonical records; originals resolve in OneDrive. |
| A09 | JPEG/PNG/WebP/BMP/TIFF/HEIC/HEIF/AVIF fixtures: correct signature routing regardless of filename; all supported pages processed; animated exclusions explicit. |
| A10 | Multi-page PDF/TIFF: contiguous pages, RGB PNG, 300 DPI PDF default, correct width/height, OCR page mapping and parent identity; no silent truncation. |
| A11 | EXIF rotation, transparency, ICC, missing metadata: orientation applied once, white alpha background, sRGB conversion; unknown date timezone preserved, not fabricated. |
| A12 | Corrupt, encrypted, unsupported and decompression-bomb fixtures: bounded execution, structured error, failed triple; no false completed or leaked temp bytes. |
| A13 | Empty-text image: successful OCR with empty string and valid text hash; unknown confidence null; non-Latin text survives UTF-8 round-trip. |
| A14 | One of multiple OCR pages fails: partial status with page-specific error, successful text retained; all-page failure: failed, null full_text. |
| A15 | Provider timeout/transient error: bounded retries, stable job; terminal failure triple committed; permanent errors not looped indefinitely. |
| A16 | Exact duplicate in same scope and matching fingerprint: new occurrence record plus relationship and duplicate_reused event, no redundant OCR; force_reprocess triggers OCR. Near-duplicate hash alone never merges. |
| A17 | Same bytes in another tenant/workspace: no reuse/existence leak. Foreign source, registry, job, relation and OCR IDs cannot be fetched through scope bypass. |
| A18 | Source version changes or disappears after admission: SOURCE_CHANGED/SOURCE_UNAVAILABLE failed triple, never ingest unpinned latest silently. |
| A19 | OneDrive upload/hash verification fails: no terminal success/reference to pending bytes; retry or durable failure; orphan cleanup never removes referenced/inflight content. |
| A20 | Attempt audit mutation/deletion or stale index write: denied; correction appends revision with reason and linked predecessor. Tampered hashes trigger INTEGRITY_FAILED. |
| A21 | Registry, Audit and Index disagree on scope/version/event/metadata/source/hash: reject entire commit. Recovery accepts only complete checksum-verified markers. |
| A22 | Local TTL/size pressure: active handles protected, released cache reclaimed; OneDrive originals untouched; no local absolute paths in canonical/public payloads. |
| A23 | Search tag/query/status and pagination: deterministic snapshot, scope-filtered rows; expired/tampered cursor rejected; projection lag observable. |
| A24 | Release mismatch Obsidian/DGE: new admissions stop; in-flight jobs stay on pinned release; no working OneDrive draft silently becomes canonical. |
| A25 | Malicious OCR text/EXIF containing instructions/URLs: stored as data, does not invoke tools, route arbitrary requests, change policy or expose secrets. |
| A26 | OCR disabled: normalized media persists, ingestion completed, OCR skipped, empty search_text; three-record invariant still enforced. |
| A27 | API validation/auth/error shape: required key, invalid UUID, unsupported schema, missing fields, unauthorized root, limits and retry headers match contract. |
| A28 | New ingestion reprocesses known content with changed options/context: bypass incompatible reuse, preserve prior immutable history, produce new results. |

Definition of done: all schemas/fixtures and protocol imports validate; all required rows pass with test evidence; cloud adapter/fencing/recovery tests run against configured nonproduction roots; no unexplained skipped criteria; no local-only persistent dependencies; secrets absent; canonical release promotion records and implementation versions supplied. Live deployment has separate configuration gates in promotion.md. Metrics include accepted/terminal counts, commit retries, oldest pending recovery, projection lag, cache use and canonical checksum failures; alert routing is configured by deployment.
