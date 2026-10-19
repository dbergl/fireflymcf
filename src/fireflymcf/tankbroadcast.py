"""The G12's `1FFB7` TANK_STATUS broadcast, and the per-rule switch that turns one off.

**Why this exists.** With no probe wired to a tank input the ADC floats to rail (`0xFFF0`),
which is above every threshold in the `CC`/`CD`/`CE` bucketing ladder (`G12-CODE.md`), so the
G12 broadcasts a perfectly well-formed `1FFB7` TANK_STATUS reading **0%** once a second for
instances 0/1/2/18. RV-C receivers cannot tell that from a real empty tank, and the panel's own
receive filter is **source-blind** -- 18-bit DGN plus a masked payload compare and nothing about
who sent the frame -- so a second, real tank sensor on the same instance does not win, it
*alternates*, at 1 Hz (the `BUS-BASELINE.md` PC.46 failure mode). Silencing the G12 is therefore
a precondition for fitting a different RV-C tank sender, not a tidy-up.

## The mechanism -- `proven-from-code`, 2026-09-07

The `0x67` blob is handed a context struct in `r4` whose function-pointer slots drive the rule
engine. Three of them appear at 100 call sites and the pairing is unambiguous:

    [r4,#0x08](N)   activate rule N        [r4,#0x0C](N)   deactivate rule N
    [r4,#0x10](N)   is rule N active?

The oracle is rule #93 (13.6), `1FFB7` instance 3 (LPG): activated at `+0x18F6`, deactivated at
`+0x19AC` -- and that is exactly the broadcast the coach is observed starting and stopping with
the `0xEF` mode write (`MCF-FORMAT.md` §9a.3, "A mode gate seen on the way"). Two call sites,
one rule, and the wire showing it going on and off. Sharper still, the black-tank gate reuses a
single call instruction for **both** directions: `+0x184C` loads the *deactivate* pointer and
branches back into the `blx` at `+0x111E`. A helper that could be read as "fire this rule once"
cannot be reached that way.

So on 13.6 the four water/waste broadcasts split into two shapes:

| rule | instance | tank | activation | gate |
|---|---|---|---|---|
| #45 | 0 | fresh | `+0x08DA` | **none** -- config-apply, never deactivated |
| #46 | 2 | grey | `+0x08E0` | **none** -- config-apply, never deactivated |
| #47 | 1 | black | `+0x111E` | `cfg[0xEC] == 1`, re-read every tick |
| #48 | 18 | grey 2 | `+0x1144` | floorplan `cfg[0xF5]` in {2,3,7,8} |

`0xEC` is the **Black Tank** OPTIONS row (`interlocks.option_for_selector`), which is why
instance 1 alone can be silenced over the bus with no patch at all --
`19FED99F#FF96EC0F0000D1EA`, an absolute `D1 EA` set observed 9x in the captures. That lever
also **hides the HOME BLACK gauge** (slot 112 keys a type-6 switch, base 801 -> 802), so it is
not equivalent to this edit: the NOP silences the broadcast and leaves the gauge drawing.
Instances 0 and 2 have no config gate anywhere in the blob and are patch-only.

## The edit

`suppress` NOPs the `blx r3` that activates each requested rule -- 2 bytes per instance, same
size, nothing shifts. The rule record itself is left alone, so the image still *describes* the
broadcast and the change is one byte-pair per instance to revert. It is confined to the G12
section, which is **independently flashable**: the lowest-risk build shape this project has.

Nothing is hardcoded. The rule indices come from table `0x62` by DGN, and the activation sites
are found by scanning for the idiom at instruction boundaries the project's own recursive-descent
walker (`g12code.analyse`) established -- so a build whose blob is shaped differently yields no
site and is refused rather than mis-patched. Validated across all three builds: the twelve-byte
`1FFB7` records are **byte-identical** on 11.23, 13.6 and 14.7 (same kind `0x81`, same mask, same
resolution 3, same selectors `B5`/`B6`/`B7`/`B8`, same instances) and only their index shifts.

**What this does NOT touch**, deliberately: `1FFB7` instance 3 (LPG) has a real sender and
manages itself through the mode gate; `0BFC1` (G12_TANK_LEVEL_SENSOR, the raw ADC that the
`PROBE TANK DIAGNOSTICS` page reads) and `1AAB7` (Firefly's private TANK_STATUS mirror) are
proprietary DGNs that collide with nothing standard.

**The limit of the claim.** The call sites and their pairing are read from these bytes. The
helpers themselves are function pointers into the G12 application at `0x08006000`, which this
project has never dumped -- so "activate" and "deactivate" are named from the call-site shape
plus the #93 wire oracle, not from reading the callee.
"""
from __future__ import annotations

from dataclasses import dataclass

import capstone

from . import g12_config, g12code, thumb

TANK_STATUS_DGN = 0x1FFB7
RULE_TABLE = 0x62

# Context-struct slots. Named from the call-site shape and the rule-#93 wire oracle; see the
# module docstring for the limit on that claim.
ACTIVATE_SLOT = 8
DEACTIVATE_SLOT = 0xC
QUERY_SLOT = 0x10

# RV-C TANK_STATUS instances (rvc2mqtt `rvc-spec.yml`). The G12 broadcasts these four plus 3.
INSTANCE_NAMES = {0: "fresh water", 1: "black waste", 2: "grey waste", 3: "lpg",
                  16: "second fresh water", 17: "second black waste",
                  18: "second grey waste", 19: "second lpg"}
# What `suppress` silences unless asked otherwise: every water/waste tank, never the LPG (3),
# whose sender is real and whose rule already switches itself off in AES mode.
DEFAULT_INSTANCES = (0, 1, 2, 18)


class TankBroadcastError(Exception):
    """The blob did not match the shape this module proves from the bytes."""


@dataclass(frozen=True)
class TankBroadcast:
    """One `1FFB7` rule record and the instruction that switches it on."""
    rule_index: int         # index within rule table 0x62
    instance: int           # RV-C TANK_STATUS instance, payload[0]
    selector: int           # config cell substituted into payload[1] (the bucketed level)
    rule_file_offset: int   # file offset of the 12-byte record
    activate_at: int        # blob address of the `blx r3`; None if nothing switches this rule on
    activate_file: int      # file offset of the same, or None
    queried_at: tuple       # blob addresses that ask whether this rule is active
    deactivated_at: tuple   # blob addresses that call the deactivate slot for this rule

    @property
    def guarded(self) -> bool:
        """Activation sits behind an is-active query -- the shape of a per-tick, gated site.

        A rule switched on once at config-apply needs no such query; one re-evaluated every
        tick does, so that the engine is not told to activate an already-active rule 1x/s.
        Both 13.6 sites that carry a config gate have one and neither ungated site does."""
        return bool(self.queried_at)

    @property
    def tank(self) -> str:
        return INSTANCE_NAMES.get(self.instance, f"instance {self.instance}")


def blob_file_offset(data: bytes) -> int:
    """Where table `0x67` starts in the file (`g12code.blob` returns the bytes, not the place)."""
    g = g12_config.parse_g12(data)
    table = g.table(g12code.TABLE_TAG)
    if table is None or not table.size:
        raise TankBroadcastError("this build has no table 0x67 (G12 code blob)")
    return g.file_offset + table.offset


def _decode(body: bytes, starts) -> dict:
    """`{addr: (mnemonic, op_str, size)}` at the walker's instruction boundaries only.

    Decoding *only* where `g12code.analyse` says an instruction begins is what keeps this off
    the literal pools -- a linear sweep cannot tell them from code and desyncs (`g12code`'s own
    module docstring). Reusing that walker rather than writing a second one is deliberate.
    """
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    out = {}
    for addr in sorted(starts):
        for insn in md.disasm(bytes(body[addr:addr + 4]), addr, count=1):
            out[addr] = (insn.mnemonic, insn.op_str, insn.size)
    return out


def _engine_calls(body: bytes, starts) -> list:
    """Every `blx rN` whose target came from `[r4,#slot]`, with the rule index in r0.

    Returns `(addr, slot, rule_index_or_None)`. The rule index is read from the nearest
    preceding write to r0 -- `movs r0,#N` for a plain call, or `adds r0,#N` where the callee
    convention leaves r0 == 0 (the firmware's own trick at `+0x111C`: the query returned 0, so
    `adds r0,#0x2f` reconstructs the index without a second `movs`).
    """
    ins = _decode(body, starts)
    addrs = sorted(ins)
    index = {a: i for i, a in enumerate(addrs)}
    calls = []
    for addr in addrs:
        mnemonic, ops, _size = ins[addr]
        if mnemonic != "blx" or not ops.startswith("r") or "," in ops:
            continue
        reg = ops
        slot = None
        for back in range(1, 8):                      # the `ldr` is 1-3 instructions back
            prev = addrs[index[addr] - back] if index[addr] >= back else None
            if prev is None:
                break
            pm, po, _ = ins[prev]
            if pm == "ldr" and po.startswith(f"{reg}, [r4"):
                tail = po.split("[r4", 1)[1].strip("], ")
                slot = int(tail.lstrip("#"), 0) if tail else 0
                break
        if slot is None:
            continue
        rule = None
        for back in range(1, 17):
            prev = addrs[index[addr] - back] if index[addr] >= back else None
            if prev is None:
                break
            pm, po, _ = ins[prev]
            if pm in ("movs", "adds") and po.startswith("r0, #"):
                rule = int(po.split("#", 1)[1], 0)
                break
            if po.startswith("r0,") or po.startswith("r0 "):
                break                    # r0 last written by something we cannot read: give up
        calls.append((addr, slot, rule))
    return calls


def tank_broadcasts(data: bytes) -> list:
    """Every `1FFB7` TANK_STATUS rule on this build, with the site that switches it on."""
    records = [r for r in g12_config.rule_records(data)
               if r.table == RULE_TABLE
               and (r.dgn_lo | ((r.raw[2] & 0x03) << 16)) == TANK_STATUS_DGN]
    if not records:
        raise TankBroadcastError("no 1FFB7 TANK_STATUS rule in table 0x62 on this build")

    section_offset = g12_config.parse_g12(data).file_offset
    body = g12code.blob(data)
    calls = _engine_calls(body, g12code.analyse(data).starts)
    base = blob_file_offset(data)

    out = []
    for rec in records:
        activations = [(a, s, n) for a, s, n in calls
                       if n == rec.index and s == ACTIVATE_SLOT]
        deactivations = tuple(a for a, s, n in calls
                              if n == rec.index and s == DEACTIVATE_SLOT)
        # Zero is a real answer -- it is what a suppressed image looks like, and it is how
        # `suppressed_instances` reads one back. More than one is not: the edit would have to
        # pick, and this module does not guess.
        if len(activations) > 1:
            raise TankBroadcastError(
                f"rule #{rec.index} (1FFB7 instance {rec.raw[7]}) has {len(activations)} "
                f"activation sites, expected at most one -- refusing to guess")
        addr = activations[0][0] if activations else None
        out.append(TankBroadcast(
            rule_index=rec.index,
            instance=rec.raw[7],
            selector=rec.raw[6],
            rule_file_offset=section_offset + rec.offset,
            activate_at=addr,
            activate_file=None if addr is None else base + addr,
            queried_at=tuple(a for a, s, n in calls
                             if n == rec.index and s == QUERY_SLOT),
            deactivated_at=deactivations))
    return sorted(out, key=lambda b: b.instance)


def suppress(data: bytes, instances=DEFAULT_INSTANCES) -> tuple:
    """NOP the activation of each named `1FFB7` instance. Returns `(image, edits)`; not resealed.

    Two bytes per instance, guarded on the site really being the `blx r3` the scan found.
    """
    wanted = tuple(dict.fromkeys(instances))
    found = {b.instance: b for b in tank_broadcasts(data)}
    missing = [i for i in wanted if i not in found]
    if missing:
        raise TankBroadcastError(
            f"this build broadcasts no 1FFB7 instance(s) {missing}; it has "
            f"{sorted(found)}")

    out = bytearray(data)
    edits = []
    for instance in wanted:
        b = found[instance]
        if b.activate_at is None:
            raise TankBroadcastError(
                f"instance {instance}: nothing in this blob switches rule #{b.rule_index} on -- "
                f"already suppressed, or a build this module has not read")
        off = b.activate_file
        old = bytes(data[off:off + 2])
        if old != thumb.blx_reg(3):
            raise TankBroadcastError(
                f"instance {instance}: expected `blx r3` at file {off:#x} (blob "
                f"+{b.activate_at:#06x}), found {old.hex()} -- already patched, or a build "
                f"this module has not read")
        thumb.assert_disassembles_to(old, b.activate_at, ["blx r3"])
        out[off:off + 2] = thumb.NOP2
        edits.append((off, old, thumb.NOP2,
                      f"1FFB7 TANK_STATUS instance {instance} ({b.tank}): NOP the activation of "
                      f"rule #{b.rule_index} at blob +{b.activate_at:#06x}; the G12 no longer "
                      f"broadcasts it, freeing the instance for another RV-C sender"))
    return bytes(out), edits


def suppressed_instances(data: bytes) -> list:
    """Which `1FFB7` instances this image never switches on -- the inverse of `suppress`.

    Read as "no instruction activates this rule", not "our NOP is present": a build that simply
    lacks the activation reports the same, which is the honest answer either way, since the
    consequence -- the G12 never broadcasts that instance -- is identical.
    """
    return [b.instance for b in tank_broadcasts(data) if b.activate_at is None]


def describe(data: bytes) -> str:
    lines = ["1FFB7 TANK_STATUS broadcasts (G12 rule table 0x62)", ""]
    quiet = set(suppressed_instances(data))
    for b in tank_broadcasts(data):
        gate = ("gated (a [r4,#0x10] query precedes)" if b.guarded
                else "UNCONDITIONAL -- no config gate")
        if b.deactivated_at:
            gate += "; deactivated at " + ", ".join(f"+{a:#06x}" for a in b.deactivated_at)
        where = ("never activated" if b.activate_at is None else
                 f"activate blob +{b.activate_at:#06x} (file {b.activate_file:#08x})")
        lines.append(
            f"  rule #{b.rule_index:<3} instance {b.instance:<3} {b.tank:<18} "
            f"level cell {b.selector:#04x}  {where}  "
            f"{'SUPPRESSED' if b.instance in quiet else gate}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------- audit
#
# `tank_broadcasts` reads a table, and a table read carries anywhere. The activation site does
# not: it is a structural scan over an idiom, validated on the three images this project owns.
# So before patching a build nobody here has seen, `audit` asks whether the shape the scan
# assumes is really this image's shape, and reports its evidence rather than its verdict alone.

# The twelve-byte record is `<dgn_lo:2> <kind|dgn_hi> <mask> <payload[7,6,5,4,11,10,9,8]>`.
# Bytes 0-4 and 8-11 are identical for every 1FFB7 rule on every corpus build; the three that
# vary are the resolution, the level cell and the instance.
CANONICAL_HEAD = bytes.fromhex("b7ff8102ff")
CANONICAL_TAIL = bytes.fromhex("ffffffff")

# What the corpus ships, per instance -- byte-identical on 11.23, 13.6 and 14.7 despite the
# rule indices shifting on every build. A new image matching these is the same rule-engine
# generation; one that does not may still be patchable, but nothing here has seen its shape.
KNOWN_RECORDS = {
    0: bytes.fromhex("b7ff8102ff03b500ffffffff"),
    1: bytes.fromhex("b7ff8102ff03b701ffffffff"),
    2: bytes.fromhex("b7ff8102ff03b602ffffffff"),
    3: bytes.fromhex("b7ff8102ff64c203ffffffff"),
    18: bytes.fromhex("b7ff8102ff03b812ffffffff"),
}

# The recursive-descent walker reaches 92.3% / 94.4% / 95.0% of the blob on 11.23 / 13.6 / 14.7.
# Below this floor it did not understand the blob, and an idiom scan over what it did reach is
# not evidence about what it did not.
MIN_BLOB_COVERAGE = 0.85


@dataclass(frozen=True)
class Signal:
    """One check, its verdict, and the evidence behind it."""
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Audit:
    broadcasts: tuple
    signals: tuple
    family_slot: int        # slot most of the tank rules are called through; None if none are
    modal_slot: int         # the most-used engine-call slot in the whole blob
    coverage: float         # what fraction of the blob the walker claimed

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.signals)

    @property
    def failures(self) -> list:
        return [s for s in self.signals if not s.ok]

    def signal(self, name: str) -> Signal:
        for s in self.signals:
            if s.name == name:
                return s
        raise KeyError(name)

    def report(self) -> str:
        lines = ["Build audit -- does this image match the shape the patcher reads?", ""]
        for s in self.signals:
            lines.append(f"  [{'PASS' if s.ok else 'FAIL'}] {s.name:<17} {s.detail}")
        lines.append("")
        lines.append("  every signal passed" if self.ok else
                     "  " + str(len(self.failures)) + " signal(s) failed -- see --help on --force")
        return "\n".join(lines)


def _rule_slots(data: bytes, broadcasts) -> dict:
    """`{rule_index: {slot: [addr, ...]}}` for the `1FFB7` rules only."""
    calls = _engine_calls(g12code.blob(data), g12code.analyse(data).starts)
    wanted = {b.rule_index for b in broadcasts}
    out = {i: {} for i in wanted}
    for addr, slot, rule in calls:
        if rule in wanted:
            out[rule].setdefault(slot, []).append(addr)
    return out


def _modal_slot(data: bytes):
    """The vtable slot the whole blob calls most often -- an activate-heavy program's plurality.

    Independent of the tank rules, so it still answers on an image whose tank activations have
    already been NOPed out.
    """
    counts = {}
    for _addr, slot, _rule in _engine_calls(g12code.blob(data), g12code.analyse(data).starts):
        counts[slot] = counts.get(slot, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    winners = [s for s, n in counts.items() if n == best]
    return winners[0] if len(winners) == 1 else None


def _family_slot(slots, live, modal):
    """The one vtable slot every live tank rule is called through.

    Start from the intersection, because `suppress` needs a slot that is present for *every*
    rule it will patch -- a plurality would let one rule be driven through something else. On
    a build where all the tank rules are gated the intersection holds two slots (the activate
    and the is-active query, e.g. 14.7), so the blob-wide plurality picks between them: an
    engine program activates far more rules than it interrogates.
    """
    if not live:
        return None
    shared = set.intersection(*(set(slots[b.rule_index]) for b in live))
    if len(shared) == 1:
        return next(iter(shared))
    return modal if modal in shared else None


def audit(data: bytes) -> Audit:
    """Check this image against the shape `suppress` assumes, and show the evidence."""
    try:
        broadcasts = tank_broadcasts(data)
    except TankBroadcastError:
        raise
    except Exception as e:
        raise TankBroadcastError(
            f"this file could not be read as a Firefly MCF container ({e}) -- pass the whole "
            f"mcf.bin, not an extracted section")

    signals = []

    # 1. The record's fixed fields. Everything a TANK_STATUS rule does not vary.
    bad = [b for b in broadcasts
           if data[b.rule_file_offset:b.rule_file_offset + 5] != CANONICAL_HEAD
           or data[b.rule_file_offset + 8:b.rule_file_offset + 12] != CANONICAL_TAIL]
    signals.append(Signal(
        "rule-shape", not bad,
        f"{len(broadcasts)} 1FFB7 rule(s), all kind 0x81 (1 Hz periodic) with mask 0x02"
        if not bad else
        "instance(s) " + ", ".join(str(b.instance) for b in bad) +
        " do not carry the fixed TANK_STATUS record fields"))

    # 2. Byte-identity with the corpus. Softer: a build may legitimately move a level cell.
    differs = []
    for b in broadcasts:
        known = KNOWN_RECORDS.get(b.instance)
        raw = bytes(data[b.rule_file_offset:b.rule_file_offset + 12])
        if known is not None and raw != known:
            differs.append((b.instance, raw.hex()))
    unknown = sorted({b.instance for b in broadcasts} - set(KNOWN_RECORDS))
    signals.append(Signal(
        "known-records", not differs,
        ("byte-identical to 11.23/13.6/14.7"
         + (f"; instance(s) {unknown} are new here" if unknown else ""))
        if not differs else
        "; ".join(f"instance {i} reads {h}, not the corpus record" for i, h in differs)))

    # 3. One activation site per rule, or none. More than one and `suppress` would have to
    #    choose, which it refuses to do -- `tank_broadcasts` already raises on that, so reaching
    #    here means the invariant held; report how many are live.
    live = [b for b in broadcasts if b.activate_at is not None]
    signals.append(Signal(
        "activation-sites", True,
        f"{len(live)} of {len(broadcasts)} rule(s) are switched on by exactly one `blx`"
        + ("" if len(live) == len(broadcasts) else
           "; instance(s) " + ", ".join(str(b.instance) for b in broadcasts
                                        if b.activate_at is None) + " already suppressed")))

    # 4/5. Two independent routes to the activate slot, both of which must land on the constant
    #      `suppress` patches through.
    modal = _modal_slot(data)
    slots = _rule_slots(data, broadcasts)
    family = _family_slot(slots, live, modal)
    if not live:
        # Every tank activation has been NOPed out, so there is no family evidence left to
        # read. That is what a correctly patched build looks like, not a damaged one.
        signals.append(Signal("activate-slot", True,
                              "no tank rule is switched on -- already suppressed, so the family "
                              "derivation has nothing to read"))
    else:
        shared = sorted(set.intersection(*(set(slots[b.rule_index]) for b in live)))
        signals.append(Signal(
            "activate-slot", family == ACTIVATE_SLOT,
            f"every one of the {len(live)} live tank rule(s) is called through [r4,#{family:#x}]"
            + ("" if len(shared) == 1 else
               f" (of {', '.join(hex(s) for s in shared)}, picked by the blob-wide plurality)")
            if family is not None else
            "the live tank rules share no single vtable slot -- cannot tell activate from "
            "deactivate on this build"))

    signals.append(Signal(
        "slot-plurality", modal == ACTIVATE_SLOT,
        f"[r4,#{modal:#x}] is the blob's most-called engine slot"
        if modal is not None else "no engine calls found in this blob"))

    analysis = g12code.analyse(data)
    coverage = analysis.coverage
    signals.append(Signal(
        "blob-coverage", coverage >= MIN_BLOB_COVERAGE,
        f"the walker claimed {coverage:.1%} of the {analysis.size}-byte blob "
        f"(corpus floor {MIN_BLOB_COVERAGE:.0%})"))

    return Audit(broadcasts=tuple(broadcasts), signals=tuple(signals),
                 family_slot=family, modal_slot=modal, coverage=coverage)
