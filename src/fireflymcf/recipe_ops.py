"""Op implementations. Each is a thin wrapper over an existing hardware-proven
primitive; no byte-format knowledge lives here. Registered on import (recipe.py
imports this module at its bottom).

One-group discipline (`wire_button`): `newbutton.retarget_command` allocates a FRESH
command group on every call, so calling it twice on the same object orphans the first
group -- the defect the original build script found the hard way (build-then-retarget).
`wire_button` calls it exactly once per compile, on a shell that has never been wired.
A recipe re-wire is not a second call on the same compiled image; it is a *changed op*
in the project, and `compile_project` always recompiles the whole project from stock
-- so the same object is never retargeted twice within one compile, and the defect
cannot recur here."""
from __future__ import annotations

import dataclasses
import struct

from fireflymcf import (aescompact, celltables, cli, container, followchannel, gestures,
                             glyphenc, navicon,
                              glyphs, growsection, guidata, holdregion, insert, interlocks,
                              labels, layout, mastergroup, metrics, newbutton, optionrow,
                              options, patch,
                              pumpauto, readout, records, recipe, register, relocate, render,
                              sramreach, status_map, statusbind, tankgates, theme, thumb,
                              voltagebattery)
from fireflymcf import absrecord
from fireflymcf import altreboot
from fireflymcf import appendfilter
from fireflymcf import battglyph
from fireflymcf import bitcombine
from fireflymcf import cardlayout
from fireflymcf import cardreadouts
from fireflymcf import cornermark
from fireflymcf import enumlabel
from fireflymcf import levelcontrols, levelfeed, levelpage, levelsetup, navmap, switchbuild
from fireflymcf import pagedraw as pd
from fireflymcf import pagewalk
from fireflymcf import statusfold
from fireflymcf import switchpin
from fireflymcf import tankbroadcast
from fireflymcf import variantgates
from fireflymcf.recipe import Ctx, Op, RecipeError, op, resolve_target


def _gs_tab(data):
    gs, _ = guidata.guidata_extent(data)
    return gs, guidata.object_tables(data)[1]


def _put16(buf, off, value, ctx, note):
    old = bytes(buf[off:off + 2])
    struct.pack_into("<H", buf, off, value)
    ctx.edits.append(relocate.Edit(off, old, bytes(buf[off:off + 2]), "recipe", note))


# Live-repaint enrollment bit: the panel enrols a record in its per-widget change-detect array
# (the located dispatch: renderer FUN_00256C54 -> FUN_00256450, gated on `*state & 0x20`, which
# the record decoder FUN_00252CB0 sets from bit 0x20 of the record's HEADER BYTE 4). Every one of
# HOME's 29 live widgets carries it; the wt5 container records this op retargets already do.
_ENROLL_BIT = 0x20


@op("rename_label")
class RenameLabel(Op):
    """Rename a label field in place: same-size by default (pads/aligns to fit the
    stock field), or a relocating grow (`labels.grow_label`) when the new text is
    longer and `allow_grow` is set.

    Params:
      object: int — stock object index holding the label
      old: str — current text (or a unique substring of the field) to match against
      new: str — replacement text
      align: "right"|"left"|"center" (default "right") — where the same-size pad goes.
             "right" preserves the indent a LEFT-aligned label draws from. Use "center" for a
             record whose R49 h-align byte is 2: it centres each line by the padded width, so
             trailing pad drags the text left (reference build v1/v2's caption sat 9 px off; owner,
             2026-08-21)
      allow_grow: bool (default False) — permit a relocating grow when `new` does not
                 fit the field `old` was found in

    Error codes:
      NO_SUCH_LABEL — object has no label field containing `old`
      AMBIGUOUS_LABEL — object has more than one label field containing `old`
      LABEL_TOO_LONG — `new` does not fit the field and `allow_grow` was not set;
                       offers=["grow", "shorten"]
      GROW_REFUSED — `labels.grow_label` refused the relocating grow

    Returns sealed image with the label field updated (and, on a grow, the image
    relocated) and edits logged to context.
    """
    PARAMS = {
        "properties": {
            "object": {"type": "integer", "description": "stock object index holding the label"},
            "old": {"type": "string", "description": "current text (or unique substring of the field)"},
            "new": {"type": "string", "description": "replacement text"},
            "align": {"type": "string", "enum": ["right", "left", "center"],
                      "default": "right",
                      "description": "which side the same-size pad goes. 'right' preserves a "
                                     "left-aligned label's indent; use 'center' when the record "
                                     "centres its text, where trailing pad shifts it visibly"},
            "allow_grow": {"type": "boolean", "default": False,
                           "description": "permit a relocating grow when the text does not fit"},
        },
        "required": ["object", "old", "new"],
    }

    def validate(self):
        self.object = self._param("object", kind=int)
        self.old = self._param("old", kind=str)
        self.new = self._param("new", kind=str)
        self.align = self._param("align", "right", kind=str)
        if self.align not in ("right", "left", "center", "centre"):
            raise RecipeError("BAD_PARAMS", "align must be 'right', 'left' or 'center', got %r"
                              % self.align, op_id=self.spec.id)
        self.allow_grow = self._param("allow_grow", False, kind=bool)

    def apply(self, data, ctx):
        gs, _ = guidata.guidata_extent(data)
        lo, hi = insert._object_span(data, self.object)
        sites = [s for s in patch.find_label_sites(data, self.old)
                 if lo <= s.text_offset - gs < hi]
        if not sites:
            raise RecipeError("NO_SUCH_LABEL", "object %d has no label field containing"
                              " %r" % (self.object, self.old))
        if len(sites) > 1:
            raise RecipeError("AMBIGUOUS_LABEL", "object %d has %d fields containing %r"
                              % (self.object, len(sites), self.old))
        site = sites[0]
        if len(self.new) <= len(site.text):
            patcher = patch.McfPatcher(data)
            try:
                patcher.set_label(site, self.old, self.new, align=self.align)
            except patch.PatchError as e:
                raise RecipeError("LABEL_TOO_LONG", str(e), offers=["grow", "shorten"])
            if not patcher.edits():
                # No-op rename (old == new, etc.); return input unchanged
                ctx.handles[self.spec.id] = {"final": self.new}
                return data
            out, _ = patcher.build()
            ctx.edits.extend(patcher.edits())
            ctx.handles[self.spec.id] = {"final": self.new}
            return out
        if not self.allow_grow:
            raise RecipeError(
                "LABEL_TOO_LONG",
                "%r (%d chars) does not fit the %d-byte field holding %r; growing it "
                "makes this a relocating build" % (self.new, len(self.new),
                                                  len(site.text), self.old),
                offers=["grow", "shorten"])
        try:
            out, padded, edits, at = labels.grow_label(data, self.object,
                                                       site.text, self.new)
        except (AssertionError, StopIteration, relocate.RelocationError) as e:
            raise RecipeError("GROW_REFUSED", "grow_label refused: %s" % e)
        ctx.edits.extend(edits)
        ctx.insertions.append(at)
        ctx.handles[self.spec.id] = {"final": padded}
        out, _ = patch.reseal(out)
        return out

    def verify(self, data, ctx):
        final = ctx.handles[self.spec.id]["final"]
        gs, tab = _gs_tab(data)
        have = {getattr(r, "label", None)
                for r in records.object_records(data, gs + tab[self.object])}
        assert any(l and final.strip() in l for l in have), \
            "rename %r lost (object %d labels: %s)" % (final, self.object,
                                                       sorted(x for x in have if x))
        return "rename: object %d %r -> %r" % (self.object, self.old, final)


_GEOM_IDX = {"x": 0, "y": 1, "w": 2, "h": 3}


def _select_record(data, gs, tab, obj, selector, op_id):
    wtype = selector.get("wtype")
    label = selector.get("label")
    nth = selector.get("nth", 0)
    recs = records.object_records(data, gs + tab[obj])
    cand = [r for r in recs
            if (wtype is None or r.wtype == wtype)
            and (label is None
                 or (getattr(r, "label", None) or "").strip() == label.strip())]
    if nth >= len(cand):
        raise RecipeError("NO_SUCH_RECORD",
                          "object %d has %d record(s) matching %r, wanted nth=%d"
                          % (obj, len(cand), selector, nth), op_id=op_id)
    return cand[nth]


@op("move_element")
class MoveElement(Op):
    """Reposition or resize a single record (selected within one object) by writing
    its geometry fields directly.

    Params:
      target: int|{"ref"} — object index; resolved via resolve_target(ctx, target)
      selector: dict — record selector: wtype (int|None), label (str|None),
                nth (int, default 0); resolved via `_select_record`
      set: dict — geometry fields to write, keys from x/y/w/h -> new int value; must
           be non-empty
      expect: dict (default {}) — prior values to validate before writing; every key
              here must also be a key in `set` (an expect on a field never written is
              a silently-inert assertion)
      optional: list[str] (default []) — `set` keys allowed to be absent on the
                matched record (skipped rather than refused)

    Error codes:
      BAD_PARAMS — a set/expect key is not x/y/w/h, set is empty, or an expect key is
                   not also in set
      NO_SUCH_RECORD — no record matches selector (from `_select_record`)
      NO_FIELD — a requested `set` key has no geometry field on the matched record and
                is not listed in `optional`
      EXPECT_MISMATCH — an `expect` value differs from the field's current value

    Returns sealed image with the matched record's geometry fields updated and edits
    logged to context.
    """
    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "selector": {"type": "object",
                         "description": "record selector: wtype (int|None), label (str|None), nth (int)"},
            "set": {"type": "object",
                    "description": "geometry fields to write, keys from x/y/w/h -> new int value"},
            "expect": {"type": "object", "default": {},
                       "description": "prior values to validate before writing (keys must be in set)"},
            "optional": {"type": "array", "items": {"type": "string"}, "default": [],
                         "description": "set keys allowed to be absent on the record"},
        },
        "required": ["target", "selector", "set"],
    }

    def validate(self):
        self.target = self._param("target")
        self.selector = self._param("selector", kind=dict)
        self.set = self._param("set", kind=dict)
        self.expect = self._param("expect", {}, kind=dict)
        self.optional = set(self._param("optional", [], kind=list))
        bad = [k for k in list(self.set) + list(self.expect) if k not in _GEOM_IDX]
        stray_expect = [k for k in self.expect if k not in self.set]
        if bad or not self.set or stray_expect:
            raise RecipeError(
                "BAD_PARAMS",
                "move_element set/expect keys must be x/y/w/h, set must be "
                "non-empty, and every expect key must also be in set (an expect on a "
                "key never written is a silently-inert assertion); got bad=%r "
                "stray_expect=%r" % (bad, stray_expect), op_id=self.spec.id)

    def apply(self, data, ctx):
        obj = resolve_target(ctx, self.target)
        gs, tab = _gs_tab(data)
        rec = _select_record(data, gs, tab, obj, self.selector, self.spec.id)
        buf = bytearray(data)
        for key, val in self.set.items():
            off = records.geom_offset(data, rec, _GEOM_IDX[key])
            if off is None:
                if key in self.optional:
                    continue
                raise RecipeError("NO_FIELD", "object %d record (%r) has no %r field"
                                  % (obj, self.selector, key))
            if key in self.expect:
                have = struct.unpack_from("<H", data, off)[0]
                if have != self.expect[key]:
                    raise RecipeError("EXPECT_MISMATCH",
                                      "object %d %s is %d, project expected %d"
                                      % (obj, key, have, self.expect[key]))
            _put16(buf, off, val, ctx, "move_element obj %d %s -> %d" % (obj, key, val))
        out, _ = patch.reseal(bytes(buf))
        ctx.handles[self.spec.id] = {"object": obj}
        return out

    def verify(self, data, ctx):
        obj = ctx.handles[self.spec.id]["object"]
        gs, tab = _gs_tab(data)
        rec = _select_record(data, gs, tab, obj, self.selector, self.spec.id)
        for key, val in self.set.items():
            off = records.geom_offset(data, rec, _GEOM_IDX[key])
            if off is None and key in self.optional:
                continue
            assert struct.unpack_from("<H", data, off)[0] == val, \
                "move_element: obj %d %s != %d" % (obj, key, val)
        return "move: object %d %s" % (obj, dict(self.set))


@op("move_attach")
class MoveAttach(Op):
    """Reposition wtype-5 attach records by updating their geometry coordinates.

    Params:
      page: str|int — page name ("home", "lights", etc.) or object index; resolved via
            cli._resolve_page_root(data, page)
      child: int|{"ref"} — object index of the child to find in attach records; resolved
             via resolve_target(ctx, child)
      x: int (optional) — new x-coordinate to write to matching attach record(s)
      y: int (optional) — new y-coordinate to write to matching attach record(s)

    Error codes:
      BAD_PARAMS — x and y are both None (at least one coordinate is required)
      NO_SUCH_PAGE — page name cannot be resolved to an object index
      NO_SUCH_RECORD — page has no wtype-5 attach records with matching child_index
      NO_FIELD — a requested coordinate (x or y) exists on no matching attach record.
                 This is an error by design: a GUI move that cannot happen is reported,
                 not silently skipped. If an attach record doesn't have the field, the
                 operation fails rather than partially moving only existing coordinates.

    Returns sealed image with geometry fields updated and edits logged to context.
    """
    PARAMS = {
        "properties": {
            "page": {"type": ["string", "integer"],
                     "description": "page name (\"home\", \"lights\", ...) or object index"},
            "child": {"type": ["integer", "object"],
                      "description": "object index or {\"ref\": op_id} of the attached child"},
            "x": {"type": ["integer", "null"], "default": None,
                  "description": "new x-coordinate (x and/or y required)"},
            "y": {"type": ["integer", "null"], "default": None,
                  "description": "new y-coordinate (x and/or y required)"},
        },
        "required": ["page", "child"],
    }

    def validate(self):
        self.page = self._param("page")
        self.child = self._param("child")
        self.x = self._param("x", None)
        self.y = self._param("y", None)
        if self.x is None and self.y is None:
            raise RecipeError("BAD_PARAMS", "move_attach wants x and/or y",
                              op_id=self.spec.id)

    def _root(self, data):
        if isinstance(self.page, int):
            return self.page
        try:
            return cli._resolve_page_root(data, self.page)
        except Exception as e:
            raise RecipeError("NO_SUCH_PAGE", "cannot resolve page %r: %s"
                              % (self.page, e))

    def apply(self, data, ctx):
        root = self._root(data)
        child = resolve_target(ctx, self.child)
        gs, tab = _gs_tab(data)
        buf = bytearray(data)
        hit = 0
        moved = set()
        for rec in records.object_records(data, gs + tab[root]):
            if rec.wtype != 5 or rec.child_index != child:
                continue
            hit += 1
            for key, val in (("x", self.x), ("y", self.y)):
                if val is None:
                    continue
                off = records.geom_offset(data, rec, _GEOM_IDX[key])
                if off is not None:
                    moved.add(key)
                    _put16(buf, off, val, ctx,
                           "attach of obj %d on page %d: %s -> %d" % (child, root, key, val))
        if hit == 0:
            raise RecipeError("NO_SUCH_RECORD", "page %d has no attach record for "
                              "object %d" % (root, child))
        for key in ("x", "y"):
            val = getattr(self, key)
            if val is not None and key not in moved:
                raise RecipeError("NO_FIELD", "attach record for object %d has no %r field"
                                  % (child, key))
        ctx.handles[self.spec.id] = {"root": root, "child": child}
        out, _ = patch.reseal(bytes(buf))
        return out

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        root_idx, child = h["root"], h["child"]
        gs, tab = _gs_tab(data)
        hit = 0
        for rec in records.object_records(data, gs + tab[root_idx]):
            if rec.wtype == 5 and rec.child_index == child:
                hit += 1
                for key, val in (("x", self.x), ("y", self.y)):
                    if val is None:
                        continue
                    off = records.geom_offset(data, rec, _GEOM_IDX[key])
                    if off is not None:
                        assert struct.unpack_from("<H", data, off)[0] == val
        assert hit > 0, "verify: page %d attach record for object %d was removed" % (
            root_idx, child)
        return "attach: object %d on page %d -> (%s, %s)" % (
            child, root_idx, self.x, self.y)


@op("set_font")
class SetFont(Op):
    """Write a record's font selector to a new slot value.

    The R38 `01 <font> ff` selector byte is persistent: later records in the walk
    inherit it, so a change affects subsequent records until the next selector.

    Params:
      target: int|{"ref"} — object index; resolved via resolve_target(ctx, target)
      selector: dict — record selector with keys wtype (int|None), label (str|None),
                nth (int, default 0); resolved via _select_record(...)
      slot: int — new font slot value to write
      expect: int|"inherit"|None — (optional) prior value to validate before writing

    Error codes:
      NO_FONT_SELECTOR — record carries no `01 <font> ff` selector byte; field
                         presence is header-gated, so same-size edit cannot add one
      EXPECT_MISMATCH — expect value differs from current selector byte
      NO_SUCH_RECORD — no record matches the selector (from _select_record)

    Returns sealed image with font selector updated and edits logged to context.
    """
    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "selector": {"type": "object",
                         "description": "record selector: wtype (int|None), label (str|None), nth (int)"},
            "slot": {"type": "integer", "description": "new font slot value to write"},
            "expect": {"type": ["integer", "string", "null"], "default": None,
                       "description": "prior selector value to validate ('inherit' for 0xff)"},
        },
        "required": ["target", "selector", "slot"],
    }

    def validate(self):
        self.target = self._param("target")
        self.selector = self._param("selector", kind=dict)
        self.slot = self._param("slot", kind=int)
        self.expect = self._param("expect", None)

    def apply(self, data, ctx):
        obj = resolve_target(ctx, self.target)
        gs, tab = _gs_tab(data)
        rec = _select_record(data, gs, tab, obj, self.selector, self.spec.id)
        fo = records.font_offset(data, rec)
        if fo is None:
            raise RecipeError(
                "NO_FONT_SELECTOR",
                "object %d's record carries no `01 <font> ff` selector; field presence "
                "is header-gated, so a same-size edit cannot add one" % obj)
        if self.expect == "inherit" and data[fo] != 0xFF:
            raise RecipeError("EXPECT_MISMATCH", "selector holds slot %d, project "
                              "expected the 0xff inherit" % data[fo])
        if isinstance(self.expect, int) and data[fo] != self.expect:
            raise RecipeError("EXPECT_MISMATCH", "selector holds %d, project expected %d"
                              % (data[fo], self.expect))
        buf = bytearray(data)
        old = bytes([buf[fo]])
        buf[fo] = self.slot
        ctx.edits.append(relocate.Edit(fo, old, bytes([self.slot]), "recipe",
                                       "font selector obj %d -> slot %d" % (obj, self.slot)))
        ctx.handles[self.spec.id] = {"object": obj}
        out, _ = patch.reseal(bytes(buf))
        return out

    def verify(self, data, ctx):
        obj = ctx.handles[self.spec.id]["object"]
        gs, tab = _gs_tab(data)
        rec = _select_record(data, gs, tab, obj, self.selector, self.spec.id)
        assert rec.font == self.slot, "font is %s, wanted %d" % (rec.font, self.slot)
        return ("font: object %d -> slot %d (selector is PERSISTENT: later records in "
                "the walk inherit it)" % (obj, self.slot))


@op("set_icon")
class SetIcon(Op):
    """Repoint an icon record's font slot and/or glyph character, optionally blanking
    the caption with same-size space padding.

    Params:
      target: int|{"ref"} — object index; resolved via resolve_target(ctx, target)
      slot: int|None — (optional) new font slot value for icon's R38 selector
      char: str|None — (optional) single character to write at icon label_offset
      blank_caption: bool (default False) — (optional) blank the caption record
                     (the other wtype-11 in the same object) with spaces
      current: str|None — (optional) the character the icon to change shows now, for an
               object holding several icon records (the nav rail's arms hold five or six);
               default: the object's first icon record

    Error codes:
      BAD_PARAMS — char is not a single character, or all three (slot, char,
                   blank_caption) are None/False
      NO_SUCH_RECORD — object has no wtype-11 icon record (one with font is not None),
                       or blank_caption=True but no caption record exists, or `current`
                       does not name exactly one icon record
      NO_FONT_SELECTOR — icon record's font slot selector `01 <font> ff` not found or
                         found multiple times (expected exactly one)

    Returns sealed image with icon record and/or caption updated and edits logged.
    """
    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "slot": {"type": ["integer", "null"], "default": None,
                     "description": "new font slot value for the icon's R38 selector "
                                     "(slot, char and/or blank_caption required)"},
            "char": {"type": ["string", "null"], "default": None,
                     "description": "single character to write at the icon's label offset "
                                     "(slot, char and/or blank_caption required)"},
            "blank_caption": {"type": "boolean", "default": False,
                              "description": "blank the object's other wtype-11 caption with spaces "
                                              "(slot, char and/or blank_caption required)"},
            "current": {"type": ["string", "null"], "default": None,
                        "description": "the character the icon to change shows now, when the "
                                       "object holds several icon records"},
        },
        "required": ["target"],
    }

    def validate(self):
        self.target = self._param("target")
        self.slot = self._param("slot", None)
        self.char = self._param("char", None)
        self.blank_caption = self._param("blank_caption", False, kind=bool)
        self.current = self._param("current", None)
        if self.current is not None and len(self.current) != 1:
            raise RecipeError("BAD_PARAMS", "current must be a single character",
                              op_id=self.spec.id)
        if self.char is not None and len(self.char) != 1:
            raise RecipeError("BAD_PARAMS", "char must be a single character",
                              op_id=self.spec.id)
        if self.slot is None and self.char is None and not self.blank_caption:
            raise RecipeError("BAD_PARAMS", "set_icon wants slot, char and/or "
                              "blank_caption", op_id=self.spec.id)

    def apply(self, data, ctx):
        obj = resolve_target(ctx, self.target)
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[obj])
        icons = [r for r in recs if r.wtype == 11 and r.font is not None]
        if not icons:
            raise RecipeError("NO_SUCH_RECORD", "object %d has no icon record "
                              "(wtype 11 with its own font)" % obj)
        if self.current is not None:
            icons = [r for r in icons if r.label == self.current]
            if len(icons) != 1:
                raise RecipeError("NO_SUCH_RECORD", "object %d has %d icon records showing "
                                  "%r, wanted exactly one" % (obj, len(icons), self.current))
        icon = icons[0]
        buf = bytearray(data)
        if self.slot is not None:
            span = bytes(buf[icon.offset:icon.offset + icon.size])
            sel = bytes((0x01, icon.font, 0xFF))
            if span.count(sel) != 1:
                raise RecipeError("NO_FONT_SELECTOR",
                                  "expected one `01 %02x ff` selector in object %d's "
                                  "icon record, found %d" % (icon.font, obj,
                                                             span.count(sel)))
            sel_at = icon.offset + span.index(sel)
            old = bytes([buf[sel_at + 1]])
            buf[sel_at + 1] = self.slot
            ctx.edits.append(relocate.Edit(sel_at + 1, old, bytes([self.slot]),
                                           "recipe", "icon font -> slot %d" % self.slot))
        if self.char is not None:
            old = bytes([buf[icon.label_offset]])
            buf[icon.label_offset] = ord(self.char)
            ctx.edits.append(relocate.Edit(icon.label_offset, old,
                                           self.char.encode("ascii"), "recipe",
                                           "icon char -> %r" % self.char))
        if self.blank_caption:
            caps = [r for r in recs if r.wtype == 11 and r is not icon and r.label]
            if not caps:
                raise RecipeError("NO_SUCH_RECORD", "object %d has no caption to blank"
                                  % obj)
            cap = caps[0]
            old_cap = bytes(buf[cap.label_offset:cap.label_offset + len(cap.label)])
            buf[cap.label_offset:cap.label_offset + len(cap.label)] = \
                b" " * len(cap.label)
            ctx.edits.append(relocate.Edit(cap.label_offset, old_cap,
                                           b" " * len(cap.label), "recipe",
                                           "caption blanked (glyph-only button)"))
        ctx.handles[self.spec.id] = {"object": obj, "record": recs.index(icon)}
        out, _ = patch.reseal(bytes(buf))
        return out

    def verify(self, data, ctx):
        obj = ctx.handles[self.spec.id]["object"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[obj])
        icon = recs[ctx.handles[self.spec.id]["record"]]
        assert icon.wtype == 11 and icon.font is not None, \
            "object %d record %d is no longer an icon" % (obj, ctx.handles[self.spec.id]["record"])
        if self.slot is not None:
            assert icon.font == self.slot
        if self.char is not None:
            assert icon.label == self.char
        if self.blank_caption:
            caps = [r for r in recs if r.wtype == 11 and r is not icon and r.label]
            assert caps, "caption record was not found"
            cap = caps[0]
            assert cap.label.strip() == "", "caption label is %r, expected blank" % cap.label
        return "icon: object %d slot=%s char=%s%s" % (
            obj, self.slot, self.char,
            " caption blanked" if self.blank_caption else "")


@op("recolor")
class Recolor(Op):
    """Recolor a button box by repointing its mode-7 fill index via the theme resolver.

    Resolves the object's button box record and delegates to McfPatcher.set_fill_color,
    which accepts either an RGB triple or a mode-7 palette index.

    Params:
      object: int|{"ref"} — object index; resolved via resolve_target(ctx, object)
      color: [r,g,b] (list of 3 ints 0-255) or {"index": int} — target colour, either
             as RGB triple (matched to theme palette via resolver) or palette index
      allow_container: bool (default False) — recolour a card of controls on purpose

    Error codes:
      BAD_PARAMS — color is not [r,g,b] list or {"index": n} dict
      CONTAINER — the object is a card of controls (it attaches child objects and holds
                  two or more touch regions, like HOME's lights card 905), so its box is the
                  card's background, not one button's; offers=["acknowledge_container"] —
                  retry with allow_container=True to recolour the whole card
      NO_SUCH_COLOR — object has no button box, or resolver rejects the colour
                      (PatchError, PaletteError, ValueError all mapped here)

    Returns sealed image with box fill colour repointed and edits logged to context.
    """
    PARAMS = {
        "properties": {
            "object": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "color": {"type": ["array", "object"],
                      "description": "[r, g, b] triple or {\"index\": n} palette index"},
            "allow_container": {"type": "boolean", "default": False,
                                "description": "recolour a card of controls on purpose"},
        },
        "required": ["object", "color"],
    }

    def validate(self):
        self.object = self._param("object")
        self.color = self._param("color")
        self.allow_container = self._param("allow_container", False, kind=bool)
        ok = (isinstance(self.color, list) and len(self.color) == 3) or \
             (isinstance(self.color, dict) and set(self.color) == {"index"})
        if not ok:
            raise RecipeError("BAD_PARAMS", "color wants [r,g,b] or {'index': n}",
                              op_id=self.spec.id)

    def apply(self, data, ctx):
        obj = resolve_target(ctx, self.object)
        if not self.allow_container:
            gs, tab = _gs_tab(data)
            recs = records.object_records(data, gs + tab[obj])
            touch = sum(r.wtype == 12 for r in recs)
            if touch >= 2 and any(r.wtype == 5 for r in recs):
                raise RecipeError(
                    "CONTAINER",
                    "object %d is a card of controls (%d touch regions and attached child "
                    "objects); its box is the card's background, so recolouring it repaints "
                    "every control on the card. Recolour one control's own object, or pass "
                    "allow_container=True" % (obj, touch), offers=["acknowledge_container"])
        try:
            box = patch.find_button_box(data, obj)
            if isinstance(self.color, dict):
                target = theme.resolve_fill_index(data, index=self.color["index"])
            else:
                target = theme.resolve_fill_index(data, color=tuple(self.color))
            patcher = patch.McfPatcher(data)
            patcher.set_fill_color(box, index=target)
            if not patcher.edits():
                # No-op recolor (already the requested colour); return input unchanged.
                ctx.handles[self.spec.id] = {"object": obj, "index": target}
                return data
            out, _ = patcher.build()
        except (patch.PatchError, theme.PaletteError, ValueError) as e:
            raise RecipeError("NO_SUCH_COLOR", str(e))
        ctx.edits.extend(patcher.edits())
        ctx.handles[self.spec.id] = {"object": obj, "index": target}
        return out

    def verify(self, data, ctx):
        # Re-read the box's fillA directly and assert it still matches the request --
        # a vacuous `find_button_box` alone would miss a later op (e.g. wire_button)
        # repointing the SAME box's fillA to a different cell after this op ran.
        h = ctx.handles[self.spec.id]
        obj, want = h["object"], h["index"]
        box = patch.find_button_box(data, obj)
        fillA = next(c for c in box.colors if c[0] == "fillA")
        assert fillA[1] == 7 and fillA[2] == want, \
            "recolor: object %d fillA is mode %s index %s, wanted mode 7 index %d " \
            "(a later op repointed it)" % (obj, fillA[1], fillA[2], want)
        return "recolor: object %d -> %s (index %d)" % (obj, self.color, want)


@op("remap_channel")
class RemapChannel(Op):
    """Repoint a labelled control's group at another G12 channel.

    Delegates to McfPatcher.set_channel, which rewrites every binding entry in the
    control's group (not just the one that matched) and makes its indicator follow —
    see set_channel's docstring for the four checks (I5-I8) that refuse a plausible-
    looking remap that would misbehave.

    **The indicator, two ways.** By default the control's own records are pointed at the
    new pin's status cells (`McfPatcher._retarget_indicator`) and no status variable
    moves, so every G12 OUTPUTS row stays honest. That path has not been on the coach:
    the op is flagged in `CompileResult.unverified`. `move_display_list` takes PC.14's v9
    instead — the status variable moves, proven on the coach, and the old channel's
    diagnostics row reports the new channel from then on.

    Params:
      label: str — control label, matched via guidata.find_control_groups (stripped
             comparison, so a literal newline in the label need not be typed)
      channel: int — destination G12 channel (0-255)
      move_display_list: bool (default False) — move the status variable instead of
                         retargeting the indicator: the coach-proven path (PC.14, v9), at
                         the cost of the old channel's G12 OUTPUTS row, which then
                         reports the new channel
      allow_capability_change: bool (default False) — forwarded to set_channel's own
                         `allow_capability_change`; suppresses I8 only (moving a
                         dimmable control onto a pin this file commands nowhere, where
                         the levels it sends would be silently meaningless). I8 is
                         LIVE unless this is set — it must be said out loud, the same
                         as add_button/wire_button's COMMANDED_CHANNEL judgement call.

    Error codes:
      NO_SUCH_CONTROL — no located control is labelled `label`
      AMBIGUOUS_LABEL — more than one located control has that label
      CAPABILITY_CHANGE — set_channel's I8 check refused (a dimmable control moved to
                          a channel this file never dims/commands) and
                          allow_capability_change was not set; offers=["acknowledge"]
      REMAP_REFUSED — any other set_channel check (I5-I7) refused the remap, or the
                      default indicator retarget did: the control's indicator names none
                      of its own pin's cells (it does not follow its command channel),
                      the destination has no GuiCode bridge block, or the image is not
                      13.6 (the only build with a bridge dump). The PatchError's message
                      names which

    The declared capability delta moves pairs, it never invents them: a pair is only
    declared LOST if this group is its SOLE producer on the old channel(s)
    (`newbutton.sole_produced_pairs`) — a pair still reachable through another group is
    not lost. GAINED is the full image of this group's own (channel, command) pairs
    under the channel rewrite, minus whatever the destination already sent (set_channel
    itself refuses a destination another binding already occupies, per I7, so this is
    normally the group's own previously-sole pairs landing on the new channel).

    Returns sealed image with binding (and status) entries repointed and edits logged.
    """
    PARAMS = {
        "properties": {
            "label": {"type": "string", "description": "control label (stripped comparison)"},
            "channel": {"type": "integer", "description": "destination G12 channel (0-255)"},
            "move_display_list": {"type": "boolean", "default": False,
                                  "description": "move the status variable (coach-proven; the old channel's G12 OUTPUTS row then reports the new one) instead of retargeting the indicator"},
            "allow_capability_change": {"type": "boolean", "default": False,
                                       "description": "suppress the I8 dimmable-onto-uncommanded-channel refusal"},
        },
        "required": ["label", "channel"],
    }

    def validate(self):
        self.label = self._param("label", kind=str)
        self.channel = self._param("channel", kind=int)
        self.move_display_list = self._param("move_display_list", False, kind=bool)
        self.allow_capability_change = self._param(
            "allow_capability_change", False, kind=bool)
        # PC.13-16 flashed the status-variable move; the indicator retarget is unflashed.
        self.hardware_verified = self.move_display_list

    def _control(self, data):
        controls = [c for c in guidata.find_control_groups(data)
                    if c.label.strip() == self.label.strip()]
        if not controls:
            known = sorted({c.label.strip().replace("\n", " ")
                            for c in guidata.find_control_groups(data)})
            raise RecipeError("NO_SUCH_CONTROL", "no located control is labelled %r; "
                              "known: %s" % (self.label, ", ".join(known)))
        if len(controls) > 1:
            raise RecipeError("AMBIGUOUS_LABEL", "%r matches %d controls"
                              % (self.label, len(controls)))
        return controls[0]

    def apply(self, data, ctx):
        control = self._control(data)
        patcher = patch.McfPatcher(data)
        try:
            patcher.set_channel(control, self.channel,
                                allow_capability_change=self.allow_capability_change,
                                move_display_list=self.move_display_list)
        except patch.PatchError as e:
            # I8's refusal is distinguished by its own wording (patch.py:526-660) so a
            # caller gets the "acknowledge" offer only for the capability-class check,
            # never for I5-I7 (which have no suppression flag to offer).
            if "allow_capability_change=True to do it anyway" in str(e):
                raise RecipeError("CAPABILITY_CHANGE", str(e), offers=["acknowledge"])
            raise RecipeError("REMAP_REFUSED", str(e))
        if not patcher.edits():
            # No-op remap (channel already set); return input unchanged.
            return data
        out, _ = patcher.build()
        ctx.edits.extend(patcher.edits())
        return out

    def capability_delta(self, ctx):
        # Computed against stock. `mine_old`/`mine_new` are this group's own
        # (channel, command) pairs before and after the rewrite (set_channel repoints
        # EVERY entry in the group, not just the sole-produced ones); `sole` is the
        # subset nothing else can still send. That gives the exact set-difference the
        # gate itself recomputes: lost = sole - mine_new (a sole pair that happened to
        # already sit on the destination channel would not actually vanish — it can't
        # in practice, since set_channel's I7 refuses an occupied destination, but the
        # subtraction is kept for honesty); gained = mine_new - stock_caps (whatever of
        # the rewritten pairs is new to the file).
        stock_caps = recipe.capabilities(ctx.stock)
        control = None
        for c in guidata.find_control_groups(ctx.stock):
            if c.label.strip() == self.label.strip():
                control = c
                break
        if control is None:
            return set(), set()
        mine_old = newbutton.group_command_pairs(ctx.stock, control.group)
        sole = newbutton.sole_produced_pairs(ctx.stock, control.group)
        mine_new = {(self.channel, cmd) for (ch, cmd) in mine_old}
        lost = sole - mine_new
        gained = mine_new - stock_caps
        return lost, gained

    def verify(self, data, ctx):
        controls = [c for c in guidata.find_control_groups(data)
                    if c.label.strip() == self.label.strip()]
        assert controls and self.channel in set(controls[0].channels), \
            "remap: %r does not command channel %d" % (self.label, self.channel)
        if not self.move_display_list:
            # The indicator must name the new pin's cells and none of the old pin's.
            obj = status_map._control_object(data, controls[0])
            pins = statusbind.pin_bindings(data)
            new = pins[self.channel]
            assert patch.indicator_sites(data, obj, {new.colour_cell, new.bool_cell}), \
                "remap: %r's indicator names none of channel %d's cells" % (
                    self.label, self.channel)
            was = [c for c in guidata.find_control_groups(ctx.stock)
                   if c.label.strip() == self.label.strip()][0].channels[0]
            if was != self.channel:
                old = pins[was]
                assert not patch.indicator_sites(data, obj, {old.colour_cell, old.bool_cell}), \
                    "remap: %r's indicator still names channel %d's cells" % (self.label, was)
        return "remap: %r -> channel %d%s" % (
            self.label, self.channel,
            " (status variable moved; the old channel's G12 OUTPUTS row now reports "
            "this one)" if self.move_display_list else
            " (indicator retargeted, status variables untouched — HARDWARE-UNVERIFIED)")


@op("set_binding_bytes")
class SetBindingBytes(Op):
    """Rewrite chosen WIRE payload bytes of existing bindings in a command group
    (`newbutton.set_binding_bytes`), a same-size edit.

    The byte positions are the order `binding_wire_frame` and candump show, translated
    through the binding word order (`newbutton.binding_payload`), never raw offsets. The
    entry's DGN, variant and gesture mask are untouched, so a control keeps its
    GuiCode-driven variants and step codes. Every `from` byte and the DGN are asserted
    before the edit, so a project written against one build fails loudly on another.

    Params:
      group: int — the command group whose bindings are edited.
      dgn: int — asserted on every binding edited (18-bit).
      bindings: "all" | [int] — which of the group's bindings, by position in its run.
      set: object — `{"<byte 0-7>": {"from": v, "to": w}}` on the wire payload.

    Error codes:
      BAD_PARAMS — a byte outside 0..7, a value outside 0..255, an empty `set`, or
                   `bindings` neither "all" nor positions inside the group.
      EXPECT_MISMATCH — a binding's DGN or a `from` byte differs; the message carries
                        the binding's current wire payload.
      NO_SUCH_GROUP — `group` is not in the group table.

    Capability delta: when `dgn` is DC_DIMMER_COMMAND_2 and a change moves the channel
    byte, the pairs moved are declared the way `remap_channel` does; otherwise none.
    """
    hardware_verified = False
    PARAMS = {
        "properties": {
            "group": {"type": "integer", "description": "command group id"},
            "dgn": {"type": "integer", "description": "expected DGN of every edited binding"},
            "bindings": {"description": '"all" or positions within the group',
                         "default": "all"},
            "set": {"type": "object",
                    "description": 'wire byte -> {"from": v, "to": w}'},
        },
        "required": ["group", "dgn", "set"],
    }

    def validate(self):
        self.group = self._param("group", kind=int)
        self.dgn = self._param("dgn", kind=int)
        self.bindings = self._param("bindings", "all")
        raw = self._param("set", kind=dict)
        self.changes = {}
        try:
            for k, v in raw.items():
                self.changes[int(k)] = (int(v["from"]), int(v["to"]))
        except (ValueError, TypeError, KeyError):
            raise RecipeError("BAD_PARAMS", 'set_binding_bytes: set must map a byte '
                              'position to {"from": v, "to": w}', op_id=self.spec.id)

    def apply(self, data, ctx):
        try:
            data, e = newbutton.set_binding_bytes(
                data, self.group, dgn=self.dgn, bindings=self.bindings,
                changes=self.changes)
        except newbutton.NewButtonError as err:
            raise RecipeError(err.code, str(err), op_id=self.spec.id)
        ctx.edits.extend(e)
        return data

    def capability_delta(self, ctx):
        if self.dgn != 0x1FEDB:
            return set(), set()
        try:
            after, _ = newbutton.set_binding_bytes(
                ctx.stock, self.group, dgn=self.dgn, bindings=self.bindings,
                changes=self.changes)
        except newbutton.NewButtonError:
            return set(), set()
        new = newbutton.group_command_pairs(after, self.group)
        lost = newbutton.sole_produced_pairs(ctx.stock, self.group) - new
        return lost, new - recipe.capabilities(ctx.stock)

    def verify(self, data, ctx):
        table = guidata.binding_table(data)
        members = guidata.group_bindings(guidata.group_table(data), table, self.group)
        chosen = (range(len(members)) if self.bindings == "all" else self.bindings)
        for i in chosen:
            wire = newbutton.binding_payload(members[i].raw)
            for pos, (_frm, to) in self.changes.items():
                assert wire[pos] == to, "binding %d payload byte %d is %#04x, not %#04x" % (
                    members[i].index, pos, wire[pos], to)
        return "binding bytes: group %d, %d binding(s), wire byte(s) %s (UNVERIFIED)" % (
            self.group, len(list(chosen)), sorted(self.changes))


@op("drop_from_master")
class DropFromMaster(Op):
    """Remove one G12 channel from a master control, so its ON/OFF buttons no longer
    command that load.

    A master (`guidata.find_control_groups`, e.g. "Light Master" groups 1/2, "Exterior
    Master" 216/217) is one on-screen control whose two buttons each drive several
    channels at once. This op neutralises every binding that names `channel` in BOTH the
    ON and OFF group, across every variant copy, via `mastergroup.drop_channel`: the DGN
    u16 becomes `0xFFFF` (the inert dead-entry form Firefly ships in group 5, and the
    `raw[:2] == FFFF` liveness convention `gestures.py` reads) and the phase mask (byte 3)
    becomes 0, which `docs/CONTROL-BEHAVIOR.md` section 3 proves stops the sender
    emitting on any gesture phase. Same-size, in-place, no binding-count change.

    The bath light's own dedicated control is untouched, so the load stays fully
    operable; only the master loses it. FLASHED AND VERIFIED 2026-09-14: with
    ch25 dropped, the coach's bath light no longer responds to Light Master, and its
    own button still works, so the op is `hardware_verified = True`.

    Params:
      master: str — master screen title (stripped comparison), e.g. "Light Master"
      channel: int — the G12 channel to drop (0-255)

    Error codes:
      NO_SUCH_MASTER — no control group's screen title is `master`
      NOT_A_MEMBER — `channel` is not currently commanded by that master (a typo fails
                     loudly rather than editing nothing)

    Capability delta: declares as LOST exactly the `(channel, command)` pairs the master
    sent to `channel` that no other group sends — for `Light Master`/25 that is
    `(25, 0)` (set-brightness) and `(25, 6)` (memory-off), which the master alone
    produces. The build's capability gate then surfaces the intended loss instead of a
    repurposed group silently eating a real control.
    """
    hardware_verified = True     # FLASHED AND VERIFIED 2026-09-14
    PARAMS = {
        "properties": {
            "master": {"type": "string",
                       "description": "master screen title, e.g. \"Light Master\""},
            "channel": {"type": "integer",
                        "description": "G12 channel to drop from the master (0-255)"},
        },
        "required": ["master", "channel"],
    }

    def validate(self):
        self.master = self._param("master", kind=str)
        self.channel = self._param("channel", kind=int)

    def apply(self, data, ctx):
        try:
            data, edits = mastergroup.drop_channel(data, self.master, self.channel)
        except mastergroup.MasterEditError as err:
            raise RecipeError(err.code, str(err))
        ctx.edits.extend(edits)
        data, _ = patch.reseal(data)
        return data

    def capability_delta(self, ctx):
        controls = mastergroup.master_controls(ctx.stock, self.master)
        if not controls:
            return set(), set()
        master_gids = {c.group for c in controls}
        gt = guidata.group_table(ctx.stock)
        bt = guidata.binding_table(ctx.stock)
        removed, elsewhere = set(), set()
        for gi, g in enumerate(gt):
            for j in range(g.start, min(g.start + g.count, len(bt))):
                if bt[j].channel != self.channel:
                    continue
                pair = (self.channel, bt[j].raw[4])
                (removed if gi in master_gids else elsewhere).add(pair)
        return removed - elsewhere, set()

    def verify(self, data, ctx):
        controls = mastergroup.master_controls(data, self.master)
        assert controls, "drop: master %r vanished" % self.master
        for c in controls:
            assert self.channel not in c.channels, \
                "drop: channel %d still in %r %s" % (self.channel, self.master, c.label)
        return "drop: channel %d removed from %r ON+OFF — HARDWARE-VERIFIED (coach, 2026-09-14)" % (
            self.channel, self.master)


@op("tank_heater_names")
class TankHeaterNames(Op):
    """GUICODE Finding 35: widen the diagnostic-name whitelists so WD names all four
    warmer rows. A TRADE: SY loses Grey and WT loses Black (no spare slots)."""

    PARAMS = {"properties": {}, "required": []}

    def apply(self, data, ctx):
        out, applied = tankgates.widen(data)
        out, _ = patch.reseal(out)
        return out

    def verify(self, data, ctx):
        sec = theme.guicode_section(data)
        base = theme.guicode_base(sec)
        n = sum(1 for a, _o, new, _w in tankgates.WIDEN_EDITS
                if data[sec.offset + (a - base):][:len(bytes.fromhex(new))]
                == bytes.fromhex(new))
        assert n == len(tankgates.WIDEN_EDITS), \
            "tank-heater edits present: %d/%d" % (n, len(tankgates.WIDEN_EDITS))
        return "tank-heater names: %d GuiCode edit(s) (trade: SY loses Grey, WT loses Black)" % n


@op("remove_option_interlocks")
class RemoveOptionInterlocks(Op):
    """GUICODE Finding 50: stop an OPTIONS row writing another row's selector.

    Stock couples Progressive Inverter and the AGS/AES mode — selecting PI also
    writes `EF := 1` (AGS), selecting AGS also writes `E6 := 1`, and either AES
    mode writes `E6 := 0` — so "PI and AES both on" is a state the panel cannot be
    driven to from its own screen. Each removal NOPs the 36-byte emit pair (the
    write frame and its read-back twin), which is same-size: nothing shifts.

    Safe to do unilaterally because the interlock has exactly one copy. It is
    emitted by the panel's tap handlers; the G12 only stores what it is told, and
    never originates an option-selection change (`BUS-BASELINE.md` PC.21 injected
    `E6 := 1` alone from an unrelated source address and the mode never moved). So
    there is no second enforcement point to fall out of step with.

    Params:
      couplings: [str]|None (default None = all four) — which to remove, from
                 `interlocks.CROSS_EMISSIONS`: "pi-forces-ags", "ags-forces-pi",
                 "aes-clears-pi", "aes-single-clears-pi".

    Error codes:
      UNKNOWN_INTERLOCK — a name not in `CROSS_EMISSIONS`
      INTERLOCK_REFUSED — a run did not emit the foreign selector exactly once, its
                          read-back twin was missing, or the block was not the
                          twelve-instruction emit idiom (i.e. this is not 13.6, or
                          an earlier op moved the code)

    Note what this does NOT touch: `D7 := 0` (Go Power! count), also written by the
    AGS/AES arms. It is a separate interlock, and leaving it is a deliberate choice
    rather than an oversight — see the module table.
    """

    PARAMS = {
        "properties": {
            "couplings": {"type": ["array", "null"], "default": None,
                          "items": {"type": "string"},
                          "description": "interlock names; null = all four"},
        },
        "required": [],
    }

    def validate(self):
        self.couplings = self._param("couplings", None)
        if self.couplings is not None:
            unknown = [c for c in self.couplings if c not in interlocks.CROSS_EMISSIONS]
            if unknown:
                raise RecipeError(
                    "UNKNOWN_INTERLOCK",
                    "unknown interlock(s) %s; known: %s"
                    % (unknown, sorted(interlocks.CROSS_EMISSIONS)),
                    op_id=self.spec.id)

    def apply(self, data, ctx):
        try:
            out, edits = interlocks.uncouple(data, self.couplings)
        except ValueError as err:
            raise RecipeError("INTERLOCK_REFUSED", str(err), op_id=self.spec.id)
        ctx.edits.extend(edits)
        out, _ = patch.reseal(out)
        return out

    def verify(self, data, ctx):
        names = self.couplings if self.couplings is not None else list(
            interlocks.CROSS_EMISSIONS)
        for name in names:
            entry, own, foreign, _why = interlocks.CROSS_EMISSIONS[name]
            emitted = [e.selector for e in interlocks.run_emissions(data, entry) if e.write]
            assert foreign not in emitted, \
                "%s: arm %#x still emits %#04x (%s)" % (name, entry, foreign, emitted)
            # The row must still do its OWN job -- a NOP run one instruction too long would
            # show up here as a missing write rather than as a silent no-op.
            assert own in emitted, \
                "%s: arm %#x lost its own %#04x write (%s)" % (name, entry, own, emitted)
        return "option interlocks removed: %s" % ", ".join(names)


@op("split_hold_region")
class SplitHoldRegion(Op):
    """Split a control's hit region so the right-hand strip is a HOLD, and wire it to a screen.

    CONTROL-BEHAVIOR §1/§7. The action dispatcher is edge-triggered on the touch id, so a
    region's tap handler fires the instant the finger lands -- a tap and a hold can share one
    region only when the tap is harmless to perform on the way through, which a load toggle is
    not. Two non-overlapping regions are the way out; overlapping them would be useless because
    the hit test returns the LOWEST matching id.

    The new region carries `hold_id`, which must already have a protected-touch arm. On 13.6
    that is `0x1795` (`holdregion.DORMANT_HOLD_ID`), a dispatch leaf with a working arm that no
    object registers -- so nothing is repointed to use it.

    Params:
      object: int — stock object index owning the hit region (must have exactly one)
      tap_width: int — new width of the retained tap region
      hold_x: int — the new region's pen offset
      hold_width: int — the new region's width
      screen_object: int — the OBJECT the hold opens. Not a screen id: `state+0x20A` ids are
                     a match-table lookup, not `object - 4`, and that arithmetic aimed an
                     earlier build at the wrong page (owner caught it on the coach).
      preserve: [[touch_id, object], ...] — arms the dispatch stub must re-implement. The
                consumer's second arm is replaced by a branch to the stub, so anything that
                arm used to do must be listed here or it is silently lost.
      hold_id: int (default 0x1795) — the touch id for the new region
      delay_ms: int (default 3000) — hold threshold
      expect: [[x0,y0,x1,y1],[x0,y0,x1,y1]]|None — required tap/hold boxes, asserted against
              a layout re-walk of the finished image. Geometry here is FLOW-RELATIVE, so pass
              this: it is what turns `hold_x` from a guess into a checked fact.
      preview_slots: {str:int}|None — slot values the walk needs to render the control at all

    Error codes:
      SPLIT_REFUSED — the object does not have exactly one hit region, the widths are out of
                      range, the synthesised record does not decode, or the split did not land
                      on `expect`
      HOLD_WIRE_REFUSED — a GuiCode site did not hold its expected stock bytes

    **No shortcut is sacrificed.** The consumer chain has two arms and no room for a third, and
    GuiCode cannot grow -- but it does not have to: `install_hold_dispatch` puts a dispatch stub
    in dead space (the NOP runs `remove_option_interlocks` leaves behind) and points the second
    arm at it, re-implementing that arm alongside the new one. `preserve` is what makes that
    explicit, and `verify` re-reads every entry from the finished image.
    """

    PARAMS = {
        "properties": {
            "object": {"type": "integer"},
            "tap_width": {"type": "integer"},
            "hold_x": {"type": "integer"},
            "hold_width": {"type": "integer"},
            "screen_object": {"type": "integer",
                              "description": "object index the hold opens (NOT a screen id)"},
            "preserve": {"type": "array", "default": [],
                         "description": "[[touch_id, object], ...] arms the stub must keep"},
            "hold_id": {"type": "integer", "default": holdregion.DORMANT_HOLD_ID},
            "delay_ms": {"type": "integer", "default": 3000},
            "expect": {"type": ["array", "null"], "default": None},
            "preview_slots": {"type": ["object", "null"], "default": None},
        },
        "required": ["object", "tap_width", "hold_x", "hold_width", "screen_object"],
    }

    def validate(self):
        self.object = self._param("object", kind=int)
        self.tap_width = self._param("tap_width", kind=int)
        self.hold_x = self._param("hold_x", kind=int)
        self.hold_width = self._param("hold_width", kind=int)
        self.screen_object = self._param("screen_object", kind=int)
        self.preserve = self._param("preserve", [])
        self.hold_id = self._param("hold_id", holdregion.DORMANT_HOLD_ID, kind=int)
        self.delay_ms = self._param("delay_ms", 3000, kind=int)
        self.expect = self._param("expect", None)
        self.preview_slots = self._param("preview_slots", None)

    def _slots(self):
        return {int(k): int(v) for k, v in (self.preview_slots or {}).items()}

    def apply(self, data, ctx):
        expect = tuple(tuple(b) for b in self.expect) if self.expect else None
        try:
            data, e1 = holdregion.split_touch_region(
                data, self.object, tap_width=self.tap_width, hold_x=self.hold_x,
                hold_width=self.hold_width, hold_id=self.hold_id, expect=expect,
                slot_values=self._slots())
        except (ValueError, relocate.RelocationError) as err:
            raise RecipeError("SPLIT_REFUSED", str(err), op_id=self.spec.id)
        ctx.edits.extend(e1)
        data, _ = patch.reseal(data)
        try:
            data, e2 = holdregion.set_hold_delay(data, self.delay_ms)
            ctx.edits.extend(e2)
            entries = [(int(tid), layout.screen_for_object(data, int(obj)))
                       for tid, obj in self.preserve]
            entries.append((self.hold_id, layout.screen_for_object(data, self.screen_object)))
            data, e3 = holdregion.install_hold_dispatch(data, entries)
        except ValueError as err:
            raise RecipeError("HOLD_WIRE_REFUSED", str(err), op_id=self.spec.id)
        ctx.edits.extend(e3)
        return patch.reseal(data)[0]

    def verify(self, data, ctx):
        boxes = holdregion.region_boxes(data, self.object, slot_values=self._slots())
        assert len(boxes) == 2, "expected 2 hit regions, walked %d" % len(boxes)
        if self.expect:
            assert boxes == [tuple(b) for b in self.expect], "regions landed at %s" % (boxes,)
        assert boxes[0][2] < boxes[1][0], "tap and hold regions overlap"
        ids = gestures.touch_ids(data, self.object)
        assert self.hold_id in ids, "the hold id is not registered: %s" % (ids,)
        sec = theme.guicode_section(data); base = theme.guicode_base(sec)
        at = lambda v, n: data[sec.offset + (v - base):][:n]
        assert at(holdregion.ARM_DELAY_SITE, 4) == holdregion.ARM_DELAY_3000
        # Read the whole hold behaviour back by walking the consumer and following the stub.
        # Every preserved arm must still resolve to its own page: this op adds a destination,
        # it must never cost one.
        got = holdregion.hold_dispatch_map(data)
        smap = layout.screen_object_map(data)
        want = {int(t): int(o) for t, o in self.preserve}
        want[self.hold_id] = self.screen_object
        for tid, obj in want.items():
            assert tid in got, "hold id %#x no longer dispatches" % tid
            assert smap[got[tid]] == obj, \
                "id %#x -> screen %d -> object %d, expected %d" % (tid, got[tid],
                                                                   smap[got[tid]], obj)
        return ("hold region %s id %#06x -> object %d (%d ms); tap kept at %s; "
                "preserved %s" % (boxes[1], self.hold_id, self.screen_object, self.delay_ms,
                                  boxes[0], [hex(int(t)) for t, _o in self.preserve]))


def _check_named_output(data, channel: int, allow: bool) -> None:
    """Refuse a button on an output the panel's G12 OUTPUTS page names for a load under ANY
    floorplan or option (`outputs.named_outputs`), unless the recipe acknowledges it. The
    channel-collision check cannot see this: a load driven by G12 rules (13.6's
    "12 - A/C Fan Low") has no panel binding to collide with.

    Every condition counts, not the owner's: floorplan and option state lives in the G12's NVM
    and is not build input (`recipe.Project`'s coach-config note), so a recipe cannot know which
    names its coach shows, and a coach can be reconfigured after the flash. 13.6's output 05 is
    blank on most floorplans and "Tank Heat Fresh" on WA/WD/WT; it is refused everywhere."""
    if allow:
        return
    from . import outputs
    try:
        named = outputs.named_outputs(data).get(channel, [])
    except ValueError:          # no outputs list on this build: nothing to compare with
        return
    if named:
        shown = "; ".join("%r (%s)" % (n.name, n.when) if n.name is not None
                          else "a name GuiCode sets at run time (%s)" % n.when for n in named)
        raise RecipeError(
            "NAMED_OUTPUT",
            "output %d is named on this build's G12 OUTPUTS list: %s. A button on it switches "
            "that load. Confirm the owner's load is on output %d, then pass "
            "allow_named_output=True" % (channel, shown, channel),
            offers=["acknowledge_named_output"])


@op("add_button")
class AddButton(Op):
    """Add a new HOME button end to end: bind the pin's status colour cell
    (`statusbind.bind_pin`), then build the boxed button on that pin directly
    (`newbutton.build_home_button`, donor-279 path — build-on-target, never
    build-then-retarget; see newbutton's own docstring for why a fresh
    `allocate_command_group` call per retarget orphans a live group).

    Params:
      channel: int — destination G12 channel/pin (0-255). This op is build-on-target
               by construction: there is no separate retarget step, so the channel
               given here is final.
      label: str — caption text (may contain '\\n'); padded to the donor field's
             width, must not exceed it (same-size only — a longer caption is a
             relocating rename_label job, not this op)
      off_colour: [r,g,b] — the pin's OFF state colour, forwarded to bind_pin
      font: int|None (default None) — (optional) caption font slot; if given, the
            new caption's R38 selector must be the 0xff inherit default (it always
            is, fresh off the donor clone)
      allow_commanded_channel: bool (default False) — opt out of build_home_button's
             channel-collision check (see newbutton.retarget_command's own docstring
             for when this judgement call is legitimate — a channel named only by
             groups the coach's floorplan cannot reach)
      allow_named_output: bool (default False) — acknowledge that the G12 OUTPUTS list
             names `channel` for a load and the owner's load is on it anyway

    Error codes:
      NAMED_OUTPUT — the G12 OUTPUTS list names `channel` for a load under some
                     floorplan or option (13.6's 12 is "A/C Fan Low" always, 05 is
                     "Tank Heat Fresh" on WA/WD/WT); offers=["acknowledge_named_output"]
                     — retry with allow_named_output=True once the owner has confirmed
                     the output
      BIND_REFUSED — statusbind.bind_pin refused (unwatched pin, no bridge block,
                     or the pin is already commanded by a LOCATED control — a
                     narrower check than build_home_button's own, see statusbind.py)
      COMMANDED_CHANNEL — build_home_button refused because the channel is already
                         commanded by an existing binding (its own, broader scan);
                         offers=["acknowledge"] — the caller may retry with
                         allow_commanded_channel=True once they've confirmed (as the
                         docstring above describes) that those bindings belong to
                         unreachable controls
      BUILD_REFUSED — any other newbutton.build_home_button refusal
      NO_FONT_SELECTOR — font was given but the new caption's R38 selector isn't the
                         expected 0xff inherit byte

    This is a relocating op (clone_onto_home inserts a new object). Registers
    ctx.handles[op_id] = {"object": idx, "cell": cell, "group": gid} so later ops
    can target this button via {"ref": op_id}. Declares gained {(channel, 5)} —
    exactly the single command-5 toggle binding this button carries.
    """
    PARAMS = {
        "properties": {
            "channel": {"type": "integer", "description": "G12 output pin (RV-C instance)"},
            "label": {"type": "string", "description": "caption; \\n makes two lines"},
            "off_colour": {"type": "array", "items": {"type": "integer"},
                           "minItems": 3, "maxItems": 3,
                           "description": "[r, g, b] indicator colour when off"},
            "font": {"type": ["integer", "null"], "default": None,
                     "description": "caption font slot; selector must be the 0xff inherit default"},
            "allow_commanded_channel": {"type": "boolean", "default": False,
                                       "description": "opt out of the channel-collision check"},
            "allow_named_output": {"type": "boolean", "default": False,
                                   "description": "acknowledge an output the G12 OUTPUTS list "
                                                  "names for a load"},
        },
        "required": ["channel", "label", "off_colour"],
    }

    def validate(self):
        self.channel = self._param("channel", kind=int)
        self.label = self._param("label", kind=str)
        self.off_colour = tuple(self._param("off_colour", kind=list))
        self.font = self._param("font", None)
        self.allow_commanded = self._param("allow_commanded_channel", False, kind=bool)
        self.allow_named = self._param("allow_named_output", False, kind=bool)

    def apply(self, data, ctx):
        _check_named_output(data, self.channel, self.allow_named)
        try:
            data, cell, e = statusbind.bind_pin(data, self.channel,
                                                off_colour=self.off_colour)
        except statusbind.StatusBindError as err:
            raise RecipeError("BIND_REFUSED", str(err))
        ctx.edits.extend(e)
        try:
            data, e = newbutton.build_home_button(
                data, channel=self.channel,
                allow_commanded_channel=self.allow_commanded,
                colour_index=cell, label=self.label)
        except ValueError as err:
            # Match only the specific "already commanded by" refusal wording
            # (newbutton.py:354-358). A bare "command" substring also appears in the
            # UNRELATED "non-DC_DIMMER_COMMAND_2 entries" refusal (newbutton.py:364-365,
            # "COMMAND_2" lowercased contains "command"), which allow_commanded_channel
            # cannot fix -- that must fall through to BUILD_REFUSED, not be offered a
            # useless "acknowledge".
            if "already commanded by" in str(err) and not self.allow_commanded:
                raise RecipeError(
                    "COMMANDED_CHANNEL",
                    "channel %d is already commanded elsewhere in this file: %s"
                    % (self.channel, err), offers=["acknowledge"])
            raise RecipeError("BUILD_REFUSED", str(err))
        ctx.edits.extend(e)
        idx = newbutton.last_object_index(data)

        if self.font is not None:
            gs, tab = _gs_tab(data)
            cap = next(r for r in records.object_records(data, gs + tab[idx])
                       if r.wtype == 11 and r.label
                       # per-LINE strip: a centred caption's pad straddles its last line
                       and records.label_text(r.label) == records.label_text(self.label))
            fo = records.font_offset(data, cap)
            if fo is None or data[fo] != 0xFF:
                raise RecipeError("NO_FONT_SELECTOR",
                                  "new caption's selector is %s, expected 0xff inherit"
                                  % (fo if fo is None else hex(data[fo])))
            buf = bytearray(data)
            buf[fo] = self.font
            ctx.edits.append(relocate.Edit(fo, b"\xff", bytes([self.font]), "recipe",
                                           "new button caption font -> slot %d"
                                           % self.font))
            data = bytes(buf)
        data, _ = patch.reseal(data)
        ctx.handles[self.spec.id] = {"object": idx, "cell": cell,
                                     "group": newbutton.object_group(data, idx)}
        return data

    def capability_delta(self, ctx):
        return set(), {(self.channel, 5)}

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        idx, cell = h["object"], h["cell"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])
        assert any(getattr(r, "label", "")
                   and records.label_text(r.label) == records.label_text(self.label)
                   for r in recs), "caption %r lost" % self.label
        w33 = next(r for r in recs if r.wtype == 33)
        fillA = [c for c in w33.colors if c[0] == "fillA"][0]
        slots = {r.value_slot for r in recs if r.value_slot is not None}
        assert fillA[1] == 7 and fillA[2] == cell and slots == {cell}, \
            "live-redraw invariant broken: fillA %s, slots %s, cell %d" \
            % (fillA, slots, cell)
        gid = newbutton.object_group(data, idx)
        grp = guidata.group_table(data)[gid]
        b0 = guidata.binding_table(data)[grp.start]
        assert grp.count == 1 and b0.channel == self.channel and b0.raw[4] == 5
        if self.font is not None:
            cap = next(r for r in recs if r.wtype == 11 and r.label
                       and records.label_text(r.label) == records.label_text(self.label))
            assert cap.font == self.font
        return "button: object %d %r on pin %d (cell %d)" % (
            idx, self.label.replace("\n", "\\n"), self.channel, cell)


@op("add_glyph_button")
class AddGlyphButton(Op):
    """Add a bare button shell to a page by cloning a donor object and sizing every
    record (box/icon/caption/touch) to w x h, with fillA and every value_slot pointed
    at a placeholder cell (`fill_cell`).

    NOT a visual-only shell: `insert.clone_object` copies the donor's bytes verbatim,
    including its wtype-12 group reference -- so an unwired clone of donor 247 still
    carries donor 247's live command group (group 40, two bindings on channel 40) and
    is a live touch target the capability gate cannot see (there is no NEW pair to
    declare, since it is the donor's own pair, already counted). `verify` refuses a
    shell that was never wired for exactly this reason -- pair this op with
    `wire_button` before shipping it; a wire_button call overwrites the inherited
    group with the shell's own.

    Params:
      page: str|int — page name (resolved via cli._resolve_page_root) or object index
            to attach the clone under
      donor: int (default 247) — object index to clone (insert.clone_object)
      x: int, y: int — absolute attach position (insert.insert_object position=)
      w: int, h: int — size written to every geometry dim2/dim3 field the clone's
         box/icon/caption/touch records carry
      fill_cell: int — placeholder mode-7 colour-table index written to fillA and every
                 value_slot (wire_button overwrites this with the pin's real cell)

    Error codes:
      INSERT_REFUSED — insert.insert_object refused (bad parent, no template, etc.)
      NO_FIELD — a box/icon/caption/touch record lacks the w or h geometry field
      NO_SUCH_PAGE — page name cannot be resolved to an object index (via cli)

    This is a relocating op (insert_object appends a new object). Registers
    ctx.handles[op_id] = {"object": idx, "cell": fill_cell, "group": None} — group is
    None until a later `wire_button` gives the shell a command group.
    """
    PARAMS = {
        "properties": {
            "page": {"type": ["string", "integer"],
                     "description": "page name (resolved via cli._resolve_page_root) or object index"},
            "donor": {"type": "integer", "default": 247,
                      "description": "object index to clone"},
            "x": {"type": "integer", "description": "absolute attach x-position"},
            "y": {"type": "integer", "description": "absolute attach y-position"},
            "w": {"type": "integer", "description": "width written to every box/icon/caption/touch geometry field"},
            "h": {"type": "integer", "description": "height written to every box/icon/caption/touch geometry field"},
            "fill_cell": {"type": "integer",
                          "description": "placeholder mode-7 colour-table index for fillA and every value_slot"},
        },
        "required": ["page", "x", "y", "w", "h", "fill_cell"],
    }

    def validate(self):
        self.page = self._param("page")
        self.donor = self._param("donor", 247, kind=int)
        self.x = self._param("x", kind=int)
        self.y = self._param("y", kind=int)
        self.w = self._param("w", kind=int)
        self.h = self._param("h", kind=int)
        self.fill_cell = self._param("fill_cell", kind=int)

    def apply(self, data, ctx):
        if isinstance(self.page, int):
            root = self.page
        else:
            try:
                root = cli._resolve_page_root(data, self.page)
            except Exception as e:
                raise RecipeError("NO_SUCH_PAGE", "cannot resolve page %r: %s"
                                  % (self.page, e))
        clone = insert.clone_object(data, self.donor)
        try:
            data, e = insert.insert_object(data, clone, root,
                                           position=(self.x, self.y))
        except insert.InsertError as err:
            raise RecipeError("INSERT_REFUSED", str(err))
        ctx.edits.extend(e)
        gs, tab = _gs_tab(data)
        idx = len(tab) - 1

        buf = bytearray(data)
        recs = records.object_records(data, gs + tab[idx])
        w33 = [r for r in recs if r.wtype == 33][0]
        icon = [r for r in recs if r.wtype == 11 and r.font is not None][0]
        cap = [r for r in recs if r.wtype == 11 and r is not icon][0]
        touch = [r for r in recs if r.wtype == 12][0]
        # The glyph takes the whole button: icon cell full width, caption cell too
        # (it collapses visually once blanked by a set_icon op).
        for rec, what in ((w33, "box"), (icon, "icon cell"),
                          (cap, "caption cell"), (touch, "touch")):
            for i, want in ((2, self.w), (3, self.h)):
                off = records.geom_offset(data, rec, i)
                if off is None:
                    raise RecipeError("NO_FIELD", "%s has no dim%d field" % (what, i))
                _put16(buf, off, want, ctx, "%s dim%d -> %d" % (what, i, want))
        fillA_off = records.color_offset(data, w33, "fillA")
        if fillA_off is None:
            raise RecipeError("NO_FIELD", "donor %d box has no fillA colour field"
                              % self.donor)
        _put16(buf, fillA_off, self.fill_cell, ctx, "fillA -> %d" % self.fill_cell)
        for rec, what in ((w33, "box"), (icon, "icon"), (cap, "caption")):
            o = records.value_slot_offset(data, rec)
            if o is not None:
                _put16(buf, o, self.fill_cell, ctx,
                       "%s value_slot -> %d" % (what, self.fill_cell))
        data, _ = patch.reseal(bytes(buf))
        ctx.handles[self.spec.id] = {"object": idx, "cell": self.fill_cell,
                                     "group": None}
        return data

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        idx = h["object"]
        assert h["group"] is not None, \
            ("shell (object %d) was never wired -- it still carries donor %d's "
             "inherited command group" % (idx, self.donor))
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])
        for r in recs:
            if r.wtype in (33, 11, 12):
                for i, want in ((2, self.w), (3, self.h)):
                    off = records.geom_offset(data, r, i)
                    assert off is not None, \
                        "object %d wtype-%d record has no dim%d field" \
                        % (idx, r.wtype, i)
                    got = struct.unpack_from("<H", data, off)[0]
                    assert got == want, \
                        "object %d wtype-%d record dim%d is %d, wanted %d" \
                        % (idx, r.wtype, i, got, want)
        return "glyph button shell: object %d on page %r at (%d,%d) %dx%d" % (
            idx, self.page, self.x, self.y, self.w, self.h)


@op("wire_button")
class WireButton(Op):
    """Wire a bare button shell (from `add_glyph_button`, or any object with a
    wtype-33/wtype-12 pair) to a G12 pin: bind the pin's status colour cell
    (`statusbind.bind_pin`), point the indicator at it (`newbutton.retarget_indicator`),
    then give the object its own single command-5 toggle group on that channel
    (`newbutton.retarget_command`).

    Params:
      target: int|{"ref"} — object index; resolved via resolve_target(ctx, target)
      channel: int — destination G12 channel/pin (0-255)
      off_colour: [r,g,b] — the pin's OFF state colour, forwarded to bind_pin
      allow_commanded_channel: bool (default False) — opt out of retarget_command's
             channel-collision check; see newbutton.retarget_command's own docstring
             for when this judgement call is legitimate
      allow_named_output: bool (default False) — acknowledge that the G12 OUTPUTS list
             names `channel` for a load and the owner's load is on it anyway

    Error codes:
      NAMED_OUTPUT — the G12 OUTPUTS list names `channel` for a load under some
                     floorplan or option; offers=["acknowledge_named_output"] — retry with
                     allow_named_output=True once the owner has confirmed it
      BAD_REF — target names an earlier op id that produced no object (from
                resolve_target)
      BIND_REFUSED — statusbind.bind_pin refused (unwatched pin, no bridge block, or
                     the pin is already commanded by a LOCATED control)
      COMMANDED_CHANNEL — retarget_command refused because the channel is already
                         commanded by an existing binding; offers=["acknowledge"] —
                         the caller may retry with allow_commanded_channel=True once
                         they've confirmed those bindings belong to unreachable controls
      WIRE_REFUSED — any other newbutton.retarget_command refusal, OR
                     newbutton.retarget_indicator refusing the target (out-of-range
                     object index, or an object with no wtype-33 indicator record --
                     e.g. a target that isn't a button shell at all)

    **One-group discipline:** this op calls `retarget_command` exactly once per
    compile, on a shell that has never been wired -- so only one group is ever
    allocated per button. The orphaned-group defect needed build-then-*re*-target;
    a recipe re-wire is a *changed op*, which recompiles from stock, so the same
    trap cannot recur here (see the module docstring above).

    Declares gained {(channel, 5)} -- exactly the single command-5 toggle binding this
    wiring adds. Updates the target handle's cell/group if the target is a ref, so a
    later op can address the now-wired button by the same id.
    """
    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id} of the button shell"},
            "channel": {"type": "integer", "description": "destination G12 channel/pin (0-255)"},
            "off_colour": {"type": "array", "items": {"type": "integer"},
                           "minItems": 3, "maxItems": 3,
                           "description": "[r, g, b] indicator colour when off"},
            "allow_commanded_channel": {"type": "boolean", "default": False,
                                       "description": "opt out of the channel-collision check"},
            "allow_named_output": {"type": "boolean", "default": False,
                                   "description": "acknowledge an output the G12 OUTPUTS list "
                                                  "names for a load"},
        },
        "required": ["target", "channel", "off_colour"],
    }

    def validate(self):
        self.target = self._param("target")
        self.channel = self._param("channel", kind=int)
        self.off_colour = tuple(self._param("off_colour", kind=list))
        self.allow_commanded = self._param("allow_commanded_channel", False, kind=bool)
        self.allow_named = self._param("allow_named_output", False, kind=bool)

    def apply(self, data, ctx):
        _check_named_output(data, self.channel, self.allow_named)
        idx = resolve_target(ctx, self.target)
        try:
            data, cell, e = statusbind.bind_pin(data, self.channel,
                                                off_colour=self.off_colour)
        except statusbind.StatusBindError as err:
            raise RecipeError("BIND_REFUSED", str(err))
        ctx.edits.extend(e)
        try:
            data, e = newbutton.retarget_indicator(data, idx, colour_index=cell)
        except ValueError as err:
            raise RecipeError("WIRE_REFUSED",
                              "retarget_indicator refused object %d: %s" % (idx, err))
        ctx.edits.extend(e)
        try:
            data, e = newbutton.retarget_command(
                data, idx, channel=self.channel,
                allow_commanded_channel=self.allow_commanded)
        except ValueError as err:
            if "already commanded by" in str(err) and not self.allow_commanded:
                raise RecipeError("COMMANDED_CHANNEL",
                                  "channel %d is already commanded elsewhere: %s"
                                  % (self.channel, err), offers=["acknowledge"])
            raise RecipeError("WIRE_REFUSED", str(err))
        ctx.edits.extend(e)
        data, _ = patch.reseal(data)
        group = newbutton.object_group(data, idx)
        if isinstance(self.target, dict):
            ctx.handles[self.target["ref"]].update(cell=cell, group=group)
        ctx.handles[self.spec.id] = {"object": idx, "cell": cell, "group": group}
        return data

    def capability_delta(self, ctx):
        return set(), {(self.channel, 5)}

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        idx, cell = h["object"], h["cell"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])
        w33 = next(r for r in recs if r.wtype == 33)
        fillA = [c for c in w33.colors if c[0] == "fillA"][0]
        slots = {r.value_slot for r in recs if r.value_slot is not None}
        assert fillA[1] == 7 and fillA[2] == cell and slots == {cell}, \
            "live-redraw invariant broken: fillA %s, slots %s, cell %d" \
            % (fillA, slots, cell)
        gid = newbutton.object_group(data, idx)
        grp = guidata.group_table(data)[gid]
        b0 = guidata.binding_table(data)[grp.start]
        assert grp.count == 1 and b0.channel == self.channel and b0.raw[4] == 5, \
            "wire: group %d count=%d channel=%s cmd=%s (wanted 1 binding, channel " \
            "%d, command 5)" % (gid, grp.count, b0.channel, b0.raw[4], self.channel)
        return "wired: object %d -> pin %d (cell %d, single command-5 toggle)" % (
            idx, self.channel, cell)


# Inline-value width by colour mode (`records.decode_record`'s own `colour()` helper: mode 3 =
# BGR u24, mode 6 = palette u8, mode 7 = colour-table u16; every other mode carries no inline
# byte and its `color_off` is already `None`, so this dict is never queried for one).
_COLOUR_WIDTH = {3: 3, 6: 1, 7: 2}


def _exposed_spans(r: "records.WRecord") -> list:
    """[(start, end)) for every byte range `r`'s DECODED fields occupy -- the header, each
    present colour field (by its mode's own width), each present geometry u16, and (the only
    other field shapes a wt-33 record can carry) `value_slot`/`child_index`/`fmt`/`count`/
    `font`/label. Anything inside `r`'s own `[offset, offset + size)` NOT covered by one of
    these spans is a field `decode_record` reads but never names on `WRecord` at all -- the
    R18-R22 per-coord register-index/clip bytes and the R29-R34 "value/index slot" loop
    (`records.py`'s own comment for that loop), gated purely by header bytes, not by widget
    type -- so this is a coverage computation, not a guess at which of those it is."""
    spans = [(r.offset, r.offset + 8)]
    for off, (_role, mode, _val) in zip(r.color_off, r.colors):
        if off is not None:
            spans.append((off, off + _COLOUR_WIDTH[mode]))
    for off in r.geom_off:
        if off is not None:
            spans.append((off, off + 2))
    for off, width in ((r.value_slot_off, 2), (r.child_index_off, 2), (r.fmt_off, 4),
                       (r.count_off, 2), (r.font_off, 1)):
        if off is not None:
            spans.append((off, off + width))
    if r.label_offset is not None:
        spans.append((r.label_offset, r.end))
    return spans


def _trailing_gap(r: "records.WRecord") -> int:
    """How many of `r`'s own last bytes are covered by NONE of `_exposed_spans(r)` -- 0 when
    the decoded fields run flush to the record's end (nothing hidden), 2 for the shape a
    gauge/capsule level index takes (one hidden trailing u16), anything else is a record
    shape this function does not understand and the caller must not guess at."""
    spans = _exposed_spans(r)
    n = r.size
    covered = bytearray(n)
    for s, e in spans:
        for i in range(max(0, s - r.offset), min(n, e - r.offset)):
            covered[i] = 1
    gap = 0
    i = n - 1
    while i >= 0 and not covered[i]:
        gap += 1
        i -= 1
    return gap


def _gauge_level_indices(data: bytes) -> set:
    """`+0x44BC8` indices already named by some wt-33 fillA-mode7 record's undecoded trailing
    u16 -- the gauge/capsule partial-fill "empty offset" `records.decode_record` reads but
    never exposes on `WRecord` (located here by `_trailing_gap`, not a hand-rolled geometry sum -- an
    earlier version summed only `colour + geom` widths and silently mislocated the field
    whenever a record also carried one of the header-gated-but-unexposed R18-R22/R29-R34
    bytes: wrong on 276 of the 13.6 corpus's 840 wt-33 records (diff histogram
    `{0:371,1:241,2:35,3:1}` against the true, decoder-derived end -- e.g. object 803 was off
    by one).

    `statusbind.spare_cell_indices` cannot see this field at all -- it only tracks `colors`
    and `value_slot`, both decoded fields -- so an index this function reports as "used" can
    still come back from `spare_cell_indices` as free. Found isolating the home-48V battery
    clone: object 440 (chassis) pairs colour index 202 with level index 203, object 443
    (house, the clone's donor) pairs 206 with 207, and `spare_cell_indices` freely offers
    203/207 straight back even though both are live -- repointing either would silently move
    the OTHER stock battery's fill level onto whatever cell this op's own allocation owns.
    Whole-corpus scan, `inferred-from-data` like `spare_cell_indices` itself; 16 indices on
    13.6 (`_trailing_gap` returns 0 or 2 for all 840 wt-33 records in the corpus -- never
    raises here in practice).

    Scoped to `colors[0]` mode 7 (fillA): a NON-mode-7 wt-33 record can carry this same
    2-byte hidden gap too (objects 721-727's mode-3 capsules, values 254/256/258/260 --
    `AddVoltageBattery`'s own safe pool). Checked rather than assumed: those values are not
    `GuiCode`-referenced as `+0x44BC8` pointers by anything this project's tracing can see
    (`sramreach.reaching_forms`), and each equals its own sibling wt-5 container's own
    (separately decoded, already `spare_cell_indices`-tracked) `value_slot` minus exactly
    one (721 -> parent value_slot 255, 723 -> 257, 725 -> 259, 727 -> 261) -- a layout/ID
    pairing, not an independent cell reference. `inferred-from-data`; not proven-from-code,
    so left OUT of this function's `used` set deliberately rather than folded in by a wider
    filter that would just be a different fitted answer."""
    gs, tab = _gs_tab(data)
    used = set()
    for off in tab:
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for r in recs:
            if r.wtype != 33 or not r.colors:
                continue
            gap = _trailing_gap(r)
            if gap == 0:
                continue                       # decoded fields run flush to the end
            mode = r.colors[0][1]
            if gap != 2:
                if mode == 7:
                    raise RecipeError(
                        "NO_DISPLAY_CELL",
                        "object record @ %#x (wt 33, fillA mode 7) has a %d-byte undecoded "
                        "gap before its end, not the clean 2-byte gauge-level shape; "
                        "refusing to guess which bytes (if any) are a +0x44BC8 index"
                        % (r.offset, gap))
                continue                       # non-mode-7, irregular shape: not this op's
                                               # concern (see docstring -- documented, not
                                               # silently trusted)
            if mode == 7:
                tail_off = r.offset + r.size - 2
                used.add(int.from_bytes(data[tail_off:tail_off + 2], "little"))
    return used


# The variable-table TYPE byte a fresh 1-byte gauge-level cell needs so the value formatter
# (`FUN_00251E38`) reads it at the right width -- the byte-read type, matching what stock's
# own level indices (203/207) already carry and `readout.py`'s private
# `_TYPE_FOR_WIDTH[1]` names for the same reason.
_LEVEL_VAR_TYPE = 1


def _isolate_cell(data: bytes, ctx, index: int, width: int, vtype, note: str):
    """Repoint spare `+0x44BC8` index `index` at a fresh, 4-aligned, wholly-unreferenced SRAM
    word (`sramreach.free_runs`, which already excludes every address ANY cell-table entry
    currently names, spare or not) and, when `vtype` is not None, set its variable-table type
    byte to match. Mirrors `readout.allocate_display_cell`'s tier-3 allocation, but the index
    is chosen by the caller (`_gauge_level_indices` plus `statusbind.spare_cell_indices`)
    rather than picked internally -- `allocate_display_cell` cannot see the gauge-level hazard
    above. Returns `(image, cell_addr)`.

    `min_bytes` is `width + 3`: the tightest run that can always yield a 4-aligned `width`-byte
    address (up to 3 bytes of slack to reach the next 4-aligned byte, then `width` more) --
    width-aware rather than a flat floor, so a width-1/2 cell is not refused a run a wider one
    would need. Runs are tried longest-first (`free_runs`'s own order) and skipped, not
    truncated to, when the 4-aligned address inside one would not leave `width` bytes -- the
    every-cell-4-aligned test in `tests/test_add_voltage_battery.py` caught an earlier version
    that fell back to the run's own (not necessarily 4-aligned) `start` here instead."""
    runs = sramreach.free_runs(data, min_bytes=width + 3)
    for start, length in runs:
        addr = (start + 3) & ~3
        if addr + width <= start + length:
            break
    else:
        raise RecipeError("NO_DISPLAY_CELL", "no free, 4-aligned SRAM run for %s" % note)
    try:
        out, edits = statusbind.index_cell(data, index, addr)
        if vtype is not None:
            out, tedits = statusbind.set_variable_type(out, index, vtype)
            edits = list(edits) + list(tedits)
    except statusbind.StatusBindError as e:
        raise RecipeError("NO_DISPLAY_CELL", "%s: %s" % (note, e))
    ctx.edits.extend(edits)
    return out, addr


# --- Task 5: position the chassis/coach battery pair on object 902 ----------------------------
#
# Object 902's own records are FLOW-positioned (pinned by `test_two_batteries_centered_
# in_lower_right`): a record's x/y geometry is a DELTA off the position the previous FLOW-GATED
# record committed (`layout._in_flow_gate`; wtype 5/11 both gate, wtype 33 does not), not an
# absolute or container-relative offset -- and a container's subtree recursion
# (`layout.Screen.walk`) does not save/restore the flow-origin registers around it, only the
# fill-colour ones. So after a wt-5 attach recurses into its child object, the flow origin for
# whatever comes NEXT in the parent's own record stream is whatever the child's own LAST
# flow-gated leaf left it at (empirically, the battery graphic's trailing 'V' unit label) --
# not the attach's own committed position. Two consequences drive this op's layout:
#
#   1. Moving a battery's ATTACH node alone shifts the drawn graphic but leaves its NAME LABEL
#      (a separate record on object 902 itself, drawn BEFORE the attach) exactly where it was
#      -- the two visibly separate. Moving the LABEL that precedes an attach instead shifts BOTH
#      together (the attach's own flow delta is unchanged, but the origin it's measured from
#      moves with the label), which is what keeps a battery's name glued to its icon.
#   2. The second pair's (House's) position is only reachable by calibrating against the FIRST
#      pair's own trailing 'V' position, not against object 902's container origin -- the two
#      are not independent unless the chassis's own shift is held fixed, which it is here (a
#      single hardcoded constant, not one of this op's params).
#
# `_CHASSIS_LABEL_DX` is calibrated so the CHASSIS battery body centres at x=732 (the RIGHT-hand
# slot); everything else -- the two attaches' own flow deltas -- keeps the template's stock/
# default value (85) unchanged. `_reposition_pair` solves the SECOND (House/clone) label's (x, y)
# offset from the caller's desired clone centre/row, using the linear relationship this fixed
# chassis shift produces.
#
# The layout matches the AGS variant (object 903), the owner's reference for the compact power
# panel: House battery on the LEFT (centre 556), Chassis on the RIGHT (centre 732), ~176px apart,
# each name label to the left of its own body, no label overlapping the neighbouring body. (The
# owner asked to swap House/Chassis from an earlier chassis-left arrangement so HOME matches the
# AES compact page.) The label's leftward reach sets the minimum spacing: a name label sits ~122px
# left of its own body-centre, so the centres must be >167px apart, and the left battery's centre
# cannot drop below ~556 without its label leaving object 902's box (x 435-790). 556/732 is that
# widest-centred pair: House-label left edge 437, Chassis body right edge 777. Verified against a
# render (House 556, Chassis 732, both on the stock y-row, each label clear of the other body).
_CHASSIS_LABEL_DX = 178          # stock label dx is 90; +178 (dx+88) shifts the chassis body's
                                 # centre from stock's x=644 to x=732 (the right-hand slot)
_HOUSE_CENTER_X_OFFSET = -870    # dx_house_label = target_center_x + this; target_center_x is
                                 # the clone (House) body's CENTRE (e.g. x=556 -> House centre 556)
_HOUSE_ROW_Y_BASELINE = 374      # dy_house_label = target_row_y0 - this (chassis's own label y0)


def _reposition_label(data: bytes, rec, *, dx: int | None = None, dy: int | None = None,
                      text: str | None = None, op_id: str | None = None) -> bytes:
    """A same-shape copy of label record `rec`'s raw bytes with its x/y flow-delta and/or its
    text replaced. `records.geom_offset` is round-trip guarded (re-reads and checks against
    `rec.geom`), so a stale `rec` cannot silently write the wrong offset. The label field is
    the record's last field (nothing trails its NUL on this donor), so a text swap that changes
    length is a plain bytearray slice-assign -- no other bytes in the record move.

    A text swap first validates the on-disk framing, the same check `labels.grow_label` makes
    before its own slice: the byte at `label_offset - 1` must equal `len(rec.label)` and the
    byte at `label_offset + len(rec.label)` must be NUL. `records.decode_record`'s R37 label
    loop (`records.py`'s final decode block) can concatenate MULTIPLE length-prefixed strings
    into one `rec.label` (a multi-line caption's count field, R37 `cnt` > 1 -- e.g. "Water\\n
    Pump" is stored as two length-prefixed parts, not one) -- there `label_offset` names only
    the FIRST part's text, so the byte before it holds that part's own (shorter) length, not
    `len(rec.label)`: this check fails and refuses rather than slicing the wrong span. Raises
    BAD_LABEL_FRAMING (a RecipeError, `op_id` if given) rather than an assert, matching this
    op's refuse-rather-than-misposition contract for a build whose donor label isn't this
    single length-prefixed-string shape."""
    buf = bytearray(data[rec.offset:rec.offset + rec.size])
    if dx is not None:
        off = records.geom_offset(data, rec, 0) - rec.offset
        struct.pack_into("<H", buf, off, dx & 0xFFFF)
    if dy is not None:
        off = records.geom_offset(data, rec, 1) - rec.offset
        struct.pack_into("<H", buf, off, dy & 0xFFFF)
    if text is not None:
        local_label = rec.label_offset - rec.offset
        old_len = len(rec.label)
        len_off = rec.label_offset - 1
        if not (data[len_off] == old_len and data[rec.label_offset + old_len] == 0x00):
            raise RecipeError(
                "BAD_LABEL_FRAMING",
                "label %r at %#x is not a single NUL-terminated length-prefixed field "
                "(length byte %#x holds %d, expected %d, or no NUL at %#x) -- a multi-part "
                "label (R37 cnt > 1) cannot be retexted by this same-shape slice"
                % (rec.label, rec.label_offset, len_off, data[len_off], old_len,
                   rec.label_offset + old_len), op_id=op_id)
        buf[local_label - 1:local_label + old_len + 1] = (
            bytes([len(text)]) + text.encode("ascii") + b"\x00")
    return bytes(buf)


def _reposition_pair(data: bytes, ctx, parent: int, clone_idx: int, target_x: int,
                     target_y: int, op_id: str) -> bytes:
    """Rebuild `parent`'s (object 902) record stream: shift the chassis (object 440) battery
    left by moving ITS label (`_CHASSIS_LABEL_DX`, see the module-level derivation above), and
    insert a new 'House' label -- cloned from the chassis's own label record, since donor 443
    carries no name of its own (`AddVoltageBattery`'s class docstring) -- positioned so the
    clone battery centres at `(target_x, target_y)` -ish: `target_x` is the clone body's target
    CENTRE x, `target_y` the target row (matched to the chassis label's own y0, 334 on 13.6).

    Raises NO_SUCH_RECORD if `parent` does not carry the expected container/chassis-label/
    chassis-attach/clone-attach shape (e.g. a build without this exact panel).
    """
    gs, tab = _gs_tab(data)
    recs = records.object_records(data, gs + tab[parent])
    container_rec = next((r for r in recs if r.wtype == 33 and r.child_index is None), None)
    chassis_rec = next((r for r in recs if r.wtype == 11
                        and (r.label or "").strip() == "Chassis"), None)
    attach440 = next((r for r in recs if r.wtype == 5 and r.child_index == 440), None)
    attach_clone = next((r for r in recs if r.wtype == 5 and r.child_index == clone_idx), None)
    if not (container_rec and chassis_rec and attach440 and attach_clone):
        raise RecipeError(
            "NO_SUCH_RECORD",
            "object %d does not carry the expected container/'Chassis' label/attach-440/"
            "attach-clone shape" % parent, op_id=op_id)

    new_chassis = _reposition_label(data, chassis_rec, dx=_CHASSIS_LABEL_DX, op_id=op_id)
    new_house = _reposition_label(
        data, chassis_rec, dx=target_x + _HOUSE_CENTER_X_OFFSET,
        dy=target_y - _HOUSE_ROW_Y_BASELINE, text="House", op_id=op_id)

    stream = [
        data[container_rec.offset:container_rec.offset + container_rec.size],
        new_chassis,
        data[attach440.offset:attach440.offset + attach440.size],
        new_house,
        data[attach_clone.offset:attach_clone.offset + attach_clone.size],
    ]
    new_body = bytes([len(stream)]) + b"".join(stream)
    try:
        out, edits = relocate.replace_object_records(data, parent, new_body)
    except relocate.RelocationError as e:
        raise RecipeError("REPOSITION_REFUSED", str(e), op_id=op_id)
    ctx.edits.extend(edits)
    return out


@op("add_voltage_battery")
class AddVoltageBattery(Op):
    """Clone a stock battery graphic beside another and drive it from a bus voltage.

    Adds the coach 48V pack to HOME's object-902 power panel next to the chassis 12V
    battery: clone donor 443 (the house battery: icon + value + 'V' + mode-7 body --
    NOT 'House': that name lives on donor 443's *parent* objects 901/903, not on 443
    itself), attach it to object 902, ISOLATE the clone's three status cells --
    colour, gauge level and value -- onto fresh SRAM so the per-tick routine below can
    drive this battery alone (leaving donor 443 and chassis object 440 untouched),
    then reposition the pair and add the 'House' label object 902 itself was missing.

    The attach is UNPOSITIONED (`insert.insert_object(..., position=None)`): object
    902's only wtype-5 container record (child 440) carries x geometry but no y, so
    `insert.insert_object` refuses a positioned attach against it (`_container_template`
    requires both geom[0] and geom[1] when `position` is given). The unpositioned
    template pins the clone at 902's flow origin; `_reposition_pair` (below) moves it
    to `(x, y)` afterwards -- not by editing the clone's own records, but by editing
    the LABEL that precedes its attach (see that function's docstring for why: a
    container's subtree recursion leaves the flow origin wherever the child's own
    last drawn element left it, so the attach's own delta alone cannot be aimed
    without knowing that leftover state, while the preceding label moves the whole
    pair as one unit).

    **The three cells.** The donor's body is a wt-33 (`0x21`, "capsule") record whose
    `fillA` is mode 7: index 206 is the colour word GUICODE Finding 18's chrome-colour
    dig documents (`0x44BC8[idx]` -> an SRAM cell read as a raw 32-bit word, no variable-
    type dispatch involved), and the record's own trailing 2 bytes -- past what
    `records.decode_record` exposes -- carry a SECOND `+0x44BC8` index (207 on the
    donor): the gauge's partial-fill "empty offset", written by the stock per-tick block
    `FUN_00247BF8` for the two ORIGINAL batteries only (`api-notes.md`'s 48V-source
    section). The object's wt-8 sibling names a third index (209) as its `value_slot` --
    the volts the body's colour/level both derive from. All three are repointed on the
    CLONE only: `records.color_offset`/`value_slot_offset` (round-trip guarded) for the
    first and third, a raw same-size write at `rec.offset + rec.size - 2` for the second
    (there is no decoder-exposed offset for it -- see `_gauge_level_indices`).

    **Picking the fresh indices.** `statusbind.spare_cell_indices` tracks only decoded
    `colors`/`value_slot` references, so it alone would happily offer back an index some
    OTHER object's undecoded gauge-level tail field still uses (empirically, 12 of 13.6's
    16 "spare" indices are exactly this trap, including 203 and 207 themselves).
    `_gauge_level_indices` closes that gap with its own whole-corpus scan; this op takes
    its three indices from `spare_cell_indices(data) - _gauge_level_indices(data)`.

    **Fresh cells, not existing "spare" addresses.** `_isolate_cell` allocates via
    `sramreach.free_runs` (a fresh, 4-aligned, wholly-unreferenced SRAM word) rather than
    `followchannel.scan_display_cell`'s existing-pointer discipline -- the latter found no
    safe candidate among the tiny remaining pool on 13.6 (its own GuiCode-reference scan
    rejected the rest). Every allocation is tier-3-shaped: unprecedented on hardware. The
    coach settled it: the op was flashed and
    verified 2026-09-12 -- the House 48V battery's number, fill and colour all update live
    on both HOME and the compact ELECTRICAL page -- so this op is now
    `hardware_verified = True`. The grow, the fresh cells, the
    per-tick routine, the container watch-cell retarget and the donor 48V rescale are all
    proven on the physical panel.

    Params:
      page: int (default 4) — recorded for context; not used to resolve the parent
      parent: int (default 902) — object index the clone is attached to
             (`insert.insert_object`)
      donor: int (default 443) — object index to clone (`insert.clone_object`)
      x: int — target CENTRE x for the clone's battery body (`_reposition_pair`); the
         chassis's own shift is a fixed constant (`_CHASSIS_LABEL_DX`), not a param --
         moving it independently would require recalibrating `x`'s formula, since the
         second pair's flow origin is measured off the first pair's own trailing
         element (see `_reposition_pair`'s docstring)
      y: int — target row y0 for the clone's battery, matched against the chassis
         label's own y0 (334 on 13.6) so the pair shares a row

    Error codes:
      INSERT_REFUSED — insert.insert_object refused the attach (bad parent, no
                       wtype-5 container template on `parent`, etc.)
      NO_FIELD — the clone carries no wt-33 fillA-mode7 body record, or no wt-8
                 value widget, to isolate
      NO_DISPLAY_CELL — fewer than 3 `+0x44BC8` indices are both widget-spare and
                        gauge-level-free, or `sramreach.free_runs` found no fresh SRAM
      UNEXPECTED_APPEND_POINT — the GuiCode append point is below stock's 0x00259BB4
                        or misaligned (`_check_append_point`)
      ROUTINE_REFUSED — `voltagebattery.build_routine` refused (a bad constant or a
                        mis-aligned code load address)
      GROW_REFUSED — `growsection.grow_section` refused the GuiCode append
      DETOUR_REFUSED — the bytes at the per-tick hook were not the stock/chained `bl`
                        `followchannel.hook_state` expected (a grow shifted something)
      NO_SUCH_RECORD — `parent` does not carry the expected container / 'Chassis' label
                        / attach-440 / attach-clone record shape (`_reposition_pair`)
      BAD_LABEL_FRAMING — the 'Chassis' label's on-disk framing is not the single
                        length-prefixed, NUL-terminated string shape this op's retext
                        assumes (`_reposition_label`) -- e.g. a multi-part label (R37
                        `cnt` > 1) on a build whose donor differs from 13.6's
      REPOSITION_REFUSED — `relocate.replace_object_records` refused the rebuilt
                        object-902 record stream (a structural gate failed)

    Registers ctx.handles[op_id] = {"object": idx, "value_cell": addr, "color_cell":
    addr, "level_cell": addr} where idx is the new object's top-level index (the
    object-table length before the insert) and the three `*_cell` values are the fresh
    SRAM addresses a later routine writes.
    """
    hardware_verified = True      # coach-verified 2026-09-12

    PARAMS = {
        "properties": {
            "page": {"type": "integer", "default": 4,
                     "description": "page context (not used to resolve the parent)"},
            "parent": {"type": "integer", "default": 902,
                       "description": "object index the clone is attached to"},
            "donor": {"type": "integer", "default": 443,
                      "description": "object index to clone"},
            "x": {"type": "integer",
                  "description": "target centre x for the clone battery body"},
            "y": {"type": "integer",
                  "description": "target row y0 for the clone battery (matched to the "
                                 "chassis label's own y0)"},
            "live_repaint": {"type": "boolean", "default": True,
                  "description": "retarget the clone's enrolled wt5 container (on the parent) "
                                 "to watch the live coach-48V cell 209 instead of the chassis "
                                 "cell 205 it inherited from the template, so the panel "
                                 "repaints the WHOLE House battery (number AND fill) live -- "
                                 "the mechanism the stock chassis uses"},
            "rescale_donor": {"type": "boolean", "default": False,
                  "description": "repoint donor 443's fill colour/level onto the clone's "
                                 "fresh 48V cells, so the ELECTRICAL coach battery stops "
                                 "showing always-100%-green (its stock cells are driven on a "
                                 "12V scale a ~53V pack saturates); 443's own enrolled "
                                 "containers already watch 209, so the fill then goes live too"},
        },
        "required": ["x", "y"],
    }

    def validate(self):
        self.page = self._param("page", 4, kind=int)
        self.parent = self._param("parent", 902, kind=int)
        self.donor = self._param("donor", 443, kind=int)
        self.x = self._param("x", kind=int)
        self.y = self._param("y", kind=int)
        self.live_repaint = self._param("live_repaint", True, kind=bool)
        self.rescale_donor = self._param("rescale_donor", False, kind=bool)

    def apply(self, data, ctx):
        clone = insert.clone_object(data, self.donor)
        idx = len(guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX])
        try:
            data, e = insert.insert_object(data, clone, self.parent, position=None)
        except insert.InsertError as err:
            raise RecipeError("INSERT_REFUSED", str(err))
        ctx.edits.extend(e)
        data, _ = patch.reseal(data)

        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])
        fillrec = next((r for r in recs if r.wtype == 33 and r.colors
                        and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3), None)
        if fillrec is None:
            raise RecipeError(
                "NO_FIELD", "clone (object %d) has no wt-33 fillA-mode7 body record" % idx,
                op_id=self.spec.id)
        valuerec = next((r for r in recs if r.wtype == 8 and r.value_slot is not None), None)
        if valuerec is None:
            raise RecipeError(
                "NO_FIELD", "clone (object %d) has no wt-8 value widget" % idx,
                op_id=self.spec.id)

        color_off = records.color_offset(data, fillrec, "fillA")
        level_off = fillrec.offset + fillrec.size - 2

        # TWO private cells the per-tick routine drives -- the CUSTOM colour and gauge level, which
        # have no stock equivalent (green->red at 50.4V, fill over 48.0-54.8V): a mode-7 colour word
        # (u32; no variable-type dispatch reads a mode-7 fill -- GUICODE Finding 18, a raw SRAM word
        # -- so vtype None) and a 1-byte gauge level. On 13.6 every reusable +0x44BC8 index is read
        # by an object in another GuiData object table (the background-bar aliasing), so a private
        # cell must be a freshly GROWN index (`readout.allocate_fresh_cells`).
        #
        # The VALUE (the voltage number) is NOT isolated: the clone keeps its donor value_slot 209,
        # the live, receive-filter-fed decoded coach-48V cell the ELECTRICAL page reads. A fresh
        # cell is not on the panel's live-repaint watch-list (only DGN-fed cells are), so a routine-
        # driven fresh value cell shows a STALE number (coach test 2026-09-12); reading 209 directly
        # makes it update live and match ELECTRICAL exactly. The grow appends at GuiData's end, so
        # `color_off`/`level_off` (both before that point) stay byte-stable.
        try:
            data, cells, cell_edits = readout.allocate_fresh_cells(
                data, [(4, None), (1, _LEVEL_VAR_TYPE)])
        except readout.ReadoutError as e:
            raise RecipeError("NO_DISPLAY_CELL", str(e), op_id=self.spec.id)
        ctx.edits.extend(cell_edits)
        color_idx, level_idx = (c.slot for c in cells)
        color_cell, level_cell = (c.addr for c in cells)
        ctx.authored_cells.update(c.slot for c in cells)   # gated: must be freshly grown

        buf = bytearray(data)
        _put16(buf, color_off, color_idx, ctx,
              "battery clone %d fillA -> cell %d (%#x)" % (idx, color_idx, color_cell))
        _put16(buf, level_off, level_idx, ctx,
              "battery clone %d level -> cell %d (%#x)" % (idx, level_idx, level_cell))
        # value widget keeps donor value_slot 209 (live receive-fed coach-48V cell) -- not repointed

        # (The clone's live-repaint retarget happens after `_reposition_pair` rebuilds the parent
        # object's records -- see the `live_repaint` block below.)

        # Rescale the donor battery in place (the donor object itself, wherever it is drawn --
        # on the reference coach that is the ELECTRICAL coach battery, donor 443): repoint its
        # fill colour/level onto the clone's fresh 48V cells. This is donor-relative -- it reads
        # `self.donor`'s own fillA record and rewrites its two cell indices; no page or container
        # object index is hardcoded here.
        #
        # Why it works, and why no extra enrol is needed: the donor's stock fill cells are driven
        # by FUN_00247BF8 on the stock (12V) scale, which a 48V pack saturates (~53V over a 12V
        # window -> always full/green). Whatever enrolled wt5 containers already draw the donor and
        # watch its voltage cell repaint the WHOLE donor child on every change, so once its fill
        # reads the 48V-scaled cell the donor goes live and correct on its own. The stock scale
        # writes to the donor's old cells become dead. (Reference-coach instance: donor 443's
        # containers are objects 901/903/829/830, child 443, already watching cell 209; its stock
        # 12V cells are 206/207.) The clone was cloned off the donor, so these edits touch only the
        # donor's own placement, not the clone.
        if self.rescale_donor:
            drecs = records.object_records(bytes(buf), gs + tab[self.donor])
            dfill = next((r for r in drecs if r.wtype == 33 and r.colors
                          and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3), None)
            if dfill is None:
                raise RecipeError(
                    "NO_FIELD", "donor %d has no fillA-mode7 body record to rescale" % self.donor,
                    op_id=self.spec.id)
            dcolor_off = records.color_offset(bytes(buf), dfill, "fillA")
            _put16(buf, dcolor_off, color_idx, ctx,
                  "donor %d fillA -> clone cell %d (48V)" % (self.donor, color_idx))
            _put16(buf, dfill.offset + dfill.size - 2, level_idx, ctx,
                  "donor %d level -> clone cell %d (48V)" % (self.donor, level_idx))

        out, _ = patch.reseal(bytes(buf))

        # The per-tick routine: read the 48V slot, drive the clone's two fresh cells (colour +
        # level; NOT the value, which reads the live cell 209 directly), and tail-chain to the
        # previous chain head -- appended to GuiCode and detoured onto the per-tick hook exactly the
        # way AddRegisterReadout appends its copy routine. The cells are fresh (nothing stock names
        # them), so no config-byte silencing is needed: the chain runs before FUN_00247BF8 and that
        # block never touches these addresses.
        slot_addr = self._resolve_48v_slot(out)
        hook_old, tail = followchannel.hook_state(out)
        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob = voltagebattery.build_routine(
                slot_addr=slot_addr, color_cell=color_cell,
                level_cell=level_cell, entry_addr=code_load_addr, tail_target=tail)
        except voltagebattery.VoltageBatteryError as e:
            raise RecipeError("ROUTINE_REFUSED", str(e), op_id=self.spec.id)
        try:
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "add_voltage_battery: %d-byte per-tick routine (48V slot %#x -> colour %#x / "
            "level %#x; value reads live cell 209)" % (len(blob), slot_addr, color_cell, level_cell)))
        hook_at = followchannel.HOOK_OFF
        hook_runtime = theme.GUICODE_BASE_DEFAULT + hook_at
        hook_new = thumb.bl(hook_runtime, code_load_addr + voltagebattery.ENTRY_OFF)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, pre.hex(), hook_old.hex()), op_id=self.spec.id)
        gbuf = bytearray(grown)
        gbuf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(
            file_off, pre, hook_new, "GuiCode",
            "add_voltage_battery: detour per-tick hook -> %#x"
            % (code_load_addr + voltagebattery.ENTRY_OFF)))
        data, _ = patch.reseal(bytes(gbuf))

        # Task 5: reposition the pair (chassis left, clone/'House' right, both centred in the
        # lower-right quadrant) and add the 'House' text donor 443 doesn't itself carry. See
        # `_reposition_pair`'s docstring for the flow-layout mechanism this relies on.
        data = _reposition_pair(data, ctx, self.parent, idx, self.x, self.y, self.spec.id)

        # Live repaint: the clone's container record on the parent (the wt5 attach `insert_object`
        # created) was cloned from the parent's CHASSIS template, so it is already enrolled but
        # watches the chassis cell 205. Retarget it to the clone's own live coach-48V cell (209) so
        # the panel's per-widget dispatch repaints the WHOLE House child -- number AND fill -- on
        # every coach-voltage change, exactly as the stock chassis rides object 902 rec2 (vslot 205,
        # child 440). Size-neutral: only the R42 value-slot u16 changes. This is done here, after
        # `_reposition_pair` has rebuilt the parent's records, so the edit is not overwritten.
        if self.live_repaint:
            live_cell = valuerec.value_slot          # 209, the donor's live receive-fed cell
            gs2, tab2 = _gs_tab(data)
            precs = records.object_records(data, gs2 + tab2[self.parent])
            crec = next((r for r in precs if r.wtype == 5 and r.child_index == idx), None)
            if crec is None or crec.value_slot is None:
                raise RecipeError(
                    "NO_SUCH_RECORD", "parent %d has no enrolled wt5 container for clone %d to "
                    "retarget for live repaint" % (self.parent, idx), op_id=self.spec.id)
            vs_off = records.value_slot_offset(data, crec)
            buf2 = bytearray(data)
            _put16(buf2, vs_off, live_cell, ctx,
                  "House container (parent %d, child %d) watch cell %d -> %d (live repaint)"
                  % (self.parent, idx, crec.value_slot, live_cell))
            data, _ = patch.reseal(bytes(buf2))

        ctx.handles[self.spec.id] = {
            "object": idx, "color_cell": color_cell,
            "level_cell": level_cell, "color_idx": color_idx, "level_idx": level_idx,
            "slot_addr": slot_addr,
            "entry": code_load_addr + voltagebattery.ENTRY_OFF, "hook_at": hook_at,
            "code_load_addr": code_load_addr, "tail_target": tail}
        return data

    def _resolve_48v_slot(self, data):
        """Instance-1 48V pack voltage (u16 raw x0.05 V). Resolve it from the register table so
        the address is checked against the running image; fall back to the api-notes-verified
        literal 0x2000809A if the register cannot be resolved (e.g. an off-13.6 build)."""
        try:
            return register.resolve_register(data, dgn=0x1FFFD, instance=1,
                                             var_index=105).slot_addr
        except register.RegisterError:
            return 0x2000809A

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        idx = h["object"]
        gs, tab = _gs_tab(data)
        assert idx < len(tab), "handle object %d out of range" % idx
        # `add_batt_heat_battery_glyph` may have wrapped the battery in a state switch; its
        # records then live in the switch's arms, which `battery_records` reads through.
        recs = battglyph.battery_records(data, idx)
        assert recs, "clone (object %d) has no records" % idx

        fillrec = next(r for r in recs if r.wtype == 33 and r.colors
                       and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
        valuerec = next(r for r in recs if r.wtype == 8 and r.value_slot is not None)
        level_off = fillrec.offset + fillrec.size - 2
        level_idx = int.from_bytes(data[level_off:level_off + 2], "little")

        # The colour/level cells are freshly-grown indices in the RELOCATED cell table, so resolve
        # them at the table's CURRENT base (`resolve_cell_tables` follows the relocation), not
        # `status_map.cell_table`'s structural scan (which finds the pre-grow copy, no entry >= 665).
        cell_base, _type_base, _old_len = readout.resolve_cell_tables(data)
        cell_off = gs + (cell_base - 0x90000000)

        def _cell_addr(index):
            return int.from_bytes(data[cell_off + index * 4:cell_off + index * 4 + 4], "little")

        for label, idx_field, want_addr in (
                ("colour", fillrec.colors[0][2], h["color_cell"]),
                ("level", level_idx, h["level_cell"])):
            assert _cell_addr(idx_field) == want_addr, \
                "clone %s cell: index %d -> %#x, handle says %#x" % (
                    label, idx_field, _cell_addr(idx_field), want_addr)

        # The value widget keeps the donor's live receive-fed voltage cell (209), NOT a fresh one --
        # so the number repaints live and matches the ELECTRICAL page (coach test 2026-09-12).
        assert valuerec.value_slot == 209, \
            "clone value widget value_slot %d != donor's live coach-48V cell 209" % valuerec.value_slot

        # Live repaint (the coach fix): the clone's enrolled wt5 container on the parent must WATCH
        # the live coach cell 209 (retargeted off the inherited chassis cell 205), so the panel
        # repaints the whole House child -- number and fill -- on every voltage change.
        if self.live_repaint:
            precs = records.object_records(data, gs + tab[self.parent])
            crec = next(r for r in precs if r.wtype == 5 and r.child_index == idx)
            assert crec.header[4] & _ENROLL_BIT, \
                "House container not enrolled: header[4]=0x%02x" % crec.header[4]
            assert crec.value_slot == valuerec.value_slot, \
                "House container watches cell %s, not the clone's live cell %s" % (
                    crec.value_slot, valuerec.value_slot)

        # Donor rescale: the ELECTRICAL coach battery (object 443) must read the clone's fresh
        # 48V cells (not its stock 12V-driven 206/207). 443's own enrolled containers already watch
        # 209, so the fill then repaints live -- assert one such container exists, so a build where
        # that structure changed does not silently ship a static ELECTRICAL fill.
        if self.rescale_donor:
            drecs = battglyph.battery_records(data, self.donor)
            dfill = next(r for r in drecs if r.wtype == 33 and r.colors
                         and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
            dlevel = int.from_bytes(data[dfill.offset + dfill.size - 2:dfill.offset + dfill.size],
                                    "little")
            assert dfill.colors[0][2] == h["color_idx"], \
                "donor %d fill colour cell %d != clone's fresh %d" % (
                    self.donor, dfill.colors[0][2], h["color_idx"])
            assert dlevel == h["level_idx"], \
                "donor %d level cell %d != clone's fresh %d" % (self.donor, dlevel, h["level_idx"])
            watchers = [oi for oi in range(len(tab))
                        for r in records.object_records(data, gs + tab[oi])
                        if r.wtype == 5 and r.child_index == self.donor
                        and (r.header[4] & _ENROLL_BIT) and r.value_slot == 209]
            assert watchers, \
                "no enrolled wt5 container watches donor %d on cell 209; its rescaled fill would " \
                "not repaint live" % self.donor

        addrs = {h["color_cell"], h["level_cell"]}
        assert len(addrs) == 2, "clone cells are not pairwise distinct: %s" % h

        parent_recs = records.object_records(data, gs + tab[self.parent])
        assert any(r.wtype == 5 and r.child_index == idx for r in parent_recs), \
            "object %d has no attach record for clone %d" % (self.parent, idx)
        assert any(r.wtype == 11 and (r.label or "").strip() == "House"
                  for r in parent_recs), \
            "object %d lost its 'House' label" % self.parent
        assert any(r.wtype == 11 and (r.label or "").strip() == "Chassis"
                  for r in parent_recs), \
            "object %d lost its 'Chassis' label" % self.parent

        return ("battery clone: object %d (donor %d) attached to %d, positioned at "
                "target (%d, %d) with a new 'House' label; "
                "cells colour=%#x level=%#x; value reads live cell 209" % (
                    idx, self.donor, self.parent, self.x, self.y,
                    h["color_cell"], h["level_cell"]))


@op("install_glyph")
class InstallGlyph(Op):
    """Author a brand-new glyph (icon or character) into an existing 4bpp icon/value
    font and point one character's stub entry at it -- the mechanism behind the
    Starlink logo (GUICODE Finding 44) and the y-bearing convention documented in
    `CONTROL-BEHAVIOR.md` "Where a glyph lands vertically". `rows` carries the pixels
    as *data* -- one hex digit (4bpp coverage, 0..f) per pixel, one hex string per row,
    all rows equal length -- so compiling a project needs no rasteriser at all; a
    PNG-import GUI step (deferred to v1.x) can build this same `rows` list later
    without this op changing.

    Params:
      slot: int — icon/value font slot (`glyphs.font_for_slot`); must be a 4bpp font
      char: str (len 1) — the character whose stub entry gets repointed at the new glyph
      y: int — the glyph's y-bearing (glyph header byte `+0xc`). Must equal the
         **convention** `(cell_height - glyph_height) // 2` -- the invariant every
         shipped icon font in this corpus follows -- unless `force_y` is set, because a
         bearing off that formula is exactly the v20 mistake: a logo authored with
         `y=0` in a 128-cell font drew 33 px above its button. `force_y` exists for the
         legitimate exception -- a v-align-1 record, where the bearing is a plain
         offset from the box top, not a centring term.
      rows: list[str] — pixel rows, one hex digit (0-f) per pixel, all rows equal length
      force_y: bool (default False) — skip the bearing-convention check
      x: int (default 0) — the glyph's x bearing (record `+0x0a`)
      profiles: list[int] (optional) — the 10 ink-profile bytes (5 left, then 5 right) the
         measurer kerns against. Supply them to reproduce a glyph EXACTLY, which is what
         `glyphenc.export_glyph` emits; omit them when authoring new artwork, where
         `glyphenc._profiles` approximates. They cannot be derived from the pixels -- the rule
         lives in Firefly's font tool, not in these files (`GUICODE.md` Finding 46) -- so an
         exported glyph that dropped them would come back pixel-identical and kern differently.
      flag: int (default 0) — record byte `+0x0e` bit 0, the measurer's endpoint-bearing flag.
         The blitter skips this bit, so it is invisible to a pixel round-trip and still changes
         `metrics.text_width`

    Error codes:
      BAD_PARAMS — char is not exactly one character, rows is empty, rows have unequal
                   lengths, or a row holds a non-hex-digit character
      BEARING_OFF_CONVENTION — y does not centre the glyph in slot's font cell and
                               force_y was not given; offers=["use_convention", "force"]
      BAD_FONT — slot's font is not 4bpp (install_glyph only encodes 4bpp coverage)
      GLYPH_OCCUPIED — char's stub entry already decodes as a real (non-placeholder)
                       glyph -- this op only claims a character that currently falls
                       back to the shared placeholder block
      GLYPH_STUB_SHARED — char's stub entry is shared by more than just char (the
                          placeholder-sharing set is bigger than [char]), so repointing
                          it would repoint every character sharing it
      RELOCATE_REFUSED — `relocate.relocate` refused the insertion. Mapped here the
                         same way `RenameLabel` maps `GROW_REFUSED`: a relocating
                         primitive's own `RelocationError` must not escape
                         `compile_project`'s `except RecipeError` filter as a bare,
                         unstructured exception.

    This is a relocating op. The encoded glyph record is inserted at
    `(guidata_extent()[1] - guidata_extent()[0]) - 4`: GuiData's last 4 bytes are its
    own stored checksum (`guidata_extent` includes them in the section span), so
    inserting at the section's very end would put the new record's tail under the
    checksum word -- the documented trap (`CLAUDE.md` "Traps"). `-4` lands the
    insertion just before that word instead.
    """
    PARAMS = {
        "properties": {
            "slot": {"type": "integer", "description": "icon/value font slot; must be a 4bpp font"},
            "char": {"type": "string", "description": "the character whose stub entry gets repointed"},
            "y": {"type": "integer",
                  "description": "glyph y-bearing; must equal (cell_height - glyph_height) // 2 unless force_y"},
            "rows": {"type": "array", "items": {"type": "string"},
                     "description": "pixel rows, one hex digit (0-f) per pixel, all rows equal length"},
            "force_y": {"type": "boolean", "default": False,
                       "description": "skip the bearing-convention check (for v-align-1 records)"},
            "x": {"type": "integer", "default": 0, "description": "the glyph's x bearing"},
            "profiles": {"type": "array", "items": {"type": "integer"},
                         "description": "the 10 ink-profile (kerning) bytes, 5 left then 5 "
                                        "right; omit when authoring new artwork"},
            "flag": {"type": "integer", "default": 0,
                     "description": "record byte +0x0e bit 0, the measurer's bearing flag"},
            "claim_block": {"type": "boolean", "default": False,
                            "description": "also claim a char whose glyph is its font's solid "
                                           "placeholder block (icon font 24's unused slots), "
                                           "provided no record draws that char in that font"},
        },
        "required": ["slot", "char", "y", "rows"],
    }

    def validate(self):
        self.slot = self._param("slot", kind=int)
        self.char = self._param("char", kind=str)
        self.y = self._param("y", kind=int)
        self.force_y = self._param("force_y", False, kind=bool)
        self.x = self._param("x", 0, kind=int)
        self.flag = self._param("flag", 0, kind=int)
        self.claim_block = self._param("claim_block", False, kind=bool)
        # `profiles` reaches `encode_glyph`, which raises ValueError on a bad shape -- and a bare
        # ValueError would escape `compile_project`'s `except RecipeError` filter as an
        # unstructured crash, exactly the way RELOCATE_REFUSED exists to stop RelocationError
        # doing. So the shape is checked here, where it becomes a BAD_PARAMS the UI can show.
        self.profiles = self.spec.params.get("profiles")
        if self.profiles is not None:
            self.profiles = list(self.profiles)
            if len(self.profiles) != 10 or any(not isinstance(v, int) or not (0 <= v <= 255)
                                               for v in self.profiles):
                raise RecipeError("BAD_PARAMS", "profiles must be 10 bytes in 0..255 "
                                  "(5 left, then 5 right)", op_id=self.spec.id)
        rows = self._param("rows", kind=list)
        if len(self.char) != 1 or not rows or len({len(r) for r in rows}) != 1:
            raise RecipeError("BAD_PARAMS", "install_glyph wants one char and "
                              "equal-length hex rows", op_id=self.spec.id)
        try:
            self.rows = [tuple(int(c, 16) for c in row) for row in rows]
        except ValueError:
            raise RecipeError("BAD_PARAMS", "rows must be hex digits (one per pixel)",
                              op_id=self.spec.id)
        self.width, self.height = len(self.rows[0]), len(self.rows)

    def apply(self, data, ctx):
        # Font/occupancy/sharing are checked before the bearing convention: whether
        # this character can be claimed AT ALL is the more fundamental question, and
        # `test_install_glyph_refuses_occupied_char` deliberately supplies an
        # off-convention y with an already-occupied char to pin that ordering --
        # GLYPH_OCCUPIED must win, not be masked by an unrelated style refusal.
        font = glyphs.font_for_slot(data, self.slot)
        bpp = glyphs.font_bpp(data, font)
        if bpp != 4:
            raise RecipeError("BAD_FONT", "slot %d is %d bpp; install_glyph supports "
                              "4 bpp icon fonts" % (self.slot, bpp))
        gi = font.glyph_index(ord(self.char))
        gtab = guidata.object_tables(data)[3]
        stub = gtab[gi]
        sharers = [c for c in range(font.first, font.last + 1)
                   if gtab[font.glyph_index(c)] == stub]
        drawn = self._drawn_chars(data) if self.claim_block else set()
        block = self.claim_block and self._unused_block(data, font, gi, drawn)
        try:
            glyphs.decode_glyph(data, gi)
            if not block:
                raise RecipeError("GLYPH_OCCUPIED", "slot %d %r already holds a real glyph"
                                  % (self.slot, self.char))
        except glyphs.GlyphError:
            pass
        if sharers != [ord(self.char)] and not (
                block and all(self._unused_block(data, font, font.glyph_index(c), drawn)
                              for c in sharers)):
            raise RecipeError("GLYPH_STUB_SHARED", "the stub entry is shared by %s"
                              % ([chr(c) for c in sharers],))

        cell = metrics.font_metrics(data, self.slot).height
        convention = (cell - self.height) // 2
        if self.y != convention and not self.force_y:
            raise RecipeError(
                "BEARING_OFF_CONVENTION",
                "y-bearing %d does not centre a %d px glyph in slot %d's %d px cell "
                "(convention: %d -- an early build drew 33 px high for exactly this); "
                "pass force_y for a v-align-1 record" % (self.y, self.height,
                                                         self.slot, cell, convention),
                offers=["use_convention", "force"])
        record = glyphenc.encode_glyph(self.rows, 4, x=self.x, y=self.y,
                                       profiles=self.profiles, flag=self.flag)
        record += b"\x00" * (-len(record) % 4)     # object offsets are 4-aligned

        gs, ge = guidata.guidata_extent(data)
        csum = [f for f in patch.checksum_fields(data)
                if f.label.endswith("GuiData")][0]
        assert csum.position == ge - 4, "GuiData checksum is not the last word"
        new_off = (ge - gs) - 4      # before the trailing checksum word, never on it
        try:
            data, e = relocate.relocate(data, new_off, record)
        except relocate.RelocationError as err:
            raise RecipeError("RELOCATE_REFUSED", "relocate refused: %s" % err)
        ctx.edits.extend(e)
        gs2, _ = guidata.guidata_extent(data)
        entry = gs2 + guidata.object_table_offset(data, 3) + gi * 4
        old_word = struct.pack("<I", guidata.OBJECT_POINTER_BASE + stub)
        assert struct.unpack_from("<I", data, entry)[0] == \
            guidata.OBJECT_POINTER_BASE + stub, \
            "table entry at %#x does not hold the stub pointer" % entry
        buf = bytearray(data)
        new_word = struct.pack("<I", guidata.OBJECT_POINTER_BASE + new_off)
        struct.pack_into("<I", buf, entry, guidata.OBJECT_POINTER_BASE + new_off)
        ctx.edits.append(relocate.Edit(
            entry, old_word, new_word, "recipe",
            "glyph table entry slot %d %r -> +0x%05x" % (self.slot, self.char, new_off)))
        data, _ = patch.reseal(bytes(buf))
        ctx.handles[self.spec.id] = {"gi": gi}
        return data

    def _drawn_chars(self, data) -> set:
        """Every character some label in the image draws in this op's font slot."""
        gs, tab = _gs_tab(data)
        out = set()
        for obj in range(len(tab)):
            try:
                recs = records.object_records(data, gs + tab[obj])
            except Exception:                  # a table slot that is not an object record list
                continue
            for r in recs:
                if r.font == self.slot and r.label:
                    out.update(r.label)
        return out

    def _unused_block(self, data, font, gi, drawn: set) -> bool:
        """`claim_block`'s condition: glyph `gi` is a solid full-coverage block and no label in
        the image draws the character in this font. Icon font 24 marks its unused characters
        this way (eight share one 48x48 block record) rather than falling back to a stub, so
        GLYPH_OCCUPIED would otherwise refuse every free slot it has. Only the character's own
        table entry is repointed, so the other characters sharing the block keep it."""
        try:
            g = glyphs.decode_glyph(data, gi)
        except glyphs.GlyphError:
            return False
        if not all(v == 0xF for row in g.rows for v in row):
            return False
        return chr(font.first + gi - font.base) not in drawn

    def verify(self, data, ctx):
        # The glyph pixel read-back: the gate that caught the v13/v14/v15 silent
        # bottom-row corruption. Never skip it.
        g = glyphs.decode_glyph(data, ctx.handles[self.spec.id]["gi"])
        assert (g.width, g.height) == (self.width, self.height), \
            "glyph decoded %dx%d, wanted %dx%d" % (g.width, g.height,
                                                   self.width, self.height)
        assert g.rows == tuple(self.rows), "glyph pixels corrupted"
        assert g.y == self.y, "glyph y-bearing is %d, wanted %d" % (g.y, self.y)
        return "glyph: slot %d %r %dx%d y=%d, pixel read-back OK" % (
            self.slot, self.char, self.width, self.height, self.y)


@op("set_gesture")
class SetGesture(Op):
    """Change one control group's interaction model. v1 supports exactly one
    direction: converting a toggle group to hold-to-toggle
    (`gestures.make_hold_to_toggle`). A single-entry command-5 toggle is
    hardware-confirmed (2026-08-19, the pin-29 Starlink button). A variant-selected
    multi-binding group (13.6's group 129, Low Volts: a selector write that the
    group's external variant setter points at 0 or 1, plus its read-back) gets the
    same edit on every live copy; COACH-VERIFIED 2026-10-19 on group 129 from the
    compact AES ELECTRICAL page (owner: the 3 s hold works). AGS mode is untested.
    Converting the other way (`kind: "latch"`) is not implemented
    for anything that is not already a latch -- it verifies and no-ops on a latch,
    and refuses everything else rather than half-editing it.

    Params:
      target: object index | {'ref': op_id} — resolved to a group via
              `newbutton.object_group` (the object's IMAGE_OPCODE record's trailing
              group reference). Ignored when `group` is given.
      group: int | None (default None) — address a group directly, bypassing
             `target`/`resolve_target` entirely. Needed for groups with no single
             owning object (e.g. the awning), and for a project that only wants to
             edit a stock group without wiring a new button first.
      kind: "hold_to_toggle" | "latch" — the target interaction model.
      seconds: int (default 3) — hold threshold in whole seconds for
               "hold_to_toggle" (`gestures.MAX_HOLD_SECONDS` caps it at 15; the
               nibble check happens inside `make_hold_to_toggle`). Ignored for
               "latch".

    Error codes:
      BAD_PARAMS — kind is not one of "latch"/"hold_to_toggle"
      NOT_SUPPORTED — kind="latch" was requested on a group whose current `.kind`
                     is not already "latch"; v1 has no latch-conversion primitive.
                     offers=["disable_gesture_op"] — the fix is to remove/disable
                     whichever earlier op made the group non-latch, not to fit a
                     new edit here.
      NO_SUCH_GROUP — the group could not be resolved at all: `group` (or the
                     group `target` resolves to via `newbutton.object_group`) is
                     out of range (`gestures.describe_group` raises a bare
                     `IndexError`), or `target` names an object with no
                     `IMAGE_OPCODE` record to read a group off of
                     (`newbutton.object_group` raises a bare `ValueError`). Both
                     are mapped here rather than left to crash `compile_project`
                     unstructured; a `RecipeError` raised underneath (e.g.
                     `resolve_target`'s own `BAD_REF`, which subclasses
                     `ValueError`) is never remapped — it is re-raised as-is.
      GESTURE_REFUSED — `gestures.make_hold_to_toggle` raised `GestureEditError`:
                        a single-entry group's binding is not a
                        `DC_DIMMER_COMMAND_2` command-5 toggle or carries the repeat
                        bit; a multi-binding group fails one of the five premises
                        (no repeat bit, one DGN/selector target, variants agreeing on
                        a release toggle, variants that write 0 and 1 or all
                        command-5 toggles, no variant cycling on release); the group
                        has no live binding; or `seconds` does not fit the
                        hold-threshold nibble.

    `gestures.make_hold_to_toggle` returns an unsealed image -- `gestures.py` never
    imports `patch` and performs no reseal of its own (checked, not assumed: grep
    confirms it, and the original build script resealed only once at the very end, well
    after its own `make_hold_to_toggle` call). This op reseals
    before returning, matching every other byte-editing op here.
    """
    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "group": {"type": ["integer", "null"], "default": None,
                      "description": "address a stock group directly (bypasses target)"},
            "kind": {"type": "string", "enum": ["latch", "hold_to_toggle"]},
            "seconds": {"type": "integer", "default": 3},
        },
        "required": ["target", "kind"],
    }

    def validate(self):
        self.target = self._param("target")
        self.group = self._param("group", None)
        self.kind = self._param("kind", kind=str)
        self.seconds = self._param("seconds", 3, kind=int)
        if self.kind not in ("latch", "hold_to_toggle"):
            raise RecipeError("BAD_PARAMS", "kind wants latch|hold_to_toggle",
                              op_id=self.spec.id)

    def _group(self, data, ctx):
        if self.group is not None:
            return self.group
        return newbutton.object_group(data, resolve_target(ctx, self.target))

    def apply(self, data, ctx):
        try:
            gid = self._group(data, ctx)
            beh = gestures.describe_group(data, gid)
        except RecipeError:
            raise
        except (ValueError, IndexError) as err:
            raise RecipeError(
                "NO_SUCH_GROUP",
                "could not resolve target %r / group %r to a gesture group: %s"
                % (self.target, self.group, err))
        if self.kind == "latch":
            if beh.kind != "latch":
                raise RecipeError(
                    "NOT_SUPPORTED",
                    "group %d is %r; this op cannot convert it to a latch — disable the "
                    "gesture op that made it so, or leave the stock behaviour"
                    % (gid, beh.kind), offers=["disable_gesture_op"])
            ctx.handles[self.spec.id] = {"gid": gid}
            return data
        try:
            data, e = gestures.make_hold_to_toggle(data, gid, self.seconds)
        except gestures.GestureEditError as err:
            raise RecipeError("GESTURE_REFUSED", str(err))
        ctx.edits.extend(e)
        data, _ = patch.reseal(data)
        ctx.handles[self.spec.id] = {"gid": gid, "multi": len(beh.emissions) > 1}
        return data

    def verify(self, data, ctx):
        gid = ctx.handles[self.spec.id]["gid"]
        beh = gestures.describe_group(data, gid)
        if self.kind == "latch":
            assert beh.kind == "latch"
            return "gesture: group %d stays a latch" % gid
        assert beh.kind == "hold-only" and beh.hold_ms == self.seconds * 1000
        held = beh.on(gestures.PHASE_HELD)
        assert held and not any(e.repeats for e in held)
        assert beh.repeat_held_ms == 0
        for dead in (gestures.PHASE_PRESS, gestures.PHASE_TAP,
                     gestures.PHASE_RELEASE_HELD):
            assert not beh.on(dead), "a tap must not switch the load"
        if not ctx.handles[self.spec.id].get("multi", False):
            return "gesture: group %d hold-to-toggle (%d s each way)" % (
                gid, self.seconds)
        assert set(beh.kinds.values()) == {"hold-only"}, beh.kinds
        assert len(held) == len(beh.emissions), "every live copy must fire on the hold"
        return ("gesture: group %d hold-to-toggle (%d s each way) on all %d live bindings "
                "of variants %s; direction is chosen by the group's external variant "
                "setter; COACH-VERIFIED 2026-10-19 (group 129, compact AES ELECTRICAL; AGS "
                "mode untested)"
                % (gid, self.seconds, len(beh.emissions),
                   "/".join(str(v) for v in beh.variants)))


def _touch_id(data, rec):
    """A wtype-12 record's trailing touch id (R50, `gestures.touch_ids`), else None."""
    return (data[rec.end - 2] | (data[rec.end - 1] << 8)) if rec.wtype == 12 else None


def _check_record_entries(entries, op_id):
    """Refuse a `records` list (`remove_records`' entry form) that is empty, or whose entries
    lack a selector or assert nothing about the record they name."""
    if not entries:
        raise RecipeError("BAD_PARAMS", "records is empty", op_id=op_id)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("selector"), dict):
            raise RecipeError("BAD_PARAMS", "each record entry needs a selector dict",
                              op_id=op_id)
        if (entry["selector"].get("label") is None and "child" not in entry
                and "touch_id" not in entry):
            raise RecipeError(
                "BAD_PARAMS", "entry %r asserts nothing about the record it removes; give "
                "a selector label, child or touch_id" % (entry,), op_id=op_id)


def _select_entries(data, obj, entries, op_id):
    """The records of `obj` that `entries` name, in entry order, each identity-checked
    (`child`, `touch_id`); BAD_PARAMS if two entries name one record."""
    gs, tab = _gs_tab(data)
    doomed = []
    for entry in entries:
        rec = _select_record(data, gs, tab, obj, entry["selector"], op_id)
        if "child" in entry and rec.child_index != entry["child"]:
            raise RecipeError("EXPECT_MISMATCH", "object %d record %r has child %r, "
                              "expected %r" % (obj, entry["selector"], rec.child_index,
                                               entry["child"]))
        if "touch_id" in entry:
            tid = _touch_id(data, rec)
            if tid != entry["touch_id"]:
                raise RecipeError("EXPECT_MISMATCH", "object %d record %r has touch id "
                                  "%r, expected %r" % (obj, entry["selector"], tid,
                                                       entry["touch_id"]))
        if rec.offset in (r.offset for r in doomed):
            raise RecipeError("BAD_PARAMS", "two entries name the record at %#x"
                              % rec.offset)
        doomed.append(rec)
    return doomed


def _remove_records(data, ctx, obj, entries, op_id):
    """`remove_records`' removal: drop the records `entries` name from `obj` and swap the
    shrunk stream in with `relocate.replace_object_records`, refusing POINTER_INSIDE for a
    GuiCode or LCD literal between the first removed record and the object's end. Returns the
    new image and the removed records (as decoded from `data`, entry order)."""
    gs, tab = _gs_tab(data)
    recs = records.object_records(data, gs + tab[obj])
    doomed = _select_entries(data, obj, entries, op_id)
    kept = [r for r in recs if r.offset not in {d.offset for d in doomed}]
    if not kept:
        raise RecipeError("BAD_PARAMS", "removing every record would leave object %d "
                          "empty" % obj)

    moved_from = min(d.offset for d in doomed) - gs
    obj_end = recs[-1].end - gs
    for section in ("GuiCode", "LCD"):
        for pos, rel in theme.section_guidata_pointers(data, section):
            if moved_from <= rel < obj_end:
                raise RecipeError(
                    "POINTER_INSIDE", "%s literal @ %#x points at GuiData+%#x, inside "
                    "object %d after its first removed record; the shrink would move what "
                    "it names" % (section, pos, rel, obj))

    body = bytes([len(kept)]) + b"".join(data[r.offset:r.end] for r in kept)
    try:
        out, edits = relocate.replace_object_records(data, obj, body)
    except relocate.RelocationError as err:
        raise RecipeError("RELOCATE_REFUSED", str(err))
    ctx.edits.extend(edits)
    return out, doomed


@op("remove_records")
class RemoveRecords(Op):
    """Delete chosen records from one object, shrinking it, so a control is gone from the
    page rather than parked off it.

    Parking (`remove_control`, `move_element` to y=900) rewrites geometry, and fails on a
    record that carries none: a wtype-12 touch region inside a card can have no `y`, so its
    hit area would stay live, invisible, on the page. This op instead rebuilds the object's
    record stream without the chosen records and swaps it in with
    `relocate.replace_object_records`, which shifts every downstream table, directory entry,
    container field and GuiCode literal by the size change, reseals, and reruns the
    relocator's gates.

    **The one hole it closes itself.** `relocate._verify_guicode_pointers` documents that a
    shrink swallowing an address GuiCode points to passes its gate. Everything after the first
    removed record moves down inside the object, and `replace_object_records` shifts only
    references at or after the object's END. So this op refuses if any GuiCode or LCD Firmware
    literal targets the object between its first removed record and its end.

    **The records after a removed one can move.** Each record lays out relative to the frame the
    previous drawn record left (`layout.py`), and a removed wtype-5 attach takes its own offset
    and its child's frame with it. On the compact AES card, removing the Low Volts attach (+29 y)
    lifted the "AES" gear button 29 px until a `move_element` gave the gear the offset back. This
    op does not compensate; diff the render (`fireflymcf verify`) and fix any record that moved,
    or use `move_control`, which removes through the same `_remove_records` and compensates.

    Bindings and groups are untouched: a removed touch region stops this object reaching its
    group, and the group still runs from any other object that registers the same id. So
    `recipe.capabilities` is unchanged and no delta is declared.

    Params:
      target: object index | {'ref': op_id} -- the object holding the records (resolved via
              `resolve_target`).
      records: list of {selector, child?, touch_id?} -- each `selector` is
               `_select_record`'s (wtype, label, nth), resolved against the object BEFORE any
               removal, so nth counts are the stock ones. `child` asserts the record's
               container `child_index`; `touch_id` asserts a wtype-12 record's trailing id.
               Each entry must carry at least one of label/child/touch_id, so an entry
               cannot remove whatever record happens to sit at an nth.

    Error codes:
      BAD_PARAMS -- `records` is empty, an entry has no identity assertion, two entries
                   name the same record, or the removal would leave the object empty.
      NO_SUCH_RECORD -- a selector matched nothing (from `_select_record`).
      EXPECT_MISMATCH -- a record's child_index or touch id is not what the entry asserts.
      POINTER_INSIDE -- a GuiCode or LCD literal points into the part of the object that
                        would move.
      RELOCATE_REFUSED -- `relocate.replace_object_records` refused (a gate failed).

    `hardware_verified = True`: COACH-VERIFIED 2026-10-19 (owner). The Low Volts attach and touch
    region were removed from the compact AES card (object 1427) with the gear button's offset
    restored; the card draws without it and the moved control works on the settings page.
    """
    hardware_verified = True

    PARAMS = {
        "properties": {
            "target": {"type": ["integer", "object"],
                       "description": "object index or {\"ref\": op_id}"},
            "records": {"type": "array",
                        "description": "records to delete: {selector, child?, touch_id?}"},
        },
        "required": ["target", "records"],
    }

    def validate(self):
        self.target = self._param("target")
        self.records = self._param("records", kind=list)
        _check_record_entries(self.records, self.spec.id)

    def apply(self, data, ctx):
        obj = resolve_target(ctx, self.target)
        out, doomed = _remove_records(data, ctx, obj, self.records, self.spec.id)
        gs, tab = _gs_tab(out)
        ctx.handles[self.spec.id] = {
            "obj": obj, "count": len(records.object_records(out, gs + tab[obj])),
            "gone": [(d.wtype, d.child_index, _touch_id(data, d)) for d in doomed]}
        return out

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[h["obj"]])
        assert len(recs) == h["count"], "object %d has %d records, wanted %d" % (
            h["obj"], len(recs), h["count"])
        for wtype, child, tid in h["gone"]:
            if wtype == 12 and tid is not None:
                assert tid not in gestures.touch_ids(data, h["obj"], tab), \
                    "touch id %d still registered by object %d" % (tid, h["obj"])
            elif child is not None:
                assert not any(r.wtype == wtype and r.child_index == child for r in recs), \
                    "object %d still attaches child %d" % (h["obj"], child)
        return "remove_records: object %d lost %d record(s) %s, now %d" % (
            h["obj"], len(h["gone"]),
            ", ".join("wtype %d%s" % (w, " touch %d" % t if t is not None else
                                      " child %d" % c if c is not None else "")
                      for w, c, t in h["gone"]), h["count"])


_LAYOUT_FIELDS = ("x", "y", "w", "h")
_LAYOUT_FORMS = ("expect", "snapshot", "unchanged_since")


def _int_keys(value, name, op_id):
    """A `{value: int}` param with JSON string keys ("12", "0x1F") as ints."""
    if value is None or value == {}:
        return None     # an empty dict walks exactly as no dict, so the two compare equal
    if not isinstance(value, dict):
        raise RecipeError("BAD_PARAMS", "%s must be a dict, got %r" % (name, value),
                          op_id=op_id)
    out = {}
    for k, v in value.items():
        try:
            key = k if isinstance(k, int) else int(k, 0)
        except (TypeError, ValueError):
            raise RecipeError("BAD_PARAMS", "%s key %r is not an integer" % (name, k),
                              op_id=op_id)
        if not isinstance(v, int):
            raise RecipeError("BAD_PARAMS", "%s[%r] must be an integer, got %r"
                              % (name, k, v), op_id=op_id)
        out[key] = v
    return out


def _check_select(sel, op_id):
    """Refuse a `select` that is neither `{key}` nor `{label, wtype?, nth?}`."""
    if not isinstance(sel, dict):
        raise RecipeError("BAD_PARAMS", "select must be a dict, got %r" % (sel,), op_id=op_id)
    if set(sel) == {"key"}:
        key = sel["key"]
        if not (isinstance(key, list) and key
                and all(isinstance(s, list) and len(s) == 2
                        and all(isinstance(v, int) for v in s) for s in key)):
            raise RecipeError("BAD_PARAMS", "select key must be [[obj, i], ...], got %r"
                              % (key,), op_id=op_id)
        return
    if set(sel) == {"object", "record"} and all(
            isinstance(sel[k], int) and not isinstance(sel[k], bool) for k in sel):
        return
    if ("label" in sel and isinstance(sel["label"], str)
            and set(sel) <= {"label", "wtype", "nth"}
            and isinstance(sel.get("wtype", 0), int) and isinstance(sel.get("nth", 0), int)):
        return
    raise RecipeError("BAD_PARAMS", "select must be {key: [[obj, i], ...]}, {object, record} or "
                      "{label, wtype?, nth?}, got %r" % (sel,), op_id=op_id)


def _tupled(v):
    return tuple(_tupled(x) for x in v) if isinstance(v, (list, tuple)) else v


def _walk_to_json(walk):
    return [{f.name: getattr(r, f.name) for f in dataclasses.fields(r)} for r in walk]


def _walk_from_json(rows):
    """`_walk_to_json`'s rows back as `WalkRec`s, tuples restored (a JSON round trip of the
    handles turns them into lists, and `diff` matches keys by equality)."""
    return [pagewalk.WalkRec(**{k: _tupled(v) if isinstance(v, list) else v
                                for k, v in row.items()}) for row in rows]


def _fmt_rec(r):
    return "%s wtype %d %r (%d,%d,%d,%d)" % ("/".join("%d:%d" % s for s in r.key), r.wtype,
                                            r.label, *r.box)


def _expect_delta(r, entry):
    """A `moved` Delta from the box `entry`'s x/y/w/h give (fields it omits taken from `r`) to
    `r`'s placed box, or None if they are equal."""
    x0, y0, x1, y1 = r.box
    ex0, ey0 = entry.get("x", x0), entry.get("y", y0)
    ebox = (ex0, ey0, ex0 + entry.get("w", x1 - x0), ey0 + entry.get("h", y1 - y0))
    if ebox == r.box:
        return None
    return pagewalk.Delta(r.key, r.wtype, r.label, "moved", dataclasses.replace(r, box=ebox), r,
                          x0 - ex0, y0 - ey0)


def _resolve_select(walk, sel, root):
    """The one record of `walk` that `sel` names: by key; by `{object, record}`, the record
    index within its own object; or by stripped label (with optional wtype) and `nth` among the
    matches in walk order. NO_MATCH if none; AMBIGUOUS if a label without `nth` matches more
    than one, or an `{object, record}` is drawn under more than one key (a shared child)."""
    if "key" in sel:
        key = _tupled(sel["key"])
        hits = [r for r in walk if r.key == key]
        if not hits:
            raise RecipeError("NO_MATCH", "root %d draws no record at key %s"
                              % (root, "/".join("%d:%d" % s for s in key)))
        return hits[0]
    if "object" in sel:
        hits = [r for r in walk if r.obj == sel["object"] and r.index == sel["record"]]
        if not hits:
            raise RecipeError("NO_MATCH", "root %d does not draw object %d record %d"
                              % (root, sel["object"], sel["record"]))
        if len(hits) > 1:
            raise RecipeError("AMBIGUOUS", "root %d draws object %d record %d under %d keys "
                              "(a shared child); give a key:\n%s"
                              % (root, sel["object"], sel["record"], len(hits),
                                 "\n".join(_fmt_rec(r) for r in hits)))
        return hits[0]
    hits = [r for r in walk if (r.label or "").strip() == sel["label"].strip()
            and ("wtype" not in sel or r.wtype == sel["wtype"])]
    if "nth" in sel:
        if sel["nth"] >= len(hits):
            raise RecipeError("NO_MATCH", "root %d has %d record(s) matching %r, wanted nth=%d"
                              % (root, len(hits), sel, sel["nth"]))
        return hits[sel["nth"]]
    if not hits:
        raise RecipeError("NO_MATCH", "root %d draws no record matching %r" % (root, sel))
    if len(hits) > 1:
        raise RecipeError("AMBIGUOUS", "root %d has %d records matching %r; give nth or a key:\n%s"
                          % (root, len(hits), sel, "\n".join(_fmt_rec(r) for r in hits)))
    return hits[0]


@op("assert_layout")
class AssertLayout(Op):
    """Check, at its position in the recipe, where records sit on a page, or that nothing on a
    page moved since an earlier snapshot. It changes no bytes.

    The page is measured by `pagewalk.walk_records` (the layout engine `FUN_00257BFC` replayed,
    containers included) and every record is named by its walk key, the chain of
    `(object, record index)` steps from the root, so a measurement survives an earlier object
    growing or shrinking. Boxes are ABSOLUTE screen pixels, as `layout.Placed` carries them, never
    the card-relative values a record's own geometry fields hold.

    The check runs in `apply` and refuses there, so it sees the image as the ops before it left
    it; `verify` only reports. A page that draws differently per switch value is checked for the
    one `switch_values` set given; a page that must hold under several needs one op per set.

    Params:
      root: object index | {'ref': op_id} -- the page root to walk (resolved via
            `resolve_target`).
      switch_values, slot_values: optional {int: int} -- passed to the walk; JSON string keys
            ("12", "0x1F") are read as integers, and {} is the same as absent.
      exactly one of:
        expect: list of {select, x?, y?, w?, h?} -- each given field must equal the selected
                record's placed box (x = x0, y = y0, w = x1 - x0, h = y1 - y0); each entry
                gives at least one.
        snapshot: true -- record the walk in this op's handle and check nothing.
        unchanged_since: op_id -- an earlier `snapshot` of the same root and values. Every
                record of the snapshot must still be drawn at the same box with the same
                inherited state (font, align, colours, `value_slot`), and no record may
                appear, except those `except` names.
      except: optional list of select, with `unchanged_since` only -- records left out of the
              comparison; each is resolved against the current walk, else the snapshot's.

      select: {key: [[obj, i], ...]} names a record by walk key; {object, record} by its index
              within its own object (AMBIGUOUS if a shared child draws it under several
              keys); {label, wtype?, nth?} by its stripped label (and wtype), `nth` counting
              the matches in walk order.

    Error codes:
      BAD_PARAMS -- no form or more than one, a malformed select or expect entry, `except`
                    without `unchanged_since`, or `unchanged_since` naming no earlier snapshot,
                    or one of another root or other switch/slot values.
      NO_MATCH -- a select names no record of the walk.
      AMBIGUOUS -- a label select without `nth` names more than one record, or an
                   {object, record} select a record drawn under several keys (each is listed).
      LAYOUT_MISMATCH -- an `expect` field differs from the placed box (`pagewalk.describe`
                         lines: expected box -> actual box and the delta).
      LAYOUT_MOVED -- `unchanged_since` found records moved, restyled, gone or appeared
                      (`pagewalk.describe` lines).

    `hardware_verified = True`: it changes no bytes, so there is nothing to run on a coach.
    `recipe.capabilities` is unchanged and no delta is declared.
    """
    hardware_verified = True

    PARAMS = {
        "properties": {
            "root": {"type": ["integer", "object"],
                     "description": "page root object index or {\"ref\": op_id}"},
            "switch_values": {"type": "object",
                              "description": "optional {switch child base: value} for the walk"},
            "slot_values": {"type": "object",
                            "description": "optional {slot: value} for the walk"},
            "expect": {"type": "array",
                       "description": "records and their absolute box: {select, x?, y?, w?, h?}"},
            "snapshot": {"type": "boolean",
                         "description": "true: record the walk for a later unchanged_since"},
            "unchanged_since": {"type": "string",
                                "description": "id of an earlier snapshot of the same root"},
            "except": {"type": "array",
                       "description": "selects left out of the unchanged_since comparison"},
        },
        "required": ["root"],
    }

    def validate(self):
        oid = self.spec.id
        self.root = self._param("root")
        if not isinstance(self.root, (int, dict)) or isinstance(self.root, bool):
            raise RecipeError("BAD_PARAMS", "root must be an object index or {'ref': op_id}",
                              op_id=oid)
        self.switch_values = _int_keys(self._param("switch_values", None), "switch_values", oid)
        self.slot_values = _int_keys(self._param("slot_values", None), "slot_values", oid)
        forms = [f for f in _LAYOUT_FORMS if f in self.spec.params]
        if len(forms) != 1:
            raise RecipeError("BAD_PARAMS", "assert_layout takes exactly one of expect, "
                              "snapshot, unchanged_since; got %s" % (forms or "none"),
                              op_id=oid)
        self.form = forms[0]
        if "except" in self.spec.params and self.form != "unchanged_since":
            raise RecipeError("BAD_PARAMS", "except goes with unchanged_since only", op_id=oid)
        if self.form == "snapshot" and self._param("snapshot") is not True:
            raise RecipeError("BAD_PARAMS", "snapshot must be true", op_id=oid)
        if self.form == "unchanged_since":
            self._param("unchanged_since", kind=str)
            for sel in self._param("except", [], kind=list):
                _check_select(sel, oid)
        if self.form == "expect":
            entries = self._param("expect", kind=list)
            if not entries:
                raise RecipeError("BAD_PARAMS", "expect is empty", op_id=oid)
            for entry in entries:
                if not isinstance(entry, dict) or "select" not in entry:
                    raise RecipeError("BAD_PARAMS", "each expect entry needs a select",
                                      op_id=oid)
                _check_select(entry["select"], oid)
                extra = set(entry) - {"select", *_LAYOUT_FIELDS}
                fields = [f for f in _LAYOUT_FIELDS if f in entry]
                if extra or not fields or not all(
                        isinstance(entry[f], int) and not isinstance(entry[f], bool)
                        for f in fields):
                    raise RecipeError("BAD_PARAMS", "expect entry %r needs integer x/y/w/h "
                                      "(at least one) and nothing else" % (entry,), op_id=oid)

    def _walk(self, data, root):
        return pagewalk.walk_records(data, root, switch_values=self.switch_values,
                                     slot_values=self.slot_values)

    def apply(self, data, ctx):
        root = resolve_target(ctx, self.root)
        walk = self._walk(data, root)
        handle = {"assert_layout": self.form, "root": root,
                  "switch_values": self.switch_values, "slot_values": self.slot_values}
        if self.form == "snapshot":
            handle["walk"] = _walk_to_json(walk)
        elif self.form == "expect":
            self._check_expect(walk, root)
            handle["count"] = len(self.spec.params["expect"])
        else:
            handle["since"] = self._check_unchanged(walk, root, ctx)
        ctx.handles[self.spec.id] = handle
        return data

    def _check_expect(self, walk, root):
        deltas = []
        for entry in self.spec.params["expect"]:
            delta = _expect_delta(_resolve_select(walk, entry["select"], root), entry)
            if delta is not None:
                deltas.append(delta)
        if deltas:
            raise RecipeError("LAYOUT_MISMATCH", "root %d: %d record(s) not where expected "
                              "(expected -> actual):\n%s"
                              % (root, len(deltas), pagewalk.describe(deltas)))

    def _check_unchanged(self, walk, root, ctx):
        since = self.spec.params["unchanged_since"]
        snap = ctx.handles.get(since)
        if not isinstance(snap, dict) or snap.get("assert_layout") != "snapshot":
            raise RecipeError("BAD_PARAMS", "unchanged_since %r names no earlier assert_layout "
                              "snapshot" % since)
        if snap["root"] != root:
            raise RecipeError("BAD_PARAMS", "snapshot %r is of root %d, this check is of root %d"
                              % (since, snap["root"], root))
        for name in ("switch_values", "slot_values"):
            theirs = _int_keys(snap[name], name, self.spec.id)
            if theirs != getattr(self, name):
                raise RecipeError("BAD_PARAMS", "snapshot %r walked with %s %r, this check "
                                  "with %r" % (since, name, theirs, getattr(self, name)))
        before = _walk_from_json(snap["walk"])
        ignore = set()
        for sel in self._param("except", []):
            try:
                ignore.add(_resolve_select(walk, sel, root).key)
            except RecipeError as e:
                if e.code != "NO_MATCH":
                    raise
                ignore.add(_resolve_select(before, sel, root).key)
        deltas = pagewalk.diff(before, walk, ignore=frozenset(ignore))
        if deltas:
            raise RecipeError("LAYOUT_MOVED", "root %d changed since %r: %d record(s):\n%s"
                              % (root, since, len(deltas), pagewalk.describe(deltas)))
        return since

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        if h["assert_layout"] == "snapshot":
            return "assert_layout: snapshot of root %d, %d record(s)" % (h["root"],
                                                                         len(h["walk"]))
        if h["assert_layout"] == "expect":
            return "assert_layout: root %d, %d record(s) where expected" % (h["root"],
                                                                           h["count"])
        return "assert_layout: root %d unchanged since %r" % (h["root"], h["since"])


_CANVAS_W, _CANVAS_H = 800, 480
# Container types the walk recurses into (`FUN_00256C54` dispatches 5/6/0x2E to `FUN_00258330`,
# GUICODE Finding 15). They draw nothing: their walked box is their pen.
_CONTAINERS = (5, 6, 0x2E)


def _moved_step(key, obj, removed):
    """The position in walk key `key` of its step through a record of `obj` in `removed`, or
    None if the key passes through none."""
    for n, (o, i) in enumerate(key):
        if o == obj and i in removed:
            return n
    return None


def _fmt_walk_key(key):
    return "/".join("%d:%d" % s for s in key)


def _compensation_target(walk_deltas, frm):
    """The edit compensation makes next, from each walk's `pagewalk.diff` deltas: the record of
    `frm` that places each walk's FIRST moved record (the record itself when it is in `frm`,
    else the nearest step through `frm` on its key: the attach in `frm` it hangs below), as
    `(after-index in frm, dx, dy)`. None when no walk has a moved record.

    Every walk with a moved record must name the same record and the same (dx, dy), or
    ARM_DISAGREES: one geometry edit cannot restore two different shifts. A first moved record
    with no step through `frm` (a record after `frm`'s attach that the removal's leftover
    frame reached) is CANNOT_COMPENSATE: compensation edits `frm` only, never a shared child
    or a parent."""
    firsts = []
    for deltas in walk_deltas:
        d = next((d for d in deltas if d.kind == "moved"), None)
        if d is None:
            continue
        steps = [i for o, i in d.after.key if o == frm]
        if not steps:
            raise RecipeError("CANNOT_COMPENSATE", "the removal moved a record no record of "
                              "object %d places:\n%s" % (frm, pagewalk.describe([d])))
        firsts.append(((steps[-1], d.dx, d.dy), d))
    if not firsts:
        return None
    if len({t for t, _ in firsts}) > 1:
        raise RecipeError("ARM_DISAGREES", "the walks disagree on what the removal moved, so one "
                          "edit cannot put it back:\n%s"
                          % pagewalk.describe([d for _, d in firsts]))
    return firsts[0][0]


def _register_dependency(walk, frm, removed):
    """A register the moved records write that something reads after the move would break:
    `(writer, reader, coordinate)`, or None. The walk keeps `regx`/`regy` across records
    (`layout.Screen.walk`: coordinate i reads `in_regs[i]`, then writes `out_regs[i]`, x
    registers on even i, y on odd). A moved record's write is lost to a SURVIVING reader; a
    re-encoded record's write is also lost to a byte-shared child below it, since
    `absrecord.absolute` drops every register field. A later writer of the same register ends
    the dependency."""
    last = {}
    for r in walk:
        step = _moved_step(r.key, frm, removed)
        top = step is not None and step == len(r.key) - 1
        for i in range(6):
            reg = r.in_regs[i] if len(r.in_regs) > i else 0
            if reg:
                w = last.get((i % 2, reg))
                if w is not None and (step is None or (w[1] and not top)):
                    return w[0], r, i
            reg = r.out_regs[i] if len(r.out_regs) > i else 0
            if reg:
                last[(i % 2, reg)] = (r, top) if step is not None else None
    return None


def _effective_slot(by_key, r):
    """`r`'s value slot: its own, else the nearest wrapping wtype-5 attach's on its key. The
    child of a wtype-5 attach carrying `value_slot` repaints from that cell
    (`OBJECT-HIERARCHY.md` 1a and §3, `inferred-from-data`: the AGS Low Volts attach, object
    315 record 8, and its child 285); the walk itself models only the record's own field."""
    if r.value_slot is not None:
        return r.value_slot
    for n in range(len(r.key) - 1, 0, -1):
        up = by_key.get(r.key[:n])
        if up is not None and up.wtype == 5 and up.value_slot is not None:
            return up.value_slot
    return None


def _inherited_slot(by_key, r):
    """`(slot, attach)` when `r` has no value slot of its own and the nearest wrapping wtype-5
    attach on its key that carries one is `attach`; else None (`_effective_slot`'s rule)."""
    if r.value_slot is not None:
        return None
    for n in range(len(r.key) - 1, 0, -1):
        up = by_key.get(r.key[:n])
        if up is not None and up.wtype == 5 and up.value_slot is not None:
            return up.value_slot, up
    return None


def _overlaps(a, b):
    """Two boxes share a pixel. Inclusive edges: the hit test returns the id of a region
    containing the point (CONTROL-BEHAVIOR §1, `0x00258BB2`-`0x00258C26`); whether its edges
    are inclusive is not read from the code, so a shared edge counts as overlap (inference,
    the conservative reading)."""
    ax0, ax1 = sorted((a[0], a[2]))
    ay0, ay1 = sorted((a[1], a[3]))
    bx0, bx1 = sorted((b[0], b[2]))
    by0, by1 = sorted((b[1], b[3]))
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


@op("move_control")
class MoveControl(Op):
    """Move a control (its draw, touch and box records) from one object to another, put back
    anything on the source page the removal moved, and assert nothing else moved on either page.

    It is `remove_records` on `from`, a compensation pass, and an `insert.insert_object` paste
    on `to`, measured throughout by `pagewalk` (the layout engine `FUN_00257BFC` replayed,
    containers included, every record keyed by its `(object, record)` path from the root):

    1. **Measure.** Walk every page root that reaches each object (`pagewalk.reaching_roots`,
       or the one `source_root`/`dest_root` given), once per switch-arm set
       (`pagewalk.switch_arm_sets` of that root: the base, then each type-6 switch's arms one at
       a time; or only the `switch_values`/`slot_values` given). The control is the chosen
       records of `from` and everything the walk draws below them (a byte-shared child of an
       attach moves with it and is never cloned), as the first source root's base walk places
       it.
    2. **Pre-check**, before any edit: NO_ROOT, UNSUPPORTED_RECORD (`absrecord.SUPPORTED`),
       REGISTER_DEPENDENCY (`_register_dependency`), TOUCH_OVERLAP (a translated wtype-12 box
       meets one of the destination's), OFF_CANVAS (a translated drawn box leaves 800x480). The
       pasted records are re-encoded here too, so a record `absrecord.absolute` cannot carry
       refuses before anything changes.
    3. **Remove** with `_remove_records` (`remove_records`' identity checks, POINTER_INSIDE and
       relocation).
    4. **Compensate** the source: re-walk, take each walk's first moved record and the record of
       `from` that places it (`_compensation_target`), and subtract the shift from that record's
       x/y geometry fields (a same-size write, as `move_element` does). Repeat until nothing
       moved. A record that lays out from the frame the previous drawn record left
       (`layout._update_frame`) comes back with the record before it, so on the compact AES card
       one edit, the gear's y +29, restores the whole gear group. A surviving record whose
       inherited font, align, colours or `value_slot` changed is STATE_CHANGED and is not
       compensated (fixing its geometry would leave it styled wrong).
    5. **Paste.** One new object (`insert.insert_object`, a wtype-5 attach appended to `to`)
       holds the chosen records re-encoded by `absrecord.absolute`: x, y absolute at `at` plus
       the record's offset from the control's bounding box (its drawn records' top-left), w, h
       pen-relative, register reads and writes dropped. `absolute`'s (x, y) is the PEN: a
       path-1 record (`layout._align_path1`) under align 2/3 and a labelled wtype 0 under align
       2/3 have their box shifted from the pen (`layout._placed_label_box`), so the pen is the
       target box's left edge with that shift inverted. The first record carrying an R38 font
       field gets the walked font explicit (UNSUPPORTED_RECORD if the walk never resolved one);
       the first align-writing record (`layout._writes_align`) gets the walked align explicit
       when it is non-zero (the decoder skips storing a 0 align, `FUN_00252CB0` 0x2531fe, so
       a 0 cannot be made explicit). A record's own value slot is kept; one with none of its own
       that inherits one from a wrapping wtype-5 attach (`_inherited_slot`, `inferred-from-data`)
       is refused UNSUPPORTED_RECORD, since that attach does not move. Colour fields are copied;
       a mode-0/5 field still inherits at the destination. The attach on `to` is not authored:
       it is `insert.insert_object`'s byte clone of `to`'s first wtype-5 record
       (`insert._container_template`), at that template's own geometry and register reads. It
       draws nothing and the control's records are absolute, so its pen does not matter; one
       carrying a value slot is refused UNSUPPORTED_RECORD (the control would inherit it).
    6. **Measure after and assert.** Re-walk every (root, arm) pair of both sides. Every record
       that was there must be unchanged (the source through the removal's index remap); the only
       new records are the attach on `to` and, under it, the control, each at its source box
       translated to `at`, with the same font, align, colours and effective `value_slot`. A
       state difference is STATE_CHANGED (the pasted record would be styled wrong); any other
       difference is LAYOUT_MOVED, with `pagewalk.describe` lines.

    Touch ids map to groups globally (`FUN_00256B84` copies the id into the hit-region table
    whatever page hosts it), so the moved touch region runs the same group from the new page: no
    group, binding or DGN changes, and `capability_delta` is `(set(), set())`.

    Params:
      from: object index | {'ref': op_id} -- the object holding the control.
      to: object index | {'ref': op_id} -- the object the control moves to; not `from`.
      records: list of {selector, child?, touch_id?} -- `remove_records`' entry form, resolved
               against `from` before any edit, each entry with an identity assertion.
      at: {x, y} -- the absolute top-left of the control's drawn records on the destination.
      source_root, dest_root: optional int -- measure only this page root (it must reach the
               object); default every root that reaches it.
      switch_values, slot_values: optional {int: int} -- measure only this value set instead
               of every arm set; JSON string keys are read as integers.

    Error codes:
      BAD_PARAMS -- a malformed param, `from == to`, an object out of range, an entry with no
                    identity assertion or two entries naming one record, or a removal that
                    would leave `from` empty.
      NO_SUCH_RECORD -- a selector matched nothing (from `_select_record`).
      EXPECT_MISMATCH -- a record's child_index or touch id is not what the entry asserts.
      POINTER_INSIDE -- a GuiCode or LCD literal points into the part of `from` that would move.
      RELOCATE_REFUSED -- `relocate.replace_object_records` refused.
      NO_ROOT -- no page root reaches `from` or `to`, a given root does not, or the first source
                 root's walk does not draw the chosen records.
      TOUCH_OVERLAP -- a moved touch region, translated to `at`, meets a destination one.
      OFF_CANVAS -- a moved drawn record, translated to `at`, leaves the 800x480 canvas.
      UNSUPPORTED_RECORD -- a chosen record's wtype is not re-encodable, its font is
                            unresolved, it inherits a value slot from an attach that stays,
                            the attach `insert_object` would clone onto `to` carries a value
                            slot, or `absrecord.absolute` cannot carry a field it needs.
      REGISTER_DEPENDENCY -- a moved record writes a register a record that stays reads.
      CANNOT_COMPENSATE -- the removal moved a record with no x/y field to put the shift back
                           in, one no record of `from` places, or one whose size changed; or the
                           passes did not converge.
      STATE_CHANGED -- the removal changed a surviving record's inherited font, align, colours
                       or `value_slot`, or a pasted record's differs from its source.
      ARM_DISAGREES -- the walks disagree on the record the removal moved first, or its shift.
      LAYOUT_MOVED -- after the paste, a record other than the moved control moved, appeared or
                      went, or a pasted record is not at its translated box.
      INSERT_REFUSED -- `insert.insert_object` refused (no wtype-5 template on `to`, or a gate).

    `hardware_verified = True`: COACH-VERIFIED 2026-10-20 (owner), the LV-mc proof build. It
    moved Low Volts from the compact AES card (object 1427) to the AES settings page (1425),
    finding the gear button's compensation itself; the card draws as LV-move did, nothing else
    moved, and the moved button arms and disarms on its 3 s hold and repaints with its status.
    Registers `ctx.handles[op_id]`; `{'ref': op_id}` resolves to the new object.
    """
    hardware_verified = True

    PARAMS = {
        "properties": {
            "from": {"type": ["integer", "object"],
                     "description": "object index or {\"ref\": op_id} holding the control"},
            "to": {"type": ["integer", "object"],
                   "description": "destination object index or {\"ref\": op_id}"},
            "records": {"type": "array",
                        "description": "the control's records: {selector, child?, touch_id?}"},
            "at": {"type": "object",
                   "description": "{x, y}: absolute top-left of the moved control"},
            "source_root": {"type": "integer",
                            "description": "optional: measure only this source page root"},
            "dest_root": {"type": "integer",
                          "description": "optional: measure only this destination page root"},
            "switch_values": {"type": "object",
                              "description": "optional {switch child base: value}: measure only "
                                             "this set"},
            "slot_values": {"type": "object",
                            "description": "optional {slot: value}: measure only this set"},
        },
        "required": ["from", "to", "records", "at"],
    }

    def validate(self):
        oid = self.spec.id
        self.frm = self._param("from")
        self.to = self._param("to")
        for name, v in (("from", self.frm), ("to", self.to)):
            if not isinstance(v, (int, dict)) or isinstance(v, bool):
                raise RecipeError("BAD_PARAMS", "%s must be an object index or {'ref': op_id}"
                                  % name, op_id=oid)
        if isinstance(self.frm, int) and self.frm == self.to:
            raise RecipeError("BAD_PARAMS", "from and to are both object %d" % self.frm,
                              op_id=oid)
        self.records = self._param("records", kind=list)
        _check_record_entries(self.records, oid)
        self.at = self._param("at", kind=dict)
        if set(self.at) != {"x", "y"} or not all(
                isinstance(self.at[k], int) and not isinstance(self.at[k], bool)
                for k in ("x", "y")):
            raise RecipeError("BAD_PARAMS", "at must be {x: int, y: int}, got %r" % (self.at,),
                              op_id=oid)
        self.source_root = self._param("source_root", None)
        self.dest_root = self._param("dest_root", None)
        for name, v in (("source_root", self.source_root), ("dest_root", self.dest_root)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                raise RecipeError("BAD_PARAMS", "%s must be an integer" % name, op_id=oid)
        self.switch_values = _int_keys(self._param("switch_values", None), "switch_values", oid)
        self.slot_values = _int_keys(self._param("slot_values", None), "slot_values", oid)

    # -- measuring -------------------------------------------------------------------------

    def _roots(self, data, obj, given, side):
        reaching = pagewalk.reaching_roots(data, obj)
        if given is not None:
            if given not in reaching:
                raise RecipeError("NO_ROOT", "%s root %d does not reach object %d (roots that "
                                  "do: %s)" % (side, given, obj, reaching or "none"))
            return [given]
        if not reaching:
            raise RecipeError("NO_ROOT", "no page root reaches %s object %d" % (side, obj))
        return reaching

    def _pairs(self, data, roots):
        """(root, switch_values, slot_values) for every walk of `roots` measured."""
        if self.switch_values is not None or self.slot_values is not None:
            return [(r, self.switch_values, self.slot_values) for r in roots]
        return [(r, arm, None) for r in roots for arm in pagewalk.switch_arm_sets(data, [r])]

    @staticmethod
    def _walk(data, pair):
        root, sv, slv = pair
        return pagewalk.walk_records(data, root, switch_values=sv, slot_values=slv)

    @staticmethod
    def _pair_id(pair):
        root, sv, slv = pair
        return (root, tuple(sorted((sv or {}).items())), tuple(sorted((slv or {}).items())))

    @staticmethod
    def _subtree(walk, frm, removed):
        """The control as `walk` draws it at the first occurrence of `frm`: `{rel: WalkRec}`,
        `rel` being `(record index in frm,) + the key steps below it`. None if not drawn."""
        prefix = next((r.key[:-1] for r in walk if r.obj == frm and r.index in removed), None)
        if prefix is None:
            return None
        n = len(prefix)
        return {(r.key[n][1],) + r.key[n + 1:]: r for r in walk
                if r.key[:n] == prefix and len(r.key) > n and r.key[n][0] == frm
                and r.key[n][1] in removed}

    def _pen_x(self, data, rec, placed, x0):
        """The pen x that places `placed`'s box left edge at `x0` (`layout._placed_label_box`
        inverted): a path-1 type under align 3 has its box at `pen - w`, under align 2 at
        `pen - w // 2`; a labelled non-path-1 record under align 2/3 at
        `metrics.aligned_x0(pen, text width, align)`; anything else at the pen."""
        align = placed.align
        if align not in (2, 3):
            return x0
        if layout._align_path1(rec.wtype):
            w = placed.box[2] - placed.box[0]
            return x0 + w if align == 3 else x0 + w // 2
        if rec.label is not None and placed.font is not None:
            width = metrics.text_width(data, placed.font, rec.label)
            if width is not None:
                return x0 + width - 1 if align == 3 else x0 + width // 2
        return x0

    def _paste_bytes(self, data, recs, moved_idx, ref, by_key, offset):
        """The new object: a count byte and each chosen record re-encoded absolute. `by_key`
        is the whole source walk `ref` was taken from, for `_effective_slot`."""
        ox, oy = offset
        font_done = align_done = False
        parts = []
        for i in moved_idx:
            rec, placed = recs[i], ref[(i,)]
            explicit_font = not font_done and rec.font_off is not None
            if explicit_font:
                font_done = True
                if placed.font is None:
                    raise RecipeError("UNSUPPORTED_RECORD", "record %d (wtype %d) carries "
                                      "the control's first font field, but the source walk "
                                      "never resolved a font for it" % (i, rec.wtype))
            reset_align = False
            if not align_done and layout._writes_align(rec.wtype):
                align_done = True
                reset_align = placed.align != 0
            inherited = _inherited_slot(by_key, placed)
            if inherited is not None and absrecord.carries_value_slot(rec.wtype):
                # the slot belongs on the wrapping attach, which does not move; writing it into
                # the record would be a field the source never had, and the inheritance itself
                # is inferred-from-data (OBJECT-HIERARCHY 1a), so refuse rather than guess
                raise RecipeError("UNSUPPORTED_RECORD", "record %d:%d inherits value_slot %d "
                                  "from attach %d:%d; moving an inherited slot is not modelled"
                                  % (placed.obj, i, inherited[0], inherited[1].obj,
                                     inherited[1].index))
            try:
                parts.append(absrecord.absolute(
                    data, rec, placed, self._pen_x(data, rec, placed, placed.box[0] + ox),
                    placed.box[1] + oy, explicit_font, reset_align, None))
            except ValueError as e:              # absrecord.Unsupported is a ValueError
                raise RecipeError("UNSUPPORTED_RECORD", "record %d (wtype %d): %s"
                                  % (i, rec.wtype, e))
        payload = bytes([len(parts)]) + b"".join(parts)
        return payload + b"\x00" * (-len(payload) % 4)      # object offsets are 4-aligned

    # -- apply -----------------------------------------------------------------------------

    def apply(self, data, ctx):
        oid = self.spec.id
        frm, to = resolve_target(ctx, self.frm), resolve_target(ctx, self.to)
        gs, tab = _gs_tab(data)
        if frm == to:
            raise RecipeError("BAD_PARAMS", "from and to are both object %d" % frm)
        for name, o in (("from", frm), ("to", to)):
            if not 0 <= o < len(tab):
                raise RecipeError("BAD_PARAMS", "%s object %d is out of range" % (name, o))
        doomed = _select_entries(data, frm, self.records, oid)
        recs = records.object_records(data, gs + tab[frm])
        index_of = {r.offset: i for i, r in enumerate(recs)}
        moved_idx = sorted(index_of[d.offset] for d in doomed)
        removed = set(moved_idx)
        if len(removed) == len(recs):
            raise RecipeError("BAD_PARAMS", "removing every record would leave object %d "
                              "empty" % frm)

        # 1. measure before
        src_pairs = self._pairs(data, self._roots(data, frm, self.source_root, "source"))
        dst_roots = self._roots(data, to, self.dest_root, "destination")
        dst_pairs = self._pairs(data, dst_roots)
        before = {}
        for p in src_pairs:
            before.setdefault(self._pair_id(p), self._walk(data, p))
        ref_walk = before[self._pair_id(src_pairs[0])]
        ref = self._subtree(ref_walk, frm, removed)
        if ref is None or {rel[0] for rel in ref if len(rel) == 1} != removed:
            raise RecipeError("NO_ROOT", "root %d's walk does not draw object %d records %s"
                              % (src_pairs[0][0], frm, moved_idx))
        # a type-6 switch inside the control is walked arm by arm on the destination too
        # (`switch_values` keys on the switch's child base)
        inner = {pagewalk._record(data, r.obj, r.index).child_index
                 for r in ref.values() if r.wtype == 6}
        if self.switch_values is None and self.slot_values is None:
            dst_pairs += [(root, arm, None) for root in dst_roots
                          for _, arm, _ in src_pairs if arm and set(arm) & inner]
        for p in dst_pairs:
            before.setdefault(self._pair_id(p), self._walk(data, p))

        # 2. pre-checks
        for i in moved_idx:
            if recs[i].wtype not in absrecord.SUPPORTED:
                raise RecipeError("UNSUPPORTED_RECORD", "object %d record %d is wtype %d; "
                                  "move_control re-encodes wtypes %s"
                                  % (frm, i, recs[i].wtype, sorted(absrecord.SUPPORTED)))
        # the paste attach is insert_object's byte clone of `to`'s first wtype-5 record
        # (`insert._container_template`); a value slot on it would be inherited by the pasted
        # records (OBJECT-HIERARCHY 1a), a cell the source never gave them
        to_recs = records.object_records(data, gs + tab[to])
        try:
            _, template = insert._container_template(data, to_recs, False)
        except insert.InsertError:
            template = None                       # insert_object refuses it: INSERT_REFUSED
        if template is not None and template.value_slot is not None:
            raise RecipeError("UNSUPPORTED_RECORD", "the attach insert_object would clone onto "
                              "object %d (its record %d) carries value_slot %d, which the "
                              "pasted control would inherit"
                              % (to, to_recs.index(template), template.value_slot))
        for pid, walk in before.items():
            dep = _register_dependency(walk, frm, removed)
            if dep is not None:
                w, r, i = dep
                raise RecipeError("REGISTER_DEPENDENCY", "root %d: %s (wtype %d, moved) writes "
                                  "%s register %d, which %s (wtype %d) reads after it; the move "
                                  "would leave it unset" % (pid[0], _fmt_walk_key(w.key), w.wtype,
                                                            "xy"[i % 2], r.in_regs[i],
                                                            _fmt_walk_key(r.key), r.wtype))
        drawn = [r for r in ref.values() if r.wtype not in _CONTAINERS] or list(ref.values())
        ox = self.at["x"] - min(r.box[0] for r in drawn)
        oy = self.at["y"] - min(r.box[1] for r in drawn)

        def shifted(box):
            return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)

        for r in drawn:
            b = shifted(r.box)
            if min(b[0], b[2]) < 0 or max(b[0], b[2]) > _CANVAS_W \
                    or min(b[1], b[3]) < 0 or max(b[1], b[3]) > _CANVAS_H:
                raise RecipeError("OFF_CANVAS", "%s (wtype %d %r) would sit at (%d,%d,%d,%d), "
                                  "outside the %dx%d canvas" % (_fmt_walk_key(r.key), r.wtype,
                                                                r.label, *b, _CANVAS_W,
                                                                _CANVAS_H))
        touches = [shifted(r.box) for r in ref.values() if r.wtype == 12]
        for p in dst_pairs:
            for t in before[self._pair_id(p)]:
                if t.wtype != 12 or _moved_step(t.key, frm, removed) is not None:
                    continue
                hit = next((b for b in touches if _overlaps(b, t.box)), None)
                if hit is not None:
                    raise RecipeError("TOUCH_OVERLAP", "the moved touch region at (%d,%d,%d,%d) "
                                      "meets root %d's %s at (%d,%d,%d,%d)"
                                      % (*hit, p[0], _fmt_walk_key(t.key), *t.box))
        payload = self._paste_bytes(data, recs, moved_idx, ref, {r.key: r for r in ref_walk},
                                    (ox, oy))

        # 3. remove
        cur, _ = _remove_records(data, ctx, frm, self.records, oid)
        remaps = {pid: {r.key: pagewalk.remap_after_removal(r.key, frm, removed) for r in walk}
                  for pid, walk in before.items()}

        # 4. compensate the source
        cur, edits = self._compensate(cur, ctx, frm, src_pairs, before, remaps,
                                      len(recs) - len(removed))

        # 5. paste
        gs, tab = _gs_tab(cur)
        n_to = len(records.object_records(cur, gs + tab[to]))   # the attach lands at the end
        new_index = len(tab)                                     # the appended object
        try:
            cur, ins_edits = insert.insert_object(cur, payload, to)
        except (insert.InsertError, relocate.RelocationError) as e:
            raise RecipeError("INSERT_REFUSED", str(e))
        ctx.edits.extend(ins_edits)
        cur, _ = patch.reseal(cur)
        assert len(_gs_tab(cur)[1]) == new_index + 1

        # 6. measure after and assert
        self._assert_after(data, cur, frm, removed, moved_idx, src_pairs, dst_pairs, before,
                           remaps, ref, (ox, oy), (to, n_to), new_index)

        ctx.handles[oid] = {
            "from": frm, "to": to, "object": new_index,
            "touch_ids": [_touch_id(data, recs[i]) for i in moved_idx if recs[i].wtype == 12],
            "at": [self.at["x"], self.at["y"]], "moved": len(moved_idx),
            "compensated": edits}
        return cur

    def _compensate(self, data, ctx, frm, src_pairs, before, remaps, surviving):
        """Step 4: restore every surviving source record's box, one geometry edit per pass.
        Returns the image and the number of geometry fields written."""
        written = 0
        for _ in range(surviving + 1):
            walk_deltas = []
            for p in src_pairs:
                pid = self._pair_id(p)
                deltas = pagewalk.diff(before[pid], self._walk(data, p), remaps[pid])
                states = [d for d in deltas if d.kind == "state"]
                if states:
                    raise RecipeError("STATE_CHANGED", "root %d: the removal changed what %d "
                                      "surviving record(s) inherit (font, align, colours or "
                                      "value_slot); move_control does not restyle them:\n%s"
                                      % (p[0], len(states), pagewalk.describe(states)))
                other = [d for d in deltas if d.kind in ("gone", "appeared")]
                if other:
                    raise RecipeError("LAYOUT_MOVED", "root %d: the removal changed which "
                                      "records the page draws:\n%s"
                                      % (p[0], pagewalk.describe(other)))
                walk_deltas.append(deltas)
            target = _compensation_target(walk_deltas, frm)
            if target is None:
                return data, written
            index, dx, dy = target
            if dx == 0 and dy == 0:
                raise RecipeError("CANNOT_COMPENSATE", "object %d record %d changed size, not "
                                  "position; an x/y edit cannot restore it" % (frm, index))
            gs, tab = _gs_tab(data)
            rec = records.object_records(data, gs + tab[frm])[index]
            buf = bytearray(data)
            for gi, d in ((0, dx), (1, dy)):
                if not d:
                    continue
                off = records.geom_offset(data, rec, gi)
                if off is None:
                    raise RecipeError("CANNOT_COMPENSATE", "object %d record %d (wtype %d) moved "
                                      "by (%d,%d) and has no %s field to put it back in"
                                      % (frm, index, rec.wtype, dx, dy, "xy"[gi]))
                old = struct.unpack_from("<H", data, off)[0]
                _put16(buf, off, (old - d) & 0xFFFF, ctx,
                       "move_control compensate obj %d rec %d %s -= %d" % (frm, index,
                                                                            "xy"[gi], d))
                written += 1
            data, _ = patch.reseal(bytes(buf))
        raise RecipeError("CANNOT_COMPENSATE", "object %d: the source still moves after %d "
                          "compensation pass(es)" % (frm, surviving + 1))

    def _assert_after(self, data, out, frm, removed, moved_idx, src_pairs, dst_pairs, before,
                      remaps, ref, offset, attach_step, new_index):
        """Step 6 (see the class docstring)."""
        ox, oy = offset
        refs = {}

        def reference(pair):
            # the control as the SOURCE draws it under this pair's switch values (its first
            # root), falling back to the base walk when that arm does not draw it
            pid = self._pair_id((src_pairs[0][0], pair[1], pair[2]))
            if pid not in refs:
                walk = before.get(pid) or self._walk(data, (src_pairs[0][0], pair[1], pair[2]))
                refs[pid] = (self._subtree(walk, frm, removed) or ref,
                             {r.key: r for r in walk})
            return refs[pid]

        dst_ids = {self._pair_id(p) for p in dst_pairs}
        seen = set()
        for p in src_pairs + dst_pairs:
            pid = self._pair_id(p)
            if pid in seen:
                continue
            seen.add(pid)
            after = self._walk(out, p)
            by_key = {r.key: r for r in after}
            deltas = pagewalk.diff(before[pid], after, remaps[pid])
            bad = [d for d in deltas if not (d.kind == "appeared" and attach_step in d.key)]
            if bad:
                raise RecipeError("LAYOUT_MOVED", "root %d: %d record(s) other than the moved "
                                  "control changed:\n%s" % (p[0], len(bad),
                                                            pagewalk.describe(bad)))
            exp, exp_keys = reference(p)
            pasted = {}
            for d in deltas:
                a = d.after
                n = a.key.index(attach_step)
                if len(a.key) == n + 1:
                    continue                                  # the attach insert_object adds
                o, j = a.key[n + 1]
                if o != new_index or j >= len(moved_idx):
                    raise RecipeError("LAYOUT_MOVED", "root %d: unexpected record %s"
                                      % (p[0], _fmt_walk_key(a.key)))
                pasted.setdefault(a.key[:n], {})[(moved_idx[j],) + a.key[n + 2:]] = a
            if not pasted:
                if pid in dst_ids:
                    raise RecipeError("LAYOUT_MOVED", "root %d does not draw the pasted "
                                      "control" % p[0])
                continue                                      # a source root only
            moved, restyled = [], []
            for got in pasted.values():
                for rel in sorted(set(exp) | set(got)):
                    e, a = exp.get(rel), got.get(rel)
                    if e is None or a is None or e.wtype != a.wtype:
                        moved.append(pagewalk.Delta((a or e).key, (a or e).wtype,
                                                    (a or e).label,
                                                    "gone" if a is None else "appeared", e, a))
                        continue
                    want = (e.box[0] + ox, e.box[1] + oy, e.box[2] + ox, e.box[3] + oy)
                    es = dataclasses.replace(e, value_slot=_effective_slot(exp_keys, e))
                    as_ = dataclasses.replace(a, value_slot=_effective_slot(by_key, a))
                    if pagewalk._state(es) != pagewalk._state(as_):
                        restyled.append(pagewalk.Delta(a.key, a.wtype, a.label, "state", es,
                                                       as_))
                    if a.box != want:
                        moved.append(pagewalk.Delta(a.key, a.wtype, a.label, "moved",
                                                    dataclasses.replace(e, box=want), a,
                                                    a.box[0] - want[0], a.box[1] - want[1]))
            if restyled:
                raise RecipeError("STATE_CHANGED", "root %d: %d pasted record(s) are not "
                                  "styled as on the source (source -> pasted):\n%s"
                                  % (p[0], len(restyled), pagewalk.describe(restyled + moved)))
            if moved:
                raise RecipeError("LAYOUT_MOVED", "root %d: %d pasted record(s) are not at "
                                  "their translated box (expected -> actual):\n%s"
                                  % (p[0], len(moved), pagewalk.describe(moved)))

    def capability_delta(self, ctx):
        # touch ids map to groups globally (FUN_00256B84): no group, binding or DGN changes
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        src_ids = gestures.touch_ids(data, h["from"], tab)
        new_ids = gestures.touch_ids(data, h["object"], tab)
        for tid in h["touch_ids"]:
            assert tid not in src_ids, "touch id %d still on object %d" % (tid, h["from"])
            assert tid in new_ids, "touch id %d not on object %d" % (tid, h["object"])
        assert any(r.wtype == 5 and r.child_index == h["object"]
                   for r in records.object_records(data, gs + tab[h["to"]])), \
            "object %d does not attach object %d" % (h["to"], h["object"])
        return ("move_control: %d record(s) from object %d to object %d at (%d, %d); source "
                "compensated by %d edit(s)"
                % (h["moved"], h["from"], h["to"], h["at"][0], h["at"][1], h["compensated"]))


@op("pin_filter_instance")
class PinFilterInstance(Op):
    """Narrow a receive-filter entry from "any device" to one RV-C instance
    (`statusbind.pin_filter_instance`, one byte at entry `+4`).

    Why this exists: `FUN_0021C760` matches a frame on the 18-bit DGN alone
    (`ubfx r2,r2,#8,#0x12` @`0x0021C930`) — never the source address, never the
    priority — and an entry carrying the `0xFF` sentinel skips its payload compare
    too. So a second device broadcasting the same DGN drives the same widget, and
    the last frame to arrive wins. That is invisible on a coach with one such
    device and becomes a live defect the moment a second one appears.

    Params:
      var: int — variable-table entry index (== slot index, `MCF-FORMAT.md` §5.4c).
      instance: int — the value payload[n] must equal, 0x00..0xFE. `0xFF` is the
                disable sentinel and is refused: this op only ever narrows.
      dgn: int|None (default None) — the DGN the entry is expected to subscribe
           to. Given, it is asserted before the edit, so a project written against
           one build fails loudly rather than pinning whichever entry now sits at
           that index.
      compare: int (default 0) — the entry's existing `+5` byte, asserted, not
               written. Its low nibble picks the payload byte and its high nibble
               the mask; keeping it out of the edit is what holds this to one byte.
      from_instance: int|None (default None) — given, the op re-pins: the entry
               must currently compare exactly this instance, and `instance` replaces
               it (an entry moves from one device to another).

    Error codes:
      FILTER_REFUSED — `statusbind.pin_filter_instance` raised: the entry is
                       already pinned, its DGN is not `dgn`, its `+5` is not
                       `compare`, its extraction mode is one the `tbb` skips (so
                       it paints nothing and narrowing it changes nothing), or
                       `instance` is out of range.

    The trade is real and belongs in the project's note, not hidden here: a pinned
    entry whose device changes instance goes **silent**, where the open one showed
    the wrong device instead. Silent-and-correct beats loud-and-wrong, but it is a
    choice the project author is making.
    """
    PARAMS = {
        "properties": {
            "var": {"type": "integer", "description": "variable-table entry index"},
            "instance": {"type": "integer", "description": "RV-C instance to match, 0x00-0xFE"},
            "dgn": {"type": ["integer", "null"], "default": None,
                    "description": "expected DGN of the entry (asserted, not written)"},
            "compare": {"type": "integer", "default": 0,
                        "description": "expected entry +5 (payload byte + mask selector)"},
            "from_instance": {"type": ["integer", "null"], "default": None,
                              "description": "re-pin: the instance the entry currently "
                                             "compares (asserted); omitted, the entry "
                                             "must be an unpinned 0xFF one"},
        },
        "required": ["var", "instance"],
    }

    def validate(self):
        self.var = self._param("var", kind=int)
        self.instance = self._param("instance", kind=int)
        self.dgn = self._param("dgn", None)
        self.compare = self._param("compare", 0, kind=int)
        self.from_instance = self._param("from_instance", None)

    def apply(self, data, ctx):
        try:
            data, e = statusbind.pin_filter_instance(
                data, self.var, self.instance,
                expect_dgn=self.dgn, expect_compare=self.compare,
                from_instance=self.from_instance)
        except statusbind.StatusBindError as err:
            raise RecipeError("FILTER_REFUSED", str(err), op_id=self.spec.id)
        ctx.edits.extend(e)
        return data

    def verify(self, data, ctx):
        entry = guidata.variable_table(data)[self.var]
        dgn = struct.unpack_from("<I", entry.raw, 0)[0] & statusbind.DGN_MASK
        assert entry.raw[statusbind.FILTER_INSTANCE_FIELD] == self.instance, \
            "var %d instance filter is %#04x" % (
                self.var, entry.raw[statusbind.FILTER_INSTANCE_FIELD])
        if self.dgn is not None:
            assert dgn == self.dgn, "var %d now watches %#07x" % (self.var, dgn)
        assert entry.raw[statusbind.FILTER_COMPARE_FIELD] == self.compare, \
            "var %d compare field moved" % self.var
        return "filter: var %d (%05X) matches payload[%d] == %#04x only" % (
            self.var, dgn, self.compare & 0xF, self.instance)


@op("remove_control")
class RemoveControl(Op):
    """Remove a control from a page WITHOUT touching its bindings: park every
    attach record (wtype-5, `child_index == object`) that any parent object holds
    for it off the 480 px canvas, by rewriting the attach record's `y` field.

    This is deliberately the group-94 pattern the reference build used for channels
    28/29: unreachable but still capable. The control's command
    group and status binding are left completely intact, so `recipe.capabilities`
    is unchanged -- there is no capability delta to declare and none is declared.
    A real detach (freeing the group, retargeting the status cell) is out of scope
    for v1; parking is the same-size, reviewable move.

    Params:
      object: int — the object index to remove (the *child*, not the parent that
              attaches it).
      park_y: int (default 900) — the y coordinate written into every attach
               record's y field. Must be >= 480 (the panel's canvas height) so the
               control cannot be scrolled or reflowed back into view.

    Error codes:
      BAD_PARAMS — park_y < 480 (would not actually leave the visible canvas)
      NO_SUCH_RECORD — no object in the file holds an actual wtype-5 attach record
                      with `child_index == object`. `insert.object_child_graph` is
                      wtype-agnostic (it also surfaces wtype-6/0x2e container-child
                      references), so it is used only as a cheap candidate
                      pre-filter here; the real gate is a hit count over each
                      candidate's own decoded records, matching `MoveElement`'s
                      pattern of counting real hits rather than trusting
                      candidate presence. An object reachable only via a
                      wtype-6/0x2e reference (e.g. a type-6 switch's candidate
                      list) is correctly refused, not silently no-opped.
      NO_FIELD — a parent's attach record for `object` was found but has no
                geometry `y` field to rewrite (unexpected shape; refuse rather
                than silently leaving that parent's attachment visible)

    `hardware_verified = False` — off-canvas parking has never been flashed to the
    coach. `compile_project` collects this into `CompileResult.unverified`, so
    every export surfaces it as a feature the manifest asserts but the hardware
    round-trip has not yet confirmed (the discipline `CLAUDE.md` calls out
    repeatedly: a defensible negative, not a fitted claim of "done").
    """
    hardware_verified = False    # off-canvas parking has never been flashed; the
                                 # manifest flags it so the first flash knows to check

    PARAMS = {
        "properties": {
            "object": {"type": "integer",
                       "description": "object index to remove (the child, not the attaching parent)"},
            "park_y": {"type": "integer", "default": 900,
                      "description": "y coordinate written into every attach record; must be >= 480"},
        },
        "required": ["object"],
    }

    def validate(self):
        self.object = self._param("object", kind=int)
        self.park_y = self._param("park_y", 900, kind=int)
        if self.park_y < 480:
            raise RecipeError("BAD_PARAMS", "park_y must be off the 480 px canvas",
                              op_id=self.spec.id)

    def apply(self, data, ctx):
        # object_child_graph is wtype-agnostic (wtype-6/0x2e container-child refs
        # surface here too), so it is only a candidate pre-filter -- the real gate
        # is the hit count below, over each candidate's own actual wtype-5 records.
        candidates = [p for p, kids in insert.object_child_graph(data).items()
                     if self.object in kids]
        gs, tab = _gs_tab(data)
        buf = bytearray(data)
        hits = []
        for parent in candidates:
            for rec in records.object_records(data, gs + tab[parent]):
                if rec.wtype == 5 and rec.child_index == self.object:
                    off = records.geom_offset(data, rec, 1)
                    if off is None:
                        raise RecipeError("NO_FIELD", "attach record of object %d in "
                                          "parent %d has no y field"
                                          % (self.object, parent))
                    _put16(buf, off, self.park_y, ctx,
                           "park object %d off-canvas (y=%d)" % (self.object,
                                                                 self.park_y))
                    hits.append(parent)
        if not hits:
            raise RecipeError("NO_SUCH_RECORD", "object %d has no wtype-5 attach "
                              "record with child_index == %d in any object -- it "
                              "is not a page-attached control this op can park "
                              "(it may be reachable only via a wtype-6/0x2e "
                              "container-child reference, which carries no "
                              "geometry to park)" % (self.object, self.object))
        ctx.handles[self.spec.id] = {"parents": sorted(set(hits))}
        out, _ = patch.reseal(bytes(buf))
        return out

    def verify(self, data, ctx):
        parents = ctx.handles[self.spec.id]["parents"]
        gs, tab = _gs_tab(data)
        hits = 0
        for parent in parents:
            for rec in records.object_records(data, gs + tab[parent]):
                if rec.wtype == 5 and rec.child_index == self.object:
                    off = records.geom_offset(data, rec, 1)
                    assert struct.unpack_from("<H", data, off)[0] == self.park_y
                    hits += 1
        assert hits > 0, "no wtype-5 attach record parked for object %d -- apply " \
                         "should have raised NO_SUCH_RECORD before reaching " \
                         "verify" % self.object
        return ("removed (parked off-canvas): object %d — bindings left intact "
                "(unreachable-but-capable, the group-94 pattern); HARDWARE-UNVERIFIED"
                % self.object)


@op("add_low_volts_control")
class AddLowVoltsControl(Op):
    """Copy the AGS Trigger section's 'Low Volts' arm onto the AES ELECTRICAL view.

    ELECTRICAL is screen object 5; it switches on slot 7 (derived from the AGS/AES mode
    by GuiCode `FUN_00247BF8`): slot7=0 -> object **829** (the AES sub-view), slot7=1 ->
    object 830 (the AGS/generator sub-view). When the coach's generator option is
    AES-selected the panel forces slot7=0 and draws object 829, which stock leaves with
    no way to arm the **Low Volts** auto-start trigger (config selector `0x2F`). That
    control exists only on the AGS Trigger panel, object **315** (a child of object 830),
    where a 'Triggers:' title sits above a 'Low Volts' toggle button.

    This op reproduces just that title + button on the AES view. It appends ONE new
    object (via `insert._append_object`/`insert_object`, the relocating child-attach path
    that grows object-table 1 by a single entry without disturbing any existing
    `child_index`) carrying three records:

      1. a **'Triggers:'** title (wtype-11) authored from donor 315's own title record --
         its font, text, width/height and R49 line-layout bytes are copied verbatim, but
         the coordinates are rewritten to be **absolute** (base-modes 0, an explicit x/y,
         and the register-3 read that positioned it inside object 315 cleared). The
         title also carries a non-zero (left) align byte so it RESETS the persistent
         align state -- object 829 ends its walk with `state.align == 3` (right-justify),
         which would otherwise drag every record of an appended child off-position (the
         persistent-align trap, `layout._placed_label_box`);
      2. a clean **absolute wtype-5 attach** (base-modes 0, no register refs -- object
         829's own rec[18]/[19] template) pointing at the SAME child object 315 uses for
         the button face (object **285**, which draws the box + the 'Low\\nVolts' label).
         That child is byte-shared, never cloned;
      3. the **wtype-12 touch region carrying touch id 129**, copied byte-for-byte from
         object 315. The touch-id -> group map is GLOBAL (`FUN_00256B84` copies the id
         into the hit-region table regardless of the hosting screen), so id 129 still
         resolves to `group_table[129]` -- start=447, count=4, whose bindings write
         `0x1FED9`/selector `0x2F` (447: `0x2F:=0`, 448: `0x2F:=1`; 449/450 the read-back
         twins). **No binding-table edit, no new group, no new DGN registration**: the
         0x2F write already exists in stock and is merely referenced from a second page.

    Only the title + button come across. The AGS panel's 'AGS' heading, its D/L glyph
    icons and its other touch regions (the GEN toggle) are deliberately left behind.

    Params:
      screen: int (default 829) -- target screen object (the AES ELECTRICAL sub-view).
      title_x, title_y: int (default 600, 30) -- absolute position of the 'Triggers:'
              title within the screen.
      button_x, button_y: int (default 615, 62) -- absolute position of the Low Volts
              button (its box, label and touch region, which coincide). Defaults land the
              control in the empty top-right quadrant of the AES page, clear of every
              existing widget -- geometry is flow-relative so the placement was verified
              by rendering, not assumed (`build/experiment-images/aes-after.png`).
      donor: int (default 315) -- the AGS Trigger panel object to source records from.

    Error codes:
      NO_TRIGGER_TITLE -- the donor has no wtype-11 'Triggers:' label to author from.
      NO_LOW_VOLTS_TOUCH -- the donor has no wtype-12 region whose id is `touch_id` (129).
      NO_LOW_VOLTS_CHILD -- the donor has no wtype-5 record whose child draws the
                            'Low\\nVolts' button face.
      INSERT_REFUSED -- `insert.insert_object` refused (bad parent, no attach template, or
                        a correctness gate failed).

    Capability: none. `recipe.capabilities` tracks only (G12 channel, wire command) pairs;
    group 129's bindings command no channel (they are `0x1FED9` config writes, channel is
    None), so they never appear in the capability set and reusing the group adds nothing to
    it. `capability_delta` is honestly `(set(), set())`, and the compile's capability gate
    confirms stock and output express exactly the same pairs.

    Behavioural caveat for the owner to watch on the coach: group 129 is the
    **external-variant** form (group byte1 `0x80`) -- the panel picks binding 447 (`0x2F:=0`)
    or 448 (`0x2F:=1`) from external state (the 0x2F value the G12 echoes), not from a local
    press counter. It is the exact group the shipped AGS button drives, reused verbatim, so
    the AES button behaves identically -- but it has never been exercised from this page, so
    the first arm/disarm from the AES view is worth confirming toggles both ways.

    This is a relocating op. Registers `ctx.handles[op_id] = {"object": idx}` so a later op
    can address the new control via `{"ref": op_id}`.
    """

    PARAMS = {
        "properties": {
            "screen": {"type": "integer", "default": 829,
                       "description": "target screen object (the AES ELECTRICAL sub-view)"},
            "title_x": {"type": "integer", "default": 600,
                        "description": "absolute x of the 'Triggers:' title"},
            "title_y": {"type": "integer", "default": 30,
                        "description": "absolute y of the 'Triggers:' title"},
            "button_x": {"type": "integer", "default": 615,
                         "description": "absolute x of the Low Volts button (box/label/touch)"},
            "button_y": {"type": "integer", "default": 62,
                         "description": "absolute y of the Low Volts button (box/label/touch)"},
            "donor": {"type": "integer", "default": 315,
                      "description": "AGS Trigger panel object to source records from"},
            "button_w": {"type": ["integer", "null"], "default": None,
                         "description": "button width; None (default) reuses the shared 105x50 "
                                        "child 285 via a wtype-5 attach (byte-identical to before). "
                                        "Setting it (or button_h/label_font) shrinks the control by "
                                        "authoring an absolute box+label on the AES side instead"},
            "button_h": {"type": ["integer", "null"], "default": None,
                         "description": "button height; None reuses 105x50 (see button_w)"},
            "label_font": {"type": ["integer", "null"], "default": None,
                           "description": "font slot for the 'Low Volts' label; None keeps font 14 "
                                          "(reuses shared child 285). Setting it shrinks the text"},
            "title_font": {"type": ["integer", "null"], "default": None,
                           "description": "font slot for the 'Triggers:' title; None keeps the "
                                          "donor's font (14)"},
            "title_text": {"type": ["string", "null"], "default": None,
                           "description": "override the title text; None keeps the donor's "
                                          "'Triggers:'. The donor is still located by 'Triggers:'"},
            "title_center": {"type": "boolean", "default": False,
                             "description": "when true, ignore title_x and centre the title "
                                            "horizontally over the button face"},
        },
        "required": [],
    }

    TITLE_TEXT = "Triggers:"
    TOUCH_ID = 129            # -> group_table[129], the 0x2F config-write toggle (GLOBAL map)
    LOW_VOLTS_LABEL = "Volts"  # substring identifying the button face child (object 285)

    def validate(self):
        self.screen = self._param("screen", 829, kind=int)
        self.title_x = self._param("title_x", 600, kind=int)
        self.title_y = self._param("title_y", 30, kind=int)
        self.button_x = self._param("button_x", 615, kind=int)
        self.button_y = self._param("button_y", 62, kind=int)
        self.donor = self._param("donor", 315, kind=int)
        self.button_w = self._opt_int("button_w")
        self.button_h = self._opt_int("button_h")
        self.label_font = self._opt_int("label_font")
        self.title_font = self._opt_int("title_font")
        self.title_text = self._param("title_text", None)
        if self.title_text is not None and not isinstance(self.title_text, str):
            raise RecipeError("BAD_PARAMS", "add_low_volts_control: title_text wants a string",
                              op_id=self.spec.id)
        self.title_center = bool(self._param("title_center", False))
        # A shrink is requested when any button-face size/font override is set; then we author
        # an absolute box+label on the AES side rather than attaching the SHARED child 285.
        self.shrink = (self.button_w is not None or self.button_h is not None
                       or self.label_font is not None)

    def _opt_int(self, name):
        v = self._param(name, None)
        if v is not None and not isinstance(v, int):
            raise RecipeError("BAD_PARAMS", "add_low_volts_control: param %r wants int or null, "
                              "got %r" % (name, v), op_id=self.spec.id)
        return v

    def _title_record(self, data, r7, x, y, font=None, text=None):
        """Author a wtype-11 'Triggers:' record from donor `r7`, made absolute-anchored and
        placed at (x, y). Font, text and the four R49 line-layout bytes are the donor's own
        (font overridable via `font`); the box width is sized to the measured text (donor `r7`'s
        220px box was for its own AGS layout and, made pen-relative below, would run its right
        edge to x+220 -- past the 'Start at' +/- column at x=320). The header is rebuilt so:

          * base-modes are **[0,0,1,1]** (b1=0x50): geom[0]/[1] (x, y) are absolute, geom[2]/[3]
            are pen-relative -- ADDED to this record's own placed x/y, so they read as WIDTH and
            HEIGHT and the box lands at (x, y)-(x+w, y+h). This is the exact encoding the shrink-
            path box (`_box_record`) and label (`_label_record`) use, and which object 829's own
            wtype-11 '  AES'/'Battery\\nVoltage:' labels use -- i.e. the confirmed-on-coach form.
            **BUG (fixed 2026-08-25):** the header shipped base-modes [0,0,0,0] (b1=0x00), which
            made geom[2]/[3] ABSOLUTE, so the decoder read w=220/h=26 as x1=220/y1=26 -> an
            inverted box (y1 < y0). The sim renderer drew it anyway; the coach drew NOTHING.
          * geom[0] is present (an explicit x) and the align byte is 1 (left, and NON-zero so it
            resets the persistent align state -- object 829 ends its walk at state.align==3, which
            would otherwise drag every record of an appended child off-position)."""
        label = text if text is not None else r7.label
        text_bytes = label.encode("ascii")
        use_font = font if font is not None else r7.font
        # Size the box to the text (+ a small right pad) rather than the donor's 220px, so the
        # pen-relative right edge (x + w) clears the 'Start at' +/- column at x=320.
        w = metrics.text_width(data, use_font, label) + 12
        h = r7.geom[3]
        r39 = data[r7.font_off + 1]          # the byte the decoder reads as R39 (0xff on stock)
        # header b0..b7: t=11 with a18 present (0x4b); b1=0x50 (base-modes [0,0,1,1] -> x/y
        # absolute, w/h pen-relative); b2=0x0f (geom0..3 present); b3 align bits = 1 (left,
        # non-zero: writes state.align).
        hdr = bytes([0x4b, 0x50, 0x0f, 0x42, 0x00, 0x00, 0x00, 0x00])
        a18 = bytes([0x00])                  # in_regs/out_regs all 0 (drops the reg-3 x read)
        geom = struct.pack("<HHHH", x, y, w, h)
        tail = bytes([0x01, font if font is not None else r7.font, r39,  # R37 count, R38 font, R39
                      r7.h_align, r7.v_align, r7.line_height, r7.line_height_adds_font])  # R49
        body = bytes([len(text_bytes)]) + text_bytes + b"\x00"
        return hdr + a18 + geom + tail + body

    @staticmethod
    def _box_record(x, y, w, h, fill_cell, style, pad_slot5=False, value_slot=None):
        """Author an absolute wtype-33 button-face box, cloned from child 285's rec[0]
        (`21 55 0c 07 | fillA(mode7 cell) | w | h | style`). Base-modes are rewritten to
        [0,0,1,1]: geom[0]/[1] (x, y) are absolute (mode 0) while geom[2]/[3] stay mode 1 --
        added to the record's own placed x/y, so they read as WIDTH/HEIGHT and the box lands
        at (x, y)-(x+w, y+h). geom[4] (style 5) and the fillA colour cell are 285's own, so
        the face is byte-identical to the shared child, just at the requested size.

        `value_slot` (when set) adds the R42 value-slot field (header byte4 bit 0x20), so the box
        NAMES the same status cell its fillA mode-7 index resolves to. On the AGS panel child 285
        is wrapped by a wtype-5 switch (obj 315 rec[8]) carrying `value_slot=45`, which 285's box
        and caption INHERIT -- that binding is what puts the fill's colour cell in the live-redraw
        set (OBJECT-HIERARCHY.md 1a; the pin-15 v7->v8 coach fix). The shrink path draws the box
        inline (no wtype-5 parent), so it must carry the value-slot itself to satisfy the invariant
        `fillA mode-7 index == box value_slot == caption value_slot`.

        `pad_slot5` adds geom slot 5 (=0) purely to lengthen the record by 2 bytes. For a
        generic-frame type the layout engine's frame is `(x0, y0, x1+1, y1)` -- it never reads
        geom[5] -- so the extra coord is inert; `apply` uses it only to nudge the appended
        object off a length that trips the relocator's GuiCode-disassembly-stability gate."""
        # b1=0x50 -> base-modes [0,0,1,1]; b2=0x0f -> geom0..3 present; b4 0x20 -> R42 value-slot
        # present; b6 -> geom4 (0x10) [+geom5].
        b6 = 0x30 if pad_slot5 else 0x10
        b4 = 0x20 if value_slot is not None else 0x00
        hdr = bytes([0x21, 0x50, 0x0f, 0x07, b4, 0x00, b6, 0x00])
        body = (struct.pack("<H", fill_cell)             # colour fillA (mode 7) read first
                + struct.pack("<HHHH", x, y, w, h)       # geom slots 0,1,2,3
                + struct.pack("<H", style))              # geom slot 4 (style)
        if pad_slot5:
            body += struct.pack("<H", 0)                 # geom slot 5 (inert length pad)
        if value_slot is not None:                       # R42 value-slot (decoded after geom)
            body += struct.pack("<H", value_slot)
        return hdr + body

    @staticmethod
    def _label_record(text, x, y, w, h, font, r39, h_align, v_align, line_height, adds,
                      value_slot=None):
        """Author an absolute wtype-11 'Low\\nVolts' label, cloned from child 285's rec[1] but
        with base-modes [0,0,1,1] (like `_box_record`) so its box is (x, y)-(x+w, y+h) and the
        h_align/v_align centring lands the two-line text inside the smaller face. Font is the
        requested `label_font`; align byte (b3>>6) is 0, so it does NOT disturb the persistent
        align the title just reset.

        `value_slot` (when set) adds the R42 value-slot field (header byte4 bit 0x20). The
        live-redraw invariant (OBJECT-HIERARCHY.md 1a) wants the caption to name the SAME status
        cell as the box's fillA mode-7 index; giving only the box a value-slot repaints the box
        but drops the caption on a status change (the pin-15 v7 defect). Both carry it here."""
        tb = text.encode("ascii")
        # b0=0x0b (no aux bytes); b1=0x50 base-modes [0,0,1,1]; b2=0x0f geom0..3; b3=0x2a
        # (fillA/fillB modes as 285's rec[1], both inherit; align bits 0); b4 0x20 -> R42 value-slot.
        b4 = 0x20 if value_slot is not None else 0x00
        hdr = bytes([0x0b, 0x50, 0x0f, 0x2a, b4, 0x00, 0x00, 0x00])
        geom = struct.pack("<HHHH", x, y, w, h)
        vslot = struct.pack("<H", value_slot) if value_slot is not None else b""
        # decode order: R37 count, R38 font, R39, [R42 value_slot], R49(h/v-align,line-height,adds).
        tail = bytes([0x01, font, r39]) + vslot + bytes([h_align, v_align, line_height, adds])
        body = bytes([len(tb)]) + tb + b"\x00"
        return hdr + geom + tail + body

    @staticmethod
    def _attach_record(x, y, child, value_slot=None):
        """An absolute wtype-5 container record placing `child` at (x, y). Byte-shape of object
        829's own rec[18]/[19] attach template: header `05 50 03` + geom(x, y) + font-inherit
        0xff + child index.

        `value_slot` (when set) adds the R42 value-slot field (header byte4 bit 0x20), placing it
        between the 0xff font byte and the child index -- the exact byte shape of the AGS panel's
        own Low-Volts attach (obj 315 rec[8]: `05 55 03 00 20 .. .. ff 2d00 1d01`, value_slot 45).
        The child (285) inherits that value-slot on its box and caption, so the fillA mode-7 colour
        cell is in the live-redraw set (OBJECT-HIERARCHY.md 1a); without it the button's colour is
        an unbound cell that never repaints on this page."""
        b4 = 0x20 if value_slot is not None else 0x00
        vslot = struct.pack("<H", value_slot) if value_slot is not None else b""
        return (bytes([0x05, 0x50, 0x03, 0x00, b4, 0x00, 0x00, 0x00])
                + struct.pack("<HH", x, y) + bytes([0xFF]) + vslot + struct.pack("<H", child))

    def apply(self, data, ctx):
        gs, tab = _gs_tab(data)
        drecs = records.object_records(data, gs + tab[self.donor])

        titles = [r for r in drecs if r.wtype == 11
                  and (r.label or "").strip() == self.TITLE_TEXT]
        if not titles:
            raise RecipeError("NO_TRIGGER_TITLE", "donor %d has no wtype-11 %r label"
                              % (self.donor, self.TITLE_TEXT), op_id=self.spec.id)
        r7 = titles[0]

        touches = [r for r in drecs if r.wtype == 12
                   and (data[r.end - 2] | (data[r.end - 1] << 8)) == self.TOUCH_ID]
        if not touches:
            raise RecipeError("NO_LOW_VOLTS_TOUCH", "donor %d has no wtype-12 region with "
                              "touch id %d" % (self.donor, self.TOUCH_ID), op_id=self.spec.id)
        touch_bytes = bytes(data[touches[0].offset:touches[0].offset + touches[0].size])

        child = None
        for r in drecs:
            if r.wtype == 5 and r.child_index is not None and r.child_index < len(tab):
                crecs = records.object_records(data, gs + tab[r.child_index])
                if any(self.LOW_VOLTS_LABEL in (getattr(cr, "label", None) or "")
                       for cr in crecs):
                    child = r.child_index
                    break
        if child is None:
            raise RecipeError("NO_LOW_VOLTS_CHILD", "donor %d has no wtype-5 child drawing a "
                              "%r label" % (self.donor, self.LOW_VOLTS_LABEL),
                              op_id=self.spec.id)

        # The button face fills via fillA mode-7 -> colour cell N (child 285's box). Binding that
        # SAME cell N as the box/caption value_slot is the live-redraw invariant
        # (OBJECT-HIERARCHY.md 1a): cell N here is 45 -> SRAM 0x2003a814, a status cell written by
        # var 42's GuiCode bridge block (the 0x2F Low-Volts flag, BUS-BASELINE PC.39). Reading it
        # once lets BOTH paths bind it -- default: on the wtype-5 attach (obj 315 rec[8]'s own
        # shape), which child 285's box+caption inherit; shrink: on the inline box+label.
        _cbox = next((cr for cr in records.object_records(data, gs + tab[child])
                      if cr.wtype == 33), None)
        face_cell = (next((v for (role, mode, v) in _cbox.colors
                           if role == "fillA" and v is not None), None)
                     if _cbox is not None else None)

        # Title x: either the caller's absolute title_x, or (title_center) centred over the button
        # face. Face width is the shared child's box width on the default path, button_w on shrink.
        title_x = self.title_x
        if self.title_center:
            face_w = (self.button_w if (self.shrink and self.button_w is not None)
                      else (_cbox.geom[2] if _cbox is not None else 105))
            use_font = self.title_font if self.title_font is not None else r7.font
            label = self.title_text if self.title_text is not None else r7.label
            tw = metrics.text_width(data, use_font, label) + 12
            title_x = self.button_x + face_w // 2 - tw // 2
        title = self._title_record(data, r7, title_x, self.title_y,
                                   font=self.title_font, text=self.title_text)

        if self.shrink:
            # SHRINK PATH -- gather the box/label templates once (used to author the inline face).
            crecs = records.object_records(data, gs + tab[child])
            boxes = [cr for cr in crecs if cr.wtype == 33]
            labels_ = [cr for cr in crecs if cr.wtype == 11
                       and self.LOW_VOLTS_LABEL in (getattr(cr, "label", None) or "")]
            if not boxes or not labels_:
                raise RecipeError("NO_LOW_VOLTS_CHILD", "child %d lacks the wtype-33 box + "
                                  "wtype-11 label to author from" % child, op_id=self.spec.id)
            box_r, lbl_r = boxes[0], labels_[0]
            fill_cell = next((v for (role, mode, v) in box_r.colors
                              if role == "fillA" and v is not None), 0)
            style = box_r.geom[4] or 0
            bw = self.button_w if self.button_w is not None else box_r.geom[2]
            bh = self.button_h if self.button_h is not None else box_r.geom[3]
            lfont = self.label_font if self.label_font is not None else lbl_r.font
            r39 = data[lbl_r.font_off + 1]
            # Match the touch hit area to the smaller visual: rewrite its w/h geom words (touch id
            # 129, byte '81 00', is untouched -- it is past the geometry, at record end).
            t0 = touches[0]
            tb = bytearray(touch_bytes)
            struct.pack_into("<H", tb, records.geom_offset(data, t0, 2) - t0.offset, bw)
            struct.pack_into("<H", tb, records.geom_offset(data, t0, 3) - t0.offset, bh)
            touch_bytes = bytes(tb)

        def build_payload(box_pad):
            if not self.shrink:
                # DEFAULT PATH -- a wtype-5 attach pointing at the SHARED child 285 (105x50), which
                # is never cloned or resized. The attach now carries value_slot=face_cell (obj 315
                # rec[8]'s shape), so 285's box+caption inherit the live-redraw cell binding.
                face = self._attach_record(self.button_x, self.button_y, child,
                                           value_slot=face_cell)
                rec_count = 3                       # title + attach + touch
            else:
                # Author the box + 'Low Volts' label INLINE at the requested size on the AES side
                # (child 285 is byte-shared with the AGS page, so it must not be resized in place).
                # Colour cell, style, R49 line-layout and the label text are cloned from 285; only
                # geom w/h and the label font change. Both records carry value_slot=fill_cell so the
                # fill's colour cell is in the live-redraw set (the live-redraw invariant; without it
                # the button's colour is an unbound cell that never repaints on this page). `box_pad`
                # lengthens the box by an inert geom slot when the natural object length trips the
                # relocator's disassembly gate below.
                box_rec = self._box_record(self.button_x, self.button_y, bw, bh, fill_cell, style,
                                           pad_slot5=box_pad, value_slot=fill_cell)
                label_rec = self._label_record(lbl_r.label, self.button_x, self.button_y, bw, bh,
                                               lfont, r39, lbl_r.h_align, lbl_r.v_align,
                                               lbl_r.line_height, lbl_r.line_height_adds_font,
                                               value_slot=fill_cell)
                face = box_rec + label_rec
                rec_count = 4                       # title + box + label + touch
            payload = bytes([rec_count]) + title + face + touch_bytes
            return payload + b"\x00" * (-len(payload) % 4)   # object offsets are 4-aligned

        # `insert_object` appends the object then splices a wtype-5 attach into the screen; that
        # second splice lands at an offset determined by the appended object's length, and one
        # unlucky length pushes it onto a GuiCode literal whose relocated bytes desync capstone's
        # disassembly, tripping relocate's (conservative, correctness-preserving) stability gate.
        # It is purely a length collision, so on the shrink path we retry with the box padded by
        # one inert geom slot (a different, safe object length). The default path is never padded.
        last_err = None
        for box_pad in ((False, True) if self.shrink else (False,)):
            try:
                # insert_object's attach step calls relocate.relocate directly, so the stability
                # gate surfaces as RelocationError; the append step wraps it as InsertError.
                data2, e = insert.insert_object(data, build_payload(box_pad), self.screen,
                                                position=(self.button_x, self.button_y))
                data, last_err = data2, None
                break
            except (insert.InsertError, relocate.RelocationError) as err:
                last_err = err
                if not (self.shrink and "disassembly is not stable" in str(err)):
                    break
        if last_err is not None:
            raise RecipeError("INSERT_REFUSED", str(last_err), op_id=self.spec.id)
        ctx.edits.extend(e)
        data, _ = patch.reseal(data)
        ctx.handles[self.spec.id] = {"object": len(guidata.object_tables(data)[1]) - 1,
                                     "child": child, "group": self.TOUCH_ID}
        return data

    def capability_delta(self, ctx):
        # Reuses the GLOBAL group 129 (a 0x1FED9/0x2F config write, channel None). Config
        # writes carry no G12 channel, so they are outside `recipe.capabilities`'s (channel,
        # command) set entirely -- nothing is gained or lost, and the gate confirms it.
        return set(), set()

    def verify(self, data, ctx):
        idx = ctx.handles[self.spec.id]["object"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])
        want_title = (self.title_text if self.title_text is not None else self.TITLE_TEXT).strip()
        assert any(r.wtype == 11 and (r.label or "").strip() == want_title
                   for r in recs), "new object %d lost its %r title" % (idx, want_title)
        w12 = [r for r in recs if r.wtype == 12]
        ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in w12]
        assert self.TOUCH_ID in ids, \
            "new object %d does not carry touch id %d (has %s)" % (idx, self.TOUCH_ID, ids)
        # The touch region (on the new object) must overlap the button face (the wtype-33 box),
        # or the arm is unreachable. The box is drawn by the SHARED child 285 on the default
        # path; on the shrink path it is authored INLINE on the new object at the smaller size.
        child = ctx.handles[self.spec.id]["child"]
        box_obj = idx if self.shrink else child
        placed = layout.Screen(data).walk(self.screen)
        touch_box = next(((p.x0, p.y0, p.x1, p.y1) for p in placed
                          if p.obj_index == idx and p.wtype == 12 and p.x0 is not None), None)
        button_box = next(((p.x0, p.y0, p.x1, p.y1) for p in placed
                           if p.obj_index == box_obj and p.wtype == 33 and p.x0 is not None), None)
        assert touch_box is not None and touch_box == button_box, \
            "touch region %s does not cover the button box %s" % (touch_box, button_box)
        if self.shrink:
            # The requested size must be exactly what was placed (box and touch agree, above).
            want_w = self.button_w if self.button_w is not None else 105
            want_h = self.button_h if self.button_h is not None else 50
            got_w, got_h = button_box[2] - button_box[0], button_box[3] - button_box[1]
            assert (got_w, got_h) == (want_w, want_h), \
                "shrunk button is %dx%d, requested %dx%d" % (got_w, got_h, want_w, want_h)
        # Group 129 must be exactly as stock left it -- no binding edit, no new group.
        grp = guidata.group_table(data)[self.TOUCH_ID]
        bt = guidata.binding_table(data)
        sels = [bt[j].raw[5] for j in range(grp.start, grp.start + grp.count)]
        vals = [bt[j].raw[11] for j in range(grp.start, grp.start + grp.count)]
        assert grp.start == 447 and grp.count == 4 and set(sels) == {0x2F}, \
            "group %d moved/changed: start=%d count=%d selectors=%s" % (
                self.TOUCH_ID, grp.start, grp.count, [hex(s) for s in sels])
        return ("low-volts control: object %d on screen %d -- 'Triggers:' + Low Volts "
                "(touch id %d -> group 129, config 0x2F:={%d,%d}); no binding/group edits, "
                "external-variant toggle reused from AGS verbatim"
                % (idx, self.screen, self.TOUCH_ID, vals[0], vals[1]))


@op("follow_channel")
class FollowChannel(Op):
    """The gated light->pump follower (`followchannel.py`): watch one G12 output's status
    bit, and on every ON/OFF transition send a CAN frame that mirrors it onto another
    channel -- but only while a repurposed OPTIONS row is enabled, so the coupling can be
    switched off from the panel without a re-flash.

    Wires three already hardware-proven primitives together, in order:

      1. Rename the gate option's OPTIONS row caption (same-size, OBJECT-SCOPED to
         object 46 -- the file's OPTIONS page -- so the CLIMATE mode-column's own
         "Heat Pump" captions on OTHER objects are left untouched). This step runs
         FIRST, against the untouched stock caption text, because step 2 derives the
         gate register by NAME: renaming after would make `gate_option` unfindable.
      2. Derive the plan (`followchannel.resolve`, against the PRE-rename image, for
         the reason above) and author the routine + templates (`followchannel.build`)
         at the GuiCode section's true append point, then append it
         (`growsection.grow_section`, which reseals descriptor + section checksums).
      3. Retarget the single stock caller (`followchannel.detour_edit`) to the new
         routine -- a same-size 4-byte edit at a now-fixed GuiCode file offset -- and
         reseal again (the detour invalidates the GuiCode section checksum a second
         time; `grow_section`'s own reseal ran before this edit existed).

    Params:
      watch_channel: int — G12 output instance whose status bit is watched
      target_channel: int — G12 output instance the follower drives
      on_level: int (default 200) — level byte the on-frame carries
      gate_option: str (default "Heat Pump") — the STOCK caption of the OPTIONS row
                   that gates transmission (object 46); also the text the rename looks
                   for
      gate_label: str (default "Pump Link") — the row's new caption; same length as
                  `gate_option` is required (9 chars <-> 9 chars for the defaults) --
                  this op only ever makes a same-size rename

    Error codes:
      NO_SUCH_LABEL — object 46 has no label field containing `gate_option`
      AMBIGUOUS_LABEL — object 46 has more than one field containing `gate_option`
      LABEL_TOO_LONG — `gate_label` does not fit the field `gate_option` was found in
      FOLLOW_REFUSED — `followchannel.resolve`/`build` could not derive an address or
                       author the routine (an unrecoverable option/status join, or the
                       stock hook byte was not the expected `bl`)
      UNEXPECTED_APPEND_POINT — the GuiCode section's append point is below the stock
                                13.6 address (0x00259BB4) or misaligned; refuses rather than
                                build against an image this op was never proven on. A point
                                BEYOND it (earlier appends) is fine: the routine chains
      GROW_REFUSED — `growsection.grow_section` refused (misaligned payload, or a
                     descriptor field did not hold the expected pre-grow value)
      DETOUR_REFUSED — the bytes at the detour's file offset were not the stock `bl`
                       this plan expects (the grow shifted something it should not have)

    Declares no capability delta: the follower is GuiCode code and touches no G12
    group/binding table, so `_finish`'s capability gate expects the image's
    (channel, command) set to be byte-identical to stock's.

    Returns sealed image with every edit (label rename, blob append, detour) logged to
    context.
    """

    GATE_OBJECT = 46

    PARAMS = {
        "properties": {
            "watch_channel": {"type": "integer",
                              "description": "G12 output instance whose status bit is watched"},
            "target_channel": {"type": "integer",
                               "description": "G12 output instance the follower drives"},
            "on_level": {"type": "integer", "default": 200,
                        "description": "level byte the on-frame carries"},
            "gate_option": {"type": "string", "default": "Heat Pump",
                            "description": "stock caption of the gating OPTIONS row (object 46)"},
            "gate_label": {"type": "string", "default": "Pump Link",
                          "description": "the row's new caption (same-size rename only)"},
        },
        "required": ["watch_channel", "target_channel"],
    }

    def validate(self):
        self.watch_channel = self._param("watch_channel", kind=int)
        self.target_channel = self._param("target_channel", kind=int)
        self.on_level = self._param("on_level", 200, kind=int)
        self.gate_option = self._param("gate_option", "Heat Pump", kind=str)
        self.gate_label = self._param("gate_label", "Pump Link", kind=str)

    def apply(self, data, ctx):
        # --- 1. rename the gate row's OPTIONS caption: same-size, object-46-scoped.
        gs, _ = guidata.guidata_extent(data)
        lo, hi = insert._object_span(data, self.GATE_OBJECT)
        sites = [s for s in patch.find_label_sites(data, self.gate_option)
                 if lo <= s.text_offset - gs < hi]
        if not sites:
            raise RecipeError(
                "NO_SUCH_LABEL", "object %d has no label field containing %r"
                % (self.GATE_OBJECT, self.gate_option), op_id=self.spec.id)
        if len(sites) > 1:
            raise RecipeError(
                "AMBIGUOUS_LABEL", "object %d has %d fields containing %r"
                % (self.GATE_OBJECT, len(sites), self.gate_option), op_id=self.spec.id)
        patcher = patch.McfPatcher(data)
        try:
            patcher.set_label(sites[0], self.gate_option, self.gate_label, align="right")
        except patch.PatchError as e:
            raise RecipeError("LABEL_TOO_LONG", str(e), op_id=self.spec.id)
        if patcher.edits():
            ctx.edits.extend(patcher.edits())
            data_after_rename, _ = patcher.build()
        else:
            # No-op rename (gate_label == gate_option already stored, etc).
            data_after_rename = data

        # --- 2. derive the plan against the PRE-rename image -- `gate_option` names the
        # STOCK caption text, which the rename above just overwrote -- then author and
        # append the routine.
        try:
            plan = followchannel.resolve(data, self.watch_channel, self.target_channel,
                                         self.on_level, self.gate_option)
        except followchannel.FollowChannelError as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)

        gc = theme.guicode_section(data_after_rename)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob, _disasm = followchannel.build(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)

        try:
            out, load_off, _changed = growsection.grow_section(
                data_after_rename, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(out).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "follow_channel: appended %d-byte follower routine (watch=%d, target=%d)"
            % (len(blob), self.watch_channel, self.target_channel)))

        # --- 3. detour the single stock caller to the new routine, then reseal again
        # (the detour invalidates the GuiCode section checksum a second time --
        # grow_section's own reseal ran before this edit existed).
        hook_at, hook_old, hook_new = followchannel.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        pre = out[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError(
                "DETOUR_REFUSED",
                "GuiCode+%#x holds %s, not the plan's expected stock bytes %s; the grow "
                "shifted something it should not have" % (hook_at, pre.hex(), hook_old.hex()),
                op_id=self.spec.id)
        buf = bytearray(out)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(
            file_off, pre, hook_new, "GuiCode",
            "follow_channel: detour bl -> routine entry %#x" % (code_load_addr + followchannel.ENTRY_OFF)))
        sealed, _changed2 = patch.reseal(bytes(buf))

        ctx.handles[self.spec.id] = {
            "hook_at": hook_at,
            "code_load_addr": code_load_addr,
            "entry": code_load_addr + followchannel.ENTRY_OFF,
        }
        return sealed

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, _ = guidata.guidata_extent(data)
        lo, hi = insert._object_span(data, self.GATE_OBJECT)
        got_new = [s.text.strip() for s in patch.find_label_sites(data, self.gate_label)
                  if lo <= s.text_offset - gs < hi]
        assert self.gate_label in got_new, \
            "gate row lost its %r caption (object %d holds: %s)" % (
                self.gate_label, self.GATE_OBJECT, got_new)
        if self.gate_option != self.gate_label:
            stale = [s.text.strip() for s in patch.find_label_sites(data, self.gate_option)
                     if lo <= s.text_offset - gs < hi]
            assert not stale, \
                "gate row still carries the old caption %r (object %d)" % (
                    self.gate_option, self.GATE_OBJECT)

        gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        file_off = gcs.offset + h["hook_at"]
        detour = data[file_off:file_off + 4]
        assert detour != followchannel.HOOK_STOCK, \
            "detour was not applied: stock bl still present at GuiCode+%#x" % h["hook_at"]
        # A later GuiCode-growing op may have chained ANOTHER routine in front of this one
        # (`followchannel.hook_state`), so the entry must be reachable through the chain --
        # not necessarily the hook's direct target -- and the chain must end at the paint fn.
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "routine entry %#x not in the per-tick chain %s" % (
            h["entry"], [hex(c) for c in chain])
        assert chain[-1] == followchannel.TAIL_TARGET

        return ("follow_channel: watch=%d -> target=%d (on_level=%d), gated by %r (object "
                "%d row); detour GuiCode+%#x -> %#x"
                % (self.watch_channel, self.target_channel, self.on_level, self.gate_label,
                   self.GATE_OBJECT, h["hook_at"], h["entry"]))



def _check_append_point(code_load_addr: int, op_id: str) -> None:
    """The GuiCode append point must be stock 13.6's (0x00259BB4) or beyond it by earlier appends,
    and word-aligned. Used to be pinned to exactly 0x00259BB4, which made any two GuiCode-growing
    ops refuse each other; the routines chain instead (`followchannel.hook_state`), so a grown
    section is expected, and a SHRUNK or misaligned one is what is refused."""
    if code_load_addr < followchannel.STOCK_APPEND_POINT or code_load_addr % 4:
        raise RecipeError(
            "UNEXPECTED_APPEND_POINT",
            "GuiCode append point is %#x; expected stock 13.6's %#x or a 4-aligned point beyond "
            "it (a section grown only by earlier appends); refusing to build a routine against "
            "an image this op was not proven on" % (code_load_addr, followchannel.STOCK_APPEND_POINT),
            op_id=op_id)

@op("add_option_follower")
class AddOptionFollower(Op):
    """Add a brand-new OPTIONS row that gates a channel-follower routine -- the whole
    feature in one composable op, built from stock with no Heat Pump rename.

    Where `follow_channel` REPURPOSES the existing Heat Pump row (renaming its caption and
    reading its config register as the gate), this op MANUFACTURES a fresh option end to
    end and leaves every stock row untouched. It wires four already-hardware-proven
    primitives together, in a load-bearing order (each later step consumes an address the
    earlier step derived):

      1. `optionrow.build_config_toggle(selector)` -- clone the firmware's config-toggle
         group (group 129, the Low Volts 0x2F toggle) onto an unused group slot (164),
         retargeted to `selector` (0xEE). This grows the binding table by four entries via
         the padding-arena same-size rewrite (see optionrow's docstring); nothing at or
         after the image container moves. Its touch id (== the reused group index) is what
         the on-screen row must emit to fire the toggle.
      2. `followchannel.resolve(tog.grown, ..., gate_slot_selector=selector,
         variant_group=tog.new_group_index, alloc_display=True)` -- derive the plan against
         the GROWN image (so the spare-cell scan sees it). This picks the DGN_CONFIG
         receive slot for `selector` as the gate -- tested `(slot & gate_mask) ==
         gate_on_value`, which defaults to the whole halfword `== 1` -- and allocates a spare
         +0x44BC8 display cell the routine strb's the gate flag into each tick. That SAME
         cell (`plan.display_slot`) is what the checkbox row reads -- routine-writes ==
         checkbox-reads, one cell.
      3. `optionrow.append_row(tog.grown, 46, 46, 6, label, tog.touch_id,
         plan.display_slot, row_x, row_y)` -- append the on-screen checkbox row (a stock
         switch/label/touch triple cloned from the Heat Pump row) as ONE new object on the
         OPTIONS page (object 46), its switch value_slot repointed to the display cell and
         its touch region carrying the toggle's id. `insert.insert_object` grows
         object-table 1 by one entry, disturbing no existing `child_index`.
      4. Author the routine on that image (`followchannel.build` at the GuiCode append
         point, asserted == the pinned 13.6 0x00259BB4), append it
         (`growsection.grow_section`), then detour the single stock caller
         (`followchannel.detour_edit`, a same-size 4-byte edit at the now-fixed GuiCode
         file offset), and reseal.

    Offset re-derivation: step 3's object insert grows GuiData (shifting the GuiCode
    section's FILE offset) but not the GuiCode section's own length, so the append-point
    RUNTIME address (`GUICODE_BASE_DEFAULT + gc.length - 4`) is unchanged and still equals
    0x00259BB4; the plan's addresses are all runtime SRAM/slot values that no GuiData
    growth moves. The detour's file offset is re-derived from the FINAL image's GuiCode
    section, so the shift is absorbed. Each grow's own gates re-verify the result.

    Params:
      watch_channel: int -- G12 output instance whose status bit is watched
      target_channel: int -- G12 output instance the follower drives
      on_level: int (default 200) -- level byte the on-frame carries
      selector: int (default 0xEE) -- the DGN_CONFIG selector the new toggle writes/reads
                and gates transmission on (must be a free config selector)
      gate_mask: int (default 0xFFFF) -- the mask applied to the selector's receive slot before
                the gate test: `(slot & gate_mask) == gate_on_value`. 0xFFFF is the whole-halfword
                test the hardware-verified builds emit, byte for byte. Narrow it when the
                selector's word carries more than one field.
      gate_on_value: int (default 1) -- the value that masked slot must equal for the follower to
                transmit (and for the button to highlight). Unmasked it must fit an 8-bit
                immediate; masked it may be any u16 within `gate_mask`.
      label: str (default "Pump Link") -- the new row's caption
      row_x: int (default 95) -- the row's switch/checkbox anchor x on the OPTIONS page
      row_y: int (default 368) -- the row's switch/checkbox anchor y (the B2.3 clear slot)

    Error codes:
      TOGGLE_REFUSED -- `build_config_toggle` refused (selector already toggled, donor
                        group not the expected 0x2F Low Volts clone, no room in the padding
                        arena, or a relocate gate failed)
      FOLLOW_REFUSED -- `followchannel.resolve`/`build` could not derive an address or
                        author the routine (no spare display cell, no gate slot for the
                        selector, or the stock hook byte was not the expected `bl`)
      ROW_REFUSED -- `optionrow.append_row` refused (donor triple missing, or
                     `insert.insert_object`'s own gates failed)
      UNEXPECTED_APPEND_POINT -- the GuiCode append point is not the pinned 13.6 address
                                 (0x00259BB4); refuses rather than build against an image
                                 this op was never proven on (as `follow_channel` does)
      GROW_REFUSED -- `growsection.grow_section` refused
      DETOUR_REFUSED -- the bytes at the detour's file offset were not the stock `bl` the
                        plan expects (the grows shifted something they should not have)

    Capability delta `(set(), set())`: the toggle's four bindings are 0x1FED9 config writes
    (channel None), which are outside `recipe.capabilities`'s (channel, command) set
    entirely, and the follower is GuiCode. Stock and output express the same pairs; the
    compile's capability gate confirms it.

    Gate mask (Task 13). The gate test defaults to the whole halfword `== 1`, which is what the
    hardware-verified v9..v13 builds emit -- omitting `gate_mask`/`gate_on_value` must therefore
    stay byte-identical for ever (the research repository's tests pin those images; they are on
    a vehicle). But a selector whose word carries TWO fields breaks that test: the pump-auto-off
    work widens `0xEE` to `minutes | link_flag << 8`, so the slot is never 1 and an unmasked gate
    is permanently dead -- the follower stops following and the button stops highlighting, with
    everything still compiling green. Such a build passes `gate_mask=0xFF00, gate_on_value=0x0100`
    ("the flag byte is 1"), which `followchannel` emits as `movw`+`ands`+`movw`+`cmp` because
    0x0100 does not fit the 8-bit compare immediate the unmasked path uses.

    Registers `ctx.handles[op_id]` with the new object index, group index, display cell,
    and routine addresses so a later op can address the control via `{"ref": op_id}`.
    """

    PARENT_OBJECT = 46        # the OPTIONS page
    DONOR_SWITCH_OBJECT = 46  # the Heat Pump row's own object
    DONOR_SWITCH_REC = 6      # the Heat Pump switch triple starts here (switch, label, touch)

    PARAMS = {
        "properties": {
            "watch_channel": {"type": "integer",
                              "description": "G12 output instance whose status bit is watched"},
            "target_channel": {"type": "integer",
                               "description": "G12 output instance the follower drives"},
            "on_level": {"type": "integer", "default": 200,
                        "description": "level byte the on-frame carries"},
            "selector": {"type": "integer", "default": 0xEE,
                         "description": "the DGN_CONFIG selector the new toggle writes/gates on"},
            "gate_mask": {"type": "integer", "default": 0xFFFF,
                          "description": "mask ANDed with the selector's receive slot before the "
                                         "gate test: (slot & gate_mask) == gate_on_value. 0xFFFF "
                                         "(default) is the whole-halfword test; narrow it when "
                                         "the selector's word packs more than one field"},
            "gate_on_value": {"type": "integer", "default": 1,
                              "description": "the value the masked gate slot must equal for the "
                                             "follower to transmit and the row to read checked"},
            "label": {"type": "string", "default": "Pump Link",
                      "description": "the new OPTIONS row's caption"},
            "row_x": {"type": "integer", "default": 95,
                      "description": "the row's switch/checkbox anchor x on the OPTIONS page"},
            "row_y": {"type": "integer", "default": 368,
                      "description": "the row's switch/checkbox anchor y (the clear slot)"},
        },
        "required": ["watch_channel", "target_channel"],
    }

    def validate(self):
        self.watch_channel = self._param("watch_channel", kind=int)
        self.target_channel = self._param("target_channel", kind=int)
        self.on_level = self._param("on_level", 200, kind=int)
        self.selector = self._param("selector", 0xEE, kind=int)
        self.gate_mask = self._param("gate_mask", 0xFFFF, kind=int)
        self.gate_on_value = self._param("gate_on_value", 1, kind=int)
        self.label = self._param("label", "Pump Link", kind=str)
        self.row_x = self._param("row_x", 95, kind=int)
        self.row_y = self._param("row_y", 368, kind=int)

    def apply(self, data, ctx):
        # --- 1. build the config-toggle group for the new selector (padding-arena grow).
        try:
            tog = optionrow.build_config_toggle(data, self.selector)
        except optionrow.OptionRowError as e:
            raise RecipeError("TOGGLE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(tog.edits)

        # --- 2. derive the plan against the GROWN image (so the spare-cell scan and the
        # gate-slot lookup see the toggle group). alloc_display + variant_group + the
        # slot gate turn on `build`'s per-tick bridge (setVariant(group) + strb the gate
        # flag into the display cell) -- the display cell the checkbox row will read.
        try:
            plan = followchannel.resolve(
                tog.grown, self.watch_channel, self.target_channel, self.on_level,
                gate_slot_selector=self.selector, gate_mask=self.gate_mask,
                gate_on_value=self.gate_on_value, variant_group=tog.new_group_index,
                alloc_display=True)
        except followchannel.FollowChannelError as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)

        # --- 3. append the on-screen checkbox row (switch/label/touch), its switch
        # reading `plan.display_slot` (the routine's display cell) and its touch carrying
        # the toggle's id. This inserts one object; it grows GuiData, not GuiCode.
        try:
            out, new_obj, row_edits = optionrow.append_row(
                tog.grown, self.PARENT_OBJECT, self.DONOR_SWITCH_OBJECT, self.DONOR_SWITCH_REC,
                self.label, tog.touch_id, plan.display_slot, self.row_x, self.row_y)
        except optionrow.OptionRowError as e:
            raise RecipeError("ROW_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(row_edits)

        # --- 4. author + append the follower routine, then detour the stock caller.
        # The object insert shifted GuiCode's FILE offset but not its length, so the
        # append-point RUNTIME address is unchanged by THIS op; re-derive and check it like
        # follow_channel does (an earlier op's append moves it, and the routine chains).
        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob, _disasm = followchannel.build(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)

        try:
            grown, load_off, _changed = growsection.grow_section(out, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "add_option_follower: appended %d-byte follower routine (watch=%d, target=%d)"
            % (len(blob), self.watch_channel, self.target_channel)))

        # Detour the single stock caller to the new routine at the now-fixed GuiCode file
        # offset, then reseal (grow_section's own reseal ran before this edit existed).
        hook_at, hook_old, hook_new = followchannel.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError(
                "DETOUR_REFUSED",
                "GuiCode+%#x holds %s, not the plan's expected stock bytes %s; a grow "
                "shifted something it should not have" % (hook_at, pre.hex(), hook_old.hex()),
                op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(
            file_off, pre, hook_new, "GuiCode",
            "add_option_follower: detour bl -> routine entry %#x"
            % (code_load_addr + followchannel.ENTRY_OFF)))
        sealed, _changed2 = patch.reseal(bytes(buf))

        ctx.handles[self.spec.id] = {
            "object": new_obj,
            "group": tog.new_group_index,
            "gate_mask": self.gate_mask,
            "gate_on_value": self.gate_on_value,
            "touch_id": tog.touch_id,
            "display_slot": plan.display_slot,
            "display_addr": plan.display_addr,
            "hook_at": hook_at,
            "code_load_addr": code_load_addr,
            "entry": code_load_addr + followchannel.ENTRY_OFF,
        }
        return sealed

    def capability_delta(self, ctx):
        # The four toggle bindings are 0x1FED9 config writes (channel None) -- outside the
        # (channel, command) capability set -- and the follower is GuiCode. Nothing gained
        # or lost; the compile's capability gate confirms stock == output.
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        idx, gid = h["object"], h["group"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[idx])

        # The appended row is a switch/label/touch triple carrying the label + toggle id.
        assert [rc.wtype for rc in recs] == [6, 11, 12], \
            "new object %d is not a switch/label/touch row: %s" % (idx, [rc.wtype for rc in recs])
        assert any(rc.wtype == 11 and (rc.label or "").strip() == self.label.strip()
                   for rc in recs), "new object %d lost its %r caption" % (idx, self.label)
        ids = [data[rc.end - 2] | (data[rc.end - 1] << 8) for rc in recs if rc.wtype == 12]
        assert h["touch_id"] in ids, \
            "new object %d does not carry touch id %d (has %s)" % (idx, h["touch_id"], ids)

        # The toggle group (reused slot) selects four fresh bindings that all write the
        # selector (byte[5]) and carry the config DGN with NO G12 channel.
        g = guidata.group_table(data)[gid]
        bt = guidata.binding_table(data)
        sels = {bt[j].raw[5] for j in range(g.start, g.start + g.count)}
        dgns = {bt[j].dgn_low for j in range(g.start, g.start + g.count)}
        chans = {bt[j].channel for j in range(g.start, g.start + g.count)}
        assert g.count == 4 and sels == {self.selector} \
            and dgns == {optionrow.CONFIG_TOGGLE_DGN_LOW} and chans == {None}, \
            "group %d is not the %#04x config toggle: count=%d sels=%s dgns=%s chans=%s" % (
                gid, self.selector, g.count, sels, dgns, chans)

        # Same-cell property: the checkbox switch reads the exact SRAM cell the routine
        # writes -- switch.value_slot == display_slot, and cell_table[value_slot] ==
        # display_addr. If these could drift the follower would toggle a cell the widget
        # never reads (a live bug); this asserts they are one cell in the finished image.
        switch = next(rc for rc in recs if rc.wtype == 6)
        assert switch.value_slot == h["display_slot"], \
            "checkbox reads cell %s, routine writes cell %s" % (switch.value_slot, h["display_slot"])
        cell_addr = struct.unpack_from(
            "<I", data, gs + celltables.cell_table_off(data) + switch.value_slot * 4)[0]
        assert cell_addr == h["display_addr"], \
            "cell %d resolves to %#x, routine writes %#x" % (
                switch.value_slot, cell_addr, h["display_addr"])

        # The follower routine is appended and the stock hook detoured to its entry.
        gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        file_off = gcs.offset + h["hook_at"]
        detour = data[file_off:file_off + 4]
        assert detour != followchannel.HOOK_STOCK, \
            "detour was not applied: stock bl still present at GuiCode+%#x" % h["hook_at"]
        # A later GuiCode-growing op may have chained ANOTHER routine in front of this one
        # (`followchannel.hook_state`), so the entry must be reachable through the chain --
        # not necessarily the hook's direct target -- and the chain must end at the paint fn.
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "routine entry %#x not in the per-tick chain %s" % (
            h["entry"], [hex(c) for c in chain])
        assert chain[-1] == followchannel.TAIL_TARGET

        return ("add_option_follower: %r row (object %d, touch id %d -> group %d, config "
                "%#04x toggle) gates watch=%d -> target=%d (on_level=%d); display cell %d "
                "(%#x); detour GuiCode+%#x -> %#x%s"
                % (self.label, idx, h["touch_id"], gid, self.selector, self.watch_channel,
                   self.target_channel, self.on_level, h["display_slot"], h["display_addr"],
                   h["hook_at"], h["entry"],
                   "" if self.gate_mask == 0xFFFF else
                   "; gate (slot & %#06x) == %#06x" % (self.gate_mask, self.gate_on_value)))


@op("retarget_inverter_gear")
class RetargetInverterGear(Op):
    """Send the Inverter settings capsule to INVERTER / CHARGER SETTINGS instead of the read-only
    PD Inverter readout.

    The capsule is object 1421 -- the "Inverter (gear)" row both compact Electrical frames draw
    beside the AGS/AES cluster. Its touch region carries action id 0x7F7, whose dispatcher body
    sets screen 39 (object 38, 'PD Inverter', a read-only readout). This op repoints that leaf at
    screen 14 (object 17, 'INVERTER / CHARGER SETTINGS'), the page the HOME inverter's 3-second
    hold reaches, which carries five live controls.

    ONE same-size, guarded 2-byte GuiCode edit (`movs r3,#0x27` -> `movs r3,#0xe`). Object 1421 is
    the id's only carrier and renders on the AGS compact frame too, so the retarget is GLOBAL by
    construction: both pages' inverter capsules follow. That is the intent, not a side effect --
    retargeting only the AES copy would need a cloned 1421 plus a second action leaf reaching
    screen 14, and the one orphaned leaf that did (0x7DE) is spent on the AES settings sub-page.

    Params:
      screen: int (default 14) -- the destination screen id; must fit `movs r3,#imm8`.

    Error codes:
      RETARGET_REFUSED -- the leaf's stock bytes did not match (already retargeted, or a drifted
                     build), the id's carrier set is not just object 1421, or the screen id does
                     not fit the immediate. Refuses rather than corrupt.

    Declares no capability delta: an action-dispatcher screen constant, no G12 group/binding.
    """

    hardware_verified = True     # coach-verified 2026-09-02

    PARAMS = {
        "properties": {
            "screen": {"type": "integer", "default": aescompact.INVERTER_SETTINGS_SCREEN_ID,
                       "description": "destination screen id (14 = INVERTER / CHARGER SETTINGS)"},
        },
        "required": [],
    }

    def validate(self):
        self.screen = self._param("screen", aescompact.INVERTER_SETTINGS_SCREEN_ID, kind=int)

    def apply(self, data, ctx):
        try:
            out, edits = aescompact.retarget_inverter_gear(data, self.screen)
        except ValueError as e:
            raise RecipeError("RETARGET_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = {"screen": self.screen,
                                     "touch_id": aescompact.INVERTER_GEAR_TOUCH_ID}
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        section = theme.guicode_section(data)
        off = section.offset + (aescompact.INVERTER_GEAR_LEAF_SITE - theme.guicode_base(section))
        want = thumb.movs_imm(3, h["screen"])
        assert bytes(data[off:off + 2]) == want, \
            "0x7f7 action leaf does not set screen %d in the finished image" % h["screen"]
        # The destination must be a real screen root, and the capsule must still carry the id.
        root = layout.screen_object_map(data).get(h["screen"])
        assert root is not None, "screen %d has no object in the finished image" % h["screen"]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[aescompact.INVERTER_GEAR_BLOCK])
        ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in recs if r.wtype == 12]
        assert h["touch_id"] in ids, \
            "object %d no longer carries the inverter capsule touch id %#06x" % (
                aescompact.INVERTER_GEAR_BLOCK, h["touch_id"])
        return ("inverter capsule (object %d, action %#06x): screen 39 (PD Inverter, read-only) "
                "-> %d (object %d); shared with the AGS compact frame by design"
                % (aescompact.INVERTER_GEAR_BLOCK, h["touch_id"], h["screen"], root))


@op("resolve_register")
class ResolveRegister(Op):
    """Name an existing bus-driven SRAM register (a variable-table status slot) and expose its
    address as a handle any later op references via {"ref": id}. Reuse-existing only: a
    subscription the stock firmware does not already carry is refused (appending a new entry is
    a future, hardware-unverified capability). Returns the image UNCHANGED (like a no-op
    rename); the value is the registered handle."""
    PARAMS = {
        "properties": {
            "dgn": {"type": ["integer", "string"], "description": "DGN low-16/18-bit or a name "
                    "(DC_DIMMER_STATUS_3, DGN_CONFIG, DC_DIMMER_COMMAND_2)"},
            "instance": {"type": ["integer", "null"], "default": None,
                         "description": "per-channel DGN instance filter"},
            "selector": {"type": ["integer", "null"], "default": None,
                         "description": "DGN_CONFIG selector filter"},
        },
        "required": ["dgn"],
    }

    def validate(self):
        self.dgn = self._param("dgn")
        self.instance = self._param("instance", None)
        self.selector = self._param("selector", None)

    def apply(self, data, ctx):
        try:
            reg = register.resolve_register(data, dgn=self.dgn, instance=self.instance,
                                            selector=self.selector)
        except register.RegisterError as e:
            code = "AMBIGUOUS_REGISTER" if "not exactly one" in str(e) else \
                   "BAD_PARAMS" if ("at most one" in str(e) or "unknown DGN" in str(e)) else \
                   "NO_SUCH_REGISTER"
            raise RecipeError(code, str(e), op_id=self.spec.id)
        ctx.handles[self.spec.id] = {
            "var_index": reg.var_index, "slot_addr": reg.slot_addr, "dgn_low": reg.dgn_low,
            "value": reg.value, "extract_mode": reg.extract_mode, "source_field": reg.source_field}
        return data          # pure resolution: no bytes change (the rename_label no-op precedent)

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        return "resolve_register: dgn %#06x value %r -> var %d slot %#x" % (
            h["dgn_low"], h["value"], h["var_index"], h["slot_addr"])


@op("append_filter")
class AppendFilter(Op):
    """Subscribe the panel to a DGN by APPENDING a new variable-table entry, instead of
    retargeting an existing one (`appendfilter.py` has the byte-level proof): the entry goes at
    the table's end via the same padding-arena window rewrite the coach already runs for binding
    growth, and the table count is bumped in both places the boot loader checks (the tag-0x41
    descriptor and GuiCode header +0x20). Exposes a `resolve_register`-shaped handle, so
    `add_register_readout` / `follow_register` take it by `{"ref": id}`.

    A new var is NOT drawable by naming its index as a widget `value_slot` (that indexes the
    display-cell table); feed a cell from its slot with `add_register_readout`.

    Error codes:
      APPEND_REFUSED -- appendfilter refused (bad entry, registry cap, no arena room, a gate).
    """

    # COACH-VERIFIED 2026-09-22: var 298 appended for 0x1FEA0,
    # the panel booted with the 299-entry table, and the HOME readout followed injected payload[0]
    # values 42 then 7 -- registration, match, extraction, slot and routine, end to end.
    hardware_verified = True

    PARAMS = {
        "properties": {
            "dgn": {"type": "integer", "description": "18-bit DGN to receive"},
            "mode": {"type": "integer", "description": "extraction mode (statusbind.LIVE_MODES)"},
            "payload_byte": {"type": "integer", "description": "payload byte the mode reads"},
            "refresh": {"type": "integer", "default": 3, "description": "staleness class 0..3"},
            "instance": {"type": "integer", "default": 255,
                        "description": "filter value for +4; 255 = no filter"},
            "compare_byte": {"type": "integer", "default": 255,
                        "description": "+5: low nibble = payload byte compared, high nibble = "
                                       "2-bit mask select (0 = whole byte)"},
        },
        "required": ["dgn", "mode", "payload_byte"],
    }

    def validate(self):
        try:
            self.filter = appendfilter.NewFilter(
                dgn=self._param("dgn", kind=int), mode=self._param("mode", kind=int),
                payload_byte=self._param("payload_byte", kind=int),
                refresh=self._param("refresh", 3, kind=int),
                instance=self._param("instance", 255, kind=int),
                compare_byte=self._param("compare_byte", 255, kind=int))
        except relocate.RelocationError as e:
            raise RecipeError("APPEND_REFUSED", str(e), op_id=self.spec.id)

    def apply(self, data, ctx):
        try:
            out, edits, (var,) = appendfilter.append_filters(data, [self.filter])
        except relocate.RelocationError as e:
            raise RecipeError("APPEND_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = appendfilter.handle_for(out, var)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        vt = guidata.variable_table(data)
        assert h["var_index"] < len(vt) and vt[h["var_index"]].raw == self.filter.raw(), \
            "appended entry does not read back"
        gc = appendfilter._guicode_section(data)
        gc_count = struct.unpack_from("<H", data, gc.offset + appendfilter.GUICODE_VAR_COUNT_FIELD)[0]
        assert gc_count == len(vt), "GuiCode count %d != table %d" % (gc_count, len(vt))
        assert len(statusbind.registered_dgns(data)) <= statusbind.REGISTRY_CAP
        return ("append_filter: var %d receives DGN %#07x mode %d payload[%d] -> slot %#x; "
                "table %d entries (GuiCode +0x20 matches); COACH-VERIFIED 2026-09-22"
                % (h["var_index"], self.filter.dgn, self.filter.mode, self.filter.payload_byte,
                   h["slot_addr"], len(vt)))


_REGISTER_HANDLE_KEYS = ("var_index", "slot_addr", "dgn_low", "value", "extract_mode",
                        "source_field")


def _resolve_register_spec(data, ctx, spec: dict, op_id: str) -> "register.Register":
    """A register from EITHER a prior resolve_register handle (`ref`) OR an inline
    `{dgn, instance|selector|index}` -- exactly one. `index` names a variable-table entry
    outright, for DGNs whose entries share a filter byte (`0x1FECC`'s 206/207). Shared by
    follow_register and add_register_readout."""
    has_ref = spec.get("ref") is not None
    has_dgn = spec.get("dgn") is not None
    if has_ref == has_dgn:
        raise RecipeError("BAD_PARAMS",
                          "register spec needs exactly one of 'ref' or 'dgn'; got %s (%r)"
                          % ("both" if has_ref else "neither", spec), op_id=op_id)
    if has_ref:
        ref = spec["ref"]
        if ref not in ctx.handles:
            raise RecipeError("BAD_REF", "ref %r names no prior op handle" % (ref,), op_id=op_id)
        h = ctx.handles[ref]
        missing = [k for k in _REGISTER_HANDLE_KEYS if k not in h]
        if missing:
            raise RecipeError("BAD_REF", "ref %r is not a resolve_register handle (missing %s)"
                              % (ref, missing[0]), op_id=op_id)
        return register.Register(h["var_index"], h["slot_addr"], h["dgn_low"], h["value"],
                                 h["extract_mode"], h["source_field"], b"")
    try:
        return register.resolve_register(data, dgn=spec.get("dgn"), instance=spec.get("instance"),
                                         selector=spec.get("selector"),
                                         var_index=spec.get("index"))
    except register.RegisterError as e:
        raise RecipeError("NO_SUCH_REGISTER", str(e), op_id=op_id)


def _resolve_status_source(data, ctx, spec, op_id: str) -> tuple[int, int, int]:
    """`(slot_addr, var_index, live_mode)` of a status source, from exactly one of:

    - `{"ref": op_id}`: a `resolve_register` / `append_filter` handle (`_resolve_register_spec`),
      or any other handle carrying a variable index (`var_index`, else `var`);
    - `{"var": N}`: variable-table entry N outright;
    - `{"dgn": D, "instance"|"selector": V}`: the one existing entry (`_resolve_register_spec`).

    `slot_addr` is the receive slot the filter writes (`register.status_slot_addr`, index-split);
    `live_mode` the entry's extraction mode (`raw[2] >> 4`, `statusbind.LIVE_MODES`), read as
    `register.resolve_register` and `appendfilter.handle_for` decode it. Other keys (`mask`,
    `shift`, a compare) are the caller's. Codes: BAD_PARAMS (not exactly one form), BAD_REF,
    NO_SUCH_REGISTER."""
    if not isinstance(spec, dict):
        raise RecipeError("BAD_PARAMS", "source must be an object, got %r" % (spec,), op_id=op_id)
    forms = [k for k in ("ref", "var", "dgn") if spec.get(k) is not None]
    if len(forms) != 1:
        raise RecipeError("BAD_PARAMS", "source needs exactly one of 'ref', 'var' or 'dgn'; got "
                          "%s (%r)" % (forms or "none", spec), op_id=op_id)
    var = None
    if forms == ["ref"]:
        h = ctx.handles.get(spec["ref"])
        if h is None:
            raise RecipeError("BAD_REF", "ref %r names no prior op handle" % (spec["ref"],),
                              op_id=op_id)
        if all(k in h for k in _REGISTER_HANDLE_KEYS):
            reg = _resolve_register_spec(data, ctx, {"ref": spec["ref"]}, op_id)
            return reg.slot_addr, reg.var_index, reg.extract_mode
        var = h.get("var_index", h.get("var"))
        if not isinstance(var, int) or isinstance(var, bool):
            raise RecipeError("BAD_REF", "ref %r carries no variable index" % (spec["ref"],),
                              op_id=op_id)
    elif forms == ["var"]:
        var = spec["var"]
        if not isinstance(var, int) or isinstance(var, bool):
            raise RecipeError("BAD_PARAMS", "source var must be an integer, got %r" % (var,),
                              op_id=op_id)
    else:
        reg = _resolve_register_spec(data, ctx, spec, op_id)
        return reg.slot_addr, reg.var_index, reg.extract_mode
    vt = guidata.variable_table(data)
    if not 0 <= var < len(vt):
        raise RecipeError("NO_SUCH_REGISTER", "var %d is not in the %d-entry variable table"
                          % (var, len(vt)), op_id=op_id)
    return register.status_slot_addr(var), var, vt[var].raw[2] >> 4


@op("follow_register")
class FollowRegister(Op):
    """Generic register-follower: watch ANY register for an edge and drive a channel, gated by
    ANY register condition(s). The superset of follow_channel/add_option_follower, minus the
    on-screen checkbox. Watch/gate refs name a resolve_register op id (or inline
    {dgn, instance|selector}). The authored routine is the hardware-verified v9 follower with
    generalized inputs; a coach check is warranted the first time a NEW watch register is used."""
    PARAMS = {
        "properties": {
            "watch": {"type": "object", "description": "{ref|dgn+instance/selector, mask?, value?}"},
            "target_channel": {"type": "integer"},
            "on_level": {"type": "integer", "default": 200},
            "gates": {"type": "array", "default": [],
                      "description": "list of {ref|dgn+..., mask?, value?}; all must hold"},
        },
        "required": ["watch", "target_channel"],
    }

    def validate(self):
        self.watch = self._param("watch", kind=dict)
        self.target_channel = self._param("target_channel", kind=int)
        self.on_level = self._param("on_level", 200, kind=int)
        self.gates = self._param("gates", [], kind=list)

    def _condition(self, data, ctx, spec, default_mask):
        # `_resolve_register_spec` covers the ref-vs-inline structural checks (BAD_PARAMS,
        # BAD_REF) with the same codes this method used to raise itself. The one behaviour to
        # preserve is what happens when an INLINE {dgn, ...} names no existing subscription:
        # before the extraction, `register.resolve_register`'s bare `RegisterError` propagated
        # out of `_condition` uncaught and was only caught (as FOLLOW_REFUSED) by `apply`'s
        # `except (register.RegisterError, ...)`. `_resolve_register_spec` now converts that
        # into a `RecipeError("NO_SUCH_REGISTER", ...)` instead -- a RecipeError, not a
        # RegisterError, so `apply`'s except clause would no longer catch it. Re-wrap it here so
        # `follow_register`'s existing error-code contract for this path is unchanged.
        try:
            reg = _resolve_register_spec(data, ctx, spec, self.spec.id)
        except RecipeError as e:
            if e.code == "NO_SUCH_REGISTER":
                raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)
            raise
        return followchannel.Condition(reg, mask=spec.get("mask", default_mask),
                                       value=spec.get("value", 1))

    def apply(self, data, ctx):
        try:
            watch = self._condition(data, ctx, self.watch, default_mask=0x1C)
            gates = [self._condition(data, ctx, g, default_mask=0xFFFF) for g in self.gates]
            plan = followchannel.plan_from_registers(
                data, watch=watch, target_channel=self.target_channel,
                on_level=self.on_level, gates=gates)
        except (register.RegisterError, followchannel.FollowChannelError) as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)
        # --- the add_option_follower step-4 sequence: build @ pinned append point, grow, detour.
        gc = theme.guicode_section(data)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob, _ = followchannel.build(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("FOLLOW_REFUSED", str(e), op_id=self.spec.id)
        try:
            grown, load_off, _ = growsection.grow_section(data, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(gcs.offset + load_off, b"", blob, "GuiCode",
                         "follow_register: appended %d-byte routine (watch var %d -> ch %d)"
                         % (len(blob), watch.register.var_index, self.target_channel)))
        hook_at, hook_old, hook_new = followchannel.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError("DETOUR_REFUSED",
                              "GuiCode+%#x holds %s, not %s" % (hook_at, pre.hex(), hook_old.hex()),
                              op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, pre, hook_new, "GuiCode",
                         "follow_register: detour bl -> %#x" % (code_load_addr + followchannel.ENTRY_OFF)))
        sealed, _ = patch.reseal(bytes(buf))
        ctx.handles[self.spec.id] = {"hook_at": hook_at, "code_load_addr": code_load_addr,
                                     "entry": code_load_addr + followchannel.ENTRY_OFF}
        return sealed

    def capability_delta(self, ctx):
        return set(), set()          # GuiCode only; no G12 group/binding table touched

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        detour = data[gcs.offset + h["hook_at"]:][:4]
        assert detour != followchannel.HOOK_STOCK, "detour not applied"
        text = thumb.disassemble(detour, theme.GUICODE_BASE_DEFAULT + h["hook_at"])[0][1]
        assert text == "bl #%#x" % h["entry"], "detour decodes to %r" % text
        return "follow_register: detour GuiCode+%#x -> %#x" % (h["hook_at"], h["entry"])


def _readout_root_objects(data: bytes, page: int, op_id: str) -> list:
    """EVERY screen-root OBJECT index that walks `page` (Controller Ruling A, sharpened by
    Ruling D): `readout.directive_in_force` and `layout.Screen.walk` both take a root OBJECT
    index, and `layout.screen_for_object` returns a SCREEN ID and raises for anything that is
    not itself a screen root -- so `page` cannot simply be handed to `screen_for_object`.

    Ruling D: many stock objects are shared template content reachable from MORE than one
    screen root (48 on 13.6 -- e.g. 523/892/540/642/689 from all 42 screens; 786/791/788/783
    from 5 each). A readout attached to one of those is walked, and must be leak-checked and
    verified, under EVERY root that reaches it -- returning only the first (as an earlier
    version of this function did) would silently skip the leak check on every other screen the
    same content appears on.

    If `page` IS a screen root (a value of `layout.screen_object_map`), it is its own (only)
    root. Otherwise, every root whose walk places `page` -- i.e. every screen `page` actually
    appears on -- is returned, sorted; refuse if none does (an object no screen can ever
    reach)."""
    roots = list(layout.screen_object_map(data).values())
    if page in roots:
        return [page]
    hits = sorted({root_obj for root_obj in roots
                  if any(p.obj_index == page for p in layout.Screen(data).walk(root_obj))})
    if not hits:
        raise RecipeError("ATTACH_REFUSED",
                          "object %d is not a screen root and is reachable from no screen "
                          "root's walk" % page, op_id=op_id)
    return hits


@op("add_register_readout")
class AddRegisterReadout(Op):
    """Show ANY register as a live number on ANY page (spec 2026-09-04-register-readout-design):
    a display cell (readout.allocate_display_cell, three sramreach-checked tiers), a per-tick copy
    routine chained on the hook, and a wtype-9 directive + wtype-8 readout object attached to
    `page` at absolute (x, y). The coach has shown a wtype-8 repaints from a routine-written cell
    (PC.58); this op's first instance (a `minutes-home` readout on a tier-2 cell) was itself FLASHED
    AND VERIFIED 2026-09-04 (`docs/BUS-BASELINE.md` PC.59). `hardware_verified` is `True` by default
    now, but a TIER-3 instance -- a fresh SRAM cell no stock instruction names -- keeps it `False`
    per instance (`apply`, when `cell.fresh`): a coach photo of one tier does not vouch for an
    address nothing has ever touched."""
    hardware_verified = True
    PARAMS = {
        "properties": {
            "register": {"type": "object", "description": "{ref} | {dgn, instance|selector}"},
            "mask": {"type": "integer", "default": 0xFFFF},
            "add": {"type": "integer", "default": 0},
            "shift": {"type": "integer", "default": 0},
            "sub": {"type": "integer", "default": 0,
                    "description": "subtracted after the mask, clamped at 0 (RV-C AC current is "
                                   "20*A + 32000: sub 32000, shift 1 gives tenths of an amp)"},
            "draw": {"type": "boolean", "default": True,
                     "description": "false: allocate the cell and chain the routine but draw "
                                    "nothing; the handle's display_slot is for another op to "
                                    "draw (add_card_readouts); excludes page/x/y/format/donor"},
            "width_bytes": {"type": "integer", "default": 2, "enum": [1, 2]},
            "page": {"type": "integer", "description": "parent object index"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "format": {"type": "object", "default": {"width": 0, "places": 0, "zero_pad": False}},
            "donor": {"type": "object", "default": {"obj": readout.DONOR_OBJECT,
                                                    "rec": readout.DONOR_READOUT_REC}},
            "cell_slot": {"type": "integer",
                          "description": "FEED AN EXISTING display cell (a stock readout's "
                                         "value_slot) instead of allocating one and authoring a "
                                         "widget; excludes page/x/y/format/donor"},
            "silence": {"type": "array", "items": {"type": "integer"},
                        "description": "with cell_slot: runtime addresses of stock 2-byte "
                                       "stores to that cell to replace with nop, so the fed "
                                       "value is not overwritten each tick"},
        },
        "required": ["register"],
    }

    def validate(self):
        self.register_spec = self._param("register", kind=dict)
        self.mask = self._param("mask", 0xFFFF, kind=int)
        self.add = self._param("add", 0, kind=int)
        self.shift = self._param("shift", 0, kind=int)
        self.sub = self._param("sub", 0, kind=int)
        self.draw = self._param("draw", True, kind=bool)
        self.width_bytes = self._param("width_bytes", 2, kind=int)
        params = self.spec.params or {}
        self.cell_slot = params.get("cell_slot")
        if self.sub:
            # a new routine shape (subtract + clamp), not yet run on the coach
            self.hardware_verified = False
        if not self.draw:
            clash = [k for k in ("page", "x", "y", "format", "donor", "cell_slot", "silence")
                     if k in params]
            if clash:
                raise RecipeError("BAD_PARAMS", "draw false authors no widget; %s do not apply"
                                  % clash, op_id=self.spec.id)
            self.page = self.x = self.y = None
            self.directive = None
            self.donor = None
            return
        if self.cell_slot is not None:
            # the existing-cell mode: nothing is drawn by this op, so the drawing params are
            # not merely ignored but refused -- a recipe that names both is asking for two
            # different things
            clash = [k for k in ("page", "x", "y", "format", "donor") if k in params]
            if clash:
                raise RecipeError("BAD_PARAMS", "cell_slot feeds an existing readout; %s do not "
                                  "apply" % clash, op_id=self.spec.id)
            if not isinstance(self.cell_slot, int) or isinstance(self.cell_slot, bool):
                raise RecipeError("BAD_PARAMS", "cell_slot must be an integer", op_id=self.spec.id)
            sil = params.get("silence", [])
            if not isinstance(sil, list) or any(not isinstance(a, int) or isinstance(a, bool)
                                                for a in sil):
                raise RecipeError("BAD_PARAMS", "silence must be a list of integer addresses",
                                  op_id=self.spec.id)
            self.silence = list(sil)
            self.page = self.x = self.y = None
            self.directive = None
            self.donor = None
            return
        if "silence" in params:
            raise RecipeError("BAD_PARAMS", "silence needs cell_slot", op_id=self.spec.id)
        for k in ("page", "x", "y"):
            if k not in params:
                raise RecipeError("BAD_PARAMS", "%s is required without cell_slot" % k,
                                  op_id=self.spec.id)
        self.page = self._param("page", kind=int)
        self.x = self._param("x", kind=int)
        self.y = self._param("y", kind=int)
        if not (0 <= self.x < render.PANEL_W and 0 <= self.y < render.PANEL_H):
            raise RecipeError(
                "BAD_PARAMS",
                "(x, y) = (%d, %d) is outside the panel (0 <= x < %d, 0 <= y < %d)"
                % (self.x, self.y, render.PANEL_W, render.PANEL_H), op_id=self.spec.id)
        fmt = self._param("format", {"width": 0, "places": 0, "zero_pad": False}, kind=dict)
        donor = self._param("donor", {"obj": readout.DONOR_OBJECT,
                                      "rec": readout.DONOR_READOUT_REC}, kind=dict)
        try:
            self.directive = readout.directive_bytes(int(fmt.get("width", 0)),
                                                     int(fmt.get("places", 0)),
                                                     bool(fmt.get("zero_pad", False)))
        except (readout.ReadoutError, ValueError, TypeError) as e:
            raise RecipeError("BAD_PARAMS", "format: %s" % e, op_id=self.spec.id)
        self.donor = (int(donor["obj"]), int(donor["rec"]))

    def apply(self, data, ctx):
        reg = _resolve_register_spec(data, ctx, self.register_spec, self.spec.id)
        silenced, remaining = [], []
        try:
            if self.cell_slot is not None:
                cell = readout.existing_display_cell(data, self.cell_slot, self.width_bytes)
                out, cell_edits = readout.silence_writers(data, cell, self.silence)
                silenced = sorted(self.silence)
                remaining = readout.stock_writers(out, cell)
                # this mode was FLASHED AND VERIFIED 2026-09-05 (`docs/BUS-BASELINE.md` PC.60): the
                # Absorb/Float rows read dashes with nothing publishing and 57.6/54.0 V from an
                # injected 0x1FECC -- values the stock fence could never pass -- so the class
                # default stands here
            elif not self.draw:
                # A cell nothing names yet: a reused "spare" index would look free to the next
                # allocation (its tier 3 repointed one under an earlier draw:false readout), so
                # mint a genuinely new index. Each op's routine names its cell before the next
                # op runs, which is allocate_fresh_cells' one-grow-at-a-time condition.
                out, cells, cell_edits = readout.allocate_fresh_cells(
                    data, [(self.width_bytes, readout._TYPE_FOR_WIDTH[self.width_bytes])])
                cell = cells[0]
            else:
                out, cell, cell_edits = readout.allocate_display_cell(data, self.width_bytes)
            plan = readout.resolve(out, register=reg, cell=cell, mask=self.mask,
                                   add=self.add, shift=self.shift, sub=self.sub)
        except readout.ReadoutError as e:
            code = "NO_DISPLAY_CELL" if "NO_DISPLAY_CELL" in str(e) else "READOUT_REFUSED"
            raise RecipeError(code, str(e), op_id=self.spec.id)
        except followchannel.FollowChannelError as e:
            raise RecipeError("READOUT_REFUSED", str(e), op_id=self.spec.id)
        if cell.fresh:
            # tier 3: a spare index repointed at fresh SRAM no stock instruction names. The
            # class default (True) covers tiers 1/2, which v17's coach run vouches for; a fresh
            # address is unprecedented on hardware regardless, so this instance stays
            # unverified until ITS tier has its own coach photo.
            self.hardware_verified = False
        ctx.edits.extend(cell_edits)
        # the routine: build at the current append point, grow, detour (the follower sequence)
        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob, _ = readout.build(plan, code_load_addr)
        except readout.ReadoutError as e:
            raise RecipeError("READOUT_REFUSED", str(e), op_id=self.spec.id)
        try:
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(gcs.offset + load_off, b"", blob, "GuiCode",
                         "add_register_readout: %d-byte copy routine (var %d -> cell %#x)"
                         % (len(blob), reg.var_index, cell.addr)))
        hook_at, hook_old, hook_new = readout.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, pre.hex(), hook_old.hex()), op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, pre, hook_new, "GuiCode",
                         "add_register_readout: detour bl -> %#x" % (code_load_addr + readout.ENTRY_OFF)))
        if self.cell_slot is not None:
            # the existing-cell mode authors nothing: the stock readout already names the cell
            sealed, _ = patch.reseal(bytes(buf))
            ctx.handles[self.spec.id] = {
                "display_slot": cell.slot, "display_addr": cell.addr, "width": cell.width,
                "tier": 0, "fresh": False, "object_index": None,
                "entry": code_load_addr + readout.ENTRY_OFF, "hook_at": hook_at,
                "code_load_addr": code_load_addr, "roots": [], "root": None, "screen": None,
                "silenced": silenced, "remaining_writers": remaining,
                "var_index": reg.var_index, "slot_addr": reg.slot_addr,
            }
            return sealed
        if not self.draw:
            # cell + routine only: another op (add_card_readouts) draws display_slot
            sealed, _ = patch.reseal(bytes(buf))
            ctx.handles[self.spec.id] = {
                "display_slot": cell.slot, "display_addr": cell.addr, "width": cell.width,
                "tier": cell.tier, "fresh": cell.fresh, "object_index": None,
                "entry": code_load_addr + readout.ENTRY_OFF, "hook_at": hook_at,
                "code_load_addr": code_load_addr, "roots": [], "root": None, "screen": None,
                "var_index": reg.var_index, "slot_addr": reg.slot_addr,
            }
            return sealed
        # the data: directive + readout object, attached to the page
        try:
            obj = readout.author_readout_object(bytes(buf), x=self.x, y=self.y,
                                                display_slot=cell.slot, directive=self.directive,
                                                donor=self.donor)
            attached, attach_edits = insert.insert_object(bytes(buf), obj, parent_index=self.page)
        except (readout.ReadoutError, insert.InsertError, relocate.RelocationError) as e:
            raise RecipeError("ATTACH_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(attach_edits)
        object_index = len(guidata.object_tables(attached)[1]) - 1
        roots = _readout_root_objects(attached, self.page, self.spec.id)
        for root in roots:
            self._refuse_directive_leak(data, attached, root, object_index)
        sealed, _ = patch.reseal(attached)
        ctx.handles[self.spec.id] = {
            "display_slot": cell.slot, "display_addr": cell.addr, "width": cell.width,
            "tier": cell.tier, "fresh": cell.fresh, "object_index": object_index,
            "entry": code_load_addr + readout.ENTRY_OFF, "hook_at": hook_at,
            "code_load_addr": code_load_addr, "roots": roots, "root": roots[0],
            "screen": layout.screen_for_object(attached, roots[0]),
        }
        return sealed

    def _refuse_directive_leak(self, before, after, root, object_index):
        """Any stock readout on the page's screen whose directive-in-force changed is a leak."""
        old = {(e[1], e[2]): e[3] for e in readout.directive_in_force(before, root) if e[0] == "readout"}
        new = {(e[1], e[2]): e[3] for e in readout.directive_in_force(after, root)
               if e[0] == "readout" and e[1] != object_index}
        changed = sorted(k for k in old if k in new and old[k] != new[k])
        if changed:
            raise RecipeError(
                "DIRECTIVE_LEAK",
                "the new directive would reformat stock readouts walked after it on screen root "
                "%d: %s (object, value_slot). Give those readouts a directive of their own, or "
                "place this one after them" % (root, changed), op_id=self.spec.id)

    def capability_delta(self, ctx):
        return set(), set()

    def _verify_existing_cell(self, data, h):
        """The fed cell keeps its stock type, our routine is on the chain and reaches the cell,
        every silenced site now disassembles to `nop` and is no longer a writer, and the
        writers that remain are exactly the ones the recipe chose to leave."""
        gs, _ = guidata.guidata_extent(data)
        assert (data[gs + celltables.type_table_off(data) + h["display_slot"]]
               == readout._TYPE_FOR_WIDTH[h["width"]])
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "the copy routine is not on the hook chain"
        cell = readout.DisplayCell(h["display_slot"], h["display_addr"], h["width"], 0, False)
        writers = readout.stock_writers(data, cell)
        assert not set(h["silenced"]) & set(writers), \
            "a silenced site still writes the cell: %r" % (set(h["silenced"]) & set(writers),)
        assert [w for w in writers if w < h["code_load_addr"]] == h["remaining_writers"], \
            (writers, h["remaining_writers"])
        sec = theme.guicode_section(data)
        base = theme.guicode_base(sec)
        for site in h["silenced"]:
            off = sec.offset + (site - base)
            thumb.assert_disassembles_to(bytes(data[off:off + 2]), site, ["nop"])
        forms = sramreach.reaching_forms(data, h["display_addr"], h["width"])
        assert not forms["state_struct"]
        ours = [int(s, 16) for s in forms["base_offset"] if int(s, 16) >= h["code_load_addr"]]
        assert ours, "our routine does not reach the cell: %r" % forms
        assert hex(h["display_addr"]) in forms["raw_word"], forms
        return ("add_register_readout: FEEDS existing cell %d @ %#x (var %d @ %#x, add %d, "
                "shift %d) from routine @ %#x; silenced %s; stock writers left %s -- HARDWARE-%s"
                % (h["display_slot"], h["display_addr"], h["var_index"], h["slot_addr"],
                   self.add, self.shift, h["entry"], [hex(a) for a in h["silenced"]],
                   [hex(a) for a in h["remaining_writers"]],
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED"))

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        if self.cell_slot is not None:
            return self._verify_existing_cell(data, h)
        for root in h["roots"]:
            events = readout.directive_in_force(data, root)
            mine = [e for e in events if e[0] == "readout" and e[1] == h["object_index"]]
            assert mine == [("readout", h["object_index"], h["display_slot"],
                             tuple(self.directive))], (root, mine)
            placed = [p for p in layout.Screen(data).walk(root)
                     if p.obj_index == h["object_index"] and p.wtype == 8]
            assert placed and (placed[0].x0, placed[0].y0) == (self.x, self.y), (root, placed)
            # CLAUDE.md: "verifying only what you set is not verification" -- check the
            # decoded placement against the physical panel, not only against the params we
            # ourselves passed in (a bug that mis-decoded or mis-authored (x, y) identically
            # on both sides would pass the equality check above and still draw off-screen).
            x0, y0 = placed[0].x0, placed[0].y0
            assert 0 <= x0 < render.PANEL_W and 0 <= y0 < render.PANEL_H, \
                "readout placed off-panel: (%d, %d)" % (x0, y0)
        gs, _ = guidata.guidata_extent(data)
        assert (data[gs + celltables.type_table_off(data) + h["display_slot"]]
               == readout._TYPE_FOR_WIDTH[h["width"]])
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "the copy routine is not on the hook chain"
        forms = sramreach.reaching_forms(data, h["display_addr"], h["width"])
        assert not forms["state_struct"]
        assert forms["base_offset"], forms
        assert all(int(s, 16) >= h["code_load_addr"] for s in forms["base_offset"]), \
            "a stock instruction reaches the display cell: %r" % forms
        # `raw_word` entries are VALUES (the cell's own address as a pool literal), not sites,
        # so bounding them against `code_load_addr` like `base_offset` would be the wrong
        # question -- the right one is that the ONLY raw word naming the cell is our own pool
        # word (`readout.build`'s "cell" literal), not some stock literal that happens to equal
        # the address we picked.
        assert forms["raw_word"] == [hex(h["display_addr"])], forms
        assert status_map.cell_table(data)[h["display_slot"]] == h["display_addr"], \
            "display slot %d no longer maps to %#x (a later allocation repointed it)" % (
                h["display_slot"], h["display_addr"])
        if not self.draw:
            return ("add_register_readout: cell %d @ %#x (tier %d, fresh=%s), routine @ %#x, "
                    "sub %d; no widget (drawn by another op) -- HARDWARE-%s"
                    % (h["display_slot"], h["display_addr"], h["tier"], h["fresh"], h["entry"],
                       self.sub, "VERIFIED" if self.hardware_verified else "UNVERIFIED"))
        return ("add_register_readout: object %d at (%d,%d) on root %d (of %s), cell %d @ %#x "
                "(tier %d, fresh=%s), routine @ %#x -- HARDWARE-%s"
                % (h["object_index"], self.x, self.y, h["root"], h["roots"], h["display_slot"],
                   h["display_addr"], h["tier"], h["fresh"], h["entry"],
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED"))


@op("swap_aes_electrical")
class SwapAesElectrical(Op):
    """Replace the AES ELECTRICAL page's inline all-in-one body (object 829) with a faithful
    compact frame mirroring the AGS compact frame (object 830), and wire a settings sub-page for
    the setpoint detail the compact frame drops.

    A thin orchestrator over `aescompact.py`: no byte-format knowledge lives here, only the
    sequencing and the RecipeError translation the other ops in this file follow. Three steps,
    each threading the previous step's returned image:

      1. `aescompact.swap_electrical_to_compact(data)` -- clones object 829's original 52-record
         body out to a fresh standalone object (the AES_BODY, for the settings sub-page), clones
         the real AES Engine control (353 + an invisible flow spacer) as a new object, clones the
         AGS cluster as a new object (repointing its engine container at that engine clone, its
         enable pill at the AES Notice action 0x7E2, and its gear id/label to the AES nav/'AES'),
         then rebuilds object 829 in place as the 7-record faithful mirror of 830 (House/Chassis
         voltages, the compact cluster, the inverter switch). The gear touch is already stamped
         with the nav id 0x7DE inside the cloned cluster (so it stays aligned with its capsule),
         which is why there is no separate retarget step.
      2. `aescompact.build_settings_screen(out, aes_body_index)` -- rebuilds the stranded
         screen-root object 31 (screen 28, reachable by no navigation in stock) into an AES
         settings frame whose body container points at the cloned AES_BODY.
      3. `aescompact.wire_settings_nav(out, SETTINGS_NAV_TOUCH_ID)` -- a GUARDED, same-size 2-byte
         GuiCode edit repointing the orphaned 0x7DE action-dispatcher (tap) leaf from screen 14
         to screen 28.

    Nothing on the AGS render path (object 5's mode switch, 830, 315, 795, 45, 486-488) is
    touched -- every donor record aescompact reads (830/315/353/443/440/1420) is CLONED and 353's
    children (249/251 and 526-529) are referenced by index, never written, so the AGS branch
    renders byte-identically before and after.

    Params:
      pump_section: `{"ref": op_id}` (default `None`) -- an `add_pump_auto_off` op's id. When
                    given, `swap_electrical_to_compact` is called with that op's handle read as
                    `pump_section_ids` (the positional `(link_touch_id, plus_touch_id,
                    minus_touch_id, readout_slot)` tuple), and the compact frame carries the
                    outlined Water Pump section (Task 7's `aescompact.build_pump_section`).
                    Left `None` (the default), the frame is the identical 7-record mirror this
                    op has always built -- byte for byte, Task 9 changes nothing about it.
                    Because this op only READS `add_pump_auto_off`'s handle, that op MUST run
                    earlier in the project's op list; if it has not run yet (or `pump_section`
                    names an id that never ran), this op refuses (`BAD_REF`) instead of wiring
                    the section to stale or absent ids.
      off_at_zero: bool (default `False`) -- v20. Replace the Auto Off numeric readout with a
                    wtype-6 switch over two arms, so minute count 0 (the disabled setting since
                    `pumpauto.MINUTES_MIN` became 0) reads **'Off'** and every other count reads
                    the number + 'Min' as before. Requires `pump_section`; refuses `BAD_PARAMS`
                    without it, since there is no readout to switch.
      link_indicator: `{"ref": op_id}` (default `None`) -- an `add_option_follower` op's id
                    (v16). Requires `pump_section`. When given, `swap_electrical_to_compact`
                    is called with that op's `display_slot` handle field as
                    `link_display_slot`: the section's static Pump Link box+label collapses
                    into a wtype-6 switch reading the bridge's 0/1 display cell over its own
                    dark/lit arm objects (`aescompact.append_link_arms` +
                    `build_pump_section`'s v16 shape), instead of the pre-v16 box that read
                    the (frozen, in AES) Low Volts cell 45. Same refuse-rather-than-wire-stale
                    discipline as `pump_section`: a ref with no `display_slot` handle refuses
                    `BAD_REF`.

    Error codes:
      SWAP_REFUSED -- `swap_electrical_to_compact` refused (a `relocate.replace_object_records`
                      or `insert` gate failed, e.g. the build has drifted from the shape the
                      donor records were verified against)
      SETTINGS_REFUSED -- `build_settings_screen` refused (the object-31 rebuild's own
                          `relocate.replace_object_records` gate failed)
      WIRE_REFUSED -- `wire_settings_nav` refused: a DISPLAYED object OUTSIDE the AES page subtree
                      already carries touch id 0x7DE (a hit-test collision), or the 0x7DE leaf's
                      stock bytes at its GuiCode site do not match (a drifted build)
      BAD_REF -- `pump_section` or `link_indicator` names an op id with no handle yet (it has
                 not run, ran after this op, or is disabled), or the handle is missing the
                 field the ref needs (`pump_section_ids`' fields, or `display_slot`) --
                 refuses rather than wire the button to stale or absent ids.

    `hardware_verified = True` by default -- HARDWARE-VERIFIED on the owner's coach 2026-09-01
    over five flash/verify rounds: AGS
    page unchanged, compact AES frame drawn, gear taps to the settings sub-page, gauges live,
    Engine control and enable-pill routing as designed -- all with `pump_section` unset. Before
    2026-09-04, `validate` set this `False` per instance the moment `pump_section` was given,
    because the outlined Water Pump section it adds had not been on a coach. It has now: the pump
    section, and its Pump Link switch when `link_indicator` is also given, were FLASHED AND
    VERIFIED 2026-09-04 (checklist items 5-7), so `validate` no
    longer touches the flag for either param and every instance keeps the class default.

    Capability delta `(set(), set())`: nothing here touches a G12 group/binding -- the AES pill's
    touch region (id 0x7E2) and the settings button (retargeted, but still a navigation id, not a
    command) are both outside `recipe.capabilities`'s (channel, command) set. The pump section's
    own bindings (if any) are `add_pump_auto_off`'s to declare, not this op's -- this op only
    places records that already exist, it authors no binding.

    Registers `ctx.handles[op_id]` with the appended AES body's object index, the settings
    screen id, the touch id the gear and the nav leaf now share, the wire edit, and (when given)
    the resolved `pump_section_ids` tuple.
    """

    hardware_verified = True     # coach-verified 2026-09-01, with or without
                                 # pump_section since 2026-09-04 (validate(), below)

    PARAMS = {
        "properties": {
            "pump_section": {"type": ["object", "null"], "default": None,
                             "description": "{\"ref\": op_id} of an add_pump_auto_off op; "
                             "when given, the compact frame carries its Water Pump section"},
            "link_indicator": {"type": ["object", "null"], "default": None,
                               "description": "{'ref': add_option_follower op id}: wire the "
                                              "Pump Link button to that op's 0/1 display cell "
                                              "as a wtype-6 switch over dark/lit arms"},
            "off_at_zero": {"type": "boolean", "default": False,
                            "description": "show 'Off' instead of '0 Min' when the Auto "
                                           "Off minute count is 0 (the disabled setting), via "
                                           "a wtype-6 switch over two readout arms"},
        },
        "required": [],
    }

    def validate(self):
        self.pump_section = self._param("pump_section", None)
        if self.pump_section is not None and not (
                isinstance(self.pump_section, dict) and set(self.pump_section) == {"ref"}):
            raise RecipeError(
                "BAD_PARAMS",
                "pump_section must be {'ref': op_id} or omitted/null, got %r"
                % (self.pump_section,), op_id=self.spec.id)
        # The Water Pump section (pump_section) and its Pump Link switch (link_indicator) were
        # both seen on the coach, flashed and verified 2026-09-04 (checklist items 5-7), so
        # this instance keeps the class default (`hardware_verified = True`) whether or not
        # pump_section is given -- unlike before then, when giving it forced the flag False here.

        self.link_indicator = self._param("link_indicator", None)
        if self.link_indicator is not None and not (
                isinstance(self.link_indicator, dict) and set(self.link_indicator) == {"ref"}):
            raise RecipeError("BAD_PARAMS",
                              "link_indicator must be {'ref': op_id} or omitted/null, got %r"
                              % (self.link_indicator,), op_id=self.spec.id)

        self.off_at_zero = self._param("off_at_zero", False, kind=bool)
        if self.off_at_zero and self.pump_section is None:
            raise RecipeError("BAD_PARAMS",
                              "off_at_zero needs pump_section: there is no Auto Off readout to "
                              "switch without the Water Pump section", op_id=self.spec.id)
        if self.link_indicator is not None and self.pump_section is None:
            raise RecipeError("BAD_PARAMS", "link_indicator needs pump_section", op_id=self.spec.id)

    def apply(self, data, ctx):
        pump_section_ids = None
        if self.pump_section is not None:
            ref = self.pump_section["ref"]
            ph = ctx.handles.get(ref)
            if ph is None or "readout_slot" not in ph:
                raise RecipeError(
                    "BAD_REF",
                    "pump_section ref %r has not produced a Water Pump section handle; "
                    "add_pump_auto_off must run BEFORE swap_aes_electrical in the project's "
                    "op list, or this op wires the section to stale/absent ids" % (ref,),
                    op_id=self.spec.id)
            pump_section_ids = (ph["link_touch_id"], ph["plus_touch_id"],
                                ph["minus_touch_id"], ph["readout_slot"])

        link_display_slot = None
        if self.link_indicator is not None:
            ref = self.link_indicator["ref"]
            lh = ctx.handles.get(ref)
            if lh is None or "display_slot" not in lh:
                raise RecipeError(
                    "BAD_REF",
                    "link_indicator ref %r has no display_slot handle; it must name the "
                    "add_option_follower op that built the Pump Link bridge, run earlier" % (ref,),
                    op_id=self.spec.id)
            link_display_slot = lh["display_slot"]

        try:
            out, aes_body_index, cluster_index = aescompact.swap_electrical_to_compact(
                data, pump_section_ids=pump_section_ids, link_display_slot=link_display_slot,
                off_at_zero=self.off_at_zero)
        except (ValueError, insert.InsertError) as e:
            raise RecipeError("SWAP_REFUSED", str(e), op_id=self.spec.id)

        try:
            out, settings_screen_id = aescompact.build_settings_screen(out, aes_body_index)
        except ValueError as e:
            raise RecipeError("SETTINGS_REFUSED", str(e), op_id=self.spec.id)

        # rev4: the gear touch is stamped with SETTINGS_NAV_TOUCH_ID during the build (inside the
        # cloned cluster, so it stays aligned with its capsule) -- no separate retarget step.
        try:
            out, wire_edits = aescompact.wire_settings_nav(
                out, aescompact.SETTINGS_NAV_TOUCH_ID)
        except ValueError as e:
            raise RecipeError("WIRE_REFUSED", str(e), op_id=self.spec.id)

        ctx.edits.extend(wire_edits)
        ctx.handles[self.spec.id] = {
            "object": aes_body_index,
            "aes_body_index": aes_body_index,
            "cluster_index": cluster_index,
            "settings_screen_id": settings_screen_id,
            "touch_id": aescompact.SETTINGS_NAV_TOUCH_ID,
            "wire_edits": wire_edits,
            "pump_section_ids": pump_section_ids,
            "link_display_slot": link_display_slot,
            "off_at_zero": self.off_at_zero,
        }
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()          # no G12 group/binding touched (nav ids, not commands)

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        aes_body_index = h["aes_body_index"]
        cluster_index = h["cluster_index"]
        gs, tab = _gs_tab(data)

        # The compact frame (still at object 829) is the faithful 7-record mirror of the AGS
        # compact frame 830 -- capsule, House/Chassis labels + voltage containers, the cluster
        # container and the inverter switch -- followed by the outlined Water Pump section's 15
        # records when pump_section was given.
        recs = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
        # Records are found by ROLE (`aescompact.frame_roles`), not by index: a later op may
        # prepend its own records (v48's cards), which shifts every index but no role.
        try:
            roles = aescompact.frame_roles(data, recs)
        except ValueError as e:
            raise AssertionError("object %d is not the compact frame: %s"
                                 % (aescompact.ELECTRICAL_AES_BODY, e))
        f = roles["frame"]
        FRAME_WTYPES = [33, 11, 5, 11, 5]
        assert [r.wtype for r in recs[f:f + 5]] == FRAME_WTYPES, \
            "object %d is not the faithful compact frame (wtypes %s)" % (
                aescompact.ELECTRICAL_AES_BODY, [r.wtype for r in recs[f:f + 5]])
        link_switch_note = None
        # `>=`, not `==`: a LATER op may append its own records to the compact frame (e.g.
        # add_aes_batt_heat's button). This op validates only the records IT authored -- the
        # 7-record frame and, when present, the Water Pump section that immediately follows it.
        if h["pump_section_ids"] is None:
            assert roles["inverter"] >= 0, \
                "object %d carries %d records with pump_section unset, expected at least the " \
                "7-record frame" % (aescompact.ELECTRICAL_AES_BODY, len(recs))
        else:
            link_id, plus_id, minus_id, readout_slot = h["pump_section_ids"]
            section_len = 14 if h["link_display_slot"] is not None else 15
            # v20: `off_at_zero` folds the (number, 'Min') pair into ONE wtype-6 switch, so the
            # section is a record shorter and the numeric readout has moved into an arm object.
            section_len -= 1 if h["off_at_zero"] else 0
            assert roles["pump"] is not None and len(recs) - roles["pump"] >= section_len, \
                "object %d carries %d records, expected at least 7 + the %d-record Water Pump " \
                "section" % (aescompact.ELECTRICAL_AES_BODY, len(recs), section_len)
            section = recs[roles["pump"]:roles["pump"] + section_len]
            touch_ids_in_order = [data[r.end - 2] | (data[r.end - 1] << 8)
                                  for r in section if r.wtype == 12]
            assert touch_ids_in_order == [link_id, plus_id, minus_id], \
                "Water Pump section touch ids %s, expected [link=%d, plus=%d, minus=%d] in " \
                "flow order" % (touch_ids_in_order, link_id, plus_id, minus_id)
            readout_note = None
            if h["off_at_zero"]:
                assert not any(r.wtype == 8 for r in section), \
                    "off_at_zero left a bare numeric readout in the section; it belongs in the " \
                    "default arm"
                rsw = [r for r in section if r.wtype == 6 and r.count == 2
                       and r.value_slot == readout_slot]
                assert len(rsw) == 1, \
                    "expected exactly one Auto Off readout switch on slot %d, found %d" % (
                        readout_slot, len(rsw))
                rbase = rsw[0].child_index
                mt = guidata.find_match_table(data)
                rmvs = [struct.unpack_from("<H", data, gs + mt + i * 2)[0]
                        for i in (rbase, rbase + 1)]
                assert rmvs == aescompact.READOUT_MATCH_VALUES, \
                    "Auto Off readout arms %d/%d carry match values %s, expected %s" % (
                        rbase, rbase + 1, rmvs, aescompact.READOUT_MATCH_VALUES)
                arm_num = records.object_records(data, gs + tab[rbase])
                arm_off = records.object_records(data, gs + tab[rbase + 1])
                assert [r.wtype for r in arm_off] == [11, 0] \
                    and arm_off[1].label == aescompact.PUMP_READOUT_OFF_TEXT, \
                    "Auto Off disabled arm is %s / %r, expected [flow anchor, wtype-0 %r]" % (
                        [r.wtype for r in arm_off], arm_off[-1].label,
                        aescompact.PUMP_READOUT_OFF_TEXT)
                assert [r.wtype for r in arm_num] == [8, 0] \
                    and arm_num[0].value_slot == readout_slot \
                    and arm_num[1].label == "Min", \
                    "Auto Off number arm is %s reading %s / %r, expected [8, 0] on slot %d + " \
                    "'Min'" % ([r.wtype for r in arm_num], arm_num[0].value_slot,
                               arm_num[1].label, readout_slot)
                readout_note = "Auto Off readout switch on slot %d -> arms %d/%d (number / '%s')" \
                    % (readout_slot, rbase, rbase + 1, aescompact.PUMP_READOUT_OFF_TEXT)
            else:
                readouts = [r.value_slot for r in section if r.wtype == 8]
                assert readouts == [readout_slot], \
                    "Water Pump readout reads slot(s) %s, expected [%d]" % (readouts,
                                                                            readout_slot)

            if h["link_display_slot"] is not None:
                sw = [r for r in section if r.wtype == 6 and r.count == 2
                     and r.value_slot == h["link_display_slot"]]
                assert len(sw) == 1, \
                    "expected exactly one Pump Link switch on cell %d, found %d" % (
                        h["link_display_slot"], len(sw))
                sw = sw[0]
                arms_base = sw.child_index
                mt = guidata.find_match_table(data)
                mvs = [struct.unpack_from("<H", data, gs + mt + i * 2)[0]
                      for i in (arms_base, arms_base + 1)]
                assert mvs == [0, 1], \
                    "Pump Link switch arms %d/%d carry match values %s, expected [0, 1]" % (
                        arms_base, arms_base + 1, mvs)
                arm_off = records.object_records(data, gs + tab[arms_base])
                arm_on = records.object_records(data, gs + tab[arms_base + 1])
                fills = [arm_off[0].colors[0][2], arm_on[0].colors[0][2]]
                assert fills == [10, 35], \
                    "Pump Link switch arm fills %s, expected [10, 35]" % fills
                assert arm_off[1].label == "Pump Link" and arm_on[1].label == "Pump Link", \
                    "Pump Link switch arm captions %r/%r, expected both 'Pump Link'" % (
                        arm_off[1].label, arm_on[1].label)
                assert not any(
                    r.wtype == 33 and r.colors and r.colors[0] == ("fillA", 7, 45)
                    for r in recs[roles["inverter"] + 1:]), \
                    "a wtype-33 record after the frame still reads the Low Volts cell (45)"
                link_switch_note = "Pump Link switch on cell %d -> arms %d/%d" % (
                    h["link_display_slot"], arms_base, arms_base + 1)
            else:
                link_switch_note = None
        # The two voltage containers carry the House/Chassis value_slots (209 / 205).
        vslots = [r.value_slot for r in recs if r.wtype == 5 and r.value_slot is not None]
        assert 209 in vslots and 205 in vslots, \
            "compact frame missing the House(209)/Chassis(205) voltage containers: %s" % vslots
        # The cluster container [5] draws the cloned cluster unconditionally (no value_slot).
        cluster_cont = recs[roles["cluster"]]
        assert cluster_cont.wtype == 5 and cluster_cont.value_slot is None \
            and cluster_cont.child_index == cluster_index, \
            "compact frame cluster container does not point unconditionally at the cloned cluster %d" \
            % cluster_index
        # The inverter switch [6] stays gated on slot 113.
        inv = recs[roles["inverter"]]
        assert inv.wtype == 6 and inv.value_slot == 113, \
            "compact frame inverter switch is not the slot-113 switch"

        # The gear touch (SETTINGS_NAV_TOUCH_ID) lives in the cloned cluster, aligned on its
        # capsule, and the cluster draws the AES 'Engine' control (Start/Stop groups 13/14).
        crecs = records.object_records(data, gs + tab[cluster_index])
        gear_ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in crecs if r.wtype == 12]
        assert h["touch_id"] in gear_ids, \
            "cloned cluster %d does not carry the gear nav id %#06x (has %s)" % (
                cluster_index, h["touch_id"], gear_ids)
        engine_cont = crecs[aescompact.CLUSTER_ENGINE_CONTAINER_REC]
        erecs = records.object_records(data, gs + tab[engine_cont.child_index])
        elabels = [r.label for r in erecs if r.label]
        assert "Engine" in elabels, \
            "cluster engine control is not the AES 'Engine' control (labels %s)" % elabels
        eids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in erecs if r.wtype == 12]
        assert 0x0d in eids and 0x0e in eids, \
            "engine control missing the Engine Start/Stop touch ids 0x0d/0x0e (has %s)" % eids
        # FIX #4: a clone of the real AES Engine (object 353) -- none of the AGS GEN (352)
        # extras (the group-170 center-touch, the value_slot-46 gen-hours line) leaked in.
        assert 0xaa not in eids, "engine still carries the stray center-touch (group 170)"
        assert 46 not in [r.value_slot for r in erecs], \
            "engine still carries the generator-hours line (value_slot 46)"
        # FIX #5: the enable pill routes through the AES Notice action (0x7E2), not the stock
        # 3-sec-hold warning group (0x0080).
        pill = crecs[aescompact.PILL_TOUCH_REC]
        pill_id = data[pill.end - 2] | (data[pill.end - 1] << 8)
        assert pill_id == aescompact.AES_NOTICE_ACTION, \
            "enable-pill touch carries id %#06x, expected the AES Notice action %#06x" % (
                pill_id, aescompact.AES_NOTICE_ACTION)

        # The cloned AES all-in-one body exists (the settings sub-page body).
        body_recs = records.object_records(data, gs + tab[aes_body_index])
        assert len(body_recs) > 0, "AES body object %d is empty" % aes_body_index

        # Screen 28 still maps to object 31 (the in-place rebuild), and its body container
        # points at the appended AES body.
        assert layout.screen_object_map(data)[h["settings_screen_id"]] == \
            aescompact.AES_SETTINGS_ROOT
        settings_recs = records.object_records(data, gs + tab[aescompact.AES_SETTINGS_ROOT])
        container_rec = settings_recs[aescompact.CONTAINER_RECORD_INDEX]
        assert container_rec.child_index == aes_body_index, \
            "AES settings frame body container points at object %s, expected the cloned body %d" \
            % (container_rec.child_index, aes_body_index)

        # The 0x7DE action-dispatcher leaf now targets the settings screen, not stock's screen 14.
        section = theme.guicode_section(data)
        base = theme.guicode_base(section)
        off = section.offset + (aescompact.SETTINGS_LEAF_SITE - base)
        got = bytes(data[off:off + 2])
        assert got == thumb.movs_imm(3, h["settings_screen_id"]), \
            "0x7DE leaf screen constant at %#x is %s, expected screen %d" % (
                aescompact.SETTINGS_LEAF_SITE, got.hex(), h["settings_screen_id"])

        section_note = ("no Water Pump section (pump_section unset)" if h["pump_section_ids"]
                        is None else "Water Pump section wired (link/plus/minus/readout = %s)"
                        % (h["pump_section_ids"],))
        if link_switch_note is not None:
            section_note += "; " + link_switch_note
        return ("swap_aes_electrical: object 829 -> faithful compact frame mirroring AGS 830 "
                "(House/Chassis voltages, AES 'Engine' Start/Stop, gear id %#06x on its capsule in "
                "cloned cluster %d, inverter); %s; AES body cloned to object %d; screen %d "
                "(object %d) rebuilt as the AES settings frame; 0x7DE nav leaf repointed "
                "screen 14 -> %d; HARDWARE-%s (coach-verify: cloned AES Engine 353 + flow "
                "spacer, pill 0x80->0x7E2 Notice flow, cluster placement%s)"
                % (h["touch_id"], cluster_index, section_note, aes_body_index,
                   h["settings_screen_id"], aescompact.AES_SETTINGS_ROOT,
                   h["settings_screen_id"],
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED",
                   "" if h["pump_section_ids"] is None else ", the outlined Water Pump section"))


@op("add_aes_low_volts_trigger")
class AddAesLowVoltsTrigger(Op):
    """Add a LIVE Low Volts arm indicator to the compact AES ELECTRICAL frame (Mechanism B).

    Stock FREEZES the Low Volts recolor cell (cell 45) in AES: a native GuiCode gate at
    0x0024A172 (`bls.w 0x24a42e`) skips the cell-45 decode whenever the mode is AES, which is
    the only mode object 829 is ever on screen for -- so the shipped AGS Low Volts button, if
    copied to this page, cannot recolor (CLAUDE.md open item 4). But the live 0x2F arm state is
    NOT frozen: its DGN_CONFIG receive-filter entry (var 42) writes slot 0x2000801C in every
    mode. This op reuses that un-frozen slot with the hardware-proven v9 follower toolchain to
    build a display-only arm indicator that works in AES -- with NO gate surgery (the Mechanism-C
    in-place `nop` of the freeze gate is the documented alternative, deliberately not taken).

    Three steps, threading the previous step's image (rev 2 -- the AGS-style control + the bridge
    that mirrors the stock cell-45 block un-gated, so the control recolors AND toggles both ways):

      1. `register.resolve_register(dgn=DGN_CONFIG, selector=0x2F)` -> var 42, slot 0x2000801C
         (reuse-existing; the subscription already ships).
      2. rev4: the AGS-style Low Volts control -- a wtype-5 recolor container (value_slot 45,
         child 285 = the 'Low Volts' box) + its group-129 touch -- is ALREADY on the compact frame,
         supplied by the cloned cluster (315[8]/[9]) that `swap_aes_electrical` placed. No splice
         here; this op only authors the bridge that drives that control's cell 45 + counter.
      3. Author the un-gated display bridge (`followchannel.build_display_bridge`: hook
         0x0024EE3C -> routine at the pinned 0x00259BB4) that MIRRORS the stock cell-45 block
         un-gated -- each tick it reads the 0x2F slot 0x2000801C and writes the group-129 variant
         counter (0x20007686 + twin = !armed, fixing the one-way toggle) and cell 45 (0x2003A814
         = armed ? ON master 0x2003A1A0 : OFF master 0x20039A58, the recolor), then tail-calls
         FUN_00247BF8. Append it (`growsection.grow_section`), detour the single stock caller
         (`followchannel.detour_display_bridge`), and reseal.

    Because the bridge runs BEFORE the tail-call, in AGS the stock block re-runs after it and
    overwrites cell 45 + the counter with identical values (its gate is false in AGS) -- AGS
    runtime is unchanged; in AES the stock decode is gated off so the bridge's values stand.

    `hardware_verified = True`: HARDWARE-VERIFIED on the owner's coach 2026-09-01 -- the Low Volts
    control recolors and arms/disarms both ways in AES, confirmed by switching AGS<->AES to force a
    repaint.

    Capability delta `(set(), set())`: group 129's bindings are 0x1FED9 config writes (channel
    None, outside `recipe.capabilities`) and the bridge is GuiCode -- the group/binding tables
    are not touched, so stock and output express exactly the same (channel, command) pairs.

    Params:
      x, y: int (default 120, 300) -- ACCEPTED FOR COMPATIBILITY but unused since rev4 (the Low
            Volts control's placement now comes from the cloned cluster, not a splice anchor).

    Error codes:
      NO_SUCH_REGISTER -- the 0x2F DGN_CONFIG subscription is not in the variable table.
      UNEXPECTED_APPEND_POINT -- the GuiCode append point is below stock's 0x00259BB4 or
                     misaligned (beyond it, from earlier appends, the routine chains).
      BRIDGE_REFUSED / GROW_REFUSED / DETOUR_REFUSED -- the bridge could not be authored,
                     appended, or the detour site was not the stock `bl`.
    """

    hardware_verified = True     # coach-verified 2026-09-01

    PARAMS = {
        "properties": {
            "x": {"type": "integer", "default": 120,
                  "description": "the Low Volts checkbox switch anchor x on the compact frame"},
            "y": {"type": "integer", "default": 300,
                  "description": "the Low Volts checkbox switch anchor y on the compact frame"},
        },
        "required": [],
    }

    APPEND_POINT = followchannel.STOCK_APPEND_POINT   # the FIRST routine lands here; later ones chain

    def validate(self):
        self.x = self._param("x", 120, kind=int)
        self.y = self._param("y", 300, kind=int)

    def apply(self, data, ctx):
        # --- 1. resolve the un-frozen 0x2F arm register (var 42 -> slot 0x2000801C).
        try:
            reg = register.resolve_register(data, dgn="DGN_CONFIG",
                                            selector=aescompact.TRIGGER_SELECTOR)
        except register.RegisterError as e:
            raise RecipeError("NO_SUCH_REGISTER", str(e), op_id=self.spec.id)

        # --- 2. rev4: the AGS-style Low Volts control (wtype-5 recolor container on value_slot 45
        # + group-129 touch) already lives in the cloned cluster (315[8]/[9]) that swap_aes_electrical
        # placed on the compact frame -- no splice here; the bridge below drives its cell 45 + counter.
        out = data

        # --- 3. author + append the un-gated display bridge, then detour the stock caller.
        # The swap grew GuiData (shifting GuiCode's FILE offset) but not GuiCode's length, so the
        # append-point RUNTIME address is stock's 0x00259BB4 unless an earlier op appended a
        # routine -- in which case this bridge lands after it and chains onto it.
        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            plan = followchannel.resolve_display_bridge(
                out, gate_addr=reg.slot_addr, gate_on_value=1)
            blob, _disasm = followchannel.build_display_bridge(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("BRIDGE_REFUSED", str(e), op_id=self.spec.id)

        try:
            grown, load_off, _changed = growsection.grow_section(out, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "add_aes_low_volts_trigger: appended %d-byte display bridge (mirrors the stock cell-45 "
            "block un-gated: 0x2F slot %#x -> counter %#x + cell 45 %#x)" % (
                len(blob), reg.slot_addr, plan.counter_addr, plan.cell_addr)))

        hook_at, hook_old, hook_new = followchannel.detour_display_bridge(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError(
                "DETOUR_REFUSED",
                "GuiCode+%#x holds %s, not the plan's expected stock bytes %s; a grow shifted "
                "something it should not have" % (hook_at, pre.hex(), hook_old.hex()),
                op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(
            file_off, pre, hook_new, "GuiCode",
            "add_aes_low_volts_trigger: detour bl -> display bridge entry %#x" % code_load_addr))
        sealed, _changed2 = patch.reseal(bytes(buf))

        ctx.handles[self.spec.id] = {
            "object": aescompact.ELECTRICAL_AES_BODY,
            "recolor_slot": aescompact.RECOLOR_SLOT,
            "cell_addr": plan.cell_addr,
            "counter_addr": plan.counter_addr,
            "gate_addr": reg.slot_addr,
            "touch_id": aescompact.TRIGGER_TOUCH_ID,
            "hook_at": hook_at,
            "code_load_addr": code_load_addr,
            "entry": code_load_addr,
        }
        return sealed

    def capability_delta(self, ctx):
        return set(), set()          # group 129 reused (channel None); bridge is GuiCode

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)

        # The Low Volts control this bridge drives: a wtype-5 recolor container reading
        # value_slot 45 (the cell the bridge writes) in an object that also registers touch id
        # 129. `swap_aes_electrical` puts one in the compact frame's cloned cluster; a later op
        # may move it (`remove_records` + `add_low_volts_control`), so look in every object
        # rather than only there. Stock's AGS hosts are excluded: they are not AES controls.
        hosts = []
        for idx in range(len(tab)):
            if idx in aescompact.AGS_LOW_VOLTS_HOSTS or not tab[idx]:
                continue
            recs = records.object_records(data, gs + tab[idx])
            conts = [r for r in recs if r.wtype == 5 and r.value_slot == h["recolor_slot"]]
            if not conts:
                continue
            ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in recs if r.wtype == 12]
            if h["touch_id"] not in ids:
                continue
            for cont in conts:
                cell_addr = struct.unpack_from(
                    "<I", data, gs + celltables.cell_table_off(data) + cont.value_slot * 4)[0]
                assert cell_addr == h["cell_addr"], \
                    "object %d container slot %d resolves to %#x, the bridge writes cell 45 " \
                    "at %#x" % (idx, cont.value_slot, cell_addr, h["cell_addr"])
            hosts.append(idx)
        assert hosts, ("no object outside the AGS panel carries a wtype-5 Low Volts container "
                       "on recolor slot %d with touch id %d" % (h["recolor_slot"], h["touch_id"]))

        # The un-gated display bridge is appended and the stock hook detoured to its entry.
        gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        detour = data[gcs.offset + h["hook_at"]:][:4]
        assert detour != followchannel.HOOK_STOCK, \
            "detour not applied: stock bl still present at GuiCode+%#x" % h["hook_at"]
        # Reachable THROUGH the per-tick chain (a later op may have chained in front), ending
        # at the displaced paint fn -- `followchannel.hook_state` / `chain_from_hook`.
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "bridge entry %#x not in the per-tick chain %s" % (
            h["entry"], [hex(c) for c in chain])
        assert chain[-1] == followchannel.TAIL_TARGET

        return ("add_aes_low_volts_trigger: AGS-style Low Volts control (object(s) %s, wtype-5 "
                "recolor container on slot %d / cell %#x, touch id %d -> group 129) driven by an "
                "UN-GATED bridge that mirrors the stock cell-45 block (0x2F slot %#x -> counter + "
                "cell 45) each tick; detour GuiCode+%#x -> %#x; HARDWARE-UNVERIFIED (coach test: "
                "recolor + two-way toggle in AES)"
                % (", ".join(map(str, hosts)), h["recolor_slot"], h["cell_addr"],
                   h["touch_id"], h["gate_addr"], h["hook_at"], h["entry"]))


@op("add_aes_batt_heat")
class AddAesBattHeat(Op):
    """Add a "Batt Heat" command button to the compact AES ELECTRICAL frame.

    The button transmits the BMS "Enable battery heater" proprietary-A frame on a press-and-hold
    (`18EF709F#AA55000000008CA0` -- 0x8CA0 = 36000 x 100ms = 60-minute arm) and the "off" frame
    on a quick tap (`18EF709F#...0000`). The panel stamps its own source address `0x9F`; the BMS
    is destination-filtered (owner-confirmed), so `0x9F` is accepted and the reference `0xFD` is
    neither needed nor spoofed. The gate that this path can emit a novel proprietary-A DGN at
    priority 6 with a raw payload is `proven-from-code` + a hardware oracle
    (`tests/test_binding_frame.py`).

    Runs AFTER `swap_aes_electrical` (it appends to the rebuilt compact object 829). Two steps:

      1. Mint a fresh command group in a free group slot and grow the binding table by the two
         EF70 entries (`optionrow._grow_bindings`), the entries composed by `newbutton.binding_raw`
         (enable on gesture mask 0x04 = while-held; off on 0x02 = short-tap release). The group's
         prefix is cloned from a stock 1-second-hold tap-or-hold group so a hold registers the
         held phase; the enable binding's repeat flag is clear, so it fires once per hold.
      2. Append the button's records to object 829 (`aescompact.build_batt_heat_button`: a filled
         box + inset outline + centred 'Batt Heat' heading + a wtype-12 touch stamped with the new
         group id) via `relocate.replace_object_records`.

    `hardware_verified = True`: COACH-VERIFIED 2026-09-21. On the owner's coach a press-and-hold
    put `18EF709F#AA55000000008CA0` on `can1` and a tap put `...0000` (both captured on the bus,
    exactly to spec), and once a BMS-side setting was enabled to allow it, the heater relay engaged
    and drew current. The panel's gesture->frame path is confirmed end-to-end. No indicator yet --
    the BMS status DGN is still being defined; that is the next piece of work.

    Capability delta `(set(), set())`: the two new bindings carry DGN 0xEF70 (channel None, outside
    `recipe.capabilities`) and nothing is removed, so stock and output express the same
    (channel, command) pairs.

    Error codes:
      NO_FREE_GROUP -- no empty group-table slot to host the button's command group.
      GROW_REFUSED  -- the binding table could not be grown (padding arena / gates).
      BUTTON_REFUSED / REPLACE_REFUSED -- the button records could not be built or spliced.
    """

    hardware_verified = True     # coach-verified 2026-09-21: hold->enable / tap->off captured on
                                 # can1, heater engaged once the BMS-side allow setting was enabled

    PARAMS = {
        "properties": {
            "x": {"type": "integer", "default": aescompact.BATT_HEAT_X,
                  "description": "button box left x on the compact frame"},
            "y": {"type": "integer", "default": aescompact.BATT_HEAT_Y,
                  "description": "button box top y"},
            "w": {"type": "integer", "default": aescompact.BATT_HEAT_W,
                  "description": "button box width"},
            "h": {"type": "integer", "default": aescompact.BATT_HEAT_H,
                  "description": "button box height"},
            "text": {"type": "string", "default": aescompact.BATT_HEAT_TEXT,
                     "description": "button caption"},
            "enable_wire": {"type": "string",
                            "default": aescompact.BATT_HEAT_ENABLE_WIRE.hex(),
                            "description": "8-byte payload (hex) sent once on a 1-s hold"},
            "off_wire": {"type": "string", "default": aescompact.BATT_HEAT_OFF_WIRE.hex(),
                         "description": "8-byte payload (hex) sent on a tap"},
            "group_pool": {"type": "string", "default": "free", "enum": ["free", "hold_safe"],
                           "description": "where the command group comes from: the first free "
                                          "slot (the heater, byte-stable), or "
                                          "newbutton.hold_safe_groups (a group GuiCode does not "
                                          "drive; holds on GuiCode-driven 136/137 were dead on "
                                          "a coach)"},
        },
        "required": [],
    }

    def validate(self):
        self.x = self._param("x", aescompact.BATT_HEAT_X, kind=int)
        self.y = self._param("y", aescompact.BATT_HEAT_Y, kind=int)
        self.w = self._param("w", aescompact.BATT_HEAT_W, kind=int)
        self.h = self._param("h", aescompact.BATT_HEAT_H, kind=int)
        self.text = self._param("text", aescompact.BATT_HEAT_TEXT, kind=str)
        self.wires = []
        for key, default in (("enable_wire", aescompact.BATT_HEAT_ENABLE_WIRE),
                             ("off_wire", aescompact.BATT_HEAT_OFF_WIRE)):
            raw = self._param(key, default.hex(), kind=str)
            try:
                wire = bytes.fromhex(raw)
            except ValueError:
                wire = b""
            if len(wire) != 8:
                raise RecipeError("BAD_PARAMS", "%s must be 8 bytes of hex, got %r" % (key, raw),
                                  op_id=self.spec.id)
            self.wires.append(wire)
        self.enable_wire, self.off_wire = self.wires
        self.group_pool = self._param("group_pool", "free", kind=str)
        if self.group_pool not in ("free", "hold_safe"):
            raise RecipeError("BAD_PARAMS", "group_pool must be 'free' or 'hold_safe'",
                              op_id=self.spec.id)
        if (self.enable_wire, self.off_wire) != (aescompact.BATT_HEAT_ENABLE_WIRE,
                                                 aescompact.BATT_HEAT_OFF_WIRE) \
                or self.group_pool != "free":
            # the coach verified the heater's frames on a free-slot group; another command (or
            # another group source) is the first of its kind until the coach shows it
            self.hardware_verified = False

    def apply(self, data, ctx):
        gt = guidata.group_table(data)
        binding_start = len(guidata.binding_table(data))

        free = (newbutton.free_group_slots(data) if self.group_pool == "free"
                else newbutton.hold_safe_groups(data, levelcontrols.RESERVED_GROUPS))
        if not free:
            raise RecipeError("NO_FREE_GROUP",
                              "no empty group-table slot to host the Batt Heat command group",
                              op_id=self.spec.id)
        group = free[0]

        donor = gt[aescompact.BATT_HEAT_HOLD_DONOR_GROUP]
        if donor.raw[0] >> 4 < 1:
            raise RecipeError(
                "GROW_REFUSED",
                "hold-donor group %d has a 0-second hold nibble; a hold could not register"
                % aescompact.BATT_HEAT_HOLD_DONOR_GROUP, op_id=self.spec.id)

        enable = newbutton.binding_raw(aescompact.BATT_HEAT_DGN, self.enable_wire,
                                       gesture_mask=aescompact.BATT_HEAT_ENABLE_MASK)
        off = newbutton.binding_raw(aescompact.BATT_HEAT_DGN, self.off_wire,
                                    gesture_mask=aescompact.BATT_HEAT_OFF_MASK)
        entry = optionrow._clone_group_entry(donor.raw, binding_start, 2)
        try:
            out, grow_edits = optionrow._grow_bindings(
                data, bytes(enable) + bytes(off), [(group, entry)])
        except optionrow.OptionRowError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(grow_edits)

        # append the button's records to the (already-compact) object 829.
        gs, _ = guidata.guidata_extent(out)
        table = guidata.object_tables(out)[1]
        obj_start = gs + table[aescompact.ELECTRICAL_AES_BODY]
        recs = records.object_records(out, obj_start)
        old_count = out[obj_start]
        records_raw = bytes(out[obj_start + 1:recs[-1].end])
        try:
            # donors are read from stock: object 829's own records are the rebuilt compact frame
            # by now, so the stock all-in-one 829 is the only stable source for its box records.
            frags = aescompact.build_batt_heat_button(
                ctx.stock, touch_id=group, x=self.x, y=self.y, w=self.w, h=self.h,
                text=self.text)
        except ValueError as e:
            raise RecipeError("BUTTON_REFUSED", str(e), op_id=self.spec.id)
        new_body = bytes([old_count + len(frags)]) + records_raw + b"".join(frags)
        try:
            out, repl_edits = relocate.replace_object_records(
                out, aescompact.ELECTRICAL_AES_BODY, new_body)
        except relocate.RelocationError as e:
            raise RecipeError("REPLACE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(repl_edits)

        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {
            "object": aescompact.ELECTRICAL_AES_BODY,
            "group": group,
            "touch_id": group,
            "binding_start": binding_start,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h, "text": self.text,
            "enable_wire": self.enable_wire, "off_wire": self.off_wire,
        }
        return out

    def capability_delta(self, ctx):
        return set(), set()          # EF70 bindings are channel None; nothing removed

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[h["object"]])

        ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in frame if r.wtype == 12]
        assert h["touch_id"] in ids, \
            "Batt Heat touch id %d not on the compact frame (has %s)" % (h["touch_id"], ids)
        # The caption is on the frame itself, or -- once `add_aes_batt_heat_status` has run -- in
        # every arm of the state switch that replaced the button body, which is where it has to be
        # for an arm repaint to redraw it (v31 lost it on enable by leaving it a sibling).
        labels = [r.label for r in frame if r.wtype == 11 and r.label]
        arm_labels = []
        for sw in [r for r in frame if r.wtype == 6]:
            for child in range(sw.child_index, sw.child_index + (sw.count or 0)):
                if child < len(tab):
                    arm_labels += [r.label for r in records.object_records(data, gs + tab[child])
                                   if r.wtype == 11 and r.label]
        assert h["text"] in labels + arm_labels, \
            ("'%s' heading missing from the compact frame and from every switch arm on it "
             "(frame has %s)" % (h["text"], labels))

        gt = guidata.group_table(data)
        bt = guidata.binding_table(data)
        g = gt[h["group"]]
        assert g.count == 2, "Batt Heat group %d has count %d, expected 2" % (h["group"], g.count)
        enable_id, enable_p = newbutton.binding_wire_frame(bt[g.start].raw)
        off_id, off_p = newbutton.binding_wire_frame(bt[g.start + 1].raw)
        assert (enable_id, enable_p) == (aescompact.BATT_HEAT_ENABLE_ID, h["enable_wire"]), \
            "enable binding emits %08X#%s" % (enable_id, enable_p.hex())
        assert (off_id, off_p) == (aescompact.BATT_HEAT_OFF_ID, h["off_wire"]), \
            "off binding emits %08X#%s" % (off_id, off_p.hex())
        assert bt[g.start].raw[3] == aescompact.BATT_HEAT_ENABLE_MASK, "enable is not held-gated"
        assert bt[g.start + 1].raw[3] == aescompact.BATT_HEAT_OFF_MASK, "off is not tap-gated"

        heater = (h["enable_wire"], h["off_wire"]) == (aescompact.BATT_HEAT_ENABLE_WIRE,
                                                       aescompact.BATT_HEAT_OFF_WIRE)
        return ("add_aes_batt_heat: %r button (object %d, touch id %d -> new group %d, "
                "bindings %d/%d) transmits %08X#%s on press-and-hold%s and %08X#%s on tap; "
                "source address 0x9F, priority 6 (firmware-forced); %s"
                % (h["text"], h["object"], h["touch_id"], h["group"], g.start, g.start + 1,
                   enable_id, enable_p.hex(), " (60-minute heater enable)" if heater else "",
                   off_id, off_p.hex(),
                   "COACH-VERIFIED 2026-09-21 (hold/tap frames captured on can1, heater engaged)"
                   if self.hardware_verified else
                   "UNVERIFIED on the coach (a command the coach has not run)"))


@op("add_alt_reboot")
class AddAltReboot(Op):
    """Add a "REBOOT APS-500" button to the ALTERNATOR SYSTEM page (screen 21) beside CLEAR
    EEPROM: a 3-second hold transmits the RV-C `GENERAL_RESET` reboot command
    `197F809F#0100FFFFFFFFFFFF` to the APS-500 alternator charger (SA `0x80`); a tap sends
    nothing.

    Two steps, the same shape as `add_aes_batt_heat`:

      1. Mint a fresh command group in a free group slot and grow the binding table by one
         `0x17F80` entry (`altreboot.binding`: held phase, no repeat). The group entry is Batt
         Heat's hold donor (group 17) with the threshold nibble 1 -> 3 s and the held-phase
         repeat zeroed (`altreboot.group_entry`).
      2. Append a clone of CLEAR EEPROM's box/caption/touch records (object 24 records 19-21),
         moved 123 px left and relabelled, the touch stamped with the new group, to object 24
         via `relocate.replace_object_records`. Object 24's own record stream carries the bottom
         bar, so the button is on this page only.

    Pinned to 13.6: `altreboot.check_page` asserts object 24's title and the donor records'
    bytes and refuses on any other build.

    `clear_faults` sends rvc2mqtt's `reset_aps` frame instead (`05`: reboot + clear faults).

    Capability delta `(set(), set())`: the binding's DGN is `0x17F80` (channel None, outside
    `recipe.capabilities`) and nothing is removed.

    Error codes:
      WRONG_BUILD   -- object 24 is not 13.6's ALTERNATOR SYSTEM page with the verified donor
                       records.
      NO_FREE_GROUP -- no empty group-table slot, outside the reserved 68/69, to host the
                       button's command group.
      GROW_REFUSED  -- the binding table could not be grown (padding arena / gates), or the hold
                       donor group is not the verified shape.
      REPLACE_REFUSED -- the page's record stream could not be relocated.
    """

    hardware_verified = True     # coach-verified 2026-10-05: one 197F809F#0100FF.. per
                                 # 3-s hold on can1, the APS-500 re-claimed and sent its boot query

    PARAMS = {
        "properties": {
            "clear_faults": {"type": "boolean", "default": False,
                             "description": "also set GENERAL_RESET's clear-faults bits "
                                            "(0x05, rvc2mqtt's reset frame) instead of reboot "
                                            "alone (0x01)"},
        },
        "required": [],
    }

    def validate(self):
        self.clear_faults = self._param("clear_faults", False, kind=bool)

    def apply(self, data, ctx):
        try:
            altreboot.check_page(data)
        except altreboot.AltRebootError as e:
            raise RecipeError("WRONG_BUILD", str(e), op_id=self.spec.id)

        gt = guidata.group_table(data)
        binding_start = len(guidata.binding_table(data))
        # a hold-only control: `newbutton.hold_safe_groups` (never 68/69, never GuiCode-driven --
        # v45's 137 never fired on a hold -- and only a press-cycled slot)
        free = newbutton.hold_safe_groups(data, levelcontrols.RESERVED_GROUPS)
        if not free:
            raise RecipeError("NO_FREE_GROUP",
                              "no empty group-table slot to host the reboot command group",
                              op_id=self.spec.id)
        group = free[0]
        try:
            entry = altreboot.group_entry(gt[altreboot.HOLD_DONOR_GROUP].raw, binding_start)
            out, grow_edits = optionrow._grow_bindings(
                data, altreboot.binding(self.clear_faults), [(group, entry)])
        except (altreboot.AltRebootError, optionrow.OptionRowError) as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(grow_edits)

        recs = altreboot.check_page(out)
        gs, _ = guidata.guidata_extent(out)
        obj_start = gs + guidata.object_tables(out)[1][altreboot.PAGE_OBJECT]
        frags = altreboot.button_records(group)
        new_body = (bytes([out[obj_start] + len(frags)])
                    + bytes(out[obj_start + 1:recs[-1].end]) + b"".join(frags))
        try:
            out, repl_edits = relocate.replace_object_records(
                out, altreboot.PAGE_OBJECT, new_body)
        except relocate.RelocationError as e:
            raise RecipeError("REPLACE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(repl_edits)

        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {"object": altreboot.PAGE_OBJECT, "group": group,
                                     "touch_id": group, "binding_start": binding_start}
        return out

    def capability_delta(self, ctx):
        return set(), set()          # one 0x17F80 binding, channel None; nothing removed

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        placed = altreboot.placed_button(data, h["touch_id"])
        assert [p.wtype for p in placed] == [33, 11, 12], \
            "reboot button not found on the ALTERNATOR SYSTEM page (touch id %d)" % h["touch_id"]
        box, cap, _touch = placed
        assert (box.x0, box.y0, box.x1, box.y1) == altreboot.BUTTON_DRAWN, \
            "reboot button box placed at %s" % ((box.x0, box.y0, box.x1, box.y1),)
        assert cap.label == altreboot.BUTTON_TEXT, "caption reads %r" % cap.label

        g = guidata.group_table(data)[h["group"]]
        assert g.count == 1, "reboot group %d has count %d, expected 1" % (h["group"], g.count)
        assert g.raw[0] >> 4 == altreboot.HOLD_SECONDS, "hold threshold is %d s" % (g.raw[0] >> 4)
        assert g.raw[2] & 0x0C == 0, "held-phase repeat is not zero"
        raw = guidata.binding_table(data)[g.start].raw
        can_id, payload = newbutton.binding_wire_frame(raw)
        want = (altreboot.REBOOT_CLEAR_FAULTS_WIRE if self.clear_faults
                else altreboot.REBOOT_WIRE)
        assert (can_id, payload) == (0x197F809F, want), \
            "reboot binding emits %08X#%s" % (can_id, payload.hex())
        assert raw[3] == altreboot.HELD_MASK and not raw[2] & 0x04, \
            "reboot binding is not held-only without repeat"
        return ("add_alt_reboot: 'REBOOT APS-500' on ALTERNATOR SYSTEM at %s (touch id %d -> new "
                "group %d, binding %d) transmits %08X#%s once after a %d s hold; nothing on a tap"
                % (altreboot.BUTTON_DRAWN, h["touch_id"], h["group"], g.start, can_id,
                   payload.hex(), altreboot.HOLD_SECONDS))


@op("add_aes_batt_heat_status")
class AddAesBattHeatStatus(Op):
    """Make the coach-verified "Batt Heat" command button reflect two live BMS states carried by
    status DGN `0x18FF7070` (broadcast from SA `0x70` every ~2 s and immediately after a command):

      - **battery heater on/off** = status byte 2 bit 0 -> the button body reads **cyan** (on) /
        **button blue** (off), the standard Firefly pair Water Pump, Starlink, Overflow and Tank
        Heater use;
      - **battery heater contactor closed** = status byte 1 bit 2 -> a **red wavy-lines glyph**
        (font slot 23 char `'2'`) in the button's **top-right** corner. The contactor is a relay
        inside the battery pack; v31 called it a heat pump, which the owner corrected.

    The receive side **appends two new variable-table entries** for `0xFF70` (`append_filter`,
    coach-verified 2026-09-22), so no existing subscription is taken over -- an earlier revision
    retargeted DC_DIMMER donors (which paint G12 OUTPUTS rows, one of them the AES Start/Stop) and
    then the solar pair; both were the wrong trade once appending was proven.

    The display side needs a routine, not just a slot: a widget's `value_slot` indexes the
    **display-cell** table. Both appended vars are folded per tick into ONE fresh private cell by a
    `bitcombine` routine (bit 0 = heater, bit 1 = contactor), and ONE wtype-6 switch over four
    whole-button arms watches that cell.

    **One switch, not two** -- this is what v31 got wrong. The panel repaints only the arm a switch
    selects, so v31's separate body and glyph switches each left something stale: enabling the
    heater painted the body arm over the sibling 'Batt Heat' caption with nothing to redraw it, and
    disabling the contactor selected an empty arm that drew nothing, leaving the red glyph lit
    until the page was navigated away and back (both reported by the owner on 2026-09-22). Here
    every arm draws the opaque full-size capsule, then the caption, then the glyph if that state
    has one, so no state can leave another's pixels behind.

    The receive filter extracts a 2-bit field rather than a single bit, so the heater reads byte 2
    bits 0-1 and the contactor byte 1 bits 2-3; the routine masks each field down to bit 0 (byte 2
    bit 1 = "alarm mask active" is independent and reserved for a future button, and byte 1 bit 3
    is always 0).

    Layered AFTER `add_aes_batt_heat`, which it leaves untouched.

    Error codes:
      APPEND_REFUSED    -- the two 0xFF70 entries could not be appended (registry cap, arena, gate).
      NO_DISPLAY_CELL   -- the cell table could not be grown for the combined-state cell.
      NO_BODY_COLOUR    -- the blue/cyan body pair is not in this build's mode-7 palette.
      INDICATOR_REFUSED -- the routine, switch or an arm could not be built or spliced onto 829.
      DETOUR_REFUSED    -- the per-tick hook did not hold the `bl` the chain expects.
      BAD_REF           -- `button` names an op with no handle (not run, later, or disabled).
    """

    # COACH-VERIFIED 2026-09-22, both halves. RECEIVE: both appended subscriptions
    # delivered, all four states seen from injected 18FF70C0 frames. DRAWING: one combined
    # cell, one switch, whole-button arms, blue/cyan; injected frames TRANSITIONED through all six
    # edges (off -> heater -> heater+contactor -> contactor opens -> heater off -> contactor only)
    # and the owner confirmed the caption survives an enable and the glyph clears on a disable.
    hardware_verified = True

    PARAMS = {
        "properties": {
            "button": {"type": "object",
                       "description": "{ref: add_aes_batt_heat op id}: take the button's w/h/text "
                                      "from that op (default: the stock 122x173 'Batt Heat')"},
        },
        "required": [],
    }

    def validate(self):
        self.button = (self.spec.params or {}).get("button")
        if self.button is not None and not (isinstance(self.button, dict)
                                            and isinstance(self.button.get("ref"), str)):
            raise RecipeError("BAD_PARAMS", "button must be {\"ref\": <op id>}",
                              op_id=self.spec.id)

    def _button(self, ctx):
        """(w, h, text) of the button the arms redraw: from the referenced op, or the defaults."""
        if self.button is None:
            return aescompact.BATT_HEAT_W, aescompact.BATT_HEAT_H, aescompact.BATT_HEAT_TEXT
        bh = ctx.handles.get(self.button["ref"])
        if bh is None or "text" not in bh:
            raise RecipeError("BAD_REF", "button ref %r has no add_aes_batt_heat handle"
                              % self.button["ref"], op_id=self.spec.id)
        return bh["w"], bh["h"], bh["text"]

    def apply(self, data, ctx):
        bw, bh_, btext = self._button(ctx)
        # (1) RECEIVE: two APPENDED entries on 0xFF70 -- no existing subscription is taken over
        # (append_filter is coach-verified 2026-09-22).
        try:
            # Neither entry filters on instance (compare_byte is a don't-care byte once instance
            # stays FILTER_DISABLED); compare_byte=0x00 is pinned explicitly here, matching the
            # +5 byte this op has shipped with since v27/v32, so the flashed image and the
            # historical coach-verified builds keep compiling byte-identically now that
            # `NewFilter`'s own default changed (Task 2, `append_filter` instance/compare_byte).
            out, edits, (var_heat, var_con) = appendfilter.append_filters(data, [
                appendfilter.NewFilter(dgn=aescompact.BATT_HEAT_STATUS_DGN,
                                       mode=aescompact.BATT_HEAT_STATUS_HEATER_MODE,
                                       payload_byte=aescompact.BATT_HEAT_STATUS_HEATER_BYTE,
                                       compare_byte=0x00),
                appendfilter.NewFilter(dgn=aescompact.BATT_HEAT_STATUS_DGN,
                                       mode=aescompact.BATT_HEAT_STATUS_CONTACTOR_MODE,
                                       payload_byte=aescompact.BATT_HEAT_STATUS_CONTACTOR_BYTE,
                                       compare_byte=0x00)])
        except relocate.RelocationError as e:
            raise RecipeError("APPEND_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)

        # (2) DISPLAY: a widget's value_slot indexes the CELL table, never the variable table, and
        # a switch reads exactly ONE slot -- so both vars are folded per tick into ONE cell
        # (bit 0 = heater, bit 1 = contactor) by a single `bitcombine` routine, and one switch
        # owns the whole button. The cell mechanism is v25's live House battery and the
        # 2026-09-22 append probe; the fold is what lets one arm draw every pixel.
        try:
            out, cells, cell_edits = readout.allocate_fresh_cells(
                out, [(2, readout._TYPE_FOR_WIDTH[2])])
        except readout.ReadoutError as e:
            raise RecipeError("NO_DISPLAY_CELL", str(e), op_id=self.spec.id)
        ctx.edits.extend(cell_edits)
        (cell_state,) = cells

        sources = (bitcombine.BitSource(register.status_slot_addr(var_heat),
                                        aescompact.BATT_HEAT_HEATER_ON_MASK,
                                        aescompact.BATT_HEAT_HEATER_WEIGHT),
                   bitcombine.BitSource(register.status_slot_addr(var_con),
                                        aescompact.BATT_HEAT_CONTACTOR_ON_MASK,
                                        aescompact.BATT_HEAT_CONTACTOR_WEIGHT))
        try:
            plan = bitcombine.resolve(out, sources=sources, cell=cell_state)
            gc = theme.guicode_section(out)
            code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
            _check_append_point(code_load_addr, self.spec.id)
            blob, _ = bitcombine.build(plan, code_load_addr)
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except (bitcombine.BitCombineError, followchannel.FollowChannelError,
                growsection.GrowSectionError) as e:
            raise RecipeError("INDICATOR_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "batt-heat status: state fold (heater var %d %#x bit0, contactor var %d %#x bit1 "
            "-> cell %d @ %#x)" % (var_heat, sources[0].slot_addr, var_con,
                                   sources[1].slot_addr, cell_state.slot, cell_state.addr)))
        hook_at, hook_old, hook_new = bitcombine.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        if grown[file_off:file_off + 4] != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, grown[file_off:file_off + 4].hex(), hook_old.hex()),
                              op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, hook_old, hook_new, "GuiCode",
                         "batt-heat status: detour bl -> %#x" % code_load_addr))
        out, _ = patch.reseal(bytes(buf))

        # (3) the switch watches the COMBINED CELL, not either var.
        slot_state = cell_state.slot

        # The body pair comes from the firmware's own palette rather than from pinned indices, so
        # the button rests at exactly the blue every other button rests at and lights to exactly
        # their cyan. A static index is preferred (`resolve_fill_index`), which is what keeps the
        # fill from coupling to some other widget's live state -- v15's defect 1 (`aescompact.py`).
        try:
            off_index = theme.resolve_fill_index(out, color=aescompact.BATT_HEAT_OFF_RGB)
            on_index = theme.resolve_fill_index(out, color=aescompact.BATT_HEAT_ON_RGB)
        except theme.PaletteError as e:
            raise RecipeError("NO_BODY_COLOUR", str(e), op_id=self.spec.id)

        try:
            out, info = aescompact.add_batt_heat_state_switch(
                out, object_index=aescompact.ELECTRICAL_AES_BODY, value_slot=slot_state,
                donors=ctx.stock, off_index=off_index, on_index=on_index,
                w=bw, h=bh_, text=btext)
        except (ValueError, relocate.RelocationError) as e:
            raise RecipeError("INDICATOR_REFUSED", str(e), op_id=self.spec.id)

        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {
            "object": aescompact.ELECTRICAL_AES_BODY,
            "var_heat": var_heat,
            "var_contactor": var_con,
            "slot_state": slot_state,
            "cell_state_addr": cell_state.addr,
            "arms_base": info["arms_base"],
            "arm_objects": info["arm_objects"],
            "off_index": off_index,
            "on_index": on_index,
            "w": bw, "h": bh_, "text": btext,
        }
        return out

    def capability_delta(self, ctx):
        # Receive-only: no binding/transmit change, so the transmit-side capability set is
        # unchanged. (Retargeting inert entries changes the *received-DGN* set -- state the
        # capability gate does not track.)
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        vt = guidata.variable_table(data)
        ff70 = [v for v in vt if v.dgn_low == (aescompact.BATT_HEAT_STATUS_DGN & 0xFFFF)]
        assert len(ff70) == 2, "expected 2 variables receiving 0xFF70, found %d" % len(ff70)
        vh, vc = vt[h["var_heat"]], vt[h["var_contactor"]]
        assert (vh.raw[2] >> 4, vh.raw[3] & 7) == (
            aescompact.BATT_HEAT_STATUS_HEATER_MODE, aescompact.BATT_HEAT_STATUS_HEATER_BYTE), \
            "heater var extracts mode %d byte %d" % (vh.raw[2] >> 4, vh.raw[3] & 7)
        assert (vc.raw[2] >> 4, vc.raw[3] & 7) == (
            aescompact.BATT_HEAT_STATUS_CONTACTOR_MODE,
            aescompact.BATT_HEAT_STATUS_CONTACTOR_BYTE), \
            "contactor var extracts mode %d byte %d" % (vc.raw[2] >> 4, vc.raw[3] & 7)
        assert len(statusbind.registered_dgns(data)) <= statusbind.REGISTRY_CAP, \
            "status DGN pushed the variable table over REGISTRY_CAP"

        # THE GUARD the v28/v29 defect needed: the switch's value_slot must resolve, through the
        # cell table, to an address a GuiCode routine writes from BOTH vars' receive slots. A
        # switch pointed straight at a var index reads an unrelated cell and never moves, and a
        # render cannot tell the difference (the renderer injects values by value_slot).
        cell_table = status_map.cell_table(data)
        gc = theme.guicode_section(data)
        code = data[gc.offset:gc.offset + gc.length]
        slot, addr = h["slot_state"], h["cell_state_addr"]
        assert cell_table[slot] == addr, \
            "state cell index %d names %#x, not the routine's cell %#x" % (
                slot, cell_table[slot], addr)
        assert struct.pack("<I", addr) in code, \
            "no GuiCode routine carries the state cell %#x" % addr
        for var, what in ((h["var_heat"], "heater"), (h["var_contactor"], "contactor")):
            slot_addr = register.status_slot_addr(var)
            assert struct.pack("<I", slot_addr) in code, \
                "%s: no GuiCode routine carries var %d's receive slot %#x" % (what, var, slot_addr)

        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[h["object"]])
        sw = [r for r in frame if r.wtype == 6 and r.value_slot == slot]
        assert len(sw) == 1, "expected 1 state switch on cell %d, found %d" % (slot, len(sw))
        assert sw[0].count == len(aescompact.BATT_HEAT_STATE_ARMS), \
            "state switch has %d arms, expected %d" % (
                sw[0].count, len(aescompact.BATT_HEAT_STATE_ARMS))
        assert sw[0].header[4] & _ENROLL_BIT, "state switch not enrolled for live redraw"
        # The button is driven by that ONE switch and nothing else: the static capsule it replaced
        # is gone, and no second switch shares its arm band. (Object 829 carries the compact AES
        # frame's own switches too, so this is scoped to the button rather than to the object.)
        # scoped to THIS button (its capsule is followed by its own caption): a second BMS
        # button (Ignore Alarms, v49) carries the same static fill until its own status op runs
        assert not [i for i, r in enumerate(frame[:-1]) if r.wtype == 33 and r.colors
                    and r.colors[0] == ("fillA", 7, aescompact.BATT_HEAT_FILL_INDEX)
                    and frame[i + 1].wtype == 11 and frame[i + 1].label == h["text"]], \
            "the static batt-heat capsule survived; the body would not follow the heater"
        # The caption MOVED into the arms. A copy left in the stream is drawn a second time at the
        # pen the arm's caption advanced to -- the first v32 render showed 'Batt Heat' twice,
        # stacked -- so its absence here is the check, not an accident of ordering.
        assert not [r for r in frame
                    if r.wtype == 11 and r.label == h["text"]], \
            "object %d still carries a %r caption outside the switch arms; it would draw twice" % (
                h["object"], h["text"])
        band = range(h["arms_base"], h["arms_base"] + len(aescompact.BATT_HEAT_STATE_ARMS))
        others = [r for r in frame if r.wtype == 6 and r is not sw[0]
                  and r.child_index in band]
        assert not others, \
            "a second switch (slots %s) selects the Batt Heat arm band; each switch repaints only " \
            "its own arm, which is how an earlier build lost the caption and kept a stale glyph" % (
                [r.value_slot for r in others],)

        # THE GUARD for the v31 repaint defects: EVERY arm must draw the full-size opaque capsule
        # AND the caption, so selecting it erases whatever the outgoing arm left. An arm missing
        # either is the exact shape of both bugs the owner reported.
        want_w = h["w"] - 2 * aescompact.PUMP_OUTLINE_INSET
        want_h = h["h"] - 2 * aescompact.PUMP_OUTLINE_INSET
        for (match, heater_on, contactor_on), obj in zip(aescompact.BATT_HEAT_STATE_ARMS,
                                                         h["arm_objects"]):
            arm = records.object_records(data, gs + tab[obj])
            want_fill = h["on_index"] if heater_on else h["off_index"]
            caps = [r for r in arm if r.wtype == 33
                    and r.colors and r.colors[0] == ("fillA", 7, want_fill)
                    and r.geom[2:4] == [want_w, want_h]]
            assert len(caps) == 1, \
                "arm %d (match %#x) has %d full-size capsules in fill %d, expected 1" % (
                    obj, match, len(caps), want_fill)
            assert any(r.wtype == 11 and r.label == h["text"] for r in arm), \
                "arm %d (match %#x) does not draw the %r caption, so selecting it would wipe it" % (
                    obj, match, h["text"])
            glyphs = [r for r in arm if r.wtype == 11
                      and r.label == aescompact.CONTACTOR_GLYPH_CHAR
                      and r.font == aescompact.CONTACTOR_GLYPH_FONT_SLOT]
            assert len(glyphs) == (1 if contactor_on else 0), \
                "arm %d (match %#x) has %d contactor glyphs, expected %d" % (
                    obj, match, len(glyphs), 1 if contactor_on else 0)

        pal = theme.mode7_palette(data)
        assert pal.get(h["off_index"]) == aescompact.BATT_HEAT_OFF_RGB, \
            "off fill index %d is %s, not the button blue %s" % (
                h["off_index"], pal.get(h["off_index"]), aescompact.BATT_HEAT_OFF_RGB)
        assert pal.get(h["on_index"]) == aescompact.BATT_HEAT_ON_RGB, \
            "on fill index %d is %s, not the cyan %s" % (
                h["on_index"], pal.get(h["on_index"]), aescompact.BATT_HEAT_ON_RGB)

        return ("add_aes_batt_heat_status: APPENDED vars %d (heater, byte 2 bits 0-1) and %d "
                "(contactor, byte 1 bits 2-3) receive 0x18FF7070 and are folded per tick into "
                "cell %d (bit 0 heater, bit 1 contactor), which ONE %d-arm switch watches; every "
                "arm redraws the whole button (blue %d / cyan %d capsule + caption + glyph), so "
                "no state leaves another's pixels; no existing subscription taken over"
                % (h["var_heat"], h["var_contactor"], slot,
                   len(aescompact.BATT_HEAT_STATE_ARMS), h["off_index"], h["on_index"]))


@op("aes_card_layout")
class AesCardLayout(Op):
    """Re-lay out the compact AES ELECTRICAL frame (object 829) as HOME's 2x2 card grid --
    Batteries, Inverter, Auto Engine Start, Water Pump -- with the controls at their stock
    sizes.

    Runs AFTER swap_aes_electrical, add_aes_batt_heat and add_aes_batt_heat_status (built with
    the full-width heater: `x 84, y 156, w 328, h 54`). Asserts the exact v47 record shapes of
    829 and the private AES cluster and refuses otherwise. Edits only those two objects: shared
    objects move through the records that place them (`cardlayout.py`).

    Error codes:
      WRONG_SHAPE     -- 829 or the cluster is not the v47 shape (cardlayout.CardLayoutError).
      REPLACE_REFUSED -- a record stream could not be relocated.
    """

    hardware_verified = True     # flashed 2026-10-15; draw defects fixed same day

    PARAMS = {"properties": {}, "required": []}

    def validate(self):
        pass

    def apply(self, data, ctx):
        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
        try:
            cluster = frame[aescompact.frame_roles(data, frame)["cluster"]].child_index
            out, info = cardlayout.apply_card_layout(data, cluster)
        except relocate.RelocationError as e:      # a ValueError subclass: catch it first
            raise RecipeError("REPLACE_REFUSED", str(e), op_id=self.spec.id)
        except (ValueError, IndexError) as e:
            raise RecipeError("WRONG_SHAPE", str(e), op_id=self.spec.id)
        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = info
        return out

    def capability_delta(self, ctx):
        return set(), set()          # layout only: no group, binding or filter changes

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[h["object"]])
        cards = [r for r in frame[:4] if r.wtype == 33
                 and r.colors[0] == ("fillA", 7, cardlayout.CARD_FILL_INDEX)]
        assert len(cards) == 4, "the four leading cards are missing"
        n = len(cardlayout.TITLE_TEXTS)
        assert [r.label for r in frame[4:4 + n]] == list(cardlayout.TITLE_TEXTS), \
            "the titles do not directly follow the cards (a later fill would paint over them)"
        sw = records.object_records(data, gs + tab[h["cluster"]])[cardlayout.CLUSTER_PILL_SWITCH]
        assert sw.child_index == h["pill_band"], "AES pill switch is not on the private band"
        titles = [r.label for r in frame if r.wtype == 11 and r.label in cardlayout.TITLE_TEXTS]
        assert titles == list(cardlayout.TITLE_TEXTS), "card titles %s" % titles
        assert not any(r.label == "Triggers:" for r in records.object_records(
            data, gs + tab[h["cluster"]])), "'Triggers:' is still drawn"
        return ("aes_card_layout: ELECTRICAL as four cards %s, controls at stock sizes"
                % (list(cardlayout.TITLE_TEXTS),))


@op("add_card_readouts")
class AddCardReadouts(Op):
    """Captioned live readouts inside an aes_card_layout card (v49: the Inverter card's right
    column).

    Each row is a white caption over a right-justified value and its unit. A value names either
    an `add_register_readout` op built with `draw: false` (`{"ref": id}`, its display cell) or an
    existing display slot (`{"slot": N}`, e.g. 232, the DC Current INVERTER / CHARGER SETTINGS
    draws). One private object holds every row; its attach goes into 829 directly after the
    card titles (`cardreadouts.attach`). Runs after `aes_card_layout`.

    Error codes:
      WRONG_SHAPE     -- 829 is not aes_card_layout's frame, `parent` (another object) has no
                         wtype-5 attach template, or a record failed its self-decode.
      BAD_REF         -- a row's ref names no draw:false readout handle.
      REPLACE_REFUSED -- 829 could not be relocated.
    """

    hardware_verified = True     # flashed 2026-10-15: readouts live on the coach

    PARAMS = {
        "properties": {
            "rows": {"type": "array",
                     "description": "[{caption, value: {ref}|{slot}, format: {width, places}, "
                                    "unit}], top to bottom"},
            "x_caption": {"type": "integer", "default": 684},
            "pen_x": {"type": "integer", "default": 760,
                      "description": "right edge of the values (they right-justify)"},
            "unit_x": {"type": "integer", "default": 763},
            "y0": {"type": "integer", "default": 78},
            "pitch": {"type": "integer", "default": 34},
            "parent": {"description": "object the readouts attach to: an object index or "
                                      "{ref: op_id}; default 829 (aes_card_layout's frame)",
                       "default": 829},
        },
        "required": ["rows"],
    }

    def validate(self):
        rows = self._param("rows", kind=list)
        if not rows:
            raise RecipeError("BAD_PARAMS", "rows is empty", op_id=self.spec.id)
        for r in rows:
            v = r.get("value") if isinstance(r, dict) else None
            if (not isinstance(r, dict) or not isinstance(r.get("caption"), str)
                    or not isinstance(r.get("unit"), str) or not isinstance(v, dict)
                    or (("ref" in v) == ("slot" in v))):
                raise RecipeError("BAD_PARAMS", "row %r needs caption, unit and value "
                                  "{ref} or {slot}" % (r,), op_id=self.spec.id)
        self.rows = rows
        self.geom = {k: self._param(k, d, kind=int) for k, d in
                     (("x_caption", 684), ("pen_x", 760), ("unit_x", 763), ("y0", 78),
                      ("pitch", 34))}
        self.parent = self._param("parent", aescompact.ELECTRICAL_AES_BODY)

    def apply(self, data, ctx):
        parent = resolve_target(ctx, self.parent)
        rows = []
        for r in self.rows:
            v = r["value"]
            if "ref" in v:
                h = ctx.handles.get(v["ref"])
                if h is None or "display_slot" not in h or h.get("object_index") is not None:
                    raise RecipeError("BAD_REF", "row %r: ref %r is not a draw:false "
                                      "add_register_readout" % (r["caption"], v["ref"]),
                                      op_id=self.spec.id)
                slot = h["display_slot"]
            else:
                slot = int(v["slot"])
            fmt = r.get("format", {})
            rows.append(cardreadouts.Row(r["caption"], slot, int(fmt.get("width", 0)),
                                         int(fmt.get("places", 0)), r["unit"]))
        try:
            obj = cardreadouts.build_object(data, rows, **self.geom)
            out, index = cardreadouts.attach(data, obj, parent)
        except insert.InsertError as e:
            if parent == aescompact.ELECTRICAL_AES_BODY:    # 829's refusals stay REPLACE_REFUSED
                raise RecipeError("REPLACE_REFUSED", str(e), op_id=self.spec.id)
            raise RecipeError("WRONG_SHAPE", str(e), op_id=self.spec.id)
        except relocate.RelocationError as e:      # a ValueError subclass: catch it first
            raise RecipeError("REPLACE_REFUSED", str(e), op_id=self.spec.id)
        except (ValueError, readout.ReadoutError, insert.InsertError) as e:
            raise RecipeError("WRONG_SHAPE", str(e), op_id=self.spec.id)
        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {"object": parent,
                                     "readouts": index,
                                     "rows": [(x.caption, x.slot, x.unit) for x in rows]}
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[h["object"]])
        if h["object"] == aescompact.ELECTRICAL_AES_BODY:
            att = frame[cardreadouts.ATTACH_AT]
            assert att.wtype == 5 and att.child_index == h["readouts"], \
                "the readouts attach is not at 829[%d]" % cardreadouts.ATTACH_AT
        else:
            assert any(r.wtype == 5 and r.child_index == h["readouts"] for r in frame), \
                "the readouts attach is not in object %d" % h["object"]
        recs = records.object_records(data, gs + tab[h["readouts"]])
        got = [(c.label, v.value_slot, u.label) for c, v, u in
               zip([r for r in recs if r.wtype == 11][0::2], [r for r in recs if r.wtype == 8],
                   [r for r in recs if r.wtype == 11][1::2])]
        assert got == [tuple(x) for x in h["rows"]], "rows read back as %s" % got
        return ("add_card_readouts: %d rows %s in object %d, attached to object %d"
                % (len(got), [g[0] for g in got], h["readouts"], h["object"]))


@op("add_aes_bms_button_status")
class AddAesBmsButtonStatus(Op):
    """Light a BMS command button (an `add_aes_batt_heat` instance, e.g. v49's Ignore Alarms) from
    ONE bit of the BMS status the heater status already receives: button blue while the bit is
    clear (or nothing has arrived), cyan while it is set.

    `status` names the `add_aes_batt_heat_status` op whose heater var extracts `0x18FF7070`
    byte 2 bits 0-1 (mode 4); `mask` picks the bit inside that field (2 = byte 2 bit 1, "alarm
    mask active"). No new subscription: a one-source `bitcombine` fold writes a fresh cell, and
    ONE two-arm switch over whole-button arms (`aescompact.BMS_BIT_STATE_ARMS`) replaces the
    button's static capsule -- the same repaint-safe shape as the heater (v32).

    Error codes:
      BAD_REF           -- `button` / `status` name no matching handle.
      NO_DISPLAY_CELL   -- the cell table could not be grown.
      NO_BODY_COLOUR    -- the blue/cyan pair is not in this build's palette.
      INDICATOR_REFUSED -- the routine or switch could not be built or spliced onto 829.
      DETOUR_REFUSED    -- the per-tick hook did not hold the expected `bl`.
    """

    hardware_verified = False

    PARAMS = {
        "properties": {
            "button": {"type": "object", "description": "{ref: add_aes_batt_heat op id}"},
            "status": {"type": "object", "description": "{ref: add_aes_batt_heat_status op id}"},
            "mask": {"type": "integer", "description": "bit(s) of the heater var's 2-bit field "
                                                       "that mean 'on' (1..3)"},
        },
        "required": ["button", "status", "mask"],
    }

    def validate(self):
        self.button = self._param("button", kind=dict)
        self.status = self._param("status", kind=dict)
        self.mask = self._param("mask", kind=int)
        if not 1 <= self.mask <= 3:
            raise RecipeError("BAD_PARAMS", "mask must be 1..3 (a 2-bit field)",
                              op_id=self.spec.id)

    def apply(self, data, ctx):
        bh = ctx.handles.get(self.button.get("ref"))
        sh = ctx.handles.get(self.status.get("ref"))
        if bh is None or "text" not in bh or "group" not in bh:
            raise RecipeError("BAD_REF", "button ref %r is not an add_aes_batt_heat handle"
                              % self.button.get("ref"), op_id=self.spec.id)
        if sh is None or "var_heat" not in sh:
            raise RecipeError("BAD_REF", "status ref %r is not an add_aes_batt_heat_status handle"
                              % self.status.get("ref"), op_id=self.spec.id)
        try:
            out, cells, cell_edits = readout.allocate_fresh_cells(
                data, [(2, readout._TYPE_FOR_WIDTH[2])])
        except readout.ReadoutError as e:
            raise RecipeError("NO_DISPLAY_CELL", str(e), op_id=self.spec.id)
        ctx.edits.extend(cell_edits)
        (cell,) = cells
        source = bitcombine.BitSource(register.status_slot_addr(sh["var_heat"]), self.mask, 1)
        try:
            plan = bitcombine.resolve(out, sources=(source,), cell=cell)
            gc = theme.guicode_section(out)
            code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
            _check_append_point(code_load_addr, self.spec.id)
            blob, _ = bitcombine.build(plan, code_load_addr)
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except (bitcombine.BitCombineError, followchannel.FollowChannelError,
                growsection.GrowSectionError) as e:
            raise RecipeError("INDICATOR_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "bms-button status: var %d mask %d -> cell %d @ %#x"
            % (sh["var_heat"], self.mask, cell.slot, cell.addr)))
        hook_at, hook_old, hook_new = bitcombine.detour_edit(plan, code_load_addr)
        file_off = gcs.offset + hook_at
        if grown[file_off:file_off + 4] != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, grown[file_off:file_off + 4].hex(), hook_old.hex()),
                              op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, hook_old, hook_new, "GuiCode",
                         "bms-button status: detour bl -> %#x" % code_load_addr))
        out, _ = patch.reseal(bytes(buf))
        try:
            off_index = theme.resolve_fill_index(out, color=aescompact.BATT_HEAT_OFF_RGB)
            on_index = theme.resolve_fill_index(out, color=aescompact.BATT_HEAT_ON_RGB)
        except theme.PaletteError as e:
            raise RecipeError("NO_BODY_COLOUR", str(e), op_id=self.spec.id)
        try:
            out, info = aescompact.add_batt_heat_state_switch(
                out, object_index=aescompact.ELECTRICAL_AES_BODY, value_slot=cell.slot,
                donors=ctx.stock, off_index=off_index, on_index=on_index,
                w=bh["w"], h=bh["h"], text=bh["text"], arms=aescompact.BMS_BIT_STATE_ARMS)
        except (ValueError, relocate.RelocationError) as e:
            raise RecipeError("INDICATOR_REFUSED", str(e), op_id=self.spec.id)
        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {
            "object": aescompact.ELECTRICAL_AES_BODY, "slot": cell.slot, "cell_addr": cell.addr,
            "var": sh["var_heat"], "mask": self.mask, "arms_base": info["arms_base"],
            "text": bh["text"], "w": bh["w"], "h": bh["h"],
            "off_index": off_index, "on_index": on_index,
        }
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        frame = records.object_records(data, gs + tab[h["object"]])
        sw = [r for r in frame if r.wtype == 6 and r.value_slot == h["slot"]]
        assert len(sw) == 1 and sw[0].count == len(aescompact.BMS_BIT_STATE_ARMS), \
            "expected one %d-arm switch on cell %d" % (len(aescompact.BMS_BIT_STATE_ARMS),
                                                       h["slot"])
        assert sw[0].header[4] & _ENROLL_BIT, "switch not enrolled for live redraw"
        assert status_map.cell_table(data)[h["slot"]] == h["cell_addr"]
        want = [h["w"] - 2 * aescompact.PUMP_OUTLINE_INSET, h["h"] - 2 * aescompact.PUMP_OUTLINE_INSET]
        for (_m, on, _g), obj in zip(aescompact.BMS_BIT_STATE_ARMS,
                                     range(h["arms_base"], h["arms_base"] + 2)):
            arm = records.object_records(data, gs + tab[obj])
            fill = h["on_index"] if on else h["off_index"]
            assert arm[0].wtype == 33 and arm[0].colors[0] == ("fillA", 7, fill) \
                and arm[0].geom[2:4] == want, "arm %d does not draw the full button" % obj
            assert any(r.wtype == 11 and r.label == h["text"] for r in arm), \
                "arm %d lacks the %r caption" % (obj, h["text"])
        gc = theme.guicode_section(data)
        code = data[gc.offset:gc.offset + gc.length]
        assert struct.pack("<II", register.status_slot_addr(h["var"]), h["cell_addr"]) in code, \
            "no routine folds var %d into cell %#x" % (h["var"], h["cell_addr"])
        return ("add_aes_bms_button_status: %r lit by var %d mask %d via cell %d (2-arm switch, "
                "blue/cyan); UNVERIFIED on the coach" % (h["text"], h["var"], h["mask"],
                                                        h["slot"]))


@op("add_enum_label")
class AddEnumLabel(Op):
    """Show a value as text from a table, on any page: `{"0": "Off", "1": "Cool", ...}`.

    A per-tick routine (`statusfold.Enum`, one per op, chained on the per-tick hook) reads the
    source slot, masks and shifts it, and writes a display cell: the index 0..n-1 of the matching
    key, or n for stale or any value not in `values`. The label is one wtype-6 switch on that
    cell (enrolled for live redraw) with n+1 arms matched on 0..n, the last drawing `default`;
    every value the routine writes is matched (`enumlabel`). Each arm is a wtype-11 label in a
    declared `w` x `h` box (the panel clips text to it, `GUICODE.md` Findings 69-70), after a
    `background` fill when one is given. The switch is absolute at `x, y`, in a new object
    attached under `parent` (`insert.insert_object`), ended by an empty label that hands back
    the font and align the page walk carried in (`enumlabel.restore_record`). The cell is always
    freshly grown (`readout.allocate_fresh_cells`, recorded in `ctx.authored_cells`). After
    placement every page root reaching `parent`, in every switch-arm set, is walked before and
    after (`enumlabel.foreign_deltas`): any change besides the label's own records refuses.

    Params:
      parent: object index | {ref} -- the object the label attaches under (it needs a wtype-5
              attach record to clone).
      x, y: absolute pen; w, h: the label box (inclusive); font: the font slot.
      align: "left" | "centre" | "right" (default "left").
      colour: [r, g, b] text colour (default white); background: [r, g, b] | null (default
              null), the arm's fill. Both must be palette colours.
      source: {ref} | {var} | {dgn, instance|selector}, with optional mask (default 0xFFFF) and
              shift (default 0) applied to the slot before the lookup.
      values: {"<int>": text}, 1-16 entries; default: text for stale and unlisted values ("--").

    Error codes:
      BAD_PARAMS       -- a malformed param: empty or >16 `values`, a non-integer or duplicate
                          key, an empty text, an unknown align, mask/shift out of range, a
                          source that is not exactly one form, or a non-positive box.
      BAD_REF          -- a `parent` or `source` ref names no usable handle.
      NO_SUCH_REGISTER -- the source names no variable-table entry.
      NO_DISPLAY_CELL  -- the cell table could not be grown.
      NO_COLOUR        -- `colour` or `background` is not in this build's palette.
      LABEL_REFUSED    -- an arm, the routine or the switch could not be built or placed; the
                          entering font/align cannot be measured or written back (no page
                          reaches `parent`, pages disagree, no font yet, align 0); or the
                          placement changes any other record on a page reaching `parent`.
      OFF_CANVAS       -- the box leaves the 800x480 canvas.
      DETOUR_REFUSED   -- the per-tick hook did not hold the expected `bl`.
    """

    hardware_verified = False

    PARAMS = {
        "properties": {
            "parent": {"description": "object index or {ref: op_id}"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "w": {"type": "integer"}, "h": {"type": "integer"},
            "font": {"type": "integer"},
            "align": {"type": "string", "default": "left",
                      "description": "left | centre | right"},
            "colour": {"type": "array", "default": [255, 255, 255]},
            "background": {"type": ["array", "null"], "default": None},
            "source": {"type": "object",
                       "description": "{ref}|{var}|{dgn, instance|selector}, + mask?, shift?"},
            "values": {"type": "object", "description": "{\"<int>\": text}, 1-16 entries"},
            "default": {"type": "string", "default": "--"},
        },
        "required": ["parent", "x", "y", "w", "h", "font", "source", "values"],
    }

    def _bad(self, msg):
        return RecipeError("BAD_PARAMS", msg, op_id=self.spec.id)

    def _rgb(self, name, v):
        if v is None:
            return None
        if not (isinstance(v, list) and len(v) == 3
                and all(isinstance(c, int) and 0 <= c <= 0xFF for c in v)):
            raise self._bad("%s must be [r, g, b] of 0..255, got %r" % (name, v))
        return tuple(v)

    def validate(self):
        self.parent = self._param("parent")
        self.x, self.y, self.w, self.h, self.font = (
            self._param(k, kind=int) for k in ("x", "y", "w", "h", "font"))
        if self.w <= 0 or self.h <= 0:
            raise self._bad("the label box must be positive, got %dx%d" % (self.w, self.h))
        if not 0 <= self.font <= 0xFE:
            raise self._bad("font must be a slot 0..0xFE, got %d" % self.font)
        align = self._param("align", "left", kind=str)
        if align not in enumlabel.ALIGNS:
            raise self._bad("align must be one of %s, got %r" % (sorted(enumlabel.ALIGNS), align))
        self.align = enumlabel.ALIGNS[align]
        self.colour = self._rgb("colour", self._param("colour", [255, 255, 255]))
        self.background = self._rgb("background", self._param("background", None))
        self.source = self._param("source", kind=dict)
        self.mask = self.source.get("mask", 0xFFFF)
        self.shift = self.source.get("shift", 0)
        if not (isinstance(self.mask, int) and 1 <= self.mask <= 0xFFFF):
            raise self._bad("source mask must be 1..0xFFFF, got %r" % (self.mask,))
        if not (isinstance(self.shift, int) and 0 <= self.shift <= 15):
            raise self._bad("source shift must be 0..15, got %r" % (self.shift,))
        values = self._param("values", kind=dict)
        if not 1 <= len(values) <= statusfold.MAX_KEYS:
            raise self._bad("values takes 1-%d entries, got %d" % (statusfold.MAX_KEYS,
                                                                  len(values)))
        table = {}
        for k, text in values.items():
            if not (isinstance(k, str) and k.isdigit() and str(int(k)) == k
                    and int(k) <= 0xFFFF):
                raise self._bad("values key %r is not a decimal integer 0..65535" % (k,))
            if not isinstance(text, str) or not text:
                raise self._bad("values[%r] must be a non-empty string, got %r" % (k, text))
            table[int(k)] = text
        self.keys = tuple(sorted(table))
        self.texts = [table[k] for k in self.keys]
        self.default = self._param("default", "--", kind=str)
        if not self.default:
            raise self._bad("default must be a non-empty string")
        if (self.x < 0 or self.y < 0 or self.x + self.w > _CANVAS_W
                or self.y + self.h > _CANVAS_H):
            raise RecipeError("OFF_CANVAS", "the label box (%d,%d,%d,%d) leaves the %dx%d canvas"
                              % (self.x, self.y, self.x + self.w, self.y + self.h, _CANVAS_W,
                                 _CANVAS_H), op_id=self.spec.id)

    def apply(self, data, ctx):
        oid = self.spec.id
        parent = resolve_target(ctx, self.parent)
        slot_addr, var, live_mode = _resolve_status_source(data, ctx, self.source, oid)
        try:
            colour = theme.resolve_fill_index(data, color=self.colour)
            bg = (None if self.background is None
                  else theme.resolve_fill_index(data, color=self.background))
        except theme.PaletteError as e:
            raise RecipeError("NO_COLOUR", str(e), op_id=oid)
        try:
            arms = [enumlabel.build_arm(data, text=t, font=self.font, w=self.w, h=self.h,
                                        align=self.align, colour_index=colour, bg_index=bg)
                    for t in self.texts + [self.default]]
        except enumlabel.EnumLabelError as e:
            raise RecipeError("LABEL_REFUSED", str(e), op_id=oid)
        # Always a freshly grown cell (ruling R8): a reused stock index can alias another
        # object's read (on stock, `scan_display_cell`'s cell 211 is object 444's gauge level).
        try:
            out, cells, cell_edits = readout.allocate_fresh_cells(
                data, [(2, readout._TYPE_FOR_WIDTH[2])])
        except readout.ReadoutError as e:
            raise RecipeError("NO_DISPLAY_CELL", str(e), op_id=oid)
        ctx.edits.extend(cell_edits)
        ctx.authored_cells.update(c.slot for c in cells)    # gated: must be freshly grown
        ((slot, cell_addr),) = [(c.slot, c.addr) for c in cells]
        item = statusfold.Enum(slot_addr=slot_addr, mask=self.mask, shift=self.shift,
                               keys=self.keys, cell_addr=cell_addr)
        try:
            plan = statusfold.resolve(out, [item])
        except followchannel.FollowChannelError as e:
            raise RecipeError("DETOUR_REFUSED", str(e), op_id=oid)
        except statusfold.StatusFoldError as e:
            raise RecipeError("LABEL_REFUSED", str(e), op_id=oid)
        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, oid)
        try:
            blob, _ = statusfold.build(plan, code_load_addr)
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except (statusfold.StatusFoldError, growsection.GrowSectionError) as e:
            raise RecipeError("LABEL_REFUSED", str(e), op_id=oid)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "enum label: var %d mask %#x shift %d keys %s -> cell %d @ %#x"
            % (var, self.mask, self.shift, list(self.keys), slot, cell_addr)))
        try:
            hook_at, hook_old, hook_new = statusfold.detour_edit(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("DETOUR_REFUSED", str(e), op_id=oid)
        file_off = gcs.offset + hook_at
        if grown[file_off:file_off + 4] != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, grown[file_off:file_off + 4].hex(), hook_old.hex()),
                              op_id=oid)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, hook_old, hook_new, "GuiCode",
                         "enum label: detour bl -> %#x" % code_load_addr))
        out, _ = patch.reseal(bytes(buf))
        try:
            placed, _edits, index = enumlabel.place(out, parent=parent, x=self.x, y=self.y,
                                                    arms=arms, value_slot=slot)
            restore = self._restore(placed, parent)
            if restore is not None:
                placed, _edits, index = enumlabel.place(out, parent=parent, x=self.x, y=self.y,
                                                        arms=arms, value_slot=slot,
                                                        restore=restore)
            bad = enumlabel.foreign_deltas(out, placed, parent, index)
        except enumlabel.EnumLabelError as e:
            raise RecipeError("LABEL_REFUSED", str(e), op_id=oid)
        if bad:
            raise RecipeError("LABEL_REFUSED", "placing the label under object %d changes %d "
                              "record(s) besides its own:\n%s"
                              % (parent, len(bad), pagewalk.describe(bad[:20])), op_id=oid)
        out, _ = patch.reseal(placed)
        gs, tab = _gs_tab(out)
        sw = records.object_records(out, gs + tab[index])[0]
        ctx.handles[oid] = {
            "object": index, "parent": parent, "slot": slot, "cell_addr": cell_addr,
            "arms_base": sw.child_index, "var": var, "slot_addr": slot_addr,
            "live_mode": live_mode, "mask": self.mask, "shift": self.shift,
            "values": dict(zip(self.keys, self.texts)), "default": self.default,
        }
        return out

    def _restore(self, placed, parent):
        """The `(font, align)` to write back after the label (`enumlabel.place`'s `restore`), or
        None when the walk already carries the label's own state past it. Raises
        `EnumLabelError` when the entering state cannot be determined or written back: no page
        reaches `parent`, pages disagree, no font is set yet (0xFF cannot be restored) or the
        align is 0 (the decoder never stores a 0 align)."""
        gs, tab = _gs_tab(placed)
        attach = len(records.object_records(placed, gs + tab[parent])) - 1
        states = enumlabel.entering_state(placed, parent, attach)
        if len(states) > 1:
            raise enumlabel.EnumLabelError(
                "the pages reaching object %d enter it with different font/align states %s; "
                "one label cannot restore them all" % (parent, sorted(states, key=str)))
        if not states:
            raise enumlabel.EnumLabelError(
                "no page root reaches object %d, so the font/align the label must hand back "
                "cannot be measured" % parent)
        ((font, align),) = states
        if font is None or align == 0:
            raise enumlabel.EnumLabelError(
                "the walk enters object %d with font %r / align %d; the label's font %d / align "
                "%d cannot be written back to that" % (parent, font, align, self.font,
                                                       pd.ALIGN_NO_SHIFT))
        font = None if font == self.font else font
        align = 0 if align == pd.ALIGN_NO_SHIFT else align
        return None if font is None and not align else (font, align)

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        parent = records.object_records(data, gs + tab[h["parent"]])
        assert any(r.wtype == 5 and r.child_index == h["object"] for r in parent), \
            "the label object %d is not attached under %d" % (h["object"], h["parent"])
        sw = records.object_records(data, gs + tab[h["object"]])[0]
        n = len(h["values"])
        assert sw.wtype == 6 and sw.value_slot == h["slot"] and sw.count == n + 1, \
            "expected one %d-arm switch on cell %d" % (n + 1, h["slot"])
        assert sw.header[4] & _ENROLL_BIT, "switch not enrolled for live redraw"
        cands = guidata.switch_candidates(data, sw, guidata.find_match_table(data))
        by_value = dict(cands)
        assert sorted(by_value) == list(range(n + 1)) and len(cands) == n + 1, \
            "match values %s" % cands
        want = [h["values"][k] for k in sorted(h["values"])] + [h["default"]]
        got = []
        for k in range(n + 1):
            arm = records.object_records(data, gs + tab[by_value[k]])
            lab = [r for r in arm if r.wtype == 11]
            assert len(lab) == 1 and lab[0].geom[2] and lab[0].geom[3], \
                "arm %d needs one label with a declared box" % k
            got.append(lab[0].label)
        assert got == want, "arm texts %s, expected %s" % (got, want)
        assert status_map.cell_table(data)[h["slot"]] == h["cell_addr"]
        gc = theme.guicode_section(data)
        code = data[gc.offset:gc.offset + gc.length]
        assert struct.pack("<II", h["slot_addr"], h["cell_addr"]) in code, \
            "no routine maps var %d into cell %#x" % (h["var"], h["cell_addr"])
        return ("add_enum_label: var %d (mask %#x, shift %d) -> fresh cell %d -> %d-arm "
                "switch %s + %r in object %d under %d; UNVERIFIED on the coach"
                % (h["var"], h["mask"], h["shift"], h["slot"], n + 1, got[:-1],
                   h["default"], h["object"], h["parent"]))


@op("add_batt_heat_battery_glyph")
class AddBattHeatBatteryGlyph(Op):
    """Show the battery heater CONTACTOR on the House battery icons: while it is closed, a WHITE
    wavy-lines glyph (the Batt Heat button's font slot 23 char '2') sits in the bottom-right
    corner of each named battery, and each battery's voltage number is raised
    `battglyph.NUMBER_RAISE` px in every state so the glyph never touches it.

    Each battery object is rewritten into ONE enrolled wt6 switch on the Batt Heat state cell
    (from the `add_aes_batt_heat_status` handle) over four arms that each draw the WHOLE battery;
    the contactor arms add the glyph. The enrolled containers that repaint the battery on every
    voltage change are untouched and walk the switch, so both repaint paths draw a complete,
    current battery (see `battglyph`).

    `style: "outline"` (owner, 2026-10-05) replaces the glyph: the number stays where stock put
    it, and while the contactor is closed the battery's terminals and a ring round its body turn
    `outline_rgb` (orange by default; `battglyph`'s outline notes). The non-contactor arms are the
    stock battery byte for byte.

    Layer after `add_aes_batt_heat_status` and after `add_voltage_battery` when a battery is its
    clone.

    Params:
      status: str (default "status") -- the add_aes_batt_heat_status op id
      batteries: list of {"object": int} or {"ref": op_id} -- the battery objects; a ref names an
        add_voltage_battery op and takes its clone
      style: "glyph" (default) | "outline" -- the contactor mark
      outline_rgb: [r, g, b] (default battglyph.OUTLINE_RGB) -- the outline style's colour

    Error codes:
      BAD_REF          -- `status` or a battery ref names no prior op of the right kind.
      NOT_A_BATTERY    -- a named object is not a House battery (seven-record signature).
      WRAP_REFUSED     -- the arms, the match table or the switch could not be built or spliced.
    """
    # COACH-VERIFIED 2026-09-26: the white glyph appears and clears with
    # the heater contactor on the HOME and ELECTRICAL House batteries. Owner note: with the voltage
    # raised 11 px the number is harder to read; the layout is open for redesign.
    # COACH-VERIFIED (outline style): flashed, the numbers sit at their stock position, and
    # the owner saw the House battery outlined orange with the heater running (reported 2026-10-10).
    hardware_verified = True

    PARAMS = {
        "properties": {
            "status": {"type": "string", "default": "status",
                       "description": "id of the add_aes_batt_heat_status op"},
            "batteries": {"type": "array",
                          "description": "battery objects: {\"object\": N} or {\"ref\": op_id} "
                                         "(an add_voltage_battery op's clone)"},
            "style": {"type": "string", "enum": list(battglyph.STYLES), "default": "glyph",
                      "description": "contactor mark: the white glyph with the number raised, "
                                     "or an outline_rgb outline"},
            "outline_rgb": {"type": "array", "default": list(battglyph.OUTLINE_RGB),
                            "description": "the outline style's [r, g, b]"},
        },
        "required": ["batteries"],
    }

    def validate(self):
        self.status = self._param("status", "status", kind=str)
        self.batteries = self._param("batteries", kind=list)
        self.style = self._param("style", "glyph", kind=str)
        if self.style not in battglyph.STYLES:
            raise RecipeError("BAD_PARAMS", "style %r is not one of %s"
                              % (self.style, battglyph.STYLES), op_id=self.spec.id)
        rgb = self._param("outline_rgb", list(battglyph.OUTLINE_RGB), kind=list)
        if not (len(rgb) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)):
            raise RecipeError("BAD_PARAMS", "outline_rgb %r is not [r, g, b]" % (rgb,),
                              op_id=self.spec.id)
        self.outline_rgb = tuple(rgb)
        if not self.batteries:
            raise RecipeError("BAD_PARAMS", "batteries is empty", op_id=self.spec.id)
        for b in self.batteries:
            if not (isinstance(b, dict) and len(b) == 1
                    and (isinstance(b.get("object"), int) or isinstance(b.get("ref"), str))):
                raise RecipeError("BAD_PARAMS", "battery %r is not {object: int} or {ref: str}"
                                  % (b,), op_id=self.spec.id)

    def _objects(self, ctx):
        out = []
        for b in self.batteries:
            if "object" in b:
                out.append(b["object"])
                continue
            h = ctx.handles.get(b["ref"])
            if h is None or "color_cell" not in h:
                raise RecipeError("BAD_REF", "battery ref %r is not a prior add_voltage_battery op"
                                  % b["ref"], op_id=self.spec.id)
            out.append(h["object"])
        return out

    def apply(self, data, ctx):
        sh = ctx.handles.get(self.status)
        if sh is None or "slot_state" not in sh:
            raise RecipeError("BAD_REF", "status %r is not a prior add_aes_batt_heat_status op"
                              % self.status, op_id=self.spec.id)
        objects = self._objects(ctx)
        out, wrapped = data, {}
        for oi in objects:
            try:
                out, info = battglyph.wrap_battery(out, object_index=oi,
                                                   value_slot=sh["slot_state"],
                                                   style=self.style,
                                                   outline_rgb=self.outline_rgb)
            except battglyph.BattGlyphError as e:
                raise RecipeError("NOT_A_BATTERY", str(e), op_id=self.spec.id)
            except (ValueError, relocate.RelocationError) as e:
                raise RecipeError("WRAP_REFUSED", str(e), op_id=self.spec.id)
            info["battery"] = battglyph.battery_records(data, oi)
            wrapped[oi] = info
        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = {"slot_state": sh["slot_state"], "wrapped": wrapped}
        return out

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        for oi, info in h["wrapped"].items():
            recs = records.object_records(data, gs + tab[oi])
            assert len(recs) == 1 and recs[0].wtype == 6, \
                "battery %d is not one switch (%s)" % (oi, [r.wtype for r in recs])
            sw = recs[0]
            assert sw.value_slot == h["slot_state"], \
                "battery %d switch watches %s, not the state cell %d" % (
                    oi, sw.value_slot, h["slot_state"])
            assert sw.header[4] & _ENROLL_BIT, "battery %d switch not enrolled" % oi
            assert sw.child_index == info["arms_base"] \
                and sw.count == len(aescompact.BATT_HEAT_STATE_ARMS), \
                "battery %d switch selects %d x%d, not its arms" % (oi, sw.child_index, sw.count)
            # THE REPAINT GUARD: every arm draws the whole battery, so any arm the switch selects
            # covers what the previous one drew; exactly the contactor arms carry the mark.
            for (match, _heat, contactor), ai in zip(aescompact.BATT_HEAT_STATE_ARMS,
                                                     info["arm_objects"]):
                arm = records.object_records(data, gs + tab[ai])
                assert battglyph.arm_ok(arm, info["battery"], contactor=contactor,
                                        style=self.style, outline_rgb=self.outline_rgb), \
                    "battery %d arm %d (match %#x) is not the whole battery with the %s mark %s" \
                    % (oi, ai, match, self.style, "on" if contactor else "off")
            # The enrolled voltage containers still point at the battery: the v25 repaint path.
            assert any(r.wtype == 5 and r.child_index == oi and (r.header[4] & _ENROLL_BIT)
                       for oj in range(len(tab))
                       for r in records.object_records(data, gs + tab[oj])), \
                "no enrolled container repaints battery %d" % oi
        if self.style == "outline":
            return ("add_batt_heat_battery_glyph: batteries %s each one enrolled switch on state "
                    "cell %d, four whole-battery arms, terminals and a %d px body ring in RGB %s "
                    "while the contactor is closed, number at its stock position; "
                    "HARDWARE-VERIFIED (coach, 2026-10-10)"
                    % (sorted(h["wrapped"]), h["slot_state"], battglyph.OUTLINE_INSET,
                       self.outline_rgb))
        return ("add_batt_heat_battery_glyph: batteries %s each one enrolled switch on state "
                "cell %d, four whole-battery arms, white slot-%d '%s' bottom-right while the "
                "contactor is closed, number raised %d px; HARDWARE-VERIFIED (coach, 2026-09-26)"
                % (sorted(h["wrapped"]), h["slot_state"], battglyph.GLYPH_FONT_SLOT,
                   battglyph.GLYPH_CHAR, battglyph.NUMBER_RAISE))


@op("add_pump_auto_off")
class AddPumpAutoOff(Op):
    """Compose Tasks 1-8 into the whole Water Pump Auto Off feature: a packed 0xEE register
    (Pump Link flag + Auto Off minutes), a two-way toggle that no longer erases the minutes
    when it flips the flag, a +/-5 minute stepper, an adopted numeric readout, and the
    per-tick routine that clamps/seeds/displays the word and fires the pump's OFF frame when
    the countdown elapses. Follows `AddAesLowVoltsTrigger`'s resolve-author-grow-detour shape.

    Five steps, each threading the previous step's image:

      1. `statusbind.set_extract_mode(data, 289, 2)` -- widen var 289's DGN_CONFIG
         subscription for selector 0xEE from a byte read to a u16 read, so the receive slot
         carries BOTH the minutes and the flag (`pumpauto`'s packed layout), not just the
         flag byte extraction mode 1 would give.
      2. ADOPT-OR-CREATE the 0xEE config-toggle group, then convert it either way.
         `optionrow.find_config_toggle(out, 0xEE)` looks for an EXISTING config-toggle group
         on selector 0xEE first -- a real composed project's `add_option_follower` ("pumplink")
         already builds one at the fixed slot 164 (`optionrow.REUSE_GROUP_SLOT`) for its own
         gated OPTIONS row, and `optionrow.build_config_toggle` refuses outright if this op
         calls it again on a selector already toggled ("fix round 1" C1). Zero found -> create
         it fresh (`build_config_toggle`, +4 bindings, refuses unless the reused group equals
         `link_group`). Exactly one found -> adopt it (no growth; refuses `TOGGLE_REFUSED` if
         it is not `link_group`, naming both, so this op can never silently adopt a toggle
         meant for something else). More than one -> `find_config_toggle` itself refuses,
         ambiguous. Either way, `optionrow.toggle_to_flag_delta(out, link_group, 0xEE)`
         immediately converts the group's two D1 absolute writes to D2 deltas of +/-0x100 --
         so the toggle this op ships NEVER erases the minute byte at runtime, whether it was
         just minted or already existed. `toggle_to_flag_delta` itself refuses a second
         conversion (D2, not D1, on a re-run), which is what makes this op idempotent-safe on
         its own output for free. `ctx.handles[op_id]["toggle_created"]` records which branch
         ran, and `verify()`'s growth check and summary string are both branch-aware.
      3. `optionrow.build_config_stepper(out, 0xEE, 5)` -- clone the stock 0x0E +/-300 arrow
         groups onto two free group slots with a +/-5 delta on selector 0xEE. Grows the
         binding table by 6 more (10 total over stock).
      4. `register.resolve_register` for the 0xEE DGN_CONFIG subscription (now widened) and
         for channel 44's DC_DIMMER_STATUS_3 (the pump's operating-status DGN).
      5. `pumpauto.resolve` derives the whole plan -- INCLUDING the two scratch cells
         (`followchannel.scan_display_cell`, internally) -- from THIS pre-append image, once.
         `pumpauto.build` authors the routine, `growsection.grow_section` appends it to
         GuiCode, `pumpauto.detour_edit` retargets the per-tick hook, and `patch.reseal`
         finishes the image. `_check_append_point` runs before `build` so a shrunk or
         misaligned append point is refused before any bytes are authored against it.

    DEVIATIONS FROM THE PLAN'S LITERAL APPLY ORDER, both load-bearing:

      * The plan's step 4 ("adopt a readout slot": `followchannel.scan_display_cell`,
        `statusbind.index_cell`, `statusbind.set_variable_type(..., 3)`) is NOT performed as
        written. `pumpauto.resolve` already calls `scan_display_cell` internally to pick
        `display_slot`/`display_addr` -- calling it AGAIN here, on the same pre-append image,
        would only rediscover the identical cell (harmless but pointless), and calling it
        AFTER the routine is appended would find a DIFFERENT, wrong cell: the safety scan is
        a literal scan over GuiCode, so once the routine's own pool word points at the cell it
        reads as "GuiCode-referenced" and the scan moves on (proven: cell 211 on stock,
        cell 215 once a routine already references cell 211-adjacent cells). The scratch
        allocation is taken from `plan.display_slot`/`plan.countdown_slot` -- computed once,
        derived from the pre-append image -- never re-derived.
        `statusbind.index_cell` is also skipped: `scan_display_cell` returns an EXISTING
        +0x44BC8 index whose table entry ALREADY points at the address it found (that is how
        the scan works -- it reads the table, not writes it), so `plan.display_slot` is
        already correctly wired to `plan.display_addr`; re-pointing it would be a same-value
        no-op through a tool this feature does not need.
        `statusbind.set_variable_type(..., 3)` is skipped ENTIRELY and must stay skipped:
        spare cell 215 (the actual allocation on this image) is already variable type 1, and
        Task 8a proved type 1 routes through the renderer's same single-byte digit path as
        type 3 -- retyping it to u16 would be pure hazard (widening a formatter's read width
        over a byte this routine deliberately writes with `strb`) for zero benefit. `verify`
        below asserts the type is left alone.
      * Step 2's adopt-or-create is itself a fix-round-1 correction, not the plan's literal
        text (which just says `toggle_to_flag_delta(out, link_group, 0xEE)`, as if a toggle
        on 0xEE always already exists). Neither half of that is true unconditionally: stock
        carries no 0xEE config-toggle group at all (confirmed empirically against
        `firefly-13.6/mcf.bin`), so the op's own compose-alone-on-stock test needs the CREATE
        path; a real project's `add_option_follower` ("pumplink") builds one at group 164
        before this op ever runs, so composing onto that image needs the ADOPT path (create
        would refuse: "selector 0xee already has a config-toggle group"). `link_group`'s
        default (164) is `optionrow.REUSE_GROUP_SLOT`, the fixed slot `build_config_toggle`
        always reuses; either branch refuses (`TOGGLE_REFUSED`) if the group it would act on
        is not `link_group`, so this op never silently builds against, or adopts, a group a
        project did not actually mean.

    Params:
      link_group: int (default 164) -- the config-toggle group id this op expects to own the
                  0xEE toggle either way: `optionrow.build_config_toggle` to reuse
                  (`optionrow.REUSE_GROUP_SLOT`) on the CREATE path, or the group
                  `optionrow.find_config_toggle` locates on the ADOPT path. Either path refuses
                  (`TOGGLE_REFUSED`) if the group it would act on is not this one.
      allow_disable: bool (default `False`) -- v20. Lower the minute floor from 5 to 0, making 0
                  a legal setting that DISABLES Auto Off, and emit the borrow recovery that
                  floor needs. Left `False` the routine is the v14-v19 one byte for byte, which
                  is what keeps every flashed image reproducible from this recipe; the whole
                  behaviour change is in that one flag. The disabling itself costs no code --
                  `armit` stores `minutes * 60`, so 0 arms to `DISARMED` -- but the floor does:
                  `-` at 0 borrows out of the minute byte into the Pump Link flag, and `clamp`
                  has to undo it or a press jumps the setting to an hour and clears the link.
      minutes_default: int (default 30) -- ACCEPTED FOR COMPATIBILITY AND PROVENANCE (recorded
                  in the handle) but not yet wired into the routine: `pumpauto.resolve`/`build`
                  take the fallback minute count from the module constant
                  `pumpauto.MINUTES_DEFAULT` (30), with no parameter to override it. A value
                  other than 30 is refused (`SECTION_REFUSED`) at `validate()` time -- before
                  any image mutation -- rather than silently shipping a default the routine
                  does not actually use.

    Error codes:
      NO_SUCH_REGISTER / AMBIGUOUS_REGISTER -- `register.resolve_register` found zero, or
                     more than one, variable-table entry for the 0xEE selector or channel 44.
      TOGGLE_REFUSED -- `find_config_toggle` found more than one 0xEE config-toggle group
                     (ambiguous); `build_config_toggle` refused on the CREATE path (donor group
                     129 not the expected Low Volts clone, no room in the padding arena, group
                     164 not unused, a relocate gate failed) or reused a group other than
                     `link_group`; the ADOPT path found exactly one existing group but it is
                     not `link_group`; or `toggle_to_flag_delta` refused either way (not an
                     absolute two-write toggle on 0xEE -- already converted, or the donor/
                     adopted shape drifted).
      STEPPER_REFUSED -- `build_config_stepper` refused (fewer than two free group slots, the
                     0x0E arrow donors drifted from their pinned identity, a relocate gate
                     failed).
      SECTION_REFUSED -- `minutes_default` is not `pumpauto.MINUTES_DEFAULT` (refused in
                     `validate()`), or `pumpauto.resolve`/`build`/`detour_edit` refused (no
                     safe scratch cell, a resolved register is outside SRAM or misaligned, a
                     template is the wrong size, the hook is not the expected stock/chained
                     bytes).
      UNEXPECTED_APPEND_POINT -- the GuiCode append point is below stock 13.6's own append
                     point or misaligned; refuses rather than build against an image this op
                     was not proven on.
      GROW_REFUSED -- `growsection.grow_section` refused.
      DETOUR_REFUSED -- the bytes at the detour's file offset were not the plan's expected
                     stock/chained `bl`; a grow shifted something it should not have.

    `hardware_verified = True` again as of 2026-09-04: a later task shortened the routine by 4
    bytes for the minute-0 clamp fix, and those emitted bytes ran on the coach, flashed and
    verified 2026-09-04 (checklist item 8, "Only goes 5-60", and item 10, the
    timed shutoff) -- so "pumpauto" (or whatever id a project gives this op) no longer appears
    in the compile result's `unverified` manifest.

    Capability delta `(set(), set())`: every binding this op writes or converts (the toggle's
    two deltas, the stepper's six) is a `0x1FED9` DGN_CONFIG write, channel `None` -- entirely
    outside `recipe.capabilities`'s (channel, command) set -- and the per-tick routine is
    GuiCode. Stock and output express exactly the same (channel, command) pairs.

    Registers `ctx.handles[op_id]` with the toggle/stepper touch ids and the readout slot --
    the exact positional tuple `aescompact.swap_electrical_to_compact`'s `pump_section_ids`
    expects -- plus `toggle_created` (whether step 2 took the CREATE or ADOPT branch, so a
    later compile-inspecting tool never has to reverse-engineer it from binding counts) and
    every address the routine's own verification needs. `swap_aes_electrical` (given this
    op's id via its new `pump_section` param) reads this handle to place the Water Pump
    section on the compact AES frame; because that op only READS the handle, this op must
    run first in the project's op list, or the ref resolves to nothing and
    `swap_aes_electrical` refuses cleanly (`BAD_REF`) instead of wiring stale ids.

    NOTE -- the composition hazard, and the mechanism that now closes it (Task 13). Widening
    var 289's subscription (step 1) also changes what the EXISTING `add_option_follower`
    ("pumplink") gate reads from the same slot: a `halfword == 1` gate, built against the
    byte-only extraction mode, goes permanently false once the slot carries the packed u16
    (`minutes | flag << 8`) the widened mode delivers, silently killing that follower and its
    button's recolor. Task 13 built the fix -- `add_option_follower` takes `gate_mask` and
    `gate_on_value`, so the Pump Link instance must be configured with
    `"gate_mask": 65280, "gate_on_value": 256` (`(slot & 0xFF00) == 0x0100`, the flag byte) to
    survive this op. It is OPT-IN and defaults to the old whole-halfword test, so composing this
    op with a DEFAULT `add_option_follower` still produces the dead gate -- that combination is
    pinned as known-bad by
    `tests/test_recipe_ops.py::test_add_pump_auto_off_composed_with_an_unmasked_follower_leaves_a_dead_gate`.
    Wiring the recipe's own `pumplink` op is a later task's; this op still must not paper over
    it (e.g. by narrowing the widened extraction mode) on its own judgement.
    """

    hardware_verified = True     # the first routine was coach-verified 2026-09-04
                                 # (repaint, countdown, persistence); a later task then
                                 # deleted two 2-byte instructions from this routine for the
                                 # minute-0 clamp fix (commit 7b4fd92), and THOSE bytes ran on
                                 # the coach too, flashed and verified 2026-09-04 (checklist
                                 # items 8 and 10).

    PUMP_LINK_SELECTOR = 0xEE
    LINK_SUBSCRIPTION_VAR = 289
    PUMP_CHANNEL = 44
    STEPPER_STEP = 5

    PARAMS = {
        "properties": {
            "link_group": {"type": "integer", "default": 164,
                           "description": "the config-toggle group id this op must create or "
                           "adopt for the 0xEE toggle (optionrow.REUSE_GROUP_SLOT); any other "
                           "value refuses"},
            "minutes_default": {"type": "integer", "default": 30,
                                "description": "recorded for provenance; the routine's actual "
                                "fallback is the fixed pumpauto.MINUTES_DEFAULT (30)"},
            "allow_disable": {"type": "boolean", "default": False,
                              "description": "lower the minute floor from 5 to 0, so 0 is "
                              "a legal setting that disables Auto Off, and emit the borrow "
                              "recovery a '-' press at that floor needs"},
        },
        "required": [],
    }

    def validate(self):
        self.link_group = self._param("link_group", 164, kind=int)
        self.minutes_default = self._param("minutes_default", 30, kind=int)
        self.allow_disable = self._param("allow_disable", False, kind=bool)
        # A pure parameter check with no image dependency -- refuse at construction time,
        # like every other op's param validation in this file, rather than three image
        # mutations into apply().
        if self.minutes_default != pumpauto.MINUTES_DEFAULT:
            raise RecipeError(
                "SECTION_REFUSED",
                "minutes_default %d is not pumpauto.MINUTES_DEFAULT (%d); the routine has no "
                "parameter to honour a different fallback" % (
                    self.minutes_default, pumpauto.MINUTES_DEFAULT), op_id=self.spec.id)

    def apply(self, data, ctx):
        # Captured before any mutation, purely from this op's own input -- composition-agnostic
        # (works whether an earlier op already grew the binding table or not), so `verify()`
        # can assert THIS op's own growth is exactly what the branch it took should produce,
        # not the growth from `ctx.stock` (which would also count any earlier op's growth).
        bindings_before = len(guidata.binding_table(data))

        # --- 1. widen the 0xEE subscription (var 289) to a u16 read: minutes + flag both.
        out, edits = statusbind.set_extract_mode(data, self.LINK_SUBSCRIPTION_VAR, 2)
        ctx.edits.extend(edits)

        # --- 2. ADOPT an existing 0xEE config-toggle group if one already exists (a real
        # project's add_option_follower/"pumplink" builds one at group 164 before this op
        # runs; optionrow.build_config_toggle refuses outright on a second call for the same
        # selector -- "fix round 1" C1), otherwise CREATE one fresh. Either way, immediately
        # convert it to +/-0x100 deltas so the shipped image never erases the minute byte --
        # "the toggle stops erasing the minutes" happens before any user ever sees the
        # feature, not as a follow-up patch.
        try:
            existing = optionrow.find_config_toggle(out, self.PUMP_LINK_SELECTOR)
        except optionrow.OptionRowError as e:
            raise RecipeError("TOGGLE_REFUSED", str(e), op_id=self.spec.id)

        toggle_created = existing is None
        if toggle_created:
            try:
                tog = optionrow.build_config_toggle(out, self.PUMP_LINK_SELECTOR)
            except optionrow.OptionRowError as e:
                raise RecipeError("TOGGLE_REFUSED", str(e), op_id=self.spec.id)
            if tog.new_group_index != self.link_group:
                raise RecipeError(
                    "TOGGLE_REFUSED",
                    "build_config_toggle reused group %d, not the configured link_group %d; "
                    "build_config_toggle cannot be steered to a different slot"
                    % (tog.new_group_index, self.link_group), op_id=self.spec.id)
            ctx.edits.extend(tog.edits)
            out = tog.grown
            link_group_index, link_touch_id = tog.new_group_index, tog.touch_id
        else:
            if existing != self.link_group:
                raise RecipeError(
                    "TOGGLE_REFUSED",
                    "an existing 0xee config-toggle group is %d, not the configured "
                    "link_group %d; refusing to adopt a toggle intended for something else"
                    % (existing, self.link_group), op_id=self.spec.id)
            # touch id == group index for this family of cloned/reused config-toggle groups
            # (ConfigToggle.touch_id mirrors new_group_index the same way); the adopted
            # group's own on-screen row (built by whatever op created it) already emits it.
            link_group_index, link_touch_id = existing, existing

        try:
            out, edits = optionrow.toggle_to_flag_delta(
                out, link_group_index, self.PUMP_LINK_SELECTOR, shift=8)
        except optionrow.OptionRowError as e:
            raise RecipeError("TOGGLE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)

        # --- 3. the +/-5 minute stepper: two cloned arrow groups on free slots.
        try:
            stepper = optionrow.build_config_stepper(
                out, self.PUMP_LINK_SELECTOR, self.STEPPER_STEP)
        except optionrow.OptionRowError as e:
            raise RecipeError("STEPPER_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(stepper.edits)
        out = stepper.grown

        # --- 4. resolve the two bus registers the routine reads (on the fully-grown,
        # still pre-append image -- the one `pumpauto.resolve`'s scratch-cell scan must see).
        try:
            config_reg = register.resolve_register(
                out, dgn="DGN_CONFIG", selector=self.PUMP_LINK_SELECTOR)
        except register.RegisterError as e:
            code = "AMBIGUOUS_REGISTER" if "not exactly one" in str(e) else "NO_SUCH_REGISTER"
            raise RecipeError(code, str(e), op_id=self.spec.id)
        try:
            pump_reg = register.resolve_register(
                out, dgn="DC_DIMMER_STATUS_3", instance=self.PUMP_CHANNEL)
        except register.RegisterError as e:
            code = "AMBIGUOUS_REGISTER" if "not exactly one" in str(e) else "NO_SUCH_REGISTER"
            raise RecipeError(code, str(e), op_id=self.spec.id)

        # --- 5. derive the plan (including both scratch cells) from THIS pre-append image,
        # exactly once -- never re-scanned once the routine below references its own cells.
        try:
            plan = pumpauto.resolve(out, config=config_reg, pump=pump_reg,
                                    minutes_min=0 if self.allow_disable
                                    else pumpauto.MINUTES_MIN)
        except pumpauto.PumpAutoError as e:
            raise RecipeError("SECTION_REFUSED", str(e), op_id=self.spec.id)

        gc = theme.guicode_section(out)
        code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
        _check_append_point(code_load_addr, self.spec.id)
        try:
            blob, _disasm = pumpauto.build(plan, code_load_addr)
        except pumpauto.PumpAutoError as e:
            raise RecipeError("SECTION_REFUSED", str(e), op_id=self.spec.id)

        try:
            grown, load_off, _changed = growsection.grow_section(out, "GuiCode", blob)
        except growsection.GrowSectionError as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "add_pump_auto_off: appended %d-byte clamp/display/auto-off routine (config slot "
            "%#x, pump slot %#x, display %#x, countdown %#x)" % (
                len(blob), plan.config_slot, plan.pump_slot, plan.display_addr,
                plan.countdown_addr)))

        try:
            hook_at, hook_old, hook_new = pumpauto.detour_edit(plan, code_load_addr)
        except pumpauto.PumpAutoError as e:
            raise RecipeError("SECTION_REFUSED", str(e), op_id=self.spec.id)
        file_off = gcs.offset + hook_at
        pre = grown[file_off:file_off + 4]
        if pre != hook_old:
            raise RecipeError(
                "DETOUR_REFUSED",
                "GuiCode+%#x holds %s, not the plan's expected stock/chained bytes %s; a grow "
                "shifted something it should not have" % (hook_at, pre.hex(), hook_old.hex()),
                op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(
            file_off, pre, hook_new, "GuiCode",
            "add_pump_auto_off: detour bl -> routine entry %#x"
            % (code_load_addr + pumpauto.ENTRY_OFF)))
        sealed, _changed2 = patch.reseal(bytes(buf))

        ctx.handles[self.spec.id] = {
            # the exact positional tuple aescompact.swap_electrical_to_compact's
            # pump_section_ids expects: (link_touch_id, plus_touch_id, minus_touch_id,
            # readout_slot).
            "link_touch_id": link_touch_id,
            "plus_touch_id": stepper.plus_touch_id,
            "minus_touch_id": stepper.minus_touch_id,
            "readout_slot": plan.display_slot,
            "link_group": link_group_index,
            "plus_group": stepper.plus_group,
            "minus_group": stepper.minus_group,
            "toggle_created": toggle_created,     # CREATE branch vs ADOPT branch (fix round 1)
            "bindings_before": bindings_before,   # this op's own pre-mutation baseline, for a
                                                  # composition-agnostic growth check in verify()
            "bindings_after": len(guidata.binding_table(sealed)),  # count right after THIS op grew
                                                  # the table -- so verify measures its own growth
                                                  # even when a LATER op grows the table too
            "config_slot": plan.config_slot,
            "pump_slot": plan.pump_slot,
            "minutes_min": plan.minutes_min,      # 5 = the shipped floor, 0 = disablable (v20)
            "display_addr": plan.display_addr,
            "countdown_addr": plan.countdown_addr,
            "countdown_slot": plan.countdown_slot,   # what a wtype-6 switch reads to see the
                                                     # timer running (0 = disarmed) -- v20's
                                                     # `add_corner_mark` gate
            "hook_at": hook_at,
            "code_load_addr": code_load_addr,
            "entry": code_load_addr + pumpauto.ENTRY_OFF,
        }
        return sealed

    def capability_delta(self, ctx):
        return set(), set()          # every binding is a channel-None 0x1FED9 config write

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, _tab = _gs_tab(data)

        # --- the widened subscription: var 289's extraction mode is 2 (u16), not 1 (byte).
        var = {v.index: v for v in guidata.variable_table(data)}[self.LINK_SUBSCRIPTION_VAR]
        assert var.raw[2] >> 4 == 2, \
            "var %d extraction mode is %d, expected 2 (u16)" % (
                self.LINK_SUBSCRIPTION_VAR, var.raw[2] >> 4)

        # --- the readout slot is STILL variable type 1: the retype step was deliberately
        # skipped (a prior spike proved type 1 renders identically to type 3 for this single
        # byte), and set_variable_type/VARIABLE_TYPE_U16 must never be called on it.
        _dptr, type_off = guidata.find_variable_tables(data)
        assert data[gs + type_off + h["readout_slot"]] == 1, \
            "readout slot %d was retyped away from 1; this feature must not call " \
            "set_variable_type on it" % h["readout_slot"]

        # --- the link toggle: two D2 deltas of +/-0x100 on selector 0xEE, no absolute writes.
        gt = guidata.group_table(data)
        bt = guidata.binding_table(data)
        g = gt[h["link_group"]]
        ents = [bt[j] for j in range(g.start, g.start + g.count)]
        sels = {e.raw[optionrow.SELECTOR_FIELD] for e in ents}
        assert sels == {self.PUMP_LINK_SELECTOR}, \
            "link group %d carries selector(s) %s, expected only 0xee" % (
                h["link_group"], sorted(hex(s) for s in sels))
        deltas = {struct.unpack_from(">h", e.raw, optionrow.VALUE_FIELD)[0]
                 for e in ents if e.raw[optionrow.FUNCTION_FIELD] == optionrow.DELTA_FUNCTION}
        assert deltas == {0x0100, -0x0100}, \
            "link group %d does not carry the +/-0x100 flag deltas: %s" % (
                h["link_group"], sorted(hex(d & 0xFFFF) for d in deltas))
        assert not any(e.raw[optionrow.FUNCTION_FIELD] == optionrow.ABSOLUTE_FUNCTION
                      for e in ents), \
            "link group %d still carries an absolute (D1) write; the minute byte is not safe" \
            % h["link_group"]

        # --- growth is branch-aware (fix round 1 I1): CREATE grows the table by
        # optionrow.DONOR_COUNT (4, the toggle) + ARROW_DONOR_COUNT*2 (6, the stepper) = 10;
        # ADOPT skips the toggle's own growth entirely (it reuses an existing group) and only
        # the stepper's 6 appears. Computed from THIS op's own pre-mutation baseline
        # (`bindings_before`, captured at the top of `apply`), not from `ctx.stock` -- that
        # stays correct even when an earlier op (e.g. add_option_follower) already grew the
        # table, which is exactly the composed case the ADOPT branch exists for. The end point
        # is `bindings_after` (the count right after THIS op grew the table), NOT `len(bt)` at
        # verify time, so a LATER op that also grows the binding table (e.g. add_aes_batt_heat)
        # does not inflate this op's own measured growth.
        expected_growth = (optionrow.DONOR_COUNT if h["toggle_created"] else 0) \
            + optionrow.ARROW_DONOR_COUNT * 2
        got_growth = h["bindings_after"] - h["bindings_before"]
        assert got_growth == expected_growth, \
            "binding table grew by %d on the %s path, expected %d" % (
                got_growth, "CREATE" if h["toggle_created"] else "ADOPT", expected_growth)

        # --- the stepper: +/-5 deltas on 0xEE, on the touch ids the handle names.
        for gid, want in ((h["plus_group"], self.STEPPER_STEP),
                         (h["minus_group"], -self.STEPPER_STEP)):
            g = gt[gid]
            ents = [bt[j] for j in range(g.start, g.start + g.count)]
            sels = {e.raw[optionrow.SELECTOR_FIELD] for e in ents}
            assert sels == {self.PUMP_LINK_SELECTOR}, \
                "stepper group %d carries selector(s) %s, expected only 0xee" % (
                    gid, sorted(hex(s) for s in sels))
            press = ents[0]
            assert press.raw[optionrow.FUNCTION_FIELD] == optionrow.DELTA_FUNCTION, \
                "stepper group %d's press binding is not a D2 delta" % gid
            got = struct.unpack_from(">h", press.raw, optionrow.VALUE_FIELD)[0]
            assert got == want, \
                "stepper group %d press delta is %+d, expected %+d" % (gid, got, want)

        # --- the per-tick routine is appended and the stock hook detoured to it.
        gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        detour = data[gcs.offset + h["hook_at"]:][:4]
        assert detour != followchannel.HOOK_STOCK, \
            "detour not applied: stock bl still present at GuiCode+%#x" % h["hook_at"]
        chain = followchannel.chain_from_hook(data)
        assert h["entry"] in chain, "routine entry %#x not in the per-tick chain %s" % (
            h["entry"], [hex(c) for c in chain])
        assert chain[-1] == followchannel.TAIL_TARGET

        toggle_branch = "CREATED fresh (+%d bindings)" % optionrow.DONOR_COUNT \
            if h["toggle_created"] else "ADOPTED a pre-existing group (no growth)"
        return ("add_pump_auto_off: var %d widened to u16 (0xee minutes+flag); group %d "
                "toggle %s, converted to +/-0x100 flag deltas (link touch %d); +/-%d minute "
                "stepper (groups %d/%d, touch %d/%d); readout slot %d (type 1, unchanged); "
                "per-tick routine appended, detour GuiCode+%#x -> %#x; HARDWARE-UNVERIFIED "
                "(coach test: toggle both ways without disturbing the minutes, stepper "
                "range/step, readout, auto-off firing)"
                % (self.LINK_SUBSCRIPTION_VAR, h["link_group"], toggle_branch,
                   h["link_touch_id"], self.STEPPER_STEP, h["plus_group"], h["minus_group"],
                   h["plus_touch_id"], h["minus_touch_id"], h["readout_slot"],
                   h["hook_at"], h["entry"]))


@op("show_nav_slides_icon")
class ShowNavSlidesIcon(Op):
    """Reveal the nav rail's sixth icon (the slide-out glyph) and point it at a chosen screen.

    Firefly authored a six-icon left rail and left it unreachable: object 974's wt-6 on slot 453
    has `child_base` 385 (five icons) and the panel only ever writes 453 = 0 or 1, so the 2- and
    4-valued arms that lead to the six-icon rail are never selected. This op repoints the switch's
    `child_base` at 389, whose arms are the same no-Electrical / Electrical pair stock uses but
    WITH the extra icon -- so nothing is lost and no never-established slot is consulted (see
    `navicon`).

    The icon's region carries ACTION id 0x7DC, whose leaf sets screen 12 (`SLIDES/AWNINGS`, the
    slide/awning operation page). A second page is also titled `SLIDES/AWNINGS` -- screen 29, the
    auto-levelling page carrying the slot 174/175 gates `bootlottery` flags -- and its only leaf
    (0x7ED) is carried by no region at all, so it is unreachable in stock. `screen` chooses which
    the icon reaches; passing 12 leaves the leaf untouched.

    TWO same-size guarded edits: 2 bytes of GuiData (the switch base) and, unless `screen` is 12,
    2 bytes of GuiCode (`movs r3,#0xc` -> `movs r3,#<screen>`).

    Params:
      screen: int (default 29) -- destination; 12 = slide/awning controls, 29 = auto-levelling.

    Error codes:
      NAV_ICON_REFUSED -- the nav switch is not at its stock base/count, the action leaf's stock
                     bytes did not match, or the screen id does not fit `movs r3,#imm8`.

    Declares no capability delta: a switch base and an action-dispatcher screen constant; no G12
    group or binding changes, so the panel gains no new command it could not already send.

    HARDWARE-VERIFIED 2026-09-03: the coach draws the six-icon rail and
    the slide-out icon opens screen 29, a page no route reached in stock.
    """

    hardware_verified = True

    PARAMS = {
        "properties": {
            "screen": {"type": "integer", "default": navicon.SLIDES_LEVELLING_SCREEN,
                       "description": "12 = slide/awning controls, 29 = auto-levelling page"},
        },
        "required": [],
    }

    def validate(self):
        self.screen = self._param("screen", navicon.SLIDES_LEVELLING_SCREEN, kind=int)

    def apply(self, data, ctx):
        try:
            out, edits = navicon.show_extra_nav_icon(data)
            if self.screen != navicon.SLIDES_OPERATION_SCREEN:
                out, more = navicon.retarget_nav_icon(out, self.screen)
                edits = edits + more
        except ValueError as e:
            raise RecipeError("NAV_ICON_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = {"screen": self.screen,
                                     "touch_id": navicon.SLIDES_TOUCH_ID,
                                     "base": navicon.NAV_RAIL_SIX_ICON_BASE}
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        rec = navicon._nav_switch_record(data)
        assert rec.child_index == h["base"], \
            "nav rail switch base is %s, expected %s" % (rec.child_index, h["base"])
        # the sixth icon must actually be carried by the arm the switch now selects
        gs, tab = _gs_tab(data)
        ids = set()
        for obj in (h["base"], h["base"] + 1):
            for r in records.object_records(data, gs + tab[obj]):
                if r.wtype == 12:
                    ids.add(data[r.end - 2] | (data[r.end - 1] << 8))
        assert h["touch_id"] in ids, \
            "neither arm %d/%d carries the sixth icon %#06x" % (
                h["base"], h["base"] + 1, h["touch_id"])
        section = theme.guicode_section(data)
        off = section.offset + (navicon.SLIDES_LEAF_SITE - theme.guicode_base(section))
        want = thumb.movs_imm(3, h["screen"])
        assert bytes(data[off:off + 2]) == want, \
            "0x7dc action leaf does not set screen %d in the finished image" % h["screen"]
        root = layout.screen_object_map(data).get(h["screen"])
        assert root is not None, "screen %d has no object in the finished image" % h["screen"]
        return ("nav rail: sixth icon (slide-out glyph, action %#06x) revealed via switch base "
                "%d; it opens screen %d (object %d)"
                % (h["touch_id"], h["base"], h["screen"], root))


@op("pin_switch_arm")
class PinSwitchArm(Op):
    """Pin a wt-6 switch to ONE of its arms regardless of its value slot (`switchpin`).

    Built for `INVERTER / CHARGER SETTINGS`'s `Absorb`/`Float` rows: object 17 record 11 is a
    switch on value slot 23, a cell nothing ever writes (`GUICODE.md` Findings 63/64/67), so
    the rows draw or not by power-up SRAM (`BUS-BASELINE.md` PC.55). Three same-size u16 edits
    make the switch's scan always miss and always fall back to the chosen arm -- the firmware's
    own default-arm rule (`layout.Screen._switch_child`, `proven-from-code`): `child_base :=
    arm`, `count := 1`, `match[arm] := 0xFFFF`.

    Params:
      object: int -- the object carrying the switch record
      record: int -- the record's index within that object
      arm:    int -- the object index to pin to; must be inside the switch's stock band

    Error codes:
      PIN_REFUSED -- not a wt-6 switch, the arm is outside its band, or the arm's match value
                     is shared with another switch (it is one u16 per object).

    Declares no capability delta: a layout selection, no groups or bindings. Verified by
    re-walking every screen root that reaches the switch's object for several slot values and
    checking the arm is placed on each and the other stock arms on none.

    HARDWARE-VERIFIED 2026-09-05 (`docs/BUS-BASELINE.md` PC.60): the rows were present immediately
    after the flash and again after a power cycle, where stock hid them after every flash.
    """

    hardware_verified = True

    PARAMS = {
        "properties": {
            "object": {"type": "integer"},
            "record": {"type": "integer"},
            "arm": {"type": "integer"},
        },
        "required": ["object", "record", "arm"],
    }

    def validate(self):
        self.object = self._param("object", kind=int)
        self.record = self._param("record", kind=int)
        self.arm = self._param("arm", kind=int)

    def apply(self, data, ctx):
        gs, tab = _gs_tab(data)
        try:
            rec = records.object_records(data, gs + tab[self.object])[self.record]
            band = list(range(rec.child_index, rec.child_index + (rec.count or 0))) \
                if rec.wtype == 6 and rec.child_index is not None else []
            out, edits = switchpin.pin_switch_arm(data, self.object, self.record, self.arm)
        except (switchpin.SwitchPinError, IndexError) as e:
            raise RecipeError("PIN_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = {"object": self.object, "record": self.record,
                                     "arm": self.arm, "stock_band": band,
                                     "value_slot": rec.value_slot}
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        assert switchpin.pinned_to(data, h["object"], h["record"]) == h["arm"]
        roots = _readout_root_objects(data, h["object"], self.spec.id)
        others = [a for a in h["stock_band"] if a != h["arm"]]
        for root in roots:
            for value in (0, 1, 2, 0xFF, 0xFFFF):
                placed = {p.obj_index for p in layout.Screen(data).walk(
                    root, slot_values={h["value_slot"]: value})}
                assert h["arm"] in placed, (root, value)
                assert not set(others) & placed, (root, value, set(others) & placed)
        return ("pin_switch_arm: object %d record %d -> arm %d on every value of slot %d "
                "(roots %s) -- HARDWARE-%s"
                % (h["object"], h["record"], h["arm"], h["value_slot"], roots,
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED"))


@op("fix_variant_gate")
class FixVariantGate(Op):
    """Repair a control that GuiCode disables every tick from a receive slot (`variantgates`,
    `GUICODE.md` Finding 68) -- built for `Shore Brkr Size`'s `-` (group 199), which 13.6 gates
    on `DGN_CONFIG` selector `0x95`, the G12's "AES parameters at seed values" flag, 0 on every
    tuned coach. 11.23 gates the same control on selector `0x99`, the breaker size itself.

    Two one-byte modes:
      mode "selector": restore the gate's var-table entry to `selector` (default `0x99`), i.e.
                       11.23's design -- `-` disabled only while the size reads 0. GuiData.
      mode "ungate":   rewrite the disabling arm's `movs r1,#1` to `movs r1,#0`, so the control
                       follows its variant-0 bindings on every slot value. GuiCode.

    Params:
      group:    int (default 199)
      mode:     "selector" | "ungate" (default "selector")
      selector: int (default 0x99), "selector" mode only

    Error codes:
      GATE_REFUSED -- no such gate on this build, the group's gate is not on DGN_CONFIG, or the
                      byte already holds the requested value.

    No capability delta: the control's bindings are untouched; only which variant the tick
    selects, or which selector the gate watches, changes.

    HARDWARE-VERIFIED 2026-09-07 (`BUS-BASELINE.md` PC.62). Running on the coach, the `-`
    put `19FED99F#FF96990FFBFFD2FF` on the bus -- a `D2 FF` delta of `0xFFFB` = -5, byte-exact
    against the prediction and the `+`'s `0500D2FF` mirrored -- followed by the `D2 EA` echo and
    the `D3 FF` read-back. The button had transmitted nothing in five presses across three
    sessions before this byte changed (`CLAUDE.md` defect 0b, PC.32). The floor is verified too:
    from a stored 5 A, `-`/`-`/`+` read back **5**, which happens only if the second `-` was
    silent at 0 A.

    **Only mode "selector" is verified.** Mode "ungate" rewrites GuiCode instead and has never
    been built or flashed, so `validate` narrows the flag per instance -- a coach photo of one
    mode does not vouch for the other.

    NOTE: keep this a plain class attribute and shadow it with an instance attribute. It must NOT
    become a `property`: `studio/server/app.py` reads `cls.hardware_verified` off the CLASS to
    build `/api/schemas`, and a property object there is not JSON-serialisable (caught by
    `tests/test_studio_server.py::test_schemas_endpoint_covers_registry`).
    """

    hardware_verified = True        # the default mode, "selector"; narrowed in validate()

    PARAMS = {
        "properties": {
            "group": {"type": "integer", "default": 199},
            "mode": {"type": "string", "enum": ["selector", "ungate"], "default": "selector"},
            "selector": {"type": "integer", "default": 0x99},
        },
        "required": [],
    }

    def validate(self):
        self.group = self._param("group", 199, kind=int)
        self.mode = self._param("mode", "selector", kind=str)
        self.selector = self._param("selector", 0x99, kind=int)
        if self.mode not in ("selector", "ungate"):
            raise RecipeError("BAD_PARAMS", "mode must be 'selector' or 'ungate'", op_id=self.spec.id)
        # Shadow the class attribute: only "selector" has been on a coach (PC.62).
        self.hardware_verified = self.mode == "selector"

    def apply(self, data, ctx):
        try:
            before = [g for g in variantgates.gated_variants(data) if g.group == self.group]
            if self.mode == "selector":
                out, edits = variantgates.restore_gate_selector(data, self.group, self.selector)
            else:
                out, edits = variantgates.ungate(data, self.group)
        except variantgates.VariantGateError as e:
            raise RecipeError("GATE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = {"group": self.group, "mode": self.mode,
                                     "selector": self.selector, "var_index": before[0].var_index,
                                     "old_selector": before[0].selector,
                                     "variant_at": before[0].variant_at}
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gates = [g for g in variantgates.gated_variants(data) if g.group == h["group"]]
        assert len(gates) == 1, gates
        g = gates[0]
        if h["mode"] == "selector":
            assert g.selector == h["selector"] and g.disabling and g.variant == 1, g
            assert guidata.variable_table(data)[h["var_index"]].value == h["selector"]
        else:
            assert g.variant == 0 and not g.disabling, g
        return ("fix_variant_gate: group %d %s (var %d selector %#x -> %#x) -- HARDWARE-%s"
                % (h["group"], h["mode"], h["var_index"], h["old_selector"],
                   g.selector, "VERIFIED" if self.hardware_verified else "UNVERIFIED"))


@op("add_corner_mark")
class AddCornerMark(Op):
    """Put a small state-gated mark in a corner of an existing button (`cornermark.py`).

    A wtype-6 value-match switch appended as the host object's LAST record selects between an
    object that draws one character at absolute screen coordinates and an object that draws
    nothing. The mark shows whenever the gate cell is anything but 0 -- so the natural gate is a
    cell whose 0 means "not happening", such as the Auto Off countdown, which `add_pump_auto_off`
    maintains every tick and zeroes when the timer is disarmed.

    Why the pieces are shaped the way they are -- the absolute coordinates, the default arm at
    the base, the inherited font -- is `cornermark.py`'s module docstring; each avoids a trap
    this project has already paid for once.

    THE GLYPH MUST ALREADY EXIST. This op draws a character; it does not install one. Run
    `install_glyph` for the same `(font, char)` earlier in the project, or point `char` at a
    character the font already carries. If the character resolves to the shared out-of-range
    placeholder this op refuses (`NO_GLYPH`) rather than shipping a button with the fallback
    block drawn in its corner.

    Params:
      object: int -- the host object index (the button whose corner gets the mark). The switch is
              appended to its record stream; its existing records are asserted unchanged.
      gate: `{"ref": op_id, "field": name}` -- read the gate cell index from an earlier op's
              handle, e.g. `{"ref": "pumpauto", "field": "countdown_slot"}`. Mutually exclusive
              with `value_slot`.
      value_slot: int -- the gate cell index, given directly. Mutually exclusive with `gate`.
      dx, dy: int -- the mark's offset from the HOST'S OWN FLOW ORIGIN, not screen coordinates.
              Every text widget type commits its coordinates as the new flow origin and that
              carries back out to the parent object, so an absolutely-placed mark drags whatever
              the walk reaches next -- see `cornermark.py`'s module docstring, where the layout
              re-walk caught exactly that. Each arm cancels its own offset, so the switch is
              flow-neutral either way and the mark tracks the button if the button ever moves.
      page_root: int -- the screen root to re-walk in `verify`, e.g. 4 for HOME.
      preview_slots: {slot: value} -- the configuration the re-walk needs to REACH the host. A
              button behind a floorplan-gated quadrant is not placed at all under a bare walk,
              so `verify` would see zero marks and refuse for the wrong reason. Same idiom, and
              the same purpose, as `split_hold_region`'s parameter of this name.
      expect: [x, y] -- where the mark must land on that page, in screen coordinates. Checked by
              re-walking the finished image; `verify` refuses if it lands anywhere else, and also
              refuses if any OTHER placement on the page moved.
      font: int -- the font slot the character is drawn from. Give the host caption's own font
              unless there is a reason not to: the R38 selector is persistent, so a different
              one leaks into whatever the walk reaches next, and leaks CONDITIONALLY because the
              arm only draws sometimes.
      char: str (len 1) -- the character. May be non-ASCII (a label is a byte string the text
              drawer indexes the font with), but must fit in one byte.

    Error codes:
      BAD_PARAMS -- neither or both of `gate`/`value_slot`, a malformed `gate`, or a `char` that
              is not exactly one single-byte character
      BAD_REF -- `gate` names an op that has not run, or a field that op's handle does not carry
      NO_GLYPH -- `font`/`char` resolves to the shared placeholder, i.e. no glyph is installed
      MARK_REFUSED -- `cornermark.add_mark` refused (a duplicate switch on the same cell, a bad
              object index, a record that failed its self-decode, or a relocate gate)

    **HARDWARE-VERIFIED 2026-09-06** (`BUS-BASELINE.md` PC.61): the Auto Off timer face
    appears in the HOME Water Pump button's lower-right corner while the countdown runs and goes
    when it stops, with `Water Pump` and `Tank Heater` unmoved in both states.

    It took two rounds. v20 shipped a mark that drew **one column of pixels**, because its record
    was copied from `aescompact._flow_anchor` -- which declares coordinates 0 and 1 only, since an
    invisible record needs no box -- and the panel clipped the glyph to the degenerate box that
    gave it. `verify` checked WHERE the mark landed and not that it had a width to land in, and
    `render.py` does not clip text to the record box, so neither caught it. Both now do.
    """
    hardware_verified = True

    PARAMS = {
        "properties": {
            "object": {"type": "integer", "description": "host object index"},
            "gate": {"type": ["object", "null"], "default": None,
                     "description": "{'ref': op_id, 'field': name}: read the gate cell index "
                                    "from an earlier op's handle"},
            "value_slot": {"type": ["integer", "null"], "default": None,
                           "description": "the gate cell index, given directly"},
            "dx": {"type": "integer", "description": "mark x offset from the host's flow origin"},
            "dy": {"type": "integer", "description": "mark y offset from the host's flow origin"},
            "page_root": {"type": "integer",
                          "description": "screen root to re-walk in verify (4 = HOME)"},
            "expect": {"type": "array", "items": {"type": "integer"},
                       "description": "[x, y] the mark must land at on that page"},
            "preview_slots": {"type": ["object", "null"], "default": None,
                              "description": "{slot: value} the re-walk needs to reach the host"},
            "font": {"type": "integer", "description": "font slot the character is drawn from"},
            "char": {"type": "string", "description": "the character to draw (one byte)"},
        },
        "required": ["object", "dx", "dy", "page_root", "expect", "font", "char"],
    }

    def validate(self):
        self.object_index = self._param("object", kind=int)
        self.dx = self._param("dx", kind=int)
        self.dy = self._param("dy", kind=int)
        self.page_root = self._param("page_root", kind=int)
        self.preview_slots = self._param("preview_slots", None)
        self.expect = self._param("expect", kind=list)
        if len(self.expect) != 2 or not all(isinstance(v, int) for v in self.expect):
            raise RecipeError("BAD_PARAMS", "expect must be [x, y], got %r" % (self.expect,),
                              op_id=self.spec.id)
        self.font = self._param("font", kind=int)
        self.char = self._param("char", kind=str)
        self.gate = self._param("gate", None)
        self.value_slot = self._param("value_slot", None)

        if (self.gate is None) == (self.value_slot is None):
            raise RecipeError("BAD_PARAMS",
                              "give exactly one of gate={'ref':…,'field':…} or value_slot",
                              op_id=self.spec.id)
        if self.gate is not None and not (
                isinstance(self.gate, dict) and set(self.gate) == {"ref", "field"}):
            raise RecipeError("BAD_PARAMS",
                              "gate must be {'ref': op_id, 'field': name}, got %r" % (self.gate,),
                              op_id=self.spec.id)
        if self.value_slot is not None and not isinstance(self.value_slot, int):
            raise RecipeError("BAD_PARAMS", "value_slot must be an integer, got %r"
                              % (self.value_slot,), op_id=self.spec.id)
        if len(self.char) != 1:
            raise RecipeError("BAD_PARAMS", "char must be exactly one character, got %r"
                              % (self.char,), op_id=self.spec.id)
        try:
            self.char.encode("latin-1")
        except UnicodeEncodeError:
            raise RecipeError("BAD_PARAMS",
                              "char %r is not a single byte; a label byte indexes the font, so "
                              "only U+0000..U+00FF can name a glyph" % (self.char,),
                              op_id=self.spec.id) from None

    def apply(self, data, ctx):
        slot = self.value_slot
        if slot is None:
            ref, field = self.gate["ref"], self.gate["field"]
            h = ctx.handles.get(ref)
            if h is None or field not in h:
                raise RecipeError(
                    "BAD_REF",
                    "gate ref %r has no %r handle field; the op it names must run BEFORE this "
                    "one in the project's op list" % (ref, field), op_id=self.spec.id)
            slot = h[field]

        # The glyph has to be there. `decode_glyph` raises on the shared placeholder, which is
        # exactly the "no glyph installed" case -- so the refusal costs no extra scan.
        font = glyphs.font_for_slot(data, self.font)
        try:
            glyphs.decode_glyph(data, font.glyph_index(ord(self.char)))
        except glyphs.GlyphError:
            raise RecipeError(
                "NO_GLYPH",
                "font slot %d has no glyph for %r (it falls back to the shared placeholder); "
                "run install_glyph for this (font, char) earlier in the project"
                % (self.font, self.char), op_id=self.spec.id) from None

        # The box comes from the GLYPH, not from a parameter: a label with no box draws a single
        # column (the v20 defect), and a box smaller than its glyph clips it just as silently.
        # `metrics.font_metrics(...).height` is the cell the panel lays the line out in, so the
        # box is the glyph's own width by that cell -- never narrower than what will be drawn.
        g = glyphs.decode_glyph(data, font.glyph_index(ord(self.char)))
        box_w = max(g.width + g.x, 1)
        box_h = max(g.height + g.y, metrics.font_metrics(data, self.font).height)
        try:
            out, info = cornermark.add_mark(data, object_index=self.object_index,
                                            value_slot=slot, dx=self.dx, dy=self.dy,
                                            w=box_w, h=box_h,
                                            font_slot=self.font, char=self.char)
        except (cornermark.CornerMarkError, relocate.RelocationError, insert.InsertError) as e:
            raise RecipeError("MARK_REFUSED", str(e), op_id=self.spec.id)

        ctx.handles[self.spec.id] = dict(
            info, object=self.object_index, page_root=self.page_root,
            expect=list(self.expect),
            preview_slots={int(k): int(v) for k, v in (self.preview_slots or {}).items()})
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()          # no G12 group or binding is touched

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[h["object"]])
        sw = recs[-1]
        assert sw.wtype == 6 and sw.value_slot == h["value_slot"] \
            and sw.child_index == h["arms_base"] and sw.count == len(cornermark.MATCH_VALUES), \
            "object %d's last record is not the mark switch (wt=%s slot=%s child=%s count=%s)" % (
                h["object"], sw.wtype, sw.value_slot, sw.child_index, sw.count)

        # The default arm must be the BASE, or the mark never draws on a running timer.
        mt = guidata.find_match_table(data)
        mvs = [struct.unpack_from("<H", data, gs + mt + i * 2)[0]
               for i in (h["arms_base"], h["arms_base"] + 1)]
        assert mvs == cornermark.MATCH_VALUES, \
            "mark arms %d/%d carry match values %s, expected %s (the 0xFFFF default must be at " \
            "the base)" % (h["arms_base"], h["arms_base"] + 1, mvs, cornermark.MATCH_VALUES)

        # BOTH arms must be flow-neutral, or the mark drags the parent's next record. The mark
        # arm cancels its own offset; the empty arm moves nothing. This is the assertion the
        # first cut of this op did not have, and the defect it would have caught.
        mark = records.object_records(data, gs + tab[h["mark_object"]])
        empty = records.object_records(data, gs + tab[h["empty_object"]])
        assert [r.wtype for r in mark] == [11, 11] and mark[0].label == h["char"] \
            and mark[0].font == h["font_slot"] and mark[0].base_modes[:4] == [1, 1, 1, 1] \
            and layout._s16(mark[0].geom[0]) == h["dx"] \
            and layout._s16(mark[0].geom[1]) == h["dy"], \
            "mark arm is not the flow-relative single-character label it was built as"
        assert mark[0].geom[2] == h["w"] and mark[0].geom[3] == h["h"], \
            "the mark record does not declare the %dx%d box it was built with" % (h["w"], h["h"])
        assert (mark[1].label or "") == "" \
            and layout._s16(mark[1].geom[0]) == -h["dx"] \
            and layout._s16(mark[1].geom[1]) == -h["dy"], \
            "mark arm does not restore the flow origin it moved"
        assert [r.wtype for r in empty] == [11] and (empty[0].label or "") == "" \
            and layout._s16(empty[0].geom[0]) == 0 \
            and layout._s16(empty[0].geom[1]) == 0, \
            "empty arm draws something or moves the flow"

        # THE LAYOUT RE-WALK. The mark must land where the project said, on the page the project
        # named, in BOTH switch states -- and nothing else on that page may move in either. A
        # structural check cannot see a displaced sibling; this is the only thing that can.
        cell, base = h["value_slot"], h["arms_base"]
        def walk(value):
            return list(layout.Screen(data).walk(
                h["page_root"], slot_values={**h["preview_slots"], cell: value}))
        def placed(value):
            return {(p.obj_index, p.record_offset, p.x0, p.y0) for p in walk(value)}
        idle, running = placed(0), placed(1)
        marks = [p for p in walk(1)
                 if p.obj_index == h["mark_object"] and p.record_offset == mark[0].offset]
        assert len(marks) == 1, \
            "the mark is placed %d times on screen root %d, expected once" % (
                len(marks), h["page_root"])
        got = [marks[0].x0, marks[0].y0]
        assert got == h["expect"], \
            "the mark lands at %s on screen root %d, the project expected %s" % (
                got, h["page_root"], h["expect"])
        # THE BOX IT LANDS IN, not just the corner it starts at. v20 shipped a mark whose placed
        # box was (571,218)-(571,218) -- zero-wide -- and the panel drew one column of the glyph.
        # Checking only the origin is what let that through.
        drawn_w, drawn_h = marks[0].x1 - marks[0].x0, marks[0].y1 - marks[0].y0
        glyph = glyphs.decode_glyph(data, glyphs.font_for_slot(data, h["font_slot"])
                                    .glyph_index(ord(h["char"])))
        assert drawn_w >= glyph.width and drawn_h >= glyph.height, \
            "the mark is placed in a %dx%d box but its glyph is %dx%d -- the panel will clip it" \
            % (drawn_w, drawn_h, glyph.width, glyph.height)
        moved = {(o, r) for o, r, _, _ in idle ^ running} - {(base, mark[0].offset),
                                                            (base, mark[1].offset),
                                                            (base + 1, empty[0].offset)}
        assert not moved, \
            "toggling cell %d moves %d placement(s) that are not the mark itself: %s -- the arms " \
            "are not flow-neutral" % (cell, len(moved), sorted(moved)[:6])

        # And the glyph is really behind the character -- the read-back that turns "the record
        # names a character" into "the panel has pixels for it".
        return ("add_corner_mark: object %d gated on cell %d -> %dx%d glyph in a %dx%d box at "
                "(%d, %d) on root %d, font %d char %#04x, arms %d/%d, flow-neutral -- HARDWARE-%s"
                % (h["object"], h["value_slot"], glyph.width, glyph.height, drawn_w, drawn_h,
                   got[0], got[1], h["page_root"], h["font_slot"], ord(h["char"]),
                   h["arms_base"], h["arms_base"] + 1,
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED"))


@op("suppress_tank_status")
class SuppressTankStatus(Op):
    """Stop the G12 broadcasting `1FFB7` TANK_STATUS for tanks it has no probe on
    (`tankbroadcast.py`, `G12-CODE.md` "The per-rule broadcast switch").

    With no sender wired, a tank input floats to rail and the bucketing ladder reports **0%** --
    a well-formed frame no RV-C receiver can tell from a real empty tank. Because the panel's
    receive filter is source-blind, a replacement sender on the same instance does not win the
    slot, it alternates with the G12 at 1 Hz (the PC.46 failure mode). This op NOPs the `blx r3`
    that switches each named instance's rule on: 2 bytes each, same size, confined to the G12
    section, and one byte-pair per instance to revert.

    The panel keeps subscribing to `1FFB7` instances 0/1/2/3/18 and none of those decodes is
    range-fenced (`decodeguards`), so a replacement sender publishing the same instances should
    drive the stock gauges with no GuiData or GuiCode edit. That last step is an inference from a
    proven mechanism until a sender is on the coach; the suppression itself is what this op does.

    Params:
      instances: list[int] -- RV-C TANK_STATUS instances to silence.
                 Default [0, 1, 2, 18] (fresh / black / grey / grey-2). Instance 3 is the LPG
                 tank, whose sender is real and whose rule already switches itself off in AES
                 mode, so it is NOT in the default.

    Error codes:
      TANK_REFUSED -- an instance this build does not broadcast, a rule with anything but one
                      activation site, or a site whose bytes are not `blx r3` (already patched,
                      or a blob shape this module has not read).

    No capability delta: this REMOVES a broadcast the G12 makes. Nothing in the panel's
    (channel, command) capability set changes -- no binding, group or DGN filter is touched.

    HARDWARE-VERIFIED 2026-09-07 (`BUS-BASELINE.md` PC.62). A 12-second capture
    carries **zero** `19FFB7` frames where every earlier capture carries ~48, while the controls
    hold exactly as designed: `18BFC19C` still sends all four raw ADCs and `19AAB79C` still sends
    the LPG mirror. The panel then showed a **foreign** sender driving its stock gauge -- an
    injected `19FFB7AF#002238FFFFFFFFFF` (34/56) read **61%** on HOME's FRESH gauge, and
    `000D64...` (13/100) read **13%** -- with the other three tanks showing the `--%` no-data
    sentinel rather than a stale 0%. So freeing an instance and letting another RV-C sender own
    it works end to end, with no GuiData or GuiCode edit anywhere in the path.
    """

    hardware_verified = True

    PARAMS = {
        "properties": {
            "instances": {"type": "array", "items": {"type": "integer"}},
        },
        "required": [],
    }

    def validate(self):
        raw = self._param("instances", list(tankbroadcast.DEFAULT_INSTANCES), kind=list)
        if not raw:
            raise RecipeError("BAD_PARAMS", "instances must name at least one tank",
                              op_id=self.spec.id)
        if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= 255 for i in raw):
            raise RecipeError("BAD_PARAMS", "instances must be RV-C instance bytes (0-255)",
                              op_id=self.spec.id)
        self.instances = list(dict.fromkeys(raw))

    def apply(self, data, ctx):
        try:
            before = {b.instance: b for b in tankbroadcast.tank_broadcasts(data)}
            out, edits = tankbroadcast.suppress(data, self.instances)
        except tankbroadcast.TankBroadcastError as e:
            raise RecipeError("TANK_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        ctx.handles[self.spec.id] = {
            "instances": self.instances,
            "rules": {i: before[i].rule_index for i in self.instances},
            "sites": {i: before[i].activate_file for i in self.instances},
        }
        out, _ = patch.reseal(out)
        return out

    def capability_delta(self, ctx):
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        quiet = set(tankbroadcast.suppressed_instances(data))
        assert set(h["instances"]) <= quiet, \
            "suppressed %s, wanted %s" % (sorted(quiet), h["instances"])
        still = [b.instance for b in tankbroadcast.tank_broadcasts(data)
                 if b.instance not in quiet]
        return ("suppress_tank_status: 1FFB7 instance(s) %s silenced (rules %s, %d G12 byte-pair"
                "(s)); still broadcast: %s -- HARDWARE-%s"
                % (h["instances"], [h["rules"][i] for i in h["instances"]],
                   len(h["instances"]), still or "none",
                   "VERIFIED" if self.hardware_verified else "UNVERIFIED"))


@op("add_level_page")
class AddLevelPage(Op):
    """The LEVEL page on screen 29 (root object 32, the stranded SLIDES/AWNINGS auto-level page
    `show_nav_slides_icon` routes the nav rail's slide icon to), fed by the level sensor's
    `0x0FFB0` records.

    The body, object 367 (root 32's only child page, record 32[6]), is replaced in place with
    `levelpage.build_body`'s stream (`relocate.replace_object_records`); root 32's records 0-5
    (background, header, title, header widgets 892, nav rail 974) are left byte-identical. The
    auto-level controls the old body held are orphaned; none worked on this coach (PC.57).

    Live (`preview` false), in one op so the cell table grows once and the hook gains one link:

      1. RECEIVE: 14 APPENDED variable-table entries on `dgn` (`appendfilter.append_filters`),
         one per `levelfeed.VARS` row, each gated on payload byte 0 == its record number
         (`+4` = record, `+5` = 0x00: whole-byte compare of payload index 0) with refresh class
         `refresh` (1 = 10 s, so a silent sensor reads 0xFFFF, the page's "no data").
      2. CELLS: all 20 `levelfeed.CELLS` minted fresh in ONE `readout.allocate_fresh_cells`
         call and recorded in `ctx.authored_cells` (the cell-aliasing gate).
      3. ROUTINE: `levelfeed.build` -- one per-tick routine copying the slots into the cells and
         deriving the wheel / banner / short / word states -- appended to GuiCode and chained
         onto the per-tick hook ahead of whatever the hook reached before.
      4. PAGE: the live body (four readouts, eight enrolled type-6 switches, their 24 arms
         appended as one band group).

    Preview (`preview` true) draws the mockup's example state as static text: no receive side,
    no cells and no GuiCode (v34a, a drawing check before the live build).

    Controls (`controls` true): the
    Blocks-carried stepper (- / +) and the Zero Level button. Three free group slots
    (`newbutton.free_group_slots` minus `levelcontrols.RESERVED_GROUPS` -- 68/69, the
    inverter/charger templates the PD-inverter bridge will drive -- taken in order minus, plus,
    zero) get
    `levelcontrols.group_entry` entries, and ONE `optionrow._grow_bindings` call appends their
    four bindings (`levelcontrols.bindings`: minus, plus, zero pitch, zero roll) at
    `binding_start`. This runs FIRST, before the receive side and the body replacement (the Batt
    Heat order), so the body is drawn on the grown image. The body then carries the stepper in
    place of the Carried row -- the carried readout moves into its centre panel -- the Zero
    Level button, and three wt-12 touch regions (`levelpage.CONTROLS`) last in the static body,
    each touch id the group index. `stepper_dgn` picks the - / + DGN: 0x0EFFF (J1939
    proprietary-A, what v35/v36 send) or 0x1EFBF (RV-C's custom DGN 1EF00h addressed to the
    sensor's pinned BFh, v37). The controls are allowed with `preview` too: the stepper shows the example carried value as static text and the touches still transmit, so a preview
    build can test the transmit side alone. They only transmit: the carried value is the
    sensor's own report, never written by the panel.

    `arrow` names the glyph the recipe's own `install_glyph` op installed before this one
    (`levelpage.recipe_ops` emits both); a missing glyph would draw the placeholder, so it is
    refused.

    Error codes:
      BAD_PARAMS      -- a parameter is malformed (dgn not 18-bit, refresh outside 0..3, arrow,
                         accent, orientation not in `levelpage.ORIENTATIONS`, controls not a
                         bool).
      NO_FREE_GROUP   -- `controls`: fewer than three free group-table slots outside
                         `levelcontrols.RESERVED_GROUPS`.
      GROW_REFUSED    -- `controls`: the binding table could not be grown (arena, gates) or a
                         donor group is not the stock shape `levelcontrols` expects.
      NO_ARROW_GLYPH  -- `arrow` names a character that still falls back to the placeholder.
      APPEND_REFUSED  -- the 14 entries could not be appended (registry cap, arena, gates).
      NO_DISPLAY_CELL -- the cell table could not be grown for the 20 cells.
      ROUTINE_REFUSED -- the routine could not be resolved, built or appended to GuiCode.
      DETOUR_REFUSED  -- the per-tick hook did not hold the `bl` the chain expects.
      PAGE_REFUSED    -- the body could not be drawn or object 367 could not be replaced.
    """

    # Nothing of this page has been on the coach: a live readout inside a switch arm has no
    # hardware precedent and the routine is new GuiCode (spec section 10).
    hardware_verified = False

    ROOT = 32
    BODY = 367
    KEPT_ROOT_RECORDS = 6

    PARAMS = {
        "properties": {
            "preview": {"type": "boolean", "default": False,
                        "description": "draw the mockup's example state as static text (no "
                                       "receive side, no GuiCode)"},
            "dgn": {"type": "integer", "default": 0x0FFB0,
                    "description": "the level sensor's 18-bit DGN"},
            "refresh": {"type": "integer", "default": 1,
                        "description": "staleness class of the 14 entries (1 = 10 s)"},
            "arrow": {"type": "object",
                      "description": "{slot, char}: the up-arrow glyph an earlier install_glyph "
                                     "op installed"},
            "accent": {"type": "array", "default": list(levelpage.ACCENT),
                       "description": "[r, g, b] of the needs-blocks wheel tiles and the SHORT ON "
                                      "BLOCKS strip. The default was pale amber (0xE6,0xA0,0x28) "
                                      "before it became orange, so a project that sets neither "
                                      "accent nor orientation no longer reproduces an image "
                                      "built under the old default"},
            "orientation": {"type": "string", "default": "rear",
                            "enum": list(levelpage.ORIENTATIONS),
                            "description": "\"rear\": the van drawn rotated 180 degrees (front at "
                                           "the bottom, driver on the right), each wheel's tile "
                                           "beside its rotated wheel; \"front\": the original "
                                           "drawing"},
            "controls": {"type": "boolean", "default": False,
                         "description": "add the Blocks-carried stepper (- / + send 18EFFF9F "
                                        "4C5604/4C5603) and Zero Level (3-s hold sends 19FDE89F "
                                        "and 19FDEA9F); three new groups, four new bindings"},
            "arrow_probe": {"type": "boolean", "default": False,
                            "description": "DIAGNOSTIC: draw the tiles' arrow three ways "
                                           "plus once outside the arms (`levelpage.ARROW_PROBE`) "
                                           "to find what cuts it on the coach"},
            "repaint": {"type": "boolean", "default": False,
                        "description": "repaint-on-change: each LEVEL/popup value sits in a "
                                       "two-arm switch on a toggle the routine flips when the "
                                       "value changes, so a narrower value never leaves the old "
                                       "one's edge (verified on the coach)"},
            "popup_background": {"type": "boolean", "default": False,
                                 "description": "the setup popup paints its own background, "
                                                "header bar and 'LEVEL SETUP' title (a screen "
                                                "change clears to white, seen on the coach)"},
            "setup": {"type": "boolean", "default": False,
                      "description": "move Zero Level and the Blocks-carried stepper into a LEVEL "
                                     "SETUP popup on screen 12 (`levelsetup`), with block height "
                                     "and tolerance steppers and AUTO; the LEVEL page gets a "
                                     "Setup button and a read-only Carried. Needs `controls`"},
            "geometry": {"type": "boolean", "default": False,
                         "description": "add Wheelbase and Track rows to the LEVEL SETUP popup "
                                        "(-/+ 0.5 in on level-sensor ops 0Bh-0Eh, repeating "
                                        "while held; values from record 04h bytes 1-4), in a "
                                        "taller five-row dialog. Needs `setup`"},
            "arrow_last": {"type": "boolean", "default": False,
                           "description": "draw each wheel tile's up arrow after its two readouts: "
                                          "a readout repaints its background over about 50 px "
                                          "left of its pen whatever its field width (seen on the "
                                          "coach), which wiped the arrow drawn before it"},
            "arrow_probe2": {"type": "boolean", "default": False,
                             "description": "DIAGNOSTIC: one tile draws its arrow last, "
                                            "another paints its readout backgrounds green/blue "
                                            "(`levelpage.ARROW_PROBE2_*`)"},
            "fill_safe": {"type": "boolean", "default": False,
                          "description": "narrow the wheel tiles' readout fields (layers 1, lift "
                                         "3 characters) so no background fill can cover the "
                                         "arrow or leave the tile in any measuring class "
                                         "(`readoutfill`); 10+ layers / 10.0+ in show dashes. "
                                         "False keeps the original (2, 4)"},
            "stepper_dgn": {"type": "integer", "default": levelcontrols.STEPPER_DGN_DEFAULT,
                            "description": "the 18-bit DGN the - / + send on: 0x0EFFF (J1939 "
                                           "proprietary-A, the first form flashed) or 0x1EFBF (the "
                                           "RV-C custom DGN addressed to the sensor at BF: "
                                           "19EFBF9F, in use since 2026-09-28); "
                                           "needs `controls`"},
        },
        "required": ["arrow"],
    }

    def validate(self):
        self.preview = self._param("preview", False, kind=bool)
        self.dgn = self._param("dgn", 0x0FFB0, kind=int)
        self.refresh = self._param("refresh", 1, kind=int)
        arrow = self._param("arrow", kind=dict)
        if not 0 <= self.dgn <= statusbind.DGN_MASK:
            raise RecipeError("BAD_PARAMS", "dgn %#x is not an 18-bit DGN" % self.dgn,
                              op_id=self.spec.id)
        if not 0 <= self.refresh <= 3:
            raise RecipeError("BAD_PARAMS", "refresh class %d outside 0..3" % self.refresh,
                              op_id=self.spec.id)
        slot, char = arrow.get("slot"), arrow.get("char")
        if set(arrow) != {"slot", "char"} or not isinstance(slot, int) \
                or not isinstance(char, str) or len(char) != 1:
            raise RecipeError("BAD_PARAMS", "arrow must be {slot: int, char: one character}, "
                              "got %r" % (arrow,), op_id=self.spec.id)
        self.arrow = (slot, char)
        accent = self._param("accent", list(levelpage.ACCENT), kind=list)
        if len(accent) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in accent):
            raise RecipeError("BAD_PARAMS", "accent must be [r, g, b] bytes, got %r" % (accent,),
                              op_id=self.spec.id)
        self.accent = tuple(accent)
        self.orientation = self._param("orientation", "rear", kind=str)
        if self.orientation not in levelpage.ORIENTATIONS:
            raise RecipeError("BAD_PARAMS", "orientation %r is not one of %s"
                              % (self.orientation, list(levelpage.ORIENTATIONS)),
                              op_id=self.spec.id)
        self.controls = self._param("controls", False, kind=bool)
        self.fill_safe = self._param("fill_safe", False, kind=bool)
        self.arrow_probe = self._param("arrow_probe", False, kind=bool)
        self.arrow_probe2 = self._param("arrow_probe2", False, kind=bool)
        self.arrow_last = self._param("arrow_last", False, kind=bool)
        self.setup = self._param("setup", False, kind=bool)
        self.repaint = self._param("repaint", False, kind=bool)
        self.popup_background = self._param("popup_background", False, kind=bool)
        self.geometry = self._param("geometry", False, kind=bool)
        if self.geometry and not self.setup:
            raise RecipeError("BAD_PARAMS", "geometry needs setup", op_id=self.spec.id)
        if (self.repaint or self.popup_background) and not self.setup:
            raise RecipeError("BAD_PARAMS", "repaint / popup_background need setup",
                              op_id=self.spec.id)
        if self.setup and (not self.controls or self.preview):
            raise RecipeError("BAD_PARAMS", "setup needs controls on the live page",
                              op_id=self.spec.id)
        self.stepper_dgn = self._param("stepper_dgn", levelcontrols.STEPPER_DGN_DEFAULT,
                                       kind=int)
        if not 0 <= self.stepper_dgn <= statusbind.DGN_MASK:
            raise RecipeError("BAD_PARAMS", "stepper_dgn %#x is not an 18-bit DGN"
                              % self.stepper_dgn, op_id=self.spec.id)
        if self.stepper_dgn != levelcontrols.STEPPER_DGN_DEFAULT and not self.controls:
            raise RecipeError("BAD_PARAMS", "stepper_dgn needs controls", op_id=self.spec.id)

    def _root_records(self, data):
        gs, tab = _gs_tab(data)
        return records.object_records(data, gs + tab[self.ROOT])

    def apply(self, data, ctx):
        root = self._root_records(data)
        body_ref = root[self.KEPT_ROOT_RECORDS] if len(root) > self.KEPT_ROOT_RECORDS else None
        if body_ref is None or body_ref.child_index != self.BODY:
            raise RecipeError("PAGE_REFUSED", "root %d record %d does not name object %d"
                              % (self.ROOT, self.KEPT_ROOT_RECORDS, self.BODY),
                              op_id=self.spec.id)
        kept = [data[r.offset:r.end].hex() for r in root[:self.KEPT_ROOT_RECORDS]]
        slot, char = self.arrow
        try:
            font = glyphs.font_for_slot(data, slot)
            glyphs.decode_glyph(data, font.glyph_index(ord(char)))
        except (glyphs.GlyphError, ValueError, IndexError) as e:
            raise RecipeError("NO_ARROW_GLYPH", "font slot %d %r holds no glyph (%s); install it "
                              "first (levelpage.recipe_ops)" % (slot, char, e),
                              op_id=self.spec.id)

        handles = {"preview": self.preview, "root_records": kept, "vars": [], "cells": {},
                   "cell_addrs": {}, "arms": {}, "routine_addr": None,
                   "controls": self.controls, "groups": {}, "group_entries": {},
                   "binding_start": None, "stepper_dgn": self.stepper_dgn}
        out = data
        if self.controls:
            out = self._grow_controls(out, ctx, handles)
        cells = None
        if not self.preview:
            out, cells = self._receive_and_routine(out, ctx, handles)

        # (4) PAGE
        try:
            out, info = levelpage.build_body(out, cells=cells, arrow=self.arrow, accent=self.accent,
                                             orientation=self.orientation,
                                             controls={n: handles["groups"][n]
                                                       for n in levelpage.CONTROL_NAMES}
                                             if handles["groups"] else None,
                                             fill_safe=self.fill_safe,
                                             arrow_probe=self.arrow_probe,
                                             arrow_probe2=self.arrow_probe2,
                                             arrow_last=self.arrow_last, setup=self.setup,
                                             refresh=self.repaint)
            out, edits = relocate.replace_object_records(out, self.BODY, info["body"])
        except (levelpage.LevelPageError, ValueError, relocate.RelocationError) as e:
            raise RecipeError("PAGE_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        handles["arms"] = info["switches"]
        handles["body_records"] = info["records"]
        handles["setup"] = self.setup
        if self.setup:
            out = self._build_popup(out, ctx, handles, cells)
        out, _ = patch.reseal(out)
        ctx.handles[self.spec.id] = handles
        return out

    def _grow_controls(self, data, ctx, handles):
        """Three fresh groups and their four bindings in one grow; the touch id is the group."""
        # never a group GuiCode drives (`newbutton.guicode_selected_groups`): v45's Track + took
        # 136 and its hold never repeated on the coach. The repeating geometry steppers take
        # `newbutton.hold_safe_groups` only; the rest keep their v42-v44 allocation.
        selected = newbutton.guicode_selected_groups(data)
        free = [g for g in newbutton.free_group_slots(data)
                if g not in levelcontrols.RESERVED_GROUPS and g not in selected]
        names = self._control_names()
        held = [n for n in names if n in levelcontrols.GEOMETRY_CONTROLS]
        plain = [n for n in names if n not in held]
        if len(free) < len(plain):
            raise RecipeError("NO_FREE_GROUP", "the LEVEL controls need %d free group slots, "
                              "%d are free outside the reserved %s (%s)"
                              % (len(plain), len(free), sorted(levelcontrols.RESERVED_GROUPS),
                                 free),
                              op_id=self.spec.id)
        safe = [g for g in newbutton.hold_safe_groups(data, levelcontrols.RESERVED_GROUPS)
                if g not in free[:len(plain)]]
        if len(safe) < len(held):
            raise RecipeError("NO_FREE_GROUP", "the geometry steppers need %d hold-safe group "
                              "slots, %d are free (%s)" % (len(held), len(safe), safe),
                              op_id=self.spec.id)
        assign = dict(zip(plain, free)) | dict(zip(held, safe))
        free = [assign[n] for n in names]
        binding_start = len(guidata.binding_table(data))
        payload, writes, at = b"", [], binding_start
        try:
            for name, group in zip(names, free):
                raws = levelcontrols.bindings(name, self.stepper_dgn)
                entry = levelcontrols.group_entry(data, name, at)
                writes.append((group, entry))
                handles["groups"][name] = group
                handles["group_entries"][name] = entry.hex()
                payload += b"".join(raws)
                at += len(raws)
            out, edits = optionrow._grow_bindings(data, payload, writes)
        except (optionrow.OptionRowError, levelcontrols.LevelControlsError) as e:
            raise RecipeError("GROW_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        handles["binding_start"] = binding_start
        return out

    def _control_names(self):
        return levelpage.CONTROL_NAMES + (levelcontrols.SETUP_CONTROLS if self.setup else ()) \
            + (levelcontrols.GEOMETRY_CONTROLS if self.geometry else ())

    def _build_popup(self, data, ctx, handles, cells):
        """Screen 12 as the LEVEL SETUP popup (`levelsetup`): leaf 0x7ED -> 12, the AUTO switch's
        arms appended, and root 15's records replaced (header/rail references kept, no
        background, touches last)."""
        try:
            out, e = navicon.retarget_action_leaf(data, levelsetup.SETUP_ACTION, 29, 12)
        except ValueError as err:
            raise RecipeError("NAV_REFUSED", str(err), op_id=self.spec.id)
        ctx.edits.extend(relocate.Edit(o, a, b, "GuiCode", d) for o, a, b, d in e)
        try:
            lay = levelsetup.layout(self.geometry)
            body, groups = levelsetup.body_records(out, cells, self.repaint, self.geometry)
            arms, values = levelsetup.auto_arms(out, self.geometry)
            groups = [("auto", (levelsetup.AUTO[0], lay["row_y"]["tol"]), arms, values)] \
                + groups
            all_arms = [pd.stream(a) for _n, _at, g, _v in groups for a in g]
            out, base = switchbuild.append_arms(out, all_arms,
                                                [v for *_r, vs in groups for v in vs])
            gs, tab = _gs_tab(out)
            recs = levelsetup.kept_root_records(out, gs, tab, self.popup_background)
            recs += body
            switches, at = {}, base
            for name, pos, g, _v in groups:
                recs.append(switchbuild.author_switch(out, value_slot=cells[name], arms_base=at,
                                                      count=len(g), at=tuple(pos)))
                switches[name] = (at, len(g))
                at += len(g)
            ids = dict(handles["groups"], close=levelsetup.CLOSE_ACTION)
            recs += levelsetup.touch_records(out, ids, self.geometry)
            out, edits = relocate.replace_object_records(out, levelsetup.POPUP_ROOT,
                                                         pd.stream(recs))
        except (levelsetup.LevelSetupError, levelpage.LevelPageError, ValueError,
                relocate.RelocationError) as err:
            raise RecipeError("PAGE_REFUSED", "setup popup: %s" % err, op_id=self.spec.id)
        ctx.edits.extend(edits)
        handles["popup"] = {"switches": switches, "touch_ids": ids,
                            "background": self.popup_background, "geometry": self.geometry}
        out, _ = patch.reseal(out)
        return out

    def _verify_popup(self, data, h):
        """Screen 12's root is the popup: its header/rail references, the three readouts on their
        cells, the AUTO switch on the auto cell, the nine touches last with their ids, and the
        two leaves (0x7ED -> 12, 0x7DC -> 29)."""
        gs, tab = _gs_tab(data)
        recs = records.object_records(data, gs + tab[levelsetup.POPUP_ROOT])
        refs = [r.child_index for r in recs if r.wtype == 5]
        assert refs[:2] == list(levelsetup.ROOT_REFS), \
            "popup root does not carry the header/rail references first: %s" % refs
        full = [r for r in recs if r.wtype == 4 and r.geom[2] == 799 and r.geom[3] == 479]
        assert bool(full) == h["popup"]["background"], \
            "popup background is %s, built %s" % (bool(full), h["popup"]["background"])
        if full:
            assert recs.index(full[0]) == 0 and any(
                r.wtype == 11 and r.label == levelsetup.TITLE for r in recs), \
                "popup background or its LEVEL SETUP title is missing"
        sw = [r for r in recs if r.wtype == 6]
        want_sw = sorted((h["cells"][n], b, c) for n, (b, c) in h["popup"]["switches"].items())
        assert sorted((r.value_slot, r.child_index, r.count) for r in sw) == want_sw, \
            "the popup switches are wrong: %s" % [(r.value_slot, r.child_index, r.count)
                                                  for r in sw]
        reads = {r.value_slot for r in recs if r.wtype == 8}
        for r in sw:
            for a in range(r.child_index, r.child_index + r.count):
                reads |= {x.value_slot for x in records.object_records(data, gs + tab[a])
                          if x.wtype == 8}
        geometry = h["popup"].get("geometry", False)
        values = ("height", "tol", "carried") + (("wb", "track") if geometry else ())
        assert {h["cells"][n] for n in values} <= reads, \
            "a popup readout does not read its cell"
        touches = [r for r in recs if r.wtype == 12]
        assert recs[-len(touches):] == touches, "the popup touches are not last"
        rr, ids = levelsetup.rects(geometry), h["popup"]["touch_ids"]
        names = levelsetup.layout(geometry)["touch_names"]
        got = [(tuple(r.geom[:4]), data[r.end - 2] | (data[r.end - 1] << 8)) for r in touches]
        want = [(tuple(rr[n]), ids[n]) for n in names]
        assert got == want, "popup touches %s are not %s" % (got, want)
        leaves = {r.touch_id: r.screen for r in navmap.routes(data)}
        assert leaves.get(levelsetup.SETUP_ACTION) == 12 and \
            leaves.get(levelsetup.CLOSE_ACTION) == 29, "the popup's leaves are wrong"
        return "; popup: screen 12 root %d, %d records, %d touches, AUTO switch%s" % (
            levelsetup.POPUP_ROOT, len(recs), len(names),
            ", wheelbase/track rows" if geometry else "")

    def _verify_controls(self, data, h, body):
        """The three groups, their decoded wire frames and the three touch regions, exactly."""
        touches = [r for r in body if r.wtype == 12]
        if not h["controls"]:
            assert not touches, "object %d carries touch regions without controls" % self.BODY
            return ""
        gt, bt = guidata.group_table(data), guidata.binding_table(data)
        at = h["binding_start"]
        names = self._control_names()
        rects = levelpage.controls_rects(self.orientation)
        on_page = ([(rects["zero"], levelsetup.SETUP_ACTION)] if h.get("setup") else
                   [(rects[n], h["groups"][n]) for n in levelpage.CONTROL_NAMES])
        got_touch = [(tuple(r.geom[:4]), data[r.end - 2] | (data[r.end - 1] << 8))
                     for r in touches]
        assert got_touch == [(tuple(rc), t) for rc, t in on_page], \
            "LEVEL touch regions %s are not %s" % (got_touch, on_page)
        assert body[-len(touches):] == touches, "the LEVEL touches are not last in the body"
        for name in names:
            g = gt[h["groups"][name]]
            want = levelcontrols.frames(name, h["stepper_dgn"])
            assert g.raw.hex() == h["group_entries"][name], \
                "group %d (%s) is %s, built %s" % (g.index, name, g.raw.hex(),
                                                   h["group_entries"][name])
            hold = levelcontrols.ZERO_HOLD_SECONDS if name == "zero" else 1
            rates = levelcontrols.GEOMETRY_HELD_RATE \
                if name in levelcontrols.GEOMETRY_CONTROLS else 0
            assert (g.raw[0] >> 4, g.raw[2], g.raw[3] & 0x80, g.start, g.count) == \
                (hold, rates, 0, at, len(want)), \
                "group %d (%s): hold %d, rates %#x, byte 3 bit 7 %#x, start %d, count %d" % (
                    g.index, name, g.raw[0] >> 4, g.raw[2], g.raw[3] & 0x80, g.start, g.count)
            raws = [bt[i].raw for i in range(g.start, g.start + g.count)]
            got = [newbutton.binding_wire_frame(r) for r in raws]
            assert got == want, "group %d (%s) sends %s, not %s" % (
                g.index, name, ["%08X#%s" % (i, p.hex()) for i, p in got],
                ["%08X#%s" % (i, p.hex()) for i, p in want])
            assert [bytes(r) for r in raws] == levelcontrols.bindings(name, h["stepper_dgn"]), \
                "group %d (%s) bindings differ from levelcontrols (mask / repeat)" % (g.index,
                                                                                      name)
            at += len(want)
        popup = self._verify_popup(data, h) if h.get("setup") else ""
        if h.get("setup"):
            return (", controls: groups %s (bindings %d-%d)%s"
                    % ("/".join("%s %d" % (n, h["groups"][n]) for n in names),
                       h["binding_start"], at - 1, popup))
        # the stepper's centre panel shows the carried value: the carried cell live, the
        # example text in the preview
        cx = levelpage.CONTROLS["minus"][0] + levelpage.CONTROLS["minus"][2]
        px = levelpage.CONTROLS["plus"][0]
        if h["preview"]:
            assert any(r.wtype == 11 and r.label == levelpage.EXAMPLE["carried"]
                       and cx < r.geom[0] < px for r in body), \
                "the preview stepper does not show the example carried value"
        else:
            carried = [r for r in body if r.wtype == 8
                       and r.value_slot == h["cells"]["carried"]]
            assert len(carried) == 1 and cx < carried[0].geom[0] < px, \
                "the stepper's readout is not the carried cell %d" % h["cells"]["carried"]
        return (", controls: groups %s (bindings %d-%d)"
                % ("/".join("%s %d" % (n, h["groups"][n]) for n in names),
                   h["binding_start"], at - 1))

    def _receive_and_routine(self, data, ctx, handles):
        # (1) RECEIVE: one appended entry per sensor field, gated on payload byte 0 == record.
        try:
            out, edits, new_vars = appendfilter.append_filters(data, [
                appendfilter.NewFilter(dgn=self.dgn, mode=mode, payload_byte=byte,
                                       refresh=self.refresh, instance=rec, compare_byte=0x00)
                for _name, rec, byte, mode in levelfeed.vars_for(self.setup, self.geometry)])
        except (relocate.RelocationError, appendfilter.AppendFilterError) as e:
            raise RecipeError("APPEND_REFUSED", str(e), op_id=self.spec.id)
        ctx.edits.extend(edits)
        handles["vars"] = list(new_vars)
        slots = {name: register.status_slot_addr(v)
                 for (name, *_r), v in zip(levelfeed.vars_for(self.setup, self.geometry),
                                           new_vars)}

        # (2) CELLS: one grow for all twenty (allocate_fresh_cells reserves SRAM per call).
        try:
            out, fresh, cell_edits = readout.allocate_fresh_cells(
                out, [(w, readout._TYPE_FOR_WIDTH[w])
                      for _n, w in levelfeed.cells_for(self.setup, self.repaint, self.geometry)])
        except readout.ReadoutError as e:
            raise RecipeError("NO_DISPLAY_CELL", str(e), op_id=self.spec.id)
        ctx.edits.extend(cell_edits)
        cells = {name: c for (name, _w), c in
                 zip(levelfeed.cells_for(self.setup, self.repaint, self.geometry), fresh)}
        ctx.authored_cells.update(c.slot for c in fresh)     # gated: must be freshly grown
        handles["cells"] = {n: c.slot for n, c in cells.items()}
        handles["cell_addrs"] = {n: c.addr for n, c in cells.items()}

        # (3) ROUTINE, then the detour (AddAesBattHeatStatus's sequence).
        try:
            plan = levelfeed.resolve(out, slots=slots, cells=cells)
            gc = theme.guicode_section(out)
            code_load_addr = theme.GUICODE_BASE_DEFAULT + (gc.length - 4)
            _check_append_point(code_load_addr, self.spec.id)
            blob, _ = levelfeed.build(plan, code_load_addr)
            grown, load_off, _ = growsection.grow_section(out, "GuiCode", blob)
        except (levelfeed.LevelFeedError, bitcombine.BitCombineError,
                followchannel.FollowChannelError, growsection.GrowSectionError) as e:
            raise RecipeError("ROUTINE_REFUSED", str(e), op_id=self.spec.id)
        gcs = next(s for s in container.parse_container(grown).sections if "GuiCode" in s.name)
        ctx.edits.append(relocate.Edit(
            gcs.offset + load_off, b"", blob, "GuiCode",
            "level page: per-tick feed (vars %d-%d -> cells %d-%d)"
            % (new_vars[0], new_vars[-1], min(handles["cells"].values()),
               max(handles["cells"].values()))))
        try:
            hook_at, hook_old, hook_new = levelfeed.detour_edit(plan, code_load_addr)
        except followchannel.FollowChannelError as e:
            raise RecipeError("DETOUR_REFUSED", str(e), op_id=self.spec.id)
        file_off = gcs.offset + hook_at
        if grown[file_off:file_off + 4] != hook_old:
            raise RecipeError("DETOUR_REFUSED", "GuiCode+%#x holds %s, not %s"
                              % (hook_at, grown[file_off:file_off + 4].hex(), hook_old.hex()),
                              op_id=self.spec.id)
        buf = bytearray(grown)
        buf[file_off:file_off + 4] = hook_new
        ctx.edits.append(relocate.Edit(file_off, hook_old, hook_new, "GuiCode",
                         "level page: detour bl -> %#x" % code_load_addr))
        handles["routine_addr"] = code_load_addr + readout.ENTRY_OFF
        out, _ = patch.reseal(bytes(buf))
        return out, {n: c.slot for n, c in cells.items()}

    def capability_delta(self, ctx):
        # The controls' bindings (0x0EFFF, 0x1FDE8/0x1FDEA) command no G12 channel, and nothing
        # is removed, so stock and output express the same (channel, command) pairs.
        return set(), set()

    def verify(self, data, ctx):
        h = ctx.handles[self.spec.id]
        root = self._root_records(data)
        got = [data[r.offset:r.end].hex() for r in root[:self.KEPT_ROOT_RECORDS]]
        assert got == h["root_records"], "root %d records 0-%d changed" % (
            self.ROOT, self.KEPT_ROOT_RECORDS - 1)
        assert root[self.KEPT_ROOT_RECORDS].child_index == self.BODY, \
            "root %d no longer names the body %d" % (self.ROOT, self.BODY)
        gs, tab = _gs_tab(data)
        body = records.object_records(data, gs + tab[self.BODY])
        assert len(body) == h["body_records"], \
            "object %d holds %d records, built %d" % (self.BODY, len(body), h["body_records"])
        switches = [r for r in body if r.wtype == 6]
        assert all(r.header[4] & _ENROLL_BIT for r in switches), \
            "a LEVEL switch is not enrolled for live redraw"
        controls = self._verify_controls(data, h, body)
        vt = guidata.variable_table(data)
        mine = [i for i, v in enumerate(vt)
                if (struct.unpack_from("<I", v.raw, 0)[0] & statusbind.DGN_MASK) == self.dgn]
        if h["preview"]:
            assert not switches and not mine, "the preview carries live parts"
            return ("add_level_page: PREVIEW -- screen 29 body (object %d, %d records) draws the "
                    "example state; no receive side, no GuiCode%s -- HARDWARE-UNVERIFIED"
                    % (self.BODY, len(body), controls))

        # RECEIVE: exactly the 14 entries, each gated on its record number, class `refresh`.
        assert mine == h["vars"], "vars on %#x are %s, built %s" % (self.dgn, mine, h["vars"])
        want = levelfeed.vars_for(self.setup, self.geometry)
        assert len(mine) == len(want), "expected %d vars on %#x, found %d" % (
            len(want), self.dgn, len(mine))
        for i, (name, rec, byte, mode) in zip(mine, want):
            raw = vt[i].raw
            assert (raw[4], raw[5]) == (rec, 0), \
                "var %d (%s) filters +4/+5 = %#x/%#x, not record %d / 0" % (i, name, raw[4],
                                                                         raw[5], rec)
            assert ((raw[2] >> 2) & 3, raw[2] >> 4, raw[3] & 7) == (self.refresh, mode, byte), \
                "var %d (%s) refresh/mode/byte wrong: %s" % (i, name, bytes(raw).hex())
        assert len(statusbind.registered_dgns(data)) <= statusbind.REGISTRY_CAP, \
            "the level DGN pushed the variable table over REGISTRY_CAP"

        # CELLS <-> ROUTINE: every widget cell names the address the routine writes, and the
        # routine's pool carries every slot and cell address (the v28/v29 class of defect).
        table = status_map.cell_table(data)
        gc = theme.guicode_section(data)
        # From the routine's entry to the section's end: its own code and pool (plus any routine
        # a later op appended), never a stock literal that happens to name the same word.
        start = h["routine_addr"] - theme.GUICODE_BASE_DEFAULT
        assert 0 <= start < gc.length, "routine %#x is outside GuiCode" % h["routine_addr"]
        code = data[gc.offset + start:gc.offset + gc.length]
        for name, idx in h["cells"].items():
            addr = h["cell_addrs"][name]
            assert idx < len(table) and table[idx] == addr, \
                "cell %s index %d does not name %#x" % (name, idx, addr)
            assert struct.pack("<I", addr) in code, "no routine carries cell %s %#x" % (name,
                                                                                         addr)
        for v in h["vars"]:
            a = register.status_slot_addr(v)
            assert struct.pack("<I", a) in code, "no routine carries var %d's slot %#x" % (v, a)
        try:
            chain = followchannel.chain_from_hook(data)
        except followchannel.FollowChannelError as e:
            raise AssertionError("per-tick chain broken: %s" % e)
        assert h["routine_addr"] in chain, \
            "the LEVEL routine %#x is not on the per-tick chain %s" % (
                h["routine_addr"], [hex(c) for c in chain])

        # PAGE: one enrolled switch per state cell, with its arm count.
        by_slot = {r.value_slot: r for r in switches}
        assert len(switches) == len(h["arms"]) == len(by_slot), \
            "expected %d switches, found %d" % (len(h["arms"]), len(switches))
        for name, s in h["arms"].items():
            sw = by_slot.get(h["cells"][name])
            assert sw is not None and sw.count == s["count"], \
                "switch %s on cell %d missing or wrong arm count" % (name, h["cells"][name])
        readouts = {r.value_slot for r in body if r.wtype == 8}
        gs_, tab_ = _gs_tab(data)
        for r in switches:
            for a in range(r.child_index, r.child_index + r.count):
                readouts |= {x.value_slot for x in records.object_records(data, gs_ + tab_[a])
                             if x.wtype == 8}
        assert {h["cells"][n] for n in ("pitch", "roll", "total", "carried")} <= readouts, \
            "a LEVEL value readout does not read its cell"
        return ("add_level_page: screen 29 body (object %d, %d records): %d vars on %#x "
                "(%d-%d, refresh %d), %d fresh cells, routine %#x on the per-tick chain, "
                "%d enrolled switches%s -- HARDWARE-UNVERIFIED"
                % (self.BODY, len(body), len(mine), self.dgn, mine[0], mine[-1], self.refresh,
                   len(h["cells"]), h["routine_addr"], len(switches), controls))
