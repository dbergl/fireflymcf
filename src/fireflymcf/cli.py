"""Command-line entry points.

    python3 -m fireflymcf.cli extract <mcf.bin> <dest-dir>
    python3 -m fireflymcf.cli regions <cli.bin> <dest-dir>
    python3 -m fireflymcf.cli identify <mcf.bin>
    python3 -m fireflymcf.cli coverage <mcf.bin> [--deep]
    python3 -m fireflymcf.cli labels <mcf.bin> [--containing TEXT]
    python3 -m fireflymcf.cli patch <mcf.bin> <out.bin>
            [--rename OLD=NEW [--site HEX]... [--on-screen TITLE]]
            [--remap LABEL=CHANNEL [--allow-capability-change]]
            [--move TEXT=X,Y [--site HEX]]
    python3 -m fireflymcf.cli options <mcf.bin> [--json]
    python3 -m fireflymcf.cli bus-map <mcf.bin> [--json] [--dgn X]... [--detail]
    python3 -m fireflymcf.cli render <mcf.bin> <out.png> [--page NAME | --root N]
            [--config FILE] [--slot SLOT=VALUE]... [--match-off HEX]

`coverage` accounts for every byte of an MCF and exits non-zero if any byte is left
unclaimed; `--deep` opens up the GuiData section as well. See src/fireflymcf/coverage.py.

`labels` lists label records -- every one containing `--containing TEXT`, or, with no
filter, the title and rows of every screen the opcode table can decode.

`patch` rewrites a label, repoints a control's G12 channel, and/or moves a draw
element to an absolute pixel position, then reseals the
container's checksums. It only ever makes same-size edits, runs the five-rung verification
ladder, and refuses to write if any rung
fails or if the destination lies inside a read-only corpus directory.

`options` lists every OPTIONS-page checkbox binding -- name, value-slot, the register it
resolves to, and its vtype (`src/fireflymcf/options.py`'s `option_bindings`) -- sorted by
name, followed by the floorplan value -> name map (`floorplan_options`). `--json` emits the
same data as one JSON object instead of a table. Some builds have no confirmed floorplan
table (`FloorplanSwitchNotFound`, e.g. 11.23); this command reports that rather than crashing.

`render` writes an 800x480 PNG of one GuiData page at a given coach configuration, using
the byte-exact decoded compositor (`src/fireflymcf/render.py`'s `climate_placements_decoded`
/ `render_climate_decoded` -- the name predates this command; it renders any page, not only
Climate). The page is chosen by `--root N` (an explicit object-table index) or `--page NAME`
(`home`/`electrical`/`lights`/`climate`/`settings`/`options`, resolved generically per build by
finding the object in `guidata.object_tables(data)[1]` whose header record -- its first labelled
record, the same convention `options._find_options_pages` uses for "OPTIONS" -- strips to that
page's header text; plus `outputs`, the G12 OUTPUTS diagnostics screen, resolved structurally
by `_resolve_outputs_root` instead, since its header text "NETWORK DIAGNOSTICS" is shared with
the DEVICES/FAULTS tab); `--root` wins if both are given, and with neither the Climate page
(`_resolve_page_root(data, "climate")` -- resolved per build, not `layout.climate_root`'s
hardcoded 13.6 value) is the default. `--page` raises a clear error (not a silent wrong
render) if a build has no object it can resolve that name to -- pass `--root` instead. The coach
configuration is a `{value_slot: value}` map built from `--config FILE`
(`options.CoachConfig.from_json` -> `options.slot_values`) merged with any `--slot SLOT=VALUE`
overrides, which win over the config file; `--match-off HEX` overrides the match-value table
offset the layout engine reads switches from, for a build where it is not auto-located.

`identify` prints both halves of what is known about the embedded sections.

embedded.identify() resolves exactly one of them on this corpus: `LCD Firmware`, a
plain Cortex-M image based at 0x00210000 (proven in Task 6 --
docs/PARSER-LOCATION.md). GuiCode, GuiData and G12 are not Cortex-M images at all, so
identify() will never resolve them and no candidate base would help. Their useful
description comes from embedded.characterise() instead -- name, length, container
`kind`, first four words, build tag -- which is why this subcommand prints that for
every section first, and the identify() result after it, clearly labelled.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
from pathlib import Path

from . import armimg, console, container, coverage, embedded, guidata, options, patch
# `render` (and its Pillow/numpy dependency) is imported lazily in `_cmd_render` so the
# read/patch commands run on a stdlib-only install -- see `pyproject.toml` extras.


def _cmd_extract(args: argparse.Namespace) -> int:
    data = Path(args.image).read_bytes()
    written = container.extract_sections(data, Path(args.dest))
    for p in written:
        print(f"{p}  ({p.stat().st_size} bytes)")
    return 0


def _cmd_regions(args: argparse.Namespace) -> int:
    """Emit header-free region files ready for Ghidra's raw loader."""
    data = Path(args.image).read_bytes()
    hdr = armimg.parse_header(data)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image).stem
    for i, r in enumerate(hdr.regions, start=1):
        out = dest / f"{stem}-region{i}.bin"
        out.write_bytes(data[r.file_offset:r.file_offset + r.length])
        print(f"{out}  load={r.load_addr:#010x}  len={r.length:#x}")
    return 0


def _cmd_identify(args: argparse.Namespace) -> int:
    data = Path(args.image).read_bytes()
    if args.json:
        import dataclasses
        print(json.dumps({"sections": [dataclasses.asdict(s) for s in embedded.characterise(data)],
                          "images": [dataclasses.asdict(i) for i in embedded.identify(data)]},
                         indent=2, default=str))
        return 0

    print("section characterisation (embedded.characterise()):")
    for sig in embedded.characterise(data):
        words = " ".join(f"{w:#010x}" for w in sig.first_words)
        tag = f" build_tag={sig.build_tag:#06x}" if sig.build_tag is not None else ""
        print(f"  {sig.section_name}: len={sig.length:#x} kind={sig.kind} "
              f"words=[{words}]{tag}")

    found = embedded.identify(data)
    print("embedded ARM image identification (embedded.identify()):")
    if not found:
        print("  no embedded ARM images identified")
    for img in found:
        print(f"  {img.section_name}: load={img.load_addr:#010x} "
              f"entry={img.entry:#010x} sp={img.initial_sp:#010x}")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    """Account for every byte of an MCF, and list anything unaccounted for."""
    data = Path(args.image).read_bytes()
    report = coverage.analyse(data, deep=args.deep)
    if args.json:
        print(json.dumps({"total": report.total, "claimed": report.claimed,
                          "unclaimed": report.unclaimed, "by_label": report.bytes_by_label(),
                          "trailing_headroom": report.trailing_headroom}, indent=2))
        return 0 if not report.unclaimed else 1
    print(coverage.format_report(report))
    return 0 if not report.unclaimed else 1


def _screen_of(screens, offset: int) -> str:
    """The decodable screen containing `offset`, described for a listing, or '-'."""
    owners = [s for s in screens if s.start <= offset < s.end]
    if not owners:
        return "-"
    return "; ".join(f"{s.title.strip()!r}@{s.start:#x}" for s in owners)


def _object_of(screen, file_offset: int) -> int | None:
    """Index of the object (object table 1) whose byte span holds `file_offset`, or None.

    Objects are stored at ascending addresses in table order is NOT guaranteed, so the owner
    is the entry with the largest start at or below the offset. This is the index
    `rename_label`, `recolor` and `pin_switch_arm` take."""
    rel = file_offset - screen.gs
    best, best_off = None, -1
    for i, off in enumerate(screen.table):
        if off and best_off < off <= rel:
            best, best_off = i, off
    return best


def _cmd_labels(args: argparse.Namespace) -> int:
    data = Path(args.image).read_bytes()
    screens = list(guidata.iter_screens(data))

    if args.containing is None:
        if args.json:
            print(json.dumps([{"start": s.start, "title": s.title,
                               "rows": [{"offset": w.offset, "label": w.label}
                                        for w in s.widgets]} for s in screens], indent=2))
            return 0
        print(f"{len(screens)} decodable screen(s):")
        for s in screens:
            print(f"  {s.start:#09x}  {s.title!r}  ({len(s.widgets)} row(s))")
            for w in s.widgets:
                print(f"      row {w.offset:#09x}  {w.label!r}")
        return 0

    from . import layout
    screen = layout.Screen(data)
    sites = patch.find_label_sites(data, args.containing)
    if args.json:
        print(json.dumps([{"text_offset": site.text_offset, "length_offset": site.length_offset,
                           "length": site.length, "object": _object_of(screen, site.text_offset),
                           "text": site.text,
                           "screens": [{"start": sc.start, "title": sc.title.strip()}
                                       for sc in screens
                                       if sc.start <= site.text_offset < sc.end]}
                          for site in sites], indent=2))
        return 0
    print(f"{len(sites)} label record(s) contain {args.containing!r}:")
    print(f"  {'text off':<10} {'len':<4} {'object':<7} {'field':<32} screen")
    for site in sites:
        obj = _object_of(screen, site.text_offset)
        print(f"  {site.text_offset:#09x}  {site.length:<4} {str(obj):<7} {site.text!r:<32} "
              f"{_screen_of(screens, site.text_offset)}")
    return 0


# Directories holding read-only evidence. CLAUDE.md: the .bin corpus, the PDF and
# captures/ are never modified, so the patcher refuses to write anywhere inside them
# rather than relying on the caller to remember.
#
# The corpus directories are `firefly-<version>` -- firefly-13.6, firefly-11.23,
# "firefly-14.7 (Terrain)". Matching the bare `firefly-` prefix also matched the repository
# root, firefly-firmware, and so refused every legitimate destination in the project; the
# digit is what separates a build directory from the checkout that contains them.
_READ_ONLY_DIRS = ("captures",)


def _is_corpus_dir(part: str) -> bool:
    return part.startswith("firefly-") and part[len("firefly-"):len("firefly-") + 1].isdigit()


def _check_writable(out: Path, image: Path) -> None:
    resolved = out.resolve()
    for part in resolved.parts:
        if _is_corpus_dir(part) or part in _READ_ONLY_DIRS:
            raise ValueError(
                f"refusing to write {out}: {part!r} holds read-only evidence")
    if resolved == image.resolve():
        raise ValueError(f"refusing to patch {image} in place")
    if resolved.exists():
        raise ValueError(f"refusing to overwrite the existing file {out}")


def _print_fill_palette(data: bytes) -> None:
    from . import theme
    palette = theme.fill_palette(data)
    print("firmware fill palette (colour -> indices):")
    for rgb in sorted(palette, key=lambda c: (-len(palette[c]["static"]), c)):
        kinds = palette[rgb]
        stat = ",".join(str(i) for i in kinds["static"]) or "-"
        rest = ",".join(str(i) for i in kinds["resting"][:8]) or "-"
        print(f"  {str(rgb):18s} static: {stat:20s} resting: {rest}")


def _cmd_patch(args: argparse.Namespace) -> int:
    image, out = Path(args.image), Path(args.out)

    if args.list_colors:
        _print_fill_palette(image.read_bytes())
        return 0

    _check_writable(out, image)

    if not args.rename and not args.remap and not args.move and not args.recolor:
        raise ValueError(
            "nothing to do: pass --rename, --remap, --move and/or --recolor")

    data = image.read_bytes()
    screens = list(guidata.iter_screens(data))
    patcher = patch.McfPatcher(data)

    for spec in args.recolor:
        if "=" not in spec:
            raise ValueError(f"--recolor wants OBJ=COLOR, got {spec!r}")
        obj_s, color_s = spec.split("=", 1)
        obj = int(obj_s, 0)
        box = patch.find_button_box(data, obj)
        if color_s.startswith("idx"):
            print(f"recolouring object {obj} box to palette index {color_s[3:]}")
            patcher.set_fill_color(box, index=int(color_s[3:], 0))
        else:
            try:
                r, g, b = (int(v) for v in color_s.split(","))
            except ValueError:
                raise ValueError(f"--recolor COLOR wants R,G,B or idxN, got {color_s!r}")
            print(f"recolouring object {obj} box to {(r, g, b)}")
            patcher.set_fill_color(box, color=(r, g, b))

    if args.move:
        if "=" not in args.move:
            raise ValueError(f"--move wants TEXT=X,Y, got {args.move!r}")
        text, coord = args.move.rsplit("=", 1)
        try:
            xs, ys = coord.split(",")
            x, y = int(xs), int(ys)
        except ValueError:
            raise ValueError(f"--move coords want X,Y, got {coord!r}")
        sites = patch.find_label_sites(data, text)
        if args.site:
            wanted = {int(s, 16) for s in args.site}
            sites = [s for s in sites if s.text_offset in wanted]
        elements = []
        for site in sites:
            try:
                el = patch.find_draw_element(data, site.length_offset)
                elements.append((site, el))
            except patch.PatchError:
                pass
        if not elements:
            raise ValueError(f"no draw element with a position found for {text!r}")
        if len(elements) > 1:
            raise ValueError(
                f"{text!r} resolves to {len(elements)} draw elements; use --site to pick "
                f"one of {', '.join(hex(s.text_offset) for s, _ in elements)}")
        el = elements[0][1]
        print(f"moving {text!r} from ({el.x},{el.y}) to ({x},{y})")
        patcher.set_position(el, x=x, y=y)

    if args.remap:
        if "=" not in args.remap:
            raise ValueError(f"--remap wants LABEL=CHANNEL, got {args.remap!r}")
        label, raw_channel = args.remap.rsplit("=", 1)
        channel = int(raw_channel, 0)
        controls = [c for c in guidata.find_control_groups(data)
                    if c.label.strip() == label.strip()]
        if not controls:
            known = ", ".join(sorted({c.label.strip().replace("\n", " ")
                                      for c in guidata.find_control_groups(data)}))
            raise ValueError(f"no located control is labelled {label!r}; known: {known}")
        if len(controls) > 1:
            raise ValueError(
                f"{label!r} matches {len(controls)} controls (groups "
                f"{', '.join(str(c.group) for c in controls)}); this is ambiguous")
        control = controls[0]
        dimmable = guidata.dimmable_channels(data)
        print(f"remapping {control.label.strip()!r} (group {control.group}) "
              f"{control.channels} -> {channel}")
        print(f"  source is {'dimmable' if set(control.channels) & dimmable else 'latch-only'}"
              f"; destination {channel} is "
              f"{'dimmable' if channel in dimmable else 'commanded nowhere in this file'}")
        patcher.set_channel(control, channel,
                            allow_capability_change=args.allow_capability_change)

    if not args.rename:
        return _finish_patch(args, data, patcher, screens, out)

    if args.rename and "=" not in args.rename:
        raise ValueError(f"--rename wants OLD=NEW, got {args.rename!r}")
    old, new = args.rename.split("=", 1)

    sites = patch.find_label_sites(data, old)
    if not sites:
        raise ValueError(f"no label record contains {old!r}")

    if args.site:
        wanted = {int(s, 16) for s in args.site}
        chosen = [s for s in sites if s.text_offset in wanted]
        missing = wanted - {s.text_offset for s in chosen}
        if missing:
            raise ValueError(
                f"--site {', '.join(hex(m) for m in sorted(missing))} is not a label "
                f"record containing {old!r}")
    elif args.on_screen:
        chosen = [s for s in sites
                  if any(scr.title.strip() == args.on_screen.strip()
                         and scr.start <= s.text_offset < scr.end for scr in screens)]
        if not chosen:
            raise ValueError(
                f"no label record containing {old!r} lies on a decodable screen titled "
                f"{args.on_screen!r}")
    else:
        chosen = sites

    skipped = [s for s in sites if s not in chosen]
    print(f"renaming {old!r} -> {new!r} at {len(chosen)} of {len(sites)} site(s)")
    for s in skipped:
        print(f"  SKIPPED  {s.text_offset:#09x}  {s.text!r}  "
              f"({_screen_of(screens, s.text_offset)})")

    for site in chosen:
        patcher.set_label(site, old, new)
    return _finish_patch(args, data, patcher, screens, out)


def _finish_patch(args: argparse.Namespace, data: bytes, patcher: "patch.McfPatcher",
                  screens: list, out: Path) -> int:
    edits = patcher.edits()
    for e in edits:
        try:
            rendered = f"{e.old.decode('ascii')!r} -> {e.new.decode('ascii')!r}"
        except UnicodeDecodeError:
            rendered = f"{e.old.hex()} -> {e.new.hex()}"
        print(f"  EDIT     {e.file_offset:#09x}  {rendered}  in {e.structure}  "
              f"({_screen_of(screens, e.file_offset)})")

    patched, changed = patcher.build()
    print(f"  resealed {len(changed)} checksum(s): {', '.join(changed)}")

    if args.verify:
        for line in patch.verify(data, patched, edits, changed):
            print(f"  {line}")

    # Name the build so the flasher's file menu distinguishes it. An explicit --name wins;
    # otherwise a custom build is auto-marked (idempotently) so it never collides with stock.
    name = getattr(args, "name", None)
    if name:
        patched, _ = patch.set_build_name(patched, name)
        print(f"  build name set to {patch.build_name(patched)!r}")
    elif not getattr(args, "no_mark", False):
        marked = patch.mark_custom(patched)
        if marked is not patched:
            patched = marked
            print(f"  build name marked: {patch.build_name(patched)!r}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patched)
    print(f"wrote {out}  ({len(patched)} bytes)")
    return 0


def _fmt_register(value: int) -> str:
    """`0x20039B49` -- lowercase `0x` prefix, uppercase hex digits, fixed 8-digit width."""
    return f"0x{value:08X}"


def _cmd_doctor(args: argparse.Namespace) -> int:
    """One build-general report of what an owner can do with THEIR firmware.

    Consolidates the cross-build analyses (identity, sections, screens, images, options,
    floorplans, and the self-identifying option interlocks) into a single read-only summary, so an
    owner runs it on their own coach's file and immediately sees what is present and which edits are
    safe -- without knowing any of the tooling. It never writes; it is also a portability smoke-test
    (every section it reports is a structural decode that must work on any build)."""
    import hashlib
    from . import container, images, interlocks, layout, options
    from .theme import CapstoneUnavailable
    data = Path(args.image).read_bytes()

    info: dict = {}
    cont = container.parse_container(data)
    info["build_name"] = patch.build_name(data)
    info["config_name"] = cont.config_name
    info["sha256"] = hashlib.sha256(data).hexdigest()
    info["total_bytes"] = len(data)
    info["sections"] = [{"name": s.name, "bytes": s.length} for s in cont.sections]

    # Sections/build-name are stdlib-only; the deeper analyses (screens, options, interlocks) need
    # the [full] extra. Distinguish "analysed, found none" from "could not analyse (missing deps)"
    # -- reporting a false "0 options / no interlocks" on a bare install would be a silent wrong
    # answer, the exact hazard this project guards against.
    UNAVAILABLE = "unavailable"

    def _try(fn, default):
        try:
            return fn()
        except CapstoneUnavailable:                   # capstone not installed (the disasm path)
            return UNAVAILABLE
        except ModuleNotFoundError as exc:            # numpy/PIL not installed
            if exc.name and exc.name.split(".")[0] in ("numpy", "capstone", "PIL"):
                return UNAVAILABLE
            return default
        except Exception:                             # a build-specific structure we cannot read
            return default

    info["screens"] = _try(lambda: len(layout.screen_object_map(data)), None)
    info["images"] = _try(lambda: images.image_count(data), None)
    info["options"] = _try(lambda: [b.name for b in options.option_bindings(data)], [])
    info["floorplans"] = _try(lambda: len(options.floorplan_options(data)), None)

    # Option interlocks, self-identifying and build-general. Group each coupling by the OPTIONS row
    # it involves, naming it from the bytes (interlocks.option_for_selector), and say whether this
    # build's family is one `uncouple` can safely edit (13.6) or only report.
    couplings = _try(lambda: interlocks.find_coupled_handlers(data), {})
    if couplings == UNAVAILABLE:
        info["interlocks"] = UNAVAILABLE
    else:
        removable = _try(lambda: interlocks.addresses_apply(data), False)
        by_option: dict[str, int] = {}
        for emissions in couplings.values():
            sels = [e.selector for e in emissions
                    if e.write and e.selector != interlocks.MODE_SELECTOR]
            name = None
            for sel in sels:
                name = _try(lambda s=sel: interlocks.option_for_selector(data, s), None)
                if name:
                    break
            key = name or (f"selector {sels[0]:#04x}" if sels else "unknown")
            by_option[key] = by_option.get(key, 0) + 1
        info["interlocks"] = {"removable": bool(removable),
                              "couplings": [{"with_option": k, "handlers": v}
                                            for k, v in sorted(by_option.items())]}

    # The G12's 1FFB7 TANK_STATUS broadcasts, read from its rule table (`tankbroadcast`): the
    # instance numbers are this file's; the names are the RV-C standard's for each number.
    # `panel_name` is the caption on the gauge the panel feeds from that instance (`tanknames`,
    # traced through the receive filter and GuiCode), or None where no gauge is traced.
    def _tanks():
        from . import tankbroadcast, tanknames
        try:
            panel = tanknames.tank_names(data)
        except tanknames.TankNameError:
            panel = {}
        return [{"instance": b.instance, "rvc_name": b.tank,
                 "panel_name": panel.get(b.instance, (None, None))[0],
                 "panel_name_source": panel.get(b.instance, (None, None))[1],
                 "broadcasting": b.activate_at is not None, "config_gated": b.guarded}
                for b in tankbroadcast.tank_broadcasts(data)]
    info["tank_broadcasts"] = _try(_tanks, [])

    # The bus map's totals, by class (`busmap.receive_map`, `busmap.send_map`), with `--bus`: they
    # need the GuiCode traces (~5 s, more than the rest of this report), so they are opt-in.
    # Counted without the OUTPUTS-page names, most of a full map's cost; `bus-map` has every
    # message with the panel's names.
    def _bus():
        from . import busmap, sendmap
        recv = busmap.receive_map(data, None, False)
        sm = busmap.send_map(data, None, False)
        live = [(g, e) for g in sm.groups for e in g.entries if not e.stub]
        return {
            "receive": {"entries": len(recv), "dgns": len({e.dgn for e in recv}),
                        **{c: sum(1 for e in recv if e.cls == c) for c in busmap.CLASSES}},
            "send": {"bindings": len(live), "groups": len(sm.groups),
                     "dgns": len({e.dgn for _g, e in live} | {s.dgn for s in sm.guicode
                                                              if s.dgn is not None}),
                     **{c: sum(1 for g, _e in live if g.cls == c)
                        for c in sendmap.GROUP_CLASSES if c != "stub"},
                     "guicode_sites": len(sm.guicode),
                     **{"guicode_" + c: sum(1 for x in sm.guicode if x.context == c)
                        for c in sendmap.CONTEXTS},
                     "config_set_calls": len(sm.config_calls)},
        }
    info["bus"] = _try(_bus, None) if getattr(args, "bus", False) else "not requested"

    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    L: list[str] = []
    a = L.append
    a(f"Firefly firmware report — {info['build_name']}")
    a(f"  config {info['config_name']!r}   sha256 {info['sha256'][:16]}…   {info['total_bytes']:,} bytes")
    a("")
    a("Sections (the panel flashes these as three units — GuiCode+GuiData are inseparable):")
    for s in info["sections"]:
        a(f"  {s['name']:<28} {s['bytes']:>9,} bytes")
    a("")
    NEEDS = "needs `pip install 'fireflymcf[full]'`"
    scr = NEEDS if info["screens"] == UNAVAILABLE else f"{info['screens']} navigable pages"
    a(f"Screens:    {scr}       (list them with `pages`)")
    a(f"Images:     {info['images']}                        (export/swap with `image-export` / `swap-image`)")
    if info["options"] == UNAVAILABLE:
        a(f"Options:    {NEEDS}")
    else:
        fp = "" if info["floorplans"] in (None, UNAVAILABLE) else f"    Floorplans: {info['floorplans']}"
        a(f"Options:    {len(info['options'])}{fp}")
        if info["options"]:
            a(f"            {', '.join(info['options'])}")
    a("")
    a("Option interlocks (cross-option couplings enforced by the panel):")
    if info["interlocks"] == UNAVAILABLE:
        a(f"  {NEEDS}")
    elif not info["interlocks"]["couplings"]:
        a("  none on this build")
    else:
        verb = "removable with `remove_option_interlocks`" if info["interlocks"]["removable"] else \
            "reported only (removal not validated for this build)"
        for c in info["interlocks"]["couplings"]:
            a(f"  generator/AES mode ↔ {c['with_option']}  ({c['handlers']} handler(s); {verb})")
    a("")
    a("Tank broadcasts (1FFB7 TANK_STATUS instances this file's G12 sends):")
    if info["tank_broadcasts"] == UNAVAILABLE:
        a(f"  {NEEDS}")
    elif not info["tank_broadcasts"]:
        a("  none found on this build")
    else:
        for t in info["tank_broadcasts"]:
            state = ("silent" if not t["broadcasting"] else
                     "broadcasting, behind a config gate" if t["config_gated"] else "broadcasting")
            a(f"  instance {t['instance']:<3}  panel: {t['panel_name'] or '-':<14} "
              f"RV-C: {t['rvc_name']:<18} {state}")
        a("  Numbers and panel names (the caption on the gauge each instance feeds) are read from")
        a("  this file; `-` means no gauge was traced. The RV-C name is the standard's for the number.")
        if any(not t["panel_name"] for t in info["tank_broadcasts"]):
            a("  Where the panel name is `-`, the RV-C name is the RV-C specification's name and may")
            a("  not match what Firefly calls that tank.")
        a("  Silence one with `fireflymcf-tankpatch` (report-only without a tank flag).")
    a("")
    a("Bus map (`bus-map` lists every message with what the panel calls it):")
    bus = info["bus"]
    if bus == "not requested":
        a("  run `doctor --bus` for the receive and send totals (about 5 s more)")
    elif bus == UNAVAILABLE:
        a(f"  {NEEDS}")
    elif not bus:
        a("  could not be read on this build")
    else:
        r, t = bus["receive"], bus["send"]
        a(f"  receives: {r['entries']} filter entries on {r['dgns']} DGNs -- {r['drawn']} reach a "
          f"painted cell, {r['config']} the config, {r['logic-only']} logic only, "
          f"{r['write-only']} written and never read, {r['untraced']} untraced, {r['inert']} inert")
        a(f"  sends:    {t['bindings']} bindings in {t['groups']} groups -- {t['reached']} on a "
          f"control a page draws, {t['unattached']} only on undrawn objects, {t['dead']} on no "
          f"control; {t['guicode_sites']} GuiCode send sites ({t['guicode_touch']} from touch "
          f"handlers, {t['guicode_other']} per-tick or other); {t['config_set_calls']} config-set calls")
    a("")
    a("What you can do on this build (all build-general, all gated):")
    a("  • Same-size edits — rename / move / recolour a label, swap a logo — pass the full")
    a("    verification ladder and are the most portable (`patch`, `swap-image`).")
    a("  • Preview any page before flashing (`render`, `pages`).")
    a("  • Bigger, structural changes — add a control, grow a label, install a glyph — via")
    a("    Firefly Studio or a recipe; higher-risk, but pointer-repaired and gated.")
    a("  • Inspect G12 output config, pins, gestures (`g12-rules`, `pins`, `gestures`).")
    a("")
    a("Flash & recover: see docs/GETTING-STARTED.md — keep your STOCK file; a bad build is a")
    a("re-flash, not a brick. Edits are authored against YOUR firmware; indices differ per build.")
    print("\n".join(L))
    return 0


def _cmd_pages(args: argparse.Namespace) -> int:
    """List every navigable page on THIS build: its screen id, its `--root N`, and its header.

    Build-general (`layout.screen_object_map` locates the screen-switch table structurally), so it
    works on any coach's file -- the point being that page *names* and object indices differ
    between builds, so an owner discovers their own coach's roots here rather than guessing. The
    `--page` column marks the pages the named `render --page` shortcuts resolve to; a blank header
    is honest (some pages, e.g. 14.7's electrical, carry a dynamic caption instead of a static
    title) -- use the `--root` value to render it.
    """
    from . import layout
    data = Path(args.image).read_bytes()
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    screen_map = layout.screen_object_map(data)

    # Reverse-map the known `--page` names to the root object each resolves to on this build.
    page_of_root: dict[int, str] = {}
    for name in _PAGE_NAMES:
        try:
            page_of_root.setdefault(_resolve_page_root(data, name), name)
        except ValueError:
            pass

    def header(obj: int) -> str:
        try:
            lab = options._first_label(options._own_records(data, gs, ge, table, obj))
        except Exception:
            return ""
        return lab.strip().replace("\n", " ") if lab else ""

    rows = [(sid, screen_map[sid], header(screen_map[sid]), page_of_root.get(screen_map[sid], ""))
            for sid in sorted(screen_map)]

    if args.json:
        print(json.dumps([{"screen": s, "root": o, "header": h, "page": p or None}
                          for s, o, h, p in rows], indent=2))
        return 0
    print(f"{'screen':>6} {'--root':>6}  {'--page':<11} header")
    for s, o, h, p in rows:
        print(f"{s:>6} {o:>6}  {p:<11} {h}")
    # Note any named page whose root is not itself a navigable screen (e.g. `outputs` is a sub-page).
    extra = sorted((r, n) for r, n in page_of_root.items() if r not in screen_map.values())
    for root, name in extra:
        print(f"{'':>6} {root:>6}  {name:<11} (resolved by --page {name}; not a top-level screen)")
    return 0


def _cmd_image_export(args: argparse.Namespace) -> int:
    """List the GuiData images, or export one to a binary P6 PPM.

    With no `--index`, prints the table of images (index, dimensions, bytes in place) so an owner
    can find the logo they want. With `--index N --out FILE.ppm`, writes that image as a PPM they
    can open and edit in any image tool, then feed back with `swap-image`.
    """
    from . import images, imageenc
    data = Path(args.image).read_bytes()
    if args.index is None and args.json:
        rows = []
        for i in range(images.image_count(data)):
            w, h = images.dimensions(data, i)
            rows.append({"index": i, "width": w, "height": h,
                         "bytes": imageenc._encoded_length(data, i)})
        print(json.dumps(rows, indent=2))
        return 0
    if args.index is None:
        print(f"{'idx':>3}  {'size':>11}  {'bytes':>6}")
        for i in range(images.image_count(data)):
            w, h = images.dimensions(data, i)
            avail = imageenc._encoded_length(data, i)
            print(f"{i:>3}  {w:>4} x {h:<4}  {avail:>6}")
        return 0
    if not args.out:
        raise ValueError("--out FILE.ppm is required with --index")
    img = images.decode_image(data, args.index)
    Path(args.out).write_bytes(imageenc.write_ppm(img.width, img.height, img.pixels))
    print(f"wrote {args.out}  ({img.width}x{img.height} P6 PPM)")
    return 0


def _cmd_swap_image(args: argparse.Namespace) -> int:
    """Replace one GuiData image with a P6 PPM, in place, and reseal.

    The new artwork is written over the original's byte span -- no relocation -- so it must
    encode no larger than the image it replaces (true for a same-or-simpler picture; a busy
    photo of the same size may not fit and is refused rather than corrupting a neighbour). Its
    dimensions may differ from the original as long as the encoding still fits. The result runs
    the same reseal + gates as any build.
    """
    from . import images, imageenc, relocate, coverage
    image, out = Path(args.image), Path(args.out)
    _check_writable(out, image)
    data = image.read_bytes()
    width, height, pixels = imageenc.read_ppm(Path(args.ppm).read_bytes())
    before = images.dimensions(data, args.index)
    print(f"swapping image {args.index} ({before[0]}x{before[1]}) "
          f"for {width}x{height} from {args.ppm}")
    patched = imageenc.replace_image(data, args.index, width, height, pixels)

    # Prove the swap: it must decode back to the supplied pixels, and the structural gates that
    # guard every build must still pass (the image sits inside GuiData, so a bad write shows up
    # as an unclaimed byte or a boundary violation).
    if images.decode_image(patched, args.index).pixels != pixels:
        raise ValueError("internal error: swapped image did not decode back to the input")
    ok, why = relocate._boundary_oracle(patched)
    if not ok:
        raise ValueError(f"boundary oracle rejected the swap: {why}")
    unclaimed = coverage.analyse(patched, deep=True).unclaimed
    if unclaimed:
        raise ValueError(f"deep coverage found {len(unclaimed)} unclaimed byte run(s)")
    print("  verified: decodes back to input, boundary oracle + deep coverage pass")

    if not getattr(args, "no_mark", False):
        marked = patch.mark_custom(patched)
        if marked is not patched:
            patched = marked
            print(f"  build name marked: {patch.build_name(patched)!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patched)
    print(f"wrote {out}  ({len(patched)} bytes)")
    return 0


USER_OPS_MSG = ("USER_OPS: %s are user-written and have never been checked by anyone but their "
                "author. Nothing written. Rebuild with --allow-user-ops once the owner has been "
                "told what they change.")


def _user_op_rows(discovered) -> list[dict]:
    return [{"type": d.type, "folder": str(d.folder), "folder_sha256": d.folder_sha256}
            for d in discovered]


def _user_ops_refusal(project, discovered, allowed: bool):
    """(code, message) when the project's user ops may not run, else None. Read from discovery
    alone (manifests and folder hashes): nothing has been imported (R6)."""
    from . import userop
    if not discovered:
        return None
    if not allowed:
        return "USER_OPS", USER_OPS_MSG % ", ".join(d.type for d in discovered)
    g12 = userop.needs_g12_ack(project, discovered)
    if g12:
        types = ", ".join(d.type for d in g12)
        return "G12_ACK_REQUIRED", (
            f"G12_ACK_REQUIRED: {types} change the G12's configuration (what drives the pumps, "
            "slides, heaters and other loads), and the owner has not accepted this version of "
            "it. Nothing written. Once the owner says yes, run `fireflymcf op accept PROJECT "
            "TYPE --g12` and rebuild.")
    return None


def _frames_text(f: dict) -> str:
    """One `userop.routine_frames` entry in words."""
    kinds = {0xD1: "D1", 0xD2: "D2", 0xD3: "D3"}
    if f["templates"]:
        what = "OPTIONS config frames: " + ", ".join(
            "selector 0x%02X %s (%s)" % (t["selector"], t["kind"],
                                         kinds.get(t["function"], hex(t["function"])))
            for t in f["templates"])
    else:
        what = "frames through the host CAN sender (not decoded)"
    return f"sends frames from its tick routine {f['routine']!r}: {what}"


def _user_ops_sends_refusal(project, discovered, sends: dict):
    """(code, message) when a compiled user op changes what the panel sends (its declared
    `capability_delta`, the (channel, command) pairs its apply changed, or the frames its tick
    routines send: `recipe.CompileResult.sends`) and the owner has not accepted that folder with `--g12`: the
    G12 acts on what the panel sends, whatever the manifest's `touches` declares. Else None."""
    from . import userop
    by = {d.type: d for d in discovered}
    need = {}
    for e in sends.values():
        d = by.get(e["type"])
        if d is not None and (userop.acceptance(project, d) or {}).get("g12") is not True:
            pairs = need.setdefault(d.type, [])
            pairs += ["gains (%d, %d)" % tuple(x) for x in e["gained"]]
            pairs += ["loses (%d, %d)" % tuple(x) for x in e["lost"]]
            pairs += [_frames_text(f) for f in e.get("frames", ())]
    if not need:
        return None
    what = "; ".join(f"{t} {', '.join(p)}" for t, p in need.items())
    return "G12_ACK_REQUIRED", (
        f"G12_ACK_REQUIRED: {what}: what the panel sends to the G12, which "
        "the G12 acts on, and the owner has not accepted this version of it with --g12. Nothing "
        "written. Once the owner says yes, run `fireflymcf op accept PROJECT TYPE --g12` and "
        "rebuild.")


def _print_user_ops_refusal(code: str, msg: str, discovered, as_json: bool, shape: dict) -> int:
    if as_json:
        print(json.dumps(dict(shape, errors=[{"op_id": None, "code": code, "message": msg,
                                              "offers": []}],
                              user_ops=_user_op_rows(discovered)), indent=2))
    else:
        print(f"error: {msg}", file=sys.stderr)
        for d in discovered:
            print(f"  {d.type}  {d.folder}", file=sys.stderr)
    return 1


def _op_folder_of(path: Path) -> Path | None:
    """The op folder (`manifest.json` and `op.py`) that holds `path`, if any."""
    for d in Path(path).resolve().parents:
        if (d / "manifest.json").is_file() and (d / "op.py").is_file():
            return d
    return None


def _scenario_refusal(files, allowed: bool, as_json: bool, shape: dict) -> int | None:
    """Refuse (USER_OPS, exit 1) to run a scenario file from an op folder without
    `--allow-user-ops`: it is that op's author's code, as its `op.py` is. None when it may run.
    A scenario of your own outside any op folder runs like any script you start."""
    if allowed:
        return None
    held = [(Path(f), _op_folder_of(Path(f))) for f in files]
    held = [(f, d) for f, d in held if d is not None]
    if not held:
        return None
    msg = ("USER_OPS: %s %s in an op folder, the op author's code, never checked by anyone but "
           "them. Nothing run. Run it again with --allow-user-ops once you have read it."
           % (", ".join(str(f) for f, _ in held), "is" if len(held) == 1 else "are"))
    if as_json:
        print(json.dumps(dict(shape, errors=[{"op_id": None, "code": "USER_OPS",
                                              "message": msg, "offers": []}],
                              folders=[str(d) for _, d in held]), indent=2))
    else:
        print(f"error: {msg}", file=sys.stderr)
        for _, d in held:
            print(f"  {d}", file=sys.stderr)
    return 1


def _announce_user_ops(discovered, as_json: bool) -> None:
    """Say what runs before anything is imported: type, folder, folder hash."""
    if as_json or not discovered:
        return
    for d in discovered:
        print(f"user op {d.type} v{d.manifest.get('version', '?')} from {d.folder} "
              f"(sha256 {d.folder_sha256}): runs with full Python trust")


def _sidecar_user_ops(project, discovered) -> list[dict]:
    """The sidecar's record of the user ops this build ran (enabled in the project)."""
    from . import userop
    used = {s.type for s in project.ops if s.enabled}
    return [{"type": d.type, "version": d.manifest.get("version"),
             "folder_sha256": d.folder_sha256,
             "source": (project.user_op_sources.get(d.type) or {}).get("source"),
             "touches": d.touches, "accepted": userop.acceptance(project, d)}
            for d in discovered if d.type in used]


def _cmd_validate(args: argparse.Namespace) -> int:
    """Pre-flash gate: compile a *.ffproj.json through every universal gate and report PASS/FAIL.

    Read-only — writes no image. The stock is the corpus image the project's base_sha256 names;
    pass it explicitly, or omit it and let the repo's known stock images be searched by hash.
    Exit 0 on PASS, 1 on FAIL.
    """
    from . import recipe, userop, validate
    discovered = []
    try:
        project = recipe.Project.from_json(Path(args.project).read_text(encoding="utf-8"))
        discovered = userop.discover_used_ops(Path(args.project), project)
        refusal = _user_ops_refusal(project, discovered, args.allow_user_ops)
        if refusal:
            return _print_user_ops_refusal(*refusal, discovered, args.json,
                                           {"passed": False, "unverified": [], "report": ""})
        if args.stock:
            stock = Path(args.stock).read_bytes()
        else:
            stock = validate.resolve_stock(project, Path.cwd())
        _announce_user_ops(discovered, args.json)
        loaded = userop.import_user_ops(discovered)
    except recipe.RecipeError as exc:
        if args.json:
            print(json.dumps({"passed": False, "errors": [exc.to_json()],
                              "unverified": [], "report": ""}, indent=2))
            return 1
        print(f"FAIL: {exc.code}: {exc}", file=sys.stderr)
        return 1
    with userop.registered(loaded):
        res = validate.validate(project, stock, require_verified=args.require_verified)
    refusal = _user_ops_sends_refusal(project, discovered, res.sends) if res.passed else None
    if refusal:
        return _print_user_ops_refusal(*refusal, discovered, args.json,
                                       {"passed": False, "unverified": res.unverified,
                                        "report": res.report})
    if args.json:
        body = {"passed": res.passed, "errors": res.errors,
                "unverified": res.unverified, "report": res.report}
        if discovered:
            body["user_ops"] = _user_op_rows(discovered)
        print(json.dumps(body, indent=2, default=str))
        return 0 if res.passed else 1
    print(res.report, file=(sys.stdout if res.passed else sys.stderr))
    return 0 if res.passed else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    """Compare a build with its stock image, whole (`buildcheck.compare`): sections, every
    differing byte run by region (same-size builds), GuiData objects changed or added, and
    every page rendered from both under each `--config`. Exit 1 when something is wrong
    whatever was intended: a checksum that does not verify, a byte run outside every known
    region, a page that fails to render, or a build name equal to stock's."""
    import json
    from . import buildcheck
    import hashlib
    scenarios: dict = {}
    missing: dict = {}
    digests: dict = {}
    if args.project:
        if not args.emulate:
            print("error: --project names the user ops whose emu_test.py --emulate runs; "
                  "give --emulate too", file=sys.stderr)
            return 2
        from . import recipe, userop
        try:
            project = recipe.Project.from_json(Path(args.project).read_text(encoding="utf-8"))
            discovered = userop.discover_used_ops(Path(args.project), project)
        except recipe.RecipeError as exc:
            print(f"error: {exc.code}: {exc}", file=sys.stderr)
            return 1
        # an emu_test.py is the op author's code too: it runs only with the same authorisation
        if discovered and not args.allow_user_ops:
            return _print_user_ops_refusal(
                "USER_OPS", USER_OPS_MSG % ", ".join(d.type for d in discovered), discovered,
                args.json, {"ok": False})
        _announce_user_ops(discovered, args.json)
        try:
            snaps = userop.verified_snapshots(discovered)
        except recipe.RecipeError as exc:
            print(f"error: {exc.code}: {exc}", file=sys.stderr)
            return 1
        for d in discovered:
            body = snaps[d.type].get("emu_test.py")
            if body is not None:
                # the worker runs it only if it still holds the bytes the folder hash covered
                scenarios[d.type] = d.folder / "emu_test.py"
                digests[d.type] = hashlib.sha256(body).hexdigest()
            else:
                missing[d.type] = f"{d.type} has no emu_test.py in {d.folder}"
    refused = _scenario_refusal(args.scenario, args.allow_user_ops, args.json, {"ok": False})
    if refused is not None:
        return refused
    from .emu.gate import result_key          # light: stdlib only
    for f in args.scenario:
        scenarios[result_key(Path(f))] = Path(f)
    build, stock = Path(args.build).read_bytes(), Path(args.stock).read_bytes()
    configs = {Path(c).stem: options.CoachConfig.from_json(c) for c in args.config} \
        or {"no-floorplan": None}
    roots = [_resolve_page_root(stock, n) for n in args.page] + list(args.root) or None
    report = buildcheck.compare(stock, build, configs, roots, render_pages=not args.no_render)
    emu = None
    if args.emulate:
        emu = _emulate_scenarios(Path(args.build), scenarios, args.emulate_timeout, missing,
                                 digests)
        if emu is None:
            return 2
    if args.json:
        body = report.to_dict()
        if emu is not None:
            body["emulation"] = {k: r.to_dict() for k, r in emu.items()}
        print(json.dumps(body, indent=2))
    else:
        print(buildcheck.format_report(report))
        if emu is not None:
            from .emu import gate
            for line in gate.summary_lines(emu):
                print(line)
    bad = report.problems or report.unexplained or any(p.error for p in report.pages)
    return 1 if bad else 0           # a failing emulator test is reported, never an exit code


# The emulator is the `emu` extra (the plugin's hook installs it). Never a bare-name install: a
# PyPI package called fireflymcf would not be this project (tests/test_install_docs.py).
EMU_MISSING = ('emulate needs the emu extra: pip install '
               '"fireflymcf[full,emu] @ git+https://github.com/dbergl/fireflymcf.git"')


def _emulate_scenarios(build: Path, scenarios: dict, timeout_s: float, missing: dict | None = None,
                       digests: dict | None = None):
    """Run the scenario files ({sidecar key: file}) on `build` and record them in its sidecar,
    with a `not emulated` entry for each user op in `missing` (no emu_test.py); None (after
    saying why) when the request cannot be carried out."""
    import importlib
    missing = missing or {}
    if not scenarios and not missing:
        print("error: --emulate needs at least one --scenario FILE, or --project with user ops",
              file=sys.stderr)
        return None
    try:
        importlib.import_module("fireflymcf.emu.machine")
    except ImportError:
        print(EMU_MISSING, file=sys.stderr)
        return None
    from .emu import gate
    results = gate.run_scenarios(build, scenarios, timeout_s, digests) if scenarios else {}
    results.update({k: gate.EmuResult("not emulated", why) for k, why in missing.items()})
    gate.record(build, results)
    return results


def _cmd_coach_config(args: argparse.Namespace) -> int:
    """Write the coach configuration (`options.CoachConfig` JSON) that `render --config` and
    `verify --config` take: the floorplan and fitted options that decide what each page draws.

    From a `candump -L` capture (`--capture`), it is the coach's own broadcast state
    (`livecfg`, DGN 0x15FCE); otherwise it is what `--floorplan` and `--option` name, checked
    against this build's floorplan codes and OPTIONS rows so a typo cannot render the wrong
    screen silently."""
    from . import livecfg
    data = Path(args.image).read_bytes()
    floorplans = options.floorplan_options(data)
    names = sorted({livecfg.canonical_option(b.name) for b in options.option_bindings(data)})
    if args.capture:
        live = livecfg.decode_capture(Path(args.capture).read_text(encoding="utf-8", errors="replace"))
        if not live.frames:
            raise ValueError(f"{args.capture} holds no configuration frames (DGN 0x15FCE); "
                             "capture at least 10 s of the RV-C bus with `candump -L`")
        body = livecfg.to_preview(data, live)
        if "floorplan" not in body:
            raise ValueError("the capture's floorplan value is not one this build names")
    else:
        if args.floorplan is None:
            raise ValueError("name --floorplan (one of %s) or give --capture"
                             % ", ".join(floorplans.values()))
        if args.floorplan not in floorplans.values():
            raise ValueError("this build has no floorplan %r; it has %s"
                             % (args.floorplan, ", ".join(floorplans.values())))
        unknown = [o for o in args.option if o not in names]
        if unknown:
            raise ValueError("this build has no option %s; it has %s"
                             % (", ".join(map(repr, unknown)), ", ".join(names)))
        body = {"floorplan": args.floorplan, "options": list(args.option)}
    _check_writable(Path(args.out), Path(args.image))
    Path(args.out).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(dict(body, ok=True, out=args.out), indent=2))
    else:
        print(f"wrote {args.out}: floorplan {body['floorplan']}, options "
              f"{', '.join(body['options']) or 'none'}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    """Print the JSON Schema of a `*.ffproj.json` project (`ops_doc.project_schema`)."""
    from . import ops_doc
    print(json.dumps(ops_doc.project_schema(), indent=2))
    return 0


def _cmd_ops(args: argparse.Namespace) -> int:
    """Print the recipe-op vocabulary from the installed package (`ops_doc.entries`).

    With no name, one line per op: name, edit class, whether its mechanism has run on a coach,
    and its purpose. With names, each op in full: status, parameters, error codes. `--json`
    emits the same data. This is `docs/RECIPE-OPS.md` for a checkout without the docs."""
    import json
    import textwrap
    from . import ops_doc
    ops = ops_doc.entries()
    user = []
    if args.project:
        from . import recipe, userop
        try:
            project = recipe.Project.from_json(Path(args.project).read_text(encoding="utf-8"))
            user = [_user_op_entry(d, userop.acceptance(project, d))
                    for d in userop.discover_user_ops(Path(args.project), project)]
        except recipe.RecipeError as exc:
            print(f"error: {exc.code}: {exc}", file=sys.stderr)
            return 1
    if args.names:
        by = {e["name"]: e for e in ops + user}
        unknown = [n for n in args.names if n not in by]
        if unknown:
            hint = "" if args.project else "; user ops need --project P"
            print(f"error: no such op: {', '.join(unknown)} (`fireflymcf ops` lists them{hint})",
                  file=sys.stderr)
            return 1
        ops = [by[n] for n in args.names]
    elif user:
        ops = ops + user
    if args.json:
        print(json.dumps(ops, indent=2))
        return 0
    if not args.names:
        builtin = [e for e in ops if not e.get("user_op")]
        w = max(len(e["name"]) for e in builtin)
        print(f"{'op':<{w}}  {'class':<10}  verified  purpose")
        for e in builtin:
            purpose = e["purpose"].split(". ")[0].rstrip(".") + "."
            print(f"{e['name']:<{w}}  {e['class']:<10}  {'yes' if e['verified'] else 'NO':<8}  "
                  f"{textwrap.shorten(purpose, 100)}")
        if args.project:
            print("\nUser ops (this project): user-written, checked by no one but their author")
            for e in user:
                print(f"  {e['name']}  v{e['manifest'].get('version', '?')}  touches "
                      f"{', '.join(e['manifest'].get('touches', [])) or 'nothing'}  "
                      f"{textwrap.shorten(e['purpose'], 80)}")
                print(f"    {e['folder']}  (sha256 {e['folder_sha256'][:12]}...)")
                print(f"    {_accepted_text(e['accepted'])}")
            if not user:
                print("  none")
        print("\n`fireflymcf ops NAME` for parameters and error codes.")
        return 0
    for e in ops:
        if e.get("user_op"):
            print(f"{e['name']}  (user op; verified: NO)")
            print(f"  folder: {e['folder']}  (sha256 {e['folder_sha256']})")
            print(f"  {_accepted_text(e['accepted'])}")
            print("  manifest.json:")
            print(textwrap.indent(json.dumps(e["manifest"], indent=2), "    "))
            print("  README.md:" if e["readme"] is not None else "  README.md: none")
            if e["readme"] is not None:
                print(textwrap.indent(e["readme"].rstrip(), "    "))
            print()
            continue
        # Per firmware, as ops_doc's `verified_on` (empty when hardware_verified is False).
        print(f"{e['name']}  ({e['class']}; verified: "
              f"{', '.join(e['verified_on']) or 'none'})")
        print(textwrap.fill(e["purpose"], 96, initial_indent="  ", subsequent_indent="  "))
        print("  firmware: " + ops_doc._firmware_cell(e))
        print(textwrap.fill("status: " + e["status"], 96, initial_indent="  ",
                            subsequent_indent="    "))
        props = e["params"].get("properties", {})
        required = set(e["params"].get("required", []))
        print("  params:" if props else "  params: none")
        for name, sch in props.items():
            head = f"    {name}: {ops_doc._type_text(sch)}"
            head += " (required)" if name in required else (
                f" = {json.dumps(sch['default'])}" if "default" in sch else "")
            print(head)
            if sch.get("description"):
                print(textwrap.fill(sch["description"], 96, initial_indent="        ",
                                    subsequent_indent="        "))
        if e["errors"]:
            print("  errors:")
            for err in e["errors"]:
                print(textwrap.fill(f"{err['code']}: {err['meaning']}", 96,
                                    initial_indent="    ", subsequent_indent="        "))
        print()
    return 0


def _accepted_text(rec) -> str:
    """The project file's acceptance record for this folder, as `ops --project` prints it. It is
    whoever wrote that file's yes: a project received from someone else carries theirs."""
    if not rec:
        return "not accepted in this project file"
    return (f"accepted {rec.get('date', '?')}{' (G12 included)' if rec.get('g12') else ''} "
            "in this project file: if you did not accept it yourself, it is not your yes")


def _user_op_entry(d, accepted=None) -> dict:
    """A discovered user op in `ops_doc.entries` shape, plus its manifest, README, folder and the
    project file's acceptance record for its current hash (`accepted`, None if none)."""
    readme = d.folder / "README.md"
    return {"name": d.type, "class": "user", "verified": False,
            "purpose": d.manifest.get("description", ""),
            "status": "user-written: never checked by anyone but its author",
            "params": {}, "errors": [], "user_op": True, "folder": str(d.folder),
            "folder_sha256": d.folder_sha256, "manifest": d.manifest, "accepted": accepted,
            "readme": readme.read_text(encoding="utf-8", errors="replace")
            if readme.is_file() else None}


def _cmd_op(args: argparse.Namespace) -> int:
    """`fireflymcf op install|pack|accept`: share and accept user ops (`opshare`). None of them
    imports or runs an op."""
    import shutil
    import zipfile
    import zlib
    from . import opshare, recipe
    try:
        if args.op_command == "install":
            res = opshare.install(Path(args.project), args.src, replace=args.replace)
            if args.json:
                print(json.dumps(dict(res.to_dict(), ok=True), indent=2))
                return 0
            print(f"installed {res.type} v{res.manifest.get('version', '?')} into {res.folder} "
                  f"(sha256 {res.folder_sha256})")
            print("manifest.json:")
            print("  " + json.dumps(res.manifest, indent=2).replace("\n", "\n  "))
            print("files (read every one before building with it: op.py runs with full Python "
                  "trust when a build passes --allow-user-ops):")
            for f in res.files:
                print(f"  {res.folder / f}")
            return 0
        if args.op_command == "pack":
            res = opshare.pack(Path(args.project), args.name, Path(args.out))
            if args.json:
                print(json.dumps(dict(res, ok=True), indent=2))
            else:
                print(f"wrote {res['out']}: {res['type']} v{res['version']}, "
                      f"{len(res['files'])} file(s), folder_sha256 {res['folder_sha256']}")
            return 0
        rec = opshare.accept(Path(args.project), args.type, g12=args.g12)
        if args.json:
            print(json.dumps({"ok": True, "type": args.type, "accepted": rec}, indent=2))
        else:
            print(f"accepted {args.type} at sha256 {rec['folder_sha256']}"
                  f"{' (G12 changes included)' if rec['g12'] else ''} on {rec['date']}; "
                  "an edit to its folder needs a new acceptance")
        return 0
    except (OSError, zipfile.BadZipFile, shutil.Error, zlib.error) as e:
        # a file the command reads or writes failed (a path, a permission, a corrupt member)
        err = recipe.RecipeError("OP_IO_ERROR", f"{type(e).__name__}: {e}")
    except recipe.RecipeError as e:
        err = e
    if args.json:
        print(json.dumps({"ok": False, "errors": [err.to_json()]}, indent=2))
    else:
        print(f"error: {err.code}: {err}", file=sys.stderr)
    return 1


def _cmd_compile(args: argparse.Namespace) -> int:
    """Build a project file (`*.ffproj.json`) against its stock image and write the result.

    The same engine Studio's export uses (`recipe.compile_project`): every op applies in
    order, the container is resealed, the build name stamped, the structural and capability
    gates run, and every op verifies itself against the finished image. Nothing is written
    unless all of that passes. Exit 0 on success, 1 on a compile error (printed as the
    engine's structured error), 2 on a refused write.
    """
    import hashlib
    from . import recipe, userop
    out, stock_path = Path(args.out), Path(args.stock)
    if not out.name.startswith("mcf-") or not out.name.endswith(".bin"):
        raise ValueError(f"refusing to write {out}: the bootloader lists only mcf-*.bin files")
    _check_writable(out, stock_path)
    discovered = []
    try:
        project = recipe.Project.from_json(Path(args.project).read_text(encoding="utf-8"))
        # R6: discovery reads manifests only; nothing is imported until the owner has authorised it
        discovered = userop.discover_used_ops(Path(args.project), project)
        refusal = _user_ops_refusal(project, discovered, args.allow_user_ops)
        if refusal:
            return _print_user_ops_refusal(*refusal, discovered, args.json, {"ok": False})
        stock = stock_path.read_bytes()
        _announce_user_ops(discovered, args.json)
        loaded = userop.import_user_ops(discovered)
        with userop.registered(loaded):
            result = recipe.compile_project(project, stock)
    except recipe.RecipeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "errors": [exc.to_json()]}, indent=2))
            return 1
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 1
    if not result.ok and args.json:
        print(json.dumps({"ok": False, "errors": result.errors}, indent=2, default=str))
        return 1
    if not result.ok:
        for err in result.errors:
            print(f"error: {err.get('code')}"
                  f"{' (op ' + str(err['op_id']) + ')' if err.get('op_id') else ''}: "
                  f"{err.get('message')}", file=sys.stderr)
            for fix in err.get("offers") or []:
                print(f"  offer: {fix}", file=sys.stderr)
        return 1
    refusal = _user_ops_sends_refusal(project, discovered, result.sends)
    if refusal:
        return _print_user_ops_refusal(*refusal, discovered, args.json, {"ok": False})
    # A user op is unverified by definition and admitted by --allow-user-ops alone; this gate is
    # the built-in ops' (one flag never implies the other).
    user_types = {d.type for d in discovered}
    builtin_unverified = [i for i in result.unverified
                          if next((s.type for s in project.ops if s.id == i), None)
                          not in user_types]
    if builtin_unverified and not args.allow_unverified:
        msg = ("UNVERIFIED: op(s) %s have never run on a coach on firmware %s. Nothing "
               "written. If the owner agrees to be the first, rebuild with --allow-unverified "
               "and tell them which (`fireflymcf ops NAME` has each op's status)."
               % (", ".join(builtin_unverified), result.firmware))
        if args.json:
            print(json.dumps({"ok": False, "unverified": builtin_unverified,
                              "errors": [{"op_id": None, "code": "UNVERIFIED_OPS",
                                          "message": msg, "offers": []}]}, indent=2))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 1
    data = result.data
    if not args.json:
        print(f"compiled {len(project.ops)} op(s) ({sum(o.enabled for o in project.ops)} "
              f"enabled) from {stock_path}")
        for line in result.manifest:
            print(f"  {line}")
        if result.unverified:
            print("  UNVERIFIED on hardware: " + ", ".join(result.unverified))
    sha = hashlib.sha256(data).hexdigest()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    sidecar = {"sha256": sha, "size": len(data), "build_name": patch.build_name(data),
               "project": str(args.project), "stock_sha256": project.base_sha256,
               "manifest": result.manifest, "unverified": result.unverified,
               "shifts": result.shifts, "placement": result.placement}
    if discovered:
        sidecar["user_ops"] = _sidecar_user_ops(project, discovered)
    Path(str(out) + ".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(dict(sidecar, ok=True, out=str(out)), indent=2))
        return 0
    print(f"wrote {out}  ({len(data)} bytes, sha256 {sha[:12]}...)  build name "
          f"{patch.build_name(data)!r}")
    return 0


def _cmd_glyph_export(args: argparse.Namespace) -> int:
    """Export one glyph as `install_glyph` parameters -- JSON a project file can carry.

    The pixels alone are not enough. A glyph record also holds 10 ink-profile bytes the text
    measurer kerns against and a flag bit in the RLE byte, and neither is derivable from the
    bitmap (`GUICODE.md` Finding 46), so both travel with the pixels here. Re-encoding what this
    prints reproduces the source record byte for byte.
    """
    from . import glyphenc
    data = Path(args.image).read_bytes()
    spec = glyphenc.export_glyph(data, args.slot, args.char)
    if args.as_op:
        spec = {"id": args.id or ("glyph-%d-%s" % (args.slot, args.char)),
                "type": "install_glyph", "params": spec}
    text = json.dumps(spec, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("wrote %s (%d rows, %dx%d)"
              % (args.out, len(spec.get("params", spec)["rows"]),
                 len(spec.get("params", spec)["rows"][0]),
                 len(spec.get("params", spec)["rows"])))
    else:
        print(text)
    return 0


def _cmd_glyph_import(args: argparse.Namespace) -> int:
    """Turn an owner's artwork into `install_glyph` parameters -- `glyph-export`'s inverse.

    The params are compiled against the image before they are printed, so what this writes is
    what `install_glyph` accepts: the slot, a free character, the conventional y-bearing.
    """
    from . import glyphimport
    data = Path(args.image).read_bytes()
    if args.list:
        print("slot  cell  chars")
        for f in glyphimport.font_slots(data):
            print("%4d  %4d  %r..%r" % (f["slot"], f["cell"], f["first"], f["last"]))
        return 0
    if not args.artwork or args.slot is None:
        print("error: glyph-import needs ARTWORK and --slot (or --list)", file=sys.stderr)
        return 2
    try:
        spec, rule = glyphimport.import_glyph(
            data, args.artwork, args.slot, args.char, ink=args.ink, height=args.height,
            width=args.width, trim=not args.no_trim, claim_block=args.claim_block)
    except glyphimport.ArtworkError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    rows = spec["rows"]
    summary = ("%dx%d glyph from %s (%s coverage), slot %d char %r, y %d"
               % (len(rows[0]), len(rows), Path(args.artwork).name, rule, spec["slot"],
                  spec["char"], spec["y"]))
    if args.preview:
        glyphimport.preview(rows, args.preview)
    if args.as_op:
        spec = {"id": args.id or ("glyph-%d-%s" % (spec["slot"], spec["char"])),
                "type": "install_glyph", "params": spec}
    text = json.dumps(spec, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("wrote %s: %s" % (args.out, summary))
    else:
        print(text)
        print(summary, file=sys.stderr)
    if args.preview:
        print("preview: %s" % args.preview, file=sys.stderr)
    return 0


def _cmd_g12_rules(args: argparse.Namespace) -> int:
    """Show how the G12 turns a stored config selector into an RV-C command broadcast.

    The panel never addresses the inverter/charger; it writes a selector and the G12 relays
    (`BUS-BASELINE.md` PC.23). An empty result for a selector is a real answer, not a failure --
    `0x99` (Shore Brkr Size) is stored by the G12 and forwarded to nobody.
    """
    from . import g12rules
    data = Path(args.image).read_bytes()
    if args.selector is None:
        print(g12rules.describe(data))
        return 0
    rules = g12rules.rule_for_selector(data, args.selector)
    if not rules:
        print("selector %#04x drives no G12 command rule "
              "(the G12 stores it but never relays it)" % args.selector)
        return 0
    for rule in rules:
        print(rule)
    return 0


def _cmd_g12_inputs(args: argparse.Namespace) -> int:
    """Show how the G12's INPUT pins reach the DEVICES/FAULTS screen.

    Three independently-derived columns: the G12's transmit rule (table 0x20), the panel's
    receive-filter var, and the screen row that paints it (`GUICODE.md` Finding 54). A pin the
    panel watches but the G12 never broadcasts -- 5 and 8 on 13.6 -- is a real answer.
    """
    from . import g12rules
    data = Path(args.image).read_bytes()
    if args.json:
        payload = []
        for st in g12rules.input_status(data):
            on, off = st.frames()
            payload.append({"pin": st.pin, "instance": st.instance, "var": st.var,
                            "slot": st.slot, "label": st.label,
                            "broadcast": st.broadcast, "watched": st.watched,
                            "frame_active": on, "frame_inactive": off})
        print(json.dumps(payload, indent=2))
        return 0
    print(g12rules.describe_inputs(data))
    return 0


def _cmd_slot_source(args) -> int:
    """Report how widget value slots are driven.

    The interesting output is the `unreferenced` class: slots whose SRAM address no GuiCode
    instruction reaches, so this project cannot say what sets them (`GUICODE.md` Finding 63).
    """
    from . import slotsource

    data = Path(args.image).read_bytes()
    if args.slot is not None:
        entry = slotsource.slot_source(data, args.slot)
        if entry is None:
            print(f"slot {args.slot}: not a GuiCode-RAM slot")
            return 1
        rows = [entry]
    elif args.unexplained:
        rows = slotsource.unexplained(data)
    else:
        rows = slotsource.slot_sources(data)

    if args.json:
        print(json.dumps([{"slot": e.slot, "address": e.address, "type": e.vtype,
                           "kind": e.kind,
                           "reads": [hex(a) for a in e.reads],
                           "writes": [hex(a) for a in e.writes]} for e in rows], indent=2))
        return 0

    counts = {}
    for e in slotsource.slot_sources(data):
        counts[e.kind] = counts.get(e.kind, 0) + 1
    print(f"{'slot':>5} {'address':>12} {'type':>4}  {'kind':<13} sites")
    # the table shows a slot's first three sites; `--slot` shows every one, the list a
    # `cell_slot` readout's `silence` is chosen from (`add_register_readout`)
    keep = None if args.slot is not None else 3
    for e in rows:
        sites = ""
        if e.writes:
            sites = "w " + ",".join(hex(a) for a in e.writes[:keep])
        elif e.reads:
            sites = "r " + ",".join(hex(a) for a in e.reads[:keep])
        print(f"{e.slot:>5} {e.address:#012x} {e.vtype:>4}  {e.kind:<13} {sites}")
    print("\ntotals: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def _cmd_pins(args: argparse.Namespace) -> int:
    """Report, per G12 output, whether its live status can drive a widget and at what cost.

    The `use` column is the actionable one (`MCF-FORMAT.md` §5.4c):
      * a number  -- ready now: point a box record's fillA index AND value_slot at it;
      * `+index`  -- bridged but its colour cell has no table entry; `statusbind.bind_pin`
                     spends one spare `+0x44BC8` index (4-byte edit) to give it one;
      * `-`       -- no GuiCode block decodes this pin's status; only a filter retarget
                     (`set_filter_instance`, which costs another pin its watcher) can help.
    """
    from . import outputs, statusbind, theme
    data = Path(args.image).read_bytes()
    bindings = statusbind.pin_bindings(data)
    rest = theme.resting_defaults(data)
    try:
        page_names = outputs.output_name_variants(data)
    except ValueError:                  # no G12 OUTPUTS page on this build
        page_names = {}
    captions = outputs.control_captions(data)
    pins = sorted(p for p in bindings
                  if statusbind.PIN_MIN <= p <= statusbind.PIN_MAX
                  and (args.all or not bindings[p].commanded))

    rows = []
    for p in pins:
        b = bindings[p]
        try:
            loads = statusbind.state_loads(data, b.var) if b.bridged else {}
        except (statusbind.StatusBindError, statusbind.BridgeUnknown):
            loads = {}
        off = loads.get(statusbind.OFF_STATE)
        on = loads.get(statusbind.ON_STATE)
        rows.append({
            "pin": p, "var": b.var, "commanded": b.commanded, "bridged": b.bridged,
            "colour_index": b.colour_index, "bool_index": b.bool_index,
            "use": (b.colour_index if b.usable else ("+index" if b.needs_index else None)),
            "off_rgb": (off.rgb if off else (rest.get(b.colour_index)
                                             if b.colour_index is not None else None)),
            "on_rgb": on.rgb if on else None,
            "consumers": b.colour_consumers,
            # What the panel itself calls the pin: the G12 OUTPUTS page row (every variant,
            # with the floorplans/options that show it) and, separately, the captions of the
            # controls joined to the pin (inferred-from-data; `outputs` module docstring).
            "outputs_page_names": [
                {"name": n.name, "kind": n.kind, "when": n.when,
                 "conditions": outputs.as_json(n.condition), "source": n.source}
                for n in page_names.get(p, [])],
            "captions": [
                {"text": c.text, "via": list(c.via), "when": c.when,
                 "conditions": outputs.as_json(c.condition),
                 "objects": [o for o, _off in c.objects],
                 "other_gates": list(c.other_gates), "source": c.source}
                for c in captions.get(p, [])],
        })

    # A built image's cell table was grown, so which rows are stock spares is unknown
    # (SPARE_ROWS_UNKNOWN); that is reported, and the pins are still listed.
    try:
        spares, spares_unknown = statusbind.spare_cell_indices(data), None
    except statusbind.SpareRowsUnknown as e:
        spares, spares_unknown = None, (f"{e.code}: the cell table was grown; which rows are "
                                        "stock spares is unknown")
    from . import celltables
    try:
        spare_label = f"spare +0x{celltables.locate(data).cell_off:X} indices"
    except (guidata.VariableTableNotFound, ValueError):
        spare_label = "spare display-cell indices"
    if args.json:
        out = {"pins": rows, "spare_cell_indices": spares}
        if spares_unknown is not None:
            out["spare_cell_indices_unknown"] = spares_unknown
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'pin':>4} {'var':>4} {'use':>7} {'bool':>5} {'off colour':>15} "
          f"{'on colour':>14}  {'OUTPUTS-page name':<34} consumers")
    for r in rows:
        use = "-" if r["use"] is None else str(r["use"])
        flag = " *" if r["commanded"] else ""
        names = " / ".join((n["name"] if n["name"] is not None else "(run time)")
                           + ("" if n["when"] == "always" else "+")
                           for n in r["outputs_page_names"]) or "-"
        print(f"{r['pin']:>4} {r['var']:>4} {use:>7} {str(r['bool_index']):>5} "
              f"{str(r['off_rgb']):>15} {str(r['on_rgb']):>14}  {names:<34} "
              f"{', '.join(map(str, r['consumers'])) or '-'}{flag}")
        if args.names:
            for n in r["outputs_page_names"]:
                print(f"{'':>10}OUTPUTS  {n['name']!r}: {n['when']}")
            for c in r["captions"]:
                print(f"{'':>10}caption  {c['text']!r} ({'+'.join(c['via'])}, objects "
                      f"{', '.join(map(str, c['objects']))}): {c['when']}")
    print(f"\n{spare_label}: "
          f"{spares if spares_unknown is None else f'unknown ({spares_unknown})'}")
    print("OUTPUTS-page name: the G12 OUTPUTS row's names; + = shown only under some floorplan "
          "or option (--names lists the conditions and the control captions)")
    if args.all:
        print("* = already commanded by a located control")
    return 0


def _parse_dgn(text: str) -> int:
    """`1FFB7`, `0x1FFB7` or `1FFB7h` -> 0x1FFB7 (DGNs are written in hex)."""
    t = text.strip().lower().removesuffix("h")
    return int(t, 16)


def _cmd_bus_map(args: argparse.Namespace) -> int:
    """The bus map: every receive-filter entry, what the panel does with it and what the panel
    calls it (`busmap.receive_map`), then every way the panel transmits (`busmap.send_map`)."""
    from . import busmap, sendmap
    data = Path(args.image).read_bytes()
    dgns = frozenset(_parse_dgn(d) for d in args.dgn) if args.dgn else None
    entries = busmap.receive_map(data, dgns)
    sends = busmap.send_map(data, dgns)
    # A pin unnamed because the status join is unavailable is not one the build never names
    from . import outputs
    join = outputs.status_join_refusal(data) \
        if any(e.dgn == busmap.DGN_DC_DIMMER_STATUS_3 for e in entries) else None
    if args.json:
        cov = busmap.coverage(entries)
        payload = {
            "receive": [busmap.as_json(e) for e in entries],
            "coverage": {"%05X" % d: dict(c) for d, c in sorted(cov.items())},
            "send": sendmap.as_json(sends),
        }
        if join:                                     # present only when the join refused
            payload["status_join_unavailable"] = join
        print(json.dumps(payload, indent=2))
        return 0
    cols = ("entries", "named", "drawn", "drawn-unnamed", "config", "logic-only", "write-only",
            "untraced", "inert")
    cov = busmap.coverage(entries)
    if join:
        print("status join unavailable (no caption is joined to a pin by its status cell): "
              + join)
    if not args.name:
        print("RECEIVE -- %d receive-filter entries, %d DGNs" % (len(entries), len(cov)))
        print("%-6s %s  %s" % ("DGN", " ".join("%7s" % c[:7] for c in cols), "specification name"))
        total = {c: 0 for c in cols}
        for dgn, c in sorted(cov.items(), key=lambda kv: (-kv[1]["entries"], kv[0])):
            print("%05X  %s  %s" % (dgn, " ".join("%7d" % c[k] for k in cols),
                                    busmap.spec_name(dgn) or "-"))
            for k in cols:
                total[k] += c[k]
        print("%-6s %s" % ("total", " ".join("%7d" % total[k] for k in cols)))
        print()
    rows = bool(args.all or args.dgn or args.detail or args.name)
    if not rows:
        _print_send_map(sends, detail=False, rows=False)
        print()
        print("This is the summary. --name TEXT lists what the panel calls TEXT (e.g. --name "
              "'water pump'), received and sent; --dgn X one DGN in full; --all every entry; "
              "--json all of it.")
        return 0
    wanted = (lambda texts: _name_match(args.name, texts)) if args.name else (lambda texts: True)
    if args.name:
        print("RECEIVE -- what the panel calls %r" % args.name)
    print("%4s %-5s %-6s %-10s %-34s %s" % ("var", "DGN", "filter", "class", "panel name (source)",
                                           "evidence"))
    for e in entries:
        if not wanted([n.text for n in e.names]):
            continue
        filt = "-" if e.filter_value is None else (
            "%#04x" % e.filter_value if e.filter_byte == 0 and e.filter_mask == 0xFF
            else "b%d&%#04x=%#04x" % (e.filter_byte, e.filter_mask, e.filter_value))
        name = "-" if not e.names else "%s (%s)" % (e.name, e.names[0].source)
        print("%4d %05X %-6s %-10s %-34s %s" % (e.var, e.dgn, filt, e.cls, name, e.evidence))
        if args.dgn or args.detail:
            if e.frame_parsers:
                print("%16sframe parser(s) compare this DGN: %s"
                      % ("", ", ".join("%#x" % f for f in e.frame_parsers)))
            groups: dict = {}
            for x in e.effects:
                key = (x.hop, x.via[:1], busmap._sink_text(x))
                groups.setdefault(key, []).append(x)
            for (hop, via, what), xs in groups.items():
                sites = sorted({x.sink.at for x in xs})
                flows = "/".join(sorted({x.sink.flow for x in xs}))
                checks = sorted({x.selector_check for x in xs if x.selector_check})
                print("%16s%s%s @%s (%s)%s" % (
                    "", "via cell %#x -> " % via[0] if hop else "", what,
                    ",".join("%#x" % a for a in sites), flows,
                    "; " + "; ".join(checks) if checks else ""))
                widgets = list({(w.obj, w.record): w for x in xs for w in x.widgets}.values())
                shown = widgets if args.dgn else widgets[:6]
                for w in shown:
                    print("%20sobject %d record %#x wt-%d %s: %s%s%s" % (
                        "", w.obj, w.record, w.wtype, w.role,
                        repr(w.caption) + " (" + w.rule + ")" if w.caption else
                        "no caption" + (" [in %r]" % w.title if w.title else ""),
                        " unit %r" % w.unit if w.unit else "",
                        " candidates %s" % list(w.candidates) if w.candidates else ""))
                if len(widgets) > len(shown):
                    print("%20s... %d more widgets (--dgn lists all)" % ("", len(widgets) - len(shown)))
    print()
    print("panel name: the firmware's own word (outputs-page / option: proven-from-code; captions: "
          "inferred-from-data). The specification name is the RV-C/J1939 standard's and may not "
          "match what Firefly calls the message. --dgn X (repeatable) or --detail lists every "
          "sink and widget.")
    print()
    _print_send_map(sends, detail=bool(args.dgn or args.detail), name=args.name)
    return 0


def _name_match(name: str | None, texts) -> bool:
    """Whether `name` (case and spacing ignored) occurs in any of `texts`; no name matches all."""
    if not name:
        return True
    key = " ".join(name.lower().split())
    return any(key in " ".join(str(t).lower().split()) for t in texts if t)


def _ctl_text(controls, limit: int = 3) -> str:
    """`'Water Pump' (HOME)` for the attached controls first; a title in brackets when the
    control itself carries no caption."""
    seen: dict = {}
    for c in sorted(controls, key=lambda c: (not c.attached, not c.pages, c.obj)):
        key = c.caption or ("[in %r]" % c.title if c.title else "object %d" % c.obj)
        if c.caption and c.title and c.title != c.caption and c.rule == "control":
            key = "%s [in %r]" % (c.caption, c.title)
        seen.setdefault(key, set()).update(c.pages if c.attached else ("unattached",))
    items = ["%s%s" % (k if k.startswith(("[", "object")) else repr(k),
                       " (%s)" % ", ".join(sorted(v)) if v else "")
             for k, v in list(seen.items())[:limit]]
    more = len(seen) - limit
    return "; ".join(items) + (" +%d more" % more if more > 0 else "") if items else "-"


def _print_send_map(m, detail: bool, rows: bool = True, name: str | None = None) -> None:
    from . import sendmap
    cov = sendmap.coverage(m)
    if name:
        print("SEND -- what the panel calls %r" % name)
    else:
        cols = ("bindings", "reached", "unattached", "dead", "named", "guicode", "touch",
                "touch-test", "config-set", "other", "guicode-named")
        nb = sum(1 for g in m.groups for e in g.entries if not e.stub)
        print("SEND -- %d bindings in %d groups, %d GuiCode send sites, %d config-set calls"
              % (nb, len(m.groups), len(m.guicode), len(m.config_calls)))
        if m.lcd is not None:
            sender_fn = dict(m.lcd.callers).get(m.lcd.binding_send)
            others = [a for a, f in m.lcd.callers if f != sender_fn]
            print("  every frame goes through the LCD transmit ring %#x: bindings from %#x (payload "
                  "copy %#x), GuiCode through its sender slot %s (API-table word %s); %d other LCD "
                  "call sites are the LCD firmware's own transmits, not decoded here (%s)"
                  % (m.lcd.transmit, m.lcd.binding_send, m.lcd.payload_copy,
                     "%#x" % m.slot if m.slot else "?",
                     ", ".join("%#x" % p for p in m.lcd.pointers) or "?", len(others),
                     ", ".join("%#x" % a for a in others)))
        short = {"bindings": "bind", "reached": "reach", "unattached": "unatt", "dead": "dead",
                 "named": "named", "guicode": "gcode", "touch": "touch", "touch-test": "test",
                 "config-set": "cfgset", "other": "other", "guicode-named": "gnamed"}
        print("%-6s %s  %s" % ("DGN", " ".join("%6s" % short[c] for c in cols), "specification name"))
        total = {c: 0 for c in cols}
        for dgn, c in sorted(cov.items(), key=lambda kv: (-(kv[1]["bindings"] + kv[1]["guicode"]),
                                                          kv[0])):
            print("%-6s %s  %s" % ("%05X" % dgn if dgn >= 0 else "?", " ".join("%6d" % c[k] for k in cols),
                                   sendmap.spec_name(dgn if dgn >= 0 else None) or "-"))
            for k in cols:
                total[k] += c[k]
        print("%-6s %s" % ("total", " ".join("%6d" % total[k] for k in cols)))
    if not rows:
        return
    print()
    print("Binding groups -- a touch region whose id is the group number sends the group's "
          "entries (LCD FUN_0021B8B4)")
    print("%4s %-10s %-50s %s" % ("grp", "class", "control (pages)", "what it sends"))
    for g in m.groups:
        if not _name_match(name, [t for c in g.controls for t in (c.caption, c.title)]
                           + [n.text for e in g.entries for n in e.names]):
            continue
        live = [e for e in g.entries if not e.stub]
        what = Counter()
        for e in live:
            what["%05X %s" % (e.dgn, e.describe().split(" level")[0] if e.dgn == 0x1FEDB
                               else e.describe())] += 1
        summary = "; ".join(("%s x%d" % (k, n)) if n > 1 else k for k, n in what.items())
        flag = (" [GuiCode sets the variant]" if g.guicode_variant
                else " [GuiCode variant unknown]" if g.guicode_variant is None else "")
        print("%4d %-10s %-50s %s%s" % (g.group, g.cls, _ctl_text(g.controls)[:50],
                                        summary[:160] if not detail else "", flag))
        if detail:
            for e in g.entries:
                if e.stub:
                    print("%20sbinding %d: ff ff stub" % ("", e.index))
                    continue
                names = ", ".join("%r (%s)" % (n.text, n.source) for n in e.names[:4])
                print("%20sbinding %d v%d %s%s %05X %s%s%s" % (
                    "", e.index, e.variant, "+".join(e.phases) or "-",
                    " repeat" if e.repeats else "", e.dgn, e.describe(),
                    " from SA 0x00" if e.source_zero else "", " -- " + names if names else ""))
    print()
    print("GuiCode sends -- `blx` through the sender slot, by what drives them")
    rows: dict = {}
    for s in m.guicode:
        if not _name_match(name, [t for c in s.controls for t in (c.caption, c.title)]
                           + [n.text for n in s.names]):
            continue
        key = (s.context, s.touch_ids if s.context in ("touch", "touch-test") else s.function)
        rows.setdefault(key, []).append(s)
    for (ctx, who), ss in rows.items():
        if ctx in ("touch", "touch-test"):
            head = ", ".join("%#x %s" % ie for ie in who)
        else:
            head = "function %#x" % who
        ctl = _ctl_text(ss[0].controls)
        frames = Counter()
        for s in ss:
            f = s.frame
            d = "%05X" % f.dgn if f.dgn is not None else f.dgn_expr
            frames["%s %s" % (d, f.reading())] += 1
        print("  %-10s %-26s %s" % (ctx, head[:26], ctl if ctl != "-" else ""))
        if detail:
            for s in ss:
                f = s.frame
                names = ", ".join("%r (%s)" % (n.text, n.source) for n in s.names
                                  if n.source != "control")
                print("%16s@%#x %s %s%s%s" % ("", s.at, "%05X" % f.dgn if f.dgn is not None
                                              else f.dgn_expr, f.reading(),
                                              " (template %#x)" % f.template if f.template else "",
                                              " -- " + names if names else ""))
        else:
            print("%16s%s" % ("", "; ".join(("%s x%d" % (k, n)) if n > 1 else k
                                             for k, n in frames.items())[:180]))
    if m.config_calls:
        print()
        print("Config-set calls -- each sends 1EE00 <offset lo> <hi> <value> when the buffer byte "
              "changes")
        for c in m.config_calls:
            if not _name_match(name, [c.option]):
                continue
            who = ", ".join("%#x %s" % ie for ie in c.touch_ids) if c.touch_ids else \
                "function %#x" % c.function
            print("  @%#x offset %-5s value %-4s %-28s %s %s" % (
                c.at, "%#x" % c.offset if c.offset is not None else "?",
                c.value if c.value is not None else "?", repr(c.option) if c.option else "-",
                c.context, who))
    print()
    print("class: reached = a touch region on the screen tree names the group; unattached = only "
          "objects the screen tree does not reach do; dead = no touch region names it. Captions "
          "are record order (inferred-from-data); outputs-page and option names are "
          "proven-from-code. --dgn X or --detail lists every entry and send.")


# Every name `gestures.GroupBehaviour.kind_for_variant` can return. Listed here rather than
# imported so `--help` works without reading an image; `test_gestures` pins that the classifier
# never returns anything outside this set.
_GESTURE_KINDS = ("momentary", "tap-or-hold", "hold-only", "hold-release",
                  "latch", "tap-only", "press-only", "unbound", "other")


def _cmd_gestures(args: argparse.Namespace) -> int:
    """Report the interaction model of every binding group (`docs/CONTROL-BEHAVIOR.md`).

    The `kind` column names the gesture pattern; `hold` is how long before a press counts as
    a hold; `repeat` is the three per-phase repeat intervals in ms (press/held/release), where
    0 means "send once". Each emission line reads "which phases send this frame".

    Controls whose touch id is at or above the group count are NOT listed: they are GuiCode
    action ids, not binding groups, and their hold timers live in GuiCode instead.
    """
    from . import gestures

    data = Path(args.image).read_bytes()
    groups = ([gestures.describe_group(data, args.group)] if args.group is not None
              else [g for g in gestures.all_groups(data) if g.emissions])
    if args.kind:
        groups = [g for g in groups if g.kind == args.kind]

    if args.json:
        print(json.dumps({"groups": [
            {"group": g.group, "kind": g.kind, "hold_ms": g.hold_ms,
             "repeat_ms": [g.repeat_press_ms, g.repeat_held_ms, g.repeat_release_ms],
             "variant_count": g.variant_count, "cycles_variant": g.cycles_variant,
             "advances_global": g.advances_global,
             "emissions": [{"phases": e.phase_names, "repeats": e.repeats,
                            "variant": e.variant, "dgn": e.dgn, "command": e.command,
                            "level": e.level, "channel": e.channel}
                           for e in g.emissions]}
            for g in groups]}, indent=2))
        return 0

    print(f"{'group':>5}  {'kind':<12} {'hold':>6}  {'repeat p/h/r':>14}  emissions")
    for g in groups:
        rpt = f"{g.repeat_press_ms}/{g.repeat_held_ms}/{g.repeat_release_ms}"
        print(f"{g.group:>5}  {g.kind:<12} {g.hold_ms:>5}ms  {rpt:>14}")
        for e in g.emissions:
            if e.variant:
                continue          # variant copies repeat the shape; show the base copy only
            chan = "-" if e.channel is None else str(e.channel)
            print(f"           {'+'.join(e.phase_names) or 'never':<34} "
                  f"dgn={e.dgn:#06x} cmd={e.command:<3} lvl={e.level:<3} ch={chan}"
                  f"{'  (repeats)' if e.repeats else ''}")
    return 0


def _cmd_options(args: argparse.Namespace) -> int:
    """List every OPTIONS-page checkbox binding and the floorplan value -> name map.

    `options.floorplan_options` can raise `FloorplanSwitchNotFound` on a build with no
    confirmed FLOORPLAN SELECTION screen (11.23) -- caught here so the command still
    reports the option bindings rather than crashing; the floorplan map comes back as
    `None` (`--json`: `"floorplans": null`) with a short note in the human output.
    """
    data = Path(args.image).read_bytes()
    bindings = sorted(options.option_bindings(data), key=lambda b: b.name)
    try:
        floorplans: dict[int, str] | None = options.floorplan_options(data)
    except options.FloorplanSwitchNotFound:
        floorplans = None

    if args.json:
        payload = {
            "options": [
                {
                    "name": b.name,
                    "value_slot": b.value_slot,
                    "register": _fmt_register(b.register),
                    "vtype": b.vtype,
                    "options_obj": b.options_obj,
                }
                for b in bindings
            ],
            "floorplans": (dict(sorted(floorplans.items()))
                           if floorplans is not None else None),
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"{len(bindings)} option binding(s):")
    print(f"  {'Name':<24} {'Slot':>5}  {'Register':<12} Type")
    for b in bindings:
        print(f"  {b.name:<24} {b.value_slot:>5}  {_fmt_register(b.register):<12} "
              f"{b.vtype}")

    print()
    if floorplans is None:
        print("floorplan map unavailable for this build")
    else:
        print(f"{len(floorplans)} floorplan value(s):")
        for value, name in sorted(floorplans.items()):
            print(f"  {value:>3}  {name}")
    return 0


# --page NAME -> header text, resolved to an object-table root generically (works on
# any build, not just 13.6 -- see `_resolve_page_root`). "outputs" is NOT in here: its
# header text ("NETWORK DIAGNOSTICS") is ambiguous -- 13.6 object 14 (the DEVICES/FAULTS
# tab) carries the same header as object 35 (the actual output grid) -- so it is resolved
# structurally instead, by `_resolve_outputs_root`.
_PAGE_HEADERS = {
    "home": "HOME",
    "electrical": "ELECTRICAL",
    "lights": "LIGHTS",
    "climate": "CLIMATE CONTROL",
    "settings": "SETTINGS",
    "options": "OPTIONS",
    "floorplan": "FLOORPLAN SELECTION",
}

# All --page names accepted by the render CLI: the header-matched ones above plus the
# structurally-resolved "outputs".
_PAGE_NAMES = sorted(set(_PAGE_HEADERS) | {"outputs"})

def _resolve_outputs_root(data: bytes) -> int:
    """The G12 OUTPUTS grid's object index (`outputs.outputs_root`)."""
    from . import outputs
    return outputs.outputs_root(data)


def _resolve_page_root(data: bytes, page: str) -> int:
    """The object-table index for a named page.

    "outputs" is resolved structurally (`_resolve_outputs_root`); every other name is
    resolved by its header record's label stripping to `page`'s header text (e.g.
    "climate" -> "CLIMATE CONTROL").

    Uses the same convention `options._find_options_pages` uses for locating "OPTIONS"
    pages -- a page's header is the first LABELLED record in its own (non-recursive)
    record list, not any label the object happens to end with (`guidata.object_label`'s
    trailing-label scan finds a *different*, smaller object here -- a nav-tab caption,
    not the page root -- and finds nothing at all for HOME/ELECTRICAL/LIGHTS/SETTINGS
    on 13.6; verified before writing this). Generalises cleanly: on 13.6 this returns
    7 for "climate", matching `layout.climate_root`'s documented root; on 11.23 it
    returns 9 and on 14.7 it returns 12, matching that same docstring's per-build
    values, so this is not a 13.6-only heuristic.
    """
    if page == "outputs":
        return _resolve_outputs_root(data)
    if page not in _PAGE_HEADERS:
        raise ValueError(f"unknown --page {page!r}; choose one of {_PAGE_NAMES}")
    wanted = _PAGE_HEADERS[page]
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    for i in range(len(table)):
        label = options._first_label(options._own_records(data, gs, ge, table, i))
        if label is not None and label.strip() == wanted:
            return i
    raise ValueError(
        f"could not resolve --page {page!r} (looking for a page whose header record "
        f"reads {wanted!r}) on this build -- it may be named differently or absent here. "
        f"Run `pages` to list this build's pages and their --root indices, then pass --root N.")


def _parse_slot_override(spec: str) -> tuple[int, int]:
    if "=" not in spec:
        raise ValueError(f"--slot wants SLOT=VALUE, got {spec!r}")
    raw_slot, raw_value = spec.split("=", 1)
    try:
        return int(raw_slot, 0), int(raw_value, 0)
    except ValueError:
        raise ValueError(f"--slot wants two integers (SLOT=VALUE), got {spec!r}")


def _cmd_emulate(args: argparse.Namespace) -> int:
    """Run the panel emulator (the `emu` extra) on an mcf.bin."""
    import importlib

    refused = _scenario_refusal(args.scenario, args.allow_user_ops, args.json, {"ok": False})
    if refused is not None:
        return refused
    try:
        importlib.import_module("fireflymcf.emu.machine")
        emu_main = importlib.import_module("fireflymcf.emu.__main__")
    except ImportError:
        print(EMU_MISSING, file=sys.stderr)
        return 2
    if args.scenario:
        results = _emulate_scenarios_on(Path(args.mcf), args.scenario, args.timeout)
        if args.json:
            print(json.dumps({k: r.to_dict() for k, r in results.items()}, indent=2))
        else:
            from .emu import gate
            for line in gate.summary_lines(results):
                print(line)
        return 0 if all(r.status == "passed" for r in results.values()) else 1
    return emu_main.run(args)


def _emulate_scenarios_on(image: Path, scenarios: list[str], timeout_s: float):
    from .emu import gate
    return gate.run_scenarios(image, [Path(s) for s in scenarios], timeout_s)


def _cmd_render(args: argparse.Namespace) -> int:
    """Render one GuiData page to an 800x480 PNG at a given coach configuration.

    `--root` overrides `--page`; with neither, the Climate page is the default --
    resolved via `_resolve_page_root(data, "climate")`, NOT `layout.climate_root(data)`.
    `layout.climate_root` hardcodes `return 7`, which is only 13.6's Climate root
    (11.23's is 9, 14.7's is 12 -- see its own docstring); using it here would silently
    render the wrong object on every other build when no flag is given, defeating the
    point of a cross-build tool. The coach configuration is a `{value_slot: value}`
    map: `--config` resolves through `options.CoachConfig.from_json` ->
    `options.slot_values`, and each `--slot SLOT=VALUE` is applied on top (repeatable;
    wins over `--config` on a colliding slot, since dict.update after the config is a
    plain last-write-wins).
    """
    from . import render                        # lazy: pulls in Pillow/numpy (render extra)
    image = Path(args.image)
    data = image.read_bytes()

    if args.root is not None:
        root = args.root
    elif args.page is not None:
        root = _resolve_page_root(data, args.page)
    else:
        root = _resolve_page_root(data, "climate")

    config = options.CoachConfig.from_json(args.config) if args.config else None
    preview: dict = dict(config.preview) if config else {}
    # Slots the panel COMPUTES rather than receives -- the HOME battery block and the
    # left nav rail, whose Electrical icon is dropped (and the rail respaced) when the
    # coach has no generator mode, no Go Power! controller and no Progressive Inverter --
    # are derived in `options.render_slots`. A `--slot` override still wins, exactly as it
    # does over `--config`.
    slot_values = options.render_slots(
        data, config, dict(_parse_slot_override(spec) for spec in args.slot))

    if getattr(args, "placements", None):
        # The page's hit map: every record the layout walk PLACES under this configuration,
        # with the object index that owns it. This is what maps "the Water Pump button" to
        # the `object` a recipe op takes, without Studio.
        from . import layout
        screen = layout.Screen(data)
        placed = screen.walk(root, slot_values=slot_values or None)
        body = {"image": str(image), "root": root, "canvas": [800, 480],
                "slot_values": {str(k): v for k, v in sorted(slot_values.items())},
                "placements": [{"obj_index": p.obj_index, "record_offset": p.record_offset,
                                "wtype": p.wtype, "box": [p.x0, p.y0, p.x1, p.y1],
                                "label": p.label, "label_offset": p.label_offset,
                                "font": p.font, "value_slot": p.value_slot,
                                "depth": p.depth}
                               for p in placed]}
        Path(args.placements).write_text(json.dumps(body, indent=1), encoding="utf-8")
        print(f"wrote {args.placements}  ({len(placed)} placed record(s))")

    # The AES/AGS indicator (slot 129) and the ELECTRICAL run-time caption (slot 269) are wtype-13
    # dynamic strings: the panel writes their TEXT into SRAM at config time from a GuiCode mode
    # table, keyed on the generator-mode register slot 37 (GUICODE Finding 31; the firmware tests
    # slot-37 {2,3}=AES / 1=AGS). So the label is derived from the FINAL slot-37 value -- after
    # the config's `slot_values` AND any `--slot` override -- NOT from the option name, so the
    # drawn label can never disagree with the switch-selected page content (both read slot 37).
    # render draws these only for wtype-13 records the walk actually PLACES, so the switch gating
    # (slots 6/7/37) still decides whether a badge appears at all. slot 37 unset / 0 -> no label.
    if args.config or args.slot:
        mode_val = slot_values.get(render.MODE_SLOT)
        mode = ("AGS" if mode_val == 1
                else "AES" if mode_val in (2, 3) else None)
        labels = render.aes_ags_labels(data)
        if mode in labels:
            indicator, caption = labels[mode]
            preview["dynamic_text"] = {render.AES_INDICATOR_SLOT: indicator,
                                       render.AES_CAPTION_SLOT: caption}

    # The HOME "House" battery and the ELECTRICAL "Battery Voltage" box are the SAME GuiData
    # register (slot 209, 0x2003A594 -- byte-verified, identical widget structure). The owner
    # reports they show different physical batteries: HOME the 12V house (never 48V), ELECTRICAL
    # the 48V pack -- two CAN messages feeding that register upstream, a split the display graph
    # does not encode (GUICODE Finding 31 voltage note). Since a render is one page, pick the
    # page-appropriate value: on HOME show the 12V `house_voltage` in slot 209's `coach_voltage`
    # key; elsewhere (ELECTRICAL) keep the 48V `coach_voltage`.
    if preview.get("house_voltage") is not None:
        try:
            on_home = root == _resolve_page_root(data, "home")
        except ValueError:
            on_home = False
        if on_home:
            preview = {**preview, "coach_voltage": preview["house_voltage"]}

    match_table_off = int(args.match_off, 16) if args.match_off else None

    # Tank fill height: --tank-level (a fraction) wins; otherwise derive it from the config's
    # preview tank_level (a percent), so one `preview` block drives both the fill and the number.
    tank_level = args.tank_level
    if tank_level == 0.0 and "tank_level" in preview:
        tank_level = preview["tank_level"] / 100.0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    render.render_climate_decoded(str(image), str(out), root=root,
                                  match_table_off=match_table_off,
                                  slot_values=slot_values or None,
                                  tank_level=tank_level,
                                  preview=preview or None)
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    console.utf8()
    parser = argparse.ArgumentParser(prog="fireflymcf")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="write MCF sections to a directory")
    p.add_argument("image")
    p.add_argument("dest")
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("regions", help="write header-free cli.bin regions")
    p.add_argument("image")
    p.add_argument("dest")
    p.set_defaults(func=_cmd_regions)

    p = sub.add_parser("identify", help="report embedded ARM images")
    p.add_argument("image")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_identify)

    p = sub.add_parser("coverage", help="account for every byte of an MCF")
    p.add_argument("image")
    p.add_argument("--deep", action="store_true",
                   help="also claim GuiData's internal structure")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_coverage)

    p = sub.add_parser("labels", help="list label records")
    p.add_argument("image")
    p.add_argument("--containing", metavar="TEXT",
                   help="only records whose field contains TEXT; "
                        "omit to list decodable screens and their rows")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_labels)

    p = sub.add_parser("patch", help="rename a label and reseal the container")
    p.add_argument("image")
    p.add_argument("out")
    p.add_argument("--rename", metavar="OLD=NEW")
    p.add_argument("--remap", metavar="LABEL=CHANNEL",
                   help="repoint a located control's G12 channel")
    p.add_argument("--move", metavar="TEXT=X,Y",
                   help="move a text/icon draw element to absolute pixel (x, y)")
    p.add_argument("--recolor", metavar="OBJ=COLOR", action="append", default=[],
                   help="recolour object OBJ's button box; COLOR is R,G,B or idxN "
                        "(a firmware-palette colour only). Repeatable. See --list-colors")
    p.add_argument("--list-colors", action="store_true",
                   help="print the firmware fill palette (colours and their indices) and exit")
    p.add_argument("--name", metavar="NAME",
                   help="set the flasher build name outright (overrides the auto '(Custom)' mark)")
    p.add_argument("--no-mark", action="store_true",
                   help="do not auto-append '(Custom)' to the build name")
    p.add_argument("--allow-capability-change", action="store_true",
                   help="permit a remap across the dim/latch boundary")
    p.add_argument("--site", action="append", metavar="HEX", default=[],
                   help="restrict to the label record at this text offset; repeatable")
    p.add_argument("--on-screen", metavar="TITLE",
                   help="restrict to records on a decodable screen with this title")
    p.add_argument("--no-verify", dest="verify", action="store_false",
                   help="skip the verification ladder (not recommended)")
    p.set_defaults(func=_cmd_patch, verify=True)

    p = sub.add_parser("options", help="list OPTIONS-page bindings and the floorplan map")
    p.add_argument("image")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_options)

    p = sub.add_parser("doctor",
                       help="one report of what this firmware contains and what you can safely change")
    p.add_argument("image")
    p.add_argument("--json", action="store_true")
    p.add_argument("--bus", action="store_true",
                   help="also count every received and sent message by class (the bus map's "
                        "totals; about 5 s more; `bus-map` lists them)")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("pages",
                       help="list this build's navigable pages, their --root index and header")
    p.add_argument("image")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_pages)

    p = sub.add_parser("image-export",
                       help="list GuiData images, or export one to a P6 PPM")
    p.add_argument("image")
    p.add_argument("--index", type=int, help="image index to export (omit to list)")
    p.add_argument("--out", help="write the PPM here")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_image_export)

    p = sub.add_parser("swap-image",
                       help="replace one GuiData image with a P6 PPM, in place, and reseal")
    p.add_argument("image")
    p.add_argument("ppm", help="the replacement image, a binary P6 PPM")
    p.add_argument("--index", type=int, required=True, help="image index to replace")
    p.add_argument("--out", required=True, help="write the new MCF here")
    p.add_argument("--no-mark", action="store_true",
                   help="do not append ' (Custom)' to the flasher build name")
    p.set_defaults(func=_cmd_swap_image)

    p = sub.add_parser("glyph-export",
                       help="export one glyph as install_glyph parameters (JSON)")
    p.add_argument("image")
    p.add_argument("--slot", type=int, required=True, help="font slot")
    p.add_argument("--char", required=True, help="the character the glyph draws")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.add_argument("--as-op", action="store_true",
                   help="wrap it as a complete recipe op, ready to paste into a .ffproj.json")
    p.add_argument("--id", help="op id to use with --as-op")
    p.set_defaults(func=_cmd_glyph_export)

    p = sub.add_parser("glyph-import",
                       help="turn artwork (PNG, JPEG, SVG, WebP, ...) into install_glyph "
                            "parameters (JSON)")
    p.add_argument("image", help="the stock mcf.bin the project compiles from")
    p.add_argument("artwork", nargs="?", help="the picture; omit with --list")
    p.add_argument("--list", action="store_true",
                   help="list the 4bpp icon fonts a glyph can go in, with their cell heights")
    p.add_argument("--slot", type=int, help="font slot (see --list)")
    p.add_argument("--char", help="the character to claim; default: the font's first free one")
    p.add_argument("--height", type=int, help="glyph height in px (default: the art's own, "
                                              "shrunk to fit the font cell)")
    p.add_argument("--width", type=int, help="glyph width in px (aspect kept if only one given)")
    p.add_argument("--ink", choices=["auto", "alpha", "dark", "light"], default="auto",
                   help="which pixels are the mark: transparency, or dark/light pixels on a "
                        "plain background (default: decide from the image)")
    p.add_argument("--no-trim", action="store_true",
                   help="keep the art's empty margins instead of trimming to its ink")
    p.add_argument("--claim-block", action="store_true",
                   help="also take a placeholder-block character (icon font 24's spare ones)")
    p.add_argument("--preview", metavar="PNG", help="also write how the panel will draw it")
    p.add_argument("--out", help="write to this file instead of stdout")
    p.add_argument("--as-op", action="store_true",
                   help="wrap it as a complete recipe op, ready to paste into a .ffproj.json")
    p.add_argument("--id", help="op id to use with --as-op")
    p.set_defaults(func=_cmd_glyph_import)

    p = sub.add_parser("g12-rules",
                       help="G12 rules mapping a config selector to an RV-C command broadcast")
    p.add_argument("image")
    p.add_argument("--selector", type=lambda v: int(v, 0),
                   help="show only the rules this config selector drives (e.g. 0x92)")
    p.set_defaults(func=_cmd_g12_rules)

    p = sub.add_parser("g12-inputs",
                       help="G12 input pins: transmit rule, receive-filter var, screen row")
    p.add_argument("image")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_g12_inputs)

    p = sub.add_parser("slot-source",
                       help="classify how each widget value slot is driven (see slotsource.py)")
    p.add_argument("image")
    p.add_argument("--slot", type=int,
                   help="report one slot, with every stock store to it, instead of the table")
    p.add_argument("--unexplained", action="store_true",
                   help="list only the slots no instruction reaches")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_slot_source)

    p = sub.add_parser("boot-lottery",
                       help="content whose visibility rides on SRAM nothing initialises "
                            "(see bootlottery.py)")
    p.add_argument("image")
    p.add_argument("--all", action="store_true",
                   help="include gates no screen reaches, and gates with a 0xFFFF default arm")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_boot_lottery)

    p = sub.add_parser("pins", help="map G12 output pins to their status colour cells")
    p.add_argument("image")
    p.add_argument("--all", action="store_true",
                   help="include pins an existing control already commands")
    p.add_argument("--names", action="store_true",
                   help="under each pin, list every OUTPUTS-page name with the floorplans and "
                        "options that show it, and the captions of the controls joined to it")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_pins)

    p = sub.add_parser("bus-map",
                       help="every RV-C message the panel receives and sends: what it does with "
                            "it and what the panel calls it (see busmap.py, sendmap.py)")
    p.add_argument("image")
    p.add_argument("--dgn", action="append", default=[],
                   help="only this DGN, in hex (e.g. 1FFB7); repeatable; lists every sink")
    p.add_argument("--detail", action="store_true",
                   help="under each entry, list every sink and widget")
    p.add_argument("--name", metavar="TEXT",
                   help="only what the panel calls TEXT (case-insensitive), received and sent")
    p.add_argument("--all", action="store_true",
                   help="list every entry and send (the default prints the summary tables)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_bus_map)

    p = sub.add_parser("gestures",
                       help="report how each control responds to tap, hold and release")
    p.add_argument("image")
    p.add_argument("--group", type=int, default=None,
                   help="report one group index instead of the whole table")
    p.add_argument("--kind", default=None, choices=sorted(_GESTURE_KINDS),
                   help="only groups whose variant-0 gesture is this kind")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.set_defaults(func=_cmd_gestures)

    p = sub.add_parser("verify", help="compare a build with its stock image: sections, byte "
                                      "runs by region, objects, and every page's render")
    p.add_argument("build", help="the compiled mcf-*.bin")
    p.add_argument("stock", help="the stock mcf.bin it was compiled from, or the previous build "
                                 "it extends (then only the new ops' changes may differ)")
    p.add_argument("--config", action="append", default=[], metavar="FILE",
                   help="CoachConfig JSON to render under (repeatable; default: no floorplan)")
    p.add_argument("--page", action="append", default=[], choices=_PAGE_NAMES,
                   help="render only this page (repeatable, with --root)")
    p.add_argument("--root", action="append", default=[], type=int, metavar="N",
                   help="render only this root object (repeatable)")
    p.add_argument("--no-render", action="store_true", help="bytes and objects only")
    p.add_argument("--emulate", action="store_true",
                   help="also run the --scenario emulator tests on the build and record them "
                        "in its sidecar (a failure is reported, not an exit code)")
    p.add_argument("--scenario", action="append", default=[], metavar="FILE",
                   help="with --emulate: an emulator test file (repeatable)")
    p.add_argument("--emulate-timeout", type=float, default=300.0, metavar="SECONDS",
                   help="with --emulate: wall-clock limit per scenario (default 300)")
    p.add_argument("--project", metavar="P.ffproj.json",
                   help="with --emulate: also run each of this project's user ops' emu_test.py "
                        "(sidecar key: the op's type)")
    p.add_argument("--allow-user-ops", action="store_true",
                   help="run user ops' emulator tests: --project's emu_test.py files, and a "
                        "--scenario file in an op folder (the author's code)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("coach-config",
                       help="write the floorplan + options file render/verify --config take, "
                            "from a candump capture or named values")
    p.add_argument("image", help="the coach's stock mcf.bin")
    p.add_argument("-o", "--out", required=True, metavar="COACH.json")
    p.add_argument("--capture", metavar="CANDUMP.log",
                   help="a `candump -L` log of the RV-C bus (the coach's own configuration)")
    p.add_argument("--floorplan", help="the floorplan code, e.g. WD (`options` lists them)")
    p.add_argument("--option", action="append", default=[], metavar="NAME",
                   help="a fitted option, by its OPTIONS-page name (repeatable)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_coach_config)

    p = sub.add_parser("schema", help="print the JSON Schema of a *.ffproj.json project")
    p.set_defaults(func=_cmd_schema)

    p = sub.add_parser("ops", help="the recipe ops a project can use: purpose, hardware "
                                   "status, parameters, error codes")
    p.add_argument("names", nargs="*", metavar="NAME", help="show these ops in full")
    p.add_argument("--project", metavar="P.ffproj.json",
                   help="also list this project's user ops (manifests only; nothing is run)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_ops)

    p = sub.add_parser("op", help="share and accept user ops: install, pack, accept "
                                  "(never imports or runs one)")
    opsub = p.add_subparsers(dest="op_command", required=True)
    q = opsub.add_parser("install", help="copy an op folder (a folder, a .zip, or a git URL) "
                                         "into <project dir>/ops/<name>/")
    q.add_argument("project", help="the project file (*.ffproj.json)")
    q.add_argument("src", help="a folder, a .zip, or a git URL (https://, file://, an SSH git@ URL)")
    q.add_argument("--replace", action="store_true", help="overwrite an installed op of that name")
    q.add_argument("--json", action="store_true", help="emit JSON")
    q.set_defaults(func=_cmd_op)
    q = opsub.add_parser("pack", help="zip one of the project's op folders for sharing")
    q.add_argument("project", help="the project file (*.ffproj.json)")
    q.add_argument("name", help="the op's folder name or type")
    q.add_argument("-o", "--out", required=True, metavar="NAME.zip")
    q.add_argument("--json", action="store_true", help="emit JSON")
    q.set_defaults(func=_cmd_op)
    q = opsub.add_parser("accept", help="record the owner's acceptance of a user op's current "
                                        "folder (an edit voids it)")
    q.add_argument("project", help="the project file (*.ffproj.json)")
    q.add_argument("type", help="the user op's type (user:...)")
    q.add_argument("--g12", action="store_true",
                   help="the owner accepts that it changes the G12 (required for such an op)")
    q.add_argument("--json", action="store_true", help="emit JSON")
    q.set_defaults(func=_cmd_op)

    p = sub.add_parser("compile",
                       help="build a *.ffproj.json project against its stock image")
    p.add_argument("project", help="the project file (*.ffproj.json)")
    p.add_argument("stock", help="the stock MCF the project's base_sha256 names")
    p.add_argument("-o", "--out", required=True, metavar="MCF-NAME.bin",
                   help="output path; must be named mcf-*.bin and must not exist")
    p.add_argument("--allow-unverified", action="store_true",
                   help="build even if an op has never run on a coach (the owner is first)")
    p.add_argument("--allow-user-ops", action="store_true",
                   help="import and run the project's user-written ops (full Python trust; "
                        "only once the owner has been told what they change)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_compile)

    p = sub.add_parser("validate",
                       help="pre-flash gate: run every structural/capability/op gate on a "
                            "*.ffproj.json and report PASS/FAIL (writes nothing)")
    p.add_argument("project", help="the project file (*.ffproj.json)")
    p.add_argument("stock", nargs="?", default=None,
                   help="the stock MCF the project's base_sha256 names; omit to auto-locate "
                        "it among the repo's corpus images by hash")
    p.add_argument("--require-verified", action="store_true",
                   help="FAIL if any op is not hardware-verified (use for owner hand-off builds)")
    p.add_argument("--allow-user-ops", action="store_true",
                   help="import and run the project's user-written ops (full Python trust)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("emulate", help="boot an mcf.bin on the panel emulator (needs the emu extra)")
    from .emu import __main__ as _emu_args       # light: no unicorn until it runs
    _emu_args.add_arguments(p)
    p.add_argument("--scenario", action="append", default=[], metavar="FILE",
                   help="run this emulator test (a file defining scenario(s)) on the image "
                        "instead of the interactive emulator (repeatable); exit 1 unless all pass")
    p.add_argument("--timeout", type=float, default=300.0, metavar="SECONDS",
                   help="wall-clock limit per scenario (default 300)")
    p.add_argument("--allow-user-ops", action="store_true",
                   help="run a --scenario file that sits in an op folder (the op author's code)")
    p.set_defaults(func=_cmd_emulate)

    p = sub.add_parser("render", help="render a GuiData page to an 800x480 PNG")
    p.add_argument("image")
    p.add_argument("out")
    p.add_argument("--page", choices=_PAGE_NAMES,
                   help="page name, resolved to a root object per build")
    p.add_argument("--root", type=lambda s: int(s, 0), metavar="N",
                   help="explicit object-table root index; overrides --page")
    p.add_argument("--config", metavar="FILE",
                   help="CoachConfig JSON (floorplan/options/extra_slots)")
    p.add_argument("--slot", action="append", metavar="SLOT=VALUE", default=[],
                   help="raw value-slot override, repeatable; wins over --config")
    p.add_argument("--match-off", metavar="HEX",
                   help="override the match-value table offset (hex)")
    p.add_argument("--placements", metavar="FILE.json",
                   help="also write the page's hit map: each placed record's object index, "
                        "record offset, widget type, box, label, font and value slot")
    p.add_argument("--tank-level", type=float, default=0.0, metavar="FRAC",
                   help="preview tank fill level 0..1 (runtime value; default 0 = empty). "
                        "The panel shows level/resolution as a percent and does NOT "
                        "quantise -- 34/56 reads 61%% on the coach (PC.62). The G12's own "
                        "ladder only ever emits 0/0.33/0.67/1.0, which is where that "
                        "belief came from")
    p.set_defaults(func=_cmd_render)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        if getattr(args, "json", False):
            # A caller that asked for JSON parses stdout; give it the failure in that form.
            print(json.dumps({"ok": False, "errors": [
                {"op_id": getattr(exc, "op_id", None),
                 "code": getattr(exc, "code", None) or type(exc).__name__,
                 "message": str(exc), "offers": list(getattr(exc, "offers", None) or [])}]},
                indent=2))
            return 2
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        # render/options/pins and `patch --recolor`/`--remap` read the disassembly or build pixel
        # arrays, which need the optional compiled dependencies. Turn the raw import failure into
        # an actionable message rather than a traceback.
        if exc.name and exc.name.split(".")[0] in ("numpy", "capstone", "PIL"):
            print(f"error: the '{args.command}' command needs the optional dependencies "
                  f"(missing {exc.name!r}); install them with:  pip install 'fireflymcf[full]'",
                  file=sys.stderr)
            return 3
        raise




def _cmd_boot_lottery(args) -> int:
    """Gates keyed on a slot nothing establishes -- see `bootlottery` for what this does and
    does not claim (a listed gate is a ranked candidate, not a diagnosed defect)."""
    from . import bootlottery

    data = Path(args.image).read_bytes()
    gates = bootlottery.lottery_gates(data) if args.all else bootlottery.at_risk(data)
    if args.json:
        print(json.dumps([{
            "slot": g.value_slot, "address": f"{_slot_addr(data, g.value_slot):#010x}",
            "switch_obj": g.switch_obj, "screens": sorted(g.screens),
            "has_default": g.has_default, "reachable": g.reachable,
            "controls": sorted(g.controls), "actions": sorted(g.actions),
            "arms": [{"match": a.match_value, "obj": a.obj,
                      "draws_content": a.draws_content,
                      "touch_ids": sorted(a.touch_ids)} for a in g.arms],
        } for g in gates], indent=2))
        return 0
    if not gates:
        print("no gates on never-established slots (or no match table on this build)")
        return 0
    print(f"{'slot':>5}  {'addr':>10}  {'obj':>5}  {'screens':<12}  {'dflt':<5}  "
          f"{'controls':<22}  actions")
    for g in gates:
        print(f"{g.value_slot:>5}  {_slot_addr(data, g.value_slot):#010x}  {g.switch_obj:>5}  "
              f"{str(sorted(g.screens)):<12}  {str(g.has_default):<5}  "
              f"{str(sorted(g.controls)):<22}  {len(g.actions)}")
    print(f"\n{len(gates)} gate(s); "
          f"{len({g.value_slot for g in gates if g.controls})} slot(s) put controls at stake.")
    print("A listed gate is a CANDIDATE ranked by content at stake, not a diagnosed defect.")
    return 0


def _slot_addr(data: bytes, slot: int) -> int:
    from . import slotsource

    entry = slotsource.slot_source(data, slot)
    return entry.address if entry else 0


if __name__ == "__main__":
    raise SystemExit(main())
