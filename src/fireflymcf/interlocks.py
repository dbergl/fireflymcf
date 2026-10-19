"""Remove the OPTIONS cross-option interlocks (`docs/GUICODE.md` Finding 50).

Four of 13.6's OPTIONS rows write *another* row's selector as well as their own. Selecting
**Progressive Inverter** also writes `EF := 1` (AGS); selecting **AGS** also writes `E6 := 1`;
selecting **AES** or **AES Single Output** writes `E6 := 0`. The invariant the panel keeps is
`PI <-> AGS`, and it is enforced on *selection* only — no deselect arm clears its partner, so
`mode = AGS, PI = 0` is already reachable on a stock panel.

The interlock is **panel-side**: the G12 stores what it is told and never originates an
option-selection change (`BUS-BASELINE.md` PC.21 injected `E6 := 1` alone and the mode stayed
put). So removing it here removes it outright — there is no second copy in the G12 to disagree
with, and nothing has to be kept in step.

**Build-general as of 2026-08-29** (`find_coupled_handlers`). The couplings are located
*structurally* — scan GuiCode for the `ldr [pc]` pool-base load that begins an emission run, work
out this build's OPTIONS **toggles** (selectors with dedicated select/deselect arms), and keep runs
that write the generator/AES **mode** selector (`0xEF`) alongside one of those toggles. Validated to
reproduce 13.6's four `CROSS_EMISSIONS` couplings exactly, which is what licenses trusting the
result on other builds. It shows the coupling is **not a fixed PI↔mode pairing but a mode↔option
one that differs per build**:

  * **13.6** — four couplings between the mode and **Progressive Inverter** (`0xE6`).
  * **11.23** — **none**: it predates AES, has no `0xE6` template at all, so nothing couples to the
    mode. `uncouple` is a clean no-op (nothing to remove).
  * **14.7** — the coupling **moved**: Progressive Inverter (`0xE6`) is present but *decoupled*, and
    the mode is instead mutually exclusive with **`0xE7`** = the new **Lithionics Batteries** option
    (selecting Lithionics clears the mode; selecting AGS clears Lithionics). So 14.7 is *not*
    interlock-free — it has a different interlock. `describe` reports both arms; `uncouple` refuses
    to *edit* it (`BuildNotSupported`), because removing it on a build with no hardware to verify
    against is not validated. **`0xE7` = Lithionics Batteries is proven, not fitted**
    (`option_for_selector`): the handler that emits `0xE7` gates on `0x2003a009`, the exact
    stored-state register the Lithionics OPTIONS widget is bound to (same method reproduces
    `0xE6` -> Progressive Inverter on both 13.6 and 14.7). Lithionics replaced 13.6's *uncoupled*
    Renogy Battery row, and being mutually exclusive with auto-gen-start fits a lithium system that
    manages its own charging. Like 13.6's PI interlock it is **panel-side only** -- the 14.7 G12
    code references `0xE7` solely to default it and never reads it at runtime, so it enforces nothing
    (`docs/G12-CODE.md`, "Does the G12 enforce the 14.7 Lithionics<->AES interlock?", 2026-08-29).

**What an emission looks like.** There is no interlock *logic* to excise: a row's tap handler is
a straight-line run that copies pre-built 12-byte `0x1FED9` templates out of GuiCode's `.rodata`
pool and hands each to the host sender, and a coupled row is simply one whose list is longer.
Every emission is the same 36-byte, twelve-instruction pair — the write frame, then its
`ff ff d3 ff` read-back twin:

    addw/add.w r3, r6, #<write template>      4      r6 = the pool base
    ldm.w      r3, {r0, r1, r2}               4      12 bytes of frame
    stm.w      r5, {r0, r1, r2}               4      into the stack buffer at r5
    ldr        r3, [r7]                       2      r7 = &sender slot (0x2003986C)
    mov        r0, r5                         2
    blx        r3                             2      send
    addw/add.w r6, r6, #<read-back template>  4      NOTE: writes r6, not r3
    ldm.w      r6, {r0, r1, r2}               4
    stm.w      r5, {r0, r1, r2}               4
    ldr        r3, [r7]                       2
    mov        r0, r5                         2
    blx        r3                             2

so removing one is **NOP-ing 36 bytes**, and the edit is same-size by construction: nothing
moves, no offset needs repairing, and the surrounding runs are untouched.

**Why the read-back goes too.** It is only a query and leaving it would be harmless, but a
handler that reads back a selector it no longer writes is a confusing artifact, and removing the
whole pair makes the result *checkable*: re-walking the patched run must return exactly the row's
own selector and nothing else. A partial edit would leave a template reference behind and the
verification would have to get weaker to accommodate it.

**Why clobbering `r6` in the read-back block is safe** — this was checked at each site, not
assumed. The read-back `add` targets `r6` (the pool base) rather than `r3`, so after the pair
`r6` is garbage. At three of the four sites the next instruction reloads it (`ldr r6,[pc,#..]`);
at the fourth the run ends immediately in `b.w`. So no later block depends on the value the
removed one destroyed, and NOP-ing the pair is strictly safer than the original.

STATUS: `proven-from-code` for what each edit removes — every site is located by *walking the
run and matching the selector*, never by a hardcoded byte offset, and the twelve-instruction
shape is asserted before anything is written. The handler-arm entry addresses are 13.6-specific
and stated as such; `uncouple` refuses rather than guessing on a build whose runs differ.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from . import memo, theme
from .theme import CapstoneUnavailable

try:
    import capstone
except ImportError:                                     # pragma: no cover
    capstone = None

# The `0x1FED9` template's first word: the CAN id with the source byte still zero.
FRAME_TAG_1FED9 = 0x19FED900
# `ff 96 <sel> 0f <u16 value> d1 ea` is a write; `ff 96 <sel> 0f ff ff d3 ff` its read-back.
READBACK_TAIL = b"\xff\xd3\xff"

# Thumb NOP encodings, by instruction size.
NOP = {2: bytes.fromhex("00bf"), 4: bytes.fromhex("aff30080")}

# The twelve-instruction emit pair, as (mnemonic prefix, size). `addw` and `add.w` are the same
# instruction to capstone's mnemonic field only up to the prefix, hence the loose match.
EMIT_SHAPE = [("add", 4), ("ldm", 4), ("stm", 4), ("ldr", 2), ("mov", 2), ("blx", 2)] * 2

# 13.6 handler arms that emit a foreign selector, keyed by a name a project file can carry, as
# `(arm entry, the row's own selector, the selector to stop emitting, why)`.
#
# Entries are cited from `docs/GUICODE.md` Finding 50's table. An entry is the starting point for
# the *walk*, not the edit site: the block to NOP is found by matching `foreign` against the
# templates the run actually references, so a build whose runs have shifted yields "not found"
# rather than a wrongly-placed patch.
#
# `D7 := 0` (Go Power! count) on the AGS/AES arms is deliberately absent from this table. It is a
# separate interlock and out of scope here; add it as a fifth entry if it is ever wanted.
CROSS_EMISSIONS = {
    "pi-forces-ags": (
        0x0024CCFA, 0xE6, 0xEF, "Progressive Inverter ON: stop forcing the AGS mode"),
    "ags-forces-pi": (
        0x0024D014, 0xEF, 0xE6, "AGS select: stop forcing Progressive Inverter on"),
    "aes-clears-pi": (
        0x0024D10C, 0xEF, 0xE6, "AES select: stop clearing Progressive Inverter"),
    "aes-single-clears-pi": (
        0x0024BF7E, 0xEF, 0xE6, "AES Single Output select: stop clearing Progressive Inverter"),
}

# Every arm of the four coupled rows, including the deselect arms this module never edits.
# Used to prove a NOP run did not eat a neighbouring row's frame.
ALL_MODE_ARMS = (0x0024CCFA, 0x0024CD4C, 0x0024D014, 0x0024D0D8,
                 0x0024D10C, 0x0024D1E8, 0x0024BF7E, 0x0024D214)


@dataclass(frozen=True)
class Emission:
    """One `0x1FED9` template reference inside a straight-line run."""

    at: int            # address of the `add` that forms the template pointer
    template: int      # mapped address of the template
    selector: int      # payload byte 2
    value: int         # payload byte 4 (meaningless on a read-back)
    write: bool        # False for the `ff ff d3 ff` read-back variant


def _md():
    if capstone is None:
        raise CapstoneUnavailable("capstone is required to edit GuiCode instructions")
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    return md


def _template(code: bytes, base: int, addr: int):
    off = addr - base
    if off < 0 or off + 12 > len(code):
        return None
    if struct.unpack_from("<I", code, off)[0] != FRAME_TAG_1FED9:
        return None
    payload = code[off + 4:off + 12]
    if payload[0] != 0xFF or payload[1] != 0x96:
        return None
    return payload[2], struct.unpack_from("<H", payload, 4)[0], payload[5:8] != READBACK_TAIL


def run_emissions(data: bytes, entry: int, limit: int = 0x400) -> list[Emission]:
    """Every `0x1FED9` template the straight-line run at `entry` references, in order.

    Writes *and* read-backs, unlike the walker in `tests/test_option_interlocks.py`, because
    this module has to remove both halves of a pair. The walk tracks only literal-pool loads and
    immediate adds off a register already holding a concrete value — that is all a handler uses
    to form a template pointer — and stops at the first unconditional branch or return, which is
    what bounds a "straight-line run".
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = bytes(data[section.offset:section.offset + section.length])
    return _run_emissions_in(code, base, _lite_md(), entry, limit)


def _lite_md():
    """A decoder for the walkers below, which read only mnemonic/op_str/address/size --
    `disasm_lite` yields exactly those as a tuple, without the `CsInsn`/detail objects that
    made `_all_runs` (33k walks per build) spend 22 s in capstone's Python layer."""
    if capstone is None:
        raise CapstoneUnavailable("capstone is required to edit GuiCode instructions")
    return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)


def _run_emissions_in(code: bytes, base: int, md, entry: int, limit: int) -> list[Emission]:
    """`run_emissions` over an already-extracted section with a shared decoder."""
    regs: dict[str, int] = {}
    out: list[Emission] = []
    for address, _size, mnemonic, op_str in md.disasm_lite(
            code[entry - base:entry - base + limit], entry):
        if mnemonic.startswith("ldr") and "[pc" in op_str:
            m = re.search(r"#(-?0x[0-9a-f]+|-?\d+)\]", op_str)
            if m:
                at = ((address + 4) & ~3) + int(m.group(1), 0)
                regs[op_str.split(",")[0].strip()] = struct.unpack_from(
                    "<I", code, at - base)[0]
        elif mnemonic in ("add", "addw", "add.w"):
            m = re.match(r"(\w+), (\w+), #(0x[0-9a-f]+|\d+)$", op_str)
            if m and m.group(2) in regs:
                addr = regs[m.group(2)] + int(m.group(3), 0)
                regs[m.group(1)] = addr
                found = _template(code, base, addr)
                if found:
                    out.append(Emission(address, addr, *found))
        elif mnemonic in ("b", "b.w", "bx", "pop"):
            break
    return out


def _pair_extent(code: bytes, base: int, at: int) -> int:
    """Byte length of the twelve-instruction emit pair starting at `at`, asserting its shape.

    Raises if the instructions are not exactly `EMIT_SHAPE`. That check is the whole safety
    argument for NOP-ing blind bytes: it refuses on anything that is not the idiom this module
    was written against, rather than blanking whatever happens to be there.
    """
    got = []
    total = 0
    for insn in _md().disasm(code[at - base:at - base + 64], at):
        if len(got) == len(EMIT_SHAPE):
            break
        got.append((insn.mnemonic, insn.size))
        total += insn.size
    if len(got) != len(EMIT_SHAPE) or any(
            not m.startswith(pm) or sz != psz
            for (m, sz), (pm, psz) in zip(got, EMIT_SHAPE)):
        raise ValueError(
            f"emit pair at {at:#x} does not match the expected shape: "
            + ", ".join(f"{m}/{sz}" for m, sz in got))
    return total


# The generator/AES mode selector -- the anchor of the interlock on every build seen so far. On
# 13.6 the mode is coupled to Progressive Inverter (0xE6); on 14.7 to a different option (0xE7).
MODE_SELECTOR = 0xEF


def _all_runs(data: bytes) -> list:
    """Every straight-line emission run in GuiCode, as (entry, emissions), deduped by the run's
    first-write address. Build-general: scans for the `ldr [pc]` pool-base loads that begin a run
    and walks each with `run_emissions`. Memoised on content: `doctor` asks for it once per
    coupling plus once per selector, and each scan is ~33k walks."""
    return [(entry, list(emissions)) for entry, emissions in _all_runs_cached(data)]


@memo.content_cached(copy=None)
def _all_runs_cached(data: bytes) -> tuple:
    md = _lite_md()
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = bytes(data[section.offset:section.offset + section.length])
    by_first: dict[int, tuple] = {}
    for off in range(0, len(code) - 1, 2):
        head = next(md.disasm_lite(code[off:off + 4], base + off), None)
        if head is None or not (head[2].startswith("ldr") and "[pc" in head[3]):
            continue
        try:
            emissions = _run_emissions_in(code, base, md, base + off, 0x400)
        except Exception:                    # a stray pc-literal that points outside the section
            continue
        writes = [e for e in emissions if e.write]
        if not writes:
            continue
        first_at = writes[0].at
        if first_at not in by_first or len(emissions) > len(by_first[first_at][1]):
            by_first[first_at] = (base + off, tuple(emissions))
    return tuple(by_first.values())


def _option_toggles(runs: list) -> set:
    """Selectors that are genuine OPTIONS toggles on this build: those with dedicated select and
    deselect arms (own-selector runs writing value 1 and value 0), as opposed to the config
    parameters (D7/0x15/F7/...) a mode row also writes as part of setting up a profile. Excludes
    the mode selector itself. This is what separates a real *cross-option* interlock from a profile
    write, without hardcoding which options a build carries."""
    from collections import defaultdict
    own_values: dict[int, set] = defaultdict(set)
    for _entry, emissions in runs:
        writes = [e for e in emissions if e.write]
        if writes and len(writes) <= 2:                  # a plain select/deselect arm
            own_values[writes[0].selector].add(writes[0].value)
    return {sel for sel, values in own_values.items()
            if {0, 1} <= values and sel != MODE_SELECTOR}


def find_coupled_handlers(data: bytes) -> dict:
    """Locate the OPTIONS cross-option interlocks **structurally**, on any build.

    Returns `{key: [Emission, ...]}` for every coupling between the generator/AES **mode** selector
    (`0xEF`) and another OPTIONS **toggle** -- a run that writes the mode selector alongside a
    foreign toggle, or a toggle's own row that writes the mode. Build-general (no per-build
    addresses): validated to reproduce 13.6's four `CROSS_EMISSIONS` couplings exactly, and it
    correctly finds 14.7's coupling too (`tests/test_interlocks.py`).

    What each corpus build shows (`GUICODE.md` Finding 50):
      * 13.6 -- four couplings between the mode and **Progressive Inverter** (`0xE6`);
      * 11.23 -- **none**: it predates AES, so there is no mode<->option coupling at all;
      * 14.7 -- **one** coupling between the mode and a *new* toggle **`0xE7`** (mutually exclusive
        with AGS): Progressive Inverter (`0xE6`) is present but decoupled here. `0xE7` IS the
        **Lithionics Batteries** option (`option_for_selector` proved the binding 2026-08-29, and
        a coach flashed to 14.7 confirmed the whole shape on the wire 2026-08-30 -- BUS-BASELINE
        PC.41). The hardware also sharpened the select-`0xE7` arm: its `mode := 0` emission is
        **conditional on the current mode being AGS** -- from AES/AES-Single the tap writes `E7`
        alone; from AGS it writes `E7:=1` + `EF:=0`. This detector reports the template
        (correctly -- it exists in the run); the runtime gate in front of it is not modeled here,
        so read the reported 14.7 coupling as "Lithionics excludes AGS", not "excludes any mode".
        PI's decoupling and the AGS -> `E7:=0` arm were observed unconditionally.
    """
    runs = _all_runs(data)
    toggles = _option_toggles(runs)
    out: dict[str, list] = {}
    for _entry, emissions in runs:
        writes = [e for e in emissions if e.write]
        if len(writes) < 2:
            continue
        own = writes[0].selector
        written = {e.selector for e in writes}
        if own == MODE_SELECTOR:
            partners = [s for s in written if s in toggles]      # mode row forcing/clearing a toggle
            partner = partners[0] if partners else None
        elif own in toggles and MODE_SELECTOR in written:        # a toggle row that moves the mode
            partner = MODE_SELECTOR
        else:
            continue
        if partner is None:
            continue
        # 13.6 keys need the mode VALUE to distinguish ags/aes/aes-single; classify precisely there.
        key = _coupling_key_for(own, partner, writes)
        if key not in out or len(emissions) > len(out[key]):
            out[key] = emissions
    return out


def _coupling_key_for(own: int, partner: int, writes: list) -> str:
    """Stable, direction-distinct key for a coupling handler. The four 13.6 PI<->mode couplings keep
    their `CROSS_EMISSIONS` names (so `uncouple` and the recipe op are unaffected); any other build's
    coupling gets a descriptive key that names the option selector AND the direction, so both arms of
    a mutual-exclusion pair are reported separately."""
    option_sel = partner if own == MODE_SELECTOR else own
    mode_value = next(e.value for e in writes if e.selector == MODE_SELECTOR)
    if option_sel == 0xE6:                                # 13.6 PI<->mode family (named)
        if own == 0xE6:
            return "pi-forces-ags"
        name = {1: "ags-forces-pi", 2: "aes-clears-pi", 3: "aes-single-clears-pi"}.get(mode_value)
        if name:
            return name
    if own == MODE_SELECTOR:                              # a mode row that moves the option toggle
        return f"mode{mode_value}-writes-{option_sel:#04x}"
    return f"{option_sel:#04x}-writes-mode"               # an option row that moves the mode


def has_interlocks(data: bytes) -> bool:
    """True if this build has any mode<->option OPTIONS interlock (structural, build-general)."""
    return bool(find_coupled_handlers(data))


def _select_handler_gate_register(data: bytes, selector: int) -> int | None:
    """The SRAM register the select handler for `selector` reads at its very start -- its
    select/deselect gate. A config-selector's tap handler always begins by reading the option's own
    stored state (`ldrb` off a pool literal) to decide whether this tap selects or deselects, so
    that register identifies which OPTIONS row the selector belongs to."""
    md = _md()
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = bytes(data[section.offset:section.offset + section.length])
    for entry, emissions in _all_runs(data):
        writes = [e for e in emissions if e.write]
        if not writes or writes[0].selector != selector or writes[0].value != 1:
            continue
        for insn in md.disasm(code[entry - base:entry - base + 0x10], entry):
            if insn.mnemonic.startswith("ldr") and "[pc" in insn.op_str:
                m = re.search(r"#(0x[0-9a-f]+|\d+)\]", insn.op_str)
                if m:
                    at = ((insn.address + 4) & ~3) + int(m.group(1), 0)
                    return struct.unpack_from("<I", code, at - base)[0]
            break                                    # only the first instruction is the gate load
    return None


def option_for_selector(data: bytes, selector: int) -> str | None:
    """The OPTIONS-row name a config `selector` belongs to, or None. Proven, not fitted: the
    selector's select handler gates on the option's stored-state register, and that register is what
    the OPTIONS widget is bound to (`options.option_bindings`). Validated on the 13.6 oracle
    (`0xE6` -> 'Progressive Inverter') and used to prove 14.7's `0xE7` is 'Lithionics Batteries'."""
    from . import options
    reg = _select_handler_gate_register(data, selector)
    if reg is None:
        return None
    for binding in options.option_bindings(data):
        if binding.register == reg:
            return binding.name
    return None


class BuildNotSupported(ValueError):
    """Raised when a PI<->mode coupling is present but its handlers are not at the known 13.6
    addresses and structural removal has not been validated for this build.

    This is now a narrow case: `find_coupled_handlers` locates the coupling structurally on any
    build, and a build with *no* coupling (11.23, 14.7) is a clean no-op, not an error. The guard
    remains only for the hypothetical build that has the coupling at shifted addresses -- `describe`
    can still report it, but `uncouple` will not edit bytes it has not been able to validate."""


def addresses_apply(data: bytes) -> bool:
    """True if every `CROSS_EMISSIONS` arm resolves to a run emitting the row's **own** selector on
    this build -- i.e. this is the 13.6-family layout the addresses were derived from. Content-
    checked, not a version-string match, so a different build that happens to share the layout still
    works and a renumbered 13.6 rebuild that does not is correctly rejected.

    Checks the `own` selector, not the `foreign` one: `uncouple` removes the foreign write, so a
    foreign-based check would call an already-uncoupled 13.6 image an unsupported build. The own
    selector survives uncoupling (it is the row's whole point), so it is the stable build signal."""
    for entry, own, _foreign, _why in CROSS_EMISSIONS.values():
        if not any(e.selector == own and e.write for e in run_emissions(data, entry)):
            return False
    return True




def uncouple(data: bytes, names=None) -> tuple[bytes, list]:
    """Remove the named cross-option emissions; return `(patched, edits)`.

    `names` is a sequence of `CROSS_EMISSIONS` keys, or None for all of them. Each edit is
    `(file_offset, old_bytes, new_bytes, why)`, same-size, in the style of `tankgates.widen`. The
    caller must still `patch.reseal` — this only rewrites GuiCode bytes.

    Refuses, rather than guessing, if a run does not emit the foreign selector exactly once as a
    write, if the write is not followed immediately by the matching read-back, or if either block
    is not the twelve-instruction idiom. A build whose handlers differ therefore fails loudly.
    """
    if names is None:
        names = list(CROSS_EMISSIONS)
    unknown = [n for n in names if n not in CROSS_EMISSIONS]
    if unknown:
        raise ValueError(f"unknown interlock(s): {unknown}; known: {sorted(CROSS_EMISSIONS)}")

    # Build-general: if this build has no PI<->mode coupling to remove (11.23 predates AES; 14.7
    # ships the rows independent -- proven structurally by `find_coupled_handlers`), the edit is a
    # clean no-op, not an error. Only when the coupling IS present but not at the 13.6 addresses do
    # we refuse, because structural *removal* has not been validated (no such build exists yet).
    if not addresses_apply(data):
        if not has_interlocks(data):
            return bytes(data), []
        raise BuildNotSupported(
            "this build has a PI<->mode coupling but not at the 13.6 handler addresses; "
            "`describe` can locate it, but `uncouple` will not edit bytes it has not validated "
            "for this build (GUICODE Finding 50).")

    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    out = bytearray(data)
    edits: list[tuple[int, bytes, bytes, str]] = []

    for name in names:
        entry, own, foreign, why = CROSS_EMISSIONS[name]
        code = bytes(out[section.offset:section.offset + section.length])
        found = run_emissions(bytes(out), entry)

        writes = [e for e in found if e.selector == foreign and e.write]
        if len(writes) != 1:
            raise ValueError(
                f"{why}: expected exactly one {foreign:#04x} write in the run at {entry:#x}, "
                f"found {len(writes)}")
        emit = writes[0]

        # The read-back twin must be the very next reference, and name the same selector --
        # otherwise this is not the pair the shape check is about to blank.
        nxt = found[found.index(emit) + 1] if found.index(emit) + 1 < len(found) else None
        if nxt is None or nxt.selector != foreign or nxt.write:
            raise ValueError(
                f"{why}: the {foreign:#04x} write at {emit.at:#x} is not followed by its "
                "read-back twin")

        size = _pair_extent(code, base, emit.at)
        if emit.at + size <= nxt.at:
            raise ValueError(f"{why}: emit pair at {emit.at:#x} does not span its read-back")

        # Same-size NOPs, laid down instruction by instruction so each keeps its own width.
        new = bytearray()
        for insn in _md().disasm(code[emit.at - base:emit.at - base + size], emit.at):
            new += NOP[insn.size]
        assert len(new) == size, (len(new), size)

        off = section.offset + (emit.at - base)
        old = bytes(out[off:off + size])
        out[off:off + size] = new
        edits.append((off, old, bytes(new),
                      f"{why} (drop {foreign:#04x} write+read-back at {emit.at:#x})"))

        # Re-walk from the finished bytes and require the run to be *exactly* what it was minus
        # the removed pair. Asserting only "the foreign selector is gone" would not notice a NOP
        # run that swallowed a neighbouring block, and the AGS/AES arms legitimately keep their
        # `D7` / `0x15` / `F7` companions, so "emits only its own selector" is not the rule.
        def signature(items):
            return [(e.selector, e.value, e.write) for e in items]

        expected = signature([e for e in found if e is not emit and e is not nxt])
        after = signature(run_emissions(bytes(out), entry))
        if after != expected:
            raise ValueError(
                f"{why}: after the edit the run emits {after}, expected {expected}")
        assert not any(sel == foreign for sel, _v, _w in after), (why, after)
        assert any(sel == own for sel, _v, _w in after), \
            f"{why}: the row's own {own:#04x} write disappeared"

    return bytes(out), edits


def describe(data: bytes, names=None) -> dict:
    """`{name: [Emission, ...]}` — what each coupled run emits, for reporting. Build-general.

    On the 13.6 family the known addresses are used directly (fast, and exactly the runs the
    editor acts on); on any other build the handlers are located structurally
    (`find_coupled_handlers`). A name that is not coupled on this build maps to an empty list --
    an honest "this row is independent here", which is the correct answer for 11.23 and 14.7 (they
    have no PI<->mode coupling), NOT a stand-in for "unsupported build"."""
    if addresses_apply(data):
        handlers = {n: run_emissions(data, CROSS_EMISSIONS[n][0]) for n in CROSS_EMISSIONS}
    else:
        handlers = find_coupled_handlers(data)          # build-general; keys are present couplings
    if names is None:
        return dict(handlers)                           # every coupling this build actually has
    return {n: handlers.get(n, []) for n in names}
