"""The package runs the same on Windows, macOS and Linux.

Windows is where the defaults differ: Python's text files and a piped stdout take the ANSI code
page (cp1252), not UTF-8, so an unencoded read decodes a UTF-8 project wrongly and a `print` of
`—` or `↔` raises. Neither shows on Linux, so these tests force the Windows behaviour.
"""
import ast
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
#: Every tree that ships or runs on an owner's machine: the package and Studio's server.
TREES = (ROOT / "src" / "fireflymcf", ROOT / "studio" / "server")
TEXT_IO = {"read_text", "write_text"}


def _mode(call: ast.Call, pos: int) -> str | None:
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return call.args[pos].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _unencoded_text_io(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "open":
            mode = _mode(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr == "open":
            mode = _mode(node, 0)
        elif isinstance(f, ast.Attribute) and f.attr == "fdopen":
            mode = _mode(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr in TEXT_IO:
            mode = "t"
        else:
            continue
        if mode is None or "b" not in mode:
            yield node.lineno


def test_every_text_file_is_opened_as_utf8():
    misses = [f"{p.relative_to(ROOT)}:{line}"
              for tree in TREES for p in sorted(tree.rglob("*.py"))
              for line in _unencoded_text_io(ast.parse(p.read_text(encoding="utf-8")))]
    assert misses == []


def test_scan_catches_an_unencoded_open():
    tree = ast.parse("open(p)\nopen(p, 'w')\nPath(p).read_text()\nopen(p, 'rb')\n"
                     "open(p, encoding='utf-8')\nPath(p).open('rb')\nos.fdopen(fd, 'w')\n"
                     "os.fdopen(fd, 'wb')\n")
    assert list(_unencoded_text_io(tree)) == [1, 2, 3, 7]


def test_console_survives_a_cp1252_pipe(monkeypatch):
    from fireflymcf import console
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    console.utf8()
    print("report — a ↔ b • …")
    sys.stdout.flush()
    assert sys.stdout.buffer.getvalue().decode("utf-8") == "report — a ↔ b • …\n"


def test_entry_points_set_up_the_console(monkeypatch):
    from fireflymcf import cli, g12flash, tankpatch
    calls = []
    monkeypatch.setattr("fireflymcf.console.utf8", lambda: calls.append(1))
    for main in (cli.main, tankpatch.main, g12flash.main):
        with pytest.raises(SystemExit):
            main(["--help"])
    assert len(calls) == 3


def test_render_font_falls_back_without_dejavu(monkeypatch):
    from PIL import Image, ImageDraw
    from fireflymcf import render
    monkeypatch.setattr(render, "_FONT_REG", "/nonexistent/DejaVuSans.ttf")
    monkeypatch.setattr(render, "_FONT_BLD", "/nonexistent/DejaVuSans-Bold.ttf")
    monkeypatch.setattr(render, "_FONT_NAMES", (("no-such-bold.ttf", "no-such.ttf"),))
    monkeypatch.setattr(render, "_font_cache", {})
    font = render._font(14)
    img = Image.new("RGB", (80, 20))
    ImageDraw.Draw(img).text((40, 10), "AES", font=font, fill=(255, 255, 255), anchor="mm")
    assert img.getbbox() is not None
