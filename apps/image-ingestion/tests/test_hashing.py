import hashlib

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from paios_ingestion import contract
from paios_ingestion.hashing import ContentHashService, dhash64_nearest
from paios_ingestion.normalize import write_page


def rows_image(values, width_scale=1, height_scale=1):
    """9x8 grayscale-as-RGB image with identical rows, optionally scaled by pixel repetition."""
    image = Image.new("RGB", (9 * width_scale, 8 * height_scale))
    for x in range(image.width):
        v = values[x // width_scale]
        for y in range(image.height):
            image.putpixel((x, y), (v, v, v))
    return image


# Issued vectors: PACKET-1-ISSUED-CONTRACT.md, "Exact hash procedure".
@pytest.mark.parametrize("values,expected", [
    ([0] * 9, "0000000000000000"),
    ([255, 224, 192, 160, 128, 96, 64, 32, 0], "ffffffffffffffff"),
    ([0, 32, 64, 96, 128, 160, 192, 224, 255], "0000000000000000"),
    ([255, 0, 255, 0, 255, 0, 255, 0, 255], "aaaaaaaaaaaaaaaa"),
])
def test_issued_vectors(values, expected):
    assert dhash64_nearest(rows_image(values)) == expected


@pytest.mark.parametrize("scale", [(2, 2), (3, 5), (7, 1)])
def test_nearest_sampling_hits_cell_centres(scale):
    # Repeating each column/row keeps sample points inside the same cell.
    image = rows_image([255, 0, 255, 0, 255, 0, 255, 0, 255], *scale)
    assert dhash64_nearest(image) == "aaaaaaaaaaaaaaaa"


def test_luminance_uses_integer_weights():
    # (0,255,0) -> floor((587*255+500)/1000) = 150, equal to grey 150: bit 0 is 0 (not >),
    # bit 1 is 1 (150 > 0), the rest 0. Every row is identical -> 0x40 per row.
    image = Image.new("RGB", (9, 8), (0, 0, 0))
    for y in range(8):
        image.putpixel((0, y), (0, 255, 0))
        image.putpixel((1, y), (150, 150, 150))
    assert dhash64_nearest(image) == "4040404040404040"


def test_sha256_and_phash_service(tmp_path):
    data = b"original bytes are hashed exactly"
    source = tmp_path / "src.bin"
    source.write_bytes(data)
    service = ContentHashService()
    assert service.sha256(source) == hashlib.sha256(data).hexdigest()

    page = write_page(rows_image([255, 0, 255, 0, 255, 0, 255, 0, 255], 4, 4), tmp_path, 1, None)
    phash = service.perceptual_hash(page)
    assert phash == {"algorithm": "dhash64-nearest", "version": "1", "value": "aaaaaaaaaaaaaaaa"}
    identity = contract.subschema("RegistryEntry")["properties"]["identity"]
    Draft202012Validator(identity).validate({"sha256": "0" * 64, "perceptual_hash": phash})
