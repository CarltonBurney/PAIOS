import io

import pytest

import fixtures as fx
from conftest import error_of
from paios_ingestion import contract
from paios_ingestion.detect import SignatureMediaTypeDetector, detection_extension, sniff
from paios_ingestion.registry import PriorityDecoderRegistry, default_registry


@pytest.mark.parametrize("builder,expected", [
    (lambda p: fx.jpeg(p / "x"), "image/jpeg"),
    (lambda p: fx.png_rgba(p / "x"), "image/png"),
    (lambda p: fx.save(fx.two_tone(), p / "x", "WEBP"), "image/webp"),
    (lambda p: fx.save(fx.two_tone(), p / "x", "BMP"), "image/bmp"),
    (lambda p: fx.tiff_pages(p / "x"), "image/tiff"),
    (lambda p: fx.heic(p / "x"), "image/heic"),
    (lambda p: fx.avif(p / "x"), "image/avif"),
    (lambda p: fx.pdf(p / "x"), "application/pdf"),
    (lambda p: fx.animated(p / "x", "GIF"), "image/gif"),
])
def test_signature_routing_ignores_filename(tmp_path, builder, expected):
    path = builder(tmp_path)
    with open(path, "rb") as stream:
        stream.seek(0)
        assert SignatureMediaTypeDetector().detect(stream, "misleading.txt") == expected
        assert stream.tell() == 0  # position restored


def test_stream_position_is_restored_when_not_zero():
    stream = io.BytesIO(b"junk" + b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    stream.seek(4)
    assert SignatureMediaTypeDetector().detect(stream, "") == "image/png"
    assert stream.tell() == 4


def test_heif_brands():
    def ftyp(major, *compatible):
        body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
        return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body

    assert sniff(ftyp(b"heic", b"mif1")) == "image/heic"
    assert sniff(ftyp(b"mif1", b"heic")) == "image/heic"
    assert sniff(ftyp(b"mif1", b"avif")) == "image/avif"
    assert sniff(ftyp(b"mif1")) == "image/heif"
    assert sniff(ftyp(b"avis", b"mif1")) == "image/avif"
    assert sniff(ftyp(b"isom", b"mp41")) is None  # MP4 video is not an image


def test_pdf_with_leading_bytes():
    assert sniff(b"\x00" * 100 + b"%PDF-1.7\n") == "application/pdf"


@pytest.mark.parametrize("data", [b"", b"plain text, not media"])
def test_unknown_or_empty_is_unsupported(data):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        SignatureMediaTypeDetector().detect(io.BytesIO(data), "photo.jpg")
    error = error_of(excinfo)
    assert (error["code"], error["stage"]) == ("UNSUPPORTED_MEDIA", "detect")


def test_hint_mismatch_is_recorded_without_filename():
    ext = detection_extension("image/png", "holiday.JPG")["paios.detect"]
    assert ext == {"detected_mime_type": "image/png", "hinted_mime_type": "image/jpeg",
                   "hint_mismatch": True}
    assert detection_extension("image/png", None)["paios.detect"]["hint_mismatch"] is False


class Fake:
    decoder_version = "1"

    def __init__(self, decoder_id, mimes):
        self.decoder_id, self.mimes = decoder_id, mimes

    def supports(self, mime):
        return mime in self.mimes

    def decode(self, *args):
        raise NotImplementedError


def test_registry_priority_then_ambiguity():
    registry = PriorityDecoderRegistry([(5, Fake("b", {"image/png"})), (9, Fake("a", {"image/png"}))])
    assert registry.resolve("image/png").decoder_id == "a"
    registry.register(Fake("c", {"image/png"}), 9)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        registry.resolve("image/png")
    error = error_of(excinfo)
    assert error["code"] == "CONTRACT_MISMATCH"
    assert error["details"] == [{"field": "decoder_id", "reason": "a, c"}]


def test_registry_rejects_duplicate_ids():
    registry = PriorityDecoderRegistry([(1, Fake("a", set()))])
    with pytest.raises(ValueError):
        registry.register(Fake("a", set()))


@pytest.mark.parametrize("mime,decoder_id", [
    ("image/jpeg", "pillow"), ("image/png", "pillow"), ("image/webp", "pillow"),
    ("image/bmp", "pillow"), ("image/tiff", "pillow"), ("image/heic", "heif"),
    ("image/heif", "heif"), ("image/avif", "heif"), ("application/pdf", "pdfium"),
])
def test_default_registry_is_unambiguous(mime, decoder_id):
    assert default_registry().resolve(mime).decoder_id == decoder_id


def test_gif_is_explicitly_unsupported():
    with pytest.raises(contract.PipelineFailure) as excinfo:
        default_registry().resolve("image/gif")
    assert error_of(excinfo)["code"] == "UNSUPPORTED_MEDIA"
