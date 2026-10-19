"""Task 3 end-to-end: build a genuinely new, always-visible button on the 13.6 home page.

The pipeline, from stock `firefly-13.6/mcf.bin`, entirely from the Task 1/2 machinery:

1. **Clone** an existing home control object (`insert.clone_object`) — a byte-exact copy that
   decodes identically to its donor.
2. **Insert** it under the home page (`insert.insert_object`, parent index 4): the clone becomes
   a brand-new object at the top object index and a new child record on the home page points at
   it. This grows the image and repairs every downstream reference; the returned `Edit` list is
   in final-image coordinates.
3. **Retarget the clone** with the proven same-size primitives (`patch.McfPatcher.set_label` +
   `set_position`) so the new button is visibly its own control: a distinct label and a distinct
   location, edited on the copy inside the *new* object's byte span — never the donor's original.
4. **Verify** with generalised L3 (`verify.generalized_l3`): every byte that differs from stock
   is an inserted span, a shifted reference, a resealed checksum, the parent's record-count, or a
   same-size retarget inside the clone — derived analytically from the accumulated edit list.

## Donor: object 1409 ("Inverter")

The home page (object 4) shows its always-visible controls through container child objects
892/899/907/974; those containers carry no label of their own, so they cannot be *retargeted*
with `set_label`. Object 1409 is one of the three labelled single-button controls the home page
renders (Awning / CLIMATE / **Inverter**, wrapped by container 899): a clean, self-contained
control that carries its own 8-character label field — roomy enough for a same-size rename to
"Starlink" (8 chars), which fits without giving up the field. Cloning it and attaching the clone
directly under the home page makes it a genuinely new, always-visible home button.

The label sits in a `wtype`-11 rounded-button record — the same record shape probe 1 moved on the
coach (obj 247 "Bath Fan", `docs/MCF-FORMAT.md` §6). `set_position` edits the two `u16` before the
`01 <font> ff` selector, which for this record shape are the button box's **far corner** (the field
probe 1 wrote), **not** the box origin; `layout.py` reads the origin from `geom[0]/geom[1]`, which
this donor inherits from its attach frame. So `set_position` here moves the box's far corner
(verified: it lands at the requested `(300, 250)`) but leaves the origin at the attach frame's
`(0, 0)`. Only same-size edits are used — the rename is 8->8, each coordinate a `u16` in place — so
no byte is added anywhere.

**Honest limitation — read before reusing.** What is *proven* here is the PIPELINE
(clone -> insert -> same-size retarget -> generalised-L3 verify): a genuinely new object,
attached to the home page, decoded and rendered, with every changed byte accounted for. What is
*not* delivered is a cleanly **placed** button: because only the far corner is
`set_position`-controllable and the origin is fixed by the attach frame, the clone's box spans
`(0,0)`-`(300,250)` and overlaps the HOME header in the decoded render. A clean, well-placed
button needs a follow-on this task does not build — a donor (or an attach-frame / box-origin
override) that also exposes the origin. In a **bounded** scan of the compact clonable objects
(small enough to be single controls, up to ~120 bytes) I found none whose label is an absolute
`base_mode`-0 text draw that `set_position` would move via `geom[0]/geom[1]`; the classic absolute
text draws (the probe-1 shape) sit embedded in large page objects, not as standalone clonable
objects. That is a bounded observation, not an exhaustive proof.
"""
from __future__ import annotations

import os

from . import guidata, insert, patch, verify

STOCK_PATH = "firefly-13.6/mcf.bin"
HOME_PAGE_INDEX = 4
DONOR_INDEX = 1409
DONOR_LABEL = "Inverter"
NEW_LABEL = "Starlink"                 # 8 chars, same size as "Inverter" — a same-size rename
NEW_POSITION = (300, 250)              # the retargeted button-box far corner (x, y)


class DemoError(RuntimeError):
    """The demo pipeline could not produce a build that passes its own gates."""


def _clone_label_site(data: bytes, clone_index: int, text: str) -> patch.LabelSite:
    """The label site for `text` that lies inside object `clone_index`'s span — the CLONE's
    copy, not the donor's. After cloning there are (at least) two sites holding the donor's
    label; selecting the one inside the new top-index object's byte span is what guarantees the
    retarget edits the new button and leaves the original untouched.
    """
    gs, _ = guidata.guidata_extent(data)
    start, end = insert._object_span(data, clone_index)
    sites = [s for s in patch.find_label_sites(data, text)
             if start <= (s.length_offset - gs) < end]
    if len(sites) != 1:
        raise DemoError(
            f"expected exactly one {text!r} label site inside the clone span "
            f"[{start:#x},{end:#x}), found {len(sites)}")
    return sites[0]


def build_new_button(stock: bytes) -> dict:
    """Run the whole pipeline against `stock`. Returns a dict with the final image, the new
    object index, the clone span, and the accumulated edit list (insert edits + retarget edits,
    in application order) that `verify.generalized_l3` consumes. Raises `DemoError`/underlying
    errors on any failure; the returned build has already passed generalised L3.
    """
    # 1. clone + 2. insert under the home page
    donor_bytes = insert.clone_object(stock, DONOR_INDEX)
    inserted, insert_edits = insert.insert_object(stock, donor_bytes, HOME_PAGE_INDEX)
    new_index = len(guidata.object_tables(inserted)[insert.OBJECT_TABLE_INDEX]) - 1

    # 3. retarget the clone (same-size): rename + reposition, on the copy inside the new span
    site = _clone_label_site(inserted, new_index, DONOR_LABEL)
    element = patch.find_draw_element(inserted, site.length_offset)
    patcher = patch.McfPatcher(inserted)
    patcher.set_label(site, DONOR_LABEL, NEW_LABEL)
    patcher.set_position(element, x=NEW_POSITION[0], y=NEW_POSITION[1])
    retarget_content = patcher.edits()
    final, changed_labels = patcher.build()

    # Build the retarget checksum edits (McfPatcher.build reseals but returns only labels): one
    # Edit per changed checksum field, in final-image coordinates == inserted coordinates (the
    # retarget is same-size, so no byte moved). Same shape as relocate/insert's checksum edits.
    positions = {f.label: f.position for f in patch.checksum_fields(inserted)}
    retarget_checksums = [
        patch.Edit(positions[label], inserted[positions[label]:positions[label] + 2],
                   final[positions[label]:positions[label] + 2], "checksum",
                   f"resealed {label}")
        for label in changed_labels]

    edits = list(insert_edits) + list(retarget_content) + retarget_checksums

    # 4. verify — generalised L3 over the accumulated edit list
    report = verify.generalized_l3(stock, final, edits)

    clone_start, clone_end = insert._object_span(final, new_index)
    return {
        "final": final,
        "new_index": new_index,
        "clone_span": (clone_start, clone_end),
        "edits": edits,
        "l3": report,
        "changed_checksums": changed_labels,
    }


def write_demo(stock_path: str = STOCK_PATH, out_dir: str = "build") -> dict:
    """Build the demo from `stock_path`, write the patched MCF and a decoded home-page render
    under `out_dir`, and return the build dict plus the two output paths. Import of the renderer
    is deferred so the core pipeline does not depend on Pillow.
    """
    from . import render

    stock = open(stock_path, "rb").read()
    build = build_new_button(stock)

    os.makedirs(out_dir, exist_ok=True)
    bin_path = os.path.join(out_dir, "relocator-new-button-13.6.bin")
    png_path = os.path.join(out_dir, "relocator-new-button-13.6.png")
    with open(bin_path, "wb") as fh:
        fh.write(build["final"])

    placements = render.climate_placements_decoded(build["final"], root=HOME_PAGE_INDEX)
    render.rasterize(placements).save(png_path)

    build["bin_path"] = bin_path
    build["png_path"] = png_path
    return build


if __name__ == "__main__":
    result = write_demo()
    r = result["l3"]
    print(f"wrote {result['bin_path']} and {result['png_path']}")
    print(f"new object index {result['new_index']}, clone span "
          f"[{result['clone_span'][0]:#x},{result['clone_span'][1]:#x})")
    print(f"grew by {r.grew_by} bytes; edits by category: {r.counts}; "
          f"{r.differing_bytes} differing bytes all explained")
    print(f"resealed checksums: {result['changed_checksums']}")
