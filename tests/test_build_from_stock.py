"""The customised build must be reproducible from the corpus alone.

Builds up to v18 were chained off the previous *binary*, and one link in that chain
(`mcf-pin15-v9.bin`) had no build script at all. Since `build/` is gitignored, the whole thing
was unreproducible from a clean checkout -- the failure this test exists to prevent.

It does NOT re-run `build/build-from-stock.py` (that needs Inkscape and takes minutes). It pins
the two properties that made the chain fragile:

  1. every build script starts from a file that a clean checkout actually has -- the corpus, or
     an output some *other* script in the tree produces;
  2. the composed pipeline the from-stock build relies on still runs end to end from stock.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
RECIPE_DIR = REPO / "builds"          # tracked recipes
SCRATCH_DIR = REPO / "build"          # gitignored outputs (and superseded ad-hoc scripts)
CORPUS = {"firefly-13.6/mcf.bin", "firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"}

_ASSIGN = re.compile(r'^\s*(BASE|STOCK|SRC|DST)\s*=\s*["\'](.+?)["\']', re.M)
_OUT = re.compile(r'^\s*OUT\s*=\s*["\'](.+?)["\']', re.M)


def _scripts():
    """Only the TRACKED recipes are governed. Anything left in the gitignored `build/` is
    scratch by definition and cannot be a dependency of a clean checkout."""
    return sorted(RECIPE_DIR.glob("*.py")) if RECIPE_DIR.is_dir() else []


def test_every_build_script_starts_from_something_a_checkout_has():
    """No script may depend on a binary that nothing in the tree can regenerate.

    `build/` is gitignored, so a base of `build/foo.bin` is only legitimate when some other
    script declares `OUT = "build/foo.bin"`. A base in the corpus is always fine.
    """
    scripts = _scripts()
    assert scripts, "no tracked build recipe in builds/ -- the build is unreproducible"

    produced = set()
    for path in scripts:
        produced.update(_OUT.findall(path.read_text()))

    orphans = []
    for path in scripts:
        for _name, base in _ASSIGN.findall(path.read_text()):
            if base in CORPUS or base in produced or not base.endswith(".bin"):
                continue
            orphans.append(f"{path.name}: {base}")
    assert not orphans, (
        "build input(s) that a clean checkout cannot produce -- the chain is unreproducible:\n  "
        + "\n  ".join(orphans))


def test_a_from_stock_build_script_exists_and_starts_at_stock():
    path = RECIPE_DIR / "from_stock.py"
    assert path.is_file(), "builds/from_stock.py is missing"
    text = path.read_text()
    assert "/tmp/" not in text, "recipe reads an input from outside the repo"
    for asset in re.findall(r'^\s*SVG\s*=\s*["\'](.+?)["\']', text, re.M):
        assert (REPO / asset).is_file(), f"vendored asset {asset} is missing"
    assert 'STOCK = "firefly-13.6/mcf.bin"' in text, "the from-stock build does not start at stock"
    assert "BASE =" not in text, "the from-stock build must not chain off another build"


def test_the_composed_pipeline_runs_from_stock(mcf_136):
    """Stock -> tank-heater widen -> Pin-15 button -> a second attached button, gates green.

    This is the spine of `build-from-stock.py` up to the point where it needs a rasteriser. If
    any of these compose only against an already-customised image, the from-stock build is a
    fiction and this fails.
    """
    from fireflymcf import (cli, coverage, guidata, insert, newbutton, patch, relocate,
                                  statusbind, tankgates, theme)
    import struct

    data, applied = tankgates.widen(mcf_136)
    assert len(applied) == len(tankgates.WIDEN_EDITS)

    data, _ = newbutton.build_home_button(data)
    pin15 = newbutton.last_object_index(data)

    # pin 15 is commanded now, so bind_pin must refuse and the narrower colour edit is the route
    pb = statusbind.pin_bindings(data)[15]
    assert pb.commanded, "the Pin-15 button did not register as commanding pin 15"
    with pytest.raises(statusbind.StatusBindError):
        statusbind.bind_pin(data, 15)
    data, _ = statusbind.set_state_colour(data, pb.var, "off", (65, 81, 163))

    clone = insert.clone_object(data, 247)
    data, _ = insert.insert_object(data, clone, cli._resolve_page_root(data, "home"),
                                   position=(630, 388))
    btn = guidata.object_tables(data)[1]
    assert len(btn) - 1 != pin15, "the second button did not get its own object index"

    data, _cell, _ = statusbind.bind_pin(data, 29, off_colour=(65, 81, 163))
    data, _ = newbutton.retarget_command(data, len(btn) - 1, channel=29,
                                         allow_commanded_channel=True)
    data, _ = patch.reseal(data)

    ok, reason = relocate._boundary_oracle(data)
    assert ok, reason
    assert coverage.analyse(data, deep=True).unclaimed == []
    for lit_off, tgt in theme.guicode_guidata_pointers(data):
        assert struct.unpack_from("<I", data, lit_off)[0] == 0x90000000 + tgt
