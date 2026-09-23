# Packet 1 architecture and implementation review

**Verdict: Changes required; Packet 1 is not yet accepted.**

Reviewed implementation: [`f0af84f0394d84fde68822c973aafb40e1af4598`](https://github.com/CarltonBurney/PAIOS/commit/f0af84f0394d84fde68822c973aafb40e1af4598), PR #6, branch `claude/modest-hawking-qx0faa`. Contract baseline: `1.0.0`, delivered in `1a9d133`. This review is a working architecture/QA record; it does not promote specifications to Obsidian/DGE or approve deployment. No runtime implementation was changed during review.

The module split, original-byte hashing, issued dHash vectors, explicit error objects, synthetic fixtures and clearly labeled storage mocks fit Packet 1. The six-outcome final gate remains Packet 4 work. Passing the current suite does not cover the failures below.

## Verified evidence and limits of this review

- GitHub Actions [run 35802923780](https://github.com/CarltonBurney/PAIOS/actions/runs/35802923780), job 106997134625: **109 tests passed in 0.71 s**, and **104 contract checks passed**, on Ubuntu/Python 3.11.16. Logs confirm Pillow 12.3.0, pillow-heif 1.8.0/libheif 1.23.4, pypdfium2 5.13.0/PDFium 153.0.7999.0.
- Reviewed source, fixtures and workflow from that exact commit; verified the 28 implementation/workflow files and 55 contract files against Git blob hashes. The contract package is unchanged from its delivered baseline.
- Windows/Python 3.12 local review: excluded the seven cache tests because the process-liveness operation is unsafe on Windows. The remaining attempt produced **74 passes, 16 failures and 12 setup errors**; all reported failures/errors were tied to the host blocking the pillow-heif native library with an Application Control policy. These are an environment limitation, not 28 independently established implementation defects. No policy bypass was attempted.
- Additional bounded synthetic probes, using the unchanged source and an explicitly injected Pillow-only registry where necessary, reproduced R1, R2, R3, R4 and R6 below. HEIF collection behavior (R5) is established by source inspection and the return packet, not a successful local libheif run.
- Real iPhone HEIC, real scanned PDF and a genuine non-sRGB color fixture were not available. No cloud records or real user assets were used.

## Required implementation corrections

### R1 — P1: Use a non-destructive, platform-correct process-liveness check

Location: [`cache.py:28–35`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/cache.py#L28).

`_pid_alive()` uses `os.kill(pid, 0)` as a Unix liveness probe. On Windows, Python documents non-control-event signals as terminating the target process; this is not a portable existence check. A probe targeting only its own disposable child process on this host instead raised uncaught `OSError: [WinError 87]`, so the function still failed rather than returning liveness. See [Python's os.kill documentation](https://docs.python.org/3.13/library/os.html#os.kill).

Cache sweep/mark_persisted calls this on active-reader PIDs. Replace it with a Windows-safe process query or a portable library that performs one; handle dead, inaccessible and invalid PIDs without sending termination signals. Include process creation identity or equivalent lease ownership to avoid mistaking a reused PID for the original reader. Add Windows CI and a disposable-child regression that proves both the reader and checking process remain alive. Do not test this against unrelated live processes.

### R2 — P1: Enforce cache capacity against output growth, not source compression size

Location: [`foundation.py:131–134`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/foundation.py#L131), with [`normalize.py:140–145`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/normalize.py#L140).

The sole reservation is `ensure_capacity(original_byte_size)` before decoding. Normalized PNGs can be much larger than a JPEG or PDF input; page writes neither reserve space nor enforce the cache budget. Reproduction: seed-5 random 256×256 RGB image encoded as JPEG quality 10, **7,828-byte source**, cache budget **25,000 bytes**; prepare returned `completed` with **152,440 bytes** in cache. The declared bounded local cache is not bounded by this path.

Account for normalized output and write buffering, enforce capacity as pages are produced, and coordinate reservations across jobs so concurrent writers cannot each claim the same free space. Fail visibly before exceeding the budget; keep cleanup within the job directory and never evict active readers. Add compressed-input expansion, multi-page growth and concurrent reservation regressions. No cloud persistence implementation is needed for this fix.

### R3 — P1: Serialize reader acquisition with cache deletion

Location: [`cache.py:89–99`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/cache.py#L89) and [`cache.py:136–142`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/cache.py#L136).

Deletion checks the reader markers, then calls `rmtree` without synchronization with `reader()`. A new reader can acquire a marker after that check and before removal. A deterministic two-thread probe paused immediately before `rmtree`, acquired a reader, then resumed deletion: `_delete` returned true and the job directory was gone while the reader context was still active. This is a same-machine race, not the documented lack of cross-host coordination.

Use an atomic acquisition/deletion protocol under an appropriate cross-process lock or equivalent lease/tombstone mechanism. A reader must either acquire a live job that cannot be deleted until release, or receive a clean unavailable result. Add a scheduled-interleaving test for reader-versus-sweep and reader-versus-mark_persisted; do not rely only on sequential context-manager tests.

### R4 — P1: Enforce size/deadline limits through the entire operation

Locations: [`foundation.py:126–149`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/foundation.py#L126) and [`decoders/base.py:36–45`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/decoders/base.py#L36).

The foundation hashes the entire input before the decoder checks max_source_bytes or the deadline. A 112-byte PNG with max_source_bytes=1 still invoked SHA-256 before rejection. A decoder also checks its deadline before metadata extraction, but not afterwards: an injected 0.3-second metadata extractor returned a successful page after a 0.15-second deadline had passed (observed duration 0.312 s). Perceptual hashing and the second source hash also run without a deadline check.

Check admission limits before full-file work, bound hashing/metadata/normalization and verify the deadline before returning success. Between-page checks alone cannot bound a single native decoder call; provide a supervised execution/cancellation boundary, or propose a concrete execution boundary that enforces it before claiming the bounded-execution criterion is met. Add expired-before-hash, timeout-during-metadata, slow-single-page and timeout-during-final-hash tests. Keep the existing Error code decision below; this finding is about enforcement.

### R5 — P2: Select the primary HEIF image only

Location: [`decoders/heif_decoder.py:32–39`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/decoders/heif_decoder.py#L32), delegated to [`decoders/pillow_decoder.py:17–35`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/decoders/pillow_decoder.py#L17).

The shared loop emits every top-level HEIF frame as a page. The issued architecture already says still HEIF/AVIF uses the primary image; multi-page behavior is for PDF/TIFF. This changes page_count, OCR inputs and which image supplies the perceptual hash, particularly when the primary image is not the first frame.

Implement primary-image selection and produce one normalized page numbered 1 for a still HEIF collection. Add a two-image HEIF fixture with the second image declared primary, and prove geometry, pixels, metadata and dHash all refer to that image. Continue rejecting animated AVIF as contracted. This is a correction to an existing requirement, not approval for collection expansion.

### R6 — P2: Reject all encrypted PDFs under the current contract

Location: [`decoders/pdf_decoder.py:24–32`](https://github.com/CarltonBurney/PAIOS/blob/f0af84f0394d84fde68822c973aafb40e1af4598/apps/image-ingestion/src/paios_ingestion/decoders/pdf_decoder.py#L24).

Only PDFium's password-required open error is rejected. A PDF encrypted with an empty user password and a nonempty owner password opens successfully and is rendered. Reproduction with a synthetic blank-page PDF: the independent reader reported `is_encrypted=true`, yet the decoder returned one page. The contract says encrypted PDFs are ENCRYPTED_MEDIA; it does not limit that to interactive-password failures.

Inspect encryption state and reject before rendering, including owner-only/empty-user-password PDFs. Keep the existing nonempty-user-password regression and add this case. A future policy allowing authorized encrypted files requires an explicit contract revision.

## Architecture decisions on the nine proposals

These are architecture-owner dispositions for the resubmission. They do not authorize Claude to edit the immutable contract release or expand packet scope.

| Proposal | Decision |
|---|---|
| 1. GPS policy argument | **Approve server-controlled decoder configuration.** No decode signature change is needed. Default false; bind the setting to the admitted workspace/context and metadata_policy_version. Do not mutate one shared decoder's policy between concurrent workspaces. |
| 2. HEIF collections | **Primary image only.** Already specified; fix R5. Do not expand to all images or reject supported still collections by default. |
| 3. Internal stage status | **Keep the internal report for Packet 1.** It is not a canonical/API record. `perceptual_hash` is a local substage; Error.stage=`hash` is appropriate. No new public enum/schema is approved. Preserve the precise internal failed substage and do not misattribute a perceptual-hash failure to a completed original-hash stage. |
| 4. Unreadable ICC | **Fail with DECODE_FAILED at decode.** If an explicit profile cannot be read/applied, do not silently label its pixels normalized sRGB. Untagged input may retain the documented/default untagged assumption. Replace the test that blesses unreadable-profile success. Add a valid non-sRGB conversion fixture with expected transformed pixels; a synthetic validated profile is sufficient for Packet 1. |
| 5. Normalization notes | **Approve Metadata.extensions["paios.normalize"].** Keep per-page notes clearly labeled as processing provenance and separate from source EXIF/capture fields. |
| 6. Deadline error | **Approve LIMIT_EXCEEDED with retryable=false for the expired attempt.** Retrying with the same expired deadline cannot work. A newly authorized attempt may have a fresh deadline. Fix enforcement in R4. |
| 7. Owner-only encrypted PDF | **Reject under v1; fix R6.** Successful passwordless opening is not evidence that the file is unencrypted. |
| 8. Metadata-read failure | **Approve DECODE_FAILED with stage=metadata.** Preserve sanitized errors; do not synthesize metadata success when extraction fails. |
| 9. Validator output | **Accept the proposal for a ChatGPT-owned contract maintenance patch.** Default to stdout or an explicit external `--report` target; leave the baseline contract package untouched. Do not let normal preflight rewrite files covered by the release manifest. Publish/version that maintenance change separately with regenerated evidence; Claude should not patch 1.0.0 itself. |

The unreadable-ICC decision is an additional correction required for resubmission. A normalization probe also found that an sRGB ICC profile survives into the output PNG: the return document's blanket claim “no EXIF or ICC chunks” is not supported by its single unprofiled JPEG test. Embedded output sRGB is not itself a contract violation; make the statement/tests accurate, permit only deliberate output-color metadata, and never carry an unusable source profile into a successful normalized artifact. In the probe, GPS and EXIF text were removed and the policy-disabled Metadata.gps stayed null; no GPS leak is claimed.

## Packet 2 proposal may proceed now

**Yes: Claude may draft the Packet 2 storage proposal in parallel with correcting Packet 1. This is permission to propose, not to implement storage or change contracts.**

The proposal must identify:

1. The durable coordinator/ledger and its actual compare-and-set, idempotency, fencing and crash-recovery guarantees; which data it stores and how it survives total local loss.
2. The DGE publication protocol for a checksum-verified Registry/Audit/Index bundle, with one visibility point and explicit recovery at every upload/marker boundary. Do not assume Google Drive provides a multi-file transaction.
3. Version-pinned OneDrive originals/normalized assets, verification before canonical references, safe retry and orphan handling.
4. Serving database/search projections, read-after-write behavior, ordered replay and rebuilding from canonical DGE records.
5. Tenant/workspace isolation, separate specification-versus-record permissions, service identities, configured roots, secrets handling, retention, recovery observability and operating costs.
6. A failure matrix mapped to A01–A08 and A17–A21, with what is mocked, what requires live nonproduction cloud testing, and any requested schema/interface changes.

Keep Obsidian/DGE specifications read-only to implementation work. Only the future authorized runtime service writes operational records to its separate configured root. Cloud roots, coordinator technology and first OCR provider are still unselected deployment decisions.

## Resubmission and final-file gate

Return a new implementation SHA, an updated Packet 1 return document, and a result for R1–R6 plus the ICC decision. Include the new regressions, supported-platform CI results and any remaining execution-boundary proposal. Preserve Packet 1 scope: no OCR, persistence, audit writes, final routing or API implementation. ChatGPT will review the corrected code and evidence before acceptance.

For the eventual four-file integrated gate, obtain an authorized real **iPhone HEIC photo** and **scanned PDF**, plus JPEG/PNG representatives. A HEIC with actual camera orientation/color metadata and a multi-page scanned PDF will exercise gaps that generated fixtures do not. Keep those originals immutable in approved working storage; do not commit private photos/documents to the repository. Record fixture hashes, expected orientation/page order, policy treatment of GPS and expected OCR observations in the gate evidence. Their absence does not prevent drafting Packet 2, and synthetic decoder regressions can be fixed now; final end-to-end acceptance remains pending.
