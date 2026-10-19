"""How a control behaves when you touch it: tap, hold, ramp, repeat, momentary.

This is the *interaction* layer, distinct from `guidata.find_control_groups`, which answers
"what channels does this control drive". Two fields carry the whole model and both were
`unknown` in `MCF-FORMAT.md` before 2026-08-19:

  * **group entry byte 0 / byte 2** -- the hold threshold, the variant cycle length, and
    three repeat rates.
  * **binding entry byte 3** -- a 4-bit mask of the gesture phases on which that one CAN
    frame is sent.

The consumer is in the **LCD Firmware**, not GuiCode: `FUN_0021BAB4` (0x0021BAB4) is the
per-tick control scanner that runs the phase machine, and `FUN_0021B8B4` (0x0021B8B4) is the
sender that walks a group's binding slice and tests byte 3. Both are cited line by line in
`docs/CONTROL-BEHAVIOR.md`. Everything this module reports is a transcription of those two
functions; nothing here is fitted to an expected behaviour.

**Scope, stated because it is easy to over-read.** A control whose touch id is >= the group
count is not described here at all: those ids go to GuiCode's action dispatcher instead, which
runs its own hold timers off `GuiCodeState+0x204` (see `guicode_hold_ms`). `describe_group`
covers the binding-group class only.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import guidata, relocate

# ---------------------------------------------------------------------------
# The phase machine (LCD Firmware FUN_0021BAB4)
# ---------------------------------------------------------------------------
#
# Input level for a control is read once per tick. A 0->1 transition sets phase 1; a 0->2
# transition sets phase 5. While the phase is 1 or 5 a deadline (now + hold_ms) runs, and
# on expiry the phase advances to 3 or 7. Release maps 1->2, 3->4, 5->6, 7->8.
#
#   0x21bd72  strb sl,[r1,#9]      phase = 1        (level became 1)
#   0x21bda2  movs r3,#5           phase = 5        (level became 2)
#   0x21bdb0  movs r2,#3           phase = 3        (hold deadline expired, from 1)
#   0x21bdb8  movs r2,#7           phase = 7        (hold deadline expired, from 5)
#   0x21bc58  strb r1(#2),[r3,#9]  phase = 2        (released from 1)
#   0x21bcb6  strb r1(#4),[r3,#9]  phase = 4        (released from 3)
#   0x21bcf4  strb r1(#6),[r3,#9]  phase = 6        (released from 5)
#   0x21bd2e  strb r1(#8),[r3,#9]  phase = 8        (released from 7)
#
# The sender's `tbb` at 0x21b95c maps phase-1 to the byte-3 bit to test, and its table is
# `04 4b 0a 4f 04 4b 0a 4f` -- phases 5..8 reuse phases 1..4's arms exactly, which is why
# one 4-bit mask covers all eight phases.
#
#   arm 0x21b968  tst r2,#1     phases 1, 5
#   arm 0x21b9f6  tst r2,#2     phases 2, 6
#   arm 0x21b974  lsls r2,#0x1d phases 3, 7   (bit 2)
#   arm 0x21b9fe  tst r2,#8     phases 4, 8

PHASE_PRESS = 0x01        # sent the moment the control goes down
PHASE_TAP = 0x02          # sent on release BEFORE the hold threshold
PHASE_HELD = 0x04         # sent once the hold threshold passes, and on every repeat after
PHASE_RELEASE_HELD = 0x08  # sent on release AFTER the hold threshold

PHASE_NAMES = {
    PHASE_PRESS: "press",
    PHASE_TAP: "tap-release",
    PHASE_HELD: "held",
    PHASE_RELEASE_HELD: "release-after-hold",
}

# Repeat intervals, in milliseconds. A 2-bit selector indexes this table; index 0 means the
# frame is sent once and not repeated. Read from the LCD Firmware at 0x0021F2A2, the pointer
# loaded at 0x21bbd0 and indexed by `ldrh r7,[r2,r3,lsl #1]` at 0x21bbd2.
REPEAT_MS = (0, 250, 500, 1000)

# The hold threshold is `1000 * (group_byte0 >> 4)` milliseconds -- `fp = 0x3e8` at 0x21bd7c
# and `mla r0,fp,r3,r0` at 0x21bd86, where r3 is the group entry's byte 0 shifted right 4.
HOLD_MS_PER_UNIT = 1000

# Binding byte 2, bit 2. The sender skips an entry that lacks it once the control has already
# fired at least once for this phase (`ldrb r1,[r7,#0xc]` / `lsls r1,r2,#0x1d` / `bpl` at
# 0x21b94c-0x21b952; the "already fired" flag is set at 0x21bba6 right after each send).
# So the bit means "send me on repeats too", not just on the first emission of the phase.
BINDING_REPEATS = 0x04


@dataclass(frozen=True)
class Emission:
    """One binding entry, read as "when is this frame sent"."""
    binding_index: int
    dgn: int
    phases: int            # bitmask of PHASE_*
    repeats: bool          # byte 2 bit 2: also sent on the repeat ticks, not just the first
    variant: int           # byte 2 high nibble: which variant copy this belongs to
    command: int           # CAN data byte 3 (the RV-C command for DC_DIMMER_COMMAND_2)
    level: int             # CAN data byte 2
    channel: int | None    # CAN data byte 0 (instance), for DC_DIMMER_COMMAND_2 only

    @property
    def phase_names(self) -> list[str]:
        return [PHASE_NAMES[b] for b in (1, 2, 4, 8) if self.phases & b]


@dataclass(frozen=True)
class GroupBehaviour:
    """The interaction model of one control group."""
    group: int
    hold_ms: int              # how long before "held" starts
    variant_count: int        # byte 0 low nibble: the per-control counter's wrap value
    cycles_variant: bool      # byte 1 bits 6-7 == 0x80 disables advancing it on release. NOT
                              # the same as "the variant never changes": external setters at
                              # 0x0021B60A / 0x0021B694 also write it, so a group that does not
                              # cycle still has its variant chosen by something -- see
                              # `docs/CONTROL-BEHAVIOR.md` section 5.
    advances_global: bool     # byte 3 bit 7: also step the shared/global variant counter
    repeat_press_ms: int      # byte 2 bits 0-1: repeat rate while in phase 1/5
    repeat_held_ms: int       # byte 2 bits 2-3: repeat rate while in phase 3/7
    repeat_release_ms: int    # byte 2 bits 4-5: repeat rate in the release phases
    emissions: tuple          # tuple[Emission, ...]
    raw: bytes = b""

    def on(self, phase: int, variant: int | None = None) -> list:
        """Every emission that fires on `phase` (a single PHASE_* bit).

        `variant=None` means "any variant"; pass an integer to restrict to one copy.
        """
        return [e for e in self.emissions
                if e.phases & phase and (variant is None or e.variant == variant)]

    @property
    def variants(self) -> tuple:
        """The variant numbers this group actually carries, ascending."""
        return tuple(sorted({e.variant for e in self.emissions}))

    def kind_for_variant(self, variant: int) -> str:
        """The interaction pattern of one variant copy, from its phase masks alone.

        Deliberately coarse and deliberately not channel-aware: it describes the *gesture*,
        which is what this module decodes. Anything that matches no named pattern comes back
        as "other" rather than being forced into one.
        """
        emissions = [e for e in self.emissions if e.variant == variant]
        if not emissions:
            return "unbound"
        phases = 0
        for e in emissions:
            phases |= e.phases
        press = bool(phases & PHASE_PRESS)
        tap = bool(phases & PHASE_TAP)
        held = bool(phases & PHASE_HELD)
        rel = bool(phases & PHASE_RELEASE_HELD)
        # Order matters: the awning carries all four phases, and it is the `press` arm that
        # makes it momentary rather than a light's tap-or-hold. Test the most specific first.
        if press and held and rel:
            return "momentary"            # awning: drives only while you hold it
        if tap and held:
            return "tap-or-hold"          # lights: tap toggles, hold ramps, release stops
        if held and not (press or tap):
            return "hold-only"            # fires once the threshold passes
        if rel and not (press or tap or held):
            return "hold-release"         # fires only when a completed hold is released
        if tap and rel:
            return "latch"                # fires on ANY release, held or not
        if tap:
            return "tap-only"             # fires on a short tap only; a hold does nothing
        if press:
            return "press-only"
        return "other"

    @property
    def kind(self) -> str:
        """The interaction pattern of **variant 0**, the base copy.

        Variants are not cosmetic: group 0 on 13.6 fires on `held` in variant 0 and on
        `press` in variant 1, so a single name for the whole group would be a fiction. Which
        variant is live is a runtime counter (module docstring, `docs/CONTROL-BEHAVIOR.md`
        section 5), so this reports the base copy and `kinds` exposes the rest.
        """
        return self.kind_for_variant(0) if self.emissions else "unbound"

    @property
    def kinds(self) -> dict:
        """`{variant: kind}` for every variant copy this group carries."""
        return {v: self.kind_for_variant(v) for v in self.variants}

    @property
    def variants_agree(self) -> bool:
        """Whether every variant copy implements the same gesture."""
        return len(set(self.kinds.values())) <= 1


def _emission(index: int, raw: bytes) -> Emission:
    dgn = raw[0] | (raw[1] << 8)
    return Emission(
        binding_index=index,
        dgn=dgn,
        phases=raw[3] & 0x0F,
        repeats=bool(raw[2] & BINDING_REPEATS),
        variant=raw[2] >> 4,
        command=raw[4],
        level=raw[5],
        channel=raw[7] if dgn == guidata.DGN_DC_DIMMER_COMMAND_2 else None,
    )


def describe_group(data: bytes, index: int) -> GroupBehaviour:
    """Decode group `index`'s interaction model. Raises IndexError if it does not exist."""
    groups = guidata.group_table(data)
    bindings = guidata.binding_table(data)
    g = groups[index]
    b0, b1, b2, b3 = g.raw[0], g.raw[1], g.raw[2], g.raw[3]
    entries = bindings[g.start:min(g.start + g.count, len(bindings))]
    emissions = tuple(_emission(e.index, e.raw) for e in entries if e.raw[:2] != b"\xff\xff")
    return GroupBehaviour(
        group=index,
        hold_ms=(b0 >> 4) * HOLD_MS_PER_UNIT,
        variant_count=b0 & 0x0F,
        cycles_variant=(b1 & 0xC0) != 0x80,
        advances_global=bool(b3 & 0x80),
        repeat_press_ms=REPEAT_MS[b2 & 3],
        repeat_held_ms=REPEAT_MS[(b2 >> 2) & 3],
        repeat_release_ms=REPEAT_MS[(b2 >> 4) & 3],
        emissions=emissions,
        raw=bytes(g.raw),
    )


def all_groups(data: bytes) -> list:
    """`describe_group` for every group in the file, in index order."""
    return [describe_group(data, i) for i in range(len(guidata.group_table(data)))]


def touch_ids(data: bytes, obj_index: int, table: list | None = None) -> list:
    """The touch-region ids a screen object registers, in record order.

    A widget of type 12 carries its id as the last `u16` of its record. That is R50 in the
    decoder (`records.decode_record`, mask `aed & 0x1000`, i.e. type 12 exactly), and because
    type 12 takes neither R51 nor the trailing string loop the field is always the record's
    final two bytes. GuiCode copies it into the hit-region table at `state+0x7084`
    (`FUN_00256B84`, `strh r1,[r3,#0x7084]` @0x256bf4) and the hit test returns it
    (`ldrsh r6,[r2,#0x7084]` @0x256bc6).

    `table`, if given, is the pre-resolved screen/object table (`guidata.object_tables(
    data)[1]`, i.e. `layout.Screen.table`) and skips the full-section rescan -- the same
    resolve-once-and-thread-through shape `metrics.text_width` takes, for callers asking
    about many objects of one unmutated `data` in a single pass.
    """
    from . import records
    gs, _ = guidata.guidata_extent(data)
    if table is None:
        table = guidata.object_tables(data)[1]
    out = []
    for rec in records.object_records(data, gs + table[obj_index]):
        if rec.wtype == 12:
            out.append(data[rec.end - 2] | (data[rec.end - 1] << 8))
    return out


# `GuiCodeState+0x20A` holds a SCREEN ID, and it is the object-table index minus this.
#
# Established from the nav rail, which is unambiguous: touch ids 0x7D0/0x7D2/0x7D3/0x838 write
# screen 0/2/3/4 (`0x0024C69C`, `0x0024C6A6`, `0x0024C6B0`, `0x0024C8BC`), and objects 4/6/7/8
# are HOME / LIGHTS / CLIMATE CONTROL / SETTINGS -- the rail in order. Every one of the 43
# values GuiCode writes lands on a distinctly-named screen object under this offset.
#
# Worth stating because the mistake is easy and was made: reading the value AS an object index
# is off by four, which silently renames every screen and produced a wrong list of unreachable
# ones. `docs/CONTROL-BEHAVIOR.md` section 1.
SCREEN_ID_TO_OBJECT = 4


def screen_object(screen_id: int) -> int:
    """The object-table index a `state+0x20A` screen id selects."""
    return screen_id + SCREEN_ID_TO_OBJECT


def is_group_id(data: bytes, touch_id: int) -> bool:
    """Whether `touch_id` is dispatched as a binding group rather than a GuiCode action.

    The LCD Firmware's scanner bounds its loop with the group count (`ldrb r3,[r8]` /
    `cmp r3,r4` / `bhi` at 0x21bad4-0x21bada), so an id at or above that count can never
    reach the phase machine and is handled by GuiCode's action dispatcher instead.
    """
    return 0 <= touch_id < len(guidata.group_table(data))


# ---------------------------------------------------------------------------
# Writers -- same-size, two bytes, with the premises asserted rather than assumed
# ---------------------------------------------------------------------------

class GestureEditError(ValueError):
    """A premise this edit depends on does not hold for the target group."""


MAX_HOLD_SECONDS = 0x0F        # the threshold is a nibble


def set_hold_seconds(data: bytes, group: int, seconds: int):
    """Rewrite group `group`'s hold threshold. One byte, same size.

    The threshold is the HIGH nibble of the group entry's byte 0; the low nibble is the
    variant wrap and is preserved. Returns `(data, [Edit])`.
    """
    if not 0 <= seconds <= MAX_HOLD_SECONDS:
        raise GestureEditError(
            f"hold of {seconds}s does not fit the nibble (0..{MAX_HOLD_SECONDS})")
    gs, _ = guidata.guidata_extent(data)
    entry = guidata.group_table(data)[group]
    off = gs + entry.offset
    old = data[off]
    new = (old & 0x0F) | (seconds << 4)
    if new == old:
        return data, []
    out = bytearray(data)
    out[off] = new
    return bytes(out), [relocate.Edit(off, bytes([old]), bytes([new]), "group-hold",
                                      f"group {group} hold {old >> 4}s -> {seconds}s")]


def set_phases(data: bytes, binding_index: int, phases: int):
    """Rewrite one binding entry's gesture-phase mask (byte 3). One byte, same size.

    `phases` is an OR of `PHASE_*`. Returns `(data, [Edit])`.
    """
    if not 0 < phases <= 0x0F:
        raise GestureEditError(f"phase mask {phases:#x} is not a non-empty 4-bit mask")
    gs, _ = guidata.guidata_extent(data)
    entry = guidata.binding_table(data)[binding_index]
    off = gs + entry.offset + 3
    old = data[off]
    if old == 0xFF:
        raise GestureEditError(
            f"binding {binding_index} is an all-0xFF stub, not a live entry")
    if old == phases:
        return data, []
    out = bytearray(data)
    out[off] = phases
    return bytes(out), [relocate.Edit(off, bytes([old]), bytes([phases]), "binding-phases",
                                      f"binding {binding_index} phases {old:#04x} -> "
                                      f"{phases:#04x}")]


_REPEAT_FIELDS = {"press": 0, "held": 2, "release": 4}    # bit position in group byte 2


def set_repeat_index(data: bytes, group: int, phase: str, index: int):
    """Rewrite one of group `group`'s three 2-bit repeat-rate selectors. One byte, same size.

    `phase` is "press", "held" or "release"; `index` selects `REPEAT_MS[index]`, where 0 means
    "send once and do not reschedule". Returns `(data, [Edit])`.
    """
    if phase not in _REPEAT_FIELDS:
        raise GestureEditError(f"phase {phase!r} is not one of {sorted(_REPEAT_FIELDS)}")
    if not 0 <= index < len(REPEAT_MS):
        raise GestureEditError(f"repeat index {index} is outside 0..{len(REPEAT_MS) - 1}")
    shift = _REPEAT_FIELDS[phase]
    gs, _ = guidata.guidata_extent(data)
    entry = guidata.group_table(data)[group]
    off = gs + entry.offset + 2
    old = data[off]
    new = (old & ~(3 << shift) & 0xFF) | (index << shift)
    if new == old:
        return data, []
    out = bytearray(data)
    out[off] = new
    return bytes(out), [relocate.Edit(
        off, bytes([old]), bytes([new]), "group-repeat",
        f"group {group} {phase} repeat {REPEAT_MS[(old >> shift) & 3]}ms -> "
        f"{REPEAT_MS[index]}ms")]


def make_hold_to_toggle(data: bytes, group: int, seconds: int = 3):
    """Turn a toggle group into "hold N seconds to switch, either way".

    Two same-size byte edits: the group's hold threshold, and the binding's phase mask ->
    `PHASE_HELD` alone, so a short tap does nothing and the toggle fires once when the
    threshold passes.

    **Why one firing and not a stream, twice over.** The sender skips any entry whose byte 2
    lacks bit 2 once the "already fired" flag is set (`ldrb r1,[r7,#0xc]` / `lsls r1,r2,#0x1d` /
    `bpl` at 0x0021B94C-0x0021B952), and a stock toggle binding has that bit clear -- which this
    function requires. That alone is enough. This ALSO zeroes the group's held-phase repeat rate,
    which stops the scheduler rescheduling at all (`0x0021BDEE` writes 0 to the next-send slot
    when the rate index is 0). The belt-and-braces is deliberate: if the byte-2-bit-2 reading
    were wrong, the failure mode with a live repeat rate is a real output toggling once a second
    for as long as a finger rests on it. Zeroing the rate makes that unreachable. The first send
    is unaffected -- entering the held phase sets the next-send time directly (0x0021BB64).

    **The premises, each checked because getting one wrong is silent.** A single-entry group's
    entry must be a `DC_DIMMER_COMMAND_2` command 5 (toggle) and must not carry the repeat bit.
    A multi-entry group is the real trap: which copy fires is chosen by a runtime variant
    counter, so editing "the" entry may edit one that never runs (section 5 of
    `docs/CONTROL-BEHAVIOR.md`). Such a group goes to `_make_multi_hold_to_toggle`, which edits
    every live copy alike and only when its five premises hold; that path is unverified on a
    coach.

    **No precedent.** No group in any corpus build sends a DC_DIMMER command on the held
    phase, and no hold-only group drives a load at all -- every shipped hold-to-confirm
    control emits 0x1FED9 config frames instead. This composition follows from the decoded
    mechanism; it has not been observed working. Treat it as needing a hardware round-trip.
    """
    entry = guidata.group_table(data)[group]
    bindings = guidata.binding_table(data)
    live = [i for i in range(entry.start, min(entry.start + entry.count, len(bindings)))
            if bindings[i].raw[:2] != b"\xff\xff"]
    if len(live) > 1:
        return _make_multi_hold_to_toggle(data, group, live, seconds)
    if not live:
        raise GestureEditError(
            f"group {group} has 0 live bindings; hold-to-toggle needs exactly one "
            f"(a multi-entry group's live copy is picked by a runtime variant counter)")
    b = bindings[live[0]]
    if b.dgn_low != guidata.DGN_DC_DIMMER_COMMAND_2:
        raise GestureEditError(
            f"group {group}'s binding is DGN {b.dgn_low:#06x}, not DC_DIMMER_COMMAND_2")
    if b.raw[4] != 5:
        raise GestureEditError(
            f"group {group}'s binding sends command {b.raw[4]}, not 5 (toggle); only a toggle "
            f"gives the same gesture for on and off")
    if b.raw[2] & BINDING_REPEATS:
        raise GestureEditError(
            f"group {group}'s binding carries the repeat bit, so a hold would toggle "
            f"every repeat tick instead of once")

    data, e1 = set_hold_seconds(data, group, seconds)
    data, e2 = set_phases(data, live[0], PHASE_HELD)
    data, e3 = set_repeat_index(data, group, "held", 0)
    return data, e1 + e2 + e3


# 0x1FED9 GENERIC_INDICATOR_COMMAND, the Firefly selector write/read (`sendmap.describe_payload`).
# Its payload byte 4, the low byte of the value a selector write sets, is binding byte 11.
DGN_GENERIC_INDICATOR_COMMAND_LOW = 0xFED9
_STATE_BYTE = 11
_RELEASE_TOGGLES = ("latch", "tap-only")


def _make_multi_hold_to_toggle(data: bytes, group: int, live: list, seconds: int):
    """`make_hold_to_toggle` for a group whose copies are picked by its variant counter.

    Safe only because **every** live copy gets the same edit, so whichever copy the counter
    selects fires on the hold. Five premises, each refused with its own message:

      1. no copy carries the repeat bit (a hold would fire on every repeat tick);
      2. every copy sends one DGN to one target (binding bytes 0-2 bar the variant nibble and
         the repeat bit, and bytes 4-7: the command, selector and instance);
      3. every variant implements the same gesture, and it is a release toggle (`latch` or
         `tap-only`), never a momentary, ramp or tap-or-hold control;
      4. the variants pick the direction: either every copy is a DC_DIMMER_COMMAND_2 toggle
         (command 5), or there are exactly two variants whose copies, paired by position, are
         byte-identical bar the variant nibble and (for 0x1FED9) the state byte, with the
         state bytes 0 and 1 in at least one pair. Group 129, Low Volts, pairs a selector
         0x2F write (`D1`, 0 / 1) with its read-back request (`D3`, identical in both);
      5. the group does not cycle its variant on release (byte 1 `0x80`), so the direction is
         chosen by the group's external variant setter alone (`docs/CONTROL-BEHAVIOR.md` §5).
         On 13.6 that is GuiCode's Low Volts block at 0x0024A176.

    Then one hold threshold, the held repeat rate zeroed, and `PHASE_HELD` on every copy.
    Unverified on a coach: the single-entry path is the one hardware has run.
    """
    bindings = guidata.binding_table(data)
    beh = describe_group(data, group)
    raws = {i: bindings[i].raw for i in live}
    head = f"group {group} has {len(live)} live bindings"

    repeating = [i for i in live if raws[i][2] & BINDING_REPEATS]
    if repeating:
        raise GestureEditError(
            f"{head}; binding(s) {repeating} carry the repeat bit, so a hold would fire "
            f"every repeat tick instead of once")

    def target(raw):
        return (raw[0], raw[1], raw[2] & 0x0F & ~BINDING_REPEATS, bytes(raw[4:8]))
    targets = {target(raws[i]) for i in live}
    if len(targets) != 1:
        raise GestureEditError(
            f"{head} sending to {len(targets)} DGN/selector targets; hold-to-toggle needs one "
            f"target (one two-state control)")

    if not beh.variants_agree:
        raise GestureEditError(
            f"{head} and its variants implement different gestures {beh.kinds}; editing them "
            f"alike would change what each one does differently")
    if beh.kind not in _RELEASE_TOGGLES:
        raise GestureEditError(
            f"{head} and is {beh.kind!r}; only a release toggle ({', '.join(_RELEASE_TOGGLES)}) "
            f"can become hold-to-toggle")

    dgn = raws[live[0]][0] | (raws[live[0]][1] << 8)
    if not (dgn == guidata.DGN_DC_DIMMER_COMMAND_2 and raws[live[0]][4] == 5):
        _check_two_state_variants(head, dgn, beh, raws)

    if beh.cycles_variant:
        raise GestureEditError(
            f"{head} and cycles its variant on release; whether the next hold fires the "
            f"opposite copy would depend on the cycle and the external setter (unproven)")

    data, edits = set_hold_seconds(data, group, seconds)
    for i in live:
        data, e = set_phases(data, i, PHASE_HELD)
        edits += e
    data, e = set_repeat_index(data, group, "held", 0)
    return data, edits + e


def _check_two_state_variants(head: str, dgn: int, beh: GroupBehaviour, raws: dict) -> None:
    """Premise 4(a): two variants whose paired copies differ only in the state they write."""
    if beh.variants != (0, 1):
        raise GestureEditError(
            f"{head} in variants {beh.variants}; a two-state control needs variants (0, 1)")
    per = {v: [e.binding_index for e in beh.emissions if e.variant == v] for v in (0, 1)}
    if len(per[0]) != len(per[1]):
        raise GestureEditError(
            f"{head}: variant 0 has {len(per[0])} and variant 1 {len(per[1])}, so its copies "
            f"do not pair")
    free = {2}
    if dgn == DGN_GENERIC_INDICATOR_COMMAND_LOW:
        free.add(_STATE_BYTE)
    two_state = False
    for a, b in zip(per[0], per[1]):
        ra, rb = raws[a], raws[b]
        other = [k for k in range(len(ra)) if k not in free and ra[k] != rb[k]]
        if other or (ra[2] & 0x0F) != (rb[2] & 0x0F):
            raise GestureEditError(
                f"{head}: bindings {a} and {b} differ in byte(s) {other or [2]}, not just "
                f"the variant and the state written")
        if _STATE_BYTE in free and {ra[_STATE_BYTE], rb[_STATE_BYTE]} == {0, 1}:
            two_state = True
        elif _STATE_BYTE in free and ra[_STATE_BYTE] != rb[_STATE_BYTE]:
            raise GestureEditError(
                f"{head}: bindings {a} and {b} write {ra[_STATE_BYTE]} and {rb[_STATE_BYTE]}, "
                f"not 0 and 1")
    if not two_state:
        raise GestureEditError(
            f"{head} but no pair writes 0 in one variant and 1 in the other, so it is not a "
            f"two-state control")
