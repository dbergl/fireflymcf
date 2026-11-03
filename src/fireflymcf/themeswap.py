"""Replace the panel's colour scheme: the chrome constants GuiCode's theme-init writes at boot,
every GuiData field that carries the same colours inline, and the GuiData mode-6 palette.

**Where the colours live (13.6).** Three places, and a re-theme must reach all three or the
stock colours show through:

1. **GuiCode theme-init** (`FUN_00247bf8`, the straight-line block at `0x248316`-`0x248388`,
   GUICODE Finding 18). Six chrome colours are formed there and stored into SRAM colour cells
   that mode-7 fills read (and that the runtime-state cells copy, Finding 34):

   | role | formed by | cells |
   |---|---|---|
   | bg | literal word `0x2485cc` (loaded once, `0x248318`) | `0x2003a124` (idx 3) |
   | header | `add.w r2,r2,#0xa90000; addw r2,r2,#0x5f3` on bg at `0x24831e` | `0x20039a88` (idx 4) |
   | panel | literal word `0x2485d8` (loaded once, `0x24832a`) | `0x20039974` (idx 8, master) |
   | button | literal word `0x2485dc` (loaded once, `0x24832e`) | `0x2003a578`, `0x20039a58` (master) |
   | active | `movw r1,#0xaeef` at `0x248336` | `0x2003a1a0` (ON master) |
   | rail | `add.w r3,r3,#0x3ac000; addw r3,r3,#0x37a` on button at `0x248340` | `0x2003a330`, `0x2003a0c8` |

   The literals are patched in place. Each 8-byte add-chain is rewritten as `movw`+`movt`
   (also 8 bytes), so header and rail take any colour, independent of bg and button. `active`
   has only 4 bytes; it becomes `add.w`/`sub.w r1, r3, #modimm` from the button value (r3 holds
   it there, loaded at `0x24832e` and unchanged until `0x248340`), so it lands on the nearest
   colour a Thumb-2 modified immediate reaches from the new button colour.
2. **GuiData inline (mode-3) fields** carrying a stock theme colour as a BGR u24: 155 on a reference
   build (the stock body boxes, the CLIMATE header, every page fireflymcf authored).
3. **The GuiData mode-6 palette** (`0x90010010`, MCF-FORMAT §5.5): entries 4/14-18 are the
   theme colours (the CLIMATE empty mode cells read entry 16).

Tank wells (wtype-32 fillB mode-7 idx 161) share the button colour's cell; `tank_wells`
repoints them to another role's static index so an empty tank does not read as button-filled.

`extra` remaps stand-alone colours that only live inline or in the mode-6 palette (not in the
theme-init): stock's own (`(27, 117, 188)`, the old Firefly blue, in palette entry 5) and the
ones fireflymcf's level ops author (`levelpage.BODY` `(38, 44, 96)`, the van body and the LEVEL
SETUP body).

Not touched: white (text and icons), the alarm red, the tank fill colours, bitmap images.

Pinned to 13.6's GuiCode: every patched instruction and literal is asserted to hold its stock
bytes first, and anything else refuses (`ThemeSwapError`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import guidata, records, theme

RGB = tuple  # (r, g, b)

# role -> stock RGB888 (13.6, theme.mode7_palette / Finding 34)
STOCK = {
    "bg": 0x15183A, "panel": 0x272862, "button": 0x4151A3,
    "header": 0xBE1E2D, "rail": 0x7C151D, "active": 0x00AEEF,
}
ROLES = tuple(STOCK)

# role -> the static mode-7 index whose cell holds that colour (tank_wells targets)
STATIC_INDEX = {"bg": 3, "header": 4, "panel": 8, "rail": 10, "button": 161}
TANK_WELL_INDEX = 161

# Runtime addresses in the GuiCode section (mapped at 0x240000 on every build).
_LIT_BG, _LIT_PANEL, _LIT_BUTTON = 0x2485CC, 0x2485D8, 0x2485DC
_HEADER_CHAIN, _ACTIVE_MOVW, _RAIL_CHAIN = 0x24831E, 0x248336, 0x248340

GUIDATA_PALETTE_OFFSET = 0x10010   # the mode-6 palette's offset in GuiData (render.py agrees)
GUIDATA_PALETTE_LEN = 30


class ThemeSwapError(ValueError):
    pass


# ----------------------------------------------------------------------- Thumb-2 encoders

def _modimm_table() -> dict:
    """Every value a Thumb-2 modified immediate encodes -> (i, imm3, imm8)."""
    out: dict = {}
    for x in range(256):
        out.setdefault(x, (0, 0, x))
        out.setdefault(x | x << 16, (0, 1, x))
        out.setdefault(x << 8 | x << 24, (0, 2, x))
        out.setdefault(x * 0x01010101, (0, 3, x))
    for rot in range(8, 32):
        for low7 in range(128):
            b = 0x80 | low7
            v = ((b >> rot) | (b << (32 - rot))) & 0xFFFFFFFF
            out.setdefault(v, ((rot >> 4) & 1, (rot >> 1) & 7, ((rot & 1) << 7) | low7))
    return out


_MODIMM = _modimm_table()


def enc_dp_imm(sub: bool, rd: int, rn: int, value: int) -> bytes:
    """`add.w`/`sub.w rd, rn, #modimm` (T3)."""
    i, imm3, imm8 = _MODIMM[value]
    op = 13 if sub else 8
    return struct.pack("<HH", 0xF000 | (i << 10) | (op << 5) | rn, (imm3 << 12) | (rd << 8) | imm8)


def enc_addw(rd: int, rn: int, imm12: int) -> bytes:
    """`addw rd, rn, #imm12` (T4)."""
    i, imm3, imm8 = (imm12 >> 11) & 1, (imm12 >> 8) & 7, imm12 & 0xFF
    return struct.pack("<HH", 0xF200 | (i << 10) | rn, (imm3 << 12) | (rd << 8) | imm8)


def enc_movw(rd: int, imm16: int, top: bool = False) -> bytes:
    """`movw rd, #imm16` (T3), or `movt` with `top`."""
    i, imm4, imm3, imm8 = (imm16 >> 11) & 1, imm16 >> 12, (imm16 >> 8) & 7, imm16 & 0xFF
    return struct.pack("<HH", (0xF2C0 if top else 0xF240) | (i << 10) | imm4,
                       (imm3 << 12) | (rd << 8) | imm8)


def enc_const(rd: int, value: int) -> bytes:
    """`movw rd, #lo; movt rd, #hi` -- any 32-bit constant in 8 bytes."""
    return enc_movw(rd, value & 0xFFFF) + enc_movw(rd, value >> 16, top=True)


# The stock bytes each patch site must hold (encoded with the same encoders: a self-check).
_STOCK_HEADER_CHAIN = enc_dp_imm(False, 2, 2, 0xA90000) + enc_addw(2, 2, 0x5F3)
_STOCK_RAIL_CHAIN = enc_dp_imm(False, 3, 3, 0x3AC000) + enc_addw(3, 3, 0x37A)
_STOCK_ACTIVE = enc_movw(1, 0xAEEF)


def _dist(a: int, b: int) -> int:
    return sum(((a >> s & 0xFF) - (b >> s & 0xFF)) ** 2 for s in (16, 8, 0))


def nearest_from(base: int, target: int) -> tuple[int, bool, int]:
    """(colour, sub, modimm): the RGB888 colour nearest `target` that `base ± modimm` reaches."""
    best = None
    for m in _MODIMM:
        for sub in (False, True):
            got = base - m if sub else base + m
            if 0 <= got <= 0xFFFFFF:
                cand = (_dist(got, target), got, sub, m)
                if best is None or cand < best:
                    best = cand
    return best[1], best[2], best[3]


def rgb_int(c) -> int:
    r, g, b = c
    for v in (r, g, b):
        if not (isinstance(v, int) and 0 <= v <= 255):
            raise ThemeSwapError("a colour wants three ints 0-255, got %r" % (c,))
    return r << 16 | g << 8 | b


def int_rgb(v: int) -> RGB:
    return (v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF)


# ----------------------------------------------------------------------------- the plan

@dataclass
class Plan:
    """Every byte edit a re-theme makes: `(file_offset, old, new, note)`, and the colours each
    role actually takes (`active` may differ from the request)."""
    edits: list
    applied: dict          # role -> RGB888 int
    inline: int            # GuiData mode-3 fields rewritten
    palette: int           # mode-6 palette entries rewritten
    wells: int             # tank-well records repointed
    literals: int = 0      # GuiCode colour literals rewritten (extra)
    tank_fills: int = 0    # tank fill fields rewritten (tank_fills)


def _guicode(data: bytes):
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    return sec, base, (lambda addr: sec.offset + (addr - base))


def inline_fields(data: bytes) -> list[tuple[int, int, int]]:
    """`(file_offset, rgb888, object)` for every mode-3 colour field in every object's records,
    each offset once."""
    gs, _ = guidata.guidata_extent(data)
    seen: set = set()
    out = []
    for idx, rel in enumerate(guidata.object_tables(data)[1]):
        if not rel:
            continue
        try:
            recs = records.object_records(data, gs + rel)
        except Exception:        # an unused / non-object table entry decodes to garbage
            continue
        for rec in recs:
            for (role, mode, val), off in zip(rec.colors, rec.color_off):
                if mode == 3 and off is not None and off not in seen:
                    seen.add(off)
                    out.append((off, val, idx))
    return out


def stored_colour_literals(data: bytes) -> dict:
    """`{literal runtime address: rgb888}` for every GuiCode literal-pool word whose **every**
    PC-relative load is stored, unchanged, straight into SRAM (`ldr rT,[pc,#]` ... `str rT,[rB]`
    with rB a PC-loaded SRAM address and nothing done to rT between). Those words are colour
    constants by use, not by value, so rewriting one cannot change a non-colour constant that
    happens to share its value. A literal loaded anywhere else (returned with `bx lr`, compared,
    added to) is left out -- the stock keypad-battery helper's green is one.

    A linear register tracer, like `theme._trace_constant_stores_impl`, with the same clearing
    rules: register state is dropped at every branch/call/return, `it`, and data island, except
    that an unconditional `b` keeps the loaded register for its target's first store (the
    two-arm `ldr r3,=a; b join; ldr r3,=b; join: ldr r0,=cell; str r3,[r0]` select that
    `voltagebattery` emits) -- followed by decoding the target, never by guessing.
    """
    import capstone
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

    sec, base, fo = _guicode(data)
    code = data[sec.offset:sec.offset + sec.length]
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def word(addr):
        off = addr - base
        return struct.unpack_from("<I", code, off)[0] if 0 <= off <= len(code) - 4 else None

    loads: dict = {}          # literal addr -> number of loads
    stored: dict = {}         # literal addr -> number of loads proven stored to SRAM

    def follow(start, reg, seed, budget=6):
        """From `start`, is `reg` stored to a PC-loaded SRAM address before anything else
        touches it? `seed` is the PC-loaded registers live at `start`. Follows at most one
        unconditional `b` (the seed does not survive it)."""
        addr, regs, jumps = start, dict(seed), 0
        regs.pop(reg, None)
        while budget > 0:
            off = addr - base
            ins = next(md.disasm(code[off:off + 4], addr), None) if 0 <= off < len(code) else None
            if ins is None:
                return False
            m, ops = ins.mnemonic, ins.operands
            if m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                    and ops[1].mem.base == ARM_REG_PC and ops[0].reg != reg:
                regs[ops[0].reg] = word((ins.address & ~3) + 4 + ops[1].mem.disp)
            elif m in ("str", "str.w") and len(ops) == 2 and ops[0].reg == reg \
                    and ops[1].type == ARM_OP_MEM and ops[1].mem.index == 0 \
                    and ops[1].mem.disp == 0 and ops[1].mem.base in regs:
                tgt = regs[ops[1].mem.base]
                return tgt is not None and 0x20000000 <= tgt < 0x20080000
            elif m in ("b", "b.w") and len(ops) == 1 and ops[0].type == ARM_OP_IMM and jumps == 0:
                addr, jumps, regs = ops[0].imm, 1, {}
                budget -= 1
                continue
            else:
                _, written = ins.regs_access()
                if reg in written or reg in ins.regs_access()[0] or ins.group(capstone.CS_GRP_JUMP) \
                        or ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_RET):
                    return False
            addr += ins.size
            budget -= 1
        return False

    pos = 0
    while pos < len(code):
        end = pos
        live: dict = {}            # registers holding a PC-loaded word, linear, cleared on flow
        for ins in md.disasm(code[pos:], base + pos):
            end = ins.address - base + ins.size
            ops = ins.operands
            if ins.mnemonic in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                    and ops[1].mem.base == ARM_REG_PC:
                la = (ins.address & ~3) + 4 + ops[1].mem.disp
                v = word(la)
                if v is not None and v <= theme.RGB888_MAX:
                    loads[la] = loads.get(la, 0) + 1
                    if follow(ins.address + ins.size, ops[0].reg, live):
                        stored[la] = stored.get(la, 0) + 1
                live[ops[0].reg] = v
                continue
            if ins.group(capstone.CS_GRP_JUMP) or ins.group(capstone.CS_GRP_CALL) \
                    or ins.group(capstone.CS_GRP_RET) or ins.mnemonic == "it":
                live.clear()
                continue
            for r in ins.regs_access()[1]:
                live.pop(r, None)
        pos = end if end > pos else pos + 2
    return {la: word(la) for la, n in loads.items() if stored.get(la) == n}


def _tank_fill_offsets(data: bytes) -> dict:
    """`{file offset: rgb888}` of the inline (mode-3) fill of every HOME tank: the wtype-33 record
    straight after a wtype-32 well whose fillB is mode-7 idx 161 (objects 719-726 on 13.6;
    `GUICODE.md` Finding 18's tank addendum)."""
    gs, _ = guidata.guidata_extent(data)
    out = {}
    for rel in guidata.object_tables(data)[1]:
        if not rel:
            continue
        try:
            recs = records.object_records(data, gs + rel)
        except Exception:
            continue
        for well, fill in zip(recs, recs[1:]):
            is_well = well.wtype == 32 and any(
                c[0] == "fillB" and c[1] == 7 and c[2] == TANK_WELL_INDEX for c in well.colors)
            if is_well and fill.wtype == 33:
                for (role, mode, val), off in zip(fill.colors, fill.color_off):
                    if role == "fillA" and mode == 3 and off is not None:
                        out[off] = val
    return out


def plan(data: bytes, colours: dict, tank_wells: str | None = None,
         extra: list | None = None, tank_fills: list | None = None) -> Plan:
    """The edits that give `data` the palette `colours` (`{role: (r, g, b)}`, any subset of
    `ROLES`; an omitted role keeps its stock colour).

    `extra` is `[(from_rgb, to_rgb), ...]`: stand-alone colours that are not theme-init roles
    and live only inline (mode-3 fields) or in the mode-6 palette -- `levelpage.BODY`
    `(38, 44, 96)`, the old Firefly blue `(27, 117, 188)`. Each is remapped wherever it occurs
    in those two places, and in any GuiCode literal word `stored_colour_literals` proves is
    only ever stored into SRAM (the theme-init's green and black, a fireflymcf routine's
    colour select). A `from` that is a role's stock colour is refused (use `colours`).

    `tank_fills` is `[(from_rgb, to_rgb), ...]` applied only to the HOME tanks' inline fill
    fields (`_tank_fill_offsets`), so the tank grey can change without every UI grey."""
    unknown = set(colours) - set(ROLES)
    if unknown:
        raise ThemeSwapError("unknown theme role(s) %s; the roles are %s"
                             % (sorted(unknown), ", ".join(ROLES)))
    if tank_wells is not None and tank_wells not in STATIC_INDEX:
        raise ThemeSwapError("tank_wells wants one of %s, got %r"
                             % (", ".join(STATIC_INDEX), tank_wells))
    want = {r: rgb_int(colours[r]) if r in colours else STOCK[r] for r in ROLES}
    extra_map: dict = {}
    for pair in extra or ():
        if len(pair) != 2:
            raise ThemeSwapError("extra wants [from, to] pairs, got %r" % (pair,))
        f, t = rgb_int(pair[0]), rgb_int(pair[1])
        if f in STOCK.values():
            raise ThemeSwapError("extra colour %s is a theme role's stock colour; set the role "
                                 "in colours instead" % (tuple(pair[0]),))
        if f in extra_map:
            raise ThemeSwapError("extra maps %s twice" % (tuple(pair[0]),))
        extra_map[f] = t
    tank_map: dict = {}
    for pair in tank_fills or ():
        if len(pair) != 2:
            raise ThemeSwapError("tank_fills wants [from, to] pairs, got %r" % (pair,))
        tank_map[rgb_int(pair[0])] = rgb_int(pair[1])
    tank_offs = _tank_fill_offsets(data) if tank_map else {}
    sec, base, fo = _guicode(data)

    def u32(addr):
        return struct.unpack_from("<I", data, fo(addr))[0]

    def at(addr, n):
        return bytes(data[fo(addr):fo(addr) + n])

    checks = [(at(_HEADER_CHAIN, 8), _STOCK_HEADER_CHAIN, "header add-chain"),
              (at(_RAIL_CHAIN, 8), _STOCK_RAIL_CHAIN, "rail add-chain"),
              (at(_ACTIVE_MOVW, 4), _STOCK_ACTIVE, "active movw")]
    lits = [(_LIT_BG, "bg"), (_LIT_PANEL, "panel"), (_LIT_BUTTON, "button")]
    for got, exp, what in checks:
        if got != exp:
            hw1 = struct.unpack_from("<H", got)[0]
            # our own rewrite: movw (the chains) or add.w/sub.w (active) in place of stock
            raised = (hw1 & 0xFBF0) == 0xF240 or (hw1 & 0xFBE0) in (0xF100, 0xF1A0)
            raise ThemeSwapError(
                "GuiCode %s holds %s, not 13.6's %s: %s" % (
                    what, got.hex(), exp.hex(),
                    "already re-themed (set_theme runs once, on a stock theme)" if raised
                    else "this build's theme-init is not the one this op was traced on"))
    for addr, role in lits:
        if u32(addr) != STOCK[role]:
            raise ThemeSwapError("GuiCode literal %#x holds %#08x, not 13.6's %s %#08x (already "
                                 "re-themed? set_theme runs once, on a stock theme)"
                                 % (addr, u32(addr), role, STOCK[role]))

    edits = []

    def put(off, old, new, note):
        if old != new:
            edits.append((off, bytes(old), bytes(new), note))

    for addr, role in lits:
        put(fo(addr), struct.pack("<I", STOCK[role]), struct.pack("<I", want[role]),
            "theme %s literal %#x" % (role, addr))
    put(fo(_HEADER_CHAIN), _STOCK_HEADER_CHAIN, enc_const(2, want["header"]),
        "theme header: add-chain -> movw/movt %#08x" % want["header"])
    put(fo(_RAIL_CHAIN), _STOCK_RAIL_CHAIN, enc_const(3, want["rail"]),
        "theme rail: add-chain -> movw/movt %#08x" % want["rail"])
    applied = dict(want)
    if want["active"] != STOCK["active"] or want["button"] != STOCK["button"]:
        if want["active"] <= 0xFFFF:
            active_bytes = enc_movw(1, want["active"])     # red 0: the movw still holds it
            applied["active"] = want["active"]
            how = "movw"
        else:
            got, sub, m = nearest_from(want["button"], want["active"])
            active_bytes = enc_dp_imm(sub, 1, 3, m)
            applied["active"] = got
            how = "button %s %#x" % ("-" if sub else "+", m)
        put(fo(_ACTIVE_MOVW), _STOCK_ACTIVE, active_bytes,
            "theme active %#08x (%s; asked %#08x)" % (applied["active"], how, want["active"]))

    remap = {STOCK[r]: applied[r] for r in ROLES if applied[r] != STOCK[r]}
    remap.update({f: t for f, t in extra_map.items() if f != t})
    inline = tanks = 0
    for off, val, obj in inline_fields(data):
        if off in tank_offs and val in tank_map:
            new = tank_map[val]
            put(off, bytes([val & 0xFF, val >> 8 & 0xFF, val >> 16]),
                bytes([new & 0xFF, new >> 8 & 0xFF, new >> 16]),
                "tank fill object %d %#08x -> %#08x" % (obj, val, new))
            tanks += 1
        elif val in remap:
            new = remap[val]
            put(off, bytes([val & 0xFF, val >> 8 & 0xFF, val >> 16]),
                bytes([new & 0xFF, new >> 8 & 0xFF, new >> 16]),
                "theme inline fill object %d %#08x -> %#08x" % (obj, val, new))
            inline += 1

    gs, _ = guidata.guidata_extent(data)
    pb = gs + GUIDATA_PALETTE_OFFSET
    pal = 0
    for i in range(GUIDATA_PALETTE_LEN):
        v = struct.unpack_from("<I", data, pb + 4 * i)[0]
        if v in remap:
            put(pb + 4 * i, struct.pack("<I", v), struct.pack("<I", remap[v]),
                "theme mode-6 palette[%d] %#08x -> %#08x" % (i, v, remap[v]))
            pal += 1

    wells = 0
    if tank_wells is not None and STATIC_INDEX[tank_wells] != TANK_WELL_INDEX:
        to = STATIC_INDEX[tank_wells]
        for idx, rel in enumerate(guidata.object_tables(data)[1]):
            if not rel:
                continue
            try:
                recs = records.object_records(data, gs + rel)
            except Exception:
                continue
            for rec in recs:
                if rec.wtype != 32:
                    continue
                for (role, mode, val), off in zip(rec.colors, rec.color_off):
                    if role.startswith("fillB") and mode == 7 and val == TANK_WELL_INDEX:
                        put(off, struct.pack("<H", val), struct.pack("<H", to),
                            "tank well object %d %s idx %d -> %d (%s)"
                            % (idx, role, val, to, tank_wells))
                        wells += 1
    lits = 0
    xmap = {f: t for f, t in extra_map.items() if f != t}
    if xmap:
        for la, v in sorted(stored_colour_literals(data).items()):
            if v in xmap:
                put(fo(la), struct.pack("<I", v), struct.pack("<I", xmap[v]),
                    "GuiCode colour literal %#x %#08x -> %#08x" % (la, v, xmap[v]))
                lits += 1
    return Plan(edits, applied, inline, pal, wells, lits, tanks)


def decoded_theme(data: bytes) -> dict:
    """`{role: rgb888}` as the GuiCode now forms them, read back by `theme.mode7_palette`
    (header, rail via their cells) and the literals. For verification."""
    pal = theme.mode7_palette(data)
    out = {}
    for role, idx in (("bg", 3), ("header", 4), ("panel", 8), ("rail", 10)):
        if idx in pal:
            out[role] = rgb_int(pal[idx])
    sec, base, fo = _guicode(data)
    out["button"] = struct.unpack_from("<I", data, fo(_LIT_BUTTON))[0]
    return out
