"""The per-tick chain budget: `followchannel.chain_from_hook` walks a chain of any length and
refuses only a loop; compile refuses (TICK_CHAIN_FULL) the op that goes past the project's
`tick_chain_limit`, which defaults to `followchannel.DEFAULT_TICK_CHAIN_LIMIT` and can be raised.
"""
import hashlib
import json

import pytest

from fireflymcf import followchannel, recipe, routinekit, theme, thumb, userop

NEEDS_RESEARCH_DATA = "installs per-tick routines on the 13.6 corpus image"

DEFAULT = followchannel.DEFAULT_TICK_CHAIN_LIMIT


def _counter_build(cell: int):
    def build(code_load_addr, tail_target):
        r = thumb.Routine({"cell": cell}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        r.ldr_lit(0, "cell")
        r.emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
        r.emit(2, lambda h, s: (thumb.adds_imm8(1, 1), "adds r1, #1"))
        r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
        routinekit.emit_tail(r, tail_target)
        return routinekit.assemble_tick(r, code_load_addr)
    return build


def _install(data: bytes, n: int) -> bytes:
    ed = userop.Editor(data)
    for i in range(n):
        cell = ed.call("fresh_sram", 4)
        routinekit.install_tick(ed, _counter_build(cell), "counter %d" % i)
    return ed.data


@pytest.fixture(scope="module")
def long_chain(mcf_136):
    return _install(mcf_136, DEFAULT + 4)


@pytest.fixture
def tick_op():
    """A test op that installs one counter routine on the per-tick chain."""
    @recipe.op("test_tick_counter")
    class TickCounter(recipe.Op):
        def apply(self, data, ctx):
            return _install(data, 1)

        def verify(self, data, ctx):
            return "counter"
    yield "test_tick_counter"
    del recipe.REGISTRY["test_tick_counter"]


def _project(mcf_136, n: int, limit=None) -> recipe.Project:
    return recipe.Project(base_sha256=hashlib.sha256(mcf_136).hexdigest(), build_name="tick",
                          ops=[recipe.OpSpec(id="t%d" % i, type="test_tick_counter", params={})
                               for i in range(n)],
                          tick_chain_limit=limit)


# --- the walker -----------------------------------------------------------------------------

def test_walker_follows_a_chain_past_the_default(long_chain):
    chain = followchannel.chain_from_hook(long_chain)
    assert len(chain) == DEFAULT + 4 + 1
    assert chain[-1] == followchannel.TAIL_TARGET


def test_walker_limit_counts_routines(long_chain):
    assert len(followchannel.chain_from_hook(long_chain, DEFAULT + 4)) == DEFAULT + 5
    with pytest.raises(followchannel.TickChainFull) as e:
        followchannel.chain_from_hook(long_chain, DEFAULT + 3)
    assert (e.value.count, e.value.limit) == (DEFAULT + 4, DEFAULT + 3)


def test_walker_refuses_a_loop(mcf_136):
    """Retarget the oldest routine's tail-branch at the newest: the chain now never reaches the
    paint fn, and the walker must say so rather than spin or blame the length."""
    data = bytearray(_install(mcf_136, 3))
    newest, _, oldest, _ = followchannel.chain_from_hook(bytes(data))
    sec = theme.guicode_section(bytes(data))
    base = theme.GUICODE_BASE_DEFAULT
    code = bytes(data[sec.offset + oldest - base:sec.offset + sec.length - 4])
    at = next(a for a, t in thumb.disassemble(code, oldest) if t.startswith("b.w #"))
    off = sec.offset + at - base
    data[off:off + 4] = thumb.b_w(at, newest)
    with pytest.raises(followchannel.FollowChannelError, match="loops back") as e:
        followchannel.chain_from_hook(bytes(data))
    assert not isinstance(e.value, followchannel.TickChainFull)


# --- compile --------------------------------------------------------------------------------

def test_compile_refuses_the_op_past_the_default(mcf_136, tick_op):
    res = recipe.compile_project(_project(mcf_136, DEFAULT + 1), mcf_136)
    assert not res.ok
    (err,) = res.errors
    assert err["code"] == "TICK_CHAIN_FULL"
    assert err["op_id"] == "t%d" % DEFAULT
    assert '"tick_chain_limit": %d' % (DEFAULT + 1) in err["message"]
    assert err["offers"] == ["raise_tick_chain_limit"]


def test_compile_allows_the_default(mcf_136, tick_op):
    res = recipe.compile_project(_project(mcf_136, DEFAULT), mcf_136)
    assert res.ok, res.errors


def test_raised_limit_compiles_the_same_bytes(mcf_136, tick_op):
    """The limit changes whether a build is allowed, never its bytes."""
    raised = recipe.compile_project(_project(mcf_136, DEFAULT + 1, DEFAULT + 1), mcf_136)
    assert raised.ok, raised.errors
    assert len(followchannel.chain_from_hook(raised.data)) == DEFAULT + 2
    higher = recipe.compile_project(_project(mcf_136, DEFAULT + 1, 200), mcf_136)
    assert higher.data == raised.data


def test_lowered_limit_refuses_a_short_chain(mcf_136, tick_op):
    res = recipe.compile_project(_project(mcf_136, 3, 2), mcf_136)
    assert [e["code"] for e in res.errors] == ["TICK_CHAIN_FULL"]
    assert res.errors[0]["op_id"] == "t2"


def test_cached_compile_enforces_a_lowered_limit(mcf_136, tick_op):
    """A prefix cached under a high limit must not let a lower one through."""
    cache = {}
    assert recipe.compile_cached(_project(mcf_136, 3, 8), mcf_136, cache).ok
    res = recipe.compile_cached(_project(mcf_136, 3, 2), mcf_136, cache)
    assert [e["code"] for e in res.errors] == ["TICK_CHAIN_FULL"]


# --- the project field ----------------------------------------------------------------------

def _doc(**extra):
    return json.dumps({"schema": 1, "base_sha256": "0" * 64, "build_name": "x", "ops": [],
                       **extra})


def test_field_round_trips_and_is_omitted_when_unset():
    p = recipe.Project.from_json(_doc(tick_chain_limit=24))
    assert p.tick_chain_limit == 24 == p.effective_tick_chain_limit
    assert json.loads(p.to_json())["tick_chain_limit"] == 24
    q = recipe.Project.from_json(_doc())
    assert q.tick_chain_limit is None and q.effective_tick_chain_limit == DEFAULT
    assert "tick_chain_limit" not in json.loads(q.to_json())


@pytest.mark.parametrize("bad", [0, -1, True, 16.0, "16"])
def test_field_refuses_a_non_positive_integer(bad):
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(_doc(tick_chain_limit=bad))
    assert e.value.code == "BAD_PROJECT"


def test_unset_limit_leaves_the_cache_key_unchanged():
    specs = [recipe.OpSpec(id="a", type="rename_label", params={})]
    assert recipe._prefix_key("h", specs) == recipe._prefix_key("h", specs, None)
    assert recipe._prefix_key("h", specs) != recipe._prefix_key("h", specs, 16)
