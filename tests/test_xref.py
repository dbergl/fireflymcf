from fireflymcf import container, xref


def test_find_u32_locates_the_mcf_magic_in_its_own_file(corpus):
    """Sanity check on the scanner: the magic is at offset 0 of every MCF."""
    hits = xref.find_u32(corpus["13.6"]["mcf"], container.MAGIC)
    assert 0 in hits


def test_find_u32_returns_empty_for_an_absent_value():
    assert xref.find_u32(b"\x00" * 1024, 0xDEADBEEF) == []


def test_find_u32_finds_every_occurrence():
    needle = (0x12345678).to_bytes(4, "little")
    data = b"\xaa" * 8 + needle + b"\xbb" * 8 + needle
    assert xref.find_u32(data, 0x12345678) == [8, 20]


def test_find_stride_constants_reports_the_known_stride(corpus):
    """0x84 is the size of an MCF section descriptor, so it is present in the MCF
    itself -- it is the u16 at each descriptor's +0x02.

    This exercises the scanner, nothing more. It is deliberately run against the MCF
    rather than against a firmware image: Task 6 proved the firmware never uses 0x84
    as a stride (it locates descriptors through a u32 pointer array), so finding 0x84
    in an image is not evidence that the image parses the container.
    """
    found = xref.find_stride_constants(corpus["13.6"]["mcf"])
    assert 0x84 in found


def test_scan_images_covers_every_input(corpus):
    result = xref.scan_images({"mcf": corpus["13.6"]["mcf"],
                               "cli": corpus["13.6"]["cli"]})
    assert set(result) == {"mcf", "cli"}
    for report in result.values():
        assert "magic_hits" in report
        assert "stride_hits" in report
