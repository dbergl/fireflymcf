"""The base install must stay stdlib-only for the common edit/read commands.

`pyproject.toml` declares numpy/capstone/Pillow as an optional `[full]` extra, and GETTING-STARTED
promises the common edits work on a bare Python. That promise is only real if importing the CLI and
running those commands never imports one of the heavy deps. This test enforces it by installing an
import blocker for numpy/capstone/PIL and running each base command; a regression (some core path
growing a top-level `import numpy`) turns this red instead of surfacing as a broken install.

The commands that legitimately need the extra -- render, options, pins, `patch --recolor`/`--remap`
-- are expected to raise ModuleNotFoundError here, and `cli.main` turns that into an actionable
message (asserted at the end).
"""
import contextlib
import importlib.abc
import io
import sys

import pytest

from tests.conftest import REPO_ROOT

_HEAVY = {"numpy", "capstone", "PIL"}


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in _HEAVY:
            raise ModuleNotFoundError(f"blocked {name}", name=name)
        return None


@pytest.fixture
def no_heavy_deps():
    """Block numpy/capstone/PIL imports, and drop any already-imported copies so a lazy
    `import numpy` inside a command re-runs the (blocked) import rather than finding a cached one."""
    blocker = _Blocker()
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in _HEAVY}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


def _run(args):
    from fireflymcf import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(args)
    return rc, buf.getvalue()


MCF = "firefly-13.6/mcf.bin"


@pytest.mark.parametrize("args", [
    ["labels", MCF],
    ["coverage", MCF],
    ["identify", MCF],
    ["image-export", MCF],
    ["glyph-export", MCF, "--slot", "27", "--char", "A"],
    ["g12-rules", MCF],
    ["gestures", MCF],
])
def test_base_commands_run_without_numpy_capstone_or_pillow(no_heavy_deps, args, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    rc, _ = _run(args)
    assert rc in (0, None), f"{args[0]} returned {rc} on a stdlib-only install"


def test_swap_image_and_rename_run_without_heavy_deps(no_heavy_deps, tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    from fireflymcf import images, imageenc  # stdlib-only modules
    stock = (REPO_ROOT / MCF).read_bytes()
    img = images.decode_image(stock, 0)
    ppm = tmp_path / "i.ppm"
    ppm.write_bytes(imageenc.write_ppm(img.width, img.height, img.pixels))
    rc, _ = _run(["swap-image", MCF, str(ppm), "--index", "0", "--out", str(tmp_path / "o.bin")])
    assert rc in (0, None)
    rc, _ = _run(["patch", MCF, str(tmp_path / "r.bin"), "--rename", "Bath Fan=Vent Fan",
                  "--no-verify"])
    assert rc in (0, None)


def test_a_command_that_needs_the_extra_gives_an_actionable_message(
        no_heavy_deps, capsys, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    from fireflymcf import cli
    rc = cli.main(["pins", MCF])
    assert rc == 3
    err = capsys.readouterr().err
    assert "fireflymcf[full]" in err and "pins" in err


def test_doctor_reports_needs_full_not_false_zeros_on_bare_install(no_heavy_deps, capsys, monkeypatch):
    """`doctor` runs on a bare install (build name + sections are stdlib-only), but the deeper
    analyses need [full]. It MUST say so, not report a false 'Options: 0 / no interlocks' -- that
    silent wrong answer is the exact hazard this project guards against."""
    monkeypatch.chdir(REPO_ROOT)
    from fireflymcf import cli
    rc = cli.main(["doctor", MCF])
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "Lyra 13v6 GuiData" in out                 # stdlib-only section list still works
    assert "fireflymcf[full]" in out                  # heavy analyses flagged, not faked
    assert "none on this build" not in out            # must NOT claim "no interlocks"
