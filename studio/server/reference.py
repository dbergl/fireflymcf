"""Reference data for the GUI's pickers, all derived from the STOCK image once and
cached (stock never changes within a server process). Everything here is read-only
description; nothing writes."""
from __future__ import annotations

import functools
import io

from PIL import Image

from fireflymcf import (cli, glyphenc, glyphs, guidata, layout, livecfg, metrics,
                              recipe, statusbind, theme)
from fireflymcf import options as optmod

SPECIMEN_CHARS = "0123456789ABCDEFabcdef"


@functools.lru_cache(maxsize=4)
def _stock_facts(stock: bytes):
    caps = recipe.capabilities(stock)
    return {
        "commanded": {c for c, _cmd in caps},
        "dimmable": guidata.dimmable_channels(stock),
        "bindings": statusbind.pin_bindings(stock),
    }


def pins(stock: bytes) -> list:
    facts = _stock_facts(stock)
    out = []
    for pin, pb in sorted(facts["bindings"].items()):
        warnings = []
        if pin in facts["commanded"]:
            warnings.append("channel %d is already commanded by an existing "
                            "control group; adding a button here needs an "
                            "explicit acknowledgement" % pin)
        out.append({"pin": pin, "var": pb.var, "bridged": pb.bridged,
                    "colour_index": pb.colour_index,
                    "commanded": pin in facts["commanded"],
                    "dimmable": pin in facts["dimmable"],
                    "warnings": warnings})
    return out


def channels(stock: bytes) -> list:
    """Every RV-C channel this file names — commanded, watched, or both.

    `pins()` is the status-binding map and lists only WATCHED pins, so a channel that
    a control commands but no filter entry watches appears nowhere in it. Those
    channels are real destinations (the G12 acts on any instance); they simply cannot
    drive an on-screen indicator. The GUI needs to see them to offer them."""
    facts = _stock_facts(stock)
    watched = set(facts["bindings"])
    known = sorted(facts["commanded"] | watched)
    return [{"channel": ch,
             "commanded": ch in facts["commanded"],
             "dimmable": ch in facts["dimmable"],
             "watched": ch in watched} for ch in known]


def colors(stock: bytes) -> list:
    return [{"rgb": list(rgb), "static": info["static"], "resting": info["resting"]}
            for rgb, info in sorted(theme.fill_palette(stock).items())]


def _decodable_glyphs(stock: bytes, slot: int) -> list:
    font = glyphs.font_for_slot(stock, slot)
    out = []
    for ch in SPECIMEN_CHARS:
        if not (font.first <= ord(ch) <= font.last):
            continue
        try:
            out.append(glyphs.decode_glyph(stock, font.glyph_index(ord(ch))))
        except glyphs.GlyphError:
            continue
    return out


def fonts(stock: bytes) -> list:
    out = []
    for slot in range(0, 40):
        try:
            font = glyphs.font_for_slot(stock, slot)
            cell = metrics.font_metrics(stock, slot).height
        except glyphs.GlyphError:
            # The only raise on this path (font_for_slot, and font_metrics which just
            # calls it) -- narrowed from `except Exception` so a real decode regression
            # (IndexError/AttributeError from a corrupted offset or a signature change)
            # fails loudly instead of being reported as "slot doesn't exist".
            continue
        out.append({"slot": slot, "cell_height": cell,
                    "glyph_count": font.last - font.first + 1})
    return out


def glyph(stock: bytes, slot: int, char: str) -> dict:
    """One stock glyph as `install_glyph` parameters, ready to add to a project.

    This is the copy-a-glyph route: pick an existing icon and put it on a free character,
    with no rasteriser and no artwork. The pixels alone would not be enough -- the 10
    kerning bytes and the +0x0e flag bit are not derivable from them (`GUICODE.md` Finding
    46) -- so `export_glyph` carries them and the reinstalled record is byte-identical.

    Only glyphs of THIS build are reachable here; a glyph from another build (14.7's
    satellite, or a logo from an image you already built) travels as the JSON that
    `cli glyph-export` writes, which the browser's importer accepts directly.

    Raises KeyError unless the character has a glyph record **of its own**, which rules
    out two different near-misses. A character the font does not cover resolves to the
    font's *fallback* (`Font.glyph_index` mirrors `FUN_00251AA0`), so exporting it would
    return another character's artwork labelled with this one -- and `install_glyph` would
    then refuse it as `GLYPH_STUB_SHARED`, since repointing a fallback entry changes what
    every uncovered character draws. A covered character can still be the shared
    placeholder, which decodes to nothing; slot 27 `'E'` is exactly that, which is why the
    Starlink logo could claim it.
    """
    if len(char) != 1:
        raise KeyError(char)
    try:
        font = glyphs.font_for_slot(stock, slot)
        if not font.covers(ord(char)):
            raise KeyError(char)
        glyphs.decode_glyph(stock, font.glyph_index(ord(char)))   # placeholder -> GlyphError
        return glyphenc.export_glyph(stock, slot, char)
    except glyphs.GlyphError:
        raise KeyError((slot, char))


def font_specimen(stock: bytes, slot: int) -> bytes:
    try:
        gs = _decodable_glyphs(stock, slot)
    except glyphs.GlyphError:
        # font_for_slot inside _decodable_glyphs is the only raise here too; see fonts().
        gs = []
    if not gs:
        raise KeyError(slot)
    height = max(g.height for g in gs)
    width = sum(g.width + 2 for g in gs)
    im = Image.new("L", (width, height), 0)
    x = 0
    for g in gs:
        for yy, row in enumerate(g.rows):
            for xx, v in enumerate(row):
                # 4bpp coverage 0..15 -> 0..255; 1bpp rows carry 0/1 (so a 1bpp
                # specimen renders near-black -- acceptable for v1, not fixed here).
                im.putpixel((x + xx, yy), min(255, v * 17))
        x += g.width + 2
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# The canonical-name rule lives in the tools layer (`livecfg.canonical_option`) so both
# ends of the join use one spelling; re-exported here because this module's callers
# already import it from here. Two spellings would drift silently.
canonical_option = livecfg.canonical_option


# Panel-local RUNTIME state that Studio applies so the canvas draws what the panel draws.
#
# Slot 512 gates the "Batteries" group: object 1182 carries a type-6 switch reading it,
# whose candidates are object 1234 (match 0, a single empty box) and object 1235 (match 1,
# the ' Batteries:' label plus the 1/2 selector). Unset, the switch correctly draws the
# blank one -- which is why the group appeared to be "not rendering".
#
# It is NOT a floorplan default and NOT a config field. GuiCode writes it 1 from tap
# handlers (0x00242F86, 0x002466AA) and 0 from 0x00243ADC; it is absent from the
# config-payload block (+0x04..+0x1A); and toggling the widget on the coach produced no
# 1FED9 traffic for it at all (`GUICODE.md` Finding 45). The panel
# sets it on entering the page, so Studio sets it too, for every floorplan.
#
# An earlier version of this table gated the slot on an owner-supplied WA/WD/WT list.
# That was inventing a floorplan rule to explain a runtime behaviour, and it is dropped.
# A preview can still force it off explicitly via `extra_slots`.
RUNTIME_GATE_SLOTS = {512: 1}


# Slots GuiCode COMPUTES from other slots, rather than receiving -- the HOME battery
# block (slot 6) and the left nav rail (slot 453). The arithmetic, the traced branches
# and the reason the two treat an absent source differently all live in ONE place,
# `options.derived_slots`, because `cli render` applies it too and two renderers that
# disagreed about what the panel computes would be worse than neither doing it.
#
# It is modelled as a derivation rather than as more pickable controls ON PURPOSE:
# offering one would let someone preview a battery block, or a rail, that cannot occur
# with the settings they chose -- a picture the panel would never draw. Picking AES in
# the coach config is what changes them, exactly as on the coach.
BATTERY_BLOCK_SLOT = optmod.BATTERY_BLOCK_SLOT
NAV_RAIL_SLOT = optmod.NAV_RAIL_SLOT
GO_POWER_SLOT = optmod.GO_POWER_SLOT
derived_slots = optmod.derived_slots


@functools.lru_cache(maxsize=4)
def _object_pages(stock: bytes) -> dict:
    """`{obj_index: page name}` for every object any page Studio can draw actually places.

    Walked with the runtime gates ON, so a gated control counts as placed rather than
    being missed for the reason it exists. First page wins; an object placed on two
    pages is named by one of them, which is all a "where do I look for this" hint needs.
    """
    roots = {}
    for name in cli._PAGE_NAMES:
        try:
            roots[name] = cli._resolve_page_root(stock, name)
        except ValueError:
            continue
    roots.update(options_page_names(stock))
    screen = layout.Screen(stock)
    out = {}
    for name, root in roots.items():
        try:
            placed = screen.walk(root, slot_values=dict(RUNTIME_GATE_SLOTS))
        except Exception:
            continue
        for p in placed:
            out.setdefault(p.obj_index, name)
    return out


def slot_groups(stock: bytes) -> list:
    """Every pickable control behind an owner-supplied gate slot, read from the file.

    A gate can front more than one group: slot 512 gates both object 1121 ('Battery',
    inner slot 509) and object 1235 (' Batteries:', inner slot 599). Both are returned,
    each tagged with its content object, rather than one being guessed at -- object 1121
    is currently reachable from no page root this project can render, so picking between
    them by position would have silently chosen the invisible one.

    Label and choice labels are the content object's own `wtype 11` records. The choice
    VALUES are positional (0, 1, ...) and that part is **empirical, not byte-derived**:
    both inner switches match on 1, so the match table cannot tell the dots apart.
    Rendering slot 599 at 0/1/2 settles it -- 0 lights '1', 1 lights '2', anything
    higher draws no dot at all."""
    from fireflymcf import options as optmod

    gs, ge = guidata.guidata_extent(stock)
    table = guidata.object_tables(stock)[1]
    gates = set(RUNTIME_GATE_SLOTS)

    def own(i):
        try:
            return optmod._own_records(stock, gs, ge, table, i)
        except Exception:
            return []

    out = []
    for i in range(len(table)):
        for r in own(i):
            if r.wtype != 6 or r.value_slot not in gates or r.child_index is None:
                continue
            for c in range(r.child_index, r.child_index + (r.count or 1)):
                recs = own(c)
                labels = [x.label for x in recs if x.label]
                inner = [x.value_slot for x in recs
                         if x.wtype == 6 and x.value_slot is not None]
                # the blank candidate (one box, no labels) is the gate's "off" arm
                if len(labels) < 2 or not inner:
                    continue
                # WHERE the control is, or null when no page Studio can draw places it.
                # 13.6 has one of each: 'Batteries' (object 1235) is on
                # options-electrical and changing it changes that page, while 'Battery'
                # (object 1121) is placed by NO root in the whole object table -- an
                # unattached template, like 247/278-281/1038. Offering both without
                # saying which is which is what left an owner hunting for the purpose of
                # a control that has none here.
                #
                # NOT the same claim as "the panel never draws it": object 247 is also
                # attached by none of the graph mechanisms (Finding 15) and is on screen
                # via the option -> placement binding this project has not located. So
                # this says only what it can: no page THIS renderer draws places it.
                out.append({"gate_slot": r.value_slot, "slot": inner[0], "object": c,
                            "label": labels[0].strip().rstrip(":").strip(),
                            "page": _object_pages(stock).get(c),
                            "choices": [{"value": n, "label": l.strip()}
                                        for n, l in enumerate(labels[1:])]})
    out.sort(key=lambda g: g["object"])
    return out


def config(stock: bytes) -> dict:
    """Floorplans, options and per-floorplan option defaults for the coach-config picker.

    `value` is the number written into `slot` when the option is selected. It is NOT
    always 1: AGS / AES / AES Single Output are a radio on a single slot distinguished
    by value (`options._option_value`), and treating them as three booleans is what
    silently rendered "AES Single Output" as AGS. The UI groups by `slot`: more than one
    option sharing a slot is a radio, a lone option is a checkbox."""
    from fireflymcf import options as optmod

    # A build with no decodable FLOORPLAN SELECTION table (11.23) has no floorplan picker: degrade
    # to an empty list rather than 500 the whole config endpoint. Options below still work, so the
    # UI keeps its option checkboxes/radios and simply omits the per-floorplan defaults.
    try:
        floorplans = [{"value": v, "code": c}
                      for v, c in sorted(optmod.floorplan_options(stock).items())]
    except optmod.FloorplanSwitchNotFound:
        floorplans = []

    opts = []
    for b in optmod.option_bindings(stock):
        opts.append({"name": canonical_option(b.name), "label": b.name,
                     "slot": b.value_slot, "value": optmod._option_value(b.name),
                     "page": b.options_obj})

    # Derived in ONE pass, deliberately. `guidata.find_option_defaults` re-runs both
    # `floorplandefaults.floorplan_defaults` and `find_option_bindings` on every call, so
    # asking it for all 14 floorplans took over two minutes and left the picker missing
    # from a cold page load. Both inputs are hoisted here and the same membership rule
    # applied: an option appears iff the floorplan's burst writes its selector.
    from fireflymcf import floorplandefaults
    raw_by_floorplan = floorplandefaults.floorplan_defaults(stock) if floorplans else {}
    selector_by_name = {canonical_option(b.name): b.selector
                        for b in guidata.find_option_bindings(stock)}
    shared = {o["slot"] for o in opts
              if sum(1 for x in opts if x["slot"] == o["slot"]) > 1}

    defaults = {}
    for f in floorplans:
        writes = raw_by_floorplan.get(f["value"])
        if writes is None:
            # No tap handler emits this floorplan's defaults. Not an error and not rare:
            # `GUICODE.md` Finding 42 found ELEVEN handlers, so on 13.6 values 11-14
            # (4NJ/3NG/3NK/3NJ) have none. Omit them -- the UI then leaves the options
            # untouched when one is picked, rather than inventing a default set.
            continue
        resolved = {}
        for o in opts:
            sel = selector_by_name.get(o["name"])
            if sel is None or sel not in writes:
                continue
            # A radio's default is the VALUE its slot is given, not a bool per name: all
            # three generator names share selector 0xEF, so a boolean reading reports
            # every one of them True. Exactly one mode is on -- the one whose written
            # value the selector carries -- and a selector value of 0 (SY) means none.
            resolved[o["name"]] = (writes[sel] == o["value"] if o["slot"] in shared
                                   else bool(writes[sel]))
        defaults[str(f["value"])] = resolved

    return {"floorplans": floorplans, "options": opts, "defaults": defaults,
            "slot_groups": slot_groups(stock), "derives": derives(stock, opts)}


def derives(stock: bytes, opts: list) -> list:
    """Which picked setting the panel COMPUTES something else from.

    Only the linkage and a plain-language name for what changes -- never the arithmetic,
    which stays in `derived_slots` and is applied server-side (`engine.preview_slots`).
    A client that re-derived the value would be a second copy of a rule read out of
    GuiCode, free to drift from it; a client that only says "this setting also drives
    the battery block" cannot.

    Returned per SOURCE slot so the picker can put the note beside the control that
    causes it, without knowing which control that is.
    """
    def slot_of(name):
        return next((o["slot"] for o in opts if o["name"] == name
                     and o["slot"] is not None), None)

    ags = slot_of("AGS")
    pi = slot_of("Progressive Inverter")

    out = []
    if ags is not None:
        out.append({"source_slot": ags,
                    "what": "the HOME battery block",
                    "detail": "The panel picks the battery display from this setting, "
                              "so it is not a separate choice."})
    # Both settings feed the nav rail, so the note goes beside each of them. The rail
    # has a third source, the Go Power! controller count, which is not a pickable
    # control here -- naming it in the detail is how someone reconciles a rail that
    # keeps its Electrical icon with a generator radio set to none.
    for slot in (s for s in (ags, pi) if s is not None):
        out.append({"source_slot": slot,
                    "what": "the left icon column",
                    "detail": "The Electrical icon shows only when the coach has "
                              "something electrical fitted -- a generator mode, a "
                              "Progressive Inverter, or a Go Power! controller. With "
                              "none of them the panel drops it and respaces the rail."})
    return out


def options_page_names(stock: bytes) -> dict:
    """`{name: object index}` for every OPTIONS page beyond the first.

    All of them carry the header label "OPTIONS", so `cli._resolve_page_root` -- which
    matches on the header -- can only ever return the first (46 on 13.6). The others had
    no name at all, which is why Electrical Options was unreachable from the UI.

    The electrical page is identified **structurally**, not by position: it is the page
    carrying the generator radio, i.e. a value slot that more than one option name binds
    (AGS / AES / AES Single Output all bind slot 37 on 13.6). Any other extra page gets a
    positional name, because nothing in the file distinguishes it. If no page carries a
    radio, no `options-electrical` is emitted rather than one being guessed."""
    from fireflymcf import options as optmod

    found = optmod._find_options_pages(stock)
    if not found:
        return {}
    bindings = optmod.option_bindings(stock)
    names_per_slot: dict[int, set] = {}
    for b in bindings:
        names_per_slot.setdefault(b.value_slot, set()).add(b.name)
    radio_slots = {s for s, n in names_per_slot.items() if len(n) > 1}
    radio_pages = {b.options_obj for b in bindings if b.value_slot in radio_slots}

    out: dict = {}
    n = 1
    for obj in found[1:]:
        if obj in radio_pages and "options-electrical" not in out:
            out["options-electrical"] = obj
        else:
            n += 1
            out["options-%d" % n] = obj
    return out


def pages(stock: bytes) -> list:
    out = []
    for name in cli._PAGE_NAMES:
        try:
            out.append({"name": name, "root": cli._resolve_page_root(stock, name)})
        except ValueError:
            # cli._resolve_page_root's only raise, for an unresolvable page on this
            # build: omit it. Narrowed from `except Exception` for the same reason as
            # fonts()/font_specimen() above.
            continue
    for name, obj in sorted(options_page_names(stock).items()):
        out.append({"name": name, "root": obj})
    return out
