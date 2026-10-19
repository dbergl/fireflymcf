"""Content-keyed memoisation for the pure `f(data, ...)` readers.

Almost every reader in this package is a pure function of an MCF's *bytes* -- the table
parsers, the object-pointer scan, the GuiCode disassembly passes -- and a build or a test
run calls the same one many times on the same bytes: `builds/from_stock.py` parsed the
object tables 74 times, the binding table 2,585 times and disassembled GuiCode 29 times
before anything here was cached (profiled 2026-09-01). This module is the one place that
memoisation lives, so the rules are stated once:

* **Keyed on content, never on `id()`.** `bytes(data)` is the key: a no-op for the common
  immutable case, a ~0.1 ms copy for a `bytearray`. An `id()`-keyed cache went stale after
  an in-place edit left the id unchanged (the glyphs module's warning); a content key cannot.
  Hashing a fresh 1 MB `bytes` costs ~0.3 ms, and CPython caches the hash on the object, so a
  repeated call with the same object pays nothing.
* **Bounded.** `maxsize=8` per function: a cache pins its keys, and a build produces a new
  1 MB image per reseal, so an unbounded cache would hold every intermediate.
* **Copied out.** The wrapper returns `copy(result)` so a caller mutating what it got back
  cannot corrupt the cache. The default copy is `list`/`dict`/`set` by type; pass `copy` for
  anything else (or `copy=None` when the result is immutable, e.g. a tuple).
* **Extra arguments must be hashable** and are part of the key.
"""
from __future__ import annotations

import functools
from typing import Any, Callable


def _default_copy(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, set):
        return set(value)
    return value


def content_cached(fn: Callable | None = None, *, maxsize: int = 8,
                   copy: Callable | None = _default_copy):
    """Memoise `fn(data, *args)` on the CONTENT of `data` plus `args`.

    Usable bare (`@content_cached`) or with options (`@content_cached(copy=None)`). The
    wrapped function gains `cache_clear()` and `cache_info()`.
    """
    def decorate(f: Callable) -> Callable:
        inner = functools.lru_cache(maxsize=maxsize)(f)

        @functools.wraps(f)
        def wrapper(data, *args):
            result = inner(bytes(data), *args)
            return copy(result) if copy is not None else result

        wrapper.cache_clear = inner.cache_clear      # type: ignore[attr-defined]
        wrapper.cache_info = inner.cache_info        # type: ignore[attr-defined]
        return wrapper

    return decorate(fn) if fn is not None else decorate
