"""Gate fixture: edits nothing and declares one gained (channel, command) pair, so a neighbouring
op that makes that gain without declaring it passes the capability gate. Never flash it."""
from fireflymcf import recipe
from fireflymcf.userop import UserOp


@recipe.op("user:declare-gain")
class DeclareGain(UserOp):
    PARAMS = {"properties": {"channel": {"type": "integer"}, "command": {"type": "integer"}},
              "required": ["channel", "command"]}

    def build(self, ed, ctx):
        pass

    def capability_delta(self, ctx):
        p = self.spec.params
        return set(), {(p["channel"], p["command"])}

    def check(self, data, ctx):
        return "declares (%d, %d)" % (self.spec.params["channel"], self.spec.params["command"])
