"""The reference coach's Low Volts builds, compiled in-test, with ops dropped or appended.

Both images come from one research recipe, `builds/mcf-13.6-low-volts-move.ffproj.json` (v52):
the "move" image is that recipe as is; the "hold" image drops its last three ops (`HOLD_DROP`:
the Low Volts removal, the gear fix and the AES-settings control), leaving the 3 s Low Volts
hold. `compile_with` compiles through ONE module-level `recipe.compile_cached` cache, so the
shared prefix compiles once per worker and every variant re-applies only its tail.

`compile_cached` applies an unverified op like any other: the `hardware_verified` refusal is the
CLI's (`cli.py`, `--allow-unverified`), not the engine's, so no keyword is needed for it here.
"""
from pathlib import Path

from fireflymcf import recipe

REPO_ROOT = Path(__file__).resolve().parent.parent
STOCK_136 = REPO_ROOT / "firefly-13.6" / "mcf.bin"
LOW_VOLTS_MOVE = REPO_ROOT / "builds" / "mcf-13.6-low-volts-move.ffproj.json"
HOLD_DROP = ("lv-off-elec", "lv-gear", "lv-aes-settings")

_CACHE: dict = {}


def available() -> bool:
    """Whether the research data these builds need is linked in."""
    return STOCK_136.exists() and LOW_VOLTS_MOVE.exists()


def compile_with(recipe_path, *extra_specs: recipe.OpSpec,
                 drop: tuple = ()) -> recipe.CompileResult:
    """Compile the recipe at `recipe_path` with the ops whose ids are in `drop` removed and
    `extra_specs` appended in order. Every id in `drop` must name an op of the recipe."""
    project = recipe.Project.from_json(Path(recipe_path).read_text())
    missing = set(drop) - {s.id for s in project.ops}
    assert not missing, "no such op in %s: %s" % (recipe_path, sorted(missing))
    project.ops = [s for s in project.ops if s.id not in drop] + list(extra_specs)
    return recipe.compile_cached(project, STOCK_136.read_bytes(), _CACHE)


def image(recipe_path, *extra_specs: recipe.OpSpec, drop: tuple = ()) -> bytes:
    """`compile_with(...)`'s image bytes; asserts the compile succeeded."""
    res = compile_with(recipe_path, *extra_specs, drop=drop)
    assert res.ok, res.errors
    return res.data
