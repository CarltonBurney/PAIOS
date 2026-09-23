"""Packet 1 stage runner: detect -> hash -> decode/normalize -> metadata -> perceptual hash.

Produces local normalized pages and per-stage status for the orchestrator
(Packet 4). It performs no persistence, audit, OCR or routing: the
orchestrator uploads pages, builds StorageRefs and calls to_normalized_media.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import contract
from .cache import JobDirectory, WorkCache
from .detect import SignatureMediaTypeDetector, detection_extension
from .errors import failure, make_error
from .hashing import ContentHashService
from .normalize import NORMALIZATION_PROFILE
from .registry import default_registry

STAGES = ("detect", "hash", "decode", "metadata", "perceptual_hash")


def new_asset_id() -> str:
    return str(uuid.uuid4())


@dataclass
class StageStatus:
    stage: str
    status: str = "pending"  # pending | completed | failed | skipped
    error: dict | None = None

    def to_dict(self) -> dict:
        return {"stage": self.stage, "status": self.status, "error": self.error}


@dataclass
class FoundationResult:
    asset_id: str
    status: str = "processing"  # completed | failed
    stages: list[StageStatus] = field(default_factory=lambda: [StageStatus(s) for s in STAGES])
    original_sha256: str | None = None
    original_byte_size: int | None = None
    detected_mime_type: str | None = None
    decode: contract.DecodeResult | None = None
    perceptual_hash: dict | None = None
    error: dict | None = None
    job: JobDirectory | None = None

    def stage(self, name: str) -> StageStatus:
        return next(s for s in self.stages if s.stage == name)

    def to_dict(self) -> dict:
        """Status report without local paths (never persisted or returned by APIs)."""
        return {
            "asset_id": self.asset_id,
            "status": self.status,
            "stages": [s.to_dict() for s in self.stages],
            "original_sha256": self.original_sha256,
            "original_byte_size": self.original_byte_size,
            "detected_mime_type": self.detected_mime_type,
            "decoder_id": self.decode.decoder_id if self.decode else None,
            "decoder_version": self.decode.decoder_version if self.decode else None,
            "page_count": len(self.decode.pages) if self.decode else None,
            "perceptual_hash": self.perceptual_hash,
            "error": self.error,
        }

    def to_normalized_media(self, scope: contract.Scope,
                            storage_refs: Mapping[int, dict]) -> dict:
        """Assemble NormalizedMedia once the orchestrator has verified StorageRefs per page."""
        if self.status != "completed" or self.decode is None:
            raise ValueError("normalized media exists only for completed decodes")
        pages = []
        for page in self.decode.pages:
            ref = storage_refs[page.page_number]
            if ref["sha256"] != page.sha256:
                raise failure("INTEGRITY_FAILED", "storage", "StorageRef hash does not match page",
                              details=[(f"pages[{page.page_number}]", "sha256 mismatch")])
            pages.append({
                "page_number": page.page_number, "width": page.width, "height": page.height,
                "format": "png", "mime_type": "image/png", "color_mode": "RGB",
                "orientation_applied": True, "render_dpi": page.render_dpi,
                "sha256": page.sha256, "storage_ref": dict(ref),
            })
        media = {
            "schema_version": contract.CONTRACT_RELEASE,
            "asset_id": self.asset_id,
            "scope": {"tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id},
            "original_sha256": self.original_sha256,
            "original_byte_size": self.original_byte_size,
            "detected_mime_type": self.detected_mime_type,
            "original_format": self.decode.original_format,
            "normalization_profile": NORMALIZATION_PROFILE,
            "decoder_id": self.decode.decoder_id,
            "decoder_version": self.decode.decoder_version,
            "pages": pages,
            "metadata": self.decode.metadata,
        }
        contract.validate("NormalizedMedia", media)
        return media


class IngestionFoundation:
    def __init__(self, cache: WorkCache, *, detector=None, registry=None, hasher=None):
        self.cache = cache
        self.detector = detector or SignatureMediaTypeDetector()
        self.registry = registry or default_registry()
        self.hasher = hasher or ContentHashService()

    def prepare(self, source: Path, filename_hint: str | None, limits: contract.Limits,
                asset_id: str | None = None) -> FoundationResult:
        """Run every pre-OCR stage. Failures are returned as structured status, not raised."""
        result = FoundationResult(asset_id=asset_id or new_asset_id())
        job = self.cache.new_job()
        result.job = job
        current = "detect"
        try:
            with job.reader():
                with open(source, "rb") as stream:
                    result.detected_mime_type = self.detector.detect(stream, filename_hint or "")
                self._done(result, "detect")

                current = "hash"
                result.original_sha256 = self.hasher.sha256(source)  # before any decoding
                result.original_byte_size = source.stat().st_size
                self._done(result, "hash")

                current = "decode"
                self.cache.ensure_capacity(result.original_byte_size)
                decoder = self.registry.resolve(result.detected_mime_type)
                decoded = decoder.decode(source, result.detected_mime_type, job.output_dir, limits)
                self._done(result, "decode")

                current = "metadata"  # extracted by the decoder from the original bytes
                decoded.metadata["extensions"].update(
                    detection_extension(result.detected_mime_type, filename_hint))
                contract.validate("Metadata", decoded.metadata)
                result.decode = decoded
                self._done(result, "metadata")

                current = "perceptual_hash"
                result.perceptual_hash = self.hasher.perceptual_hash(decoded.pages[0])
                self._done(result, "perceptual_hash")

                current = "hash"
                if self.hasher.sha256(source) != result.original_sha256:
                    raise failure("INTEGRITY_FAILED", "hash", "Original bytes changed during processing")
                result.status = "completed"
        except contract.PipelineFailure as exc:
            reported = exc.error["stage"]
            self._fail(result, reported if reported in STAGES else current, exc.error)
        except Exception as exc:
            stage = "hash" if current == "perceptual_hash" else current
            self._fail(result, current, make_error(
                "INTERNAL_ERROR", stage, "Unexpected failure", details=[("stage", type(exc).__name__)]))
        return result

    @staticmethod
    def _done(result: FoundationResult, stage: str) -> None:
        result.stage(stage).status = "completed"

    @staticmethod
    def _fail(result: FoundationResult, stage: str, error: dict) -> None:
        result.status = "failed"
        result.error = error
        result.decode = None
        result.perceptual_hash = None
        failed = result.stage(stage)
        failed.status, failed.error = "failed", error
        for s in result.stages:
            if s.status == "pending":
                s.status = "skipped"
        if result.job is not None:
            result.job.mark_failed()
