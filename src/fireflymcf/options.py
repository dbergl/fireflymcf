"""The option -> value-slot -> register binding map (GUICODE.md Findings 16-17).

`guidata.find_variable_tables` and `layout.variable_register` close the *display*
side of thread 4 (Findings 16-17): an OPTIONS-page checkbox is a type-6 value-match
switch record (`records.WRecord.value_slot`), and the conditional widget it gates is
some *other* type-6 switch reading the *same* slot. This module walks every
OPTIONS-labelled page to build the checkbox -> slot -> register map, plus a
floorplan value -> name map read the same way.

**The pairing trap this module exists to avoid.** A checkbox's label is not the
"nearest preceding switch" -- on the Go Power!/Renogy tab (obj 1358 in 13.6, obj 1141
in 11.23) the caption record follows a hit-region record, not the switch directly, so
"nearest switch above this label" would still get the right answer there, but
"nearest label above/after some switch" style scans elsewhere in this corpus have
been seen to grab one label for several different switches. What is actually true
of the bytes, checked below in `_iter_option_rows`: on every OPTIONS page in all
three builds a checkbox row is a short run of sibling records bounded by two type-6
switches (or by the end of the object) -- `<switch><label><hit-region>` on the
Floorplans/Main Options/Electrical Options tabs, `<switch><hit-region><label>` on
Go Power!/Renogy -- so pairing a switch with the first label found before the *next*
switch in the same list is precise either way.

A second trap surfaced while testing this on 14.7: an earlier version of
`_iter_option_rows` also recursed into every container reachable from the OPTIONS
header object, on the theory that a build might nest its rows one level deeper. Two
of those containers are decorative (a battery/temperature icon cluster, a Go Power!
numeric +/- adjuster) and are NOT part of the option list, but each has its OWN
internal switch+label pairs for its own purposes -- and recursing into them pulled
those in as if they were options: 14.7 produced spurious bindings named "1", "2" and
"D". Every real row on all three builds is a literal top-level record of the header
object, so `_iter_option_rows` does not recurse at all; see its docstring for the
tradeoff this accepts.

**Cross-build note.** `option_bindings` generalizes cleanly: 13.6 finds three OPTIONS
objects (46, 47, 1358), 11.23 finds one (1141, just "Renogy Battery"), 14.7 finds two
(42, 43, including the shipped `'Starlink Power'` example CLAUDE.md points at). No
`value_slot` number is shared across builds (13.6's Bath Fan is slot 109, 14.7's
equivalent row is a different slot entirely) -- only the *mechanism* is build-stable,
which is why nothing here hardcodes a slot.

`floorplan_options` is a harder cross-build case and says so where it applies: unlike
an OPTIONS checkbox, the floorplan-name switch is not found by a page-header label
(it lives on the Floorplans tab among many unrelated slot-5-reading switches, most of
which show a floorplan-INDEPENDENT caption per candidate -- see the docstring below).
It is identified instead by cross-referencing `guidata.find_floorplan_table` (a wholly
different, already-proven mechanism: an object-table anchor scan), and raises rather
than guessing when that oracle itself has nothing confirmed to check against -- the
documented gap is 11.23, whose file has no FLOORPLAN SELECTION screen at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import guidata, layout, records

OPTIONS_HEADER_LABEL = "OPTIONS"

# How far a type-6 switch's candidate group is scanned before giving up -- the real
# groups measured on the corpus (option rows, floorplan codes) are well under this;
# it only bounds work on a malformed/huge group.
_MAX_GROUP_SCAN = 64
# Recursion depth guard for both the OPTIONS-row walk and the gated-widget harvest --
# well above anything seen (the option pages nest at most two levels deep).
_MAX_RECURSE_DEPTH = 20
# `floorplan_options` accepts a switch as "the" floorplan-name switch only if its
# candidate group reproduces at least this many (value, code) pairs from the
# independent `guidata.find_floorplan_table` oracle. 13.6 scores 10/10, 14.7 scores
# 9/9; nothing else in either file scores above 0. This is a floor, not a fit -- see
# the module docstring for why the check exists at all.
_FLOORPLAN_OVERLAP_MIN = 3


class FloorplanSwitchNotFound(ValueError):
    """No type-6 switch's candidate group reproduces the known floorplan codes.

    Raised rather than returning a best-effort-but-wrong table. The known codes come
    from `guidata.find_floorplan_table`, which needs a confirmed FLOORPLAN SELECTION
    screen; 11.23 has none (`Floorplan.confirmed` is False for every entry there), so
    there is nothing to cross-check a candidate switch against and this always raises
    on that file. That is a documented gap, not a bug: nothing here invents a mapping
    for a build with no independent oracle to validate one against.
    """


@dataclass(frozen=True)
class OptionBinding:
    """One OPTIONS-page checkbox: its label, the value-slot its switch reads, and the
    register that slot resolves to (`layout.variable_register`)."""
    name: str
    value_slot: int
    register: int
    vtype: int
    options_obj: int


def _own_records(data: bytes, gs: int, ge: int, table: list[int],
                 index: int) -> list["records.WRecord"]:
    """The records physically stored at object-table index `index`, or `[]` if the
    index is out of range or its offset is not a real object -- never raises, so
    callers can walk a `child_index` without re-checking bounds themselves."""
    if not (0 <= index < len(table)):
        return []
    off = table[index]
    if not (0 < off < (ge - gs)):
        return []
    try:
        return records.object_records(data, gs + off)
    except (IndexError, ValueError):
        return []


def _first_label(recs: list) -> str | None:
    """The first labelled record's raw label, or None -- the same "first label in an
    object's own record list" convention `guidata.find_screens_by_title` uses for a
    screen's title."""
    for r in recs:
        if r.label is not None:
            return r.label
    return None


def _find_options_pages(data: bytes) -> list[int]:
    """Object-table indices whose header label strips to "OPTIONS".

    On 13.6 this finds three: 46 (Floorplans/Main Options tab), 47 (Electrical
    Options tab) and 1358 (Go Power!/Renogy). 11.23 finds one (1141); 14.7 finds two
    (42, 43). Title is not unique, exactly as `guidata.iter_screens`' docstring notes
    for the byte-heuristic screen walk -- this is the same fact, seen again in the
    byte-exact record decoder.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = []
    for i in range(len(table)):
        label = _first_label(_own_records(data, gs, ge, table, i))
        if label is not None and label.strip() == OPTIONS_HEADER_LABEL:
            out.append(i)
    return out


def _iter_option_rows(data: bytes, gs: int, ge: int, table: list[int], obj_index: int):
    """Yield `(value_slot, label)` for every checkbox row in `obj_index`'s OWN record
    list -- deliberately NOT recursive. See the module docstring for the two sibling
    orders a row takes (`<switch><label><hit-region>` vs `<switch><hit-region><label>`)
    and why a row's label is whichever labelled record sits between its switch and the
    NEXT switch in the same list, not the running last switch seen anywhere.

    **Why this does not recurse into containers, even though an earlier version did.**
    Every OPTIONS page in all three builds carries its real checkbox rows as literal
    top-level records of the header object itself -- confirmed by direct inspection,
    not assumed. The header object's top-level list ALSO carries a few decorative
    containers unrelated to the option list (battery/temperature icon clusters, a
    Go Power! numeric +/- adjuster), and those containers hold their OWN switch+label
    pairs for THEIR OWN purposes -- a battery-status letter ("D"), an adjuster's digit
    captions ("1", "2"). Recursing into them (as a first version of this function did)
    pulled those in as if they were OPTIONS checkboxes: on 14.7 it produced spurious
    entries named "1", "2" and "D" that are not options at all. Restricting the scan
    to the header object's own list removes every one of those false positives and
    still finds every known-truth row on all three builds, because none of the real
    rows are nested. A build that nests its option rows one level deeper would be
    missed by this -- a defensible, documented limitation, preferred here over
    contaminating the map with non-option pairs.
    """
    recs = _own_records(data, gs, ge, table, obj_index)
    n = len(recs)
    for i, rec in enumerate(recs):
        if rec.wtype == 6 and rec.value_slot is not None:
            label = None
            j = i + 1
            while j < n and recs[j].wtype != 6:
                if recs[j].label is not None and recs[j].label.strip():
                    label = recs[j].label.strip()
                    break
                j += 1
            if label is not None:
                yield rec.value_slot, label


def option_bindings(data: bytes) -> list[OptionBinding]:
    """Every OPTIONS-page checkbox, paired to its own switch's value-slot and register.

    Walks each object whose header label strips to "OPTIONS" (`_find_options_pages`),
    pairs each row's switch with its own label (`_iter_option_rows` -- the pairing this
    module's docstring explains), and resolves each slot to a register via
    `layout.variable_register`. The variable-register tables are located once
    (`guidata.find_variable_tables`) and the offsets threaded through every call,
    rather than re-locating per slot (Task 1's noted performance gap).

    De-duplicated by `(name, value_slot)`: a label is not unique across pages (nothing
    in this corpus repeats one, but nothing guarantees it either), and de-duping on the
    slot too means a genuine same-named-same-slot repeat collapses while two rows that
    happen to share a name but read different slots both survive.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    dptr_off, type_off = guidata.find_variable_tables(data)

    seen_pairs: set[tuple[str, int]] = set()
    out: list[OptionBinding] = []
    for page in _find_options_pages(data):
        for slot, label in _iter_option_rows(data, gs, ge, table, page):
            key = (label, slot)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            register, vtype = layout.variable_register(
                data, slot, dptr_table_off=dptr_off, type_table_off=type_off)
            out.append(OptionBinding(name=label, value_slot=slot, register=register,
                                     vtype=vtype, options_obj=page))
    return out


def _switch_group(scr: "layout.Screen", base: int) -> list[tuple[int, int]]:
    """`[(obj_index, match_value)]` for the candidate run starting at `base`.

    Mirrors `Screen._switch_child`'s own boundary rule (a repeated non-0xFFFF match
    value starts the next switch's group) but returns the whole run instead of
    resolving one driven value.
    """
    out = []
    seen_mv: set[int] = set()
    for k in range(_MAX_GROUP_SCAN):
        mv = scr._match_value(base + k)
        if mv is None:
            break
        if mv != 0xFFFF:
            if mv in seen_mv:
                break
            seen_mv.add(mv)
        out.append((base + k, mv))
    return out


def _find_floorplan_switch(data: bytes) -> tuple[int, dict[int, str]]:
    """`(value_slot, {floorplan value: name})` for the floorplan-name switch.

    The shared implementation behind `floorplan_options` (which hands back just the
    name map, matching its documented contract) and `slot_values` (which additionally
    needs the value_slot number itself, to write a `{value_slot: value}` entry for a
    `CoachConfig.floorplan` name -- nothing here hardcodes slot 5; 14.7 uses a
    different slot number for the same mechanism, per the cross-build note below, and
    this function is what makes that true for both callers instead of just the one
    that used to return a dict).

    **Why this needs an oracle, not just the match table.** Dozens of type-6 switches
    in GuiData read the floorplan variable (slot 5 in 13.6) to select floorplan-
    dependent content, but most of them show the SAME caption for every candidate --
    the "Interior"/"Main"/"FRESH" groups measured while building this module all
    repeat one label across their whole run, because the switch is there to pick a
    per-floorplan NUMBER or IMAGE, not to name the floorplan. The genuine floorplan-
    name switch is the one whose candidates spell out distinct codes (RT, RA, RD, ...);
    telling it apart from, say, the Go Power! inverter-model switch (which also has
    dozens of distinct one-per-candidate labels, "MM612", "MM1212", ...) by label count
    alone does not work -- measured on 13.6, the inverter-model switch scores 38
    distinct labels against the floorplan switch's 14.

    So this cross-checks every type-6 switch's candidate group against
    `guidata.find_floorplan_table` -- a wholly different mechanism (an object-table
    anchor scan, `MCF-FORMAT.md`/`guidata.py`'s own docstring) -- and accepts the
    switch whose group reproduces at least `_FLOORPLAN_OVERLAP_MIN` of that oracle's
    CONFIRMED `(value, code)` pairs. Measured: 13.6 scores 10/10 (obj value_slot 5),
    14.7 scores 9/9 (a different slot number); nothing else in either file scores
    above 0, so the threshold is not a close call.

    Raises `FloorplanSwitchNotFound` if `guidata.find_floorplan_table` has no confirmed
    entries to check against (11.23: no FLOORPLAN SELECTION screen at all, so every
    entry there is `confirmed=False`) or if no switch reaches the overlap floor.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    scr = layout.Screen(data)
    if scr.match_table_off is None:
        raise FloorplanSwitchNotFound(
            "no match-value table located in this GuiData (guidata.find_match_table "
            "raised); cannot resolve any switch's candidate group")

    target = {fp.value: fp.code for fp in guidata.find_floorplan_table(data)
              if fp.confirmed}
    if not target:
        raise FloorplanSwitchNotFound(
            "guidata.find_floorplan_table has no confirmed entries for this build (no "
            "FLOORPLAN SELECTION screen was found), so there is no independent oracle "
            "to identify the floorplan-name switch among the many switches that read "
            "the floorplan variable for other purposes. Known gap: 11.23.")

    best: dict[int, str] | None = None
    best_slot: int | None = None
    best_overlap = -1
    for i in range(len(table)):
        for rec in _own_records(data, gs, ge, table, i):
            if not (rec.wtype == 6 and rec.value_slot is not None
                    and rec.child_index is not None and rec.child_index < len(table)):
                continue
            labels: dict[int, str] = {}
            for obj_idx, mv in _switch_group(scr, rec.child_index):
                if mv == 0xFFFF:
                    continue
                label = _first_label(_own_records(data, gs, ge, table, obj_idx))
                if label is None or not label.strip():
                    continue
                # Value 0 is "no floorplan" ONLY where its arm says so: 13.6/14.7's
                # name switches carry the anchor text at mv 0 (and 'Ship Mode' as the
                # DEFAULT arm), while 11.23 inverts both -- mv 0 IS 'Ship Mode', a real
                # pickable floorplan, and the anchor text is the default arm
                # (`observed-on-hardware` 2026-08-30, BUS-BASELINE PC.40). So skip the
                # arm by its label, never by the bare value.
                if mv == 0 and label.strip() == guidata.FLOORPLAN_ANCHOR:
                    continue
                labels.setdefault(mv, label.strip())
            overlap = sum(1 for value, code in target.items()
                         if labels.get(value) == code)
            if overlap > best_overlap:
                best_overlap = overlap
                best = labels
                best_slot = rec.value_slot

    if best is None or best_overlap < _FLOORPLAN_OVERLAP_MIN:
        raise FloorplanSwitchNotFound(
            f"no type-6 switch's candidate group reproduces at least "
            f"{_FLOORPLAN_OVERLAP_MIN} of the {len(target)} known floorplan codes "
            f"(best match: {best_overlap})")
    assert best_slot is not None
    return best_slot, best


def floorplan_options(data: bytes) -> dict[int, str]:
    """`{floorplan value: name}` (e.g. `8: "WD"`), for the coach-config picker.

    Read off the floorplan-name switch where one exists -- all three corpus builds have
    one (11.23's, objects 852/853 on slot 7, was invisible until 2026-08-30: the
    off-by-one label-run base made the oracle unmatchable and the failed match read as
    absence; BUS-BASELINE PC.40 Result 7). A build with no matchable switch falls back
    to `guidata.find_floorplan_table`'s confirmed entries -- the handler-aligned label
    run. Raises `FloorplanSwitchNotFound` only when neither source yields anything.
    """
    try:
        _, names = _find_floorplan_switch(data)
        return names
    except FloorplanSwitchNotFound:
        confirmed = {fp.value: fp.code
                     for fp in guidata.find_floorplan_table(data) if fp.confirmed}
        if not confirmed:
            raise
        return confirmed


def _harvest_labels(data: bytes, gs: int, ge: int, table: list[int], obj_index: int,
                    seen: set | None = None, depth: int = 0) -> set[str]:
    """Every non-empty label reachable from `obj_index`'s subtree."""
    if seen is None:
        seen = set()
    if (depth > _MAX_RECURSE_DEPTH or obj_index in seen
            or not (0 <= obj_index < len(table))):
        return set()
    seen.add(obj_index)
    labels: set[str] = set()
    for rec in _own_records(data, gs, ge, table, obj_index):
        if rec.label is not None and rec.label.strip():
            labels.add(rec.label.strip())
        if rec.wtype in (5, 6, 0x2e) and rec.child_index is not None:
            labels |= _harvest_labels(data, gs, ge, table, rec.child_index, seen,
                                      depth + 1)
    return labels


def widgets_gated_by(data: bytes, value_slot: int) -> list[str]:
    """Labels that appear only under a non-default candidate of a switch reading
    `value_slot` -- best-effort, for annotating the option map (spec's `②`).

    For every type-6 switch anywhere in GuiData whose `value_slot` matches, this
    harvests every label reachable from the switch's base (match-value-0, "default")
    candidate and from each other candidate, and reports the labels a non-default
    candidate adds that the default does not. On 13.6, `widgets_gated_by(data, 109)`
    (Bath Fan) includes "Bath Fan" -- the Climate-page button (obj 247) that only
    renders when the switch is driven away from its default -- matching the worked
    example in `GUICODE.md` Finding 16. Returns `[]` rather than raising if no match
    table is located or no switch reads this slot; this is an annotation aid, not a
    thing `option_bindings` depends on.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    scr = layout.Screen(data)
    if scr.match_table_off is None:
        return []

    found: set[str] = set()
    for i in range(len(table)):
        for rec in _own_records(data, gs, ge, table, i):
            if not (rec.wtype == 6 and rec.value_slot == value_slot
                    and rec.child_index is not None and rec.child_index < len(table)):
                continue
            base = rec.child_index
            default_labels = _harvest_labels(data, gs, ge, table, base)
            for obj_idx, mv in _switch_group(scr, base):
                if mv in (0, 0xFFFF):
                    continue
                found |= _harvest_labels(data, gs, ge, table, obj_idx) - default_labels
    return sorted(found)


@dataclass
class CoachConfig:
    """A named coach configuration: floorplan + options, in the form a human would
    write it down, not the `{value_slot: value}` form `layout.Screen.walk` wants.

    `options` accepts either form the spec allows: a bare list of names (each driven
    to value 1, the common "this option is present" case) or a `{name: value}` dict
    for anything that isn't a plain on/off flag (AGS's slot is a 3-way selector, not
    a bool -- see `option_bindings`'s `vtype` field). `extra_slots` is the escape
    hatch for a raw `{value_slot: value}` override that has no name yet (or never
    will -- e.g. probing a slot found by inspection, not by an OPTIONS-page label).

    All three fields are optional and independent: `slot_values` resolves whichever
    are set and merges the results, with `extra_slots` applied last so it can override
    a name-derived entry if the two ever collide.
    """
    floorplan: str | None = None
    options: dict[str, int] | list[str] = field(default_factory=list)
    extra_slots: dict[int, int] = field(default_factory=dict)
    preview: dict = field(default_factory=dict)
    # `preview` holds RUNTIME sample values for rendering only -- tank fill %, battery voltage,
    # temperature, clock. These are NOT byte-derived: the panel receives them over the bus
    # (`docs/CLOSED-THREADS.md`, thread 5), so they are never decoded from the MCF. They exist purely so a
    # preview render can show a live-looking screen; the render tags anything sourced from here as
    # `src="preview"`, distinct from `"decoded"`. Free-form so new value kinds need no schema change.

    @staticmethod
    def from_json(path: str | Path) -> "CoachConfig":
        """Load a `CoachConfig` from the minimal JSON form documented in the spec,
        e.g. `configs/probe5.json`:
        `{"floorplan": "WD", "options": ["Black Tank", "AGS"], "extra_slots": {"6": 2}}`.

        JSON object keys are always strings, so `extra_slots`' keys are parsed back
        to `int` here (its values -- and `options`' values, in the dict form -- are
        already JSON numbers, so those are left alone). All four top-level keys are
        optional; a missing one falls back to the dataclass default. `preview` (runtime
        display values, not byte-derived -- see the field comment) is loaded verbatim.
        """
        with open(path) as f:
            raw = json.load(f)
        extra_slots = {int(k): v for k, v in raw.get("extra_slots", {}).items()}
        return CoachConfig(floorplan=raw.get("floorplan"),
                           options=raw.get("options", []),
                           extra_slots=extra_slots,
                           preview=raw.get("preview", {}))


def _option_slot_by_name(bindings: list[OptionBinding]) -> dict[str, int]:
    """`{name: value_slot}`, raising `ValueError` if the same name is ever bound to
    two DIFFERENT slots rather than silently keeping whichever iterates last.

    Cannot happen on the three current builds (`option_bindings` de-dups by
    `(name, value_slot)`, and no name repeats across a different slot in any of
    them -- `tests/test_options.py`'s own `_by_name` helper asserts exactly this
    invariant while building its lookup dict for Task 2's tests). Hardened here
    anyway per the global constraint that a name collision must raise loudly, not
    resolve to an arbitrary one of the colliding slots.
    """
    out: dict[str, int] = {}
    for b in bindings:
        if b.name in out and out[b.name] != b.value_slot:
            raise ValueError(
                f"option name {b.name!r} is bound to two different slots: "
                f"{out[b.name]} and {b.value_slot}")
        out[b.name] = b.value_slot
    return out


def _option_value(name: str) -> int:
    """The slot VALUE a list-form option name selects (dict-form configs give values directly).

    A boolean option (Heat Pump, Bunk Accent, ...) selects 1 = enabled. The generator mode is
    the exception: AGS / AES are a RADIO on ONE slot (slot 37 on 13.6 -- three option names bind
    it, `option_bindings` above), and the firmware distinguishes the choices by the VALUE written,
    not by a per-name bool. So a blind `1` for every list option silently rendered "AES Single
    Output" as AGS. The name->value map is byte-authoritative from the OPTIONS-page radio checkboxes
    (obj 47): each row's slot-37 switch turns its dot cyan at exactly one value -- AGS at 1 (base
    139 cand 141), AES at 2 (base 398 cand 399), AES Single Output at 3 (base 144 cand 145). So
    1 = AGS, 2 = AES, 3 = AES Single Output. (Values 2 and 3 both render AES-mode content on
    HOME/ELECTRICAL -- "Engine Not Running", the AES caption -- which is why the HOME obj-903 switch
    alone could not tell 2 from 3; the OPTIONS checkboxes name them.)

    The map is `proven-from-code` (the checkbox switch match values); it is confined to this one
    helper."""
    n = name.upper()
    if n.startswith("AGS"):
        return 1
    if n.startswith("AES"):
        return 3 if "SINGLE" in n else 2
    return 1


def slot_values(data: bytes, config: CoachConfig) -> dict[int, int]:
    """`{value_slot: value}` for `config`, ready for `layout.Screen.walk`'s
    `slot_values` parameter -- the bridge from a human-named `CoachConfig` to the
    register-indexed form the layout engine actually reads.

    Each piece resolves independently and unknown names raise `KeyError` naming the
    offending value and listing everything that WAS available, rather than silently
    dropping it or resolving to a wrong slot:

    - `config.floorplan` (a name like `"WD"`) resolves via the inverse of
      `_find_floorplan_switch`'s name map -- the same structural, non-hardcoded slot
      number `floorplan_options` uses (13.6's is 5; 14.7's is a different number).
    - each name in `config.options` (list or dict form) resolves via `option_bindings`,
      called ONCE and turned into a `name -> value_slot` map (`_option_slot_by_name`,
      which raises `ValueError` rather than silently mis-resolving if a name is ever
      bound to two different slots), not once per name.
    - `config.extra_slots` is merged last, as the documented override/escape hatch.
    """
    out: dict[int, int] = {}

    if config.floorplan is not None:
        try:
            floorplan_slot, names_by_value = _find_floorplan_switch(data)
        except FloorplanSwitchNotFound:
            # No matchable floorplan-name switch (no corpus build hits this since the
            # 11.23 switch was unmasked, but a foreign build may). The slot is still
            # derivable from the receive mechanism -- the config-unpack's cfg-0x00
            # register's unique index in the variable pointer table
            # (`config.floorplan_slot`, validated cross-build: 5/10/7). Only when THAT
            # also fails (no capstone, no proven pair) does a known name degrade to a
            # no-op on the slots; an unknown one always raises so a typo is not
            # silently ignored.
            names_by_value = floorplan_options(data)
            if config.floorplan not in names_by_value.values():
                raise KeyError(
                    f"unknown floorplan {config.floorplan!r}; available: "
                    f"{sorted(names_by_value.values())}")
            from . import config as _config
            try:
                slot = _config.floorplan_slot(data)
            except _config.CapstoneUnavailable:
                slot = None
            if slot is not None:
                value_by_name = {name: value for value, name in names_by_value.items()}
                out[slot] = value_by_name[config.floorplan]
        else:
            value_by_name = {name: value for value, name in names_by_value.items()}
            if config.floorplan not in value_by_name:
                raise KeyError(
                    f"unknown floorplan {config.floorplan!r}; available: "
                    f"{sorted(value_by_name)}")
            out[floorplan_slot] = value_by_name[config.floorplan]

    slot_by_name = _option_slot_by_name(option_bindings(data))
    option_items = (config.options.items() if isinstance(config.options, dict)
                    else [(name, _option_value(name)) for name in config.options])
    for name, value in option_items:
        if name not in slot_by_name:
            raise KeyError(
                f"unknown option {name!r}; available: {sorted(slot_by_name)}")
        out[slot_by_name[name]] = value

    out.update(config.extra_slots)
    return out


# ---------------------------------------------------------------------------
# Slots GuiCode COMPUTES from other slots, rather than receiving.
#
# `slot_values` above turns a coach configuration into the registers the panel is
# *handed* over the bus. Two display slots are not among them: the panel works them out
# for itself, in `FUN_00247BF8`, every time a config payload lands. A render that does
# not do the same arithmetic draws a screen the panel never draws -- which is why this
# lives in the tools layer and not in the studio server, so `cli render` and Studio
# cannot disagree about it.
#
# 1. THE HOME BATTERY BLOCK, slot 6. Object 4's type-6 switch picks between four blocks
#    (901/902/903/904 for values 0/1/2/3) and nothing writes slot 6 but this:
#
#      0x00249CFA  ldr r3,[0x00249e2c]   ; = 0x2003A38C   AGS / AES selector (slot 37)
#      0x00249CFC  ldrb r2,[r3,#0x0]
#      0x00249CFE  subs r3,r2,#0x2       ; AGS in {2,3} (AES, AES Single Output)?
#      0x00249D04  bls.w 0x00249ECC      ;   -> slot 6 = 1  (object 902)
#      0x00249D08  cmp r2,#0x1           ; AGS == 1 (AGS proper)?
#      0x00249D0A  beq.w 0x00249ED4      ;   -> slot 6 = 2  (object 903)
#      0x00249D12  strb r1,[r2,#0x0]     ; else            0  (object 901), r1 = 0
#
#    so object 904 (value 3) is a candidate the firmware never selects.
#
# 2. THE LEFT NAV RAIL, slot 453. Object 974's type-6 switch has `child_base` 385 and
#    count 4; the candidates' match values are 385 = 0xffff (the explicit default),
#    386 = 0, 387 = 2, 388 = 4. So value 0 selects object 386 and everything else falls
#    through the scan to the default, 385. Both are complete rails, authored by Firefly:
#
#      385  rail box + P D Q R L   -- five icons, 12 px gaps (D = the bolt in a circle)
#      386  rail box + P   Q R L   -- four icons, 20 px gaps (no Electrical, respaced)
#
#    The block that computes it follows the battery block above and merges into the
#    same path:
#
#      0x00249D3E  ldr r3,[0x00249e2c]   ; = 0x2003A38C  AGS / AES selector   (slot 37)
#      0x00249D44  bne.w 0x00249F02      ;   any generator mode    -> 453 = 1
#      0x00249D48  ldr r3,[0x00249e24]   ; = 0x2003A4E9  Go Power! count      (slot 39)
#      0x00249D4E  bne.w 0x00249F02      ;   any solar controller  -> 453 = 1
#      0x00249D52  ldr r3,[0x00249e0c]   ; = 0x2003A62B  Progressive Inverter (slot 113)
#      0x00249D58  bne.w 0x00249F02      ;   inverter fitted       -> 453 = 1
#      0x00249D5C  ldr r3,[0x00249e44]   ; = 0x2003A617  the rail selector
#      0x00249D60  strb r2,[r3]          ;   all three zero        -> 453 = 0
#      0x00249F06  strb r2,[r3]          ; the 1 store (r2 = 1, literal at 0x00249f38)
#
#    i.e. the Electrical icon is drawn iff the coach has *something* electrical to show.
#    Those two stores are the complete writer set: 0x2003A617 appears exactly twice in
#    GuiCode's literal pools, in no movw/movt pair, and its only other occurrence in the
#    whole file is slot 453's own entry in GuiData's variable table. `proven-from-code`.
#    (`GUICODE.md` Finding 19 already noted a flag "cleared iff AGS==0 && 0x2003A4E9==0
#    && PI==0"; what it did not say is what READS it, which is why no render drove it.)
#
#    Values 2 and 4 -- objects 387/388, each a further switch on slot 185 whose
#    candidates 389/390 add a sixth icon -- are unreachable on 13.6, since nothing
#    writes 453 anything but 0 or 1. They are left alone rather than modelled.
#
#    Cross-check, not a fit: run the floorplan bursts' own defaults through this and
#    exactly one stock floorplan comes out with the four-icon rail -- **SY**, the one
#    CLAUDE.md already records as the AGS-mode exception (`EF = 0`, none).
#
# The SOURCE slots are looked up by option name (build-derived) where the OPTIONS page
# binds them. Only the target slots, the Go Power! count slot (not an OPTIONS row -- it
# arrives as selector 0xD7 in the floorplan burst) and the arithmetic are transcribed,
# and those are 13.6's.
BATTERY_BLOCK_SLOT = 6
NAV_RAIL_SLOT = 453
GO_POWER_SLOT = 39


def _battery_block_for(ags: int) -> int:
    if ags in (2, 3):
        return 1
    return 2 if ags == 1 else 0


def _nav_rail_for(ags: int, go_power: int, prog_inverter: int) -> int:
    return 1 if (ags or go_power or prog_inverter) else 0


def source_slot(data: bytes, name: str) -> int | None:
    """The value slot the OPTIONS page binds `name` to, or None if this build has none.

    Matched on the canonical (first-line) name, so 13.6's two-line labels join.
    """
    from .livecfg import canonical_option

    for b in option_bindings(data):
        if canonical_option(b.name) == name and b.value_slot is not None:
            return b.value_slot
    return None


def render_slots(data: bytes, config: CoachConfig | None,
                 overrides: dict[int, int] | None = None) -> dict[int, int]:
    """The `{value_slot: value}` map a render of `data` uses for `config`: its named
    floorplan and options, then `overrides` (`render --slot`), then the slots GuiCode
    derives for itself, which never replace an override. `render` and `verify` share it;
    Studio's engine applies the same `derived_slots` (`studio/server/engine.py`)."""
    values = slot_values(data, config) if config is not None else {}
    values.update(overrides or {})
    for slot, value in derived_slots(data, values).items():
        if slot not in (overrides or {}):
            values[slot] = value
    return values


def derived_slots(data: bytes, values: dict[int, int]) -> dict[int, int]:
    """The slots GuiCode would compute for itself, given the ones it was handed.

    `values` is a `slot_values` result. Returns only the computed slots; merge them over
    it, letting anything the caller set by hand win -- an explicit override stays the
    escape hatch it is everywhere else.

    The two derivations differ in how they treat an ABSENT source, on purpose:

    - **slot 6** is keyed on the AGS slot being present, so nothing is invented for a
      configuration that never chose a generator mode: slot 6 stays unset and the walk
      draws the same default block (901) the panel does.
    - **slot 453** reads an absent source as 0. That is the faithful reading here and it
      is the case that matters: the panel always holds a concrete byte for all three
      sources (the config payload carries all three), an option not selected IS off, and
      "no generator, no solar, no inverter" is precisely when the icon goes away. Since
      the rail's default candidate is the FIVE-icon one, treating absent as unknown
      would draw the Electrical icon for exactly the coach that does not have it.

    An empty `values` -- no configuration expressed at all -- derives nothing either way.
    """
    if not values:
        return {}
    ags = source_slot(data, "AGS")
    prog_inverter = source_slot(data, "Progressive Inverter")

    out: dict[int, int] = {}
    if ags is not None and ags in values:
        out[BATTERY_BLOCK_SLOT] = _battery_block_for(values[ags])
    out[NAV_RAIL_SLOT] = _nav_rail_for(
        values.get(ags, 0) if ags is not None else 0,
        values.get(GO_POWER_SLOT, 0),
        values.get(prog_inverter, 0) if prog_inverter is not None else 0,
    )
    return out
