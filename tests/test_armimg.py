import pytest

from tests import answer_key
from fireflymcf import armimg


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_header_matches_answer_key(corpus, version):
    expected = answer_key.CLI_HEADER[version]
    hdr = armimg.parse_header(corpus[version]["cli"])
    assert hdr.magic == expected["magic"]
    assert hdr.version == expected["version"]
    assert len(hdr.regions) == 2
    r1, r2 = hdr.regions
    assert (r1.load_addr, r1.file_offset, r1.length) == (
        expected["r1_load"], expected["r1_off"], expected["r1_len"])
    assert (r2.load_addr, r2.file_offset, r2.length) == (
        expected["r2_load"], expected["r2_off"], expected["r2_len"])
    assert hdr.word_24 == expected["word_24"]
    assert hdr.word_28 == expected["word_28"]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_regions_tile_the_file_exactly(corpus, version):
    """Region 1 starts at the end of the header, region 2 starts at the end of
    region 1, and region 2 ends at EOF. No gaps, no overlap, nothing unaccounted."""
    data = corpus[version]["cli"]
    hdr = armimg.parse_header(data)
    r1, r2 = hdr.regions
    assert r1.file_offset == 0x2C
    assert r1.file_offset + r1.length == r2.file_offset
    assert r2.file_offset + r2.length == len(data)


def test_bad_magic_raises():
    with pytest.raises(ValueError, match="magic"):
        armimg.parse_header(b"\x00" * 64)


def test_truncated_input_raises():
    with pytest.raises(ValueError):
        armimg.parse_header(bytes.fromhex("2e101bc1"))


def test_vector_table_of_cli_region_1(cli_136):
    hdr = armimg.parse_header(cli_136)
    r1 = hdr.regions[0]
    payload = cli_136[r1.file_offset:r1.file_offset + r1.length]
    vt = armimg.read_vector_table(payload, r1.load_addr)
    assert vt.initial_sp == answer_key.CLI_INITIAL_SP
    assert vt.reset == answer_key.CLI_ENTRY


def test_vector_table_handlers_are_thumb_and_in_range(cli_136):
    """Every non-null handler must be odd (Thumb) and land inside the loaded image.
    This is what makes the load address falsifiable rather than assumed."""
    hdr = armimg.parse_header(cli_136)
    r1 = hdr.regions[0]
    payload = cli_136[r1.file_offset:r1.file_offset + r1.length]
    vt = armimg.read_vector_table(payload, r1.load_addr)
    lo, hi = r1.load_addr, r1.load_addr + r1.length
    seen = 0
    for h in vt.handlers:
        if h == 0:
            continue
        seen += 1
        assert h & 1, f"handler {h:#010x} is not Thumb-tagged"
        assert lo <= (h & ~1) < hi, f"handler {h:#010x} outside [{lo:#x},{hi:#x})"
    assert seen >= 4, "expected several real handlers"


def test_looks_like_vector_table_accepts_the_real_one(cli_136):
    hdr = armimg.parse_header(cli_136)
    r1 = hdr.regions[0]
    payload = cli_136[r1.file_offset:r1.file_offset + r1.length]
    assert armimg.looks_like_vector_table(payload, r1.load_addr, r1.length)


def test_looks_like_vector_table_rejects_noise():
    assert not armimg.looks_like_vector_table(b"\x00" * 256, 0x20000000, 256)
    assert not armimg.looks_like_vector_table(bytes(range(256)), 0x20000000, 256)
