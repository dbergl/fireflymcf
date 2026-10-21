"""The recipe engine: a PROJECT is an ordered list of typed ops applied to stock 13.6.

Every op wraps an
existing hardware-proven primitive; this module holds no byte-format knowledge of its
own. `compile_project` is pure and deterministic: same project, byte-identical binary.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import struct

from fireflymcf import coverage, guidata, patch, relocate, theme

SCHEMA = 1


class RecipeError(ValueError):
    """A structured, GUI-renderable failure. `offers` name resolutions the caller may apply."""

    def __init__(self, code: str, message: str, *, op_id: str | None = None,
                 offers: tuple | list = ()):
        super().__init__(message)
        self.code = code
        self.op_id = op_id
        self.offers = list(offers)

    def to_json(self) -> dict:
        return {"op_id": self.op_id, "code": self.code,
                "message": str(self), "offers": self.offers}


@dataclasses.dataclass
class OpSpec:
    id: str
    type: str
    params: dict
    enabled: bool = True
    note: str = ""


@dataclasses.dataclass
class Project:
    base_sha256: str
    build_name: str | None
    ops: list
    schema: int = SCHEMA
    # Studio's coach-config preview: which floorplan and options to RENDER under.
    # Deliberately NOT build input -- floorplan/option state lives in G12 NVM and
    # arrives over the bus (`MCF-FORMAT.md` §10), so it cannot be a property of the
    # file. `_prefix_key` does not read it, so a preview change can never invalidate
    # a compile or alter an output byte; `tests/test_recipe.py` pins that.
    preview: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> "Project":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            raise RecipeError("BAD_PROJECT", "not valid JSON: %s" % e)
        if not isinstance(doc, dict):
            raise RecipeError("BAD_PROJECT", "project document must be a JSON object")
        if doc.get("schema") != SCHEMA:
            raise RecipeError("BAD_SCHEMA", "schema %r is not the supported %d"
                              % (doc.get("schema"), SCHEMA))
        for field in ("base_sha256", "build_name", "ops"):
            if field not in doc:
                raise RecipeError("BAD_PROJECT", "missing field %r" % field)
        if not isinstance(doc["ops"], list):
            raise RecipeError("BAD_PROJECT", "field 'ops' must be a list, got %r"
                              % (doc["ops"],))
        ops = []
        for i, o in enumerate(doc["ops"]):
            if not isinstance(o, dict):
                raise RecipeError("BAD_PROJECT", "op %d must be an object, got %r"
                                  % (i, o))
            for field in ("id", "type", "params"):
                if field not in o:
                    raise RecipeError("BAD_PROJECT", "op %d missing %r" % (i, field))
            ops.append(OpSpec(id=o["id"], type=o["type"], params=o["params"],
                              enabled=o.get("enabled", True), note=o.get("note", "")))
        ids = [o.id for o in ops]
        if len(ids) != len(set(ids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise RecipeError("DUPLICATE_OP_ID", "duplicate op id(s): %s" % dup)
        preview = doc.get("preview") or {}
        if not isinstance(preview, dict):
            raise RecipeError("BAD_PROJECT", "field 'preview' must be an object, got %r"
                              % (preview,))
        return cls(base_sha256=doc["base_sha256"], build_name=doc["build_name"], ops=ops,
                   preview=preview)

    def to_json(self) -> str:
        return json.dumps({
            "schema": self.schema,
            "base_sha256": self.base_sha256,
            "build_name": self.build_name,
            "ops": [{"id": o.id, "type": o.type, "params": o.params,
                     "enabled": o.enabled, "note": o.note} for o in self.ops],
            "preview": self.preview,
        }, indent=2)


@dataclasses.dataclass
class Ctx:
    stock: bytes
    edits: list
    insertions: list
    handles: dict
    # +0x44BC8 display-cell indices an op minted fresh (via readout.allocate_fresh_cells). The
    # cell-aliasing gate asserts each is a freshly-GROWN index, never a reused stock index.
    authored_cells: set = dataclasses.field(default_factory=set)


REGISTRY: dict[str, type] = {}


def op(name: str):
    """Class decorator: register an Op subclass under its project-file type name."""
    def register(cls):
        cls.type = name
        REGISTRY[name] = cls
        return cls
    return register


class Op:
    type: str = "?"
    hardware_verified = True     # False marks the feature for the export manifest
    PARAMS = {"properties": {}, "required": []}  # subclasses override; this default
    # keeps a future op that forgets to set PARAMS from 500ing /api/schemas.

    def __init__(self, spec: OpSpec):
        self.spec = spec
        # A param the op does not declare would be ignored, so a typo ("allow_grwo") would
        # build something other than what the project says. Refuse it instead.
        declared = set(self.PARAMS.get("properties", {}))
        unknown = sorted(set(spec.params) - declared) if isinstance(spec.params, dict) else []
        if unknown:
            raise RecipeError("BAD_PARAMS", "%s: unknown param(s) %s; it takes %s"
                              % (self.type, ", ".join(map(repr, unknown)),
                                 ", ".join(sorted(declared)) or "none"), op_id=spec.id)
        self.validate()

    def _param(self, name, default=..., kind=None):
        p = self.spec.params
        if name not in p:
            if default is not ...:
                return default
            raise RecipeError("BAD_PARAMS", "%s: missing param %r" % (self.type, name),
                              op_id=self.spec.id)
        v = p[name]
        if kind is not None and not isinstance(v, kind):
            raise RecipeError("BAD_PARAMS", "%s: param %r wants %s, got %r"
                              % (self.type, name, kind, v), op_id=self.spec.id)
        return v

    def validate(self) -> None:
        pass

    def apply(self, data: bytes, ctx: Ctx) -> bytes:
        raise NotImplementedError

    def verify(self, data: bytes, ctx: Ctx) -> str:
        raise NotImplementedError

    def capability_delta(self, ctx: Ctx):
        return set(), set()


def resolve_target(ctx: Ctx, target) -> int:
    if isinstance(target, int):
        return target
    if isinstance(target, dict) and set(target) == {"ref"}:
        h = ctx.handles.get(target["ref"])
        if h is None:
            raise RecipeError("BAD_REF", "no earlier op with id %r produced an object"
                              % target["ref"])
        return h["object"]
    raise RecipeError("BAD_PARAMS", "target must be an object index or {'ref': op_id}, "
                      "got %r" % (target,))


@dataclasses.dataclass
class CompileResult:
    ok: bool
    data: bytes | None
    errors: list
    manifest: list
    unverified: list


def capabilities(img: bytes) -> set:
    """Every (channel, wire command) pair the image's group/binding tables can send.

    Lifted from the original build script's capability gate (added 2026-08-19 after a
    repurposed group silently ate the awning: v11 lost (3,1), v18 lost that plus
    channel 86 — both flashed before anyone noticed)."""
    gt, bt = guidata.group_table(img), guidata.binding_table(img)
    return {(bt[j].channel, bt[j].raw[4]) for g in gt
            for j in range(g.start, g.start + g.count)
            if 0 <= j < len(bt) and bt[j].channel is not None}


def _run_structural_gates(data: bytes, ctx: Ctx) -> None:
    ok, reason = relocate._boundary_oracle(data)
    assert ok, reason
    ok, reason = relocate._verify_record_geometry(data)
    assert ok, reason
    assert coverage.analyse(data, deep=True).unclaimed == []
    for lit_off, tgt in theme.guicode_guidata_pointers(data):
        assert struct.unpack_from("<I", data, lit_off)[0] == 0x90000000 + tgt, \
            "stale GuiCode->GuiData pointer at %#x" % lit_off
    if ctx.insertions:
        # Check the LOWEST insertion — the most conservative `at`, since the gate
        # refuses if the separately-flashed LCD Firmware points anywhere ABOVE it.
        lcd_ok, lcd_why = relocate._verify_no_stranded_lcd_pointers(data, min(ctx.insertions))
        assert lcd_ok, lcd_why

    _assert_authored_cells_fresh(ctx)
    _assert_reserved_groups_intact(ctx.stock, data)
    _assert_no_guicode_selected_group_reused(ctx.stock, data)


# Command groups no op may claim, though `newbutton.free_group_slots` lists them: on 13.6, 68 and
# 69 hold the stock `1FFD3` / `1FFC5` inverter/charger enable/disable templates that the
# PD-inverter bridge brief (`docs/HARDWARE-VERIFIED.md`) names as the route it will drive. The gate
# keys on those DGNs in the stock image, so another build (whose 68/69 are something else) is not
# gated. v44's first build overwrote 68 with the APS-500 reboot group; this is what catches it.
RESERVED_GROUPS = {68: 0xFFD3, 69: 0xFFC5}


def _assert_reserved_groups_intact(stock: bytes, data: bytes) -> None:
    sg, dg = guidata.group_table(stock), guidata.group_table(data)
    sb, db = guidata.binding_table(stock), guidata.binding_table(data)
    for g, dgn_low in RESERVED_GROUPS.items():
        if g >= len(sg) or not sg[g].count:
            continue
        slice_ = range(sg[g].start, sg[g].start + sg[g].count)
        if not all(sb[i].dgn_low == dgn_low for i in slice_):
            continue
        assert dg[g].raw == sg[g].raw and all(db[i].raw == sb[i].raw for i in slice_), \
            "reserved group %d (the stock %#x inverter/charger template) was changed" % (
                g, 0x10000 | dgn_low)


def _assert_no_guicode_selected_group_reused(stock: bytes, data: bytes) -> None:
    """A group an op REUSES (its binding slice moved, so it now sends new bindings) must not be one
    GuiCode drives by number (`newbutton.guicode_selected_groups`). v45 reused 136 and 137 from
    13.6's 130-139 block: a tap on 136 worked, but its hold never repeated, and 137's hold-only
    reboot never fired, on the coach 2026-09-30. Edits that keep a group's slice (a gesture or a
    binding byte on an existing control) are not reuse and are not gated."""
    from fireflymcf import newbutton
    selected = newbutton.guicode_selected_groups(stock)
    sg, dg = guidata.group_table(stock), guidata.group_table(data)
    bad = [g for g in selected if g < len(sg) and g < len(dg) and dg[g].start != sg[g].start]
    assert not bad, ("group(s) %s were reused, but GuiCode drives them by number "
                     "(newbutton.guicode_selected_groups); a hold on them does not work" % bad)


def _assert_authored_cells_fresh(ctx: Ctx) -> None:
    """Cross-table cell-aliasing gate. Every `+0x44BC8` display cell an op minted (recorded in
    `ctx.authored_cells`) must be a freshly-GROWN index -- one at or above the stock cell-table
    row count -- never a reused stock index. On 13.6 every reusable stock index is actually read
    by an object in another GuiData object-pointer table (`status_map.foreign_table_refs` names
    them for 254/256/258), which the historical table-1-only spare scan could not see and the
    renderer's page walk cannot show; that is how the coach-48V build repointed 256 and painted
    background bars. Enforcing "authored cells are freshly grown, never reused" is the invariant
    that makes that class impossible, and it is what would have failed the original coach-48V
    build. Reads the stock row count structurally (`readout.resolve_cell_tables`); a build whose
    cell table cannot be located or uses an unsupported bound encoding is left ungated -- the ops
    that grow support only the 13.6 `cmp.w #imm` bound form."""
    if not ctx.authored_cells:
        return
    from fireflymcf import readout
    try:
        _cell_base, _type_base, stock_len = readout.resolve_cell_tables(ctx.stock)
    except Exception:
        return
    reused = sorted(i for i in ctx.authored_cells if i < stock_len)
    assert not reused, (
        "op authored display cell index(es) %r below the stock cell-table row count (%d) -- a "
        "private cell must be a freshly grown index, never a reused +0x44BC8 index (on 13.6 every "
        "reusable index is read by an object in another GuiData object table: the coach-48V "
        "background-bar aliasing). Use readout.allocate_fresh_cells." % (reused, stock_len))


def _build_ops(specs: list) -> tuple:
    """Construct+validate an Op instance for each spec. Returns (ops, errors); an
    unknown type or a validation failure appends a RecipeError to `errors` instead of
    raising, so the caller can collect every problem in one pass."""
    ops, errors = [], []
    for spec in specs:
        cls = REGISTRY.get(spec.type)
        if cls is None:
            errors.append(RecipeError("UNKNOWN_OP", "no op type %r" % spec.type,
                                      op_id=spec.id))
            continue
        try:
            ops.append(cls(spec))
        except RecipeError as e:
            e.op_id = e.op_id or spec.id
            errors.append(e)
    return ops, errors


def _check_toggles_driven(specs: list) -> list:
    """`TOGGLE_UNDRIVEN` for every enabled `add_rvc_button` with `toggle` that no enabled
    `add_button_status` LATER in `specs` lights (an entry whose `button.ref` is its id). A toggle
    sends by its variant, which only that op's routine sets (`setVariant(group, lit)`); undriven,
    the variant sits at 0 and the button only ever sends "on". A disabled spec drives nothing,
    and a status op before the button cannot name it. Static: no op is constructed or applied,
    so a malformed status op simply drives nothing (its own validation reports it)."""
    errors = []
    for i, spec in enumerate(specs):
        if not (spec.enabled and spec.type == "add_rvc_button"
                and isinstance(spec.params, dict) and spec.params.get("toggle") is not None):
            continue
        driven = False
        for later in specs[i + 1:]:
            if not (later.enabled and later.type == "add_button_status"
                    and isinstance(later.params, dict)):
                continue
            entries = later.params.get("buttons")
            if not isinstance(entries, list):
                continue
            if any(isinstance(e, dict) and isinstance(e.get("button"), dict)
                   and e["button"].get("ref") == spec.id for e in entries):
                driven = True
                break
        if not driven:
            errors.append(RecipeError(
                "TOGGLE_UNDRIVEN", "toggle button %r is driven by no later add_button_status: "
                "its variant would stay 0 and it would only ever send its on_payload" % spec.id,
                op_id=spec.id))
    return errors


def _finish(project: Project, stock: bytes, data: bytes, ctx: Ctx,
           ops: list) -> CompileResult:
    """The one finishing path shared by compile_project and compile_cached: reseal,
    stamp the build name, run the structural + capability gates, then verify every op.

    `ops` are the already-constructed, validated Op instances for every enabled spec,
    in order — regardless of whether THIS call's apply() loop ran all of them or
    resumed from a cached prefix. That is safe because every op's verify() and
    capability_delta() reads its state from ctx.handles (populated during apply, and
    snapshotted into the cache alongside the data), never from instance attributes —
    see the per-op handle refactor. (The one instance attribute verify() writes is
    `hardware_verified` where it depends on a resolved ref, `add_card_readouts`'s parent;
    it is read only after verify, so a resumed compile still sets it.) So a cache-resumed compile verifies correctly
    without re-running the skipped ops' apply."""
    data, _ = patch.reseal(data)
    if project.build_name is not None:
        # `set_build_name` refuses a non-ASCII name or one over the header field's 127
        # usable bytes with PatchError, which is not a RecipeError and so would escape
        # compile unstructured. The build name is a project field a caller can set
        # freely (the editor exposes it), so a bad one has to fail like every other
        # project-level problem: a structured refusal, not a crash.
        try:
            data, _ = patch.set_build_name(data, project.build_name)
        except patch.PatchError as exc:
            raise RecipeError("BAD_BUILD_NAME", str(exc))

    _run_structural_gates(data, ctx)

    # Capability gate: the finished image must still send every (channel, command)
    # pair stock could, minus/plus exactly what the ops declare.
    lost_want, gained_want = set(), set()
    for o in ops:
        lo, ga = o.capability_delta(ctx)
        lost_want |= lo
        gained_want |= ga
    stock_caps, out_caps = capabilities(stock), capabilities(data)
    assert stock_caps - out_caps == lost_want, \
        "capability gate: lost %s, ops declared %s" % (
            sorted(stock_caps - out_caps), sorted(lost_want))
    assert out_caps - stock_caps == gained_want, \
        "capability gate: gained %s, ops declared %s" % (
            sorted(out_caps - stock_caps), sorted(gained_want))

    manifest = [o.verify(data, ctx) for o in ops]
    res, ch = patch.reseal(data)
    assert res == data, "image not sealed after verify: %s" % ch
    unverified = [o.spec.id for o in ops if not o.hardware_verified]
    return CompileResult(True, data, [], manifest, unverified)


def compile_project(project: Project, stock: bytes) -> CompileResult:
    if hashlib.sha256(stock).hexdigest() != project.base_sha256:
        raise RecipeError("BASE_MISMATCH",
                          "the supplied stock image does not match the project's base "
                          "(sha256 %s...)" % project.base_sha256[:12])
    enabled = [s for s in project.ops if s.enabled]

    ops, errors = _build_ops(enabled)
    errors += _check_toggles_driven(enabled)
    if errors:
        return CompileResult(False, None, [e.to_json() for e in errors], [], [])

    ctx = Ctx(stock=stock, edits=[], insertions=[], handles={})
    data = stock
    for o in ops:
        try:
            data = o.apply(data, ctx)
        except RecipeError as e:
            e.op_id = e.op_id or o.spec.id
            return CompileResult(False, None, [e.to_json()], [], [])

    return _finish(project, stock, data, ctx, ops)


def _prefix_key(base_hash: str, specs: list) -> str:
    """A cache key for the state after applying exactly `specs`, in order, to
    `base_hash`'s stock. Keyed on (id, type, params) only — `enabled`/`note` never
    reach here since callers always filter to the enabled list first, and `note` has
    no effect on the compiled bytes."""
    doc = [(s.id, s.type, s.params) for s in specs]
    return hashlib.sha256((base_hash + json.dumps(doc, sort_keys=True))
                          .encode()).hexdigest()


def _snapshot(ctx: Ctx) -> tuple:
    """A deep, independent copy of the parts of Ctx that accumulate op-produced
    state. Stored in the cache alongside the data so a later resume never aliases a
    dict/list a subsequent compile might mutate in place (e.g. WireButton updates an
    earlier op's handle dict). `authored_cells` is carried too: the cell-aliasing gate
    (`_assert_authored_cells_fresh`) reads it, so a resume that dropped it would skip the
    gate for every cell the cached prefix minted."""
    return (list(ctx.edits), list(ctx.insertions), copy.deepcopy(ctx.handles),
            set(ctx.authored_cells))


def compile_cached(project: Project, stock: bytes, cache: dict) -> CompileResult:
    """Same result as compile_project, resuming from the longest cached enabled-op
    prefix. `cache` is a plain dict the caller owns (the server holds one per
    project); entries are keyed by `_prefix_key` and store `(data, ctx_snapshot)` —
    the `ctx_snapshot` tuple `_snapshot` returns. Editing op N of M re-applies only
    ops N..M: every op before the edit shares its prefix key with the previous
    compile, so its cached (data, snapshot) is reused verbatim and only the tail is
    re-applied. Guaranteed byte-identical to compile_project (tests/test_recipe.py
    `test_cache_hit_is_byte_identical_and_resumes`) because op verify-state lives in
    ctx.handles, not on the op instance (save a `hardware_verified` that verify() settles
    from a resolved ref, see `_finish`) — a resumed compile's verify()/
    capability_delta() read the snapshotted handles exactly as a from-scratch compile
    would have populated them."""
    if hashlib.sha256(stock).hexdigest() != project.base_sha256:
        raise RecipeError("BASE_MISMATCH",
                          "the supplied stock image does not match the project's base "
                          "(sha256 %s...)" % project.base_sha256[:12])
    enabled = [s for s in project.ops if s.enabled]

    start = 0
    data, snap = stock, ([], [], {}, set())
    for i in range(len(enabled), 0, -1):
        key = _prefix_key(project.base_sha256, enabled[:i])
        if key in cache:
            data, snap = cache[key]
            start = i
            break
    ctx = Ctx(stock=stock, edits=list(snap[0]), insertions=list(snap[1]),
             handles=copy.deepcopy(snap[2]), authored_cells=set(snap[3]))

    ops, errors = _build_ops(enabled)
    errors += _check_toggles_driven(enabled)
    if errors:
        return CompileResult(False, None, [e.to_json() for e in errors], [], [])

    for i in range(start, len(enabled)):
        try:
            data = ops[i].apply(data, ctx)
        except RecipeError as e:
            e.op_id = e.op_id or ops[i].spec.id
            return CompileResult(False, None, [e.to_json()], [], [])
        cache[_prefix_key(project.base_sha256, enabled[:i + 1])] = (
            data, _snapshot(ctx))

    return _finish(project, stock, data, ctx, ops)


from fireflymcf import recipe_ops  # noqa: E402,F401  (populates REGISTRY)
