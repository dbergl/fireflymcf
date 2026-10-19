"""Tests for `statusbind` — the pin -> cell binding toolkit (MCF-FORMAT.md §5.4c).

The load-bearing claims are (a) `var` is one number across the receive filter, the slot array
and GuiCode's `2*var` immediate, (b) each editable joint is same-size and touches nothing else,
and (c) every refusal is a real gate. Each is tested from the bytes, not from a constant.
"""
import re

import pytest

from fireflymcf import guidata, patch, status_map, statusbind as sb, theme

BUILD = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def data():
    with open(BUILD, "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------------------
# the mechanism
# --------------------------------------------------------------------------------------

def test_var_is_one_number_across_the_whole_chain(data):
    """`var` == receive-filter entry index == slot index == GuiCode's `2*var` immediate.

    This is what lets a single number address the filter, the slot array and the colour cell,
    and it is the premise every other function here rests on. Checked over EVERY block in the
    committed bridge, not a sample.
    """
    import capstone
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    checked = 0
    with open("src/fireflymcf/data/slot_bridge_136.txt") as fh:
        for line in fh:
            m = re.match(r"var=(\d+) read@(\w+)", line.strip())
            if not m:
                continue
            var, addr = int(m.group(1)), int(m.group(2), 16)
            ins = next(md.disasm(code[addr - base:addr - base + 4], addr), None)
            assert ins is not None, f"var {var}: read@{addr:#x} did not decode"
            imm = re.search(r"#(0x[0-9a-f]+|\d+)", ins.op_str)
            got = 0 if imm is None else int(imm.group(1), 0)   # `[r4]` encodes #0
            assert got == 2 * var, (
                f"var {var}: read immediate {got} != 2*var ({2 * var}) in "
                f"{ins.mnemonic} {ins.op_str}")
            checked += 1
    assert checked > 100, f"expected the whole bridge, only checked {checked} blocks"


def test_slot_array_base_is_the_struct_plus_0x10c():
    """The LCD writes slot[var] at struct+0x10C and GuiCode indexes r4; they must be one array."""
    assert 0x20007EBC + 0x10C == 0x20007FC8


def test_pin_bindings_match_the_status_map_backbone(data):
    """`pin_bindings` is a view over `status_map.backbone`; spot-check two pins end to end."""
    b = sb.pin_bindings(data)
    assert b[15].var == 32 and b[15].colour_index == 145 and b[15].bool_index == 67
    assert b[15].bridged and b[15].usable and not b[15].commanded
    assert b[30].var == 72 and b[30].colour_index == 313
    # pin 42's var carries no GuiCode block -- the one thing this module cannot manufacture
    assert not b[42].bridged and not b[42].usable


def test_free_pins_are_g12_outputs_only(data):
    """The filter also watches instances far above 44 (other bus devices); those are not pins."""
    free = sb.free_pins(data)
    assert free, "expected some uncommanded G12 outputs"
    assert all(sb.PIN_MIN <= p <= sb.PIN_MAX for p in free), free
    commanded = status_map.commanded_instances(data)
    assert not (set(free) & commanded)


def test_spare_cell_indices_are_referenced_by_no_record(data):
    """A spare index must be nameable by nothing on screen, or repointing it recolours a widget."""
    spare = sb.spare_cell_indices(data)
    assert spare, "expected spare +0x44BC8 indices"
    con = status_map.object_consumers(data)
    for i in spare:
        assert i not in con.colour and i not in con.vslot


def test_state_loads_key_states_by_cmp_value_not_branch_order(data):
    """3-way blocks compare 4 BEFORE 1, so 'the first branch' is the fault colour, not the on
    colour. Var 22 (pin 5) is such a block: taking branch order labelled its RED as 'on'."""
    ls = sb.state_loads(data, 22)
    assert ls[sb.OFF_STATE].rgb == (65, 81, 163)     # button blue
    assert ls[sb.ON_STATE].rgb == (0, 174, 239)      # cyan, as every on-branch
    assert ls[4].rgb == (204, 33, 39)                # the fault red, NOT reported as "on"


def test_every_on_branch_examined_uses_the_same_cyan_master(data):
    """The claim behind ON_STATE: state 1 is 'output on' across the firmware."""
    bridge = status_map.load_bridge("136")
    seen = set()
    for var in bridge:
        try:
            ls = sb.state_loads(data, var)
        except sb.StatusBindError:
            continue
        if sb.ON_STATE in ls and ls[sb.ON_STATE].rgb is not None:
            seen.add(ls[sb.ON_STATE].rgb)
    assert seen == {(0, 174, 239)}, seen


# --------------------------------------------------------------------------------------
# joint 1: the receive filter
# --------------------------------------------------------------------------------------

def test_set_filter_instance_is_one_byte_and_moves_the_whole_bridge(data):
    out, edits = sb.set_filter_instance(data, 32, 42)
    payload = [e for e in edits if e.structure == "filter-instance"]
    assert len(payload) == 1 and len(payload[0].new) == 1
    assert len(out) == len(data)
    row = next(r for r in status_map.backbone(out) if r.var == 32)
    # the bridge follows the var, not the pin
    assert row.instance == 42 and row.colour_idx == [145] and row.bool_idx == [67]
    # exactly one byte of payload changed (plus the checksum)
    diff = [i for i in range(len(data)) if data[i] != out[i]]
    assert len(diff) == 3, diff          # 1 instance byte + 2 checksum bytes


def test_set_filter_instance_refuses_a_non_dimmer_entry(data):
    vt = guidata.variable_table(data)
    other = next(i for i, e in enumerate(vt) if e.dgn_low != sb.DGN_DC_DIMMER_STATUS_3)
    with pytest.raises(sb.StatusBindError, match="not DC_DIMMER_STATUS_3"):
        sb.set_filter_instance(data, other, 20)


def test_set_filter_instance_refuses_a_non_g12_instance(data):
    with pytest.raises(sb.StatusBindError, match="outside the G12"):
        sb.set_filter_instance(data, 32, 90)


# --------------------------------------------------------------------------------------
# joint 2: the cell table
# --------------------------------------------------------------------------------------

def test_index_cell_writes_four_bytes_and_makes_the_pin_usable(data):
    b = sb.pin_bindings(data)[17]
    assert b.needs_index, "pin 17 is the bridged-but-unindexed case this covers"
    idx = sb.spare_cell_indices(data)[0]
    out, edits = sb.index_cell(data, idx, b.colour_cell)
    assert [e.structure for e in edits] == ["cell-table-entry", "checksum"]
    assert status_map.cell_table(out)[idx] == b.colour_cell
    assert sb.pin_bindings(out)[17].colour_index == idx
    assert sb.pin_bindings(out)[17].usable


def test_index_cell_refuses_an_index_a_record_uses(data):
    """The gate that stops a bind from silently recolouring an existing widget."""
    con = status_map.object_consumers(data)
    used = sorted(set(con.colour) | set(con.vslot))[0]
    with pytest.raises(sb.StatusBindError, match="referenced by a record"):
        sb.index_cell(data, used, 0x20039974)


def test_index_cell_refuses_a_cell_outside_sram(data):
    idx = sb.spare_cell_indices(data)[0]
    with pytest.raises(sb.StatusBindError, match="outside the GuiCode SRAM"):
        sb.index_cell(data, idx, 0x90001234)


# --------------------------------------------------------------------------------------
# joint 3: the state colours
# --------------------------------------------------------------------------------------

def test_set_state_colour_reproduces_the_hardware_verified_v9_edit(data):
    """v9 (flashed 2026-08-17) fixed pin 15's off colour by hand: the 16-bit LDR at 0x002488EA
    went `1c 49` -> `22 49`. The tool must derive the same two bytes structurally."""
    out, edits = sb.set_state_colour(data, 32, "off", (65, 81, 163))
    payload = [e for e in edits if e.structure == "guicode-state-colour"]
    assert len(payload) == 1
    assert payload[0].old == bytes((0x1C, 0x49))
    assert payload[0].new == bytes((0x22, 0x49))
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    assert payload[0].file_offset == section.offset + (0x002488EA - base)


def test_set_state_colour_changes_exactly_one_cells_resting_default(data):
    """The safety property: repointing the LDR, not the master constant, leaves every other
    cell fed by either master untouched."""
    before = theme.resting_defaults(data)
    out, _ = sb.set_state_colour(data, 32, "off", (65, 81, 163))
    after = theme.resting_defaults(out)
    moved = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert moved == {145}, moved
    assert after[145] == (65, 81, 163)


def test_set_state_colour_is_idempotent(data):
    out, _ = sb.set_state_colour(data, 32, "off", (65, 81, 163))
    again, edits = sb.set_state_colour(out, 32, "off", (65, 81, 163))
    assert again == out and edits == []


def test_set_state_colour_refuses_a_colour_the_firmware_does_not_define(data):
    """Only masters that already exist are reachable; inventing a constant would need a free
    pool word and would recolour every cell fed by the master."""
    assert (1, 2, 3) not in sb.colour_masters(data)
    with pytest.raises(sb.StatusBindError, match="no theme master holds"):
        sb.set_state_colour(data, 32, "off", (1, 2, 3))


def test_set_state_colour_touches_only_the_guicode_checksum(data):
    out, edits = sb.set_state_colour(data, 32, "off", (65, 81, 163))
    labels = [e.note for e in edits if e.structure == "checksum"]
    assert labels == ["resealed section-checksum:Lyra 13v6 GuiCode"]
    resealed, changed = patch.reseal(out)
    assert resealed == out and changed == []


# --------------------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------------------

def test_bind_pin_on_an_indexed_pin_only_sets_the_colour(data):
    out, idx, edits = sb.bind_pin(data, 15, off_colour=(65, 81, 163))
    assert idx == 145                                  # already indexed; no table edit needed
    assert "cell-table-entry" not in [e.structure for e in edits]
    assert sb.state_loads(out, 32)[sb.OFF_STATE].rgb == (65, 81, 163)


def test_bind_pin_indexes_a_spare_for_an_unindexed_pin(data):
    out, idx, edits = sb.bind_pin(data, 17, off_colour=(65, 81, 163))
    assert idx in sb.spare_cell_indices(data)
    assert "cell-table-entry" in [e.structure for e in edits]
    b = sb.pin_bindings(out)[17]
    assert b.colour_index == idx and b.usable


def test_bind_pin_refuses_an_unbridged_pin(data):
    """Pin 42 has no GuiCode block; the module must say so rather than invent a cell."""
    with pytest.raises(sb.StatusBindError, match="no GuiCode bridge block"):
        sb.bind_pin(data, 42)


def test_bind_pin_refuses_a_pin_an_existing_control_commands(data):
    commanded = sorted(status_map.commanded_instances(data) & set(range(1, 45)))
    with pytest.raises(sb.StatusBindError, match="already commanded"):
        sb.bind_pin(data, commanded[0])


def test_bind_pin_leaves_a_valid_resealed_image(data):
    out, _idx, _edits = sb.bind_pin(data, 17, off_colour=(65, 81, 163))
    resealed, changed = patch.reseal(out)
    assert resealed == out and changed == []


# --------------------------------------------------------------------------------------
# prove-can-fail
# --------------------------------------------------------------------------------------

def test_the_resting_default_assertion_can_fail(data):
    """`test_set_state_colour_changes_exactly_one_cells_resting_default` is load-bearing: if the
    edit had instead rewritten the MASTER constant, many cells would move. Simulate that and
    confirm the same predicate goes False, so a passing build passes on merit."""
    before = theme.resting_defaults(data)
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    # 0x20039974 is the navy master; find where the theme-init stores its constant and change it
    corrupt = bytearray(data)
    hits = 0
    for off in range(0, len(code) - 4, 2):
        if int.from_bytes(code[off:off + 4], "little") == 0x00272862:      # (39,40,98)
            corrupt[section.offset + off:section.offset + off + 4] = (0x4151A3).to_bytes(4, "little")
            hits += 1
    assert hits, "expected the navy constant to appear in a literal pool"
    after = theme.resting_defaults(bytes(corrupt))
    moved = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert len(moved) > 1, (
        f"rewriting the shared master should move MANY cells, moved {moved} -- the "
        f"one-cell assertion would not be discriminating")


# --------------------------------------------------------------------------------------
# joint 1c: which mode extracts a subscription (GuiData, 1 byte high nibble)
# --------------------------------------------------------------------------------------

def test_set_extract_mode_widens_var_289_to_u16():
    """0xEE's entry reads one byte today; the packed register needs both. Mode 2 is
    `payload[n] | payload[n+1] << 8` (MCF-FORMAT.md §5.4a, the tbb at 0x0021C99A)."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    before = {v.index: v for v in guidata.variable_table(data)}[289]
    # NOTE: guidata.Variable carries (index, offset, dgn_low, value, raw) only -- there
    # is no `extract_mode` attribute; the mode is the high nibble of raw[2] and the
    # source payload byte is raw[3] & 7 (MCF-FORMAT.md §5.4a).
    assert before.raw[2] >> 4 == 1, "fixture drift: var 289 is not mode 1 in stock"
    assert before.raw.hex() == "ce5f1d02ee000000", "fixture drift: var 289 is not 0xEE"

    out, edits = sb.set_extract_mode(data, 289, 2)

    after = {v.index: v for v in guidata.variable_table(out)}[289]
    assert after.raw[2] >> 4 == 2
    # everything else about the entry is untouched
    assert after.dgn_low == before.dgn_low
    assert after.value == before.value          # the 0xEE selector filter
    assert after.raw[3] == before.raw[3]        # source payload byte
    assert after.raw[2] & 0x0F == before.raw[2] & 0x0F   # refresh class preserved
    assert len(out) == len(data)


def test_set_extract_mode_edits_exactly_one_byte_plus_the_checksum():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _edits = sb.set_extract_mode(data, 289, 2)
    differing = [i for i in range(len(data)) if data[i] != out[i]]
    # one entry byte + the 2-byte GuiData checksum
    assert len(differing) == 3, differing


def test_set_extract_mode_refuses_a_mode_that_writes_nothing():
    """Modes 0 and 3 are the skip arms -- the entry would stop updating its slot
    entirely. Refuse rather than silently kill a subscription."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    for dead in (0, 3, 8, 15):
        with pytest.raises(sb.StatusBindError):
            sb.set_extract_mode(data, 289, dead)


def test_set_extract_mode_refuses_an_out_of_range_var():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with pytest.raises(sb.StatusBindError):
        sb.set_extract_mode(data, 9999, 2)


# --------------------------------------------------------------------------------------
# joint 2b: which type a variable slot has (GuiData, 1 byte)
# --------------------------------------------------------------------------------------

def test_set_variable_type_makes_a_spare_slot_u16():
    """A wtype-8 numeric readout needs a type-3 (u16) slot; every spare index is type
    1 or 2, so the type byte has to be set alongside the pointer."""
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    spare = sb.spare_cell_indices(data)[0]
    assert layout.variable_register(data, spare)[1] != 3

    out, edits = sb.set_variable_type(data, spare, 3)

    assert layout.variable_register(out, spare)[1] == 3
    assert layout.variable_register(out, spare)[0] == \
        layout.variable_register(data, spare)[0], "the pointer must not move"
    assert len(out) == len(data)


def test_set_variable_type_edits_one_byte_plus_the_checksum():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    spare = sb.spare_cell_indices(data)[0]
    out, _ = sb.set_variable_type(data, spare, 3)
    differing = [i for i in range(len(data)) if data[i] != out[i]]
    assert len(differing) == 3, differing


def test_set_variable_type_refuses_a_referenced_index():
    """Re-typing a slot a widget already reads would silently change how that widget
    formats -- the same hazard index_cell refuses for the pointer."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with pytest.raises(sb.StatusBindError):
        sb.set_variable_type(data, 278, 3)     # the live Time at Stop Volts slot


def test_set_variable_type_refuses_an_unknown_type_code():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    spare = sb.spare_cell_indices(data)[0]
    with pytest.raises(sb.StatusBindError):
        sb.set_variable_type(data, spare, 99)
