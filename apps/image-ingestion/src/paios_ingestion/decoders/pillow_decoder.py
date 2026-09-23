"""Pillow adapter: JPEG, PNG, WebP, BMP and (multi-page) TIFF."""
from __future__ import annotations

import PIL
from PIL import ImageOps

from ..errors import failure
from .base import BaseDecoder
from ..metadata import open_image
from ..normalize import check_deadline, to_srgb_rgb, write_page

# Formats whose extra frames are animation, not document pages.
_ANIMATED = {"image/png", "image/webp", "image/gif"}


def decode_frames(image, mime, output_dir, limits, budget, written, *, transpose: bool):
    """Normalize every frame of an opened Pillow image as pages 1..N."""
    frames = getattr(image, "n_frames", 1)
    if frames > 1 and mime in _ANIMATED:
        raise failure("UNSUPPORTED_MEDIA", "decode", "Animated images are not supported",
                      details=[("frames", str(frames))])
    budget.check_page_count(frames)
    for index in range(frames):  # sizes are header reads; check all before decoding any
        image.seek(index)
        budget.admit(index + 1, *image.size)
    pages, notes = [], []
    for index in range(frames):
        check_deadline(limits)
        image.seek(index)
        frame = ImageOps.exif_transpose(image) if transpose else image.copy()
        # exif_transpose drops info on some paths; keep the ICC profile for colour conversion.
        if "icc_profile" in image.info:
            frame.info.setdefault("icc_profile", image.info["icc_profile"])
        rgb, note = to_srgb_rgb(frame)
        page = write_page(rgb, output_dir, index + 1, None)
        written.append(page.path)
        pages.append(page)
        notes.append({"page_number": index + 1, **note})
    return pages, notes


class PillowDecoder(BaseDecoder):
    decoder_id = "pillow"
    decoder_version = f"pillow-{PIL.__version__}"
    mime_types = frozenset({"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"})

    def _decode(self, source, mime, output_dir, limits, budget, written):
        with open_image(source, mime) as image:
            pages, notes = decode_frames(image, mime, output_dir, limits, budget, written,
                                         transpose=True)
            return pages, image.format, notes
