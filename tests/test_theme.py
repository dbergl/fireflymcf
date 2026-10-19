"""The mode-7 chrome palette extractor (`fireflymcf.theme`).

Every colour is byte-derived from the GuiCode theme-init (`FUN_00247bf8`), per
`docs/GUICODE.md` Finding 18 and its 2026-08-15 colour-dig correction. Nothing here is fitted
to a photo: the expected values are the constants Ghidra traced out of the store block
(`extractions/color_*.txt`), written as hex in the test and decoded to RGB the same way the
firmware would. The three anchors (idx 3/4/8) and the recognizable format constants
(0x00FFFFFF/0x00141414/0x00AA00AA) are the oracle.
"""
from __future__ import annotations

import pytest

from fireflymcf import theme


def _rgb(value: int) -> tuple[int, int, int]:
    """Decode a stored u32 as RGB888 (0x00RRGGBB), the way `mode7_palette` does."""
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


# The constants Ghidra traced out of the 13.6 theme-init, by mode-7 index. Hex here, so the
# test states the byte value and derives the RGB rather than hard-coding a colour tuple.
TRACED_136 = {
    3: 0x0015183A,   # dark blue,  direct  ldr r2,[=0x0015183A]
    4: 0x00BE1E2D,   # chrome red, add-chain: 0x0015183A + 0xA90000 + 0x5F3
    8: 0x00272862,   # direct  ldr r2,[=0x00272862]
}


def test_136_reproduces_traced_constants_exactly(mcf_136):
    """The dig's three anchors come back byte-for-byte. If the extractor returned nothing (a
    stub), or desynced, these fail — this is the non-vacuity guard for the whole module."""
    palette = theme.mode7_palette(mcf_136)
    for index, constant in TRACED_136.items():
        assert palette[index] == _rgb(constant), f"idx {index}"
    # And the concrete decoded values, spelled out, as a second reading of the same fact.
    assert palette[3] == (21, 24, 58)
    assert palette[4] == (190, 30, 45)
    assert palette[8] == (39, 40, 98)


def test_add_chain_is_actually_followed(mcf_136):
    """idx 4 (the chrome red) is only reachable through the two-step add-chain
    `add.w r2,r2,#0xA90000 ; addw r2,r2,#0x5F3` applied to idx 3's constant left in r2.

    This is the non-vacuous check for add-chain handling: a decoder that ignored the add
    instructions would store r2 unchanged and report idx 4 as idx 3's colour (21,24,58). So
    asserting idx 4 == the arithmetic result — and, explicitly, != idx 3 — fails the moment
    add-chain handling is disabled (verified by mutation during development)."""
    base = TRACED_136[3]                       # 0x0015183A, left in r2 after the idx-3 store
    red = (base + 0xA90000 + 0x5F3) & 0xFFFFFFFF
    assert red == 0x00BE1E2D                   # the arithmetic, independent of the extractor

    palette = theme.mode7_palette(mcf_136)
    assert palette[4] == _rgb(red)
    assert palette[4] != palette[3]            # would be equal if the add-chain were skipped


def test_rgb888_format_constants_decode(mcf_136):
    """The block's recognizable constants pin the format as RGB888 and must decode as such:
    0x00FFFFFF -> white (mvn r1,#0xff000000), 0x00141414 -> grey, 0x00AA00AA -> magenta
    (mov.w r1,#0xaa00aa)."""
    values = set(theme.mode7_palette(mcf_136).values())
    assert _rgb(0x00FFFFFF) == (255, 255, 255) and (255, 255, 255) in values
    assert _rgb(0x00141414) == (0x14, 0x14, 0x14) and (0x14, 0x14, 0x14) in values
    assert _rgb(0x00AA00AA) == (0xAA, 0x00, 0xAA) and (0xAA, 0x00, 0xAA) in values


@pytest.mark.parametrize("build", ["136", "1123", "147"])
def test_runs_on_every_build_and_is_well_formed(build, request):
    """The same cell-pool method locates the theme-init on every build (no 13.6 hard-coding):
    each returns a non-empty palette of valid RGB triples, and the chrome red (190,30,45) —
    the one mode-7 colour shared across all three builds — is present. 14.7 resolves fewer
    cells than 13.6/11.23 (more of its chrome is written as runtime copies rather than in-line
    constants); that is an honest per-build result, not a failure — every colour returned is
    still byte-derived."""
    data = request.getfixturevalue(f"mcf_{build}")
    palette = theme.mode7_palette(data)
    assert palette, "no chrome colours resolved"
    for rgb in palette.values():
        assert len(rgb) == 3 and all(0 <= c <= 255 for c in rgb)
    assert (190, 30, 45) in palette.values()


def test_unresolvable_cells_are_omitted_not_guessed(mcf_136):
    """A colour is only reported when a single constant store is recovered. The palette is a
    strict subset of the type-10 (colour) cells — most cells are runtime-written and therefore
    named residuals the caller keeps heuristic — and every key is a real colour-cell index."""
    palette = theme.mode7_palette(mcf_136)
    colour_indices = set(theme._colour_cells(mcf_136).values())
    assert set(palette) <= colour_indices
    assert len(palette) < len(colour_indices)   # residuals exist; nothing is invented to fill


def test_ambiguous_runtime_selected_cell_is_dropped(mcf_1123):
    """11.23 writes cell 0x200395c8 (mode-7 index 544) from a `cmp`/`bhi` chain of three
    different candidate colours — a runtime value-match, no single constant. It must be
    omitted rather than have one candidate guessed (the cardinal rule). This guards the
    multi-distinct-value filter; if that filter regressed to first-wins, 544 would reappear."""
    palette = theme.mode7_palette(mcf_1123)
    assert 544 not in palette


# --- resting_defaults: copy-fed runtime-state cells' resting/default colour (Finding 34) ---

def test_resting_idx145_is_derived_navy(mcf_136):
    """idx145 (instance-15's indicator cell 0x2003a2fc) has its resting/default BYTE-DERIVED:
    the theme-init copies master cell 0x20039974 into it (fall-through at 0x2488ea-f0), and that
    master holds the single constant 0x00272862 = (39,40,98). This is the two-level chain
    `mode7_palette` cannot follow (it clears the register on the copy's memory read), which is
    why idx145 was previously an inference. The derived value is the NAVY, not the button blue
    (65,81,163) it used to be guessed as — assert both, so a regression to the old guess fails."""
    section = theme.guicode_section(mcf_136)
    base = theme.guicode_base(section)
    code = mcf_136[section.offset:section.offset + section.length]
    cells = theme._colour_cells(mcf_136)
    cell = {v: k for k, v in cells.items()}[145]
    assert cell == 0x2003A2FC
    # The copy's default master is 0x20039974, and that master's single constant is 0x00272862.
    masters = theme._copy_store_masters(code, base, set(cells))[cell]
    assert masters == {0x20039974}
    consts = theme._trace_constant_stores(
        code, base, {a: a for a in theme._pcrel_sram_addrs(code, base)})
    assert consts[0x20039974] == 0x00272862

    rd = theme.resting_defaults(mcf_136)
    assert rd[145] == _rgb(0x00272862) == (39, 40, 98)
    assert rd[145] != (65, 81, 163)             # not the old inferred blue


def test_resting_defaults_reproduce_curated_render_values(mcf_136):
    """Independent cross-check: for every index the render's hand-curated `_RESTING_FILLS` and
    the byte-derived resolver BOTH cover, they agree exactly. This is what makes idx145's
    correction trustworthy — the same resolver reproduces the oracle-confirmed blues (Water Pump
    idx160, the CLIMATE option box idx144, Start/Stop idx167/168, the mode-grid idx376/377/378)
    with zero disagreement. If the resolver were fitting, it would not land on the curated set."""
    from fireflymcf import render
    rd = theme.resting_defaults(mcf_136)
    overlap = {i for i in render._RESTING_FILLS if i in rd}
    assert len(overlap) >= 10                   # a real overlap, not one lucky index
    disagreements = {i: (render._RESTING_FILLS[i], rd[i])
                     for i in overlap if render._RESTING_FILLS[i] != rd[i]}
    assert disagreements == {}, disagreements


def test_resting_defaults_omits_ambiguous_and_non_copy(mcf_136):
    """A defensible negative: cells whose default master is not a single constant (idx455 Tank
    Heater — a deeper copy chain) or whose init is a value-match (idx375 CLIMATE header, an
    active-tab branch) are OMITTED, never guessed. They stay curated in the render. The result is
    a strict subset of the colour cells and disjoint from `mode7_palette` (which owns the direct
    single-constant cells)."""
    rd = theme.resting_defaults(mcf_136)
    assert 455 not in rd and 375 not in rd
    colour_indices = set(theme._colour_cells(mcf_136).values())
    assert set(rd) <= colour_indices
    assert set(rd).isdisjoint(theme.mode7_palette(mcf_136))


@pytest.mark.parametrize("build", ["136", "1123", "147"])
def test_resting_defaults_well_formed_every_build(build, request):
    """Runs build-generally (no 13.6 addresses) and returns only valid RGB triples on all three
    builds; every reported index is a real colour-cell index and none collides with the
    direct-constant palette."""
    data = request.getfixturevalue(f"mcf_{build}")
    rd = theme.resting_defaults(data)
    colour_indices = set(theme._colour_cells(data).values())
    for index, rgb in rd.items():
        assert index in colour_indices
        assert len(rgb) == 3 and all(0 <= c <= 255 for c in rgb)
    assert set(rd).isdisjoint(theme.mode7_palette(data))


# --- fill_palette / resolve_fill_index: the firmware-legal recolour choices ---

def test_fill_palette_groups_indices_by_colour(mcf_136):
    """Every colour maps to the indices that carry it, split static (invariant chrome) vs resting
    (state-cell default). The button blue (65,81,163) is present with at least one STATIC index --
    the property that lets a recolour avoid coupling to a state cell."""
    pal = theme.fill_palette(mcf_136)
    assert (65, 81, 163) in pal, "button blue not in palette"
    assert pal[(65, 81, 163)]["static"], "no static index for button blue"
    # a static index really is in mode7_palette; a resting one really is in resting_defaults
    m7 = theme.mode7_palette(mcf_136)
    rd = theme.resting_defaults(mcf_136)
    for rgb, kinds in pal.items():
        for i in kinds["static"]:
            assert m7[i] == rgb
        for i in kinds["resting"]:
            assert rd[i] == rgb


def test_resolve_fill_index_prefers_static_and_rejects_unknown(mcf_136):
    """A colour resolves to a static index when one exists (no state coupling); an index is
    accepted only if it resolves to a real colour; an off-palette colour or a dead index raise
    PaletteError -- the writer never points a fill at an undefined colour (the firmware rule)."""
    # (65,81,163) has static indices; resolve must return one of them
    idx = theme.resolve_fill_index(mcf_136, color=(65, 81, 163))
    assert idx in theme.fill_palette(mcf_136)[(65, 81, 163)]["static"]
    assert theme.mode7_palette(mcf_136)[idx] == (65, 81, 163)

    # a named index that resolves is returned unchanged
    assert theme.resolve_fill_index(mcf_136, index=idx) == idx

    # off-palette colour -> refused
    with pytest.raises(theme.PaletteError):
        theme.resolve_fill_index(mcf_136, color=(1, 2, 3))
    # an index that resolves to nothing -> refused
    dead = max(theme.fill_palette(mcf_136)[(65, 81, 163)]["static"]) + 100000
    with pytest.raises(theme.PaletteError):
        theme.resolve_fill_index(mcf_136, index=dead)
    # exactly one of color/index required
    with pytest.raises(theme.PaletteError):
        theme.resolve_fill_index(mcf_136, color=(65, 81, 163), index=idx)
    with pytest.raises(theme.PaletteError):
        theme.resolve_fill_index(mcf_136)
