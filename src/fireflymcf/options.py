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

from . import guidata, layout, memo, records

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


@memo.content_cached(copy=None)
def page_root(data: bytes, header: str) -> int | None:
    """The first object-table index whose header -- the first labelled record of its OWN record
    list (`_first_label`) -- strips to `header`, or None. `cli._resolve_page_root`'s lookup for a
    named page; memoised on the image's bytes, the header passed in."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    for i in range(len(table)):
        label = _first_label(_own_records(data, gs, ge, table, i))
        if label is not None and label.strip() == header:
            return i
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
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return CoachConfig.from_dict(raw)

    @staticmethod
    def from_dict(raw: dict) -> "CoachConfig":
        """`from_json`'s parse of an already-loaded object: `extra_slots`' string keys become
        `int`, and a missing key takes the dataclass default. A recipe's `coach` block loads
        through this (`recipe.Project` has checked its shape first)."""
        extra_slots = {}
        for k, v in raw.get("extra_slots", {}).items():
            if not (isinstance(k, str) and k.isascii() and k.isdigit()):
                raise ValueError("extra_slots key %r is not a slot number (ASCII digits)" % (k,))
            if int(k) in extra_slots:
                raise ValueError("extra_slots keys name slot %d twice (%r)" % (int(k), k))
            extra_slots[int(k)] = v
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

    Each name is also keyed by `livecfg.canonical_option` (its first line), the spelling
    `coach-config` writes: 13.6 stores `'Cargo / Bath Light\\n    (CH. 25)'`, and a
    `coach.json` naming `'Cargo / Bath Light'` must render without a hand edit.
    """
    from .livecfg import canonical_option
    out: dict[str, int] = {}
    for b in bindings:
        for name in {b.name, canonical_option(b.name)}:
            if name in out and out[name] != b.value_slot:
                raise ValueError(
                    f"option name {name!r} is bound to two different slots: "
                    f"{out[name]} and {b.value_slot}")
            out[name] = b.value_slot
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
# *handed* over the bus. A few display slots are not among them: the panel works them out
# for itself, in the config-payload handler (13.6 `FUN_00247BF8`), every time a config
# payload lands. A render that does not do the same arithmetic draws a screen the panel
# never draws -- which is why this lives in the tools layer and not in the studio server,
# so `cli render` and Studio cannot disagree about it.
#
# The arithmetic is per firmware, and each firmware's is used only on an image that still
# carries the routine it transcribes: `located_derivation` hashes the routine's cited
# instructions and literal-pool words (`DERIVATION_GUARDS`) and compares them to stock. An
# image whose bytes differ there -- another firmware, or a build that rewrote the routine --
# derives nothing rather than a guess.
#
# ---- 13.6 (GuiCode maps at 0x00240000) ------------------------------------------------
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
#                                        ; (slot 6 = 0x2003A03E, literals 0x00249e30/0x00249f24)
#
#    so object 904 (value 3) is a candidate the firmware never selects.
#
# 1a. THE ELECTRICAL LAYOUT FLAG, slot 7 (0x20039F24). The instructions after that store
#    write slot 7 from the same test --
#
#      0x00249D14  cmp r3,#0x1           ; r3 = (AGS - 2) & 0xFF, as above
#      0x00249D16  bls.w 0x00249EDC      ;   AGS in {2,3} -> slot 7 = 0 (strb 0x00249EE0,
#                                        ;                  literal 0x00249f28)
#      0x00249D1E  strb r2,[r3]          ;   else r2 = 1  -> slot 7 = 1 (literal 0x00249e34)
#
#    `slot-source` lists those two stores as its only writers; a switch based at 829, arms
#    {0, 1} and no default, reads it. It is 13.6's counterpart of 14.7's slot 12 below.
#    Which arm is which layout is read from the candidates' content, not from this code:
#    829 (value 0) carries the AES sub-view ("Engine Not Running", "Max Engine Run Time"),
#    830 (value 1) the AGS one (GEN, Triggers, Go Power! Solar) (`GUICODE.md` Finding 31);
#    the reference coach, AES (slot 37 = 3), draws 829's records on its ELECTRICAL page.
#    Modelled since 2026-11-11 (it was left out while 13.6's output was pinned unchanged).
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
# ---- 14.7 (GuiCode maps at 0x00240000) ------------------------------------------------
#
# The same handler, with a Lithionics row (slot 105) added ahead of the battery block, and
# the generator test split out into its own slot. Registers: slot 11 = 0x20039BF5, slot 12 =
# 0x20039D9D, slot 319 = 0x2003A14F; sources slot 33 (AGS / AES selector) = 0x20039F08,
# slot 95 (Go Power! count) = 0x2003A037, slot 104 (Progressive Inverter) = 0x2003A169,
# slot 105 (Lithionics Batteries) = 0x2003A009.
#
# 3. THE HOME BATTERY CARD, slot 11 (switch based at 765, arms {0,1,2,3}, no default):
#
#      0x0024A12E  ldr r3,[0x0024a250]   ; = 0x2003A009  Lithionics (slot 105)
#      0x0024A134  beq.w 0x0024A2FC      ;   zero -> the AGS test
#      0x0024A138  ldr r3,[0x0024a260]   ; = 0x20039BF5
#      0x0024A13C  strb r2,[r3]          ;   r2 = 3       -> slot 11 = 3
#      0x0024A2FC  ldr r3,[0x0024a544]   ; = 0x20039F08  AGS / AES selector (slot 33)
#      0x0024A300  cmp r3,#1 ; beq 0x0024A30C
#      0x0024A308  strb r2,[r3]          ;   r2 = 0       -> slot 11 = 0 (literal 0x0024a548)
#      0x0024A310  strb r2,[r3]          ;   r2 = 2, AGS  -> slot 11 = 2
#
#    Lithionics wins over AGS, and decides slot 11 without reading slot 33 at all; value 1
#    is never written (its arm is unreachable).
#
# 4. THE GENERATOR FLAG, slot 12 (switch based at 428, arms {1, default}):
#
#      0x0024A13E  ldr r3,[0x0024a24c]   ; = 0x20039F08  slot 33
#      0x0024A142  subs r3,#2 ; uxtb ; cmp r3,#1
#      0x0024A148  bls.w 0x0024A314      ;   33 in {2,3} -> slot 12 = 0 (strb 0x0024A318,
#                                        ;                  literal 0x0024a54c)
#      0x0024A150  strb r2,[r3]          ;   else r2 = 1 -> slot 12 = 1 (literal 0x0024a264)
#
# 5. THE LEFT NAV RAIL, slot 319 (switch based at 343, arms {0,2,4, default}; as 13.6's 453,
#    0 draws the four-icon rail, 2 and 4 are unreachable):
#
#      0x0024A170  ldr r3,[0x0024a24c]   ; = 0x20039F08  slot 33       bne.w 0x0024A33A
#      0x0024A17A  ldr r2,[0x0024a254]   ; = 0x2003A037  slot 95       bne.w 0x0024A33A
#      0x0024A184  ldr r2,[0x0024a23c]   ; = 0x2003A169  slot 104      bne.w 0x0024A33A
#      0x0024A18E  ldr r3,[0x0024a274]   ; = 0x2003A14F
#      0x0024A190  strb r2,[r3]          ;   all three zero (r2 = 0) -> slot 319 = 0
#      0x0024A33A  ldr r2,[0x0024a55c] ; movs r1,#1 ; strb @0x0024A33E -> slot 319 = 1
#
#    Slot 95 is the Go Power! count: the same clamp setter as 13.6's slot 39 at 0x002477D0
#    (`cmp r0,#5; bhi`, store 0x002477DE), and the config-payload byte +0x0B stored at
#    0x00247D06 (13.6: 0x00247C50, slot 39).
#
# The SOURCE slots are looked up by option name (build-derived) where the OPTIONS page
# binds them. Only the target slots, the Go Power! count slot (not an OPTIONS row -- it
# arrives as selector 0xD7 in the floorplan burst) and the arithmetic are transcribed, per
# firmware.
BATTERY_BLOCK_SLOT = 6
ELECTRICAL_LAYOUT_SLOT = 7
NAV_RAIL_SLOT = 453
GO_POWER_SLOT = 39

# 14.7's targets and Go Power! count (cited above).
BATTERY_CARD_SLOT_147 = 11
GENERATOR_FLAG_SLOT_147 = 12
NAV_RAIL_SLOT_147 = 319
GO_POWER_SLOT_147 = 95

# `(firmware, ((start, end), ...), sha256)`: the GuiCode address ranges (end exclusive) of
# each derivation and the literal-pool words its cited loads read, and the sha256 of their
# bytes concatenated, read from stock. Each routine is guarded as two contiguous runs, from
# its first cited load through the branch that rejoins the rail test or leaves it (13.6
# 0x00249CFA..0x00249D62 `b 0x249F08`, and 0x00249ECC..0x00249F08 with `b 0x249D3E` at
# 0x00249F00; 14.7 0x0024A12E..0x0024A192 `b 0x24A352`, and 0x0024A2FC..0x0024A33E with
# `b 0x24A170` at 0x0024A338), so every compare, branch and store on the derivation's path
# is covered, along with the stores between them (13.6's slot 7, the config copies). No
# stock-derived build differs from stock inside these ranges (every build image checked,
# 2026-11-10). 13.6's slot-7 literal words (0x00249E34, 0x00249F28, both = 0x20039F24) were
# added on 2026-11-11, when slot 7 was modelled; same check.
DERIVATION_GUARDS: tuple = (
    ("13.6",
     ((0x00249CFA, 0x00249D64), (0x00249ECC, 0x00249F0A),
      (0x00249E0C, 0x00249E10), (0x00249E24, 0x00249E28), (0x00249E2C, 0x00249E38),
      (0x00249E44, 0x00249E48), (0x00249F24, 0x00249F2C), (0x00249F38, 0x00249F3C)),
     "24785d5bb751a173e14187a058c4bad60ba920cf1bb34cf785ed59261de11e6a"),
    ("14.7",
     ((0x0024A12E, 0x0024A194), (0x0024A2FC, 0x0024A340),
      (0x0024A23C, 0x0024A240), (0x0024A24C, 0x0024A258), (0x0024A260, 0x0024A268),
      (0x0024A274, 0x0024A278), (0x0024A544, 0x0024A550), (0x0024A55C, 0x0024A560)),
     "9587cd5e5329f814ddaa356d59ff3af9ad62eb2629825beb7adfac602c0620ed"),
)


# The table is bound as a default argument, so the memo stays content-keyed and
# monkeypatching DERIVATION_GUARDS has no effect: a test passes `guards=` instead.
def located_derivation(data: bytes, guards: tuple = DERIVATION_GUARDS) -> str | None:
    """`"13.6"`, `"14.7"` or None: the firmware whose derivation routine `data`'s GuiCode
    carries byte for byte (`guards`, default `DERIVATION_GUARDS`). Memoised on content; the
    table is an argument (the memo trap)."""
    return _located_derivation(data, guards)


@memo.content_cached(copy=None)
def _located_derivation(data: bytes, guards: tuple) -> str | None:
    import hashlib
    from . import theme
    try:
        section = theme.guicode_section(data)
        base = theme.guicode_base(section)
    except Exception:                      # no container, no GuiCode: nothing is located
        return None
    code = data[section.offset:section.offset + section.length]
    for firmware, ranges, sha in guards:
        parts = []
        for start, end in ranges:
            lo, hi = start - base, end - base
            if lo < 0 or hi > len(code):
                break
            parts.append(code[lo:hi])
        else:
            if hashlib.sha256(b"".join(parts)).hexdigest() == sha:
                return firmware
    return None


def _battery_block_for(ags: int) -> int:
    if ags in (2, 3):
        return 1
    return 2 if ags == 1 else 0


def _nav_rail_for(ags: int, go_power: int, prog_inverter: int) -> int:
    return 1 if (ags or go_power or prog_inverter) else 0


def _battery_card_for_147(ags: int, lithionics: int) -> int:
    if lithionics:
        return 3
    return 2 if ags == 1 else 0


def _generator_flag_for_147(ags: int) -> int:
    return 0 if ags in (2, 3) else 1


def _electrical_layout_for(ags: int) -> int:
    return 0 if ags in (2, 3) else 1


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
    floorplan and options, its `extra_slots`, then `overrides` (`render --slot`), then the
    slots GuiCode derives for itself, which never replace an `extra_slots` entry or an
    override -- as `coach_slots` merges them, so render, verify and the placement gate read
    one set of settings alike. `render` and `verify` share it; Studio's engine applies the
    same `derived_slots` (`studio/server/engine.py`)."""
    values = slot_values(data, config) if config is not None else {}
    values.update(overrides or {})
    named = set(overrides or {}) | (set(config.extra_slots) if config is not None else set())
    for slot, value in derived_slots(data, values).items():
        if slot not in named:
            values[slot] = value
    return values


def derived_slots(data: bytes, values: dict[int, int]) -> dict[int, int]:
    """The slots GuiCode would compute for itself, given the ones it was handed.

    `values` is a `slot_values` result. Returns only the computed slots; merge them over
    it, letting anything the caller set by hand win -- an explicit override stays the
    escape hatch it is everywhere else.

    Which derivation runs is `located_derivation(data)`'s answer: 13.6's (slots 6, 7 and
    453) or 14.7's (slots 11, 12 and 319). An image that carries neither routine byte for
    byte derives nothing (`{}`).

    An ABSENT source reads as the panel's "none" (owner ruling 2026-11-11):

    - **The AGS slot** (13.6 slot 37, 14.7 slot 33) is a radio -- 0 none, 1 AGS, 2 AES,
      3 AES Single Output -- and settings that name no generator mode mean 0. The AGS slot
      itself is derived as 0 (an explicit value always wins and is never returned here), and
      slots 6 and 7 (13.6) and 11 and 12 (14.7) follow from it, so they are always derived:
      the no-generator block (6 = 0), ELECTRICAL arm 1 (7 = 1; the AGS sub-view, object
      830, by its decoded content, `GUICODE.md` Finding 31), the
      no-generator card (11 = 0) and generator flag 1 (12 = 1). The cost: a coach that has
      AGS or AES but whose settings omit the mode is drawn, and checked by the placement
      gate, as a coach with no generator; name the mode to get its arms. Slot 11 with
      Lithionics (slot 105) present and non-zero is 3 regardless: the routine stores 3
      without reading slot 33 (the branch at 0x0024A134).
    - **slot 453** (13.6) and **slot 319** (14.7) read an absent source as 0 too. The panel
      always holds a concrete byte for all three sources (the config payload carries all
      three), an option not selected IS off, and "no generator, no solar, no inverter" is
      precisely when the icon goes away. Since the rail's default candidate is the
      FIVE-icon one, treating absent as unknown would draw the Electrical icon for exactly
      the coach that does not have it.

    (Before 2026-11-11 slots 6, 11 and 12 were derived only when the AGS slot was named,
    and 13.6's slot 7 was not derived; an unset slot 12 drew its 0xFFFF default, the AES
    arm, where the panel draws arm 1 for a coach without AES.)

    An empty `values` -- no configuration expressed at all -- derives nothing. Settings with
    only `extra_slots` are not empty: they derive as above.
    """
    if not values:
        return {}
    firmware = located_derivation(data)
    if firmware == "13.6":
        return _derived_slots_136(data, values)
    if firmware == "14.7":
        return _derived_slots_147(data, values)
    return {}


def _source_value(data: bytes, values: dict[int, int], name: str) -> int:
    slot = source_slot(data, name)
    return values.get(slot, 0) if slot is not None else 0


def _derived_slots_136(data: bytes, values: dict[int, int]) -> dict[int, int]:
    ags = source_slot(data, "AGS")
    out: dict[int, int] = {}
    if ags is not None:
        mode = values.get(ags, 0)          # absent: "none" (derived_slots' docstring)
        if ags not in values:
            out[ags] = 0
        out[BATTERY_BLOCK_SLOT] = _battery_block_for(mode)
        out[ELECTRICAL_LAYOUT_SLOT] = _electrical_layout_for(mode)
    out[NAV_RAIL_SLOT] = _nav_rail_for(
        _source_value(data, values, "AGS"),
        values.get(GO_POWER_SLOT, 0),
        _source_value(data, values, "Progressive Inverter"),
    )
    return out


def _derived_slots_147(data: bytes, values: dict[int, int]) -> dict[int, int]:
    ags = source_slot(data, "AGS")
    lithionics = source_slot(data, "Lithionics Batteries")
    out: dict[int, int] = {}
    if ags is not None and ags not in values:
        out[ags] = 0                       # absent: "none" (derived_slots' docstring)
    mode = values.get(ags, 0) if ags is not None else 0
    if lithionics is not None and values.get(lithionics, 0):
        # 0x0024A134: a non-zero Lithionics byte stores 3 without reading slot 33
        out[BATTERY_CARD_SLOT_147] = 3
    elif ags is not None:
        out[BATTERY_CARD_SLOT_147] = _battery_card_for_147(mode, 0)
    if ags is not None:
        out[GENERATOR_FLAG_SLOT_147] = _generator_flag_for_147(mode)
    out[NAV_RAIL_SLOT_147] = _nav_rail_for(
        _source_value(data, values, "AGS"),
        values.get(GO_POWER_SLOT_147, 0),
        _source_value(data, values, "Progressive Inverter"),
    )
    return out


# ---------------------------------------------------------------------------
# A coach's settings, as `compile` takes them: a recipe's `coach` block, or `compile --config`
# in its place (`recipe.resolve_coach`). The same `CoachConfig` form a render takes, resolved the
# way a render resolves it (`render_slots`), and validated against the image, because a gate
# that reads these settings must never read a slot or a value the panel cannot draw.

# The firmwares whose `derived_slots` derivation `coach_slots` applies, when
# `located_derivation` finds that firmware's routine in the image. On any other image the
# derivations are skipped and their slots stay unset.
DERIVED_ON: frozenset = frozenset({"13.6", "14.7"})


@dataclass(frozen=True)
class CoachSlots:
    """A coach's settings, resolved: `slots` is `{value_slot: value}` (named floorplan and
    options, then `extra_slots`, then the derived slots where located); `pinned` the sorted
    slots `extra_slots` names; `source` where the settings came from (`"recipe"` or
    `"--config <path>"`)."""
    slots: dict
    pinned: tuple
    source: str

    def to_json(self) -> dict:
        return {"slots": dict(sorted(self.slots.items())), "pinned": list(self.pinned),
                "source": self.source}


def coach_canonical(config: CoachConfig) -> str:
    """The text that identifies what `config` resolves to, for a cache key: the floorplan, the
    options in their given order (two names on one slot resolve last-wins), and `extra_slots`
    sorted. `preview` is not part of it: it is render-only."""
    opts = config.options
    options_form = (["dict", [[k, v] for k, v in opts.items()]] if isinstance(opts, dict)
                    else ["list", list(opts)])
    return json.dumps([config.floorplan, options_form,
                       sorted([[k, v] for k, v in config.extra_slots.items()],
                              key=lambda kv: (str(type(kv[0])), kv[0]))])


@memo.content_cached(copy=None)
def _switch_readers(data: bytes) -> tuple:
    """`(readers, bases)` over every type-6 switch record in every object: `readers` maps a
    value slot to `((child_base, candidate match values, has default), ...)`, `bases` a
    `child_base` to the slots its records read. A switch has a default when its base
    candidate's match value is 0xFFFF (`layout.Screen._switch_child`: on no match the panel
    draws the base only then). Returned shared and read-only."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    match_off = guidata.find_match_table(data)
    readers: dict[int, list] = {}
    bases: dict[int, set] = {}
    for i in range(len(table)):
        for rec in _own_records(data, gs, ge, table, i):
            if rec.wtype != 6 or rec.value_slot is None or rec.child_index is None:
                continue
            cands = guidata.switch_candidates(data, rec, match_off)
            default = any(obj == rec.child_index and v == 0xFFFF for v, obj in cands)
            readers.setdefault(rec.value_slot, []).append(
                (rec.child_index, frozenset(v for v, _o in cands), default))
            bases.setdefault(rec.child_index, set()).add(rec.value_slot)
    return ({s: tuple(r) for s, r in readers.items()},
            {b: frozenset(s) for b, s in bases.items()})


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def coach_slots(data: bytes, config: CoachConfig, source: str,
                derive_on: frozenset = DERIVED_ON) -> CoachSlots | None:
    """`config` resolved for `data` and validated, or None when it names nothing (no
    floorplan, no options, no extra_slots): an empty block is no settings.

    Resolution is `slot_values`, then, only where `located_derivation` names a firmware in
    `derive_on`, `derived_slots` merged under `extra_slots` (an extra slot wins, as
    `render_slots`' override does).
    Refuses `COACH_CONFIG` (`recipe.RecipeError`), naming the slot or the name:
    - a floorplan or option name `slot_values` cannot resolve;
    - a slot `extra_slots` names that no type-6 switch record reads and that the names and
      the derivations did not produce;
    - a value `extra_slots` gives that no switch reading its slot has an arm for, unless one
      of them has a 0xFFFF default arm.
    Slots that come from a name or a derivation are taken as `slot_values` resolved them.

    The shared-base rule (two slots read by records based at one `child_base`, given different
    values) is not checked here: the placement gate applies it per page, over the bases that
    page's walk reaches (`pagewalk.ArmPaths.slots`). Over the whole image it is no error. A base
    is often a candidate group shared by many independent switch records, each reading its own
    slot (13.6 base 394, the OPTIONS checkbox group, is read by slots 107-113 among others), and
    `layout.Screen.walk` picks each record's arm by its own `value_slot` when given slot values
    (layout.py:688-692). The ambiguity exists only where settings drive switches by base
    (`switch_values`), as `pagewalk.switch_arm_paths` does.
    Memoised on the bytes and `coach_canonical(config)`; `derive_on` is an argument."""
    resolved = _coach_slots(data, coach_canonical(config), derive_on)
    if resolved is None:
        return None
    slots, pinned = resolved
    return CoachSlots(dict(slots), pinned, source)


@memo.content_cached(copy=None)
def _coach_slots(data: bytes, canonical: str, derive_on: frozenset):
    from .recipe import RecipeError

    def refuse(message: str):
        raise RecipeError("COACH_CONFIG", "coach settings: " + message)

    floorplan, (form, items), extra_items = json.loads(canonical)
    opts = {k: v for k, v in items} if form == "dict" else list(items)
    for k, v in extra_items:
        if not _is_int(k) or not _is_int(v):
            refuse("extra_slots entry %r: %r is not an integer slot and value" % (k, v))
    extra = {k: v for k, v in extra_items}
    if isinstance(opts, dict):
        bad = [(k, v) for k, v in opts.items() if not _is_int(v)]
        if bad:
            refuse("option %r: value %r is not an integer" % bad[0])
    if floorplan is None and not opts and not extra:
        return None

    named = CoachConfig(floorplan=floorplan, options=opts)
    try:
        values = slot_values(data, named)
        if floorplan is not None and not slot_values(data, CoachConfig(floorplan=floorplan)):
            refuse("floorplan %r resolves to no value slot on this image" % (floorplan,))
    except KeyError as e:
        refuse(str(e.args[0]) if e.args else str(e))
    except ValueError as e:              # FloorplanSwitchNotFound, an option name on two slots
        refuse(str(e))
    produced = set(values)
    values.update(extra)
    if located_derivation(data) in derive_on:
        derived = derived_slots(data, values)
        produced |= set(derived)
        for slot, value in derived.items():
            if slot not in extra:
                values[slot] = value

    readers, _bases = _switch_readers(data)
    for slot in sorted(extra):
        value = extra[slot]
        rs = readers.get(slot, ())
        if not rs:
            if slot in produced:
                continue
            refuse("slot %d: no type-6 switch on this image reads it, and no floorplan, "
                   "option or derivation produces it" % slot)
        if any(default for _b, _v, default in rs):
            continue
        arms = sorted(set().union(*(v for _b, v, _d in rs)) - {0xFFFF})
        if value not in arms:
            refuse("slot %d = %d: no switch reading slot %d has an arm for %d; it takes %s"
                   % (slot, value, slot, value, ", ".join(map(str, arms))))
    return tuple(sorted(values.items())), tuple(sorted(extra))
