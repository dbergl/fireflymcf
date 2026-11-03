#!/usr/bin/env python3
"""Parse Spyder Controls / Firefly firmware packages.

Two formats are handled:

* ``.mcf`` packages (e.g. ``mcf.bin``): a 0xa4-byte header followed by a table
  of component descriptors (magic 0xdecd).  Each descriptor names a component,
  its file offset/size and the address it is programmed to.
* RAM images (e.g. ``cli.bin``): magic 0xc11b102e, 0x2c-byte header, payload
  loaded at a RAM address and entered through its own vector table.

Usage:
    python -m fireflymcf.emu.mcf info  <file>
    python -m fireflymcf.emu.mcf extract <file> <outdir> [--symbols file.sym]   # raw .bin + .elf per component
"""
import os
import struct
import sys
from dataclasses import dataclass

DESC_MAGIC = 0xDECD
GUI_MODULE_MAGIC = 0x1A2B3C4D  # GuiCode header: magic, 2 words, export table
RAMIMG_MAGIC = 0xC11B102E


@dataclass
class Component:
    index: int
    name: str
    version: int
    offset: int
    size: int
    load_addr: int
    data: bytes

    @property
    def gui_exports(self):
        """Thumb entry points exported by a GuiCode module header, else []."""
        if len(self.data) < 0x20 or struct.unpack_from("<I", self.data)[0] != GUI_MODULE_MAGIC:
            return []
        out = []
        for (v,) in struct.iter_unpack("<I", self.data[0x0C:0x30]):
            if not (v & 1 and self.load_addr <= v < self.load_addr + len(self.data)):
                break
            out.append(v & ~1)
        return out

    @property
    def is_code(self):
        # Code components begin with a Cortex-M vector table: SP in SRAM, Thumb reset vector.
        if len(self.data) < 8:
            return False
        sp, reset = struct.unpack_from("<II", self.data)
        return (sp & 0xFFF00000) == 0x20000000 and reset & 1


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1")


def parse_mcf(blob):
    title = _cstr(blob[0x24:0xA4])
    ptrs = []
    off = 0xA4
    while True:
        (p,) = struct.unpack_from("<I", blob, off)
        if p + 4 > len(blob) or struct.unpack_from("<H", blob, p)[0] != DESC_MAGIC:
            break
        ptrs.append(p)
        off += 4
    comps = []
    for i, p in enumerate(ptrs):
        magic, dlen = struct.unpack_from("<HH", blob, p)
        name = _cstr(blob[p + 4 : p + 0x44])
        version, foff, fsize = struct.unpack_from("<III", blob, p + 0x44)
        if dlen == 0x84:
            (load,) = struct.unpack_from("<I", blob, p + 0x5C)
        else:
            # Short (0x74) descriptor: load address sits 0x10 earlier.
            (load,) = struct.unpack_from("<I", blob, p + 0x58)
        comps.append(Component(i, name, version, foff, fsize, load, blob[foff : foff + fsize]))
    return title, comps


def parse_ramimg(blob):
    magic, ver, _, load, hdr, size = struct.unpack_from("<IIIIII", blob)
    assert magic == RAMIMG_MAGIC
    data = blob[hdr : hdr + size]
    return "RAM image", [Component(0, "ram", ver, hdr, size, load, data)]


def load(path):
    blob = open(path, "rb").read()
    if struct.unpack_from("<I", blob)[0] == RAMIMG_MAGIC:
        return parse_ramimg(blob)
    return parse_mcf(blob)


CORE_VECTORS = ["", "Reset", "NMI", "HardFault", "MemManage", "BusFault", "UsageFault",
                "", "", "", "", "SVC", "DebugMon", "", "PendSV", "SysTick"]


def vector_symbols(data, addr, count=128):
    """Name each distinct handler in a Cortex-M vector table at the start of ``data``."""
    syms = {}
    for i in range(1, min(count, len(data) // 4)):
        (v,) = struct.unpack_from("<I", data, 4 * i)
        if not v & 1 or not addr <= (v & ~1) < addr + len(data):
            continue
        name = (CORE_VECTORS[i] if i < 16 else f"IRQ{i - 16}") + "_Handler"
        syms.setdefault(v & ~1, name)
    return syms


def load_symbols(path):
    syms = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].split()
        if len(line) >= 2:
            syms[int(line[0], 0)] = line[1]
    return syms


def make_elf(data, addr, entry, executable, symbols=None):
    """Minimal ELF32 ARM: one PT_LOAD segment, .text, and FUNC symbols for Ghidra."""
    symbols = {a: n for a, n in (symbols or {}).items() if addr <= a < addr + len(data)}
    ehsize, phsize, shsize = 52, 32, 40
    shstr = b"\0.text\0.shstrtab\0.symtab\0.strtab\0"
    strtab = bytearray(b"\0")
    symtab = bytearray(16)  # null symbol
    for a, name in sorted(symbols.items()):
        symtab += struct.pack("<IIIBBH", len(strtab), a | 1, 0, 0x12, 0, 1)  # GLOBAL FUNC, .text
        strtab += name.encode() + b"\0"
    data_off = ehsize + phsize
    parts = [data, shstr, bytes(symtab), bytes(strtab)]
    offs = []
    pos = data_off
    for p in parts:
        pos = (pos + 3) & ~3
        offs.append(pos)
        pos += len(p)
    sh_off = (pos + 3) & ~3
    flags = 5 if executable else 4  # R+X / R
    eh = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    eh += struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, entry, ehsize, sh_off,
                      0x05000400,  # EABI5, hard-float
                      ehsize, phsize, 1, shsize, 5, 2)
    ph = struct.pack("<IIIIIIII", 1, data_off, addr, addr, len(data), len(data), flags, 4)
    sh = b"\0" * shsize
    sh += struct.pack("<IIIIIIIIII", 1, 1, 0x6 if executable else 0x2, addr, offs[0],
                      len(data), 0, 0, 4, 0)
    sh += struct.pack("<IIIIIIIIII", 7, 3, 0, 0, offs[1], len(shstr), 0, 0, 1, 0)
    sh += struct.pack("<IIIIIIIIII", 17, 2, 0, 0, offs[2], len(symtab), 4, 1, 4, 16)
    sh += struct.pack("<IIIIIIIIII", 25, 3, 0, 0, offs[3], len(strtab), 0, 0, 1, 0)
    out = bytearray(eh + ph)
    for off, p in zip(offs, parts):
        out += b"\0" * (off - len(out)) + p
    out += b"\0" * (sh_off - len(out))
    return bytes(out + sh)


def main(argv):
    symfile = None
    if "--symbols" in argv:
        i = argv.index("--symbols")
        symfile = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 3 or argv[1] not in ("info", "extract"):
        print(__doc__)
        return 1
    title, comps = load(argv[2])
    print(f"{argv[2]}: {title}")
    for c in comps:
        kind = "code" if c.is_code else "data"
        extra = ""
        if c.is_code:
            sp, reset = struct.unpack_from("<II", c.data)
            extra = f" sp=0x{sp:08x} reset=0x{reset:08x}"
        print(f"  [{c.index}] {c.name!r:28} ver=0x{c.version:05x} off=0x{c.offset:06x} "
              f"size=0x{c.size:06x} addr=0x{c.load_addr:08x} {kind}{extra}")
    if argv[1] == "extract":
        out = argv[3]
        os.makedirs(out, exist_ok=True)
        for c in comps:
            stem = os.path.join(out, f"{c.index}_{c.name.replace(' ', '_') or 'unnamed'}"
                                     f"_{c.load_addr:08x}")
            open(stem + ".bin", "wb").write(c.data)
            entry = struct.unpack_from("<I", c.data, 4)[0] if c.is_code else c.load_addr
            syms = vector_symbols(c.data, c.load_addr) if c.is_code else {}
            syms.update({a: f"gui_export{i}" for i, a in enumerate(c.gui_exports)})
            executable = c.is_code or bool(c.gui_exports)
            if symfile:
                syms.update(load_symbols(symfile))
            open(stem + ".elf", "wb").write(make_elf(c.data, c.load_addr, entry, executable, syms))
            print(f"  wrote {stem}.bin/.elf")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
