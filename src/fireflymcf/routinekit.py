"""The primitive surface user ops build on: per-tick routines, slots, SRAM, registers, frames.

Every routine-authoring op in the library does the same handful of things: chain a per-tick
routine onto the one per-tick hook (`hooklocate`; 13.6: GuiCode+0xEE3C), find the receive slot
a register lands in, find SRAM nothing else reaches, build an OPTIONS config-write frame and
hand it to the host sender.
This module exposes those steps, wrapping the library's own functions (no built-in op calls
through here, so their bytes are unchanged) so a user op can do them without reaching into
library internals. `plugin/skills/write-new-op/USER-OPS.md` ("Primitives") is the reference;
the surface is semver-stable.

Two kinds of name:

* **Primitives** (`PRIMITIVES`): `f(data: bytes, ...) -> (data_out, result)`, deterministic in
  their arguments, which are JSON-able or bytes. A user op calls them through
  `userop.Editor.call(name, ...)`, which records the call so the engine can replay it. A
  read-only primitive returns `data` unchanged.
* **Helpers**: pure functions on bytes or on a `thumb.Routine` (`config_frame`, `emit_*`,
  `assemble_tick`), and `install_tick`, the two-phase tick install written against an `Editor`.

Installing a tick routine is two primitive calls, because the routine's bytes depend on where it
will load and what it must tail-branch to, and an `Editor` call cannot carry a callable:

    plan = ed.call("tick_install_plan")                        # read-only
    blob, entry = build(plan.code_load_addr, plan.tail_target)  # the op assembles its routine
    ed.call("install_tick_routine", blob, entry, note,
            code_load_addr=plan.code_load_addr, tail_target=plan.tail_target)

`install_tick(ed, build, note)` does exactly that. The second call refuses if the image no longer
matches the plan (another growth in between), so a routine is never installed at an address it
was not assembled for.

Addresses: GuiCode runs at `theme.GUICODE_BASE_DEFAULT` (0x00240000 on 11.23, 13.6 and 14.7),
the base `followchannel.hook_state`, `chain_from_hook` and every `detour_edit` use. The plan
refuses an image whose GuiCode descriptor (`theme.guicode_base`) says otherwise, so the one rule
holds wherever this module runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from . import (colourcell, container, followchannel, growsection, guidata, optionrow, patch,
               pumpauto, register, repaint, sramreach, theme, thumb)
from .recipe import RecipeError

__all__ = ["PRIMITIVES", "RoutineKitError", "TickPlan", "TickInstall", "tick_install_plan",
           "install_tick_routine", "install_tick", "slot_addr", "resolve_register",
           "fresh_sram", "find_config_toggle", "config_frame", "emit_prologue", "emit_tail",
           "emit_send_frame", "assemble_tick", "thumb", "PROLOGUE_TEXT", "EPILOGUE_TEXT",
           "GuiCodeView", "guicode", "find_tag", "tick_chain", "own_colour_cell",
           "own_colour_cells", "ColourCell", "repaint_on_cell", "own_repaint_trigger",
           "Repaint", "RepaintTrigger"]


class RoutineKitError(RecipeError):
    """A primitive refused. Codes: STALE_PLAN, BAD_ROUTINE, BAD_HOOK, NO_SRAM, NO_SUCH_REGISTER,
    AMBIGUOUS_REGISTER, AMBIGUOUS_TOGGLE, BAD_FRAME, BAD_SLOT, NO_TAG, AMBIGUOUS_TAG,
    `own_colour_cell`'s (`colourcell.ColourCellError`'s codes) and `repaint_on_cell`'s
    (`repaint.RepaintError`'s).
    `RoutineKitError(msg)` (one
    argument, as `thumb.Routine(error=...)` raises it) is BAD_ROUTINE."""

    def __init__(self, code: str, message: str | None = None, **kw):
        if message is None:
            code, message = "BAD_ROUTINE", code
        super().__init__(code, message, **kw)


# --------------------------------------------------------------------------------------------
# The per-tick hook
# --------------------------------------------------------------------------------------------

# The transparency contract every link on the chain obeys (`followchannel.hook_state`): save
# r0-r3 and lr, never write r4-r7, restore and tail-branch to whatever the hook reached before.
PROLOGUE_TEXT = "push {r0, r1, r2, r3, lr}"
EPILOGUE_TEXT = "pop.w {r0, r1, r2, r3, lr}"


@dataclass(frozen=True)
class TickPlan:
    """Where the next tick routine loads and what it must tail-branch to."""
    code_load_addr: int     # runtime address of the blob's first byte (the GuiCode append point)
    tail_target: int        # the routine's `b.w` target: the hook's current callee
    hook_at: int            # GuiCode-section offset of the hook `bl` (13.6: 0xEE3C)
    hook_old: bytes         # its current 4 bytes


@dataclass(frozen=True)
class TickInstall:
    load_addr: int          # runtime address of the blob's first byte
    entry_addr: int         # load_addr + entry_offset: what the hook now calls
    tail_target: int        # what the routine tail-branches to (the previous callee)
    hook_old: bytes         # the hook's bytes before
    hook_new: bytes         # and after


def _plan(data: bytes) -> TickPlan:
    sec = theme.guicode_section(data)
    if theme.guicode_base(sec) != theme.GUICODE_BASE_DEFAULT:
        raise RoutineKitError(
            "BAD_HOOK", f"GuiCode maps at {theme.guicode_base(sec):#x}, not "
            f"{theme.GUICODE_BASE_DEFAULT:#x}; the hook chain is proven only at the default base")
    try:
        hook_at, hook_old, tail = followchannel.hook_site(data)
    except followchannel.FollowChannelError as e:
        raise RoutineKitError("BAD_HOOK", str(e)) from e
    load = theme.GUICODE_BASE_DEFAULT + sec.length - 4
    # The located hook proves the chain; the image alone cannot say where stock's GuiCode ended
    # (`hooklocate.stock_append_point` needs the stock image), so only the alignment is checked.
    if load % 4:
        raise RoutineKitError("BAD_HOOK", f"GuiCode append point {load:#x} is not 4-aligned")
    return TickPlan(load, tail, hook_at, hook_old)


# --------------------------------------------------------------------------------------------
# Readers (helpers, not primitives: they never change an image)
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GuiCodeView:
    """The GuiCode section of an image, at the runtime address the panel runs it from."""
    load_addr: int          # runtime address of body[0] (the base every primitive uses)
    offset: int             # file offset of body[0]
    body: bytes             # the section's bytes, trailing checksum included

    def at(self, addr: int, n: int) -> bytes:
        """`n` bytes at runtime address `addr`."""
        i = addr - self.load_addr
        if not 0 <= i <= len(self.body) - n:
            raise RoutineKitError("BAD_ROUTINE", f"{addr:#x}+{n} is outside GuiCode "
                                  f"({self.load_addr:#x}+{len(self.body):#x})")
        return self.body[i:i + n]

    def u32(self, addr: int) -> int:
        return int.from_bytes(self.at(addr, 4), "little")


def guicode(data: bytes) -> GuiCodeView:
    """The image's GuiCode at its runtime base. Refuses (BAD_HOOK) a descriptor that maps it
    anywhere but the base every primitive uses, as `tick_install_plan` does."""
    sec = theme.guicode_section(data)
    if theme.guicode_base(sec) != theme.GUICODE_BASE_DEFAULT:
        raise RoutineKitError(
            "BAD_HOOK", f"GuiCode maps at {theme.guicode_base(sec):#x}, not "
            f"{theme.GUICODE_BASE_DEFAULT:#x}")
    return GuiCodeView(theme.GUICODE_BASE_DEFAULT, sec.offset,
                       bytes(data[sec.offset:sec.offset + sec.length]))


def find_tag(data: bytes, tag: bytes) -> int:
    """Runtime address of the one occurrence of `tag` in GuiCode: how a test or a later `check`
    finds what an op put in its routine's prefix (`assemble_tick(..., prefix=tag + ...)`).
    Refuses NO_TAG (absent) and AMBIGUOUS_TAG (more than once: choose a longer tag)."""
    tag = bytes(tag)
    if len(tag) < 4:
        raise RoutineKitError("BAD_ROUTINE", "a tag needs at least 4 bytes")
    g = guicode(data)
    at = g.body.find(tag)
    if at < 0:
        raise RoutineKitError("NO_TAG", f"no {tag!r} in GuiCode")
    if g.body.find(tag, at + 1) >= 0:
        raise RoutineKitError("AMBIGUOUS_TAG", f"{tag!r} occurs more than once in GuiCode")
    return g.load_addr + at


def tick_chain(data: bytes) -> list[int]:
    """The per-tick chain the hook reaches: each appended routine's entry in call order, newest
    first, ending with the stock paint function (`followchannel.chain_from_hook`)."""
    try:
        return followchannel.chain_from_hook(data)
    except followchannel.FollowChannelError as e:
        raise RoutineKitError("BAD_HOOK", str(e)) from e


def tick_install_plan(data: bytes) -> tuple[bytes, TickPlan]:
    """Read-only. The plan the next `install_tick_routine` call must be assembled against."""
    return data, _plan(data)


def _check_link(data: bytes, entry: int, tail_target: int) -> None:
    """The routine at runtime address `entry` obeys the chain contract: it starts with
    `push {r0-r3, lr}`, and its first `b.w` (what `chain_from_hook` follows) is preceded by
    `pop.w {r0-r3, lr}` and reaches `tail_target`."""
    sec = theme.guicode_section(data)
    base = theme.GUICODE_BASE_DEFAULT
    end = base + sec.length - 4
    code = data[sec.offset + entry - base:sec.offset + end - base]
    dis = thumb.disassemble(code, entry)
    if not dis or dis[0][0] != entry or dis[0][1] != PROLOGUE_TEXT:
        raise RoutineKitError("BAD_ROUTINE", f"routine at {entry:#x} does not start with "
                              f"{PROLOGUE_TEXT!r} (got {dis[0][1] if dis else 'nothing'!r})")
    for i, (_, text) in enumerate(dis):
        if text.startswith("b.w #"):
            target = int(text[5:], 16)
            if target != tail_target:
                raise RoutineKitError(
                    "BAD_ROUTINE", f"routine at {entry:#x}: its first b.w reaches {target:#x}, "
                    f"not the tail target {tail_target:#x}")
            if i == 0 or dis[i - 1][1] != EPILOGUE_TEXT:
                raise RoutineKitError(
                    "BAD_ROUTINE", f"routine at {entry:#x}: its tail b.w is not preceded by "
                    f"{EPILOGUE_TEXT!r}")
            return
    raise RoutineKitError("BAD_ROUTINE", f"routine at {entry:#x} has no b.w tail-branch")


def install_tick_routine(data: bytes, blob: bytes, entry_offset: int, note: str, *,
                         code_load_addr: int, tail_target: int) -> tuple[bytes, TickInstall]:
    """Append `blob` to GuiCode and retarget the located per-tick hook (13.6: GuiCode+0xEE3C) to
    `code_load_addr + entry_offset`.

    `code_load_addr`/`tail_target` are the `tick_install_plan` values `blob` was assembled
    against; the call refuses (STALE_PLAN) if the image no longer gives them. The blob's entry
    must obey the chain contract (`_check_link`); the hook's bytes are asserted before they are
    replaced, and afterwards `followchannel.chain_from_hook` must list the new entry first and
    every previous link after it."""
    blob = bytes(blob)
    plan = _plan(data)
    if (plan.code_load_addr, plan.tail_target) != (code_load_addr, tail_target):
        raise RoutineKitError(
            "STALE_PLAN", f"{note}: the routine was assembled for load {code_load_addr:#x} / "
            f"tail {tail_target:#x}, but the image now gives load {plan.code_load_addr:#x} / "
            f"tail {plan.tail_target:#x}; call tick_install_plan again and reassemble")
    if not blob or len(blob) % 4:
        raise RoutineKitError("BAD_ROUTINE", f"{note}: blob length {len(blob)} is not a "
                              "positive multiple of 4")
    if not (0 <= entry_offset < len(blob)) or entry_offset % 2:
        raise RoutineKitError("BAD_ROUTINE", f"{note}: entry offset {entry_offset} is not an "
                              f"even offset inside the {len(blob)}-byte blob")
    before = followchannel.chain_from_hook(data)
    try:
        grown, load_off, _ = growsection.grow_section(data, "GuiCode", blob)
    except growsection.GrowSectionError as e:
        raise RoutineKitError("BAD_ROUTINE", f"{note}: {e}") from e
    if theme.GUICODE_BASE_DEFAULT + load_off != code_load_addr:
        raise RoutineKitError("STALE_PLAN", f"{note}: the blob loaded at "
                              f"{theme.GUICODE_BASE_DEFAULT + load_off:#x}, not {code_load_addr:#x}")
    entry = code_load_addr + entry_offset
    _check_link(grown, entry, tail_target)

    gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
    file_off = gcs.offset + plan.hook_at
    if grown[file_off:file_off + 4] != plan.hook_old:
        raise RoutineKitError(
            "BAD_HOOK", f"{note}: GuiCode+{plan.hook_at:#x} holds "
            f"{grown[file_off:file_off + 4].hex()}, not the planned {plan.hook_old.hex()}")
    hook_new = thumb.bl(theme.GUICODE_BASE_DEFAULT + plan.hook_at, entry)
    sealed, _ = patch.reseal(grown[:file_off] + hook_new + grown[file_off + 4:])

    after = followchannel.chain_from_hook(sealed)
    if after != [entry] + before:
        raise RoutineKitError("BAD_HOOK", f"{note}: the chain after install is "
                              f"{[hex(a) for a in after]}, not the new entry then "
                              f"{[hex(a) for a in before]}")
    return sealed, TickInstall(code_load_addr, entry, tail_target, plan.hook_old, hook_new)


def install_tick(ed, build: Callable[[int, int], tuple[bytes, int]], note: str) -> TickInstall:
    """The two-phase install through an `Editor`: `build(code_load_addr, tail_target)` returns
    `(blob, entry_offset)` (`assemble_tick` returns exactly that)."""
    plan = ed.call("tick_install_plan")
    blob, entry_offset = build(plan.code_load_addr, plan.tail_target)
    return ed.call("install_tick_routine", bytes(blob), int(entry_offset), note,
                   code_load_addr=plan.code_load_addr, tail_target=plan.tail_target)


# --------------------------------------------------------------------------------------------
# Slots, registers, SRAM, toggles
# --------------------------------------------------------------------------------------------

def slot_addr(data: bytes, var_index: int) -> tuple[bytes, int]:
    """Read-only. The SRAM halfword the receive filter writes for variable-table entry
    `var_index`, with the index-split base (<= 255 at 0x20007FC8 + 2i, > 255 at
    0x20008000 + 2i; `register.status_slot_addr`, FUN_0021C760, PC.44)."""
    n = len(guidata.variable_table(data))
    if not isinstance(var_index, int) or not 0 <= var_index < n:
        raise RoutineKitError("BAD_SLOT", f"var index {var_index!r} is outside the variable "
                              f"table (0..{n - 1})")
    return data, register.status_slot_addr(var_index)


def resolve_register(data: bytes, *, dgn, instance: int | None = None,
                     selector: int | None = None, var_index: int | None = None,
                     field: int | None = None) -> tuple[bytes, register.Register]:
    """Read-only. The ONE existing variable-table subscription for `dgn` (a name in
    `register.DGN_NAMES` or a DGN number) filtered by `instance` (per-channel DGNs) or
    `selector` (DGN_CONFIG), `var_index`, and `field` (the entry's source-field number,
    `raw[3] & 7`, for DGNs whose entries share a filter byte). Refuses on none or several."""
    try:
        if field is not None:
            dgn_low = register._dgn_low(dgn)
            want = instance if instance is not None else selector
            hits = [v.index for v in guidata.variable_table(data)
                    if v.dgn_low == dgn_low and (want is None or v.value == want)
                    and (var_index is None or v.index == var_index)
                    and v.raw[3] & 7 == field]
            if len(hits) != 1:
                code = "AMBIGUOUS_REGISTER" if hits else "NO_SUCH_REGISTER"
                raise RoutineKitError(code, f"dgn={dgn_low:#06x} value={want!r} field={field} "
                                      f"matches {len(hits)} entries, not exactly one")
            var_index = hits[0]
        return data, register.resolve_register(data, dgn=dgn, instance=instance,
                                               selector=selector, var_index=var_index)
    except register.RegisterError as e:
        code = "AMBIGUOUS_REGISTER" if "not exactly one" in str(e) else "NO_SUCH_REGISTER"
        raise RoutineKitError(code, str(e)) from e


def fresh_sram(data: bytes, nbytes: int, *,
               taken: Iterable[int] = frozenset()) -> tuple[bytes, int]:
    """Read-only. A 4-aligned address of `nbytes` SRAM that nothing in the image reaches
    (`sramreach.free_runs`, longest run first, as `readout`'s tier 3 and `_isolate_cell`
    allocate) and that avoids every byte address in `taken`.

    The scan sees only what the image references, so two allocations made before the routine
    that uses them is installed must pass the first in `taken` (`list(range(a, a + n))`). Once
    the routine is installed, its pool word makes the cell referenced and later calls avoid it.
    Power-on contents are garbage (the C runtime initialises SRAM only up to 0x2000AD98)."""
    if not isinstance(nbytes, int) or not 1 <= nbytes <= 0x100:
        raise RoutineKitError("NO_SRAM", f"nbytes {nbytes!r} is not in 1..256")
    avoid = set(taken)
    for start, length in sramreach.free_runs(data, min_bytes=nbytes + 3):
        addr = (start + 3) & ~3
        while addr + nbytes <= start + length:
            if not avoid.intersection(range(addr, addr + nbytes)):
                return data, addr
            addr += 4
    raise RoutineKitError("NO_SRAM", f"no free 4-aligned SRAM run fits {nbytes} bytes")


def find_config_toggle(data: bytes, selector: int) -> tuple[bytes, int | None]:
    """Read-only. The group index of the existing config-toggle group on OPTIONS `selector`
    (`optionrow.find_config_toggle`), or None. Refuses (AMBIGUOUS_TOGGLE) on two."""
    try:
        return data, optionrow.find_config_toggle(data, selector)
    except optionrow.OptionRowError as e:
        raise RoutineKitError("AMBIGUOUS_TOGGLE", str(e)) from e


ColourCell = colourcell.ColourCell


def own_colour_cell(data: bytes, object_index: int, field: str, *, record: int | None = None,
                    expect: bytes | None = None, repaint: bool = False
                    ) -> tuple[bytes, ColourCell]:
    """Give one record's `field` ("border" or "fill") of object `object_index` (an object-table
    index, never a screen id) a private run-time colour cell: a new row of the display-cell
    table, on 4 fresh bytes of SRAM, that the field now names in mode 7.

    `record` picks the record within the object (its index in `records.object_records`'s list);
    without it the object must have exactly one record with that field. Fields by widget type
    (`GUICODE.md` Finding 28): wtype 3 and 32 (rounded box, tank well) border = fillA, fill =
    fillB; wtype 33 (solid panel or level fill) fill = fillA. `expect`, the record's bytes as the
    op was written against them, refuses FIRMWARE_MISMATCH on any other bytes.

    Returns `ColourCell`: `addr` (the SRAM word a tick routine stores, `width` 4, `encoding`
    "rgb888", i.e. `0x00RRGGBB`), `cell_index`, and the colour the field drew before
    (`before` (r, g, b) and `before_word`, read from the image as it stands, so after any earlier
    op such as `set_theme`; `before_cell_addr`, for a field that named a row before, is that
    row's SRAM word, which a routine can copy to follow it at run time).

    **The cell is not initialised at boot** (SRAM above `0x2000AD98` is garbage at power-on):
    the op must install a tick routine that, every tick, first stores `before_word` into `addr`
    unconditionally and then, when its condition holds, the new colour over it. The chain runs
    before `FUN_00247BF8` draws anything, so the control never shows the
    garbage (`colourcell`'s docstring has the argument). A new colour shows at the control's
    next repaint, not at the store, unless `repaint` is set: then the object's enrolled
    containers watch the cell too (`repaint_on_cell`), and a store of a different colour
    repaints the object in place in the same tick's live redraw (`ColourCell.repaint` says
    which containers). `repaint` also refuses as `repaint_on_cell` does.

    Changes GuiCode and GuiData (the cell table's grow repoints GuiCode literals): declare both
    in `touches`. Refuses (codes of `colourcell.ColourCellError`): BAD_OBJECT, NO_COLOUR_FIELD,
    AMBIGUOUS_RECORD, UNSUPPORTED_FIELD, UNSUPPORTED_MODE (the field inherits, mode 0/4/5, or is
    the theme default, mode 1), STATEFUL_RECORD (a record on a value slot draws its on/off
    pair), INHERITED_COLOUR (a later record of the object inherits the field's colour, or the
    object ends before any record sets it again),
    FIRMWARE_MISMATCH, NO_DISPLAY_CELL, BAD_EDIT (a relocation failure names GuiData's tail
    headroom), GUIDATA_TOO_LARGE (GuiData would end past `limits.GUIDATA_END_MAX`). For more than one field use `own_colour_cells`: one grow for all of them."""
    try:
        return colourcell.own_colour_cell(data, object_index, field, record=record,
                                          expect=expect, repaint=repaint)
    except colourcell.ColourCellError as e:
        raise RoutineKitError(e.code, str(e)) from e


def own_colour_cells(data: bytes, requests: list, *,
                     repaint: bool = False) -> tuple[bytes, list[ColourCell]]:
    """`own_colour_cell` for several fields with ONE grow of the cell table: each request is
    `[object_index, field]`, `[object_index, field, record]` or a dict with keys `object`,
    `field` and optionally `record`, `expect` and `repaint` (default: the call's `repaint`).
    Returns a `ColourCell` per request, in order,
    on consecutive rows. Every request is checked before anything changes; one refusal
    refuses them all, and the same field twice is BAD_EDIT. Prefer it to one call per field:
    each grow copies the whole table to the end of GuiData."""
    try:
        return colourcell.own_colour_cells(data, requests, repaint=repaint)
    except colourcell.ColourCellError as e:
        raise RoutineKitError(e.code, str(e)) from e


Repaint = repaint.Repaint
RepaintTrigger = repaint.RepaintTrigger


def repaint_on_cell(data: bytes, object_index: int,
                    cell_index: int) -> tuple[bytes, Repaint]:
    """Repaint object `object_index` in place whenever the u32 at display-cell row
    `cell_index` changes: every enrolled wt-5 container that draws the object gets a mode-7
    colour field on the row, and the panel's live redraw re-checks those rows every tick
    (`FUN_002588A8` -> `FUN_002568A0` -> `FUN_00256868`, *proven-from-code*; `repaint`'s
    docstring has the instructions). The container's field is one the object's first record
    sets itself, so the cell's word never reaches a drawn pixel through the container.

    Pass a `ColourCell.cell_index` to repaint when the colour changes (or use
    `own_colour_cell(..., repaint=True)`). Returns `Repaint`: `addr` (the row's SRAM word),
    `watchers` ((parent, record, role) per container bound) and `skipped` (containers of the
    object without the enrol bit; those pages show a change only at their next draw).

    Changes GuiData and GuiCode (each parent object grows 2 bytes and is swapped back in at its
    own index, which shifts GuiCode's literals into later GuiData): declare both.
    Refuses (codes of `repaint.RepaintError`): BAD_OBJECT, BAD_CELL, NO_LIVE_CONTAINER (no
    enrolled container draws the object), UNSUPPORTED_CONTAINER (a container has no mode-0
    field the object's first record sets, or stores coordinates to registers),
    CHILD_INHERITS (the object's first record sets no colour field itself), BAD_EDIT,
    GUIDATA_TOO_LARGE."""
    try:
        return repaint.repaint_on_cell(data, object_index, cell_index)
    except repaint.RepaintError as e:
        raise RoutineKitError(e.code, str(e)) from e


def own_repaint_trigger(data: bytes, object_index: int) -> tuple[bytes, RepaintTrigger]:
    """A private 4-byte cell (a new display-cell row on fresh SRAM, as `own_colour_cell`
    grows) bound to object `object_index` by `repaint_on_cell`: a tick routine that stores a
    word different from the last tick's (`adds` one, say) repaints the object once, for a
    repaint that follows no colour. Garbage at power-on, so the first tick's store may repaint
    once. Changes GuiCode and GuiData (the grow repoints GuiCode literals): declare both.
    Refuses as `repaint_on_cell`, and NO_DISPLAY_CELL when the table cannot grow."""
    try:
        return repaint.own_repaint_trigger(data, object_index)
    except repaint.RepaintError as e:
        raise RoutineKitError(e.code, str(e)) from e


PRIMITIVES: dict = {
    "tick_install_plan": tick_install_plan,
    "install_tick_routine": install_tick_routine,
    "slot_addr": slot_addr,
    "resolve_register": resolve_register,
    "fresh_sram": fresh_sram,
    "find_config_toggle": find_config_toggle,
    "own_colour_cell": own_colour_cell,
    "own_colour_cells": own_colour_cells,
    "repaint_on_cell": repaint_on_cell,
    "own_repaint_trigger": own_repaint_trigger,
}


# --------------------------------------------------------------------------------------------
# Pure helpers: frames and routine building blocks
# --------------------------------------------------------------------------------------------

FUNCTION_WRITE = pumpauto.FUNCTION_WRITE            # 0xD1, absolute set
FUNCTION_DELTA = 0xD2                               # signed delta (13.6's own +1000 on 0xCC)
FUNCTION_READBACK = pumpauto.FUNCTION_READBACK      # 0xD3, read-back request
_TRAILER = {FUNCTION_WRITE: pumpauto.WRITE_TRAILER, FUNCTION_DELTA: pumpauto.WRITE_TRAILER,
            FUNCTION_READBACK: pumpauto.READBACK_TRAILER}


def config_frame(selector: int, value: int, function: int) -> bytes:
    """A 12-byte `0x1FED9` OPTIONS config frame in the pool's wire order (u32 LE CAN id, then
    `FF 96 <selector> 0F <value u16 LE> <function> <trailer>`): `pumpauto.config_frame` with the
    trailer the function carries (0xEA for D1/D2, 0xFF for the D3 read-back, whose value field
    13.6's own records fill with 0xFFFF)."""
    if function not in _TRAILER:
        raise RoutineKitError("BAD_FRAME", f"function {function!r} is not 0xD1, 0xD2 or 0xD3")
    try:
        return pumpauto.config_frame(selector, value, function, _TRAILER[function])
    except pumpauto.PumpAutoError as e:
        raise RoutineKitError("BAD_FRAME", str(e)) from e


def emit_prologue(r: thumb.Routine) -> None:
    """`push {r0, r1, r2, r3, lr}`: a tick routine's first instruction."""
    r.emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), PROLOGUE_TEXT))


def emit_tail(r: thumb.Routine, tail_target: int) -> None:
    """`pop.w {r0, r1, r2, r3, lr}; b.w tail_target`: a tick routine's exit, which must be the
    first `b.w` in the routine (`followchannel.chain_from_hook` follows it)."""
    r.emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), EPILOGUE_TEXT))
    r.emit(4, lambda h, s: (thumb.b_w(h, tail_target), f"b.w #{tail_target:#x}"))


def emit_send_frame(r: thumb.Routine, template_addr: int, *, value_in_r3: bool = False) -> None:
    """Transmit the 12-byte frame at `template_addr` through the host CAN sender: pumpauto's
    send sequence (Finding 7; flashed since 2026-09-04), instruction for instruction.

    `sub sp,#0x14` keeps sp 8-aligned at the `blx` given the prologue's five pushed words; the
    frame is copied to the stack with r0-r2 and `r0 = sp` is the argument. With `value_in_r3`,
    r3 is stored over the frame's value halfword (payload[4..5], frame offset 8) before the send,
    so a D1 write can carry a runtime-computed word. The sender is an AAPCS call: r0-r3 are dead
    after it. Adds the sender pointer and the template address to `r.pool`."""
    tname = f"_frame_{template_addr:08x}"
    r.pool.setdefault(tname, template_addr)
    r.pool.setdefault("_sender", followchannel.SENDER_PTR_ADDR)
    r.emit(2, lambda h, s: (thumb.sub_sp_imm(20), "sub sp, #0x14"))
    r.ldr_lit(0, tname)
    r.emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
    r.emit(2, lambda h, s: (thumb.ldr_imm(2, 0, 4), "ldr r2, [r0, #4]"))
    r.emit(2, lambda h, s: (thumb.str_sp_imm(1, 0), "str r1, [sp]"))
    r.emit(2, lambda h, s: (thumb.str_sp_imm(2, 4), "str r2, [sp, #4]"))
    r.emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 8), "ldr r1, [r0, #8]"))
    r.emit(2, lambda h, s: (thumb.str_sp_imm(1, 8), "str r1, [sp, #8]"))
    r.emit(2, lambda h, s: (thumb.mov_reg(0, 13), "mov r0, sp"))
    if value_in_r3:
        r.emit(2, lambda h, s: (thumb.strh_imm(3, 0, 8), "strh r3, [r0, #8]"))
    r.ldr_lit(2, "_sender")
    r.emit(2, lambda h, s: (thumb.ldr_imm(2, 2, 0), "ldr r2, [r2]"))
    r.emit(2, lambda h, s: (thumb.blx_reg(2), "blx r2"))
    r.emit(2, lambda h, s: (thumb.add_sp_imm(20), "add sp, #0x14"))


def assemble_tick(r: thumb.Routine, code_load_addr: int, *,
                  prefix: bytes = b"") -> tuple[bytes, int]:
    """`(blob, entry_offset)` for `install_tick_routine`: `prefix` (frame templates, say; padded
    to 4) at `code_load_addr`, the routine after it, its pool after that. A template at prefix
    offset k is at runtime address `code_load_addr + k`."""
    prefix = bytes(prefix)
    prefix += b"\x00" * (-len(prefix) % 4)
    blob, _ = r.assemble(code_load_addr, len(prefix))
    return prefix + blob[len(prefix):], len(prefix)
