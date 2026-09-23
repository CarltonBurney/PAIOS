"""SHA-256 identity and the advisory `dhash64-nearest` v1 perceptual hash."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from . import contract

PHASH_ALGORITHM = "dhash64-nearest"
PHASH_VERSION = "1"
_CHUNK = 1024 * 1024


class ContentHashService:
    """HashService."""

    def sha256(self, source: Path) -> str:
        digest = hashlib.sha256()
        with open(source, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def perceptual_hash(self, page: contract.LocalPage) -> dict | None:
        with Image.open(page.path, formats=["PNG"]) as image:
            if image.mode != "RGB":
                raise ValueError("perceptual hash requires a normalized RGB page")
            value = dhash64_nearest(image)
        return {"algorithm": PHASH_ALGORITHM, "version": PHASH_VERSION, "value": value}


def dhash64_nearest(image: Image.Image) -> str:
    """Exact procedure from PACKET-1-ISSUED-CONTRACT.md; no library resampling."""
    width, height = image.size
    pixels = image.load()
    luminance = []
    for y in range(8):
        sy = min(height - 1, (2 * y + 1) * height // 16)  # floor((y+0.5)*H/8)
        row = []
        for x in range(9):
            sx = min(width - 1, (2 * x + 1) * width // 18)  # floor((x+0.5)*W/9)
            r, g, b = pixels[sx, sy][:3]
            row.append((299 * r + 587 * g + 114 * b + 500) // 1000)
        luminance.append(row)
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (1 if luminance[y][x] > luminance[y][x + 1] else 0)
    return f"{bits:016x}"
