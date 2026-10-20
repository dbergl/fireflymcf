"""Status-indicator map: which RV-C status instances can drive an on-screen indicator,
and how cheaply (same-size edit vs. relocator insert), per floorplan.

Mechanism (GUICODE Finding 33): LCD hands GuiCode's paint tick the receive-filter slot-array
base (`0x20007fc8`), which reaches `FUN_00247BF8`; that function reads `slot[var]` at a
hard-coded immediate `ldrsh [r4, #2*var]` and writes the decoded on/off colour (a `str` word)
and bool (a `strb` byte) into `0x2003xxxx` SRAM cells. A widget shows the live state when its
record's fillA mode-7 colour index resolves (through the `GuiData+0x44BC8` table) to that
colour cell (and/or its `value_slot` resolves to the bool cell).

This module joins two halves:
  * GuiData (this package): the variable table, the `0x44BC8` value/colour cell table,
    control groups, per-object records, and the per-floorplan screen walk (`layout.Screen`).
  * GuiCode: the `FUN_00247BF8` slot->cell bridge, extracted once by
    `ghidra/extract_slot_bridge.py` and committed as `data/slot_bridge_<build>.txt` (read here,
    not re-disassembled). Only CLEAN 2-dest blocks (one word colour store + one byte bool store)
    are trusted; multi-dest latch-master fan-outs are excluded because they over-attribute.

`build_report()` renders `docs/STATUS-INDICATOR-MAP.md`. Run as a script to regenerate it.

Confidence: the backbone (bridged/indexed/cell/consumer) is `proven-from-code` (cited GuiCode
reads + GuiData table lookups). The per-floorplan *visibility* overlay is **best-effort**: the
screen walk under-approximates floorplan light-grid expansion (CLAUDE.md multi-root note), so a
"displayed" set is a lower bound and a Tier-1 candidate it lists could still be shown by an
unexpanded grid cell. The backbone consumer column is the conservative cross-check.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

from . import guidata, records, statusjoin, layout, options, cli

DGN_DC_DIMMER_STATUS_3 = guidata.DGN_DC_DIMMER_STATUS_3
CELL_TABLE_OFF = 0x44BC8            # GuiData-relative base of the DAT_00253D04 pointer table
CELL_TABLE_LEN = 665               # entries (MCF-FORMAT §5.5 / GUICODE Finding 17)
# ...but BOTH of those are 13.6's values, and this table MOVES between builds: 11.23 puts it at
# 0x44B60 (26 entries lower) and 14.7 at 0x42B74. Using the 13.6 constants on another build reads
# the right table at the wrong base, so every index resolves to a cell ~26 entries away -- which
# looks plausible (they are all SRAM addresses in the same region) and is silently wrong. That
# produced a confident cross-build claim in session on 2026-08-23 before a control caught it.
# `cell_table()` below locates the base per build; the constants remain for 13.6-pinned callers
# (`statusbind.index_cell`, whose edits are all 13.6 same-size patches).
SRAM_LOW, SRAM_HIGH = 0x20030000, 0x20040000

# Floorplans are driven via layout switch slot 5 (GUICODE Finding 16; floorplan value -> the
# match-table group name). Derived from the bytes per build -- 13.6 declares 14 (values 1-14,
# the last four unreachable from the selection screen, MCF-FORMAT §7), 14.7 declares 9. An
# earlier revision hardcoded a 12-entry list, which silently skipped 3NK/3NJ and could not
# follow another build at all.
def floorplans(data: bytes) -> list[tuple[int, str]]:
    """`[(slot-5 value, floorplan code)]` for this build, ascending."""
    return sorted(options.floorplan_options(data).items())
PAGES = ("home", "lights", "electrical", "settings", "climate")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _bridge_path(build: str) -> str:
    return os.path.join(_DATA_DIR, "slot_bridge_%s.txt" % build)


def load_bridge(build: str = "136") -> dict[int, dict]:
    """Parse the committed GuiCode bridge dump into `{var: {...}}` for CLEAN 2-dest blocks.

    Returns per var: `colour_cell` (word store), `bool_cell` (byte store), `read_addr`.
    Vars with no clean 2-dest block are absent (i.e. not cleanly bridged).
    """
    out: dict[int, dict] = {}
    with open(_bridge_path(build), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"var=(\d+) read@(\w+) ndest=(\d+) (.*)", line)
            if not m or int(m.group(3)) != 2:
                continue
            var, raddr = int(m.group(1)), m.group(2)
            cells = [(int(a, 16), mn)
                     for a, mn in (t.split(":") for t in m.group(4).split(",") if ":" in t)]
            word = [c for c, mn in cells if mn in ("str", "str.w", "strh", "strh.w")]
            byte = [c for c, mn in cells if mn.startswith("strb")]
            if len(word) == 1 and len(byte) == 1:
                out[var] = {"colour_cell": word[0], "bool_cell": byte[0], "read_addr": raddr}
    return out


def cell_table(data: bytes) -> list[int]:
    """The value-slot pointer table as a list of SRAM cells, **located per build**.

    A widget's `value_slot` indexes this table (GUICODE Finding 17), so getting the base wrong
    does not fail — it returns a neighbouring cell, which is still a plausible-looking SRAM
    address. `guidata.find_variable_tables` finds the base structurally: 13.6 `0x44BC8`, 11.23
    `0x44B60`, 14.7 `0x42B74`.

    The length is derived rather than assumed (13.6's 665 is not universal): entries are SRAM
    pointers, and a slot that is unused reads as something outside that range, so the walk
    tolerates gaps and stops only after a sustained run of non-pointers. Non-pointer entries come
    back as 0, which callers must treat as "no cell" rather than as address zero.
    """
    gs, _ = guidata.guidata_extent(data)
    base, _types = guidata.find_variable_tables(data)
    out: list[int] = []
    miss = 0
    for i in range(4096):
        off = gs + base + i * 4
        if off + 4 > len(data):
            break
        word = struct.unpack_from("<I", data, off)[0]
        if SRAM_LOW - 0x10000 <= word < SRAM_HIGH:
            out.append(word)
            miss = 0
        else:
            out.append(0)
            miss += 1
            if miss > 32:
                break
    while out and out[-1] == 0:
        out.pop()
    return out


def _cell_to_indices(table: list[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for idx, c in enumerate(table):
        out.setdefault(c, []).append(idx)
    return out


@dataclass
class Consumers:
    colour: dict[int, list[int]] = field(default_factory=dict)   # table idx -> objects (mode6/7)
    vslot: dict[int, list[int]] = field(default_factory=dict)    # table idx -> objects (value_slot)


def _scan_table_consumers(data: bytes, gs: int, table: list[int], con: Consumers) -> None:
    """Fold one object-pointer table's records into `con` in place. `obj` indices are local to
    `table` (the same convention every other table-1 caller here already used); a caller that
    folds multiple tables gets object indices that alias across tables, which is fine -- this
    module only ever reports table indices (cell indices) as the return value, using `obj` lists
    solely as internal cross-checks (`grid_objects`, `idle_safe_targets`)."""
    for obj, off in enumerate(table):
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for r in recs:
            vs = getattr(r, "value_slot", None)
            if isinstance(vs, int) and 0 <= vs < CELL_TABLE_LEN:
                con.vslot.setdefault(vs, []).append(obj)
            for entry in (getattr(r, "colors", None) or []):
                if len(entry) > 2 and entry[1] in (6, 7) and isinstance(entry[2], int) \
                        and 0 <= entry[2] < CELL_TABLE_LEN:
                    con.colour.setdefault(entry[2], []).append(obj)


def object_consumers(data: bytes) -> Consumers:
    """For every object in GuiData's main object-pointer table (`guidata.object_tables(data)[1]`),
    which `+0x44BC8` table indices its records reference as a mode-6/7 colour or as a
    `value_slot`. Decoded with the correct `gs+table[obj]` idiom (an offset-only call reads
    subtree garbage).

    Scans table 1 only, as it always has -- its existing callers (`grid_objects`,
    `idle_safe_targets`, `spare_cell_indices`) are calibrated to that. The all-four-tables view
    needed to detect cross-table cell aliasing is `all_table_cell_consumers` below; the honest
    spare computation is migrated onto it in a later task, together with the fresh-cell allocator
    that grows the table when the (correctly empty) pool is exhausted -- flipping this function
    alone would strand every cell-allocating op with no allocator to grow for it."""
    gs, _ = guidata.guidata_extent(data)
    con = Consumers()
    _scan_table_consumers(data, gs, guidata.object_tables(data)[1], con)
    for d in (con.colour, con.vslot):
        for k in d:
            d[k] = sorted(set(d[k]))
    return con


def foreign_table_refs(data: bytes, cells: "set[int]") -> dict[int, list[tuple[int, int]]]:
    """For each cell index in `cells`, the `(table_index, object_index)` of any object in object
    tables 0, 2 or 3 that references it as a mode-6/7 colour or a `value_slot`.

    This is the cross-table view the renderer's page walk cannot produce (it traverses only table
    1's reachable tree). Scanning tables 0/2/3 for a specific, existing cell index is what
    demonstrates the coach-48V aliasing: `foreign_table_refs(stock, {254,256,258})` names the
    3295-entry-table objects that painted the background bars from cells the table-1-only spare
    scan reported free.

    Match a specific `cells` set, never a range: the 30- and 3295-entry tables carry repeated and
    garbage pointers that decode to nonsense out-of-range `value_slot`s, so a range scan (e.g. "any
    index >= the row count") is dominated by decode noise. A freshly-GROWN index is never really
    referenced by these tables at all -- that safety is enforced structurally (a grown index is a
    new appended index nothing pre-existing names, `readout.allocate_fresh_cells`), and gated by
    `recipe._assert_authored_cells_fresh`, not by this scan.
    """
    if not cells:
        return {}
    gs, _ = guidata.guidata_extent(data)
    tables = guidata.object_tables(data)
    out: dict[int, list[tuple[int, int]]] = {}
    for ti in (0, 2, 3):
        for obj, off in enumerate(tables[ti]):
            try:
                recs = records.object_records(data, gs + off)
            except Exception:
                continue
            for r in recs:
                refs: list[int] = []
                vs = getattr(r, "value_slot", None)
                if isinstance(vs, int) and vs in cells:
                    refs.append(vs)
                for entry in (getattr(r, "colors", None) or []):
                    if len(entry) > 2 and entry[1] in (6, 7) and entry[2] in cells:
                        refs.append(entry[2])
                for idx in refs:
                    out.setdefault(idx, []).append((ti, obj))
    return out


def all_table_cell_consumers(data: bytes) -> set[int]:
    """Every `+0x44BC8` cell index referenced as a mode-6/7 colour or a `value_slot` by any
    object in ANY of the four GuiData object-pointer tables (`guidata.object_tables`), sanity-
    filtered to `0 <= idx < CELL_TABLE_LEN` (the two big tables carry repeated/garbage pointers
    that decode to nonsense out-of-range slots).

    This is the honest consumer set: cells 254/256/258/260 are referenced only by objects in the
    3295-entry table (table index 3), so the table-1-only `object_consumers` reports them as
    unconsumed -- `spare_cell_indices` then handed them to a fresh allocation, corrupting whatever
    table-3 object actually painted from them (the coach-48V background bars, 2026-09-11). The
    true spare set is this set's complement minus `recipe_ops._gauge_level_indices` (indices named
    via an undecoded trailing field neither scan can see)."""
    gs, _ = guidata.guidata_extent(data)
    con = Consumers()
    for table in guidata.object_tables(data):
        _scan_table_consumers(data, gs, table, con)
    return set(con.colour) | set(con.vslot)


@dataclass
class StatusRow:
    instance: int
    var: int
    bridged: bool
    colour_cell: int | None
    colour_idx: list[int]              # table index(es) of the colour cell ([] = unindexed)
    bool_cell: int | None
    bool_idx: list[int]                # table index(es) of the bool cell (== value_slot)
    colour_consumers: list[int]
    bool_consumers: list[int]
    commanded: bool                    # instance is the channel of a located single-channel control


def backbone(data: bytes, build: str = "136") -> list[StatusRow]:
    """Row per DC_DIMMER_STATUS_3 variable (the display-list status vars), sorted by instance."""
    bridge = load_bridge(build)
    table = cell_table(data)
    c2i = _cell_to_indices(table)
    con = object_consumers(data)
    commanded = commanded_instances(data)

    vt = guidata.variable_table(data)
    rows: list[StatusRow] = []
    for v in vt:
        if v.dgn_low != DGN_DC_DIMMER_STATUS_3:
            continue
        b = bridge.get(v.index)
        cc = b["colour_cell"] if b else None
        bc = b["bool_cell"] if b else None
        cidx = c2i.get(cc, []) if cc is not None else []
        bidx = c2i.get(bc, []) if bc is not None else []
        ccons: list[int] = []
        for ci in cidx:
            ccons += con.colour.get(ci, [])
        bcons: list[int] = []
        for bi in bidx:
            bcons += con.vslot.get(bi, [])
        rows.append(StatusRow(
            instance=v.channel, var=v.index, bridged=b is not None,
            colour_cell=cc, colour_idx=cidx, bool_cell=bc, bool_idx=bidx,
            colour_consumers=sorted(set(ccons)), bool_consumers=sorted(set(bcons)),
            commanded=v.channel in commanded))
    rows.sort(key=lambda r: (r.instance, r.var))
    return rows


def commanded_instances(data: bytes) -> set[int]:
    """Instances commanded by ANY located control (any floorplan) — single-channel buttons AND
    multi-channel master groups. Matching the coordinator's 'free = not commanded' definition;
    a master-group channel (e.g. instance 25 in the interior master) counts as commanded."""
    out: set[int] = set()
    for c in guidata.find_control_groups(data):
        out.update(c.channels)
    return out


def _control_object(data: bytes, control) -> int | None:
    return statusjoin._object_index_for_offset(data, control.offset)


def _walk_floorplan(scr: layout.Screen, roots: dict[str, int], floorplan: int,
                    colour_idx_to_instance: dict[int, int]) -> tuple[set[int], set[int]]:
    """One walk per page under `slot_values={5:N}`; return (visible object set, displayed
    instance set) in a single pass."""
    objs: set[int] = set()
    disp: set[int] = set()
    for root in roots.values():
        if root is None:
            continue
        try:
            placed = scr.walk(root, slot_values={5: floorplan})
        except Exception:
            continue
        for pl in placed:
            objs.add(pl.obj_index)
            for e in (pl.colors or []):
                if len(e) > 2 and e[1] == 7 and isinstance(e[2], int) \
                        and e[2] in colour_idx_to_instance:
                    disp.add(colour_idx_to_instance[e[2]])
    return objs, disp


def commanded_on_floorplan(control_objs: dict[int, int], visible: set[int]) -> set[int]:
    """`control_objs` maps object index -> commanded instance."""
    return {inst for obj, inst in control_objs.items() if obj in visible}


@dataclass
class FloorplanView:
    code: str
    n: int
    commanded: set[int]
    displayed: set[int]
    in_use: set[int]


# --- consumer classification (walk-independent, so it survives the grid-expansion gap) --------
GRID_COLOUR_MIN = 3   # a consumer referencing >= this many distinct status colours is a grid


def _bridged_colour_indices(rows: list[StatusRow]) -> set[int]:
    out: set[int] = set()
    for r in rows:
        out.update(r.colour_idx)
    return out


def grid_objects(data: bytes, rows: list[StatusRow]) -> set[int]:
    """Objects whose records reference >= GRID_COLOUR_MIN distinct *status* colour indices — the
    multi-instance light grids / diagnostic status grids (obj 838, 906, 940-965 …). An indicator
    on such an object is 'in use' regardless of whether the screen walk expands it."""
    status_idx = _bridged_colour_indices(rows)
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out: set[int] = set()
    for obj, off in enumerate(table):
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        seen = set()
        for r in recs:
            for e in (getattr(r, "colors", None) or []):
                if len(e) > 2 and e[1] in (6, 7) and e[2] in status_idx:
                    seen.add(e[2])
        if len(seen) >= GRID_COLOUR_MIN:
            out.add(obj)
    return out


def per_floorplan(data: bytes, rows: list[StatusRow],
                  build: str = "136") -> tuple[list[FloorplanView], set[int]]:
    scr = layout.Screen(data)
    roots = {}
    for p in PAGES:
        try:
            roots[p] = cli._resolve_page_root(data, p)
        except Exception:
            roots[p] = None
    # colour table index -> instance, for bridged vars with a clean colour cell
    colour_idx_to_instance: dict[int, int] = {}
    for r in rows:
        for ci in r.colour_idx:
            colour_idx_to_instance[ci] = r.instance
    # object index -> commanded instance (computed once)
    control_objs: dict[int, int] = {}
    for c in guidata.find_control_groups(data):
        if len(c.channels) == 1:
            obj = _control_object(data, c)
            if obj is not None:
                control_objs[obj] = c.channels[0]
    views: list[FloorplanView] = []
    visible_union: set[int] = set()
    for n, code in floorplans(data):
        vis, disp = _walk_floorplan(scr, roots, n, colour_idx_to_instance)
        visible_union |= vis
        cmd = commanded_on_floorplan(control_objs, vis)
        views.append(FloorplanView(code=code, n=n, commanded=cmd, displayed=disp,
                                    in_use=cmd | disp))
    return views, visible_union


# ---- tiering -------------------------------------------------------------------------------

def idle_safe_targets(rows: list[StatusRow], grid_objs: set[int],
                      visible_union: set[int], displayed_any: set[int]) -> list[StatusRow]:
    """High-confidence, floorplan-independent cheap targets: free (not commanded), bridged,
    colour cell indexed, colour cell consumed by NO grid object and NO on-page (walk-visible)
    object, and never displayed on any floorplan. These stay valid across every floorplan
    because their only consumers are off-page diagnostic/config indicators."""
    off_page = grid_objs | visible_union
    out = []
    for r in rows:
        if r.commanded or not r.bridged or not r.colour_idx:
            continue
        if r.instance in displayed_any:
            continue
        if any(o in off_page for o in r.colour_consumers):
            continue
        out.append(r)
    return out


def tier1_targets(rows: list[StatusRow], view: FloorplanView) -> list[StatusRow]:
    """Best-effort per-floorplan cheap targets: not commanded, bridged, indexed, and not in use
    on this floorplan per the walk. NOTE: the walk under-expands light grids, so this is a
    superset of the truly-idle set — cross-check the backbone `colour consumers` column."""
    out = []
    for r in rows:
        if r.commanded or not r.bridged or not r.colour_idx:
            continue
        if r.instance in view.in_use:
            continue
        out.append(r)
    return out


def tier2_targets(rows: list[StatusRow]) -> list[StatusRow]:
    """Free + bridged but colour cell UNINDEXED -> needs a relocator pointer-table insert."""
    return [r for r in rows if not r.commanded and r.bridged and not r.colour_idx]


# ---- doc rendering -------------------------------------------------------------------------

def _fmt_objs(objs: list[int], limit: int = 8) -> str:
    if not objs:
        return "-"
    s = ", ".join(str(o) for o in objs[:limit])
    return s + (" …" if len(objs) > limit else "")


def build_report(data: bytes, build: str = "136") -> str:
    rows = backbone(data, build)
    views, visible_union = per_floorplan(data, rows, build)
    grids = grid_objects(data, rows)
    displayed_any: set[int] = set()
    for v in views:
        displayed_any |= v.displayed
    idle_safe = idle_safe_targets(rows, grids, visible_union, displayed_any)
    L: list[str] = []
    a = L.append

    a("# Status-indicator map — which RV-C instances can drive an on-screen indicator")
    a("")
    a("*Generated by `src/fireflymcf/status_map.py` (build 13.6). Do not hand-edit; "
      "regenerate with `python3 -m fireflymcf.status_map firefly-13.6/mcf.bin`.*")
    a("")
    a("## 1. Mechanism (GUICODE Finding 33)")
    a("")
    a("LCD hands GuiCode's paint tick the receive-filter **slot-array base** `0x20007fc8`, which "
      "reaches the 25 KB `FUN_00247BF8`. That function reads `slot[var]` at a **hard-coded** "
      "immediate `ldrsh [r4,#2*var]` and writes the decoded on/off **colour** (a `str` word) and "
      "**bool** (a `strb` byte) into `0x2003xxxx` SRAM cells. A widget shows the live state when "
      "its record's **fillA mode-7 colour index** resolves through the `GuiData+0x44BC8` table to "
      "that colour cell (and/or its `value_slot` resolves to the bool cell). So a status instance "
      "is *usable* for a lit indicator iff `FUN_00247BF8` bridges it **and** its colour cell is "
      "present in that table (INDEXED); otherwise the pointer must be inserted (relocator).")
    a("")

    # backbone table
    a("## 2. Backbone — every status instance (floorplan-independent, `proven-from-code`)")
    a("")
    a("`var` = variable-table index (= receive-filter slot). `bridged` = `FUN_00247BF8` has a "
      "clean 2-dest `ldrsh [r4,#2*var]` block. `colour idx` / `bool vslot` = the `0x44BC8` table "
      "index of the colour / bool cell (blank colour idx = **UNINDEXED**). `cmd` = commanded by a "
      "located single-channel control. Consumers are the objects currently reading each cell.")
    a("")
    a("| inst | var | cmd | bridged | colour cell | colour idx | bool cell | bool vslot | colour consumers | bool consumers |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a("| %d | %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r.instance, r.var, "Y" if r.commanded else "", "Y" if r.bridged else "",
            ("0x%08x" % r.colour_cell) if r.colour_cell else "-",
            (",".join(map(str, r.colour_idx)) if r.colour_idx else ("**none**" if r.bridged else "-")),
            ("0x%08x" % r.bool_cell) if r.bool_cell else "-",
            (",".join(map(str, r.bool_idx)) if r.bool_idx else "-"),
            _fmt_objs(r.colour_consumers), _fmt_objs(r.bool_consumers)))
    a("")

    # high-confidence, floorplan-independent
    a("## 3. Recommended targets — Tier 1 (high confidence, floorplan-independent)")
    a("")
    a("Free (not commanded), bridged, colour cell **indexed**, and the colour cell is consumed "
      "**only by off-page diagnostic/config indicators** (no floorplan light-grid object, no "
      "walk-visible on-page control) and never displayed on any floorplan. These stay valid "
      "across **all %d floorplans** — the safest same-size-edit targets." % len(views))
    a("")
    if idle_safe:
        a("| instance | var | fillA mode-7 colour idx | value_slot (bool) | colour cell | consumer(s) |")
        a("|---|---|---|---|---|---|")
        for r in idle_safe:
            a("| %d | %d | %s | %s | 0x%08x | %s |" % (
                r.instance, r.var, ",".join(map(str, r.colour_idx)),
                ",".join(map(str, r.bool_idx)) or "-", r.colour_cell or 0,
                _fmt_objs(r.colour_consumers)))
        a("")
        a("The **purest** targets are those whose only consumer is an off-page *diagnostic* "
          "indicator (the G12-OUTPUTS / model-config status grids) rather than a named "
          "sub-screen control — currently instances **27, 28, 29, 30** (consumers obj 835/837). "
          "The rest (15/19/20/31) share their colour cell with a control on a non-floorplan "
          "sub-screen (Start / GEN / Low), so the instance may be wired to that function; still "
          "same-size-editable, but confirm the instance is spare on the coach.")
    else:
        a("**NONE** — every indexed free instance's colour cell is consumed by an on-page or "
          "grid object; use a Tier-2 (relocator) target instead.")
    a("")

    # per-floorplan
    a("## 4. Per-floorplan availability (best-effort visibility overlay)")
    a("")
    a("Driven by `layout.Screen.walk(slot_values={5:N})` across "
      "`home/lights/electrical/settings/climate`. **Best-effort:** the walk under-expands the "
      "floorplan light grids (CLAUDE.md multi-root note), so `displayed` is a *lower bound* — a "
      "Tier-1 entry could still be shown by an unexpanded grid cell; cross-check the backbone "
      "`colour consumers` column (a floorplan-light-grid consumer such as obj 906/946–965 means "
      "the instance is a light, not idle).")
    a("")
    for v in views:
        t1 = tier1_targets(rows, v)
        a("### Floorplan %s (slot 5 = %d)" % (v.code, v.n))
        a("")
        a("- **in use** (commanded ∪ displayed): %s" %
          (", ".join(map(str, sorted(x for x in v.in_use if x is not None))) or "none"))
        a("- **Tier 1 (walk best-effort, superset)** — free + bridged + indexed + not in use "
          "here; ⚠ cross-check §2 consumers, many are floorplan lights the walk missed: %s" %
          (", ".join("inst %d" % r.instance for r in t1) or "**NONE**"))
        a("")

    # tier 2 (floorplan-independent)
    t2 = tier2_targets(rows)
    a("## 5. Tier 2 — free + bridged but UNINDEXED colour cell (needs a relocator insert)")
    a("")
    a("These have a live status feed but no table entry for the colour cell, so a lit indicator "
      "needs a pointer-table insertion (relocator), not a same-size edit — e.g. **instance 17**.")
    a("")
    a("| inst | var | colour cell (unindexed) | bool vslot |")
    a("|---|---|---|---|")
    for r in t2:
        a("| %d | %d | 0x%08x | %s |" % (
            r.instance, r.var, r.colour_cell or 0,
            ",".join(map(str, r.bool_idx)) or "-"))
    a("")

    a("## 6. How to use")
    a("")
    a("To light a **new cloned button**'s indicator with a **same-size edit (no relocator)**: pick "
      "a Tier-1 instance for your floorplan, command that **RV-C instance**, and set *three* "
      "fields of the cloned button to the **same** `<colour idx>` — the box record's **fillA "
      "mode-7 colour index**, the box record's **`value_slot`**, and the **caption record's "
      "`value_slot`**. `FUN_00247BF8` already reads that instance's slot every paint, so the "
      "indicator then tracks the live output. For a Tier-2 instance (e.g. 17), the colour cell "
      "must first be inserted into the `0x44BC8` table via the relocator.")
    a("")
    a("**Use the colour index for all three, not the `bool vslot` column.** The mode-7 colour "
      "table and the value-slot table are the same table (GUICODE Finding 18), so index N and "
      "slot N are one cell, and the live-redraw invariant (`MCF-FORMAT.md` §6.0a) requires all "
      "three fields to name that one cell. Splitting them — fillA at the colour index while a "
      "`value_slot` names the bool cell — is a real failure mode, not a theoretical one: it was "
      "flashed (on pin 15) and gave a button whose **box repainted live but whose caption "
      "vanished** until the page was re-entered. Setting all three equal fixed it on the "
      "coach. The `bool vslot` column below is documentation of where the bool cell sits, not a "
      "value to write into a widget.")
    a("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="regenerate docs/STATUS-INDICATOR-MAP.md")
    p.add_argument("mcf", help="path to the MCF (e.g. firefly-13.6/mcf.bin)")
    p.add_argument("--build", default="136", help="bridge-data build id (default 136)")
    p.add_argument("--out", default="docs/STATUS-INDICATOR-MAP.md")
    args = p.parse_args(argv)
    data = open(args.mcf, "rb").read()
    text = build_report(data, args.build)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s (%d bytes)" % (args.out, len(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
