"""`docs/RECIPE-OPS.md` is generated; this pins it to the generator and the generator to the registry."""
from pathlib import Path

import pytest

import json

from fireflymcf import cli, ops_doc, recipe

ROOT = Path(__file__).resolve().parents[1]


def test_every_registered_op_has_a_note():
    assert set(recipe.REGISTRY) <= set(ops_doc.OP_NOTES)
    assert set(ops_doc.OP_NOTES) <= set(recipe.REGISTRY), "note for an op that no longer exists"
    for name, (klass, purpose, status, proof) in ops_doc.OP_NOTES.items():
        assert klass in {c[0] for c in ops_doc.CLASSES}, name
        assert purpose.endswith(".") and status and proof, name


def test_committed_doc_matches_the_generator():
    committed = (ROOT / "docs" / "RECIPE-OPS.md").read_text()
    assert committed == ops_doc.render(), \
        "docs/RECIPE-OPS.md is stale: run python3 -m fireflymcf.ops_doc"


def test_generator_refuses_an_undocumented_op():
    class Fake(recipe.Op):
        pass
    with pytest.raises(KeyError, match="fake_op"):
        ops_doc.render(dict(recipe.REGISTRY, fake_op=Fake))


def test_error_codes_are_parsed_from_the_docstring():
    codes = dict(ops_doc._error_codes(recipe.REGISTRY["rename_label"]))
    assert {"NO_SUCH_LABEL", "AMBIGUOUS_LABEL", "LABEL_TOO_LONG", "GROW_REFUSED"} <= set(codes)
    assert "offers" in codes["LABEL_TOO_LONG"]


def test_the_doc_does_not_read_the_research_repo():
    # The public reference is generated from the package alone: no coach's recipe feeds it.
    assert not hasattr(ops_doc, "RUNNING_RECIPE")
    assert "builds/" not in ops_doc.render()


def test_ops_lists_every_op_with_its_verified_flag(capsys):
    assert cli.main(["ops"]) == 0
    out = capsys.readouterr().out
    for name in recipe.REGISTRY:
        assert name in out
    line = next(l for l in out.splitlines() if l.startswith("remove_control "))
    assert " NO " in line


def test_ops_name_prints_params_and_error_codes(capsys):
    assert cli.main(["ops", "rename_label"]) == 0
    out = capsys.readouterr().out
    assert "allow_grow" in out and "LABEL_TOO_LONG" in out and "(required)" in out


def test_ops_name_header_says_verified_per_firmware(capsys):
    """The header follows `verified_on` (empty when hardware_verified is False), not the class
    default: an op enabled on 14.7 by the emulator alone is verified on 13.6 only."""
    for name, want in [("rename_label", "verified: 13.6"), ("remove_control", "verified: none")]:
        assert cli.main(["ops", name]) == 0
        head = capsys.readouterr().out.splitlines()[0]
        assert head == f"{name}  ({ops_doc.OP_NOTES[name][0]}; {want})"
    assert "14.7" in recipe.REGISTRY["append_filter"].firmwares
    assert cli.main(["ops", "append_filter"]) == 0
    assert "verified: 13.6)" in capsys.readouterr().out.splitlines()[0]


def test_ops_json_is_the_entries(capsys):
    assert cli.main(["ops", "--json", "remove_control"]) == 0
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["name"] == "remove_control" and entry["verified"] is False
    assert entry["params"] == recipe.REGISTRY["remove_control"].PARAMS


def test_ops_refuses_an_unknown_name(capsys):
    assert cli.main(["ops", "no_such_op"]) == 1
    assert "no such op" in capsys.readouterr().err


def test_entries_carry_firmware_support():
    e = {x["name"]: x for x in ops_doc.entries(recipe.REGISTRY)}
    assert e["set_theme"]["firmwares"] == ["13.6", "14.7"]
    assert e["set_theme"]["verified_on"] == ["13.6"] and e["set_theme"]["emulated_on"] == ["14.7"]
    assert e["set_gesture"]["firmwares"] == ["13.6"] and e["set_gesture"]["emulated_on"] == []
    assert "| Firmware |" in ops_doc.render(recipe.REGISTRY)


def test_an_unverified_op_is_verified_on_no_firmware():
    e = {x["name"]: x for x in ops_doc.entries(recipe.REGISTRY)}
    unverified = [n for n, x in e.items() if not x["verified"]]
    assert unverified
    for n in unverified:
        assert e[n]["verified_on"] == [], n


def _firmware_set_problems(registry):
    """Every op's verification and emulation lie inside the firmwares it may compile on. An op
    whose class default is unverified is verified nowhere (`Op.is_verified`), so its effective
    verified set is empty; its `verified_on` must still name only listed firmwares."""
    out = []
    for name, cls in sorted(registry.items()):
        verified = set(cls.verified_on) if getattr(cls, "hardware_verified", True) else set()
        for label, have in (("verified_on", set(cls.verified_on)), ("verified", verified),
                            ("emulated_on", set(cls.emulated_on))):
            if not have <= set(cls.firmwares):
                out.append(f"{name}: {label} {sorted(have)} not within "
                           f"firmwares {sorted(cls.firmwares)}")
    return out


def test_every_op_verifies_and_emulates_only_where_it_compiles():
    assert _firmware_set_problems(recipe.REGISTRY) == []


def test_the_firmware_set_check_can_fail():
    class Stray(recipe.Op):
        firmwares = frozenset({"13.6"})
        emulated_on = frozenset({"14.7"})

    class StrayVerified(recipe.Op):
        verified_on = frozenset({"13.6", "14.7"})
    assert _firmware_set_problems({"stray": Stray}) == [
        "stray: emulated_on ['14.7'] not within firmwares ['13.6']"]
    assert len(_firmware_set_problems({"sv": StrayVerified})) == 2


# --- the coded refusals of the locators an op builds on are documented. `recipe._CODED` surfaces
# each of its classes (`DonorError`, `GrammarError`, `CellTableError`, `SramReachError`, ...)
# under its code, so an op that can reach one must list the code in its `Error codes:`. Which ops
# reach which codes is derived, not hand-listed: a static call graph over the package
# (module-alias attribute references, same-module names, `from .m import f`, `self.` methods
# through same-module bases), from each op class's methods to every function that constructs a
# `_CODED` class: `CellTableError("CODE", ...)` by its literal first argument, a class with a
# class-level `code` (`GrammarError`, `SpareRowsUnknown`, ...) by that code. It over-approximates (a referenced function counts as called);
# `_UNREACHABLE` holds the over-approximations checked by hand.

_UNREACHABLE = {
    # readout._donor_pair raises NO_DONOR only for a caller-named donor; both reach it through
    # readout.author_readout_object with the default donor (cardreadouts._value_records,
    # pagedraw.readout), which is the frozen template path.
    ("add_card_readouts", "NO_DONOR"),
    ("add_level_page", "NO_DONOR"),
}


def _coded_refusals_by_op() -> dict:
    import ast
    import inspect

    import fireflymcf
    pkg = Path(fireflymcf.__file__).parent
    trees = {p.stem: ast.parse(p.read_text()) for p in sorted(pkg.glob("*.py"))}
    funcs, classes, aliases, names = {}, {}, {}, {}
    for m, t in trees.items():
        for n in t.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[(m, n.name)] = n
            elif isinstance(n, ast.ClassDef):
                classes[(m, n.name)] = n
                for b in n.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        funcs[(m, f"{n.name}.{b.name}")] = b
        al, nm = aliases.setdefault(m, {}), names.setdefault(m, {})
        for n in ast.walk(t):
            if isinstance(n, ast.ImportFrom) and (n.level == 1 or (n.module or "").startswith(
                    "fireflymcf")):
                if n.module in (None, "fireflymcf"):
                    al.update({a.asname or a.name: a.name for a in n.names})
                else:
                    sub = n.module.split(".")[-1]
                    nm.update({a.asname or a.name: (sub, a.name) for a in n.names})

    def methods(m, c):
        return {k for k in funcs if k[0] == m and k[1].startswith(c + ".")}

    def mro(m, c):
        out, todo = [], [c]
        while todo:
            c = todo.pop(0)
            if c not in out and (m, c) in classes:
                out.append(c)
                todo += [b.id for b in classes[(m, c)].bases if isinstance(b, ast.Name)]
        return out

    # Every class `recipe._CODED` surfaces under its code: one with a class-level `code` raises
    # that code wherever it is constructed; any other takes its code as the first argument,
    # which must be a literal, or the code could not be read and the site is reported.
    class_code = {c.__name__: vars(c)["code"] for c in recipe._CODED if "code" in vars(c)}
    first_arg = {c.__name__ for c in recipe._CODED} - set(class_code)
    # A reference inside a `try` whose handler catches the class (by its own name or a base's,
    # `CellTableError` is a `VariableTableNotFound`, a `ValueError`) does not surface the code
    # through that edge: the handler converts it (e.g. `readout._located` to NO_DISPLAY_CELL)
    # or falls back. A handler that re-raises (a bare `raise`) catches nothing, and neither does
    # one that raises the caught error's own code (`except statusbind.BridgeUnknown as err:
    # raise RecipeError(err.code, ...)`, `add_button`/`wire_button`): the code passes through.
    # Handlers match in order, so a pass-through handler ahead of a broader one (`except
    # newbutton.VariantSetterUnknown as err: raise RecipeError(err.code, ...)` then `except
    # ValueError`) exempts its class from the later one: the mask entry is then
    # `(caught, passed)`, which catches a class of `caught` that is none of `passed`.
    is_a = {c.__name__: {k.__name__ for k in c.__mro__} for c in recipe._CODED}

    def masked(klass, mask):
        bases = is_a[klass]
        return any(e in bases if isinstance(e, str) else e[0] in bases and not e[1] & bases
                   for e in mask)

    def passes_code(h):
        return h.name is not None and any(
            isinstance(x, ast.Raise) and x.exc is not None and any(
                isinstance(a, ast.Attribute) and a.attr == "code"
                and isinstance(a.value, ast.Name) and a.value.id == h.name
                for a in ast.walk(x.exc))
            for x in ast.walk(h))

    def caught(handlers):
        out, passed = set(), set()
        for h in handlers:
            ts = (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]) if h.type else []
            names_ = {getattr(t, "attr", getattr(t, "id", None)) for t in ts} if ts \
                else {"BaseException"}
            if any(isinstance(x, ast.Raise) and x.exc is None for x in ast.walk(h)):
                continue
            if passes_code(h):
                passed |= names_
                continue
            out |= {(n, frozenset(passed)) if passed else n for n in names_}
        return frozenset(out)

    graph, raised, unread = {}, {}, []
    for (m, q), node in funcs.items():
        cls = q.split(".")[0] if "." in q else None
        out, codes = set(), set()

        def visit(n, mask, m=m, q=q, cls=cls, out=out, codes=codes):
            if isinstance(n, ast.Try):
                inner = mask | caught(n.handlers)
                for b in n.body:
                    visit(b, inner)
                for b in [*n.handlers, *n.orelse, *n.finalbody]:
                    visit(b, mask)
                return
            if isinstance(n, ast.Name):
                if (m, n.id) in funcs:
                    out.add(((m, n.id), mask))
                if names[m].get(n.id) in funcs:
                    out.add((names[m][n.id], mask))
                if (m, n.id) in classes:
                    out |= {(k, mask) for k in methods(m, n.id)}
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                v = n.value.id
                if v in aliases[m]:
                    tm = aliases[m][v]
                    if (tm, n.attr) in funcs:
                        out.add(((tm, n.attr), mask))
                    if (tm, n.attr) in classes:
                        out |= {(k, mask) for k in methods(tm, n.attr)}
                elif v in ("self", "cls") and cls:
                    hit = [(m, f"{c}.{n.attr}") for c in mro(m, cls)
                           if (m, f"{c}.{n.attr}") in funcs]
                    out |= {(k, mask) for k in hit[:1]}
            if isinstance(n, ast.Call):
                fn = getattr(n.func, "attr", getattr(n.func, "id", None))
                if fn in class_code:
                    codes.add((class_code[fn], fn, mask))
                elif fn in first_arg:
                    if n.args and isinstance(n.args[0], ast.Constant):
                        codes.add((n.args[0].value, fn, mask))
                    else:
                        unread.append(f"{m}.{q}:{n.lineno}")
            for c in ast.iter_child_nodes(n):
                visit(c, mask)

        visit(node, frozenset())
        graph[(m, q)], raised[(m, q)] = out, codes

    result = {}
    for name, op_cls in recipe.REGISTRY.items():
        m = Path(inspect.getsourcefile(op_cls)).stem
        seen, todo = set(), [(k, frozenset()) for k in methods(m, op_cls.__name__)]
        while todo:
            k, mask = todo.pop()
            if (k, mask) not in seen:
                seen.add((k, mask))
                todo += [(c, mask | cm) for c, cm in graph.get(k, ())]
        codes = {code for k, mask in seen for code, klass, site in raised[k]
                 if not masked(klass, mask | site)}
        if codes:
            result[name] = codes
    assert not unread, f"coded refusals whose code is not a literal: {unread}"
    return result


@pytest.mark.slow
def test_coded_refusals_are_documented():
    reach = _coded_refusals_by_op()
    # the derivation finds the template, donor and mode-7 users it must (a broken graph that
    # found nothing would pass the loop below vacuously)
    for name, codes in (("add_text_label", {"GRAMMAR", "NO_TEMPLATE", "MODE7_INDEX"}),
                        ("add_rvc_button", {"GRAMMAR", "NO_TEMPLATE", "MODE7_INDEX"}),
                        ("set_record_color", {"MODE7_INDEX"}),
                        ("add_glyph_button", {"NO_DONOR"}),
                        ("add_register_readout", {"NO_DONOR", "CELL_TABLE_NO_BOUND",
                                                  "CELL_TABLE_BOUND_FORM", "RENDER_STATE_BASES",
                                                  "DECODER_RANGE_UNKNOWN"}),
                        ("add_rvc_button", {"VARIANT_SETTER_UNKNOWN", "LCD_GRAMMAR"}),
                        ("follow_register", {"SPARE_ROWS_UNKNOWN"}),
                        ("add_button", {"BRIDGE_UNKNOWN", "VARIANT_SETTER_UNKNOWN", "GRAMMAR",
                                        "NO_TEMPLATE", "MODE7_INDEX", "LCD_GRAMMAR",
                                        "PLACEMENT_OVERLAP"}),
                        ("wire_button", {"BRIDGE_UNKNOWN", "VARIANT_SETTER_UNKNOWN"})):
        assert codes <= reach.get(name, set()), (name, reach.get(name))
    missing = {name: sorted(c for c in codes - {c for c, _ in
                                                  ops_doc._error_codes(recipe.REGISTRY[name])}
                            if (name, c) not in _UNREACHABLE)
               for name, codes in reach.items()}
    assert not {k: v for k, v in missing.items() if v}, missing
    for name, code in _UNREACHABLE:
        assert code in reach.get(name, set()), f"stale _UNREACHABLE entry {(name, code)}"


def test_a_line_may_name_several_codes_that_share_one_meaning():
    class Fake(recipe.Op):
        """Fake.

        Error codes:
          ONE — its own
          TWO, THREE — shared
            meaning
          FOUR — last
        """
    assert ops_doc._error_codes(Fake) == [("ONE", "its own"), ("TWO", "shared meaning"),
                                          ("THREE", "shared meaning"), ("FOUR", "last")]
    assert ops_doc._grouped(ops_doc._error_codes(Fake)) == [
        (["ONE"], "its own"), (["TWO", "THREE"], "shared meaning"), (["FOUR"], "last")]


def _gate_codes_in_recipe() -> set:
    """Every code a whole-image gate in `recipe.py` refuses with: the literal second argument of
    each `_gate(...)` and first argument of each `GateError(...)`. A non-literal code is the
    `_CODED` class's own (`e.code`); `test_every_gate_code_is_documented` names those."""
    import ast
    tree = ast.parse(Path(recipe.__file__).read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            arg = {"_gate": 1, "GateError": 0}.get(n.func.id)
            if arg is not None and len(n.args) > arg and isinstance(n.args[arg], ast.Constant):
                out.add(n.args[arg].value)
    return out


def test_every_gate_code_is_documented():
    from fireflymcf import newbutton
    found = _gate_codes_in_recipe()
    assert {"STRUCTURAL_GATE", "CAPABILITY_GATE", "NOT_SEALED", "HOOK_REFUSED"} <= found
    # the reuse gate refuses with the locator's own code where the driven groups are unknown
    found.add(newbutton.VariantSetterUnknown.code)
    assert found <= set(ops_doc.GATE_CODES), found - set(ops_doc.GATE_CODES)
    doc = ops_doc.render()
    for code in ops_doc.GATE_CODES:
        assert "`%s`" % code in doc, code
