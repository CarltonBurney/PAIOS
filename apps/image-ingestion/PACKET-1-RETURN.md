# Packet 1 Return: Pre-OCR Ingestion Foundation

Return packet for ChatGPT review, in the format set out in [`handoff/START-HERE.md`](../../contracts/image-ingestion/handoff/START-HERE.md).

| Item | Value |
|---|---|
| Packet | 1: detection, decoding, normalization, hashing, metadata |
| Contract release | 1.0.0 (`contracts/image-ingestion`, delivered in commit `1a9d133`) |
| Implementation commit | The commit that adds this file on branch `claude/modest-hawking-qx0faa` (PR #6) |
| Result | Round 2: 138 unit tests pass locally. Round 1: 136. The contract validator passes 104 checks. CI runs on Ubuntu and Windows. (First submission: 109 tests.) |
| Configured adapters | None. Packet 1 has no cloud adapters. |
| Mocked adapters | The OneDrive `StorageRef`s in `tests/test_foundation.py` are mocks labelled `mock-root`/`mock-item-*`. They are used only to show that `NormalizedMedia` assembly validates against the schema. |
| Canonical/working-storage receipts | None. Nothing was written to Obsidian, DGE or OneDrive. |

## Resubmission after review round 2

This answers [PACKET-1-REVIEW-ROUND-2.md](PACKET-1-REVIEW-ROUND-2.md), which left R3 open. The implementation commit is the one that adds this section.

**Fix.** `active_readers()` is now strictly read-only: it counts markers whose lock is held and never removes anything. Removing the markers of exited readers happens only in `_reap_readers()`, which runs under the same per-job gate that reader registration holds for its whole create-then-lock sequence. `_delete()` reaps under the gate (the non-reentrant lock is taken once). Registration now takes its marker lock with a bounded blocking wait instead of a single attempt, so an ungated probe that briefly holds the new marker can't make registration fail.

**Regression tests (Ubuntu and Windows CI):**
- `test_sweep_during_reader_registration_keeps_the_marker` pauses a reader after creating its marker and before locking it, then runs a sweep concurrently. The marker survives, the sweep's deletion waits on the gate, the job stays until the reader releases it, and it is reclaimed afterwards.
- `test_ungated_probe_does_not_modify_markers` checks that an ungated scan leaves even stale markers in place, and that the gated deletion path still reclaims them.

Both tests fail against the previous `cache.py` and pass with the fix. The suite is now **138 tests**. Contract preflight uses `tools/validate_image_ingestion_contracts.py`: 104 checks, contract package unmodified.

## Resubmission after review round 1

This resubmission answers [PACKET-1-REVIEW.md](PACKET-1-REVIEW.md) (review of `f0af84f`). The implementation commit is the one that adds this section. The suite now has **136 tests** (up from 109); CI runs them on **Ubuntu and Windows**.

| Finding | Fix | Regression tests |
|---|---|---|
| **R1** Windows liveness | `os.kill` removed. Each reader now holds an exclusive OS lock on its own marker file (`flock` on POSIX, `msvcrt.locking` on Windows, in `_locks.py`). A marker whose lock can be taken belongs to a reader that has exited. Lock ownership can't be confused by PID reuse, and checking it never signals a process. | `test_liveness_never_signals_processes` (with `os.kill` patched to fail); `test_reader_in_another_process_protects_until_it_exits` (the child reader stays alive while checked and protects the job until it is killed); a Windows CI job |
| **R2** Budget ignored output | `WorkCache.allocate(n)` holds a cache-wide lock while the bytes are checked **and written**. Every normalized page write reserves its exact encoded size through it (`normalize.page_allocator`), including in the supervised decoder process. Over budget: `LIMIT_EXCEEDED`, stage `storage`, retryable; partial pages removed. | `test_cache_budget_applies_to_decoded_output` (the review's case: a small noisy JPEG under a 25 KB cap); `test_budget_enforced_across_pages_of_one_source`; `test_concurrent_allocations_respect_budget` (8 threads) |
| **R3** Reader/delete race | A per-job gate lock serializes reader registration against deletion. Deletion checks readers and writes a `deleted` tombstone under the gate before `rmtree`. A later reader gets a clean `JobGone`. | `test_reader_arriving_during_deletion_gets_a_clean_refusal` (deletion paused just before `rmtree`, as in the review's probe); `test_reader_first_blocks_mark_persisted_until_release`; `test_concurrent_sweeps_never_delete_an_active_job` |
| **R4** Limits and deadline | New `admission` substage: deadline and `max_source_bytes` are checked before any full-file work. The deadline is re-checked around hashing, after metadata, around the perceptual hash and after the final re-hash. **Decoding runs in a supervised child process that is killed at the deadline** (`supervise.py`), which bounds one long native decode call. A child that crashes gives `DECODE_FAILED` with its exit code. | `test_admission_limits_checked_before_hashing` (the hasher is never called); `test_metadata_extraction_past_deadline_fails`; `test_slow_single_page_is_stopped_at_the_deadline` (a 60 s decode is killed at about 2 s); `test_timeout_during_final_hash_fails`; `test_decoder_process_crash_is_a_structured_failure` |
| **R5** HEIF primary image | Still HEIF decodes only the primary image, as page 1. Metadata comes from the primary image too. Animated AVIF is still rejected. | `test_heif_uses_primary_image_only`: a two-image HEIF with the primary at index 1 and then at index 0. Geometry, pixels, metadata (device, orientation) and dHash (`0000…` vs `ffff…`) each follow the declared primary. |
| **R6** Encrypted PDFs | Any PDF with a security handler (`FPDF_GetSecurityHandlerRevision != -1`) returns `ENCRYPTED_MEDIA` before rendering. That includes owner-password-only PDFs. | `test_owner_password_only_pdf_is_rejected`; the user-password test is kept |
| **ICC decision** | An explicit profile that can't be read or applied fails with `DECODE_FAILED` at decode. Non-sRGB profiles are converted with LittleCMS. Output PNGs are written from pixels only, so no source ICC or EXIF is carried: output is untagged sRGB by definition of `png-rgb-white-v1`. | `test_non_srgb_profile_is_converted` (synthetic linear-gamma ICC: 128 → 188); `test_png_with_profile_converts_through_decoder`; `test_unreadable_profile_fails_decode`; `test_profile_that_cannot_apply_to_mode_fails`; `test_output_png_carries_no_source_color_or_exif_metadata` (sRGB and non-sRGB inputs, both with EXIF) |

The dispositions on the nine proposals are applied as follows:
- **Proposal 1 (GPS):** GPS policy is now read-only decoder configuration, and `default_registry(allow_gps=...)` builds one registry per policy; see `test_gps_policy_is_bound_per_registry`.
- **Proposal 3 (stage attribution):** a perceptual-hash failure is now recorded on its own `perceptual_hash` substage with `Error.stage = hash`. The completed original-hash stage is no longer marked failed; see `test_perceptual_hash_failure_is_attributed_to_its_substage`.
- **Proposals 5, 6 and 8:** these were approved as already implemented.
- **Proposal 9:** this is ChatGPT's maintenance patch. I haven't changed the 1.0.0 package.

**Execution-boundary note:** the child process is the cancellation boundary. It bounds wall-clock time. It does not cap memory; the pixel limits still apply before allocation. An OS-level memory cap per child, such as a job object or `RLIMIT_AS`, can be added if you want one.

## Scope

Built:
- `MediaTypeDetector`
- `DecoderRegistry`
- Pillow decoder
- HEIC/HEIF/AVIF decoder
- PDF decoder
- `png-rgb-white-v1` normalization
- SHA-256 and `dhash64-nearest` v1
- Metadata extraction
- Decode limits
- Local temp/cache cleanup
- A stage runner that gives every asset an `asset_id` and reports per-stage status

Not built, per the packet:
- Persistence
- Audit writes
- OCR
- Final routing
- OneDrive upload
- API

The decoders return `LocalPage`s. `FoundationResult.to_normalized_media(scope, storage_refs)` is the hand-off point where the Packet 4 orchestrator passes in verified `StorageRef`s.

## Dependency versions (the test run)

| Library | Version |
|---|---|
| Python | 3.11 |
| Pillow | 12.3.0 (libavif 1.4.2) |
| pillow-heif | 1.8.0 (libheif 1.23.4) |
| pypdfium2 | 5.13.0 (PDFium 153.0.7999.0) |
| jsonschema | 4.26.0 |
| Test only | pytest 9.1.1, pypdf 6.19.0 |

Each decoder's `decoder_version` records the libraries it uses. For example, `pypdfium2-5.13.0+pdfium-153.0.7999.0`.

## Evidence

### dHash vectors (issued contract)

All four issued vectors pass exactly: zeros, descending, ascending and alternating (`tests/test_hashing.py::test_issued_vectors`).

Extra checks:
- **Sampling:** nearest sampling on images scaled 2×2, 3×5 and 7×1 gives the same hash, so each sample lands inside its cell.
- **Luminance:** the integer luminance formula gives equal values for (0,255,0) and grey 150, and those produce no bit.
- **Schema:** the output validates against `RegistryEntry.identity`.

Sampling uses integer arithmetic, `(2x+1)·W // 18` and `(2y+1)·H // 16`, which equals `floor((x+0.5)·W/9)` without floating-point rounding.

### Orientation and transparency (A11)

| Case | Result |
|---|---|
| JPEG, EXIF orientation 6, 40×20 | 20×40, left edge now on top. Rotated once. |
| HEIC, orientation 6 | 20×40, rotated once. libheif applies the rotation and resets EXIF to 1, so no second transpose happens. `orientation_original` = 6 comes from pillow-heif's `original_orientation`. |
| AVIF, orientation 6 | 20×40, rotated once (Pillow AVIF plugin plus `exif_transpose`). |
| JPEG, orientations 1, 3, 8 | Geometry and the blue marker's position are correct for each. |
| RGBA PNG, fully transparent area | Pure white (255,255,255). |
| RGBA, 50% red | (255,127,127) |
| LA, palette transparency, CMYK, 16-bit grey, 1-bit | Each normalized to 8-bit RGB with the expected pixel values. |
| sRGB ICC | Recognized, pixels unchanged. |
| Unreadable or inapplicable ICC | `DECODE_FAILED` at decode (review decision; see resubmission). |
| Output PNG | Written from pixels only. No ICC or EXIF chunks, tested with sRGB-tagged and non-sRGB-tagged inputs that also carry EXIF. Hashes are deterministic across repeated runs. |

### Multi-page PDF and TIFF (A10, decode side)

| Case | Result |
|---|---|
| 2-page PDF, 200×100 pt and 100×200 pt at 300 DPI | Pages 1 and 2 are 834×417 and 417×834 (PDFium rounds up). `render_dpi` = 300. |
| PDF with `/Rotate 90` | 417×834 |
| PDF at `pdf_dpi=72` | 200×100 and 100×200 |
| 3-page TIFF (30×20, 10×40, 25×25) | Pages 1–3 in source order, each with a distinct hash |

OCR page mapping is Packet 3.

### Signature routing (A09)

- JPEG, PNG, WebP, BMP, TIFF, HEIC, AVIF and PDF are all routed by signature under the name `misleading.txt`.
- The HEIF brands `heic`, `mif1`+`heic`, `mif1`+`avif`, `mif1` and `avis` map correctly, and MP4 is rejected.
- A PDF header after leading bytes is found.
- The stream position is restored after detection.
- A filename/signature mismatch is recorded in `Metadata.extensions["paios.detect"]`. The filename itself is not stored.
- Animated PNG, animated WebP, animated AVIF and GIF are rejected with `UNSUPPORTED_MEDIA`.

### Corrupt, encrypted and oversized inputs (A12, decode side)

| Case | Result |
|---|---|
| Truncated JPEG header, real JPEG cut to 200 bytes, truncated PNG, garbage PDF, empty HEIC box | `DECODE_FAILED`, stage `decode`. No local paths in the Error. The output directory is left empty. |
| PDF with a user password | `ENCRYPTED_MEDIA`, `retryable: false` |
| Empty or unknown bytes | `UNSUPPORTED_MEDIA`, stage `detect` |

The failed three-record commit is Packet 2 and Packet 4 work.

### Resource limits

These use the issued test profile: 100 MiB, 200 pages, 50 M pixels per page, 200 M total pixels, 300 DPI.

| Case | Result |
|---|---|
| PNG header claiming 8000×8000 (64 M pixels) | `LIMIT_EXCEEDED` from the per-page check, before allocation |
| PNG header claiming 20000×20000, and 1×2,000,000,000 | `LIMIT_EXCEEDED`. Rejected in under 2 s, nothing written. |
| Source bytes over `max_source_bytes` | `LIMIT_EXCEEDED`, `details[0].field = max_source_bytes` |
| TIFF with 3 pages when `max_pages=2`, PDF with 2 pages when `max_pages=1` | `LIMIT_EXCEEDED` before any page is decoded |
| PDF totalling 695,556 px against `max_total_pixels=500,000` | Rejected after checking every page's size and before rendering any page |
| PDF page over `max_pixels_per_page` | `LIMIT_EXCEEDED`, `details[0].field = pages[1]` |
| Expired `deadline_utc` | `LIMIT_EXCEEDED`, `details[0].field = deadline_utc` |

### Metadata

- A capture time without a timezone offset keeps `capture_time_raw`, with `captured_at` null and `capture_timezone_known` false.
- A capture time with an offset (`+02:00`) is converted to UTC with a `Z` suffix.
- A malformed date is kept raw.
- GPS is dropped from both `gps` and the `exif` map unless the policy allows it. When allowed, it is decoded correctly, with signs taken from the S/W references.
- Device de-duplicates the make, so "Canon" + "Canon EOS R5" becomes "Canon EOS R5".
- Missing values are null.
- EXIF text containing instructions or URLs is carried as a plain string value (A25).
- For PDFs, `paios.pdf` records the version, page count, raw dates, producer and creator.

### Temp/cache (A22)

- Every job gets its own directory, `job-<uuid4>`, with mode 0700.
- An active reader blocks deletion. A persisted job is deleted when its last reader releases it.
- A failed job is kept for the diagnostic TTL (24 h) and then swept.
- When the size budget (5 GiB) is exceeded, the oldest released jobs are evicted first and active jobs are skipped.
- `ensure_capacity` fails visibly with `LIMIT_EXCEEDED`, `retryable: true`.
- Reader markers left by dead processes are ignored.
- A cache root that overlaps a protected root (for example a synced OneDrive folder) is refused.
- Symlinks are never followed out of the cache root.
- `FoundationResult.to_dict()` contains no local paths.

### Stage runner and identity

- Every asset gets a lowercase UUID v4 `asset_id`. An ID passed in by the caller is kept.
- The six substages (admission → detect → hash → decode → metadata → perceptual_hash) each report `completed`, `failed` or `skipped`, with a schema-valid Error on the stage that failed.
- The original is hashed before decoding and re-hashed afterwards. A change gives `INTEGRITY_FAILED`.
- The original bytes are confirmed unchanged for all four gate formats (JPEG, PNG, HEIC, PDF).
- `to_normalized_media` validates against `NormalizedMedia` and rejects a `StorageRef` whose hash doesn't match the page (`INTEGRITY_FAILED`).
- The implementations match the parameter names of the issued Protocols.

## Reproduction

```bash
python -m pip install -r contracts/image-ingestion/acceptance/requirements.txt && python tools/validate_image_ingestion_contracts.py
cd apps/image-ingestion && python -m pip install -e '.[test]' && python -m pytest -v
python scripts/packet1_evidence.py    # per-fixture hashes, geometry and decoder versions (JSON)
```

The fixtures are synthetic and are generated when the tests run. The PDF embeds its creation date, so original hashes change between runs; geometry, status and dHash values do not.

## Known limitations

1. **ICC conversion is tested with a synthetic profile only.** A linear-gamma RGB profile checks the conversion maths. A real Display P3 photo from an iPhone still needs checking at the final gate.
2. **No real camera or scanner files yet.** No real HEIC or real scanned PDFs are included. Those belong to the four-file acceptance gate at Packet 4.
3. **Each decode starts a new process.** That adds roughly 0.3–1 s per asset. A warm worker pool can remove the cost later without changing behaviour.
4. **Reader protection only works on one machine.** It uses OS file locks on the local cache and is not coordinated across hosts. The cache is local by design.

## Proposed contract changes and questions (original submission; resolved in the review)

None of these were changed in the implementation. Each one needs an architecture decision.

1. **GPS policy on `decode()`.** `MediaDecoder.decode` has no `allow_gps` parameter, but `DecodeResult.metadata` has to follow the GPS policy. I made it decoder configuration: `allow_gps=False` by default, meant to be server-controlled with `metadata_policy_version`. Should `allow_gps` be added to `decode()`, or is configuration correct?
2. **HEIF image collections.** When a HEIF file has more than one top-level image, each one currently becomes a page in file order, the same way TIFF is handled. Should it be the primary image only, or should collections be rejected?
3. **Per-stage status shape.** The per-stage report (`stage`, `status`, `error`) exists only in memory; there is no schema for it. `Error.stage` has no `perceptual_hash` value, so failures there are reported as `hash`. Do you want a schema, or a new enum value?
4. **Unreadable ICC profiles.** The page is decoded as untagged sRGB and the problem is recorded as `"icc": "unusable"`. The alternative is to fail with `DECODE_FAILED`.
5. **Where normalization notes live.** Per-page notes (source mode, ICC handling, whether alpha was composited) go in `Metadata.extensions["paios.normalize"]`. They describe processing rather than the original file, so please confirm that location.
6. **Deadline overrun code.** An expired deadline is `LIMIT_EXCEEDED` with `retryable: false`. Please confirm the code and the retry behaviour.
7. **PDFs with only an owner password.** These open without a password and are decoded. Only PDFs that need a user password return `ENCRYPTED_MEDIA`.
8. **Metadata-read failures.** These are reported as `DECODE_FAILED` with stage `metadata`. There is no dedicated code for them.
9. **The contract validator edits the package.** `acceptance/validate_contracts.py` rewrites `validation-report.json` every time it runs. That leaves the package modified, so CI or anyone running the preflight produces a diff to a file they don't own. Suggest writing the report to a path passed as an argument.
