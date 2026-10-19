"""On-screen register readouts: a display cell, a per-tick copy routine, a directive + readout
record pair. Spec: its design note (not published).

The mechanism is the Water Pump minute readout generalised (pumpauto step 3): a wtype-8 draws
the cell its value_slot names every tick (coach, PC.58), a wtype-9 directive walked before it
sets width/places (GUICODE Finding 65), and a routine on the hook chain writes the cell.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import (celltables, followchannel, guidata, layout, records, sramreach, statusbind,
              status_map, theme, thumb)
from .register import Register


class ReadoutError(Exception):
    """Refuse rather than guess: no cell, no donor, a bad parameter."""


@dataclass(frozen=True)
class DisplayCell:
    slot: int        # +0x44BC8 index the readout's value_slot names
    addr: int        # SRAM address the routine writes
    width: int       # 1 -> strb / variable type 1; 2 -> strh / variable type 3
    tier: int        # 1 proven spare halfword; 2 odd spare byte; 3 fresh SRAM via index_cell
    fresh: bool      # True only for tier 3 -- an address no stock instruction names


_TYPE_FOR_WIDTH = {1: 1, 2: statusbind.VARIABLE_TYPE_U16}

# The variable-type table offset `_ensure_type` reads/writes -- named here (not just inlined)
# so a caller checking a cell's stored type (recipe_ops.py's add_register_readout.verify) can
# reference it instead of restating the literal. Re-exports `layout.VAR_TYPE_TABLE_136`, which
# is what `_ensure_type` itself already matched -- both are 13.6-specific like every other
# hardcoded GuiCode address this feature's ops refuse against off-build (`_check_append_point`).
VAR_TYPE_TABLE_OFF = layout.VAR_TYPE_TABLE_136


def _only_spare_cell_entries(data: bytes, addr: int, width: int) -> bool:
    forms = sramreach.reaching_forms(data, addr, width)
    if forms["raw_word"] or forms["base_offset"] or forms["state_struct"] or forms["indexed_table"]:
        return False
    cells = status_map.cell_table(data)
    spare = set(statusbind.spare_cell_indices(data))
    for c in forms["cell_table"]:
        c = int(c, 16)
        if any(i not in spare for i, a in enumerate(cells) if a == c):
            return False
    return True


def _ensure_type(data: bytes, idx: int, width: int) -> tuple[bytes, list]:
    """Type entry `idx` to `_TYPE_FOR_WIDTH[width]` if it isn't already -- `FUN_00257BFC` copies
    this byte into render state and `FUN_00251E38` uses it as the READ WIDTH, so every tier must
    leave it equal to the readout's width, not only the fresh tier-3 cell. Returns `data`
    unchanged with `edits == []` when the type already matches."""
    gs, _ = guidata.guidata_extent(data)
    if data[gs + celltables.type_table_off(data) + idx] == _TYPE_FOR_WIDTH[width]:
        return data, []
    out, edits = statusbind.set_variable_type(data, idx, _TYPE_FOR_WIDTH[width])
    return out, list(edits)


def allocate_display_cell(data: bytes, width: int) -> tuple[bytes, DisplayCell, list]:
    """A cell a readout may name and a routine may write, by the cheapest safe tier.
    Returns `(image, cell, edits)`; the image is unchanged only when the cell's type
    already matches the width -- every tier leaves the type byte equal to `width`
    (`_ensure_type`), tier 3 additionally repointing the index at fresh SRAM."""
    if width not in _TYPE_FOR_WIDTH:
        raise ReadoutError(f"width must be 1 or 2 bytes, got {width!r}")
    cells = status_map.cell_table(data)
    spare = statusbind.spare_cell_indices(data)

    # tier 1: the discipline every shipping build used
    try:
        idx, addr = followchannel.scan_display_cell(data)
        if _only_spare_cell_entries(data, addr, width):
            out, edits = _ensure_type(data, idx, width)
            return out, DisplayCell(idx, addr, width, 1, False), edits
    except followchannel.FollowChannelError:
        pass

    # tier 2: a byte readout may take an odd spare cell -- strb/ldrb do not care about parity
    if width == 1:
        sec = theme.guicode_section(data)
        section = data[sec.offset:sec.offset + sec.length]
        base = theme.guicode_base(sec)
        for idx in spare:
            addr = cells[idx]
            if addr % 2 == 0 or not (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH):
                continue
            if followchannel._guicode_references(section, base, addr):
                continue
            if _only_spare_cell_entries(data, addr, 1):
                out, edits = _ensure_type(data, idx, 1)
                return out, DisplayCell(idx, addr, 1, 2, False), edits

    # tier 3: a spare index repointed at free SRAM -- the first use of a fresh address
    runs = sramreach.free_runs(data, 8)
    for start, length in runs:
        addr = (start + 1) & ~1 if width == 2 else start
        if addr + width > start + length:
            continue
        if not _only_spare_cell_entries(data, addr, width):
            continue
        # A viable free run exists but there is no spare index left to repoint at it -- refuse
        # right here, by name, instead of falling through to rescan every remaining free run for
        # nothing (the old `for idx in spare: ... return` fell out of this loop silently on an
        # empty `spare` and only raised the generic message below, after exhausting every run).
        if not spare:
            raise ReadoutError(
                "NO_DISPLAY_CELL: a free SRAM run at %#x fits a %d-byte readout, but no spare "
                "index is left to repoint at it" % (addr, width))
        idx = spare[0]
        out, edits = statusbind.index_cell(data, idx, addr)
        out, type_edits = _ensure_type(out, idx, width)
        edits = list(edits) + list(type_edits)
        return out, DisplayCell(idx, addr, width, 3, True), edits
    raise ReadoutError(
        "NO_DISPLAY_CELL: no spare halfword (tier 1), no odd spare byte (tier 2, width 1 only) "
        "and no free SRAM run (tier 3) fits a %d-byte readout" % width)


# --- fresh cells that alias NOTHING, by growing the +0x44BC8 cell/type table set ------------
#
# `allocate_display_cell` above repoints a table-1 "spare" index, but on 13.6 every such index
# is actually read by an object in one of the OTHER three GuiData object-pointer tables
# (`status_map.all_table_cell_consumers`) -- the coach-48V background-bar bug. There are ZERO
# genuinely-free indices, so a private cell must be a genuinely-NEW table index, minted by
# growing the table (`tablegrow.grow_indexed_tables`). `resolve_cell_tables` (below) reads the
# table's CURRENT base and row count STRUCTURALLY -- so it tracks an earlier op's relocation and
# carries no pinned GuiCode address of its own.
#
# 13.6 reference values, kept only as a documented cross-check (a test asserts `resolve_cell_tables`
# reproduces them) -- NOT the operative path. The bases are `0x90000000 +` the offsets
# `guidata.find_variable_tables` returns; the bound is the immediate of the `cmp.w` at the site
# below. A relocation repoints these, which is why the live path resolves them rather than reading
# the constants.
_CELL_BASE_REF = 0x2535B4    # a GuiCode literal loading the cell/colour table base (0x90044BC8)
_TYPE_BASE_REF = 0x253D08    # a GuiCode literal loading the type table base (0x9004492C)
_CELL_BOUND_SITE = 0x2533BA  # a GuiCode `cmp.w rN,#(row_count-1)` gating the cell table
_CELL_BASE_REF_COUNT = 7     # 13.6: literals loading the cell/colour base
_TYPE_BASE_REF_COUNT = 4     # 13.6: literals loading the type base
_CELL_BOUND_SITE_COUNT = 14  # 13.6: cmp.w index-bound sites


def resolve_cell_tables(data: bytes) -> "tuple[int, int, int]":
    """`(cell_base, type_base, old_len)` for the `+0x44BC8` display-cell table and its parallel
    `+0x4492C` type table, discovered structurally with NO pinned GuiCode literal address.

    The two base runtime addresses come from `guidata.find_variable_tables` (a per-build scan
    that also follows a prior `tablegrow` relocation, so this stays correct after an earlier op
    has grown the table); the shared row-count bound comes from `tablegrow.find_index_bound`,
    which reads the gating `cmp.w` immediate. On 13.6 this returns exactly the values the
    reference constants above name: `(0x90044BC8, 0x9004492C, 665)`.

    Raises `ReadoutError` if the tables cannot be located, or if the build gates them with a
    bound encoding `find_index_bound` does not recognise (e.g. 11.23's movw-materialised
    register compare) -- a clean refusal, never a guess.
    """
    from . import tablegrow  # local import: tablegrow -> theme/xref only, avoid an import cycle
    try:
        dptr_off, type_off = guidata.find_variable_tables(data)
        cell_base = 0x90000000 + dptr_off
        type_base = 0x90000000 + type_off
        old_len = tablegrow.find_index_bound(data, cell_base)
    except (tablegrow.TableGrowError, guidata.VariableTableNotFound) as e:
        raise ReadoutError(
            "NO_DISPLAY_CELL: cannot locate the cell table structurally (unsupported build?): %s" % e)
    return cell_base, type_base, old_len


def allocate_fresh_cells(data: bytes, specs: "list[tuple[int, int | None]]"
                         ) -> "tuple[bytes, list[DisplayCell], list]":
    """Mint one genuinely-private display cell per `(width, vtype)` in `specs` by growing the
    `+0x44BC8` cell table (and its parallel type table) with `tablegrow`.

    Each new cell is a fresh, appended table INDEX (>= the table's current row count) backed by
    a fresh, 4-aligned SRAM address that no stock instruction names -- so nothing else in any of
    the four object tables can reference it, unlike the reused "spare" indices of
    `allocate_display_cell` (which on 13.6 are all cross-table-aliased). Always grows: 13.6 has
    no genuinely-free index to reuse, and mixing a reused index (written at the table's fixed
    stock offset) with a relocated table would write the dead copy. `vtype` is the variable-type
    byte (e.g. `_TYPE_FOR_WIDTH[width]`; `None` -> 0, for a raw colour word the value formatter
    never reads).

    Returns `(image, cells, edits)`; `cells[i]` describes `specs[i]` (tier 3, fresh=True).

    LIMITATION (single grow per recipe): the SRAM reservation carves distinct addresses within
    ONE call, but `sramreach.free_runs` sees only addresses already reachable in the image, so a
    SECOND `allocate_fresh_cells` call in the same recipe would reserve the same SRAM unless the
    first call's addresses became reach-visible in between (e.g. the consuming op appended a
    routine that loads them, as `add_voltage_battery` does). Today only `add_voltage_battery` grows
    the cell table, so this is safe; a future recipe with two growing ops must ensure each op's
    addresses are reachable before the next call, or this must reserve across calls explicitly.
    """
    from . import tablegrow  # local import: tablegrow -> theme/xref only, avoid an import cycle
    if not specs:
        return data, [], []

    cell_base, type_base, old_len = resolve_cell_tables(data)

    # Discover the GuiCode patch surfaces structurally too (base literals to repoint, bound sites
    # to rebump) and pass their counts to the grow as its self-consistency guard. Refuse if any is
    # empty -- a table with no relocatable refs or no rebumpable bound cannot be grown safely.
    cell_refs = tablegrow.find_base_refs(data, cell_base)
    type_refs = tablegrow.find_base_refs(data, type_base)
    bound_sites = tablegrow.find_bound_sites(data, old_len - 1)
    if not (cell_refs and type_refs and bound_sites):
        raise ReadoutError(
            "NO_DISPLAY_CELL: cell table has no relocatable GuiCode surface "
            "(cell refs=%d, type refs=%d, bound sites=%d) -- unsupported build"
            % (len(cell_refs), len(type_refs), len(bound_sites)))

    # Reserve a distinct, 4-aligned SRAM address of the right width for each cell, carving them
    # out of the free runs so two cells never overlap.
    runs = [[start, length] for start, length in sramreach.free_runs(data, min_bytes=4)]

    def _take(width: int) -> int:
        for run in runs:
            start, length = run
            addr = (start + 3) & ~3
            if addr + width <= start + length:
                run[0] = addr + width               # consume through the taken bytes
                run[1] = (start + length) - run[0]
                return addr
        raise ReadoutError(
            "NO_DISPLAY_CELL: no free 4-aligned SRAM run for a %d-byte fresh cell" % width)

    addrs = [_take(width) for width, _ in specs]
    cell_fill = b"".join(struct.pack("<I", a) for a in addrs)
    type_fill = bytes((vt if vt is not None else 0) for _, vt in specs)

    # Append in stock physical order -- the u8 TYPE table immediately before the u32 CELL table --
    # so `guidata.find_variable_tables`' structural scan (a maximal SRAM-pointer run, with a
    # backward scan for the adjacent type array) locates the RELOCATED pair the same way it locates
    # the stock pair. Appending the cell table first instead lets a preceding pointer-looking word
    # bleed into the run and mislocates the base by a few bytes, which mis-resolves every mode-7
    # colour in the render (24 stock fills fell into the coach-battery branch).
    tables = [tablegrow.IndexedTable(type_base, 1), tablegrow.IndexedTable(cell_base, 4)]
    try:
        image, new_indices, edits = tablegrow.grow_indexed_tables(
            data, tables, old_len=old_len, extra=len(specs),
            expect_base_refs={cell_base: len(cell_refs), type_base: len(type_refs)},
            expect_bound_sites=len(bound_sites),
            new_entry_fill={cell_base: cell_fill, type_base: type_fill})
    except tablegrow.TableGrowError as e:
        raise ReadoutError("NO_DISPLAY_CELL: cannot grow the cell table: %s" % e)

    cells = [DisplayCell(idx, addr, width, 3, True)
             for idx, addr, (width, _vt) in zip(new_indices, addrs, specs)]
    return image, cells, edits


ENTRY_OFF = 0        # no frames precede the code: the entry is the blob's first byte


@dataclass(frozen=True)
class ReadoutPlan:
    slot_addr: int
    mask: int
    add: int
    shift: int
    cell: DisplayCell
    hook_at: int
    hook_old: bytes
    tail_target: int
    sub: int = 0          # subtracted after the mask, clamped at 0 (RV-C's +1600 A current offset)


def resolve(data: bytes, *, register: Register, cell: DisplayCell, mask: int = 0xFFFF,
            add: int = 0, shift: int = 0, sub: int = 0) -> ReadoutPlan:
    if not 1 <= mask <= 0xFFFF:
        raise ReadoutError(f"mask must be 1..0xFFFF, got {mask:#x}")
    if not 0 <= add <= 0xFF:
        raise ReadoutError(f"add must fit `adds rN,#imm8` (0..255), got {add}")
    if not 0 <= shift <= 15:
        raise ReadoutError(f"shift must be 0..15, got {shift}")
    if not 0 <= sub <= 0xFFFF:
        raise ReadoutError(f"sub must fit `movw` (0..0xFFFF), got {sub}")
    hook_old, tail = followchannel.hook_state(data)
    return ReadoutPlan(register.slot_addr, mask, add, shift, cell,
                       followchannel.HOOK_OFF, hook_old, tail, sub)


def build(plan: ReadoutPlan, code_load_addr: int) -> tuple[bytes, list]:
    """The per-tick copy: (max(slot & mask - sub, 0) + add) >> shift -> cell, sentinel passed
    through. `sub` is an unsigned subtract clamped at 0 (`subs` sets C on no borrow; `bhs`
    keeps the difference, otherwise it becomes 0) -- RV-C AC current is 20*A + 32000.
    Same stream/label/pool builder as pumpauto.build; the blob is verified instruction for
    instruction by `thumb.assert_disassembles_to` before it is returned."""
    if code_load_addr % 4:
        raise ReadoutError(f"code_load_addr {code_load_addr:#x} is not 4-aligned")

    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    pool_order = ["slot", "cell"]
    pool_val = {"slot": plan.slot_addr, "cell": plan.cell.addr}
    stream: list = []

    def emit(size, fn):
        stream.append(("ins", size, fn))

    def label(name):
        stream.append(("lbl", name))

    def ldrlit_text(rt, here, target):
        off = target - ((here + 4) & ~3)
        return f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"

    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("slot")), ldrlit_text(0, h, s("slot"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    emit(4, lambda h, s: (thumb.movw(2, followchannel.STALE_SENTINEL),
                          f"movw r2, #{followchannel.STALE_SENTINEL:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(1, 2), "cmp r1, r2"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("store"), "eq"), f"beq #{s('store'):#x}"))
    if plan.mask != 0xFFFF:
        emit(4, lambda h, s: (thumb.movw(2, plan.mask), f"movw r2, #{plan.mask:#x}"))
        emit(2, lambda h, s: (thumb.ands_reg(1, 2), "ands r1, r2"))
    if plan.sub:
        emit(4, lambda h, s: (thumb.movw(2, plan.sub), f"movw r2, #{plan.sub:#x}"))
        emit(2, lambda h, s: (thumb.subs_reg(1, 1, 2), "subs r1, r1, r2"))
        emit(2, lambda h, s: (thumb.bcc_n(h, s("nonneg"), "cs"), f"bhs #{s('nonneg'):#x}"))
        emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
        label("nonneg")
    if plan.add:
        emit(2, lambda h, s: (thumb.adds_imm8(1, plan.add), f"adds r1, #{imm(plan.add)}"))
    if plan.shift:
        emit(2, lambda h, s: (thumb.lsrs_imm(1, 1, plan.shift), f"lsrs r1, r1, #{imm(plan.shift)}"))
    label("store")
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("cell")), ldrlit_text(0, h, s("cell"))))
    if plan.cell.width == 2:
        emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    else:
        emit(4, lambda h, s: (thumb.strb_imm12(1, 0, 0), "strb.w r1, [r0]"))
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    labels: dict = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]
    entry = code_load_addr + ENTRY_OFF
    pool_off = (ENTRY_OFF + code_len + 3) & ~3
    pool_abs = {n: code_load_addr + pool_off + i * 4 for i, n in enumerate(pool_order)}

    def sym(name):
        return entry + labels[name] if name in labels else pool_abs[name]

    code = bytearray()
    expected: list = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _k, size, fn = item
        raw, text = fn(entry + len(code), sym)
        if len(raw) != size:
            raise ReadoutError(f"size mismatch at {entry + len(code):#x}: {text!r}")
        code += raw
        expected.append(text)
    thumb.assert_disassembles_to(bytes(code), entry, expected)

    blob = bytearray(code)
    while len(blob) < pool_off:
        blob += b"\x00"
    for n in pool_order:
        blob += struct.pack("<I", pool_val[n])
    assert len(blob) % 4 == 0
    return bytes(blob), thumb.disassemble(bytes(code), entry)


def detour_edit(plan: ReadoutPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    return plan.hook_at, plan.hook_old, thumb.bl(hook_runtime, code_load_addr + ENTRY_OFF)


DONOR_OBJECT = 793          # the Time at Stop Volts stepper (the pump section's proven donor)
DONOR_READOUT_REC = 5       # wtype-8; record 4 before it is the (0, 0x18, 3, 0) directive


JUSTIFY = {"left": 0, "centre": 1, "right": 2}


def directive_bytes(width: int, places: int, zero_pad: bool = False,
                    justify: str = "right") -> bytes:
    """A mode-0 number-format directive (GUICODE Finding 65): flag bit 4 (0x10) keeps trailing
    fractional zeros; bits 2-3 justify within the width (`0x002529DC`: 0 left, 1 centre, 2 right),
    so the default right-justify is flags 0x18 and centre is 0x14 (stock centred steppers, objects
    1131/1132); bit 1 zero-pads to the width, honoured only when right-justified."""
    if not 0 <= width <= 9:
        raise ReadoutError(f"width must be 0..9 (0 = none), got {width}")
    if not 0 <= places <= 3:
        raise ReadoutError(f"places must be 0..3, got {places}")
    if justify not in JUSTIFY:
        raise ReadoutError(f"justify must be one of {sorted(JUSTIFY)}, got {justify!r}")
    return bytes([0, 0x10 | JUSTIFY[justify] << 2 | (0x02 if zero_pad else 0), width, places])


def author_readout_object(data: bytes, *, x: int, y: int, display_slot: int, directive: bytes,
                          donor: tuple[int, int] = (DONOR_OBJECT, DONOR_READOUT_REC)) -> bytes:
    """`<count=2><directive><readout>`: the donor's own directive record with its four tail
    bytes replaced, then the donor readout made absolute at (x, y) reading `display_slot`."""
    if len(directive) != 4 or directive[0] != 0:
        raise ReadoutError("directive must be 4 bytes with mode 0 (see directive_bytes)")
    obj, j = donor
    gs, _ = guidata.guidata_extent(data)
    tbl = guidata.object_tables(data)[1]
    recs = records.object_records(data, gs + tbl[obj])
    if (j < 1 or recs[j].wtype != 8 or recs[j].value_slot is None
            or recs[j - 1].wtype != 9 or recs[j - 1].fmt is None):
        raise ReadoutError(
            f"object {obj} records {j - 1}/{j} are not a (directive, wtype-8 readout) pair")
    d, r = recs[j - 1], recs[j]
    dfrag = bytearray(data[d.offset:d.offset + d.size])
    dfrag[d.fmt_off - d.offset:d.fmt_off - d.offset + 4] = directive
    rfrag = bytearray(data[r.offset:r.offset + r.size])
    rfrag[1] = rfrag[1] & 0xF0                      # coords 0/1 -> base mode 0 (absolute)
    for slot, value in ((0, x), (1, y)):
        off = records.geom_offset(data, r, slot)
        if off is None:
            raise ReadoutError(f"donor readout carries no geom[{slot}]")
        struct.pack_into("<H", rfrag, off - r.offset, value)
    struct.pack_into("<H", rfrag, records.value_slot_offset(data, r) - r.offset, display_slot)
    payload = bytes([2]) + bytes(dfrag) + bytes(rfrag)
    payload += b"\x00" * (-len(payload) % 4)
    chk = records.object_records(payload, 0)
    if not (len(chk) == 2 and chk[0].wtype == 9 and chk[0].fmt == tuple(directive)
            and chk[1].wtype == 8 and chk[1].value_slot == display_slot
            and chk[1].geom[0] == x and chk[1].geom[1] == y and chk[1].base_modes[:2] == [0, 0]):
        raise ReadoutError("authored readout object failed its self-decode")
    return payload


def directive_in_force(data: bytes, root: int, *, switch_values=None,
                       slot_values=None) -> list:
    """The Finding-65 walk: every directive as it is decoded and every wtype-8 readout with the
    directive in force when it is placed, in layout order."""
    gs, _ = guidata.guidata_extent(data)
    tbl = guidata.object_tables(data)[1]
    seen: set = set()
    cur = None
    out: list = []
    for p in layout.Screen(data).walk(root, switch_values=switch_values, slot_values=slot_values):
        for rec in records.object_records(data, gs + tbl[p.obj_index]):
            if rec.offset > p.record_offset:
                break
            if rec.wtype == 9:
                # The firmware overwrites the persistent directive state on EVERY wtype-9
                # decode (Finding 65), so `cur` must track the LAST one reached even on a
                # re-visit -- only the ("directive", ...) event itself is deduped by `seen`,
                # to keep the Finding-65 directive count in tests/test_records.py unchanged.
                cur = rec.fmt
                if (p.obj_index, rec.offset) not in seen:
                    seen.add((p.obj_index, rec.offset))
                    out.append(("directive", p.obj_index, cur))
            if rec.wtype == 8 and rec.offset == p.record_offset:
                out.append(("readout", p.obj_index, rec.value_slot, cur))
    return out


# --- feeding an EXISTING display cell (2026-09-04) ---------------------------------------------
#
# The first readouts allocated a fresh cell and authored a widget to show it. The Absorb/Float
# case (`BUS-BASELINE.md` PC.55) is the other shape: the panel ALREADY draws the value -- object
# 303's wtype-8 readouts on cells 162/163 -- and what starves them is the stock decode's 12-18 V
# fence. Feeding those cells from the receive slots makes the panel's own rows right, with their
# labels, steppers and volt formatting, and authors nothing. Two things have to hold, and both
# are checked rather than assumed: the cell's variable type must equal the routine's write width
# (`FUN_00251E38` reads at the type's width), and every stock instruction that would overwrite
# the cell each tick must be silenced -- `FUN_00247BF8` copies a config byte into 162/163 at
# `0x247C34`/`0x247C3A` before the fenced decode ever runs, and the hook chain runs BEFORE
# `FUN_00247BF8`, so without that the stock copy wins every tick and the rows keep reading the
# config byte's 0xFF sentinel (the 25.5 V). `silence_writers` only accepts a site that
# `slotsource` proves is a store to this very cell, and only a 2-byte store it can replace with
# a 2-byte `nop` -- same-size, guarded on the instruction's meaning, not just its bytes.

_NOP_T1 = bytes([0x00, 0xBF])


def existing_display_cell(data: bytes, slot: int, width: int) -> DisplayCell:
    """The cell a stock readout already names, as a `DisplayCell` a routine may write. Refuses
    a slot whose variable type does not match `width` (the panel would read the wrong width),
    or whose pointer is not in GuiCode RAM. `tier` is 0: not an allocation."""
    if width not in _TYPE_FOR_WIDTH:
        raise ReadoutError(f"width must be 1 or 2 bytes, got {width!r}")
    cells = status_map.cell_table(data)
    if not 0 <= slot < len(cells):
        raise ReadoutError(f"slot {slot} is outside the cell table (0..{len(cells) - 1})")
    addr = cells[slot]
    if not (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH):
        raise ReadoutError(f"slot {slot} points at {addr:#x}, not GuiCode RAM")
    gs, _ = guidata.guidata_extent(data)
    vtype = data[gs + celltables.type_table_off(data) + slot]
    if vtype != _TYPE_FOR_WIDTH[width]:
        raise ReadoutError(
            f"slot {slot} has variable type {vtype}, but a {width}-byte readout needs type "
            f"{_TYPE_FOR_WIDTH[width]}; feeding it would make FUN_00251E38 read the wrong width")
    return DisplayCell(slot, addr, width, 0, False)


def stock_writers(data: bytes, cell: DisplayCell) -> list:
    """Runtime addresses of every GuiCode store that names `cell.addr` directly."""
    from . import slotsource
    src = slotsource.slot_source(data, cell.slot)
    return sorted(src.writes) if src is not None else []


def silence_writers(data: bytes, cell: DisplayCell, sites: list) -> tuple[bytes, list]:
    """Replace each 2-byte stock store to `cell` at `sites` (runtime addresses) with a 2-byte
    `nop`. Every site must be one `slotsource` lists as a WRITE to this cell's address, and must
    be a 2-byte `str*` -- anything else is refused before a byte moves. Returns
    `(image, edits)` with `(file_off, old, new, note)` tuples; the image is NOT resealed."""
    writers = stock_writers(data, cell)
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    out = bytearray(data)
    edits = []
    for site in sites:
        if site not in writers:
            raise ReadoutError(
                f"{site:#x} is not a store to cell {cell.slot} ({cell.addr:#x}); the stock "
                f"writers are {[hex(w) for w in writers]}")
        off = sec.offset + (site - base)
        old = bytes(out[off:off + 2])
        texts = [t for _, t in thumb.disassemble(old, site)]
        if not texts or not texts[0].startswith("str"):
            raise ReadoutError(
                f"{site:#x} is not a 2-byte store ({texts[:1]}); only a 2-byte store can become a nop")
        out[off:off + 2] = _NOP_T1
        thumb.assert_disassembles_to(_NOP_T1, site, ["nop"])
        edits.append((off, old, _NOP_T1,
                      f"silence stock `{texts[0]}` at {site:#x} -- the per-tick writer of cell "
                      f"{cell.slot} ({cell.addr:#x}) that would overwrite the fed value"))
    return bytes(out), edits
