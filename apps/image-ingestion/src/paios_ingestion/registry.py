"""Deterministic decoder registry: select by verified MIME, then configured priority."""
from __future__ import annotations

from typing import Iterable

from . import contract
from .errors import failure


class PriorityDecoderRegistry:
    """DecoderRegistry. Higher priority wins; an equal-priority tie is CONTRACT_MISMATCH."""

    def __init__(self, entries: Iterable[tuple[int, contract.MediaDecoder]] = ()):
        self._entries: list[tuple[int, contract.MediaDecoder]] = []
        for priority, decoder in entries:
            self.register(decoder, priority)

    def register(self, decoder: contract.MediaDecoder, priority: int = 0) -> None:
        if any(d.decoder_id == decoder.decoder_id for _, d in self._entries):
            raise ValueError(f"decoder already registered: {decoder.decoder_id}")
        self._entries.append((priority, decoder))

    def resolve(self, detected_mime_type: str) -> contract.MediaDecoder:
        candidates = sorted(
            ((p, d) for p, d in self._entries if d.supports(detected_mime_type)),
            key=lambda item: (-item[0], item[1].decoder_id),
        )
        if not candidates:
            raise failure("UNSUPPORTED_MEDIA", "decode",
                          f"No decoder is registered for {detected_mime_type}",
                          details=[("detected_mime_type", detected_mime_type)])
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            tied = sorted(d.decoder_id for p, d in candidates if p == candidates[0][0])
            raise failure("CONTRACT_MISMATCH", "decode",
                          f"Ambiguous decoder selection for {detected_mime_type}",
                          details=[("decoder_id", ", ".join(tied))])
        return candidates[0][1]


def default_registry() -> PriorityDecoderRegistry:
    from .decoders import HeifDecoder, PdfDecoder, PillowDecoder

    return PriorityDecoderRegistry([(10, PillowDecoder()), (10, HeifDecoder()), (10, PdfDecoder())])
