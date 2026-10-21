"""Keyed page walks, containers included, and their diff.

`layout.Screen.walk` replays the layout engine (`FUN_00257BFC`) and returns `Placed` leaves,
each identified by its `record_offset` -- a file offset that changes whenever an earlier object
grows or shrinks. A measurement that must survive relocation keys each record by its POSITION in
the walk instead: the chain of `(object index, record index within the object)` steps from the
root down to the record (`layout.Screen.walk`'s `trace`). The chain includes the attach and
switch records (wtype 5/6/0x2e) the walk passes through, and the walk lists those containers
too, before their children, so an edit to an attach is visible as a delta on the attach itself.

Removing records from one object renumbers only that object's records: `remap_after_removal`
rewrites a key's steps through that object, and `diff` takes the resulting before-to-after key
map.

A walk depends on switch values. `switch_arm_sets` gives the value sets that cover every arm of
every type-6 switch a page reaches: the base walk (`{}`), then each switch's arms one at a time
with every other switch at its base.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import guidata, layout, memo, records

# Container types the walk recurses into (`FUN_00256C54` dispatches 5/6/0x2E to `FUN_00258330`;
# GUICODE Finding 15). The `+0x28` field of any other record is not an object index.
_CONTAINERS = (5, 6, 0x2E)
_SWITCHES = (6, 0x2E)


@dataclass(frozen=True)
class WalkRec:
    """One record as the walk placed it."""
    key: tuple                 # ((obj, rec_index), ...) from the root to the record
    wtype: int
    label: str | None
    obj: int                   # the record's own object
    index: int                 # its index in `records.object_records` of that object
    box: tuple                 # (x0, y0, x1, y1), as `Placed` carries it (after the align block)
    font: int | None           # persistent state.font after this record
    align: int                 # persistent state.align after this record
    colors: tuple              # effective colours (`layout._apply_colors`)
    value_slot: int | None
    in_regs: tuple
    out_regs: tuple


def walk_records(data: bytes, root: int, switch_values: dict | None = None,
                 slot_values: dict | None = None) -> list[WalkRec]:
    """Every record the walk of `root` passes, in walk order (a container before its
    children). `switch_values`/`slot_values` are passed to `layout.Screen.walk`."""
    trace: list = []
    layout.Screen(data).walk(root, switch_values=switch_values, slot_values=slot_values,
                             trace=trace)
    return [WalkRec(key=key, wtype=rec.wtype, label=rec.label, obj=obj, index=index,
                    box=box, font=font, align=align, colors=colors,
                    value_slot=rec.value_slot, in_regs=tuple(rec.in_regs),
                    out_regs=tuple(rec.out_regs))
            for key, obj, index, rec, box, font, align, colors in trace]


@memo.content_cached
def _container_graph(data: bytes) -> dict[int, list[int]]:
    """`{object: children}` over the edges the walk can take: the `child_index` of each
    container record (wtype 5/6/0x2E), and for a switch every candidate in its scan range
    `[child_index, child_index + count)` (`guidata.switch_candidates`), not only its base.

    This mirrors `layout.Screen.walk`'s recursion. `insert.object_child_graph` is not used:
    it counts the `+0x28` field of wtype-10/18 records as a child, and that field is an image
    index (0..7, GUICODE Finding 15), not an object, so it would let a root seem to reach
    objects 0-7. It also lists a switch's base only, not its other arms."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    try:
        match_off = guidata.find_match_table(data)
    except guidata.MatchTableNotFound:
        match_off = None
    graph: dict[int, list[int]] = {}
    for index, start in enumerate(table):
        if not (0 < start < ge - gs):
            continue
        try:
            recs = records.object_records(data, gs + start)
        except (IndexError, ValueError):
            continue
        children: set[int] = set()
        for r in recs:
            if r.wtype not in _CONTAINERS or r.child_index is None:
                continue
            children.add(r.child_index)
            if r.wtype in _SWITCHES and match_off is not None:
                children.update(obj for _v, obj in
                                guidata.switch_candidates(data, r, match_off))
        graph[index] = sorted(c for c in children if c < len(table))
    return graph


def reaching_roots(data: bytes, obj: int) -> list[int]:
    """The sorted screen roots (`layout.screen_object_map` values) whose object graph, every
    switch arm included, reaches `obj`."""
    graph = _container_graph(data)
    out = []
    for root in sorted(set(layout.screen_object_map(data).values())):
        seen, todo = {root}, deque([root])
        while todo:
            node = todo.popleft()
            if node == obj:
                out.append(root)
                break
            for child in graph.get(node, ()):
                if child not in seen:
                    seen.add(child)
                    todo.append(child)
    return out


def _reaches(graph: dict, start: int, target: int) -> bool:
    seen, todo = {start}, deque([start])
    while todo:
        node = todo.popleft()
        if node == target:
            return True
        for child in graph.get(node, ()):
            if child not in seen:
                seen.add(child)
                todo.append(child)
    return False


def switch_arm_sets(data: bytes, roots, touching: int | None = None) -> list[dict]:
    """The `switch_values` sets that cover every arm of every type-6 switch the base walks of
    `roots` reach: `{}` first, then `{child_base: value}` for each (switch, distinct candidate
    match value) whose selected candidate is not the base one, in walk order. Each set drives
    one switch; the rest stay at their base, so a switch nested in a non-base arm is not reached
    (its parent is at base) and is not listed.
    `layout.Screen.walk` selects an arm for wtype 6 only, so 0x2E switches are not listed.

    `touching`, an object index, keeps only the sets under which an edit inside that object can
    show a shift the base walk does not: a switch whose selected arm reaches `touching` (the
    edit is drawn only under it), or one walked after the walk first passes through `touching`
    (its arm lays out from a frame the edit can move). A switch walked wholly before is laid
    out before any record the edit can move (inference from the walk's sequential frame,
    `layout.Screen.walk`)."""
    match_off = guidata.find_match_table(data)
    graph = _container_graph(data) if touching is not None else None
    sets: list[dict] = [{}]
    first: dict = {}
    added: set = set()
    for root in roots:
        past = touching is None
        for r in walk_records(data, root):
            past = past or any(o == touching for o, _i in r.key)
            if r.wtype != 6:
                continue
            rec = _record(data, r.obj, r.index)
            for value, obj in guidata.switch_candidates(data, rec, match_off):
                # the first candidate with a value is the one `_switch_child` selects for it;
                # the base candidate is what the base walk already draws
                arm = first.setdefault((rec.child_index, value), obj)
                if (arm == rec.child_index or (rec.child_index, value) in added
                        or not (past or _reaches(graph, arm, touching))):
                    continue
                added.add((rec.child_index, value))
                sets.append({rec.child_index: value})
    return sets


def _record(data: bytes, obj: int, index: int) -> records.WRecord:
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][obj])[index]


def remap_after_removal(key: tuple, obj: int, removed: set[int]) -> tuple | None:
    """`key` with each `(obj, i)` step renumbered for records `removed` from `obj`: `i` less
    the number of removed indices below it. None if the key passes through a removed record."""
    out = []
    for o, i in key:
        if o == obj:
            if i in removed:
                return None
            i -= sum(1 for r in removed if r < i)
        out.append((o, i))
    return tuple(out)


@dataclass
class Delta:
    """One difference between two walks. `key` is the before-key (the after-key for
    `appeared`); `dx`/`dy` are the shift of the box's top-left (0 unless `moved`)."""
    key: tuple
    wtype: int
    label: str | None
    kind: str                  # "moved", "state", "gone" or "appeared"
    before: WalkRec | None
    after: WalkRec | None
    dx: int = 0
    dy: int = 0


def _state(r: WalkRec) -> tuple:
    return (r.font, r.align, r.colors, r.value_slot)


def diff(before: list[WalkRec], after: list[WalkRec], remap: dict | None = None,
         ignore=frozenset()) -> list[Delta]:
    """The differences from walk `before` to walk `after`, in before-walk order with the
    `appeared` entries last (in after-walk order).

    `remap` maps a before-key to its after-key, or to None to drop the record from the
    comparison; an unmapped key compares with the same key. `ignore` holds keys to leave out:
    a before-key, or an after-key for a record that would otherwise be `appeared`.

    A record whose box differs is `moved`; one whose box is equal but whose font, align,
    colours or `value_slot` differ is `state`. A key with no record after it, or one that
    lands on a record of another wtype, is `gone` (and that record `appeared`). Only boxes
    and inherited state are compared, so a label or register change that keeps the box gives
    no delta."""
    remap = remap or {}
    by_key = {r.key: r for r in after}
    matched: set = set()
    out: list[Delta] = []
    for b in before:
        if b.key in ignore:
            continue
        k = remap.get(b.key, b.key)
        if k is None:
            continue
        a = by_key.get(k)
        if a is None or a.wtype != b.wtype:
            out.append(Delta(b.key, b.wtype, b.label, "gone", b, None))
            continue
        matched.add(k)
        if a.box != b.box:
            out.append(Delta(b.key, b.wtype, b.label, "moved", b, a,
                             a.box[0] - b.box[0], a.box[1] - b.box[1]))
        elif _state(a) != _state(b):
            out.append(Delta(b.key, b.wtype, b.label, "state", b, a))
    out.extend(Delta(a.key, a.wtype, a.label, "appeared", None, a)
               for a in after if a.key not in matched and a.key not in ignore)
    return out


def _fmt_key(key: tuple) -> str:
    return "/".join("%d:%d" % step for step in key)


def _fmt_side(r: WalkRec | None, kind: str) -> str:
    if r is None:
        return "-"
    if kind == "state":
        return "font=%r align=%d value_slot=%r colors=%r" % (r.font, r.align, r.value_slot,
                                                             r.colors)
    return "(%d,%d,%d,%d)" % r.box


def describe(deltas: list[Delta]) -> str:
    """One line per delta: `key wtype 'label' kind (dx,dy) before→after`."""
    return "\n".join("%s wtype %d %r %s (%d,%d) %s→%s"
                     % (_fmt_key(d.key), d.wtype, d.label, d.kind, d.dx, d.dy,
                        _fmt_side(d.before, d.kind), _fmt_side(d.after, d.kind))
                     for d in deltas)
