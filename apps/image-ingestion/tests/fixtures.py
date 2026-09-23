"""Synthetic fixture builders (no private content). Deterministic for a given library build."""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

from PIL import Image

RED, BLUE, WHITE = (255, 0, 0), (0, 0, 255), (255, 255, 255)


def two_tone(size=(40, 20)) -> Image.Image:
    """Red image whose left quarter is blue: shows where 'left' ends up after rotation."""
    image = Image.new("RGB", size, RED)
    image.paste(BLUE, (0, 0, size[0] // 4, size[1]))
    return image


def exif_bytes(orientation=None, make=None, model=None, original=None, offset=None, gps=None) -> bytes:
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if make:
        exif[0x010F] = make
    if model:
        exif[0x0110] = model
    sub = exif.get_ifd(0x8769)
    if original:
        sub[0x9003] = original
    if offset:
        sub[0x9011] = offset
    if gps:
        exif.get_ifd(0x8825).update(gps)
    return exif.tobytes()


def save(image: Image.Image, path: Path, fmt: str, **params) -> Path:
    image.save(path, fmt, **params)
    return path


def jpeg(path: Path, orientation=6, **exif) -> Path:
    return save(two_tone(), path, "JPEG", quality=95, exif=exif_bytes(orientation, **exif))


def png_rgba(path: Path) -> Path:
    image = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    image.paste((0, 0, 255, 255), (0, 0, 8, 8))
    return save(image, path, "PNG")


def heic(path: Path, orientation=6) -> Path:
    import pillow_heif

    pillow_heif.register_heif_opener()
    return save(two_tone(), path, "HEIF", quality=95, exif=exif_bytes(orientation, make="Apple",
                                                                     model="iPhone Test"))


def avif(path: Path, orientation=6) -> Path:
    return save(two_tone(), path, "AVIF", quality=95, exif=exif_bytes(orientation))


def pdf(path: Path, sizes=((200, 100), (100, 200))) -> Path:
    images = [Image.new("RGB", size, WHITE) for size in sizes]
    images[0].save(path, "PDF", resolution=72, save_all=True, append_images=images[1:])
    return path


def encrypted_pdf(path: Path) -> Path:
    from pypdf import PdfReader, PdfWriter

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), WHITE).save(buffer, "PDF", resolution=72)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buffer.getvalue())))
    writer.encrypt(user_password="secret", owner_password="owner")
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def rotated_pdf(path: Path) -> Path:
    from pypdf import PdfReader, PdfWriter

    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), WHITE).save(buffer, "PDF", resolution=72)
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buffer.getvalue())))
    writer.pages[0].rotate(90)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def tiff_pages(path: Path, sizes=((30, 20), (10, 40), (25, 25))) -> Path:
    images = [Image.new("RGB", size, (i * 60, 100, 200)) for i, size in enumerate(sizes)]
    images[0].save(path, "TIFF", save_all=True, append_images=images[1:])
    return path


def animated(path: Path, fmt: str) -> Path:
    frames = [Image.new("RGB", (8, 8), c) for c in (RED, BLUE)]
    frames[0].save(path, fmt, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


def png_header_only(path: Path, width: int, height: int) -> Path:
    """Valid PNG header claiming huge dimensions with almost no pixel data (bomb probe)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b"")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + body)
    return path
