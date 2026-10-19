"""Which on-screen content is decided by SRAM nothing initialises? -- the audit, 2026-09-03.

`slotsource.never_established` names the widget slots that no instruction writes and that the C
runtime does not initialise either (`raminit`). This module asks the question that matters to
someone using the panel: **which of those slots decide what is drawn**, and on which screens.

The mechanism is the one Finding 63 worked out on a single row. A wt-6 switch keyed on such a
slot resolves its arm by the firmware's own scan (`layout.Screen._switch_child`,
`proven-from-code`): it compares the slot's value against exactly `count` match values and, on no
match, draws the base only if `match[base] == 0xFFFF` -- otherwise it draws **nothing**. So a
switch on a never-established slot has its content chosen by whatever the SRAM cell happened to
hold, and the effect runs **both ways**:

- the value matches no arm, and content that should be there is **missing**;
- the value happens to equal a content arm's match value, and content that is normally absent
  **appears**.

The coach has shown the second: the `Absorb`/`Float` rows on `INVERTER / CHARGER SETTINGS` are
hidden after a firmware update and present after a power cycle, because the flash's warm reset
preserves the cell while a power-on re-rolls it (`BUS-BASELINE.md` PC.56, `GUICODE.md`
"The 210 explained").

**Reachability is the whole point of the audit and is not optional.** 13.6 carries plenty of
switches on orphaned template objects that no screen can display; counting those as findings would
repeat a mistake this project has already made twice -- `cli g12-rules` reading one of four tables,
and the four incomplete groups of which only object 929 turned out to be reachable. So every gate
is walked back to the screen roots (`layout.screen_object_map`, read from the match table, never
computed as `object - 4`) and the ones no screen reaches are reported separately.

**What this does NOT claim.** `never_established` is an argument from absence about the *writer*,
and this project's standing rule is that such an argument is only as good as the guarantee that a
writer would have been found. The guarantee here is decent -- full-coverage disassembly of both
images across all addressing forms, Ghidra's reference database agreeing on the motivating case,
and one instance corroborated by hardware behaviour -- but it is not proof that some mechanism
does not set these cells. A gate listed here is a **candidate**, ranked by how much content rides
on it, not a diagnosed defect.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import guidata, layout, memo, optiongates, slotsource

MATCH_DEFAULT = 0xFFFF


@dataclass(frozen=True)
class Arm:
    """One candidate of a switch, and whether it draws anything."""

    match_value: int
    obj: int
    touch_ids: frozenset
    children: frozenset

    @property
    def draws_content(self) -> bool:
        """True if the arm's subtree carries a control or a container child."""
        return bool(self.touch_ids or self.children)


@dataclass(frozen=True)
class LotteryGate:
    """A switch whose drawn content is decided by a slot nothing establishes."""

    switch_obj: int
    record_offset: int
    value_slot: int
    base: int
    count: int
    has_default: bool
    screens: frozenset
    arms: tuple = field(default_factory=tuple)

    @property
    def reachable(self) -> bool:
        return bool(self.screens)

    @property
    def content_arms(self) -> tuple:
        return tuple(a for a in self.arms if a.draws_content)

    @property
    def at_risk(self) -> bool:
        """The gate can change what the user sees depending on the boot's SRAM."""
        return self.reachable and bool(self.content_arms)

    group_count: int = 0

    @property
    def touch_ids(self) -> frozenset:
        out: set = set()
        for a in self.content_arms:
            out |= a.touch_ids
        return frozenset(out)

    @property
    def controls(self) -> frozenset:
        """Touch ids that are GROUP indices, i.e. real controls that command something.

        A wt-12 record's trailing u16 is matched by the dispatcher against two different id
        spaces: group indices (`< group_count`, a control with bindings) and ACTION ids, which
        are much larger and navigate (the compact-AES gear used `0x7DE`/`0x7F7`). Splitting them
        matters because "12 controls at stake" and "12 navigation targets at stake" are very
        different findings, and the raw touch set does not distinguish them."""
        return frozenset(t for t in self.touch_ids if t < self.group_count)

    @property
    def actions(self) -> frozenset:
        """Touch ids outside the group table: navigation/ACTION leaves, not controls."""
        return frozenset(t for t in self.touch_ids if t >= self.group_count)

    @property
    def rank(self) -> tuple:
        """Sort key, worst first: controls at stake, then content arms, then screens."""
        return (-len(self.controls), -len(self.content_arms), -len(self.actions),
                -len(self.screens), self.value_slot, self.switch_obj)


@memo.content_cached()
def _reachable_screens(data: bytes) -> dict:
    """`{object index: frozenset(screen ids)}` for every object a screen root can reach.

    Descends wt-5 children unconditionally and **every** candidate of a nested switch, because
    any of them may draw depending on slot values -- the same rule `optiongates._subtree` uses.
    """
    from . import options

    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    reach: dict = {}
    for screen, root in layout.screen_object_map(data).items():
        stack, seen = [root], set()
        while stack:
            i = stack.pop()
            if i in seen or not (0 <= i < len(table)):
                continue
            seen.add(i)
            reach.setdefault(i, set()).add(screen)
            for rec in options._own_records(data, gs, ge, table, i):
                if rec.wtype == 5 and rec.child_index is not None:
                    stack.append(rec.child_index)
                elif rec.wtype in (6, 0x2E) and rec.child_index is not None:
                    stack.extend(rec.child_index + k for k in range(rec.count or 0))
    return {k: frozenset(v) for k, v in reach.items()}


@memo.content_cached()
def lottery_gates(data: bytes) -> tuple:
    """Every switch keyed on a never-established slot, worst first.

    Includes gates with an explicit `0xFFFF` default (`has_default`) and gates no screen reaches
    (`reachable` False) so the filtering is visible rather than silent; `at_risk` is the subset
    that can actually change what the panel draws.
    """
    never = {s.slot for s in slotsource.never_established(data)}
    scr = layout.Screen(data)
    if scr.match_table_off is None:
        return ()
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    reach = _reachable_screens(data)

    out = []
    for obj, offset, slot, base, count in optiongates._all_gates(data):
        if slot not in never:
            continue
        arms = []
        for k in range(count):
            idx = base + k
            touch, children = optiongates._subtree(data, gs, ge, table, idx)
            arms.append(Arm(scr._match_value(idx), idx, touch, children))
        out.append(LotteryGate(
            switch_obj=obj, record_offset=offset, value_slot=slot, base=base, count=count,
            has_default=scr._match_value(base) == MATCH_DEFAULT,
            screens=reach.get(obj, frozenset()), arms=tuple(arms),
            group_count=len(guidata.group_table(data))))
    return tuple(sorted(out, key=lambda g: g.rank))


def at_risk(data: bytes) -> tuple:
    """The gates that are reachable from a screen AND have content riding on them."""
    return tuple(g for g in lottery_gates(data) if g.at_risk)


def gates_for_slot(data: bytes, value_slot: int) -> tuple:
    return tuple(g for g in lottery_gates(data) if g.value_slot == value_slot)
