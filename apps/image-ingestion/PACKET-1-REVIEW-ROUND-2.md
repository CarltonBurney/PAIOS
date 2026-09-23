# Packet 1 review — round 2

Reviewed commit: `1a29007b82c5ec8966ccf6024819b00d73d204af`, PR #6. Baseline: contract release 1.0.0. **Verdict: one remaining cache-concurrency correction; acceptance pending.** This is a working review, not canonical promotion or deployment approval.

## Evidence

GitHub Actions [run 35805036386](https://github.com/CarltonBurney/PAIOS/actions/runs/35805036386) completed successfully. Logs show Ubuntu **136 passed**, Windows **135 passed, 1 skipped** (the symlink test), and **104 contract checks** on each platform. The revised cache suite also ran locally on Windows: **13 passed, 1 skipped**. Native HEIF execution on the review machine remains blocked by its application-control policy; the Linux/Windows CI results are the execution evidence for that adapter.

## Disposition of the first review

| Item | Result |
|---|---|
| R1: Windows process check | Addressed. Reader liveness uses held OS file locks instead of signaling PIDs. Cross-process and Windows coverage was added. |
| R2: output capacity | Addressed for normalized file writes. The allocator serializes the check and write against actual encoded bytes; expansion/concurrent-allocation regressions were added. This is a disk-cache budget, not a cap on decoder RAM. |
| R3: reader/delete race | The original check-versus-rmtree race is addressed by the gate and tombstone, but an unlocked stale-marker scan leaves the registration race below. Keep R3 open. |
| R4: limits/deadlines | Admission runs before full hashing; late results fail; a child process bounds native decoding, and metadata/final-hash tests cover deadline expiry. Original review cases are addressed. Blocking source I/O and RAM limits still belong in the documented deployment execution envelope; post-operation checks must not be described as preemptive cancellation of every function. |
| R5: primary HEIF image | Addressed by primary selection, primary metadata and both primary-index regression cases. |
| R6: encrypted PDFs | Addressed by security-handler detection before rendering, including owner-only encryption. |
| ICC decision | Addressed: explicit unusable profiles fail, real conversion is tested with a synthetic linear profile, and output is rebuilt from pixels to avoid carrying source metadata. |

## Remaining finding: P1 — stale-marker cleanup must share the registration gate

Location: `apps/image-ingestion/src/paios_ingestion/cache.py`, `JobDirectory.active_readers()`, `JobDirectory.reader()` and `WorkCache.sweep()`.

The reader takes the gate, creates its marker with `open(..., "xb")`, then acquires the marker's OS lock. `sweep()` calls `job.active_readers()` **without taking that gate**. `active_readers()` treats any marker whose lock it can acquire as stale and unlinks it. On POSIX, this interleaving is possible:

1. The reader holds the gate and creates the marker, but pauses before `try_lock(marker)`.
2. A sweep scans outside the gate, acquires the still-unlocked marker, releases it and unlinks it as stale.
3. The reader resumes, locks its already-open but now-unlinked file descriptor, releases the gate and starts using the job.
4. `_delete()` acquires the gate, finds no marker in the directory, and deletes an actively read job.

The gate around `_delete()` cannot protect a marker already removed by the earlier scan. The new tests pause before rmtree or start with fully registered readers; neither schedules this creation-to-lock interval. This is a source-inspection finding specific to POSIX unlink semantics, not a claim that the Windows cache suite failed.

**Correction:** serialize every scan that removes stale markers with reader registration under the same gate. Separate an internal gate-already-held scan from the public gated operation to avoid taking a non-reentrant OS lock twice in `_delete()`. Alternatively make ungated scans strictly nonmutating and re-check under the gate before cleanup. Treat uncertain registration conservatively as active.

**Required regression:** on Ubuntu, pause after marker creation and before its lock acquisition; concurrently run sweep/stale cleanup. Verify the marker survives registration and the job remains until the reader releases it. Then verify an actually dead reader is still reclaimable. Keep the existing Windows, deletion-interleaving and cross-process tests.

Do not expand Packet 1 into storage/OCR work. Submit the small R3 correction and its regression as the next implementation commit. The Packet 2 development scope below can proceed independently.

## ChatGPT-owned validator maintenance is completed

The repository now provides `tools/validate_image_ingestion_contracts.py`. It runs the original 1.0.0 validator against an isolated temporary copy, prints the result, and optionally writes `--report` outside the contract package. It refuses an in-package report path. CI uses this entrypoint.

Validation: 104 checks passed; an explicit external report was produced; an in-package report was rejected; every source-package file remained byte-identical. The immutable 1.0.0 schema/interface package and its manifest are unchanged. This avoids silently rewriting a published release or requiring a new data-contract version for a tooling fix. The old package-local command remains historical; use the new repository entrypoint going forward.

## What may proceed

Claude may implement the bounded Packet 2 foundation authorized in `proposals/PACKET-2-ARCHITECTURE-DECISION.md` while finishing this correction. Packet 1 acceptance, live cloud publication, deployment and the real-file end-to-end gate remain separate decisions. No production cloud resources were created or configured by this review.
