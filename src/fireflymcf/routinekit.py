"""Primitives a user op may call through `userop.Editor.call` (filled by Task 9).

`PRIMITIVES` maps a name to a callable `f(data: bytes, *args, **kwargs)` returning the new
bytes, or `(new_bytes, result)`. Empty until Task 9 fills it.
"""

PRIMITIVES: dict = {}
