"""First-class register ops (register.py, followchannel.plan_from_registers, the recipe ops).

Task 1 is a characterization guard: the experiment build must keep compiling to the shipped,
hardware-verified v9 image byte-for-byte through this whole refactor.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.conftest import compiled_image
from fireflymcf import g12_config, tankbroadcast

REPO = Path(__file__).resolve().parent.parent
# The shipped v9 (the reference coach's build log (not published) "Pump Link ... (v9) HARDWARE-VERIFIED"): golden-v22-anchor.ffproj.json
# compiled against firefly-13.6/mcf.bin. The refactor must not move one byte of this.
V9_SHA256 = "84531793b1e3427e62d891e06536e6459ad394701fe9a36a7449255c0f266167"
# The FULL golden-v22-anchor.ffproj.json build minus `navslides` -- v13 plus the Water Pump section
# wired in (Task 11) -- add_pump_auto_off ("pumpauto") run before swap_aes_electrical ("aesswap",
# passed pump_section={"ref": "pumpauto"}), and pumplink's add_option_follower masked
# (gate_mask=0xFF00, gate_on_value=0x0100) so its gate survives the widened 0xEE slot. This is
# the op subset the reference coach's build log (not published)'s "Experimental v14" entry describes and the flashed image
# `mcf-experiment-v14.bin` (sha256 `fa89c04b…`, see that entry's "what is actually on the coach"
# note) was built from a commit on this lineage. BUT this test no longer reproduces those flashed
# bytes: v16 Task 1 (commit 7b4fd92, 2026-09-04, "a zero minute byte pins to the floor and keeps
# the link flag -- v15 defect 2") rewrote the pump routine's minute-0 clamp, and Task 5
# (`swap_aes_electrical`'s `link_indicator` param) changed what `aesswap` itself emits for this
# op set too (both params are set in `builds/golden-v22-anchor.ffproj.json` unconditionally, so "minus
# navslides" still carries them) -- so this constant is **today's tools compiling v14's op
# subset**, not a historical oracle for the flashed v14 image. An intermediate build is
# reproducible from this tree when it is an op-filtered subset of today's recipe -- the way the
# v9 oracle above is -- and only for as long as nothing in tools/ moves what one of its ops
# emits. That is a real limit, not a formality: v10 and v11 are exactly how it fails, because the
# v12 pill shift changed what `swap_aes_electrical` itself emits, so no subset of this recipe can
# produce them again; the v16 clamp fix is the same failure mode a second time, this time
# deliberately (the fix is the point of Task 1, not a regression). Today v12 (`4cb8ad12...`) and
# v13 (`43c6677b...`) do both still reproduce as of the commits that built them, and
# `tests/test_pumpauto.py` reconstructs v12 that way to get the pre-append image its scratch
# allocation is derived from -- pinned to that sha, so it fails loudly the day it stops.
# The fallback if any reconstruction stops working is the build commit itself: b222f0e builds
# v10, aa59254 builds v12, and 00516ee builds v13. (That last hash was wrong until 2026-09-03 --
# it read 13c09ae, which is a Task 13 fix round that never touches builds/golden-v22-anchor.ffproj.json
# and is not on the build lineage at all. Verified: 00516ee is titled "-- v13" and does modify
# the recipe. A fallback pointer nobody has followed is worth re-checking before it is needed.)
V14_SHA256 = "cd5f4875c99ade64be8b1d9ed0afb550308e73a466d8467dc162a76a3a425ece"
# v17: v16 (v15 plus Task 1's minute-0 clamp fix and Task 5's `link_indicator` wiring on
# `aesswap`) plus `minutes-home` (Task 6 of the register-readout plan, `add_register_readout`):
# the Auto Off minute byte of DGN_CONFIG selector 0xEE, shown on HOME at (300, 357) -- Fix round
# 1 moved it down from (300, 345) after review found the glyph cell (y 325-351 at 345) flush
# with the Awning bar's last drawn row (325); at 357 the cell is y 337-363, 11 px clear of the
# bar and 19 px above object 1409's gated region (y >= 382). A tier-2 display cell
# (219 @ 0x20039f51), a 44-byte per-tick copy routine chained onto the hook ahead of
# aeslowvolts/pumpauto/pumplink, and a two-record (directive + readout) object attached to HOME's
# root (object 4). the reference coach's build log (not published) "Experimental v17". BUILT NOT FLASHED.
V17_SHA256 = "b32913a492d6c4a694d1a5a00e69899a752e0676b25e71cf076ad562f9876d98"
# v18: v17 plus the Absorb/Float rows made right for a 48 V coach (owner 2026-09-04):
# `rows-permanent` (pin_switch_arm: object 17 record 11 pinned to arm 303 by the firmware's
# default-arm rule, three same-size u16 edits), and `absorb-rows`/`float-rows`
# (add_register_readout in its NEW `cell_slot` mode: feed the rows' own display cells 162/163
# from 0x1FECC vars 206/207 with the panel's (raw+1)>>1 rounding, and silence FUN_00247BF8's
# per-tick config-byte stores at 0x247C34/0x247C3A that would overwrite them). Two 40-byte
# routines chained ahead of minutes-home; no object authored. the reference coach's build log (not published) "Experimental
# v18". FLASHED AND VERIFIED 2026-09-05 (PC.60).
V18_SHA256 = "5340767d743aec333544d016d5dde31185e503e680b2a791d3d0d4866552294b"
V18_NEW_OPS = ["rows-permanent", "absorb-rows", "float-rows"]
# v19: v18 plus `shore-brkr-minus` (fix_variant_gate, mode "selector"): var 213's compare byte
# 0x95 -> 0x99, restoring 11.23's gate on the breaker size so the `-` is disabled only at 0 A
# (`GUICODE.md` Finding 68). One GuiData byte plus the section checksum. BUILT NOT FLASHED.
V19_SHA256 = "dcdd1372be49e70bf9b09e653f720aacd9b70492ccf6ae59a073cfdcdc5a3b7b"
V19_NEW_OPS = ["shore-brkr-minus"]
# v20: v19 plus the Auto Off timer. Two new ops -- `timer-glyph` (install_glyph: the timer face
# into slot 13, the HOME caption font) and `pump-timer-mark` (add_corner_mark: the glyph in the
# HOME Water Pump button's lower-right corner while the countdown runs) -- and two parameter
# changes on ops that already existed: `pumpauto.allow_disable` lowers the minute floor to 0 so 0
# disables Auto Off (and turns on the borrow recovery that floor needs), `aesswap.off_at_zero`
# swaps the AES page's numeric readout for a two-arm switch reading 'Off' at 0.
#
# THE PARAMETER CHANGES ARE WHY `_without_v20` EXISTS. Every pin below compiles the CURRENT
# project file with later ops filtered out, which works only while a later version adds ops. v20
# also flips two flags, and leaving them set would move v14-v19's routine bytes and their pins
# with it -- so the flags are stripped as well, and both default to the pre-v20 behaviour
# precisely so that stripping them is enough. BUILT NOT FLASHED.
# **v20 WAS FLASHED and is superseded by v21** -- its recorded sha is kept only as history, and
# NOT pinned by a test, because the recipe no longer produces it: `cornermark.build_mark_record`
# now declares a box (v20 drew one column of the glyph without one) and `aesswap.off_at_zero` is
# gone (v20's swapped-in 'Off' ghosted over the number it replaced; the owner accepted the plain
# `0`). Round-1 findings are in the reference coach's build log (not published) "Experimental v20".
V20_SHA256_SUPERSEDED = "b90c3e148f21e9ff3aaa33aa652bb0e738660b705ca02aa99a26a76aba98b48e"
V20_SHA256 = "4b639367be48c06876e809231583b44f160b7ddfd3d35ecc887cb760598c77b8"
V20_NEW_OPS = ["timer-glyph", "pump-timer-mark"]
V20_PARAM_CHANGES = {"pumpauto": ["allow_disable"]}


# Ops that v19 and earlier COMPILED and v21 ships disabled. `minutes-home` -- v17's bare minute
# count on HOME -- was switched off at the owner's request once the timer got its own indicator
# (2026-09-06). It stays in the project file with `enabled: false` rather than being deleted
# precisely so the pins below can put it back: v17, v18 and v19 all carry it, and v18 is the image
# that was running on the coach.
V21_DISABLED_OPS = ["minutes-home"]

# v22: v21 plus `no-tank-bcast` (suppress_tank_status) -- four 2-byte NOPs in the G12 code blob
# that stop the G12 activating its `1FFB7` TANK_STATUS rules for instances 0/1/2/18, freeing
# those instances for a replacement RV-C tank sender (`tankbroadcast.py`). The ONLY section it
# touches is G12, which no other op in the recipe reads or writes.
#
# It also renames the build, and that is why `_at_v21` restores the name rather than only
# dropping the op: `build_name` lands in the header at 0x24 and moves the header checksum, so a
# rename alone would drift every pin below for a reason that has nothing to do with its ops.
V22_NEW_OPS = ["no-tank-bcast"]
# Every op in the recipe is hardware-verified as of 2026-09-07 (`BUS-BASELINE.md` PC.62): the
# coach showed `shore-brkr-minus`'s `-` transmitting byte-exact, and v22 flashed with zero
# `19FFB7` on the bus. So `unverified` is empty on EVERY pin below, including the older ones --
# the flag is a property of the op class, not of the image, so promoting one clears it
# retroactively. A future op that ships unverified will fail these; that is the point.
UNVERIFIED_OPS: list[str] = []
V21_BUILD_NAME = "Jayco B-Van V13 (Experimental)"
V22_SHA256 = "008a9f7d85c8fbc1673892011c000a033d4d81eb9f2eeea23debd0c0e9554d06"


def _at_v21(doc):
    """The project as it stood at v21: v22's op dropped and v21's build name restored."""
    doc["ops"] = [o for o in doc["ops"] if o["id"] not in V22_NEW_OPS]
    doc["build_name"] = V21_BUILD_NAME
    return doc


def _without_v20(doc):
    """The project as it stood at v19: v20's ops dropped, v20's flags cleared, and the ops v21
    disabled switched back on. Rolls back through v22 first, so every pre-v20 pin is taken
    against v21's build name."""
    doc = _at_v21(doc)
    doc["ops"] = [o for o in doc["ops"] if o["id"] not in V20_NEW_OPS]
    for o in doc["ops"]:
        if o["id"] in V21_DISABLED_OPS:
            o["enabled"] = True
        for key in V20_PARAM_CHANGES.get(o["id"], ()):
            o["params"].pop(key, None)
    return doc


def test_experiment_compile_is_byte_stable():
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _without_v20(json.loads(
        (REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    # v9 predates the AES page ops added 2026-09-01 (`aesswap`, `aeslowvolts`, `invgear`), the
    # `1FFC6` filter pin added for v13 (`pin125`), and the whole Water Pump Auto Off feature
    # wired in for v14 (Task 11): the routine op itself (`pumpauto`) and the three `move_element`
    # edits that park the old Pump Link OPTIONS row (Task 10, `pumprow-switch`/`pumprow-label`/
    # `pumprow-touch`). The v9 oracle is the recipe WITHOUT any of them.
    # `minutes-home` (v17, Task 6 of the register-readout plan) is excluded the same way
    # `navslides` was when IT arrived: it is a brand-new, unconditionally-enabled op appended
    # after every op this subset already keeps, so leaving it in would move the v9 pin for a
    # reason that has nothing to do with v9.
    doc["ops"] = [o for o in doc["ops"]
                  if o["id"] not in ("aesswap", "aeslowvolts", "invgear", "pin125", "pumpauto",
                                     "pumprow-switch", "pumprow-label", "pumprow-touch",
                                     "navslides", "minutes-home",
                                     "rows-permanent", "absorb-rows", "float-rows",
                                     "shore-brkr-minus")]
    # `pumplink` (add_option_follower) itself predates v9 and stays in the subset, but its
    # gate_mask/gate_on_value params were added for v14 (PC.47) -- v9's shipped image gates on
    # the whole halfword unmasked (the byte-only 0xEE extraction mode was never widened until
    # `pumpauto`), so the v9 oracle must compile it with the pre-Task-11 defaults, not today's
    # masked params.
    for o in doc["ops"]:
        if o["id"] == "pumplink":
            o["params"].pop("gate_mask", None)
            o["params"].pop("gate_on_value", None)
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V9_SHA256, \
        "experiment build is no longer byte-identical to the shipped v9 — the refactor moved a byte"


def test_experiment_v14_is_byte_stable():
    """v14's op subset (the recipe WITHOUT `navslides` or `minutes-home`), compiled with TODAY's
    tools.

    This is no longer an oracle for the flashed `mcf-experiment-v14.bin` (sha256 `fa89c04b…`):
    v16 Task 1 rewrote the pump routine's minute-0 clamp (commit 7b4fd92, v15 defect 2) and
    Task 5 added `link_indicator` to `aesswap` (both changes apply unconditionally in
    `builds/golden-v22-anchor.ffproj.json`, so this op subset carries them too), so recompiling this
    same op subset today produces different bytes than the image that was actually flashed. It
    still pins that today's tools compile this subset deterministically. `pumpauto` is back in
    the unverified manifest at the time it was written, but v16 was itself flashed as part of
    v17 and verified (the reference coach's build log (not published) "Experimental v16" -> "### v16 FLASHED AND VERIFIED
    (as part of v17), 2026-09-04"): the clamp fix (item 8 of the checklist, "Only goes 5-60")
    and the Pump Link switch/Low Volts decoupling (items 5-7) both passed, so
    `add_pump_auto_off` and `swap_aes_electrical`'s per-instance flags are both back to
    `hardware_verified = True` and this op subset reports neither unverified. `minutes-home`
    (v17, Task 6 of the register-readout plan) is excluded for the same reason `navslides`
    already was: it is newer than this subset's own oracle and would move the pin for a reason
    unrelated to v14.
    """
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _without_v20(json.loads(
        (REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    doc["ops"] = [o for o in doc["ops"] if o["id"] not in (
        "navslides", "minutes-home", "rows-permanent", "absorb-rows", "float-rows",
        "shore-brkr-minus")]
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V14_SHA256, \
        "v14's op subset, compiled with today's tools, no longer matches -- update the sha and " \
        "this docstring if the change is intentional"
    # the reference coach's build log (not published) "Experimental v16" -> "### v16 FLASHED AND VERIFIED (as part of v17),
    # 2026-09-04": the clamp fix ran on the coach, so pumpauto is verified again.
    assert "pumpauto" not in image.unverified
    assert "aesswap" not in image.unverified, \
        "the Water Pump section (and the v16 Pump Link switch) were seen on v17's coach run"


def test_experiment_v17_is_byte_stable():
    """v17's op subset -- the recipe WITHOUT v18's three ops -- compiled with today's tools.
    v17 = v16 (v15 + Task 1's minute-0 clamp fix + Task 5's `link_indicator` wiring on
    `aesswap`) plus `minutes-home` (the first on-screen register readout, Task 6 of the
    register-readout plan). the reference coach's build log (not published) "Experimental v17". This is the image ON THE
    COACH, so its pin is kept exact even though the full recipe has moved on to v18.

    v14's op subset keeps its own oracle above, built from the recipe MINUS `navslides` and
    `minutes-home`, because it is the entry the reference coach's build log (not published) describes and the one the
    verification checklist targets -- though, per that test's docstring, it no longer reproduces
    the flashed v14 bytes either, for reasons unrelated to this op.

    v17 itself was FLASHED AND VERIFIED 2026-09-04 (the reference coach's build log (not published) "Experimental v17" ->
    "### v17 FLASHED AND VERIFIED, 2026-09-04"; `docs/BUS-BASELINE.md` PC.59): all eleven
    checklist items passed, including the HOME readout matching the AES page's own minutes
    (items 1-4) and the dashes-then-value sentinel path predicted by `GUICODE.md` Finding 65
    (item 3). So none of this build's three previously-unverified ops report unverified any
    more.
    """
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _without_v20(json.loads(
        (REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    doc["ops"] = [o for o in doc["ops"] if o["id"] not in V18_NEW_OPS + V19_NEW_OPS]
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V17_SHA256, \
        "v17's op subset is no longer byte-identical to the image on the coach"
    # `show_nav_slides_icon` was coach-verified 2026-09-03 (the build log), so it must not report
    # unverified -- this fails if the flag is ever flipped back without a reason.
    assert "navslides" not in image.unverified
    # v17 FLASHED AND VERIFIED, 2026-09-04 (the reference coach's build log (not published), docs/BUS-BASELINE.md PC.59): the
    # clamp fix, the Pump Link switch and the first on-screen register readout (a tier-2 cell)
    # all ran on the coach, so none of these three report unverified any more.
    assert "pumpauto" not in image.unverified
    assert "aesswap" not in image.unverified
    assert "minutes-home" not in image.unverified



def test_experiment_v18_is_byte_stable():
    """v18's op subset -- the recipe WITHOUT v19's op -- v17 plus `rows-permanent`, `absorb-rows` and `float-rows` -- the
    Absorb/Float rows on `INVERTER / CHARGER SETTINGS` made permanent and fed from `0x1FECC`
    past the stock decode's 12-18 V fence (`BUS-BASELINE.md` PC.55). the reference coach's build log (not published)
    "Experimental v18". FLASHED AND VERIFIED 2026-09-05 (`docs/BUS-BASELINE.md` PC.60): rows
    present after the flash and after a power cycle, dashes with nothing publishing, 57.6/54.0 V
    from an injected frame -- so none of the three new ops report unverified."""
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _without_v20(json.loads(
        (REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    doc["ops"] = [o for o in doc["ops"] if o["id"] not in V19_NEW_OPS]
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V18_SHA256, \
        "v18's op subset is no longer byte-identical to the image on the coach"
    assert list(image.unverified) == []


def test_experiment_v19_is_byte_stable():
    """v18 plus `shore-brkr-minus`. the reference coach's build log (not published) "Experimental v19". `shore-brkr-minus` was the
    only unverified op here until 2026-09-07, when the coach showed the `Shore Brkr Size` `-`
    transmitting `19FED99F#FF96990FFBFFD2FF` (PC.62)."""
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _without_v20(json.loads((REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V19_SHA256, \
        "v19's op subset is no longer byte-identical to the recorded v19"
    assert list(image.unverified) == UNVERIFIED_OPS


def test_experiment_v21_is_byte_stable():
    """The FULL recipe: v19 plus the Auto Off timer -- the mark on HOME (boxed, after v20's
    round-1 clipping) and the 0-disables floor, with v17's `minutes-home` count switched off.
    the reference coach's build log (not published) "Experimental v21"; FLASHED AND VERIFIED 2026-09-06 (`BUS-BASELINE.md`
    PC.61). `shore-brkr-minus` is the only op still reporting unverified: `pump-timer-mark`
    became hardware-verified with this flash."""
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = _at_v21(json.loads((REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text()))
    image = compiled_image(json.dumps(doc), stock)
    assert hashlib.sha256(image.data).hexdigest() == V20_SHA256, \
        "the full experiment build is no longer byte-identical to the recorded v21"
    assert list(image.unverified) == UNVERIFIED_OPS


def test_experiment_v22_is_byte_stable():
    """The FULL recipe: v21 plus `no-tank-bcast`. the reference coach's build log (not published) "Experimental v22".
    FLASHED AND VERIFIED 2026-09-07 (PC.62): zero `19FFB7` on the bus, the raw-ADC and LPG
    broadcasts untouched, and an injected foreign sender driving HOME's FRESH gauge."""
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    image = compiled_image((REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text(), stock)
    assert hashlib.sha256(image.data).hexdigest() == V22_SHA256, \
        "the full experiment build is no longer byte-identical to the recorded v22"
    assert list(image.unverified) == UNVERIFIED_OPS


def test_v22_touches_only_the_g12_section_of_v21():
    """The suppression's whole blast radius, measured rather than argued (the standing rule).

    Eight instruction bytes plus the G12 section checksum, and nothing outside that section
    except the build-name field in the header -- so v22 carries every v21 feature by
    construction, not by assertion."""
    stock = (REPO / "firefly-13.6" / "mcf.bin").read_bytes()
    doc = json.loads((REPO / "builds" / "golden-v22-anchor.ffproj.json").read_text())
    v22 = compiled_image(json.dumps(doc), stock).data
    v21 = compiled_image(json.dumps(_at_v21(json.loads(json.dumps(doc)))), stock).data
    assert len(v21) == len(v22)

    differing = {i for i in range(len(v21)) if v21[i] != v22[i]}
    g12 = g12_config.parse_g12(v22)
    section = set(range(g12.file_offset, g12.file_offset + g12.length))
    header = set(range(0x100))                      # build name at 0x24 + the header checksum
    assert differing <= section | header, \
        "v22 differs from v21 outside the header and the G12 section: %r" % sorted(
            differing - section - header)

    # Inside the section: exactly the four `blx r3` sites, and the section checksum.
    sites = {s + k for b in tankbroadcast.tank_broadcasts(v21)
             if b.instance in (0, 1, 2, 18) for s in (b.activate_file,) for k in (0, 1)}
    assert len(sites) == 8
    checksum = set(range(g12.file_offset + g12.length - g12_config.CHECKSUM_SIZE,
                         g12.file_offset + g12.length))
    assert (differing & section) - sites <= checksum, \
        "unexpected G12 bytes changed: %r" % sorted((differing & section) - sites - checksum)
    assert sites <= differing, "an activation site did not change"

    assert sorted(tankbroadcast.suppressed_instances(v22)) == [0, 1, 2, 18]
    assert tankbroadcast.suppressed_instances(v21) == []
    # The LPG rule is deliberately untouched -- its sender is real.
    assert 3 not in tankbroadcast.suppressed_instances(v22)
