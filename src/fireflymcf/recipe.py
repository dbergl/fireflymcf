"""The recipe engine: a PROJECT is an ordered list of typed ops applied to a stock image.

Each op lists the firmware it may compile on (`Op.firmwares`); compile refuses it elsewhere,
and refuses an image `firmware.identify` does not know (UNSUPPORTED_FIRMWARE).

Every op wraps an
existing hardware-proven primitive; this module holds no byte-format knowledge of its
own. `compile_project` is pure and deterministic: same project, byte-identical binary.
"""
from __future__ import annotations

import contextvars
import copy
import dataclasses
import hashlib
import json
import struct

from fireflymcf import (celltables, coverage, decodeguards, donors, firmware, followchannel,
                        grammar, guidata, newbutton, patch, relocate, sramreach, statusbind,
                        stockscope, theme)

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


class GateError(RecipeError, AssertionError):
    """A whole-image gate refused the build (`STRUCTURAL_GATE`, `CAPABILITY_GATE`, `NOT_SEALED`,
    `HOOK_REFUSED`, `VARIANT_SETTER_UNKNOWN`; `ops_doc.GATE_CODES` documents each).
    Raised explicitly, never by `assert`, so `python -O` cannot remove a gate. It is also an
    AssertionError, the type these gates raised before they were explicit."""


def _gate(ok, code: str, message) -> None:
    if not ok:
        raise GateError(code, str(message))


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
    # User-op folders outside `<project dir>/ops/` (paths relative to the project file), and the
    # owner's acceptance records for user ops (`userop`; `fireflymcf op accept` fills it), and where
    # each installed user op came from (`fireflymcf op install`: {type: {source, folder_sha256}}).
    # Each is written only when non-empty, so a project without user ops serialises as before.
    ext_ops: list = dataclasses.field(default_factory=list)
    user_op_acceptance: dict = dataclasses.field(default_factory=dict)
    user_op_sources: dict = dataclasses.field(default_factory=dict)
    # How many authored per-tick routines this project may chain on the GuiCode hook; None is
    # `followchannel.DEFAULT_TICK_CHAIN_LIMIT`. Compile refuses an op that goes past it
    # (TICK_CHAIN_FULL). Written only when set, so a project that never raised it serialises as
    # before.
    tick_chain_limit: int | None = None

    @property
    def effective_tick_chain_limit(self) -> int:
        return (followchannel.DEFAULT_TICK_CHAIN_LIMIT if self.tick_chain_limit is None
                else self.tick_chain_limit)

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
        ext_ops = doc.get("ext_ops") or []
        if not (isinstance(ext_ops, list) and all(isinstance(x, str) for x in ext_ops)):
            raise RecipeError("BAD_PROJECT", "field 'ext_ops' must be a list of paths, got %r"
                              % (ext_ops,))
        acceptance = doc.get("user_op_acceptance") or {}
        if not isinstance(acceptance, dict):
            raise RecipeError("BAD_PROJECT", "field 'user_op_acceptance' must be an object, "
                              "got %r" % (acceptance,))
        sources = doc.get("user_op_sources") or {}
        if not isinstance(sources, dict):
            raise RecipeError("BAD_PROJECT", "field 'user_op_sources' must be an object, "
                              "got %r" % (sources,))
        limit = doc.get("tick_chain_limit")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                                  or limit < 1):
            raise RecipeError("BAD_PROJECT", "field 'tick_chain_limit' must be a positive "
                              "integer, got %r" % (limit,))
        return cls(base_sha256=doc["base_sha256"], build_name=doc["build_name"], ops=ops,
                   preview=preview, ext_ops=ext_ops, user_op_acceptance=acceptance,
                   user_op_sources=sources, tick_chain_limit=limit)

    def to_json(self) -> str:
        doc = {
            "schema": self.schema,
            "base_sha256": self.base_sha256,
            "build_name": self.build_name,
            "ops": [{"id": o.id, "type": o.type, "params": o.params,
                     "enabled": o.enabled, "note": o.note} for o in self.ops],
            "preview": self.preview,
        }
        if self.ext_ops:
            doc["ext_ops"] = self.ext_ops
        if self.user_op_acceptance:
            doc["user_op_acceptance"] = self.user_op_acceptance
        if self.user_op_sources:
            doc["user_op_sources"] = self.user_op_sources
        if self.tick_chain_limit is not None:
            doc["tick_chain_limit"] = self.tick_chain_limit
        return json.dumps(doc, indent=2)


@dataclasses.dataclass
class Ctx:
    stock: bytes
    edits: list
    insertions: list
    handles: dict
    # +0x44BC8 display-cell indices an op minted fresh (via readout.allocate_fresh_cells). The
    # cell-aliasing gate asserts each is a freshly-GROWN index, never a reused stock index.
    authored_cells: set = dataclasses.field(default_factory=set)
    # The stock image's `firmware.KNOWN` key; compile refuses an image it cannot identify, so in a
    # compile this is never None
    firmware: str | None = None


REGISTRY: dict[str, type] = {}

# User ops (`userop`) never enter REGISTRY. `userop.registered(...)` sets this overlay for the
# duration of a compile, so a loaded op cannot outlive the block or leak into another thread's (or
# test's) compile; `_lookup` consults REGISTRY first, and the loader refuses a built-in's name.
# Values are `userop.LoadedOp`.
_USER_OPS: contextvars.ContextVar = contextvars.ContextVar("fireflymcf_user_ops", default=None)
# While the loader imports an `op.py`, `@op` registers into this dict instead of REGISTRY, so a
# user op naming a built-in can never replace it, not even between import and refusal.
_CAPTURE: contextvars.ContextVar = contextvars.ContextVar("fireflymcf_op_capture", default=None)


def op(name: str):
    """Class decorator: register an Op subclass under its project-file type name."""
    def register(cls):
        cls.type = name
        target = _CAPTURE.get()
        (REGISTRY if target is None else target)[name] = cls
        return cls
    return register


def _user_ops() -> dict:
    return _USER_OPS.get() or {}


def _lookup(type_name: str):
    cls = REGISTRY.get(type_name)
    if cls is None:
        loaded = _user_ops().get(type_name)
        cls = loaded.cls if loaded is not None else None
    return cls


class Op:
    type: str = "?"
    # The firmware keys (`firmware.KNOWN`) this op may compile on. Compile refuses it on any other
    # (UNSUPPORTED_FIRMWARE) before any op code runs. A firmware joins the list only with a test
    # that compiles the op there.
    firmwares: frozenset = frozenset({"13.6"})
    # Where a coach has run it: `hardware_verified` (and its instance overrides) means "verified on
    # the firmware in `verified_on`". `emulated_on` is information only; it never verifies.
    verified_on: frozenset = frozenset({"13.6"})
    emulated_on: frozenset = frozenset()
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

    def is_verified(self, fw: str | None) -> bool:
        """True when a coach on firmware `fw` has run this op's mechanism."""
        return bool(self.hardware_verified) and fw in self.verified_on


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
    # {op_id: [entry, ...]}: what each `remove_records`/`move_element` moved besides its own
    # records (`recipe_ops.shift_report`); report only, additive
    shifts: dict = dataclasses.field(default_factory=dict)
    # {op_id: {"type", "lost", "gained", "frames"}}: each user op that changes what the panel
    # sends, by the capability_delta it declares, by the (channel, command) pairs its own apply
    # changed, or by the frames its tick routines send (`userop.routine_frames`). The CLI asks the
    # owner's G12 acceptance for these (`G12_ACK_REQUIRED`); additive
    sends: dict = dataclasses.field(default_factory=dict)
    # The `firmware.KNOWN` key of the stock image; `unverified` is judged for this firmware
    firmware: str | None = None
    # {op_id: [{"arm": {switch: value}, "objects": [...]}, ...]}: the switch arms other than the
    # base under which a new button's footprint covers another record (`recipe_ops.
    # placement_report`, `newbutton.check_placement`); which arm a coach draws is its
    # configuration, so this is report only; additive
    placement: dict = dataclasses.field(default_factory=dict)


# ctx.handles key under which a user op's apply records the capabilities it changed
USER_OP_CAPS = "__user_op_caps__"


def capabilities(img: bytes) -> set:
    """Every (channel, wire command) pair the image's group/binding tables can send.

    Lifted from the original build script's capability gate (added 2026-08-19 after a
    repurposed group silently ate the awning: one build lost (3,1), a later one lost that plus
    channel 86 — both flashed before anyone noticed)."""
    gt, bt = guidata.group_table(img), guidata.binding_table(img)
    return {(bt[j].channel, bt[j].raw[4]) for g in gt
            for j in range(g.start, g.start + g.count)
            if 0 <= j < len(bt) and bt[j].channel is not None}


def _run_structural_gates(data: bytes, ctx: Ctx) -> None:
    from fireflymcf import limits
    gs, ge = guidata.guidata_extent(data)
    limits.check_guidata_size(ge - gs)
    ok, reason = relocate._boundary_oracle(data)
    _gate(ok, "STRUCTURAL_GATE", reason)
    ok, reason = relocate._verify_record_geometry(data)
    _gate(ok, "STRUCTURAL_GATE", reason)
    unclaimed = coverage.analyse(data, deep=True).unclaimed
    _gate(unclaimed == [], "STRUCTURAL_GATE",
          "deep coverage found %d unclaimed byte run(s): %r" % (len(unclaimed), unclaimed[:4]))
    for lit_off, tgt in theme.guicode_guidata_pointers(data):
        _gate(struct.unpack_from("<I", data, lit_off)[0] == 0x90000000 + tgt, "STRUCTURAL_GATE",
              "stale GuiCode->GuiData pointer at %#x" % lit_off)
    if ctx.insertions:
        # Check the LOWEST insertion — the most conservative `at`, since the gate
        # refuses if the separately-flashed LCD Firmware points anywhere ABOVE it.
        lcd_ok, lcd_why = relocate._verify_no_stranded_lcd_pointers(data, min(ctx.insertions))
        _gate(lcd_ok, "STRUCTURAL_GATE", lcd_why)

    _assert_authored_cells_fresh(ctx)
    _assert_reserved_groups_intact(ctx.stock, data)
    _assert_no_guicode_selected_group_reused(ctx.stock, data)


def _assert_reserved_groups_intact(stock: bytes, data: bytes) -> None:
    """No op may change a stock inverter/charger template (`newbutton.RESERVED_DGNS`, the
    `1FFD3` / `1FFC5` enable/disable groups the PD-inverter bridge brief in
    `docs/HARDWARE-VERIFIED.md` names as the route it will drive), though
    `newbutton.free_group_slots` lists them. The groups are located in the STOCK image
    (`newbutton.template_groups`: every group whose whole slice is that DGN; 68 and 69 on 11.23,
    13.6 and 14.7, 2026-11-08), so a stock with no such group has nothing gated, and one with
    several has each gated. The reboot button's first build overwrote 68 with the APS-500 reboot
    group; this is what catches it."""
    from fireflymcf import newbutton
    sg, dg = guidata.group_table(stock), guidata.group_table(data)
    sb, db = guidata.binding_table(stock), guidata.binding_table(data)
    for dgn, groups in newbutton.template_groups(stock).items():
        for g in groups:
            slice_ = range(sg[g].start, sg[g].start + sg[g].count)
            _gate(g < len(dg) and dg[g].raw == sg[g].raw
                  and all(i < len(db) and db[i].raw == sb[i].raw for i in slice_),
                  "STRUCTURAL_GATE",
                  "reserved group %d (the stock %#x inverter/charger template) was changed" % (
                      g, dgn))


def _assert_no_guicode_selected_group_reused(stock: bytes, data: bytes) -> None:
    """A group an op REUSES (its binding slice moved, so it now sends new bindings) must not be one
    GuiCode drives by number (`newbutton.guicode_selected_groups`). A build of 2026-09-30 reused 136
    and 137 from
    13.6's 130-139 block: a tap on 136 worked, but its hold never repeated, and 137's hold-only
    reboot never fired, on the coach 2026-09-30. Driven is sufficient to refuse, not proof a
    hold is dead: a hold on driven group 129 (Low Volts) works on the coach (2026-10-19), and
    what separates it from 136/137 is not established (`newbutton.guicode_selected_groups`).
    Edits that keep a group's slice (a gesture or a binding byte on an existing control) are not
    reuse and are not gated.

    Where the GuiCode-driven groups are not known (`newbutton.VariantSetterUnknown`: the setter
    cell or the LCD's writer does not prove out) a build that reuses no group has nothing to
    check and passes; one that reuses any group is refused VARIANT_SETTER_UNKNOWN, since it
    cannot be shown to miss them. 11.23, 13.6 and 14.7 all locate (2026-11-08)."""
    from fireflymcf import newbutton
    sg, dg = guidata.group_table(stock), guidata.group_table(data)
    try:
        selected = newbutton.guicode_selected_groups(stock)
    except newbutton.VariantSetterUnknown as e:
        moved = [g.index for g in sg if g.index < len(dg) and dg[g.index].start != g.start]
        _gate(not moved, e.code,
              "group(s) %s were reused, and which groups GuiCode drives by number is not known "
              "on this image: %s" % (moved, e))
        return
    bad = [g for g in selected if g < len(sg) and g < len(dg) and dg[g].start != sg[g].start]
    _gate(not bad, "STRUCTURAL_GATE",
          "group(s) %s were reused, but GuiCode drives them by number "
          "(newbutton.guicode_selected_groups); holds on driven 136/137 died on a coach "
          "(2026-09-30), so a reused group must not be one" % bad)


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
    cell table cannot be located is left ungated. The grow itself rebumps only a `cmp.w #imm`
    bound (13.6, 14.7) and refuses 11.23's `movw+cmp` (`tablegrow.cell_table_bound_sites`)."""
    if not ctx.authored_cells:
        return
    from fireflymcf import readout
    try:
        _cell_base, _type_base, stock_len = readout.resolve_cell_tables(ctx.stock)
    except Exception:
        return
    reused = sorted(i for i in ctx.authored_cells if i < stock_len)
    _gate(not reused, "STRUCTURAL_GATE", (
        "op authored display cell index(es) %r below the stock cell-table row count (%d) -- a "
        "private cell must be a freshly grown index, never a reused +0x44BC8 index (on 13.6 every "
        "reusable index is read by an object in another GuiData object table: the coach-48V "
        "background-bar aliasing). Use readout.allocate_fresh_cells." % (reused, stock_len)))


def _check_firmware(specs: list, stock: bytes) -> tuple:
    """(firmware key, refusals). An image `firmware.identify` does not know is refused for every
    spec (once, op_id None, when there is none): it is never guessed. A built-in op whose
    `firmwares` does not list the image's firmware is refused. User ops (`user_op`) skip the list,
    since their own checks are their evidence, but not the identification. Static: reads only each
    op's class, so a refused op's code never runs. An unknown type is `_build_ops`' to report."""
    fw = firmware.identify(stock)
    if fw is None:
        msg = ("this firmware is not one fireflymcf has been tested on (%s)"
               % firmware.describe(stock))
        return None, ([RecipeError("UNSUPPORTED_FIRMWARE", msg, op_id=s.id) for s in specs]
                      or [RecipeError("UNSUPPORTED_FIRMWARE", msg)])
    errors = []
    for spec in specs:
        cls = _lookup(spec.type)
        if cls is None or getattr(cls, "user_op", False) or fw in cls.firmwares:
            continue
        errors.append(RecipeError(
            "UNSUPPORTED_FIRMWARE", "%s is not supported on firmware %s (%s): it has been tested "
            "on %s" % (spec.type, fw, firmware.describe(stock), ", ".join(sorted(cls.firmwares))),
            op_id=spec.id))
    return fw, errors


def _build_ops(specs: list, refused: set = frozenset()) -> tuple:
    """Construct+validate an Op instance for each spec. Returns (ops, errors); an
    unknown type or a validation failure appends a RecipeError to `errors` instead of
    raising, so the caller can collect every problem in one pass. A spec whose id is in
    `refused` (`_check_firmware` refused it) is not constructed."""
    ops, errors = [], []
    for spec in specs:
        if spec.id in refused:
            continue
        cls = _lookup(spec.type)
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
    `add_button_status` LATER in `specs` lights (an entry whose `button.ref` is its id), and
    `CYCLE_UNDRIVEN` for every one with `cycle` that no later enabled `add_button_states`
    names. A toggle or cycle sends by its variant, which only that op's routine sets
    (`setVariant`); undriven, the variant sits at 0 and the button only ever sends its first
    payload. A disabled spec drives nothing, and a status op before the button cannot name it.
    Static: no op is constructed or applied, so a malformed status op simply drives nothing (its
    own validation reports it)."""
    kinds = (("toggle", "add_button_status", "TOGGLE_UNDRIVEN", "toggle", "on_payload"),
             ("cycle", "add_button_states", "CYCLE_UNDRIVEN", "cycle", "first payload"))
    errors = []
    for i, spec in enumerate(specs):
        if not (spec.enabled and spec.type == "add_rvc_button" and isinstance(spec.params, dict)):
            continue
        for key, driver, code, what, sends in kinds:
            if spec.params.get(key) is None:
                continue
            driven = False
            for later in specs[i + 1:]:
                if not (later.enabled and later.type == driver
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
                    code, "%s button %r is driven by no later %s: its variant would stay 0 and "
                    "it would only ever send its %s" % (what, spec.id, driver, sends),
                    op_id=spec.id))
    return errors


def _prepare(enabled: list, stock: bytes) -> tuple:
    """(firmware key, ops, errors) for the enabled specs: the firmware refusals, then each op
    constructed (a refused one is not), then the toggle check. Every refusal is collected before
    any apply, and both compile paths take it before reading their cache."""
    fw, errors = _check_firmware(enabled, stock)
    if fw is None:
        return None, [], errors
    ops, build_errors = _build_ops(enabled, {e.op_id for e in errors})
    errors += build_errors
    errors += _check_toggles_driven(enabled)
    return fw, ops, errors


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
    sends = {}
    for o in ops:
        lo, ga = o.capability_delta(ctx)
        lost_want |= lo
        gained_want |= ga
        if getattr(o, "user_op", False):
            m = ctx.handles.get((USER_OP_CAPS, o.spec.id)) or {}
            lo_all = set(lo) | {tuple(x) for x in m.get("lost", ())}
            ga_all = set(ga) | {tuple(x) for x in m.get("gained", ())}
            frames = list(m.get("frames", ()))
            if lo_all or ga_all or frames:
                sends[o.spec.id] = {"type": o.spec.type, "lost": sorted(lo_all),
                                    "gained": sorted(ga_all), "frames": frames}
    stock_caps, out_caps = capabilities(stock), capabilities(data)
    _gate(stock_caps - out_caps == lost_want, "CAPABILITY_GATE",
          "capability gate: lost %s, ops declared %s" % (
              sorted(stock_caps - out_caps), sorted(lost_want)))
    _gate(out_caps - stock_caps == gained_want, "CAPABILITY_GATE",
          "capability gate: gained %s, ops declared %s" % (
              sorted(out_caps - stock_caps), sorted(gained_want)))

    manifest = [_verify(o, data, ctx) for o in ops]
    res, ch = patch.reseal(data)
    _gate(res == data, "NOT_SEALED", "image not sealed after verify: %s" % ch)
    fw = ctx.firmware
    unverified = [o.spec.id for o in ops if not o.is_verified(fw)]
    # An op's shift report joins its own line: the manifest stays one entry per op
    shifts, texts = recipe_ops.shift_report(ops, ctx)
    manifest = [m + "; " + texts[o.spec.id] if o.spec.id in texts else m
                for o, m in zip(ops, manifest)]
    return CompileResult(True, data, [], manifest, unverified, shifts, sends, fw,
                         recipe_ops.placement_report(ops, ctx))


# Coded refusals from the locators an op builds on (a template or donor this image does not
# prove, `donors`; a record grammar or LCD group handling that is not 13.6's, `grammar`; a cell
# or type table that does not locate, `celltables`), surfaced under their own code as the op's
# refusal, as FollowChannelError is above.
_CODED = (donors.DonorError, grammar.GrammarError, grammar.LcdGrammarError,
          celltables.CellTableError,
          sramreach.SramReachError, statusbind.SpareRowsUnknown, statusbind.BridgeUnknown,
          newbutton.VariantSetterUnknown, newbutton.ReservedGroupUnknown,
          newbutton.PlacementOverlap, decodeguards.DecoderRangeUnknown)


def _verify(o, data: bytes, ctx: Ctx) -> str:
    """`o.verify`, with a per-tick hook or chain that no longer proves out (`followchannel`'s
    `FollowChannelError`, e.g. `hooklocate.HookNotFound`) refused as HOOK_REFUSED. The hook is
    whole-image state another op may have rewritten, so it is refused as a gate is: a failed
    CompileResult in a build that holds a user op, raised otherwise (`_finish_or_refuse`)."""
    try:
        with stockscope.stock_scope(getattr(ctx, "stock", None)):
            return o.verify(data, ctx)
    except followchannel.FollowChannelError as e:
        raise GateError("HOOK_REFUSED", "%s verify: the per-tick hook does not prove out: %s"
                        % (o.spec.id, e), op_id=o.spec.id) from e
    except _CODED as e:
        raise GateError(e.code, "%s verify: %s" % (o.spec.id, e), op_id=o.spec.id) from e


def _apply(o, data: bytes, ctx: Ctx, limit: int) -> bytes:
    """`o.apply` plus the tick-chain check, with a per-tick hook that does not prove out
    (`FollowChannelError` escaping an op) refused as DETOUR_REFUSED, naming the op."""
    try:
        with stockscope.stock_scope(getattr(ctx, "stock", None)):
            out = o.apply(data, ctx)
    except followchannel.FollowChannelError as e:
        raise RecipeError("DETOUR_REFUSED", "%s: the per-tick hook does not prove out: %s"
                          % (o.spec.id, e), op_id=o.spec.id) from e
    except _CODED as e:
        raise RecipeError(e.code, "%s: %s" % (o.spec.id, e), op_id=o.spec.id) from e
    _check_tick_chain(data, out, limit)
    return out


def _finish_or_refuse(project: Project, stock: bytes, data: bytes, ctx: Ctx,
                      ops: list) -> CompileResult:
    """`_finish`, except that a user op's refusal (`userop` tags it `user_op`) surfacing in its
    verify or capability_delta comes back as a failed CompileResult, the shape every other
    user-op failure has, and so does a whole-image gate's refusal (`GateError`) in a build that
    holds a user op. A built-in op's or the project's RecipeError, and a gate's in a build of
    built-in ops alone, still raises as before."""
    try:
        return _finish(project, stock, data, ctx, ops)
    except GateError as e:
        # A whole-image gate names no op; with a user op in the build it is most likely the
        # user op's doing, and it comes back in the same shape as the user op's own refusals.
        if not any(getattr(o, "user_op", False) for o in ops):
            raise
        return CompileResult(False, None, [e.to_json()], [], [], firmware=ctx.firmware)
    except RecipeError as e:
        if not getattr(e, "user_op", False):
            raise
        return CompileResult(False, None, [e.to_json()], [], [], firmware=ctx.firmware)


def _check_tick_chain(before: bytes, after: bytes, limit: int) -> None:
    """Refuse (TICK_CHAIN_FULL) an op that left more than `limit` authored per-tick routines on
    the GuiCode hook. Only an op that changed the hook's 4 bytes can have added one, so the chain
    is walked only then. A hook that is not a chain is not this check's to refuse."""
    if followchannel.hook_bytes(after) == followchannel.hook_bytes(before):
        return
    try:
        followchannel.chain_from_hook(after, limit)
    except followchannel.TickChainFull as e:
        raise RecipeError(
            "TICK_CHAIN_FULL",
            "this op adds per-tick routine %d; the project allows %d. Every routine runs on each "
            "screen refresh. The panel sets no limit of its own, but the default (%d) is as many "
            "as fireflymcf expects a build to need. To allow more, add \"tick_chain_limit\": %d "
            "(or higher) at the top level of the project file and compile again."
            % (e.count, limit, followchannel.DEFAULT_TICK_CHAIN_LIMIT, e.count),
            offers=["raise_tick_chain_limit"]) from e
    except followchannel.FollowChannelError:
        # The hook holds something other than a chain (an op patched the site itself). There is
        # no routine to count; whether those bytes are sound is the op's own verify's question.
        return


def compile_project(project: Project, stock: bytes) -> CompileResult:
    if hashlib.sha256(stock).hexdigest() != project.base_sha256:
        raise RecipeError("BASE_MISMATCH",
                          "the supplied stock image does not match the project's base "
                          "(sha256 %s...)" % project.base_sha256[:12])
    enabled = [s for s in project.ops if s.enabled]

    fw, ops, errors = _prepare(enabled, stock)
    if errors:
        return CompileResult(False, None, [e.to_json() for e in errors], [], [], firmware=fw)

    ctx = Ctx(stock=stock, edits=[], insertions=[], handles={}, firmware=fw)
    data = stock
    limit = project.effective_tick_chain_limit
    for o in ops:
        try:
            data = _apply(o, data, ctx, limit)
        except RecipeError as e:
            e.op_id = e.op_id or o.spec.id
            return CompileResult(False, None, [e.to_json()], [], [], firmware=fw)

    return _finish_or_refuse(project, stock, data, ctx, ops)


def _prefix_key(base_hash: str, specs: list, tick_chain_limit: int | None = None) -> str:
    """A cache key for the state after applying exactly `specs`, in order, to
    `base_hash`'s stock. Keyed on (id, type, params) only — `enabled`/`note` never
    reach here since callers always filter to the enabled list first, and `note` has
    no effect on the compiled bytes. A set `tick_chain_limit` joins the key: it changes no
    byte, but a cached prefix was checked against the limit it was compiled under, so a
    lowered limit must recompile to be enforced. Unset, the key is unchanged."""
    user = _user_ops()
    # A user op's bytes come from its folder, not the package: its folder hash joins the key, so
    # an edited op misses. Built-in entries keep their 3-tuple, so their keys are unchanged.
    doc = [(s.id, s.type, s.params) + ((user[s.type].folder_sha256,) if s.type in user else ())
           for s in specs]
    text = base_hash + json.dumps(doc, sort_keys=True)
    if tick_chain_limit is not None:
        text += "|tick_chain_limit=%d" % tick_chain_limit
    return hashlib.sha256(text.encode()).hexdigest()


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

    # Every refusal comes before the cache is read: a cached prefix never carries an op past
    # the firmware gate.
    fw, ops, errors = _prepare(enabled, stock)
    if errors:
        return CompileResult(False, None, [e.to_json() for e in errors], [], [], firmware=fw)

    start = 0
    data, snap = stock, ([], [], {}, set())
    for i in range(len(enabled), 0, -1):
        key = _prefix_key(project.base_sha256, enabled[:i], project.tick_chain_limit)
        if key in cache:
            data, snap = cache[key]
            start = i
            break
    ctx = Ctx(stock=stock, edits=list(snap[0]), insertions=list(snap[1]),
             handles=copy.deepcopy(snap[2]), authored_cells=set(snap[3]), firmware=fw)

    limit = project.effective_tick_chain_limit
    for i in range(start, len(enabled)):
        try:
            data = _apply(ops[i], data, ctx, limit)
        except RecipeError as e:
            e.op_id = e.op_id or ops[i].spec.id
            return CompileResult(False, None, [e.to_json()], [], [], firmware=fw)
        cache[_prefix_key(project.base_sha256, enabled[:i + 1], project.tick_chain_limit)] = (
            data, _snapshot(ctx))

    return _finish_or_refuse(project, stock, data, ctx, ops)


from fireflymcf import recipe_ops  # noqa: E402,F401  (populates REGISTRY)
