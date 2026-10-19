"""Per-floorplan option defaults, recovered from the panel's floorplan tap handlers.

GUICODE Finding 42. This closes the project's oldest strict `xfail`.

**Why it was open, and why it is now closed.** `GUICODE.md` Finding 6 searched GuiCode for the
defaults and found nothing; Finding 19 read the option registers' *write* side and found each is
written once from a **received** CAN payload, concluding the state is bus-delivered and "not in
the file at all". Both were right about what they examined and both looked at the **receive**
path. `BUS-BASELINE.md` PC.12a had meanwhile proved the opposite about the source: a floorplan
selection completes in ~50 ms with **no G12 frame interleaved**, so the panel is not querying
anyone -- the twelve values are already in its flash when the burst begins.

They are, on the **send** path:

  1. Each FLOORPLAN SELECTION button's touch record (in the page object, not the button child)
     ends with a tap **action id**, and the ids are `0x0898 + floorplan value` exactly.
  2. GuiCode's tap dispatcher branches to that floorplan's handler.
  3. The handler emits a run of static 12-byte CAN-frame templates from Finding 7's `.rodata`
     pool, copied wholesale by `ldm {r0,r1,r2}` and passed to the transmit pointer -- the same
     mechanism Finding 23 found for the OPTIONS rows.
  4. Each template is an `0x1FED9` config write, `00 d9 fe 19 | ff 96 <sel> 0f <val> 00 d1 ea`.
     The **defaults are those literal `<sel, val>` bytes.**

**The handlers self-identify, so nothing here is hardcoded.** Every floorplan's handler writes
selector `0xF5` (the floorplan itself) with its own value as its first write, so a candidate is
labelled by its own bytes rather than by an address or a dispatcher walk.

**Build-general as of 2026-08-29.** The handler *identity* was always structural, but the one
remaining 13.6-ism was the template-pool address window (`0x259000..0x25A000`), hardcoded: on 14.7
the pool sits above `0x25A000`, so the fixed window matched no pool-base load and this returned an
empty dict there (Studio's 14.7 floorplan picker applied no defaults). `_pool_bounds` now derives
the window from the templates themselves, so 13.6 (11 floorplans), 14.7 (10) and 11.23 (8) all
recover their default bursts. 13.6's output is unchanged and still reproduces all 90 answer-key
entries.
"""
from __future__ import annotations

import struct

from . import memo, theme

# Finding 7's CAN-frame template pool. Its address differs per build (13.6 ~0x2594xx, 14.7
# ~0x25bdxx), so the bounds are computed structurally from the templates themselves
# (`_pool_bounds`) rather than hardcoded -- the old fixed 0x259000-0x25A000 window silently found
# ZERO handlers on 14.7 (its pool is above 0x25A000), so this returned no floorplan defaults there.
# Kept only as the 13.6 reference values the window used to be.
POOL_LO_13V6, POOL_HI_13V6 = 0x00259000, 0x0025A000
TEMPLATE_MAGIC = bytes((0x00, 0xD9, 0xFE, 0x19, 0xFF, 0x96))   # 0x1FED9 write-template header
POOL_BASE_MARGIN = 0x800                     # the handler's pool base sits just below template 0
SEL_FLOORPLAN = 0xF5
WRITE_DGN_BYTES = (0xD9, 0xFE)              # 0x1FED9, the panel's config-write DGN
READBACK = 0xFF                             # a value of 0xFF is the read-back request, not a write
TEMPLATE_LEN = 12
ACTION_BASE = 0x0898                        # tap action id = ACTION_BASE + floorplan value


def _disasm(code: bytes, base: int):
    """Address -> instruction for the whole section, resyncing on data islands."""
    try:
        import capstone
    except ImportError as exc:  # pragma: no cover
        raise theme.CapstoneUnavailable("capstone is required") from exc
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    out, pos = {}, 0
    while pos < len(code):
        end = pos
        for ins in md.disasm(code[pos:], base + pos):
            end = ins.address - base + ins.size
            out[ins.address] = ins
        if end <= pos:
            end = pos + 2
        pos = end
    return out


def _pool_bounds(code: bytes, base: int) -> tuple[int, int]:
    """The `[lo, hi)` address range of this build's `0x1FED9` template pool, from the templates
    themselves. A handler loads its pool base at or just below the first template and offsets *up*
    into the pool, so `lo` reaches a little below the lowest template (`POOL_BASE_MARGIN`). Returns
    the 13.6 reference window when no template is present, so callers never get an empty range."""
    hits = []
    i = code.find(TEMPLATE_MAGIC)
    while i >= 0:
        hits.append(base + i)
        i = code.find(TEMPLATE_MAGIC, i + 1)
    if not hits:
        return POOL_LO_13V6, POOL_HI_13V6
    return min(hits) - POOL_BASE_MARGIN, max(hits) + TEMPLATE_LEN


def _templates_from(insns, addrs, start, code, base, pool_lo, pool_hi, limit=600,
                    position=None):
    """Pool addresses a handler emits, in order, from `start`.

    `limit` is an instruction budget, not a semantic bound -- the walk normally stops at the
    handler's own `bx`/`pop`/back-branch. It was 200 and truncated RT's tail, dropping its `0xF7`
    (retry interval) write; 600 completes every handler at twelve selectors, matching PC.12a's
    count exactly. Ship Mode's eight is genuine: it is not a floorplan and writes fewer.
    """
    try:
        import capstone
        from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover
        raise theme.CapstoneUnavailable("capstone is required") from exc
    pool_reg = pool_val = None
    out = []
    # `position` is `{addr: index in addrs}`, built once by the caller: `list.index` on a
    # ~200k-entry list, twice per handler candidate, was 46 s of a 50 s call.
    at_start = position[start] if position is not None else addrs.index(start)
    for a in addrs[at_start:at_start + limit]:
        ins = insns[a]
        m, ops = ins.mnemonic, ins.operands
        if m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                and ops[1].mem.base == ARM_REG_PC:
            at = (a & ~3) + 4 + ops[1].mem.disp - base
            if 0 <= at <= len(code) - 4:
                v = struct.unpack_from("<I", code, at)[0]
                if pool_lo <= v < pool_hi:
                    pool_reg, pool_val = ops[0].reg, v
        elif m in ("addw", "add.w", "add") and pool_val is not None and len(ops) == 3 \
                and ops[1].type == ARM_OP_REG and ops[1].reg == pool_reg \
                and ops[2].type == ARM_OP_IMM:
            out.append(pool_val + ops[2].imm)
        elif m in ("bx", "pop"):
            break
        elif m.startswith("b") and len(ops) == 1 and ops[0].type == ARM_OP_IMM \
                and ops[0].imm < start:
            break                                   # jumped back into the dispatcher
    return out


def floorplan_defaults(data: bytes) -> dict[int, dict[int, int]]:
    """`_floorplan_defaults` (whose docstring is the contract), memoised on the content of
    `data` and deep-copied out. `find_option_defaults` calls this once per floorplan value and
    `doctor`/the Studio config endpoint call that for every floorplan; the whole-section
    decode + handler walk (~4 s) is the same each time."""
    return {fp: dict(sels) for fp, sels in _floorplan_defaults(data).items()}


@memo.content_cached(copy=None)
def _floorplan_defaults(data: bytes) -> dict[int, dict[int, int]]:
    """`{floorplan value: {selector: default value}}`, derived from the tap handlers.

    A handler is any instruction that loads the template pool base; it is attributed to the
    floorplan whose value it writes to selector `0xF5`. Selectors written `0xFF` are the
    read-back requests that follow each write and are excluded.
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    insns = _disasm(code, base)
    addrs = sorted(insns)
    position = {a: i for i, a in enumerate(addrs)}
    pool_lo, pool_hi = _pool_bounds(code, base)

    def template(addr):
        off = section.offset + (addr - base)
        return data[off:off + TEMPLATE_LEN]

    out: dict[int, dict[int, int]] = {}
    for a in addrs:
        ins = insns[a]
        if ins.mnemonic not in ("ldr", "ldr.w"):
            continue
        writes: dict[int, int] = {}
        first_fp = None
        for pool_addr in _templates_from(insns, addrs, a, code, base, pool_lo, pool_hi,
                                         position=position):
            t = template(pool_addr)
            if len(t) < TEMPLATE_LEN or (t[1], t[2]) != WRITE_DGN_BYTES:
                continue
            if t[4] != 0xFF or t[5] != 0x96:
                continue
            sel, val = t[6], t[8]
            if val == READBACK:
                # Each write is followed by a read-back request carrying 0xFF. Skipping it is
                # DEFENSIVE rather than load-bearing: `setdefault` below keeps the first value for
                # a selector and the write always precedes its read-back, so removing this filter
                # changes nothing today (verified by mutation). It is kept so the extraction does
                # not silently depend on that ordering.
                continue
            writes.setdefault(sel, val)
            if sel == SEL_FLOORPLAN and first_fp is None:
                first_fp = val
        # A real handler leads with its own floorplan write and carries more than that alone.
        # Several addresses inside one handler load the pool base, so the same floorplan is seen
        # more than once with progressively truncated views; keep the MOST COMPLETE one. (Keeping
        # the first instead still reproduced all 90 answer-key entries, because the six OPTIONS
        # selectors are written early -- but it dropped later ones like 0x15 / 0xF7 on some
        # floorplans, which is how the truncation showed up.)
        if first_fp is not None and len(writes) > 1 and len(writes) > len(out.get(first_fp, ())):
            out[first_fp] = writes
    return out
