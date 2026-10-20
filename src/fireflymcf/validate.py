"""Build-agnostic pre-flash validation.

Compile a recipe through every universal gate `recipe.compile_project` already runs — the
structural gates, the capability gate, each op's own `verify()`, and the seal check — and render
one PASS/FAIL verdict. This is the single definition of "validated": the `validate` CLI command
and the research repository's recipe tests both call `validate()` so they can never disagree on
what a safe-to-flash build is. Read-only: it writes no image.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import recipe

# Corpus-relative stock images `resolve_stock` searches when the caller passes no explicit stock.
# A recipe pins its base by sha256, so the search matches on the hash, not the filename. Extend
# this tuple when a new corpus version lands.
KNOWN_STOCK = (
    "firefly-11.23/mcf.bin",
    "firefly-13.6/mcf.bin",
    "firefly-14.7 (Terrain)/mcf.bin",
)


@dataclass
class ValidationResult:
    passed: bool
    report: str
    unverified: list
    errors: list


def resolve_stock(project: "recipe.Project", repo_root: Path) -> bytes:
    """The stock image `project.base_sha256` names, found under `repo_root`. Raises
    `recipe.RecipeError('BASE_MISMATCH', ...)` if no known corpus image matches the hash."""
    for rel in KNOWN_STOCK:
        p = repo_root / rel
        if p.is_file():
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() == project.base_sha256:
                return data
    raise recipe.RecipeError(
        "BASE_MISMATCH",
        "no stock image under %s matches the project's base_sha256 %s"
        % (repo_root, project.base_sha256))


def _format_report(project: "recipe.Project", result: "recipe.CompileResult") -> str:
    lines = ["%d op(s), %d enabled"
             % (len(project.ops), sum(o.enabled for o in project.ops))]
    for line in result.manifest:
        lines.append("  " + line)
    if result.unverified:
        lines.append("  UNVERIFIED on hardware: " + ", ".join(result.unverified))
    return "\n".join(lines)


def validate(project: "recipe.Project", stock: bytes,
             require_verified: bool = False) -> ValidationResult:
    """Compile `project` against `stock` through every universal gate and return a verdict.

    A structural-gate failure (`AssertionError`) or a structured refusal (`recipe.RecipeError`)
    becomes a FAIL verdict rather than a traceback; an unexpected exception from an op is left to
    propagate. `unverified` ops are reported but not fatal unless `require_verified` is set (use
    it for a build being handed to an owner).
    """
    try:
        result = recipe.compile_project(project, stock)
    except (recipe.RecipeError, AssertionError) as exc:
        return ValidationResult(False, "FAIL: %s" % exc, [], [{"code": getattr(exc, "code", "ERROR"), "message": str(exc)}])
    if not result.ok:
        report = "FAIL:\n" + "\n".join(
            "  %s: %s" % (e.get("code"), e.get("message")) for e in result.errors)
        return ValidationResult(False, report, [], result.errors)

    body = _format_report(project, result)
    if require_verified and result.unverified:
        return ValidationResult(
            False, body + "\nFAIL: unverified ops present and --require-verified set",
            result.unverified, [])
    return ValidationResult(True, body + "\nPASS", result.unverified, [])
