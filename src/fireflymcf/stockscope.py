"""The recipe's stock image, for readers that must judge an edit against the image a build
started from rather than the one an earlier op left (2026-11-08).

`recipe` holds `stock_scope(ctx.stock)` around every op's apply and verify. Inside it,
`stock_image(data)` is the compile's stock; outside it, a direct caller's `data` is taken as the
stock. Readers: `donors` (record witnesses, as `witness_scope`/`witness_image`) and
`statusbind.spare_cell_indices` (the stock row count of the display-cell table).

A built image used as a compile base is that recipe's stock. A reader that cannot judge such an
image (one whose display-cell table was grown) refuses rather than guess.
"""
from __future__ import annotations

import contextlib
import contextvars

_STOCK_IMAGE: contextvars.ContextVar = contextvars.ContextVar("fireflymcf_stock_image",
                                                              default=None)


@contextlib.contextmanager
def stock_scope(stock: bytes | None):
    """Within the block, `stock_image` returns `stock` (None: each caller's own `data`)."""
    token = _STOCK_IMAGE.set(None if stock is None else bytes(stock))
    try:
        yield
    finally:
        _STOCK_IMAGE.reset(token)


def stock_image(data: bytes) -> bytes:
    """The recipe's stock inside `stock_scope`, else `data` itself."""
    stock = _STOCK_IMAGE.get()
    return bytes(data) if stock is None else stock
