"""Optional native helpers, compiled on first use with the system C compiler.

Nothing here is required: when no compiler is available the emulator falls
back to pure-Python/Unicorn equivalents (slower).
"""
import ctypes
import hashlib
import logging
import os
import subprocess
import tempfile

from unicorn import UC_HOOK_BLOCK
from unicorn.unicorn_py3.unicorn import uclib

log = logging.getLogger("native")

HERE = os.path.dirname(os.path.abspath(__file__))


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "fireflymcf-emu")


def build(name):
    """Compile native/<name>.c into a cached shared library; returns its CDLL or None."""
    src = os.path.join(HERE, name + ".c")
    with open(src, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    out = os.path.join(_cache_dir(), f"{name}-{digest}.so")
    if not os.path.exists(out):
        cc = os.environ.get("CC", "cc")
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".so", dir=os.path.dirname(out))
            os.close(fd)
            subprocess.run([cc, "-O2", "-shared", "-fPIC", "-o", tmp, src], check=True,
                           capture_output=True)
            os.replace(tmp, out)
        except (OSError, subprocess.CalledProcessError) as e:
            log.info("native %s unavailable (%s); using the slower fallback", name, e)
            return None
    return ctypes.CDLL(out)


class _SlicerState(ctypes.Structure):
    _fields_ = [("budget", ctypes.c_int64), ("used", ctypes.c_uint64), ("stop", ctypes.c_void_p)]


class Slicer:
    """Instruction budget per emu_start via a native block hook (see slicer.c).

    Unicorn's own ``count`` limit runs a helper on every instruction (~3.7x
    slower); this one runs once per translated block.  Cost is charged in
    Thumb halfwords: 1 per 16-bit and 2 per 32-bit instruction.
    """

    def __init__(self, uc, lib):
        self.state = _SlicerState(0, 0, ctypes.cast(uclib.uc_emu_stop, ctypes.c_void_p).value)
        self._fn = ctypes.cast(lib.slicer_block, ctypes.c_void_p)
        handle = ctypes.c_size_t()
        status = uclib.uc_hook_add(uc._uch, ctypes.byref(handle), UC_HOOK_BLOCK, self._fn,
                                   ctypes.byref(self.state), ctypes.c_uint64(1), ctypes.c_uint64(0))
        if status:
            raise RuntimeError(f"uc_hook_add(UC_HOOK_BLOCK) failed: {status}")

    def arm(self, budget):
        self.state.budget = budget
        self.state.used = 0

    @property
    def used(self):
        return self.state.used


def slicer(uc):
    """A Slicer for ``uc``, or None if the native helper cannot be built."""
    if os.environ.get("FIREFLY_NO_NATIVE"):
        return None
    lib = build("slicer")
    return Slicer(uc, lib) if lib is not None else None
