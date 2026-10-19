"""The GuiData section header, the table directory, and the GuiCode vector table.

Unlike `test_guidata.py`, everything asserted here is **`proven-from-code`**: it is the
decode performed by `FUN_0021BE50` in the LCD Firmware, read off the instructions and
cited in `docs/LCD-FIRMWARE.md` Findings 8-11. That function is the one place found so
far that dereferences the GuiData base -- possible because `0x90000000` is QUADSPI
memory-mapped mode on this STM32F7, so GuiData is external flash read by ordinary `ldr`s.

The decode, with the instruction that proves each part:

    GuiData+0x00  u8   build id       0021bf1a  ldrb   r1,[r1,#0x0]
    GuiData+0x04  u16  version stamp  0021bf10  ldrh   r0,[r1,#0x4]
    GuiData+0x07  u8   entry count    0021bf2c  ldrb.w r12,[r9,#0x7]
                                      0021bf3a  lsl.w  r12,r12,#0x3   (x8 = stride)
    GuiData+0x08  N x 8-byte descriptors <u8 tag><u8 b1><u8 b2><u8 b3><u16 off><u16 size>
        tag       0021bf80  ldrb.w r11,[r10,#0x8] / 0021bf84  cmp.w r11,#0x41
        count     0021bf9a/0021bf96  b3 | ((b1 & 3) << 8)
        offset    0021bf8e  ldrh.w r6,[r10,#0xc] / 0021bf94  add r6,r9  (GuiData-relative)

`FUN_0021BE50` rejects the blobs unless GuiData, GuiCode and the GuiData sub-container
agree on the build id and version stamp, and unless the variable table's entry count
matches the u16 at `GuiCode+0x20`. Those are the cross-checks pinned below. They are
worth a regression test because they are the project's only code-proven statements about
GuiData's contents, and because two of them corrected `MCF-FORMAT.md` in place: the
descriptor tag is a `u8` (§5.1 had `<u16 tag>`), and the variable table's DGN is 18 bits
(§5.4 had `<u16 dgn_low>`).

These assertions must hold for all three builds. 14.7 is the load-bearing case: its
version stamp is `0x0807` where 11.23 and 13.6 read `0x1234`, so the equalities cannot
be a constant fitted to one file.
"""
import struct

import pytest

from fireflymcf import container

# FUN_0021BE50 loads these from the blob descriptor table at 0x0021C010.
GUICODE_MAGIC = 0x1A2B3C4D          # slot 0x0021C014
SUBCONTAINER_MAGIC = 0x8F7E6D5C     # slot 0x0021C020
SUBCONTAINER_OFFSET = 0x10000       # where the sub-container sits inside GuiData

VARIABLE_TABLE_TAG = 0x41           # 'A', compared at 0021bf84
DGN_MASK = 0x3FFFF                  # 18 bits, from `& 0x3ffff` in the init loop
MAX_VARIABLES = 1000                # cmp.w r6,#0x3e8 at 0021bfe6

VERSIONS = ("11.23", "13.6", "14.7")


def _blob(mcf: bytes, kind: str) -> bytes:
    """The bytes of the GuiCode or GuiData section, straight out of the container."""
    c = container.parse_container(mcf)
    matches = [s for s in c.sections if s.name and kind in s.name and s.length]
    assert len(matches) == 1, f"expected exactly one {kind} section, got {matches!r}"
    s = matches[0]
    return mcf[s.offset:s.offset + s.length]


@pytest.fixture(params=VERSIONS)
def blobs(request, corpus):
    """(version, guidata, guicode, subcontainer) for each build in the corpus."""
    mcf = corpus[request.param]["mcf"]
    guidata = _blob(mcf, "GuiData")
    guicode = _blob(mcf, "GuiCode")
    return request.param, guidata, guicode, guidata[SUBCONTAINER_OFFSET:]


def _is_guidata_pointer(guidata: bytes, word: int) -> bool:
    """§5.5's rule: a `0x900XXXXX` word whose target lands inside the section."""
    return 0x90000000 <= word and (word - 0x90000000) < len(guidata)


def _pointer_run_length(guidata: bytes, start: int) -> int:
    """How many consecutive GuiData pointers begin at `start`."""
    n = 0
    while start + (n + 1) * 4 <= len(guidata):
        word = struct.unpack_from("<I", guidata, start + n * 4)[0]
        if not _is_guidata_pointer(guidata, word):
            break
        n += 1
    return n


def _object_table_bases(guidata: bytes) -> tuple[int, int]:
    """(font table, glyph table) offsets, derived rather than hardcoded.

    §5.5 finds the object tables as maximal runs of GuiData pointers. The glyph table is
    the largest such run, and the font table is the run immediately preceding it in
    address order -- the 20-byte font descriptors live in the gap between them, which is
    why the two are adjacent. Deriving these keeps the test honest across builds, where
    the tables sit at different offsets and differ in size.
    """
    runs, i, cur = [], 0, None
    while i + 4 <= len(guidata):
        word = struct.unpack_from("<I", guidata, i)[0]
        if _is_guidata_pointer(guidata, word):
            cur = [i, i + 4] if cur is None else [cur[0], i + 4]
        else:
            if cur is not None and (cur[1] - cur[0]) >= 32:
                runs.append(tuple(cur))
            cur = None
        i += 4
    if cur is not None and (cur[1] - cur[0]) >= 32:
        runs.append(tuple(cur))

    glyph = max(runs, key=lambda r: r[1] - r[0])
    before = [r for r in runs if r[1] <= glyph[0]]
    assert before, "no pointer run precedes the glyph table"
    font = max(before, key=lambda r: r[0])
    return font[0], glyph[0]


def _descriptors(guidata: bytes) -> list[dict]:
    """Walk GuiData's table directory exactly as 0021bf7c-0021bfc2 does."""
    count = guidata[0x07]
    out = []
    for k in range(count):
        off = 0x08 + 8 * k
        tag, b1, b2, b3 = guidata[off:off + 4]
        out.append({
            "tag": tag,
            "b1": b1,
            "b2": b2,
            "b3": b3,
            # count = b3 | ((b1 & 3) << 8); the mask is `and r3,r0,#0x300`, so exactly
            # two bits of b1 reach the count -- not the whole byte.
            #
            # KNOWN BLIND SPOT: b1 is 0x01 in all three builds, so `b1 & 3` and `b1`
            # give identical results on this corpus and no test here can tell them
            # apart. Verified by mutation: dropping the mask leaves all tests passing.
            # The `& 3` is proven by the instruction alone, not by the data -- so if a
            # build ever appears with b1 > 3, this is where it matters.
            "count": b3 | ((b1 & 3) << 8),
            "offset": struct.unpack_from("<H", guidata, off + 4)[0],
            "size": struct.unpack_from("<H", guidata, off + 6)[0],
        })
    return out


def test_magics_are_where_the_firmware_looks_for_them(blobs):
    """0021beca and 0021bf00: the two magics FUN_0021BE50 checks before anything else."""
    _, guidata, guicode, sub = blobs
    assert struct.unpack_from("<I", guicode, 0)[0] == GUICODE_MAGIC
    assert struct.unpack_from("<I", sub, 0)[0] == SUBCONTAINER_MAGIC
    # 0021bed0 / 0021bf06: both blobs carry a format byte that must read 1.
    assert guicode[0x04] == 1
    assert sub[0x06] == 1


def test_build_id_agrees_across_all_three_blobs(blobs):
    """GuiData+0x00 == GuiCode+0x0A == subcontainer+0x07.

    Instructions 0021bf1a / 0021bf18 / 0021bf1c, and 0021bf26 / 0021bf28. A mismatch
    branches to 0021bed6, which returns 0 and rejects the upload.
    """
    _, guidata, guicode, sub = blobs
    build_id = guidata[0x00]
    assert guicode[0x0A] == build_id
    assert sub[0x07] == build_id


def test_version_stamp_agrees_across_all_three_blobs(blobs):
    """GuiData+0x04 == GuiCode+0x08 == subcontainer+0x04, as u16.

    Instructions 0021bf10 / 0021bf0e / 0021bf14, and 0021bf20 / 0021bf22.
    """
    version, guidata, guicode, sub = blobs
    stamp = struct.unpack_from("<H", guidata, 4)[0]
    assert struct.unpack_from("<H", guicode, 8)[0] == stamp
    assert struct.unpack_from("<H", sub, 4)[0] == stamp
    # Pinned so a corpus change cannot quietly turn this into a tautology: the stamp is
    # NOT constant across the corpus, which is what makes the equalities above evidence.
    assert stamp == {"11.23": 0x1234, "13.6": 0x1234, "14.7": 0x0807}[version]


def test_directory_declares_five_tables_that_chain_contiguously(blobs):
    """GuiData+0x07 is the entry count and the stride is 8 (0021bf2c, 0021bf3a).

    Contiguity is not something the code checks -- it is a property of the corpus that
    corroborates the offset/size field placement, since a wrong split would not chain.
    """
    version, guidata, _, _ = blobs
    descs = _descriptors(guidata)
    assert len(descs) == 5
    assert [d["tag"] for d in descs] == [0x10, 0x20, 0x30, 0x41, 0x42]
    for prev, nxt in zip(descs, descs[1:]):
        assert prev["offset"] + prev["size"] == nxt["offset"], (
            f"{version}: {prev['tag']:#04x} does not chain into {nxt['tag']:#04x}")
    # Section offsets are u16, so nothing a descriptor declares can reach the
    # sub-container at 0x10000.
    for d in descs:
        assert d["offset"] + d["size"] <= SUBCONTAINER_OFFSET


def test_variable_table_count_is_confirmed_three_independent_ways(blobs):
    """The `'A'` descriptor's count, `size / 8`, and the u16 at GuiCode+0x20 all agree.

    This is the assertion that proves the count decode is not fitted: the three sources
    are a bitfield in GuiData's directory, a division of that table's byte length, and a
    field in a different blob entirely. 0021bfe0 is where the firmware compares the last
    two and rejects on mismatch.
    """
    version, guidata, guicode, _ = blobs
    var = [d for d in _descriptors(guidata) if d["tag"] == VARIABLE_TABLE_TAG]
    assert len(var) == 1, f"{version}: expected exactly one 'A' descriptor"
    var = var[0]

    assert var["size"] == var["count"] * 8
    assert struct.unpack_from("<H", guicode, 0x20)[0] == var["count"]
    # cmp.w r6,#0x3e8 at 0021bfe6 rejects anything above this.
    assert var["count"] <= MAX_VARIABLES
    assert var["count"] == {"11.23": 291, "13.6": 298, "14.7": 310}[version]


def test_the_tag_is_a_single_byte(blobs):
    """`ldrb.w r11,[r10,#0x8]` / `cmp.w r11,#0x41` is a BYTE compare against 'A'.

    MCF-FORMAT.md §5.1 previously read the descriptor as `<u16 tag><u16 aux>` and gave
    the variable table's tag as 0x0141. Under that reading the 'A' descriptor's u16 tag
    would be 0x0141 rather than 0x0041, and `aux` would be 0x2A00 = 10752 rather than the
    true count of 298. Both are pinned here so the corrected reading cannot regress.
    """
    _, guidata, _, _ = blobs
    var = next(d for d in _descriptors(guidata) if d["tag"] == VARIABLE_TABLE_TAG)
    assert var["b1"] == 0x01                       # the byte §5.1 folded into the tag
    u16_tag = struct.unpack_from(
        "<H", guidata, 0x08 + 8 * 3)[0]
    assert u16_tag == 0x0141                       # what the old reading saw
    assert u16_tag != var["tag"]                   # and why it was wrong
    assert var["count"] != 0x2A00                  # the old `aux` reading


def test_variable_table_dgns_are_18_bit_and_well_formed(blobs):
    """The init loop masks each record's first u32 with 0x3FFFF, so the DGN is 18 bits.

    A wrong mask width is self-evident here: 16 bits would drop 0x15FCE to 0x5FCE, and
    every RV-C DGN in this table would lose its high bit. The check is that the masked
    values land in RV-C's range rather than that they equal a fitted list.
    """
    _, guidata, _, _ = blobs
    var = next(d for d in _descriptors(guidata) if d["tag"] == VARIABLE_TABLE_TAG)
    dgns = [
        struct.unpack_from("<I", guidata, var["offset"] + k * 8)[0] & DGN_MASK
        for k in range(var["count"])
    ]
    assert len(dgns) == var["count"]
    # Every DGN fits 18 bits by construction; the real assertion is that they are
    # plausible RV-C identifiers, i.e. none is zero and the common ones are 0x1Fxxx.
    assert all(0 < d <= DGN_MASK for d in dgns)
    assert 0x1FEDA in dgns          # DC_DIMMER_STATUS_3
    assert 0x15FCE in dgns          # G12 configuration
    # A 16-bit read would truncate both of the above, so pin that it actually differs.
    truncated = [
        struct.unpack_from("<H", guidata, var["offset"] + k * 8)[0]
        for k in range(var["count"])
    ]
    assert 0x1FEDA not in truncated and 0x15FCE not in truncated


def test_guicode_vector_table_holds_five_thumb_pointers(blobs):
    """GuiCode is native ARM Thumb-2, not bytecode (Finding 10).

    FUN_0021BE50 calls through `*(GuiCode+0x0C)` and `*(GuiCode+0x18)`. Every slot must
    therefore be an odd (Thumb) address inside GuiCode's own load region, and -- the part
    that makes "native code" more than a guess -- each target must begin with a `push`
    prologue. `push {...,lr}` is 0xB4xx/0xB5xx; `push.w` is 0xE92D.
    """
    version, _, guicode, _ = blobs
    load_base = 0x00240000
    for i in range(5):
        ptr = struct.unpack_from("<I", guicode, 0x0C + i * 4)[0]
        assert ptr & 1, f"{version}: vector {i} = {ptr:#010x} is not a Thumb pointer"
        offset = (ptr & ~1) - load_base
        assert 0x30 <= offset < len(guicode), (
            f"{version}: vector {i} target {ptr:#010x} is outside the blob")
        first = struct.unpack_from("<H", guicode, offset)[0]
        is_push = (first & 0xFE00) == 0xB400 or first == 0xE92D
        assert is_push, (
            f"{version}: vector {i} target {ptr:#010x} starts {first:#06x}, not a push")


def test_font_descriptors_partition_the_glyph_table_exactly(blobs):
    """The font/glyph mechanism from `docs/GUICODE.md` Finding 3.

    `FUN_002520F4` in GuiCode selects a font from a 30-entry pointer table (bound
    `cmp #0x1e`), and `FUN_00251AA0` looks a character up as

        glyph = GLYPH_TABLE[font.base + c - font.first]

    falling back to `GLYPH_TABLE[font.fallback]` outside `[first, last]`. That gives a
    descriptor of `<u8 first><u8 last><u16 base><u16 fallback>`.

    What makes this more than a plausible read, and what this test pins: the descriptors'
    `base` indices partition the glyph table **contiguously**, `fallback` is always
    exactly `base + span` (the slot just past the font's own range), and the last font's
    fallback is the final index of the table. A wrong field offset or width would break
    the chain immediately.

    The table locations come from `MCF-FORMAT.md` §5.5 and are re-derived here rather
    than hardcoded, so this stays honest across the three builds, whose tables differ in
    both position and size.
    """
    version, guidata, _, _ = blobs
    font_table, glyph_table = _object_table_bases(guidata)

    descriptors = []
    for i in range(_pointer_run_length(guidata, font_table)):
        ptr = struct.unpack_from("<I", guidata, font_table + i * 4)[0]
        off = ptr - 0x90000000
        assert 0 <= off < len(guidata), (
            f"{version}: font slot {i} = {ptr:#010x} is outside GuiData")
        first, last = guidata[off], guidata[off + 1]
        base = struct.unpack_from("<H", guidata, off + 2)[0]
        fallback = struct.unpack_from("<H", guidata, off + 4)[0]
        assert first <= last, f"{version}: font {i} has first > last"
        # The invariant that makes the layout self-proving.
        assert fallback == base + (last - first + 1), (
            f"{version}: font {i} fallback {fallback} != base {base} + span "
            f"{last - first + 1}")
        descriptors.append((ptr, base, last - first + 1, fallback))

    # Contiguous partition: each distinct descriptor picks up where the previous ended.
    # Slots may alias (13.6 slots 0 and 1 share a descriptor), so dedupe by pointer
    # while preserving order.
    seen, chain = set(), []
    for ptr, base, span, fallback in descriptors:
        if ptr not in seen:
            seen.add(ptr)
            chain.append((base, span, fallback))
    assert chain[0][0] == 0, f"{version}: first font does not start at glyph 0"
    for (base, span, fallback), (next_base, _, _) in zip(chain, chain[1:]):
        assert next_base == fallback + 1, (
            f"{version}: glyph range gap — {base}+{span} -> fallback {fallback}, "
            f"next base {next_base}")

    # The final fallback must be the last index of the glyph table: the table is fully
    # consumed, which is what rules out an off-by-one in the descriptor layout.
    n_glyphs = _pointer_run_length(guidata, glyph_table)
    assert chain[-1][2] == n_glyphs - 1, (
        f"{version}: last fallback {chain[-1][2]} != final glyph index {n_glyphs - 1}")


def test_glyph_and_font_table_sizes_match_the_spec(blobs):
    """§5.5's four object tables, and the two GuiCode names by literal.

    Sizes are `MCF-FORMAT.md` §5.5's, and the two bounds GuiCode enforces (`cmp #0x1e`
    for fonts, and the glyph table's full extent) have to agree with them.
    """
    version, guidata, _, _ = blobs
    font_table, glyph_table = _object_table_bases(guidata)
    expected = {"11.23": (30, 2687), "13.6": (30, 3295), "14.7": (29, 3171)}[version]
    assert _pointer_run_length(guidata, font_table) == expected[0]
    assert _pointer_run_length(guidata, glyph_table) == expected[1]


def test_guicode_code_starts_at_0x30(blobs):
    """GuiCode+0x30 is where the code begins, and it decodes as a Thumb-2 opening.

    Not necessarily a `push`: 13.6 and 14.7 read `B5 10` = `push {r4,lr}`, but 11.23
    reads `4B 04` = `ldr r3,[pc,#0x10]`, a leaf function that never saves `lr`. An
    earlier draft of this test asserted `push` for all three and failed on 11.23 --
    the generalisation was wrong, and `LCD-FIRMWARE.md` Finding 10 was corrected to
    match. What all three share is only that +0x30 is a valid instruction boundary.
    """
    version, _, guicode, _ = blobs
    first = struct.unpack_from("<H", guicode, 0x30)[0]
    assert first == {"11.23": 0x4B04, "13.6": 0xB510, "14.7": 0xB510}[version]
    is_push = (first & 0xFE00) == 0xB400 or first == 0xE92D
    is_pc_literal_load = 0x4800 <= first <= 0x4FFF        # ldr rN,[pc,#imm]
    assert is_push or is_pc_literal_load


def test_entry4_and_its_status_parser_gate_on_their_dgns(corpus):
    """GUICODE Finding 75: pin the two dispatch DGNs of the GuiCode RV-C receive path (13.6).

    GuiCode_entry4 dispatches its main arm on DGN 0x1FEDA (the G12 per-channel indicator status,
    `DAT_00247a4c`); its largest status parser FUN_00240D20 gates on DGN 0x1FDFF via
    `(canid & 0x03ffff00) == 0x01fdff00` (`DAT_00241040`, with the mask at `DAT_0024103c`), then
    writes into the SRAM state block at 0x20030068. These are 13.6 GuiCode addresses; reading the
    literals straight from the section keeps the finding anchored to code, not to prose.
    """
    import struct

    gc = _blob(corpus["13.6"]["mcf"], "GuiCode")
    base = 0x240000

    def u32(vaddr):
        return struct.unpack_from("<I", gc, vaddr - base)[0]

    assert u32(0x247A4C) == 0x0001FEDA          # entry4 main dispatch DGN (G12 channel status)
    assert u32(0x24103C) == 0x03FFFF00          # FUN_00240D20 DGN mask
    assert u32(0x241040) == 0x01FDFF00          # FUN_00240D20 DGN gate -> PGN 0x1FDFF
    assert u32(0x241090) == 0x20030068          # its per-instance status state block


def test_lcd_can_task_is_a_traced_scheduler_step(corpus):
    """LCD-FIRMWARE Finding 15: FUN_0021317C is the CAN mailbox task, run as a traced step.

    Two anchors, read straight from the 13.6 LCD Firmware section: its interrupt-flag accessor
    FUN_002162E4 computes `*(base+0x18) & *(base+0x14)` (pending & enabled -- the ISR idiom), and
    the only caller is a scheduler stub at 0x21E388 that trace-wraps the call (`movs r0,#0xd0` /
    `bl` trace / `ldr r0,[pc]` / `bl FUN_0021317C`). Reading the code keeps the finding anchored.
    """
    import capstone

    lcd = _blob(corpus["13.6"]["mcf"], "LCD Firmware")
    base = 0x00210000
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    def ins_at(vaddr, n):
        off = vaddr - base
        return [(i.mnemonic, i.op_str) for i in md.disasm(lcd[off:off + n], vaddr)]

    # FUN_002162E4: ldr r0,[r0,#0x18] ; ldr r1,[r0-... ] & -> "and" ; the pending&enabled read.
    acc = ins_at(0x002162E4, 12)
    text = " ".join(f"{m} {o}" for m, o in acc)
    assert "#0x18]" in text and "#0x14]" in text and any(m.startswith("and") for m, _ in acc)

    # The traced scheduler stub that calls FUN_0021317C (trace id 0xd0, then the call).
    stub = ins_at(0x0021E388, 16)
    joined = " | ".join(f"{m} {o}" for m, o in stub)
    assert "movs r0, #0xd0" in joined
    assert any(m == "bl" and o == "#0x21317c" for m, o in stub)


def test_lcd_serial_receiver_syncs_and_is_a_scheduler_step(corpus):
    """LCD-FIRMWARE Finding 16: FUN_0021CE28 is a framed serial-ring receiver, run as step 5.

    Anchors read from the 13.6 LCD section: the RX-ring available-count accessor FUN_00216AE8
    masks the head-tail difference to a 128-byte ring (`& 0x7f`), and the scheduler stub at
    0x21B27E calls FUN_0021CE28 between trace markers 4 and 5 (`movs r0,#4` ... `bl FUN_0021CE28`
    ... `movs r0,#5`). The 0xF0 0x0F frame sync appears in the function body.
    """
    import capstone

    lcd = _blob(corpus["13.6"]["mcf"], "LCD Firmware")
    base = 0x00210000
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    def text_at(vaddr, n):
        off = vaddr - base
        return " | ".join(f"{i.mnemonic} {i.op_str}" for i in md.disasm(lcd[off:off + n], vaddr))

    # RX ring available-count: (head - tail) & 0x7f  -> a 128-byte circular buffer.
    assert "#0x7f" in text_at(0x00216AE8, 20)

    # The scheduler stub: trace 4, call the receiver, trace 5.
    stub = text_at(0x0021B274, 20)
    assert "movs r0, #4" in stub and "bl #0x21ce28" in stub and "movs r0, #5" in stub

    # The frame sync: the body compares a read byte against 0xf0 then 0xf.
    body = text_at(0x0021CE28, 0x120)
    assert "#0xf0" in body and "#0xf" in body


def test_guicode_links_a_soft_double_library(corpus):
    """GUICODE Finding 76: FUN_002403E8 is a soft-double op (libgcc/AEABI), not app code.

    The double-precision add/sub prologue is unmistakable: assemble the exponent mask 0x7ff as
    `#0xff | #0x700`, extract each operand's 11-bit exponent with `lsr #20`, and branch to the
    NaN/Inf/zero special-case handler (0x2405c4). That the largest remaining dark GuiCode
    functions are toolchain soft-float is why the section's dark residue is not application code.
    """
    import capstone

    gc = _blob(corpus["13.6"]["mcf"], "GuiCode")
    base = 0x240000
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    off = 0x002403E8 - base
    text = " | ".join(f"{i.mnemonic} {i.op_str}"
                       for i in md.disasm(gc[off:off + 40], 0x002403E8))
    assert "mov.w ip, #0xff" in text and "orr ip, ip, #0x700" in text   # build 0x7ff
    assert "lsr #20" in text                                            # extract double exponent
    assert "#0x2405c4" in text                                          # special-case handler


def test_lcd_clock_config_targets_rcc_and_pwr(corpus):
    """LCD-FIRMWARE Finding 17: FUN_002145FC is the RCC/PWR clock bring-up (HAL/startup).

    Its two data pointers are the peripheral bases, read straight from the 13.6 LCD section:
    DAT_00214900 = 0x40023800 (STM32F7 RCC) and DAT_00214904 = 0x40007000 (PWR). Those addresses
    make the identification proof, not inference -- the last >1 KB dark LCD function is clock-tree
    configuration, confirming the LCD's dark bulk is HAL/driver code, not Firefly application logic.
    """
    import struct

    lcd = _blob(corpus["13.6"]["mcf"], "LCD Firmware")
    base = 0x00210000
    rcc = struct.unpack_from("<I", lcd, 0x00214900 - base)[0]
    pwr = struct.unpack_from("<I", lcd, 0x00214904 - base)[0]
    assert rcc == 0x40023800   # STM32F7 RCC
    assert pwr == 0x40007000   # STM32F7 PWR
