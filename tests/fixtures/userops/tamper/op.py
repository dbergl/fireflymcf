"""Loader fixture: a build that writes the Editor's private buffer, bypassing the ledger."""
from fireflymcf import container, followchannel, recipe
from fireflymcf.userop import UserOp


@recipe.op("user:tamper")
class Tamper(UserOp):
    def build(self, ed, ctx):
        s = ed.section("GuiCode")
        at = s.offset + followchannel.HOOK_OFF
        ed._buf = ed._buf[:at] + bytes.fromhex("00bf00bf") + ed._buf[at + 4:]

    def check(self, data, ctx):
        return "unreachable"
