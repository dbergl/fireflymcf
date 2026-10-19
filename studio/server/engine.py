"""Compile/render facade over the recipe engine. Holds the only server state:
per-project prefix caches (recipe.compile_cached's caller-owned dicts) and an
etag-keyed memo of results. The etag covers everything that affects output
bytes: the enabled-op prefix key plus the build name (which recipe applies in
_finish, outside the prefix key)."""
from __future__ import annotations

import hashlib
import io
import struct
from collections import OrderedDict

from fireflymcf import recipe

# Bounds for the per-knob-tag memos below. A placements body is a few tens of KB of
# plain dicts and a PNG is ~50-100 KB, so even full these hold a few MB -- enough that
# every page of a working session stays warm, small enough to never matter.
_PLACEMENTS_MEMO_MAX = 128
_PNG_MEMO_MAX = 64


def _memo_put(memo: OrderedDict, key: str, value, cap: int) -> None:
    memo[key] = value
    memo.move_to_end(key)
    while len(memo) > cap:
        memo.popitem(last=False)


class EngineError(Exception):
    def __init__(self, code: str, payload, status: int = 422):
        super().__init__(code)
        self.code = code
        self.payload = payload
        self.status = status


def parse_pairs(text: str) -> dict:
    """'5:8,109:1' -> {5: 8, 109: 1}; empty/None -> {}."""
    out = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition(":")
        try:
            out[int(k)] = int(v)
        except ValueError:
            raise EngineError("BAD_QUERY", {"message": "slots/switches want K:V "
                              "integer pairs, got %r" % part}, status=422)
    return out


def resolve_slots(data: bytes, preview: dict, slots: str) -> str:
    """The slot string a render actually runs at: `slots=` if given, else the preview.

    An explicit `slots=` still goes through the panel's own derivations
    (`options.derived_slots`) before it is used, with every slot the caller named left
    exactly as they wrote it. Without that, the query escape hatch quietly renders a
    *different* screen from the saved preview it is meant to reproduce -- the caller
    would have to know, and re-type, every slot the firmware computes for itself, which
    is the one thing they cannot be expected to know. It is still an escape hatch:
    naming a derived slot explicitly overrides the derivation.
    """
    if not slots:
        return preview_slots(data, preview)
    from fireflymcf import options as optmod

    values = parse_pairs(slots)
    for slot, value in optmod.derived_slots(data, values).items():
        values.setdefault(slot, value)
    return ",".join("%d:%d" % kv for kv in sorted(values.items()))


def preview_slots(data: bytes, preview: dict) -> str:
    """`project.preview` -> the same `"5:8,109:1"` string the `slots=` query takes.

    Returning the STRING, not a dict, is deliberate: the caller feeds it to both
    `parse_pairs` and the response ETag, so the preview is covered by the existing knob
    hash by construction. `Engine.etag` excludes preview on purpose (it is not build
    input), so without this two floorplans would serve each other's cached PNG.

    The slot arithmetic stays here, in `options.slot_values`, and is never reimplemented
    client-side: AGS / AES / AES Single Output are a radio on ONE slot distinguished by
    the value written (`options._option_value`), and a blind `1` per option silently
    renders "AES Single Output" as AGS. The floorplan's own slot number is build-derived
    too (5 on 13.6, different on 14.7)."""
    from fireflymcf import options as optmod
    from studio.server.reference import RUNTIME_GATE_SLOTS

    if not preview:
        # Still emit the runtime gates: they are what the panel sets on entering a page,
        # so they apply to a project that has no coach config yet exactly as to one that
        # does. Returning "" here would make a fresh project the one case where the
        # canvas does NOT draw what the panel draws.
        return ",".join("%d:%d" % kv for kv in sorted(RUNTIME_GATE_SLOTS.items()))
    # The UI speaks canonical option names, but `slot_values` resolves against the
    # verbatim OPTIONS-row label -- on 13.6 the cargo light is stored as
    # 'Cargo / Bath Light\n    (CH. 25)'. Map back, or every preview containing it 422s.
    # The rule itself is imported, not restated: two spellings of the same join would
    # drift silently, and the whole point of canonicalising is that both ends agree.
    from studio.server.reference import canonical_option

    verbatim = {}
    for b in optmod.option_bindings(data):
        verbatim[canonical_option(b.name)] = b.name
    chosen = [verbatim.get(n, n) for n in (preview.get("options") or [])]
    # JSON object keys are strings, so a saved preview's extra_slots arrives as
    # {"512": 1}; slot_values indexes by int. Coerce, and let a bad key 422 rather than
    # TypeError deep inside the merge.
    extra = {}
    for k, v in (preview.get("extra_slots") or {}).items():
        try:
            extra[int(k)] = int(v)
        except (TypeError, ValueError):
            raise EngineError("BAD_PREVIEW",
                              {"message": "extra_slots wants integer slot:value pairs, "
                                          "got %r: %r" % (k, v)}, status=422)
    # Runtime gates first, so an explicit extra_slots entry can still override one.
    from studio.server.reference import RUNTIME_GATE_SLOTS

    merged = dict(RUNTIME_GATE_SLOTS)
    merged.update(extra)
    cfg = optmod.CoachConfig(floorplan=preview.get("floorplan") or None,
                             options=chosen,
                             extra_slots=merged)
    try:
        values = optmod.slot_values(data, cfg)
    except (KeyError, ValueError) as e:
        # slot_values raises KeyError naming the unknown floorplan/option and listing
        # what WAS available -- a user-fixable mistake, so 422 rather than a 500.
        raise EngineError("BAD_PREVIEW", {"message": str(e)}, status=422)
    # Slots the PANEL computes rather than receives: the HOME battery block follows the
    # generator setting (see reference.derived_slots). Applied after the chosen options
    # so it can read them, and skipped for any slot the preview set by hand -- an
    # explicit extra_slots entry stays the escape hatch it is for the runtime gates.
    from studio.server.reference import derived_slots

    for slot, value in derived_slots(data, values).items():
        if slot not in extra:
            values[slot] = value
    return ",".join("%d:%d" % (k, v) for k, v in sorted(values.items()))


KIND = {33: "box", 11: "label", 12: "touch", 5: "attach",
        # wtype 0 is the single-line text/value widget, and a wtype-0 record that
        # carries stored text is static text -- the `G12 OUTPUTS` rows are all wtype 0,
        # not wtype 11. Classifying them as bare "widget" advertised only move_element,
        # so the one thing anyone actually wants to do to them (rename '29 - ' to
        # '29 - Starlink', which is two of the reference build's own ops) had no route
        # through the editor at all.
        #
        # Deliberately NOT wtype 13: those are the 14 DYNAMIC rows GuiCode fills over
        # the bus from its own table (MCF-FORMAT.md 6.1). Renaming one edits a string
        # the panel overwrites at runtime, so offering it would be offering a no-op.
        0: "text"}
EDITABLE = {
    "label": ["rename_label", "set_font", "move_element"],
    "text": ["rename_label", "set_font", "move_element"],
    # `set_icon` sits on the box because it targets the OBJECT, not a specific record:
    # the engine locates the icon (the wtype-11 record carrying its own font selector)
    # itself. That is the same shape as recolor and remove_control, which are already
    # here. It is deliberately NOT on "label" -- a caption and an icon are both wtype-11
    # placements and nothing in the payload distinguishes them, so advertising it there
    # would attach the control to whichever one happened to be found first.
    "box": ["recolor", "move_element", "remap_channel", "wire_button",
            "set_icon", "remove_control"],
    "touch": ["set_gesture", "move_element"],
    "attach": ["move_attach"],
    "widget": ["move_element"],
}
_ALL_PHASES = (1, 2, 4, 8)


class Engine:
    def __init__(self, stock: bytes, base_sha256: str):
        self.stock = stock
        self.base_sha256 = base_sha256
        self._caches: dict[str, dict] = {}       # project name -> prefix cache
        self._results: dict[str, recipe.CompileResult] = {}   # etag -> result
        # Knob-tag-keyed memos (the tag already covers compile etag + root + every
        # preview knob, see app._knob_tag), so a revisited page is served from memory
        # instead of re-walking the layout / re-rendering the pixels.
        self._placements_memo: OrderedDict[str, dict] = OrderedDict()
        self._png_memo: OrderedDict[str, bytes] = OrderedDict()

    def etag(self, project: recipe.Project) -> str:
        enabled = [s for s in project.ops if s.enabled]
        key = recipe._prefix_key(project.base_sha256, enabled)
        return hashlib.sha256((key + "|" + (project.build_name or ""))
                              .encode()).hexdigest()

    def compile(self, name: str, project: recipe.Project):
        tag = self.etag(project)
        result = self._results.get(tag)
        if result is None:
            cache = self._caches.setdefault(name, {})
            result = recipe.compile_cached(project, self.stock, cache)
            self._results[tag] = result
        return result, tag

    def image(self, name: str, project: recipe.Project):
        result, tag = self.compile(name, project)
        if not result.ok:
            raise EngineError("COMPILE_FAILED", result.errors, status=409)
        return result.data, tag

    def render_png(self, data: bytes, root: int, tank_level: float,
                   slot_values: dict, switch_values: dict,
                   tag: str | None = None) -> bytes:
        """`tag`, if given, is the request's knob tag and memoises the PNG under it --
        bytes are immutable, so unlike the placements memo no copy is needed."""
        if tag is not None and tag in self._png_memo:
            self._png_memo.move_to_end(tag)
            return self._png_memo[tag]
        from fireflymcf import render
        im = render.render_screen_image(data, root, tank_level=tank_level,
                                        slot_values=slot_values or None,
                                        switch_values=switch_values or None)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png = buf.getvalue()
        if tag is not None:
            _memo_put(self._png_memo, tag, png, _PNG_MEMO_MAX)
        return png

    def placements(self, data: bytes, root: int,
                   slot_values: dict, switch_values: dict,
                   tag: str | None = None) -> dict:
        """The hit-test map for the browser's click overlay: every placed record's box,
        clipped to the 800x480 canvas, plus a per-object binding summary (the group/gesture/
        channels a control drives, when it is one). `KIND`/`EDITABLE` name the vocabulary
        plan 3's inspector offers per widget kind; the coherence test (`test_studio_server.py`)
        asserts every `editable` entry is a real op in `recipe.REGISTRY`. Each placement also
        carries `geom`: the record's own raw stored (x, y), or `None` if it has none -- the
        coordinate space `move_element` writes into, distinct from `box`'s layout-resolved
        absolute placement (see `_raw_geom`). A drag-to-move client must build its
        `move_element` `set`/`expect` off `geom`, never off `box`.

        `tag`, if given, is the request's knob tag (compile etag + root + knobs, see
        app._knob_tag) and memoises the whole body under it. A shallow copy is handed
        out each time so the route's own `body.update(etag=...)` cannot poison the memo.

        Each object also carries `attach`: the RAW stored (x, y) of the page root's
        wtype-5 attach record for it, or None when the root carries no such record --
        the coordinate space `move_attach` writes, which is what lets a client express
        "drag the whole control" as attach-geom + delta. Like `geom`, keys are
        independently optional (an attach record can store x and not y)."""
        if tag is not None and tag in self._placements_memo:
            self._placements_memo.move_to_end(tag)
            return dict(self._placements_memo[tag])
        from fireflymcf import layout
        screen = layout.Screen(data)
        placed = screen.walk(root,
                             switch_values=switch_values or None,
                             slot_values=slot_values or None)
        attach_map = self._attach_map(data, screen, root)
        out, objects = [], {}
        for p in placed:
            kind = KIND.get(p.wtype, "widget")
            # Exclude on the PRE-clamp box, not just the post-clamp one. For every
            # well-ordered box (x0<=x1, y0<=y1) actually seen in the corpus this is provably
            # equivalent to the post-clamp inverted check below: max(0,.)/min(bound,.) is
            # monotonic in a way that guarantees a fully-off-canvas box comes out inverted
            # (see the fix report for the walkthrough and an exhaustive sweep), and the one
            # gap -- a box whose edge lands EXACTLY on the boundary pixel -- is degenerate
            # either way. But relying on that coincidence is fragile: it breaks the moment
            # either bound in the clamp changes independently (e.g. a future non-canvas
            # clip), and it puts the reader through the same proof to convince themselves
            # nothing survives. Testing the ORIGINAL coordinates says outright what is
            # excluded (fully off-canvas, from remove_control's park_y >= 480 or an
            # off-bounds move_element) versus what a genuinely partially-visible box still
            # gets: clipped and kept.
            if p.x1 < 0 or p.x0 > 800 or p.y1 < 0 or p.y0 > 480:
                continue                      # translated off-canvas: not hit-testable
            x0, y0 = max(0, p.x0), max(0, p.y0)
            x1, y1 = min(800, p.x1), min(480, p.y1)
            if x1 < x0 or y1 < y0:
                continue                      # inverted box: not hit-testable
            x1, y1 = self._text_extent(data, p, x0, y0, x1, y1,
                                       glyph_table=screen.glyph_table)
            out.append({"obj_index": p.obj_index, "wtype": p.wtype, "kind": kind,
                        "box": [x0, y0, x1, y1], "label": p.label,
                        "font": p.font, "editable": EDITABLE[kind],
                        "geom": self._raw_geom(data, screen, p)})
            if p.obj_index not in objects:
                # First placement wins: an obj_index can be placed more than once (e.g. a
                # template reused at two spots), and the binding is a property of the
                # OBJECT (its group/gesture), not of any one placement, so later duplicates
                # correctly reuse rather than recompute it.
                objects[p.obj_index] = {
                    "binding": self._binding(data, p.obj_index, table=screen.table),
                    "attach": attach_map.get(p.obj_index)}
        body = {"canvas": [800, 480], "placements": out,
                "objects": {str(k): v for k, v in objects.items()}}
        if tag is not None:
            _memo_put(self._placements_memo, tag, body, _PLACEMENTS_MEMO_MAX)
        return dict(body)

    def _attach_map(self, data: bytes, screen, root: int) -> dict:
        """obj_index -> the page ROOT's wtype-5 attach geometry for it: whichever of
        {"x", "y"} the record stores (raw values, `move_attach`'s coordinate space).
        Only the root's own records are scanned -- the same set `move_attach` edits, so
        an object this map omits is exactly one that op would refuse with
        NO_SUCH_RECORD. First attach wins, matching `move_attach.apply`'s edit order.
        Guarded like `_raw_geom`: a malformed table yields an empty map, never a 500."""
        from fireflymcf import records
        out: dict[int, dict] = {}
        if not (0 <= root < len(screen.table)) or not screen.table[root]:
            return out
        try:
            recs = records.object_records(data, screen.gs + screen.table[root])
        except (IndexError, ValueError):
            return out
        for rec in recs:
            if rec.wtype != 5 or rec.child_index is None:
                continue
            geom = {}
            for key, dim in (("x", 0), ("y", 1)):
                try:
                    off = records.geom_offset(data, rec, dim)
                except ValueError:
                    continue
                if off is not None:
                    geom[key] = struct.unpack_from("<H", data, off)[0]
            out.setdefault(rec.child_index, geom)
        return out

    def _text_extent(self, data: bytes, p, x0: int, y0: int, x1: int, y1: int,
                     glyph_table=None):
        """Give a text record that reports a DEGENERATE box the extent it actually draws.

        A type-0/8 widget's frame comes from the content-render extents
        (`+0x6c12..+0x6c18`, GUICODE Finding 14) -- the box the text drawing primitive
        writes as it paints. `layout.py` does not reproduce those, so such a record
        arrives here as a single point: the `G12 OUTPUTS` rows all report
        `[325, 286, 325, 286]`. A zero-area box is not clickable, so those rows could be
        neither selected nor edited even once they were classified as text.

        The extent is computed the way `render.py` DRAWS them -- `metrics.text_width`
        (the `FUN_002518F8` kerned-advance replay) and the font cell height, closed with
        Finding 14's inclusive convention (`y1 = y0 + cell - 1`). So the hit box lands on
        the pixels the panel paints, from the same numbers, rather than on a guessed pad.

        Only ever WIDENS a degenerate box, and only for a record that has text and a
        resolvable font: anything unmeasurable is left exactly as the walk reported it.
        """
        label = getattr(p, "label", None)
        if not label or (x1 > x0 and y1 > y0):
            return x1, y1
        slot = getattr(p, "font", None)
        if slot is None:
            return x1, y1
        from fireflymcf import metrics
        try:
            width = metrics.text_width(data, slot, label, table=glyph_table)
            cell = metrics.font_metrics(data, slot, table=glyph_table).height
        except Exception:
            return x1, y1                     # unmeasurable: report the walk's own box
        if not width or not cell:
            return x1, y1
        return (min(800, max(x1, x0 + width - 1)), min(480, max(y1, y0 + cell - 1)))

    def _raw_geom(self, data: bytes, screen, p):
        """The record's own RAW stored geometry -- the coordinate space `move_element`
        writes into (`records.geom_offset`) -- as `{"x": .., "y": .., "w": .., "h": ..}`,
        with each key `None` when the record carries no such field, or `None` for the
        whole geom when it carries none of the four.

        EVERY KEY IS INDEPENDENTLY OPTIONAL, because record geometry is presence-masked
        and the combinations really are ragged: donor 247's box record has w and h but
        no x and no y; donor 279's box has x, w and h but no y; its touch record has
        only w and h. This used to return None unless BOTH x and y were present, which
        reported "no geometry" for the majority of records -- including every record
        the reference build's own l-geom/l-cap/l-touch ops resize. A client offering a
        resize could therefore never see the fields it was meant to edit.

        A client that MOVES (a drag) must still require x and y to be non-null before
        using them; a client that RESIZES uses whichever of w/h are present and marks
        the rest `optional` on the op, which is why the reference build's l-cap carries
        `optional: ["h"]`.

        This is deliberately NOT `p.x0`/`p.y0`: those are `box`'s layout-RESOLVED absolute
        placement, `coord = geom + register + base_reference` (`layout.py`'s pen
        arithmetic). For a record nested inside a container the two differ by the
        container's own offset -- a drag-to-move client that anchors its delta on `box`
        instead of `geom` writes `resolved + container_offset` into the file, landing the
        element in the wrong place for every record that isn't at the container root. The
        client cannot recover `geom` on its own (it never sees raw bytes), so the server
        supplies it alongside `box`.

        Guarded end to end so a placement the walk itself already produced can never turn
        into a 500: `object_records`/`geom_offset` failures and an unmatched
        `record_offset` (e.g. a monkeypatched/synthetic `Placed` in a test) all fall
        through to `None` rather than raising.
        """
        from fireflymcf import records
        idx = p.obj_index
        if not (0 <= idx < len(screen.table)):
            return None
        off = screen.table[idx]
        if not off:
            return None
        try:
            recs = records.object_records(data, screen.gs + off)
        except (IndexError, ValueError):
            return None
        rec = next((r for r in recs if r.offset == p.record_offset), None)
        if rec is None:
            return None
        try:
            x_off = records.geom_offset(data, rec, 0)
            y_off = records.geom_offset(data, rec, 1)
        except ValueError:
            return None
        out = {"x": None, "y": None, "w": None, "h": None}
        for key, off in (("x", x_off), ("y", y_off)):
            if off is not None:
                out[key] = struct.unpack_from("<H", data, off)[0]
        for key, dim in (("w", 2), ("h", 3)):
            try:
                off = records.geom_offset(data, rec, dim)
            except ValueError:
                continue
            if off is not None:
                out[key] = struct.unpack_from("<H", data, off)[0]
        if all(v is None for v in out.values()):
            return None
        return out

    def _binding(self, data: bytes, obj_index: int, table: list | None = None):
        """The interaction model of `obj_index`'s control, or None if it isn't one (most
        objects -- labels, boxes, containers -- have no touch region at all).

        Swept across every object in all three corpus MCFs (13.6/11.23/14.7, ~3600 objects
        total): zero exceptions were raised, because `obj_index` here always comes from a
        `layout.Screen.walk()` placement -- i.e. `records.object_records` already succeeded
        for it once, and `is_group_id` bounds-checks against the very table `describe_group`
        indexes, so a placed object can't hand `describe_group` an out-of-range id. The catch
        below is therefore defensive, not observed-empirically: it narrows to what
        `gestures.touch_ids`/`describe_group` can raise on a malformed table (list/slice
        indexing -- IndexError; `layout.Screen.walk` guards the analogous
        `records.object_records` call with the same pair), not a bare `except Exception`.
        """
        from fireflymcf import gestures
        try:
            gids = [t for t in gestures.touch_ids(data, obj_index, table=table)
                    if gestures.is_group_id(data, t)]
            if not gids:
                return None
            beh = gestures.describe_group(data, gids[0])
            channels = sorted({e.channel for ph in _ALL_PHASES
                               for e in beh.on(ph) if e.channel is not None})
            return {"group": beh.group, "gesture": beh.kind, "channels": channels}
        except (IndexError, ValueError):
            return None                        # not a control; nothing to report
