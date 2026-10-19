"""`cli render` -- Task 5. Render any page of any build at any coach config to PNG.

Known-truth values reused from the codebase this leans on: `layout.climate_root`'s
docstring gives 13.6's Climate root as object 7, and `_resolve_page_root` (this task)
independently reproduces that same index by resolving "climate" -> the object whose
header record reads "CLIMATE CONTROL" -- confirmed directly against the corpus while
building this (see cli.py's `_resolve_page_root` docstring). 13.6's HOME root is object
4, resolved the same way -- verified directly (`options._first_label` over each
object's own record list) before writing the `--root 4` == `--page home` test below.

`Placement`/`Prim` come from `src/fireflymcf/render.py`; `climate_placements_decoded`
is the byte-exact compositor `_cmd_render` calls into via `render_climate_decoded`.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from fireflymcf import cli, options, render
from fireflymcf.cli import _resolve_page_root, main

MCF_136 = "firefly-13.6/mcf.bin"


def _text_prims(placements) -> set[str]:
    return {p.text for pl in placements for p in pl.prims if p.kind == "text"}


def test_render_home_with_slot_overrides_writes_an_800x480_png(tmp_path):
    out = tmp_path / "home.png"
    rc = main(["render", MCF_136, str(out), "--page", "home",
               "--slot", "5=8", "--slot", "112=1"])
    assert rc == 0
    assert out.exists()
    with Image.open(out) as img:
        assert img.size == (800, 480)


def test_root_overrides_page_when_both_are_given(tmp_path):
    """13.6's HOME object is index 4 (verified directly with
    `options._first_label`/`options._own_records`, the same convention
    `_resolve_page_root` uses). `--page settings` resolves too, to a DIFFERENT object
    (index 8) -- both flags name real, renderable pages, so a passing render alone
    proves nothing about precedence. The only real check is which page the combined
    call actually rendered: `by_both` must be byte-IDENTICAL to `by_root` (--root 4
    alone) and byte-DIFFERENT from `by_page` (--page settings alone). A prior version
    of this test asserted only `rc == 0` and PNG size for all three renders, which
    stayed green even with --root/--page precedence swapped in `_cmd_render` (a
    reviewer mutation test caught this); the byte comparisons below are what actually
    exercises the precedence rule.
    """
    out_root = tmp_path / "by_root.png"
    out_page = tmp_path / "by_page.png"
    out_both = tmp_path / "by_both.png"

    assert main(["render", MCF_136, str(out_root), "--root", "4"]) == 0
    assert main(["render", MCF_136, str(out_page), "--page", "settings"]) == 0
    assert main(["render", MCF_136, str(out_both), "--root", "4",
                 "--page", "settings"]) == 0

    for out in (out_root, out_page, out_both):
        with Image.open(out) as img:
            assert img.size == (800, 480)

    assert out_both.read_bytes() == out_root.read_bytes()
    assert out_both.read_bytes() != out_page.read_bytes()


def test_no_flags_defaults_via_resolve_page_root_not_the_hardcoded_layout_root(
        tmp_path, monkeypatch):
    """With neither --page nor --root, `_cmd_render` must default through
    `_resolve_page_root(data, "climate")` -- NOT `layout.climate_root(data)`, which
    hardcodes `return 7` for 13.6 only and would silently render the wrong object on
    11.23 (root 9) or 14.7 (root 12). Spies on `cli._resolve_page_root` to confirm it
    is actually called with "climate" for a no-flag invocation (the thing that
    distinguishes "goes through the general resolver" from "coincidentally lands on
    the same object"), then confirms the no-flag render is byte-identical to an
    explicit `--page climate` render -- both must be the exact same call.
    """
    calls: list[str] = []
    real_resolve = cli._resolve_page_root

    def spy(data, page):
        calls.append(page)
        return real_resolve(data, page)

    monkeypatch.setattr(cli, "_resolve_page_root", spy)

    out_default = tmp_path / "default.png"
    assert main(["render", MCF_136, str(out_default)]) == 0
    assert calls == ["climate"]

    monkeypatch.undo()  # restore the real resolver for the explicit-page render below
    out_explicit = tmp_path / "explicit_climate.png"
    assert main(["render", MCF_136, str(out_explicit), "--page", "climate"]) == 0

    assert out_default.read_bytes() == out_explicit.read_bytes()


def test_unresolvable_page_name_errors_instead_of_guessing(tmp_path, capsys):
    """14.7 has no "ELECTRICAL"-headed object (checked directly while building this:
    `_resolve_page_root` finds HOME/LIGHTS/CLIMATE CONTROL/SETTINGS there but not
    ELECTRICAL). This must fail clearly, not render some unrelated page."""
    out = tmp_path / "should-not-exist.png"
    rc = main(["render", "firefly-14.7 (Terrain)/mcf.bin", str(out),
               "--page", "electrical"])
    assert rc != 0
    assert not out.exists()
    err = capsys.readouterr().err
    assert "electrical" in err.lower()
    assert "--root" in err


def test_cli_slot_override_changes_the_rendered_image(tmp_path):
    """Falsifiability at the CLI level: the Lights page (13.6 root 6) only draws its
    light grid once a floorplan is selected (slot 5). If `_cmd_render` silently
    dropped `--slot`, both renders below would be byte-identical."""
    with_slot = tmp_path / "with_slot.png"
    without_slot = tmp_path / "without_slot.png"
    assert main(["render", MCF_136, str(with_slot), "--page", "lights",
                 "--slot", "5=8"]) == 0
    assert main(["render", MCF_136, str(without_slot), "--page", "lights"]) == 0
    assert with_slot.read_bytes() != without_slot.read_bytes()


def test_config_drives_content_lights_page_shows_main_with_floorplan_slot():
    """Config actually reaches the compositor, checked at the `climate_placements_decoded`
    level (per the task brief) with the exact root + slot_values `_cmd_render` builds:
    floorplan slot 5 = 8 ("WD", `tests/test_options.py`'s known value) makes the Lights
    page (root resolved via `_resolve_page_root`, matching `--page lights`) render a
    "Main" light-group label; the plain grid without a floorplan selected does not."""
    data = Path(MCF_136).read_bytes()
    root = _resolve_page_root(data, "lights")

    with_config = render.climate_placements_decoded(data, root=root, slot_values={5: 8})
    without_config = render.climate_placements_decoded(data, root=root, slot_values={})

    assert "Main" in _text_prims(with_config)
    # Falsifiable: confirms this isn't just always-present text -- a stubbed
    # `_cmd_render` that ignored slot_values entirely would still pass the first
    # assertion here (nothing about it depends on the CLI), which is exactly why the
    # CLI-level byte-diff test above also exists.
    assert "Main" not in _text_prims(without_config)


def test_cli_derives_the_nav_rail_from_the_config(tmp_path):
    """`cli render` must compute the slots the panel computes, not just the ones it is
    handed -- otherwise it and Studio draw different screens from the same config.

    Slot 453 selects the left icon column and GuiCode derives it from three registers
    (`options.derived_slots`): with no generator mode, no Go Power! controller and no
    Progressive Inverter the panel drops the Electrical bolt and respaces the rail
    (object 386 instead of 385). Owner-reported from the coach, 2026-08-21.
    """
    data = Path(MCF_136).read_bytes()
    root = _resolve_page_root(data, "home")

    def rail(slots):
        merged = dict(slots)
        merged.update(options.derived_slots(data, merged))
        placed = render.screen_placements(data, root, slot_values=merged)
        return sorted(pr.text for pl in placed for pr in pl.prims
                      if pr.kind == "glyph" and pr.x < 60)

    # The bolt is 'D' in font slot 24; the other four icons are the pages that are
    # always there. AES fitted -> five icons; nothing electrical -> four.
    assert rail({5: 8, 37: 3}) == ["D", "L", "P", "Q", "R"]
    assert rail({5: 8}) == ["L", "P", "Q", "R"]
    assert rail({5: 8, 113: 1}) == ["D", "L", "P", "Q", "R"]

    # And end to end: the derivation must reach the PNG, not just the placement list.
    with_aes = tmp_path / "aes.png"
    without = tmp_path / "none.png"
    assert main(["render", MCF_136, str(with_aes), "--page", "home",
                 "--slot", "5=8", "--slot", "37=3"]) == 0
    assert main(["render", MCF_136, str(without), "--page", "home",
                 "--slot", "5=8"]) == 0
    assert with_aes.read_bytes() != without.read_bytes()

    # An explicit override still wins over the derivation, as it does everywhere else.
    forced = tmp_path / "forced.png"
    assert main(["render", MCF_136, str(forced), "--page", "home",
                 "--slot", "5=8", "--slot", "453=1"]) == 0
    assert forced.read_bytes() != without.read_bytes()
