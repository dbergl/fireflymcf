"""`--page outputs` -- the G12 OUTPUTS diagnostics screen (44-row output grid).

Object indices are NOT build-stable, and the header text alone is ambiguous: object 14
on 13.6 ALSO carries the header "NETWORK DIAGNOSTICS" (it's the DEVICES/FAULTS tab, not
the output grid), so `_resolve_page_root`'s usual header-match convention
(`_PAGE_HEADERS`) cannot be reused unmodified for this page.

`_resolve_page_root(data, "outputs")` instead uses a structural signature: the object
whose OWN (non-recursive) record list contains a label that strips to exactly
"G12 OUTPUTS" -- confirmed unique per build (13.6: object 35 with 44 numbered rows,
11.23: object 675 with 11, 14.7: object 32 with 44), including known pin-name rows like
"44 - Water Pump". Verified directly against `firefly-13.6/mcf.bin` before writing this
test (`cli render firefly-13.6/mcf.bin x.png --root 35` -> "G12 OUTPUTS" grid).
"""
from __future__ import annotations

from fireflymcf.cli import _resolve_page_root, main


MCF_136 = "firefly-13.6/mcf.bin"


def test_resolve_page_root_outputs_is_object_35_on_136():
    data = open(MCF_136, "rb").read()
    assert _resolve_page_root(data, "outputs") == 35


def test_root_35_and_page_outputs_render_the_same_bytes(tmp_path):
    """`--page outputs` must resolve to the same object `--root 35` does -- the
    object independently verified (per CLAUDE.md's task) to render "NETWORK
    DIAGNOSTICS / G12 OUTPUTS" with the 44-output grid including "Water Pump" at
    row 44."""
    by_root = tmp_path / "by_root.png"
    by_page = tmp_path / "by_page.png"
    assert main(["render", MCF_136, str(by_root), "--root", "35"]) == 0
    assert main(["render", MCF_136, str(by_page), "--page", "outputs"]) == 0
    assert by_root.read_bytes() == by_page.read_bytes()


def test_page_outputs_is_accepted_by_the_render_cli(tmp_path):
    out = tmp_path / "out.png"
    rc = main(["render", MCF_136, str(out), "--page", "outputs"])
    assert rc == 0
    assert out.exists()
