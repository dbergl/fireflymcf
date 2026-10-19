"""Total byte coverage: every byte of every MCF must be claimed by exactly one parser.

The tests below are written so they cannot pass on an empty result. Each one that walks
a report also asserts that the thing it is walking is non-empty, because `all([])` is
True and a coverage test that vacuously passes is worse than no coverage test.
"""
import struct

import pytest

from fireflymcf import container, coverage

VERSIONS = ["11.23", "13.6", "14.7"]


# ---------------------------------------------------------------------------
# The brief's falsifier: no unclaimed bytes, no overlaps.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_every_byte_is_accounted_for(corpus, version):
    report = coverage.analyse(corpus[version]["mcf"])
    assert report.unclaimed == [], (
        f"{version}: {len(report.unclaimed)} unclaimed spans, "
        f"first at {report.unclaimed[0][0]:#x} "
        f"({report.unclaimed[0][1]} bytes)")


@pytest.mark.parametrize("version", VERSIONS)
def test_claimed_equals_file_size(corpus, version):
    data = corpus[version]["mcf"]
    report = coverage.analyse(data)
    assert report.total == len(data)
    assert report.claimed == len(data)


def test_claims_do_not_overlap(corpus):
    """Two parsers claiming the same bytes means one of them is wrong."""
    report = coverage.analyse(corpus["13.6"]["mcf"])
    spans = sorted(report.spans)
    assert len(spans) > 1
    for (a_start, a_len, a_label), (b_start, _b_len, b_label) in zip(spans, spans[1:]):
        assert a_start + a_len <= b_start, (
            f"{a_label} overlaps {b_label} at {b_start:#x}")


def test_overlapping_claim_is_rejected():
    report = coverage.CoverageReport(total=100)
    coverage.claim(report, 0, 50, "a")
    with pytest.raises(ValueError, match="overlap"):
        coverage.claim(report, 40, 20, "b")


def test_out_of_range_claim_is_rejected():
    report = coverage.CoverageReport(total=100)
    with pytest.raises(ValueError, match="range"):
        coverage.claim(report, 90, 20, "past-eof")


def test_empty_claim_is_rejected():
    """A zero-length claim accounts for nothing while reading like a claim."""
    report = coverage.CoverageReport(total=100)
    with pytest.raises(ValueError, match="empty"):
        coverage.claim(report, 10, 0, "nothing")


@pytest.mark.parametrize("start,length", [
    (10, 5),     # wholly inside an existing claim
    (0, 10),     # sharing the existing claim's start
    (45, 5),     # sharing the existing claim's end
    (0, 60),     # wholly containing an existing claim
])
def test_contained_and_containing_claims_are_rejected(start, length):
    """`claim` only inspects the two neighbouring spans, so containment in either
    direction has to be checked explicitly -- it is the case a neighbour-only test
    would miss."""
    report = coverage.CoverageReport(total=100)
    coverage.claim(report, 0, 50, "a")
    with pytest.raises(ValueError, match="overlap"):
        coverage.claim(report, start, length, "b")


# ---------------------------------------------------------------------------
# The deep pass: GuiData claimed structure by structure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_deep_analysis_is_also_total(corpus, version):
    """With GuiData opened up, the sub-claims must still tile the whole file: they
    have to fill the section exactly, neither leaving a hole nor spilling past it."""
    data = corpus[version]["mcf"]
    report = coverage.analyse(data, deep=True)
    assert report.unclaimed == [], (
        f"{version}: {len(report.unclaimed)} unclaimed spans, "
        f"first at {report.unclaimed[0][0]:#x} ({report.unclaimed[0][1]} bytes)")
    assert report.claimed == len(data)


@pytest.mark.parametrize("version", VERSIONS)
def test_deep_analysis_opens_the_guidata_section(corpus, version):
    """The deep pass must actually replace the one big GuiData span with structure --
    otherwise `test_deep_analysis_is_also_total` would pass on the shallow claims."""
    data = corpus[version]["mcf"]
    guidata_section = next(
        s for s in container.parse_container(data).sections if "GuiData" in s.name)
    shallow = coverage.analyse(data)
    deep = coverage.analyse(data, deep=True)

    assert any(label == f"section:{guidata_section.name}"
               for _, _, label in shallow.spans)
    assert not any(label == f"section:{guidata_section.name}"
                   for _, _, label in deep.spans)

    inside = [
        (start, length, label) for start, length, label in deep.spans
        if guidata_section.offset <= start < guidata_section.offset + guidata_section.length
    ]
    # 1000+ object spans, plus the tables, the head and the image container header.
    assert len(inside) > 1000
    assert sum(length for _, length, _ in inside) == guidata_section.length
    labels = {label.split(":", 1)[0] for _, _, label in inside}
    assert {"guidata-head", "guidata-table-descriptors", "guidata-table",
            "image-container-header", "object-table", "object",
            "padding"} <= labels


@pytest.mark.parametrize("version,counts", [
    ("11.23", [8, 1201, 30, 2687]),
    ("13.6", [8, 1422, 30, 3295]),
    ("14.7", [9, 1077, 29, 3171]),
])
def test_all_four_object_tables_are_found(corpus, version, counts):
    """The four object-pointer tables per file, with their entry counts.

    Pinned because a higher minimum run length silently drops the 8- and 30-entry
    tables, and the spans around them then stretch to cover the hole -- 100% coverage
    with 221 KB inside one span that names nothing.
    """
    found = [
        int(label.split("[", 1)[1].rstrip("]"))
        for _, _, label in coverage.analyse(corpus[version]["mcf"], deep=True).spans
        if label.startswith("object-table:")
    ]
    assert found == counts


@pytest.mark.parametrize("version", VERSIONS)
def test_image_container_header_is_a_header_not_a_blob(corpus, version):
    """0x80 bytes in 11.23 and 13.6, 0x8C in 14.7. This span's extent is the distance
    from a magic to the next pointer table, so it is the claim most able to absorb an
    undetected structure; it must stay header-sized."""
    headers = [length for _, length, label
               in coverage.analyse(corpus[version]["mcf"], deep=True).spans
               if label == "image-container-header"]
    assert len(headers) == 1
    assert 0 < headers[0] <= 0x100


def test_a_missed_pointer_table_raises_instead_of_being_absorbed(corpus, monkeypatch):
    """The guard that the first version of this module did not have.

    Raising the minimum run length back to 64 hides the 8- and 30-entry tables. The
    `image-container-header` span then stretches from 128 bytes to 221,512 and the file
    still reports 100% claimed -- a catch-all wearing the name of a real structure. The
    size bound on that span must turn it into a loud failure instead.
    """
    monkeypatch.setattr(coverage, "MIN_POINTER_TABLE", 64)
    with pytest.raises(coverage.CoverageError, match="image sub-container header"):
        coverage.analyse(corpus["13.6"]["mcf"], deep=True)


def test_an_oversized_trailing_object_raises(corpus, monkeypatch):
    """The other half of the same guard, on the spans that have no next pointer.

    Dropping the last object table leaves the previous table's trailing object running
    all the way to the section checksum. That is exactly the shape of an absorbed
    structure, and the size bound must reject it.
    """
    real = coverage._pointer_tables
    monkeypatch.setattr(coverage, "_pointer_tables",
                        lambda *args, **kwargs: real(*args, **kwargs)[:-1])
    with pytest.raises(coverage.CoverageError, match="absorbing something else"):
        coverage.analyse(corpus["13.6"]["mcf"], deep=True)


@pytest.mark.parametrize("version,headroom", [
    ("11.23", 58092),
    ("13.6", 59144),
    ("14.7", 60148),
])
def test_trailing_object_headroom_is_pinned(corpus, version, headroom):
    """The weakest claim in the module, measured so it cannot grow unnoticed.

    One object per table -- the last -- ends where the next structure starts and
    nowhere else, so the only guard on it is a size bound. `trailing_headroom` is how
    many more bytes that bound would still have accepted. Pinning the number means a
    change that widens the slack shows up as a test failure rather than as a silently
    more generous claim.
    """
    data = corpus[version]["mcf"]
    report = coverage.analyse(data, deep=True)
    assert report.trailing_headroom == headroom
    assert coverage.analyse(data).trailing_headroom == 0

    trailing = [(start, length) for start, length, label in report.spans
                if label.startswith("object-trailing:")]
    assert len(trailing) == 4, "one trailing object per object table"


@pytest.mark.parametrize("version", VERSIONS)
def test_the_uniform_stride_table_is_uniform_20_byte_stride(corpus, version):
    """§10 item 13: the 29/30-entry table is a uniform 20-byte-stride table.

    The trailing-object bound on that table is not the max-sibling heuristic the other
    three take -- it is the stride itself, which is why its headroom is zero by
    construction rather than a knife edge. That reasoning is only sound if the table
    really is uniform stride, so pin it: every object it delimits must be exactly 20
    bytes, in every build.
    """
    data = corpus[version]["mcf"]
    section = next(s for s in container.parse_container(data).sections
                   if "GuiData" in s.name)
    end = section.offset + ((section.length - 4) & ~1)
    tables = coverage._pointer_tables(data, section.offset, end)
    # The small table -- 30 entries in 11.23/13.6, 29 in 14.7 -- is the uniform one.
    small = [t for t in tables if len(t[2]) in (29, 30)]
    assert len(small) == 1, "the uniform-stride table should be found exactly once"
    _, _, pointers = small[0]
    distinct = sorted(set(pointers))
    sizes = {b - a for a, b in zip(distinct, distinct[1:])}
    assert sizes == {20}, f"{version}: expected a pure 20-byte stride, got {sizes}"


def test_a_non_stride_trailing_object_in_the_uniform_table_raises(corpus, monkeypatch):
    """The uniform-stride bound must fire when the trailing span is not one stride.

    Dropping the last object table makes the uniform 30-entry table the last one, so its
    trailing object runs all the way to the section checksum -- far more than one 20-byte
    record. That is the shape of an absorbed structure, and the stride bound must reject
    it with the stride-specific message, not the max-sibling one.
    """
    real = coverage._pointer_tables
    monkeypatch.setattr(coverage, "_pointer_tables",
                        lambda *args, **kwargs: real(*args, **kwargs)[:-1])
    with pytest.raises(coverage.CoverageError,
                       match="uniform-stride table.*not the table's 0x14-byte stride"):
        coverage.analyse(corpus["13.6"]["mcf"], deep=True)


@pytest.mark.parametrize("version", VERSIONS)
def test_coverage_and_guidata_agree_on_the_object_tables(corpus, version):
    """The two modules run the same scan and must not contradict each other.

    `guidata.object_tables` used a minimum run length of 64 and so reported two tables
    where there are four -- a false statement about the format sitting in a module
    docstring. Lowering it to 4 is output-neutral (see
    tests/test_guidata.py::test_lowering_the_object_table_minimum_changes_no_answer).
    """
    data = corpus[version]["mcf"]
    from fireflymcf import guidata

    section = next(s for s in container.parse_container(data).sections
                   if "GuiData" in s.name)
    end = section.offset + ((section.length - 4) & ~1)
    mine = [len(pointers) for _, _, pointers
            in coverage._pointer_tables(data, section.offset, end)]
    assert [len(t) for t in guidata.object_tables(data)] == mine
    assert len(mine) == 4


@pytest.mark.parametrize("version", VERSIONS)
def test_every_object_span_is_non_empty(corpus, version):
    """An object table whose pointers repeat or run backwards would produce empty or
    negative spans; `claim` would take a zero-length one silently."""
    objects = [
        (start, length, label)
        for start, length, label in coverage.analyse(corpus[version]["mcf"], deep=True).spans
        if label.startswith("object:")
    ]
    assert len(objects) > 1000
    assert all(length > 0 for _, length, _ in objects)


# ---------------------------------------------------------------------------
# Padding discipline.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_padding_claims_contain_only_padding_bytes(corpus, version):
    data = corpus[version]["mcf"]
    padding = [(start, length) for start, length, label
               in coverage.analyse(data, deep=True).spans if label == "padding"]
    assert padding, "no padding was claimed at all -- this test would be vacuous"
    for start, length in padding:
        assert set(data[start:start + length]) <= {0x00, 0xFF}, (
            f"{version}: padding claim at {start:#x} contains real data")


def test_padding_rule_rejects_anything_else():
    assert coverage.is_padding(b"\x00" * 16)
    assert coverage.is_padding(b"\xff" * 16)
    assert coverage.is_padding(b"\x00\xff\x00\xff")
    assert not coverage.is_padding(b"\x00" * 15 + b"\x01")
    assert not coverage.is_padding(b"\xfe")


# ---------------------------------------------------------------------------
# Checksums: claimed only after being verified.
# ---------------------------------------------------------------------------


def test_crc_matches_every_stored_checksum(corpus):
    """The 27 stored checksum fields of the corpus -- 3 header, 12 descriptor and 12
    section-payload -- all verify under CRC-16/AUG-CCITT."""
    checked = 0
    for version in VERSIONS:
        data = corpus[version]["mcf"]
        c = container.parse_container(data)
        fields = [(0, 0xB4)]
        for i in range(c.section_count):
            at = struct.unpack_from("<I", data, c.header_word_20 + 4 * i)[0]
            size = struct.unpack_from("<H", data, at + 2)[0]
            fields.append((at, at + size - 4))
        for s in c.sections:
            fields.append((s.offset, s.offset + ((s.length - 4) & ~1)))
        for start, at in fields:
            stored = struct.unpack_from("<H", data, at)[0]
            assert coverage.crc16_aug_ccitt(data[start:at]) == stored, (
                f"{version}: checksum at {at:#x} does not verify")
            checked += 1
    assert checked == 27


@pytest.mark.parametrize("version", VERSIONS)
def test_a_corrupted_payload_is_rejected_not_claimed(corpus, version):
    """A checksum claim is worth nothing unless a wrong checksum raises. Mutates an
    in-memory copy; the corpus files are never written."""
    data = bytearray(corpus[version]["mcf"])
    section = next(s for s in container.parse_container(bytes(data)).sections
                   if "GuiData" in s.name)
    data[section.offset + 0x40] ^= 0x01
    with pytest.raises(coverage.CoverageError, match="checksum"):
        coverage.analyse(bytes(data))


@pytest.mark.parametrize("first_offset", [
    0x0008,   # equal to the head size: no room for the descriptor that declares it
    0x0000,   # before the head
    0x0031,   # not a whole number of descriptors past the head
])
def test_a_malformed_guidata_descriptor_chain_raises_coverage_error(corpus, first_offset):
    """Malformed input must raise CoverageError, not IndexError or a claim.

    0x0008 is the case that used to walk zero descriptors and then index `tags[0]`.
    """
    data = bytearray(corpus["13.6"]["mcf"])
    section = next(s for s in container.parse_container(bytes(data)).sections
                   if "GuiData" in s.name)
    struct.pack_into("<H", data, section.offset + 0x08 + 4, first_offset)
    with pytest.raises(coverage.CoverageError, match="first table"):
        coverage.analyse(bytes(data), deep=True)


def test_format_report_states_that_claimed_is_not_decoded(corpus):
    """The CLI output is what a documentation author pastes; the caveat the module
    docstring carries has to be in the printed report too."""
    text = coverage.format_report(coverage.analyse(corpus["13.6"]["mcf"], deep=True))
    assert "NOT decoded" in text
    assert "record level" in text
    assert "59144" in text, "the trailing-object headroom must be printed"


def test_a_corrupted_header_is_rejected_not_claimed(corpus):
    data = bytearray(corpus["13.6"]["mcf"])
    data[0x30] ^= 0x01          # inside the config-name field, before the checksum
    with pytest.raises(coverage.CoverageError, match="checksum"):
        coverage.analyse(bytes(data))


def test_crc_matches_the_bit_serial_reference_on_random_data():
    """`crc16_aug_ccitt` is `binascii.crc_hqx` seeded with `CRC_INIT`; this pins it to the
    bit-serial definition it replaced, on lengths 0..40 and a few kilobytes of random input.
    Stub either side to a constant and this fails, so it can fail."""
    import random
    rng = random.Random(0x1D0F)
    for n in list(range(0, 41)) + [255, 256, 4097]:
        blob = bytes(rng.getrandbits(8) for _ in range(n))
        assert coverage.crc16_aug_ccitt(blob) == coverage._crc16_aug_ccitt_reference(blob), n
    assert coverage.crc16_aug_ccitt(b"123456789") == 0xE5CC     # the catalogue check value
