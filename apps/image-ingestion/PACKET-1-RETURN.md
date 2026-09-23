# Packet 1 Return: Pre-OCR Ingestion Foundation

Return packet for ChatGPT review, in the format set out in [`handoff/START-HERE.md`](../../contracts/image-ingestion/handoff/START-HERE.md).

| Item | Value |
|---|---|
| Packet | 1: detection, decoding, normalization, hashing, metadata |
| Contract release | 1.0.0 (`contracts/image-ingestion`, delivered in commit `1a9d133`) |
| Implementation commit | The commit that adds this file on branch `claude/modest-hawking-qx0faa` (PR #6) |
| Result | 109 unit tests pass. The contract validator passes 104 checks. |
| Configured adapters | None. Packet 1 has no cloud adapters. |
| Mocked adapters | The OneDrive `StorageRef`s in `tests/test_foundation.py` are mocks labelled `mock-root`/`mock-item-*`. They are used only to show that `NormalizedMedia` assembly validates against the schema. |
| Canonical/working-storage receipts | None. Nothing was written to Obsidian, DGE or OneDrive. |

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
| Unreadable ICC | Reported as `"icc": "unusable"`, and the image is treated as untagged (see proposal 4). |
| Output PNG | No EXIF or ICC chunks. Hashes are deterministic across repeated runs. |

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
- The five stages (detect → hash → decode → metadata → perceptual_hash) each report `completed`, `failed` or `skipped`, with a schema-valid Error on the stage that failed.
- The original is hashed before decoding and re-hashed afterwards. A change gives `INTEGRITY_FAILED`.
- The original bytes are confirmed unchanged for all four gate formats (JPEG, PNG, HEIC, PDF).
- `to_normalized_media` validates against `NormalizedMedia` and rejects a `StorageRef` whose hash doesn't match the page (`INTEGRITY_FAILED`).
- The implementations match the parameter names of the issued Protocols.

## Reproduction

```bash
cd contracts/image-ingestion && python -m pip install -r acceptance/requirements.txt && python acceptance/validate_contracts.py
cd ../../apps/image-ingestion && python -m pip install -e '.[test]' && python -m pytest -v
python scripts/packet1_evidence.py    # per-fixture hashes, geometry and decoder versions (JSON)
```

The fixtures are synthetic and are generated when the tests run. The PDF embeds its creation date, so original hashes change between runs; geometry, status and dHash values do not.

## Known limitations

1. **ICC conversion is only lightly tested.** It is implemented with LittleCMS through `ImageCms`, but only sRGB and unreadable profiles are covered. A real Display P3 or Adobe RGB fixture, such as an iPhone photo, is needed.
2. **No real camera or scanner files yet.** No real HEIC or real scanned PDFs are included. Those belong to the four-file acceptance gate at Packet 4.
3. **Deadlines are checked between pages, not inside a single decode.** One very slow page can overrun the deadline by that page's decode time.
4. **Reader protection only works on one machine.** It relies on PID liveness and is not coordinated across hosts.

## Proposed contract changes and questions

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
