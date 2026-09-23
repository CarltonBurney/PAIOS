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


def decode_frames(image, mime, output_dir, limits, budget, written, *, transpose: bool,
                  frames=None):
    """Normalize the given frame indexes (default: every frame) as pages 1..N."""
    if frames is None:
        count = getattr(image, "n_frames", 1)
        if count > 1 and mime in _ANIMATED:
            raise failure("UNSUPPORTED_MEDIA", "decode", "Animated images are not supported",
                          details=[("frames", str(count))])
        frames = list(range(count))
    budget.check_page_count(len(frames))
    for page_number, index in enumerate(frames, 1):  # header reads; check all before decoding
        image.seek(index)
        budget.admit(page_number, *image.size)
    pages, notes = [], []
    for page_number, index in enumerate(frames, 1):
        check_deadline(limits)
        image.seek(index)
        frame = ImageOps.exif_transpose(image) if transpose else image.copy()
        # exif_transpose drops info on some paths; keep the ICC profile for colour conversion.
        if "icc_profile" in image.info:
            frame.info.setdefault("icc_profile", image.info["icc_profile"])
        rgb, note = to_srgb_rgb(frame)
        page = write_page(rgb, output_dir, page_number, None)
        written.append(page.path)
        pages.append(page)
        notes.append({"page_number": page_number, **note})
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
