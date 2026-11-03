"""Direct libunicorn access: 32-bit ARM registers, MMIO and interrupt-hook callbacks.

Unicorn's Python reg_read/reg_write dispatch through several layers per call
(~3 us); exception entry/return does ~20 of them, thousands of times a second.
These call the C API through the binding's own ctypes handle instead.
"""
import ctypes

from unicorn import UC_HOOK_INTR, UcError
from unicorn.unicorn_py3.unicorn import uclib


class FastRegs:
    def __init__(self, uc):
        self._h = uc._uch
        self._val = ctypes.c_uint32()
        self._ptr = ctypes.byref(self._val)
        self._read = uclib.uc_reg_read
        self._write = uclib.uc_reg_write

    def read(self, reg):
        self._read(self._h, reg, self._ptr)
        return self._val.value

    def write(self, reg, value):
        self._val.value = value & 0xFFFFFFFF
        self._write(self._h, reg, self._ptr)

    def batch(self, regs):
        """Prepare a register set; returns (read() -> list, write(values))."""
        n = len(regs)
        ids = (ctypes.c_int * n)(*regs)
        vals = (ctypes.c_uint32 * n)()
        ptrs = (ctypes.c_void_p * n)(*[ctypes.addressof(vals) + 4 * i for i in range(n)])
        h, rd, wr = self._h, uclib.uc_reg_read_batch, uclib.uc_reg_write_batch

        def read():
            rd(h, ids, ptrs, n)
            return vals[:]

        def write(values):
            for i, v in enumerate(values):
                vals[i] = v & 0xFFFFFFFF
            wr(h, ids, ptrs, n)

        return read, write


# -- callbacks registered straight with libunicorn ------------------------------
# The binding wraps every callback in two extra Python frames plus an exception
# guard; MMIO and the SVC/EXC_RETURN hook fire ~100k times per simulated second.
# These callers must catch their own exceptions (a raise inside a ctypes callback
# is only printed, never propagated).

_MMIO_READ = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint,
                              ctypes.c_void_p)
_MMIO_WRITE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint,
                               ctypes.c_uint64, ctypes.c_void_p)
_HOOK_INTR = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
_keep = []  # ctypes thunks must outlive the engine


def mmio_map(uc, base, size, read, write):
    """Map MMIO with read(address, size) -> int and write(address, size, value)."""
    rd = _MMIO_READ(lambda h, off, sz, ud: read(base + off, sz))
    wr = _MMIO_WRITE(lambda h, off, sz, val, ud: write(base + off, sz, val))
    _keep.append((rd, wr))
    status = uclib.uc_mmio_map(uc._uch, base, size, rd, None, wr, None)
    if status:
        raise UcError(status)


def mmio_map_offsets(uc, base, size, read, write):
    """As mmio_map, but write(offset, size, value) -- offset from ``base``, one frame
    less on the hottest path.  ``write`` must catch its own exceptions."""
    rd = _MMIO_READ(lambda h, off, sz, ud: read(base + off, sz))
    wr = _MMIO_WRITE(lambda h, off, sz, val, ud: write(off, sz, val))
    _keep.append((rd, wr))
    status = uclib.uc_mmio_map(uc._uch, base, size, rd, None, wr, None)
    if status:
        raise UcError(status)


def hook_intr(uc, callback):
    """UC_HOOK_INTR with callback(intno)."""
    cb = _HOOK_INTR(lambda h, intno, ud: callback(intno))
    _keep.append(cb)
    handle = ctypes.c_size_t()
    status = uclib.uc_hook_add(uc._uch, ctypes.byref(handle), UC_HOOK_INTR, cb, None,
                               ctypes.c_uint64(1), ctypes.c_uint64(0))
    if status:
        raise UcError(status)
