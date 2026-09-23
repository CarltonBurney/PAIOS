"""Shared decoder lifecycle: limits, failure mapping and cleanup of partial output."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .. import contract
from ..errors import failure
from ..metadata import OriginalMetadataExtractor
from ..normalize import PixelBudget, check_deadline, check_source_size


class BaseDecoder:
    decoder_id: str
    decoder_version: str
    mime_types: frozenset[str] = frozenset()

    def __init__(self, metadata_extractor: contract.MetadataExtractor | None = None,
                 allow_gps: bool = False):
        # GPS policy is immutable server configuration bound to the workspace's
        # metadata_policy_version: build one decoder per policy, never mutate a shared one.
        self.metadata_extractor = metadata_extractor or OriginalMetadataExtractor()
        self._allow_gps = allow_gps

    @property
    def allow_gps(self) -> bool:
        return self._allow_gps

    def supports(self, detected_mime_type: str) -> bool:
        return detected_mime_type in self.mime_types

    def decode(self, source: Path, detected_mime_type: str, output_dir: Path,
               limits: contract.Limits) -> contract.DecodeResult:
        if not self.supports(detected_mime_type):
            raise failure("CONTRACT_MISMATCH", "decode",
                          f"{self.decoder_id} does not support {detected_mime_type}")
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        try:
            check_deadline(limits)
            check_source_size(source, limits)
            pages, original_format, notes = self._decode(
                source, detected_mime_type, output_dir, limits, PixelBudget(limits), written)
            check_deadline(limits)
            metadata = self.metadata_extractor.extract(source, detected_mime_type, self.allow_gps)
            metadata["extensions"]["paios.normalize"] = {"pages": notes}
            contract.validate("Metadata", metadata)
            check_deadline(limits)  # metadata extraction must not push a late result through
            return contract.DecodeResult(
                pages=pages, metadata=metadata, decoder_id=self.decoder_id,
                decoder_version=self.decoder_version, original_format=original_format)
        except contract.PipelineFailure:
            self._discard(written)
            raise
        except Image.DecompressionBombError:
            self._discard(written)
            raise failure("LIMIT_EXCEEDED", "decode", "Image dimensions exceed decoder safety limits",
                          details=[("max_pixels_per_page", "rejected before allocation")]) from None
        except MemoryError:
            self._discard(written)
            raise failure("LIMIT_EXCEEDED", "decode", "Decoding exhausted available memory") from None
        except Exception as exc:
            self._discard(written)
            # Library messages can echo paths; only the exception class is surfaced.
            raise failure("DECODE_FAILED", "decode", "Source could not be decoded",
                          details=[("source", type(exc).__name__)]) from None

    def _decode(self, source, mime, output_dir, limits, budget, written):
        raise NotImplementedError

    @staticmethod
    def _discard(written: list[Path]) -> None:
        for path in written:
            path.unlink(missing_ok=True)
