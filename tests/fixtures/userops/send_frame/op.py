"""Gate fixture: chain a per-tick routine whose prefix holds a D1 write and a D3 read-back
template for selector 0xEE and that sends both through the host CAN sender
(`routinekit.emit_send_frame`). With `params.send` false the routine only returns: it carries no
template and no sender pointer. Its manifest touches GuiCode only. Never flash it."""
from fireflymcf import recipe, routinekit, thumb
from fireflymcf.userop import UserOp

SEL = 0xEE


@recipe.op("user:send-frame")
class SendFrame(UserOp):
    PARAMS = {"properties": {"send": {"type": "boolean"}}, "required": []}

    def build(self, ed, ctx):
        send = self.spec.params.get("send", True)

        def assemble(load, tail):
            r = thumb.Routine({}, error=routinekit.RoutineKitError)
            routinekit.emit_prologue(r)
            prefix = b""
            if send:
                prefix = (routinekit.config_frame(SEL, 0, 0xD1)
                          + routinekit.config_frame(SEL, 0xFFFF, 0xD3))
                routinekit.emit_send_frame(r, load)
                routinekit.emit_send_frame(r, load + 12)
            routinekit.emit_tail(r, tail)
            return routinekit.assemble_tick(r, load, prefix=prefix)

        ctx.handles[self.spec.id] = routinekit.install_tick(ed, assemble, "send frame").entry_addr

    def check(self, data, ctx):
        return "tick routine at %#x" % ctx.handles[self.spec.id]
