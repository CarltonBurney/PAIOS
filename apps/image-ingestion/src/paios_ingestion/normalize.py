"""`png-rgb-white-v1` normalization and decode-limit enforcement shared by all decoders."""
from __future__ import annotations

import hashlib
import io
import math
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageCms

from . import contract
from .errors import failure

NORMALIZATION_PROFILE = "png-rgb-white-v1"
PNG_COMPRESS_LEVEL = 6

_ALPHA_MODES = {"RGBA", "LA", "PA", "RGBa", "La"}
_SRGB = ImageCms.createProfile("sRGB")

# Set by the caller that owns output_dir (e.g. IngestionFoundation with WorkCache.allocate).
# Called with the encoded byte count; the page is written inside the returned context,
# so local cache budgets are enforced on actual output, not only on source size.
page_allocator: ContextVar = ContextVar("page_allocator", default=None)


def parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamp must be RFC 3339 UTC with Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def check_deadline(limits: contract.Limits, stage: str = "decode") -> None:
    if datetime.now(timezone.utc) > parse_utc(limits.deadline_utc):
        raise failure("LIMIT_EXCEEDED", stage, "Processing deadline exceeded",
                      details=[("deadline_utc", "deadline passed before completion")])


def check_source_size(source: Path, limits: contract.Limits, stage: str = "decode") -> int:
    size = source.stat().st_size
    if size > limits.max_source_bytes:
        raise failure("LIMIT_EXCEEDED", stage, "Source exceeds the configured byte limit",
                      details=[("max_source_bytes", f"{size} > {limits.max_source_bytes}")])
    return size


class PixelBudget:
    """Checks page count and pixel limits before any raster is allocated."""

    def __init__(self, limits: contract.Limits):
        self.limits = limits
        self.total_pixels = 0

    def check_page_count(self, pages: int) -> None:
        if pages < 1:
            raise failure("DECODE_FAILED", "decode", "Source contains no pages")
        if pages > self.limits.max_pages:
            raise failure("LIMIT_EXCEEDED", "decode", "Source exceeds the configured page limit",
                          details=[("max_pages", f"{pages} > {self.limits.max_pages}")])

    def admit(self, page_number: int, width: int, height: int) -> None:
        if width < 1 or height < 1:
            raise failure("DECODE_FAILED", "decode", "Page has non-positive dimensions",
                          details=[(f"pages[{page_number}]", f"{width}x{height}")])
        pixels = width * height
        if pixels > self.limits.max_pixels_per_page:
            raise failure("LIMIT_EXCEEDED", "decode", "Page exceeds the configured pixel limit",
                          details=[(f"pages[{page_number}]",
                                    f"{pixels} > {self.limits.max_pixels_per_page} pixels")])
        if self.total_pixels + pixels > self.limits.max_total_pixels:
            raise failure("LIMIT_EXCEEDED", "decode", "Source exceeds the configured total pixel limit",
                          details=[("max_total_pixels",
                                    f"{self.total_pixels + pixels} > {self.limits.max_total_pixels}")])
        self.total_pixels += pixels


def pdf_pixel_size(width_pt: float, height_pt: float, dpi: int) -> tuple[int, int]:
    """Upper bound of the rendered size of a PDF page, used before rendering."""
    return math.ceil(width_pt * dpi / 72), math.ceil(height_pt * dpi / 72)


def _source_profile(icc: bytes) -> ImageCms.ImageCmsProfile:
    """Parse an embedded profile; an unreadable explicit profile fails the decode."""
    try:
        return ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except (ImageCms.PyCMSError, OSError, TypeError, ValueError):
        raise failure("DECODE_FAILED", "decode", "Embedded ICC profile could not be read",
                      details=[("icc_profile", "unreadable")]) from None


def _is_srgb(profile: ImageCms.ImageCmsProfile) -> bool:
    try:
        description = ImageCms.getProfileDescription(profile)
    except (ImageCms.PyCMSError, OSError, TypeError):
        return False
    return description.strip().lower().startswith("srgb")


def _to_8bit(image: Image.Image) -> Image.Image:
    sixteen_bit = image.mode.startswith("I;16")
    if sixteen_bit:
        image = image.convert("I")
    if image.mode == "I":
        maximum = 65535 if sixteen_bit or image.getextrema()[1] > 255 else 255
        return image.point(lambda v: v * (255 / maximum)).convert("L")
    if image.mode == "F":
        return image.point(lambda v: min(max(v, 0.0), 255.0)).convert("L")
    return image


def to_srgb_rgb(image: Image.Image) -> tuple[Image.Image, dict]:
    """Return an 8-bit sRGB RGB image with alpha composited on white, plus a provenance note."""
    note = {"source_mode": image.mode, "icc": "none", "alpha_composited": False}
    icc = image.info.get("icc_profile")
    image = _to_8bit(image)

    has_alpha = image.mode in _ALPHA_MODES or "transparency" in image.info
    alpha = None
    if has_alpha:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        base = image.convert("L") if image.mode in ("LA", "La") else rgba.convert("RGB")
    elif image.mode in ("L", "RGB", "CMYK"):
        base = image
    else:
        base = image.convert("RGB")

    if icc:
        profile = _source_profile(icc)
        if _is_srgb(profile) and base.mode == "RGB":
            note["icc"] = "srgb"
        else:
            try:
                base = ImageCms.profileToProfile(
                    base, profile, _SRGB,
                    renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")
            except (ImageCms.PyCMSError, OSError, ValueError):
                # Never label pixels sRGB when their explicit profile could not be applied.
                raise failure("DECODE_FAILED", "decode", "Embedded ICC profile could not be applied",
                              details=[("icc_profile", f"not applicable to mode {base.mode}")]) from None
            note["icc"] = "converted"
    rgb = base.convert("RGB")

    if alpha is not None:
        canvas = Image.new("RGB", rgb.size, (255, 255, 255))
        canvas.paste(rgb, mask=alpha)
        rgb = canvas
        note["alpha_composited"] = True
    return rgb, note


def write_page(image: Image.Image, output_dir: Path, page_number: int,
               render_dpi: int | None) -> contract.LocalPage:
    """Encode a normalized RGB page to PNG (no ancillary metadata) and hash the encoded bytes."""
    if image.mode != "RGB":
        raise ValueError("write_page requires RGB input")
    # Pixels only: Pillow would otherwise re-embed info["icc_profile"]/EXIF from the
    # source. Output is untagged 8-bit sRGB by definition of png-rgb-white-v1.
    clean = Image.frombytes("RGB", image.size, image.tobytes())
    buffer = io.BytesIO()
    clean.save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    data = buffer.getvalue()
    path = output_dir / f"page-{page_number:06d}.png"
    allocator = page_allocator.get()
    with allocator(len(data)) if allocator else nullcontext():
        with open(path, "xb") as handle:  # never overwrite an existing page
            handle.write(data)
    return contract.LocalPage(
        page_number=page_number, path=path, width=image.width, height=image.height,
        sha256=hashlib.sha256(data).hexdigest(), render_dpi=render_dpi)
