"""`indirectwrites`: the pointer-store forms no literal scan sees (`GUICODE.md` Finding 67).

Two kinds of test. The synthetic ones prove each scan can fire and can stop (a redefinition
ends a pointer's lifetime, a copy extends a parameter's), so an empty result on the corpus is
a finding and not a scanner that never matched. The corpus ones pin the 13.6 enumeration
EXACTLY -- every hit is hand-classified in Finding 67, so a new hit on a rebuilt image must be
read, not absorbed.
"""
from __future__ import annotations

import pytest

from fireflymcf import indirectwrites as iw
from fireflymcf import raminit, slotsource, sramreach, thumb

STOCK = "firefly-13.6/mcf.bin"
BUILDS = ["firefly-11.23/mcf.bin", STOCK, "firefly-14.7 (Terrain)/mcf.bin"]

SLOT_174 = 0x2003A62C          # SLIDES/AWNINGS `Auto Level` <-> `Cancel` (type 0, byte)
SLOT_175 = 0x20039DDD          # SLIDES/AWNINGS `All Retract` <-> `Cancel`
SLOT_23 = 0x20039999           # INVERTER / CHARGER SETTINGS `Absorb`/`Float` gate (Finding 63)
CELLS = (SLOT_174, SLOT_175, SLOT_23)

BX_LR = bytes([0x70, 0x47])
NOP = bytes([0x00, 0xBF])
PUSH_R4_LR = bytes([0x10, 0xB5])
POP_R4_PC = bytes([0x10, 0xBD])
STRB_R2_R1 = bytes([0x0A, 0x70])                 # strb r2, [r1]
LDRW_R0_R1_R2_LSL2 = bytes([0x51, 0xF8, 0x22, 0x00])   # ldr.w r0, [r1, r2, lsl #2]


@pytest.fixture(scope="module")
def data() -> bytes:
    with open(STOCK, "rb") as handle:
        return handle.read()


# --- the scans can fire, and can stop --------------------------------------------------------

def test_deref_store_fires_and_a_redefinition_ends_the_lifetime() -> None:
    body = thumb.ldr_imm(1, 0, 4) + STRB_R2_R1 + BX_LR
    hits = iw._deref_stores(body, 0x240000, 20)
    assert [(src, site.store) for src, site in hits] == [("[r0, #4]", "strb r2, [r1]")]

    redefined = thumb.ldr_imm(1, 0, 4) + thumb.movs_imm(1, 0) + STRB_R2_R1 + BX_LR
    assert iw._deref_stores(redefined, 0x240000, 20) == ()


def test_param_store_fires_through_a_callee_saved_copy_across_a_call() -> None:
    # 0x240000: bl F ; bx lr ; nop
    # F 0x240008: push {r4,lr} ; mov r4,r0 ; bl G ; str r1,[r4] ; pop {r4,pc}
    # G 0x240014: bx lr
    body = (thumb.bl(0x240000, 0x240008) + BX_LR + NOP
            + PUSH_R4_LR + thumb.mov_reg(4, 0) + thumb.bl(0x24000C, 0x240014)
            + thumb.str_imm(1, 4, 0) + POP_R4_PC
            + BX_LR + NOP)
    found = dict(iw._param_store_functions(body, 0x240000))
    assert set(found) == {0x240008}
    assert [s.store for s in found[0x240008]] == ["str r1, [r4]"]

    # the same function without the copy: the call clobbers r0, so the later store is not
    # through a parameter and must NOT be flagged
    body2 = (thumb.bl(0x240000, 0x240008) + BX_LR + NOP
             + PUSH_R4_LR + thumb.bl(0x24000A, 0x240014) + thumb.str_imm(1, 0, 0) + POP_R4_PC
             + NOP + BX_LR + NOP)
    assert iw._param_store_functions(body2, 0x240000) == ()


def test_table_fed_call_fires_on_a_word_indexed_argument() -> None:
    body = LDRW_R0_R1_R2_LSL2 + thumb.bl(0x240004, 0x240008) + BX_LR + NOP
    hits = iw._table_fed_calls(body, 0x240000, 14)
    assert [(h.load, h.store) for h in hits] == [("ldr.w r0, [r1, r2, lsl #2]", "bl #0x240008")]


def test_base_call_covers_is_a_range_test() -> None:
    c = iw.BaseCall(0, "bl", 0x20039000, 0x1000, None)
    assert c.covers(0x20039FFF) and not c.covers(0x2003A000)
    assert not iw.BaseCall(0, "bl", 0x20039000, None, None).covers(0x20039001)


# --- the 13.6 enumeration, pinned exactly ------------------------------------------------------

def test_guicode_pointer_deref_stores_are_the_fifteen_classified_sources(data: bytes) -> None:
    d = iw.deref_stores(data, iw.GUICODE)
    assert sum(len(v) for v in d.values()) == 36
    assert set(d) == {
        "[r2, #4]", "[sp, #4]", "[r0, #0x64]", "[r2, #0x74]", "[sp, #0x220]", "[sp, #0x1c0]",
        "[sp, #0x338]", "[r0, #8]", "[r0, #0xc]", "[r2, r3]", "[sp, #8]", "[r0, #0x44]",
        "[r6, #0x54]", "[r4, #0x64]", "[r1, #0x14]",
    }
    # the one register-indexed pointer store is the framebuffer pixel write
    (fb,) = d["[r2, r3]"]
    assert (fb.load_at, fb.store_at, fb.store) == (0x250FB6, 0x250FD0, "strb r4, [r1, r3]")


def test_lcd_pointer_deref_stores_are_hal_handles_and_the_printf_stream(data: bytes) -> None:
    d = iw.deref_stores(data, iw.LCD)
    assert sum(len(v) for v in d.values()) == 188
    assert len(d) == 26
    # the two big sources are `handle->Instance` peripheral-register writes through a struct's
    # first word (HAL shape); the +0x24 family is the `*p++ = c` byte emitter of the printf path
    assert sorted(d, key=lambda k: -len(d[k]))[:2] == ["[r4]", "[r0]"]
    assert len(d["[r4]"]) + len(d["[r0]"]) > 130
    assert len(d["[r4, #0x24]"]) == 5


def test_guicode_functions_storing_through_a_parameter_are_the_ten_read(data: bytes) -> None:
    assert set(iw.param_store_functions(data, iw.GUICODE)) == {
        0x247BF8,   # the per-tick config unpack: strb [r4=cfg,#0x21a] into its own argument
        0x250E04, 0x250E2C,   # range/clamp helpers on stack out-params
        0x251734,   # number formatter writing the digit buffer
        0x2519F0,   # strh to a stack out-param
        0x25211C, 0x252128, 0x252140,   # record-stream cursor readers (advance *cursor)
        0x256014,   # NOT a store: the `ldr.w pc,[r2,r3,lsl #2]` jump table at 0x25607c decoded as code
        0x256868,   # slot compare-and-cache into the caller's local
    }
    sites = iw.param_store_functions(data, iw.GUICODE)[0x256014]
    assert all(0x256080 <= s.store_at < 0x256140 for s in sites)   # all inside the jump table


def test_table_fed_calls_reach_only_read_side_callees(data: bytes) -> None:
    g = iw.table_fed_calls(data, iw.GUICODE)
    assert len(g) == 12
    assert {s.store.split()[1] for s in g} == {"#0x2409ac", "#0x2513a4", "#0x251e38", "#0x257bfc"}
    # strlen, the image blit, the typed reader and the layout engine: none stores through r0
    assert len(iw.table_fed_calls(data, iw.LCD)) == 4


def test_no_bulk_call_covers_the_three_cells_except_the_known_false_positive(data: bytes) -> None:
    calls = iw.base_calls(data, iw.GUICODE)
    assert len(calls) == 99
    covering = {c.at for c in calls for cell in CELLS if c.covers(cell)}
    assert covering == {0x253DE4}
    (fp,) = [c for c in calls if c.at == 0x253DE4]
    assert (fp.r0, fp.r2) == (0x20036D54, 0x6BA4)   # r2 is a movw state offset, not a length
    strcpy = sorted(c.r0 for c in calls if c.callee == "bl #0x259236")
    assert strcpy == [0x20039A60, 0x20039AA0, 0x20039ABC, 0x20039AD8, 0x20039AEC]
    assert max(strcpy) + 12 < SLOT_175          # 12-byte stack sources; furthest reach 0x20039af8
    assert iw.base_calls(data, iw.LCD) == []


def test_no_block_store_has_a_literal_base_in_guicode_ram(data: bytes) -> None:
    for section, count in ((iw.GUICODE, 562), (iw.LCD, 16)):
        sites = iw.stm_stores(data, section)
        assert len(sites) == count
        assert not [s for s in sites if s[2] is not None and 0x20030000 <= s[2] < 0x20040000]


def test_lcd_heap_is_found_and_bounded_far_below_the_working_area(data: bytes) -> None:
    h = iw.heap(data)
    assert h is not None
    assert (h.sbrk, h.brk_cell, h.malloc) == (0x21F0CE, 0x2000A124, 0x21EF2C)
    assert h.start == raminit.ram_init(data).bss_end == 0x2000AD98
    assert h.calls == [(0x218680, 0x21C)]
    assert h.bounded_top == 0x2000AFBC < sramreach.SRAM_LOW


def test_the_three_cells_are_unreached_by_every_form(data: bytes) -> None:
    """The Finding 67 negative in one place: direct forms (slotsource), traced bases and
    indexed tables (sramreach), and nothing above resolves to them."""
    for cell in CELLS:
        forms = sramreach.reaching_forms(data, cell, 1)
        assert forms["raw_word"] == [] and forms["base_offset"] == [] and forms["indexed_table"] == []
        assert not forms["state_struct"]
    for slot in (174, 175, 23):
        assert slotsource.slot_source(data, slot).kind == "unreferenced"


@pytest.mark.parametrize("path", BUILDS)
def test_every_scan_runs_on_every_build(path: str) -> None:
    with open(path, "rb") as handle:
        data = handle.read()
    for section in (iw.GUICODE, iw.LCD):
        assert iw.deref_stores(data, section)
        assert iw.param_store_functions(data, section)
        iw.table_fed_calls(data, section)
        iw.base_calls(data, section)
        assert iw.stm_stores(data, section)
    h = iw.heap(data)
    assert h is not None and h.calls == [(0x218680, 0x21C)]
    assert h.start == raminit.ram_init(data).bss_end
