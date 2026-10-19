"""The image encoder -- inverse of `images.decode_image`.

Correctness is a round trip: every shipped image, decoded and re-encoded, must decode again to the
identical pixels. (Byte-for-byte reproduction of the shipped stream is NOT required and not aimed
for -- the shipped encoder makes some suboptimal run splits; ours is valid and slightly smaller,
which is what a same-size swap wants.)
"""
import pytest

from fireflymcf import images, imageenc

BUILDS = ["firefly-11.23/mcf.bin", "firefly-13.6/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"]


def _data(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("path", BUILDS)
def test_every_shipped_image_round_trips_through_the_encoder(path):
    data = _data(path)
    for i in range(images.image_count(data)):
        img = images.decode_image(data, i)
        enc = imageenc.encode_image(img.width, img.height, img.pixels)
        # decode the freshly-encoded stream and compare pixels
        redecoded = _decode_stream(enc, img.width, img.height)
        assert redecoded == img.pixels, f"{path} image {i} did not round-trip"


@pytest.mark.parametrize("path", BUILDS)
def test_our_encoding_is_no_larger_than_the_shipped_one(path):
    # Efficiency matters: a re-encode <= original means the common same-size swap fits in place.
    from fireflymcf import guidata
    data = _data(path)
    gs, _ = guidata.guidata_extent(data)
    t0 = sorted(guidata.object_tables(data)[0])
    for oi, start in enumerate(t0):
        end = t0[oi + 1] if oi + 1 < len(t0) else None
        if end is None:
            continue
        idx = guidata.object_tables(data)[0].index(start)
        img = images.decode_image(data, idx)
        enc = imageenc.encode_image(img.width, img.height, img.pixels)
        assert len(enc) <= (end - start), f"{path} image {idx}: re-encode grew"


def test_a_synthetic_gradient_and_a_bilevel_pattern_round_trip():
    # Not from the corpus: a smooth gradient (fill/literal mix) and a checkerboard (repeat rows).
    w, h = 40, 30
    grad = bytes(c for y in range(h) for x in range(w)
                 for c in (x * 6 % 256, y * 8 % 256, (x + y) % 256))
    check = bytes(c for y in range(h) for x in range(w)
                  for c in ((0, 0, 0) if (x // 4 + y // 4) % 2 else (255, 255, 255)))
    for px in (grad, check):
        enc = imageenc.encode_image(w, h, px)
        assert _decode_stream(enc, w, h) == px


def test_replace_image_writes_a_new_image_in_place_and_reseals():
    # An owner swapping a logo: recolor an image (R<->B), write it back same-size, and the resealed
    # MCF must decode to the new pixels, leave its neighbours untouched, and still pass the gates.
    from fireflymcf import relocate, coverage
    data = _data("firefly-13.6/mcf.bin")
    img = images.decode_image(data, 3)
    px = bytearray(img.pixels)
    for i in range(0, len(px), 3):
        px[i], px[i + 2] = px[i + 2], px[i]
    new = imageenc.replace_image(data, 3, img.width, img.height, bytes(px))
    assert images.decode_image(new, 3).pixels == bytes(px)
    assert images.decode_image(new, 2).pixels == images.decode_image(data, 2).pixels
    assert relocate._boundary_oracle(new)[0]
    assert coverage.analyse(new, deep=True).unclaimed == []


def test_replace_image_refuses_an_image_too_large_to_fit_in_place():
    # A random-noise image of the same dimensions cannot be run-length compressed, so it overflows
    # the original's byte span -- replace_image must refuse rather than corrupt neighbouring data.
    data = _data("firefly-13.6/mcf.bin")
    img = images.decode_image(data, 3)
    noise = bytes((x * 7 + y * 13 + c * 53) % 256
                  for y in range(img.height) for x in range(img.width) for c in range(3))
    with pytest.raises(ValueError, match="needs the relocator"):
        imageenc.replace_image(data, 3, img.width, img.height, noise)


def _decode_stream(buf: bytes, width: int, height: int) -> bytes:
    import struct
    out = bytearray(); p = 0; prev = None
    for _ in range(height):
        rl = struct.unpack_from("<H", buf, p)[0]
        if rl == 0:
            out += prev; p += 2; continue
        q, e, b = p + 2, p + rl, bytearray()
        while q < e:
            c = buf[q]; q += 1; cnt = c & 0x7F
            if c & 0x80:
                b += buf[q:q + cnt * 3]; q += cnt * 3
            else:
                b += buf[q:q + 3] * cnt; q += 3
        out += b; prev = b; p = e
    return bytes(out)
