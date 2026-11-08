"""Frozen record templates for the builders that author new controls, accepted on an image only
behind a grammar proof (2026-11-08).

**Why templates, not donors.** The builders here (`rvcbutton.button_records`,
`statelights.capsule`, `switchbuild.author_switch`, `readout.author_readout_object` and the
`pagedraw` primitives) used to read a "donor" record by (object, record) index from the image and
clone it. Each rewrites every field of that record that means anything in the image (geometry,
value slot, child band, touch id, text, and in `pagedraw` every colour), so what the donor gave
was only the record's grammar: its header, presence bits and kept constants. The index itself
meant nothing on another firmware: on 14.7 object 829 record 7 is a wt-0 glyph, and on 11.23
object 829 has two records (an `IndexError`), measured 2026-11-07.

So each donor is now the library's own bytes: `TEMPLATES[name].raw` is 13.6's record at the old
pin (`source`), byte for byte, and a 13.6 build is unchanged by construction (`tests/test_donors.py`
asserts all twelve). `template(data, name)` returns those bytes once two gates pass on `data`:

1. **Grammar.** `grammar.prove(data)` succeeds: the image's record decoder, layout engine and
   shape-renderer dispatch are twins of 13.6's. The template's wtype must also be proven in the
   renderer: a drawing template's wtype in `renderer_wtypes - renderer_default_wtypes` (on 14.7
   the arms for wt 10/14/18 differ, so a template of those refuses). The one non-drawing template,
   the wt-9 number-format directive, reaches the renderer's default arm on 13.6, which draws
   nothing; it is accepted when its wtype reaches the default arm on the image too.
2. **Witness (text templates only, and only where the text drawers are not twins).** The wt-11
   caption and the wt-8 readout are drawn by `FUN_002559A0` / `FUN_002557C0`, and the directive
   is read by that wt-8 path. `grammar.prove` fingerprints both drawers
   (`GrammarProof.text_drawers_twin`). Where they are twins (13.6) the proof covers the drawer and
   no witness is needed. Where they are not (14.7, 2026-11-08), a text template needs at least
   one stock record of the same form: one whose `canonical` bytes equal the template's.
   `canonical` zeroes every field the builder rewrites, and the value of a colour field another
   locator supplies (the readout's background cell). A shape template needs no witness: its
   decode, layout and draw path is inside the proven twins.

**Parametric text (`pagedraw`).** `pagedraw.label`/`readout` set the font, the header align and
the R49 alignment and pitch per call, so no fixed form exists to witness. `check_text_record`
gates each emitted wt-11/wt-8 record. Where the text drawers are twins, the proof suffices.
Otherwise every field value the drawer reads must occur, field by field, in some record of the
same wtype on the image: the font slot, the header align, and for wt 11 the R49 h-align, v-align
and (line height, adds-font) pair. A missing value refuses NO_TEMPLATE naming the field.
A fixed text template emitted with a caller's font (the rvcbutton caption override,
`cardreadouts.VALUE_FONT`, `pagedraw.readout`) is checked the same way on such an image: the
font must occur in a stock record of that wtype.
Masked (not checked), because a proven twin consumes them and the drawers never see them raw:
- geometry: the layout engine resolves it; the drawers read only its outputs, the box at state
  `+0x2a..+0x30` (text11 `0x2559AE`-`0x2559C0` `ldrsh.w [r4, #0x2a/#0x2c/#0x2e/#0x30]`; text8
  `0x2557FA`, `0x25580A`), both inside the text-drawer fingerprints;
- header byte 1 (the base modes): the decoder reads b1 back only at `0x25309E` and
  unpacks it to state `+0x7034..+0x7037` (`0x2530A4`-`0x2530C8`), which only the layout engine
  reads (`0x257C98`, `0x257D78`, `0x257E00`, `0x258248`). The drawers' only `+0x70xx` access is
  `+0x704C` (`0x2559E8`, `0x255852`), measured by a `movw` sweep of 13.6's GuiCode, 2026-11-08;
- every colour mode and value: the decoder resolves them into render state. fillA2 at
  `0x25340A`-`0x25341A` (header byte 5 bits 1-3, a jump table, the colour to `+0x6C`); fillB2 at
  `0x2535C6`-`0x25362A` (bits 4-6, `tbb`, `+0x70`, transparency bit `0x8000`); fillB mode 3 at
  `0x253508`-`0x253514` and mode 5 at `0x253532` (`+0x68`, bit 2);
- the text and value_slot, which no twin covers: they are the op's parameters, the text checked
  glyph by glyph (`check_text`) and the value_slot naming the cell the op allocates or names.
Per-field evidence does not prove combinations: each value is drawn somewhere on the image, not
necessarily together. Pixel evidence for pagedraw text on 14.7 is the emulator's
(`tests/emu/test_ops147.py::test_add_text_label`, 2026-11-08: font 16, white, left-aligned,
every glyph drawn in its box).

**Witnesses come from the stock image** (2026-11-08). Inside a recipe, `recipe` holds
`witness_scope(ctx.stock)` around every op's apply and verify, so a record an earlier op wrote
(ungated writers such as `set_font` included) can never witness a later one. Outside a recipe, a
direct caller's `data` is taken as the stock. A built image used as a compile base is that
recipe's stock: its authored records count, which is acceptable because they passed their own
build's gates.

Witnesses are reported for every template, required or not (`TemplateProof.witnesses`). Several
witnesses are corroboration only, since equal canonical bytes draw identically.

**Fonts.** A template that carries a font (the caption's slot 14, the readout's 15), and any
`font` the caller will write, must name a font the image has (`glyphs.font_for_slot`). The text
drawn in it must be covered too (`check_text`: `metrics.text_width` finds every glyph, as
`pagedraw.label` requires): a caption's lines, and a readout's `READOUT_CHARS` (the digits,
`.` and `-` a number formats to; `check_readout_font`). On 13.6 and 14.7 every stock font
covers every byte, measured 2026-11-08, so the check refuses only an image whose font lacks one.

**The readout's background.** `readout.wt8` paints an opaque mode-7 background that erases the
previous digits; on 13.6 that is cell 8, the theme's panel colour. The cell is not grammar, so
`template` writes the panel cell `themeswap.locate_theme_init` locates (8 on 13.6, 1 on 14.7,
both from the theme-init block's stores) and refuses NO_TEMPLATE when the block is not located.

**Group prefixes** (`GROUP_PREFIXES`, `group_prefix`): the 4-byte prefix of a stock tap group
(103, `10 00 04 80`) and hold group (17, `10 00 0c 00`), which `rvcbutton.gesture_group` edits
into a new gesture. Their meaning is read by the LCD firmware's group code (`FUN_0021B420`, the
variant setter `FUN_0021B5E8`, the sender `FUN_0021B8B4`), not GuiCode, so `grammar.prove` does
not cover it; it is the only gate. That the prefixes mean the same on another image is data
evidence, not code proof: groups 17 and 103 hold the same prefix bytes on 11.23, 13.6 and 14.7
(measured 2026-11-07).

**The mode-7 index limit** (`mode7_index_limit`, `check_mode7_index`). The decoder clamps a mode-7
colour index to 0 above `cmp.w r0, #0x298` (13.6 `0x2533BA`, then `bls`; GrammarProof bound
`mode7_index`), 664 on 13.6 and 504 on 14.7. A builder writing a mode-7 index refuses one at or
above the image's limit (MODE7_INDEX).

**Refusals** (`DonorError`, with `code`): GRAMMAR (no grammar proof, or the wtype is not proven),
NO_TEMPLATE (no witness for a text template, an unknown name, a missing font, an unlocated colour
cell), NO_DONOR (a caller-named donor record that does not exist or has the wrong form),
MODE7_INDEX. `DonorError` is not a `ValueError`, so the builders' own `except ValueError`
wrappers do not recode it; `recipe` surfaces the code as the op's refusal.
"""
from __future__ import annotations

import contextlib
import contextvars
import struct
import types
from dataclasses import dataclass

from . import glyphs, grammar, guidata, memo, metrics, records, themeswap


class DonorError(Exception):
    """A template, group prefix or donor this image does not prove usable. `code` is one of
    GRAMMAR, NO_TEMPLATE, NO_DONOR, MODE7_INDEX."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Template:
    """A frozen record template.

    `source` is the (object, record) of 13.6 it was taken from (documentation and the 13.6
    equality test; never read). `rewrites` names the fields the builders rewrite, which
    `canonical` zeroes: `geom0`..`geom5`, a colour role (`fillA`, `fillA2`, `fillB`, `fillB2`:
    mode bits and inline value), `align`, `b1`, `value_slot`, `child`, `count`, `fmt`, `touch`,
    `label`. `located` is `((colour role, theme role), ...)`: colours whose mode-7 cell
    `template` takes from the theme locator; `canonical` masks their value. `text` templates
    need a witness; `draws` is False only for the non-drawing directive."""
    source: tuple
    raw: bytes
    rewrites: tuple
    text: bool
    draws: bool = True
    located: tuple = ()


def _h(s: str) -> bytes:
    return bytes.fromhex(s)


_BOX = ("geom0", "geom1", "geom2", "geom3")
TEMPLATES: dict[str, Template] = {
    # rvcbutton.button_records (add_rvc_button, add_button_status/states, add_aes_*)
    "rvc.box": Template((829, 7), _h("20500fd2000010002d011001dc0050000500"),
                        _BOX + ("align",), text=False),
    "rvc.capsule": Template((285, 0), _h("21550c07000010002d00690032000500"),
                            ("geom2", "geom3", "fillA"), text=False),
    "rvc.caption": Template((285, 1),
                            _h("0b550c2a0000000069003200010eff02021400094c6f770a566f6c747300"),
                            ("geom2", "geom3", "label"), text=True),
    "rvc.touch": Template((315, 9), _h("0c550c2a00000000690032008100"),
                          ("geom2", "geom3", "touch"), text=False),
    # switchbuild.author_switch (add_enum_label, add_button_status/states)
    "switch": Template((9, 9), _h("0650030020000000f4015e01ff0b000600ad00"),
                       ("geom0", "geom1", "b1", "value_slot", "child", "count"), text=False),
    # readout.author_readout_object (add_register_readout, add_card_readouts, pagedraw.readout)
    "readout.directive": Template((793, 4), _h("095000000000000000180300"), ("fmt",),
                                  text=True, draws=False),
    "readout.wt8": Template((793, 5), _h("085503f820000800080032000000690046000f1601"),
                            ("geom0", "geom1", "b1", "value_slot"), text=True,
                            located=(("fillB", "panel"),)),
    # pagedraw (add_text_label, add_enum_label; levelpage): rewritten nearly whole
    "pd.fill": Template((32, 2), _h("04500c070000000004001f032400"),
                        _BOX + ("fillA", "fillA2", "align", "b1"), text=False),
    "pd.rbox": Template((368, 1), _h("60550f3b000010009e9e9e030044f6ff0500fa00aa000a00"),
                        _BOX + ("geom4", "fillA", "fillA2", "fillB", "fillB2", "align", "b1"),
                        text=False),
    "pd.hline": Template((367, 6), _h("02500f000000000058029a0050005000"),
                         _BOX + ("fillA", "fillA2", "align", "b1"), text=False),
    "pd.frame": Template((947, 3), _h("43500f6a00000000990c005f00ae01160100"),
                         _BOX + ("fillA", "fillA2", "fillB", "fillB2", "align", "b1"), text=False),
    "pd.touch": Template((186, 1), _h("0c500f0000000000610218014b0034002000"),
                         _BOX + ("align", "touch"), text=False),
}


@dataclass(frozen=True)
class GroupPrefix:
    source: int        # the 13.6 group it was taken from (documentation and the 13.6 test)
    prefix: bytes      # group-table entry bytes 0-3


GROUP_PREFIXES: dict[str, GroupPrefix] = {
    "group.tap": GroupPrefix(103, _h("10000480")),     # the LEVEL stepper's -300 arrow
    "group.hold": GroupPrefix(17, _h("10000c00")),     # a 1-s tap-or-hold group (Batt Heat's)
}


@dataclass(frozen=True)
class TemplateProof:
    name: str
    wtype: int
    decoder: int                # the grammar twins' runtime addresses
    layout: int
    renderer: int
    witnesses: tuple            # ((object, record), ...) of the same canonical form
    colour: types.MappingProxyType   # {colour role: located mode-7 cell}
    lines: tuple                # human-readable proof


# Header (byte, shift) of each colour field's 3-bit mode (`records.decode_record`).
_MODE_BITS = {"fillA": (3, 0), "fillB": (3, 3), "fillA2": (5, 1), "fillB2": (5, 4)}
_INLINE_W = {3: 3, 6: 1, 7: 2}
_FIELD_W = {"value_slot": 2, "child": 2, "count": 2, "fmt": 4}
_FIELD_OFF = {"value_slot": "value_slot_off", "child": "child_index_off", "count": "count_off",
              "fmt": "fmt_off"}
_COLOURS = tuple(_MODE_BITS)


def _decode(raw: bytes, what: str) -> records.WRecord:
    try:
        r = records.decode_record(raw, 0)
    except (IndexError, ValueError) as e:
        raise DonorError("NO_TEMPLATE", f"{what} does not decode: {e}") from e
    if r.size != len(raw):
        raise DonorError("NO_TEMPLATE", f"{what} does not decode standalone "
                                        f"({r.size} != {len(raw)} bytes)")
    return r


def _canonical(raw: bytes, rewrites: tuple, located: tuple) -> bytes:
    r = _decode(raw, "the record")
    b = bytearray(raw)
    for i in range(6):
        if f"geom{i}" in rewrites and r.geom_off[i] is not None:
            b[r.geom_off[i]:r.geom_off[i] + 2] = b"\0\0"
    masked = {c for c, _theme in located}
    for (role, mode, _v), off in zip(r.colors, r.color_off):
        w = _INLINE_W.get(mode, 0)
        if role in rewrites:
            byte, shift = _MODE_BITS[role]
            b[byte] &= ~(7 << shift) & 0xFF
        if (role in rewrites or role in masked) and off is not None:
            b[off:off + w] = bytes(w)
    if "align" in rewrites:
        b[3] &= 0x3F
    if "b1" in rewrites:
        b[1] = 0
    for key, attr in _FIELD_OFF.items():
        off = getattr(r, attr)
        if key in rewrites and off is not None:
            b[off:off + _FIELD_W[key]] = bytes(_FIELD_W[key])
    if "touch" in rewrites:
        b[-2:] = b"\0\0"
    if "label" in rewrites and r.label_offset is not None:
        n = b[r.label_offset - 1]
        b = b[:r.label_offset - 1] + b"\0" + b[r.label_offset + n:]
    return bytes(b)


def canonical(record_bytes: bytes, name: str) -> bytes:
    """`record_bytes` with every field template `name`'s builders rewrite zeroed (a label emptied
    to length 0), and the value of each colour the theme locator supplies masked. Raises
    DonorError when the bytes are not one standalone record."""
    t = _get(name)
    return _canonical(bytes(record_bytes), t.rewrites, t.located)


def _get(name: str) -> Template:
    try:
        return TEMPLATES[name]
    except KeyError:
        raise DonorError("NO_TEMPLATE", f"no template named {name!r}") from None


def _prove(data: bytes, what: str) -> grammar.GrammarProof:
    try:
        return grammar.prove(data)
    except grammar.GrammarError as e:
        raise DonorError("GRAMMAR", f"{what}: the image's record grammar is not 13.6's: {e}") \
            from e


@memo.content_cached(copy=None)
def _records_by_first_byte(data: bytes) -> types.MappingProxyType:
    """`{header byte 0: (((object, record), raw), ...)}` over every object that decodes."""
    gs, _ = guidata.guidata_extent(data)
    out: dict[int, list] = {}
    for i, off in enumerate(guidata.object_tables(data)[1]):
        try:
            recs = records.object_records(data, gs + off)
        except (IndexError, ValueError):
            continue
        for j, r in enumerate(recs):
            raw = bytes(data[r.offset:r.end])
            if raw:
                out.setdefault(raw[0], []).append(((i, j), raw))
    return types.MappingProxyType({k: tuple(v) for k, v in out.items()})


def _witnesses(data: bytes, t: Template) -> tuple:
    want = _canonical(t.raw, t.rewrites, t.located)
    hits = []
    for where, raw in _records_by_first_byte(data).get(t.raw[0], ()):
        try:
            if _canonical(raw, t.rewrites, t.located) == want:
                hits.append(where)
        except DonorError:
            continue
    return tuple(hits)


def _check_font(data: bytes, slot: int, what: str) -> None:
    try:
        glyphs.font_for_slot(data, slot)
    except glyphs.GlyphError as e:
        raise DonorError("NO_TEMPLATE", f"{what}: font slot {slot} has no font in this image "
                                        f"({e})") from e


READOUT_CHARS = "0123456789.-"


def check_text(data: bytes, slot: int, text: str, what: str) -> None:
    """DonorError NO_TEMPLATE unless font `slot` exists and has a glyph for every character of
    every line of `text`."""
    _check_font(data, slot, what)
    for line in text.split("\n"):
        if metrics.text_width(data, slot, line) is None:
            raise DonorError("NO_TEMPLATE", f"{what}: font {slot} cannot draw {line!r} "
                                            f"(missing glyph)")


def check_readout_font(data: bytes, slot: int, what: str) -> None:
    """`check_text` for each of `READOUT_CHARS` in font `slot`."""
    for ch in READOUT_CHARS:
        check_text(data, slot, ch, what)


def require_grammar(data: bytes, what: str) -> grammar.GrammarProof:
    """`grammar.prove(data)`, or DonorError GRAMMAR naming `what`."""
    return _prove(data, what)


_WITNESS_IMAGE: contextvars.ContextVar = contextvars.ContextVar("donors_witness_image",
                                                                default=None)


@contextlib.contextmanager
def witness_scope(stock: bytes | None):
    """Within the block, witnesses (whole-record and per-field) come from `stock`: `recipe`
    holds one around every op's apply and verify, with the compile's stock image (module
    docstring)."""
    token = _WITNESS_IMAGE.set(None if stock is None else bytes(stock))
    try:
        yield
    finally:
        _WITNESS_IMAGE.reset(token)


def witness_image(data: bytes) -> bytes:
    """The image witnesses are read from: the recipe's stock inside `witness_scope`, else
    `data` itself (a direct caller's `data` is taken as the stock)."""
    stock = _WITNESS_IMAGE.get()
    return bytes(data) if stock is None else stock


@memo.content_cached(copy=None)
def _text_field_values(data: bytes) -> types.MappingProxyType:
    """`{(wtype, field): frozenset(values)}` over every wt-11 and wt-8 record that decodes."""
    out: dict = {}
    for first, recs in _records_by_first_byte(data).items():
        for _where, raw in recs:
            try:
                r = records.decode_record(raw, 0)
            except (IndexError, ValueError):
                continue
            if r.wtype not in (8, 11):
                continue
            for field, value in _text_fields(r).items():
                out.setdefault((r.wtype, field), set()).add(value)
    return types.MappingProxyType({k: frozenset(v) for k, v in out.items()})


def _text_fields(r: records.WRecord) -> dict:
    """The drawer-read fields of a text record that `pagedraw` sets (module docstring)."""
    out = {"font": r.font, "align": r.align}
    if r.wtype == 11:
        out.update(h_align=r.h_align, v_align=r.v_align,
                   pitch=None if r.line_height is None
                   else (r.line_height, r.line_height_adds_font))
    return {k: v for k, v in out.items() if v is not None}


def check_text_record(data: bytes, raw: bytes, what: str) -> tuple:
    """Gate a parametric wt-11/wt-8 record (`pagedraw`) on `data`: the text drawers are twins,
    or each drawer-read field value occurs in a record of the same wtype on the image (module
    docstring). Returns proof lines; refuses GRAMMAR or NO_TEMPLATE."""
    p = _prove(data, what)
    r = _decode(bytes(raw), what)
    if r.wtype not in (8, 11):
        raise DonorError("NO_TEMPLATE", f"{what}: wt {r.wtype} is not a text record")
    if p.text_drawers_twin:
        return ("the text drawers are twins of 13.6's; no witness needed",)
    seen = _text_field_values(witness_image(data))
    lines = []
    for field, value in _text_fields(r).items():
        if value not in seen.get((r.wtype, field), ()):
            raise DonorError("NO_TEMPLATE", f"{what}: the text drawers are not twins of 13.6's "
                                            f"and no wt-{r.wtype} record on this image has "
                                            f"{field} {value!r}")
        lines.append(f"wt {r.wtype} {field} {value!r}: present on this image")
    return tuple(lines)


def mode7_index_limit(data: bytes) -> int:
    """The image's mode-7 colour-index limit: the decoder's `mode7_index` bound where
    `grammar.prove` reports it differing, else 13.6's fingerprint value (664). Raises
    DonorError GRAMMAR without a grammar proof."""
    p = _prove(data, "the mode-7 index limit")
    bound = p.bounds.get("mode7_index")
    if bound is not None:
        return bound[1]
    return grammar.fingerprints()["functions"]["decoder"]["bound_values"]["mode7_index"]


def check_mode7_index(data: bytes, index: int, what: str) -> int:
    """`index`, or DonorError MODE7_INDEX when it is not below the image's `mode7_index_limit`
    (the decoder clamps a larger index to cell 0)."""
    limit = mode7_index_limit(data)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < limit:
        raise DonorError("MODE7_INDEX", f"{what}: mode-7 colour index {index!r} is not below "
                                        f"this image's limit {limit}")
    return index


def template(data: bytes, name: str, *, font: int | None = None) -> tuple[bytes, TemplateProof]:
    """`(record bytes, proof)` for template `name` on `data` (module docstring), or DonorError.
    `font`, when given, is a slot the caller will write into the record; it must exist too.
    Memoised on content."""
    t = _get(name)
    try:
        import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way
    except ImportError as e:                                    # pragma: no cover
        from . import theme
        raise theme.CapstoneUnavailable(str(e)) from e
    return _template(data, name, t, font, witness_image(data))


@memo.content_cached(copy=None)
def _template(data: bytes, name: str, t: Template, font: int | None, base: bytes
              ) -> tuple[bytes, TemplateProof]:
    p = _prove(data, f"template {name}")
    rec = _decode(t.raw, f"template {name}")
    wt = rec.wtype
    drawn = p.renderer_wtypes - p.renderer_default_wtypes
    if t.draws and wt not in drawn:
        why = [ln for ln in p.proof if f"wt {wt}" in ln or f", {wt}" in ln] or ["not proven"]
        raise DonorError("GRAMMAR", f"template {name}: the renderer's wt {wt} arm is not a twin "
                                    f"of 13.6's ({'; '.join(why)})")
    if not t.draws and wt not in p.renderer_default_wtypes:
        raise DonorError("GRAMMAR", f"template {name}: wt {wt} does not reach the renderer's "
                                    f"default (non-drawing) arm as on 13.6")
    lines = [f"grammar: decoder {p.decoder:#x}, layout {p.layout:#x}, renderer {p.renderer:#x}"
             + (f", bounds {p.bounds}" if p.bounds else ""),
             f"wt {wt} " + ("drawn by a proven renderer arm" if t.draws
                            else "reaches the default arm")]
    raw = bytearray(t.raw)
    colour: dict[str, int] = {}
    for role, theme_role in t.located:
        try:
            ti = themeswap.locate_theme_init(data)
        except themeswap.ThemeSwapError as e:
            raise DonorError("NO_TEMPLATE", f"template {name}: its {role} cell (the theme's "
                                            f"{theme_role}) is not located: {e}") from e
        idx = ti.indices.get(theme_role, ())
        if len(idx) != 1:
            raise DonorError("NO_TEMPLATE", f"template {name}: the theme's {theme_role} role "
                                            f"names {len(idx)} cells, not one")
        k = [c[0] for c in rec.colors].index(role)
        mode, off = rec.colors[k][1], rec.color_off[k]
        if mode != 7 or off is None:
            raise DonorError("NO_TEMPLATE", f"template {name}: {role} is not a mode-7 cell")
        check_mode7_index(data, idx[0], f"template {name} {role}")
        struct.pack_into("<H", raw, off, idx[0])
        colour[role] = idx[0]
        lines.append(f"{role} = cell {idx[0]}, the theme-init block's {theme_role} store "
                     f"(themeswap.locate_theme_init)")
    for slot in {s for s in (rec.font, font) if s is not None}:
        _check_font(data, slot, f"template {name}")
    if t.text and font is not None and font != rec.font and not p.text_drawers_twin:
        if font not in _text_field_values(base).get((wt, "font"), ()):
            raise DonorError("NO_TEMPLATE", f"template {name}: the text drawers are not twins "
                                            f"of 13.6's and no stock wt-{wt} record has font "
                                            f"{font!r}")
        lines.append(f"caller font {font}: present in a stock wt-{wt} record")
    wit = _witnesses(base, t)
    if t.text and not wit and not p.text_drawers_twin:
        canon = _canonical(t.raw, t.rewrites, t.located).hex()
        raise DonorError("NO_TEMPLATE", f"template {name}: no stock record has its form "
                                        f"(canonical {canon}); its text drawer is not a proven "
                                        f"twin, so a witness is required")
    need = t.text and not p.text_drawers_twin
    lines.append(f"witnesses: {len(wit)}" + ("" if need else " (not required: "
                 + ("the text drawers are twins)" if t.text else "a shape template)")))
    return bytes(raw), TemplateProof(name, wt, p.decoder, p.layout, p.renderer, wit,
                                     types.MappingProxyType(colour), tuple(lines))


def group_prefix(data: bytes, name: str) -> tuple[bytes, tuple]:
    """`(4-byte prefix, proof lines)` for group prefix `name` (module docstring). The only gate
    is `grammar.prove`; the prefix's meaning is LCD-firmware code, its cross-image equality data
    evidence (2026-11-07)."""
    try:
        g = GROUP_PREFIXES[name]
    except KeyError:
        raise DonorError("NO_TEMPLATE", f"no group prefix named {name!r}") from None
    p = _prove(data, f"group prefix {name}")
    return g.prefix, (f"grammar: decoder {p.decoder:#x}",
                      "prefix semantics are the LCD firmware's (FUN_0021B420); equal at this "
                      "group on 11.23, 13.6 and 14.7 (data evidence, 2026-11-07)")


@dataclass(frozen=True)
class ContentProof:
    """Why an object is a glyph-button donor: its value_slot (shared by box, icon and caption)
    and the two labels, for the manifest."""
    object: int
    value_slot: int
    icon_label: str | None
    caption_label: str | None


def _glyph_clause(recs) -> str | None:
    """None when `recs` has the glyph-button shape, else the first clause it fails. The shape
    is what `add_glyph_button` rewrites and relies on: a box, an icon cell, a caption cell and
    a touch region, the box filled by a mode-7 colour index (`fillA`), a font on the icon (so
    `set_icon` has a glyph cell), the w and h geometry fields on every record, and ONE
    value_slot on box, icon and caption (the live-redraw invariant: the clone redraws in place
    once `wire_button` points that slot, the fillA index and the group at the pin)."""
    if [r.wtype for r in recs] != [33, 11, 11, 12]:
        return f"records are wtypes {[r.wtype for r in recs]}, not [33, 11, 11, 12]"
    box, icon, cap, _touch = recs
    if not box.colors or box.colors[0][:2] != ("fillA", 7):
        return "the box's first colour is not fillA mode 7"
    if icon.font is None:
        return "the icon record has no font"
    if any(r.geom[2] is None or r.geom[3] is None for r in recs):
        return "a record lacks the geom[2]/geom[3] (w, h) fields"
    slots = {box.value_slot, icon.value_slot, cap.value_slot}
    if None in slots or len(slots) != 1:
        return "box, icon and caption do not share one value_slot"
    return None


def _object_records(data: bytes, index: int):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    if not 0 <= index < len(tab):
        return None
    try:
        return records.object_records(data, gs + tab[index])
    except (IndexError, ValueError):
        return None


def _proof(index: int, recs) -> ContentProof:
    box, icon, cap, _touch = recs
    return ContentProof(index, box.value_slot, icon.label, cap.label)


@memo.content_cached(copy=None)
def _glyph_hits(data: bytes) -> tuple:
    hits = []
    for i in range(len(guidata.object_tables(data)[1])):
        recs = _object_records(data, i)
        if recs is not None and _glyph_clause(recs) is None:
            hits.append(_proof(i, recs))
    return tuple(hits)


def glyph_button(data: bytes, donor: int | None = None) -> tuple[int, ContentProof]:
    """`(object index, proof)` of the glyph-button donor `add_glyph_button` clones. With no
    `donor` the object is found by the shape predicate (`_glyph_clause`): exactly one match, or
    DonorError NO_DONOR naming the count. With `donor`, that object is checked by the same
    predicate and NO_DONOR names the clause it fails (an index outside the object table, or an
    object that does not decode, included). No grammar proof is required: this reads the data
    shape only. Measured 2026-11-07: 13.6 object 247, 14.7 object 214, 11.23 object 178."""
    if donor is not None:
        recs = (_object_records(data, donor)
                if isinstance(donor, int) and not isinstance(donor, bool) else None)
        if recs is None:
            raise DonorError("NO_DONOR", f"donor {donor} is not a decodable object of this image")
        why = _glyph_clause(recs)
        if why is not None:
            raise DonorError("NO_DONOR", f"donor {donor} is not a glyph-button shape: {why}")
        return donor, _proof(donor, recs)
    hits = _glyph_hits(data)
    if len(hits) != 1:
        raise DonorError("NO_DONOR", f"{len(hits)} glyph-button donors on this image "
                         f"(want exactly one): {[h.object for h in hits]}"
                         + ("; pass `donor` explicitly with one of these indices (a wired "
                            "shell from an earlier build has the same shape)" if hits else ""))
    return hits[0].object, hits[0]
