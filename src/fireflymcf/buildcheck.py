"""Compare a build with the stock image it was compiled from: what `fireflymcf verify` prints.

"Verifying only what you set is not verification." A build is checked whole, two ways:

1. **Bytes.** Every checksum must verify and every byte be claimed by a known structure
   (`coverage.analyse`), whatever the build's size. Every section is compared with its stock
   counterpart. GuiData is compared object
   by object through the four object tables, so a change lands on a named object, not an
   offset. When the two images are the same length (every same-size build), every differing
   run is also classified by the region `coverage.analyse` claims it for: the header's build
   name, a checksum, an object, a section body.
2. **Pixels.** Every page the screen-switch table reaches is rendered from both images under
   each configuration, and the differing area is reported as a bounding box. Everything that
   changed must be something an op meant to change; anything else is a defect.

The report says what differs. Deciding whether each difference was intended is the reader's
job, with the project's manifest beside it.
"""
from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field

from . import container, coverage, guidata, layout, options, patch, render

REGION_NAMES = {
    "header": "header (build name)",
    "header-checksum": "header checksum",
    "section-checksum": "section checksum",
    "descriptor": "section descriptor",
    "descriptor-checksum": "descriptor checksum",
}


@dataclass
class SectionDiff:
    name: str
    stock_length: int
    build_length: int
    status: str             # "same", "changed" (same length) or "resized"
    differing_bytes: int    # in the common prefix


@dataclass
class ByteRun:
    offset: int
    length: int
    region: str
    object: int | None = None


@dataclass
class ObjectDiff:
    table: int
    changed: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)


@dataclass
class PageDiff:
    root: int
    screen: int | None
    header: str
    config: str
    bbox: list[int] | None      # [x0, y0, x1, y1) of the changed pixels, None if identical
    changed_pixels: int = 0
    error: str | None = None


@dataclass
class Report:
    stock_name: str
    build_name: str
    stock_length: int
    build_length: int
    sections: list[SectionDiff]
    runs: list[ByteRun] | None
    objects: list[ObjectDiff]
    pages: list[PageDiff]
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def unexplained(self) -> list[ByteRun]:
        """Same-size runs outside every recognised region: always a defect."""
        return [r for r in (self.runs or []) if r.region == "unclaimed"]


def _sections(stock: bytes, build: bytes) -> list[SectionDiff]:
    a = {s.name: s for s in container.parse_container(stock).sections}
    b = {s.name: s for s in container.parse_container(build).sections}
    out = []
    for name in list(a) + [n for n in b if n not in a]:
        sa, sb = a.get(name), b.get(name)
        if sa is None or sb is None:
            out.append(SectionDiff(name, sa.length if sa else 0, sb.length if sb else 0,
                                   "added" if sa is None else "removed", 0))
            continue
        x = stock[sa.offset:sa.offset + sa.length]
        y = build[sb.offset:sb.offset + sb.length]
        n = min(len(x), len(y))
        diff = sum(1 for i in range(n) if x[i] != y[i]) if x[:n] != y[:n] else 0
        status = "resized" if len(x) != len(y) else ("changed" if diff else "same")
        out.append(SectionDiff(name, len(x), len(y), status, diff))
    return out


def _bounds(data: bytes) -> list[list[tuple[int, int]]]:
    """Section-relative (start, end) of every object in every table.

    `guidata._object_bounds` ends object 0 (the highest) at the section end, which takes in
    the section checksum and every table above it. Here object 0 ends at the next structure
    above its start (another table's object or the table itself), or at the checksum."""
    gs, ge = guidata.guidata_extent(data)
    tables = guidata.object_tables(data)
    marks = sorted({x for t in tables for x in t}
                   | {guidata.object_table_offset(data, i) for i, t in enumerate(tables) if t})
    limit = ge - gs - 4
    out = []
    for t in tables:
        rows = []
        for k in range(len(t)):
            if k:
                rows.append((t[k], t[k - 1]))
            else:
                i = bisect.bisect_right(marks, t[0])
                rows.append((t[0], min(marks[i], limit) if i < len(marks) else limit))
        out.append(rows)
    return out


def _objects(stock: bytes, build: bytes) -> list[ObjectDiff]:
    """Each object table compared entry by entry, on the bytes each object occupies."""
    out = []
    ga, _ = guidata.guidata_extent(stock)
    gb, _ = guidata.guidata_extent(build)
    ta, tb = _bounds(stock), _bounds(build)
    for t, (xa, xb) in enumerate(zip(ta, tb)):
        d = ObjectDiff(t)
        for k in range(min(len(xa), len(xb))):
            (a0, a1), (b0, b1) = xa[k], xb[k]
            if stock[ga + a0:ga + a1] != build[gb + b0:gb + b1]:
                d.changed.append(k)
        d.added = list(range(len(xa), len(xb)))
        d.removed = list(range(len(xb), len(xa)))
        out.append(d)
    return out


def _object_at(data: bytes, offset: int) -> int | None:
    """The table-1 object whose bytes hold file `offset` (the table pages and controls use)."""
    gs, ge = guidata.guidata_extent(data)
    if not gs <= offset < ge:
        return None
    rel = offset - gs
    for k, (lo, hi) in enumerate(_bounds(data)[1]):
        if lo <= rel < hi:
            return k
    return None


def _coverage_spans(build: bytes, problems: list[str]) -> list:
    """Every byte of the build accounted for, as `coverage.analyse` claims it. A checksum that
    does not verify or a byte nothing claims is a problem whatever the build's size: the panel
    rejects the first, and the second is structure no tool understands."""
    try:
        report = coverage.analyse(build, deep=True)
    except coverage.CoverageError as exc:   # a bad checksum: the panel would reject the file
        problems.append("coverage: %s" % exc)
        return []
    if report.unclaimed:
        problems.append("coverage: %d byte span(s) no structure claims, first at %#x"
                        % (len(report.unclaimed), report.unclaimed[0][0]))
    return report.spans


def _runs(stock: bytes, build: bytes, spans: list) -> list[ByteRun]:
    out = []
    for off, length in patch.diff_ranges(stock, build):
        i = bisect.bisect_right(spans, (off, float("inf"))) - 1
        region, key = "unclaimed", ""
        if i >= 0 and spans[i][0] <= off < spans[i][0] + spans[i][1]:
            label = spans[i][2]
            key, _, rest = label.partition(":")
            region = REGION_NAMES.get(key, key)
            if key == "section-checksum" and rest:
                region += " (%s)" % rest
        obj = None
        if key == "object":
            region, obj = "GuiData object", _object_at(build, off)
        out.append(ByteRun(off, length, region, obj))
    return out


def _page_header(data: bytes, root: int) -> str:
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    try:
        lab = options._first_label(options._own_records(data, gs, ge, table, root))
    except Exception:   # a page without a static title (a dynamic caption) is not an error
        return ""
    return lab.strip().replace("\n", " ") if lab else ""


def pages(data: bytes) -> list[tuple[int | None, int]]:
    """(screen id, root object) for every page the screen-switch table reaches, plus the
    G12 OUTPUTS diagnostic list, which is a sub-page and not a screen."""
    seen: dict[int, int | None] = {}
    for screen, root in sorted(layout.screen_object_map(data).items()):
        seen.setdefault(root, screen)
    try:
        from .cli import _resolve_page_root
        seen.setdefault(_resolve_page_root(data, "outputs"), None)
    except ValueError:
        pass
    return [(screen, root) for root, screen in seen.items()]


def _render(data: bytes, root: int, config: options.CoachConfig | None):
    slots = options.render_slots(data, config)
    preview = dict(config.preview) if config else None
    return render.render_screen_image(data, root, slot_values=slots or None,
                                      preview=preview or None).convert("RGB")


def _pages(stock: bytes, build: bytes, configs: dict[str, options.CoachConfig | None],
           roots: list[int] | None) -> list[PageDiff]:
    from PIL import ImageChops
    listed = pages(stock)
    if roots is not None:
        by_root = dict((r, s) for s, r in listed)
        listed = [(by_root.get(r), r) for r in roots]
    out = []
    for label, config in configs.items():
        for screen, root in listed:
            header = _page_header(stock, root)
            try:
                a, b = _render(stock, root, config), _render(build, root, config)
            except Exception as exc:    # report, never hide: a page that fails is a finding
                out.append(PageDiff(root, screen, header, label, None, 0,
                                    "%s: %s" % (type(exc).__name__, exc)))
                continue
            diff = ImageChops.difference(a, b)
            bbox = diff.getbbox()
            n = sum(1 for px in diff.getdata() if px != (0, 0, 0)) if bbox else 0
            out.append(PageDiff(root, screen, header, label,
                                list(bbox) if bbox else None, n))
    return out


def compare(stock: bytes, build: bytes,
            configs: dict[str, options.CoachConfig | None] | None = None,
            roots: list[int] | None = None, render_pages: bool = True) -> Report:
    """The whole-build comparison. `configs` maps a label to a coach configuration (None
    renders with no floorplan set); `roots` limits the pages rendered."""
    configs = configs if configs is not None else {"none": None}
    same_size = len(stock) == len(build)
    problems: list[str] = []
    if patch.build_name(build) == patch.build_name(stock):
        problems.append("the build name equals stock's; the boot menu cannot tell them apart")
    spans = _coverage_spans(build, problems)
    return Report(
        stock_name=patch.build_name(stock), build_name=patch.build_name(build),
        stock_length=len(stock), build_length=len(build),
        sections=_sections(stock, build),
        runs=_runs(stock, build, spans) if same_size else None,
        objects=_objects(stock, build),
        pages=_pages(stock, build, configs, roots) if render_pages else [],
        problems=problems)


def format_report(r: Report) -> str:
    lines = ["stock: %r (%d bytes)" % (r.stock_name, r.stock_length),
             "build: %r (%d bytes)" % (r.build_name, r.build_length)]
    for problem in r.problems:
        lines.append("PROBLEM: " + problem)
    lines.append("")
    lines.append("sections:")
    for s in r.sections:
        extra = ""
        if s.status == "resized":
            extra = " %+d bytes" % (s.build_length - s.stock_length)
        if s.status == "changed":
            extra += ", %d byte(s) differ" % s.differing_bytes
        lines.append("  %-34s %s%s" % (s.name, s.status, extra))
    if r.runs is not None:
        lines.append("")
        lines.append("differing byte runs (%d):" % len(r.runs))
        for run in r.runs:
            obj = " %d" % run.object if run.object is not None else ""
            lines.append("  0x%07x  %5d  %s%s" % (run.offset, run.length, run.region, obj))
        if r.unexplained:
            lines.append("  UNEXPLAINED: %d run(s) outside every known region" % len(r.unexplained))
    lines.append("")
    lines.append("GuiData objects:")
    for o in r.objects:
        if o.changed or o.added or o.removed:
            parts = []
            if o.changed:
                parts.append("changed %s" % _ids(o.changed))
            if o.added:
                parts.append("added %s" % _ids(o.added))
            if o.removed:
                parts.append("removed %s" % _ids(o.removed))
            lines.append("  table %d: %s" % (o.table, "; ".join(parts)))
    if not any(o.changed or o.added or o.removed for o in r.objects):
        lines.append("  none differ")
    if r.pages:
        changed = [p for p in r.pages if p.bbox or p.error]
        lines.append("")
        lines.append("pages rendered: %d; changed: %d" % (len(r.pages), len(changed)))
        for p in changed:
            name = "root %d%s %s" % (p.root, " (screen %d)" % p.screen if p.screen is not None
                                     else "", p.header.strip() or "")
            if p.error:
                lines.append("  [%s] %s: RENDER FAILED: %s" % (p.config, name, p.error))
            else:
                lines.append("  [%s] %s: %d px in %s" % (p.config, name, p.changed_pixels,
                                                         tuple(p.bbox)))
    return "\n".join(lines)


def _ids(ids: list[int]) -> str:
    return ", ".join(str(i) for i in ids[:20]) + (" … (%d)" % len(ids) if len(ids) > 20 else "")
