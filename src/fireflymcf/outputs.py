"""What the panel calls each G12 output pin: the G12 OUTPUTS page's names and the controls' captions.

NETWORK DIAGNOSTICS -> G12 OUTPUTS draws one row per G12 output, `"NN - NAME"`. **A row's name is
not always in its label.** A build names a pin three ways, and this module reads all three:

1. **Static**: the whole row is one label in the page object's own record list (13.6
   `"12 - A/C Fan Low"`). Shown always.
2. **Dynamic string cell** (13.6, 14.7): the label is the bare prefix (`"05 - "`) and the next
   record is a wtype-13 whose `value_slot` is a type-9 string cell (`MCF-FORMAT.md` §6.1). GuiCode
   fills the cell every tick from its string table, gated on the floorplan and option registers
   (`GUICODE.md` Findings 35 and 81). `cellwriters` runs that block under every input combination,
   so each name comes with the floorplans and options that show it. 13.6 has 14 such rows.
3. **Switch arm** (11.23): the row label itself sits inside the arms of a type-6 switch on the
   floorplan slot (11.23 objects 811-821 on slot 7: `"05 - TANK HEATER FRESH"` under some
   floorplans, `"06 - TANK HEATER GR 1"` under others). The arm the panel draws for a value is
   `layout.Screen._switch_child`'s replay of `FUN_00256C54`'s scan, so the condition is that scan
   inverted.

So the earlier statement here, "static labels ... the same on every floorplan", was wrong for
14 of 13.6's 44 rows and for every switched row on 11.23. `output_names` keeps its old contract
(the label text, one per pin) for callers that want only that; `output_name_variants` is the full
answer.

**Conditions.** A condition is a tuple of clauses (OR); a clause is a tuple of `(Gate, values)`
terms (AND). `ALWAYS = ((),)`. Gates are named from the build: the floorplan register (`config`
cfg offset 0) and slot (`config.floorplan_slot`) with codes from `options.floorplan_options`,
and each OPTIONS-page register by its row names (`options.option_bindings`). A slot or register
no OPTIONS row names is reported raw. `describe` and `as_json` render one.

**Second name source: control captions** (`control_captions`). A control's caption is the text
it draws. It is joined to a pin two ways, kept apart: `status` (the control's box takes its fill
from the cell-table index the pin's GuiCode status block colours, `statusbind.pin_bindings`) and
`command` (the control's touch region names a binding group whose entries command only that pin,
`guidata.group_table`/`binding_table`). That the label inside the control is the control's
caption is `inferred-from-data`: it is the record order controls use (box, caption, touch
region), not an instruction.

A name is the build's statement of what the output drives on its coaches. An owner's installer
may wire a different load, so a button wired to a named output switches that load unless the
coach differs; `add_button` and `wire_button` refuse one without `allow_named_output`, and they
count a pin as named if ANY floorplan or option names it (`named_outputs`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import guidata, layout, memo, options

# A numbered G12 output row, e.g. "44 - Water Pump" or "20 -" (no name wired). Both
# 13.6/14.7 ("01 - ") and 11.23 ("03 - AWNING EXT") use this "NN - NAME" convention.
ROW_RE = re.compile(r"^(\d{1,2})\s*-\s*(.*)$", re.S)

DYNAMIC_STRING_WTYPE = 13      # MCF-FORMAT §6.1: a wtype-13 record draws its type-9 string cell
SWITCH_WTYPE = 6
CONTAINER_WTYPES = (5, 0x2E)
MAX_DEPTH = 12
MAX_PATHS = 32                 # parent chains followed per object in `control_captions`

ALWAYS = ((),)


def outputs_root(data: bytes) -> int:
    """The object-table index for the G12 OUTPUTS diagnostics grid.

    Header text alone is ambiguous ("NETWORK DIAGNOSTICS" is shared with the
    DEVICES/FAULTS tab), so this resolves structurally instead: among every object
    whose OWN (non-recursive) record list -- the same convention `cli._resolve_page_root`
    and `options._find_options_pages` use -- contains a label that strips to exactly
    "G12 OUTPUTS", pick the one with the most rows matching the "NN - NAME" numbered
    output convention (`ROW_RE`); ties broken by lowest object index. Verified
    unique per build with no tie-break needed: 13.6 -> object 35 (44 numbered rows,
    including "44 - Water Pump"), 11.23 -> object 675 (11 rows, including
    "44 - WATER PUMP"), 14.7 -> object 32 (44 rows). The DEVICES/FAULTS tab (13.6
    object 14) has no "G12 OUTPUTS" label at all in its own record list, so it is
    never a candidate.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    best: tuple[int, int] | None = None  # (numbered_row_count, object_index)
    for i in range(len(table)):
        recs = options._own_records(data, gs, ge, table, i)
        labels = [r.label.strip() for r in recs if r.label is not None]
        if "G12 OUTPUTS" not in labels:
            continue
        numbered = sum(1 for label in labels if ROW_RE.match(label))
        candidate = (numbered, -i)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise ValueError(
            "could not resolve --page 'outputs' (no object's own record list carries "
            "a 'G12 OUTPUTS' label) on this build; pass --root N explicitly instead")
    return -best[1]


def output_names(data: bytes) -> dict[int, str]:
    """`{output pin: label text}` from the G12 OUTPUTS object's own rows; "" for a bare prefix.

    Only the text stored in the row label, so a dynamic row (13.6's `"05 - "`) reads "" here
    although the panel shows a name under some floorplans, and 11.23's switched rows are absent.
    `output_name_variants` returns every name with its condition.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out: dict[int, str] = {}
    for r in options._own_records(data, gs, ge, table, outputs_root(data)):
        m = ROW_RE.match(r.label.strip()) if r.label is not None else None
        if m:
            out.setdefault(int(m.group(1)), _norm(m.group(2)))
    return out


def _norm(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------------------------ gates

@dataclass(frozen=True)
class Gate:
    """One input the panel tests to choose what it draws."""
    kind: str              # "floorplan" | "option" | "slot" | "register"
    key: int               # the register (floorplan/option/register) or the slot
    label: str             # "floorplan", "Black Tank", "AGS / AES / AES Single Output", "slot 23"
    values: tuple          # every value considered, sorted
    names: tuple           # display name per value, same order

    def name(self, value: int) -> str:
        return self.names[self.values.index(value)] if value in self.values else str(value)


@dataclass(frozen=True)
class _Gates:
    by_register: dict
    by_slot: dict
    cfg_of_register: dict          # config-unpack register -> cfg offset (`config.config_offsets`)
    data: bytes
    variable_tables: tuple         # `guidata.find_variable_tables`, located once

    def register_of_slot(self, slot: int) -> int | None:
        try:
            return layout.variable_register(self.data, slot, *self.variable_tables)[0]
        except Exception:
            return None

    def _cfg(self, register) -> str:
        off = self.cfg_of_register.get(register)
        return "" if off is None else " (config byte %#04x)" % off

    def for_register(self, register: int) -> Gate:
        return self.by_register.get(register) or Gate(
            "register", register, "register %#x%s" % (register, self._cfg(register)),
            (0, 1), ("0", "1"))

    def for_slot(self, slot: int, seen_values=()) -> Gate:
        if slot in self.by_slot:
            return self.by_slot[slot]
        vals = tuple(sorted(set(seen_values) | {-1}))
        return Gate("slot", slot,
                    "slot %d%s" % (slot, self._cfg(self.register_of_slot(slot))), vals,
                    tuple("other" if v == -1 else str(v) for v in vals))


@memo.content_cached(copy=None)
def _gates(data: bytes) -> _Gates:
    from . import config
    from .livecfg import canonical_option
    by_register: dict = {}
    by_slot: dict = {}
    try:
        fp_names = options.floorplan_options(data)
    except options.FloorplanSwitchNotFound:
        fp_names = {}
    values = tuple(sorted(set(fp_names) | {0}))
    names = tuple(fp_names.get(v, "#%d" % v) for v in values)
    try:
        cfg = config.config_offsets(data)
        fp_reg = cfg.get(config.CFG_FLOORPLAN)
        fp_slot = config.floorplan_slot(data)
    except config.CapstoneUnavailable:
        cfg, fp_reg, fp_slot = {}, None, None
    if fp_slot is None:
        try:
            fp_slot = options._find_floorplan_switch(data)[0]
        except options.FloorplanSwitchNotFound:
            fp_slot = None
    fp = Gate("floorplan", fp_reg if fp_reg is not None else -1, "floorplan", values, names)
    if fp_reg is not None:
        by_register[fp_reg] = fp
    if fp_slot is not None:
        by_slot[fp_slot] = fp

    grouped: dict = {}
    for b in options.option_bindings(data):
        grouped.setdefault(b.register, []).append(b)
    for register, rows in grouped.items():
        labels: list = []
        for b in rows:
            n = canonical_option(b.name)
            if n not in labels:
                labels.append(n)
        if len(labels) == 1:
            vals, vnames = (0, 1), ("off", "on")
        else:                          # a radio on one register (13.6 AGS / AES / AES Single)
            by_value: dict = {}
            for n in labels:
                by_value.setdefault(options._option_value(n), n)
            vals = tuple(sorted(set(by_value) | {0}))
            vnames = tuple(by_value.get(v, "none") for v in vals)
        gate = Gate("option", register, " / ".join(labels), vals, vnames)
        by_register.setdefault(register, gate)
        for b in rows:
            by_slot.setdefault(b.value_slot, gate)
    return _Gates(by_register, by_slot, {r: o for o, r in cfg.items()}, bytes(data),
                  tuple(guidata.find_variable_tables(data)))


# -------------------------------------------------------------------------------- conditions

def _clause(terms: dict) -> tuple:
    """A clause from `{Gate: set of values}`, dropping every gate left unconstrained."""
    out = []
    for gate, vals in terms.items():
        vals = frozenset(vals)
        if vals >= set(gate.values):
            continue
        out.append((gate, vals))
    return tuple(sorted(out, key=lambda t: (t[0].kind != "floorplan", t[0].label)))


def _and(a: tuple, b: tuple) -> tuple:
    """Conjunction of two conditions."""
    out = []
    for ca in a:
        for cb in b:
            terms: dict = {}
            ok = True
            for gate, vals in ca + cb:
                cur = terms.get(gate)
                terms[gate] = vals if cur is None else cur & vals
                if not terms[gate]:
                    ok = False
            if ok:
                c = _clause(terms)
                if c not in out:
                    out.append(c)
    return _simplify(out)


def _implies(a: dict, b: dict) -> bool:
    """Clause `a` implies clause `b` (every assignment satisfying `a` satisfies `b`)."""
    return all(g in a and a[g] <= vals for g, vals in b.items())


def _simplify(clauses: list) -> tuple:
    """Drop implied clauses and merge clauses that differ in one gate, until stable."""
    cl = [dict(c) for c in clauses]
    changed = True
    while changed:
        changed = False
        for i in range(len(cl)):
            for j in range(len(cl)):
                if i == j:
                    continue
                a, b = cl[i], cl[j]
                if _implies(a, b):
                    cl.pop(i)
                    changed = True
                    break
                if set(a) == set(b):
                    diff = [g for g in a if a[g] != b[g]]
                    if len(diff) == 1:
                        merged = dict(a)
                        merged[diff[0]] = a[diff[0]] | b[diff[0]]
                        cl[i] = dict(_clause(merged))
                        cl.pop(j)
                        changed = True
                        break
            if changed:
                break
    out = []
    for c in cl:
        t = _clause(c)
        if t == ():
            return ALWAYS
        if t not in out:
            out.append(t)
    return tuple(sorted(out, key=lambda c: [(g.label, sorted(v)) for g, v in c]))


def _from_table(table: dict, gates: list, rel: tuple, text: str) -> tuple:
    """The condition under which `table` (`{input values: text}`) yields `text`."""
    clauses = []
    for key, t in table.items():
        if t != text:
            continue
        c = _clause({gates[i]: {key[i]} for i in rel})
        if c not in clauses:
            clauses.append(c)
    return _simplify(clauses)


def _term_text(gate: Gate, vals: frozenset) -> str:
    names = [gate.name(v) for v in sorted(vals)]
    if gate.kind == "floorplan":
        rest = [gate.name(v) for v in gate.values if v not in vals]
        if len(rest) < len(names):
            return "any floorplan but " + ", ".join(rest)
        return "floorplan " + ", ".join(names)
    if gate.kind == "option" and gate.names == ("off", "on"):
        return "%s %s" % (gate.label, names[0])
    return "%s = %s" % (gate.label, " or ".join(names))


def describe(condition: tuple) -> str:
    """`condition` in words: "always", "floorplan RT, WT and Black Tank on", "...; or ..."."""
    if condition == ALWAYS:
        return "always"
    if not condition:
        return "never"
    return "; or ".join(" and ".join(_term_text(g, v) for g, v in c) for c in condition)


def as_json(condition: tuple) -> list:
    """`[{gate label: [value names]}, ...]`, one dict per clause; `[{}]` is always."""
    return [{g.label: [g.name(v) for v in sorted(vals)] for g, vals in c} for c in condition]


# ------------------------------------------------------------------------ OUTPUTS page names

@dataclass(frozen=True)
class OutputName:
    """One name the G12 OUTPUTS page can show for a pin, and when."""
    pin: int
    name: str | None       # None: a dynamic row whose block could not be run (see `source`)
    kind: str              # "static" | "switched" | "dynamic"
    condition: tuple       # ALWAYS for an unconditional row
    source: str            # the chain, cited

    @property
    def when(self) -> str:
        return describe(self.condition)


def _switch_arms(scr: layout.Screen, rec, gates: _Gates) -> list:
    """`[(arm object, condition)]` for every arm `rec` (a type-6 switch) can draw.

    Inverts `layout.Screen._switch_child` (`FUN_00256C54`'s scan): the first candidate in
    `[base, base+count)` whose match value equals the slot's value is drawn; on no match the base
    is drawn iff its own match value is 0xFFFF, otherwise nothing.
    """
    base = rec.child_index
    count = rec.count
    if scr.match_table_off is None or count is None:
        return [(base, ALWAYS)]
    mvs = [scr._match_value(base + k) for k in range(count)]
    gate = gates.for_slot(rec.value_slot, [m for m in mvs if m is not None and m != 0xFFFF])
    arms: dict = {}
    for v in gate.values:
        if v == -1:                    # a raw slot's "any other value"
            child = base if mvs and mvs[0] == 0xFFFF else None
        else:
            child = scr._switch_child(base, v, count)
        if child is not None:
            arms.setdefault(child, set()).add(v)
    return [(child, _simplify([_clause({gate: vals})])) for child, vals in arms.items()]


@memo.content_cached(copy=None)
def _variants(data: bytes) -> tuple:
    from . import cellwriters
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    scr = layout.Screen(data)
    gates = _gates(data)
    root = outputs_root(data)
    found: list = []           # OutputName, in walk order
    dynamic: list = []         # (pin, slot, condition, obj, record offset)

    def walk(obj: int, cond: tuple, stack: tuple) -> None:
        if obj in stack or len(stack) > MAX_DEPTH:
            return
        stack = stack + (obj,)
        pin = None
        for rec in options._own_records(data, gs, ge, table, obj):
            m = ROW_RE.match(rec.label.strip()) if rec.label is not None else None
            if m:
                pin = int(m.group(1))
                kind = "static" if cond == ALWAYS else "switched"
                found.append(OutputName(
                    pin, _norm(m.group(2)), kind, cond,
                    "object %d record @%#x label %r" % (obj, rec.offset, rec.label.strip())
                    + ("" if kind == "static" else
                       " in a switch arm (FUN_00256C54 scan: %s)" % describe(cond))))
                continue
            if rec.wtype == DYNAMIC_STRING_WTYPE and pin is not None and rec.value_slot is not None:
                dynamic.append((pin, rec.value_slot, cond, obj, rec.offset))
                continue
            if rec.child_index is None or rec.child_index >= len(table):
                continue
            if rec.wtype in CONTAINER_WTYPES:
                walk(rec.child_index, cond, stack)
            elif rec.wtype == SWITCH_WTYPE:
                for child, arm_cond in _switch_arms(scr, rec, gates):
                    walk(child, _and(cond, arm_cond), stack)

    walk(root, ALWAYS, ())

    if dynamic:
        dptr, toff = guidata.find_variable_tables(data)
        cells = {d: layout.variable_register(data, d[1], dptr, toff)[0] for d in dynamic}
        values = {g.key: g.values for g in gates.by_register.values()}
        try:
            ct = cellwriters.cell_table(data, set(cells.values()), values)
            error = None
        except Exception as e:          # CellEmulationError, CapstoneUnavailable
            ct, error = None, "%s: %s" % (type(e).__name__, e)
        if ct is not None:
            tab = ct.table()
            in_gates = [gates.for_register(a) for a in ct.inputs]
        for d in dynamic:
            pin, slot, cond, obj, off = d
            cell = cells[d]
            head = "object %d record @%#x wtype-13 slot %d -> string cell %#x" % (
                obj, off, slot, cell)
            if ct is None:
                found.append(OutputName(pin, None, "dynamic", cond,
                                        head + "; names not read (%s)" % error))
                continue
            ctab = tab[cell]
            rel = cellwriters.relevant_inputs(ctab, ct.inputs, ct.values)
            for text in sorted({t for t in ctab.values() if t}, key=str):
                c = _and(cond, _from_table(ctab, in_gates, rel, text))
                sites = ", ".join("%#x" % s for s in ct.sites_for(cell, text))
                found.append(OutputName(
                    pin, _norm(text), "dynamic", c,
                    "%s; GuiCode %#x-%#x stores it at %s (emulated over %s)" % (
                        head, ct.start, ct.end, sites,
                        ", ".join(g.label for g in in_gates) or "no inputs")))
    return tuple(found)


def output_name_variants(data: bytes) -> dict[int, list[OutputName]]:
    """`{pin: [OutputName, ...]}`: every name the G12 OUTPUTS page can show for each pin.

    Every pin with a row appears; a pin whose row is only ever a bare prefix maps to `[]`.
    Variants are all returned, never one picked. Raises `ValueError` when the build has no
    OUTPUTS page.
    """
    merged: dict = {}
    for v in _variants(data):
        rows = merged.setdefault(v.pin, {})
        if v.name == "":
            continue
        key = (v.name, v.kind)
        if key in rows:            # the same name from several arms: one entry, conditions ORed
            prev = rows[key]
            sources = prev.source if v.source in prev.source else prev.source + " | " + v.source
            rows[key] = OutputName(v.pin, v.name, v.kind,
                                   _simplify(list(prev.condition) + list(v.condition)), sources)
        else:
            rows[key] = v
    return {pin: list(rows.values()) for pin, rows in sorted(merged.items())}


def named_outputs(data: bytes, floorplan: str | None = None) -> dict[int, list[OutputName]]:
    """`{pin: [OutputName, ...]}` for every pin that some floorplan or option names.

    With `floorplan` (a code, e.g. `"WD"`), only names that floorplan can show are kept; options
    are not narrowed, since a recipe cannot know which the owner will enable later. A dynamic row
    whose names could not be read counts as named: refusing on an unknown is the safe side.
    """
    out = {}
    for pin, names in output_name_variants(data).items():
        keep = [n for n in names if floorplan is None or _shows_on(n.condition, floorplan)]
        if keep:
            out[pin] = keep
    return out


def _shows_on(condition: tuple, floorplan: str) -> bool:
    for clause in condition:
        fps = [(g, vals) for g, vals in clause if g.kind == "floorplan"]
        if not fps or all(floorplan in {g.name(v) for v in vals} for g, vals in fps):
            return True
    return False


# ---------------------------------------------------------------------------- control captions

@dataclass(frozen=True)
class Caption:
    """The text a control draws, and how that control is joined to a pin."""
    pin: int
    text: str
    objects: tuple         # ((object index, first caption record offset), ...)
    via: tuple             # ("status",), ("command",) or both
    condition: tuple       # the floorplan/option switch arms above the object (ALWAYS if none)
    other_gates: tuple     # labels of the other switches above it (screen/runtime state), dropped
    source: str

    @property
    def when(self) -> str:
        """The floorplan/option gates above the control. "no floorplan/option gate" means none
        was found on any chain of containers and switch arms above it -- not that a screen
        always shows it (a page is reached by navigation, which this does not model)."""
        if self.condition == ALWAYS:
            return "no floorplan/option gate"
        return describe(self.condition)


def _fill_index(rec) -> int | None:
    for entry in rec.colors or []:
        if len(entry) > 2 and entry[0] == "fillA" and entry[1] == 7 and isinstance(entry[2], int):
            return entry[2]
    return None


def _segments(recs: list) -> list:
    """An object's own records cut into controls: a new control at every record with its own
    mode-7 fill (the box), which is the record order controls use (box, caption, touch region)."""
    segs: list = []
    for rec in recs:
        if _fill_index(rec) is not None or not segs:
            segs.append([])
        segs[-1].append(rec)
    return segs


@memo.content_cached(copy=None)
def _parents(data: bytes) -> dict:
    """`{object: [(parent object, condition)]}` over every container and switch arm."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    scr = layout.Screen(data)
    gates = _gates(data)
    out: dict = {}
    for i in range(len(table)):
        for rec in options._own_records(data, gs, ge, table, i):
            if rec.child_index is None or rec.child_index >= len(table):
                continue
            if rec.wtype in CONTAINER_WTYPES:
                out.setdefault(rec.child_index, []).append((i, ALWAYS))
            elif rec.wtype == SWITCH_WTYPE:
                for child, cond in _switch_arms(scr, rec, gates):
                    out.setdefault(child, []).append((i, cond))
    return out


def _reach(parents: dict, obj: int) -> tuple:
    """The condition under which `obj` is drawn, from the switch arms on the chains above it."""
    paths: list = []

    def up(o: int, cond: tuple, stack: tuple) -> None:
        if len(paths) >= MAX_PATHS:
            return
        ps = [p for p in parents.get(o, []) if p[0] not in stack]
        if not ps or len(stack) > MAX_DEPTH:
            paths.append(cond)
            return
        for p, c in ps:
            up(p, _and(cond, c), stack + (o,))

    up(obj, ALWAYS, ())
    clauses = [c for cond in paths for c in cond]
    return _simplify(clauses) if clauses else ()


def _configuration_only(condition: tuple) -> tuple:
    """`(condition over floorplan/option gates only, labels of the gates dropped)`.

    A switch on a screen or runtime slot (13.6 slots 1 and 2 pick the page a card sits on) says
    when the card is on screen, not which coaches have it. Dropping a term widens a clause, so the
    result over-states where a caption can appear, never under-states it.
    """
    dropped = sorted({g.label for c in condition for g, _v in c
                      if g.kind not in ("floorplan", "option")})
    kept = [tuple((g, v) for g, v in c if g.kind in ("floorplan", "option")) for c in condition]
    return (_simplify(kept) if kept else ()), tuple(dropped)


@memo.content_cached(copy=None)
def _captions(data: bytes) -> tuple:
    from . import statusbind
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    bindings = statusbind.pin_bindings(data)
    by_colour = {b.colour_index: p for p, b in bindings.items()
                 if b.colour_index is not None and b.bridged
                 and statusbind.PIN_MIN <= p <= statusbind.PIN_MAX}
    groups = guidata.group_table(data)
    btab = guidata.binding_table(data)
    sole: dict = {}
    for gid in range(len(groups)):
        chans = {b.channel for b in guidata.group_bindings(groups, btab, gid)
                 if b.channel is not None}
        if len(chans) == 1:
            sole[gid] = chans.pop()
    parents = _parents(data)
    found: list = []
    for obj in range(len(table)):
        recs = options._own_records(data, gs, ge, table, obj)
        for seg in _segments(recs):
            labels = [r for r in seg if r.label is not None and len(r.label.strip()) > 1
                      and r.wtype != DYNAMIC_STRING_WTYPE and not ROW_RE.match(r.label.strip())]
            if not labels:
                continue
            fill = _fill_index(seg[0])
            links: dict = {}
            if fill in by_colour:
                links.setdefault(by_colour[fill], []).append(
                    "status: record @%#x fill index %d is the pin's colour cell"
                    % (seg[0].offset, fill))
            for r in seg:
                if r.wtype == guidata.IMAGE_OPCODE:
                    gid = data[r.end - 2] | (data[r.end - 1] << 8)
                    if gid in sole and statusbind.PIN_MIN <= sole[gid] <= statusbind.PIN_MAX:
                        links.setdefault(sole[gid], []).append(
                            "command: touch region @%#x group %d commands only this pin"
                            % (r.offset, gid))
            if not links:
                continue
            text = " / ".join(_norm(r.label) for r in labels)
            cond, other = _configuration_only(_reach(parents, obj))
            for pin, why in links.items():
                via = tuple(sorted({w.split(":")[0] for w in why}, reverse=True))
                found.append(Caption(pin, text, ((obj, labels[0].offset),), via, cond, other,
                                     "object %d %s; caption record(s) %s by record order "
                                     "(inferred-from-data)"
                                     % (obj, "; ".join(why),
                                        ", ".join("@%#x" % r.offset for r in labels))))
    return tuple(found)


def control_captions(data: bytes) -> dict[int, list[Caption]]:
    """`{pin: [Caption, ...]}`: what the build's own controls call each pin.

    Kept apart from the OUTPUTS-page name on purpose: the two are separate statements in the file,
    and a caption's tie to its control is inference (module docstring).
    """
    merged: dict = {}
    for c in _captions(data):
        rows = merged.setdefault(c.pin, {})
        key = (c.text, c.via)
        prev = rows.get(key)
        if prev is None:
            rows[key] = c
            continue
        rows[key] = Caption(c.pin, c.text, prev.objects + c.objects, c.via,
                            _simplify(list(prev.condition) + list(c.condition)),
                            tuple(sorted(set(prev.other_gates) | set(c.other_gates))),
                            prev.source + " | " + c.source)
    return {pin: list(rows.values()) for pin, rows in sorted(merged.items())}
