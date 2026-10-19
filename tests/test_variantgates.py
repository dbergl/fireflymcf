"""`variantgates`: controls GuiCode enables/disables per tick from a receive slot -- the
`Shore Brkr Size` `-` defect (`GUICODE.md` Finding 68) and its two one-byte fixes."""
from __future__ import annotations

import pytest

from fireflymcf import guidata, register, statusbind, thumb, variantgates as vg

BUILDS = {"11.23": "firefly-11.23/mcf.bin", "13.6": "firefly-13.6/mcf.bin",
          "14.7": "firefly-14.7 (Terrain)/mcf.bin"}
SHORE_BRKR_MINUS = 199
VAR = 213
SEED_FLAG, BREAKER_SIZE = 0x95, 0x99


@pytest.fixture(scope="module")
def corpus() -> dict:
    return {k: open(p, "rb").read() for k, p in BUILDS.items()}


def test_13_6_gates_exactly_one_control_and_it_is_the_shore_brkr_minus(corpus) -> None:
    gates = vg.gated_variants(corpus["13.6"])
    assert len(gates) == 1
    g = gates[0]
    assert (g.call_at, g.variant_at, g.group, g.variant) == (0x24A616, 0x24A612, SHORE_BRKR_MINUS, 1)
    assert (g.var_index, g.selector, g.is_config, g.disabling) == (VAR, SEED_FLAG, True, True)
    assert g.group_header[:2] == b"\x11\x80"           # externally selected, variants 0..1
    # the slot the gate reads is the one the var table feeds for var 213 (index-split rule)
    assert register.status_slot_addr(VAR) == 0x20007FC8 + 2 * VAR == 0x20008172


def test_11_23_has_the_same_gate_on_the_breaker_size_itself(corpus) -> None:
    gates = vg.gated_variants(corpus["11.23"])
    assert len(gates) == 1
    g = gates[0]
    assert (g.group, g.variant, g.var_index, g.selector, g.disabling) == (SHORE_BRKR_MINUS, 1, VAR, BREAKER_SIZE, True)


def test_14_7_keeps_the_group_but_never_gates_it(corpus) -> None:
    assert vg.gated_variants(corpus["14.7"]) == []
    gt = guidata.group_table(corpus["14.7"])
    assert bytes(gt[SHORE_BRKR_MINUS].raw[:2]) == b"\x11\x80"


def test_the_disabling_arm_really_has_no_bindings(corpus) -> None:
    assert vg._bindings_variants(corpus["13.6"], SHORE_BRKR_MINUS) == {0}
    assert vg._bindings_variants(corpus["13.6"], 200) == {0}     # the working `+` twin


def test_ungate_is_one_guarded_byte(corpus) -> None:
    data = corpus["13.6"]
    out, edits = vg.ungate(data, SHORE_BRKR_MINUS)
    assert len(edits) == 1 and edits[0][1] == b"\x01\x21" and edits[0][2] == b"\x00\x21"
    assert len(out) == len(data)
    assert [i for i in range(len(data)) if data[i] != out[i]] == [edits[0][0]]
    after = [g for g in vg.gated_variants(out) if g.group == SHORE_BRKR_MINUS]
    assert [(g.variant, g.disabling) for g in after] == [(0, False)]
    with pytest.raises(vg.VariantGateError):
        vg.ungate(out, SHORE_BRKR_MINUS)                    # already variant 0
    with pytest.raises(vg.VariantGateError):
        vg.ungate(data, 200)                                # not gated


def test_restore_selector_is_one_guarded_byte_matching_11_23(corpus) -> None:
    data = corpus["13.6"]
    out, edits = vg.restore_gate_selector(data, SHORE_BRKR_MINUS, BREAKER_SIZE)
    assert len(edits) == 1 and edits[0][1] == bytes([SEED_FLAG]) and edits[0][2] == bytes([BREAKER_SIZE])
    gs, _ = guidata.guidata_extent(data)
    assert edits[0][0] == gs + guidata.variable_table(data)[VAR].offset + statusbind.FILTER_INSTANCE_FIELD
    assert len(out) == len(data)
    g = [g for g in vg.gated_variants(out) if g.group == SHORE_BRKR_MINUS][0]
    assert g.selector == BREAKER_SIZE and g.disabling       # the gate stays; it now reads the size
    assert guidata.variable_table(out)[VAR].raw == guidata.variable_table(corpus["11.23"])[VAR].raw
    with pytest.raises(vg.VariantGateError):
        vg.restore_gate_selector(out, SHORE_BRKR_MINUS, BREAKER_SIZE)   # already there
    with pytest.raises(vg.VariantGateError):
        vg.restore_gate_selector(data, 200, BREAKER_SIZE)             # not gated


def test_scan_can_fail(corpus, monkeypatch) -> None:
    monkeypatch.setattr(vg.indirectwrites, "_disasm", lambda body, base: ())
    assert vg.gated_variants(corpus["13.6"]) == []


def test_the_recipe_op_restores_the_selector_and_can_ungate(corpus) -> None:
    import hashlib
    from fireflymcf import recipe
    data = corpus["13.6"]
    sha = hashlib.sha256(data).hexdigest()
    r = recipe.compile_project(recipe.Project(base_sha256=sha, build_name=None, ops=[
        recipe.OpSpec(id="fix", type="fix_variant_gate", params={})]), data)
    assert r.ok, r.errors
    # Mode "selector" is HARDWARE-VERIFIED (PC.62, the coach's `-` transmitting and its 0 A
    # floor). Mode "ungate" is not -- it rewrites GuiCode and has never been flashed -- and the
    # op reports per-instance, so the two differ here.
    assert r.unverified == []
    assert guidata.variable_table(r.data)[VAR].value == BREAKER_SIZE
    r2 = recipe.compile_project(recipe.Project(base_sha256=sha, build_name=None, ops=[
        recipe.OpSpec(id="fix", type="fix_variant_gate", params={"mode": "ungate"})]), data)
    assert r2.ok, r2.errors
    assert r2.unverified == ["fix"], "the ungate mode has never been flashed"
    assert [(g.variant, g.disabling) for g in vg.gated_variants(r2.data)] == [(0, False)]
    r3 = recipe.compile_project(recipe.Project(base_sha256=sha, build_name=None, ops=[
        recipe.OpSpec(id="fix", type="fix_variant_gate", params={"group": 200})]), data)
    assert not r3.ok and r3.errors[0]["code"] == "GATE_REFUSED"
