"""The bus map, receive side: every receive-filter entry, what the panel does with it, and what the
panel calls it -- read from the file, nothing per build.

The panel's receive filter (GuiData `+0x2A70`, LCD `FUN_0021C760`, `MCF-FORMAT.md` §5.4a) is a list
of 8-byte entries. Each matches an 18-bit DGN (and optionally a masked payload byte, the
"instance filter"), extracts a field, and writes it to the entry's slot. `receive_map(data)`
follows every entry from there:

```
entry var  -- mode, field, filter                      guidata.variable_table   [proven-from-code]
  |  slot = register.status_slot_addr(var)  (index split; var>255 is +0x38)       [proven-from-code]
  v
GuiCode reads of the slot                               slottrace.slot_reads     [proven-from-code]
  |  ldr*/ldrsh [r4,#disp] in a decoder region, or ldr rX,=slot ; ldr* [rX]
  v  data and control flow to stores and calls          slottrace.Tracer
sinks: SRAM cells, GuiCodeState fields, the config-set helper, other calls
  |  cell -> +0x44BC8 cell-table index -> a record whose value_slot / mode-6/7 colour names it
  v  (a cell no record names is followed one more hop: another block's read of it)
widgets  -> caption by record order                                               [inferred-from-data]
```

## Names, and how sure each is

A name is the firmware's own word, never the RV-C table's; `spec_name` is kept apart and labelled
as the RV-C specification's. Four sources, kept apart in `Name.source`:

* `outputs-page` -- a `DC_DIMMER_STATUS_3` entry's instance is a G12 output pin, and the G12
  OUTPUTS page names it (`outputs.output_name_variants`, GUICODE Finding 81). Reused, not re-derived.
* `option` -- the trace reaches the config-set helper (13.6 `FUN_00259100`, located by
  `config._find_config_set`) with a constant config offset in `r1`; that offset's register
  (`config.config_offsets`) is the one an OPTIONS row is bound to (`options.option_bindings`).
  `proven-from-code` to the register; the row's label is the row's own (options docstring).
* `gauge` / `control` / `readout` / `parent` -- a caption read from the record order around the
  widget that paints the cell (`inferred-from-data`, like `tanknames` and `outputs`):
  - `gauge`: the labelled record immediately before a wt-5 container whose `value_slot` is the
    cell (`tanknames._captioned_containers`, the tank rule, unchanged);
  - `control`: the labels of the control whose box's mode-7 fill is the cell (`outputs._segments`);
  - `readout`: for a wt-8 number or wt-13 string whose `value_slot` is the cell, the one non-empty
    wt-11 label between the previous value-bearing record and it. Two or more there is ambiguous
    (13.6 object 484 has `'I/C Amps'` and `'Charge Rate'` before one readout) and gives no
    caption, only `candidates`; the unit is a wt-0 label immediately after;
  - `parent`: the same `readout` rule applied at the wt-5 record that contains the object.
  Every variant is returned, most-used first, joined by `" / "`, as `tanknames` does.

A caption reached only through a second hop (`Effect.hop == 1`) is used only when no direct one
exists, and says so.

## Classes (`ReceiveEntry.cls`)

| class | meaning, and the evidence it carries |
|---|---|
| `inert` | extraction mode 0, 3 or 8-15: `FUN_0021C760` writes no slot (`MCF-FORMAT.md` §5.4a) |
| `write-only` | no GuiCode read, and `slottrace.unread_guarantee` holds (Finding 77's method) |
| `untraced` | no read found, but the negative is not guaranteed (a narrow or foreign-base load could exist); or a read whose trace reaches no sink |
| `logic-only` | read; it reaches stores or calls, none of them a cell a widget names |
| `config` | it reaches the config-set helper or the config buffer (an option/floorplan register) |
| `drawn` | some sink is a cell a widget record names |

`named` is separate: an entry is named when any source above gives it a name, whatever its class
(a `DC_DIMMER_STATUS_3` entry nobody reads still has an OUTPUTS-page name for its pin).

**What a negative here means.** The tracer follows what it is shown (`slottrace` docstring); it
does not see a read through a pointer to the slot array kept elsewhere (Finding 77, residual) nor
a frame parser that reads the DGN from the raw frame (`GuiCode_entry4`, Findings 75 and 77). Those
parsers are reported as `frame_parsers`: GuiCode functions holding the DGN, shifted left 8 as
`canid & 0x03FFFF00` compares it, in their literal pool. A `write-only` entry with a frame parser
is the DM_RV pattern: the filter's slot is dead, the DGN is consumed from the frame.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

from . import guidata, memo, outputs, register, slottrace, status_map, tanknames

DGN_DC_DIMMER_STATUS_3 = 0x1FEDA
DGN_CONFIG = 0x15FCE
DGN_TANK_STATUS = 0x1FFB7

# Extraction modes that write the slot (`MCF-FORMAT.md` §5.4a, the `tbb` at 0x0021C99A).
WRITING_MODES = (1, 2, 4, 5, 6, 7)
REFRESH = {0: "2 s", 1: "10 s", 2: "30 s", 3: "never stale"}

READOUT_WTYPES = (8, 13)
CAPTION_WTYPE = 11
UNIT_WTYPE = 0
CONTAINER_WTYPES = (5, 0x2E)

# The RV-C specification's names for the DGNs in the corpus receive filters, as transcribed in
# rvc2mqtt's `rvc-spec.yml` (Apache-2.0, Wandertech's initial transcription; entries added since
# for particular coaches are not used). They are the standard's words, not Firefly's: the panel
# may call the same message something else, and `Name` never uses them.
RVC_SPEC_NAMES = {
    0x1FEAE: "SOLAR_EQUALIZATION_CONFIGURATION_STATUS", 0x1FEAF: "SOLAR_EQUALIZATION_STATUS",
    0x1FEB2: "SOLAR_CONTROLLER_CONFIGURATION_STATUS", 0x1FEB3: "SOLAR_CONTROLLER_STATUS",
    0x1FEBF: "CHARGER_CONFIGURATION_STATUS_4", 0x1FEC7: "DC_SOURCE_STATUS_6",
    0x1FEC8: "DC_SOURCE_STATUS_5", 0x1FEC9: "DC_SOURCE_STATUS_4", 0x1FECA: "DM_RV",
    0x1FECC: "CHARGER_CONFIGURATION_STATUS_3", 0x1FECE: "INVERTER_CONFIGURATION_STATUS_3",
    0x1FED8: "GENERIC_CONFIGURATION_STATUS", 0x1FEDA: "DC_DIMMER_STATUS_3",
    0x1FEE8: "INVERTER_DC_STATUS", 0x1FF85: "ATS_AC_STATUS_4",
    0x1FF89: "CHARGER_ACFAULT_CONFIGURATION_STATUS_1", 0x1FF8F: "INVERTER_AC_STATUS_4",
    0x1FF96: "CHARGER_CONFIGURATION_STATUS_2", 0x1FF98: "CHARGER_EQUALIZATION_CONFIGURATION_STATUS",
    0x1FF99: "CHARGER_EQUALIZATION_STATUS", 0x1FF9C: "THERMOSTAT_AMBIENT_STATUS",
    0x1FFAB: "ATS_AC_STATUS_3", 0x1FFAD: "ATS_AC_STATUS_1", 0x1FFB7: "TANK_STATUS",
    0x1FFB8: "DIGITAL_INPUT_STATUS", 0x1FFBF: "AC_LOAD_STATUS",
    0x1FFC6: "CHARGER_CONFIGURATION_STATUS", 0x1FFC7: "CHARGER_STATUS",
    0x1FFCA: "CHARGER_AC_STATUS_1", 0x1FFCE: "INVERTER_STATISTIC_STATUS",
    0x1FFD2: "INVERTER_CONFIGURATION_STATUS_1", 0x1FFD4: "INVERTER_STATUS",
    0x1FFD5: "INVERTER_AC_STATUS_3", 0x1FFD6: "INVERTER_AC_STATUS_2",
    0x1FFD7: "INVERTER_AC_STATUS_1", 0x1FFDC: "GENERATOR_STATUS_1", 0x1FFE2: "THERMOSTAT_STATUS_1",
    0x1FFEC: "LEVELING_JACK_STATUS", 0x1FFED: "LEVELING_CONTROL_STATUS",
    0x1FFFB: "DC_SOURCE_STATUS_3", 0x1FFFC: "DC_SOURCE_STATUS_2", 0x1FFFD: "DC_SOURCE_STATUS_1",
    0x1FFFF: "DATE_TIME_STATUS",
}
# SAE J1939 parameter groups RV-C carries unchanged (J1939-21/-81): named by J1939, not RV-C.
J1939_NAMES = {
    0x0EBFF: "TP.DT (transport protocol data transfer, global)",
    0x0EEFF: "Address Claimed (global)",
}


def spec_name(dgn: int) -> str | None:
    """The specification's name for `dgn`, prefixed by whose it is; None when neither table has
    it. Proprietary groups (J1939 `0EFxx` Proprietary A, `0FFxx` Proprietary B) are described as
    such, since their content is the manufacturer's by definition."""
    if dgn in RVC_SPEC_NAMES:
        return "RV-C " + RVC_SPEC_NAMES[dgn]
    if dgn in J1939_NAMES:
        return "J1939 " + J1939_NAMES[dgn]
    if dgn >> 8 == 0x0EF:
        return "J1939 Proprietary A (destination %#04x)" % (dgn & 0xFF)
    if dgn >> 8 == 0x0FF:
        return "J1939 Proprietary B (group extension %#04x)" % (dgn & 0xFF)
    return None


# ------------------------------------------------------------------------------- data shapes

@dataclass(frozen=True)
class Widget:
    """A record that paints a cell, with its caption if the record order gives one."""
    obj: int                   # object-table (table 1) index
    record: int                # the record's file offset
    wtype: int
    role: str                  # "value" (value_slot) or "colour:<role>" (mode-6/7 colour field)
    cell_index: int
    caption: str | None
    rule: str | None           # "control" | "row" | "gauge" | "readout" | "parent"
    candidates: tuple = ()     # labels that made a readout caption ambiguous
    unit: str | None = None
    title: str | None = None   # the object's first label: context for an uncaptioned widget,
                               # never used as a name


@dataclass(frozen=True)
class Effect:
    """One sink of one read, and what it reaches."""
    read_at: int
    sink: slottrace.Sink
    hop: int = 0                       # 1: through a cell another GuiCode block reads
    via: tuple = ()                    # hop 1: (intermediate cell, the read of it)
    cell_indices: tuple = ()
    widgets: tuple = ()
    config_offset: int | None = None   # config-set helper / config-buffer store
    register: int | None = None        # that offset's option register
    option: str | None = None          # the OPTIONS row bound to that register
    selector_check: str | None = None  # the config offset's own selector, against the entry's


@dataclass(frozen=True)
class Name:
    text: str
    source: str        # "outputs-page" | "output-caption" | "option" |
                       # "control" | "row" | "gauge" | "readout" | "parent"
    count: int         # how many places say it
    proof: str         # "proven-from-code" | "inferred-from-data"
    hop: int = 0


@dataclass(frozen=True)
class ReceiveEntry:
    var: int
    dgn: int
    raw: bytes
    mode: int
    payload_byte: int
    refresh: str
    filter_value: int | None           # None: the 0xFF sentinel, no filter
    filter_byte: int
    filter_mask: int
    slot: int
    reads: tuple
    effects: tuple
    cls: str
    names: tuple                       # Name, most-used first
    evidence: str
    frame_parsers: tuple = ()          # GuiCode functions comparing the raw frame's DGN

    @property
    def spec_name(self) -> str | None:
        return spec_name(self.dgn)

    @property
    def name(self) -> str | None:
        """The firmware's name: every variant of the best source, joined by " / "."""
        if not self.names:
            return None
        best = self.names[0].source
        return " / ".join(n.text for n in self.names if n.source == best)

    @property
    def named(self) -> bool:
        return bool(self.names)

    @property
    def widgets(self) -> tuple:
        return tuple(w for e in self.effects for w in e.widgets)

    @property
    def instance(self) -> int | None:
        return self.filter_value if self.filter_byte == 0 else None


# ------------------------------------------------------------------------------- the entries

def entry_dgn(raw: bytes) -> int:
    """The 18-bit DGN an entry matches (`+0..+3` bits 0-17; `ubfx r0,r0,#0,#0x12` @0x0021C92C)."""
    return struct.unpack_from("<I", raw, 0)[0] & 0x3FFFF


def _filter(raw: bytes) -> tuple:
    value = raw[4]
    byte = raw[5] & 0xF
    hi = raw[5] >> 4
    mask = 0xFF if hi == 0 else sum(0x03 << (2 * b) for b in range(4) if hi & (1 << b))
    return (None if value == 0xFF else value), byte, mask


# ---------------------------------------------------------------------------- widget indexing

@memo.content_cached(copy=None)
def _widget_index(data: bytes) -> tuple:
    """`({cell index: ((obj, position, role), ...)}, {obj: records}, {obj: ((parent, pos), ...)})`
    over table 1's own record lists."""
    from . import options
    gs, ge = guidata.guidata_extent(data)
    t1 = guidata.object_tables(data)[1]
    refs: dict = {}
    own: dict = {}
    parents: dict = {}
    for obj in range(len(t1)):
        recs = options._own_records(data, gs, ge, t1, obj)
        own[obj] = tuple(recs)
        for n, r in enumerate(recs):
            vs = r.value_slot
            if isinstance(vs, int):
                refs.setdefault(vs, []).append((obj, n, "value"))
            for entry in r.colors or []:
                if len(entry) > 2 and entry[1] in (6, 7) and isinstance(entry[2], int):
                    refs.setdefault(entry[2], []).append((obj, n, "colour:" + str(entry[0])))
            if r.wtype in CONTAINER_WTYPES and r.child_index is not None:
                parents.setdefault(r.child_index, []).append((obj, n))
    return ({k: tuple(v) for k, v in refs.items()}, own,
            {k: tuple(v) for k, v in parents.items()})


@memo.content_cached(copy=None)
def _cell_indices(data: bytes) -> dict:
    out: dict = {}
    for i, c in enumerate(status_map.cell_table(data)):
        if c:
            out.setdefault(c, []).append(i)
    return {c: tuple(v) for c, v in out.items()}


def _norm(text: str) -> str:
    return " ".join(text.split())


def _text(r) -> str:
    return _norm(r.label) if r.label is not None else ""


def _valued(r) -> bool:
    return isinstance(r.value_slot, int)


def _readout_caption(recs: tuple, n: int) -> tuple:
    """`(caption, candidates, unit)` for the value record at position `n` (module docstring).

    The run is the records after the previous value-bearing record (less that record's unit, the
    label right after it) up to `n`. Its labels of two or more characters are the candidates;
    exactly one is the caption."""
    start = 0
    for k in range(n - 1, -1, -1):
        if _valued(recs[k]):
            start = k + 1
            if start < n and recs[start].label is not None and recs[start].wtype == UNIT_WTYPE:
                start += 1
            break
    labels = [_text(recs[k]) for k in range(start, n)
              if recs[k].wtype in (CAPTION_WTYPE, UNIT_WTYPE) and len(_text(recs[k])) > 1]
    unit = None
    if n + 1 < len(recs) and recs[n + 1].wtype == UNIT_WTYPE and _text(recs[n + 1]):
        unit = _text(recs[n + 1])
    if len(labels) == 1:
        return labels[0], (), unit
    return None, tuple(labels), unit


def _title(recs: tuple) -> str | None:
    for r in recs:
        if len(_text(r)) > 1:
            return _text(r)
    return None


@memo.content_cached(copy=None)
def _gauge_captions(data: bytes) -> dict:
    """`{obj: ((caption offset, caption text), ...)}` from the tank rule,
    `tanknames._captioned_containers`, reused unchanged."""
    out: dict = {}
    for obj, cap_off, text, _vs in tanknames._captioned_containers(data):
        out.setdefault(obj, []).append((cap_off, text))
    return {k: tuple(v) for k, v in out.items()}


RULE_ORDER = {"control": 0, "row": 1, "gauge": 2, "readout": 3, "parent": 4}


@memo.content_cached(maxsize=2048, copy=None)
def _widgets_for(data: bytes, index: int) -> tuple:
    refs, own, parents = _widget_index(data)
    gauges = _gauge_captions(data)
    out = []
    for obj, n, role in refs.get(index, ()):
        recs = own[obj]
        r = recs[n]
        caption, rule, cands, unit = None, None, (), None
        if role.startswith("colour:"):
            for seg in outputs._segments(list(recs)):
                if seg and seg[0].offset == r.offset and outputs._fill_index(seg[0]) == index:
                    labels = [x for x in seg if x.label is not None and len(x.label.strip()) > 1
                              and x.wtype != outputs.DYNAMIC_STRING_WTYPE
                              and not outputs.ROW_RE.match(x.label.strip())]
                    if labels:
                        caption = " / ".join(_text(x) for x in labels)
                        rule = "control"
        if caption is None and n > 0 and outputs.ROW_RE.match(_text(recs[n - 1])) \
                and outputs.ROW_RE.match(_text(recs[n - 1])).group(2):
            caption, rule = _text(recs[n - 1]), "row"
        if caption is None and r.wtype == 5 and role == "value" and n > 0:
            prev = recs[n - 1]
            if len(_text(prev)) > 1 and (prev.offset, prev.label) in gauges.get(obj, ()):
                caption, rule = _text(prev), "gauge"
        if caption is None and role == "value" and r.wtype in READOUT_WTYPES:
            caption, cands, unit = _readout_caption(recs, n)
            rule = "readout" if caption else None
            if caption is None and not cands:
                for pobj, pn in parents.get(obj, ()):
                    c2, cands2, _u = _readout_caption(own[pobj], pn)
                    if c2:
                        caption, rule = c2, "parent"
                        break
                    cands = cands + cands2
        out.append(Widget(obj, r.offset, r.wtype, role, index, caption, rule, tuple(cands), unit,
                          _title(recs)))
    return tuple(out)


# ------------------------------------------------------------------------------- config joins

@memo.content_cached(copy=None)
def _config_context(data: bytes) -> tuple:
    """`(config-set helper address or None, {cfg offset: register}, {register: [row names]})`."""
    from . import config, options
    helper = config.config_set_helper(data)
    try:
        offsets = config.config_offsets(data)
    except Exception:                       # pragma: no cover
        offsets = {}
    rows: dict = {}
    for b in options.option_bindings(data):
        rows.setdefault(b.register, [])
        if b.name not in rows[b.register]:
            rows[b.register].append(b.name)
    return helper, dict(offsets), rows


def _option_for(data: bytes, offset: int) -> tuple:
    from . import config
    helper, offsets, rows = _config_context(data)
    reg = offsets.get(offset)
    name = None
    if reg is not None:
        names = rows.get(reg)
        if names:
            name = " / ".join(_norm(n) for n in names)
        elif offset == config.CFG_FLOORPLAN:
            name = "floorplan"
    return reg, name


# ------------------------------------------------------------------------------- frame parsers

@memo.content_cached(copy=None)
def _frame_parser_literals(data: bytes) -> dict:
    """`{dgn: (function entry, ...)}` for every GuiCode `ldr rX,=(dgn << 8)` a raw-frame parser
    compares against `canid & 0x03FFFF00` (Finding 77's `FUN_00240D20` dispatch). The function is
    the nearest `push {..., lr}` before the load."""
    tr = slottrace._tracer(data)
    by_lit = slottrace.literal_loads(data)
    out: dict = {}
    entries = [i.address for i in tr.ins if tanknames._is_push_lr(i)]
    import bisect
    for word, ks in by_lit.items():
        if word & 0xFC0000FF or not (0x00010000 <= word >> 8 <= 0x3FFFF):
            continue
        dgn = word >> 8
        for k in ks:
            a = tr.ins[k].address
            e = bisect.bisect_right(entries, a) - 1
            fn = entries[e] if e >= 0 else None
            out.setdefault(dgn, set()).add(fn)
    return {d: tuple(sorted(x for x in v if x is not None)) for d, v in out.items()}


# ------------------------------------------------------------------------------- the map

def _effects(data: bytes, read: slottrace.SlotRead, hop: int = 0, seen: frozenset = frozenset()):
    from . import config
    helper, _offsets, _rows = _config_context(data)
    c2i = _cell_indices(data)
    out = []
    for s in read.sinks:
        if s.kind == "call" and helper is not None and s.target == helper:
            off = next((v for r, k, v in s.args if r == "r1" and k == "abs"), None)
            if off is not None:
                reg, name = _option_for(data, off)
                out.append(Effect(read.at, s, hop, (), (), (), off, reg, name))
                continue
        if s.kind == "config":
            reg, name = _option_for(data, s.target)
            out.append(Effect(read.at, s, hop, (), (), (), s.target, reg, name))
            continue
        if s.kind == "cell":
            idx = c2i.get(s.target, ())
            widgets = tuple(w for i in idx for w in _widgets_for(data, i))
            out.append(Effect(read.at, s, hop, (), idx, widgets))
            if not widgets and hop == 0 and s.target not in seen:
                for r2 in slottrace.cell_reads(data, s.target):
                    for e2 in _effects(data, r2, 1, seen | {s.target}):
                        if e2.widgets or e2.option:
                            out.append(Effect(read.at, e2.sink, 1, (s.target, r2.at),
                                              e2.cell_indices, e2.widgets, e2.config_offset,
                                              e2.register, e2.option))
            continue
        out.append(Effect(read.at, s, hop))
    return out


def _names(entry_dgn: int, value, effects: list, out_names: dict, captions: dict) -> list:
    names: list = []
    if entry_dgn == DGN_DC_DIMMER_STATUS_3 and value is not None:
        c = Counter(n.name for n in out_names.get(value, ()) if n.name)
        for text, k in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
            names.append(Name(text, "outputs-page", k, "proven-from-code"))
        c = Counter()
        for cap in captions.get(value, ()):
            c[cap.text] += len(cap.objects)
        for text, k in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
            names.append(Name(text, "output-caption", k, "inferred-from-data"))
        if names:
            return names
    opts = Counter(e.option for e in effects if e.option)
    for text, k in sorted(opts.items(), key=lambda kv: (-kv[1], kv[0])):
        names.append(Name(text, "option", k, "proven-from-code"))
    for hop in (0, 1):
        caps: Counter = Counter()
        rule_of: dict = {}
        for e in effects:
            if e.hop != hop:
                continue
            for w in e.widgets:
                if w.caption:
                    caps[w.caption] += 1
                    if w.caption not in rule_of or RULE_ORDER[w.rule] < RULE_ORDER[rule_of[w.caption]]:
                        rule_of[w.caption] = w.rule
        if caps:
            ranked = sorted(caps.items(), key=lambda kv: (RULE_ORDER[rule_of[kv[0]]], -kv[1], kv[0]))
            for text, k in ranked:
                names.append(Name(text, rule_of[text], k, "inferred-from-data", hop))
            break
    return names


def _classify(mode: int, reads: tuple, effects: list, var: int, data: bytes) -> tuple:
    if mode not in WRITING_MODES:
        return "inert", ("extraction mode %d: FUN_0021C760 writes no slot (bhi @0x0021C998 or a "
                         "tbb arm straight to the loop increment)" % mode)
    if not reads:
        ok, why = slottrace.unread_guarantee(data, var)
        return ("write-only" if ok else "untraced"), "no GuiCode read; " + why
    sites = ", ".join("%#x" % r.at for r in reads)
    if any(e.widgets for e in effects):
        cells = sorted({e.sink.target for e in effects if e.widgets and e.hop == 0})
        return "drawn", "read at %s; cells %s are painted" % (
            sites, ", ".join("%#x" % c for c in cells) or "(via a second hop)")
    if any(e.config_offset is not None for e in effects):
        offs = sorted({e.config_offset for e in effects if e.config_offset is not None})
        return "config", "read at %s; reaches config offset(s) %s" % (
            sites, ", ".join("%#x" % o for o in offs))
    if effects:
        kinds = Counter(e.sink.kind for e in effects)
        return "logic-only", "read at %s; %s; no cell any widget names" % (
            sites, ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())))
    return "untraced", "read at %s; the trace reaches no store or call" % sites


@memo.content_cached(copy=None)
def receive_map(data: bytes, dgns: frozenset | None = None) -> tuple:
    """A `ReceiveEntry` per receive-filter entry, in var order; only those matching `dgns` (a
    frozenset of 18-bit DGNs) when given. The OUTPUTS-page names are read only when a
    `DC_DIMMER_STATUS_3` entry is included (`outputs.output_name_variants` runs GuiCode's string
    block under every input and is most of the cost)."""
    from . import config
    table = [v for v in guidata.variable_table(data)
             if dgns is None or entry_dgn(v.raw) in dgns]
    out_names: dict = {}
    captions: dict = {}
    if any(entry_dgn(v.raw) == DGN_DC_DIMMER_STATUS_3 for v in table):
        try:
            out_names = outputs.output_name_variants(data)
        except ValueError:
            out_names = {}
        captions = outputs.control_captions(data)
    parsers = _frame_parser_literals(data)
    entries = []
    for v in table:
        raw = v.raw
        dgn = entry_dgn(raw)
        mode = raw[2] >> 4
        fval, fbyte, fmask = _filter(raw)
        reads = slottrace.slot_reads(data, v.index) if mode in WRITING_MODES else ()
        effects = [e for r in reads for e in _effects(data, r)]
        selector = fval if (dgn == DGN_CONFIG and fbyte == 0) else None
        if selector is not None:
            checked = []
            for e in effects:
                if e.config_offset is None:
                    checked.append(e)
                    continue
                own = config.option_selector(data, e.config_offset)
                chk = ("the panel writes selector %#04x for config offset %#x (config."
                       "option_selector): %s" % (own, e.config_offset,
                                                 "agrees" if own == selector else "DIFFERS")
                       if own is not None else None)
                checked.append(Effect(e.read_at, e.sink, e.hop, e.via, e.cell_indices,
                                      e.widgets, e.config_offset, e.register, e.option, chk))
            effects = checked
        cls, why = _classify(mode, reads, effects, v.index, data)
        names = _names(dgn, fval if fbyte == 0 else None, effects, out_names, captions)
        entries.append(ReceiveEntry(
            var=v.index, dgn=dgn, raw=raw, mode=mode, payload_byte=raw[3] & 7,
            refresh=REFRESH[(raw[2] >> 2) & 3], filter_value=fval, filter_byte=fbyte,
            filter_mask=fmask, slot=register.status_slot_addr(v.index), reads=tuple(reads),
            effects=tuple(effects), cls=cls, names=tuple(names), evidence=why,
            frame_parsers=parsers.get(dgn, ())))
    return tuple(entries)


CLASSES = ("drawn", "config", "logic-only", "write-only", "untraced", "inert")


def coverage(entries) -> dict:
    """`{dgn: Counter}` with `entries`, `named`, and one count per class."""
    out: dict = {}
    for e in entries:
        c = out.setdefault(e.dgn, Counter())
        c["entries"] += 1
        c[e.cls] += 1
        if e.named:
            c["named"] += 1
        elif e.cls == "drawn":
            c["drawn-unnamed"] += 1
    return out


# ------------------------------------------------------------------------------- reporting

def _sink_text(e: Effect) -> str:
    s = e.sink
    if e.config_offset is not None:
        t = "config offset %#x" % e.config_offset
        if e.register is not None:
            t += " -> register %#x" % e.register
        if e.option:
            t += " (%s)" % e.option
        return t
    if s.kind == "cell":
        t = "cell %#x" % s.target
        if e.cell_indices:
            t += " [index %s]" % ",".join(map(str, e.cell_indices))
        return t
    if s.kind in ("state", "config"):
        return "%s+%#x" % ("GuiCodeState" if s.kind == "state" else "config buffer", s.target)
    if s.kind == "call":
        tgt = ("%#x" % s.target) if s.target is not None else (
            "[%#x]" % s.via if s.via is not None else "indirect")
        args = " ".join("%s=%s" % (r, ("%#x" % v) if k == "abs" else k) for r, k, v in s.args)
        return "call %s %s" % (tgt, args)
    return "store (address not resolved)"


def as_json(e: ReceiveEntry) -> dict:
    return {
        "var": e.var, "dgn": "%05X" % e.dgn, "spec_name": e.spec_name,
        "entry": e.raw.hex(), "mode": e.mode, "payload_byte": e.payload_byte,
        "refresh": e.refresh,
        "filter": (None if e.filter_value is None else
                   {"value": e.filter_value, "payload_byte": e.filter_byte,
                    "mask": e.filter_mask}),
        "slot": "%#x" % e.slot, "class": e.cls, "name": e.name,
        "names": [{"text": n.text, "source": n.source, "count": n.count, "proof": n.proof,
                   "hop": n.hop} for n in e.names],
        "evidence": e.evidence,
        "reads": ["%#x" % r.at for r in e.reads],
        "effects": [{"read": "%#x" % x.read_at, "at": "%#x" % x.sink.at, "kind": x.sink.kind,
                     "flow": x.sink.flow, "what": _sink_text(x), "hop": x.hop,
                     "via": (["%#x" % x.via[0], "%#x" % x.via[1]] if x.via else None),
                     "selector_check": x.selector_check,
                     "widgets": [{"object": w.obj, "record": "%#x" % w.record, "wtype": w.wtype,
                                  "role": w.role, "cell_index": w.cell_index,
                                  "caption": w.caption, "rule": w.rule,
                                  "candidates": list(w.candidates), "unit": w.unit,
                                  "object_title": w.title}
                                 for w in x.widgets]}
                    for x in e.effects],
        "frame_parsers": ["%#x" % f for f in e.frame_parsers],
    }
