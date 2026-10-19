"""Decoder for the MCF **G12 section** (container index 3, name starts "G12").

The section is a self-describing container (8-byte head, self-terminating 8-byte-descriptor
directory, 11 tables tiling the body to a trailing checksum). Two kinds of table live in it:

- **Base hardware-config tables** -- the per-output electrical configuration. **Table 0x82
  (tag 130) is the per-output config**, and it partitions all 44 outputs by hardware type:
    * region A (+0x2c, 8 entries)  -> outputs 1-8,  high-power relays, current rating in mA;
    * region B (+0x6c, 12 entries) -> outputs 9-20, half-bridge, pin bitmask + config word;
    * region C (+0xcc, 24 entries) -> outputs 21-44, high-current, a per-output *class byte*
      (majority value = 6 A / standard; the lone minority = 12 A) + calibration constants;
    * region D (+0x18c, 24 entries)-> outputs 21-44, a stable per-output binary code.
  8 + 12 + 24 = 44. These tables are **byte-identical across 13.6 / 11.23 / 14.7** for
  regions A/B/D, and region C isolates output 44 as the unique 12 A output in every build.

- **Coach-specific automation-rule tables** (0x20/0x30/0x61/0x62) -- 12-byte RV-C event->
  action rule records `<u16 dgn_lo><cmd><...><target_inst@+7>` plus the `ce5fc1` signal
  registry. These *do* vary per build (feature evolution: floorplans/options/AES added over
  time), and table 0x20 is empty on 14.7. Table 0x67 is an ARM **Thumb-1** code blob --
  no Thumb-2-only encoding anywhere in the recursively-walked body (corrected 2026-08-19; this
  line used to say Thumb-2). The core is an STM32L4/Cortex-M4 (PC.19); the encoding does not
  imply the core, as this line's dropped "Cortex-M0 class" did. Note the criterion is *mnemonics*,
  not width: the 101 `bl` calls are 32-bit encodings that exist in Thumb-1 too, so "100%
  16-bit" would be wrong. It is per-build generated code that grows with feature count, and
  it carries a config-defaults routine keyed on an 0xFFFF "unset" sentinel.
  See `docs/G12-CODE.md`.

**Correction of an earlier read.** A first pass wrongly concluded "no per-output config is
present," reasoning from the automation tables (which are coach-specific) and from output
instances being absent from the rule set. That was mistaken: the base config is in table 0x82,
keyed by *output position within a hardware-type group*, not by RV-C instance -- which is why a
scan for instances 21-44 in the rule records missed it. Validated against the owner's oracles
(PDF): outputs 1-8 = 15/15/20/20/10/10/10/10 A (exact mA in the bytes); output 44 = the lone
12 A high-current output; 21-43 = 6 A.

Fields this module does NOT claim from the G12 section (documented negatives, not fitted):
- latch-vs-dimmer and master membership: those are the panel-side bindings and live in
  GuiData (`guidata.dimmable_channels`, `guidata.find_control_groups`), not table 0x82;
- exact over-current-protection *times* (0.27 s / 2.1 s): not raw-encoded -- the 6 A/12 A
  class byte (region C) is the only 44-vs-rest discriminator, so the OCP class rides with the
  current class but the seconds themselves are a spec fact, not a decodable number;
- region D's binary code (1221 / 3498, a stable 12/12 split): a real per-output attribute whose
  *meaning* is unidentified, exposed as raw data. Identified 2026-08-28 as **redundant with
  region C's `type_lo`** -- reading the two regions entry-by-entry in physical array order,
  `type_lo` fixes the code every time ({0xa38a: 1221, 0x3916: 3498, 0x722b: 3498}). Region D is
  therefore a parallel array to region C, indexed the same way (see `highcurrent_config`).

Everything is computed from bytes; nothing is hardcoded per output.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

from . import container

# --- section head / directory constants (proven from the bytes, build-general) ----------
HEAD_SIZE = 8
DESCRIPTOR_SIZE = 8
# The tables tile the body up to the trailing 4 bytes: a u16 checksum + 2 pad, observed
# identically across 11.23 / 13.6 / 14.7.
CHECKSUM_SIZE = 4

RULE_TABLE_TAGS = (0x0020, 0x0030, 0x0061, 0x0062)
RULE_RECORD_SIZE = 12
REGISTRY_SIG = bytes((0xCE, 0x5F, 0xC1))
CODE_TABLE_TAG = 0x0067
CODE_HEADER_SIG = bytes((0x9B, 0xE1))

# --- table 0x82 (the per-output config) layout, table-relative -------------------------
CONFIG_TABLE_TAG = 0x0082
# region A: outputs 1-8, 8 x 8-byte  <u16 milliamps><u16 0x00fa><u16 f2><u16 key>
RA_OFF, RA_N, RA_STRIDE = 0x2c, 8, 8
# region B: outputs 9-20, 12 x 8-byte <u32 pin_bitmask><u32 config>
RB_OFF, RB_N, RB_STRIDE = 0x6c, 12, 8
# region C: outputs 21-44, 24 x 8-byte <u16 type_lo><u16 calib_hi><u8 class><00><00><u8 index>
RC_OFF, RC_N, RC_STRIDE = 0xcc, 24, 8
# region D: outputs 21-44, 24 x 4-byte <u16 0><u16 code>
RD_OFF, RD_N, RD_STRIDE = 0x18c, 24, 4

# Output-number ranges each region configures (proven: 8+12+24 = 44).
RELAY_RANGE = range(1, 9)          # region A
HALFBRIDGE_RANGE = range(9, 21)    # region B
HIGHCURRENT_RANGE = range(21, 45)  # regions C/D

_ROLE = {
    0x0010: "bank/range records (8-byte)",
    0x0020: "automation rule records (12-byte)",
    0x0030: "automation rule records (12-byte, DGN 0xFEDA)",
    0x0650: "mixed: index list + float32 constants + rule records",
    0x0061: "automation rule records (12-byte)",
    0x0062: "automation rule records + ce5fc1 signal registry (12-byte)",
    0x0065: "empty placeholder slot",
    0x1766: "config/timing constants blob",
    0x0068: "config blob",
    0x0082: "PER-OUTPUT HARDWARE CONFIG (rating/type/class)",
    0x0067: "Thumb-1 code blob (per-build compiled logic; docs/G12-CODE.md)",
}


class G12FormatError(ValueError):
    """The G12 section body did not match the structure this module proves from the bytes."""


@dataclass(frozen=True)
class G12Table:
    tag: int
    field: int
    offset: int          # section-relative
    size: int
    role: str

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True)
class RuleRecord:
    table: int
    index: int
    offset: int
    raw: bytes
    dgn_lo: int

    @property
    def is_registry(self) -> bool:
        return self.raw[:3] == REGISTRY_SIG

    @property
    def registry_instance(self) -> int | None:
        return self.raw[4] if self.is_registry else None

    @property
    def target_instance(self) -> int | None:
        return None if self.is_registry else self.raw[7]


@dataclass(frozen=True)
class G12Section:
    name: str
    file_offset: int
    length: int
    body: bytes
    build_tag: int
    minor_version: int
    tables: list[G12Table]

    def table(self, tag: int) -> G12Table | None:
        for t in self.tables:
            if t.tag == tag:
                return t
        return None


def _g12_descriptor(data: bytes) -> container.Section:
    for s in container.parse_container(data).sections:
        if s.name.startswith("G12"):
            return s
    raise G12FormatError("no section whose name starts with 'G12'")


def parse_g12(data: bytes) -> G12Section:
    """Decode the G12 section's container structure. Proven from the bytes, build-general."""
    sec = _g12_descriptor(data)
    body = data[sec.offset:sec.offset + sec.length]
    if len(body) < HEAD_SIZE + DESCRIPTOR_SIZE + CHECKSUM_SIZE:
        raise G12FormatError(f"G12 body too short: {len(body)} bytes")

    build_tag = struct.unpack_from("<H", body, 4)[0]
    minor_version = body[6]

    first_offset = struct.unpack_from("<H", body, HEAD_SIZE + 4)[0]
    if first_offset <= HEAD_SIZE or (first_offset - HEAD_SIZE) % DESCRIPTOR_SIZE:
        raise G12FormatError(
            f"first table offset {first_offset:#x} is not a whole number of "
            f"{DESCRIPTOR_SIZE}-byte descriptors past {HEAD_SIZE:#x}")

    checksum_at = len(body) - CHECKSUM_SIZE
    tables: list[G12Table] = []
    at = HEAD_SIZE
    while at < first_offset:
        tag, fld, off, size = struct.unpack_from("<HHHH", body, at)
        if size and off + size > checksum_at:
            raise G12FormatError(
                f"table {tag:#06x} at {off:#x}+{size:#x} runs past the checksum "
                f"({checksum_at:#x})")
        tables.append(G12Table(tag=tag, field=fld, offset=off, size=size,
                               role=_ROLE.get(tag, "unclassified")))
        at += DESCRIPTOR_SIZE

    spans = sorted((t.offset, t.end) for t in tables if t.size)
    cursor = first_offset
    for start, end in spans:
        if start != cursor:
            raise G12FormatError(
                f"table gap/overlap: expected next table at {cursor:#x}, found {start:#x}")
        cursor = end
    if cursor != checksum_at:
        raise G12FormatError(
            f"tables end at {cursor:#x}, not at the checksum {checksum_at:#x}")

    return G12Section(
        name=sec.name, file_offset=sec.offset, length=sec.length, body=body,
        build_tag=build_tag, minor_version=minor_version, tables=tables)


# --- automation rules (coach-specific) --------------------------------------------------
def rule_records(data: bytes) -> list[RuleRecord]:
    """Every 12-byte record from the fixed-stride rule tables (the automation program)."""
    g = parse_g12(data)
    out: list[RuleRecord] = []
    for tag in RULE_TABLE_TAGS:
        t = g.table(tag)
        if t is None or not t.size:
            continue
        for i in range(t.size // RULE_RECORD_SIZE):
            off = t.offset + i * RULE_RECORD_SIZE
            raw = g.body[off:off + RULE_RECORD_SIZE]
            out.append(RuleRecord(table=tag, index=i, offset=off, raw=raw,
                                  dgn_lo=struct.unpack_from("<H", raw, 0)[0]))
    return out


def signal_registry(data: bytes) -> dict[int, list[int]]:
    """The `ce5fc1` registry: signal/instance -> record type byte(s)."""
    reg: dict[int, list[int]] = {}
    for r in rule_records(data):
        if r.is_registry:
            reg.setdefault(r.registry_instance, []).append(r.raw[3])
    return reg


# --- table 0x82: per-output hardware config ---------------------------------------------
def _config_table(data: bytes) -> tuple[bytes, int]:
    g = parse_g12(data)
    t = g.table(CONFIG_TABLE_TAG)
    if t is None or not t.size:
        raise G12FormatError("G12 section has no per-output config table 0x82")
    return g.body, t.offset


def relay_ratings(data: bytes) -> dict[int, int]:
    """Region A -> {output(1-8): current rating in milliamps}. Proven from the bytes."""
    body, base = _config_table(data)
    out: dict[int, int] = {}
    for i in range(RA_N):
        ma = struct.unpack_from("<H", body, base + RA_OFF + i * RA_STRIDE)[0]
        out[i + 1] = ma
    return out


def halfbridge_config(data: bytes) -> dict[int, tuple[int, int]]:
    """Region B -> {output(9-20): (pin_bitmask, config_word)}. Proven structural; the
    config word is 0x10 for outputs 9-18 and 0x00 for 19-20 across every build."""
    body, base = _config_table(data)
    out: dict[int, tuple[int, int]] = {}
    for i in range(RB_N):
        o = base + RB_OFF + i * RB_STRIDE
        pin = struct.unpack_from("<I", body, o)[0]
        cfg = struct.unpack_from("<I", body, o + 4)[0]
        out[9 + i] = (pin, cfg)
    return out


def highcurrent_config(data: bytes) -> dict[int, dict]:
    """Region C (indexed by an `index` byte 0-23 -> output 21+index) joined with region D.

    Returns {output(21-44): {class_byte, is_high_current, type_lo, calib_hi, code}}.
    `is_high_current` is derived purely from the bytes: the class byte that is in the
    *minority* across the 24 outputs marks the 12 A output(s); the majority marks 6 A. This
    isolates output 44 in every build without any hardcoded byte value."""
    body, base = _config_table(data)
    recs: dict[int, dict] = {}
    class_bytes: list[int] = []
    out_of_pos: list[int] = []          # physical position i -> output number, via region C's index
    for i in range(RC_N):
        o = base + RC_OFF + i * RC_STRIDE
        type_lo = struct.unpack_from("<H", body, o)[0]
        calib_hi = struct.unpack_from("<H", body, o + 2)[0]
        cls = body[o + 4]
        idx = body[o + 7]
        out_no = 21 + idx
        out_of_pos.append(out_no)
        recs[out_no] = {"class_byte": cls, "type_lo": type_lo, "calib_hi": calib_hi}
        class_bytes.append(cls)
    majority = Counter(class_bytes).most_common(1)[0][0]
    # Region D is a parallel array to region C in the SAME physical order (proven 2026-08-28: read
    # entry-by-entry, region C's `type_lo` fixes region D's code every time). So entry i belongs to
    # the output region C's entry i does -- 21 + its index byte -- NOT positionally to 21+i. Mapping
    # it positionally misassigns the code wherever the index byte is not the identity (it is a real
    # permutation here), which is why joining the two regions by output number appears to break the
    # redundancy. `region_d_code` is unused downstream today, so this was inert, but wrong.
    for i in range(RD_N):
        code = struct.unpack_from("<H", body, base + RD_OFF + i * RD_STRIDE + 2)[0]
        recs[out_of_pos[i]]["code"] = code
    for out_no, r in recs.items():
        r["is_high_current"] = r["class_byte"] != majority
    return recs


@dataclass(frozen=True)
class OutputInfo:
    instance: int
    output_type: str                    # 'relay' | 'half-bridge' | 'high-current'
    current_amps: float | None = None   # byte-proven where present
    current_source: str = ""            # provenance label
    current_class: str | None = None    # 'standard'(6A) | 'high'(12A) for 21-44
    rating_milliamps: int | None = None # region A raw
    polarity: str | None = None         # 9-20: 'positive' if config==0x10 else None
    pin_bitmask: int | None = None      # 9-20 raw
    region_d_code: int | None = None    # 21-44 raw; redundant with type_lo (parallel to region C)
    # automation-program reference (coach-specific)
    referenced: bool = False
    in_registry: bool = False   # always False: the G12 registry names config selectors, not
    #                             pins. Kept so callers and stored reports do not break; see the
    #                             note in `output_config`.
    references: tuple = field(default=())
    # documented negatives (never decoded from the G12 section)
    latch_or_dimmer: str | None = None  # -> use guidata.dimmable_channels
    master: str | None = None           # -> use guidata.find_control_groups


def output_config(data: bytes, instances=range(1, 45)) -> dict[int, OutputInfo]:
    """Per-output config from the G12 section (table 0x82), joined with the automation-rule
    references. `latch_or_dimmer` and `master` stay None here by design -- those are GuiData
    (panel) facts. See the module docstring for exactly what is proven vs left negative."""
    ratings = relay_ratings(data)
    hb = halfbridge_config(data)
    hc = highcurrent_config(data)

    # The `ce 5f c1` registry names CONFIG SELECTORS, not output pins -- proven on the bus
    # 2026-08-24 (`GUICODE.md` Finding 52 addendum): each such record is the transmit template
    # for the G12's periodic 0x15FCE broadcast of one selector, and 44 of them were matched
    # against captured frames. Selector 0x0F is `Min Run Time` = 1800 s, 0x31 is the AES
    # Start-at = 1020 (51.0 V), 0x0D is Stop-at = 1060.
    #
    # This function used to fold those records into a pin's references and set `in_registry`
    # from `pin_number in signal_registry(...)`, which flagged pins 1, 3-6, 10, 12-15, 17, 21,
    # 22, 38, 43 and 44 purely because a *selector* shares their number. No build ever read the
    # field, so nothing shipped wrong -- but "pin 15 is in the registry" is exactly the kind of
    # coincidence that steers a later decision, so it is no longer reported.
    recs = rule_records(data)
    refs: dict[int, list[tuple[int, str]]] = {}
    for r in recs:
        if r.is_registry:
            continue
        inst = r.target_instance
        if inst is None:
            continue
        refs.setdefault(inst, []).append((r.table, "rule-target"))

    out: dict[int, OutputInfo] = {}
    for n in instances:
        refl = refs.get(n, [])
        common = dict(referenced=bool(refl), in_registry=False, references=tuple(refl))
        if n in RELAY_RANGE:
            ma = ratings[n]
            out[n] = OutputInfo(
                instance=n, output_type="relay", rating_milliamps=ma,
                current_amps=ma / 1000.0, current_source="region-A milliamps (proven)",
                **common)
        elif n in HALFBRIDGE_RANGE:
            pin, cfg = hb[n]
            out[n] = OutputInfo(
                instance=n, output_type="half-bridge", pin_bitmask=pin,
                polarity=("positive" if cfg == 0x10 else None),
                current_source="region-B has no current value; 1A is the half-bridge spec "
                                "(inferred, not decoded)",
                **common)
        elif n in HIGHCURRENT_RANGE:
            r = hc[n]
            high = r["is_high_current"]
            out[n] = OutputInfo(
                instance=n, output_type="high-current",
                current_amps=(12.0 if high else 6.0),
                current_source="region-C class byte (minority=12A), amps from oracle/PDF",
                current_class=("high" if high else "standard"),
                region_d_code=r["code"], **common)
        else:
            out[n] = OutputInfo(instance=n, output_type="unknown", **common)
    return out
