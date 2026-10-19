"""levelfeed: the LEVEL page's per-tick routine, run instruction by instruction on real 0x0FFB0
bursts captured from the bench sensor on can0 (2026-09-25) and on the spec's edge frames."""
import pytest

from tests import emu
from fireflymcf import levelfeed, readout, register

BASE = 0x25A000
NA = 0xFFFF
BENCH = {1: "01 03 00 00 00 00 04 02", 2: "02 01 01 00 00 FF FF FF",
         3: "03 08 07 01 00 FF FF FF", 4: "04 36 06 AD 02 14 0A 19"}
FAULT = {1: "01 FF FF FF FF DF FF FF", 2: "02 FF FF FF FF FF FF FF",
         3: "03 FF FF FF FF FF FF FF", 4: "04 36 06 AD 02 14 0A 19"}


def _slots(setup=False):
    return {name: register.status_slot_addr(300 + i)
            for i, (name, *_r) in enumerate(levelfeed.vars_for(setup))}


def _cells(setup=False, repaint=False):
    return {name: readout.DisplayCell(700 + i, 0x2003A000 + 4 * i, w, 3, True)
            for i, (name, w) in enumerate(levelfeed.cells_for(setup, repaint))}


def _feed(frames: dict, setup=False):
    """Slot values as FUN_0021C760 would leave them: each var's field from its record, or NA.
    Mode 2 is a u16 at n,n+1; mode 4 is bits 0-1 of byte n; else the whole byte."""
    vals = {}
    for name, rec, byte, mode in levelfeed.vars_for(setup):
        if rec not in frames:
            vals[name] = NA
            continue
        p = bytes.fromhex(frames[rec])
        vals[name] = (p[byte] | p[byte + 1] << 8 if mode == 2 else
                      p[byte] & 3 if mode == 4 else p[byte])
    return vals


def _run(frames, setup=False):
    slots, cells = _slots(setup), _cells(setup)
    plan = levelfeed.LevelFeedPlan(slots=slots, cells=cells, hook_at=0xEE3C,
                                   hook_old=b"\0" * 4, tail_target=BASE + 0x1000)
    code, _ = levelfeed.build(plan, BASE)
    mem = {}
    for name, v in _feed(frames, setup).items():
        mem[slots[name]], mem[slots[name] + 1] = v & 0xFF, v >> 8
    for c in cells.values():                       # cells hold junk before the first tick
        mem[c.addr], mem[c.addr + 1] = 0x5A, 0xA5
    mem = emu.run(code, BASE, mem, stop_at=BASE + 0x1000)
    return {n: mem[c.addr] | mem[c.addr + 1] << 8 for n, c in cells.items()}


def test_bench_burst():
    c = _run(BENCH)
    assert (c["pitch"], c["roll"], c["total"], c["carried"]) == (3, 0, 2, 10)
    assert [c[f"lay_{k}"] for k in ("fl", "fr", "rl", "rr")] == [1, 1, 0, 0]
    assert [c[f"lift_{k}"] for k in ("fl", "fr", "rl", "rr")] == [8, 7, 1, 0]
    assert [c[f"wheel_{k}"] for k in ("fl", "fr", "rl", "rr")] == [1, 1, 0, 0]
    assert (c["banner"], c["short"], c["pitch_word"], c["roll_word"]) == (0, 0, 2, 0)


def test_all_stale():
    c = _run({})
    assert all(c[n] == NA for n in ("pitch", "roll", "total", "carried", "banner",
                                    "lay_fl", "lift_rr", "wheel_fl", "wheel_rr"))
    assert (c["short"], c["pitch_word"], c["roll_word"]) == (0, 0, 0)


def test_partial_stale():
    c = _run({1: BENCH[1], 4: BENCH[4]})            # attitude live, wheels stale
    assert c["banner"] == 0 and c["pitch"] == 3
    assert [c[f"wheel_{k}"] for k in ("fl", "fr", "rl", "rr")] == [NA] * 4
    c = _run({2: BENCH[2], 3: BENCH[3]})            # wheels live, attitude stale
    assert c["banner"] == NA and c["wheel_fl"] == 1


def test_fault_record():
    c = _run(FAULT)
    assert (c["banner"], c["short"], c["pitch_word"], c["roll_word"]) == (2, 0, 0, 0)
    assert c["pitch"] == NA and c["wheel_fl"] == NA and c["carried"] == 10


@pytest.mark.parametrize("frames,checks", [
    ({1: "01 FE FF 05 00 05 11 FE", 2: "02 0C FE 00 FA FF FF FF", 3: "03 FE FA 00 00 FF FF FF"},
     {"pitch": 0xFFFE, "roll": 5, "short": 1, "banner": 1, "total": NA,
      "lay_fl": 12, "lay_fr": NA, "lay_rr": 250, "lift_fl": NA, "lift_fr": 250,
      "wheel_fl": 1, "wheel_fr": NA, "pitch_word": 1, "roll_word": 1}),
    ({1: "01 00 00 00 00 00 40 FD", 2: "02 FD FF 00 01 FF FF FF"},
     {"roll_word": 2, "pitch_word": 0, "total": 0xFD, "lay_fl": 0xFD, "lay_fr": NA,
      "wheel_fl": 1}),
])
def test_edge_values(frames, checks):
    c = _run(frames)
    for k, v in checks.items():
        assert c[k] == v, (k, c[k], v)


# ---- the LEVEL SETUP popup's values (setup, 2026-09-29) ------------------------------------------

def test_setup_values_from_a_burst():
    """Height 14h (1.00 in, 0.05 in units) -> 100 (0.01 in); tolerance 32h -> 50; the auto flag
    from record 02h byte 5 bits 0-1 (FDh -> 01b, auto)."""
    frames = {**BENCH, 2: "02 01 01 00 00 FD FF FF", 4: "04 36 06 AD 02 14 0A 32"}
    c = _run(frames, setup=True)
    assert (c["height"], c["tol"], c["auto"]) == (100, 50, 1)
    assert (c["pitch"], c["carried"], c["wheel_fl"]) == (3, 10, 1)      # the page is unchanged


def test_setup_fixed_tolerance_and_edges():
    c = _run({**BENCH, 2: "02 01 01 00 00 FC FF FF", 4: "04 36 06 AD 02 FA 0A 00"},
             setup=True)
    assert (c["height"], c["tol"], c["auto"]) == (1250, 0, 0)          # 12.50 in, 0.00 in, fixed


def test_setup_na():
    c = _run({**BENCH, 2: "02 01 01 00 00 FF FF FF", 4: "04 36 06 AD 02 FE 0A FF"},
             setup=True)
    assert (c["height"], c["tol"], c["auto"]) == (NA, NA, NA)           # FEh / FFh / 11b
    c = _run({}, setup=True)
    assert (c["height"], c["tol"], c["auto"]) == (NA, NA, NA)           # stale


# ---- repaint-on-change (v43) -----------------------------------------------------------------------

def test_repaint_toggle_flips_only_when_a_value_changes():
    """Tick by tick on one memory image: the first tick normalises junk toggles to 0/1; a repeat
    value leaves every toggle alone; a changed height flips only height_t."""
    slots, cells = _slots(True), _cells(True, True)
    plan = levelfeed.LevelFeedPlan(slots=slots, cells=cells, hook_at=0xEE3C,
                                   hook_old=b"\0" * 4, tail_target=BASE + 0x1000)
    code, _ = levelfeed.build(plan, BASE)
    mem = {}
    for c in cells.values():
        mem[c.addr], mem[c.addr + 1] = 0x5A, 0xA5                       # junk before tick 1

    def tick(frames):
        nonlocal mem
        for name, v in _feed(frames, True).items():
            mem[slots[name]], mem[slots[name] + 1] = v & 0xFF, v >> 8
        mem = emu.run(code, BASE, mem, stop_at=BASE + 0x1000)
        return {n: mem[c.addr] | mem[c.addr + 1] << 8 for n, c in cells.items()}

    setup = {**BENCH, 2: "02 01 01 00 00 FD FF FF", 4: "04 36 06 AD 02 14 0A 32"}
    names = levelfeed.refresh_names(True, True)
    t1 = tick(setup)
    assert all(t1[f"{n}_t"] in (0, 1) for n in names)                  # junk -> 0/1
    assert all(t1[f"{n}_p"] == t1[n] for n in names)
    t2 = tick(setup)
    assert all(t2[f"{n}_t"] == t1[f"{n}_t"] for n in names)            # nothing changed
    t3 = tick({**setup, 4: "04 36 06 AD 02 37 0A 32"})                 # height 1.00 -> 2.75 in
    assert t3["height"] == 275 and t3["height_t"] == 1 - t2["height_t"]
    assert all(t3[f"{n}_t"] == t2[f"{n}_t"] for n in names if n != "height")
