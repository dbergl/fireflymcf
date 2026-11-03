"""The checked `Editor`: the only way a user-written op changes image bytes.

Every write carries the bytes expected at the site (a mismatch means the op was written for
another firmware), and every step lands in a ledger. `replay(input, ledger)` re-applies the
ledger to the op's input; equality with the op's output proves no byte changed except through a
recorded, checked step (a section growth shifts offsets, so a run-by-run diff cannot attribute
bytes, but replay can).

Addresses: GuiCode, GuiData and the LCD firmware are mapped at their descriptor's flash
destination (`theme.guicode_base`). G12 has no CPU address the panel uses, so its "address" is
the offset within the section.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import container, growsection, recipe, relocate, routinekit, theme
from .recipe import RecipeError


class EditorError(RecipeError):
    """Codes: FIRMWARE_MISMATCH, NOT_A_PRIMITIVE, BAD_EDIT."""


@dataclass(frozen=True)
class Step:
    kind: Literal["patch", "grow", "call"]
    args: tuple
    note: str


def _check_arg(v) -> None:
    if isinstance(v, (bytes, str, int, float, bool)) or v is None:
        return
    if isinstance(v, (list, tuple)):
        for x in v:
            _check_arg(x)
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise EditorError("BAD_EDIT", f"call argument key {k!r} is not a string")
            _check_arg(x)
        return
    raise EditorError("BAD_EDIT", f"call argument {v!r} is not JSON-able or bytes")


class Editor:
    def __init__(self, data: bytes):
        self._buf = bytes(data)
        self._ledger: list[Step] = []

    @property
    def data(self) -> bytes:
        return bytes(self._buf)

    @property
    def ledger(self) -> tuple[Step, ...]:
        return copy.deepcopy(tuple(self._ledger))

    def section(self, marker: str) -> container.Section:
        found = [s for s in container.parse_container(self._buf).sections if marker in s.name]
        if not found:
            raise EditorError("BAD_EDIT", f"no section matching {marker!r}")
        if len(found) > 1:
            raise EditorError("BAD_EDIT", f"{marker!r} matches several sections: "
                              f"{[s.name for s in found]}")
        return found[0]

    def offset_of(self, marker: str, addr: int) -> int:
        s = self.section(marker)
        rel = addr if marker == "G12" else addr - theme.guicode_base(s)
        if not 0 <= rel < s.length:
            raise EditorError("BAD_EDIT", f"{marker}+{addr:#x} is outside the section "
                              f"({s.length:#x} bytes)")
        return s.offset + rel

    def expect(self, off: int, expected: bytes) -> None:
        self._expect(off, expected, f"@{off:#x}")

    def _expect(self, off: int, expected: bytes, where: str) -> None:
        if off < 0 or off + len(expected) > len(self._buf):
            raise EditorError("BAD_EDIT", f"{where} is outside the image")
        found = self._buf[off:off + len(expected)]
        if found != expected:
            raise EditorError(
                "FIRMWARE_MISMATCH",
                f"this op does not match your firmware: {where} holds {found.hex()}, "
                f"the op expects {expected.hex()}")

    def patch(self, off: int, expected: bytes, new: bytes, note: str,
              _where: str | None = None) -> None:
        expected, new = bytes(expected), bytes(new)
        if len(expected) != len(new):
            raise EditorError("BAD_EDIT", "patch must not change the length "
                              f"({len(expected)} expected, {len(new)} new)")
        self._expect(off, expected, _where or f"@{off:#x}")
        self._buf = self._buf[:off] + new + self._buf[off + len(new):]
        self._ledger.append(Step("patch", (off, expected, new), note))

    def patch_addr(self, marker: str, addr: int, expected: bytes, new: bytes, note: str) -> None:
        off = self.offset_of(marker, addr)
        self.patch(off, expected, new, note, _where=f"{marker}+{addr:#x}")

    def grow_guicode(self, payload: bytes, note: str) -> int:
        payload = bytes(payload)
        try:
            sealed, load_off, _ = growsection.grow_section(self._buf, "GuiCode", payload)
        except growsection.GrowSectionError as e:
            raise EditorError("BAD_EDIT", str(e)) from e
        self._buf = sealed
        self._ledger.append(Step("grow", (payload,), note))
        return theme.GUICODE_BASE_DEFAULT + load_off

    def call(self, name: str, *args, **kwargs):
        fn = routinekit.PRIMITIVES.get(name)
        if fn is None:
            raise EditorError("NOT_A_PRIMITIVE", f"{name!r} is not a routinekit primitive")
        _check_arg(args)
        _check_arg(kwargs)
        # The primitive gets its own copy and the ledger keeps another, so neither an in-place
        # mutation by the primitive nor a later edit by the caller changes what replay re-invokes.
        rec = copy.deepcopy((name, args, kwargs))
        out = fn(self._buf, *copy.deepcopy(args), **copy.deepcopy(kwargs))
        new, result = out if isinstance(out, tuple) else (out, None)
        self._buf = bytes(new)
        self._ledger.append(Step("call", rec, f"call {name}"))
        return result


def replay(data: bytes, ledger: tuple[Step, ...]) -> bytes:
    return _replay(data, ledger)[0]


def _replay(data: bytes, ledger: tuple[Step, ...]) -> tuple[bytes, list]:
    """`replay`, plus one `relocate.Edit` per patch and grow step (output-image offsets)."""
    ed, edits = Editor(data), []
    for st in ledger:
        if st.kind == "patch":
            off, expected, new = st.args
            ed.patch(off, expected, new, st.note)
            edits.append(relocate.Edit(off, bytes(expected), bytes(new), "user op", st.note))
        elif st.kind == "grow":
            gc = ed.section("GuiCode")
            at = gc.offset + gc.length - 4          # where `grow_section` inserts
            ed.grow_guicode(*st.args, st.note)
            edits.append(relocate.Edit(at, b"", bytes(st.args[0]), "user op", st.note))
        elif st.kind == "call":
            name, args, kwargs = st.args
            ed.call(name, *args, **kwargs)
        else:
            raise EditorError("BAD_EDIT", f"unknown step kind {st.kind!r}")
    return ed.data, edits


# ---------------------------------------------------------------------------------------------
# UserOp and the loader
# ---------------------------------------------------------------------------------------------

TOUCHES = frozenset({"guicode", "guidata", "g12", "lcd"})
MANIFEST_FIELDS = {"name": str, "type": str, "version": str, "author": str,
                   "description": str, "touches": list, "written_against": list,
                   "provenance": list}
# What each list field's items must be: `touches` names sections; `written_against` and
# `provenance` hold objects (the design's {build_name, base_sha256, ...} / {kind, ...}).
MANIFEST_ITEMS = {"touches": str, "written_against": dict, "provenance": dict}
PREFIX = "user:"


def _where(exc: BaseException, folder: Path | None = None) -> str:
    """`file:line` of the frame that raised: the deepest frame inside the op's folder when there
    is one (a crash inside a library call is reported where the op made it), else the deepest."""
    if isinstance(exc, SyntaxError) and exc.filename:
        return f"{_rel(exc.filename, folder)}:{exc.lineno}"
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "?"
    mine = [f for f in frames if folder is not None and _inside(f.filename, folder)]
    f = (mine or frames)[-1]
    return f"{_rel(f.filename, folder)}:{f.lineno}"


def _inside(filename: str, folder: Path) -> bool:
    try:
        Path(filename).resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _rel(filename: str, folder: Path | None) -> str:
    if folder is not None and _inside(filename, folder):
        return Path(filename).resolve().relative_to(folder.resolve()).as_posix()
    return filename


def _crashed(type_name: str, exc: BaseException, folder: Path | None, op_id=None) -> RecipeError:
    return _mark(RecipeError("USER_OP_CRASHED", f"{type_name}: {type(exc).__name__}: {exc} "
                             f"(at {_where(exc, folder)})", op_id=op_id))


def _mark(e: RecipeError) -> RecipeError:
    """Tag a refusal as a user op's, so `recipe._finish_or_refuse` returns it as a failed
    CompileResult wherever in the compile it surfaced (verify and the capability gate included)."""
    e.user_op = True
    return e


# Section name marker -> the `touches` value that declares it (as `Editor.section` matches).
SECTION_TOUCHES = (("GuiCode", "guicode"), ("GuiData", "guidata"), ("G12", "g12"),
                   ("LCD", "lcd"))


HEADER = "header"                  # the touch key for bytes outside every section; undeclarable


def _layout(data: bytes) -> dict:
    """{touch key: [(section name, payload, masked descriptor)]}, plus HEADER: every byte that is
    neither a payload nor a descriptor (container header, pointer array, gaps), in file order.

    The image is resealed first, so a checksum differs only where the bytes it covers do: an op
    whose reseal rewrites a stale checksum left by an earlier op has not touched that section.
    A descriptor is compared with its payload offset and length and its own checksum masked (a
    growth moves later payloads and lengthens the grown one, which the payload already shows);
    every other descriptor byte (type, tagged entries, flash destination) is the section's."""
    from . import patch
    data, _ = patch.reseal(data)
    c = container.parse_container(data)
    descs = container._descriptor_offsets(data, c.section_count, c.header_word_20)
    out: dict = {}
    claimed = []
    for s, d in zip(c.sections, descs):
        entry = bytearray(s.raw_entry)
        entry[container.DESC_EXTENT_OFFSET:container.DESC_EXTENT_OFFSET + 8] = bytes(8)
        entry[len(entry) - 4:len(entry) - 2] = bytes(2)
        key = next((t for m, t in SECTION_TOUCHES if m in s.name), s.name)
        out.setdefault(key, []).append((s.name, bytes(data[s.offset:s.offset + s.length]),
                                        bytes(entry)))
        claimed += [(s.offset, s.offset + s.length), (d, d + len(s.raw_entry))]
    rest, pos = [], 0
    for a, b in sorted(claimed):
        if a > pos:
            rest.append(data[pos:a])
        pos = max(pos, b)
    rest.append(data[pos:])
    out[HEADER] = [("container header", b"".join(rest), b"")]
    return out


def _touched(before: bytes, after: bytes) -> dict:
    """{touch key: section name} for each section whose payload or descriptor differs between the
    two images (matched by section identity, not offset), and HEADER when anything outside them
    does. An unknown section is keyed by its own name; neither it nor HEADER can be declared."""
    a, b = _layout(before), _layout(after)
    out = {}
    for k in a.keys() | b.keys():
        if a.get(k) != b.get(k):
            out[k] = (a.get(k) or b.get(k))[0][0]
    return out


def touched_sections(before: bytes, after: bytes) -> set[str]:
    """The `touches` values (section kinds) whose bytes differ from `before` to `after`."""
    return set(_touched(before, after))


class UserOp(recipe.Op):
    """A user-written op. Subclasses implement `build` (every write through the `Editor`) and
    `check` (returns the manifest line); they must not override `apply` or `verify`, which are
    the gate: the engine replays the Editor's ledger against the op's input and refuses any byte
    the ledger does not account for. Always `hardware_verified = False`."""
    hardware_verified = False
    user_op = True
    manifest: dict = {}                                   # set by the loader
    folder_sha256: str = ""
    folder: Path | None = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # `capability_delta` is the one Op hook a user op may override (an op that changes a
        # binding declares it). The engine calls it from `_finish`, so it gets the same boundary.
        own = cls.__dict__.get("capability_delta")
        if own is not None and not getattr(own, "_user_op_guarded", False):
            def capability_delta(self, ctx, _own=own):
                out = self._guard(spec_id=self.spec.id, fn=_own, args=(self, ctx))
                if not (isinstance(out, tuple) and len(out) == 2
                        and all(isinstance(x, (set, frozenset)) for x in out)):
                    raise _mark(RecipeError(
                        "USER_OP_CRASHED", f"{self.type}: capability_delta returned {out!r}, "
                        "not (lost, gained) sets", op_id=self.spec.id))
                return out
            capability_delta._user_op_guarded = True
            cls.capability_delta = capability_delta

    def _guard(self, *, spec_id, fn, args):
        """Run user code: a RecipeError passes through (tagged), any other exception, SystemExit
        included, becomes USER_OP_CRASHED. Only KeyboardInterrupt escapes: it is the user's."""
        try:
            return fn(*args)
        except KeyboardInterrupt:
            raise
        except RecipeError as e:
            e.op_id = e.op_id or spec_id
            raise _mark(e)
        except BaseException as e:  # noqa: BLE001 - a user op's crash is a structured refusal
            raise _crashed(self.type, e, self.folder, spec_id) from e

    def __init__(self, spec: recipe.OpSpec):
        # runs the subclass's validate()
        self._guard(spec_id=spec.id, fn=super().__init__, args=(spec,))

    def build(self, ed: Editor, ctx: recipe.Ctx) -> None:
        raise NotImplementedError

    def check(self, data: bytes, ctx: recipe.Ctx) -> str:
        raise NotImplementedError

    def apply(self, data: bytes, ctx: recipe.Ctx) -> bytes:
        ed = Editor(data)
        self._guard(spec_id=self.spec.id, fn=self.build, args=(ed, ctx))
        out = ed.data
        try:
            replayed, edits = _replay(data, ed.ledger)
        except RecipeError as e:
            raise _mark(RecipeError("USER_OP_TAMPERED", f"{self.type}: its ledger does not "
                                    f"replay on its input ({e.code}: {e})",
                                    op_id=self.spec.id)) from e
        if replayed != out:
            diff = next((i for i, (a, b) in enumerate(zip(replayed, out)) if a != b),
                        min(len(replayed), len(out)))
            raise _mark(RecipeError("USER_OP_TAMPERED", f"{self.type}: its output differs "
                                    f"from the replay of its ledger (first at file offset "
                                    f"{diff:#x}): a byte was written outside the Editor",
                                    op_id=self.spec.id))
        declared = set(self.manifest.get("touches", []))
        try:
            touched = _touched(data, out)
        except Exception as e:  # noqa: BLE001 - an unreadable output is the op's failure
            raise _mark(RecipeError("USER_OP_UNDECLARED_TOUCH", f"{self.type}: its output is "
                                    f"not a readable container ({e})", op_id=self.spec.id))
        undeclared = {k: n for k, n in touched.items() if k not in declared}
        if undeclared:
            raise _mark(RecipeError(
                "USER_OP_UNDECLARED_TOUCH",
                f"{self.type}: it changed the {', '.join(sorted(undeclared.values()))} "
                f"section(s) ({', '.join(sorted(undeclared))}), which its manifest's 'touches' "
                f"{sorted(declared)} does not declare; an op may change only what it declares "
                "(and a G12 change needs the owner's acceptance)", op_id=self.spec.id))
        ctx.edits.extend(edits)
        # what the panel can send, before and after this op: a change asks the G12 acceptance
        # whatever the manifest's `touches` says (`recipe.CompileResult.sends`)
        try:
            before, after = recipe.capabilities(data), recipe.capabilities(out)
        except Exception as e:  # noqa: BLE001 - an unreadable table is the op's failure
            raise _mark(RecipeError("USER_OP_UNDECLARED_TOUCH", f"{self.type}: its output's "
                                    f"group or binding table is unreadable ({e})",
                                    op_id=self.spec.id))
        ctx.handles[(recipe.USER_OP_CAPS, self.spec.id)] = {
            "lost": sorted(before - after), "gained": sorted(after - before)}
        return out

    def verify(self, data: bytes, ctx: recipe.Ctx) -> str:
        line = self._guard(spec_id=self.spec.id, fn=self.check, args=(data, ctx))
        if not isinstance(line, str):
            raise _mark(RecipeError("USER_OP_CRASHED", f"{self.type}: check returned "
                                    f"{type(line).__name__}, not a manifest line",
                                    op_id=self.spec.id))
        return "user op %s v%s (%s): %s" % (self.type, self.manifest.get("version", "?"),
                                            self.folder_sha256[:12], line)


@dataclass
class LoadedOp:
    type: str
    cls: type
    folder: Path
    manifest: dict
    folder_sha256: str


IGNORED = frozenset({"__pycache__", ".git"})      # folder names never hashed, packed or copied


def ignored(rel: Path) -> bool:
    """Whether a path relative to an op folder is outside what the folder hash covers."""
    return rel.suffix == ".pyc" or any(part in IGNORED for part in Path(rel).parts)


def _snapshot(folder: Path) -> dict[str, bytes]:
    """Every hashed file's bytes, by posix relative path: what `folder_hash` covers and what the
    loader compiles from. A symbolic link is refused, never followed: following one would hash,
    run or pack a file from outside the folder."""
    folder = Path(folder)
    snap: dict[str, bytes] = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if ignored(rel):
            continue
        if p.is_symlink():
            raise _bad(folder, f"{rel.as_posix()} is a symbolic link; an op folder holds only "
                       "regular files")
        if p.is_file():
            snap[rel.as_posix()] = p.read_bytes()
    return snap


def _hash_snapshot(snap: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(snap):
        body = snap[rel]
        h.update(rel.encode() + b"\0" + len(body).to_bytes(8, "little"))
        h.update(body)
    return h.hexdigest()


def folder_hash(folder: Path) -> str:
    """sha256 over the folder's files, sorted by posix relative path (path, NUL, length, bytes
    per file), ignoring `__pycache__`, `*.pyc` and `.git` (`IGNORED`). Raises BAD_USER_OP on a
    symbolic link."""
    return _hash_snapshot(_snapshot(folder))


def _bad(folder: Path, why: str) -> RecipeError:
    return RecipeError("BAD_USER_OP", f"{folder}: {why}")


def _read_manifest(folder: Path) -> dict:
    mf = folder / "manifest.json"
    if not mf.is_file():
        raise _bad(folder, "no manifest.json")
    if not (folder / "op.py").is_file():
        raise _bad(folder, "no op.py")
    try:
        text = mf.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise _bad(folder, f"cannot read manifest.json: {e}")
    try:
        m = json.loads(text)
    except (ValueError, RecursionError) as e:
        raise _bad(folder, f"manifest.json is not valid JSON: {e}")
    if not isinstance(m, dict):
        raise _bad(folder, "manifest.json must be an object")
    for k, kind in MANIFEST_FIELDS.items():
        if not isinstance(m.get(k), kind):
            raise _bad(folder, f"manifest field {k!r} must be a {kind.__name__}, "
                       f"got {m.get(k)!r}")
    for k, kind in MANIFEST_ITEMS.items():
        wrong = [x for x in m[k] if not isinstance(x, kind)]
        if wrong:
            raise _bad(folder, f"manifest field {k!r} must hold only "
                       f"{'strings' if kind is str else 'objects'}, got {wrong[0]!r}")
    bad = [t for t in m["touches"] if t not in TOUCHES]
    if bad:
        raise _bad(folder, f"manifest 'touches' has {bad}; allowed: {sorted(TOUCHES)}")
    t = m["type"]
    if not t.startswith(PREFIX) or len(t) == len(PREFIX):
        raise RecipeError("USER_OP_NAME", f"{folder}: user op type {t!r} must start with "
                          f"{PREFIX!r} and name the op")
    if t in recipe.REGISTRY:
        raise RecipeError("USER_OP_NAME", f"{folder}: {t!r} is a built-in op")
    return m


class _SourceLoader(importlib.abc.Loader):
    """Compiles the exact source bytes the folder hash covered; never reads or writes bytecode
    (R7: a stale `__pycache__` must not run in place of the accepted source)."""

    def __init__(self, source: bytes, path: Path, is_package: bool):
        self.source, self.path, self._is_package = source, str(path), is_package

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = compile(self.source, self.path, "exec", dont_inherit=True)
        exec(code, module.__dict__)

    def is_package(self, fullname):
        return self._is_package

    def get_source(self, fullname):
        return importlib.util.decode_source(self.source)


def _spec(name: str, folder: Path, snap: dict, rel: str, package_dir: str | None):
    spec = importlib.machinery.ModuleSpec(name, _SourceLoader(snap[rel], folder / rel,
                                                              package_dir is not None),
                                          origin=str(folder / rel),
                                          is_package=package_dir is not None)
    spec.has_location = True
    if package_dir is not None:
        spec.submodule_search_locations = [str(folder / package_dir) if package_dir
                                           else str(folder)]
    return spec


class _OpFinder(importlib.abc.MetaPathFinder):
    """Resolves `<op package>.x[.y]` (an op's `from . import x`) from the op's hashed snapshot.
    A name under an op package that the snapshot lacks is an error, never a search on disk, so a
    sourceless `.pyc` or an extension module beside op.py cannot be imported."""
    packages: dict = {}                    # package name -> (folder, snapshot)

    def find_spec(self, fullname, path=None, target=None):
        pkg, dot, rest = fullname.partition(".")
        if not dot or pkg not in self.packages:
            return None
        folder, snap = self.packages[pkg]
        rel = rest.replace(".", "/")
        if f"{rel}.py" in snap:
            return _spec(fullname, folder, snap, f"{rel}.py", None)
        if f"{rel}/__init__.py" in snap:
            return _spec(fullname, folder, snap, f"{rel}/__init__.py", rel)
        raise ModuleNotFoundError(f"no module {rest!r} in the op folder {folder} (a sibling "
                                  "module is imported as `from . import name`, from its .py "
                                  "source)", name=fullname)


_FINDER = _OpFinder()


def _forget(name: str) -> None:
    for k in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        sys.modules.pop(k, None)


def _import(folder: Path, manifest: dict, sha: str, snap: dict) -> type:
    """Import op.py as the package `fireflymcf_userop_<sha12>` from `snap`, the source bytes
    that hash to `sha`."""
    name = f"fireflymcf_userop_{sha[:12]}"
    if "op.py" not in snap:
        raise _bad(folder, "no op.py")
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
    _forget(name)                          # a sibling cached under this name is from this hash
    _OpFinder.packages[name] = (folder, snap)
    spec = _spec(name, folder, snap, "op.py", "")
    module = importlib.util.module_from_spec(spec)
    captured: dict = {}
    token = recipe._CAPTURE.set(captured)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except KeyboardInterrupt:
        _forget(name)
        raise
    except BaseException as e:  # noqa: BLE001 - a broken op.py (SystemExit too) is a refusal
        _forget(name)
        raise _bad(folder, f"importing op.py failed: {type(e).__name__}: {e} "
                   f"(at {_where(e, folder)})") from e
    finally:
        recipe._CAPTURE.reset(token)
    t = manifest["type"]
    cls = captured.get(t)
    if cls is None or not (isinstance(cls, type) and issubclass(cls, UserOp)):
        raise _bad(folder, f"op.py defines no UserOp subclass decorated @recipe.op({t!r}) "
                   f"(it registers {sorted(captured)})")
    extra = sorted(set(captured) - {t})
    if extra:
        raise _bad(folder, f"op.py registers {extra} besides its manifest type {t!r}")
    for gate in ("apply", "verify"):
        if getattr(cls, gate) is not getattr(UserOp, gate):
            raise _bad(folder, f"{cls.__name__} overrides {gate}(), which is the engine's "
                       "gate; implement build() and check()")
    if cls.__dict__.get("hardware_verified", False) is not False:
        raise _bad(folder, "a user op cannot declare hardware_verified")
    cls.hardware_verified = False
    cls.manifest = manifest
    cls.folder_sha256 = sha
    cls.folder = folder
    return cls


def _folders(project_path: Path, project: recipe.Project) -> list[Path]:
    project_path = Path(project_path)
    base = project_path if project_path.is_dir() else project_path.parent
    out: list[Path] = []
    ops_dir = base / "ops"
    if ops_dir.is_dir():
        out += sorted(p for p in ops_dir.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "__")))
    for entry in project.ext_ops:
        p = Path(entry)
        p = p if p.is_absolute() else base / p
        if not p.is_dir():
            raise RecipeError("BAD_USER_OP", f"ext_ops entry {entry!r} is not a folder "
                              f"({p})")
        out.append(p)
    seen, unique = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


@dataclass
class DiscoveredOp:
    """An op folder whose manifest has been read and checked, and whose files are hashed. Nothing
    in it has been imported: discovery is what `ops`, `op install`/`pack`/`accept`, and every
    refusal before `--allow-user-ops` read (R6)."""
    type: str
    folder: Path
    manifest: dict
    folder_sha256: str

    @property
    def touches(self) -> list:
        return list(self.manifest.get("touches", []))


def discover_user_ops(project_path: Path, project: recipe.Project) -> list[DiscoveredOp]:
    """Every op folder beside the project (`<project dir>/ops/*/`) and in `project.ext_ops`,
    its manifest validated and its folder hashed. Never imports `op.py`. Raises RecipeError
    (`BAD_USER_OP`, `USER_OP_NAME`, `USER_OP_DUPLICATE`) on the first bad folder."""
    found: list[DiscoveredOp] = []
    by_type: dict[str, Path] = {}
    for folder in _folders(project_path, project):
        m = _read_manifest(folder)
        if m["type"] in by_type:
            raise RecipeError("USER_OP_DUPLICATE", f"user op type {m['type']!r} is defined "
                              f"in both {by_type[m['type']]} and {folder}")
        by_type[m["type"]] = folder
        try:
            sha = folder_hash(folder)
        except OSError as e:
            raise _bad(folder, f"cannot read the op folder: {e}")
        found.append(DiscoveredOp(m["type"], folder, m, sha))
    return found


def used_types(project: recipe.Project) -> set[str]:
    """The user op types an enabled op of `project` names: the only ones its build runs."""
    return {s.type for s in project.ops if s.enabled and s.type.startswith(PREFIX)}


def discover_used_ops(project_path: Path, project: recipe.Project) -> list[DiscoveredOp]:
    """`discover_user_ops`, narrowed to the folders whose type an enabled op of `project` names
    (`used_types`): what compile, validate and verify gate, accept-check and import. A folder
    the project does not use is left alone, broken or not, so one folder of projects can hold an
    op that only some of them use. A broken folder is still reported when a used type is found in
    no good folder (it may be that op's folder). Never imports `op.py`."""
    want = used_types(project)
    if not want:
        return []
    found: list[DiscoveredOp] = []
    by_type: dict[str, Path] = {}
    broken: list[RecipeError] = []
    for folder in _folders(project_path, project):
        try:
            m = _read_manifest(folder)
        except RecipeError as e:
            broken.append(e)
            continue
        if m["type"] not in want:
            continue
        if m["type"] in by_type:
            raise RecipeError("USER_OP_DUPLICATE", f"user op type {m['type']!r} is defined "
                              f"in both {by_type[m['type']]} and {folder}")
        by_type[m["type"]] = folder
        try:
            sha = folder_hash(folder)
        except OSError as e:
            raise _bad(folder, f"cannot read the op folder: {e}")
        found.append(DiscoveredOp(m["type"], folder, m, sha))
    if broken and want - set(by_type):
        raise broken[0]
    return found


def _verified_snapshot(d: DiscoveredOp) -> dict[str, bytes]:
    """`d`'s files read once, refused (BAD_USER_OP) unless they still hash to what discovery
    saw: what was shown (and accepted) is what runs."""
    try:
        snap = _snapshot(d.folder)
    except OSError as e:
        raise _bad(d.folder, f"cannot read the op folder: {e}")
    now = _hash_snapshot(snap)
    if now != d.folder_sha256:
        raise _bad(d.folder, f"its files changed since they were read (sha256 "
                   f"{d.folder_sha256[:12]}... is now {now[:12]}...); run the command again")
    return snap


def verified_snapshots(discovered: list[DiscoveredOp]) -> dict[str, dict[str, bytes]]:
    """{type: files} for each discovered op, checked against its folder hash (`verify
    --project` takes each `emu_test.py`'s bytes from here)."""
    return {d.type: _verified_snapshot(d) for d in discovered}


def import_user_ops(discovered: list[DiscoveredOp]) -> list[LoadedOp]:
    """Import each discovered op's `op.py`: this runs the op's code with full Python trust, so the
    caller must hold the owner's authorization (`--allow-user-ops`). A folder whose files changed
    since discovery is refused, so what was shown (and accepted) is what runs."""
    loaded: list[LoadedOp] = []
    for d in discovered:
        snap = _verified_snapshot(d)       # read once: these bytes are hashed and compiled
        loaded.append(LoadedOp(d.type, _import(d.folder, d.manifest, d.folder_sha256, snap),
                               d.folder, d.manifest, d.folder_sha256))
    return loaded


def load_user_ops(project_path: Path, project: recipe.Project) -> list[LoadedOp]:
    """`discover_user_ops` then `import_user_ops`: validated and imported. Nothing enters
    `recipe.REGISTRY`: compile inside `with registered(loaded):`."""
    return import_user_ops(discover_user_ops(project_path, project))


def acceptance(project: recipe.Project, d: DiscoveredOp) -> dict | None:
    """The owner's acceptance record for `d`, if it names `d`'s current folder hash; else None
    (never accepted, or accepted before an edit)."""
    rec = project.user_op_acceptance.get(d.type)
    if isinstance(rec, dict) and rec.get("folder_sha256") == d.folder_sha256:
        return rec
    return None


def needs_g12_ack(project: recipe.Project, discovered: list[DiscoveredOp]) -> list[DiscoveredOp]:
    """The discovered ops whose manifest `touches` the G12 and whose current folder the owner has
    not accepted with `g12: true` (`fireflymcf op accept PROJECT TYPE --g12`)."""
    return [d for d in discovered if "g12" in d.touches
            and (acceptance(project, d) or {}).get("g12") is not True]


@contextlib.contextmanager
def registered(loaded: list[LoadedOp]):
    """Make `loaded` resolvable by `recipe.compile_project`/`compile_cached` for the block's
    duration (this thread/context only); the previous overlay is restored on exit."""
    overlay = {}
    for lo in loaded:
        if lo.type in recipe.REGISTRY or not lo.type.startswith(PREFIX):
            raise RecipeError("USER_OP_NAME", f"{lo.type!r} is not a user op type")
        if lo.type in overlay:
            raise RecipeError("USER_OP_DUPLICATE", f"user op type {lo.type!r} given twice")
        overlay[lo.type] = lo
    token = recipe._USER_OPS.set(overlay)
    try:
        yield overlay
    finally:
        recipe._USER_OPS.reset(token)
