"""Bind a G12 output pin's live status to an on-screen widget, without reusing an existing
control's binding.

The chain this module edits is `MCF-FORMAT.md` §5.4c, `proven-from-code` end to end:

```
CAN DC_DIMMER_STATUS_3, instance = pin
  |  LCD FUN_0021C760 scans the variable table (GuiData +0x2A70); entry+4 = instance filter
  v  writes slot[var] at 0x20007FC8 + var*2
  |  GuiCode FUN_00247BF8 reads ldrsh [r4, #2*var]; cmp/beq picks a colour master
  v  str -> colour cell (0x2003xxxx) - strb -> bool cell
  |  GuiData +0x44BC8 maps a table index -> that cell address (§5.4b)
  v  a widget whose fillA mode-7 index / value_slot names that index paints it (§6.0a)
```

`var` is ONE number throughout: receive-filter entry index == slot index == the `2*var`
immediate in GuiCode (verified across all 136 bridge blocks).

**What is editable, and what is not.** GuiCode's per-var blocks are straight-line code with
hard-coded cell addresses, so the *set* of bridged vars is fixed by the firmware — a var with no
block cannot be given one, and this module refuses rather than inventing one. Everything else is
a same-size edit and is exposed here:

| joint | function | size |
|---|---|---|
| which pin feeds a var | `set_filter_instance` | 1 byte |
| which cell a table index names | `index_cell` | 4 bytes |
| off / on colour of a var | `set_state_colour` | 2 bytes (a GuiCode `imm8`) |

Widget-side edits (a record's `fillA` index and `value_slot`) belong to the record and live in
`newbutton`/`records`; `bind_pin` returns the table index those fields should be set to.

Every function returns `(new_data, edits)` with `relocate.Edit`s, reseals, and asserts which
checksums that reseal was allowed to change — the same contract as `patch`/`newbutton`.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import celltables, guidata, memo, patch, relocate, status_map, theme

DGN_DC_DIMMER_STATUS_3 = guidata.DGN_DC_DIMMER_STATUS_3

# The instance-filter byte of an 8-byte variable-table entry (`MCF-FORMAT.md` §5.4a: "+4 =
# expected value for the filter; 0xFF disables it").
FILTER_INSTANCE_FIELD = 4
FILTER_DISABLED = 0xFF

CELL_TABLE_OFF = status_map.CELL_TABLE_OFF        # GuiData-relative base of the +0x44BC8 table
CELL_TABLE_LEN = status_map.CELL_TABLE_LEN
SRAM_LOW, SRAM_HIGH = status_map.SRAM_LOW, status_map.SRAM_HIGH

# The G12 drives 44 outputs (`g12_config`: 8 relay + 12 half-bridge + 24 high-current).
PIN_MIN, PIN_MAX = 1, 44

GUIDATA_CHECKSUM = "section-checksum:Lyra 13v6 GuiData"
GUICODE_CHECKSUM = "section-checksum:Lyra 13v6 GuiCode"


class StatusBindError(ValueError):
    """A premise this module refuses to guess past.

    Raised rather than returning a status: every caller here is building a firmware image that
    will be flashed to a coach, and a silently-wrong colour cell or filter byte is a bricked
    screen or a control that reports another pin's state.
    """


# --------------------------------------------------------------------------------------
# reading the binding
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PinBinding:
    """Everything the file says about one pin's status path."""
    pin: int
    var: int | None                     # receive-filter entry index (== slot index)
    bridged: bool                       # GuiCode FUN_00247BF8 has a clean 2-dest block for it
    colour_cell: int | None             # SRAM word the block writes
    colour_index: int | None            # +0x44BC8 index naming that cell (None = unindexed)
    bool_cell: int | None
    bool_index: int | None
    commanded: bool                     # some located control already commands this pin
    colour_consumers: list = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """True when a widget can paint this pin's state with record edits alone — the var is
        bridged AND its colour cell already has a table index to name."""
        return self.bridged and self.colour_index is not None

    @property
    def needs_index(self) -> bool:
        """Bridged, but no `+0x44BC8` entry names its colour cell: `index_cell` a spare first."""
        return self.bridged and self.colour_index is None


def pin_bindings(data: bytes, build: str = "136") -> dict:
    """`{pin: PinBinding}` for every G12 output that a `DC_DIMMER_STATUS_3` filter entry watches.

    A pin watched by more than one entry keeps the **bridged** one (a duplicate entry with no
    GuiCode block cannot drive anything); ties break to the lowest var, deterministically.
    """
    rows = status_map.backbone(data, build)
    commanded = status_map.commanded_instances(data)
    best: dict = {}
    for r in rows:
        if r.instance is None:
            continue
        cand = PinBinding(
            pin=r.instance, var=r.var, bridged=r.bridged,
            colour_cell=r.colour_cell,
            colour_index=(r.colour_idx[0] if r.colour_idx else None),
            bool_cell=r.bool_cell,
            bool_index=(r.bool_idx[0] if r.bool_idx else None),
            commanded=r.instance in commanded,
            colour_consumers=list(r.colour_consumers))
        prev = best.get(r.instance)
        if prev is None or (cand.usable, cand.bridged) > (prev.usable, prev.bridged):
            best[r.instance] = cand
    return best


def free_pins(data: bytes, build: str = "136") -> list:
    """G12 outputs no located control commands, sorted — the candidates for a new button.

    Restricted to `PIN_MIN..PIN_MAX`: the filter table also watches instances well above 44
    (other RV-C devices on the bus), and those are not outputs this panel can drive.
    """
    return sorted(p for p, b in pin_bindings(data, build).items()
                  if not b.commanded and PIN_MIN <= p <= PIN_MAX)


def spare_cell_indices(data: bytes) -> list:
    """`+0x44BC8` indices referenced by **no** record's colour field and no `value_slot`.

    Such an index is dead: a widget reaches the table only by naming an index (§5.4b), so an
    index nothing names contributes nothing on screen and may be repointed. `inferred-from-data`
    — it is a whole-corpus scan of record references, and nothing is *known* to iterate the
    table rather than index it, but that has not been proven from code.
    """
    con = status_map.object_consumers(data)
    used = set(con.colour) | set(con.vslot)
    return [i for i in range(CELL_TABLE_LEN) if i not in used]


def cell_index(data: bytes, cell: int) -> int | None:
    """The lowest `+0x44BC8` index naming `cell`, or None."""
    tbl = status_map.cell_table(data)
    for i, c in enumerate(tbl):
        if c == cell:
            return i
    return None


# --------------------------------------------------------------------------------------
# joint 1: which pin feeds a var (GuiData, 1 byte)
# --------------------------------------------------------------------------------------

def _reseal_with(out: bytes, edits: list, expected: list) -> tuple:
    """Reseal and append the checksum edits, refusing any reseal that touched a section this
    edit had no business changing."""
    positions = {f.label: f.position for f in patch.checksum_fields(out)}
    resealed, changed = patch.reseal(out)
    if changed != expected:
        raise StatusBindError(f"reseal changed {changed}, expected exactly {expected}")
    for label in changed:
        pos = positions[label]
        edits.append(relocate.Edit(pos, out[pos:pos + 2], resealed[pos:pos + 2],
                                   "checksum", f"resealed {label}"))
    return resealed, edits


def set_filter_instance(data: bytes, var: int, instance: int) -> tuple:
    """Point receive-filter entry `var` at RV-C `instance` — a 1-byte edit of entry `+4`.

    The whole bridge follows the var, not the pin: after this, `var`'s GuiCode block (and so its
    colour and bool cells, and every widget naming them) tracks `instance`.

    Refuses a var whose entry is not a `DC_DIMMER_STATUS_3` subscription, and refuses to write
    the `0xFF` filter-disabled sentinel as if it were a pin.
    """
    vt = guidata.variable_table(data)
    if not 0 <= var < len(vt):
        raise StatusBindError(f"var {var} out of range [0, {len(vt)})")
    entry = vt[var]
    if entry.dgn_low != DGN_DC_DIMMER_STATUS_3:
        raise StatusBindError(
            f"var {var} subscribes to DGN {entry.dgn_low:#06x}, not DC_DIMMER_STATUS_3 "
            f"({DGN_DC_DIMMER_STATUS_3:#06x}); retargeting it would change what it decodes")
    if not PIN_MIN <= instance <= PIN_MAX:
        raise StatusBindError(
            f"instance {instance} outside the G12's {PIN_MIN}..{PIN_MAX} outputs")

    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset + FILTER_INSTANCE_FIELD
    old = data[off:off + 1]
    if old[0] == FILTER_DISABLED:
        raise StatusBindError(
            f"var {var}'s instance filter is {FILTER_DISABLED:#x} (disabled); it matches every "
            f"instance, so setting a pin here changes its meaning -- do that deliberately")
    out = bytearray(data)
    out[off] = instance
    edits = [relocate.Edit(off, old, bytes([instance]), "filter-instance",
                           f"variable-table entry {var} +{FILTER_INSTANCE_FIELD} instance "
                           f"{old[0]} -> {instance}")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


# --------------------------------------------------------------------------------------
# joint 1b: which DGN an entry subscribes to at all (GuiData, <=6 bytes)
# --------------------------------------------------------------------------------------

# `FUN_0021A618` is the DGN-registration function, and the boot loop calls it once per
# variable-table entry (`FUN_0021BE50` @`0x0021C03E`, bounded by the count at `state+0x108`).
# So an entry's DGN bytes are the ONLY thing that has to change to subscribe to something new --
# registration happens on the next boot with no further edit. The registry dedupes and refuses
# past a hard cap:
#
#     0x0021A62A  cmp r2, #0x53      83 distinct DGNs
#     0x0021A62C  beq 0x0021A656     -> returns 0, and no caller checks it
#
# Overflow is SILENT -- no error, no log, no boot failure, the DGN simply never arrives -- which
# is why `registered_dgns` exists and why callers should assert against `REGISTRY_CAP`.
REGISTRY_CAP = 0x53

# 18 bits, not 16: `FUN_0021C760` @`0x0021C92C` is `ubfx r0,r0,#0,#0x12` against `ubfx
# r2,r2,#8,#0x12` on the CAN id (`MCF-FORMAT.md` S5.4a, LCD Finding 11).
DGN_MASK = 0x3FFFF

# The extraction modes `FUN_0021C760`'s `tbb` @`0x0021C99A` actually dispatches. Modes 0 and 3
# (and 8..15) branch to the loop increment instead: such an entry matches its DGN and then writes
# no slot and no staleness deadline. That is legal -- 13.6 ships 14 of them -- but it is never
# what a caller *retargeting* an entry means, so this refuses rather than silently building one.
LIVE_MODES = {1: "whole byte", 2: "u16 le at n,n+1",
              4: "bits 0-1", 5: "bits 2-3", 6: "bits 4-5", 7: "bits 6-7"}

FILTER_COMPARE_FIELD = 5
_MODE_U16 = 2


def registered_dgns(data: bytes) -> set[int]:
    """The distinct DGNs the boot loop registers from the variable table.

    This is the set `FUN_0021A618` accumulates, so `len()` is what `REGISTRY_CAP` bounds --
    *from this table*. Four other call sites (`0x21A2F6`, `0x21A782`, `0x21A86C`, `0x21B222`)
    also register, at least one of them `0x1FED9`, and they are NOT counted here. Treat a
    result of N as "at least N registered", and leave margin under the cap accordingly.
    """
    return {struct.unpack_from("<I", v.raw, 0)[0] & DGN_MASK
            for v in guidata.variable_table(data)}


def retarget_filter(data: bytes, var: int, dgn: int, mode: int, payload_byte: int, *,
                    instance: int = FILTER_DISABLED, compare_byte: int | None = None,
                    refresh: int | None = None) -> tuple:
    """Point receive-filter entry `var` at a different DGN -- a same-size edit of `+0..+5`.

    Everything downstream follows the var: its slot, its GuiCode bridge block, and every widget
    naming the cells that block writes. So this re-sources an existing on-screen indicator from
    a new DGN without touching the widget at all.

    `mode` and `payload_byte` say how to extract the value (`LIVE_MODES`); `refresh` defaults to
    the entry's existing staleness class. `instance` defaults to `0xFF` (filter disabled, match
    every frame); giving a real one requires `compare_byte` too, since the pair is what
    `FUN_0021C760` tests.

    Refuses to exceed `REGISTRY_CAP`, because that failure is silent on hardware.
    """
    vt = guidata.variable_table(data)
    if not 0 <= var < len(vt):
        raise StatusBindError(f"var {var} out of range [0, {len(vt)})")
    if not 0 <= dgn <= DGN_MASK:
        raise StatusBindError(f"DGN {dgn:#x} is not an 18-bit value")
    if mode not in LIVE_MODES:
        raise StatusBindError(
            f"extraction mode {mode} is not one of the modes FUN_0021C760 dispatches "
            f"({sorted(LIVE_MODES)}); 0 and 3 match the DGN and then write nothing")
    if not 0 <= payload_byte <= 7:
        raise StatusBindError(f"payload byte {payload_byte} outside an 8-byte payload")
    if mode == _MODE_U16 and payload_byte > 6:
        raise StatusBindError(
            f"mode 2 reads payload[{payload_byte}] and payload[{payload_byte + 1}]; "
            f"{payload_byte + 1} is off the end of an 8-byte payload")
    if instance != FILTER_DISABLED and compare_byte is None:
        raise StatusBindError(
            f"instance filter {instance:#04x} needs `compare_byte` -- entry +5 says which "
            f"payload byte +4 is tested against")

    entry = vt[var]
    old = bytes(entry.raw)
    if refresh is None:
        refresh = (old[2] >> 2) & 3
    if not 0 <= refresh <= 3:
        raise StatusBindError(f"refresh class {refresh} outside 0..3")

    new = bytearray(old)
    new[0] = dgn & 0xFF
    new[1] = (dgn >> 8) & 0xFF
    # byte 2 packs three fields: DGN bits 16-17, refresh class, extraction mode.
    new[2] = ((dgn >> 16) & 0x03) | (refresh << 2) | (mode << 4)
    # byte 3's bits 3-7 are undecoded; preserve them rather than zeroing what we cannot explain.
    new[3] = (old[3] & 0xF8) | payload_byte
    new[4] = instance
    if compare_byte is not None:
        new[5] = compare_byte

    out = bytearray(data)
    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset
    out[off:off + len(new)] = bytes(new)
    if len(out) != len(data):
        raise StatusBindError("retarget_filter must be same-size")

    after = registered_dgns(bytes(out))
    if len(after) > REGISTRY_CAP:
        raise StatusBindError(
            f"this build would register {len(after)} distinct DGNs from the variable table, "
            f"over the firmware's cap of {REGISTRY_CAP}; FUN_0021A618 refuses silently")

    edits = [relocate.Edit(off, old, bytes(new), "filter-dgn",
                           f"variable-table entry {var}: DGN {entry.dgn_low:#06x} -> {dgn:#07x}, "
                           f"mode {mode} ({LIVE_MODES[mode]}) of payload[{payload_byte}]")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


def pin_filter_instance(data: bytes, var: int, instance: int, *,
                        expect_dgn: int | None = None, expect_compare: int = 0x00,
                        from_instance: int | None = None) -> tuple:
    """Switch ON receive-filter entry `var`'s masked payload compare -- ONE byte at entry `+4`.

    `0xFF` there is the sentinel `FUN_0021C760` tests at `0x0021C93E`; it skips the compare
    entirely, so the entry accepts its DGN from **every** device on the bus. Writing a real value
    activates the check that entry `+5` already describes -- its low nibble picks which payload
    byte to test (`and r0,r0,#0xf` @`0x0021C97A`, `ldrb r0,[r0,#4]` @`0x0021C984`), its high
    nibble expands to a 2-bit-pair mask (nibble 0 => mask `0xff`, `0021C976`-`0021C980`).

    This is the deliberate counterpart to `set_filter_instance`, which refuses exactly this case
    ("it matches every instance, so setting a pin here changes its meaning") and only handles
    `DC_DIMMER_STATUS_3` pins anyway. Here the DGN is arbitrary and the caller states which one it
    expects, so a table that shifted under us fails loudly instead of pinning the wrong entry.

    `+5` is NOT written: keeping this to one byte is what makes it reviewable, and every entry
    worth pinning already carries the byte selector it needs. If `+5` has to change too, that is a
    `retarget_filter` call, not this.

    With `from_instance` given this is a re-pin instead: it moves an entry from one device to
    another. The entry must currently compare against exactly `from_instance` (else it raises,
    naming the current value), the disabled-sentinel refusal is skipped, and `instance` is
    written. Without it the behaviour is as above.

    The obvious failure mode is worth stating: a pinned entry whose device changes instance goes
    **silent**, where the unpinned one would have shown the wrong device. That is the better
    failure, but it is a choice.
    """
    vt = guidata.variable_table(data)
    if not 0 <= var < len(vt):
        raise StatusBindError(f"var {var} out of range [0, {len(vt)})")
    if not 0 <= instance < FILTER_DISABLED:
        raise StatusBindError(
            f"instance {instance:#04x} outside 0x00..0xFE; {FILTER_DISABLED:#04x} is the "
            f"disable sentinel, so this cannot be used to turn a filter back off")

    entry = vt[var]
    old = bytes(entry.raw)
    dgn = struct.unpack_from("<I", old, 0)[0] & DGN_MASK
    if expect_dgn is not None and dgn != expect_dgn:
        raise StatusBindError(
            f"var {var} subscribes to {dgn:#07x}, not the expected {expect_dgn:#07x}")
    if from_instance is not None:
        if old[FILTER_INSTANCE_FIELD] != from_instance:
            raise StatusBindError(
                f"var {var} compares against {old[FILTER_INSTANCE_FIELD]:#04x}, not the "
                f"expected from_instance {from_instance:#04x}")
    elif old[FILTER_INSTANCE_FIELD] != FILTER_DISABLED:
        raise StatusBindError(
            f"var {var} already compares against {old[FILTER_INSTANCE_FIELD]:#04x}; this op "
            f"exists to ENABLE a disabled filter, so re-pointing a live one must be explicit")
    if old[FILTER_COMPARE_FIELD] != expect_compare:
        raise StatusBindError(
            f"var {var}'s compare field +5 is {old[FILTER_COMPARE_FIELD]:#04x}, not the expected "
            f"{expect_compare:#04x}; it selects the payload byte and mask, so pinning without "
            f"agreeing on it would test the wrong byte")
    mode = old[2] >> 4
    if mode not in LIVE_MODES:
        raise StatusBindError(
            f"var {var}'s extraction mode is {mode}, which FUN_0021C760's tbb skips -- the entry "
            f"writes no slot, so narrowing what it matches changes nothing observable")

    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset + FILTER_INSTANCE_FIELD
    out = bytearray(data)
    out[off] = instance
    edits = [relocate.Edit(off, old[FILTER_INSTANCE_FIELD:FILTER_INSTANCE_FIELD + 1],
                           bytes([instance]), "filter-instance",
                           f"variable-table entry {var} ({dgn:#07x}) +{FILTER_INSTANCE_FIELD}: "
                           f"{'any instance' if from_instance is None else f'{from_instance:#04x}'} -> payload[{old[FILTER_COMPARE_FIELD] & 0xF}] == "
                           f"{instance:#04x}")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


# --------------------------------------------------------------------------------------
# joint 1c: which mode extracts a subscription (GuiData, 1 byte high nibble)
# --------------------------------------------------------------------------------------

# The extraction-mode arms that actually write a slot. 0 and 3 are the tbb's skip arms
# and 8-15 fall to the same `bhi` (MCF-FORMAT.md §5.4a, dispatch at 0x0021C99A), so
# setting one would silently stop the subscription updating.
LIVE_EXTRACT_MODES = (1, 2, 4, 5, 6, 7)


def set_extract_mode(data: bytes, var: int, mode: int) -> tuple:
    """Rewrite variable-table entry `var`'s extraction mode — the high nibble of `+2`.

    A one-byte edit that preserves the low nibble (bits 2-3 are the refresh class,
    selecting the 2 s / 10 s / 30 s / none staleness timeout; bits 0-1 belong to the
    18-bit DGN). Use it to widen a byte subscription to the u16 a packed register needs:
    mode 1 reads `payload[n]`, mode 2 reads `payload[n] | payload[n+1] << 8`.

    Returns `(sealed, edits)`. Raises `StatusBindError` on an unknown var or a mode that
    writes no slot.
    """
    if mode not in LIVE_EXTRACT_MODES:
        raise StatusBindError(
            f"extraction mode {mode} is not one of the live arms {LIVE_EXTRACT_MODES}; "
            f"modes 0, 3 and 8-15 skip the entry entirely and would stop var {var} "
            f"updating its slot")
    entries = {v.index: v for v in guidata.variable_table(data)}
    if var not in entries:
        raise StatusBindError(f"no variable-table entry with index {var}")
    entry = entries[var]

    # `Variable.offset` is GUIDATA-RELATIVE (var 289 -> 0x3378 == 0x2A70 + 289*8), so it
    # must be rebased onto the section start to address the file. Verified this session.
    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset + 2
    old = data[off:off + 1]
    was = data[off] >> 4
    new_byte = (data[off] & 0x0F) | (mode << 4)
    out = bytearray(data)
    out[off] = new_byte
    edits = [relocate.Edit(off, old, bytes([new_byte]), "var-extract-mode",
                           f"var {var} extraction mode {was} -> {mode}")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


# --------------------------------------------------------------------------------------
# joint 2: which cell a table index names (GuiData, 4 bytes)
# --------------------------------------------------------------------------------------

def index_cell(data: bytes, index: int, cell: int) -> tuple:
    """Point `+0x44BC8` entry `index` at SRAM `cell` — a 4-byte edit.

    Use it to give a bridged-but-unindexed var a table index a widget can name. `index` must be
    a `spare_cell_indices` member: repointing an index some record already paints from would
    silently recolour that widget.
    """
    row_count = celltables.row_count(data)
    if not 0 <= index < row_count:
        raise StatusBindError(f"index {index} out of range [0, {row_count})")
    if not SRAM_LOW <= cell < SRAM_HIGH:
        raise StatusBindError(
            f"cell {cell:#x} outside the GuiCode SRAM working area "
            f"[{SRAM_LOW:#x},{SRAM_HIGH:#x})")
    spare = spare_cell_indices(data)
    if index not in spare:
        raise StatusBindError(
            f"index {index} is referenced by a record; repointing it would recolour that "
            f"widget. Spare indices: {spare}")

    gs, _ = guidata.guidata_extent(data)
    off = gs + celltables.cell_table_off(data) + index * 4
    old = data[off:off + 4]
    out = bytearray(data)
    struct.pack_into("<I", out, off, cell)
    edits = [relocate.Edit(off, old, bytes(out[off:off + 4]), "cell-table-entry",
                           f"+0x44BC8[{index}] {struct.unpack('<I', old)[0]:#010x} -> "
                           f"{cell:#010x}")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


# --------------------------------------------------------------------------------------
# joint 2b: which type a variable slot has (GuiData, 1 byte)
# --------------------------------------------------------------------------------------

# The value-formatter's type codes (FUN_00251E38's ten-way dispatch at 0x00251E58:
# ldrb / ldrsb / ldrh / ldrsh / ldr / vldr.32 -- GUICODE.md Finding 17 + the type
# dispatch note). 0 = bool/u8, 3 = u16 (what a numeric setpoint readout uses).
VARIABLE_TYPES = tuple(range(0, 11))
# NOT used by the Water Pump Auto Off readout, deliberately: its spare cell is already type 1
# (a single `ldrb`), which is the exact width the per-tick routine writes with `strb`, and a
# spike proved type 1 renders identically to type 3 for that single byte -- so widening the
# formatter's read over that byte would be pure hazard for no benefit. `recipe_ops.py`'s
# `add_pump_auto_off.verify` asserts `set_variable_type` was never called on that slot.
VARIABLE_TYPE_U16 = 3


def set_variable_type(data: bytes, index: int, vtype: int) -> tuple:
    """Set variable-table entry `index`'s TYPE byte (`+0x4492C`) — a one-byte edit.

    The companion to `index_cell`, which repoints the same entry's data pointer
    (`+0x44BC8`). A `wtype-8` numeric readout formats its value by this type, so a spare
    index adopted for a numeric readout needs both: the pointer at the cell, and the
    type set to `VARIABLE_TYPE_U16`.

    `index` must be a `spare_cell_indices` member — re-typing an index some record
    already reads would silently change that widget's formatting.
    """
    if vtype not in VARIABLE_TYPES:
        raise StatusBindError(
            f"variable type {vtype} is not one of the formatter's codes {VARIABLE_TYPES}")
    row_count = celltables.row_count(data)
    if not 0 <= index < row_count:
        raise StatusBindError(f"index {index} out of range [0, {row_count})")
    spare = spare_cell_indices(data)
    if index not in spare:
        raise StatusBindError(
            f"index {index} is referenced by a record; re-typing it would change that "
            f"widget's formatting. Spare indices: {spare}")

    _dptr, type_off = guidata.find_variable_tables(data)
    gs, _ = guidata.guidata_extent(data)
    off = gs + type_off + index
    old = data[off:off + 1]
    out = bytearray(data)
    out[off] = vtype
    edits = [relocate.Edit(off, old, bytes([vtype]), "variable-type",
                           f"+{type_off:#x}[{index}] type {data[off]} -> {vtype}")]
    return _reseal_with(bytes(out), edits, [GUIDATA_CHECKSUM])


# --------------------------------------------------------------------------------------
# joint 3: a var's off / on colour (GuiCode, 2 bytes)
# --------------------------------------------------------------------------------------

def _disasm(code: bytes, base: int):
    """Every instruction in `code`, resyncing 2 bytes on after each data island.

    Capstone desyncs on the literal pools interleaved through GuiCode; a single linear pass
    silently drops whole functions (it found none of the four cell loads before this resync was
    added). Mirrors `theme._trace_constant_stores`'s loop.
    """
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    pos = 0
    while pos < len(code):
        end = pos
        for ins in md.disasm(code[pos:], base + pos):
            end = ins.address - base + ins.size
            yield ins
        if end <= pos:
            end = pos + 2
        pos = end


def _literal_target(pc: int, disp: int) -> int:
    """Address a PC-relative load at `pc` reads: `Align(pc,4) + 4 + disp`."""
    return (pc & ~3) + 4 + disp


# The slot value that means "output on" in every GuiCode block examined (its branch loads the
# same 0x2003a1a0 cyan master on 2-way blocks). 3-way blocks add state 4, a fault/unavailable
# branch — which is why states are keyed by their actual `cmp` immediate rather than by
# position: taking "the first branch" labelled var 22's state-4 RED as its on-colour.
ON_STATE = 1
OFF_STATE = "off"                      # the fall-through path — no cmp matched


@dataclass(frozen=True)
class StateLoad:
    """One `ldr rX,[pc,#imm]` in a var's block that selects a state's colour master."""
    state: object              # OFF_STATE, or the int the block compares the slot against
    addr: int                  # address of the ldr
    imm8: int                  # its current imm8 (target = Align(addr,4)+4+imm8*4)
    master: int                # SRAM master cell the pool word names
    rgb: tuple | None          # that master's RGB888 constant, if resolvable


def _guicode(data: bytes):
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    return section, base, data[section.offset:section.offset + section.length]


def _word(code: bytes, base: int, addr: int):
    off = addr - base
    return struct.unpack_from("<I", code, off)[0] if 0 <= off <= len(code) - 4 else None


@memo.content_cached(copy=None)
def _code_index(code: bytes, base: int) -> tuple:
    """One resyncing pass over the section, memoised on its content: `(literal_loads, branches)`
    where `literal_loads` is `((addr, pool word), ...)` for every PC-relative `ldr` and
    `branches` is `{target: (branch addr, ...)}` for every immediate jump. `state_loads` used to
    redo this pass (~1 s) per var; a recolour of N pins now pays for it once."""
    import capstone
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC
    loads: list = []
    branches: dict = {}
    for ins in _disasm(code, base):
        if (ins.mnemonic.startswith("ldr") and len(ins.operands) == 2
                and ins.operands[1].type == ARM_OP_MEM
                and ins.operands[1].mem.base == ARM_REG_PC):
            word = _word(code, base, _literal_target(ins.address, ins.operands[1].mem.disp))
            loads.append((ins.address, word))
        elif ins.group(capstone.CS_GRP_JUMP) and ins.operands and \
                ins.operands[0].type == ARM_OP_IMM:
            branches.setdefault(ins.operands[0].imm, []).append(ins.address)
    return tuple(loads), {k: tuple(v) for k, v in branches.items()}


def state_loads(data: bytes, var: int, build: str = "136") -> dict:
    """`{OFF_STATE | <cmp value>: StateLoad}` for `var`'s GuiCode block.

    Located structurally, not by address: find the `ldr rX,[pc,#..]` whose pool word holds the
    var's **colour cell**; the block's shape (verified on cells 145/160/138/313) is

        ldr rA,[pc,#M]     <- the master pointer     (the fall-through path = OFF_STATE)
        ldr rB,[rA]
        ldr rC,[pc,#C]     <- the colour cell        (the anchor this finds)
        str rB,[rC]

    so the off master load sits 4 bytes before the anchor. Each state branch ends `b <anchor>`,
    so its master load is 4 bytes before that branch; the state it serves is read from the
    `cmp rX,#N` two bytes before the branch (`cmp` narrow + `beq.w`). Keying by that immediate
    rather than by branch order matters: 3-way blocks compare 4 *before* 1, so "the first
    branch" is the fault colour, not the on colour.

    A state whose shape does not match is omitted rather than guessed.
    """
    bridge = status_map.load_bridge(build)
    if var not in bridge:
        raise StatusBindError(
            f"var {var} has no clean GuiCode bridge block; its colour is not firmware-driven "
            f"and cannot be recoloured here")
    cell = bridge[var]["colour_cell"]
    _section, base, code = _guicode(data)
    masters = theme._trace_constant_stores(
        code, base, {a: a for a in theme._pcrel_sram_addrs(code, base)})

    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

    literal_loads, branches = _code_index(code, base)
    anchors = [addr for addr, word in literal_loads if word == cell]

    if len(anchors) != 1:
        raise StatusBindError(
            f"expected exactly one GuiCode load of var {var}'s colour cell {cell:#x}, "
            f"found {len(anchors)}")
    anchor = anchors[0]

    def branch_state(block_start: int):
        """Which slot value selects the state block beginning at `block_start`.

        The block is reached by a `beq` from the dispatch, and *ends* with a branch back to the
        anchor -- so the `cmp` that chose it is not near the back-branch at all, it is two bytes
        before the forward `beq`. Follow that link: find a branch targeting `block_start`, then
        read the `cmp rX,#N` immediately before it. None if that is not the shape.
        """
        for beq in branches.get(block_start, []):
            ins = next(_disasm(code[beq - 2 - base:beq - base], beq - 2), None)
            if ins is None or ins.mnemonic != "cmp" or ins.size != 2:
                continue
            if len(ins.operands) == 2 and ins.operands[1].type == ARM_OP_IMM:
                return ins.operands[1].imm
        return None

    def read_load(addr: int, state):
        """Decode `ldr rX,[pc,#imm]` at `addr`; None unless it is the 16-bit literal form."""
        ins = next(_disasm(code[addr - base:addr - base + 4], addr), None)
        if ins is None or ins.size != 2 or not ins.mnemonic.startswith("ldr"):
            return None
        if (len(ins.operands) != 2 or ins.operands[1].type != ARM_OP_MEM
                or ins.operands[1].mem.base != ARM_REG_PC):
            return None
        disp = ins.operands[1].mem.disp
        master = _word(code, base, _literal_target(addr, disp))
        if master is None or not SRAM_LOW <= master < SRAM_HIGH:
            return None
        v = masters.get(master)
        rgb = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF) if v is not None else None
        return StateLoad(state=state, addr=addr, imm8=disp // 4, master=master, rgb=rgb)

    out = {}
    off = read_load(anchor - 4, OFF_STATE)
    if off is not None:
        out[OFF_STATE] = off
    for br in branches.get(anchor, []):
        block_start = br - 4                       # ldr master; ldr value; b <anchor>
        st = branch_state(block_start)
        if st is None or st in out:
            continue
        load = read_load(block_start, st)
        if load is not None:
            out[st] = load
    return out


def _resolve_state(state):
    """Map the caller's friendly name onto a `state_loads` key."""
    if state in ("off", OFF_STATE):
        return OFF_STATE
    if state == "on":
        return ON_STATE
    if isinstance(state, int):
        return state
    raise StatusBindError(
        f"state must be 'off', 'on', or an int cmp value; got {state!r}")


def colour_masters(data: bytes) -> dict:
    """`{rgb: [master SRAM addresses]}` — every single-constant master the theme-init writes.

    These are the only colours a state can be given: `set_state_colour` repoints a load at an
    existing master, it never writes a new constant (writing one would need a free pool word and
    would change every cell fed by that master).
    """
    _section, base, code = _guicode(data)
    masters = theme._trace_constant_stores(
        code, base, {a: a for a in theme._pcrel_sram_addrs(code, base)})
    out: dict = {}
    for addr, v in masters.items():
        out.setdefault(((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF), []).append(addr)
    for k in out:
        out[k] = sorted(out[k])
    return out


def set_state_colour(data: bytes, var: int, state: str, rgb: tuple,
                     build: str = "136") -> tuple:
    """Give `var`'s `state` ("off" or "on") the colour `rgb` — a 2-byte GuiCode edit.

    Repoints that one `ldr`'s `imm8` at a pool word holding a master that already carries `rgb`.
    The master constants themselves are never touched, so every other cell fed by either master
    keeps its colour — the property that makes this safe. Only colours the firmware already
    defines are reachable; `colour_masters` lists them.

    Raises if no master carries `rgb`, or if no pool word holding one is within the 16-bit Thumb
    LDR-literal range (`Align(pc,4)+4+imm8*4`, `imm8 <= 255`) of this instruction.
    """
    key = _resolve_state(state)
    loads = state_loads(data, var, build)
    if key not in loads:
        raise StatusBindError(
            f"var {var}'s GuiCode block has no recognised {key!r} colour load "
            f"(it has {sorted(loads, key=str)})")
    load = loads[key]

    wanted = tuple(rgb)
    candidates = colour_masters(data).get(wanted)
    if not candidates:
        raise StatusBindError(
            f"no theme master holds RGB {wanted}; only firmware-defined colours are reachable")

    _section, base, code = _guicode(data)
    # Every pool word this 16-bit LDR could name, nearest first: Align(pc,4)+4+imm8*4.
    origin = (load.addr & ~3) + 4
    choice = None
    for imm8 in range(256):
        w = _word(code, base, origin + imm8 * 4)
        if w in candidates:
            choice = imm8
            break
    if choice is None:
        raise StatusBindError(
            f"no pool word holding a master for RGB {wanted} is within LDR-literal range of "
            f"{load.addr:#x} (searched {origin:#x}..{origin + 255 * 4:#x}); masters at "
            f"{[hex(c) for c in candidates]}")
    if choice == load.imm8:
        return data, []                                  # already that colour

    section, base, code = _guicode(data)
    file_off = section.offset + (load.addr - base)
    old = data[file_off:file_off + 2]
    if old[1] & 0xF8 != 0x48:
        raise StatusBindError(
            f"instruction at {load.addr:#x} is {old.hex()}, not a 16-bit Thumb LDR (literal); "
            f"refusing to rewrite its immediate")
    out = bytearray(data)
    out[file_off] = choice
    edits = [relocate.Edit(
        file_off, old, bytes(out[file_off:file_off + 2]), "guicode-state-colour",
        f"var {var} {state} colour master {load.master:#010x} {load.rgb} -> "
        f"{_word(code, base, origin + choice * 4):#010x} {wanted}")]
    return _reseal_with(bytes(out), edits, [GUICODE_CHECKSUM])


# --------------------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------------------

def bind_pin(data: bytes, pin: int, *, off_colour: tuple | None = None,
             on_colour: tuple | None = None, index: int | None = None,
             build: str = "136") -> tuple:
    """Make `pin`'s live status paintable, and return the table index a widget should name.

    Returns `(new_data, colour_index, edits)`. `colour_index` is what to write into a box
    record's `fillA` mode-7 field **and** its `value_slot` (they must be the same cell —
    `MCF-FORMAT.md` §6.0a); `newbutton.retarget_indicator` performs those record edits.

    Steps, each skipped when already satisfied so the function is idempotent:
      1. resolve the pin's var and refuse if the firmware has no bridge block for it;
      2. if its colour cell has no `+0x44BC8` index, point a spare index at it;
      3. set the off and/or on colour if asked.

    It deliberately does NOT retarget any filter's instance byte: on 13.6 every pin 1-44 is
    already watched, so doing so would silently steal another pin's watcher. Call
    `set_filter_instance` explicitly when that trade is what you want.
    """
    b = pin_bindings(data, build).get(pin)
    if b is None:
        raise StatusBindError(f"no DC_DIMMER_STATUS_3 filter entry watches pin {pin}")
    if not b.bridged:
        raise StatusBindError(
            f"pin {pin} (var {b.var}) has no GuiCode bridge block, so the firmware never "
            f"decodes its status into a colour cell. Point a bridged var at this pin with "
            f"set_filter_instance instead (it costs that var's own pin)")
    if b.commanded:
        raise StatusBindError(
            f"pin {pin} is already commanded by a located control; binding a second widget to "
            f"it would give two controls one output")

    edits: list = []
    colour_index = b.colour_index
    if colour_index is None:
        spare = spare_cell_indices(data)
        if index is None:
            if not spare:
                raise StatusBindError(
                    "pin's colour cell is unindexed and no spare +0x44BC8 index remains")
            index = spare[0]
        data, e = index_cell(data, index, b.colour_cell)
        edits += e
        colour_index = index

    for state, rgb in (("off", off_colour), ("on", on_colour)):
        if rgb is None:
            continue
        data, e = set_state_colour(data, b.var, state, rgb, build)
        edits += e

    return data, colour_index, edits
