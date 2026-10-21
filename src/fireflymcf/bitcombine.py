"""Per-tick routine folding single bits from SEVERAL receive slots into ONE display cell.

`readout.build` copies one receive slot into one display cell, which is all a numeric readout
needs. A **state-switch** needs the opposite shape: one cell carrying every bit the switch has to
distinguish, because a `wtype-6` switch reads exactly one `value_slot` and each arm it selects is
the whole picture it draws.

That asymmetry is not a style preference, it is what makes the drawing correct. A switch repaints
only the arm it selects, so an arm that leaves something out leaves the previous arm's pixels on
the glass -- the v31 "Batt Heat" button shipped two independent switches and both failure modes
the owner reported (2026-09-22) are that: the body switch's arm painted its capsule over the
sibling 'Batt Heat' caption and nothing redrew it, and the glyph switch's off arm drew nothing at
all, so the red glyph stayed lit until the page was navigated away and back. Combining the bits
into one cell lets ONE switch own the whole button, so every arm draws every pixel and no state
can be left stale.

The routine is the same shape as `readout.build` -- `push {r0-r3,lr}`, work, `pop.w`, `b.w` to the
displaced per-tick function -- and is chained onto `followchannel`'s hook the same way. Per source
it reads the u16 receive slot, skips it when the slot still holds `STALE_SENTINEL` (nothing has
arrived on the bus yet), masks the bit that means "on", and adds that source's weight to an
accumulator held in `r1`. The accumulator starts at zero, so a cell fed by this routine reads a
genuine "everything off" before the first frame rather than a stale pattern.

Nothing is trusted on inspection: `build` runs `thumb.assert_disassembles_to` over the emitted
blob, re-reading every instruction with capstone and comparing it to the text this module claims
it authored, exactly as `readout.build` and `followchannel.build` do.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import followchannel, readout, theme, thumb


class BitCombineError(Exception):
    """A combine routine could not be resolved or built (bad source, cell or weight)."""


# The accumulator is a u16 cell, and `STALE_SENTINEL` must stay reachable as "no value yet" for
# anything else reading the cell, so the sum of all weights is kept well below it.
MAX_TOTAL = 0xFF


@dataclass(frozen=True)
class BitSource:
    """One watched receive slot: `weight` is added to the combined value when `slot_addr`'s
    extracted field is not stale and has any bit of `mask` set."""
    slot_addr: int
    mask: int
    weight: int


@dataclass(frozen=True)
class CombinePlan:
    sources: tuple[BitSource, ...]
    cell: readout.DisplayCell
    hook_at: int
    hook_old: bytes
    tail_target: int


def resolve(data: bytes, *, sources, cell: readout.DisplayCell) -> CombinePlan:
    """Validate `sources` against `cell` and read the current detour state off `data`.

    Like `readout.resolve`, the hook is re-read from the image being built, so a routine resolved
    on an image that already carries one chains ahead of it instead of replacing it.
    """
    sources = tuple(sources)
    if not sources:
        raise BitCombineError("a combine routine needs at least one source")
    if cell.width != 2:
        raise BitCombineError(
            f"combined state cell must be a u16 (width 2), got width {cell.width}")
    seen = set()
    total = 0
    for src in sources:
        if not 1 <= src.mask <= 0xFF:
            raise BitCombineError(f"mask must be 1..0xFF, got {src.mask:#x}")
        if not 1 <= src.weight <= 0xFF:
            raise BitCombineError(f"weight must be 1..0xFF, got {src.weight:#x}")
        if src.weight & (src.weight - 1):
            raise BitCombineError(
                f"weight {src.weight:#x} is not a power of two; overlapping weights make the "
                "combined value ambiguous, so the switch arms could not name a state")
        if src.weight in seen:
            raise BitCombineError(f"weight {src.weight:#x} is used by two sources")
        seen.add(src.weight)
        total += src.weight
    if total > MAX_TOTAL:
        raise BitCombineError(f"weights total {total:#x}, over MAX_TOTAL {MAX_TOTAL:#x}")
    hook_old, tail = followchannel.hook_state(data)
    return CombinePlan(sources, cell, followchannel.HOOK_OFF, hook_old, tail)


def build(plan: CombinePlan, code_load_addr: int) -> tuple[bytes, list]:
    """The per-tick fold: `cell = sum(weight for each live source whose masked field is set)`.

    Same stream/label/pool shape as `readout.build`, assembled by `thumb.Routine`; the blob is
    verified instruction for instruction by `thumb.assert_disassembles_to` before it is returned.
    """
    if code_load_addr % 4:
        raise BitCombineError(f"code_load_addr {code_load_addr:#x} is not 4-aligned")

    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    pool = {"slot%d" % i: s.slot_addr for i, s in enumerate(plan.sources)}
    pool["cell"] = plan.cell.addr
    r = thumb.Routine(pool, error=BitCombineError)
    emit = r.emit

    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
    for i, src in enumerate(plan.sources):
        nxt = "next%d" % i
        r.ldr_lit(0, "slot%d" % i)
        emit(2, lambda h, s: (thumb.ldrh_imm(3, 0, 0), "ldrh r3, [r0]"))
        emit(4, lambda h, s: (thumb.movw(2, followchannel.STALE_SENTINEL),
                              f"movw r2, #{followchannel.STALE_SENTINEL:#x}"))
        emit(2, lambda h, s: (thumb.cmp_reg(3, 2), "cmp r3, r2"))
        emit(2, lambda h, s, n=nxt: (thumb.bcc_n(h, s(n), "eq"), f"beq #{s(n):#x}"))
        emit(2, lambda h, s, m=src.mask: (thumb.movs_imm(2, m), f"movs r2, #{imm(m)}"))
        # `ands` sets the flags, so the branch below tests the masked bit with no extra `cmp`.
        emit(2, lambda h, s: (thumb.ands_reg(3, 2), "ands r3, r2"))
        emit(2, lambda h, s, n=nxt: (thumb.bcc_n(h, s(n), "eq"), f"beq #{s(n):#x}"))
        emit(2, lambda h, s, w=src.weight: (thumb.adds_imm8(1, w), f"adds r1, #{imm(w)}"))
        r.label(nxt)
    r.ldr_lit(0, "cell")
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))
    return r.assemble(code_load_addr, readout.ENTRY_OFF)


def detour_edit(plan: CombinePlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """`(hook_offset, stock_bytes, new_bl)` -- identical contract to `readout.detour_edit`."""
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    return plan.hook_at, plan.hook_old, thumb.bl(hook_runtime, code_load_addr + readout.ENTRY_OFF)
