"""The bus map, send side: every way the panel transmits, and what the panel calls each message --
read from the file, nothing per build. `busmap.send_map` is the entry point; this module holds the
mechanism. `GUICODE.md` Finding 83 is the write-up.

The panel has two transmit paths into one queue, and this module follows both from the file.

```
GuiData binding table (+0x778), sliced by the group table (+0x30)        [proven-from-code]
  |  a touch region (wt-12) whose id < group count selects group `id`     (CONTROL-BEHAVIOR §1)
  |  LCD FUN_0021B8B4: for the group's slice, on gesture phase p, every entry whose variant
  v  (+2 >> 4) matches the runtime counter and whose phase mask (+3) has p's bit
     id  = bfi(entry u32, #8, #18)      0x0021B9AA ldr r1,[r4] / 0x0021B9B4 bfi r2,r1,#8,#0x12
     pri = 6                            0x0021B9B2 movs r3,#6 / 0x0021B9C0 bfi r2,r3,#2,#3
     bit 29 = entry +2 bit 3            0x0021B9BE ldrb r1,[r4,#2] / ubfx r1,r1,#3,#1 / bfi #5,#1
     data[0..7] = entry +7,+6,+5,+4,+11,+10,+9,+8      FUN_0021B890 (0x0021B890-0x0021B8AE)
     id byte 0 = 0                      0x0021B9D4 strb fp,[r2,#-0xc]!   (fp = 0 @0x0021B8FC)
  |  bl FUN_0021825C @0x0021B9DE
  v
GuiCode, through the host API table's +0x08 word                         [proven-from-code]
  |  LCD 0x0021C198 ldr r3,=0x0021825D ; str r3,[sp,#0x2c]  (table at sp+0x24, so +0x08)
  |  LCD 0x0021C1AE ldr r3,[r5,#0xc] ; blx r3  -> GuiCode_entry0 (header +0x0C) with r0 = table
  |  GuiCode_entry0: ldr r2,[r4,#8] ; str r2,[=slot]   (13.6 0x00247A9E/0x00247AA2, slot 0x2003986C)
  |  every send: ldr rN,[=slot] ; ldr rN,[rN] ; blx rN with r0 -> a 12-byte <u32 id><8 data>
  v
LCD FUN_0021825C, the 64-entry transmit ring                             [proven-from-code]
     DGN  = id bits 8-25                0x00218298 ubfx r1,r2,#8,#0x12
     pri  = id bits 26-28               0x002182B2 ubfx r1,r1,#2,#3
     SA   = the byte at 0x20000018, unless id bit 29 is set or state+0x6A8 is non-zero, when it
            is the frame's own byte 0 (0x00218282 lsls r3,r3,#0x1a / bpl; 0x00218300 ldr r2,=..)
```

So for a binding, `+0..+2` bits 0-1 ARE the DGN and `+4..+11` ARE the payload, in that order:
both are now `proven-from-code`, where `MCF-FORMAT.md` §5.3 had them `inferred-from-data`. The
source address is the byte at `0x20000018` (the panel's claimed address; `0x9F` on every
capture, `observed-on-hardware`) for every frame except a binding with `+2` bit 3 set, which goes
out from SA `0x00` (the sender zeroes byte 0). Input level 2 (phases 5-8) uses a second slice,
`start + count` for `count2` entries (`0x0021B934`-`0x0021B940`); `count2` is 0 in every corpus
group, so level 2 sends nothing. A group with byte 3 bit 7 also sends the first `[state+0x0C]`
bindings after its own (`0x0021B90C`-`0x0021B92C`), and that count (tag-0x20 `b2`) is 0 in every
build: nothing.

## What is reported

`BindingGroup` -- one per group: its slice, decoded per entry (`BindingSend`), the controls whose
touch regions name it, and a class:

| class | meaning |
|---|---|
| `reached` | a touch region names the group, on an object reachable from the screen tree (`screen_tree`) |
| `unattached` | a touch region names it, but only on objects the screen tree does not reach |
| `dead` | no touch region in the file names the group (nothing the user touches sends it) |
| `stub` | every entry in the slice is the `ff ff` sentinel; nothing to send |

`guicode_variant` says GuiCode selects the group's variant by immediate (`newbutton.
guicode_selected_groups`, the `setVariant` pointer): the touch still sends, GuiCode picks which
copy (`GUICODE.md` Finding 57). It is not a send by GuiCode.

`GuiCodeSend` -- one per `blx` through the sender slot: the frame (a `.rodata` template copied by
`ldm`/`stm`, or a frame built in place on the stack) and what drives it:

| context | meaning |
|---|---|
| `touch` | reached from a handler of GuiCode's touch-id dispatcher (`touch_dispatch`: the search after the id-change gate, on press, and the `0xFF` handler's search on the latched id, on release) by a control-flow walk, `proven-from-code`; `touch_ids` are `(id, edge)` and the ids name the controls |
| `touch-test` | not in a handler, but the function compared the current touch id (`[r4,#0x208]`) with a constant shortly before (a hold consumer): `inferred-from-code` (a linear look-back, not a path) |
| `config-set` | inside the config-set helper (`config.config_set_helper`): the `0x1EE00` offset frame; its callers are listed separately (`ConfigSetCall`) with the OPTIONS row of each offset |
| `other` | none of the above: per-tick code, a poller, a parser. The function is named |

## Names

A name is the firmware's own word; the RV-C specification's is kept apart (`spec_name`), as on the
receive side. Sources, kept apart in `Name.source`:

* `control` -- the caption of a control whose touch region selects the group / reaches the
  handler (record order, `touch_regions`; `inferred-from-data`);
* `outputs-page` -- a `DC_DIMMER_COMMAND_2` instance is a G12 output pin and the OUTPUTS page names
  it (`outputs.output_name_variants`, phase 1; `proven-from-code`);
* `option` -- a config-set offset's OPTIONS row (`config.config_offsets` + `options.option_bindings`;
  `proven-from-code` to the register), or a `0x1FED9` selector the panel's OPTIONS handlers write
  (`config.option_selector`, the send side of Finding 23);
* `echo` -- a `0x1FED9` write's selector is the `0x15FCE` selector the G12 echoes, and the receive
  map names that entry (phase 2; the selector match is the protocol, `observed-on-hardware` PC.2,
  PC.39; the name inherits the receive side's proof).
"""
from __future__ import annotations

import bisect
import struct
from collections import Counter
from dataclasses import dataclass

from . import guidata, memo, tanknames, theme

DGN_DC_DIMMER_COMMAND_2 = 0x1FEDB
DGN_GENERIC_INDICATOR_COMMAND = 0x1FED9
DGN_CONFIG_OFFSET = 0x1EE00          # config.CONFIG_PSEUDO_PGN
FIREFLY_CONFIG_GROUP = 0x96          # 1FED9 payload byte 1 of a selector write / read-back

ENTRY0_VECTOR = 0x0C                 # GuiCode header word: GuiCode_entry0, the API installer
SENDER_API_OFFSET = 0x08             # the host API table word GuiCode_entry0 copies to the slot
TOUCH_ID_OFFSET = 0x208              # GuiCodeState+0x208: the current touch id (CONTROL-BEHAVIOR §1)
TOUCH_REGION_WTYPE = 12
STUB = b"\xff\xff"

HANDLER_WALK_LIMIT = 3000            # instructions one handler's control-flow walk may visit
LOOKBACK = 48                        # instructions a `touch-test` / frame look-back may cross
RESOLVE_WINDOW = 600                 # instructions `_resolve` walks back for a register's last write

# The RV-C specification's command names for DC_DIMMER_COMMAND_2 byte 3, as transcribed in
# rvc2mqtt's rvc-spec.yml (the Wandertech initial transcription). The standard's words, not
# Firefly's.
DIMMER_COMMANDS = {
    0: "set brightness", 1: "on duration", 2: "on delay", 3: "off", 4: "stop", 5: "toggle",
    6: "memory off", 17: "ramp brightness", 18: "ramp toggle", 19: "ramp up", 20: "ramp down",
    21: "ramp up/down", 33: "lock", 34: "unlock", 49: "flash", 50: "flash momentarily",
}

# A `0x1FED9` group-0x96 frame's byte 6 (`FF 96 <sel> 0F <lo> <hi> <op> ..`), as the captures
# show it used (`BUS-BASELINE.md` PC.2 Result 3, PC.39): D1 sets the value, D2 adjusts by a signed
# step (the `-`/`+` buttons), D3 asks the G12 to read the selector back.
CONFIG_OP_SET, CONFIG_OP_ADJUST, CONFIG_OP_READ = 0xD1, 0xD2, 0xD3


def describe_payload(dgn: int | None, p) -> str:
    """A short reading of a sent payload: DC_DIMMER_COMMAND_2's instance/command/level, a
    `0x1FED9` selector operation, a `0x1EE00` offset write; the bytes otherwise."""
    if any(not isinstance(b, int) for b in p):
        return " ".join(("%02X" % b) if isinstance(b, int) else b for b in p)
    p = bytes(p)
    if dgn == DGN_DC_DIMMER_COMMAND_2:
        return "instance %d cmd %d (%s) level %#04x" % (p[0], p[3], DIMMER_COMMANDS.get(p[3], "?"),
                                                        p[2])
    if dgn == DGN_GENERIC_INDICATOR_COMMAND and p[1] == FIREFLY_CONFIG_GROUP:
        v = p[4] | (p[5] << 8)
        if p[6] == CONFIG_OP_SET:
            return "selector %#04x := %d" % (p[2], v)
        if p[6] == CONFIG_OP_ADJUST:
            return "selector %#04x += %d" % (p[2], v - 0x10000 if v & 0x8000 else v)
        if p[6] == CONFIG_OP_READ:
            return "selector %#04x read-back request" % p[2]
        return "selector %#04x (%s)" % (p[2], p.hex())
    if dgn == DGN_CONFIG_OFFSET:
        return "config offset %#x := %d" % (p[0] | (p[1] << 8), p[2])
    return p.hex()


# The send-side DGNs the receive table (`busmap.RVC_SPEC_NAMES`) does not already name, from the
# same transcription. Commands, mostly; used only as `spec_name`, never as the panel's name.
RVC_SEND_NAMES = {
    0x1FEDB: "DC_DIMMER_COMMAND_2", 0x1FED9: "GENERIC_INDICATOR_COMMAND",
    0x1FEF9: "THERMOSTAT_COMMAND_1", 0x1FFEE: "LEVELING_CONTROL_COMMAND",
    0x1FFBE: "AC_LOAD_COMMAND", 0x1FFFE: "SET_DATE_TIME_COMMAND", 0x1FFBC: "DC_LOAD_COMMAND",
    0x1FFD3: "INVERTER_COMMAND", 0x1FFC5: "CHARGER_COMMAND",
    0x1FF95: "CHARGER_CONFIGURATION_COMMAND_2", 0x1FFC4: "CHARGER_CONFIGURATION_COMMAND",
    0x1FFD0: "INVERTER_CONFIGURATION_COMMAND_1", 0x1FF87: "CHARGER_ACFAULT_CONFIGURATION_COMMAND_1",
    0x1FEBE: "CHARGER_CONFIGURATION_COMMAND_4",
    0x1FEB0: "SOLAR_CONTROLLER_CONFIGURATION_COMMAND", 0x1FEB1: "SOLAR_CONTROLLER_COMMAND",
    0x1FEAD: "SOLAR_EQUALIZATION_CONFIGURATION_COMMAND",
}


def spec_name(dgn: int | None) -> str | None:
    """The specification's name for a sent DGN, prefixed by whose it is (`busmap.spec_name`
    plus the command DGNs and J1939's Request)."""
    from . import busmap
    if dgn is None:
        return None
    if dgn in RVC_SEND_NAMES:
        return "RV-C " + RVC_SEND_NAMES[dgn]
    if dgn >> 8 == 0x0EA:
        return "J1939 Request (destination %#04x)" % (dgn & 0xFF)
    return busmap.spec_name(dgn)


# ------------------------------------------------------------------------------- data shapes

@dataclass(frozen=True)
class Name:
    text: str
    source: str          # "control" | "outputs-page" | "output-caption" | "option" | "echo"
    count: int
    proof: str           # "proven-from-code" | "inferred-from-data"


@dataclass(frozen=True)
class Control:
    """A touch region that selects a group (or a GuiCode handler), with its caption."""
    obj: int
    record: int                  # the wt-12 record's file offset
    touch_id: int
    caption: str | None
    rule: str | None             # "control" | "after" | "preceding" (`touch_regions`) | None
    title: str | None            # the object's first label: context, never a name
    attached: bool               # reachable from the screen tree
    pages: tuple = ()            # titles of the screen objects above it (inferred-from-data)


@dataclass(frozen=True)
class BindingSend:
    """One binding entry: the frame it puts on the wire, and when."""
    index: int
    raw: bytes
    dgn: int
    payload: bytes               # the 8 wire bytes (FUN_0021B890's order)
    variant: int
    phases: tuple                # gestures.PHASE_NAMES of the +3 mask, in phase order
    repeats: bool                # +2 bit 2
    source_zero: bool            # +2 bit 3: sent from SA 0x00, not the panel's address
    names: tuple = ()            # Name, for what the payload addresses (pin, selector)

    @property
    def stub(self) -> bool:
        return self.raw[:2] == STUB

    @property
    def instance(self) -> int | None:
        return self.payload[0] if self.dgn == DGN_DC_DIMMER_COMMAND_2 else None

    @property
    def command(self) -> int | None:
        return self.payload[3] if self.dgn == DGN_DC_DIMMER_COMMAND_2 else None

    @property
    def selector(self) -> int | None:
        if self.dgn == DGN_GENERIC_INDICATOR_COMMAND and self.payload[1] == FIREFLY_CONFIG_GROUP:
            return self.payload[2]
        return None

    @property
    def write_value(self) -> int | None:
        """The value of a `0x1FED9` selector write (`.. 0F <lo> <hi> D1 ..`); None otherwise."""
        if self.selector is None or self.payload[6] != CONFIG_OP_SET:
            return None
        return self.payload[4] | (self.payload[5] << 8)

    def describe(self) -> str:
        return describe_payload(self.dgn, self.payload)


@dataclass(frozen=True)
class BindingGroup:
    group: int
    raw: bytes
    start: int
    count: int
    count2: int
    hold_ms: int
    kinds: dict                  # {variant: gestures kind}
    entries: tuple               # BindingSend
    controls: tuple              # Control
    guicode_variant: bool        # GuiCode passes this group to setVariant by immediate
    cls: str                     # "reached" | "unattached" | "dead" | "stub"

    @property
    def caption(self) -> str | None:
        caps = [c.caption for c in self.controls if c.caption and c.attached] or \
               [c.caption for c in self.controls if c.caption]
        if not caps:
            return None
        return " / ".join(t for t, _n in Counter(caps).most_common())

    @property
    def dgns(self) -> tuple:
        return tuple(sorted({e.dgn for e in self.entries if not e.stub}))


@dataclass(frozen=True)
class Frame:
    """A GuiCode-built frame: `<u32 id><8 data>`. `data` items are ints (constant), `"[0x2..]"`
    (a byte loaded from that SRAM cell) or `"?"`."""
    dgn: int | None
    dgn_expr: str                # how the DGN was found: "template", "0x1FEB0", "0xEA00+[cell]"
    data: tuple
    template: int | None         # the `.rodata` template's mapped address, if copied
    priority: int | None
    exact: bool                  # every field is a constant from straight-line code

    def describe(self) -> str:
        return " ".join(("%02X" % b) if isinstance(b, int) else b for b in self.data)

    def reading(self) -> str:
        return describe_payload(self.dgn, self.data)

    @property
    def selector(self) -> int | None:
        d = self.data
        if self.dgn == DGN_GENERIC_INDICATOR_COMMAND and d[1] == FIREFLY_CONFIG_GROUP \
                and isinstance(d[2], int):
            return d[2]
        return None

    @property
    def instance(self) -> int | None:
        if self.dgn == DGN_DC_DIMMER_COMMAND_2 and isinstance(self.data[0], int):
            return self.data[0]
        return None


@dataclass(frozen=True)
class GuiCodeSend:
    at: int                      # the `blx`
    function: int                # enclosing `push {..., lr}`
    frame: Frame
    context: str                 # "touch" | "touch-test" | "config-set" | "other"
    touch_ids: tuple = ()
    controls: tuple = ()
    names: tuple = ()
    evidence: str = ""

    @property
    def dgn(self) -> int | None:
        return self.frame.dgn


@dataclass(frozen=True)
class ConfigSetCall:
    """One `bl <config-set helper>`: the panel sends `0x1EE00 <offset lo> <hi> <value> FF x5` when the
    buffer byte changes (Finding 23; `FUN_00259100`'s compare at `0x0025910A`)."""
    at: int
    function: int
    offset: int | None
    value: int | None
    register: int | None
    option: str | None
    context: str                 # as GuiCodeSend
    touch_ids: tuple = ()
    controls: tuple = ()


# ------------------------------------------------------------------------- the GuiCode stream

def _gc(data: bytes) -> tuple:
    return tanknames._guicode(data)


@memo.content_cached(copy=None)
def _index(data: bytes) -> tuple:
    """`(function entries (indices), branch targets (addresses))` over GuiCode."""
    import capstone.arm as A
    _code, _base, ins, _at = _gc(data)
    entries = tuple(k for k, x in enumerate(ins) if tanknames._is_push_lr(x))
    targets = set()
    for x in ins:
        m = x.mnemonic
        if m in ("bl", "blx"):
            continue
        if m.startswith(("cbz", "cbnz")) and len(x.operands) == 2:
            targets.add(x.operands[1].imm)
        elif m.startswith("b") and len(x.operands) == 1 and x.operands[0].type == A.ARM_OP_IMM:
            targets.add(x.operands[0].imm)
    return entries, frozenset(targets)


def _fstart(data: bytes, k: int) -> int:
    entries, _t = _index(data)
    e = bisect.bisect_right(entries, k) - 1
    return entries[e] if e >= 0 else 0


def _writes(x) -> set:
    return tanknames._writes(x)


def _resolve(data: bytes, reg: str, k: int, lo: int | None = None, depth: int = 0):
    """`(value, index of the defining instruction)` for `reg` just before `ins[k]`, by a linear
    walk back to its last write: a pool literal, a `mov`/`movw` immediate, `add #imm` or a `mov`
    of a register so resolved. `("sp", off)` for a stack address. None when the last write is
    anything else (a load from memory, arithmetic): the walk never guesses."""
    import capstone.arm as A
    code, base, ins, _at = _gc(data)
    if lo is None:
        lo = _fstart(data, k)
    if depth > 8:
        return None
    for j in range(k - 1, max(lo, k - RESOLVE_WINDOW) - 1, -1):
        x = ins[j]
        if reg not in _writes(x):
            continue
        m, ops = x.mnemonic, x.operands
        if m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM \
                and ops[1].mem.base == A.ARM_REG_PC:
            v = tanknames._literal(code, base, x)
            return (v, j) if v is not None else None
        if m in ("add", "add.w", "addw", "adds") and len(ops) == 3 \
                and ops[1].type == A.ARM_OP_REG and ops[2].type == A.ARM_OP_IMM:
            src = x.reg_name(ops[1].reg)
            if src == "sp":
                return ("sp", ops[2].imm), j
            v = _resolve(data, src, j, lo, depth + 1)
            if v and isinstance(v[0], int):
                return (v[0] + ops[2].imm) & 0xFFFFFFFF, v[1]
            return None
        if m in ("add", "adds") and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
            v = _resolve(data, reg, j, lo, depth + 1)
            if v and isinstance(v[0], int):
                return (v[0] + ops[1].imm) & 0xFFFFFFFF, v[1]
            return None
        if m in ("mov", "movs", "mov.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_REG:
            src = x.reg_name(ops[1].reg)
            if src == "sp":
                return ("sp", 0), j
            return _resolve(data, src, j, lo, depth + 1)
        if m in ("mov", "movs", "mov.w", "movw") and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
            return ops[1].imm & 0xFFFFFFFF, j
        return None
    return None


def _straight(data: bytes, j: int, k: int) -> bool:
    """No branch lands strictly after `ins[j]` up to and including `ins[k]`: the value defined at
    `j` is the one every path into `k` carries."""
    _code, _base, ins, _at = _gc(data)
    _e, targets = _index(data)
    return not any(ins[q].address in targets for q in range(j + 1, k + 1))


# ------------------------------------------------------------------------------- the sender

@memo.content_cached(copy=None)
def sender_slot(data: bytes) -> tuple:
    """`(slot address, store address)`: where `GuiCode_entry0` keeps the host API table's
    `+0x08` word, the CAN frame sender (13.6 `0x2003986C`, stored at `0x00247AA2`). Located
    from the header vector, not assumed. `(None, None)` when the shape is absent."""
    import capstone.arm as A
    code, base, ins, at = _gc(data)
    sec = theme.guicode_section(data)
    e0 = struct.unpack_from("<I", data, sec.offset + ENTRY0_VECTOR)[0] & ~1
    k0 = at.get(e0)
    if k0 is None:
        return None, None
    for j in range(k0, min(k0 + 32, len(ins))):
        x = ins[j]
        ops = x.operands
        if x.mnemonic in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM \
                and x.reg_name(ops[1].mem.base) == "r4" and ops[1].mem.disp == SENDER_API_OFFSET:
            rx = x.reg_name(ops[0].reg)
            for q in range(j + 1, min(j + 4, len(ins))):
                y = ins[q]
                if y.mnemonic == "str" and x.reg_name(y.operands[0].reg) == rx \
                        and y.operands[1].type == A.ARM_OP_MEM and y.operands[1].mem.disp == 0:
                    v = _resolve(data, y.reg_name(y.operands[1].mem.base), q)
                    if v and isinstance(v[0], int):
                        return v[0], y.address
    return None, None


@memo.content_cached(copy=None)
def _indirect_calls(data: bytes) -> tuple:
    """`((index of blx, pointer cell or None, defining address), ...)` for every `blx rN` whose
    `rN` was loaded `ldr rN,[rM,#d]` with `rM` resolvable: the cell is `rM + d`."""
    import capstone.arm as A
    _code, _base, ins, _at = _gc(data)
    out = []
    for k, x in enumerate(ins):
        if x.mnemonic != "blx" or not x.operands or x.operands[0].type != A.ARM_OP_REG:
            continue
        rn = x.op_str
        lo = _fstart(data, k)
        cell = None
        for j in range(k - 1, max(lo, k - 40) - 1, -1):
            y = ins[j]
            if rn not in _writes(y):
                continue
            ops = y.operands
            if y.mnemonic in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM \
                    and ops[1].mem.index == 0 and ops[1].mem.base != A.ARM_REG_PC:
                v = _resolve(data, y.reg_name(ops[1].mem.base), j)
                if v and isinstance(v[0], int):
                    cell = (v[0] + ops[1].mem.disp) & 0xFFFFFFFF
            break
        out.append((k, cell))
    return tuple(out)


def _template(data: bytes, addr: int):
    code, base, _ins, _at = _gc(data)
    lo = addr - base
    if not 0 <= lo <= len(code) - 12:
        return None
    return code[lo:lo + 12]


def _frame(data: bytes, k: int) -> Frame:
    """The frame `ins[k]` (a send) passes in r0."""
    import capstone.arm as A
    _code, _base, ins, _at = _gc(data)
    lo = _fstart(data, k)
    r0 = _resolve(data, "r0", k)
    tmpl = None
    exact = False
    if r0 and isinstance(r0[0], int):
        tmpl, exact = r0[0], _straight(data, r0[1], k)
    elif r0 and isinstance(r0[0], tuple):
        for j in range(k - 1, max(lo, k - 30) - 1, -1):
            y = ins[j]
            if y.mnemonic == "blx":
                break
            if y.mnemonic.startswith("ldm"):
                v = _resolve(data, y.reg_name(y.operands[0].reg), j)
                if v and isinstance(v[0], int) and _template(data, v[0]) is not None:
                    tmpl, exact = v[0], _straight(data, v[1], k)
                break
    if tmpl is not None and _template(data, tmpl) is not None:
        raw = _template(data, tmpl)
        word = struct.unpack_from("<I", raw, 0)[0]
        return Frame((word >> 8) & 0x3FFFF, "template", tuple(raw[4:12]), tmpl,
                     (word >> 26) & 7, exact)
    # built in place: r0 = sp + off; id at [sp,#off], data at [sp,#off+4 ..]
    off = r0[0][1] if r0 and isinstance(r0[0], tuple) else None
    start = lo
    for j in range(k - 1, max(lo, k - 4 * LOOKBACK) - 1, -1):
        if ins[j].mnemonic == "blx":
            start = j + 1
            break
    else:
        start = max(lo, k - 4 * LOOKBACK)
    dgn, expr, ok = None, "?", False
    cells: list = ["?"] * 8
    exact = True
    for j in range(start, k):
        y = ins[j]
        ops = y.operands
        if y.mnemonic == "bfi" and y.op_str.endswith("#8, #0x12"):
            rd = y.reg_name(ops[1].reg)
            v = _resolve(data, rd, j, start)
            if v and isinstance(v[0], int):
                dgn, expr, ok = v[0] & 0x3FFFF, "%#07x" % (v[0] & 0x3FFFF), _straight(data, v[1], j)
            else:
                dgn, expr, ok = None, _expr(data, rd, j, start), False
        elif off is not None and y.mnemonic in ("strb", "strb.w") and len(ops) == 2 \
                and ops[1].type == A.ARM_OP_MEM and y.reg_name(ops[1].mem.base) == "sp" \
                and off + 4 <= ops[1].mem.disp < off + 12:
            rv = y.reg_name(ops[0].reg)
            v = _resolve(data, rv, j, start)
            pos = ops[1].mem.disp - off - 4
            if v and isinstance(v[0], int):
                cells[pos] = v[0] & 0xFF
                exact = exact and _straight(data, v[1], j)
            else:
                cells[pos] = _expr(data, rv, j, start)
                exact = False
    return Frame(dgn, expr, tuple(cells), None, None, exact and ok and "?" not in cells)


def _expr(data: bytes, reg: str, k: int, lo: int) -> str:
    """A short description of a non-constant byte: `[0x2003..]` for a byte loaded from a
    resolvable cell, `0xEA00+[..]` for an `add #imm` on one, else `?`."""
    import capstone.arm as A
    _code, _base, ins, _at = _gc(data)
    for j in range(k - 1, max(lo, k - 40) - 1, -1):
        x = ins[j]
        if reg not in _writes(x):
            continue
        ops = x.operands
        if x.mnemonic.startswith("ubfx") or x.mnemonic.startswith("uxt"):
            return _expr(data, x.reg_name(ops[1].reg), j, lo)
        if x.mnemonic in ("add", "add.w", "adds") and len(ops) == 3 and ops[2].type == A.ARM_OP_IMM:
            return "%#x+%s" % (ops[2].imm, _expr(data, x.reg_name(ops[1].reg), j, lo))
        if x.mnemonic.startswith("ldr") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM \
                and ops[1].mem.base != A.ARM_REG_PC and ops[1].mem.index == 0:
            v = _resolve(data, x.reg_name(ops[1].mem.base), j)
            if v and isinstance(v[0], int):
                return "[%#x]" % ((v[0] + ops[1].mem.disp) & 0xFFFFFFFF)
            return "?"
        return "?"
    return "?"


# ---------------------------------------------------------------------- the touch dispatcher

@memo.content_cached(copy=None)
def touch_dispatch(data: bytes) -> tuple:
    """GuiCode's touch-id dispatcher: `{handler address: frozenset((touch id, edge), ...)}` and
    where it was read from.

    The gate (13.6 `0x0024ACB2`-`0x0024ACBC`, CONTROL-BEHAVIOR §1) loads the current id
    (`ldrh.w r3,[r4,#0x208]`) and the id latched on the previous pass (`ldrh r2,[r2,#0xe]`,
    `0x20030190`), compares them and skips everything when they are equal. What follows is a
    binary search on the current id: the **press** edge (`edge="press"`). Its handler for id
    `0xFF` -- nothing under the finger, i.e. the finger has just lifted -- clears the two hold
    deadlines and then runs a **second** binary search, on the *latched* register, which still
    holds the id that was under the finger (13.6 `0x0024B01A`-`0x0024B02A`: `movw r3,#0x846` /
    `cmp r2,r3` / `beq.w`): the **release** edge (`edge="release"`). The second search is
    taken only if no instruction on the path from the gate to it writes the latched register.

    Walked, not pattern-matched: a node is `mov(w) rK,#K` / `cmp rId,..` / a conditional branch;
    `beq T` after `cmp rId,K` makes `T` K's handler, `bne T` makes the fall-through K's handler
    and `T` the rest of the search; any other branch continues the search at both successors.
    When several gates exist, the one whose search finds the most handlers is taken.
    Returns `(handlers, gates, exits, search addresses, loose ends)`; `({}, (), (), (), ())`
    when no gate is found."""
    import capstone.arm as A
    _code, _base, ins, at = _gc(data)
    best = ({}, (), frozenset(), frozenset(), frozenset())
    for k, x in enumerate(ins[:-4]):
        ops = x.operands
        if not (x.mnemonic in ("ldrh", "ldrh.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM
                and ops[1].mem.disp == TOUCH_ID_OFFSET and x.reg_name(ops[1].mem.base) == "r4"):
            continue
        rid = x.reg_name(ops[0].reg)
        for j in range(k + 1, k + 4):
            y = ins[j]
            if not (y.mnemonic.startswith("cmp") and y.op_str.startswith(rid + ",")
                    and y.operands[1].type == A.ARM_OP_REG and ins[j + 1].mnemonic.startswith("beq")):
                continue
            rprev = y.reg_name(y.operands[1].reg)
            exits = frozenset({ins[j + 1].operands[0].imm})
            found, written, tree, loose = _walk_tree(ins, at, ins[j + 2].address, rid, exits, {})
            handlers = {h: {(i, "press") for i in ids} for h, ids in found.items()}
            tree = set(tree)
            loose = set(loose)
            for h, ids in found.items():
                if 0xFF not in ids or rprev in written[h]:
                    continue
                # the release search: the first compare of the latched register in the 0xFF
                # handler, if the handler has not written it first
                q = at.get(h)
                pre: set = set()
                while q is not None and q < len(ins) and ins[q].address - h < 0x40:
                    z = ins[q]
                    if z.mnemonic.startswith("cmp") and z.operands \
                            and z.reg_name(z.operands[0].reg) == rprev:
                        start = q
                        while start > at[h] and ins[start - 1].mnemonic in ("movw", "mov", "movs") \
                                and z.reg_name(ins[start - 1].operands[0].reg) not in (rprev,):
                            start -= 1
                        rel, _w, tree2, loose2 = _walk_tree(ins, at, ins[start].address, rprev,
                                                            exits, {})
                        tree |= tree2
                        loose |= loose2
                        for h2, ids2 in rel.items():
                            handlers.setdefault(h2, set()).update((i, "release") for i in ids2)
                        break
                    if rprev in _writes(z) or z.mnemonic.startswith(("b", "cb", "pop")):
                        break
                    q += 1
            if len(handlers) > len(best[0]):
                best = ({hh: frozenset(v) for hh, v in handlers.items()}, (x.address,), exits,
                        frozenset(tree), frozenset(loose - set(handlers)))
            break
    return best


def _walk_tree(ins, at, start: int, rid: str, exits, consts0: dict) -> tuple:
    """The binary search on `rid` from `start` (`touch_dispatch`): `({handler: ids},
    {handler: registers written on the path to it}, search addresses, loose ends)`.

    Each path carries the interval the id must lie in (it is a `ldrh`, so `[0, 0xFFFF]` at the
    start), narrowed by every decided compare. A compiled search ends a range that holds one
    integer without a final compare (13.6 `0x0024BA72`-`0x0024BA80`: `cmp r2,#0x844` / `beq` /
    `bhi`, and the fall-through is id `0x843`'s handler because an earlier branch fixed
    `id > 0x842`), so a path that reaches a non-search instruction with a one-value interval has
    reached that value's handler. A loose end is a path that stops anywhere else: code the walk
    could not attribute to an id."""
    import capstone.arm as A
    handlers: dict = {}
    written: dict = {}
    seen: set = set()
    loose: set = set()
    todo = [(start, dict(consts0), frozenset(), 0, 0xFFFF, frozenset())]

    def hit(h, ident, wr):
        handlers.setdefault(h, set()).add(ident)
        written.setdefault(h, set()).update(wr)

    def trim(lo, hi, excl):
        while lo <= hi and lo in excl:
            lo += 1
        while hi >= lo and hi in excl:
            hi -= 1
        return lo, hi

    while todo:
        a, consts, wr, lo, hi, excl = todo.pop()
        consts = dict(consts)
        cur = None
        while a in at and a not in seen:
            seen.add(a)
            x = ins[at[a]]
            ops = x.operands
            nxt = x.address + x.size
            m = x.mnemonic
            if m in ("movw", "mov", "movs", "mov.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
                consts[x.reg_name(ops[0].reg)] = ops[1].imm
                wr = wr | {x.reg_name(ops[0].reg)}
                a = nxt
                continue
            if m.startswith("cmp") and len(ops) == 2 and x.reg_name(ops[0].reg) == rid:
                cur = ops[1].imm if ops[1].type == A.ARM_OP_IMM else consts.get(x.reg_name(ops[1].reg))
                a = nxt
                continue
            if m.startswith("b") and len(ops) == 1 and ops[0].type == A.ARM_OP_IMM and m not in ("bl", "blx"):
                t = ops[0].imm
                cc = m.split(".")[0][1:]
                if cc in ("", "al"):
                    a = t
                    continue
                if cur is None:
                    loose.add(a)
                    break
                k = cur
                if cc == "eq":
                    hit(t, k, wr)
                    excl = excl | {k}
                    lo, hi = trim(lo, hi, excl)
                    a = nxt
                    continue
                if cc == "ne":
                    hit(nxt, k, wr)
                    if t not in exits:
                        todo.append((t, consts, wr) + trim(lo, hi, excl | {k}) + (excl | {k},))
                    break
                if cc in ("hi", "gt"):
                    taken, fall = (max(lo, k + 1), hi), (lo, min(hi, k))
                elif cc in ("ls", "le"):
                    taken, fall = (lo, min(hi, k)), (max(lo, k + 1), hi)
                elif cc in ("hs", "cs", "ge"):
                    taken, fall = (max(lo, k), hi), (lo, min(hi, k - 1))
                elif cc in ("lo", "cc", "lt"):
                    taken, fall = (lo, min(hi, k - 1)), (max(lo, k), hi)
                else:
                    loose.add(a)
                    break
                if t not in exits:
                    todo.append((t, consts, wr) + trim(*taken, excl) + (excl,))
                lo, hi = trim(*fall, excl)
                a = nxt
                continue
            seen.discard(a)                    # not a search instruction: the path left the tree
            if lo == hi:
                hit(a, lo, wr)                 # the one value this range can hold
            else:
                loose.add(a)
            break
    return ({h: frozenset(v) for h, v in handlers.items()},
            {h: frozenset(v) for h, v in written.items()}, frozenset(seen), frozenset(loose))


@memo.content_cached(copy=None)
def _handler_reach(data: bytes) -> dict:
    """`{instruction index: frozenset(touch ids)}`: every instruction a handler's control-flow
    walk reaches, with the ids whose handlers reach it. The walk follows both arms of every
    conditional branch and every unconditional one, stops at a return, at the dispatcher's
    exits (the id re-latch tail every handler branches to), at another handler's entry, and at
    `HANDLER_WALK_LIMIT`. `bl` is stepped over (its target is attributed separately)."""
    import capstone.arm as A
    _code, _base, ins, at = _gc(data)
    handlers, _gate, exits, tree, _loose = touch_dispatch(data)
    tail = set(exits) | set(tree)
    targets = Counter()
    for h in handlers:
        k = at.get(h)
        # the shared tail: the most common unconditional target handlers jump to
        for j in range(k, min(k + 400, len(ins))) if k is not None else ():
            x = ins[j]
            if x.mnemonic in ("b", "b.w") and x.operands and x.operands[0].type == A.ARM_OP_IMM:
                targets[x.operands[0].imm] += 1
                break
    if targets:
        tail.add(targets.most_common(1)[0][0])
    reach: dict = {}
    for h, ids in handlers.items():
        seen: set = set()
        todo = [h]
        n = 0
        while todo and n < HANDLER_WALK_LIMIT:
            a = todo.pop()
            while a in at and a not in seen and a not in tail and (a == h or a not in handlers):
                seen.add(a)
                n += 1
                k = at[a]
                x = ins[k]
                reach.setdefault(k, set()).update(ids)
                m = x.mnemonic
                if (m.startswith("pop") and "pc" in x.op_str) or m == "bx":
                    break
                ops = x.operands
                if m.startswith(("cbz", "cbnz")) and len(ops) == 2:
                    todo.append(ops[1].imm)
                elif m.startswith("b") and m not in ("bl", "blx") and len(ops) == 1 \
                        and ops[0].type == A.ARM_OP_IMM:
                    cc = m.split(".")[0][1:]
                    if cc in ("", "al"):
                        a = ops[0].imm
                        continue
                    todo.append(ops[0].imm)
                elif m.startswith("tbb") or m.startswith("tbh"):
                    break
                a = x.address + x.size
    return {k: frozenset(v) for k, v in reach.items()}


def _touch_test(data: bytes, k: int) -> tuple:
    """Touch ids a `cmp` of the current touch id (`ldrh rX,[r4,#0x208]`) tests within
    `LOOKBACK` instructions before `ins[k]`, in the same function. A linear look-back
    (`inferred-from-code`): it says the send sits after such a test, not that it is gated by it."""
    import capstone.arm as A
    _code, _base, ins, _at = _gc(data)
    lo = _fstart(data, k)
    ids = set()
    idregs = set()
    for j in range(max(lo, k - LOOKBACK), k):
        x = ins[j]
        ops = x.operands
        if x.mnemonic in ("ldrh", "ldrh.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM \
                and ops[1].mem.disp == TOUCH_ID_OFFSET:
            idregs.add(x.reg_name(ops[0].reg))
            continue
        if x.mnemonic.startswith("cmp") and len(ops) == 2 and x.reg_name(ops[0].reg) in idregs:
            if ops[1].type == A.ARM_OP_IMM:
                ids.add(ops[1].imm)
            else:
                v = _resolve(data, x.reg_name(ops[1].reg), j)
                if v and isinstance(v[0], int):
                    ids.add(v[0])
            continue
        idregs -= _writes(x)
    return tuple(sorted(ids))


# ------------------------------------------------------------------------------ the controls

def _caption_item(rec, own: dict) -> str | None:
    """The text a record contributes to a control's caption: its own label (two or more
    characters, not a dynamic string or an `NN - NAME` row), or for a wt-5 container the one
    label of the child object it draws (13.6 object 31's `Low Volts` is the child 285 of the
    container just before touch region `0x81`). A child with several labels (11.23's tank
    wells) is a cluster, not a caption."""
    from . import busmap, outputs
    if rec.label is not None:
        t = rec.label.strip()
        if len(t) > 1 and rec.wtype != outputs.DYNAMIC_STRING_WTYPE and not outputs.ROW_RE.match(t):
            return busmap._text(rec)
        return None
    if rec.wtype == 5 and rec.child_index is not None:
        texts = [busmap._text(x) for x in own.get(rec.child_index, ())
                 if x.label is not None and len(x.label.strip()) > 1
                 and x.wtype != outputs.DYNAMIC_STRING_WTYPE]
        return texts[0] if len(texts) == 1 else None     # a cluster of labels is not a caption
    return None


@memo.content_cached(copy=None)
def touch_regions(data: bytes) -> dict:
    """`{touch id: (Control, ...)}` for every wt-12 record in table 1's own record lists.

    The caption is record order (`inferred-from-data`), the order controls use: a box with its
    own mode-7 fill starts a control (`outputs._segments`), then caption and touch region. The
    control's records are its segment from just after the previous touch region in it to the
    next one; the caption is the caption item (`_caption_item`) nearest before the touch region
    (`rule="control"`), else the nearest after it (`"after"`: 13.6's AGS pill, box / touch /
    `AGS`), else the nearest label before it anywhere in the object (`"preceding"`)."""
    from . import busmap, outputs
    _refs, own, _parents = busmap._widget_index(data)
    reach = screen_tree(data)
    pages = _page_titles(data)
    out: dict = {}
    for obj, recs in own.items():
        segs = outputs._segments(list(recs))
        for n, r in enumerate(recs):
            if r.wtype != TOUCH_REGION_WTYPE:
                continue
            tid = data[r.end - 2] | (data[r.end - 1] << 8)
            caption, rule = None, None
            seg = next((s for s in segs if any(x.offset == r.offset for x in s)), [])
            pos = next(i for i, x in enumerate(seg) if x.offset == r.offset)
            first = max([i + 1 for i in range(pos) if seg[i].wtype == TOUCH_REGION_WTYPE] or [0])
            last = min([i for i in range(pos + 1, len(seg)) if seg[i].wtype == TOUCH_REGION_WTYPE]
                       or [len(seg)])
            for x in reversed(seg[first:pos]):
                caption = _caption_item(x, own)
                if caption:
                    rule = "control"
                    break
            if caption is None:
                for x in seg[pos + 1:last]:
                    caption = _caption_item(x, own)
                    if caption:
                        rule = "after"
                        break
            if caption is None:
                for x in reversed(recs[:n]):
                    if x.label is not None and len(x.label.strip()) > 1 \
                            and x.wtype != outputs.DYNAMIC_STRING_WTYPE:
                        caption, rule = busmap._text(x), "preceding"
                        break
            out.setdefault(tid, []).append(Control(
                obj, r.offset, tid, caption, rule, busmap._title(recs), obj in reach,
                pages.get(obj, ())))
    return {k: tuple(v) for k, v in out.items()}


@memo.content_cached(copy=None)
def screen_tree(data: bytes) -> frozenset:
    """Every object the renderer can reach from a screen: `guidata.reachable_objects` from
    object 0 and from each `layout.screen_object_map` object (14.7's object 0 is not the screen
    switch, so object 0 alone reaches nothing there). Containers and switch candidates only:
    an object GuiCode shows some other way is not in it, which is why `unattached` is a class
    and not a verdict."""
    from . import layout
    try:
        match = guidata.find_match_table(data)
        roots = {0} | set(layout.screen_object_map(data, match).values())
        out: set = set()
        for r in sorted(roots):
            if r not in out:
                out |= guidata.reachable_objects(data, r, match)
        return frozenset(out)
    except Exception:                          # pragma: no cover - a build the walk refuses
        return frozenset(range(len(guidata.object_tables(data)[1])))


@memo.content_cached(copy=None)
def _page_titles(data: bytes) -> dict:
    """`{object: (title of each screen object above it, ...)}` (`outputs._parents`, the
    container and switch-arm graph, walked up to a `layout.screen_object_map` object)."""
    from . import busmap, layout, outputs
    try:
        screens = set(layout.screen_object_map(data).values())
    except Exception:                          # pragma: no cover
        return {}
    parents = outputs._parents(data)
    _refs, own, _p = busmap._widget_index(data)
    memo_: dict = {}

    def up(o: int, stack: frozenset) -> frozenset:
        if o in memo_:
            return memo_[o]
        found = {o} if o in screens else set()
        for p, _cond in parents.get(o, ()):
            if p not in stack and len(stack) < 24:
                found |= up(p, stack | {o})
        memo_[o] = frozenset(found)
        return memo_[o]

    out: dict = {}
    for obj in own:
        ss = up(obj, frozenset())
        titles = sorted({busmap._title(own.get(s, ())) or "object %d" % s for s in ss})
        out[obj] = tuple(titles)
    return out


# ---------------------------------------------------------------------------------- the names

@memo.content_cached(copy=None)
def _name_context(data: bytes) -> tuple:
    """`(outputs-page names, control captions, {selector: echo names}, {selector: option rows})`."""
    from . import busmap, config, options, outputs
    try:
        out_names = outputs.output_name_variants(data)
    except ValueError:
        out_names = {}
    captions = outputs.control_captions(data)
    echo: dict = {}
    for e in busmap.receive_map(data, frozenset({busmap.DGN_CONFIG})):
        if e.filter_value is not None and e.filter_byte == 0 and e.names:
            echo.setdefault(e.filter_value, []).append(e)
    rows: dict = {}
    try:
        offsets = config.config_offsets(data)
    except Exception:                          # pragma: no cover
        offsets = {}
    by_reg: dict = {}
    for b in options.option_bindings(data):
        by_reg.setdefault(b.register, [])
        if b.name not in by_reg[b.register]:
            by_reg[b.register].append(b.name)
    for off, reg in offsets.items():
        sel = config.option_selector(data, off)
        if sel is not None and reg in by_reg:
            rows.setdefault(sel, [])
            for n in by_reg[reg]:
                if n not in rows[sel]:
                    rows[sel].append(n)
    return out_names, captions, echo, rows


def _norm(t: str) -> str:
    return " ".join(t.split())


def payload_names(data: bytes, dgn: int | None, instance: int | None,
                  selector: int | None) -> tuple:
    """Names for what a frame addresses: a G12 pin's OUTPUTS-page names and captions for a
    `DC_DIMMER_COMMAND_2` instance, the OPTIONS row and the receive map's name for a `0x1FED9`
    selector."""
    out_names, captions, echo, rows = _name_context(data)
    names: list = []
    if dgn == DGN_DC_DIMMER_COMMAND_2 and instance is not None:
        c = Counter(n.name for n in out_names.get(instance, ()) if n.name)
        for text, k in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
            names.append(Name(text, "outputs-page", k, "proven-from-code"))
        c = Counter()
        for cap in captions.get(instance, ()):
            c[cap.text] += len(cap.objects)
        for text, k in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
            names.append(Name(text, "output-caption", k, "inferred-from-data"))
    if dgn == DGN_GENERIC_INDICATOR_COMMAND and selector is not None:
        for row in rows.get(selector, ()):
            names.append(Name(_norm(row), "option", 1, "proven-from-code"))
        seen = {n.text for n in names}
        for e in echo.get(selector, ()):
            for n in e.names:
                if n.text not in seen:
                    seen.add(n.text)
                    names.append(Name(n.text, "echo", n.count, n.proof))
    return tuple(names)


def _control_names(controls) -> tuple:
    c = Counter(x.caption for x in controls if x.caption and x.attached)
    if not c:
        c = Counter(x.caption for x in controls if x.caption)
    return tuple(Name(t, "control", k, "inferred-from-data")
                 for t, k in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


# ------------------------------------------------------------------------------ binding groups

def _binding_send(data: bytes, b: guidata.Binding, names: bool = True) -> BindingSend:
    from . import gestures, newbutton
    raw = b.raw
    dgn = struct.unpack_from("<I", raw, 0)[0] & 0x3FFFF
    payload = newbutton.binding_payload(raw)
    phases = tuple(gestures.PHASE_NAMES[bit] for bit in (1, 2, 4, 8) if raw[3] & bit)
    send = BindingSend(b.index, raw, dgn, payload, raw[2] >> 4, phases, bool(raw[2] & 0x04),
                       bool(raw[2] & 0x08))
    if send.stub or not names:
        return send
    return BindingSend(b.index, raw, dgn, payload, send.variant, phases, send.repeats,
                       send.source_zero, payload_names(data, dgn, send.instance, send.selector))


@memo.content_cached(copy=None)
def binding_groups(data: bytes, names: bool = True) -> tuple:
    """A `BindingGroup` per group-table entry, in index order. `names=False` skips the payload
    names (the OUTPUTS-page names cost most of a map; the classes do not need them)."""
    from . import gestures, newbutton
    groups = guidata.group_table(data)
    bindings = guidata.binding_table(data)
    regions = touch_regions(data)
    try:
        selected = newbutton.guicode_selected_groups(data)
    except Exception:                          # pragma: no cover
        selected = frozenset()
    out = []
    for g in groups:
        end = min(g.start + g.count, len(bindings))
        entries = tuple(_binding_send(data, b, names) for b in bindings[g.start:end])
        controls = regions.get(g.index, ())
        live = [e for e in entries if not e.stub]
        if not live:
            cls = "stub"
        elif any(c.attached for c in controls):
            cls = "reached"
        elif controls:
            cls = "unattached"
        else:
            cls = "dead"
        beh = gestures.describe_group(data, g.index)
        out.append(BindingGroup(g.index, g.raw, g.start, g.count, g.raw[7], beh.hold_ms,
                                dict(beh.kinds), entries, controls, g.index in selected, cls))
    return tuple(out)


# ------------------------------------------------------------------------------ GuiCode sends

def _context(data: bytes, k: int, helper_range) -> tuple:
    """`(context, touch ids, controls, evidence)` for the instruction at index `k`."""
    _code, _base, ins, _at = _gc(data)
    a = ins[k].address
    if helper_range and helper_range[0] <= a < helper_range[1]:
        return "config-set", (), (), "inside the config-set helper %#x" % helper_range[0]
    reach = _handler_reach(data)
    regions = touch_regions(data)
    ids = reach.get(k)
    if ids:
        ids = tuple(sorted(ids))
        ctl = tuple(c for i, _e in ids for c in regions.get(i, ()))
        return "touch", ids, ctl, ("reached from the touch dispatcher's handler(s) for %s"
                                   % ", ".join("%#x (%s)" % ie for ie in ids))
    ids = tuple((i, "test") for i in _touch_test(data, k))
    if ids:
        ctl = tuple(c for i, _e in ids for c in regions.get(i, ()))
        return "touch-test", ids, ctl, ("after a compare of the current touch id with %s "
                                        "(linear look-back)" % ", ".join("%#x" % i for i, _e in ids))
    return "other", (), (), "function %#x" % ins[_fstart(data, k)].address


def _helper_range(data: bytes):
    from . import config
    h = config.config_set_helper(data)
    if h is None:
        return None
    _code, _base, ins, at = _gc(data)
    k = at.get(h)
    if k is None:
        return None
    for j in range(k + 1, len(ins)):
        x = ins[j]
        if (x.mnemonic.startswith("pop") and "pc" in x.op_str) or x.mnemonic == "bx":
            # the helper has two early returns before its body; take the last return within
            # 0x100 bytes
            end = x.address + x.size
            for q in range(j + 1, len(ins)):
                y = ins[q]
                if y.address - h > 0x100 or tanknames._is_push_lr(y) and y.address - h > 0x10:
                    break
                if (y.mnemonic.startswith("pop") and "pc" in y.op_str) or y.mnemonic == "bx":
                    end = y.address + y.size
            return h, end
    return h, h + 0x100


@memo.content_cached(copy=None)
def guicode_sends(data: bytes, names: bool = True) -> tuple:
    """A `GuiCodeSend` per `blx` through the sender slot, in address order (`names` as in
    `binding_groups`)."""
    slot, _store = sender_slot(data)
    if slot is None:
        return ()
    _code, _base, ins, _at = _gc(data)
    hr = _helper_range(data)
    out = []
    for k, cell in _indirect_calls(data):
        if cell != slot:
            continue
        fr = _frame(data, k)
        ctx, ids, ctl, why = _context(data, k, hr)
        found = list(_control_names(ctl))
        if names:
            found += payload_names(data, fr.dgn, fr.instance, fr.selector)
        out.append(GuiCodeSend(ins[k].address, ins[_fstart(data, k)].address, fr, ctx, ids,
                               ctl, tuple(found), why))
    return tuple(out)


@memo.content_cached(copy=None)
def config_set_calls(data: bytes) -> tuple:
    """A `ConfigSetCall` per `bl <config-set helper>`, with the offset's OPTIONS row."""
    import capstone.arm as A
    from . import busmap, config
    helper = config.config_set_helper(data)
    if helper is None:
        return ()
    _code, _base, ins, _at = _gc(data)
    hr = _helper_range(data)
    out = []
    for k, x in enumerate(ins):
        if x.mnemonic != "bl" or not x.operands or x.operands[0].type != A.ARM_OP_IMM \
                or (x.operands[0].imm & ~1) != helper:
            continue
        r1 = _resolve(data, "r1", k)
        r2 = _resolve(data, "r2", k)
        off = r1[0] if r1 and isinstance(r1[0], int) else None
        val = r2[0] if r2 and isinstance(r2[0], int) else None
        reg, opt = busmap._option_for(data, off) if off is not None else (None, None)
        ctx, ids, ctl, _why = _context(data, k, hr)
        out.append(ConfigSetCall(x.address, ins[_fstart(data, k)].address, off, val, reg, opt,
                                 ctx, ids, ctl))
    return tuple(out)


# ----------------------------------------------------------------------- the LCD's own sender

LCD_BASE = 0x210000                  # the raw-loader base every LCD citation uses

# FUN_0021B890, the binding payload copy: dst[0..7] = src[3,2,1,0,7,6,5,4] (`ldrb r3,[r1,#i]` /
# `strb r3,[r0,#j]` x 8, then `bx lr`). Matched as bytes, so a build that moves it is still found.
_PAYLOAD_COPY = bytes.fromhex("cb780370" "8b784370" "4b788370" "0b78c370"
                              "cb790371" "8b794371" "4b798371" "0b79c371" "7047")


@dataclass(frozen=True)
class LcdSender:
    payload_copy: int            # FUN_0021B890 on 13.6
    binding_send: int            # the `bl` to the transmit ring right after the copy
    transmit: int                # FUN_0021825C on 13.6: the ring every frame goes through
    callers: tuple               # every `bl transmit` site: ((site, enclosing function), ...)
    pointers: tuple              # literal-pool words holding transmit|1 (the GuiCode API table)


@memo.content_cached(copy=None)
def lcd_sender(data: bytes):
    """The LCD firmware's transmit ring and everything that calls it, located structurally:
    the payload copy by its bytes, the ring as the `bl` after the binding sender's call to the
    copy (`bl copy ; mov r0,r2 ; bl ring`, 13.6 `0x0021B9D8`-`0x0021B9DE`). None when absent."""
    import capstone
    from . import container
    try:
        sec = next(s for s in container.parse_container(data).sections if "LCD" in s.name)
    except StopIteration:                       # pragma: no cover
        return None
    lcd = bytes(data[sec.offset:sec.offset + sec.length])
    pos = lcd.find(_PAYLOAD_COPY)
    if pos < 0:
        return None
    copy = LCD_BASE + pos
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    ins = []
    p = 0
    while p < len(lcd) - 1:
        try:
            i = next(md.disasm(lcd[p:p + 4], LCD_BASE + p, count=1))
        except StopIteration:
            p += 2
            continue
        ins.append(i)
        p += i.size
    entries = [i.address for i in ins if i.mnemonic.startswith("push") and "lr" in i.op_str]
    send, ring = None, None
    for k, i in enumerate(ins[:-3]):
        if i.mnemonic == "bl" and i.op_str == "#%#x" % copy:
            for j in range(k + 1, k + 3):
                if ins[j].mnemonic == "bl":
                    send, ring = ins[j].address, int(ins[j].op_str.lstrip("#"), 16)
                    break
            break
    if ring is None:
        return None
    callers = []
    for i in ins:
        if i.mnemonic == "bl" and i.op_str == "#%#x" % ring:
            e = bisect.bisect_right(entries, i.address) - 1
            callers.append((i.address, entries[e] if e >= 0 else None))
    ptrs = tuple(LCD_BASE + o for o in range(0, len(lcd) - 3, 4)
                 if struct.unpack_from("<I", lcd, o)[0] == (ring | 1))
    return LcdSender(copy, send, ring, tuple(callers), ptrs)


# ------------------------------------------------------------------------------------ the map

@dataclass(frozen=True)
class SendMap:
    groups: tuple                # BindingGroup, every group (or those carrying a wanted DGN)
    guicode: tuple               # GuiCodeSend
    config_calls: tuple          # ConfigSetCall
    lcd: LcdSender | None
    slot: int | None             # GuiCode's sender slot


def send_map(data: bytes, dgns: frozenset | None = None, names: bool = True) -> SendMap:
    """Every way the panel transmits (module docstring); only messages on `dgns` (a frozenset of
    18-bit DGNs) when given. The config-set calls are kept when `0x1EE00` is wanted. With
    `names=False` the payload names are skipped (control captions are kept: they are cheap)."""
    groups = binding_groups(data, names)
    sends = guicode_sends(data, names)
    calls = config_set_calls(data)
    if dgns is not None:
        groups = tuple(g for g in groups if set(g.dgns) & dgns)
        sends = tuple(s for s in sends if s.dgn in dgns)
        calls = calls if DGN_CONFIG_OFFSET in dgns else ()
    return SendMap(groups, sends, calls, lcd_sender(data), sender_slot(data)[0])


GROUP_CLASSES = ("reached", "unattached", "dead", "stub")
CONTEXTS = ("touch", "touch-test", "config-set", "other")


def coverage(m: SendMap) -> dict:
    """`{dgn: Counter}`: binding entries by their group's class (`bindings`, `reached`,
    `unattached`, `dead`, `named`), and GuiCode send sites by context (`guicode`, `touch`,
    `touch-test`, `config-set`, `other`, `guicode-named`). A binding is `named` when its group's
    controls carry a caption or its payload addresses a named pin or selector."""
    out: dict = {}
    for g in m.groups:
        for e in g.entries:
            if e.stub:
                continue
            c = out.setdefault(e.dgn, Counter())
            c["bindings"] += 1
            c[g.cls] += 1
            if g.caption or e.names:
                c["named"] += 1
    for s in m.guicode:
        c = out.setdefault(s.dgn if s.dgn is not None else -1, Counter())
        c["guicode"] += 1
        c[s.context] += 1
        if s.names:
            c["guicode-named"] += 1
    return out


def _hex(v):
    return None if v is None else "%#x" % v


def control_json(c: Control) -> dict:
    return {"object": c.obj, "record": "%#x" % c.record, "touch_id": "%#x" % c.touch_id,
            "caption": c.caption, "rule": c.rule, "object_title": c.title,
            "attached": c.attached, "pages": list(c.pages)}


def _names_json(names) -> list:
    return [{"text": n.text, "source": n.source, "count": n.count, "proof": n.proof}
            for n in names]


def group_json(g: BindingGroup) -> dict:
    return {
        "group": g.group, "raw": g.raw.hex(), "class": g.cls, "caption": g.caption,
        "slice": [g.start, g.count], "count2": g.count2, "hold_ms": g.hold_ms,
        "kinds": {str(k): v for k, v in g.kinds.items()},
        "guicode_variant": g.guicode_variant,
        "controls": [control_json(c) for c in g.controls],
        "entries": [{"binding": e.index, "raw": e.raw.hex(), "stub": e.stub,
                     "dgn": None if e.stub else "%05X" % e.dgn,
                     "spec_name": None if e.stub else spec_name(e.dgn),
                     "payload": e.payload.hex(), "variant": e.variant,
                     "phases": list(e.phases), "repeats": e.repeats,
                     "source_zero": e.source_zero,
                     "what": None if e.stub else e.describe(),
                     "names": _names_json(e.names)} for e in g.entries],
    }


def send_json(s: GuiCodeSend) -> dict:
    f = s.frame
    return {
        "at": "%#x" % s.at, "function": "%#x" % s.function,
        "dgn": None if f.dgn is None else "%05X" % f.dgn, "dgn_expr": f.dgn_expr,
        "spec_name": spec_name(f.dgn), "data": f.describe(), "template": _hex(f.template),
        "exact": f.exact, "context": s.context,
        "touch": [{"id": "%#x" % i, "edge": e} for i, e in s.touch_ids],
        "controls": [control_json(c) for c in s.controls],
        "names": _names_json(s.names), "evidence": s.evidence,
    }


def call_json(c: ConfigSetCall) -> dict:
    return {"at": "%#x" % c.at, "function": "%#x" % c.function, "offset": _hex(c.offset),
            "value": c.value, "register": _hex(c.register), "option": c.option,
            "context": c.context,
            "touch": [{"id": "%#x" % i, "edge": e} for i, e in c.touch_ids],
            "controls": [control_json(x) for x in c.controls]}


def as_json(m: SendMap) -> dict:
    cov = coverage(m)
    lcd = None
    if m.lcd is not None:
        lcd = {"payload_copy": "%#x" % m.lcd.payload_copy, "transmit": "%#x" % m.lcd.transmit,
               "binding_send": "%#x" % m.lcd.binding_send,
               "callers": [{"at": "%#x" % a, "function": _hex(f)} for a, f in m.lcd.callers],
               "pointers": ["%#x" % p for p in m.lcd.pointers]}
    return {
        "sender_slot": _hex(m.slot), "lcd": lcd,
        "groups": [group_json(g) for g in m.groups],
        "guicode": [send_json(s) for s in m.guicode],
        "config_set_calls": [call_json(c) for c in m.config_calls],
        "coverage": {("%05X" % d) if d >= 0 else "?": dict(c) for d, c in sorted(cov.items())},
    }
