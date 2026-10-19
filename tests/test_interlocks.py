"""Removing the OPTIONS cross-option interlocks (`fireflymcf.interlocks`).

`docs/GUICODE.md` Finding 50: four of 13.6's OPTIONS rows write another row's selector as well
as their own, which is why "Progressive Inverter *and* AES" is a state the stock panel cannot be
driven to from its own screen. `uncouple` NOPs the 36-byte emit pair behind each cross-write.

The expectations here are the finding's table, cited from the disassembly. They are an oracle,
not a search target — nothing in the module reads this file.
"""
from __future__ import annotations

import pytest

from fireflymcf import interlocks, patch

capstone = pytest.importorskip("capstone")


def _writes(data, name):
    entry = interlocks.CROSS_EMISSIONS[name][0]
    return [(e.selector, e.value) for e in interlocks.run_emissions(data, entry) if e.write]


# Finding 50's table: what each coupled arm emits in STOCK 13.6, writes only.
STOCK_WRITES = {
    "pi-forces-ags":        [(0xE6, 1), (0xEF, 1)],
    "ags-forces-pi":        [(0xEF, 1), (0xD7, 0), (0xE6, 1), (0x15, 0x2801), (0xF7, 0x3C)],
    "aes-clears-pi":        [(0xEF, 2), (0xD7, 0), (0xE6, 0), (0x15, 0x2801), (0xF7, 0x3C)],
    "aes-single-clears-pi": [(0xEF, 3), (0xD7, 0), (0xE6, 0), (0x15, 0x2801), (0xF7, 0x3C)],
}


@pytest.mark.parametrize("name", sorted(STOCK_WRITES))
def test_stock_arm_emits_the_finding_50_table(mcf_136, name):
    assert _writes(mcf_136, name) == STOCK_WRITES[name]


@pytest.mark.parametrize("name", sorted(STOCK_WRITES))
def test_uncouple_drops_exactly_the_foreign_write(mcf_136, name):
    """The patched arm must be its stock list minus the one foreign selector — not "minus
    everything that is not its own", which would also take the `D7`/`0x15`/`F7` companions the
    AGS and AES rows legitimately keep."""
    out, _edits = interlocks.uncouple(mcf_136, [name])
    foreign = interlocks.CROSS_EMISSIONS[name][2]
    assert _writes(out, name) == [w for w in STOCK_WRITES[name] if w[0] != foreign]


def test_uncouple_is_same_size_and_touches_only_guicode(mcf_136):
    out, edits = interlocks.uncouple(mcf_136)
    assert len(out) == len(mcf_136), "an interlock removal must not change the image length"
    assert len(edits) == 4
    assert all(len(old) == len(new) == 36 for _off, old, new, _why in edits)
    # Every changed byte must land inside GuiCode: a stray edit into GuiData would need
    # relocation and is exactly what "same-size GuiCode patch" is claiming not to do.
    from fireflymcf import theme
    sec = theme.guicode_section(mcf_136)
    changed = [i for i in range(len(out)) if out[i] != mcf_136[i]]
    assert changed, "nothing was written"
    assert min(changed) >= sec.offset and max(changed) < sec.offset + sec.length
    assert len(changed) <= 4 * 36


def test_uncouple_leaves_the_other_arms_alone(mcf_136):
    """The deselect arms are not edited, and no NOP run may eat a neighbouring row's frame.

    Without this, a mis-located block could blank a *different* row's emission and every
    other check here would still pass — they only look at the four arms being changed.
    """
    out, _ = interlocks.uncouple(mcf_136)
    targets = {a: f for a, _o, f, _w in interlocks.CROSS_EMISSIONS.values()}
    for arm in interlocks.ALL_MODE_ARMS:
        was = [(e.selector, e.value, e.write) for e in interlocks.run_emissions(mcf_136, arm)]
        now = [(e.selector, e.value, e.write) for e in interlocks.run_emissions(out, arm)]
        dropped = [x for x in was if x not in now]
        allowed = targets.get(arm)
        assert all(sel == allowed for sel, _v, _w in dropped), \
            "arm %#x dropped %s, only %s was authorised" % (arm, dropped, allowed)


def test_deselect_arms_are_untouched(mcf_136):
    """Stock's deselect arms already leave the partner set — that asymmetry is owner-observed
    (`BUS-BASELINE.md` PC.21 Result 4) and this patch is not meant to change it."""
    out, _ = interlocks.uncouple(mcf_136)
    for arm in (0x0024CD4C, 0x0024D0D8, 0x0024D1E8, 0x0024D214):
        before = [(e.selector, e.value, e.write) for e in interlocks.run_emissions(mcf_136, arm)]
        after = [(e.selector, e.value, e.write) for e in interlocks.run_emissions(out, arm)]
        assert before == after, "deselect arm %#x changed" % arm


def test_uncouple_is_idempotent_only_by_refusing(mcf_136):
    """Re-running on an already-patched image must raise, not silently do nothing.

    A no-op second run would let a build apply the same edit twice and still look clean; the
    "exactly one write of this selector" precondition is what makes the second run loud.
    """
    out, _ = interlocks.uncouple(mcf_136)
    with pytest.raises(ValueError, match="expected exactly one"):
        interlocks.uncouple(out)


def test_uncouple_refuses_an_unknown_name(mcf_136):
    with pytest.raises(ValueError, match="unknown interlock"):
        interlocks.uncouple(mcf_136, ["no-such-coupling"])


def test_uncouple_refuses_when_the_shape_is_not_the_emit_idiom(mcf_136):
    """The shape check is the whole safety argument for NOP-ing bytes by address, so it has to
    actually fire. Corrupt the first instruction of the PI arm's foreign block and the patch
    must refuse rather than blank whatever is there."""
    from fireflymcf import theme
    sec = theme.guicode_section(mcf_136)
    base = theme.guicode_base(sec)
    emit = next(e for e in interlocks.run_emissions(mcf_136, 0x0024CCFA)
                if e.selector == 0xEF and e.write)
    broken = bytearray(mcf_136)
    off = sec.offset + (emit.at - base)
    broken[off + 4:off + 6] = b"\x00\xbf"       # turn the `ldm.w` into a nop + garbage
    with pytest.raises(ValueError, match="does not match the expected shape"):
        interlocks.uncouple(bytes(broken), ["pi-forces-ags"])


def test_patched_image_reseals_to_a_single_guicode_checksum(mcf_136):
    """A GuiCode-only edit must invalidate exactly one stored checksum. More would mean the
    edit strayed out of the section; fewer would mean nothing was written."""
    out, _ = interlocks.uncouple(mcf_136)
    _sealed, changed = patch.reseal(out)
    assert changed == ["section-checksum:Lyra 13v6 GuiCode"], changed


def test_find_coupled_handlers_reproduces_13_6_exactly(mcf_136):
    """The structural locator is the oracle test: on 13.6 it must find exactly the four
    CROSS_EMISSIONS couplings, by name, with no per-build addresses. This is what licenses trusting
    its *absence* result on other builds."""
    found = interlocks.find_coupled_handlers(mcf_136)
    assert set(found) == set(interlocks.CROSS_EMISSIONS)
    # each located run actually emits both the mode (0xEF) and Progressive Inverter (0xE6) selectors
    for name, emissions in found.items():
        sels = {e.selector for e in emissions if e.write}
        assert interlocks.MODE_SELECTOR in sels and 0xE6 in sels


def test_11_23_predates_aes_so_has_no_coupling_and_uncouple_is_a_no_op(corpus):
    """11.23 has no `0xE6` Progressive-Inverter template at all -- it predates AES, so there is no
    mode<->option coupling. `uncouple` is a clean, honest no-op: nothing to remove on this coach."""
    data = corpus["11.23"]["mcf"]
    assert interlocks.has_interlocks(data) is False
    assert interlocks.find_coupled_handlers(data) == {}
    out, edits = interlocks.uncouple(data)
    assert out == data and edits == []


def test_14_7_has_a_different_coupling_mode_to_a_new_option(corpus):
    """14.7 does NOT couple Progressive Inverter to the mode (0xE6 is present but independent here);
    instead it couples the mode to a NEW toggle 0xE7 (mutually exclusive with AGS). The generalized
    structural detector finds it -- so this is a real interlock, not the 'no coupling' a 0xE6-only
    detector would wrongly report. `uncouple` refuses to EDIT it (removal is not validated for this
    build / the option is unidentified), rather than silently no-op'ing a coupling that is present."""
    data = corpus["14.7"]["mcf"]
    coupled = interlocks.find_coupled_handlers(data)
    # both arms of the mutual exclusion between the mode and the new 0xE7 toggle:
    assert set(coupled) == {"mode1-writes-0xe7", "0xe7-writes-mode"}
    # selecting the 0xE7 option clears the mode (EF := 0)
    e7_arm = {(e.selector, e.value) for e in coupled["0xe7-writes-mode"] if e.write}
    assert (0xE7, 1) in e7_arm and (interlocks.MODE_SELECTOR, 0) in e7_arm
    # selecting AGS (mode := 1) clears the 0xE7 option
    ags_arm = {(e.selector, e.value) for e in coupled["mode1-writes-0xe7"] if e.write}
    assert (interlocks.MODE_SELECTOR, 1) in ags_arm and (0xE7, 0) in ags_arm
    # 0xE6 (Progressive Inverter) is present but NOT coupled here -- unlike 13.6
    assert not any("0xe6" in k for k in coupled)
    assert interlocks.has_interlocks(data) is True
    with pytest.raises(interlocks.BuildNotSupported):     # coupling present, removal not validated
        interlocks.uncouple(data)


def test_option_for_selector_proves_e7_is_lithionics_on_14_7(corpus, mcf_136):
    """Prove the coupled selector's identity, not fit it: a config selector's select handler gates
    on the option's stored-state register, which is what the OPTIONS widget is bound to. Validated
    on the 13.6 oracle (0xE6 -> Progressive Inverter) and used to settle 14.7: its coupled 0xE7 is
    the Lithionics Batteries option (and 0xE6 there is still Progressive Inverter, just decoupled)."""
    assert interlocks.option_for_selector(mcf_136, 0xE6) == "Progressive Inverter"
    data = corpus["14.7"]["mcf"]
    assert interlocks.option_for_selector(data, 0xE7) == "Lithionics Batteries"
    assert interlocks.option_for_selector(data, 0xE6) == "Progressive Inverter"


def test_13_6_has_the_coupling_and_uncoupling_removes_it(mcf_136):
    assert interlocks.has_interlocks(mcf_136) is True
    assert set(interlocks.find_coupled_handlers(mcf_136)) == set(interlocks.CROSS_EMISSIONS)
    out, edits = interlocks.uncouple(mcf_136)
    assert len(edits) == 4                                        # the four couplings removed
    assert interlocks.has_interlocks(out) is False               # and gone afterwards
