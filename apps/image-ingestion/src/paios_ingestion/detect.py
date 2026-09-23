"""Signature-based media type detection. Filenames are hints only."""
from __future__ import annotations

import struct
from pathlib import PurePath
from typing import BinaryIO

from .errors import failure

_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs"}
_AVIF_BRANDS = {b"avif", b"avis"}
_HEIF_BRANDS = {b"mif1", b"msf1", b"mif2"}

_EXTENSION_HINTS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    ".bmp": "image/bmp", ".dib": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".heic": "image/heic", ".heif": "image/heif", ".hif": "image/heif",
    ".avif": "image/avif", ".pdf": "application/pdf",
}

_HEAD_BYTES = 1024  # PDF header may be preceded by junk within the first 1024 bytes


def _isobmff_mime(head: bytes) -> str | None:
    if len(head) < 16 or head[4:8] != b"ftyp":
        return None
    box_size = struct.unpack(">I", head[0:4])[0]
    if box_size < 16:
        return None
    major = head[8:12]
    compatible = {head[i:i + 4] for i in range(16, min(box_size, len(head)) - 3, 4)}
    if major in _HEIC_BRANDS:
        return "image/heic"
    if major in _AVIF_BRANDS:
        return "image/avif"
    if major in _HEIF_BRANDS or compatible & (_HEIF_BRANDS | _HEIC_BRANDS | _AVIF_BRANDS):
        if compatible & _HEIC_BRANDS:
            return "image/heic"
        if compatible & _AVIF_BRANDS:
            return "image/avif"
        return "image/heif"
    return None


def sniff(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM") and len(head) >= 18:
        return "image/bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if b"%PDF-" in head[:_HEAD_BYTES]:
        return "application/pdf"
    return _isobmff_mime(head)


def hint_mime(filename_hint: str | None) -> str | None:
    if not filename_hint:
        return None
    return _EXTENSION_HINTS.get(PurePath(filename_hint).suffix.lower())


class SignatureMediaTypeDetector:
    """MediaTypeDetector: routes by verified content signature, never by filename."""

    def detect(self, stream: BinaryIO, filename_hint: str) -> str:
        position = stream.tell()
        try:
            head = stream.read(_HEAD_BYTES)
        finally:
            stream.seek(position)
        if not head:
            raise failure("UNSUPPORTED_MEDIA", "detect", "Source is empty",
                          details=[("source", "zero bytes")])
        mime = sniff(head)
        if mime is None:
            raise failure("UNSUPPORTED_MEDIA", "detect", "No supported media signature found",
                          details=[("source", "unrecognized signature")])
        return mime


def detection_extension(detected_mime: str, filename_hint: str | None) -> dict:
    """Record hint/signature mismatch for Metadata.extensions (never the filename itself)."""
    hinted = hint_mime(filename_hint)
    return {
        "paios.detect": {
            "detected_mime_type": detected_mime,
            "hinted_mime_type": hinted,
            "hint_mismatch": hinted is not None and hinted != detected_mime,
        }
    }
