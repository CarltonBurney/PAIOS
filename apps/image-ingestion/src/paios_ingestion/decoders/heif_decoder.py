"""HEIF-family adapter: HEIC/HEIF via pillow-heif (libheif), AVIF via Pillow's libavif plugin."""
from __future__ import annotations

import PIL
from PIL import features

from ..errors import failure
from ..metadata import open_image
from .base import BaseDecoder
from .pillow_decoder import decode_frames


def _version() -> str:
    import pillow_heif

    libheif = pillow_heif.libheif_info().get("libheif", "unknown")
    libavif = features.version("avif") or "unavailable"
    return f"pillow-heif-{pillow_heif.__version__}+libheif-{libheif}+pillow-{PIL.__version__}+libavif-{libavif}"


class HeifDecoder(BaseDecoder):
    decoder_id = "heif"
    mime_types = frozenset({"image/heic", "image/heif", "image/avif"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decoder_version = _version()

    def _decode(self, source, mime, output_dir, limits, budget, written):
        if mime == "image/avif" and not features.check("avif"):
            raise failure("UNSUPPORTED_MEDIA", "decode", "AVIF support is not available in this build")
        with open_image(source, mime) as image:
            # libheif applies irot/imir while decoding and resets EXIF orientation to 1,
            # so a second transpose would rotate twice. Pillow's AVIF plugin exposes the
            # container transform as EXIF orientation instead, which is applied once here.
            if mime == "image/avif" and getattr(image, "n_frames", 1) > 1:
                raise failure("UNSUPPORTED_MEDIA", "decode", "Animated AVIF is not supported",
                              details=[("frames", str(image.n_frames))])
            pages, notes = decode_frames(image, mime, output_dir, limits, budget, written,
                                         transpose=mime == "image/avif")
            return pages, "AVIF" if mime == "image/avif" else "HEIF", notes
