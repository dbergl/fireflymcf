"""The G12's transmit-rule tables: config-selector commands, and the input-pin indicators.

**What this is.** The panel never speaks to the inverter/charger. It writes a *G12 config
selector* over `0x1FED9` (`GUICODE.md` Finding 51); the G12 stores that value and then
**broadcasts** an RV-C command DGN carrying the value it just stored. The mapping from selector
to command lives in the G12 section as 12-byte records in table `0x62`, and this module decodes
and edits them.

**The same 12-byte record also carries the G12's INPUT pins** (`GUICODE.md` Finding 54), in
table `0x20`. That went unseen for a day because every entry point here read table `0x62`
alone -- `command_rules`, `sweep_templates` and `cli g12-rules` all did -- so three of the four
rule tables were invisible to the only tool that could have shown them. `all_rules` and
`table_census` now cover all four, and a census is printed by `cli g12-rules` precisely so a
whole table cannot go missing again.

Confirmed on the coach 2026-08-23 (`BUS-BASELINE.md` **PC.23**): four selectors were adjusted on
the wire and the G12 relayed within 2-10 ms, with payloads this decoder reproduces **byte for
byte**. The strong control is `0x99` (`Shore Brkr Size`): it relayed *nothing*, and it appears in
no rule record anywhere in the section. That is a falsifiable prediction of the decode, made
before the table was read and confirmed by it.

## The record

    offset 0-1   DGN, low 16 bits, little-endian
    offset 2     DGN bits 16-17 plus the KIND field (bits 2-7, `CommandRule.kind`). 0x01 on
                 every confirmed command rule; the other values are catalogued per table with
                 their OBSERVED wire behaviour in `KIND_OBSERVED` (2026-09-02) -- periodic
                 classes 0x81/0xC1/0xCD, the event-fired A/C and AES groups, and table 0x20's
                 repeat-while-held bit. `command_rules` still reports only 0x01.
    offset 3     table 0x62: substitution mask, one bit per payload byte.
                 table 0x20: a gesture PHASE mask (1 press, 2 tap-release, 4 held,
                 8 release-after-hold) -- the same encoding as a panel binding's byte +3.
                 The two readings are not interchangeable, so `selectors` returns nothing
                 outside 0x62 and `phase_mask` returns nothing outside 0x20.
    offset 4-11  the 8-byte payload template, in the order `[7,6,5,4,11,10,9,8]`

That last one is not a new discovery: it is **the same byte order the panel's own binding table
uses** (`newbutton.binding_payload`, two little-endian u32 words). Finding the identical layout
on the G12 side is why the decode was trusted quickly -- and it was still checked against four
captured frames rather than assumed.

A payload byte is a **literal** unless its mask bit is set, in which case the byte names a config
selector whose current value is substituted. Mask bit set *and* byte `0xFF` means "would
substitute, but no selector is assigned" -- the `1FFD0` rule has two such slots and they stay
`0xFF` on the wire, which is what reconciles its `0x66` mask with its two real selectors.

## What is editable, and why it is the cheapest build in this project

Every field above is a same-size byte edit, and the whole blast radius is **the byte plus the
G12 section's 2-byte checksum** -- verified, not assumed (`tests/test_g12rules.py` re-derives the
diff). The G12 section is **independently flashable** (owner, 2026-08-18), so a rule change ships
as a G12-only build that never touches the GuiCode/GuiData pair and therefore cannot strand a
pointer or lose a GuiData feature. That is the lowest-risk shape of build available here.

The single most useful byte is **the instance**, payload[0] (record byte 7). Every shipped
charger/inverter rule commands instance `0x01`, so a device that claims a different charger or
inverter instance is retargeted one byte per rule.

**What this module deliberately does not do.** It will not invent a rule, grow the table, or
write a DGN into a record whose flag byte it cannot explain. The G12 runs a real event->action
program and a malformed record is interpreted by firmware this project has only partly read;
refusing is cheap and a bricked G12 is not.
"""
from __future__ import annotations

import dataclasses
import re
import struct

from . import g12_config, patch

# payload[i] = record[PAYLOAD_ORDER[i]] -- two little-endian u32 words, exactly as the panel's
# binding table stores its own 8 CAN data bytes.
PAYLOAD_ORDER = (7, 6, 5, 4, 11, 10, 9, 8)
RECORD_SIZE = 12
RULE_TABLE_TAG = 98                      # table 0x62
RULE_TABLE_TAGS = g12_config.RULE_TABLE_TAGS      # 0x20, 0x30, 0x61, 0x62
COMMAND_FLAG = 0x01                      # record byte 2 on every confirmed command rule
NO_SELECTOR = 0xFF
# Tables where record byte 3 is PROVEN to be a substitution mask (validated against captured
# frames, PC.23). Elsewhere the byte means something else -- see `CommandRule.phase_mask`.
SUBSTITUTION_TABLES = (98,)
REGISTRY_SIG = bytes.fromhex("ce5fc1")   # a template for the periodic 0x15FCE sweep
CONFIG_STATUS_DGN = 0x15FCE

# --- the kind field (record byte 2, bits 2-7) ---------------------------------------------
KIND_MASK = 0xFC
REPEAT_WHILE_HELD = 0x04                 # table 0x20 only -- see CommandRule.repeats_while_held
STATUS_TABLE_TAG = 0x61

# What the coach has been OBSERVED to do with each (table, kind) -- the 13.6 G12 section, 34
# candump captures, 2026-08-12..30 (the G12 section was never reflashed in that span). Keyed on
# the pair because the same value reads differently per table. Values are
# `(behaviour, confidence)`; "unobserved" is a real answer, not a gap to fill.
#
#   0x62 kind 0x80 (`0x81`)   1FFB7 TANK_STATUS, 4 instances, every 1.000 s, all captures; the
#                             fifth record (instance 3, LPG, selector 0xC2) is gated -- it runs
#                             only while mode selector 0xEF is 0 or 1 (AGS/none) and stops on
#                             EF := 3 (AES Single); ON edges follow the EF write by <= 35 ms.
#   0x62 kind 0xC0 (`0xC1`)   the 0x15FCE sweep and 1FACF, every 5.000 s (Finding 52 addendum).
#   0x62 kind 0xCC (`0xCD`)   1FEDB ON to A/C COOL (ch 43 + ch 9), every 5.000 s WHILE the
#                             compressor program runs (pc8/pc34/pc38: 25 pairs over 120 s).
#   0x62 kind 0x40 (`0x41`)   1FEDB OFF to ch 43/9 at the END of that program, sent 3x at
#                             250 ms.
#   0x62 kind 0x20/0x24/0x28  the A/C FAN records (ch 11/12): 0x24 pair fires at program
#                             start, 0x28 pair 15 ms later, 0x20 pair (OFF with duration 0x0F)
#                             at the end -- once each, never periodic.
#   0x62 kind 0x08 (`0x09`)   the AES-START group: ch 20 OFF, [0x73] ON, 0x79 := 1, 0x7D := 2 --
#                             all four on the wire in pc39 within 2 ms (`19FEDB9C#14FF0003..`,
#                             `12FFC801..`, `19FED99C#FF96790F0100D1FF`, `..7D0F0200D1FF`).
#   0x62 kind 0x00 (`0x01`)   the config relays (PC.23) AND the AES-STOP group (#4-#7),
#                             which has never fired; so 0x01 is "event-fired", not
#                             "relay-on-config-write" specifically.
#   0x20 kind 0x04            re-fires at 1.000 s while the input is held (PC.24). The re-send is
#                             the G12 APPLICATION acting on this bit, not the table-0x67 blob,
#                             which emits no 0x1FBDA input frame (G12-CODE.md, "The 1 Hz input
#                             refresh is not in this blob").
#   0x20 kind 0x00            fires once on its phase.
#   0x61 kind 0x24/0x28/0x2C  1 Hz status broadcasts carrying a config cell (StatusRule).
#   0x61 kind 0x14/0x18/0x1C  never sourced by the G12 from these records; cells they name are
#                             ones the blob READS and never writes (StatusRule.direction).
#   0x61 kind 0x48/0x5C       unobserved (1FFFC on 11.23; 1FEDA instance 20 on 13.6/14.7).
KIND_OBSERVED = {
    (0x62, 0x80): ("1 Hz periodic; one record additionally mode-gated", "observed"),
    (0x62, 0xC0): ("5 s periodic", "observed"),
    (0x62, 0xCC): ("5 s periodic while the A/C program runs", "observed"),
    (0x62, 0x40): ("event: 3x at 250 ms at A/C program end", "observed"),
    (0x62, 0x20): ("event: A/C program end (OFF, duration 0x0F)", "observed"),
    (0x62, 0x24): ("event: A/C program start, first pair", "observed"),
    (0x62, 0x28): ("event: A/C program start, second pair (+15 ms)", "observed"),
    (0x62, 0x08): ("event: the AES-start group, fired together", "observed"),
    (0x62, 0x00): ("event: config relay (PC.23) / AES-stop group (never fired)", "observed"),
    (0x20, 0x04): ("phase-fired, re-sent at 1 s while held", "observed"),
    (0x20, 0x00): ("phase-fired once", "observed"),
    (0x30, 0x00): ("unobserved: 1FEDA instance 0x36 has never been on the bus from any node",
                   "unobserved"),
    (0x61, 0x24): ("1 Hz broadcast, u8 cell value at byte +3", "observed"),
    (0x61, 0x28): ("1 Hz broadcast, u16 cell value at byte +3", "observed"),
    (0x61, 0x2C): ("1 Hz broadcast, per-instance u16 (tank sensors 1-4)", "observed"),
    (0x61, 0x14): ("receive into cell (inferred)", "inferred"),
    (0x61, 0x18): ("receive into cell (inferred); DGN never seen", "unobserved"),
    (0x61, 0x1C): ("receive into cell (inferred)", "inferred"),
    (0x61, 0x48): ("unobserved", "unobserved"),
    (0x61, 0x5C): ("unobserved", "unobserved"),
}

# --- the input-pin indicators (Finding 54) ----------------------------------------------
# The G12 reports each input pin as GENERIC_INDICATOR_COMMAND, instance 0xA0 + pin, with the
# state in payload[1]. The panel matches those instances in its own receive filter and paints
# the G12 INPUTS block on DEVICES/FAULTS.
INDICATOR_DGN = 0x1FBDA
INPUT_INSTANCE_BASE = 0xA0
INPUT_PINS = range(1, 11)                # instances 0xA1..0xAA
INPUT_BLOCK_LABEL = "G12 INPUTS"

# Table 0x10 is the INPUT DISPATCH table: one 8-byte entry per input pin, naming the run of
# table-0x20 rules that pin fires (Finding 54a). This is what makes the rule->pin association
# a fact rather than an inference from record adjacency.
#
#     +0..1  u16  0x0010, an entry tag
#     +2     u8   0x0C configured, 0x00 unconfigured
#     +3     u8   bit 0x40 = sense is 12V; clear = switched to ground
#     +4..5  u16  index of the pin's FIRST rule in table 0x20
#     +6     u8   how many rules the pin fires
#     +7     u8   0xFF
INPUT_TABLE_TAG = 0x10
INPUT_ENTRY_SIZE = 8
INPUT_CONFIGURED = 0x0C
SENSE_12V = 0x40
# A row caption is '<pin> - <name>'; the pin number in the caption is the pin, so the block
# needs no register arithmetic to be read.
_ROW_RE = re.compile(r"^\s*(\d+)\s*-")

# The block PC.23 exercised. Kept as an explicit expectation so a build against a different
# image fails loudly instead of editing whatever happens to sit at the same index.
CONFIRMED = {
    0x1FFD0: (0x82, "Low Batt. Cutout"),
    0x1FF95: (0x92, "Max Chrge Rate"),
    0x1FF87: (0x86, "VAC Dropout"),
    0x1FFC4: (0x88, "Battery Type"),
}


class G12RuleError(ValueError):
    """A rule edit whose premise does not hold."""


@dataclasses.dataclass(frozen=True)
class CommandRule:
    """One config-selector -> RV-C command broadcast rule."""

    index: int                  # record index within its table
    offset: int                 # FILE offset of the record
    dgn: int
    mask: int
    raw: bytes
    table: int = RULE_TABLE_TAG  # which rule table the record came from

    @property
    def payload(self) -> list:
        """The 8 template bytes in wire order."""
        return [self.raw[i] for i in PAYLOAD_ORDER]

    @property
    def selectors(self) -> dict:
        """`{payload index: selector}` for every slot the G12 substitutes into.

        **Empty outside table `0x62`, deliberately.** Byte 3 is a substitution mask only where
        that was proven against the wire (the charger/inverter rules and the `0x15FCE` sweep,
        both in `0x62`). In table `0x20` the same byte is a **phase mask** — see `phase_mask` —
        so reading it as a substitution mask produced a confident `p[0]<-0xfb` for a record whose
        payload[0] is a plain literal instance. Reporting nothing beats reporting that.
        """
        if self.table not in SUBSTITUTION_TABLES:
            return {}
        pay = self.payload
        return {i: pay[i] for i in range(8)
                if (self.mask >> i) & 1 and pay[i] != NO_SELECTOR}

    @property
    def phase_mask(self):
        """Byte 3 read as the LCD's gesture phase mask, for table `0x20`; None elsewhere.

        `1` press, `2` short-tap release, `4` while held, `8` release-after-hold — the same
        encoding as a panel binding's byte `+0x03` (`CONTROL-BEHAVIOR.md`). `inferred`, and the
        evidence is that it makes input 1's three output rules read as the standard light gesture:
        `02` -> command 5 (toggle), `04` -> command 21 (ramp), `08` -> command 4 (stop), which is
        exactly the triple the panel emits for a light. The indicator pairs are then `05`
        (press | held) -> report ON and `0A` (tap-release | release-after-hold) -> report OFF.
        """
        if self.table != 0x20:
            return None
        names = [n for bit, n in ((1, "press"), (2, "tap-release"),
                                  (4, "held"), (8, "release-after-hold"))
                 if self.mask & bit]
        return tuple(names)

    @property
    def kind(self) -> int:
        """Record byte 2 with the two DGN bits stripped: bits 2-7, the **kind field**.

        `proven-from-file` as a field; its *meaning* is per table and mostly `inferred` -- see
        `KIND_OBSERVED` for what the coach has been seen to do with each value, and
        `repeats_while_held` / `StatusRule.direction` for the two readings this module commits
        to. It is deliberately NOT collapsed into one enum: the same value means different
        things in different tables (`0x24` is an A/C phase record in `0x62` and a 1 Hz status
        broadcast in `0x61`), so a decoder must key on `(table, kind)`.
        """
        return self.raw[2] & KIND_MASK

    @property
    def repeats_while_held(self):
        """Table `0x20` only: kind bit 2 (`0x04`) -- the rule re-fires while the input is held.

        `inferred`, 2026-09-02, from two independent facts. **File:** on 13.6 (32 records) and
        11.23 (25) the bit is set on exactly the records whose phase mask carries `held`
        (`0x04`) -- the ON indicators and the ramp / EXT-on / RET-on commands -- and clear on
        every OFF indicator and every release-phase command. **Wire:** `BUS-BASELINE.md` PC.24
        measured every ON indicator repeating at `1.000 s` while the input stayed active and no
        OFF indicator ever repeating, and RV-C's EXT/RET-on with `duration 2` needs exactly such
        a re-send to keep an awning moving. Nothing in the record encodes the 1 s period itself.
        None outside table `0x20`.
        """
        if self.table != 0x20:
            return None
        return bool(self.raw[2] & REPEAT_WHILE_HELD)

    @property
    def value_fields(self) -> list:
        """`[(payload index, width, selector), ...]` -- substitution slots, u16 pairs merged.

        **Adjacent masked slots naming the SAME selector are one little-endian u16**, not two
        independent bytes. Proven against the wire on 2026-08-24: the `0x15FCE` sweep template
        for selector `0x92` masks payload[2] and payload[3] and the coach transmits `C8 00`,
        which is 200 little-endian -- the selector's live value -- not `C8 C8`. Every swept
        registry template behaves the same way, and the `1FFD0` charger rule's payload[5]/[6]
        pair is the same construction (`BUS-BASELINE.md` PC.23 caught it there first).
        """
        sel = self.selectors
        out, skip = [], set()
        for i in sorted(sel):
            if i in skip:
                continue
            if i + 1 in sel and sel[i + 1] == sel[i]:
                out.append((i, 2, sel[i]))
                skip.add(i + 1)
            else:
                out.append((i, 1, sel[i]))
        return out

    @property
    def instance(self) -> int:
        """Payload byte 0 -- the RV-C instance this rule commands."""
        return self.payload[0]

    def payload_offset(self, payload_index: int) -> int:
        """FILE offset of the template byte that becomes `payload[payload_index]`."""
        if not 0 <= payload_index <= 7:
            raise G12RuleError(f"payload index {payload_index} outside an 8-byte payload")
        return self.offset + PAYLOAD_ORDER[payload_index]

    def render(self) -> str:
        pay, sel = self.payload, self.selectors
        return "".join("__" if i in sel else "%02X" % pay[i] for i in range(8))

    def __str__(self) -> str:
        if self.table in SUBSTITUTION_TABLES:
            sel = ", ".join("p[%d]<-%#04x" % (k, v) for k, v in self.selectors.items())
            tail = "mask %02X  %s" % (self.mask, sel or "(no selector)")
        elif self.phase_mask is not None:
            tail = "phase %02X  %s" % (self.mask, "|".join(self.phase_mask) or "(none)")
        else:
            tail = "b3 %02X  (undecoded in table %#04x)" % (self.mask, self.table)
        return ("t%02X #%-3d %#08x  DGN %05X  inst %#04x  %s  %s"
                % (self.table, self.index, self.offset, self.dgn, self.instance,
                   self.render(), tail))


def _rule_table(data: bytes, tag: int = RULE_TABLE_TAG) -> tuple:
    """`(file offset of the rule table, record count)`, located structurally."""
    section = g12_config._g12_descriptor(data)
    table = next((t for t in g12_config.parse_g12(data).tables if t.tag == tag), None)
    if table is None:
        raise G12RuleError("this image has no G12 table %#04x" % tag)
    return section.offset + table.offset, table.size // RECORD_SIZE


def all_rules(data: bytes, *, tags: tuple = RULE_TABLE_TAGS) -> list:
    """Every 12-byte record from every rule table, decoded, with no filtering at all.

    The filtered views (`command_rules`, `sweep_templates`) are the ones to use for the
    charger/inverter work; this is the one that guarantees nothing is invisible. Records whose
    kind this project has not decoded are still returned -- with their raw bytes -- because a
    record left out of a listing is how table `0x20` stayed unread.
    """
    out = []
    for tag in tags:
        try:
            base, count = _rule_table(data, tag)
        except G12RuleError:
            continue
        for index in range(count):
            offset = base + index * RECORD_SIZE
            raw = bytes(data[offset:offset + RECORD_SIZE])
            if len(raw) < RECORD_SIZE:
                continue
            dgn = raw[0] | raw[1] << 8 | (raw[2] & 3) << 16
            out.append(CommandRule(index=index, offset=offset, dgn=dgn,
                                   mask=raw[3], raw=raw, table=tag))
    return out


def table_census(data: bytes) -> list:
    """`[(tag, record count, {dgn: count}), ...]` -- what each rule table actually holds."""
    out = []
    for tag in RULE_TABLE_TAGS:
        rules = [r for r in all_rules(data, tags=(tag,))]
        if not rules:
            continue
        by_dgn = {}
        for r in rules:
            by_dgn[r.dgn] = by_dgn.get(r.dgn, 0) + 1
        out.append((tag, len(rules), by_dgn))
    return out


def kind_census(data: bytes) -> dict:
    """`{(table, kind): count}` over every rule table -- the raw distribution, nothing inferred."""
    out = {}
    for rule in all_rules(data):
        key = (rule.table, rule.kind)
        out[key] = out.get(key, 0) + 1
    return out


# --- table 0x61: the G12's status broadcasts and bus subscriptions -----------------------
@dataclasses.dataclass(frozen=True)
class StatusRule:
    """One table-`0x61` record, read as a **cell <-> bus** rule rather than a literal template.

    The 12-byte stride and the DGN/kind bytes are the §9a record; the rest is NOT the
    `[7,6,5,4,11,10,9,8]` template of table `0x62`, and reading it as one produces frames the
    G12 has never sent (`FF 28 FB FB 01 01 00 00` for a `1FBDA`). Layout, 2026-09-02:

        +3      payload byte index the cell value occupies (`inferred`, see below)
        +4..+5  a pair, usually equal -- an instance filter/range (`01 05` on the tank-sensor
                record whose wire instances are 1-4; `01 02` on the charger-config record
                whose relay rules address instances 1 and 2). `inferred`
        +6      a **config cell** (selector) number -- `proven-from-data` for one record
        +7      0xFF, or a literal (0xF0 on the tank record, 0xF1 on DM_RV). `unknown`
        +8..+11 two u16 LE; on the transmit records these bracket the value (200..320 =
                10.0-16.0 V on the DC-voltage record; 7100..11368 = -51..82 C on the
                thermostat record). `inferred`

    **The one proven join.** Record #16 on 13.6 (`b7aa2501 ffff c2 ff ..`) names cell `0xC2` at
    byte 1 and broadcasts `19AAB79C#035064FFFFFFFFFF` at 1 Hz; table `0x62` #93 -- an ordinary
    masked template whose mechanism PC.23 proved -- puts *selector `0xC2`* at payload[1] of
    `19FFB79C#03__64FFFFFFFFFF`, and the coach transmits `035064`/`035164` on both DGNs with the
    same value (657 of 681 paired frames agree; the 24 are 1 Hz phase skew across a flip). Two
    tables, two mechanisms, one cell, one number.

    **Direction.** Kinds `0x24`/`0x28`/`0x2C` are the G12's 1 Hz status broadcasts, six of
    seven observed on the coach (`1FFFF` clock, `1AAFD` DC voltage x2 instances, `1FF9C`
    ambient temperature, `0BFC1` tank sensors x4, `1AADC` generator, `1AAB7` LPG level; `1FFFD`
    never -- **corrected 2026-10-21: #15 `1FFFD` is a receive** (the APS-500's instance 1 lands in
    cell `0x55`; G12-CODE.md "Executing the blob"), so kind `0x28` does not fix the direction). Kinds `0x14`/`0x18`/`0x1C` are read as **subscriptions**: none of their
    DGNs is sourced by the G12 *from that record* (the `1FBDA` instances it does send come from
    table `0x20`; `1AADC` is sent by #12/#13; DM_RV is every RV-C node's own), instance `0xFE`,
    `1FF96`, `1FFFE` and `1FFAB` have never been seen from anyone, and the cells they name are
    exactly ones the `0x67` blob reads without ever writing -- `0x28` is struct `+0x50`, the
    ignition/awning-interlock sense whose writer `G12-CODE.md` could not find, and `0x53` is
    `+0xA6`, the "is set" gate on the AES watchdog's `+0x0D0C` block. 14.7 (a Terrain with no
    G12 inputs) swaps record #0 from `1FBDA` instance `0xFB` to `1FFB8` DIGITAL_INPUT_STATUS
    instance `0x0A` and keeps cell `0x28`. That is `inferred`; the receive path itself is in
    the G12 application, not the MCF.
    """

    index: int
    offset: int
    dgn: int
    kind: int
    raw: bytes

    @property
    def payload_index(self) -> int:
        return self.raw[3]

    @property
    def instance_range(self) -> tuple:
        return (self.raw[4], self.raw[5])

    @property
    def cell(self) -> int:
        """The config cell / selector the rule reads from or writes to (record byte 6)."""
        return self.raw[6]

    @property
    def params(self) -> tuple:
        return struct.unpack_from("<HH", self.raw, 8)

    @property
    def direction(self):
        """`"transmit"` (kind `0x2x`), `"receive"` (kind `0x1x`), or None -- `inferred`."""
        if self.kind & 0x30 == 0x20 and not self.kind & 0xC0:
            return "transmit"
        if self.kind & 0x30 == 0x10 and not self.kind & 0xC0:
            return "receive"
        return None

    def __str__(self) -> str:
        return ("t61 #%-3d %#08x  DGN %05X  kind %02X  %-8s  byte %d  inst %02X..%02X  "
                "cell %#04x  b7 %02X  params %5d %5d"
                % (self.index, self.offset, self.dgn, self.kind, self.direction or "?",
                   self.payload_index, self.raw[4], self.raw[5], self.cell, self.raw[7],
                   *self.params))


def status_rules(data: bytes) -> list:
    """Every table-`0x61` record as a `StatusRule`. Empty if the image has no such table."""
    return [StatusRule(index=r.index, offset=r.offset, dgn=r.dgn, kind=r.kind, raw=r.raw)
            for r in all_rules(data, tags=(STATUS_TABLE_TAG,))]


def command_rules(data: bytes, *, dgn_low: int = 0x1FE00, dgn_high: int = 0x1FFFF) -> list:
    """Every selector-driven command rule in table 0x62.

    Restricted to records whose flag byte is `COMMAND_FLAG` and whose DGN is in the RV-C
    status/command range, because the same 12-byte stride also carries rule kinds this project
    has not decoded. A wider net produces confident nonsense -- reading the Thumb code blob at
    table 0x67 on this stride yields dozens of "rules" that are just instructions.
    """
    base, count = _rule_table(data)
    out = []
    for index in range(count):
        offset = base + index * RECORD_SIZE
        raw = bytes(data[offset:offset + RECORD_SIZE])
        if len(raw) < RECORD_SIZE or raw[2] != COMMAND_FLAG:
            continue
        dgn = raw[0] | raw[1] << 8 | (raw[2] & 3) << 16
        if not dgn_low <= dgn <= dgn_high:
            continue
        rule = CommandRule(index=index, offset=offset, dgn=dgn, mask=raw[3], raw=raw)
        if rule.selectors:
            out.append(rule)
    return out


def rule_for_selector(data: bytes, selector: int) -> list:
    """Every rule the given config selector drives. Empty is a real answer -- `0x99` has none."""
    return [r for r in command_rules(data) if selector in r.selectors.values()]


def sweep_templates(data: bytes) -> list:
    """The `0x15FCE` registry entries, decoded as the transmit templates they are.

    **The registry is not a second mechanism.** It looked like one -- the records carry a
    distinctive `ce 5f c1` signature and `g12_config.RuleRecord.is_registry` has always split
    them off -- but they are ordinary records in the same format, and each is the template for
    the G12's periodic `0x15FCE` broadcast of one selector. Proven 2026-08-24 by predicting the
    wire from the file: for all **44** registry selectors the coach was observed sweeping, every
    unmasked byte matches the captured frame and the masked payload[2]/[3] pair carries the
    selector's live value as a little-endian u16. Zero mismatches.

    So all 151 records in tables 0x20/0x30/0x61/0x62 are transmit templates, and "registry
    entry" is just what a template for `0x15FCE` looks like.
    """
    base, count = _rule_table(data)
    out = []
    for index in range(count):
        offset = base + index * RECORD_SIZE
        raw = bytes(data[offset:offset + RECORD_SIZE])
        if len(raw) < RECORD_SIZE or raw[:3] != REGISTRY_SIG:
            continue
        out.append(CommandRule(index=index, offset=offset, dgn=CONFIG_STATUS_DGN,
                               mask=raw[3], raw=raw))
    return out


# --- the input pins: G12 transmit rule <-> panel receive filter <-> screen ---------------
@dataclasses.dataclass(frozen=True)
class InputStatus:
    """One G12 input pin, joined across both images.

    Every field is derived independently of the others, which is what makes the join evidence
    rather than an assumption: `on_rule`/`off_rule` come from the G12 section, `var` from the
    panel's receive-filter table, and `slot`/`label` from GuiData's screen records. On 13.6 all
    three agree on the same eight pins.
    """

    pin: int
    instance: int
    on_rule: CommandRule | None = None    # G12 -> "input active"   (payload[1] == 1)
    off_rule: CommandRule | None = None   # G12 -> "input inactive" (payload[1] == 0)
    var: int | None = None                # panel receive-filter entry (== slot array index)
    slot: int | None = None               # value slot the screen's indicator reads
    label: str | None = None              # the row caption on DEVICES/FAULTS
    config: InputConfig | None = None     # the pin's table-0x10 dispatch entry
    extra_instances: tuple = ()           # other 0x1FBDA instances this pin also broadcasts

    @property
    def broadcast(self) -> bool:
        """Does the G12 actually report this pin? Pins 5 and 8 on 13.6 do not."""
        return self.on_rule is not None or self.off_rule is not None

    @property
    def watched(self) -> bool:
        """Does the panel subscribe to it? On 13.6 all ten are watched, including 5 and 8."""
        return self.var is not None

    def frames(self) -> tuple:
        """`(active frame, inactive frame)` as they appear on the wire, source `0x9C`."""
        def frame(rule):
            if rule is None:
                return None
            return "19FBDA9C#" + "".join("%02X" % b for b in rule.payload)
        return frame(self.on_rule), frame(self.off_rule)


@dataclasses.dataclass(frozen=True)
class InputConfig:
    """One entry of the table-`0x10` input dispatch table."""

    pin: int
    configured: bool
    sense: str                  # "12V" | "ground" | "unconfigured"
    first_rule: int             # index into table 0x20
    rule_count: int
    raw: bytes


def input_dispatch(data: bytes) -> list:
    """`[InputConfig, ...]`, one per input pin, from table `0x10`.

    This is the table that says **which rules a pin fires**, and it is why the rule -> pin
    association is not guesswork. Finding 54 originally grouped table `0x20` by adjacency and
    said so; this table gives the grouping explicitly, and confirms it — including the two
    cases adjacency could not have settled:

    * **pins 5 and 8 have `rule_count == 0`.** The negative is structural, not an absence.
    * **pin 10 fires FOUR rules** — its own `0xAA` indicator pair *and* the `0xFB` pair. One
      physical input, two broadcast instances, which is the answer to "what sets `0xFB`".

    Independent cross-check that was not used to derive anything: `+3` bit `0x40` is the 12V /
    switched-to-ground sense, and it agrees with the `(12V)` / `(GND)` suffix authored into all
    six labelled screen captions — two images, no shared source.
    """
    try:
        section = g12_config._g12_descriptor(data)
        table = next(t for t in g12_config.parse_g12(data).tables if t.tag == INPUT_TABLE_TAG)
    except (StopIteration, Exception):                       # noqa: BLE001
        return []
    base = section.offset + table.offset
    out = []
    for pin in range(1, table.size // INPUT_ENTRY_SIZE + 1):
        raw = bytes(data[base + (pin - 1) * INPUT_ENTRY_SIZE:][:INPUT_ENTRY_SIZE])
        if len(raw) < INPUT_ENTRY_SIZE:
            break
        configured = raw[2] == INPUT_CONFIGURED
        out.append(InputConfig(
            pin=pin, configured=configured,
            sense=("12V" if raw[3] & SENSE_12V else "ground") if configured else "unconfigured",
            first_rule=raw[4] | raw[5] << 8, rule_count=raw[6], raw=raw))
    return out


def input_group_rules(data: bytes, pin: int) -> list:
    """Every table-`0x20` rule the given input pin fires, in order."""
    entry = next((e for e in input_dispatch(data) if e.pin == pin), None)
    if entry is None or not entry.rule_count:
        return []
    table = {r.index: r for r in all_rules(data, tags=(0x20,))}
    return [table[i] for i in range(entry.first_rule, entry.first_rule + entry.rule_count)
            if i in table]


def indicator_rules(data: bytes) -> list:
    """Every GENERIC_INDICATOR_COMMAND (`0x1FBDA`) transmit template, across all rule tables."""
    return [r for r in all_rules(data) if r.dgn == INDICATOR_DGN]


def instance_source_pin(data: bytes, instance: int):
    """Which input pin broadcasts a given `0x1FBDA` instance, or None.

    `instance_source_pin(data, 0xFB)` is `10` on 13.6 — the ignition sense input, which fires
    the `0xFB` engine-running pair as well as its own `0xAA` pair (Finding 55).
    """
    for entry in input_dispatch(data):
        for rule in input_group_rules(data, entry.pin):
            if rule.dgn == INDICATOR_DGN and rule.instance == instance:
                return entry.pin
    return None


def input_rules(data: bytes) -> dict:
    """`{pin: {"on": rule, "off": rule}}` for the indicator instances that name an input pin.

    A pin's two records are told apart by **payload[1]**, the indicator value: `01` active,
    `00` inactive. They are not told apart by record byte 2 or 3, whose meaning in this table is
    undecoded -- byte 3 is `0x05` on the active record and `0x0A` on the inactive one, which
    looks like an edge or level condition but has not been proven and is not read here.
    """
    out = {}
    for rule in indicator_rules(data):
        pin = rule.instance - INPUT_INSTANCE_BASE
        if pin not in INPUT_PINS:
            continue
        state = {1: "on", 0: "off"}.get(rule.payload[1])
        if state is None:
            continue
        out.setdefault(pin, {})[state] = rule
    return out


def panel_input_vars(data: bytes) -> dict:
    """`{instance: var}` for every receive-filter entry watching `0x1FBDA`.

    The whole indicator namespace, not just the inputs -- 13.6 also watches `0x91`-`0x99`,
    `0xAB`/`0xAC`, `0xBF` and `0xFA`-`0xFD`. Callers pick the range they mean.
    """
    from . import guidata
    out = {}
    for entry in guidata.variable_table(data):
        if (struct.unpack_from("<I", entry.raw, 0)[0] & 0x3FFFF) != INDICATOR_DGN:
            continue
        out.setdefault(entry.raw[4], entry.index)
    return out


def _input_block_root(data: bytes):
    """The object index of the `G12 INPUTS` row container, or None."""
    from . import guidata, records
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    for index in range(len(table)):
        try:
            recs = records.object_records(data, gs + table[index])
        except Exception:                                   # noqa: BLE001 - not every index decodes
            continue
        header = False
        for rec in recs:
            label = getattr(rec, "label", None)
            if isinstance(label, str) and label.strip() == INPUT_BLOCK_LABEL:
                header = True
            elif header and rec.wtype == 5 and getattr(rec, "child_index", None) is not None:
                return rec.child_index
    return None


def input_rows(data: bytes) -> dict:
    """`{pin: (label, value_slot)}` read off the DEVICES/FAULTS screen.

    Walks the `G12 INPUTS` container and its switch children -- three of 13.6's eight rows are
    nested behind type-6 switches rather than sitting in the top object. A row's pin comes from
    its own caption (`'6 - Awning EXT (12V)'`), so this needs no register arithmetic and stays
    build-general; its indicator slot is the first type-6 record after the caption.
    """
    from . import guidata, records
    root = _input_block_root(data)
    if root is None:
        return {}
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    rows, seen = {}, set()

    def walk(index):
        if index in seen or index >= len(table):
            return
        seen.add(index)
        try:
            recs = records.object_records(data, gs + table[index])
        except Exception:                                   # noqa: BLE001
            return
        pending = None
        for rec in recs:
            label = getattr(rec, "label", None)
            if isinstance(label, str):
                match = _ROW_RE.match(label)
                pending = (int(match.group(1)), label.strip()) if match else None
            if rec.wtype != 6:
                continue
            if pending is not None:
                pin, text = pending
                rows.setdefault(pin, (text, getattr(rec, "value_slot", None)))
                pending = None
            child = getattr(rec, "child_index", None)
            if child is not None:
                walk(child)

    walk(root)
    return rows


def input_status(data: bytes) -> list:
    """The full G12-input picture: transmit rule, receive-filter var, screen row -- per pin.

    Returns every pin in `INPUT_PINS`, including ones the G12 never reports, because "pin 5 is
    watched by the panel and broadcast by nobody" is a result worth seeing rather than an
    absent row.
    """
    rules = input_rules(data)
    vars_by_instance = panel_input_vars(data)
    rows = input_rows(data)
    dispatch = {e.pin: e for e in input_dispatch(data)}
    out = []
    for pin in INPUT_PINS:
        instance = INPUT_INSTANCE_BASE + pin
        pair = rules.get(pin, {})
        label, slot = rows.get(pin, (None, None))
        extra = tuple(sorted({r.instance for r in input_group_rules(data, pin)
                              if r.dgn == INDICATOR_DGN and r.instance != instance}))
        out.append(InputStatus(pin=pin, instance=instance,
                               on_rule=pair.get("on"), off_rule=pair.get("off"),
                               var=vars_by_instance.get(instance),
                               slot=slot, label=label,
                               config=dispatch.get(pin), extra_instances=extra))
    return out


def describe_inputs(data: bytes) -> str:
    """Human-readable G12 input report, for `cli g12-inputs`."""
    lines = [
        "G12 input pins -> RV-C GENERIC_INDICATOR_COMMAND (0x1FBDA), instance 0xA0 + pin",
        "",
        "%-4s %-6s %-13s %-9s %-6s %-6s %s" % (
            "pin", "inst", "sense", "G12 rule", "var", "slot", "row caption"),
    ]
    for st in input_status(data):
        extra = ("  + also broadcasts %s"
                 % ", ".join("%#04x" % i for i in st.extra_instances)) if st.extra_instances else ""
        lines.append("%-4d %#04x   %-13s %-9s %-6s %-6s %s%s" % (
            st.pin, st.instance,
            st.config.sense if st.config else "?",
            "%d rules" % st.config.rule_count if st.config else "?",
            st.var if st.var is not None else "-",
            st.slot if st.slot is not None else "-",
            st.label or "(not on screen)", extra))
    lines += ["", "Watch it on the coach:  candump -L can1 | grep -i 19FBDA9C",
              "  (observed 2026-08-24, BUS-BASELINE PC.24: these frames are exact. A held pin",
              "   also REFRESHES every 1.000 s. The repeat is marked IN this table -- byte-2 bit",
              "   0x04 on the ACTIVE template ('kind 0x04'), cleared on the OFF template -- and the",
              "   code that acts on it is the G12 application at 0x08006000, NOT the flashable",
              "   table-0x67 blob: that blob emits 0x1FBDA only as instance 0xBF, and table 0x62",
              "   holds no 0x1FBDA rule (G12-CODE.md, 'The 1 Hz input refresh'). A tap under ~1 s",
              "   sends one ON/OFF pair and no repeat.)"]
    for st in input_status(data):
        on, off = st.frames()
        if on and off:
            lines.append("  pin %-2d %-24s active %s   inactive %s"
                         % (st.pin, (st.label or "")[:24], on, off))
    return "\n".join(lines)


def _edit(data: bytes, offset: int, old: int, new: int, why: str) -> tuple:
    if data[offset] != old:
        raise G12RuleError(
            f"premise failed at {offset:#x}: expected {old:#04x}, found {data[offset]:#04x}")
    out = bytearray(data)
    out[offset] = new
    sealed, changed = patch.reseal(bytes(out))
    if len(sealed) != len(data):
        raise G12RuleError("a rule edit must be same-size")
    if changed != ["section-checksum:G12"]:
        raise G12RuleError(
            f"expected only the G12 section checksum to move, got {changed} -- "
            f"the edit landed outside the G12 section")
    return sealed, [(offset, old, new, why)]


def set_rule_instance(data: bytes, rule: CommandRule, instance: int) -> tuple:
    """Retarget a rule at a different RV-C instance -- payload byte 0, ONE byte.

    Every shipped charger/inverter rule commands instance 1, so this is the edit for a device
    that claims something else. `0xFF` is refused: on the wire that is the RV-C "not supported"
    sentinel, and a rule addressing it commands nobody.
    """
    if not 0 <= instance <= 0xFE:
        raise G12RuleError(f"instance {instance:#04x} outside 0x00..0xFE (0xFF is the RV-C null)")
    offset = rule.payload_offset(0)
    return _edit(data, offset, rule.instance, instance,
                 f"rule #{rule.index} DGN {rule.dgn:05X}: instance "
                 f"{rule.instance:#04x} -> {instance:#04x}")


def set_rule_selector(data: bytes, rule: CommandRule, payload_index: int, selector: int) -> tuple:
    """Change which config selector feeds one payload byte -- ONE byte.

    Only a slot whose mask bit is already set can be pointed at a selector: the mask decides
    whether the G12 substitutes at all, and setting a selector into an unmasked slot would write
    a literal that looks like a selector and behaves like neither.
    """
    if not (rule.mask >> payload_index) & 1:
        raise G12RuleError(
            f"rule #{rule.index} payload[{payload_index}] is a LITERAL (mask {rule.mask:02X} "
            f"bit {payload_index} clear); set_rule_mask first if that is really intended")
    offset = rule.payload_offset(payload_index)
    return _edit(data, offset, rule.payload[payload_index], selector,
                 f"rule #{rule.index} DGN {rule.dgn:05X}: payload[{payload_index}] selector "
                 f"{rule.payload[payload_index]:#04x} -> {selector:#04x}")


def set_rule_dgn(data: bytes, rule: CommandRule, dgn: int) -> tuple:
    """Change the command DGN a rule broadcasts -- TWO bytes, same-size.

    Restricted to DGNs sharing bits 16-17 with the current one, because byte 2 also carries
    flags this project has not decoded (0x02/0x81/0xC1 all occur in this table). Rewriting it
    would mean guessing at a byte whose meaning is `unknown`, which is exactly the move that
    produced this project's earlier wrong assertions.
    """
    if not 0 <= dgn <= 0x3FFFF:
        raise G12RuleError(f"DGN {dgn:#x} is not an 18-bit value")
    if (dgn >> 16) != (rule.dgn >> 16):
        raise G12RuleError(
            f"DGN {dgn:#07x} differs from {rule.dgn:#07x} in bits 16-17; record byte 2 mixes "
            f"those with undecoded flags, so this module will not rewrite it")
    old = struct.pack("<H", rule.dgn & 0xFFFF)
    new = struct.pack("<H", dgn & 0xFFFF)
    if bytes(data[rule.offset:rule.offset + 2]) != old:
        raise G12RuleError(f"premise failed at {rule.offset:#x}: expected DGN low {old.hex()}")
    out = bytearray(data)
    out[rule.offset:rule.offset + 2] = new
    sealed, changed = patch.reseal(bytes(out))
    if len(sealed) != len(data) or changed != ["section-checksum:G12"]:
        raise G12RuleError(f"unexpected blast radius: {changed}")
    return sealed, [(rule.offset, old, new,
                     f"rule #{rule.index}: DGN {rule.dgn:05X} -> {dgn:05X}")]


def describe(data: bytes) -> str:
    """Human-readable dump of every command rule, for `cli g12-rules`.

    Opens with a census of **all four** rule tables. That is not decoration: this listing showed
    table `0x62` alone for a day, so the 18 input-pin records in table `0x20` were absent from
    the one place anybody would have looked for them (`GUICODE.md` Finding 54).
    """
    lines = ["G12 rule tables -- census", ""]
    for tag, count, by_dgn in table_census(data):
        dgns = "  ".join("%05X x%d" % (d, n) for d, n in sorted(by_dgn.items()))
        lines.append("  table %#04x  %3d records   %s" % (tag, count, dgns))
    lines += ["", "Kind field (byte 2 bits 2-7) per table -- observed behaviour, see KIND_OBSERVED", ""]
    for (tag, kind), n in sorted(kind_census(data).items()):
        note, conf = KIND_OBSERVED.get((tag, kind), ("undecoded", "unknown"))
        lines.append("  table %#04x  kind %02X  x%-3d  %s  [%s]" % (tag, kind, n, note, conf))
    status = status_rules(data)
    if status:
        lines += ["", "Table 0x61 -- cell <-> bus status rules (layout inferred; see StatusRule)", ""]
        lines += ["  " + str(r) for r in status]
    lines += ["",
              "Selector -> RV-C command rules (table 0x62; see `cli g12-inputs` for 0x1FBDA)",
              ""]
    for rule in command_rules(data):
        note = ""
        for sel in rule.selectors.values():
            if rule.dgn in CONFIRMED and CONFIRMED[rule.dgn][0] == sel:
                note = "   <- %s (PC.23, confirmed on the coach)" % CONFIRMED[rule.dgn][1]
        lines.append(str(rule) + note)
    return "\n".join(lines)
