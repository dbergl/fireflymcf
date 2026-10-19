import pytest
import subprocess
import sys

from fireflymcf import armimg


def _run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "fireflymcf.cli", *args],
        cwd=cwd, capture_output=True, text=True)


def test_regions_command_strips_the_header(tmp_path, corpus):
    """The emitted region file must begin at the vector table, not the image header,
    so that loading it at 0x20000000 puts the reset vector at 0x200002C0."""
    from tests.conftest import REPO_ROOT
    r = _run("regions", "firefly-13.6/cli.bin", str(tmp_path), cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    region1 = tmp_path / "cli-region1.bin"
    assert region1.exists()

    hdr = armimg.parse_header(corpus["13.6"]["cli"])
    assert region1.stat().st_size == hdr.regions[0].length
    assert region1.read_bytes()[:4] != bytes.fromhex("2e101bc1")
    vt = armimg.read_vector_table(region1.read_bytes(), 0x20000000)
    assert vt.reset == 0x200002C0


def test_extract_command_writes_sections(tmp_path):
    """13.6's mcf.bin carries 4 named sections (LCD Firmware, GuiCode, GuiData, and the
    unversioned "G12" -- see test_container.py::test_extract_writes_one_file_per_named_section
    and answer_key.SECTION_NAMES["13.6"], which is also 4 long)."""
    from tests.conftest import REPO_ROOT
    r = _run("extract", "firefly-13.6/mcf.bin", str(tmp_path), cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert len(list(tmp_path.glob("*.bin"))) == 4


def test_regions_rejects_a_non_firefly_file(tmp_path):
    from tests.conftest import REPO_ROOT
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00" * 4096)
    r = _run("regions", str(junk), str(tmp_path), cwd=REPO_ROOT)
    assert r.returncode != 0
    assert "magic" in (r.stderr + r.stdout).lower()


def test_identify_rejects_a_non_firefly_file(tmp_path):
    """identify() was repointed at characterise() (see module docstring in cli.py),
    but a junk file still isn't a parseable MCF container, so it must still fail
    loudly rather than silently reporting nothing."""
    from tests.conftest import REPO_ROOT
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00" * 4096)
    r = _run("identify", str(junk), cwd=REPO_ROOT)
    assert r.returncode != 0
    assert "magic" in (r.stderr + r.stdout).lower()


def test_coverage_command_reports_full_coverage():
    from tests.conftest import REPO_ROOT
    r = _run("coverage", "firefly-13.6/mcf.bin", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "unclaimed spans: none" in r.stdout
    assert "1067000 bytes (100.0000%)" in r.stdout


def test_coverage_command_deep_opens_guidata():
    from tests.conftest import REPO_ROOT
    r = _run("coverage", "firefly-13.6/mcf.bin", "--deep", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "unclaimed spans: none" in r.stdout
    assert "object-table" in r.stdout
    assert "image-container-header" in r.stdout


def test_coverage_command_rejects_a_non_firefly_file(tmp_path):
    from tests.conftest import REPO_ROOT
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00" * 4096)
    r = _run("coverage", str(junk), cwd=REPO_ROOT)
    assert r.returncode != 0
    assert "magic" in (r.stderr + r.stdout).lower()


def test_identify_command_reports_characterisation():
    """`identify` prints two things: embedded.characterise() for every section --
    name, length, kind, first four words, build tag -- and then the
    embedded.identify() result, clearly labelled.

    Both halves are needed. identify() resolves only LCD Firmware (base 0x00210000,
    proven in Task 6); GuiCode/GuiData/G12 are not Cortex-M images so it will never
    resolve them, and characterise() is what says anything useful about those three.
    This test previously asserted identify() printed "no embedded ARM images
    identified" -- that was true only while Task 4's since-disproved reading of LCD
    Firmware as a compressed image stood."""
    from tests.conftest import REPO_ROOT
    r = _run("identify", "firefly-13.6/mcf.bin", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    out = r.stdout

    # Section characterisation is present for every named section.
    assert "LCD Firmware" in out
    assert "Lyra 13v6 GuiCode" in out
    assert "Lyra 13v6 GuiData" in out
    assert "G12" in out
    assert "kind=vector-table-like" in out
    assert "kind=gui-code" in out
    assert "kind=gui-container" in out

    # The identify() result is reported, clearly labelled, with the proven base.
    assert "identify()" in out
    assert "load=0x00210000" in out
    assert "entry=0x002104d0" in out
    # ...and only for LCD Firmware: the three container-format sections must not
    # appear in the identify() half, only in the characterisation half above it.
    identify_half = out.split("identify()")[-1]
    assert "LCD Firmware" in identify_half
    for name in ("GuiCode", "GuiData", "G12"):
        assert name not in identify_half, (
            f"{name} must not be reported as an identified ARM image")


def test_patch_list_colors_lists_the_button_blue():
    from tests.conftest import REPO_ROOT
    r = _run("patch", "firefly-13.6/mcf.bin", "/dev/null", "--list-colors", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "(65, 81, 163)" in r.stdout            # the Water Pump / button blue
    assert "161" in r.stdout                       # a static index carrying it


def test_patch_recolor_object_box_passes_the_ladder(tmp_path):
    from tests.conftest import REPO_ROOT
    out = tmp_path / "recolored.bin"
    # object 250 is the stock 'Start' button box (wt-33, fillA mode-7 145)
    r = _run("patch", "firefly-13.6/mcf.bin", str(out), "--recolor", "250=idx161",
             cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert "L5" in r.stdout                         # full verification ladder ran
    assert "section-checksum:Lyra 13v6 GuiData" in r.stdout
    # the fillA index really changed to 161 in the output
    from fireflymcf import patch, records
    data = out.read_bytes()
    box = patch.find_button_box(data, 250, "fillA")
    off = records.color_offset(data, box, "fillA")
    assert data[off] | (data[off + 1] << 8) == 161


def test_patch_recolor_rejects_off_palette_colour(tmp_path):
    from tests.conftest import REPO_ROOT
    out = tmp_path / "nope.bin"
    r = _run("patch", "firefly-13.6/mcf.bin", str(out), "--recolor", "250=1,2,3",
             cwd=REPO_ROOT)
    assert r.returncode != 0                        # off-palette colour refused
    assert not out.exists()


def test_patch_auto_marks_custom_build(tmp_path):
    from tests.conftest import REPO_ROOT
    from fireflymcf import patch
    out = tmp_path / "marked.bin"
    r = _run("patch", "firefly-13.6/mcf.bin", str(out), "--recolor", "250=idx161",
             cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert patch.build_name(out.read_bytes()) == "Jayco B-Van V13 (Custom)"


def test_patch_name_overrides_and_no_mark_suppresses(tmp_path):
    from tests.conftest import REPO_ROOT
    from fireflymcf import patch
    named = tmp_path / "named.bin"
    r = _run("patch", "firefly-13.6/mcf.bin", str(named), "--recolor", "250=idx161",
             "--name", "My Rig", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert patch.build_name(named.read_bytes()) == "My Rig"
    plain = tmp_path / "plain.bin"
    r = _run("patch", "firefly-13.6/mcf.bin", str(plain), "--recolor", "250=idx161",
             "--no-mark", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert patch.build_name(plain.read_bytes()) == "Jayco B-Van V13"     # unmarked


def test_glyph_export_emits_reinstallable_parameters(tmp_path):
    """`glyph-export` must emit exactly what `install_glyph` consumes, JSON and all.

    The pixels are not the whole glyph: the 10 kerning bytes and the +0x0e flag bit cannot be
    derived from them (`GUICODE.md` Finding 46), so the command carries them, and re-encoding
    what it prints must reproduce the source record byte for byte.
    """
    import json

    from tests.conftest import REPO_ROOT
    from fireflymcf import glyphenc, glyphs

    r = _run("glyph-export", "firefly-13.6/mcf.bin", "--slot", "27", "--char", "3",
             cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    spec = json.loads(r.stdout)
    assert set(spec) == {"slot", "char", "x", "y", "rows", "profiles", "flag"}
    assert len(spec["profiles"]) == 10

    data = (REPO_ROOT / "firefly-13.6" / "mcf.bin").read_bytes()
    off, _ = glyphs.glyph_extent(data, glyphs.font_for_slot(data, 27).glyph_index(ord("3")))
    rec = glyphenc.encode_glyph([tuple(int(c, 16) for c in row) for row in spec["rows"]], 4,
                                x=spec["x"], y=spec["y"], profiles=spec["profiles"],
                                flag=spec["flag"])
    assert rec == data[off:off + len(rec)]


def test_glyph_export_as_op_is_a_complete_recipe_op(tmp_path):
    """--as-op wraps it so it can be pasted straight into a .ffproj.json and compiled."""
    import json

    from tests.conftest import REPO_ROOT
    from fireflymcf import recipe, recipe_ops

    out = tmp_path / "glyph.json"
    r = _run("glyph-export", "firefly-13.6/mcf.bin", "--slot", "27", "--char", "3",
             "--as-op", "--out", str(out), cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    op = json.loads(out.read_text())
    assert op["type"] == "install_glyph" and op["id"]
    spec = recipe.OpSpec(id=op["id"], type=op["type"], params=op["params"])
    # It must construct against the op's own validation, not merely look right.
    recipe_ops  # noqa: B018 -- imported for its registration side effect
    recipe.REGISTRY[spec.type](spec)


def test_image_export_lists_and_dumps_a_ppm(tmp_path):
    """`image-export` with no --index lists the images; with one it writes a P6 PPM whose header
    dimensions match the table."""
    from tests.conftest import REPO_ROOT
    from fireflymcf import images
    r = _run("image-export", "firefly-13.6/mcf.bin", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "125 x 125" in r.stdout           # image 0

    ppm = tmp_path / "img0.ppm"
    r = _run("image-export", "firefly-13.6/mcf.bin", "--index", "0", "--out", str(ppm),
             cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    w, h = images.dimensions((REPO_ROOT / "firefly-13.6/mcf.bin").read_bytes(), 0)
    assert ppm.read_bytes().startswith(b"P6\n%d %d\n255\n" % (w, h))


def test_swap_image_replaces_an_image_in_place(tmp_path):
    """Export image 0, invert it, swap it back: the new MCF must carry the inverted image, leave a
    neighbour untouched, and be a valid resealed container."""
    from tests.conftest import REPO_ROOT
    from fireflymcf import images, imageenc, coverage
    stock = (REPO_ROOT / "firefly-13.6/mcf.bin").read_bytes()

    img = images.decode_image(stock, 0)
    inv = bytes(255 - b for b in img.pixels)
    ppm = tmp_path / "inv.ppm"
    ppm.write_bytes(imageenc.write_ppm(img.width, img.height, inv))

    out = tmp_path / "mcf-inv.bin"
    r = _run("swap-image", "firefly-13.6/mcf.bin", str(ppm), "--index", "0",
             "--out", str(out), cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    new = out.read_bytes()
    assert images.decode_image(new, 0).pixels == inv
    assert images.decode_image(new, 1).pixels == images.decode_image(stock, 1).pixels
    assert coverage.analyse(new, deep=True).unclaimed == []


def test_pages_lists_navigable_pages_with_root_and_page_columns():
    """`pages` enumerates every build's navigable screens, their --root index, and header. It is
    build-general, so it must work on all three corpus builds and annotate the named-page shortcuts
    (home/lights/climate/settings resolve on every build)."""
    import json
    from tests.conftest import REPO_ROOT
    for mcf in ["firefly-13.6/mcf.bin", "firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"]:
        r = _run("pages", mcf, "--json", cwd=REPO_ROOT)
        assert r.returncode == 0, r.stderr
        pages = json.loads(r.stdout)
        assert len(pages) > 20                      # dozens of navigable screens per build
        by_page = {p["page"]: p for p in pages if p["page"]}
        # HOME is screen 0 on every build; the named shortcuts resolve to real roots.
        home = next(p for p in pages if p["screen"] == 0)
        assert home["header"] == "HOME" and home["page"] == "home"
        for name in ("home", "lights", "climate", "settings"):
            assert name in by_page, f"{name} not annotated on {mcf}"


def test_pages_root_index_actually_renders(tmp_path):
    """A --root value from `pages` must render -- specifically 14.7's electrical page, whose static
    header is blank (dynamic AES/AGS caption) so `--page electrical` cannot find it, is reachable by
    the --root the tool reports for screen 1."""
    import json
    from tests.conftest import REPO_ROOT
    r = _run("pages", "firefly-14.7 (Terrain)/mcf.bin", "--json", cwd=REPO_ROOT)
    pages = {p["screen"]: p for p in json.loads(r.stdout)}
    root = pages[1]["root"]                          # screen 1 = the electrical slot
    out = tmp_path / "e.png"
    r = _run("render", "firefly-14.7 (Terrain)/mcf.bin", str(out), "--root", str(root),
             cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert out.exists()


def test_doctor_reports_build_general_capability_summary():
    """`doctor` is the one-command owner report; it must work on every corpus build and surface the
    self-identified interlocks -- Progressive Inverter (removable) on 13.6, Lithionics Batteries
    (reported-only) on 14.7, none on 11.23."""
    import json
    from tests.conftest import REPO_ROOT
    def report(mcf):
        r = _run("doctor", mcf, "--json", cwd=REPO_ROOT)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    d136 = report("firefly-13.6/mcf.bin")
    assert d136["build_name"] == "Jayco B-Van V13"
    assert d136["screens"] > 20 and d136["images"] == 8
    assert any(s["name"].endswith("GuiData") for s in d136["sections"])
    assert d136["interlocks"]["removable"] is True
    assert d136["interlocks"]["couplings"] == [{"with_option": "Progressive Inverter", "handlers": 4}]

    d147 = report("firefly-14.7 (Terrain)/mcf.bin")
    assert d147["interlocks"]["removable"] is False
    assert d147["interlocks"]["couplings"] == [{"with_option": "Lithionics Batteries", "handlers": 2}]

    d112 = report("firefly-11.23/mcf.bin")
    assert d112["interlocks"]["couplings"] == []


def test_every_subcommand_prints_help():
    """`render --help` used to raise TypeError: a literal `%` in the --tank-level help string
    was read by argparse as a format directive. Every documented command must print its help."""
    import argparse
    import contextlib
    import io
    from fireflymcf import cli as cli_mod
    parser = argparse.ArgumentParser(prog="fireflymcf")
    # Reuse main()'s parser by driving it through main(): --help raises SystemExit(0).
    for cmd in ("extract", "regions", "identify", "coverage", "labels", "patch", "options",
                "doctor", "pages", "image-export", "swap-image", "glyph-export", "g12-rules",
                "g12-inputs", "slot-source", "boot-lottery", "pins", "gestures", "compile",
                "validate", "render"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as e:
            cli_mod.main([cmd, "--help"])
        assert e.value.code == 0, cmd
        assert "usage: fireflymcf %s" % cmd in out.getvalue(), cmd


def test_cli_validate_passes_v26():
    """`validate` is the one-command pre-flash gate: it must PASS (exit 0) on the recipe that
    is actually running on the coach, with the stock auto-located by base_sha256."""
    from tests.conftest import REPO_ROOT
    r = _run("validate", "builds/experiment-v26-coach48v.ffproj.json", cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout


def test_cli_validate_fails_on_wrong_stock():
    """Pointed at a stock image the project was not built against, compilation must fail its
    base-hash gate and `validate` must report FAIL and exit 1."""
    from tests.conftest import REPO_ROOT
    r = _run("validate", "builds/experiment-v26-coach48v.ffproj.json",
             "firefly-11.23/mcf.bin", cwd=REPO_ROOT)
    assert r.returncode == 1
    assert "FAIL" in (r.stdout + r.stderr)
