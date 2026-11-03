"""Drive the emulated panel by control name and simulated bus value.

A `Scenario` boots an image on the emulator (`machine.Machine`) with the demo network
(`peers.demo_network`: the G12 running the image's own G12 logic, dimmers, inverter, solar,
APS-500) and a recorder on the bus, then lets a test say what a person would do and what the
coach would see:

    s = Scenario(image)
    s.boot()                          # accepts the terms page, lands on HOME
    s.tap("Main Ceiling")             # by the label the page draws
    s.run(2.0)                        # simulated seconds
    s.frames(dgn=0x1FEDB)             # what the panel sent
    s.goto("OPTIONS"); s.tap("Heat Pump"); s.g12_selector(0xEB)
    s.set_tank_raw(1, 63000)          # a G12 sensor input

**Where a control is.** A control is resolved on the page the panel is showing *now*, from the
image under test: `layout.Screen.walk` (the layout replay `render.screen_placements` composes
from; `screen_placements` itself drops touch regions and object indices, which this needs) over
the current page's root, with every type-6 switch driven by the value its variable holds in
emulated memory at that moment, so a floorplan- or option-dependent page resolves as drawn. A
label's tap point is the centre of the touch-region record (wtype 12) that contains the label's
centre, preferring one of the label's own object, then the smallest; a label with no touch region
around it falls back to its own box. Never a hard-coded pixel. The panel's hit test returns the
lowest matching touch id (`holdregion`, `0x00258BB2`), so a tap point inside a lower-id region is
moved to a point of the chosen region no lower-id region covers.

**Which page is showing** is the value of the variable the top-level screen switch reads: a
screen is a candidate of that wtype-6 switch (`layout._screen_switch_record`, located
structurally), so the slot it carries (`value_slot`, 2 on 13.6 -> SRAM `0x2003A1FC`, u8, through
`layout.variable_register`) holds the id of the screen drawn, and `layout.screen_object_map`
maps that id to its page. (`GuiCodeState+0x20A`, the navigation byte, is not it: it stays 0 while
the terms page, screen 96, is up.) The emulator gate is 13.6-only.

**Pages are screen ids, not header text.** A header rename is the commonest user edit, so HOME
is screen 0 whatever its title says. The rail pages are screens 0-4 (the screen switch is by
definition the one covering {0..4}); OPTIONS is the screen `navmap.routes` resolves for the "Main
Options" tab's touch id `MAIN_OPTIONS_ID`. The names HOME, ELECTRICAL, LIGHTS, CLIMATE (CONTROL),
SETTINGS, OPTIONS and any header the image draws are aliases resolving to those screens.

**Navigation** (`goto`) knows the rail and OPTIONS, all without reading a caption:
* the rail: the object on HOME whose touch regions `navmap` routes to screens 1-3; its five
  regions top to bottom are screens 0-4 (13.6: ids `0x7D0`-`0x7D3`, `0x838`);
* OPTIONS: SETTINGS, hold (`FLOORPLAN_HOLD_S`; ~3.2 s on the panel, `EMULATOR-NOTES.md`) the touch
  region around the floorplan-name switch (`options._find_floorplan_switch`) -> ADDITIONAL OPTIONS,
  touch id `PROCEED_ID` -> FLOORPLAN SELECTION, touch id `MAIN_OPTIONS_ID` -> OPTIONS. Each step
  waits for the page carrying the next step's touch id, the last for the OPTIONS screen.
Anything else raises `ScenarioError("no known path to page X")`. The terms page at boot is
accepted by its only touch region, again not by its caption.

**Time.** `run` advances simulated time in `CHUNK_S` slices; between slices the wall-clock budget
(`timeout_s`, counted from construction) is checked. A CPU fault (the machine's stop reason, or an
exception out of a peripheral hook) raises `ScenarioError("fault ...")`.

Most touch actions run on RELEASE (GuiCode's `0xFF` finger-lifted handler, `GUICODE.md`
Finding 83), so `tap` presses, holds for `max(hold_s, PRESS_S)`, releases, and runs `RELEASE_S`.
"""
from __future__ import annotations

import os
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import guidata, layout, options, records
from . import rvc
from .canbus import Peer

PANEL_SA = 0x9F
CHUNK_S = 0.25                   # simulated seconds per run slice between wall-clock checks
PRESS_S = 1 / 6                  # a tap's press, as tools/drive.py
RELEASE_S = 0.5                  # run after the release so its 0xFF handler and sends complete
BOOT_S = 3.0                     # the terms page (fresh EEPROM) or HOME is up by then
ARRIVE_S = 5.0                   # longest wait for a navigation step to land
FLOORPLAN_HOLD_S = 4.0           # the Floorplan button's protected hold (~3.2 s on the panel)

# A variable's type byte (`+0x4492C`) is FUN_00251E38's read width; (size, signed). Types 7+
# (float, 64-bit, none, colour) never drive a type-6 switch's match and are not read.
_VAR_READ = {0: (1, False), 1: (1, False), 2: (1, True), 3: (2, False), 4: (2, True),
             5: (4, False), 6: (4, True)}

# Pages are identified by SCREEN ID, never by header text: a header rename (`rename_label` on a
# page's title, e.g. HOME -> MYCOACHNAME) is the commonest user edit and must not move a page.
# The rail screens are 0-4 by the screen switch's own contract (`layout._screen_switch_record`
# is defined as the switch covering {0..4}); OPTIONS is the screen `navmap.routes` resolves for
# the "Main Options" tab's touch id. Names below (and the stock headers) are aliases only; any
# header the image actually draws also resolves, so "MYCOACHNAME" finds screen 0 too.
RAIL_SCREENS = {"home": 0, "electrical": 1, "lights": 2, "climate": 3, "climate control": 3,
                "settings": 4}
OPTIONS_NAMES = {"options"}
# Touch ids the GuiCode ACTION dispatcher acts on (13.6), label-independent:
MAIN_OPTIONS_ID = 0x7FF      # FLOORPLAN SELECTION's "Main Options" tab; navmap: -> screen 47
PROCEED_ID = 0x7E0           # ADDITIONAL OPTIONS' "Proceed" (-> screen 16, seen in emulation;
                             # its store is not one `navmap` resolves)


class ScenarioError(Exception):
    """A scenario step could not be carried out (unknown control, no path, timeout, fault)."""


@dataclass(frozen=True)
class Frame:
    """One frame seen on the emulated bus. `t` is simulated seconds since boot."""
    t: float
    can_id: int
    dgn: int
    sa: int
    data: bytes


def _norm(text: str) -> str:
    """Labels compare whitespace-normalised: 'Main Ceiling' finds the panel's 'Main\\nCeiling'
    and 'Heat Pump' its '   Heat Pump'."""
    return " ".join(text.split())


class _Recorder(Peer):
    """Every frame on the bus: the panel's (CanBus._from_firmware) and every peer's."""

    def __init__(self, machine):
        self.m = machine
        self.log: list[Frame] = []

    def on_frame(self, frame):
        _, dgn, sa = rvc.split_id(frame.can_id)
        self.log.append(Frame(self.m.cycles / self.m.cpu_hz, frame.can_id, dgn, sa,
                              bytes(frame.data)))


class Scenario:
    def __init__(self, image: bytes, *, eeprom: Path | None = None, timeout_s: float = 300.0):
        from .machine import Machine

        self._t0 = time.monotonic()
        self.timeout_s = timeout_s
        self.image = bytes(image)
        self._m = Machine()
        if eeprom is not None:
            self._m.eeprom.attach_file(str(eeprom))
        fd, tmp = tempfile.mkstemp(suffix=".bin", prefix="scenario-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self.image)
            self._m.load_mcf(tmp)
        finally:
            os.unlink(tmp)
        self._booted = False
        self._recorder: _Recorder | None = None
        self._screen = layout.Screen(self.image)
        self._screen_map = layout.screen_object_map(self.image)
        self._screen_cell = self._screen_variable()
        self._headers = self._page_headers()
        self._vars = self._variable_cells()
        self._rail = self._rail_ids()
        self._options_screen = self._route_target(MAIN_OPTIONS_ID)

    def _route_target(self, touch_id: int) -> int | None:
        from .. import navmap

        hits = {r.screen for r in navmap.routes(self.image) if r.touch_id == touch_id}
        return hits.pop() if len(hits) == 1 else None

    def _rail_ids(self) -> dict[int, int]:
        """{screen 0-4: rail touch id}, found structurally on HOME's static layout: the object
        whose touch regions `navmap` routes to screens 1, 2 and 3 is the rail; its five regions,
        top to bottom, are screens 0-4 (the HOME and SETTINGS taps are stores `navmap` does not
        resolve, so their ids come from their place on the rail). Empty if not found."""
        from .. import navmap

        home = self._screen_map.get(0)
        if home is None:
            return {}
        routes = {}
        for r in navmap.routes(self.image):
            routes.setdefault(r.touch_id, set()).add(r.screen)
        regions = self._regions(self._screen.walk(home))
        objs = {o for _, t, o in regions if routes.get(t, set()) & {1, 2, 3}}
        for obj in objs:
            mine = sorted((b[1], t) for b, t, o in regions if o == obj)
            if len(mine) == 5 and all(i in routes.get(t, ()) for i, (_, t) in
                                      enumerate(mine) if i in (1, 2, 3)):
                return {i: t for i, (_, t) in enumerate(mine)}
        return {}

    # -- static reads of the image -------------------------------------------------------
    def _page_headers(self) -> dict[int, str]:
        """{root object: header} for every screen root, as `fireflymcf pages` prints them."""
        gs, ge = guidata.guidata_extent(self.image)
        table = guidata.object_tables(self.image)[1]
        out = {}
        for root in set(self._screen_map.values()):
            try:
                lab = options._first_label(options._own_records(self.image, gs, ge, table, root))
            except Exception:
                lab = None
            out[root] = _norm(lab) if lab else ""
        return out

    def _screen_variable(self) -> tuple[int, int]:
        """(address, size) of the variable the top-level screen switch reads."""
        gs, _ = guidata.guidata_extent(self.image)
        sw = layout._screen_switch_record(self.image, guidata.find_match_table(self.image), gs)
        addr, vtype = layout.variable_register(self.image, sw.value_slot)
        if vtype not in _VAR_READ:
            raise ScenarioError(f"the screen switch's variable has type {vtype}, not an integer")
        return addr, _VAR_READ[vtype][0]

    def _variable_cells(self) -> list[tuple[int, int, int, bool]]:
        """[(slot, address, size, signed)] for every slot a switch could read."""
        from .. import celltables

        gs, _ = guidata.guidata_extent(self.image)
        dptr = gs + celltables.cell_table_off(self.image)
        vtype = gs + celltables.type_table_off(self.image)
        out = []
        for slot in range(celltables.row_count(self.image)):
            read = _VAR_READ.get(self.image[vtype + slot])
            if read is not None:
                out.append((slot, struct.unpack_from("<I", self.image, dptr + slot * 4)[0], *read))
        return out

    # -- time ------------------------------------------------------------------------------
    @property
    def now(self) -> float:
        """Simulated seconds since boot."""
        return self._m.cycles / self._m.cpu_hz

    def _check_wall(self):
        spent = time.monotonic() - self._t0
        if spent > self.timeout_s:
            raise ScenarioError(f"timeout: {spent:.1f} s wall clock exceeds the scenario's "
                                f"{self.timeout_s:g} s (simulated t={self.now:.2f} s)")

    def run(self, seconds: float) -> None:
        """Advance `seconds` of simulated time."""
        if not self._booted:
            raise ScenarioError("boot() the scenario before running it")
        m = self._m
        end = m.cycles + int(max(0.0, seconds) * m.cpu_hz)
        chunk = int(CHUNK_S * m.cpu_hz)
        while m.cycles < end:
            self._check_wall()
            try:
                why = m.run(min(chunk, end - m.cycles))
            except Exception as e:      # an exception escaping a peripheral/peer hook
                raise ScenarioError(f"fault: {type(e).__name__}: {e} "
                                    f"(simulated t={self.now:.3f} s)") from e
            if why:
                raise ScenarioError(f"fault: {why} (simulated t={self.now:.3f} s)")
        self._check_wall()

    # -- boot ------------------------------------------------------------------------------
    def boot(self, accept_terms: bool = True) -> None:
        """Reset the panel onto the demo network and run it until it is up: with
        `accept_terms`, past the terms page (if shown) to HOME."""
        if self._booted:
            raise ScenarioError("the scenario is already booted")
        from .peers import demo_network

        self._m.boot()
        self._recorder = self._m.canbus.add_peer(_Recorder(self._m))
        demo_network(self._m.canbus, self._m.guidata)
        self._booted = True
        self.run(BOOT_S)
        if not accept_terms:
            return
        if self._screen_id() != 0:
            # The terms page (a fresh EEPROM) has exactly one touch region, I ACCEPT; found
            # structurally rather than by its caption.
            root = self._root()
            regions = self._regions(self._placed(root)) if root is not None else []
            if len(regions) != 1:
                raise ScenarioError(f"boot: the panel shows {self.page!r}, not HOME or a "
                                    "one-button terms page")
            box, tid, _ = regions[0]
            self._press(*self._tap_point(box, tid, regions), 0.0)
        self._await({0}, "boot: accepting the terms")

    # -- pages -------------------------------------------------------------------------------
    def _screen_id(self) -> int:
        addr, size = self._screen_cell
        return int.from_bytes(self.read_mem(addr, size), "little")

    def _root(self) -> int | None:
        return self._screen_map.get(self._screen_id())

    @property
    def screen(self) -> int:
        """The id of the screen showing now (`fireflymcf pages`' screen column)."""
        return self._screen_id()

    @property
    def page(self) -> str:
        """The header of the page showing now, whitespace-normalised (`fireflymcf pages`)."""
        root = self._root()
        return self._headers.get(root, "") if root is not None else f"<screen {self._screen_id()}>"

    def _screens_for(self, page: str) -> set[int]:
        """Screen ids `page` names: a structural name (HOME, ELECTRICAL, LIGHTS, CLIMATE
        CONTROL, SETTINGS, OPTIONS), or any header this image draws."""
        key = _norm(page).casefold()
        if key in RAIL_SCREENS:
            return {RAIL_SCREENS[key]}
        if key in OPTIONS_NAMES and self._options_screen is not None:
            return {self._options_screen}
        by_root = {}
        for sid, root in self._screen_map.items():
            by_root.setdefault(root, set()).add(sid)
        return {sid for r, h in self._headers.items() if h.casefold() == key
                for sid in by_root.get(r, ())}

    def _roots_for(self, page: str) -> set[int]:
        return {self._screen_map[s] for s in self._screens_for(page) if s in self._screen_map}

    def _await(self, screens, what: str) -> None:
        deadline = self.now + ARRIVE_S
        while self._screen_id() not in screens:
            if self.now >= deadline:
                raise ScenarioError(f"{what}: the panel shows screen {self._screen_id()} "
                                    f"({self.page!r}), expected screen "
                                    f"{' or '.join(map(str, sorted(screens)))}")
            self.run(0.1)

    def _await_touch_id(self, tid: int, what: str) -> None:
        """Wait until the page showing carries a touch region with id `tid` (the next step)."""
        deadline = self.now + ARRIVE_S
        while True:
            root = self._root()
            if root is not None and any(t == tid for _, t, _ in self._regions(self._placed(root))):
                return
            if self.now >= deadline:
                raise ScenarioError(f"{what}: the panel shows {self.page!r}, which has no "
                                    f"touch region {tid:#x}")
            self.run(0.1)

    def _floorplan_region(self) -> tuple[tuple, int, list]:
        """SETTINGS' Floorplan button: the touch region around the floorplan-name switch
        (`options._find_floorplan_switch`'s slot), found in the live walk's trace."""
        slot, _ = options._find_floorplan_switch(self.image)
        root = self._page_root(None)
        trace: list = []
        values = self._slot_values()
        placed = self._screen.walk(root, slot_values=values, trace=trace)
        regions = self._regions(placed)
        hits = set()
        for _key, _obj, _ri, rec, box, *_ in trace:
            if rec.wtype == 6 and rec.value_slot == slot:
                x, y = box[0], box[1]
                for r in regions:
                    if r[0][0] <= x < r[0][2] and r[0][1] <= y < r[0][3]:
                        hits.add(r)
        if len(hits) != 1:
            raise ScenarioError(f"goto OPTIONS: {len(hits)} touch regions hold the floorplan "
                                "name on SETTINGS, expected 1")
        box, tid, _ = hits.pop()
        return box, tid, regions

    def goto(self, page: str) -> None:
        """Navigate to `page` (HOME, ELECTRICAL, LIGHTS, CLIMATE CONTROL, SETTINGS, OPTIONS, or
        a header this image draws for one of them) by tapping its path."""
        targets = self._screens_for(page)
        if self._screen_id() in targets:
            return
        rail = targets & set(self._rail)
        if rail:
            sid = rail.pop()
            self._tap_touch_id(self._rail[sid], page)
            self._await({sid}, f"goto {page!r}")
        elif self._options_screen is not None and targets == {self._options_screen}:
            self.goto("SETTINGS")
            box, tid, regions = self._floorplan_region()
            self._press(*self._tap_point(box, tid, regions), FLOORPLAN_HOLD_S)
            self._await_touch_id(PROCEED_ID, f"goto {page!r}: holding Floorplan")
            self._tap_touch_id(PROCEED_ID, page)
            self._await_touch_id(MAIN_OPTIONS_ID, f"goto {page!r}: Proceed")
            self._tap_touch_id(MAIN_OPTIONS_ID, page)
            self._await(targets, f"goto {page!r}: Main Options")
        else:
            raise ScenarioError(f"no known path to page {page!r}")

    # -- controls ----------------------------------------------------------------------------
    def _slot_values(self) -> dict[int, int]:
        values = {}
        for slot, addr, size, signed in self._vars:
            raw = self._m.mem(addr, size)
            if raw is not None:
                values[slot] = int.from_bytes(bytes(raw), "little", signed=signed) & 0xFFFF
        return values

    def _placed(self, root: int) -> list:
        """The live layout of `root`: every type-6 switch driven by its variable's value now."""
        return self._screen.walk(root, slot_values=self._slot_values())

    def _labels(self, text: str, root: int | None, placed: list | None = None) -> list:
        if root is None:
            return []
        placed = self._placed(root) if placed is None else placed
        seen, out = set(), []
        for q in placed:
            if q.wtype != 12 and q.label and _norm(q.label) == _norm(text):
                key = (q.obj_index, q.x0, q.y0, q.x1, q.y1)
                if key not in seen:
                    seen.add(key)
                    out.append(q)
        return out

    def _page_root(self, page: str | None) -> int:
        if page is None:
            root = self._root()
            if root is None:
                raise ScenarioError(f"the panel shows an unknown screen {self._screen_id()}")
            return root
        roots = self._roots_for(page)
        if not roots:
            raise ScenarioError(f"no page named {page!r} on this image")
        return self._root() if self._root() in roots else min(roots)

    def _regions(self, placed: list) -> list[tuple[tuple, int, int]]:
        """[(box, touch id, object)] of every touch-region record placed."""
        out = []
        for q in placed:
            if q.wtype == 12:
                end = records.decode_record(self.image, q.record_offset).end
                tid = struct.unpack_from("<H", self.image, end - guidata.GROUP_REFERENCE_TAIL)[0]
                out.append(((q.x0, q.y0, q.x1, q.y1), tid, q.obj_index))
        return out

    def _resolve(self, control: str | int, root: int) -> tuple[tuple, int | None, list]:
        """(box, touch id or None, all regions) of `control` on `root`."""
        placed = self._placed(root)
        regions = self._regions(placed)
        where = self._headers.get(root, f"root {root}") or f"root {root}"
        if isinstance(control, int):
            own = [r for r in regions if r[2] == control]
            if len(own) != 1:
                raise ScenarioError(
                    f"object {control} has {len(own)} touch regions on page {where!r}"
                    + (f": {', '.join(str(b) for b, _, _ in own)}" if own else ""))
            return own[0][0], own[0][1], regions
        labels = self._labels(control, root, placed)
        if not labels:
            raise ScenarioError(f"no control labelled {control!r} on page {where!r}")
        if len(labels) > 1:
            raise ScenarioError(
                f"control {control!r} is ambiguous on page {where!r}: {len(labels)} labels match "
                f"(objects {', '.join(f'{q.obj_index} at {(q.x0, q.y0, q.x1, q.y1)}' for q in labels)})"
                "; pass an object index")
        q = labels[0]
        cx, cy = (q.x0 + q.x1) // 2, (q.y0 + q.y1) // 2
        around = [r for r in regions if r[0][0] <= cx < r[0][2] and r[0][1] <= cy < r[0][3]]
        if around:
            area = lambda r: (r[0][2] - r[0][0]) * (r[0][3] - r[0][1])  # noqa: E731
            best = min(around, key=lambda r: (r[2] != q.obj_index, area(r)))
            return best[0], best[1], regions
        # No touch region contains the label (a header, a caption): its own box, flagged by the
        # None touch id. A tap there reaches whatever region the panel finds, if any.
        return (q.x0, q.y0, q.x1, q.y1), None, regions

    def control_box(self, control: str | int, page: str | None = None) -> tuple[int, int, int, int]:
        """`(x0, y0, x1, y1)` of `control`'s touch region (its label box if it has none), on
        `page` (default: the page showing now), from the image under test."""
        return self._resolve(control, self._page_root(page))[0]

    @staticmethod
    def _tap_point(box, tid, regions) -> tuple[int, int]:
        x0, y0, x1, y1 = box
        lower = [b for b, t, _ in regions if tid is not None and t < tid]

        def free(x, y):
            return not any(b[0] <= x < b[2] and b[1] <= y < b[3] for b in lower)

        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        if free(cx, cy):
            return cx, cy
        pts = [(x, y) for y in range(y0, y1, 4) for x in range(x0, x1, 4) if free(x, y)]
        if not pts:
            raise ScenarioError(f"touch region {box} is covered by lower-id regions")
        return min(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)

    def _press(self, x: int, y: int, hold_s: float) -> None:
        self._m.touch.touch([(x, y)])
        try:
            self.run(max(hold_s, PRESS_S))
        finally:
            self._m.touch.touch([])
        self.run(RELEASE_S)

    def _tap_touch_id(self, tid: int, page: str) -> None:
        root = self._page_root(None)
        regions = self._regions(self._placed(root))
        mine = [r for r in regions if r[1] == tid]
        if not mine:
            raise ScenarioError(f"no known path to page {page!r} from page {self.page!r}")
        self._press(*self._tap_point(mine[0][0], tid, regions), 0.0)

    def tap(self, control: str | int, *, hold_s: float = 0.0, page: str | None = None) -> None:
        """Tap (or hold for `hold_s`) `control`: label text (whitespace-normalised, exactly one
        on the page) or an object index, on `page` (navigated to first) or the page showing."""
        if not self._booted:
            raise ScenarioError("boot() the scenario before tapping")
        if page is not None:
            self.goto(page)
        box, tid, regions = self._resolve(control, self._page_root(None))
        self._press(*self._tap_point(box, tid, regions), hold_s)

    # -- the bus and the G12 -------------------------------------------------------------------
    def _g12(self):
        from .g12 import G12
        from .peers import ProtocolG12

        for p in self._m.canbus.peers:
            if isinstance(p, (G12, ProtocolG12)):
                return p
        return None

    def set_tank_raw(self, sensor: int, raw: int) -> None:
        """Set the G12's raw tank sensor `sensor` (1 fresh, 2 grey, 3 black, 4 grey 2; high =
        empty). The G12 reads it into its cells once a second."""
        if not 0 <= raw <= 0xFFFF:
            raise ScenarioError(f"tank raw value {raw} is not a u16")
        g12 = self._g12()
        if g12 is None:
            raise ScenarioError("no emulated G12 on the bus (boot() first)")
        if hasattr(g12, "inputs"):
            g12.inputs["tanks"][sensor] = raw
        else:
            g12.tanks[sensor] = raw

    def g12_selector(self, selector: int) -> int | None:
        """The emulated G12's stored value for configuration selector `selector` (None: unset,
        the G12's 0xFFFF fresh-NVM value, or no G12)."""
        g12 = self._g12()
        if g12 is None:
            return None
        if hasattr(g12, "cell"):
            v = g12.cell(selector)
            return None if v == 0xFFFF else v
        return g12.sel.get(selector)

    def frames(self, *, dgn: int | None = None, sa: int | None = PANEL_SA,
               since: float = 0.0) -> list[Frame]:
        """Frames seen on the bus at or after `since`, filtered by DGN (`rvc.split_id`'s, PDU1
        destination included) and source address (default the panel's; None = any)."""
        log = self._recorder.log if self._recorder else []
        return [f for f in log if f.t >= since and (dgn is None or f.dgn == dgn)
                and (sa is None or f.sa == sa)]

    # -- the display and memory -----------------------------------------------------------------
    def screenshot(self, path: Path) -> np.ndarray:
        """Save the display as a PNG at `path`; returns the (480, 800, 3) RGB array."""
        from PIL import Image

        img = self._m.periph["LTDC"].render()
        if img is None:
            raise ScenarioError("the display is not configured yet")
        img = np.array(img, copy=True)
        Image.fromarray(img).save(path)
        return img

    def read_mem(self, addr: int, size: int) -> bytes:
        """`size` bytes of emulated RAM at `addr` (SRAM, SDRAM, QSPI-mapped GuiData)."""
        view = self._m.mem(addr, size)
        if view is None:
            raise ScenarioError(f"0x{addr:08X}+{size} is not plain emulated RAM")
        return bytes(view)
