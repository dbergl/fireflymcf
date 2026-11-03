"""Loader fixture: a build that raises a plain KeyError."""
from fireflymcf import recipe
from fireflymcf.userop import UserOp


@recipe.op("user:crash")
class Crash(UserOp):
    def build(self, ed, ctx):
        return {}["x"]

    def check(self, data, ctx):
        return "unreachable"
