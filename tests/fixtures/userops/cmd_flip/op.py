"""Gate fixture: change the command byte of the first channel binding a group sends, to a command
that channel had no binding for, so the image gains one (channel, command) pair. With
`params.declare` it declares that gain in capability_delta; without, the capability gate must
refuse it. Never flash it."""
from fireflymcf import guidata, recipe
from fireflymcf.userop import UserOp

CMD = 4                              # Binding.raw index of the wire command


def _target(data):
    caps = recipe.capabilities(data)
    gt, bt = guidata.group_table(data), guidata.binding_table(data)
    for g in gt:
        for j in range(g.start, g.start + g.count):
            if 0 <= j < len(bt) and bt[j].channel is not None:
                b = bt[j]
                new = next(c for c in range(256) if (b.channel, c) not in caps)
                return b, new
    raise recipe.RecipeError("NO_TARGET", "no channel binding")


@recipe.op("user:cmd-flip")
class CmdFlip(UserOp):
    PARAMS = {"properties": {"declare": {"type": "boolean"}}, "required": []}

    def build(self, ed, ctx):
        b, new = _target(ed.data)
        off = guidata.guidata_extent(ed.data)[0] + b.offset + CMD
        ed.patch(off, bytes([b.raw[CMD]]), bytes([new]), "flip one binding's command")
        ctx.handles[self.spec.id] = {"gained": [b.channel, new]}

    def capability_delta(self, ctx):
        if not self.spec.params.get("declare"):
            return set(), set()
        return set(), {tuple(ctx.handles[self.spec.id]["gained"])}

    def check(self, data, ctx):
        return "gained %s" % (ctx.handles[self.spec.id]["gained"],)
