# Image Ingestion

Implementation of the PAIOS image ingestion pipeline, built to the contract package in [`contracts/image-ingestion`](../../contracts/image-ingestion) (release 1.0.0). The plan and packet sequence are in [`docs/IMAGE_INGESTION_BUILD_PLAN.md`](../../docs/IMAGE_INGESTION_BUILD_PLAN.md).

**Status:** Packet 1 (pre-OCR foundation) resubmitted after review round 1. See [PACKET-1-RETURN.md](PACKET-1-RETURN.md) and [PACKET-1-REVIEW.md](PACKET-1-REVIEW.md). The Packet 2 storage proposal is in [proposals/PACKET-2-STORAGE-PROPOSAL.md](proposals/PACKET-2-STORAGE-PROPOSAL.md).

## Packet 1 scope

| Component | Module |
|---|---|
| `MediaTypeDetector`: routes by content signature; filenames are hints only | `paios_ingestion/detect.py` |
| `DecoderRegistry`: selects by MIME type, then priority; a tie fails with `CONTRACT_MISMATCH` | `paios_ingestion/registry.py` |
| Pillow decoder: JPEG, PNG, WebP, BMP, multi-page TIFF | `paios_ingestion/decoders/pillow_decoder.py` |
| HEIC/HEIF (libheif) and AVIF (libavif) decoder | `paios_ingestion/decoders/heif_decoder.py` |
| PDF decoder (PDFium, 300 DPI default) | `paios_ingestion/decoders/pdf_decoder.py` |
| `png-rgb-white-v1` normalization and decode limits | `paios_ingestion/normalize.py` |
| SHA-256 and `dhash64-nearest` v1 perceptual hash | `paios_ingestion/hashing.py` |
| Metadata extraction from the original bytes | `paios_ingestion/metadata.py` |
| Per-job local temp/cache with TTL, size budget and reader protection | `paios_ingestion/cache.py` |
| Stage runner with structured per-stage status | `paios_ingestion/foundation.py` |
| Supervised decode: child process killed at the deadline | `paios_ingestion/supervise.py` |
| Cross-platform file locks for the cache | `paios_ingestion/_locks.py` |

The interface dataclasses, protocols and JSON Schema are loaded from the contract package at runtime (`paios_ingestion/contract.py`), so this code never keeps its own copy of a shared shape.

Out of scope for this packet: persistence, audit writes, OCR, routing, OneDrive uploads and the HTTP API.

## Running the tests

```bash
cd apps/image-ingestion
python -m pip install -e '.[test]'
python -m pytest -v
python scripts/packet1_evidence.py   # fixture hashes, geometry and decoder versions as JSON
```

CI runs the contract validator and these tests in `.github/workflows/validate-image-ingestion.yml`.
