"""Prove that an image decodes, lays out and draws GuiData records exactly as firmware 13.6 does,
by locating twins of 13.6's record decoder, layout engine and shape renderer in its GuiCode
(2026-11-08).

**What is compared.** 13.6's decoder `FUN_00252CB0`, layout engine `FUN_00257BFC` and renderer
`FUN_00256C54` ship as instruction lists (`data/grammar136.json`), generated from 13.6 stock by
`scripts/gen_grammar_fingerprints.py` over their exact Ghidra extents (project `guicode136`). No
address of any other firmware appears here. Each list is a linear sweep of the function, and each
item is compared as follows.

* **Instructions** must have the same mnemonic and operands once registers are abstracted.
  Immediates and state offsets are compared exactly. The registers themselves must follow one
  consistent **bijection** from 13.6's registers to the image's: per function for the decoder and
  layout engine, per segment for the renderer. A renaming passes. Swapping one register for
  another does not (`r0`-`r12`, `sb`/`sl`/`fp`/`ip`; `sp`/`lr`/`pc` are compared literally).
* **PC-relative operands** (branch, `cbz`, literal `ldr`, `adr`) are masked in the text and their
  targets compared separately:
  * a code target inside the function is compared by position (`@segment:item`);
  * a pool word inside the function is compared by position from its segment's end
    (`@segment:-k`);
  * a call (`bl` and any other branch out of the function) must follow one consistent injective
    mapping from 13.6 callee to image callee: the same callee maps the same everywhere, and
    distinct callees map to distinct ones. The callee's own code is not compared;
  * a literal load from outside the function is compared by its value, as pool words are.
* **Data** is skipped as code and compared as values:
  * **Pool words** (the targets of `ldr Rt, [pc, #]`) are classified as SRAM (`0x2xxxxxxx`),
    GuiData (`0x9xxxxxxx`), GuiCode (inside the section) or constant. A constant must be equal.
    An address must stay in its class and follow one consistent injective mapping per function
    (per segment in the renderer). `proof` reports the shifts, e.g. "SRAM pool values shifted
    +0x4".
  * **Pads**: the `nop`/zero halfword before an aligned pool.
  * **Tables**: the absolute jump tables after `ldr.w pc, [Rn, Rm, lsl #2]` and the inline
    `tbb`/`tbh` tables. Their entries are counted from the preceding `cmp` and compared by the
    position they jump to.

  Over 13.6's three functions this code/data split equals Ghidra's function bodies byte for byte
  (the generator asserts it).

The one other allowed difference is a **named bound**: a `cmp.w R, #imm` site the fingerprint
declares. Its immediate may differ, but it must be one value across all its sites, and that value
is reported. The decoder's `mode7_index` is `cmp.w r0, #0x298` (664) at 13 sites over its exact
extent. The research of 2026-11-07 counted 11 sites over a window.

**Search.** Every halfword in GuiCode that encodes a `push` matching the fingerprint's first item
is a candidate. At each candidate the sweep reads as many items as the fingerprint has and
compares them all. A twin has no unexplained difference. When no candidate is a twin, the refusal
names the candidate with the longest matching prefix and its first difference.

**The renderer is a switch.**
- **Dispatch.** Its entry through `tbh [pc, r3, lsl #1]` and its table must be a twin.
- **Segments.** Each segment after the dispatch is fingerprinted separately: the code after the
  table, then each switch arm, from its start in the table to the next arm start. The arm
  partition (which wtypes share an arm) must be 13.6's.
- **Proven wtypes.** A wtype is proven when its arm, and every segment that it or the dispatch can
  reach by branch or fall-through, are twins. Pool loads are not transfers of control.
  `GrammarProof.renderer_wtypes` holds the proven wtypes.
- **Default arm.** `renderer_default_wtypes` is the subset whose table entry is the default arm
  (the dispatch's out-of-range target). Those wtypes draw nothing of their own, so they are proven
  only in the sense that they reach that arm.
- **For consumers.** A consumer (`donors`, Plan 4 Task 2) must refuse a template whose wtype is
  not in `renderer_wtypes - renderer_default_wtypes`.
- **On 14.7** the arms for wt 10/18 and wt 14 differ (moved state offsets and a re-laid record
  array). `proof` names them as not proven, with the first differing instruction.

**Text drawers** (2026-11-08). The wt-11 drawer `FUN_002559A0` and wt-8 drawer `FUN_002557C0`
are fingerprinted too and compared the same way. Their result is `text_drawers_twin`: True when
both have exactly one twin, else False with the reason in `proof`. It never makes `prove` refuse.
On 14.7 they are not twins; `proof` names the first differing instruction. `donors` uses the
flag for its witness gate.

**What is not proven.** A callee's own code: the mapping proves only that the twin calls
corresponding helpers in the same pattern. Fingerprinted code is compared, not executed.

**Refusals** (`GrammarError`, code `GRAMMAR`): no GuiCode; the decoder, layout engine or renderer
dispatch has no twin or more than one; a bound with inconsistent sites; no renderer arm proven.
11.23 refuses: 13.6's decoder has no twin there.
"""
from __future__ import annotations

import dataclasses
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from typing import Iterator

from . import memo, theme

FINGERPRINT_RESOURCE = "data/grammar136.json"
# The text drawers: fingerprinted and compared like the decoder, reported in
# `GrammarProof.text_drawers_twin`, never refused on.
TEXT_DRAWERS = ("text11", "text8")


class GrammarError(ValueError):
    """An image's record grammar is not proven to be 13.6's."""
    code = "GRAMMAR"


@dataclass(frozen=True)
class GrammarProof:
    decoder: int                        # runtime address of the decoder twin
    layout: int                         # of the layout-engine twin
    renderer: int                       # of the renderer twin (its dispatch)
    bounds: dict[str, tuple[int, int]]  # name -> (13.6 value, this image's), only where they differ
    proof: tuple[str, ...]
    renderer_wtypes: frozenset[int]     # wtypes whose renderer arm (and all it reaches) is a twin
    # the subset whose arm is the default (out-of-range) arm: they draw nothing of their own
    renderer_default_wtypes: frozenset[int] = frozenset()
    # both text drawers (wt 11 `FUN_002559A0`, wt 8 `FUN_002557C0`) have a twin; never a refusal
    text_drawers_twin: bool = False


# ---------------------------------------------------------------------------------------------
# The sweep (shared with scripts/gen_grammar_fingerprints.py, so fingerprint and search agree)
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    addr: int
    size: int
    text: str                    # concrete registers, PC-relative operands as `<rel>`
    raw: str                     # capstone's text, for messages
    target: int | None = None    # a PC-relative operand's absolute target
    entries: tuple[int, ...] = ()  # a table's jump targets
    exit: bool = False           # an unconditional transfer: control never falls past it
    value: bytes = b""           # a data item's bytes


_REG = re.compile(r"\b(?:r\d{1,2}|sb|sl|fp|ip)\b")
_PCMEM = re.compile(r"\[pc, #(-?(?:0x[0-9a-f]+|\d+))\]")
_BRANCH = re.compile(r"(?:b|bl|blx)(eq|ne|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)?(?:\.w|\.n)?$")
_IMM_TAIL = re.compile(r"#(-?(?:0x[0-9a-f]+|\d+))$")
_CMP_IMM = re.compile(r"#(0x[0-9a-f]+|\d+)$")
_BOUND = re.compile(r"^cmp\.w R, #(0x[0-9a-f]+|\d+)$")
_PC_WRITE = re.compile(r"^(pop(\.w)? \{[^}]*\bpc\}|(ldr|mov)(\.w)? pc, )")

_PADS = (b"\x00\xbf", b"\x00\x00")
_MD = None


def _cs():
    global _MD
    if _MD is None:
        import capstone
        _MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    return _MD


def abstract(text: str) -> str:
    """`text` with its registers abstracted to `R`."""
    return _REG.sub("R", text)


def _literal_size(mnem: str, ops: str) -> int:
    base = mnem.split(".")[0]
    if base == "ldrd" or (base == "vldr" and ops.startswith("d")):
        return 8
    if base in ("ldrh", "ldrsh"):
        return 2
    if base in ("ldrb", "ldrsb"):
        return 1
    return 4


def _instruction(code: bytes, base: int, pc: int) -> Item | None:
    lo = pc - base
    if lo < 0 or lo + 2 > len(code):
        return None
    got = next(_cs().disasm_lite(code[lo:lo + 4], pc, 1), None)
    if got is None:
        return None
    _addr, size, mnem, ops = got
    raw = f"{mnem} {ops}".strip()
    target = None
    m_branch = _BRANCH.match(mnem)
    if (m_branch or mnem in ("cbz", "cbnz", "adr", "adr.w")) and _IMM_TAIL.search(ops):
        imm = int(_IMM_TAIL.search(ops).group(1), 0)
        # capstone prints branch targets absolute, adr as an offset from Align(pc + 4, 4)
        target = imm if not mnem.startswith("adr") else ((pc + 4) & ~3) + imm
        ops = _IMM_TAIL.sub("<rel>", ops)
    elif _PCMEM.search(ops):
        target = ((pc + 4) & ~3) + int(_PCMEM.search(ops).group(1), 0)
        ops = _PCMEM.sub("[pc, <rel>]", ops)
    text = f"{mnem} {ops}".strip()
    exit_ = bool(mnem in ("b", "b.w", "b.n", "bx", "tbb", "tbh") or _PC_WRITE.match(raw))
    return Item(pc, size, text, raw, target, (), exit_)


def sweep(code: bytes, base: int, start: int, *, end: int | None = None,
          data: dict[int, int] | None = None) -> Iterator[Item]:
    """Items from `start`, linearly, skipping data found on the way, until `end` (if given) or
    until the caller stops. `data` (address -> size) is shared across calls by the caller, so a
    pool referenced in one segment is skipped in the next. An undecodable halfword, or a sweep
    leaving the section, yields `<bad>` and stops."""
    data = {} if data is None else data
    pc, last_cmp, in_it = start, None, 0
    while end is None or pc < end:
        if pc in data:
            n = data[pc]
            yield Item(pc, n, f"<data:{n}>", f"<data:{n}>",
                       value=bytes(code[pc - base:pc - base + n]))
            pc += n
            continue
        if pc % 4 == 2 and pc + 2 in data and code[pc - base:pc - base + 2] in _PADS:
            # the halfword aligning a pool, only when it is a `nop` or zero: a real instruction
            # may sit there (13.6 `0x257112 b #0x2570da`, just before a pool at `0x257114`)
            yield Item(pc, 2, "<pad>", "<pad>")
            pc += 2
            continue
        ins = _instruction(code, base, pc)
        if ins is None:
            yield Item(pc, 2, "<bad>", "<bad>", exit=True)
            return
        mnem = ins.raw.split(" ")[0]
        if in_it:
            # inside an IT block (capstone, one instruction at a time, cannot see the IT
            # state): a `b` or `pop {pc}` here is conditional, so control may fall past it
            in_it -= 1
            ins = dataclasses.replace(ins, exit=False)
        elif re.fullmatch(r"it[te]{0,3}", mnem):
            in_it = len(mnem) - 1
        if ins.target is not None and "[pc" in ins.text and not mnem.startswith(("tbb", "tbh")):
            data.setdefault(ins.target, _literal_size(mnem, ins.raw.split(" ", 1)[1]))
        if mnem.startswith("cmp"):
            m = _CMP_IMM.search(ins.raw)
            last_cmp = int(m.group(1), 0) if m else None
        yield ins
        pc += ins.size
        table = None
        if mnem.startswith("ldr") and ins.raw.split(" ", 1)[1].startswith("pc, [") \
                and "lsl #2" in ins.raw:
            n = (last_cmp or 0) + 1
            if last_cmp is None or pc - base + 2 + 4 * n > len(code):
                yield Item(pc, 2, "<bad>", "<bad>", exit=True)
                return
            if pc % 4 == 2:
                yield Item(pc, 2, "<pad>", "<pad>")
                pc += 2
            words = struct.unpack_from(f"<{n}I", code, pc - base)
            table = Item(pc, 4 * n, "", "", entries=tuple(w & ~1 for w in words))
        elif mnem in ("tbb", "tbh"):
            if last_cmp is None or pc - base + (last_cmp + 1) * 2 > len(code):
                yield Item(pc, 2, "<bad>", "<bad>", exit=True)
                return
            n = last_cmp + 1
            if mnem == "tbb":
                offs = list(code[pc - base:pc - base + n])
                size = n + (n & 1)
            else:
                offs = list(struct.unpack_from(f"<{n}H", code, pc - base))
                size = 2 * n
            table = Item(pc, size, "", "", entries=tuple(pc + 2 * o for o in offs))
        if table is not None:
            yield dataclasses.replace(table, text=f"<table:{table.size}>",
                                      raw=f"<table:{table.size}>", exit=True)
            pc += table.size


def _words(value: bytes) -> str:
    if len(value) in (4, 8):
        return ",".join(f"{w:#x}" for w in struct.unpack(f"<{len(value) // 4}I", value))
    return f"{int.from_bytes(value, 'little'):#x}"


def encode(segments: list[list[Item]], code: bytes, base: int) -> list[list[str]]:
    """Each item's comparison string: its text, then `|` and a token for what it refers to:
    - `@s:i`: a code target inside the swept segments, item `i` of segment `s`;
    - `@s:-k`: a pool word, `k` items from its segment's end;
    - `call=0x…`: a code target outside them;
    - `lit=…`: a literal load from outside them, by value;
    - `=…`: a data item's value (words, comma-separated);
    - `T=…`: a table's jump targets, by position.
    """
    where = {}
    for s, items in enumerate(segments):
        for i, it in enumerate(items):
            where[it.addr] = (s, i - len(items) if it.text.startswith("<data") else i)

    def pos(addr: int) -> str:
        p = where.get(addr)
        return f"@{p[0]}:{p[1]}" if p else f"call={addr:#x}"

    out = []
    for items in segments:
        row = []
        for it in items:
            if it.value:
                row.append(f"{it.text}|={_words(it.value)}")
            elif it.entries:
                row.append(f"{it.text}|T=" + ",".join(pos(e) for e in it.entries))
            elif it.target is None:
                row.append(it.text)
            elif it.target in where:
                row.append(f"{it.text}|{pos(it.target)}")
            elif "[pc, <rel>]" in it.text:
                n = _literal_size(it.raw.split(" ")[0], it.raw.split(" ", 1)[1])
                lo = it.target - base
                row.append(f"{it.text}|lit={_words(code[lo:lo + n])}"
                           if 0 <= lo and lo + n <= len(code) else f"{it.text}|ext")
            else:
                row.append(f"{it.text}|call={it.target:#x}")
        out.append(row)
    return out


def text_part(enc: str) -> str:
    return enc.split("|", 1)[0]


def _token(enc: str) -> str | None:
    return enc.split("|", 1)[1] if "|" in enc else None


def reaches(enc_items: list[str], next_segment: int | None, exits: list[int]) -> set[int]:
    """Segments an encoded segment can transfer to: its in-function targets, and the next
    segment when its last code item (`exits` lists the unconditional exits by index) is not an
    unconditional exit. Data items are not code. A table's entries (`T=…`) are not counted: the
    only table in a renderer segment list is the dispatch's tbh, whose arms are the wtypes."""
    out = set()
    for e in enc_items:
        tok = _token(e)
        if tok and tok.startswith("@"):
            seg, idx = tok[1:].split(":")
            if not idx.startswith("-"):          # a pool load is not a transfer of control
                out.add(int(seg))
    code = [i for i, e in enumerate(enc_items)
            if not text_part(e).startswith(("<data", "<pad"))]
    if next_segment is not None and (not code or code[-1] not in set(exits)):
        out.add(next_segment)
    return out


def section(data: bytes) -> tuple[bytes, int]:
    """The GuiCode bytes and runtime base, or GrammarError."""
    try:
        sec = theme.guicode_section(data)
    except ValueError as e:
        raise GrammarError(f"GRAMMAR: {e}") from e
    return bytes(data[sec.offset:sec.offset + sec.length]), theme.guicode_base(sec)


# ---------------------------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------------------------

_FP_TEXT: str | None = None


def _fingerprint_text() -> str:
    global _FP_TEXT
    if _FP_TEXT is None:
        _FP_TEXT = resources.files("fireflymcf").joinpath(FINGERPRINT_RESOURCE).read_text(
            encoding="utf-8")
    return _FP_TEXT


def fingerprints() -> dict:
    """The shipped fingerprints (a fresh parse)."""
    return json.loads(_fingerprint_text())


def _prologue_candidates(code: bytes, base: int, first: str) -> list[int]:
    out = []
    for off in range(0, len(code) - 4, 2):
        hw = code[off] | code[off + 1] << 8
        if (hw & 0xFE00) != 0xB400 and hw != 0xE92D:
            continue
        ins = _instruction(code, base, base + off)
        if ins is not None and abstract(ins.text) == abstract(first):
            out.append(base + off)
    return out


def _value_class(v: int, region: tuple[int, int]) -> str:
    if region[0] <= (v & ~1) < region[1]:
        return "GuiCode"
    if v >> 28 == 0x2:
        return "SRAM"
    if v >> 28 == 0x9:
        return "GuiData"
    return "constant"


class _Maps:
    """One consistent injective mapping per kind (registers, callees, each address class),
    13.6 -> image, over one scope (a function, or a renderer segment)."""

    def __init__(self, ref_region: tuple[int, int], img_region: tuple[int, int]):
        self.fwd: dict[tuple[str, object], object] = {}
        self.inv: dict[tuple[str, object], object] = {}
        self.where: dict[tuple[str, object], int] = {}    # where each binding was first made
        self.ref_region, self.img_region = ref_region, img_region

    def bind(self, kind: str, a, b, at: int) -> str | None:
        f, r = self.fwd.get((kind, a)), self.inv.get((kind, b))
        fmt = (lambda x: f"{x:#x}") if isinstance(a, int) else str
        if f is not None and f != b:
            return (f"13.6 {kind} {fmt(a)} maps to {fmt(f)} as at "
                    f"{self.where[(kind, a)]:#x}, {fmt(b)} here")
        if r is not None and r != a:
            return (f"{kind} {fmt(b)} is 13.6's {fmt(r)} as at "
                    f"{self.where[(kind, r)]:#x}, {fmt(a)} here")
        if f is None:
            self.fwd[(kind, a)], self.inv[(kind, b)], self.where[(kind, a)] = b, a, at
        return None

    def bind_value(self, a: int, b: int, at: int) -> str | None:
        ca, cb = _value_class(a, self.ref_region), _value_class(b, self.img_region)
        if ca == "constant":
            return None if a == b else f"constant {a:#x} is {b:#x} here"
        if ca != cb:
            return f"13.6 {ca} address {a:#x} is {cb} {b:#x} here"
        return self.bind(f"{ca} value", a, b, at)

    def shifts(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for (kind, a), b in self.fwd.items():
            if kind.endswith(" value"):
                out.setdefault(kind[:-6], Counter())[b - a] += 1
        return out


@dataclass
class _Diff:
    matched: int
    at: int
    want: str
    have: str
    why: str = ""

    def describe(self) -> str:
        why = f" ({self.why})" if self.why else ""
        return f"differs at {self.at:#x}: 13.6 has `{self.want}`, this image `{self.have}`{why}"


def _values(tok: str) -> list[int]:
    return [int(x, 16) for x in tok.split("=", 1)[1].split(",")]


def _compare(want: list[str], items: list[Item], enc: list[str], maps: _Maps,
             bound_sites: dict[int, str], bound_seen: dict[str, tuple[int, list[int]]]
             ) -> _Diff | None:
    """Item by item: abstracted text, the register bijection, tokens (positions, callee and
    value mappings) and the declared bounds."""
    for i, w in enumerate(want):
        if i >= len(items):
            last = items[-1].addr + items[-1].size if items else 0
            return _Diff(i, last, text_part(w), "<end of segment>")
        it, have = items[i], enc[i]
        wt, ht = text_part(w), text_part(have)
        diff = lambda why="": _Diff(i, it.addr, w.replace("|", " -> "),  # noqa: E731
                                    f"{it.raw} -> {_token(have)}" if _token(have) else it.raw,
                                    why)
        if abstract(wt) != abstract(ht):
            if not (i in bound_sites and _BOUND.match(abstract(wt))
                    and _BOUND.match(abstract(ht))):
                return _Diff(i, it.addr, wt, it.raw)
            name = bound_sites[i]
            value = int(_BOUND.match(abstract(ht)).group(1), 0)
            prior = bound_seen.get(name)
            if prior is not None and prior[0] != value:
                return _Diff(i, it.addr, f"cmp.w R, #{prior[0]:#x} (bound {name} as at "
                             f"{prior[1][0]:#x})", it.raw)
        if i in bound_sites:
            value = int(_BOUND.match(abstract(ht)).group(1), 0)
            bound_seen.setdefault(bound_sites[i], (value, []))[1].append(it.addr)
        for a, b in zip(_REG.findall(wt), _REG.findall(ht)):
            why = maps.bind("register", a, b, it.addr)
            if why:
                return diff(why)
        wtok, htok = _token(w), _token(have)
        if wtok is None or htok is None:
            if wtok != htok:
                return diff()
            continue
        if wtok.startswith(("=", "lit=")) and htok.startswith(wtok.split("=")[0] + "="):
            wv, hv = _values(wtok), _values(htok)
            if len(wv) != len(hv):
                return diff()
            for a, b in zip(wv, hv):
                why = maps.bind_value(a, b, it.addr)   # a byte or halfword is a constant
                if why:
                    return diff(why)
        elif wtok.startswith("call=") and htok.startswith("call="):
            why = maps.bind("callee", int(wtok[5:], 16), int(htok[5:], 16), it.addr)
            if why:
                return diff(why)
        elif wtok != htok:
            return diff()
    if len(items) > len(want):
        return _Diff(len(want), items[len(want)].addr, "<end of segment>", items[len(want)].raw)
    return None


def _bound_index(fn: dict) -> dict[int, dict[int, str]]:
    out: dict[int, dict[int, str]] = {}
    for name, sites in fn.get("bounds", {}).items():
        for seg, idx in sites:
            out.setdefault(seg, {})[idx] = name
    return out


def _take(gen: Iterator[Item], n: int) -> list[Item]:
    out = []
    for it in gen:
        out.append(it)
        if len(out) == n or it.text == "<bad>":
            break
    return out


def _shift_lines(name: str, maps: list[_Maps]) -> list[str]:
    total: dict[str, Counter] = {}
    for m in maps:
        for cls, c in m.shifts().items():
            total.setdefault(cls, Counter()).update(c)
    callees = sum(1 for m in maps for (kind, _a) in m.fwd if kind == "callee")
    out = [f"{name}: {callees} distinct callees, mapped one-to-one"] if callees else []
    for cls, c in sorted(total.items()):
        shifts = ", ".join(f"{d:+#x} x{n}" for d, n in sorted(c.items()))
        out.append(f"{name}: {cls} pool values ({sum(c.values())} distinct) shifted {shifts}")
    return out


def _whole(code: bytes, base: int, name: str, fn: dict, regions: tuple
           ) -> tuple[int, dict[str, tuple[int, int, list[int]]], list[str]]:
    """Locate the one twin of a whole-function fingerprint."""
    want = fn["segments"][0]["items"]
    bidx = _bound_index(fn).get(0, {})
    cands = _prologue_candidates(code, base, text_part(want[0]))
    twins, best = [], None
    for c in cands:
        items = _take(sweep(code, base, c), len(want))
        seen: dict = {}
        maps = _Maps(*regions)
        d = _compare(want, items, encode([items], code, base)[0], maps, bidx, seen)
        if d is None:
            twins.append((c, seen, maps))
        elif best is None or d.matched > best[1].matched:
            best = (c, d)
    label = f"13.6's {name} {fn['ghidra']}"
    if len(twins) != 1:
        if twins:
            raise GrammarError(f"GRAMMAR: {len(twins)} twins of {label} "
                               f"({', '.join(f'{t[0]:#x}' for t in twins)}); refusing to choose")
        if best is None:
            raise GrammarError(f"GRAMMAR: no twin of {label}: no prologue `{text_part(want[0])}` "
                               f"in this GuiCode")
        c, d = best
        raise GrammarError(f"GRAMMAR: no twin of {label} ({len(want)} items): of "
                           f"{len(cands)} prologue candidates the closest, {c:#x}, matches "
                           f"{d.matched} items and {d.describe()}")
    addr, seen, maps = twins[0]
    proof = [f"{name}: {label} ({len(want)} items) has one twin, at {addr:#x}, among "
             f"{len(cands)} prologue candidates"]
    proof += _shift_lines(name, [maps])
    bounds = {}
    for bname, (value, sites) in seen.items():
        want_value = fn["bound_values"][bname]
        bounds[bname] = (want_value, value, sites)
        proof.append(f"{name} bound {bname}: `cmp.w #{want_value:#x}` on 13.6, `#{value:#x}` "
                     f"here, one value at {len(sites)} sites "
                     f"({', '.join(f'{s:#x}' for s in sites[:3])}, ...)")
    return addr, bounds, proof


def default_segment(fn: dict) -> int | None:
    """The renderer segment the dispatch sends an out-of-range wtype to: the target of the last
    branch before its tbh, when that target starts a segment."""
    disp = fn["segments"][0]["items"]
    tbh = next(i for i, e in enumerate(disp) if text_part(e).startswith("tbh"))
    for e in reversed(disp[:tbh]):
        tok = _token(e)
        if text_part(e).startswith("b") and tok and tok.startswith("@"):
            seg, idx = tok[1:].split(":")
            return int(seg) if idx == "0" else None
    return None


def _renderer(code: bytes, base: int, fn: dict, regions: tuple
              ) -> tuple[int, frozenset[int], frozenset[int], list[str]]:
    segs = fn["segments"]
    disp_want = segs[0]["items"]
    cands = _prologue_candidates(code, base, text_part(disp_want[0]))
    label = f"13.6's renderer {fn['ghidra']}"
    passed, best = [], None
    for c in cands:
        data: dict[int, int] = {}
        items = _take(sweep(code, base, c, data=data), len(disp_want))
        d = None
        if len(items) != len(disp_want) or not items[-1].entries:
            d = _Diff(len(items) - 1, items[-1].addr, "<tbh table>", items[-1].raw)
        if d is None:
            got = _arms(code, base, c, items, data, fn, regions)
            if isinstance(got, _Diff):
                d = got
            else:
                passed.append((c, got))
                continue
        if best is None or d.matched > best[1].matched:
            best = (c, d)
    proven = [(c, g) for c, g in passed if g[0]]
    if len(proven) != 1:
        if len(proven) > 1:
            raise GrammarError(f"GRAMMAR: {len(proven)} twins of {label} "
                               f"({', '.join(f'{p[0]:#x}' for p in proven)}); refusing to choose")
        if passed:
            raise GrammarError(f"GRAMMAR: {label}: the dispatch at {passed[0][0]:#x} is a twin "
                               f"but no wtype arm is; " + "; ".join(passed[0][1][2]))
        if best is None:
            raise GrammarError(f"GRAMMAR: no twin of {label}: no prologue "
                               f"`{text_part(disp_want[0])}` in this GuiCode")
        c, d = best
        raise GrammarError(f"GRAMMAR: no twin of {label}'s dispatch: of {len(cands)} prologue "
                           f"candidates the closest, {c:#x}, matches {d.matched} items and "
                           f"{d.describe()}")
    addr, (wtypes, defaults, notes) = proven[0]
    proof = [f"renderer: {label} dispatch ({len(disp_want)} items, through its tbh) has one twin, "
             f"at {addr:#x}; {len(segs) - 1} segments after it; proven wtypes "
             f"{_ranges(sorted(wtypes))}, of which {_ranges(sorted(defaults))} only reach the "
             f"default arm"] + notes
    return addr, frozenset(wtypes), frozenset(defaults), proof


def _arms(code: bytes, base: int, entry: int, disp: list[Item], data: dict, fn: dict,
          regions: tuple):
    """Sweep and compare the dispatch and every segment after it. Returns (proven wtypes,
    default-arm wtypes, notes), or a _Diff when the dispatch or the arm structure is not
    13.6's."""
    segs = fn["segments"]
    table = disp[-1]
    table_end = table.addr + table.size
    starts = sorted({table_end, *table.entries})
    if starts[0] != table_end or len(starts) != len(segs) - 1:
        return _Diff(len(disp) - 1, table.addr,
                     f"{len(segs) - 1} segments after the table",
                     f"{len(starts)} (arm starts {', '.join(f'{s:#x}' for s in starts[:4])}, ...)")
    by_start: dict[int, list[int]] = {}
    for w, t in enumerate(table.entries):
        by_start.setdefault(t, []).append(w)
    for k, s in enumerate(starts, 1):
        if sorted(by_start.get(s, [])) != segs[k]["wtypes"]:
            return _Diff(len(disp) - 1, table.addr,
                         f"arm partition {[g['wtypes'] for g in segs[1:]]}",
                         f"wtypes {sorted(by_start.get(s, []))} at {s:#x}")
    swept = [disp]
    for k, s in enumerate(starts, 1):
        if k < len(starts):
            items = list(sweep(code, base, s, end=starts[k], data=data))
        else:
            items = _take(sweep(code, base, s, data=data), len(segs[k]["items"]))
        swept.append(items)
    enc = encode(swept, code, base)
    ok, maps = {}, {}
    for k in range(len(segs)):
        maps[k] = _Maps(*regions)
        d = _compare(segs[k]["items"], swept[k], enc[k], maps[k], {}, {})
        if 0 < k < len(starts):
            last = swept[k][-1]
            if last.addr + last.size != starts[k]:
                d = d or _Diff(len(swept[k]) - 1, last.addr, "an item ending at the next arm",
                               f"{last.raw} running past {starts[k]:#x}")
        ok[k] = d
    if ok[0] is not None:
        return ok[0]
    # reachability over 13.6's encoded fingerprint
    edges = {k: reaches(segs[k]["items"], k + 1 if k + 1 < len(segs) else None,
                        segs[k]["exits"]) for k in range(len(segs))}
    wtypes = set()
    for k in range(1, len(segs)):
        for w in segs[k]["wtypes"]:
            todo, seen = [0, k], set()        # the dispatch's own code runs for every wtype
            while todo:
                x = todo.pop()
                if x in seen:
                    continue
                seen.add(x)
                todo.extend(edges[x])
            if all(ok[x] is None for x in seen):
                wtypes.add(w)
    dseg = default_segment(fn)
    defaults = {w for w in wtypes if dseg is not None and w in segs[dseg]["wtypes"]}
    notes = _shift_lines("renderer", [maps[k] for k in ok if ok[k] is None])
    for k in range(1, len(segs)):
        if ok[k] is not None:
            who = f"wt {', '.join(str(w) for w in segs[k]['wtypes'])}" if segs[k]["wtypes"] \
                else "the shared code after the table"
            notes.append(f"renderer arm {who} at {starts[k - 1]:#x} not proven: "
                         f"{ok[k].describe()}")
    unreached = [w for k in range(1, len(segs)) for w in segs[k]["wtypes"]
                 if ok[k] is None and w not in wtypes]
    if unreached:
        notes.append(f"renderer wtypes {_ranges(sorted(unreached))} not proven: their arms are "
                     f"twins but reach an arm that is not")
    return wtypes, defaults, notes


def _ranges(ws: list[int]) -> str:
    out, i = [], 0
    while i < len(ws):
        j = i
        while j + 1 < len(ws) and ws[j + 1] == ws[j] + 1:
            j += 1
        out.append(str(ws[i]) if i == j else f"{ws[i]}-{ws[j]}")
        i = j + 1
    return ", ".join(out) or "none"


def _copy(p: GrammarProof) -> GrammarProof:
    return dataclasses.replace(p, bounds=dict(p.bounds))


@memo.content_cached(copy=_copy)
def _prove(data: bytes, fp_text: str) -> GrammarProof:
    whole = json.loads(fp_text)
    fp = whole["functions"]
    code, base = section(data)
    regions = (tuple(whole["guicode_region"]), (base, base + len(code)))
    dec, dec_bounds, proof = _whole(code, base, "decoder", fp["decoder"], regions)
    lay, lay_bounds, p2 = _whole(code, base, "layout", fp["layout"], regions)
    ren, wtypes, defaults, p3 = _renderer(code, base, fp["renderer"], regions)
    bounds = {n: (a, b) for n, (a, b, _s) in {**dec_bounds, **lay_bounds}.items() if a != b}
    texts, p4 = True, []
    for name in TEXT_DRAWERS:
        try:
            _addr, _b, lines = _whole(code, base, name, fp[name], regions)
            p4 += lines
        except GrammarError as e:
            texts = False
            p4.append(f"{name}: not a twin (text drawers not proven): {e}")
    return GrammarProof(dec, lay, ren, bounds, tuple(proof + p2 + p3 + p4), wtypes, defaults,
                        texts)


def prove(data: bytes) -> GrammarProof:
    """The decoder, layout and renderer twins of 13.6's, with any differing named bound and the
    proven renderer wtypes, or `GrammarError` (code `GRAMMAR`). Memoised on content."""
    try:
        import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way
    except ImportError as e:                                    # pragma: no cover
        raise theme.CapstoneUnavailable(str(e)) from e
    return _prove(data, _fingerprint_text())
