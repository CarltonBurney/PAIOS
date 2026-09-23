# Packet 1 — issued decoder/metadata contract

This answers the eight blockers reported in PR #6's working plan. The full plan was subsequently reviewed at PR head 7cfce260660618449e6403bbdf94c4aa5cc6a19f. These are architecture-owner decisions for implementation; canonical promotion remains pending.

| Input | Issued decision |
|---|---|
| asset_id | Lowercase UUID v4, generated once per admitted source occurrence. Identical idempotency key/request reuses it; equal bytes with a new key have a distinct occurrence ID. |
| Status/error | Registry/Job: accepted, processing, completed, partial, failed. Adapter failures expose Error through PipelineFailure, including stage/retryable. OCR pages: completed/failed. See schemas/state graph. |
| Metadata | Metadata schema: original orientation, raw/UTC capture date, device, policy-controlled GPS, color profile, EXIF and namespaced extensions. Unknown scalars are null. |
| Normalization | RGB 8-bit sRGB PNG; orientation applied once; alpha on white. No automatic downscale. Test profile: source ≤100 MiB, ≤200 pages, ≤50 million pixels/page, ≤200 million pixels total. PDF at 300 DPI; one normalized page per source page. Any exceeded limit fails visibly. |
| Perceptual hash | `dhash64-nearest`, version `1`, 16 lowercase hex characters, first normalized page only. Exact procedure below. Advisory similarity only; never identity/automatic merge. |
| Decoder registry | MediaTypeDetector, DecoderRegistry.resolve, MediaDecoder.supports/decode, LocalPage/DecodeResult in interfaces/contracts.py. Verified MIME routing; deterministic priority; ambiguity fails explicitly. |
| Temp/cache | Isolated per-job directory, active-reader protection, 24-hour diagnostic TTL and 5 GiB test budget. Delete after verified persistence and reader release. Never delete synced originals. No persistent/API local paths. |
| OneDrive routing | Configured root, logical route `{tenant}/{workspace}/ingestion/{asset_id}/original/{original_sha256}` and `.../normalized/png-rgb-white-v1/page-{page_number:06d}/{page_sha256}.png`. Never use untrusted filenames as path segments. StorageRef holds opaque item/version IDs. Adapter mapping must remain deterministic and scope-isolated. |

Limits are issued development/test defaults; production requires reviewed configuration. Changing normalization or hash semantics requires a new profile/version. Enforce budgets before allocation and during decode, not just at admission.

## Exact hash procedure

Use the first normalized RGB page. For x=0..8, y=0..7, sample `sx=min(W-1,floor((x+0.5)*W/9))`, `sy=min(H-1,floor((y+0.5)*H/8))`. Integer luminance is `floor((299*R+587*G+114*B+500)/1000)`. For y=0..7 and x=0..7, output 1 when L[y,x] > L[y,x+1], else 0. Concatenate row-major, first comparison most-significant; serialize exactly 16 lowercase hex characters. This nearest-sampling variant avoids library resize defaults. Compare matching algorithm/version only. No similarity threshold is approved in v1.

Vectors for a 9×8 grayscale RGB image, all rows identical: zeros → `0000000000000000`; 255,224,192,160,128,96,64,32,0 → `ffffffffffffffff`; 0,32,64,96,128,160,192,224,255 → `0000000000000000`; 255,0,255,0,255,0,255,0,255 → `aaaaaaaaaaaaaaaa`.

## Six-output acceptance interpretation

The verified plan defines six end-to-end outcomes: **stored asset, Registry entry, Audit history, Master Index entry, OCR result, retrievable API record**. Preserve that gate for valid JPEG, PNG, HEIC and PDF. StorageRef/NormalizedMedia describe the stored asset; Metadata is nested in NormalizedMedia; OCRResult is embedded in Registry; GET returns that Registry. The API record is a retrieval view, not a sixth independently mutable database. The three mandatory durable record families are Registry, Audit and Index. Failed decoding leaves normalized/OCR null and records failure in the triple. Packet 1 unit tests prove decoding/metadata only; the six-outcome gate applies at integrated Packet 4. The earlier provisional six-object interpretation is superseded by this verified mapping.

Packet 1 can begin against these contracts with labeled storage mocks. Production acceptance requires configured cloud adapters and promotion. Return fixture evidence, decoder versions, resource-limit results and proposed contract changes to ChatGPT.
