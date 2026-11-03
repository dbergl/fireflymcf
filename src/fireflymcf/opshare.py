"""Sharing user ops: `fireflymcf op install`, `op pack` and `op accept`.

None of this imports or runs an op (R6): install copies files and reads the manifest, pack zips a
folder, accept records the owner's yes against the folder's current hash. A user op runs with full
Python trust once a build is authorised with `--allow-user-ops`; the replay check is not a sandbox,
which is why install prints every file for Claude to read before anything is built.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import recipe, userop
from .recipe import RecipeError

GIT_TIMEOUT_S = 300
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# Only these git URL forms; no `ext::`, no option-shaped arguments, no whitespace.
_GIT_URL = re.compile(r"^(?:https://[^\s]+|file://[^\s]+|git@[A-Za-z0-9][A-Za-z0-9.-]*:[^\s-][^\s]*)$")
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


# --- the project file --------------------------------------------------------------------------

def load_project(path: Path) -> recipe.Project:
    return recipe.Project.from_json(Path(path).read_text(encoding="utf-8"))


def project_text(path: Path, project: recipe.Project) -> str:
    """`project.to_json()` for rewriting `path`, refused when it would drop a key the file holds
    (to_json writes only the fields `Project` knows; a hand-added key would be lost)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    text = project.to_json()
    new = json.loads(text)
    lost = [k for k, v in raw.items() if k not in new and v not in ({}, [], None)]
    for i, (ro, no) in enumerate(zip(raw.get("ops") or [], new["ops"])):
        if isinstance(ro, dict):
            lost += [f"ops[{i}].{k}" for k in ro if k not in no]
    if lost:
        raise RecipeError("PROJECT_REWRITE", f"refusing to rewrite {path}: it holds "
                          f"{', '.join(lost)}, which the project format does not carry and "
                          "would be lost; move them out (or into an op's note) and try again")
    return text + "\n"


def write_project(path: Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _base(project_path: Path) -> Path:
    p = Path(project_path)
    return p if p.is_dir() else p.parent


def find(project_path: Path, project: recipe.Project, name: str) -> userop.DiscoveredOp:
    """A discovered op by type (`user:x`) or folder name."""
    found = [d for d in userop.discover_user_ops(project_path, project)
             if name in (d.type, d.folder.name)]
    if not found:
        raise RecipeError("NO_SUCH_USER_OP", f"this project has no user op {name!r} "
                          "(`fireflymcf ops --project P` lists them)")
    return found[0]


def files_of(folder: Path) -> list[str]:
    folder = Path(folder)
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                  if p.is_file() and not p.is_symlink()
                  and not userop.ignored(p.relative_to(folder)))


# --- accept --------------------------------------------------------------------------------------

def accept(project_path: Path, type_name: str, g12: bool, today: str | None = None) -> dict:
    """Record the owner's acceptance of `type_name` at its current folder hash; returns the
    record. A G12-touching op is accepted only with `g12=True`."""
    project = load_project(project_path)
    d = find(project_path, project, type_name)
    if "g12" in d.touches and not g12:
        raise RecipeError("G12_ACK_REQUIRED", f"{d.type} changes the G12's configuration (what "
                          "drives the pumps, slides, heaters and other loads); accept it with "
                          "--g12 once the owner has said yes to that")
    rec = {"folder_sha256": d.folder_sha256, "g12": bool(g12),
           "date": today or datetime.date.today().isoformat()}
    project.user_op_acceptance = dict(project.user_op_acceptance, **{d.type: rec})
    write_project(project_path, project_text(project_path, project))
    return rec


# --- pack ----------------------------------------------------------------------------------------

def pack(project_path: Path, name: str, out: Path) -> dict:
    """Zip one op folder as `<folder name>/...` (no `__pycache__`, `*.pyc`, `.git`), with its
    folder hash in the zip comment, which `install` checks."""
    out = Path(out)
    if out.suffix.lower() != ".zip":
        raise RecipeError("BAD_PACK", f"{out}: the pack must be named *.zip")
    if out.exists():
        raise RecipeError("BAD_PACK", f"refusing to overwrite the existing file {out}")
    project = load_project(project_path)
    d = find(project_path, project, name)
    _no_links(name, d.folder)              # never pack a link's target (a key file, say)
    files = files_of(d.folder)
    meta = {"folder_sha256": d.folder_sha256, "type": d.type,
            "version": d.manifest.get("version")}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            info = zipfile.ZipInfo(f"{d.folder.name}/{rel}", _ZIP_EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (d.folder / rel).read_bytes())
        zf.comment = json.dumps(meta, sort_keys=True).encode()
    return dict(meta, out=str(out), files=files)


# --- install -------------------------------------------------------------------------------------

def _unsafe(src: str, why: str) -> RecipeError:
    return RecipeError("UNSAFE_SOURCE", f"{src}: unsafe source: {why}; nothing installed")


def _extract_zip(src: str, dest: Path) -> str | None:
    """Extract `src` into `dest` after refusing every absolute, drive-letter, `..` or symlink
    member; return the folder hash the pack embedded, if any."""
    try:
        zf = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as e:
        raise RecipeError("BAD_SOURCE", f"{src}: not a readable zip: {e}")
    with zf:
        for info in zf.infolist():
            n = info.filename.replace("\\", "/")
            if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
                raise _unsafe(src, f"member {info.filename!r} has an absolute path")
            if ".." in PurePosixPath(n).parts:
                raise _unsafe(src, f"member {info.filename!r} climbs out with '..'")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise _unsafe(src, f"member {info.filename!r} is a symbolic link")
        zf.extractall(dest)
        try:
            meta = json.loads(zf.comment.decode()) if zf.comment else {}
        except (ValueError, UnicodeDecodeError):
            meta = {}
    sha = meta.get("folder_sha256") if isinstance(meta, dict) else None
    return sha if isinstance(sha, str) else None


def _clone(src: str, dest: Path) -> None:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", "clone", "--depth", "1", "--quiet", "--", src, str(dest)],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S, env=env,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise RecipeError("BAD_SOURCE", "installing from a git URL needs git on the PATH")
    except subprocess.TimeoutExpired:
        raise RecipeError("BAD_SOURCE", f"{src}: git clone did not finish in {GIT_TIMEOUT_S} s")
    if proc.returncode != 0:
        tail = " | ".join(proc.stderr.strip().splitlines()[-3:])
        raise RecipeError("BAD_SOURCE", f"{src}: git clone failed: {tail}")


def _op_root(src: str, root: Path) -> Path:
    if (root / "manifest.json").is_file():
        return root
    subs = [d for d in sorted(root.iterdir()) if d.is_dir()
            and not d.name.startswith((".", "__")) and (d / "manifest.json").is_file()]
    if len(subs) != 1:
        raise RecipeError("BAD_SOURCE", f"{src}: an op source must hold exactly one op folder "
                          f"(a manifest.json at its root or in exactly one subfolder); found "
                          f"{len(subs)}")
    return subs[0]


def _no_links(src: str, folder: Path) -> None:
    for p in folder.rglob("*"):
        if p.is_symlink() and not userop.ignored(p.relative_to(folder)):
            raise _unsafe(src, f"{p.relative_to(folder).as_posix()} is a symbolic link")


@dataclass
class Installed:
    type: str
    folder: Path
    folder_sha256: str
    source: str
    manifest: dict
    files: list

    def to_dict(self) -> dict:
        return {"type": self.type, "folder": str(self.folder),
                "folder_sha256": self.folder_sha256, "source": self.source,
                "manifest": self.manifest, "files": self.files}


def install(project_path: Path, src: str, replace: bool = False) -> Installed:
    """Copy one op folder from `src` (a folder, a `.zip`, or a git URL) into
    `<project dir>/ops/<manifest name>/` and record `{source, folder_sha256}` under the
    project's `user_op_sources`. Reads the manifest; never imports the op."""
    project_path = Path(project_path)
    project = load_project(project_path)
    ops_dir = _base(project_path) / "ops"
    with tempfile.TemporaryDirectory(prefix="fireflymcf-op-") as tmp:
        tmp = Path(tmp)
        p, expected = Path(src), None
        if p.is_dir():
            root = p
        elif p.is_file() and p.suffix.lower() == ".zip":
            root = tmp / "zip"
            root.mkdir()
            expected = _extract_zip(src, root)
        elif _GIT_URL.match(src):
            root = tmp / "git"
            _clone(src, root)
        else:
            raise RecipeError("BAD_SOURCE", f"{src!r} is not a folder, a .zip or a git URL "
                              "(https://, file:// or an SSH git@ URL)")
        op_root = _op_root(src, root)
        _no_links(src, op_root)
        m = userop._read_manifest(op_root)
        name = m["name"]
        if not _NAME.match(name):
            raise RecipeError("BAD_USER_OP", f"{src}: manifest name {name!r} is not a folder "
                              "name (letters, digits, '_', '-', '.')")
        dest = ops_dir / name
        if dest.exists() and not replace:
            raise RecipeError("OP_EXISTS", f"{dest} exists; pass --replace to overwrite it")
        try:
            others = userop.discover_user_ops(project_path, project)
        except RecipeError:
            others = []                  # a broken neighbour is the compile's to report
        clash = [d for d in others if d.type == m["type"] and d.folder != dest.resolve()]
        if clash:
            raise RecipeError("USER_OP_DUPLICATE", f"user op type {m['type']!r} is already "
                              f"defined in {clash[0].folder}")
        stage = Path(tempfile.mkdtemp(prefix=f".{name}.install-", dir=_mkdir(ops_dir)))
        try:
            shutil.rmtree(stage)
            shutil.copytree(op_root, stage, symlinks=True, ignore=lambda d, names: [
                n for n in names if n in userop.IGNORED or n.endswith(".pyc")])
            sha = userop.folder_hash(stage)
            if expected is not None and sha != expected:
                raise RecipeError("BAD_SOURCE", f"{src}: its files hash to {sha[:12]}..., not "
                                  f"the folder_sha256 {expected[:12]}... it was packed with; "
                                  "it changed after packing")
            project.user_op_sources = dict(project.user_op_sources,
                                           **{m["type"]: {"source": src, "folder_sha256": sha}})
            text = project_text(project_path, project)
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(stage, dest)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        write_project(project_path, text)
    return Installed(m["type"], dest, sha, src, m, files_of(dest))


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
