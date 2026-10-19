import struct

import pytest

from tests import answer_key
from fireflymcf import container


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_config_name(corpus, version):
    c = container.parse_container(corpus[version]["mcf"])
    assert c.config_name == answer_key.CONFIG_NAMES[version]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_section_count_is_four(corpus, version):
    """The u16 at +0x20 reads 4 in every file, and all four descriptors really are
    named -- see test_every_file_has_four_named_sections_including_g12 below. 13.6 was
    once thought to name only three; its fourth is the bare 3-character "G12", which a
    6-character minimum regex dropped in early recon."""
    c = container.parse_container(corpus[version]["mcf"])
    assert c.section_count == 4


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_named_sections_match_answer_key(corpus, version):
    c = container.parse_container(corpus[version]["mcf"])
    named = [s.name for s in c.sections if s.name]
    assert named == answer_key.SECTION_NAMES[version]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_every_file_has_four_named_sections_including_g12(corpus, version):
    """All three files carry a G12 section: 11.23 and 14.7 name it with a version
    suffix ("G12 11v23", "G12 14v7"); 13.6's is unversioned, just "G12". The bare
    3-character form was missed in earlier recon (a 6-char minimum regex dropped
    it), which is why it was once thought absent."""
    c = container.parse_container(corpus[version]["mcf"])
    named = [s.name for s in c.sections if s.name]
    assert len(named) == 4
    g12 = [n for n in named if n.startswith("G12")]
    assert len(g12) == 1
    if version == "13.6":
        assert g12[0] == "G12"
    else:
        assert g12[0] != "G12"


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_section_extents_are_inside_the_file(corpus, version):
    data = corpus[version]["mcf"]
    c = container.parse_container(data)
    for s in c.sections:
        if s.length == 0:
            continue
        assert 0 < s.offset < len(data), f"{s.name}: offset {s.offset:#x} out of range"
        assert s.offset + s.length <= len(data), f"{s.name}: extends past EOF"


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_sections_do_not_overlap(corpus, version):
    c = container.parse_container(corpus[version]["mcf"])
    spans = sorted(
        (s.offset, s.offset + s.length) for s in c.sections if s.length)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert prev_end <= next_start, "sections overlap"


def test_bad_magic_raises():
    with pytest.raises(ValueError, match="magic"):
        container.parse_container(b"\x00" * 1024)


def test_find_extent_position_rejects_ambiguous_candidates():
    """Two different entry-relative positions can each look like a valid,
    file-tiling (offset, length) pair -- the selection must refuse to
    tie-break between them rather than silently picking one (e.g. the
    lowest), which is exactly the failure mode a plausible-but-wrong field
    position would produce with no visible sign anything was wrong."""
    def pair(off: int, length: int) -> bytes:
        return struct.pack("<II", off, length)

    # Two 16-byte "entries" against a 100-byte file. Position 0 and position 8
    # both read (1, 50) then (51, 49) -- both tile the file exactly (1..51..100).
    entry0 = pair(1, 50) + pair(1, 50)
    entry1 = pair(51, 49) + pair(51, 49)
    with pytest.raises(ValueError, match="ambiguous"):
        container._find_extent_position([entry0, entry1], 100)


def test_find_extent_position_rejects_no_surviving_candidate():
    """If nothing both (a) looks like a plausible offset/length pair in every
    entry and (b) tiles the file end-to-end, the function must raise rather
    than fall back to a zeroed or arbitrary extent."""
    entry0 = struct.pack("<II", 0, 0) * 2
    entry1 = struct.pack("<II", 0, 0) * 2
    with pytest.raises(ValueError, match="ambiguous"):
        container._find_extent_position([entry0, entry1], 100)


def test_extract_writes_one_file_per_named_section(corpus, tmp_path):
    written = container.extract_sections(corpus["13.6"]["mcf"], tmp_path)
    assert len(written) == 4
    names = sorted(p.name for p in written)
    assert names == sorted([
        "00-LCD_Firmware.bin",
        "01-Lyra_13v6_GuiCode.bin",
        "02-Lyra_13v6_GuiData.bin",
        "03-G12.bin",
    ])
    for p in written:
        assert p.stat().st_size > 0


def test_extracted_bytes_match_the_source_slice(corpus, tmp_path):
    data = corpus["13.6"]["mcf"]
    c = container.parse_container(data)
    written = container.extract_sections(data, tmp_path)
    by_index = {s.index: s for s in c.sections if s.name}
    for path in written:
        idx = int(path.name.split("-", 1)[0])
        s = by_index[idx]
        assert path.read_bytes() == data[s.offset:s.offset + s.length]


# ---------------------------------------------------------------------------------------
# Which container-header fields the loader interprets -- MCF-FORMAT.md §10 item 3.
#
# The container parser in cli.bin reads the header as a fixed 0xA4-byte block, folds the
# whole block into a header checksum, and then reads exactly four fields out of it:
# +0x00 magic, +0x04 checksum locator, +0x20 descriptor count, +0x22 header size
# (docs/PARSER-LOCATION.md). Those fields sit in the struct at [r4, #0x8e8 + offset] in
# 13.6 and 14.7 (byte-identical clis). +0x06 and +0x08..+0x1F are checksummed but read by
# no field access, so they are reserved -- and, being >= 0x8e8, they can only be reached
# by the wide T3 encoding this scan covers exhaustively (the narrow T1 immediate cannot
# reach that far), so an absence here is a real negative, not a scan blind spot.
# ---------------------------------------------------------------------------------------

# Wide T3 load/store first halfwords with base register r4 (nnnn = 4).
_WIDE_R4_MEM = {
    0xF8D4: "ldr.w", 0xF8B4: "ldrh.w", 0xF894: "ldrb.w",
    0xF9B4: "ldrsh.w", 0xF994: "ldrsb.w",
    0xF8C4: "str.w", 0xF8A4: "strh.w", 0xF884: "strb.w",
}
_HEADER_STRUCT_BASE = 0x8E8  # [r4, #0x8e8] == header[+0x00] in 13.6/14.7


def _header_fields_the_loader_touches(cli: bytes) -> set[int]:
    """Header offsets read/written via [r4, #0x8e8 + offset] anywhere in region 1."""
    from fireflymcf import armimg

    hdr = armimg.parse_header(cli)
    region1 = cli[hdr.regions[0].file_offset:
                  hdr.regions[0].file_offset + hdr.regions[0].length]
    fields: set[int] = set()
    for off in range(0, len(region1) - 4, 2):
        hw1 = struct.unpack_from("<H", region1, off)[0]
        if hw1 in _WIDE_R4_MEM:
            imm = struct.unpack_from("<H", region1, off + 2)[0] & 0xFFF
            if _HEADER_STRUCT_BASE <= imm <= _HEADER_STRUCT_BASE + 0x22:
                fields.add(imm - _HEADER_STRUCT_BASE)
    return fields


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_the_loader_reads_only_four_header_fields(corpus, version):
    """§10 item 3: +0x06 and +0x08..+0x1F are read by no field access -- reserved."""
    fields = _header_fields_the_loader_touches(corpus[version]["cli"])
    assert fields == {0x00, 0x04, 0x20, 0x22}, (
        f"{version}: loader touches header fields {sorted(hex(f) for f in fields)}")
    # The reserved bytes, stated as the negative that matters:
    assert 0x06 not in fields
    assert not (fields & set(range(0x08, 0x20)))


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_the_reserved_header_bytes_are_constant_across_the_corpus(corpus, version):
    """The reserved fields carry the same bytes in every shipping build: +0x06 == 0x0001,
    +0x08..+0x1F all zero. Constant across 11.x/13.x/14.x and read by nothing -- reserved.
    """
    mcf = corpus[version]["mcf"]
    assert struct.unpack_from("<H", mcf, 0x06)[0] == 0x0001
    assert mcf[0x08:0x20] == b"\x00" * 0x18


# ---------------------------------------------------------------------------------------
# The two descriptor fields §2 once labelled `unknown`: +0x45 and +0x50..+0x57.
#
# +0x45 (=0x03 in every descriptor of every build) is NOT reserved: it is a flags byte the
# loader reads from the state-struct descriptor copy ([r4, #0x239], since desc+N is at
# [r5, #0x1f4 + N] with r5 = 0x20034DD0) and branches on. At cli 0x20016a5e it tests bit 1
# (`lsls #0x1e` + `bpl`) and at 0x20016a8c bit 2 (`lsls #0x1d` + `bpl`), each gating a call
# into the tagged-entry / per-section helpers 0x20014734 / 0x20014238. Stock 0x03 sets bits
# 0 and 1, so the bit-1 path is enabled and the bit-2 path disabled. (There is a second,
# per-section copy at state offset +0x902 that is written once and never read -- dead.)
#
# +0x50..+0x57 IS reserved: the fixed-field reads stop at +0x4c (payload length) and the
# tagged-entry walk is indexed from +0x58 (first tag at +0x58, stride 8, count at +0x46),
# so the 8 bytes between them are read by nothing. Constant zero in every build.
# ---------------------------------------------------------------------------------------

_DESCRIPTOR_STRUCT_BASE = 0x1F4  # desc+N is at [r5, #0x1f4 + N], r5 = 0x20034DD0

# `ldrb.w r3, [r4, #0x239]` -- the read of the state-struct copy of desc+0x45.
_READ_PLUS_45 = bytes.fromhex("94 f8 39 32")
_PLUS_45_READ_SITES = (0x20016A5E, 0x20016A8C)  # cli region-1 addresses, 13.6 == 14.7


def _descriptor_offsets(mcf: bytes) -> list[int]:
    count = struct.unpack_from("<H", mcf, 0x20)[0]
    return [struct.unpack_from("<I", mcf, 0xA4 + 4 * i)[0] for i in range(count)]


def _descriptor_fields_the_loader_touches(cli: bytes) -> set[int]:
    """Descriptor offsets read/written via the state-struct copy [r4|r5, #0x1f4 + off]."""
    from fireflymcf import armimg

    hdr = armimg.parse_header(cli)
    region1 = cli[hdr.regions[0].file_offset:
                  hdr.regions[0].file_offset + hdr.regions[0].length]
    fields: set[int] = set()
    for off in range(0, len(region1) - 4, 2):
        hw1 = struct.unpack_from("<H", region1, off)[0]
        # wide T3 load/store with base r4 (…4) or r5 (…5)
        if (hw1 & 0xFFF0) in (0xF8D0, 0xF8B0, 0xF890, 0xF9B0, 0xF990,
                              0xF8C0, 0xF8A0, 0xF880) and (hw1 & 0xF) in (4, 5):
            disp = struct.unpack_from("<H", region1, off + 2)[0] & 0xFFF
            if _DESCRIPTOR_STRUCT_BASE + 0x44 <= disp <= _DESCRIPTOR_STRUCT_BASE + 0x6b:
                fields.add(disp - _DESCRIPTOR_STRUCT_BASE)
    return fields


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_descriptor_plus_45_is_0x03_everywhere(corpus, version):
    mcf = corpus[version]["mcf"]
    for off in _descriptor_offsets(mcf):
        assert mcf[off + 0x45] == 0x03


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_the_loader_reads_descriptor_plus_45_as_a_flags_byte(corpus, version):
    """§10 item 3: +0x45 is a tested flags byte, not reserved -- read and branched on."""
    from fireflymcf import armimg

    cli = corpus[version]["cli"]
    region1_off = armimg.parse_header(cli).regions[0].file_offset
    for addr in _PLUS_45_READ_SITES:
        fo = region1_off + (addr - 0x20000000)
        assert cli[fo:fo + 4] == _READ_PLUS_45, (
            f"{version}: expected `ldrb.w r3,[r4,#0x239]` at {addr:#x}")
    # And the scan sees +0x45 among the descriptor fields the loader reads:
    assert 0x45 in _descriptor_fields_the_loader_touches(cli)


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_descriptor_plus_50_to_57_is_reserved(corpus, version):
    """The 8-byte gap between +0x4c and the +0x58 tagged entries is zero and unread."""
    mcf = corpus[version]["mcf"]
    for off in _descriptor_offsets(mcf):
        assert mcf[off + 0x50:off + 0x58] == b"\x00" * 8


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_no_state_struct_read_touches_descriptor_plus_50_to_57(corpus, version):
    fields = _descriptor_fields_the_loader_touches(corpus[version]["cli"])
    assert not (fields & set(range(0x50, 0x58))), (
        f"{version}: a state-struct access lands in the +0x50..+0x57 gap: {fields}")


# ---------------------------------------------------------------------------------------
# cli.bin header +0x28 -- §10 item 2. A per-build scalar whose only reader (the panel's
# primary bootloader, magic 0xC11B102E) is not in the corpus, so it is out of code-first
# reach; recorded as a defensible negative in MCF-FORMAT §4.2. This pins the observed
# values so a corpus change or a future "resolution" that contradicts them is caught, and
# guards the ruled-out checksum hypotheses.
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("version,expected", [
    ("11.23", 0x1E6A), ("13.6", 0x9E3), ("14.7", 0x9E3),
])
def test_cli_header_word_28_observed_value(corpus, version, expected):
    from fireflymcf import armimg
    assert armimg.parse_header(corpus[version]["cli"]).word_28 == expected


def test_cli_header_word_28_is_not_a_checksum_of_the_regions(corpus):
    """The ruled-out hypothesis, kept as a guard: +0x28 is not a simple checksum of either
    region under the common algorithms (sum, xor, CRC32)."""
    import zlib
    from fireflymcf import armimg
    for version in ("11.23", "13.6"):
        cli = corpus[version]["cli"]
        h = armimg.parse_header(cli)
        want = h.word_28
        for r in h.regions:
            body = cli[r.file_offset:r.file_offset + r.length]
            assert want != sum(body) & 0xFFFFFFFF
            assert want != sum(body) & 0xFFFF
            assert want != zlib.crc32(body)
            assert want != zlib.crc32(body) & 0xFFFF


# ---------------------------------------------------------------------------------------
# ExtFuncCode (tag 0x0B) is the panel's device-function identity -- §10 item 3 / §2.
#
# The same value 0x02004665 sits on all three panel images (LCD Firmware, GuiCode, GuiData)
# and is absent on the G12 (which identifies by the short FuncCode instead). So ExtFuncCode
# names the device; LcdDescType (tag 0x0C: 1 = LCD, 2 = GuiCode) names the image within it.
# ---------------------------------------------------------------------------------------

def _tagged_entries(mcf: bytes, desc_off: int) -> dict[int, int]:
    """{tag: value} for a section descriptor's 8-byte tagged entries at +0x58."""
    n = struct.unpack_from("<H", mcf, desc_off + 0x46)[0]
    out: dict[int, int] = {}
    for e in range(n):
        eo = desc_off + 0x58 + e * 8
        tag = struct.unpack_from("<H", mcf, eo)[0]
        out[tag] = struct.unpack_from("<I", mcf, eo + 4)[0]
    return out


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_extfunccode_is_the_panel_device_identity(corpus, version):
    mcf = corpus[version]["mcf"]
    descs = _descriptor_offsets(mcf)              # [LCD, GuiCode, GuiData, G12]
    panel = [_tagged_entries(mcf, descs[i]) for i in range(3)]
    g12 = _tagged_entries(mcf, descs[3])

    # Every panel image carries the same ExtFuncCode; the G12 carries none.
    for ent in panel:
        assert ent.get(0x0B) == 0x02004665
    assert 0x0B not in g12, "the G12 should not declare ExtFuncCode"

    # LcdDescType (tag 0x0C) names the image within the device: 1 LCD, 2 GuiCode.
    assert panel[0].get(0x0C) == 1                # LCD Firmware
    assert panel[1].get(0x0C) == 2                # GuiCode


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_extfunccode_in_the_lcd_device_header_matches_the_descriptor(corpus, version):
    """The value the loader reads from the firmware's own device header (payload +0x410)
    equals the descriptor's declared ExtFuncCode -- the device/file identity match."""
    mcf = corpus[version]["mcf"]
    lcd = _descriptor_offsets(mcf)[0]
    payload = struct.unpack_from("<I", mcf, lcd + 0x48)[0]
    header_value = struct.unpack_from("<I", mcf, payload + 0x410)[0]
    assert header_value == _tagged_entries(mcf, lcd)[0x0B] == 0x02004665
    # The device header sits ExtFuncCode among version and geometry (800x480):
    assert struct.unpack_from("<H", mcf, payload + 0x414)[0] == 800
    assert struct.unpack_from("<H", mcf, payload + 0x416)[0] == 480


# ---------------------------------------------------------------------------------------
# The 0x8F7E6D5C GUI-resource sub-container (MCF-FORMAT §10 item 12).
#
# Two images carry it -- cli.bin region 2 (the loader's GUI blob) and GuiData at +0x10000 --
# behind a shared 16-byte header, and the loader's ParamID name table is its first payload.
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_the_guidata_subcontainer_matches_its_parent_stamp(corpus, version):
    """GuiData's sub-container carries the parent section's build stamp.

    The LCD Firmware cross-checks the sub-container's u16 +0x04 and byte +0x07 against the
    parent (`GuiData +0x04`/`+0x00`, `GuiCode +0x08`/`+0x0A`) and rejects on a mismatch
    (§5.1). Proving all four agree per build is what ties GuiData's palette sub-container to
    the same release as the section that carries it -- e.g. on 14.7 the stamp is 0x0807, not
    13.6's 0x1234.
    """
    from fireflymcf import subcontainer

    mcf = corpus[version]["mcf"]
    c = container.parse_container(mcf)
    gd = next(s for s in c.sections if "GuiData" in s.name)
    gc = next(s for s in c.sections if "GuiCode" in s.name)

    # Parent section head: build id is byte +0x00, version stamp is u16 +0x04 (§5.1).
    parent_build = mcf[gd.offset]
    parent_version = struct.unpack_from("<H", mcf, gd.offset + 4)[0]
    # GuiCode carries the same pair swapped: u16 +0x08 = version, byte +0x0A = build id.
    assert struct.unpack_from("<H", mcf, gc.offset + 8)[0] == parent_version
    assert mcf[gc.offset + 0x0A] == parent_build

    gd_hdr = subcontainer.parse_header(mcf, gd.offset + 0x10000)
    assert gd_hdr.magic == subcontainer.MAGIC
    assert (gd_hdr.version, gd_hdr.build_id) == (parent_version, parent_build)


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_the_loader_subcontainer_header_is_the_shared_wrapper(corpus, version):
    """cli.bin's region-2 sub-container has the same 16-byte header, at 0xC0400000.

    Its stamp is the *loader's* build, not the mcf's: 13.6 and 14.7 ship a byte-identical
    cli.bin (§4.2), so 14.7's loader sub-container carries 13.6's 0x1234, unlike 14.7's
    GuiData sub-container (0x0807). Both instances still share the magic, the vendor build id
    0x66, and the zero reserved words -- one wrapper, two payloads.
    """
    from fireflymcf import subcontainer

    cli = corpus[version]["cli"]
    blob, base = subcontainer.loader_region2(cli)
    hdr = subcontainer.parse_header(blob)

    assert hdr.magic == subcontainer.MAGIC
    assert hdr.build_id == 0x66                          # vendor stamp, all builds
    assert base == 0xC0400000                            # real load address, armimg +0x24 (§4.2)
    assert struct.unpack_from("<II", blob, 8) == (0, 0)  # +0x08/+0x0C reserved


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_loader_param_names_are_the_firmware_table(corpus, version):
    """The loader's ParamID -> name table, read straight from the sub-container.

    These are the authoritative names for the descriptor tags (§2) and the RV-C 0FF02
    parameters. The table is 0x83 slots wide and build-stable, and it is a consistent
    superset of `upload.PARAM_NAMES` (which was hand-transcribed from a loader log capture),
    so the two now agree by construction rather than by coincidence.
    """
    from fireflymcf import subcontainer, upload

    cli = corpus[version]["cli"]
    names = subcontainer.loader_param_names(cli)

    assert subcontainer.loader_param_name_count(cli) == 0x83
    # A spread of tags across the three id bands (device identity / commands / MCU identity).
    for tag, name in {0x00: "FuncCode", 0x0B: "ExtFuncCode", 0x0C: "HwVer/LcdDescType",
                      0x40: "LdAddr", 0x43: "ConfigCmd2", 0x82: "McuRamSize"}.items():
        assert names[tag] == name

    assert set(upload.PARAM_NAMES) <= set(names)
    assert all(names[tag] == nm for tag, nm in upload.PARAM_NAMES.items())


def test_loader_region2_rejects_a_non_loader_image(corpus):
    """The magic guard is real: an mcf.bin (whose region layout is not a cli.bin) does not
    parse as a loader region-2 sub-container, so a caller cannot mistake one for the other."""
    from fireflymcf import subcontainer

    with pytest.raises((ValueError, IndexError, Exception)):
        subcontainer.loader_region2(corpus["13.6"]["mcf"])
    with pytest.raises(ValueError):
        subcontainer.parse_header(b"\x00" * 0x20)


# ---------------------------------------------------------------------------------------
# The loader's GUI record grammar (MCF-FORMAT §10 item 12): it is the GuiData record format.
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_loader_gui_is_the_guidata_record_format(corpus, version):
    """The loader's screen records decode with the GuiData decoder, yielding its menu text.

    That `records.object_records` walks these records at all -- landing on clean boundaries
    and producing the loader's own on-screen strings -- is the proof that cli.bin's GUI is the
    same widget-record format as the panel's, not a separate grammar. The strings below are the
    loader's, present on every build.
    """
    from fireflymcf import subcontainer

    cli = corpus[version]["cli"]
    table = subcontainer.loader_gui_record_table(cli)
    screens = subcontainer.loader_gui_screens(cli)
    text = set(subcontainer.loader_gui_text(cli))

    assert len(table) >= 130                       # 137 (11.23) / 138 (13.6, 14.7)
    assert len(screens) == len(table)
    # The overwhelming majority decode cleanly with the GuiData decoder.
    assert sum(s is not None for s in screens) >= len(table) - 3
    for want in ("AUTO LOAD CONFIGURATION", "CANCEL", "EXIT"):
        assert want in text


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_the_loader_r35_gate_decodes_every_reference_build_screen(corpus, version):
    """The loader's R35 gate, found by disassembling FUN_20009cd8, decodes all 13.6/14.7 screens.

    The one difference between the loader's decoder and the panel's is R35: the loader reads its
    cursor byte whenever header byte-5 bit 0x80 is set, where the panel (FUN_00252CB0, transcribed
    by `records.py`) also gates it on `(uV16 & 0x2901) or (aee & 0x40)` (§4.2). Slot 46 is a
    wtype-8 record with that bit set -- the presence path the panel never exercises -- so it
    derails under the panel gate and decodes to a clean boundary under the loader gate. That flip,
    on the same bytes, is the proof the difference is real (a decoder difference, not a `records.py`
    bug) and that the loader gate is right.
    """
    from fireflymcf import records, subcontainer

    cli = corpus[version]["cli"]
    # With the loader gate (what loader_gui_screens uses), every screen decodes.
    assert all(s is not None for s in subcontainer.loader_gui_screens(cli))

    # Slot 46 holds the demonstrator record: a wtype-8 with header byte-5 bit 0x80 set.
    blob, _base = subcontainer.loader_region2(cli)
    table = subcontainer.loader_gui_record_table(cli)
    start = table[46]
    extent = min(o for o in table if o > start)
    # rec1 is the wtype-8 (rec0 is a 12-byte wtype-9 spacer): decode it alone under each gate.
    rec1_off = start + 1 + 12
    assert (blob[rec1_off] & 0x3F) == 8 and (blob[rec1_off + 5] & 0x80)
    panel_rec = records.decode_record(blob, rec1_off, loader_r35=False)
    loader_rec = records.decode_record(blob, rec1_off, loader_r35=True)
    assert loader_rec.end == panel_rec.end + 1     # the loader reads one more byte -- R35
    # Under the loader gate the whole object fits its extent exactly (padding 0-3); the panel
    # gate mis-sizes rec1 and the sequential walk runs off the object entirely.
    loader_recs = records.object_records(blob, start, loader_r35=True)
    assert 0 <= extent - loader_recs[-1].end < 4


def test_the_11_23_configuration_screens_are_the_remaining_open_divergence(corpus):
    """On the older 11.23 toolkit two screens still derail after the R35 fix -- the AUTO LOAD /
    MANUAL LOAD CONFIGURATION pages, which use a further field-presence path this decoder does
    not model. Pinned as the last open point so a later fix (or a regression) is visible."""
    from fireflymcf import subcontainer

    screens = subcontainer.loader_gui_screens(corpus["11.23"]["cli"])
    assert [i for i, s in enumerate(screens) if s is None] == [125, 130]


def test_loader_gui_record_table_is_the_second_pointer_run(corpus):
    """The record table is located structurally -- the long pointer run that is not the ParamID
    name table at +0x10 -- so it tracks 11.23's different offset (+0xec4) without a constant."""
    from fireflymcf import subcontainer

    for version, expect in (("11.23", 137), ("13.6", 138), ("14.7", 138)):
        cli = corpus[version]["cli"]
        assert len(subcontainer.loader_gui_record_table(cli)) == expect
        # Distinct from the 0x83-slot name table.
        assert len(subcontainer.loader_gui_record_table(cli)) != \
            subcontainer.loader_param_name_count(cli)
