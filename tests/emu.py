# tests/emu.py
"""Run an authored GuiCode routine on an emulated Cortex-M (Unicorn) and read memory back.

The guarantee is BYTE-level, not page-level. Unicorn only maps whole 4 KB pages, so mapping one
caller-given address also maps its unprovided page-neighbours; a page-granular check would let a
routine that touches an adjacent, wrong slot (the PC.44 class of bug) read or write silently
instead of faulting. `run` maps the pages that hold the caller's addresses, the code and a stack
page, then installs read/write hooks that check every accessed BYTE against the exact set the
caller provided (the keys of `mem`, the code span, the stack span) and stop emulation the moment
one falls outside it. A routine that stores to or loads from a byte the test did not provide
faults -- either via that hook (a page-neighbour the caller did not provide) or via Unicorn's own
`UC_ERR_*_UNMAPPED` (a page nothing in this call touches) -- both raised as `AssertionError`
naming the address, size and access kind, with the word "unmapped" so callers can match on it.
Unicorn is a REQUIRED test dependency (pyproject `test` extra, docs/TESTING.md)."""
try:
    from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_ERR_FETCH_UNMAPPED, UC_ERR_READ_UNMAPPED,
                          UC_ERR_WRITE_UNMAPPED, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                          UC_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB)
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP
except ImportError as e:                     # fail loudly: never let routine tests skip
    raise ImportError(
        "tests/emu.py needs the unicorn CPU emulator: "
        "python3 -m pip install --user --break-system-packages 'unicorn>=2.1' "
        "(or pip install -e '.[full,test]')") from e

PAGE = 0x1000
STACK_TOP = 0x2004F000

# Unicorn error codes that mean "this address is not mapped at all" -- worth calling out as
# "unmapped" in the message. Anything else (an alignment fault, an invalid instruction) is a
# different kind of failure and should not carry that framing.
_UNMAPPED_ERRNOS = {UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED, UC_ERR_FETCH_UNMAPPED}


def _pages(addrs):
    return sorted({a & ~(PAGE - 1) for a in addrs})


def run(code: bytes, base: int, mem: dict, *, stop_at: int, max_insns: int = 5000) -> dict:
    if base % 4:
        raise ValueError(f"base {base:#x} is not 4-aligned")
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    code_span = range(base, base + len(code))
    stack_span = range(STACK_TOP - PAGE, STACK_TOP)
    for a in mem:
        if a in code_span:
            raise ValueError(f"mem address {a:#x} overlaps the code")

    # Map every page the code spans (not just its first and last byte's page), plus the pages
    # holding stop_at, the stack, and every caller-given address.
    code_pages = range(base & ~(PAGE - 1), base + len(code), PAGE)
    need = set(mem) | set(code_pages) | {stop_at, STACK_TOP - PAGE}
    for page in _pages(need):
        mu.mem_map(page, PAGE)
    mu.mem_write(base, bytes(code))
    for a, v in mem.items():
        mu.mem_write(a, bytes([v & 0xFF]))
    mu.reg_write(UC_ARM_REG_SP, STACK_TOP - 16)
    mu.reg_write(UC_ARM_REG_LR, 0xFFFFFFFF)

    # The exact bytes this call permits -- narrower than what got mapped above, since mapping is
    # page-granular and this guarantee is not.
    allowed = set(mem) | set(code_span) | set(stack_span)
    violations = []

    def _watch(uc, access, address, size, value, user_data):
        bad = next((a for a in range(address, address + size) if a not in allowed), None)
        if bad is not None:
            kind = "write" if access == UC_MEM_WRITE else "read"
            violations.append((kind, bad, size))
            uc.emu_stop()

    mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, _watch)

    try:
        mu.emu_start(base | 1, stop_at, count=max_insns)
    except UcError as e:
        if e.errno in _UNMAPPED_ERRNOS:
            raise AssertionError(f"routine faulted on an unmapped address ({e})") from None
        raise AssertionError(f"routine faulted ({e})") from None
    if violations:
        kind, addr, size = violations[0]
        raise AssertionError(
            f"routine {kind} of {size} byte(s) at {addr:#x} touched an unmapped address: not "
            "provided to emu.run (mem, code or stack)")
    pc = mu.reg_read(UC_ARM_REG_PC)
    if pc != stop_at:
        raise AssertionError(f"routine did not reach stop_at {stop_at:#x} within {max_insns} "
                             f"instructions (pc={pc:#x})")
    return {a: mu.mem_read(a, 1)[0] for a in mem}
