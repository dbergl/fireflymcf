"""The 8 GuiData artwork images (object table 0), decoded by `fireflymcf.images`.

Format proven from the blitter `FUN_002513a4` (GuiCode): per-row `<u16 len>` + RLE runs, each run
`<(literal<<7)|count>` + RGB888 (fill = one repeated, literal = `count` verbatim); a zero-length row
is fully transparent. Confirmed by decoding every image to real artwork (Firefly / MIRA / ECLIPSE
logos + product QR codes).
"""
import pytest

from fireflymcf import images

BUILDS = {
    "firefly-13.6/mcf.bin": [(125, 125), (150, 150), (208, 61), (180, 180),
                             (139, 139), (180, 180), (139, 139), (150, 150)],
    "firefly-11.23/mcf.bin": [(125, 125), (150, 150), (208, 61), (180, 180),
                              (139, 139), (180, 180), (139, 139), (150, 150)],
    # 14.7 (SW20) carries 9 images (extra 50x50 / 75x75) and pads its dimension arrays.
    "firefly-14.7 (Terrain)/mcf.bin": [(50, 50), (150, 150), (208, 61), (75, 75), (180, 180),
                                       (139, 139), (180, 180), (139, 139), (150, 150)],
}


def _data(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("path,dims", BUILDS.items())
def test_every_image_decodes_to_its_declared_size(path, dims):
    data = _data(path)
    assert images.image_count(data) == len(dims)
    for i, (w, h) in enumerate(dims):
        img = images.decode_image(data, i)
        assert (img.width, img.height) == (w, h)
        assert len(img.pixels) == w * h * 3          # opaque RGB, one triple per pixel


def _rows(img):
    stride = img.width * 3
    return [img.pixels[r * stride:(r + 1) * stride] for r in range(img.height)]


def test_a_zero_length_row_repeats_the_previous_row():
    # img 0 (125x125 QR) has zero-length rows (verified: rows 11-14). The regression: those were
    # decoded as blank instead of repeating the previous row, which put black gaps through the
    # QR modules. Assert those exact rows equal their predecessor.
    import struct
    from fireflymcf import guidata
    import fireflymcf.recipe_ops as ro
    data = _data("firefly-13.6/mcf.bin")
    gs, _ = ro._gs_tab(data)
    p = gs + guidata.object_tables(data)[0][0]
    zero_rows = []
    for row in range(125):
        rl = struct.unpack_from("<H", data, p)[0]
        if rl == 0:
            zero_rows.append(row); p += 2
        else:
            p += rl
    assert zero_rows, "img0 must contain zero-length (repeat) rows for this to be meaningful"
    rows = _rows(images.decode_image(data, 0))
    for r in zero_rows:
        assert rows[r] == rows[r - 1], f"row {r} should repeat row {r-1}"


def test_a_qr_image_is_only_black_and_white_ish():
    # a correct QR decode is near-bilevel; the bug left mid-grey/black streaks. Check the palette
    # is dominated by near-black and near-white with few mid-tones.
    img = images.decode_image(_data("firefly-13.6/mcf.bin"), 7)
    px = img.pixels
    mid = sum(1 for i in range(0, len(px), 3) if 0x40 < px[i] < 0xC0)
    assert mid / (len(px) // 3) < 0.15, "too many mid-tone pixels for a QR/data-matrix"


def test_out_of_range_index_is_rejected():
    with pytest.raises(ValueError):
        images.decode_image(_data("firefly-13.6/mcf.bin"), 99)
