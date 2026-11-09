"""The groups GuiCode drives by number, located on every firmware (2026-11-08).

`lcdgroups.host_writer` proves, from the LCD firmware's own code, that host-API `+0x04` (the
word `GuiCode_entry0` copies to the setter cell) is the ungated group-variant writer
(13.6 `FUN_0021B678`), and that GuiCode's group number is the group-table index (the GuiData
tag-0x10 directory `b3` is 0). `newbutton.setter_calls` then visits every load of the located
cell and resolves each call's group. The 14.7, 11.23 and 13.6 numbers below are test
expectations taken from the research of 2026-11-08, never locators.
"""
import struct
from pathlib import Path

import pytest

from fireflymcf import container, guidata, hooklocate, lcdgroups, newbutton, theme
from fireflymcf.raminit import LCD_BASE

NEEDS_RESEARCH_DATA = "reads the 11.23, 13.6 and 14.7 corpus images"

# The 49 groups the 13.6-only scan returned before this locator (2026-11-08).
GROUPS_136 = frozenset({
    0, 1, 2, 6, 7, 8, 13, 14, 21, 22, 23, 24, 25, 26, 27, 28, 32, 34, 35, 40, 44, 99, 100,
    128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145,
    146, 170, 184, 199, 216, 217, 231, 232})
# 14.7's 47 driven groups (research 2026-11-08, `groups.md` §3).
GROUPS_147 = frozenset({
    0, 1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 17, 19, 20, 21, 22, 23, 24, 25, 32, 34, 35, 44,
    129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146,
    170, 184, 216, 217, 231, 232})
GROUPS_1123 = frozenset({
    5, 6, 7, 8, 21, 22, 23, 24, 25, 32, 34, 35, 40, 44, 61, 62, 128, 129, 130, 131, 132, 133,
    134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 184, 199, 216, 217})
# 14.7: a deref that reaches its `blx` only through an unconditional `b` (group 0x8D).
BRANCHED_DEREF_147 = 0x2490D0


def _lcd(data):
    return next(s for s in container.parse_container(data).sections
                if "LCD Firmware" in (s.name or ""))


# --- lcdgroups.host_writer ----------------------------------------------------------------------

@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_host_writer_proves_the_ungated_writer(corpus, version):
    w = lcdgroups.host_writer(corpus[version]["mcf"])
    assert w.target == 0x21B678
    assert w.builder == 0x21C1AE
    assert (w.state, w.records, w.b3) == (0x20006E5C, 0x20006E6C, 0)
    assert w.proof


def test_host_writer_refuses_a_broken_writer_shape(mcf_136):
    broken = bytearray(mcf_136)
    at = _lcd(mcf_136).offset + 0x21B678 - LCD_BASE + 14      # strb r1,[r0,#0xb]
    assert broken[at:at + 2] == bytes.fromhex("c172")
    broken[at:at + 2] = b"\x00\xbf"                            # nop
    with pytest.raises(lcdgroups.HostWriterUnknown) as ei:
        lcdgroups.host_writer(bytes(broken))
    assert ei.value.code == "VARIANT_SETTER_UNKNOWN"
    with pytest.raises(newbutton.VariantSetterUnknown) as ei:
        newbutton.guicode_selected_groups(bytes(broken))
    assert ei.value.code == "VARIANT_SETTER_UNKNOWN"


def test_host_writer_refuses_a_nonzero_b3(mcf_136):
    broken = bytearray(mcf_136)
    gs, _ = guidata.guidata_extent(mcf_136)
    at = next(gs + 8 + i * 8 for i in range(mcf_136[gs + 7]) if mcf_136[gs + 8 + i * 8] == 0x10)
    broken[at + 3] = 1
    with pytest.raises(lcdgroups.HostWriterUnknown, match="b3"):
        lcdgroups.host_writer(bytes(broken))


def test_a_container_without_an_lcd_section_refuses_cleanly(mcf_147):
    head = mcf_147[:_lcd(mcf_147).offset]
    at = head.index(b"LCD Firmware\x00")
    renamed = bytearray(mcf_147)
    renamed[at:at + 3] = b"XYZ"
    assert not any("LCD Firmware" in (s.name or "")
                   for s in container.parse_container(bytes(renamed)).sections)
    with pytest.raises(lcdgroups.HostWriterUnknown, match="LCD"):
        lcdgroups.host_writer(bytes(renamed))
    with pytest.raises(newbutton.VariantSetterUnknown):
        newbutton.guicode_selected_groups(bytes(renamed))


# --- the driven-group scan ----------------------------------------------------------------------

def test_13_6_returns_todays_49_groups(mcf_136):
    assert newbutton.guicode_selected_groups(mcf_136) == GROUPS_136
    s = newbutton.setter_calls(mcf_136)
    assert s.cell == newbutton.SET_VARIANT_PTR
    assert (s.n_literal_loads, s.n_raw_loads, s.n_derefs, s.n_calls) == (76, 76, 105, 105)


def test_14_7_returns_the_47_researched_groups(mcf_147):
    assert newbutton.guicode_selected_groups(mcf_147) == GROUPS_147
    s = newbutton.setter_calls(mcf_147)
    assert s.cell == hooklocate.host_api_cells(mcf_147)[0x04] == 0x200395FC
    assert (s.n_literal_loads, s.n_raw_loads, s.n_derefs, s.n_calls) == (86, 86, 113, 113)
    assert s.writer.target == 0x21B678
    assert {c.group for c in s.calls} == GROUPS_147
    assert any(c.deref == BRANCHED_DEREF_147 and c.group == 0x8D for c in s.calls)


def test_11_23_returns_39_groups(mcf_1123):
    assert newbutton.guicode_selected_groups(mcf_1123) == GROUPS_1123
    s = newbutton.setter_calls(mcf_1123)
    assert (s.n_literal_loads, s.n_raw_loads, s.n_derefs, s.n_calls) == (64, 64, 90, 90)


def test_skipping_the_branch_follow_refuses_14_7(mcf_147, monkeypatch):
    """The can-fail check: without the one `b` the walk may follow, 14.7's call at
    `BRANCHED_DEREF_147` is unresolved and the locator refuses rather than drop it."""
    monkeypatch.setattr(newbutton, "SETTER_BRANCH_HOPS", 0)
    with pytest.raises(newbutton.VariantSetterUnknown, match=hex(BRANCHED_DEREF_147)):
        newbutton.guicode_selected_groups(mcf_147)


def test_a_cell_live_past_the_visit_budget_refuses(mcf_136, monkeypatch):
    """The walk from a load visits every path while the cell is live; a budget that runs out
    first refuses rather than miss the derefs past it."""
    monkeypatch.setattr(newbutton, "SETTER_LOAD_WINDOW", 100)
    with pytest.raises(newbutton.VariantSetterUnknown, match="still live"):
        newbutton.guicode_selected_groups(mcf_136)


def _guicode_offset(data, addr):
    gc = theme.guicode_section(data)
    return gc.offset + addr - theme.GUICODE_BASE_DEFAULT


def test_a_deref_without_its_blx_refuses(mcf_147):
    call = newbutton.setter_calls(mcf_147).calls[0]
    broken = bytearray(mcf_147)
    at = _guicode_offset(mcf_147, call.blx)
    assert struct.unpack_from("<H", broken, at)[0] >> 7 == 0x8F       # blx rM
    broken[at:at + 2] = b"\x00\xbf"                                  # nop
    with pytest.raises(newbutton.VariantSetterUnknown, match="no blx") as ei:
        newbutton.guicode_selected_groups(bytes(broken))
    assert ei.value.code == "VARIANT_SETTER_UNKNOWN"


def test_a_non_constant_r0_refuses(mcf_147):
    call = newbutton.setter_calls(mcf_147).calls[0]
    broken = bytearray(mcf_147)
    for a in range(call.deref + 2, call.blx, 2):          # the call's `movs r0,#group`
        at = _guicode_offset(mcf_147, a)
        if broken[at + 1] == 0x20 and broken[at] == call.group:
            broken[at:at + 2] = bytes([0x40, 0x1C])      # adds r0, r0, #1
            break
    else:
        pytest.fail("no movs r0,#group between the deref and its blx")
    with pytest.raises(newbutton.VariantSetterUnknown, match="r0"):
        newbutton.guicode_selected_groups(bytes(broken))


def test_another_store_to_the_cell_refuses(mcf_147):
    """The cell has one writer (entry0's copy); a second `str` through a register holding it
    would make the scan's answer depend on what that store wrote."""
    def fields(call):
        ldr = struct.unpack_from("<H", mcf_147, _guicode_offset(mcf_147, call.deref))
        return ldr[0], (ldr[0] >> 3) & 7, ldr[0] & 7     # ldr rT,[rN,#0]: (raw, rN, rT)

    # a deref through a register that still holds the cell after it (rN != rT)
    call = next(c for c in newbutton.setter_calls(mcf_147).calls if fields(c)[1] != fields(c)[2])
    broken = bytearray(mcf_147)
    at = _guicode_offset(mcf_147, call.deref)
    ldr = fields(call)[0]
    assert ldr >> 11 == 0b01101
    struct.pack_into("<H", broken, at, ldr & ~0x0800)    # str rT,[rN,#0]
    with pytest.raises(newbutton.VariantSetterUnknown, match="store"):
        newbutton.guicode_selected_groups(bytes(broken))


def test_an_indirect_transfer_while_the_cell_is_live_refuses(mcf_147):
    """A `bx r3` where the cell's register is still live: the paths past it are not followed,
    so the scan refuses rather than stop there."""
    def regs(call):
        ldr = struct.unpack_from("<H", mcf_147, _guicode_offset(mcf_147, call.deref))[0]
        return (ldr >> 3) & 7, ldr & 7                  # ldr rT,[rN,#0]: (rN, rT)

    call = next(c for c in newbutton.setter_calls(mcf_147).calls if regs(c)[0] >= 4)
    broken = bytearray(mcf_147)
    struct.pack_into("<H", broken, _guicode_offset(mcf_147, call.deref), 0x4718)   # bx r3
    with pytest.raises(newbutton.VariantSetterUnknown, match="transfers control") as ei:
        newbutton.guicode_selected_groups(bytes(broken))
    assert ei.value.code == "VARIANT_SETTER_UNKNOWN"


def test_a_load_hidden_from_the_decode_fails_the_census(mcf_147):
    """A load of the cell swallowed by a 32-bit instruction is invisible to the decode the walk
    runs on; the raw halfword census still counts it, and the counts disagree. The load's
    32-bit predecessor becomes `nop ; 0xF000`, and `0xF000` pairs with the load."""
    s = newbutton.setter_calls(mcf_147)
    assert s.n_raw_loads == s.n_literal_loads == 86
    load = s.calls[0].load
    at = _guicode_offset(mcf_147, load) - 4
    assert struct.unpack_from("<H", mcf_147, at)[0] >> 11 in (0b11101, 0b11110, 0b11111)
    broken = bytearray(mcf_147)
    broken[at:at + 4] = b"\x00\xbf\x00\xf0"              # nop ; first half of a 32-bit insn
    with pytest.raises(newbutton.VariantSetterUnknown, match="raw halfword scan"):
        newbutton.guicode_selected_groups(bytes(broken))


def test_the_gate_reads_the_147_driven_groups(mcf_147, monkeypatch):
    """`recipe`'s reuse gate works on 14.7 now: moving a driven group's slice is refused as a
    driven-group reuse (STRUCTURAL_GATE), not VARIANT_SETTER_UNKNOWN."""
    import dataclasses
    from fireflymcf import recipe
    real = guidata.group_table
    built = bytearray(mcf_147)
    built[-1] ^= 1

    def moved(data):
        gt = real(data)
        if data != mcf_147:
            gt = list(gt)
            gt[5] = dataclasses.replace(gt[5], start=gt[5].start + 1)
        return gt

    monkeypatch.setattr(guidata, "group_table", moved)
    with pytest.raises(recipe.GateError) as ei:
        recipe._assert_no_guicode_selected_group_reused(mcf_147, bytes(built))
    assert ei.value.code == "STRUCTURAL_GATE"


# --- the emulator: the located set is what the LCD's records show after boot ---------------------

def test_boot_on_147_drives_only_located_groups(mcf_147):
    """After boot on stock 14.7, every group whose LCD runtime record (`host_writer.records`,
    16 bytes per group, variant counters at +0x0A/+0x0B) is non-zero is in the located set,
    and no hold-safe group is (2026-11-08: 36 groups non-zero)."""
    pytest.importorskip("unicorn")
    from fireflymcf.emu.scenario import Scenario
    w = lcdgroups.host_writer(mcf_147)
    n = len(guidata.group_table(mcf_147))
    s = Scenario(mcf_147, timeout_s=240)
    s.boot()
    rec = s.read_mem(w.records, 16 * n)
    lit = {g for g in range(n) if rec[g * 16 + 0x0A] or rec[g * 16 + 0x0B]}
    assert lit, "no group record was written: the read is not of the records"
    assert lit <= newbutton.guicode_selected_groups(mcf_147)
    assert not lit & set(newbutton.hold_safe_groups(mcf_147))


def test_sendmap_reports_driven_groups_as_unknown_when_the_scan_refuses(mcf_147):
    from fireflymcf import sendmap
    assert sendmap.binding_groups(mcf_147, False)[5].guicode_variant is True
    head = mcf_147[:_lcd(mcf_147).offset]
    renamed = bytearray(mcf_147)
    at = head.index(b"LCD Firmware\x00")
    renamed[at:at + 3] = b"XYZ"
    groups = sendmap.binding_groups(bytes(renamed), False)
    assert groups and all(g.guicode_variant is None for g in groups)


# --- reserved groups and the pools (2026-11-08) -------------------------------------------------

# The stock inverter/charger enable/disable templates sit in groups 68/69 on all three images
# (research 2026-11-08, `groups.md` §2.4/§3). Test expectations, never locators.
RESERVED_ALL = {68: 0x1FFD3, 69: 0x1FFC5}
# 13.6's hold-safe pool as the library returned it with the 13.6 constant {68, 69} (2026-11-08).
HOLD_SAFE_136 = [15, 36, 46, 70, 81, 96, 126, 127, 162, 168, 171, 174, 175, 182, 183, 185, 190,
                 191, 192, 193, 194, 195, 196, 197, 198, 229]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_reserved_groups_locate_on_every_image(corpus, version):
    assert newbutton.reserved_groups(corpus[version]["mcf"]) == RESERVED_ALL


def test_hold_safe_on_13_6_is_todays_list(mcf_136):
    assert newbutton.hold_safe_groups(mcf_136) == HOLD_SAFE_136


def test_hold_safe_on_14_7_has_no_driven_reserved_or_externally_selected_group(mcf_147):
    gt = guidata.group_table(mcf_147)
    safe = newbutton.hold_safe_groups(mcf_147)
    assert len(safe) == 44                          # research 2026-11-08: 88 free, 44 hold-safe
    assert not set(safe) & GROUPS_147
    assert not set(safe) & set(RESERVED_ALL)
    assert not [g for g in safe if gt[g].raw[1] & 0xC0]


def test_unused_command_groups_on_14_7_offers_no_driven_group(mcf_147, monkeypatch, request):
    unused = newbutton.unused_command_groups(mcf_147)
    assert unused and not set(unused) & GROUPS_147
    # can fail: a driven set holding the lowest candidate takes it out of the pool
    newbutton.unused_command_groups.cache_clear()
    request.addfinalizer(newbutton.unused_command_groups.cache_clear)
    monkeypatch.setattr(newbutton, "guicode_selected_groups",
                        lambda data: GROUPS_147 | {unused[0]})
    assert newbutton.unused_command_groups(mcf_147) == unused[1:]


def _second_template(data, dgn_group, victim):
    """`data` with group `victim` pointed at the first binding of `dgn_group`'s slice: a second
    group whose whole slice is that template's DGN."""
    gt = guidata.group_table(data)
    gs, _ = guidata.guidata_extent(data)
    out = bytearray(data)
    at = gs + gt[victim].offset
    struct.pack_into("<H", out, at + guidata.GROUP_START_FIELD, gt[dgn_group].start)
    out[at + guidata.GROUP_COUNT_FIELD] = 1
    return bytes(out)


def test_a_second_pure_template_group_refuses(mcf_136):
    two = _second_template(mcf_136, 68, 70)
    with pytest.raises(newbutton.ReservedGroupUnknown) as ei:
        newbutton.reserved_groups(two)
    assert ei.value.code == "RESERVED_GROUP_UNKNOWN"
    assert "68" in str(ei.value) and "70" in str(ei.value)
    with pytest.raises(newbutton.ReservedGroupUnknown):
        newbutton.hold_safe_groups(two)


def test_an_absent_template_refuses(mcf_136):
    gt = guidata.group_table(mcf_136)
    gs, _ = guidata.guidata_extent(mcf_136)
    gone = bytearray(mcf_136)
    gone[gs + gt[69].offset + guidata.GROUP_COUNT_FIELD] = 0
    with pytest.raises(newbutton.ReservedGroupUnknown, match="0x1ffc5"):
        newbutton.reserved_groups(bytes(gone))


def test_the_reserved_refusal_is_coded_through_a_recipe():
    from fireflymcf import recipe
    assert newbutton.ReservedGroupUnknown in recipe._CODED


def test_the_finish_gate_guards_every_located_template(mcf_136):
    """The finish gate protects the groups the stock holds the templates in, located: a build
    that changes one is refused, whichever its number."""
    from fireflymcf import recipe
    two = _second_template(mcf_136, 68, 70)
    changed = bytearray(two)
    gs, _ = guidata.guidata_extent(two)
    changed[gs + guidata.group_table(two)[70].offset + 1] ^= 0x10
    with pytest.raises(recipe.GateError, match="reserved group 70"):
        recipe._assert_reserved_groups_intact(two, bytes(changed))
    recipe._assert_reserved_groups_intact(mcf_136, mcf_136)


REPO_ROOT = Path(__file__).resolve().parent.parent
_GROWN_BUILDS = sorted((REPO_ROOT / "builds").glob("*.bin"))


def _added_groups(stock, data):
    """Groups a build changed from stock (its entry or its binding slice)."""
    sg, dg = guidata.group_table(stock), guidata.group_table(data)
    sb, db = guidata.binding_table(stock), guidata.binding_table(data)
    out = set()
    for g in dg:
        if g.index >= len(sg):
            out.add(g.index)
            continue
        s = sg[g.index]
        if g.raw != s.raw or [b.raw for b in db[g.start:g.start + g.count]] != \
                [b.raw for b in sb[s.start:s.start + s.count]]:
            out.add(g.index)
    return out


@pytest.mark.parametrize("path", _GROWN_BUILDS)
def test_a_build_used_as_stock_never_offers_its_own_groups(mcf_136, path):
    """Review focus (2026-11-08): every pool reads the image's own GuiCode and GuiData, so a
    reference build used as the stock of the next build sees the groups it added as taken."""
    data = path.read_bytes()
    added = _added_groups(mcf_136, data)
    if not added:
        pytest.skip("this build added no group")
    assert newbutton.reserved_groups(data) == RESERVED_ALL
    for pool in (newbutton.free_group_slots, newbutton.unused_command_groups,
                 newbutton.assignable_groups, newbutton.hold_safe_groups):
        assert not added & set(pool(data)), (pool.__name__, sorted(added & set(pool(data))))
