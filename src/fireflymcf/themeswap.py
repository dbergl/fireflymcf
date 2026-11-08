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
   theme colours (the CLIMATE empty mode cells read entry 16). Its place and its 20 entries are
   read from the record decoder by `theme.colour_decode`; the words past it are not touched.

Tank wells (wtype-32 fillB mode-7, 13.6 idx 161) share the button colour's first cell;
`tank_wells` repoints them to another role's index so an empty tank does not read as
button-filled. Both indices are read from the located block (`ThemeInit.indices`, `.well_index`).

`extra` remaps stand-alone colours that only live inline or in the mode-6 palette (not in the
theme-init): stock's own (`(27, 117, 188)`, the old Firefly blue, in palette entry 5) and the
ones fireflymcf's level ops author (`levelpage.BODY` `(38, 44, 96)`, the van body and the LEVEL
SETUP body).

Not touched: white (text and icons), the alarm red, the tank fill colours, bitmap images.

Located, not pinned (2026-11-07): `locate_theme_init` finds the block by its 22-instruction
skeleton and reads the sites, cells and indices from it. 13.6 and 14.7 match (14.7
`0x248386..0x2483BC`, the same bytes); 11.23 has none and refuses. The block must replay the
six stock colours; an image `plan` has already re-themed matches the second skeleton
(`movw`/`movt` chains) and refuses as already re-themed (`ThemeSwapError`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import guidata, memo, records, theme

RGB = tuple  # (r, g, b)

# role -> stock RGB888 (13.6, theme.mode7_palette / Finding 34)
STOCK = {
    "bg": 0x15183A, "panel": 0x272862, "button": 0x4151A3,
    "header": 0xBE1E2D, "rail": 0x7C151D, "active": 0x00AEEF,
}
ROLES = tuple(STOCK)

# The roles `tank_wells` may repoint the wells to (each the first cell of a theme-init store).
TANK_WELL_ROLES = ("bg", "header", "panel", "rail", "button")

# The mode-6 palette's offset and length come from the code (`theme.colour_decode`): 13.6
# `0x90010010`, 20 entries, bounded by the next structure's literal `0x90010060` (2026-11-07).


class ThemeSwapError(ValueError):
    pass


class ThemeInitError(ThemeSwapError):
    """The theme-init block was not found in either skeleton's shape, was found more than
    once, or a store it makes is not to a type-10 colour cell."""


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


# ------------------------------------------------------------------- locating the theme-init

# The theme-init's 22 instructions, as `(label, shapes)` per position: a shape is
# `(mnemonic, operands)` with `L` a PC-relative literal load and `I` any immediate, so literal
# displacements and immediates are wildcards and only mnemonics and registers are matched.
# The stock form, 13.6 `0x248316..0x24834C` (`GUICODE.md` Finding 18):
_SKELETON_STOCK = (
    ("bg cell", (("ldr", "r3,L"),)), ("bg literal", (("ldr", "r2,L"),)),
    ("bg store", (("str", "r2,[r3]"),)),
    ("header cell", (("ldr", "r3,L"),)), ("header chain", (("add.w", "r2,r2,I"),)),
    ("header chain 2", (("addw", "r2,r2,I"),)), ("header store", (("str", "r2,[r3]"),)),
    ("panel cell", (("ldr", "r3,L"),)), ("panel literal", (("ldr", "r2,L"),)),
    ("panel store", (("str", "r2,[r3]"),)),
    ("button literal", (("ldr", "r3,L"),)), ("button cell", (("ldr", "r2,L"),)),
    ("button store", (("str", "r3,[r2]"),)),
    ("active cell", (("ldr", "r2,L"),)), ("active", (("movw", "r1,I"),)),
    ("active store", (("str", "r1,[r2]"),)),
    ("button cell 2", (("ldr", "r2,L"),)), ("button store 2", (("str", "r3,[r2]"),)),
    ("rail chain", (("add.w", "r3,r3,I"),)), ("rail chain 2", (("addw", "r3,r3,I"),)),
    ("rail cell", (("ldr", "r2,L"),)), ("rail store", (("str", "r3,[r2]"),)),
)
# The form `plan` leaves: each 8-byte chain a `movw`/`movt` constant, and `active` a `movw` or
# an `add.w`/`sub.w r1, r3, #modimm` from the button value in r3.
_SKELETON_RETHEMED = tuple(
    (label, {"header chain": (("movw", "r2,I"),), "header chain 2": (("movt", "r2,I"),),
             "active": (("movw", "r1,I"), ("add.w", "r1,r3,I"), ("sub.w", "r1,r3,I")),
             "rail chain": (("movw", "r3,I"),), "rail chain 2": (("movt", "r3,I"),)
             }.get(label, shapes))
    for label, shapes in _SKELETON_STOCK)
_SKELETONS = (("stock", _SKELETON_STOCK), ("rethemed", _SKELETON_RETHEMED))

# Skeleton positions: where each role's colour is formed, and each role's cell stores.
_POS = {label: k for k, (label, _s) in enumerate(_SKELETON_STOCK)}
_CELL_POS = {"bg": ("bg cell",), "header": ("header cell",), "panel": ("panel cell",),
             "button": ("button cell", "button cell 2"), "active": ("active cell",),
             "rail": ("rail cell",)}


@dataclass(frozen=True)
class ThemeInit:
    """The theme-init block as `locate_theme_init` reads it.

    `sites` are runtime GuiCode addresses: the literal words `lit_bg`, `lit_panel`,
    `lit_button`, the 8-byte chains `header_chain` and `rail_chain`, and the 4-byte `active`
    instruction. `cells` is `{role: (SRAM cell, ...)}` in store order (button stores two),
    `indices` the mode-7 index of each, parallel. `values` is `{role: rgb888}` as the block
    forms them. `well_index` is the button's first cell's index, the one the wtype-32 tank
    wells read. `form` is `"stock"` or `"rethemed"` (the form `plan` leaves). `proof` is
    `((label, value), ...)`: the block's bounds and the address of each of its 22
    instructions (`"insn <label>"`)."""
    sites: dict
    cells: dict
    values: dict
    indices: dict
    well_index: int
    form: str
    proof: tuple


def locate_theme_init(data: bytes) -> ThemeInit:
    """The theme-init block (13.6 `FUN_00247BF8`'s straight-line block at
    `0x248316..0x24834C`, GUICODE Finding 18), located from the code.

    The method (measured 2026-11-07):

      1. Decode GuiCode linearly, resyncing (`theme.detail_runs`).
      2. Match the 22-instruction skeleton on mnemonic and register operands, with PC-literal
         displacements and immediates as wildcards, over contiguous instructions. Two forms are
         tried: the stock one (`add.w`/`addw` chains, `movw r1` for active) and the one `plan`
         leaves (`movw`/`movt` chains). Exactly one match over both is required.
      3. Roles by position: bg is the position-1 literal; header the chain at positions 4-5
         on bg's r2; panel the position-8 literal; button the position-10 literal (r3, not
         written again until the rail chain reads it); active the position-14 instruction;
         rail the chain at positions 18-19 on the button's r3.
      4. Cells are the store targets' literals; each must be SRAM and a type-10 entry of the
         variable table the code loads (`guidata.find_variable_tables`), named by exactly one
         such entry (its mode-7 index); a second refuses.
      5. The values are replayed from the instructions.

    13.6 and 14.7 match (14.7 `0x248386..0x2483BC`, the same bytes); 11.23 has no match and
    refuses. Raises `ThemeInitError` on zero or several matches, or a store target that is
    not a colour cell. Needs capstone (`theme.CapstoneUnavailable`)."""
    try:
        import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way
    except ImportError as e:                                    # pragma: no cover
        raise theme.CapstoneUnavailable(str(e)) from e
    return _locate_theme_init(data, _SKELETONS, theme.SRAM_LOW, theme.SRAM_HIGH,
                              theme.COLOUR_CELL_TYPE)


def _insn_shape(ins) -> tuple:
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
    ops = []
    for o in ins.operands:
        if o.type == ARM_OP_REG:
            ops.append(ins.reg_name(o.reg))
        elif o.type == ARM_OP_IMM:
            ops.append("I")
        elif o.type == ARM_OP_MEM:
            if o.mem.base == ARM_REG_PC and o.mem.index == 0:
                ops.append("L")
            elif o.mem.index == 0 and o.mem.disp == 0:
                ops.append("[%s]" % ins.reg_name(o.mem.base))
            else:
                ops.append("?")
        else:
            ops.append("?")
    return ins.mnemonic, ",".join(ops)


@memo.content_cached(copy=None)
def _locate_theme_init(data: bytes, skeletons: tuple, sram_low: int, sram_high: int,
                       colour_type: int) -> ThemeInit:
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    code = data[sec.offset:sec.offset + sec.length]
    flat = [i for _s, _e, run in theme.detail_runs(code, base) for i in run]
    n = len(skeletons[0][1])
    firsts = {s for _form, sk in skeletons for s in sk[0][1]}

    hits = []
    for k in range(len(flat) - n + 1):
        if _insn_shape(flat[k]) not in firsts:
            continue
        if any(flat[k + m + 1].address != flat[k + m].address + flat[k + m].size
               for m in range(n - 1)):
            continue
        shapes = [_insn_shape(i) for i in flat[k:k + n]]
        for form, sk in skeletons:
            if all(shp in allowed for shp, (_l, allowed) in zip(shapes, sk)):
                hits.append((k, form))
    if len(hits) != 1:
        raise ThemeInitError(
            "theme-init skeleton: %d matches%s; set_theme locates the block by exactly one "
            "(22 instructions, 13.6 FUN_00247BF8's straight-line block)" % (
                len(hits), (" at " + ", ".join("%#x (%s)" % (flat[k].address, f)
                                              for k, f in hits)) if hits else ""))
    k, form = hits[0]
    block = flat[k:k + n]
    at = {label: block[m] for label, m in _POS.items()}

    def literal(label):
        ins = at[label]
        la = (ins.address & ~3) + 4 + ins.operands[1].mem.disp
        off = la - base
        if not 0 <= off <= len(code) - 4:
            raise ThemeInitError("the %s literal %#x (loaded at %#x) lies outside GuiCode"
                                 % (label, la, ins.address))
        return la, struct.unpack_from("<I", code, off)[0]

    def imm(label, j):
        return at[label].operands[j].imm & 0xFFFFFFFF

    bg, panel, button = literal("bg literal")[1], literal("panel literal")[1], \
        literal("button literal")[1]
    if form == "stock":
        header = bg + imm("header chain", 2) + imm("header chain 2", 2)
        rail = button + imm("rail chain", 2) + imm("rail chain 2", 2)
        active = imm("active", 1)
    else:
        header = imm("header chain", 1) | imm("header chain 2", 1) << 16
        rail = imm("rail chain", 1) | imm("rail chain 2", 1) << 16
        act = at["active"]
        if act.mnemonic == "movw":
            active = imm("active", 1)
        elif act.mnemonic == "add.w":
            active = button + imm("active", 2)
        else:
            active = button - imm("active", 2)
    values = {r: v & 0xFFFFFFFF for r, v in (("bg", bg), ("panel", panel), ("button", button),
                                             ("header", header), ("rail", rail),
                                             ("active", active))}

    # the cells, and their mode-7 indices in the code-cited variable table
    gs, ge = guidata.guidata_extent(data)
    try:
        dptr_off, type_off = guidata.find_variable_tables(data)
    except guidata.VariableTableNotFound as e:
        raise ThemeInitError("the theme-init's cells cannot be typed: %s" % e) from e
    by_cell: dict = {}
    index = 0
    while gs + dptr_off + 4 * index + 4 <= ge and gs + type_off + index < ge:
        addr = struct.unpack_from("<I", data, gs + dptr_off + 4 * index)[0]
        if not (addr == 0 or sram_low <= addr < sram_high):
            break
        if addr and data[gs + type_off + index] == colour_type:
            by_cell.setdefault(addr, []).append(index)
        index += 1
    cells: dict = {}
    indices: dict = {}
    for role in ROLES:
        cs, ix = [], []
        for label in _CELL_POS[role]:
            la, cell = literal(label)
            if not sram_low <= cell < sram_high:
                raise ThemeInitError("the theme-init %s store at %#x targets %#x (literal %#x), "
                                     "not SRAM" % (role, at[label].address, cell, la))
            if cell not in by_cell:
                raise ThemeInitError("the theme-init %s store at %#x targets %#x, which no "
                                     "type-%d (colour) variable-table entry names"
                                     % (role, at[label].address, cell, colour_type))
            if len(by_cell[cell]) != 1:
                raise ThemeInitError(
                    "the theme-init %s store at %#x targets %#x, which %d type-%d (colour) "
                    "variable-table entries name (indices %s); its mode-7 index is ambiguous"
                    % (role, at[label].address, cell, len(by_cell[cell]), colour_type,
                       ", ".join(str(i) for i in by_cell[cell])))
            cs.append(cell)
            ix.append(by_cell[cell][0])
        cells[role], indices[role] = tuple(cs), tuple(ix)

    sites = {"lit_bg": literal("bg literal")[0], "lit_panel": literal("panel literal")[0],
             "lit_button": literal("button literal")[0],
             "header_chain": at["header chain"].address, "active": at["active"].address,
             "rail_chain": at["rail chain"].address}
    proof = (("block start", block[0].address), ("block end", block[-1].address + block[-1].size)) \
        + tuple(("insn " + label, at[label].address) for label in _POS)
    return ThemeInit(sites, cells, values, indices, indices["button"][0], form, proof)


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


def _tank_fill_offsets(data: bytes, well_index: int | None = None) -> dict:
    """`{file offset: rgb888}` of the inline (mode-3) fill of every HOME tank: the wtype-33 record
    straight after a wtype-32 well whose fillB is mode-7 `well_index` (`GUICODE.md` Finding 18's
    tank addendum). `well_index` defaults to the located theme-init's (`ThemeInit.well_index`:
    13.6 161, 14.7 146)."""
    if well_index is None:
        well_index = locate_theme_init(data).well_index
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
                c[0] == "fillB" and c[1] == 7 and c[2] == well_index for c in well.colors)
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
    if tank_wells is not None and tank_wells not in TANK_WELL_ROLES:
        raise ThemeSwapError("tank_wells wants one of %s, got %r"
                             % (", ".join(TANK_WELL_ROLES), tank_wells))
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
    ti = locate_theme_init(data)
    if ti.form != "stock":
        raise ThemeSwapError(
            "GuiCode theme-init at %#x is in the form set_theme leaves (movw/movt chains): "
            "already re-themed (set_theme runs once, on a stock theme)"
            % dict(ti.proof)["block start"])
    for role in ROLES:
        if ti.values[role] != STOCK[role]:
            raise ThemeSwapError(
                "GuiCode theme-init at %#x forms %s %#08x, not the stock %#08x (already "
                "re-themed? set_theme runs once, on a stock theme)"
                % (dict(ti.proof)["block start"], role, ti.values[role], STOCK[role]))
    tank_offs = _tank_fill_offsets(data, ti.well_index) if tank_map else {}
    sec, base, fo = _guicode(data)

    def at(addr, n):
        return bytes(data[fo(addr):fo(addr) + n])

    edits = []

    def put(off, old, new, note):
        if old != new:
            edits.append((off, bytes(old), bytes(new), note))

    lits = [(ti.sites["lit_bg"], "bg"), (ti.sites["lit_panel"], "panel"),
            (ti.sites["lit_button"], "button")]
    for addr, role in lits:
        put(fo(addr), struct.pack("<I", STOCK[role]), struct.pack("<I", want[role]),
            "theme %s literal %#x" % (role, addr))
    header_at, rail_at, active_at = (ti.sites["header_chain"], ti.sites["rail_chain"],
                                     ti.sites["active"])
    put(fo(header_at), at(header_at, 8), enc_const(2, want["header"]),
        "theme header: add-chain -> movw/movt %#08x" % want["header"])
    put(fo(rail_at), at(rail_at, 8), enc_const(3, want["rail"]),
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
        put(fo(active_at), at(active_at, 4), active_bytes,
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
    try:
        decode = theme.colour_decode(data)
    except theme.ColourDecodeError as e:
        raise ThemeSwapError("the mode-6 palette is not located: %s" % e) from e
    pb = gs + decode.palette_offset
    pal = 0
    for i in range(decode.palette_len):
        v = struct.unpack_from("<I", data, pb + 4 * i)[0]
        if v in remap:
            put(pb + 4 * i, struct.pack("<I", v), struct.pack("<I", remap[v]),
                "theme mode-6 palette[%d] %#08x -> %#08x" % (i, v, remap[v]))
            pal += 1

    wells = 0
    if tank_wells is not None and ti.indices[tank_wells][0] != ti.well_index:
        to = ti.indices[tank_wells][0]
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
                    if role.startswith("fillB") and mode == 7 and val == ti.well_index:
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
    ti = locate_theme_init(data)
    pal = theme.mode7_palette(data)
    out = {}
    for role in ("bg", "header", "panel", "rail"):
        idx = ti.indices[role][0]
        if idx in pal:
            out[role] = rgb_int(pal[idx])
    sec, base, fo = _guicode(data)
    out["button"] = struct.unpack_from("<I", data, fo(ti.sites["lit_button"]))[0]
    return out
