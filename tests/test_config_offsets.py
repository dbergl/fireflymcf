"""The option config-buffer schema (`fireflymcf.config`).

`docs/GUICODE.md` Finding 20: `GuiCode_entry1` calls the config-unpack (`FUN_00247bf8` in
13.6, `FUN_00247cac` in 14.7), which dereferences a config buffer pointer at
`GuiCodeState+0x214` and copies fixed byte offsets straight into SRAM option registers via
`ldrb rD,[rB,#imm] ; ldr rT,=<register> ; strb rD,[rT]`. The expected values here are Finding
20's table, cited from Ghidra, not fitted: this test is the oracle-reproduction anchor for the
whole module, not a search target (the join was found by locating the config-unpack function
structurally — `ldr rX,[rY,#0x214]` — and reading its instruction stream, not by grepping for
these bytes).
"""
from __future__ import annotations

from fireflymcf import config

# 13.6 pairs, cited from `docs/GUICODE.md` Finding 20's table.
SRAM_LOW = 0x20000000
SRAM_HIGH = 0x20080000


def test_config_offsets_reproduce_finding_20(mcf_136):
    got = config.config_offsets(mcf_136)
    assert got[0x00] == 0x2003A77A      # floorplan
    assert got[0x0A] == 0x2003A296      # Heat Pump
    assert got[0x0D] == 0x20039B49      # Bath Fan
    assert got[0x0E] == 0x2003A4B4      # Black Tank
    assert got[0x11] == 0x20039E70      # Bunk Accent
    assert got[0x19] == 0x20039EBD      # Bath Light
    assert got[0x1A] == 0x20039CF8      # Cargo Light


def test_config_offsets_find_starlink_in_147(mcf_147):
    assert config.config_offsets(mcf_147)[0x1B] == 0x200396FE


def test_config_offsets_1123_does_not_crash(mcf_1123):
    """11.23 may legitimately resolve a different set (build-generality is not free — see
    `theme.mode7_palette`'s 20/18/6 cell counts across the three builds), but whatever comes
    back must be non-empty and every register must land in the SRAM range."""
    got = config.config_offsets(mcf_1123)
    assert got
    for offset, register in got.items():
        assert SRAM_LOW <= register < SRAM_HIGH, f"offset {offset:#x} -> {register:#x}"


# ---------------------------------------------------------------------------
# The selector join: `cfg offset -> 0x15FCE selector`
# ---------------------------------------------------------------------------
#
# Oracle: Finding 20's seven `(cfg offset, selector)` pairs, PC.2/PC.3 on the subject coach.
# **Verification only** — the extractor was written from the mechanism (a `.rodata` frame
# template copied beside the config-set call / the option's gate register) and these bytes
# were never searched for. Six of the seven are 13.6 pairs; the seventh, the floorplan row, is
# a Rule A pair in 11.23 and is honestly absent from 13.6 (see below).

SELECTORS_136 = {0x0A: 0xEB, 0x0D: 0xE9, 0x0E: 0xEC, 0x11: 0xE5, 0x19: 0xE4, 0x1A: 0xE3}


def test_selector_join_matches_the_live_protocol(mcf_136):
    for offset, selector in SELECTORS_136.items():
        assert config.selector_for_offset(mcf_136, offset) == frozenset({selector}), (
            f"cfg {offset:#04x}")


def test_the_floorplan_pair_is_a_1123_adjacency(mcf_1123, mcf_136):
    """The seventh oracle pair, cfg `0x00` -> `0xF5`.

    11.23 writes it from a per-floorplan handler that calls the config-set helper directly
    (`bl` @`0x0024B000`, template `0x002571A4`), so Rule A reaches it there. 13.6 and 14.7
    route the floorplan through the bulk-save block, and their floorplan register is read only
    as a *guard* — 13.6's `0x2003A77A` is read two instructions into the Cargo Light handler
    (`0x0024CE00`), which a naive gate rule would report as `0xE3`. The guard rejects it, so
    13.6 returns nothing here: a defensible negative, not a fitted `0xF5`.
    """
    assert config.selector_for_offset(mcf_1123, 0x00) == frozenset({0xF5})
    assert config.selector_for_offset(mcf_136, 0x00) == frozenset()


def test_selector_sites_cite_their_instructions(mcf_136):
    """Every claim cites bytes: the Rule A row Task 2 transcribes in full, and the Rule B row
    whose handler entry it cites (`0x0024CF1C`, reached by `bhi.w` @`0x0024BB68`)."""
    sites = config.selector_sites(mcf_136)

    bath_fan = {site.join: site for site in sites[0x0D]}
    assert set(bath_fan) == {0x0024CA8C, 0x0024CABE}         # both arms of the toggle
    assert bath_fan[0x0024CA8C].rule == "A"
    assert bath_fan[0x0024CA8C].frames[0].template == 0x002596A0
    assert (bath_fan[0x0024CA8C].selector, bath_fan[0x0024CA8C].value) == (0xE9, 1)
    assert (bath_fan[0x0024CABE].selector, bath_fan[0x0024CABE].value) == (0xE9, 0)

    bath_light, = sites[0x19]
    assert bath_light.rule == "B"
    assert (bath_light.join, bath_light.handler) == (0x0024CF2A, 0x0024CF1C)
    assert bath_light.register == 0x20039EBD                  # config_offsets' cfg 0x19
    assert bath_light.predicate == ("eq", 0)                  # this arm: the option is off
    assert bath_light.frames[0].template == 0x0025985C
    # the run's second frame is the paired row being switched off -- E3:=0, PC.2 Result 2's
    # radio pair. It is NOT attributed to cfg 0x19.
    assert [(f.selector, f.value) for f in bath_light.frames] == [(0xE4, 1), (0xE3, 0)]


def test_a_dependency_frame_is_not_attributed(mcf_136):
    """cfg `0x17` (Progressive Inverter) emits its own `0xE6` and then forces `0xEF` (AGS) —
    `BUS-BASELINE.md` PC.3 Result 2, `E6` then `EF`, from one straight-line run. The
    dependency is visible in the frames and excluded from the attribution."""
    site, = config.selector_sites(mcf_136)[0x17]
    assert [(f.selector, f.value) for f in site.frames] == [(0xE6, 1), (0xEF, 1)]
    assert config.selector_for_offset(mcf_136, 0x17) == frozenset({0xE6})


def test_the_ags_enums_three_gates_all_emit_one_selector(mcf_147):
    """cfg `0x13`'s register is gated against 1, 2 and 3 at three sites in **14.7** (rvc2mqtt's
    `EF` enum: AGS / AES / AES Single Output, `BUS-BASELINE.md` PC.3 Result 3) and every one
    emits `0xEF`. Three gates, three constants, one selector.

    **This does not assert the value correspondence, and the old name
    (`..._writes_its_compared_constant`) wrongly said it did.** That correspondence -- emitted
    value == compared constant -- is a **13.6** property, and it is 13.6's three-gate argument
    that earns cfg `0x13` its B+ grade in `GUICODE.md` Finding 23. On 14.7 all three sites are
    the *turn-off* arms: each emits value **0**, from the one shared template `0x0025BFB8`. So
    what is checked here is the gate predicates and the single selector, which is what makes
    the recovery build-general -- not the 13.6 correspondence.
    """
    sites = config.selector_sites(mcf_147)[0x13]
    assert sorted(site.predicate for site in sites) == [("eq", 1), ("eq", 2), ("eq", 3)]
    assert {site.selector for site in sites} == {0xEF}


def test_the_held_out_147_rows(mcf_147):
    """14.7's two OPTIONS rows that 13.6 does not have. Derived, **not verified**: there is no
    oracle entry and no 14.7 capture for either.

    **Finding 20's cfg `0x1B` -> `0xE7` is retracted.** It was an elimination over the 14.7
    variable-table run `{E7,E4,E5,E3,EB,EC}` resting on an unstated premise -- that `0xE4`
    there was Bath Light's kept-but-hidden declaration. The bytes say otherwise: the `0x855`
    handler gates on `0x200396FE` (Starlink Power) and emits template `0x0025C0D8` =
    `FF 96 E4 0F 01 00 D1 EA`, and `0xE7` belongs to cfg `0x14` (Lithionics Batteries), the
    row Finding 20's reading had to leave with no selector at all.
    """
    assert config.selector_for_offset(mcf_147, 0x1B) == frozenset({0xE4})
    assert config.selector_for_offset(mcf_147, 0x14) == frozenset({0xE7})

    starlink, = config.selector_sites(mcf_147)[0x1B]
    assert (starlink.join, starlink.register) == (0x0024C380, 0x200396FE)
    assert starlink.frames[0].template == 0x0025C0D8


def test_the_147_false_positive_is_rejected(mcf_147):
    """cfg `0x14`'s register is read again at `0x0024E6D4`, outside its handler, in a run that
    emits `sel 0x0C value 0x3C`. That arm implies the register is non-zero, so `0x3C` is not a
    provable change and the site is dropped -- the trace's false-positive class (iii)."""
    assert [site.join for site in config.selector_sites(mcf_147)[0x14]] == [0x0024D68E]


def test_disagreeing_sites_are_reported_not_resolved(mcf_1123):
    """11.23's cfg `0x0F` is written from three sites whose nearest frames disagree
    (`0xF0` twice, `0xF5` once). The relation keeps both; `option_selector` refuses."""
    assert config.selector_for_offset(mcf_1123, 0x0F) == frozenset({0xF0, 0xF5})
    assert config.option_selector(mcf_1123, 0x0F) is None
    assert config.option_selector(mcf_1123, 0x0A) == 0xEB


def test_an_unjoined_offset_is_empty(mcf_136):
    assert config.selector_for_offset(mcf_136, 0xFE) == frozenset()
    assert config.option_selector(mcf_136, 0xFE) is None


def test_floorplan_slot_is_derived_from_the_unpack_on_every_build(mcf_136, mcf_147, mcf_1123):
    """`config.floorplan_slot`: the floorplan variable slot, derived from the receive
    mechanism -- the config-unpack's cfg-`0x00` store names the floorplan register
    (`config_offsets`, Finding 20/45/59's path), and that register's unique index in
    Finding 17's GuiData variable pointer table is the slot.

    13.6 (slot 5) and 14.7 (slot 10) are the validation: both are known independently
    from the floorplan-name switch (`options._find_floorplan_switch`), and 14.7 is the
    build where the rejected read-histogram heuristic picked 114 -- so agreement here is
    exactly the cross-build check that heuristic failed. 11.23's 7 (register
    `0x20039A8E`, pointer-table hit unique) was derived here first, then confirmed on
    the coach (BUS-BASELINE PC.40) AND by the name switch the corrected value map
    unmasked (objects 852/853, also slot 7) -- three mechanisms, one answer. This
    derivation remains the fallback for a build with no matchable switch."""
    assert config.floorplan_slot(mcf_136) == 5
    assert config.floorplan_slot(mcf_147) == 10
    assert config.floorplan_slot(mcf_1123) == 7


def test_floorplan_slot_refuses_when_the_register_is_not_in_the_table(mcf_136):
    """An ambiguous or absent pointer-table hit must yield None, never a guess -- the
    module's standing omit-don't-guess rule. Exercised by handing the resolver a
    register that is provably not the floorplan's: patching the unpack result is not
    possible from here, so instead check the underlying lookup contract directly."""
    assert config._slot_of_register(mcf_136, 0xDEADBEEF) is None


def test_rule_a_over_match_is_cut_by_the_value(mcf_1123):
    """The trace's Rule A over-match: 11.23's cfg `0x0B` handler emits a burst before the next
    `bl`, and the spike reported `{A0, ED, F5}` for it. Requiring the nearest write frame to
    carry the value the config-set call is writing leaves `0xED` -- twice, from two sites whose
    values (0 and 5) differ and each match. cfg `0x16`'s only sites emit a frame whose value
    does not match at all, so nothing is attributed rather than the nearest byte guessed."""
    sites = config.selector_sites(mcf_1123)
    assert config.selector_for_offset(mcf_1123, 0x0B) == frozenset({0xED})
    assert sorted(site.value for site in sites[0x0B]) == [0, 5]
    assert config.selector_for_offset(mcf_1123, 0x16) == frozenset()
