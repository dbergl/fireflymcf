import hashlib
import json
import struct
import pytest
from fireflymcf import cli, guidata, patch, records, recipe

STOCK_PATH = "firefly-13.6/mcf.bin"

@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()

@pytest.fixture(scope="module")
def stock_hash(stock):
    return hashlib.sha256(stock).hexdigest()

def compile_ops(stock, stock_hash, *specs):
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=list(specs))
    return recipe.compile_project(p, stock)

def labels_of(data, obj_index):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    return {getattr(r, "label", None)
            for r in records.object_records(data, gs + tab[obj_index])}


def _pump_feature_ops():
    """The op list `builds/golden-v22-anchor.ffproj.json` composes for the whole Water Pump
    feature -- a fresh list of `recipe.OpSpec` every call, since callers mutate `.params`
    freely:

      "pumplink" (`add_option_follower`) -- the brand-new Pump Link OPTIONS row + the
      bath-light -> water-pump follower, masked (`gate_mask`/`gate_on_value`) so its gate
      survives `add_pump_auto_off`'s widening of the shared 0xEE slot to a u16 (PC.47).
      "pumpauto" (`add_pump_auto_off`) -- the Auto Off routine; ADOPTS "pumplink"'s group
      164 config-toggle rather than minting a second one, so it must run right after it.
      "aes" (`swap_aes_electrical`) -- the compact AES frame, wired to "pumpauto"'s Water
      Pump section (`pump_section={"ref": "pumpauto"}`); must run after "pumpauto"."""
    return [
        recipe.OpSpec(id="pumplink", type="add_option_follower",
                      params={"watch_channel": 25, "target_channel": 44,
                              "gate_mask": 0xFF00, "gate_on_value": 0x0100}),
        recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}),
        recipe.OpSpec(id="aes", type="swap_aes_electrical",
                      params={"pump_section": {"ref": "pumpauto"}}),
    ]


def test_rename_same_size(stock, stock_hash):
    # 'Bath Fan' lives on the HOME page control; same length replacement.
    site = [s for s in patch.find_label_sites(stock, "Bath Fan")][0]
    obj = _object_of(stock, site.text_offset)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": obj, "old": "Bath Fan",
                                          "new": "Vent Fan"}))
    assert r.ok, r.errors
    assert len(r.data) == len(stock)
    assert any(lbl and "Vent Fan" in lbl for lbl in labels_of(r.data, obj))

def _object_of(data, text_offset):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    spans = sorted((off, i) for i, off in enumerate(tab))
    prev = None
    for off, i in spans:
        if gs + off > text_offset:
            return prev
        prev = i
    return prev

def test_rename_grow(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28 - Overflow Valve",
                                          "allow_grow": True}))
    assert r.ok, r.errors
    assert len(r.data) > len(stock)
    assert any(lbl and lbl.startswith("28 - Overflow Valve")
               for lbl in labels_of(r.data, 35))

def test_rename_too_long_offers_grow(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28 - Overflow Valve"}))
    assert not r.ok
    assert r.errors[0]["code"] == "LABEL_TOO_LONG"
    assert r.errors[0]["offers"] == ["grow", "shorten"]
    assert r.errors[0]["op_id"] == "a"

def test_rename_unknown_label(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "zzz", "new": "yyy"}))
    assert not r.ok and r.errors[0]["code"] == "NO_SUCH_LABEL"

def test_rename_noop_same_size(stock, stock_hash):
    # Renaming a label to itself should succeed with no size change.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28 - "}))
    assert r.ok, r.errors
    assert len(r.data) == len(stock)

def test_rename_grow_relocation_error(stock, stock_hash, monkeypatch):
    # When grow_label raises RelocationError, ensure GROW_REFUSED is returned.
    from fireflymcf import labels, relocate

    def raise_relocation_error(*args, **kwargs):
        raise relocate.RelocationError("boom")

    monkeypatch.setattr(labels, "grow_label", raise_relocation_error)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28 - Overflow Valve",
                                          "allow_grow": True}))
    assert not r.ok
    assert r.errors[0]["code"] == "GROW_REFUSED"

def test_rename_invalid_align(stock, stock_hash):
    # 'center' became a real mode on 2026-08-21 (a centred record shifts with its pad), so the
    # invalid case needs a value that is genuinely not one.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28 - X", "align": "middle"}))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_PARAMS"


def test_rename_center_align_splits_the_pad(stock, stock_hash):
    """A centred record must not take its slack all at the end -- that is the reference build v1/v2
    caption defect, where the panel centred 'Valve   ' and drew the word 9 px left."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="rename_label",
                                  params={"object": 35, "old": "28 - ",
                                          "new": "28-X", "align": "center"}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    labels = [x.label for x in records.object_records(r.data, gs + tab[35]) if x.label]
    # the field is 5 bytes and '28-X' is 4, so the single slack space goes to the LEFT
    assert " 28-X" in labels, [l for l in labels if "28-X" in l]

def _gs_tab_t(data):
    gs, _ = guidata.guidata_extent(data)
    return gs, guidata.object_tables(data)[1]

def _geom(data, obj, wtype, nth, i):
    gs, tab = _gs_tab_t(data)
    recs = [r for r in records.object_records(data, gs + tab[obj]) if r.wtype == wtype]
    off = records.geom_offset(data, recs[nth], i)
    return struct.unpack_from("<H", data, off)[0]

def test_move_element_battery(stock, stock_hash):
    # The from_stock battery-block move: object 902, the 'Chassis' record, y 71 -> 6.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="move_element",
                                  params={"target": 902,
                                          "selector": {"label": "Chassis"},
                                          "set": {"y": 6}, "expect": {"y": 71}}))
    assert r.ok, r.errors
    assert len(r.data) == len(stock)

def test_move_element_expect_mismatch(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="move_element",
                                  params={"target": 902,
                                          "selector": {"label": "Chassis"},
                                          "set": {"y": 6}, "expect": {"y": 9999}}))
    assert not r.ok and r.errors[0]["code"] == "EXPECT_MISMATCH"

def test_move_element_no_field(stock, stock_hash):
    # FOLD-IN: the old `if not r.ok:` guard here was conditionally vacuous -- object
    # 902's 'Chassis' record actually carries x/y/w/h, so the op always succeeds and
    # the guarded assertion never ran. Assert the real, concrete outcome instead.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="move_element",
                                  params={"target": 902,
                                          "selector": {"label": "Chassis"},
                                          "set": {"x": 1, "y": 6, "w": 1, "h": 1}}))
    assert r.ok, r.errors
    # and the optional form never fails on a missing field:
    r2 = compile_ops(stock, stock_hash,
                     recipe.OpSpec(id="b", type="move_element",
                                   params={"target": 902,
                                           "selector": {"label": "Chassis"},
                                           "set": {"y": 6, "h": 50},
                                           "optional": ["h"]}))
    assert r2.ok, r2.errors

def test_move_element_expect_key_not_in_set_is_bad_params(stock, stock_hash):
    # FOLD-IN: an `expect` key that never appears in `set` is a silently-inert
    # assertion (it never gets checked, since the write loop drives the check) --
    # a user-authored safety assertion must never be silently inert. Refuse instead.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="move_element",
                                  params={"target": 902,
                                          "selector": {"label": "Chassis"},
                                          "set": {"y": 6},
                                          "expect": {"x": 999}}))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_PARAMS"

def test_move_attach(stock, stock_hash):
    # Move an existing HOME child's attach record. Find a wtype-5 child of HOME first.
    home = cli._resolve_page_root(stock, "home")
    gs, tab = _gs_tab_t(stock)
    att = [r for r in records.object_records(stock, gs + tab[home])
           if r.wtype == 5 and r.child_index is not None and r.geom[1] is not None]
    child = att[0].child_index
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="move_attach",
                                  params={"page": "home", "child": child, "y": 388}))
    assert r.ok, r.errors
    out_att = [r2 for r2 in records.object_records(r.data, gs + tab[home])
               if r2.wtype == 5 and r2.child_index == child]
    off = records.geom_offset(r.data, out_att[0], 1)
    assert struct.unpack_from("<H", r.data, off)[0] == 388

def test_set_font(stock, stock_hash):
    # Water Pump's caption (obj 295) is slot 13; set another wtype-11 caption to 13.
    # Use obj 279 (the donor button) whose caption carries a selector.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_font",
                                  params={"target": 279,
                                          "selector": {"wtype": 11, "nth": 0},
                                          "slot": 13}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    from fireflymcf import records as R
    recs = [x for x in R.object_records(r.data, gs + tab[279]) if x.wtype == 11]
    assert recs[0].font == 13

def test_set_font_no_selector_is_error(stock, stock_hash, monkeypatch):
    # When a record has no R38 selector, ensure NO_FONT_SELECTOR is returned.
    # Monkeypatch font_offset to return None for the recipe_ops module's use.
    import fireflymcf.recipe_ops

    def mock_font_offset(*args, **kwargs):
        return None

    monkeypatch.setattr(fireflymcf.recipe_ops.records, "font_offset",
                        mock_font_offset)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_font",
                                  params={"target": 279,
                                          "selector": {"wtype": 11, "nth": 0},
                                          "slot": 13}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_FONT_SELECTOR"

def test_recolor_by_rgb(stock, stock_hash):
    # Recolour the donor button 279's box to button blue via the theme resolver.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="recolor",
                                  params={"object": 279, "color": [65, 81, 163]}))
    assert r.ok, r.errors
    assert len(r.data) == len(stock)

def test_recolor_no_button_box_is_no_such_color(stock, stock_hash):
    # FINDING 1(a): object 1 has no mode-7 fillA box, so patch.find_button_box raises
    # patch.PatchError. It used to sit OUTSIDE Recolor's try, so it escaped
    # compile_project's `except RecipeError` filter unstructured -- must come back as
    # a structured NO_SUCH_COLOR failure instead.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="recolor",
                                  params={"object": 1, "color": [65, 81, 163]}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_SUCH_COLOR"

def test_recolor_to_current_value_is_noop(stock, stock_hash):
    # FINDING 1(b): recolouring to the value already present makes zero edits, so
    # McfPatcher.build() raises PatchError("no edits to apply") -- that call used to
    # sit OUTSIDE the try too. Recolour object 279 to its own current index twice in
    # one project; both must succeed as no-ops, byte-identical to stock.
    from fireflymcf import patch
    box = patch.find_button_box(stock, 279)
    fillA = next(c for c in box.colors if c[0] == "fillA")
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="recolor",
                                  params={"object": 279, "color": {"index": fillA[2]}}),
                    recipe.OpSpec(id="b", type="recolor",
                                  params={"object": 279, "color": {"index": fillA[2]}}))
    assert r.ok, r.errors
    assert r.data == stock

def test_recolor_verify_catches_later_repoint(stock, stock_hash):
    # FINDING 2: Recolor.verify() used to only re-resolve find_button_box, which is
    # vacuous -- a later op repointing the SAME box's fillA (wire_button, on a shell
    # this recolor also targets) would lose the recolor while the manifest still
    # claims it. verify() must re-read fillA and assert it still matches the request.
    p = recipe.Project(
        base_sha256=stock_hash, build_name=None,
        ops=[recipe.OpSpec(id="shell", type="add_glyph_button",
                           params={"page": "home", "donor": 247, "x": 630, "y": 388,
                                   "w": 120, "h": 60, "fill_cell": 164}),
             recipe.OpSpec(id="a", type="recolor",
                           params={"object": {"ref": "shell"}, "color": [65, 81, 163]}),
             recipe.OpSpec(id="wire", type="wire_button",
                           params={"target": {"ref": "shell"}, "channel": 29,
                                   "off_colour": [65, 81, 163],
                                   "allow_commanded_channel": True})])
    with pytest.raises(AssertionError, match="a later op repointed it"):
        recipe.compile_project(p, stock)

def test_set_icon_slot_and_char(stock, stock_hash):
    # Object 247 (Bath Fan template): icon is font 24 char 'E'. Change the char.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_icon",
                                  params={"target": 247, "char": "F"}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    from fireflymcf import records as R
    icon = [x for x in R.object_records(r.data, gs + tab[247])
            if x.wtype == 11 and x.font is not None][0]
    assert icon.label == "F"

def test_set_icon_blank_caption(stock, stock_hash):
    # Object 247 (Bath Fan template): blank the caption, leaving the icon unchanged.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_icon",
                                  params={"target": 247, "blank_caption": True}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    from fireflymcf import records as R
    recs = [x for x in R.object_records(r.data, gs + tab[247]) if x.wtype == 11]
    # Find the icon (has font) and caption (no font, label is spaces)
    icon = [x for x in recs if x.font is not None][0]
    caption = [x for x in recs if x is not icon][0]
    assert caption.label.strip() == "", "caption label should be blank"

def test_set_icon_current_picks_one_of_several_icons(stock, stock_hash):
    # Object 390 (the nav rail's six-icon arm): P D Q R S L. `current` addresses 'S' alone.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_icon",
                                  params={"target": 390, "current": "S", "char": "Y"}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    from fireflymcf import records as R
    icons = [x.label for x in R.object_records(r.data, gs + tab[390])
             if x.wtype == 11 and x.font is not None]
    assert icons == ["P", "D", "Q", "R", "Y", "L"]


def test_set_icon_current_must_name_exactly_one_icon(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_icon",
                                  params={"target": 390, "current": "Z", "char": "Y"}))
    assert not r.ok and r.errors[0]["code"] == "NO_SUCH_RECORD"


def test_remap_channel(stock, stock_hash):
    # The proven job: Bath Fan (instance 40) -> another instance. Use 38 as the
    # hardware remap did (PC.13-16).
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "Bath Fan", "channel": 38}))
    assert r.ok, r.errors
    caps_before = recipe.capabilities(stock)
    caps_after = recipe.capabilities(r.data)
    moved_cmds = {cmd for c, cmd in caps_before if c == 40} - \
                 {cmd for c, cmd in caps_after if c == 40}
    assert moved_cmds, "remap moved nothing off channel 40"
    assert all((38, cmd) in caps_after for cmd in moved_cmds)
    # The default retargets the indicator, which has not been on the coach; PC.13-16 proved
    # the move_display_list path (v9).
    assert r.unverified == ["a"]

def test_remap_channel_move_display_list_is_the_verified_path(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "Bath Fan", "channel": 38,
                                          "move_display_list": True}))
    assert r.ok, r.errors
    assert r.unverified == []

def test_remap_channel_refuses_a_control_that_does_not_follow_its_pin(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "Water\nPump", "channel": 38}))
    assert not r.ok and r.errors[0]["code"] == "REMAP_REFUSED"
    assert "does not follow" in r.errors[0]["message"]

def test_remap_unknown_label(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "No Such Control", "channel": 38}))
    assert not r.ok and r.errors[0]["code"] == "NO_SUCH_CONTROL"

def test_remap_channel_to_own_channel_is_noop(stock, stock_hash):
    # FINDING 1(c): remapping to the channel a control already sits on makes zero
    # edits, so McfPatcher.build() raises PatchError("no edits to apply") -- that
    # call used to sit OUTSIDE RemapChannel's try. Must succeed as a byte-identical
    # no-op instead.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "Bath Fan", "channel": 40}))
    assert r.ok, r.errors
    assert r.data == stock

def test_remap_channel_capability_change_needs_acknowledge(stock, stock_hash, monkeypatch):
    # FINDING 3: remap_channel used to pass allow_capability_change=True unconditionally,
    # silently suppressing set_channel's I8 check (a dimmable control moved onto a pin
    # this file never dims/commands). On 13.6 no unambiguously-labelled control both
    # commands a single dimmable channel and remaps to a plausible destination without
    # tripping I5-I7 first, so the reviewer's fallback applies: monkeypatch
    # guidata.dimmable_channels so Bath Fan's own channel (40) reads as dimmable and
    # its destination (38) does not -- the exact shape of I8's "was != now" check.
    from fireflymcf import guidata
    monkeypatch.setattr(guidata, "dimmable_channels", lambda data: {40})

    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="remap_channel",
                                  params={"label": "Bath Fan", "channel": 38}))
    assert not r.ok
    assert r.errors[0]["code"] == "CAPABILITY_CHANGE"
    assert r.errors[0]["offers"] == ["acknowledge"]

    r2 = compile_ops(stock, stock_hash,
                     recipe.OpSpec(id="a", type="remap_channel",
                                   params={"label": "Bath Fan", "channel": 38,
                                           "allow_capability_change": True}))
    assert r2.ok, r2.errors

def test_tank_heater_names(stock, stock_hash):
    from fireflymcf import tankgates, theme
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="tank_heater_names", params={}))
    assert r.ok, r.errors
    sec = theme.guicode_section(r.data)
    base = theme.guicode_base(sec)
    n = sum(1 for a, _o, new, _w in tankgates.WIDEN_EDITS
            if r.data[sec.offset + (a - base):][:len(bytes.fromhex(new))]
            == bytes.fromhex(new))
    assert n == len(tankgates.WIDEN_EDITS)
    assert "tank-heater" in r.manifest[0]

def test_add_button(stock, stock_hash):
    from fireflymcf import gestures, newbutton, records as R
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="btn", type="add_button",
                                  params={"channel": 28, "label": "Overflow\nValve",
                                          "off_colour": [65, 81, 163], "font": 13,
                                          "allow_commanded_channel": True}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    idx = newbutton.last_object_index(r.data)
    recs = R.object_records(r.data, gs + tab[idx])
    # live-redraw invariant: fillA mode-7 index == every value_slot, one cell
    w33 = next(x for x in recs if x.wtype == 33)
    fillA = [c for c in w33.colors if c[0] == "fillA"][0]
    slots = {x.value_slot for x in recs if x.value_slot is not None}
    assert fillA[1] == 7 and slots == {fillA[2]}
    # command: single command-5 binding on channel 28, plain latch
    gid = newbutton.object_group(r.data, idx)
    grp = guidata.group_table(r.data)[gid]
    b0 = guidata.binding_table(r.data)[grp.start]
    assert grp.count == 1 and b0.channel == 28 and b0.raw[4] == 5
    assert gestures.describe_group(r.data, gid).kind == "latch"
    # caption font applied
    # per-LINE strip: the caption is centre-aligned, so its pad straddles the last line
    cap = next(x for x in recs if x.wtype == 11 and x.label
               and records.label_text(x.label) == "Overflow\nValve")
    assert cap.font == 13
    assert cap.label == "Overflow\n  Valve ", (
        "a centred caption must take its pad on both sides of the last line -- "
        "trailing-only pad is what put reference build v1/v2's 'Valve' 9 px left of centre")
    # capability gate accepted exactly (28, 5)
    assert recipe.capabilities(r.data) - recipe.capabilities(stock) == {(28, 5)}

def test_add_button_commanded_channel_needs_acknowledge(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="btn", type="add_button",
                                  params={"channel": 28, "label": "X",
                                          "off_colour": [65, 81, 163]}))
    assert not r.ok
    assert r.errors[0]["code"] == "COMMANDED_CHANNEL"
    assert r.errors[0]["offers"] == ["acknowledge"]

def test_add_button_unrelated_valueerror_is_build_refused(stock, stock_hash, monkeypatch):
    # newbutton.py:364-365's non-DC_DIMMER_COMMAND_2 refusal also contains the substring
    # "command" (inside "COMMAND_2", lowercased) but is NOT the channel-collision refusal
    # -- allow_commanded_channel cannot fix it, so it must fall through to BUILD_REFUSED,
    # not be misclassified as COMMANDED_CHANNEL with a useless "acknowledge" offer.
    import fireflymcf.recipe_ops as recipe_ops_mod

    def fake_build_home_button(*args, **kwargs):
        raise ValueError(
            "allocated group 7 contains non-DC_DIMMER_COMMAND_2 entries ([0x1234])")

    monkeypatch.setattr(recipe_ops_mod.newbutton, "build_home_button",
                        fake_build_home_button)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="btn", type="add_button",
                                  params={"channel": 28, "label": "X",
                                          "off_colour": [65, 81, 163]}))
    assert not r.ok
    assert r.errors[0]["code"] == "BUILD_REFUSED"


def test_add_glyph_button_then_wire(stock, stock_hash):
    from fireflymcf import newbutton, records as R
    r = compile_ops(
        stock, stock_hash,
        recipe.OpSpec(id="shell", type="add_glyph_button",
                      params={"page": "home", "donor": 247, "x": 630, "y": 388,
                              "w": 120, "h": 60, "fill_cell": 164}),
        recipe.OpSpec(id="wire", type="wire_button",
                      params={"target": {"ref": "shell"}, "channel": 29,
                              "off_colour": [65, 81, 163],
                              "allow_commanded_channel": True}))
    assert r.ok, r.errors
    gs, tab = _gs_tab_t(r.data)
    idx = newbutton.last_object_index(r.data)
    recs = R.object_records(r.data, gs + tab[idx])
    w33 = next(x for x in recs if x.wtype == 33)
    fillA = [c for c in w33.colors if c[0] == "fillA"][0]
    slots = {x.value_slot for x in recs if x.value_slot is not None}
    assert fillA[1] == 7 and slots == {fillA[2]}      # invariant, now on the REAL cell
    assert fillA[2] != 164                            # placeholder was overwritten
    gid = newbutton.object_group(r.data, idx)
    grp = guidata.group_table(r.data)[gid]
    b0 = guidata.binding_table(r.data)[grp.start]
    assert grp.count == 1 and b0.channel == 29 and b0.raw[4] == 5
    assert recipe.capabilities(r.data) - recipe.capabilities(stock) == {(29, 5)}
    # geometry: all four records sized 120x60
    import struct as S
    for x in recs:
        if x.wtype in (33, 11, 12):
            for i, want in ((2, 120), (3, 60)):
                off = R.geom_offset(r.data, x, i)
                assert off is not None and S.unpack_from("<H", r.data, off)[0] == want

def test_add_glyph_button_without_wire_fails_cleanly(stock, stock_hash):
    # FINDING 4: the "visual shell only" docstring claim was false -- insert.clone_object
    # copies donor 247's bytes verbatim, including its live wtype-12 group reference
    # (group 40, two bindings on channel 40), so an unwired shell is a live touch target
    # the capability gate cannot see. AddGlyphButton.verify() must refuse a shell that
    # was never wired.
    p = recipe.Project(
        base_sha256=stock_hash, build_name=None,
        ops=[recipe.OpSpec(id="shell", type="add_glyph_button",
                           params={"page": "home", "donor": 247, "x": 630, "y": 388,
                                   "w": 120, "h": 60, "fill_cell": 164})])
    with pytest.raises(AssertionError, match="was never wired"):
        recipe.compile_project(p, stock)

def test_wire_button_without_shell_fails_cleanly(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="wire", type="wire_button",
                                  params={"target": {"ref": "ghost"}, "channel": 29,
                                          "off_colour": [65, 81, 163]}))
    assert not r.ok and r.errors[0]["code"] == "BAD_REF"

def test_wire_button_indicator_refusal_is_wire_refused(stock, stock_hash, monkeypatch):
    # newbutton.retarget_indicator raises a bare ValueError for an out-of-range object
    # index or an object with no wtype-33 record (newbutton.py:520-527). That must not
    # escape compile_project's `except RecipeError` filter -- it needs to come back as a
    # structured WIRE_REFUSED, the same way the add_button tests pin BUILD_REFUSED for
    # build_home_button's unrelated ValueError.
    import fireflymcf.recipe_ops as recipe_ops_mod

    def fake_retarget_indicator(*args, **kwargs):
        raise ValueError("object 9999 has no wtype-33 (indicator) record")

    monkeypatch.setattr(recipe_ops_mod.newbutton, "retarget_indicator",
                        fake_retarget_indicator)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="shell", type="add_glyph_button",
                                  params={"page": "home", "donor": 247, "x": 630,
                                          "y": 388, "w": 120, "h": 60,
                                          "fill_cell": 164}),
                    recipe.OpSpec(id="wire", type="wire_button",
                                  params={"target": {"ref": "shell"}, "channel": 29,
                                          "off_colour": [65, 81, 163],
                                          "allow_commanded_channel": True}))
    assert not r.ok
    assert r.errors[0]["code"] == "WIRE_REFUSED"

def test_install_glyph(stock, stock_hash):
    from fireflymcf import glyphs, metrics
    # A tiny 8x4 test glyph with the convention bearing for slot 27 (cell 128).
    rows = ["ff00ff00", "0f0f0f0f", "f0f0f0f0", "00ff00ff"]
    y = (metrics.font_metrics(stock, 27).height - 4) // 2
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 27, "char": "E", "y": y,
                                          "rows": rows}))
    assert r.ok, r.errors
    g = glyphs.decode_glyph(r.data,
                            glyphs.font_for_slot(r.data, 27).glyph_index(ord("E")))
    assert (g.width, g.height) == (8, 4) and g.y == y
    assert g.rows == tuple(tuple(int(c, 16) for c in row) for row in rows)

def test_install_glyph_round_trips_an_exported_glyph(stock, stock_hash):
    """A glyph exported from a build must install back byte-identically.

    This is the property that lets a project file carry a glyph: pixels alone are not enough,
    because the 10 kerning bytes and the +0x0e flag bit cannot be derived from them
    (`GUICODE.md` Finding 46). Slot 27 'A' is a placeholder in stock, so the export is taken
    from a REAL glyph (slot 27 '3', a Go Power! icon) and installed onto the free character --
    the same shape as copying a glyph between builds.
    """
    from fireflymcf import glyphenc, glyphs

    spec = glyphenc.export_glyph(stock, 27, "3")
    src_off, _ = glyphs.glyph_extent(
        stock, glyphs.font_for_slot(stock, 27).glyph_index(ord("3")))
    want = stock[src_off:src_off + len(glyphenc.encode_glyph(
        [tuple(int(c, 16) for c in row) for row in spec["rows"]], 4,
        x=spec["x"], y=spec["y"], profiles=spec["profiles"], flag=spec["flag"]))]

    params = dict(spec, char="E", force_y=True)     # 'E' is free; keep the source's bearing
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph", params=params))
    assert r.ok, r.errors
    off, _ = glyphs.glyph_extent(r.data,
                                 glyphs.font_for_slot(r.data, 27).glyph_index(ord("E")))
    assert r.data[off:off + len(want)] == want, "installed record differs from the exported one"


def test_install_glyph_rejects_malformed_profiles(stock, stock_hash):
    """A bad `profiles` must surface as BAD_PARAMS, not as a bare ValueError from the encoder."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 27, "char": "E", "y": 62,
                                          "rows": ["ff", "ff"], "force_y": True,
                                          "profiles": [1, 2, 3]}))
    assert not r.ok and r.errors[0]["code"] == "BAD_PARAMS"


def test_install_glyph_refuses_occupied_char(stock, stock_hash):
    # Slot 27 '3' (a Go Power! glyph) already holds a real glyph.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 27, "char": "3", "y": 0,
                                          "rows": ["ff"]}))
    assert not r.ok and r.errors[0]["code"] == "GLYPH_OCCUPIED"

def test_install_glyph_claim_block(stock, stock_hash):
    """Icon font 24 marks its unused characters with one shared solid 48x48 block. Without
    `claim_block` they are occupied; with it an undrawn block character is claimed, only its own
    table entry moves, and a real glyph ('S', the slide-out icon) is still refused."""
    rows = ["f" * 4] * 4
    spec = {"slot": 24, "char": "Y", "y": 22, "rows": rows}
    r = compile_ops(stock, stock_hash, recipe.OpSpec(id="g", type="install_glyph", params=spec))
    assert not r.ok and r.errors[0]["code"] == "GLYPH_OCCUPIED"
    r = compile_ops(stock, stock_hash, recipe.OpSpec(id="g", type="install_glyph",
                                                     params=spec | {"claim_block": True}))
    assert r.ok, r.errors
    from fireflymcf import glyphs as G
    font = G.font_for_slot(r.data, 24)
    assert G.decode_glyph(r.data, font.glyph_index(ord("Y"))).rows == ((15,) * 4,) * 4
    assert G.decode_glyph(r.data, font.glyph_index(ord("Z"))).width == 48    # still the block
    r = compile_ops(stock, stock_hash, recipe.OpSpec(
        id="g", type="install_glyph", params=spec | {"char": "S", "claim_block": True}))
    assert not r.ok and r.errors[0]["code"] == "GLYPH_OCCUPIED"


def test_install_glyph_claim_block_refuses_a_drawn_block(stock, stock_hash):
    """A block character some record draws is not free: point the rail's 'S' at 'Z' first, and
    'Z' can no longer be claimed."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="a", type="set_icon",
                                  params={"target": 390, "current": "S", "char": "Z"}),
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 24, "char": "Z", "y": 22,
                                          "rows": ["ffff"] * 4, "claim_block": True}))
    assert not r.ok and r.errors[0]["code"] == "GLYPH_OCCUPIED"


def test_install_glyph_warns_off_convention_bearing(stock, stock_hash):
    # y != (cell - h) / 2 is the v20 '33 px too high' mistake -- refuse unless
    # explicitly overridden (v-align-1 records are the legitimate exception).
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 27, "char": "E", "y": 0,
                                          "rows": ["ff", "ff"]}))
    assert not r.ok and r.errors[0]["code"] == "BEARING_OFF_CONVENTION"
    assert r.errors[0]["offers"] == ["use_convention", "force"]
    r2 = compile_ops(stock, stock_hash,
                     recipe.OpSpec(id="g", type="install_glyph",
                                   params={"slot": 27, "char": "E", "y": 0,
                                           "rows": ["ff", "ff"], "force_y": True}))
    assert r2.ok, r2.errors

def test_install_glyph_relocation_error(stock, stock_hash, monkeypatch):
    # When relocate.relocate raises RelocationError, ensure RELOCATE_REFUSED is
    # returned (mirrors test_rename_grow_relocation_error).
    from fireflymcf import metrics, relocate

    def raise_relocation_error(*args, **kwargs):
        raise relocate.RelocationError("boom")

    monkeypatch.setattr(relocate, "relocate", raise_relocation_error)
    rows = ["ff00ff00", "0f0f0f0f", "f0f0f0f0", "00ff00ff"]
    y = (metrics.font_metrics(stock, 27).height - 4) // 2
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="install_glyph",
                                  params={"slot": 27, "char": "E", "y": y,
                                          "rows": rows}))
    assert not r.ok
    assert r.errors[0]["code"] == "RELOCATE_REFUSED"

def test_set_gesture_hold_to_toggle(stock, stock_hash):
    from fireflymcf import gestures, newbutton
    r = compile_ops(
        stock, stock_hash,
        recipe.OpSpec(id="btn", type="add_button",
                      params={"channel": 29, "label": "X",
                              "off_colour": [65, 81, 163],
                              "allow_commanded_channel": True}),
        recipe.OpSpec(id="g", type="set_gesture",
                      params={"target": {"ref": "btn"}, "kind": "hold_to_toggle",
                              "seconds": 3}))
    assert r.ok, r.errors
    idx = newbutton.last_object_index(r.data)
    beh = gestures.describe_group(r.data, newbutton.object_group(r.data, idx))
    assert beh.kind == "hold-only" and beh.hold_ms == 3000
    held = beh.on(gestures.PHASE_HELD)
    assert [(e.command, e.channel) for e in held] == [(5, 29)]
    assert not any(e.repeats for e in held) and beh.repeat_held_ms == 0
    for dead in (gestures.PHASE_PRESS, gestures.PHASE_TAP,
                 gestures.PHASE_RELEASE_HELD):
        assert not beh.on(dead)

def test_set_gesture_single_binding_stays_verified(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": 0, "group": 18,
                                          "kind": "hold_to_toggle"}))
    assert r.ok, r.errors
    assert r.unverified == []
    assert "UNVERIFIED" not in r.manifest[0]

def test_set_gesture_low_volts_converts_every_copy_and_is_unverified(stock, stock_hash):
    """Group 129 (Low Volts): every live copy fires on the hold, and the op says unverified."""
    from fireflymcf import gestures
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": 0, "group": 129,
                                          "kind": "hold_to_toggle", "seconds": 3}))
    assert r.ok, r.errors
    assert r.unverified == ["g"]
    line = r.manifest[0]
    assert "HARDWARE-UNVERIFIED" in line and "external variant setter" in line
    beh = gestures.describe_group(r.data, 129)
    assert beh.kinds == {0: "hold-only", 1: "hold-only"} and beh.hold_ms == 3000
    assert all(e.phases == gestures.PHASE_HELD for e in beh.emissions)

def test_set_gesture_latch_on_nonlatch_is_error(stock, stock_hash):
    # The awning group is momentary; asking for latch must refuse, not half-edit.
    # `all_groups` already returns decoded GroupBehaviour objects (not indices), so the
    # finder reads `.kind` off them directly instead of re-decoding through
    # `describe_group` a second time.
    from fireflymcf import gestures
    momentary = next(g.group for g in gestures.all_groups(stock) if g.kind != "latch")
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": -1, "group": momentary,
                                          "kind": "latch"}))
    assert not r.ok and r.errors[0]["code"] == "NOT_SUPPORTED"

def test_remove_control_parks_off_canvas(stock, stock_hash):
    # Remove the Bath Fan control (object 247's ATTACHED sibling is runtime-selected;
    # use a HOME child with a plain wtype-5 attach instead).
    from fireflymcf import cli as C, insert, records as R
    home = C._resolve_page_root(stock, "home")
    gs, tab = _gs_tab_t(stock)
    # HOME's own wtype-5 children are not all real placements: the brief's naive
    # "first child_index" pick lands on object 892, a full-canvas fill attached with
    # NO geometry at all (all six geom slots None, in 42 different parents) -- there
    # is no y field to park. Pick the first HOME child whose attach record actually
    # carries a y field in EVERY parent that references it, so the op has something
    # concrete to move and nothing to refuse on.
    graph = insert.object_child_graph(stock)

    def _has_y_everywhere(child):
        parents = [p for p, kids in graph.items() if child in kids]
        recs = [pr for p in parents
                for pr in R.object_records(stock, gs + tab[p])
                if pr.wtype == 5 and pr.child_index == child]
        return bool(recs) and all(R.geom_offset(stock, pr, 1) is not None
                                  for pr in recs)

    child = next(r.child_index for r in R.object_records(stock, gs + tab[home])
                 if r.wtype == 5 and r.child_index is not None
                 and _has_y_everywhere(r.child_index))
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="rm", type="remove_control",
                                  params={"object": child}))
    assert r.ok, r.errors
    assert r.unverified == ["rm"]          # flagged: never proven on hardware
    att = [x for x in R.object_records(r.data, gs + tab[home])
           if x.wtype == 5 and x.child_index == child]
    import struct as S
    for a in att:
        off = R.geom_offset(r.data, a, 1)
        assert off is not None and S.unpack_from("<H", r.data, off)[0] == 900
    # nothing lost on the wire
    assert recipe.capabilities(r.data) == recipe.capabilities(stock)

def test_remove_control_non_wtype5_only_is_no_such_record(stock, stock_hash):
    # Object 1 is attached only via wtype-6/0x2e container-child references (no
    # wtype-5 attach record anywhere), reproducing the review finding: the old
    # NO_SUCH_RECORD guard used insert.object_child_graph directly, which is
    # wtype-agnostic, so it passed on candidate presence alone -- the wtype-5-only
    # edit loop then found nothing, and the op returned a false "removed" success
    # with a byte-identical image. It must now refuse instead.
    from fireflymcf import insert, records as R
    gs, tab = _gs_tab_t(stock)
    graph = insert.object_child_graph(stock)
    parents = [p for p, kids in graph.items() if 1 in kids]
    assert parents, "object 1 must still be graph-reachable for this test to mean anything"
    wtype5_hits = [p for p in parents
                   for rec in R.object_records(stock, gs + tab[p])
                   if rec.wtype == 5 and rec.child_index == 1]
    assert not wtype5_hits, "object 1 unexpectedly has a real wtype-5 attach record"
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="rm", type="remove_control",
                                  params={"object": 1}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_SUCH_RECORD"
    assert r.errors[0]["op_id"] == "rm"

def test_set_gesture_bad_group_is_no_such_group(stock, stock_hash):
    # group=999999 is out of range; gestures.describe_group raises a bare
    # IndexError that must not escape compile_project unstructured.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": -1, "group": 999999,
                                          "kind": "latch"}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_SUCH_GROUP"
    assert r.errors[0]["op_id"] == "g"

def test_set_gesture_target_without_group_is_no_such_group(stock, stock_hash):
    # Object 1 carries no IMAGE_OPCODE (wtype 12) record, so
    # newbutton.object_group raises a bare ValueError; must not escape
    # compile_project unstructured either.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": 1, "kind": "latch"}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_SUCH_GROUP"
    assert r.errors[0]["op_id"] == "g"

def test_set_gesture_latch_on_already_latch_group_is_noop(stock, stock_hash):
    # A stock latch group (found the same way the momentary-group test finds its
    # counterexample) must verify and no-op, not refuse.
    from fireflymcf import gestures
    latch = next(g.group for g in gestures.all_groups(stock) if g.kind == "latch")
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="g", type="set_gesture",
                                  params={"target": -1, "group": latch,
                                          "kind": "latch"}))
    assert r.ok, r.errors
    assert len(r.data) == len(stock)
    assert any("stays a latch" in line for line in r.manifest)


def test_every_op_declares_params_schema():
    from fireflymcf import recipe
    for name, cls in sorted(recipe.REGISTRY.items()):
        schema = getattr(cls, "PARAMS", None)
        assert isinstance(schema, dict), "%s has no PARAMS" % name
        assert isinstance(schema.get("properties"), dict), name
        assert isinstance(schema.get("required"), list), name
        for req in schema["required"]:
            assert req in schema["properties"], (name, req)


# --- add_low_volts_control: the AGS 'Low Volts' arm copied onto the AES ELECTRICAL view ----

def _touch_ids_of(data, obj_index):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    return [data[r.end - 2] | (data[r.end - 1] << 8)
            for r in records.object_records(data, gs + tab[obj_index]) if r.wtype == 12]


def test_add_low_volts_control_builds_and_places(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control", params={}))
    assert r.ok, r.errors
    # One new object appended at the top index; existing indices (829) untouched.
    new_idx = len(guidata.object_tables(r.data)[1]) - 1
    assert new_idx == len(guidata.object_tables(stock)[1])
    # The new object carries the 'Triggers:' title and a wtype-12 region with touch id 129.
    assert any(lbl and lbl.strip() == "Triggers:" for lbl in labels_of(r.data, new_idx))
    assert 129 in _touch_ids_of(r.data, new_idx)


def test_add_low_volts_control_leaves_group_129_untouched(stock, stock_hash):
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control", params={}))
    assert r.ok, r.errors
    # No new group or binding; group 129 is byte-identical to stock (no binding-table edit).
    assert len(guidata.group_table(r.data)) == len(guidata.group_table(stock))
    assert len(guidata.binding_table(r.data)) == len(guidata.binding_table(stock))
    gs_stock = guidata.group_table(stock)[129]
    gs_out = guidata.group_table(r.data)[129]
    assert (gs_out.start, gs_out.count) == (gs_stock.start, gs_stock.count) == (447, 4)
    bs, bo = guidata.binding_table(stock), guidata.binding_table(r.data)
    for j in range(gs_stock.start, gs_stock.start + gs_stock.count):
        assert bo[j].raw == bs[j].raw
        assert bo[j].raw[5] == 0x2F                      # config selector 0x2F, unchanged
    # The capability gate is honest: reusing the config group adds/loses no (channel, command)
    # pair (a compile that gained/lost one would have failed inside compile_project).
    assert recipe.capabilities(r.data) == recipe.capabilities(stock)


def test_add_low_volts_control_touch_covers_button(stock, stock_hash):
    from fireflymcf import layout
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"button_x": 615, "button_y": 62}))
    assert r.ok, r.errors
    new_idx = len(guidata.object_tables(r.data)[1]) - 1
    placed = layout.Screen(r.data).walk(829)
    touch = next(p for p in placed if p.obj_index == new_idx and p.wtype == 12)
    assert (touch.x0, touch.y0, touch.x1, touch.y1) == (615, 62, 720, 112)


def test_add_low_volts_control_default_reuses_shared_child(stock, stock_hash):
    # With no shrink params the button face is the SHARED child 285 via a wtype-5 attach --
    # the new object carries exactly title + attach + touch (3 records, no inline box/label),
    # and the box is drawn by object 285, unchanged at 105x50.
    from fireflymcf import layout, records
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"button_x": 615, "button_y": 62}))
    assert r.ok, r.errors
    new_idx = len(guidata.object_tables(r.data)[1]) - 1
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[new_idx])
    assert [rc.wtype for rc in recs] == [11, 5, 12]           # title, attach, touch (no box)
    placed = layout.Screen(r.data).walk(829)
    box = next(p for p in placed if p.obj_index == 285 and p.wtype == 33)
    assert (box.x1 - box.x0, box.y1 - box.y0) == (105, 50)   # shared child, unchanged


def test_add_low_volts_control_shrink_sizes_box_and_touch(stock, stock_hash):
    # A shrink request authors the box + 'Low Volts' label INLINE on the new object (child 285
    # is byte-shared, so it is never resized) at exactly button_w x button_h; the touch region
    # matches, touch id 129 is preserved, and the button clears the Start block at y=188.
    from fireflymcf import layout, records
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"screen": 829, "title_x": 145, "title_y": 110,
                                          "button_x": 80, "button_y": 136,
                                          "button_w": 90, "button_h": 40, "label_font": 13}))
    assert r.ok, r.errors
    new_idx = len(guidata.object_tables(r.data)[1]) - 1
    assert 129 in _touch_ids_of(r.data, new_idx)             # arm still reachable
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[new_idx])
    # The face is inline: title(11) + box(33) + label(11) + touch(12) all on the new object.
    assert 33 in [rc.wtype for rc in recs]
    assert 285 not in [rc.child_index for rc in recs if rc.child_index is not None]
    placed = layout.Screen(r.data).walk(829)
    box = next(p for p in placed if p.obj_index == new_idx and p.wtype == 33)
    touch = next(p for p in placed if p.obj_index == new_idx and p.wtype == 12)
    label = next(p for p in placed if p.obj_index == new_idx and p.wtype == 11
                 and (p.label or "").startswith("Low"))
    assert (box.x0, box.y0, box.x1, box.y1) == (80, 136, 170, 176)   # exactly 90x40
    assert (touch.x0, touch.y0, touch.x1, touch.y1) == (80, 136, 170, 176)  # hit == face
    assert (label.x0, label.y0, label.x1, label.y1) == (80, 136, 170, 176)  # centred in the box
    assert box.y1 <= 178                                     # >= 10px above the Start block (y=188)


def test_add_low_volts_control_title_renders_on_hardware(stock, stock_hash):
    # BUG-1 regression (2026-08-25): the 'Triggers:' title shipped with base-modes [0,0,0,0]
    # (all absolute). With w/h absolute the decoder read the width/height words as x1/y1, so the
    # box came out inverted (y1 < y0) -- the sim drew it, the COACH drew nothing. The fix authors
    # it with base-modes [0,0,1,1] (x/y absolute, w/h pen-relative), the encoding object 829's own
    # wtype-11 labels and the shrink-path box/label use and which is confirmed on the coach.
    from fireflymcf import layout, records
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"screen": 829, "title_x": 145, "title_y": 110,
                                          "button_x": 80, "button_y": 136,
                                          "button_w": 90, "button_h": 40, "label_font": 13}))
    assert r.ok, r.errors
    new_idx = len(guidata.object_tables(r.data)[1]) - 1
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[new_idx])
    title = next(rc for rc in recs if rc.wtype == 11 and (rc.label or "").strip() == "Triggers:")
    # The exact thing that broke on hardware: base-modes and a non-inverted placed box.
    assert title.base_modes == [0, 0, 1, 1, 0, 0]
    placed = layout.Screen(r.data).walk(829)
    tp = next(p for p in placed if p.obj_index == new_idx and p.wtype == 11
              and (p.label or "").strip() == "Triggers:")
    assert tp.x1 > tp.x0, "title width inverted (x1 <= x0)"
    assert tp.y1 > tp.y0, "title height inverted (y1 <= y0) -- the coach draws nothing"
    assert tp.x1 < 320, "title right edge overlaps the 'Start at' +/- column at x=320"


def test_add_low_volts_control_button_binds_live_redraw_cell(stock, stock_hash):
    # BUG-2 regression (2026-08-25): the button face fills via fillA mode-7 -> colour cell 45, but
    # the shrink path authored the box/label with NO value_slot, so the fill named an unbound cell
    # (black when disabled, no live repaint). The AGS panel's button (obj 315 rec[8], a wtype-5
    # value_slot=45 wrapping child 285) puts that cell in the live-redraw set. The fix binds the
    # SAME cell as box.fillA == box.value_slot == caption.value_slot (OBJECT-HIERARCHY.md 1a).
    from fireflymcf import records
    # Shrink path: inline box + label must both carry value_slot == the box's fillA mode-7 cell.
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"button_x": 80, "button_y": 136,
                                          "button_w": 90, "button_h": 40, "label_font": 13}))
    assert r.ok, r.errors
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[len(tab) - 1])
    box = next(rc for rc in recs if rc.wtype == 33)
    lbl = next(rc for rc in recs if rc.wtype == 11 and (rc.label or "").startswith("Low"))
    fill = next(v for (role, mode, v) in box.colors if role == "fillA" and v is not None)
    assert box.value_slot == fill, "box value_slot must name its own fillA cell"
    assert lbl.value_slot == fill, "caption value_slot must name the same cell (or it vanishes)"
    # Default path: the wtype-5 attach carries the same value_slot (child 285 inherits it).
    r2 = compile_ops(stock, stock_hash,
                     recipe.OpSpec(id="lv", type="add_low_volts_control", params={}))
    assert r2.ok, r2.errors
    gs2, _ = guidata.guidata_extent(r2.data)
    tab2 = guidata.object_tables(r2.data)[1]
    recs2 = records.object_records(r2.data, gs2 + tab2[len(tab2) - 1])
    attach = next(rc for rc in recs2 if rc.wtype == 5 and rc.child_index == 285)
    assert attach.value_slot == fill == 45


def test_record_geometry_gate_catches_inverted_box(stock, stock_hash):
    # The general gate behind BUG-1: relocate._verify_record_geometry rejects a record whose
    # geometry encodes a negative-extent box (an absolute extent coordinate that precedes its
    # absolute position). It is load-bearing only if it actually fires on the bad byte, so build
    # a good control and revert its title record's base-mode byte to the pre-fix 0x00 (all
    # absolute) -- which makes the width word read as an absolute x1 that precedes x.
    from fireflymcf import relocate
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="lv", type="add_low_volts_control",
                                  params={"screen": 829, "title_x": 145, "title_y": 110,
                                          "button_x": 80, "button_y": 136,
                                          "button_w": 90, "button_h": 40, "label_font": 13}))
    assert r.ok, r.errors
    ok, _ = relocate._verify_record_geometry(r.data)
    assert ok, "the real fixed build must pass the geometry gate"
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[len(tab) - 1])
    title = next(rc for rc in recs if rc.wtype == 11 and (rc.label or "").strip() == "Triggers:")
    tampered = bytearray(r.data)
    assert tampered[title.offset + 1] == 0x50            # base-modes [0,0,1,1] (the fix)
    tampered[title.offset + 1] = 0x00                    # revert to the pre-fix [0,0,0,0] bug
    ok, reason = relocate._verify_record_geometry(bytes(tampered))
    assert not ok and "negative-width" in reason, reason


def test_follow_channel_op_produces_a_sealed_gated_follower(stock, stock_hash):
    from fireflymcf import container, options, theme, thumb

    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="follow", type="follow_channel",
                                  params={"watch_channel": 25, "target_channel": 44,
                                          "on_level": 200, "gate_option": "Heat Pump",
                                          "gate_label": "Pump Link"}))
    assert r.ok, r.errors
    out = r.data

    # 1. GuiData changed ONLY in the 9-byte row label; the CLIMATE arms are intact.
    gs, ge = guidata.guidata_extent(out)
    tab = guidata.object_tables(out)[1]
    rows = dict(options._iter_option_rows(out, gs, ge, tab, 46))
    assert rows[107].strip() == "Pump Link"

    # The rename must be object-46-SCOPED: stock has 7 "Heat Pump" label sites (the
    # gate row plus the CLIMATE mode-column's own "Heat Pump" captions on six other
    # objects: 70/71/360/361/798/799); only the gate row's should be touched, leaving exactly 6. A
    # regression that widened the rename filter (e.g. dropped the object-span check)
    # would still pass the row-107 assertion above but would shrink this count.
    remaining = patch.find_label_sites(out, "Heat Pump")
    assert len(remaining) == 6, [s.text for s in remaining]

    # 2. File grew by the blob length; downstream sections shifted; all checksums seal.
    assert len(out) > len(stock)
    resealed, changed = patch.reseal(out)
    assert changed == []          # already sealed

    # 3. The detour is in place at GuiCode file offset 0xEE3C, retargeted away from the
    # stock `bl 0x00247BF8` and decoding to a `bl` reaching the routine entry
    # (0x00259BB4 + 0x18 == 0x00259BCC).
    gc = container.parse_container(out)
    gcs = next(s for s in gc.sections if "GuiCode" in s.name)
    detour = out[gcs.offset + 0xEE3C: gcs.offset + 0xEE3C + 4]
    assert detour != bytes.fromhex("f8f7dcfe")
    hook_runtime = theme.GUICODE_BASE_DEFAULT + 0xEE3C
    text = thumb.disassemble(detour, hook_runtime)[0][1]
    assert text == "bl #0x259bcc", text

    # 4. No capability delta: the follower is GuiCode code, and touches no G12
    # group/binding table.
    assert recipe.capabilities(out) == recipe.capabilities(stock)


def test_experiment_build_uses_new_pump_link_option_row(stock, stock_hash):
    """B3.2: the shipped Experimental build's `pumplink` op is `add_option_follower` --
    a dedicated new 'Pump Link' OPTIONS row -- not `follow_channel`'s Heat Pump
    repurpose. Supersedes the old byte-diff blast-radius test for `follow_channel`
    (which asserted the Heat Pump label field was the ONE legitimate rewrite site);
    that op is no longer part of this build, so that specific proof no longer applies,
    and `add_option_follower`'s own from-stock blast radius is already exhaustively
    proven at the op level by
    `test_add_option_follower_composes_toggle_row_and_gated_follower`.

    What only a FULL-build compile can show -- and what this test adds -- is that
    Heat Pump stays untouched, and every other declared feature still verifies, in
    the presence of the other ~21 experiment ops. `recipe._finish` runs every op's
    `verify()` against the SAME final compiled image, so `result.ok` alone already
    proves each feature structurally; the substring checks below just name that proof
    per-feature (CLAUDE.md build-hygiene: assert every feature, never inherit one)."""
    from pathlib import Path
    from fireflymcf import config, layout

    doc = json.loads(Path("builds/golden-v22-anchor.ffproj.json").read_text())
    # ENABLED ops only: the manifest carries one entry per compiled op, and the project may hold
    # disabled ones (`minutes-home` since 2026-09-06 -- kept so the v17/v18/v19 pins can restore
    # it, see tests/test_registers.py).
    op_ids = [o["id"] for o in doc["ops"] if o.get("enabled", True)]
    assert "pumplink" in op_ids, "pumplink op missing from builds/golden-v22-anchor.ffproj.json"
    # add_option_follower reuses a FIXED command-group slot (optionrow.REUSE_GROUP_SLOT
    # == 164); "left"/"wire" allocate their own groups dynamically (lowest free id) and
    # would otherwise claim 164 first, refusing pumplink with TOGGLE_REFUSED. So pumplink
    # must run before both.
    assert op_ids.index("pumplink") < op_ids.index("left"), \
        "pumplink must run before 'left' -- it needs group slot 164 still free"
    assert op_ids.index("pumplink") < op_ids.index("wire"), \
        "pumplink must run before 'wire' -- it needs group slot 164 still free"
    pumplink_spec = next(o for o in doc["ops"] if o["id"] == "pumplink")
    assert pumplink_spec["type"] == "add_option_follower", (
        "pumplink must use add_option_follower (a new OPTIONS row), not follow_channel "
        "(which repurposes the Heat Pump row) -- got %r" % pumplink_spec["type"])
    assert "gate_option" not in pumplink_spec["params"] \
        and "gate_label" not in pumplink_spec["params"], (
        "gate_option/gate_label are follow_channel params; add_option_follower has no "
        "such params -- their presence means the JSON was not fully rewired")

    project = recipe.Project.from_json(json.dumps(doc))
    assert project.base_sha256 == stock_hash, \
        "the project file is pinned to a different base image"
    result = recipe.compile_project(project, stock)
    assert result.ok, result.errors
    out = result.data

    assert len(result.manifest) == len(op_ids) and all(result.manifest), \
        "every op must verify something on the final image"
    manifest_by_id = dict(zip(op_ids, result.manifest))

    # --- Heat Pump is stock: nothing in this build renames it or reads its selector as
    # a gate any more -- still 0xEB, still "Heat Pump", all 7 stock label sites intact
    # (the old follow_channel rename would have shrunk this to 6).
    assert config.option_selector(out, 0x0A) == 0xEB
    assert len(patch.find_label_sites(out, "Heat Pump")) == 7

    # --- the new 'Pump Link' row exists, gates on 0xEE, and drives watch=25 -> target=44.
    # AddOptionFollower.verify() already proved the row/group/detour/same-cell facts
    # structurally against `out`; this pins the same facts by name in the manifest so a
    # future param change that silently dropped the gate/selector fails here too.
    pl = manifest_by_id["pumplink"]
    assert "'Pump Link'" in pl, pl
    assert "0xee" in pl.lower(), pl
    assert "watch=25 -> target=44" in pl, pl

    # Rendered on the OPTIONS page too (not just registered structurally). pumplink runs
    # right after "tank" (the only earlier op, and it inserts no objects), so its new
    # object is the first index past stock's object table -- not "the last object table
    # entry", which would now belong to a later op (e.g. "left"/"shell").
    new_idx = len(guidata.object_tables(stock)[1])
    placed = layout.Screen(out).walk(46)
    assert any(p.obj_index == new_idx and p.wtype == 11
               and (p.label or "").strip() == "Pump Link" for p in placed), \
        "Pump Link row did not render on the OPTIONS page"

    # --- every other experiment feature, asserted present by name -------------------
    assert "tank-heater" in manifest_by_id["tank"]
    assert "Overflow" in manifest_by_id["left"] and "pin 28" in manifest_by_id["left"]
    assert "28 - Overflow Valve" in manifest_by_id["row28"]
    assert "29 - Starlink" in manifest_by_id["row29"]
    assert "Renogy 48v Inverter" in manifest_by_id["optname"]
    assert "option interlocks removed" in manifest_by_id["uncouple"]
    assert manifest_by_id["pin115"] and manifest_by_id["pin260"] and manifest_by_id["pin261"]
    assert manifest_by_id["invhold"]
    assert manifest_by_id["hold"] and manifest_by_id["hold-left"]
    assert manifest_by_id["logo"] and manifest_by_id["shell"] and manifest_by_id["wire"]

    # No unexpected capability change: `_finish`'s capability gate already enforces
    # every op's declared delta matches the actual one; pumplink declares (set(), set()).
    assert recipe.capabilities(out) != recipe.capabilities(stock), \
        "the experiment build is expected to gain/lose some capabilities from its " \
        "other features (e.g. pin 28/29 buttons) -- if this ever matches stock exactly " \
        "something upstream silently stopped wiring a control"


def test_add_option_follower_composes_toggle_row_and_gated_follower(stock, stock_hash):
    """B3.1: one op that builds the 0xEE toggle group, appends a 'Pump Link' OPTIONS
    checkbox row, and installs a follower routine gated on that row -- with Heat Pump
    left untouched and no capability change."""
    from fireflymcf import (container, followchannel, layout, optionrow,
                                  statusbind, theme, thumb, config)

    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pl", type="add_option_follower",
                                  params={"watch_channel": 25, "target_channel": 44}))
    assert r.ok, r.errors
    out = r.data
    new_idx = len(guidata.object_tables(out)[1]) - 1

    # (a) The 0xEE toggle group exists: reused slot 164 is repointed at four fresh
    # config-toggle bindings that all write selector 0xEE (byte[5]) and carry the
    # 0xFED9 config DGN (channel None -- a config write, not a G12 command).
    gt, bt = guidata.group_table(out), guidata.binding_table(out)
    assert len(bt) == len(guidata.binding_table(stock)) + 4
    g = gt[164]
    sel = [bt[j].raw[5] for j in range(g.start, g.start + g.count)]
    dgn = [bt[j].dgn_low for j in range(g.start, g.start + g.count)]
    chan = [bt[j].channel for j in range(g.start, g.start + g.count)]
    assert g.count == 4 and set(sel) == {0xEE} and set(dgn) == {0xFED9} and set(chan) == {None}, \
        (g, [hex(s) for s in sel], [hex(d) for d in dgn], chan)

    # (b) Object 46 gained the row -- the appended object renders a 'Pump Link' caption
    # placed on the OPTIONS page (walking the parent screen reaches the attached child).
    gs0, _ = guidata.guidata_extent(out)
    tab0 = guidata.object_tables(out)[1]
    recs = records.object_records(out, gs0 + tab0[new_idx])
    assert [rc.wtype for rc in recs] == [6, 11, 12]        # switch, label, touch
    placed = layout.Screen(out).walk(46)
    pl = next((p for p in placed if p.obj_index == new_idx and p.wtype == 11
               and (p.label or "").strip() == "Pump Link"), None)
    assert pl is not None, "Pump Link row did not render on the OPTIONS page"
    # The touch region carries the toggle's touch id (== group index 164).
    assert 164 in _touch_ids_of(out, new_idx)

    # (c) The follower routine is appended and the stock hook detoured.
    assert len(out) > len(stock)
    gcs = next(s for s in container.parse_container(out).sections if "GuiCode" in s.name)
    detour = out[gcs.offset + followchannel.HOOK_OFF: gcs.offset + followchannel.HOOK_OFF + 4]
    assert detour != followchannel.HOOK_STOCK, "stock bl still present -- detour not applied"
    text = thumb.disassemble(detour, theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF)[0][1]
    assert text == "bl #0x259bcc", text        # 0x259bb4 + ENTRY_OFF(0x18)

    # (d) Capability set byte-identical to stock -- no (channel, command) pair added.
    assert recipe.capabilities(out) == recipe.capabilities(stock)

    # (e) Heat Pump's row untouched: still selector 0xEB, caption present, all 7 stock
    # label sites intact (a rename would have shrunk this to 6).
    assert config.option_selector(out, 0x0A) == 0xEB
    assert len(patch.find_label_sites(out, "Heat Pump")) == 7

    # Same-cell property: the cell the checkbox reads (its switch value_slot) is the
    # cell the follower routine writes (plan.display_addr). Re-derive the plan against
    # the toggle-grown image (what the op resolved on) and cross-check the compiled
    # switch + cell table.
    tog = optionrow.build_config_toggle(stock, 0xEE)
    plan = followchannel.resolve(tog.grown, 25, 44, 200, gate_slot_selector=0xEE,
                                 variant_group=tog.new_group_index, alloc_display=True)
    switch = next(rc for rc in recs if rc.wtype == 6)
    assert switch.value_slot == plan.display_slot, \
        "checkbox reads cell %s, routine writes cell %s" % (switch.value_slot, plan.display_slot)
    gs, _ = guidata.guidata_extent(out)
    cell_addr = struct.unpack_from("<I", out, gs + statusbind.CELL_TABLE_OFF
                                   + switch.value_slot * 4)[0]
    assert cell_addr == plan.display_addr, \
        "cell %d -> %#x, routine writes %#x" % (switch.value_slot, cell_addr, plan.display_addr)


def test_add_option_follower_masked_gate_targets_the_flag_byte(stock, stock_hash):
    """Task 13: `0xEE` now carries two fields (auto-off minutes low byte, Pump Link flag high
    byte), so the follower's gate must test `(slot & 0xFF00) == 0x0100`, not the whole halfword
    `== 1` -- which the widened word can never be. The op expresses that with `gate_mask` +
    `gate_on_value`, and the DEFAULT (no params) must stay byte-identical: `tests/test_registers.py`
    pins builds that are on a physical vehicle."""
    from fireflymcf import container, thumb

    base = dict(watch_channel=25, target_channel=44)
    plain = compile_ops(stock, stock_hash,
                        recipe.OpSpec(id="pl", type="add_option_follower", params=dict(base)))
    assert plain.ok, plain.errors

    # (a) Passing the defaults EXPLICITLY changes nothing -- the mask is opt-in.
    explicit = compile_ops(stock, stock_hash,
                           recipe.OpSpec(id="pl", type="add_option_follower",
                                         params=dict(base, gate_mask=0xFFFF, gate_on_value=1)))
    assert explicit.ok, explicit.errors
    assert explicit.data == plain.data, "the unmasked default is no longer byte-identical"

    # (b) The masked build differs, and its GuiCode carries the masked comparison.
    masked = compile_ops(stock, stock_hash,
                         recipe.OpSpec(id="pl", type="add_option_follower",
                                       params=dict(base, gate_mask=0xFF00, gate_on_value=0x0100)))
    assert masked.ok, masked.errors
    assert masked.data != plain.data

    def guicode(img):
        s = next(x for x in container.parse_container(img).sections if "GuiCode" in x.name)
        return img[s.offset:s.offset + s.length]

    seq = thumb.movw(1, 0xFF00) + thumb.ands_reg(0, 1)      # movw r1,#0xff00 ; ands r0,r1
    val = thumb.movw(1, 0x0100) + thumb.cmp_reg(0, 1)       # movw r1,#0x100  ; cmp r0,r1
    assert seq in guicode(masked.data) and val in guicode(masked.data)
    assert seq not in guicode(plain.data) and val not in guicode(plain.data)


def test_add_option_follower_refuses_an_unsatisfiable_mask(stock, stock_hash):
    """A value outside its own mask can never hold; the op must refuse rather than author a
    permanently-dead gate (which is exactly the defect Task 13 fixes)."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pl", type="add_option_follower",
                                  params={"watch_channel": 25, "target_channel": 44,
                                          "gate_mask": 0xFF00, "gate_on_value": 1}))
    assert not r.ok
    assert any("FOLLOW_REFUSED" in str(e) for e in r.errors), r.errors


def test_resolve_register_op_sets_handle(stock, stock_hash):
    """`resolve_register` is a pure resolution: no bytes change, and its handle carries
    the resolved slot/var so a later op can reference it by `{"ref": id}`. Values pinned
    against `register.resolve_register` directly: DC_DIMMER_STATUS_3 instance 25 is var
    67 at the index-split low-array slot 0x2000804E."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="light25", type="resolve_register",
                                  params={"dgn": "DC_DIMMER_STATUS_3", "instance": 25}))
    assert r.ok, r.errors
    assert r.data == stock          # pure resolution: no bytes change
    manifest = r.manifest[0]
    assert "var 67" in manifest and "0x2000804e" in manifest.lower(), manifest


def test_follow_register_reproduces_a_follower_detour(stock, stock_hash):
    """The generic register-follower composes two resolve_register refs (a watch and a
    gate) into the same detour add_option_follower's step 4 installs, at the pinned
    13.6 append point."""
    from fireflymcf import container, followchannel, theme, thumb

    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="light25", type="resolve_register",
                                  params={"dgn": "DC_DIMMER_STATUS_3", "instance": 25}),
                    recipe.OpSpec(id="pumpsel", type="resolve_register",
                                  params={"dgn": "DGN_CONFIG", "selector": 0xEE}),
                    recipe.OpSpec(id="link", type="follow_register",
                                  params={"watch": {"ref": "light25", "mask": 0x1C, "value": 0x04},
                                          "target_channel": 44, "on_level": 200,
                                          "gates": [{"ref": "pumpsel", "value": 1}]}))
    assert r.ok, r.errors
    out = r.data
    gcs = next(s for s in container.parse_container(out).sections if "GuiCode" in s.name)
    detour = out[gcs.offset + followchannel.HOOK_OFF: gcs.offset + followchannel.HOOK_OFF + 4]
    assert detour != followchannel.HOOK_STOCK, "stock bl still present -- detour not applied"
    text = thumb.disassemble(detour, theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF)[0][1]
    assert text == "bl #0x259bcc", text        # 0x259bb4 + ENTRY_OFF(0x18)
    assert recipe.capabilities(out) == recipe.capabilities(stock)


def test_resolve_register_op_missing_subscription_errors(stock, stock_hash):
    """No existing variable-table entry subscribes DGN_CONFIG selector 0x7F -- refuse,
    never fabricate a new one (that is a future, hardware-unverified append-new path)."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="nope", type="resolve_register",
                                  params={"dgn": "DGN_CONFIG", "selector": 0x7F}))
    assert not r.ok
    assert r.errors[0]["code"] == "NO_SUCH_REGISTER", r.errors


def test_follow_register_unknown_ref_is_a_structured_error_not_a_crash(stock, stock_hash):
    """A typo'd/nonexistent `{"ref": ...}` must refuse with BAD_REF -- not raise a bare
    KeyError out of `_condition` (which `compile_project`'s apply loop only catches as
    RecipeError, so an unguarded lookup would break out of compile_project entirely,
    the refuse-rather-than-crash discipline `recipe.resolve_target`'s own BAD_REF
    already upholds for `{"ref": op_id}` targets)."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="link", type="follow_register",
                                  params={"watch": {"ref": "no-such-op"},
                                          "target_channel": 44}))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_REF", r.errors


def test_follow_register_inline_condition_without_dgn_refuses(stock, stock_hash):
    """FINDING 2: a malformed inline condition with neither `ref` nor `dgn` (e.g.
    {"mask":5,"value":1}) must refuse with a structured BAD_PARAMS -- not raise a bare
    KeyError out of `_condition`, which is not in the (RegisterError, FollowChannelError)
    tuple `apply` catches and so would escape `compile_project` unstructured. Same
    refuse-don't-corrupt gap the BAD_REF guard closed for the ref path."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="link", type="follow_register",
                                  params={"watch": {"mask": 5, "value": 1},
                                          "target_channel": 44}))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_PARAMS", r.errors


def test_follow_register_inline_dgn_with_no_subscription_refuses(stock, stock_hash):
    """Fix round 1, finding 5 (Minor): `_condition`'s re-wrap of `_resolve_register_spec`'s
    `NO_SUCH_REGISTER` back into `FOLLOW_REFUSED` (recipe_ops.py, preserving follow_register's
    pre-extraction error-code contract) was unreached by any test. An inline `{dgn, selector}`
    condition that resolves structurally (exactly one of ref/dgn given) but names no existing
    variable-table subscription must surface as FOLLOW_REFUSED, not the newer NO_SUCH_REGISTER
    `resolve_register`/`add_register_readout` use for the same underlying failure."""
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="link", type="follow_register",
                                  params={"watch": {"dgn": "DGN_CONFIG", "selector": 0x7F},
                                          "target_channel": 44}))
    assert not r.ok
    assert r.errors[0]["code"] == "FOLLOW_REFUSED", r.errors


# ======================================================================================
# Task 9: add_pump_auto_off -- composes Tasks 1-8 into the whole Water Pump feature, and
# the pump_section ref add_pump_auto_off publishes for swap_aes_electrical to consume.
# ======================================================================================

def _pump_pre_append_image(stock):
    """Replay `add_pump_auto_off`'s steps 1-3 (widen, toggle-to-delta, stepper) standalone,
    to independently recompute what `followchannel.scan_display_cell` must return on the
    same pre-append image the op derives its plan from -- without reaching into ctx.handles,
    which CompileResult does not expose."""
    from fireflymcf import optionrow, statusbind
    out, _ = statusbind.set_extract_mode(stock, 289, 2)
    tog = optionrow.build_config_toggle(out, 0xEE)
    out, _ = optionrow.toggle_to_flag_delta(tog.grown, tog.new_group_index, 0xEE, shift=8)
    stepper = optionrow.build_config_stepper(out, 0xEE, 5)
    return stepper.grown


def test_add_pump_auto_off_composes_and_is_hardware_verified(stock, stock_hash):
    from fireflymcf import guidata, recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    assert r.data != stock
    # +4 (optionrow.build_config_toggle's donor clone at group 164) + 6 (build_config_stepper's
    # two 3-binding arrow groups) = +10. The brief's own literal test pins +6 -- that omits the
    # toggle's own growth; DONOR_COUNT (optionrow.py) is 4 and ARROW_DONOR_COUNT*2 is 6, both
    # verified directly against firefly-13.6/mcf.bin this session, so +10 is what this op's
    # apply order (which must create the 0xee toggle from scratch to run standalone on stock)
    # actually produces.
    assert len(guidata.binding_table(r.data)) == len(guidata.binding_table(stock)) + 10
    # hardware_verified = True again as of v17 (the reference coach's build log (not published) "Experimental v16" ->
    # "### v16 FLASHED AND VERIFIED (as part of v17), 2026-09-04"): v16's minute-0 clamp fix
    # (checklist item 8, "Only goes 5-60") ran on the coach as part of the flashed v17 image.
    assert r.unverified == []


def test_add_pump_auto_off_declares_no_capability_delta(stock, stock_hash):
    from fireflymcf import recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    assert recipe.capabilities(r.data) == recipe.capabilities(stock)


def test_add_pump_auto_off_widens_the_0xEE_subscription(stock, stock_hash):
    from fireflymcf import guidata, recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    after = {v.index: v for v in guidata.variable_table(r.data)}[289]
    assert after.raw[2] >> 4 == 2, "var 289 must read the whole u16, not just the flag"


def test_add_pump_auto_off_does_not_retype_the_readout_slot(stock, stock_hash):
    """Spare cell 215 (whatever `scan_display_cell` actually picks on the pre-append image)
    must stay variable type 1 -- a prior spike proved type 1 renders this single byte exactly
    like type 3, and retyping it is pure hazard for no benefit. `statusbind.set_variable_type`
    / `VARIABLE_TYPE_U16` must never be called by this op."""
    from fireflymcf import followchannel, guidata, recipe
    pre = _pump_pre_append_image(stock)
    idx, _addr = followchannel.scan_display_cell(pre)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    _dptr, type_off = guidata.find_variable_tables(r.data)
    gs, _ = guidata.guidata_extent(r.data)
    assert r.data[gs + type_off + idx] == 1, \
        "readout slot %d was retyped away from its stock type 1" % idx


def test_add_pump_auto_off_link_toggle_carries_no_absolute_write(stock, stock_hash):
    """The toggle this op ships must never erase the minute byte: its two D1 absolute
    writes must already be converted to +/-0x100 D2 deltas in the compiled image."""
    from fireflymcf import guidata, optionrow, recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    gt = guidata.group_table(r.data)
    bt = guidata.binding_table(r.data)
    g = gt[164]
    ents = [bt[j] for j in range(g.start, g.start + g.count)]
    assert not any(e.raw[optionrow.FUNCTION_FIELD] == optionrow.ABSOLUTE_FUNCTION for e in ents)
    deltas = {struct.unpack_from(">h", e.raw, optionrow.VALUE_FIELD)[0]
             for e in ents if e.raw[optionrow.FUNCTION_FIELD] == optionrow.DELTA_FUNCTION}
    assert deltas == {0x0100, -0x0100}


def test_swap_aes_electrical_reads_the_pump_section_ref_when_ordered_first(stock, stock_hash):
    """The path Task 9 designs for: add_pump_auto_off runs first and publishes a handle,
    swap_aes_electrical reads it via {"ref": "pumpauto"} and wires the Water Pump section
    onto the compact frame -- both hardware-verified as of v17 (the reference coach's build log (not published) "Experimental
    v16" -> "### v16 FLASHED AND VERIFIED (as part of v17), 2026-09-04")."""
    from fireflymcf import records, guidata, recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}),
                    recipe.OpSpec(id="aes", type="swap_aes_electrical",
                                  params={"pump_section": {"ref": "pumpauto"}}))
    assert r.ok, r.errors
    # Both "aes" (the pump-section instance of aesswap) and "pumpauto" ran on the coach as part
    # of v17 (the reference coach's build log (not published) "Experimental v16"/"Experimental v17" FLASHED AND VERIFIED
    # sections), so neither reports unverified any more.
    assert set(r.unverified) == set()
    from fireflymcf import aescompact
    gs, _ = guidata.guidata_extent(r.data)
    tab = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
    assert len(recs) == 7 + 15


def test_swap_aes_electrical_refuses_the_pump_section_ref_when_ordered_second(stock, stock_hash):
    """Constraint 3: swap_aes_electrical reads add_pump_auto_off's handle for the section's
    touch ids and readout slot, so it must run AFTER that op. If a project gets the order
    backwards, compile must refuse cleanly (BAD_REF) rather than silently wire the section to
    ids that were never computed."""
    from fireflymcf import recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="aes", type="swap_aes_electrical",
                                  params={"pump_section": {"ref": "pumpauto"}}),
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_REF", r.errors


# ======================================================================================
# Task 5 (v16): swap_aes_electrical's link_indicator param -- wires the Pump Link switch to
# add_option_follower's 0/1 display cell instead of the pre-v16 box reading the (AES-frozen)
# Low Volts cell 45.
# ======================================================================================

def _pumplink_display_slot(stock):
    """Replay `_pump_feature_ops()`'s "pumplink" spec standalone (recipe internals, not
    `compile_project`) to independently recompute what `add_option_follower` allocates as its
    display cell on stock -- without reaching into ctx.handles, which CompileResult does not
    expose. Same pattern `_pump_pre_append_image` uses for `add_pump_auto_off`'s readout slot."""
    from fireflymcf import recipe
    spec = next(o for o in _pump_feature_ops() if o.id == "pumplink")
    built, errs = recipe._build_ops([spec])
    assert not errs, errs
    ctx = recipe.Ctx(stock=stock, edits=[], insertions=[], handles={})
    built[0].apply(stock, ctx)
    return ctx.handles["pumplink"]["display_slot"]


def test_swap_with_link_indicator_wires_the_pump_link_switch_to_the_bridge_cell(stock, stock_hash):
    import struct
    from fireflymcf import aescompact, guidata, recipe, records
    ops = _pump_feature_ops()            # the helper the composed pump tests use; if it does not
                                         # exist, inline the same (pumplink, pumpauto, aes) specs
    aes = next(o for o in ops if o.type == "swap_aes_electrical")
    aes.params["link_indicator"] = {"ref": "pumplink"}
    r = compile_ops(stock, stock_hash, *ops)
    assert r.ok, r.errors
    display_slot = _pumplink_display_slot(stock)
    gs, _ = guidata.guidata_extent(r.data)
    tbl = guidata.object_tables(r.data)[1]
    recs = records.object_records(r.data, gs + tbl[aescompact.ELECTRICAL_AES_BODY])
    sw = [x for x in recs if x.wtype == 6 and x.count == 2 and x.value_slot == display_slot]
    assert len(sw) == 1, "exactly one Pump Link switch on the bridge's display cell"
    base = sw[0].child_index
    mt = guidata.find_match_table(r.data)
    assert [struct.unpack_from("<H", r.data, gs + mt + i * 2)[0] for i in (base, base + 1)] == [0, 1]
    arms = [records.object_records(r.data, gs + tbl[i]) for i in (base, base + 1)]
    assert [a[0].colors[0][2] for a in arms] == [10, 35]
    assert all(a[1].label == "Pump Link" for a in arms)
    # nothing on the page reads cell 45 for the Pump Link any more
    assert not any(x.wtype == 33 and x.colors and x.colors[0] == ("fillA", 7, 45)
                   for x in recs[7:]), "the v15 box that read the Low Volts cell is gone"
    # v16's Pump Link switch (this test's own subject) was itself hardware-verified as part of
    # v17 (the reference coach's build log (not published) "Experimental v16" -> "### v16 FLASHED AND VERIFIED (as part of
    # v17), 2026-09-04", checklist items 5-7), so "aes" no longer reports unverified.
    assert "aes" not in r.unverified


def test_swap_link_indicator_refuses_a_ref_without_a_display_slot(stock, stock_hash):
    from fireflymcf import recipe
    ops = _pump_feature_ops()
    aes = next(o for o in ops if o.type == "swap_aes_electrical")
    aes.params["link_indicator"] = {"ref": "pumpauto"}      # a handle, but not the bridge's
    r = compile_ops(stock, stock_hash, *ops)
    assert not r.ok and r.errors[0]["code"] == "BAD_REF"


# ======================================================================================
# Fix round 1, Task 9: adopt-or-create the 0xEE config-toggle group (finding C1) instead of
# always minting a fresh one -- optionrow.build_config_toggle refuses outright when a config-
# toggle group already exists on the selector, which is exactly what a real composed project
# hits (add_option_follower/"pumplink" already builds one at group 164 for its own gated
# OPTIONS row before add_pump_auto_off ever runs).
# ======================================================================================

_PUMPLINK_PARAMS = {"watch_channel": 25, "target_channel": 44}    # matches builds/experiment
                                                                  # .ffproj.json's "pumplink" op


def test_add_pump_auto_off_creates_the_toggle_when_run_alone_on_stock(stock, stock_hash):
    """C3 (create-path counterpart): with nothing else on the image, group 164 does not yet
    carry a 0xEE config-toggle, so the op must take the CREATE branch -- named in its own
    manifest summary so a compile-inspecting tool never has to reverse-engineer it from
    binding counts."""
    from fireflymcf import recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert r.ok, r.errors
    assert "CREATED" in r.manifest[0] and "ADOPTED" not in r.manifest[0], r.manifest[0]


def test_add_pump_auto_off_refuses_before_the_fix_would_have_failed_to_compose(stock,
                                                                                stock_hash):
    """C1, reproduced directly: `add_option_follower` ("pumplink", the real project's own op)
    already builds a 0xEE config-toggle group at 164 before `add_pump_auto_off` would run.
    Before the fix, `add_pump_auto_off` unconditionally called `optionrow.build_config_toggle`
    a second time on the same selector, which refuses ("selector 0xee already has a
    config-toggle group") -- so the two ops could never compose. This test's twin below
    (`test_add_pump_auto_off_adopts_an_existing_toggle_...`) proves the fix; this one pins
    that the failure mode C1 named is real by exercising `optionrow.build_config_toggle`
    directly against the image `add_option_follower` produces."""
    from fireflymcf import optionrow, recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumplink", type="add_option_follower",
                                  params=_PUMPLINK_PARAMS))
    assert r.ok, r.errors
    with pytest.raises(optionrow.OptionRowError, match="already has a config-toggle group"):
        optionrow.build_config_toggle(r.data, 0xEE)


def test_add_pump_auto_off_adopts_an_existing_toggle_when_composed_after_add_option_follower(
        stock, stock_hash):
    """C3: the path Task 9 must actually support. `add_option_follower` builds the 0xEE
    toggle at group 164 (its own +4 growth); `add_pump_auto_off` composed right after it must
    ADOPT that group -- no second toggle, no further growth from the toggle step, and the
    handle's link_group is still 164 (checked here via the group the compiled image's own
    delta bindings live at, since CompileResult exposes no handles)."""
    from fireflymcf import guidata, optionrow, recipe

    follower_alone = compile_ops(stock, stock_hash,
                                 recipe.OpSpec(id="pumplink", type="add_option_follower",
                                               params=_PUMPLINK_PARAMS))
    assert follower_alone.ok, follower_alone.errors

    composed = compile_ops(stock, stock_hash,
                           recipe.OpSpec(id="pumplink", type="add_option_follower",
                                         params=_PUMPLINK_PARAMS),
                           recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))
    assert composed.ok, composed.errors

    # toggle_created == False, named in the manifest (the id order is ["pumplink", "pumpauto"]).
    pumpauto_summary = composed.manifest[1]
    assert "ADOPTED" in pumpauto_summary and "CREATED" not in pumpauto_summary, pumpauto_summary
    assert "group 164" in pumpauto_summary

    # No growth from the toggle step: composed adds exactly the stepper's 6 bindings over
    # "pumplink alone" (not 4 more for a second toggle) -- the delta ISOLATES this op's own
    # contribution regardless of what add_option_follower already grew.
    grown = len(guidata.binding_table(composed.data)) - len(guidata.binding_table(follower_alone.data))
    assert grown == optionrow.ARROW_DONOR_COUNT * 2

    # The handle's link_group is 164: group 164's bindings are the ones now carrying the
    # delta-converted (not absolute) 0xee writes.
    gt = guidata.group_table(composed.data)
    bt = guidata.binding_table(composed.data)
    g = gt[164]
    ents = [bt[j] for j in range(g.start, g.start + g.count)]
    assert {e.raw[optionrow.SELECTOR_FIELD] for e in ents} == {0xEE}
    assert not any(e.raw[optionrow.FUNCTION_FIELD] == optionrow.ABSOLUTE_FUNCTION for e in ents)
    deltas = {struct.unpack_from(">h", e.raw, optionrow.VALUE_FIELD)[0]
             for e in ents if e.raw[optionrow.FUNCTION_FIELD] == optionrow.DELTA_FUNCTION}
    assert deltas == {0x0100, -0x0100}

    # pumplink is hardware-verified (v9 on the coach); pumpauto is verified again as of v17
    # (the reference coach's build log (not published) "Experimental v16" -> "### v16 FLASHED AND VERIFIED (as part of v17),
    # 2026-09-04") -- the clamp fix ran on the coach.
    assert composed.unverified == []


def test_add_pump_auto_off_composed_with_an_unmasked_follower_leaves_a_dead_gate(stock,
                                                                                  stock_hash):
    """Task 13 fix round 1, finding 3: pin the KNOWN-BAD composition rather than leaving it as
    prose in `add_pump_auto_off`'s docstring.

    `add_pump_auto_off` widens var 289's extraction to mode 2, so the 0xEE slot stops being the
    Pump Link flag alone and becomes `minutes | flag << 8`. A DEFAULT (unmasked)
    `add_option_follower` gates on that whole halfword `== 1`, and no word the widened slot can
    hold is ever 1 -- so the follower is dead and the button never highlights, with the build
    still green. The masked configuration is the fix, and it is opt-in, so this composition must
    stay observably broken until a recipe asks for the mask."""
    from fireflymcf import container, recipe, register, thumb

    def guicode(img):
        sec = next(x for x in container.parse_container(img).sections if "GuiCode" in x.name)
        return img[sec.offset:sec.offset + sec.length]

    masked_seq = thumb.movw(1, 0xFF00) + thumb.ands_reg(0, 1)     # the masked flag-byte test
    ops = lambda params: (recipe.OpSpec(id="pumplink", type="add_option_follower", params=params),
                          recipe.OpSpec(id="pumpauto", type="add_pump_auto_off", params={}))

    # (a) DEFAULT follower + the widening op: compiles green, and the gate is dead.
    dead = compile_ops(stock, stock_hash, *ops(dict(_PUMPLINK_PARAMS)))
    assert dead.ok, dead.errors
    reg = register.resolve_register(dead.data, dgn="DGN_CONFIG", selector=0xEE)
    assert reg.extract_mode == 2, "the slot is not widened -- this test no longer pins the hazard"
    assert masked_seq not in guicode(dead.data), "the default follower must NOT be masked"

    # The deadness itself, argued over the values the widened slot can actually hold: the flag
    # byte is 0 or 1 and the clamped minute byte is a multiple of 5 in [5,60]. None is 1.
    legal = {minutes | (flag << 8) for flag in (0, 1) for minutes in range(5, 61, 5)}
    assert 1 not in legal, "an unmasked `== 1` gate would still be reachable -- premise broken"
    assert not any(w == 1 for w in legal)

    # (b) The SAME composition with the mask configured is alive: the masked test is emitted,
    # and it holds for exactly the linked half of those words.
    alive = compile_ops(stock, stock_hash,
                        *ops(dict(_PUMPLINK_PARAMS, gate_mask=0xFF00, gate_on_value=0x0100)))
    assert alive.ok, alive.errors
    assert masked_seq in guicode(alive.data), "the masked gate was not emitted"
    assert {w for w in legal if (w & 0xFF00) == 0x0100} == {0x0100 | m for m in range(5, 61, 5)}


def test_add_pump_auto_off_refuses_a_link_group_the_adopted_toggle_does_not_match(stock,
                                                                                   stock_hash):
    """Adopt-or-create must never silently adopt a toggle intended for something else: if a
    project's `link_group` disagrees with the group `find_config_toggle` actually locates,
    refuse (TOGGLE_REFUSED), naming both."""
    from fireflymcf import recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumplink", type="add_option_follower",
                                  params=_PUMPLINK_PARAMS),
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off",
                                  params={"link_group": 99}))
    assert not r.ok
    assert r.errors[0]["code"] == "TOGGLE_REFUSED", r.errors
    assert "164" in r.errors[0]["message"] and "99" in r.errors[0]["message"]


def test_add_pump_auto_off_minutes_default_mismatch_refuses_at_construction(stock, stock_hash):
    """I2: minutes_default has no image dependency, so a bad value must refuse in validate()
    (op construction), not partway through apply() after mutations have already run. Observed
    the same way every other BAD_PARAMS-style construction failure in this file is: the whole
    compile refuses before any op's apply() runs, with a single structured error."""
    from fireflymcf import recipe
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="pumpauto", type="add_pump_auto_off",
                                  params={"minutes_default": 45}))
    assert not r.ok
    assert r.data is None
    assert r.errors[0]["code"] == "SECTION_REFUSED", r.errors


# ======================================================================================
# Task 5: add_register_readout -- any register as a live number on any page.
# ======================================================================================

# Fix round, item 1: this used to read `"y": 560` -- past the panel's 480 px canvas height --
# and only a human caught it before it reached the coach. `AddRegisterReadout.validate` now
# refuses any out-of-panel (x, y) outright (see the dedicated regression test below), so the
# fixture itself has to stay in-bounds; (300, 357) is v17's own flashed-not-yet position.
READOUT_PARAMS = {"register": {"dgn": "DGN_CONFIG", "selector": 0xEE}, "mask": 0xFF,
                  "width_bytes": 1, "page": 4, "x": 300, "y": 357,
                  "format": {"width": 2, "places": 0, "zero_pad": False}}


def test_add_register_readout_refuses_a_y_outside_the_panel(stock, stock_hash):
    """Important fix round item 1: the plan once shipped `y: 560` (the panel is only 480 px
    tall) and only a human caught it before it reached the coach. `validate()` must refuse any
    (x, y) outside the physical panel, not rely on a human to notice a bad number."""
    from fireflymcf import recipe
    params = dict(READOUT_PARAMS, y=560)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=params))
    assert not r.ok
    assert r.errors[0]["code"] == "BAD_PARAMS", r.errors


def test_add_register_readout_refuses_an_x_outside_the_panel(stock, stock_hash):
    """The symmetric case for x, and a negative value on top -- `validate` must reject both
    below 0 and at/above the panel width, not just the y axis the original bug hit."""
    from fireflymcf import recipe
    for bad_x in (-1, 800):
        params = dict(READOUT_PARAMS, x=bad_x)
        r = compile_ops(stock, stock_hash,
                        recipe.OpSpec(id="ro", type="add_register_readout", params=params))
        assert not r.ok
        assert r.errors[0]["code"] == "BAD_PARAMS", (bad_x, r.errors)


def test_add_register_readout_on_stock_draws_the_minutes_on_home(stock, stock_hash):
    """On stock this lands on tier 1 (cell 211, `fresh=False`) -- the same tier class as v17's
    own `minutes-home` (tier 2, also not fresh), which was FLASHED AND VERIFIED 2026-09-04
    (the reference coach's build log (not published) "Experimental v17", `docs/BUS-BASELINE.md` PC.59), so
    `AddRegisterReadout`'s class default is `hardware_verified = True` and a non-fresh cell
    keeps it."""
    from fireflymcf import followchannel, guidata, readout, recipe, sramreach
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=dict(READOUT_PARAMS)))
    assert r.ok, r.errors
    assert r.unverified == []
    img = r.data
    n = len(guidata.object_tables(img)[1])
    # Ruling A: HOME's root is OBJECT 4 (screen id 0), not the screen id -- directive_in_force
    # and Screen.walk both take a root object index.
    events = readout.directive_in_force(img, 4)
    mine = [e for e in events if e[0] == "readout" and e[1] == n - 1]
    assert mine and mine[0][3] == (0, 0x18, 2, 0)
    chain = followchannel.chain_from_hook(img)
    assert chain and chain[0] >= followchannel.STOCK_APPEND_POINT
    slot = mine[0][2]
    from fireflymcf import status_map
    addr = status_map.cell_table(img)[slot]
    forms = sramreach.reaching_forms(img, addr, 1)
    assert forms["raw_word"] == [hex(addr)] and not forms["state_struct"]
    assert all(int(s, 16) >= followchannel.STOCK_APPEND_POINT for s in forms["base_offset"])


def _readout_claimed_image(stock):
    """Stock with every halfword-safe spare cell (211/213/215/217) claimed by a fake pool word,
    so `readout.allocate_display_cell`'s tier 1 refuses -- the same construction
    `tests/test_readout.py::_claimed_image` uses, replicated here so this file does not reach
    into another test module for a private helper."""
    from fireflymcf import patch, status_map, theme
    cells = status_map.cell_table(stock)
    sec = theme.guicode_section(stock)
    buf = bytearray(stock)
    end = sec.offset + sec.length - 4
    for k, idx in enumerate((211, 213, 215, 217)):
        struct.pack_into("<I", buf, end - 16 + 4 * k, cells[idx])
    sealed, _ = patch.reseal(bytes(buf))
    return sealed


def test_add_register_readout_tier_2_cell_reports_verified(stock, stock_hash):
    """A tier-2 cell (an odd spare byte, `fresh=False`) is exactly what v17's `minutes-home`
    used on the coach -- cell 219 @ 0x20039f51 (the reference coach's build log (not published) "Experimental v17";
    FLASHED AND VERIFIED 2026-09-04, `docs/BUS-BASELINE.md` PC.59). Forcing tier 1 to refuse
    (`_readout_claimed_image`) and asking for a width-1 readout lands on tier 2, and the op's
    class default (`hardware_verified = True`) survives a non-fresh instance."""
    from fireflymcf import recipe
    claimed = _readout_claimed_image(stock)
    claimed_hash = hashlib.sha256(claimed).hexdigest()
    params = dict(READOUT_PARAMS, width_bytes=1, mask=0xFF)
    r = compile_ops(claimed, claimed_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=params))
    assert r.ok, r.errors
    assert "ro" not in r.unverified


def test_add_register_readout_tier_3_fresh_cell_stays_unverified(stock, stock_hash):
    """Tier 3 (a spare index repointed at fresh SRAM no stock instruction names) is the one
    case the design spec (2026-09-04-register-readout-design.md Sec5 item 7) says keeps
    `hardware_verified = False` even after the class default flips True: a coach photo of one
    tier does not vouch for an address nothing has ever touched. Forcing every halfword-safe
    cell claimed (`_readout_claimed_image`) and asking for a width-2 readout leaves no odd
    spare byte to use either, so `allocate_display_cell` falls through to tier 3."""
    from fireflymcf import recipe
    claimed = _readout_claimed_image(stock)
    claimed_hash = hashlib.sha256(claimed).hexdigest()
    params = dict(READOUT_PARAMS, width_bytes=2, mask=0xFFFF)
    r = compile_ops(claimed, claimed_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=params))
    assert r.ok, r.errors
    assert "ro" in r.unverified


def test_add_register_readout_refuses_a_directive_leak(stock, stock_hash, monkeypatch):
    """Ruling C: on stock, `insert.insert_object` always appends the new child at the END of
    its parent's record stream, so a real stock compile can never make an existing readout get
    walked AFTER ours with a changed directive -- `DIRECTIVE_LEAK` cannot be triggered honestly
    on stock. This test forces the condition `_refuse_directive_leak` exists to catch: it
    monkeypatches `readout.directive_in_force` to leave the BEFORE image's decode alone but
    rewrite one stock readout's fmt in the AFTER image (distinguished by length -- the after
    image is longer, since our object was appended), simulating a leak, and checks the op
    refuses with DIRECTIVE_LEAK rather than shipping it."""
    from fireflymcf import readout, recipe

    real = readout.directive_in_force
    before_len = len(stock)

    def fake(data, root, **kw):
        events = real(data, root, **kw)
        if len(data) == before_len:
            return events        # the BEFORE image: unchanged
        # the AFTER image: rewrite one stock readout's fmt to simulate a reformatted neighbour
        out = []
        mutated = False
        for e in events:
            if not mutated and e[0] == "readout" and e[3] is not None:
                out.append(("readout", e[1], e[2], (0, 0x18, 9, 3)))
                mutated = True
            else:
                out.append(e)
        return out

    monkeypatch.setattr(readout, "directive_in_force", fake)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=dict(READOUT_PARAMS)))
    assert not r.ok and r.errors[0]["code"] == "DIRECTIVE_LEAK", r.errors


def test_stock_directive_windows_have_no_attachable_parent_between_them(stock):
    """Fix round 1, finding 1 (Important): the FIRST draft of this test (naive "src first
    placed inside obj") was wrong on its own counterexample: on HOME (root 4), object 539 is
    first placed at walk index 80, object 93 (its directive source) at 82, and 539's readout at
    83 -- 93 IS inside 539's window, so that pair does not refute anything. The REAL
    sibling-inheritance case is object 440 (slot 205, root 4): its directive comes from object
    441, first placed at walk index 101 -- OUTSIDE 440's own window (440 first placed at 105,
    its readout at 110). So "own object or first-placed inside it" is false on stock, and a
    test that only checks that licenses nothing.

    What actually licenses `test_add_register_readout_refuses_a_directive_leak` needing a
    monkeypatch, for every such "outside" (sibling-inheritance) pair: no object that CAN be an
    `insert.insert_object` attach point (carries a wtype-5 record) has its true reach -- its own
    last placement, or the last placement of anything nested under it via the object graph
    (`insert.object_child_graph`) -- strictly between the directive's placement index and the
    readout's index. A plain per-tag "last placement" is NOT enough here: object 901 (the
    common parent that attaches BOTH 443, at [97,103], carrying 441, AND 440, at [105,111], as
    its own two wtype-5 children) has an own-tag last placement of 104 -- strictly inside
    (101,110) -- which would flag a false violation, because 901's own record stream already
    ends with the attach-of-440 record: appending a NEW child to 901 lands after 901's ENTIRE
    existing content, i.e. after 440's own subtree too (confirmed by real insertion in
    `test_inserting_into_a_real_shared_parent_lands_after_its_existing_children` below, and
    that empirical confirmation is why this property is checked with the object graph, not
    trusted on the strength of this test alone). Using the graph-aggregated true reach, 901's
    real bound is 111, which is NOT strictly between 101 and 110 -- no violation, on any of the
    224 stock readouts across every screen root. `_refuse_directive_leak` is the guard that
    would catch a future image where this property stops holding."""
    from fireflymcf import guidata, insert, layout, records

    gs, _ = guidata.guidata_extent(stock)
    tbl = guidata.object_tables(stock)[1]
    graph = insert.object_child_graph(stock)

    desc_cache: dict = {}

    def descendants(o):
        if o not in desc_cache:
            seen, stack = set(), [o]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(graph.get(cur, []))
            desc_cache[o] = seen
        return desc_cache[o]

    wtype5_cache: dict = {}

    def has_wtype5(obj):
        if obj not in wtype5_cache:
            try:
                wtype5_cache[obj] = any(
                    r.wtype == 5 for r in records.object_records(stock, gs + tbl[obj]))
            except (IndexError, ValueError):
                wtype5_cache[obj] = False
        return wtype5_cache[obj]

    checked = 0
    sibling_cases = 0
    for root in layout.screen_object_map(stock).values():
        placed = layout.Screen(stock).walk(root)
        first_idx, last_idx = {}, {}
        for i, p in enumerate(placed):
            first_idx.setdefault(p.obj_index, i)
            last_idx[p.obj_index] = i

        def true_reach(o):
            return max((last_idx[d] for d in descendants(o) if d in last_idx), default=-1)

        cur = None
        cur_src = None
        for i, p in enumerate(placed):
            for rec in records.object_records(stock, gs + tbl[p.obj_index]):
                if rec.offset > p.record_offset:
                    break
                if rec.wtype == 9:
                    cur, cur_src = rec.fmt, p.obj_index
                if rec.wtype == 8 and rec.offset == p.record_offset:
                    checked += 1
                    obj = p.obj_index
                    assert cur is not None, "readout %r on root %d has no directive" % (
                        (obj, i), root)
                    src_first = first_idx[cur_src]
                    if cur_src == obj or first_idx[obj] <= src_first <= i:
                        continue          # own object or contained: not a sibling-inheritance case
                    sibling_cases += 1
                    bad = [o for o in last_idx
                          if src_first < true_reach(o) < i and has_wtype5(o)]
                    assert not bad, (
                        "root %d: readout on %d (idx %d) inherits from %d (first placed %d), "
                        "and attachable object(s) %s reach strictly between them -- a real "
                        "insertion there could interpose a new directive" % (
                            root, obj, i, cur_src, src_first, bad))
    assert checked > 0 and sibling_cases > 0        # the 440/441 pair must actually be exercised


def test_inserting_into_a_real_shared_parent_lands_after_its_existing_children(stock):
    """The empirical half of Fix round 1 finding 1: object 901 is the real, non-synthetic
    parent from the 440/441 case above (it attaches both 443 and 440 as its own two wtype-5
    children, in that order). Appending a THIRD child to 901 must land after ALL of 901's
    existing content -- including 440's entire subtree, not just 901's own tagged records --
    and must change no existing stock readout's resolved directive. `insert_object`'s attach
    point is `recs[-1].end` of the PARENT's own record list, and 901's own last record already
    IS the attach-of-440 record, which is what makes this true (`insert.py`'s "child at the
    END of its parent" -- here "end" already includes 440)."""
    from fireflymcf import guidata, insert, layout, readout

    obj = readout.author_readout_object(stock, x=1, y=1, display_slot=211,
                                        directive=readout.directive_bytes(2, 0))
    img, _edits = insert.insert_object(stock, obj, parent_index=901)
    new_index = len(guidata.object_tables(img)[1]) - 1

    placed = layout.Screen(img).walk(4)
    first_idx = {}
    for i, p in enumerate(placed):
        first_idx.setdefault(p.obj_index, i)
    assert first_idx[new_index] > first_idx[440] > first_idx[443]

    before = {(e[1], e[2]): e[3] for e in readout.directive_in_force(stock, 4) if e[0] == "readout"}
    after = {(e[1], e[2]): e[3] for e in readout.directive_in_force(img, 4)
            if e[0] == "readout" and e[1] != new_index}
    assert before == after, "a real insertion into 901 changed an existing readout's directive"


def test_add_register_readout_composes_after_the_pump_feature_on_a_tier_2_cell(stock, stock_hash):
    from fireflymcf import recipe
    ops = _pump_feature_ops() + [
        recipe.OpSpec(id="ro", type="add_register_readout", params=dict(READOUT_PARAMS))]
    r = compile_ops(stock, stock_hash, *ops)
    assert r.ok, r.errors
    # `CompileResult.manifest` is a list of verify()-return STRINGS in op order (recipe.py
    # `_finish`), not dicts -- the "ro" op's own summary is the last entry.
    summary = r.manifest[-1]
    assert summary.startswith("add_register_readout")
    assert "tier 2" in summary and "fresh=False" in summary


def test_add_register_readout_on_a_page_shared_by_several_screens(stock, stock_hash):
    """Fix round 1, finding 2 (Ruling D): 48 stock objects are reachable from more than one
    screen root -- object 793 (the Time at Stop Volts stepper, also this feature's own default
    donor object) is one, reachable from screens 5/16/39/45 -- and a readout attached there must
    be leak-checked and verified under EVERY one of those roots, not just the first. The handle
    carries `roots` (all of them) alongside `root`/`screen` (the first, kept for the summary
    line); this asserts the readout's event actually shows up under at least two of them."""
    from fireflymcf import guidata, readout, recipe

    params = dict(READOUT_PARAMS, page=793)
    r = compile_ops(stock, stock_hash,
                    recipe.OpSpec(id="ro", type="add_register_readout", params=params))
    assert r.ok, r.errors
    img = r.data
    n = len(guidata.object_tables(img)[1]) - 1

    roots_with_it = [root for root in (5, 16, 39, 45)
                     if any(e[0] == "readout" and e[1] == n
                            for e in readout.directive_in_force(img, root))]
    assert len(roots_with_it) > 1, roots_with_it
    # This alone would pass even under the pre-fix single-root implementation (the object is
    # genuinely attached and visible from every root that reaches page 793, regardless of how
    # many roots the op itself leak-checked). The discriminating check is that the handle
    # actually carries every root -- surfaced in the verify() summary line, which prints
    # `h["roots"]` verbatim.
    assert "(of [5, 16, 39, 45])" in r.manifest[0], r.manifest[0]


def test_drop_from_master_removes_bath_light_from_light_master(stock, stock_hash):
    res = compile_ops(stock, stock_hash,
                      recipe.OpSpec(id="d", type="drop_from_master",
                                    params={"master": "Light Master", "channel": 25}))
    assert res.ok, res.errors
    lm = {c.label: c.channels for c in guidata.find_control_groups(res.data)
          if c.screen_title == "Light Master"}
    assert lm == {"ON": [22, 24, 26, 32, 34, 35], "OFF": [22, 24, 26, 32, 34, 35]}
    # FLASHED AND VERIFIED 2026-09-14 (v26): the coach's bath light no longer follows
    # Light Master, so the op reports verified.
    assert "d" not in res.unverified, "drop_from_master is hardware-verified since v26"


def test_drop_from_master_declares_the_master_only_pairs_it_loses(stock, stock_hash):
    # set-brightness (cmd 0) and memory-off (cmd 6) to ch25 are sent ONLY by the master;
    # the bath's own button keeps on/off/toggle. Compiling proves the gate accepts the
    # declared loss (a wrong declaration makes _finish's capability assert fail).
    res = compile_ops(stock, stock_hash,
                      recipe.OpSpec(id="d", type="drop_from_master",
                                    params={"master": "Light Master", "channel": 25}))
    assert res.ok, res.errors
    lost = recipe.capabilities(stock) - recipe.capabilities(res.data)
    assert lost == {(25, 0), (25, 6)}


def test_drop_from_master_rejects_a_non_member(stock, stock_hash):
    res = compile_ops(stock, stock_hash,
                      recipe.OpSpec(id="d", type="drop_from_master",
                                    params={"master": "Light Master", "channel": 99}))
    assert not res.ok
    assert res.errors[0]["code"] == "NOT_A_MEMBER"


def test_retarget_action_leaf_is_guarded(stock):
    from fireflymcf import navicon, navmap
    out, edits = navicon.retarget_action_leaf(stock, 0x7ED, 29, 12)
    assert {r.touch_id: r.screen for r in navmap.routes(out)}[0x7ED] == 12 and len(edits) == 1
    with pytest.raises(ValueError):                       # not where the caller thinks it goes
        navicon.retarget_action_leaf(stock, 0x7ED, 12, 29)
    with pytest.raises(ValueError):                       # no such leaf
        navicon.retarget_action_leaf(stock, 0x6FF, 29, 12)


def test_no_op_reads_two_params_into_one_attribute():
    """v43's first build assigned a new boolean param `refresh` to `self.refresh`, which the LEVEL
    op already used for its staleness class; the class silently became 0 in every rebuild. Every
    op's `validate` must read each attribute from exactly one param name."""
    import ast
    import inspect
    from fireflymcf import recipe as R
    import textwrap
    bad = []
    for name, cls in R.REGISTRY.items():
        src = textwrap.dedent(inspect.getsource(cls.validate))
        seen = {}
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            t, v = node.targets[0], node.value
            if not (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "self" and isinstance(v, ast.Call)
                    and isinstance(v.func, ast.Attribute) and v.func.attr == "_param"
                    and v.args and isinstance(v.args[0], ast.Constant)):
                continue
            seen.setdefault(t.attr, set()).add(v.args[0].value)
        bad += [(name, attr, sorted(ps)) for attr, ps in seen.items() if len(ps) > 1]
    assert not bad, bad
