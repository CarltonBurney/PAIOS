"""Metadata extraction from original bytes (never from rendered pages)."""
from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import PIL
from PIL import ExifTags, Image, ImageCms, TiffImagePlugin

from . import contract
from .errors import failure

EXTRACTOR_ID = "paios-metadata"
EXTRACTOR_VERSION = f"1.0.0+pillow-{PIL.__version__}"

PILLOW_FORMATS = {
    "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/bmp": "BMP",
    "image/tiff": "TIFF", "image/heic": "HEIF", "image/heif": "HEIF", "image/avif": "AVIF",
}

_MAX_STRING = 1024
_EXIF_IFD = ExifTags.IFD.Exif
_GPS_IFD = ExifTags.IFD.GPSInfo


def open_image(source: Path, detected_mime_type: str) -> Image.Image:
    """Open with the single Pillow format matching the verified signature."""
    fmt = PILLOW_FORMATS.get(detected_mime_type)
    if fmt is None:
        raise failure("UNSUPPORTED_MEDIA", "decode", f"{detected_mime_type} is not an image format")
    if fmt == "HEIF":
        import pillow_heif

        pillow_heif.register_heif_opener()
    return Image.open(source, formats=[fmt])


def _json_value(value):
    if isinstance(value, TiffImagePlugin.IFDRational):
        return None if value.denominator == 0 else _json_value(float(value))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        cleaned = value.replace("\x00", "").strip()
        return cleaned[:_MAX_STRING] if cleaned else None
    if isinstance(value, (tuple, list)):
        items = [_json_value(v) for v in value]
        return items if len(items) <= 64 else None
    return None  # bytes (MakerNote etc.) and unknown types are not carried


def _exif_map(exif: Image.Exif, allow_gps: bool) -> dict:
    result = {}
    groups = [("ifd0", dict(exif), ExifTags.TAGS)]
    for label, ifd_id, table in (("exif", _EXIF_IFD, ExifTags.TAGS),
                                 ("gps", _GPS_IFD, ExifTags.GPSTAGS)):
        if ifd_id == _GPS_IFD and not allow_gps:
            continue
        try:
            groups.append((label, dict(exif.get_ifd(ifd_id)), table))
        except (KeyError, ValueError, TypeError, OSError):
            continue
    for label, tags, table in groups:
        for tag, value in tags.items():
            if label == "ifd0" and tag in (_EXIF_IFD, _GPS_IFD, ExifTags.IFD.Interop):
                continue
            converted = _json_value(value)
            if converted is not None:
                result[f"{label}.{table.get(tag, f'0x{tag:04x}')}"] = converted
    return result


def _text(value) -> str | None:
    converted = _json_value(value)
    return converted if isinstance(converted, str) else None


def _capture(exif_ifd: dict) -> tuple[str | None, str | None, bool]:
    """Return (raw, utc, timezone_known). Never invents a timezone."""
    for time_tag, offset_tag in ((0x9003, 0x9011), (0x9004, 0x9012)):  # Original, Digitized
        raw = _text(exif_ifd.get(time_tag))
        if not raw:
            continue
        offset = _text(exif_ifd.get(offset_tag))
        try:
            local = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return raw, None, False
        if not offset:
            return raw, None, False
        try:
            sign = -1 if offset.startswith("-") else 1
            hours, minutes = offset.lstrip("+-").split(":")
            delta = timedelta(hours=int(hours), minutes=int(minutes)) * sign
        except ValueError:
            return raw, None, False
        utc = (local - delta).replace(tzinfo=timezone.utc)
        return raw, utc.strftime("%Y-%m-%dT%H:%M:%SZ"), True
    return None, None, False


def _gps(exif: Image.Exif) -> dict | None:
    try:
        gps = exif.get_ifd(_GPS_IFD)
    except (KeyError, ValueError, TypeError, OSError):
        return None

    def coordinate(value, ref, negative_ref, bound):
        try:
            d, m, s = (float(v) for v in value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        result = d + m / 60 + s / 3600
        if _text(ref) == negative_ref:
            result = -result
        return result if math.isfinite(result) and abs(result) <= bound else None

    lat = coordinate(gps.get(2), gps.get(1), "S", 90)
    lon = coordinate(gps.get(4), gps.get(3), "W", 180)
    if lat is None or lon is None:
        return None
    return {"latitude": lat, "longitude": lon}


def _color_profile(image: Image.Image) -> str | None:
    icc = image.info.get("icc_profile")
    if icc:
        try:
            description = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
            return description.strip() or None
        except (ImageCms.PyCMSError, OSError, TypeError):
            return None
    if "srgb" in image.info:  # PNG sRGB chunk
        return "sRGB"
    return None


def _empty(extensions: dict | None = None) -> dict:
    return {
        "extractor": EXTRACTOR_ID, "extractor_version": EXTRACTOR_VERSION,
        "capture_time_raw": None, "captured_at": None, "capture_timezone_known": False,
        "orientation_original": None, "device": None, "gps": None, "color_profile": None,
        "exif": {}, "extensions": extensions or {},
    }


class OriginalMetadataExtractor:
    """MetadataExtractor for the Pillow/HEIF/PDF formats in Packet 1."""

    def extract(self, source: Path, detected_mime_type: str, allow_gps: bool) -> dict:
        try:
            if detected_mime_type == "application/pdf":
                metadata = self._pdf(source)
            else:
                metadata = self._image(source, detected_mime_type, allow_gps)
        except contract.PipelineFailure:
            raise
        except Exception as exc:  # malformed metadata must not become a silent success
            raise failure("DECODE_FAILED", "metadata", "Metadata could not be read",
                          details=[("metadata", type(exc).__name__)]) from None
        contract.validate("Metadata", metadata)
        return metadata

    def _image(self, source: Path, mime: str, allow_gps: bool) -> dict:
        with open_image(source, mime) as image:
            exif = image.getexif()
            exif_ifd = dict(exif.get_ifd(_EXIF_IFD)) if exif else {}
            metadata = _empty()
            raw, utc, known = _capture(exif_ifd)
            metadata["capture_time_raw"] = raw
            metadata["captured_at"] = utc
            metadata["capture_timezone_known"] = known
            orientation = image.info.get("original_orientation", exif.get(0x0112))
            metadata["orientation_original"] = orientation if orientation in range(1, 9) else None
            make, model = _text(exif.get(0x010F)), _text(exif.get(0x0110))
            if make and model and model.lower().startswith(make.lower()):
                make = None
            metadata["device"] = " ".join(p for p in (make, model) if p) or None
            metadata["gps"] = _gps(exif) if allow_gps else None
            metadata["color_profile"] = _color_profile(image)
            metadata["exif"] = _exif_map(exif, allow_gps)
            return metadata

    def _pdf(self, source: Path) -> dict:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(source))
        try:
            info = doc.get_metadata_dict(skip_empty=True)
            extension = {
                "pdf_version": doc.get_version(),
                "page_count": len(doc),
                "creation_date_raw": info.get("CreationDate"),
                "modification_date_raw": info.get("ModDate"),
                "producer": _text(info.get("Producer")),
                "creator": _text(info.get("Creator")),
            }
        finally:
            doc.close()
        return _empty({"paios.pdf": extension})
