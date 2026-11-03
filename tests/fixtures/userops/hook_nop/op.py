"""Loader fixture: nop the per-tick hook's `bl` (GuiCode+HOOK_OFF). Never flash it."""
from fireflymcf import container, followchannel, recipe, theme
from fireflymcf.userop import UserOp

NOPS = bytes.fromhex("00bf00bf")


@recipe.op("user:hook-nop")
class HookNop(UserOp):
    def build(self, ed, ctx):
        addr = theme.guicode_base(ed.section("GuiCode")) + followchannel.HOOK_OFF
        ed.patch_addr("GuiCode", addr, followchannel.HOOK_STOCK, NOPS, "nop the per-tick hook")

    def check(self, data, ctx):
        s = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
        got = data[s.offset + followchannel.HOOK_OFF:s.offset + followchannel.HOOK_OFF + 4]
        assert got == NOPS, got.hex()
        return "hook at GuiCode+%#x is %s" % (followchannel.HOOK_OFF, got.hex())
