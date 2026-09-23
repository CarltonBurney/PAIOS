"""A09-A12 evidence: routing, pages, geometry/orientation, alpha, corrupt and limit inputs."""
import time

import pytest
from PIL import Image

import fixtures as fx
from conftest import error_of, limited
from paios_ingestion import contract
from paios_ingestion.decoders import HeifDecoder, PdfDecoder, PillowDecoder
from paios_ingestion.normalize import to_srgb_rgb


def near(pixel, colour, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour))


def page_image(page):
    image = Image.open(page.path)
    assert image.format == "PNG" and image.mode == "RGB"
    assert image.size == (page.width, page.height)
    return image


def assert_rotated_once(result):
    """Source is 40x20 with blue on the left; orientation 6 puts that strip on top."""
    (page,) = result.pages
    assert (page.width, page.height) == (20, 40)
    image = page_image(page)
    assert near(image.getpixel((10, 3)), fx.BLUE)
    assert near(image.getpixel((10, 30)), fx.RED)
    assert result.metadata["orientation_original"] == 6


def decode(decoder, path, mime, tmp_path, limits):
    return decoder.decode(path, mime, tmp_path / "out", limits)


def test_jpeg_exif_orientation_applied_once(tmp_path, limits):
    result = decode(PillowDecoder(), fx.jpeg(tmp_path / "a.jpg"), "image/jpeg", tmp_path, limits)
    assert_rotated_once(result)
    assert result.original_format == "JPEG" and result.pages[0].render_dpi is None


def test_heic_orientation_applied_once(tmp_path, limits):
    result = decode(HeifDecoder(), fx.heic(tmp_path / "a.heic"), "image/heic", tmp_path, limits)
    assert_rotated_once(result)
    assert result.original_format == "HEIF"
    assert result.metadata["device"] == "Apple iPhone Test"


def test_avif_orientation_applied_once(tmp_path, limits):
    result = decode(HeifDecoder(), fx.avif(tmp_path / "a.avif"), "image/avif", tmp_path, limits)
    assert_rotated_once(result)
    assert result.original_format == "AVIF"


@pytest.mark.parametrize("orientation,size", [(1, (40, 20)), (3, (40, 20)), (8, (20, 40))])
def test_other_orientations(tmp_path, limits, orientation, size):
    path = fx.jpeg(tmp_path / "a.jpg", orientation=orientation)
    (page,) = decode(PillowDecoder(), path, "image/jpeg", tmp_path, limits).pages
    assert (page.width, page.height) == size
    image = page_image(page)
    blue_at = {1: (3, 10), 3: (36, 10), 8: (10, 36)}[orientation]
    assert near(image.getpixel(blue_at), fx.BLUE)


def test_transparency_composited_on_white(tmp_path, limits):
    (page,) = decode(PillowDecoder(), fx.png_rgba(tmp_path / "a.png"), "image/png", tmp_path,
                     limits).pages
    image = page_image(page)
    assert image.getpixel((12, 4)) == (255, 255, 255)
    assert image.getpixel((2, 4)) == (0, 0, 255)


@pytest.mark.parametrize("mode,pixel,expected", [
    ("RGBA", (255, 0, 0, 128), (255, 127, 127)),     # half-transparent red over white
    ("LA", (0, 0), (255, 255, 255)),
    ("L", 77, (77, 77, 77)),
    ("CMYK", (0, 255, 255, 0), (255, 0, 0)),
    ("I;16", 65535, (255, 255, 255)),
    ("1", 1, (255, 255, 255)),
])
def test_mode_normalization(mode, pixel, expected):
    rgb, note = to_srgb_rgb(Image.new(mode, (2, 2), pixel))
    assert rgb.mode == "RGB"
    assert near(rgb.getpixel((0, 0)), expected, tolerance=1)
    assert note["source_mode"] == mode


def test_palette_transparency():
    image = Image.new("P", (2, 1))
    image.putpalette([0, 0, 0, 0, 255, 0] + [0] * 762)
    image.putpixel((1, 0), 1)
    image.info["transparency"] = 0
    rgb, note = to_srgb_rgb(image)
    assert rgb.getpixel((0, 0)) == (255, 255, 255) and rgb.getpixel((1, 0)) == (0, 255, 0)
    assert note["alpha_composited"]


def test_srgb_profile_is_recognised_and_unusable_profile_is_reported():
    from PIL import ImageCms
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGB", (1, 1), (10, 20, 30))
    image.info["icc_profile"] = srgb
    rgb, note = to_srgb_rgb(image)
    assert note["icc"] == "srgb" and rgb.getpixel((0, 0)) == (10, 20, 30)
    image.info["icc_profile"] = b"not a profile"
    rgb, note = to_srgb_rgb(image)
    assert note["icc"] == "unusable" and rgb.getpixel((0, 0)) == (10, 20, 30)


def test_webp_and_bmp(tmp_path, limits):
    for fmt, mime in (("WEBP", "image/webp"), ("BMP", "image/bmp")):
        path = fx.save(fx.two_tone(), tmp_path / f"a.{fmt}", fmt, **({"lossless": True} if fmt == "WEBP" else {}))
        result = decode(PillowDecoder(), path, mime, tmp_path / fmt, limits)
        assert [(p.width, p.height) for p in result.pages] == [(40, 20)]


def test_multipage_tiff_contiguous_pages(tmp_path, limits):
    result = decode(PillowDecoder(), fx.tiff_pages(tmp_path / "a.tif"), "image/tiff", tmp_path, limits)
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert [(p.width, p.height) for p in result.pages] == [(30, 20), (10, 40), (25, 25)]
    assert len({p.sha256 for p in result.pages}) == 3
    notes = result.metadata["extensions"]["paios.normalize"]["pages"]
    assert [n["page_number"] for n in notes] == [1, 2, 3]


def test_multipage_pdf_at_300_dpi(tmp_path, limits):
    result = decode(PdfDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path, limits)
    assert [p.page_number for p in result.pages] == [1, 2]
    # 200x100 pt and 100x200 pt at 300 DPI (x 300/72, rounded up by PDFium).
    assert [(p.width, p.height) for p in result.pages] == [(834, 417), (417, 834)]
    assert all(p.render_dpi == 300 for p in result.pages)
    assert result.original_format == "PDF"
    assert result.metadata["extensions"]["paios.pdf"]["page_count"] == 2
    assert page_image(result.pages[0]).getpixel((400, 200)) == (255, 255, 255)


def test_pdf_page_rotation_is_applied(tmp_path, limits):
    result = decode(PdfDecoder(), fx.rotated_pdf(tmp_path / "r.pdf"), "application/pdf", tmp_path, limits)
    assert [(p.width, p.height) for p in result.pages] == [(417, 834)]


def test_pdf_dpi_is_configurable(tmp_path):
    result = decode(PdfDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path,
                    limited(pdf_dpi=72))
    assert [(p.width, p.height, p.render_dpi) for p in result.pages] == [(200, 100, 72), (100, 200, 72)]


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_animated_images_are_explicitly_rejected(tmp_path, limits, fmt):
    path = fx.animated(tmp_path / f"a.{fmt}", fmt)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), path, f"image/{fmt.lower()}", tmp_path, limits)
    assert error_of(excinfo)["code"] == "UNSUPPORTED_MEDIA"


def test_encrypted_pdf(tmp_path, limits):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PdfDecoder(), fx.encrypted_pdf(tmp_path / "e.pdf"), "application/pdf", tmp_path, limits)
    error = error_of(excinfo)
    assert (error["code"], error["stage"], error["retryable"]) == ("ENCRYPTED_MEDIA", "decode", False)


@pytest.mark.parametrize("name,data,decoder,mime", [
    ("truncated.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 40, PillowDecoder, "image/jpeg"),
    ("truncated.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, PillowDecoder, "image/png"),
    ("broken.pdf", b"%PDF-1.4\n garbage without objects", PdfDecoder, "application/pdf"),
    ("broken.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64,
     HeifDecoder, "image/heic"),
])
def test_corrupt_inputs_fail_without_leaking_bytes(tmp_path, limits, name, data, decoder, mime):
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(decoder(), path, mime, tmp_path, limits)
    error = error_of(excinfo)
    assert error["code"] == "DECODE_FAILED" and error["stage"] == "decode"
    assert str(tmp_path) not in str(error)  # no local paths in errors
    assert list((tmp_path / "out").iterdir()) == []


def test_truncated_real_jpeg(tmp_path, limits):
    path = fx.jpeg(tmp_path / "a.jpg")
    path.write_bytes(path.read_bytes()[:200])
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), path, "image/jpeg", tmp_path, limits)
    assert error_of(excinfo)["code"] == "DECODE_FAILED"


@pytest.mark.parametrize("width,height", [(8000, 8000), (20000, 20000), (1, 2_000_000_000)])
def test_decompression_bomb_rejected_before_allocation(tmp_path, limits, width, height):
    path = fx.png_header_only(tmp_path / "bomb.png", width, height)
    started = time.monotonic()
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), path, "image/png", tmp_path, limits)
    assert time.monotonic() - started < 2
    assert error_of(excinfo)["code"] == "LIMIT_EXCEEDED"
    assert list((tmp_path / "out").iterdir()) == []


def test_source_byte_limit(tmp_path):
    path = fx.jpeg(tmp_path / "a.jpg")
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), path, "image/jpeg", tmp_path, limited(max_source_bytes=100))
    assert error_of(excinfo)["details"][0]["field"] == "max_source_bytes"


def test_page_limit_tiff_and_pdf(tmp_path):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), fx.tiff_pages(tmp_path / "a.tif"), "image/tiff", tmp_path, limited(max_pages=2))
    assert error_of(excinfo)["details"][0]["field"] == "max_pages"
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PdfDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path, limited(max_pages=1))
    assert error_of(excinfo)["code"] == "LIMIT_EXCEEDED"
    assert list((tmp_path / "out").iterdir()) == []


def test_total_pixel_limit_checked_before_rendering_any_page(tmp_path):
    # Each PDF page is 834x417 = 347,778 px at 300 DPI; two pages exceed 500,000.
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PdfDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path,
               limited(max_total_pixels=500_000))
    assert error_of(excinfo)["details"][0]["field"] == "max_total_pixels"
    assert list((tmp_path / "out").iterdir()) == []


def test_per_page_pixel_limit(tmp_path):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PdfDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path,
               limited(max_pixels_per_page=300_000))
    assert error_of(excinfo)["details"][0]["field"] == "pages[1]"


def test_expired_deadline(tmp_path):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), fx.jpeg(tmp_path / "a.jpg"), "image/jpeg", tmp_path,
               limited(deadline_utc="2000-01-01T00:00:00Z"))
    assert error_of(excinfo)["details"][0]["field"] == "deadline_utc"


def test_decoder_refuses_unsupported_mime(tmp_path, limits):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        decode(PillowDecoder(), fx.pdf(tmp_path / "a.pdf"), "application/pdf", tmp_path, limits)
    assert error_of(excinfo)["code"] == "CONTRACT_MISMATCH"


def test_normalized_output_is_deterministic(tmp_path, limits):
    path = fx.pdf(tmp_path / "a.pdf")
    first = decode(PdfDecoder(), path, "application/pdf", tmp_path / "1", limits)
    second = decode(PdfDecoder(), path, "application/pdf", tmp_path / "2", limits)
    assert [p.sha256 for p in first.pages] == [p.sha256 for p in second.pages]


def test_decoded_png_has_no_ancillary_metadata(tmp_path, limits):
    (page,) = decode(PillowDecoder(), fx.jpeg(tmp_path / "a.jpg"), "image/jpeg", tmp_path, limits).pages
    image = Image.open(page.path)
    assert "exif" not in image.info and "icc_profile" not in image.info
