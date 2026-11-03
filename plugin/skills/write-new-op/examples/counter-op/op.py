"""user:tick-counter: add `step` to a u32 in fresh SRAM on every GuiCode tick.

The smallest complete per-tick routine, written against `userop.Editor` and `routinekit` only. It
draws nothing and sends nothing, so it is safe to emulate and pointless to flash: copy it as the
shape of a real op (fresh cell, two-phase install, link contract, a tag the test can find).

Routine (each link of the hook chain obeys this contract, docs/USER-OPS.md "Per-tick routines"):

    TAG "uCNT", cell address        ; prefix data at code_load_addr: emu_test.py finds the cell
    push {r0, r1, r2, r3, lr}       ; emit_prologue
    ldr  r0, =cell
    ldr  r1, [r0]
    adds r1, #step
    str  r1, [r0]
    pop.w {r0, r1, r2, r3, lr}      ; emit_tail
    b.w  <previous link>            ;   ... the first b.w: chain_from_hook follows it
"""
import struct

from fireflymcf import container, followchannel, recipe, routinekit, theme, thumb
from fireflymcf.userop import UserOp

TAG = b"uCNT"          # 4 bytes, so the cell word after it stays 4-aligned


def find_tagged(data: bytes) -> tuple[int, int]:
    """`(entry_addr, cell)` of the installed counter, found by its tag in GuiCode."""
    gc = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
    body = data[gc.offset:gc.offset + gc.length]
    at = body.find(TAG)
    if at < 0 or body.find(TAG, at + 1) >= 0:
        raise AssertionError(f"expected exactly one {TAG!r} tag in GuiCode")
    cell = struct.unpack_from("<I", body, at + 4)[0]
    return theme.GUICODE_BASE_DEFAULT + at + 8, cell


@recipe.op("user:tick-counter")
class TickCounter(UserOp):
    PARAMS = {"properties": {"step": {"type": "integer", "minimum": 1, "maximum": 255,
                                      "default": 1}},
              "required": []}

    def validate(self):
        step = self._param("step", 1, int)
        if not 1 <= step <= 255:                 # adds T2 takes an 8-bit immediate
            raise recipe.RecipeError("BAD_PARAMS", f"{self.type}: step {step} is not 1-255",
                                     op_id=self.spec.id)
        self.step = step

    def build(self, ed, ctx):
        cell = ed.call("fresh_sram", 4)          # SRAM no form reaches; garbage at power-on

        def assemble(code_load_addr, tail_target):
            r = thumb.Routine({"cell": cell}, error=routinekit.RoutineKitError)
            routinekit.emit_prologue(r)
            r.ldr_lit(0, "cell")
            r.emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
            r.emit(2, lambda h, s: (thumb.adds_imm8(1, self.step), f"adds r1, #{self.step}"))
            r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
            routinekit.emit_tail(r, tail_target)
            return routinekit.assemble_tick(r, code_load_addr,
                                            prefix=TAG + struct.pack("<I", cell))

        inst = routinekit.install_tick(ed, assemble, "tick counter")
        ctx.handles[self.spec.id] = {"cell": cell, "entry": inst.entry_addr}

    def check(self, data, ctx):
        entry, cell = find_tagged(data)
        chain = followchannel.chain_from_hook(data)
        assert entry in chain, f"counter entry {entry:#x} is not on the tick chain {chain}"
        return f"counter at {entry:#x} adds {self.step} to {cell:#x} each tick"
