"""The OPTIONS interlocks (`docs/GUICODE.md` Finding 50).

Selecting *Progressive Inverter* also selects AGS; selecting either AES mode deselects
Progressive Inverter. Both are **panel-side**: a row's tap handler emits a fixed list of
`0x1FED9` write templates in one straight-line run, and the coupling is authored into the
sequence rather than computed.

Two independent claims are pinned here, one per source of truth:

* **the code** — each handler arm's full emission list, walked from the handler entry;
* **the bus** — across the whole capture corpus, the G12 never *originates* a change to an
  option-*selection* selector; every such change is an echo of a panel write.

`tests/test_config_offsets.py::test_selector_site_progressive_inverter_forces_ags` already
pins the `E6`-then-`EF` pair via `config.selector_sites`. This module exists because that
API deliberately truncates its `frames` tuple at the first call boundary (it returns 2 frames
for `cfg 0x13`, whose handler emits 5), so it cannot express the reverse coupling, the
`D7`/`0x15`/`F7` companions, or the selection/deselection asymmetry.

**Why pin this at all.** Finding 19 concluded in 2026-08-14 that this coupling was "upstream,
not in GuiCode" — a false negative that survived eight days *after* Finding 23 recorded the
two-template run in passing, because nothing failed when it went stale. The walker below is a
second, independent expression of the finding: it re-derives the emission lists from the image
rather than reading them back out of `config.py`.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from fireflymcf import theme

capstone = pytest.importorskip("capstone")

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURES = REPO_ROOT / "captures"

# The `0x1FED9` template's first word: the CAN id with the source byte still zero.
FRAME_TAG_1FED9 = 0x19FED900
# `ff 96 <sel> 0f <value u16> d1 ea` is a write; `ff 96 <sel> 0f ff ff d3 ff` its read-back.
READBACK_TAIL = b"\xff\xd3\xff"


def _walk_emissions(mcf: bytes, entry: int, limit: int = 0x400):
    """`[(addr, selector, value)]` for every `0x1FED9` *write* template the straight-line run
    at `entry` emits, in order.

    A handler addresses its templates as `pool_base + imm`, so the walk tracks two things
    only: literal pool loads (`ldr rD,[pc,#imm]`) and immediate adds off a register already
    holding a concrete value. Any `add` whose result lands on a well-formed template is an
    emission. The walk stops at the first unconditional branch or return, which is exactly
    what "straight-line run" means — a deselect arm that tail-jumps into a shared epilogue
    reports only its own frames, and the epilogue is walked separately.
    """
    section = theme.guicode_section(mcf)
    base = theme.guicode_base(section)
    code = mcf[section.offset:section.offset + section.length]

    def template(addr: int):
        off = addr - base
        if off < 0 or off + 12 > len(code):
            return None
        if struct.unpack_from("<I", code, off)[0] != FRAME_TAG_1FED9:
            return None
        payload = code[off + 4:off + 12]
        if payload[0] != 0xFF or payload[1] != 0x96:
            return None
        if payload[5:8] == READBACK_TAIL:
            return None
        return payload[2], struct.unpack_from("<H", payload, 4)[0]

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    regs: dict[str, int] = {}
    out: list[tuple[int, int, int]] = []
    for insn in md.disasm(code[entry - base:entry - base + limit], entry):
        if insn.mnemonic.startswith("ldr") and "[pc" in insn.op_str:
            m = re.search(r"#(-?0x[0-9a-f]+|-?\d+)\]", insn.op_str)
            if m:
                at = ((insn.address + 4) & ~3) + int(m.group(1), 0)
                regs[insn.op_str.split(",")[0].strip()] = struct.unpack_from(
                    "<I", code, at - base)[0]
        elif insn.mnemonic in ("add", "addw", "add.w"):
            m = re.match(r"(\w+), (\w+), #(0x[0-9a-f]+|\d+)$", insn.op_str)
            if m and m.group(2) in regs:
                addr = regs[m.group(2)] + int(m.group(3), 0)
                regs[m.group(1)] = addr
                found = template(addr)
                if found:
                    out.append((insn.address, *found))
        elif insn.mnemonic in ("b", "b.w", "bx", "pop"):
            break
    return out


# `docs/GUICODE.md` Finding 50's table. Handler-arm entry addresses are cited from the
# disassembly; the expected emissions are what the run puts on the wire, corroborated
# frame-for-frame by `BUS-BASELINE.md` PC.3.
EMISSIONS = {
    # Progressive Inverter row, gate `[0x2003A62B]`.
    "pi_on":       (0x0024CCFA, [(0xE6, 1), (0xEF, 1)]),
    "pi_off":      (0x0024CD4C, [(0xE6, 0)]),
    # AGS / AES / AES Single Output radios, gate `[0x2003A38C]`.
    "ags_select":  (0x0024D014, [(0xEF, 1), (0xD7, 0), (0xE6, 1), (0x15, 0x2801), (0xF7, 0x3C)]),
    "ags_desel":   (0x0024D0D8, [(0xEF, 0)]),
    "aes_select":  (0x0024D10C, [(0xEF, 2), (0xD7, 0), (0xE6, 0), (0x15, 0x2801), (0xF7, 0x3C)]),
    "aes_desel":   (0x0024D1E8, [(0xEF, 0)]),
    "aeso_select": (0x0024BF7E, [(0xEF, 3), (0xD7, 0), (0xE6, 0), (0x15, 0x2801), (0xF7, 0x3C)]),
    "aeso_desel":  (0x0024D214, [(0xEF, 0)]),
}


@pytest.mark.parametrize("arm", sorted(EMISSIONS))
def test_handler_arm_emissions(mcf_136, arm):
    entry, expected = EMISSIONS[arm]
    got = [(sel, val) for _, sel, val in _walk_emissions(mcf_136, entry)]
    assert got == expected


def test_selection_forces_but_deselection_does_not_clear(mcf_136):
    """The invariant `PI <-> AGS` is enforced on *selection* only.

    Both selection arms force their partner; no deselection arm clears anything. That
    asymmetry is why `mode = AGS, PI = 0` is reachable — the coach sat in it for 29 s in
    PC.3 — and why nothing may assume the pair is consistent.
    """
    def emitted(arm):
        return [(sel, val) for _, sel, val in _walk_emissions(mcf_136, EMISSIONS[arm][0])]

    assert (0xEF, 1) in emitted("pi_on"), "selecting PI must force mode AGS"
    assert (0xE6, 1) in emitted("ags_select"), "selecting AGS must force PI on"
    assert (0xE6, 0) in emitted("aes_select"), "selecting AES must force PI off"
    assert (0xE6, 0) in emitted("aeso_select"), "selecting AES SO must force PI off"

    for arm in ("pi_off", "ags_desel", "aes_desel", "aeso_desel"):
        others = [sel for sel, _ in emitted(arm)]
        assert others in ([0xE6], [0xEF]), (
            f"{arm} emits {others!r}: a deselect arm must write only its own selector, "
            "so the partner option is left set")


# Selectors that name an option *selection*. `0x15`/`F7` and the AGS parameter profile
# (`0x0A`, `0x0D`, `0x31`, ...) are deliberately excluded: the G12 legitimately re-derives
# those when the mode changes, which is the behaviour this test must not flag.
SELECTION_SELECTORS = {0xD7, 0xD8, 0xE3, 0xE4, 0xE5, 0xE6, 0xE9, 0xEB, 0xEC, 0xEF, 0xF5}
G12_SA = "9C"


def _capture_events(path: Path):
    """`(t, kind, selector, value)` for `0x1FED9` writes and G12 `0x15FCE` reports.

    A write is credited to whoever sent it, from **any** source address — not just the panel's
    `9F`. The claim under test is that the G12 never *originates* an option-selection change, so
    any other node's write is a legitimate external cause. `captures/pc21-...` is the case that
    forces this: its `E6 := 1` comes from the probe address `0xC0`, and scoping the rule to `9F`
    would score that injection as a G12-originated change (it did, before this was fixed).
    """
    t0 = None
    for line in path.read_text().splitlines():
        m = re.match(r"\((\d+\.\d+)\) \S+ ([0-9A-F]+)#([0-9A-F]*)", line.strip())
        if not m:
            continue
        ts = float(m.group(1))
        t0 = t0 if t0 is not None else ts
        can_id, data = m.group(2), bytes.fromhex(m.group(3))
        if (can_id.startswith("19FED9") and len(data) == 8
                and data[0] == 0xFF and data[1] == 0x96 and data[5:8] != READBACK_TAIL):
            yield ts - t0, "bus_write", data[2], struct.unpack_from("<H", data, 4)[0]
        elif can_id.startswith("195FCE") and can_id.endswith(G12_SA) and len(data) >= 4:
            yield ts - t0, "g12_report", data[0], struct.unpack_from("<H", data, 2)[0]


def test_g12_never_originates_an_option_selection_change():
    """`proven-from-bus`: every change the G12 reports in an option-*selection* selector is
    accounted for by something else on the bus having written that value.

    The G12 broadcasts its whole config every ~5 s, so a report is not a change — only a report
    whose value differs from the last one is. Each such change is then attributed by **value**:
    it is explained if the most recent bus write of that selector carried the same value, and
    counted as G12-originated otherwise.

    **Why value and not elapsed time.** The first cut allowed 1 s, which fits the panel's own
    traffic (the G12 echoes a tap within ~2 ms) and is wrong in general: in `pc21` the injected
    `E6 := 1` was not reflected until the periodic sweep came round **2.9 s** later, and the test
    scored the coach's own report as a G12-originated change. Widening the window would only
    trade one arbitrary constant for another, so attribution now asks the question that actually
    matters — *did anyone ask for this value?* — and is time-independent.

    **What this test cannot do**, stated because the limitation is real: where the panel writes
    both halves of a coupled pair, a G12 that *also* forced the partner would be indistinguishable
    from an echo, since the value matches either way. That ambiguity is precisely what the
    injection in `test_pc21_injection_g12_does_not_force_ags` removes, by writing one half and
    leaving the other unwritten. This test carries the breadth; that one carries the sharp claim.
    """
    logs = sorted(CAPTURES.glob("*.log"))
    assert logs, "no captures found"

    echoes = 0
    originated: list[tuple[str, float, int, int]] = []
    for path in logs:
        last_write: dict[int, int] = {}
        last_report: dict[int, int] = {}
        for t, kind, sel, val in _capture_events(path):
            if kind == "bus_write":
                last_write[sel] = val
            elif sel in SELECTION_SELECTORS and last_report.get(sel) != val:
                if sel in last_report:            # first sighting is not a change
                    if last_write.get(sel) == val:
                        echoes += 1
                    else:
                        originated.append((path.name, t, sel, val))
                last_report[sel] = val

    assert echoes > 0, "no option-selection changes observed at all -- test proves nothing"
    assert not originated, (
        "the G12 originated an option-selection change: "
        + ", ".join(f"{n} t={t:.3f} sel {s:02X} = {v:#06x}" for n, t, s, v in originated))


def test_pi_off_leaves_ags_set_on_the_coach():
    """The decisive observation: at PC.3 t=76.668 the panel wrote `E6 := 0` and nothing else,
    and the G12 went on reporting `mode = 1` until a manual tap cleared it at t=105.942.

    A G12 enforcing `PI <-> AGS` would have cleared the mode. The panel's asymmetric
    deselect arm isolated the variable for us, which is what makes this a single-variable
    test rather than a correlation.
    """
    path = CAPTURES / "pc3-electrical-2026-08-12.log"
    events = list(_capture_events(path))

    pi_off = [t for t, k, sel, v in events
              if k == "bus_write" and sel == 0xE6 and v == 0]
    assert pi_off, "PC.3 must contain a PI-off write"
    t_off = pi_off[0]

    # No write of any kind accompanies it: this is the isolated variable.
    companions = [(sel, v) for t, k, sel, v in events
                  if k == "bus_write" and abs(t - t_off) < 0.5 and sel != 0xE6]
    assert companions == [], f"PI-off was not isolated, it came with {companions!r}"

    next_mode_write = min((t for t, k, sel, _ in events
                           if k == "bus_write" and sel == 0xEF and t > t_off), default=None)
    assert next_mode_write is not None and next_mode_write - t_off > 25

    mode_reports = [v for t, k, sel, v in events
                    if k == "g12_report" and sel == 0xEF and t_off < t < next_mode_write]
    assert len(mode_reports) >= 5, f"too few G12 mode broadcasts to conclude: {mode_reports!r}"
    assert set(mode_reports) == {1}, (
        f"expected the G12 to hold mode = AGS after PI was cleared, got {mode_reports!r}")


def test_pc21_injection_g12_does_not_force_ags():
    """`BUS-BASELINE.md` PC.21, the *setting* direction — the half no screen tap could reach.

    One frame went on the wire from probe address `0xC0` with the coach's AGS/PI/AES all off:
    `19FED9C0#FF96E60F0100D1EA` — `E6 := 1` alone, no `EF`, no read-back twin. If the G12
    enforced `PI <-> AGS` it would have set the mode.

    The positive control is load-bearing and is asserted first: had the G12 rejected a write
    from a non-panel source address, `EF` staying put would have proven nothing.
    """
    path = CAPTURES / "pc21-option-interlock-injection-2026-08-23.log"
    events = list(_capture_events(path))

    injected = [(t, v) for t, k, sel, v in events if k == "bus_write" and sel == 0xE6]
    assert injected == [(pytest.approx(injected[0][0]), 1)], (
        f"PC.21 must contain exactly one E6 write, of value 1; got {injected!r}")
    t_inject = injected[0][0]

    # Nothing else was written -- this is what makes it a single-variable probe.
    assert [(sel, v) for t, k, sel, v in events if k == "bus_write" and sel != 0xE6] == []

    after = [(sel, v) for t, k, sel, v in events if k == "g12_report" and t > t_inject]

    # Positive control: the write landed, from an arbitrary source address.
    pi = [v for sel, v in after if sel == 0xE6]
    assert pi and set(pi) == {1}, f"the injected write did not land: E6 reports {pi!r}"
    assert len(pi) >= 4, f"too few post-injection broadcasts to conclude: {pi!r}"

    # The result: the G12 did not force AGS, and did not touch the companions either.
    assert set(v for sel, v in after if sel == 0xEF) == {0}, "the G12 forced the AGS/AES mode"
    assert set(v for sel, v in after if sel == 0xD7) == {0}
