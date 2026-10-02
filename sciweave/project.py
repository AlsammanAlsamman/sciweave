"""The project model: graph.json, version store, history log, staleness.

Everything that changes a project goes through `Project`. The CLI, the
dashboard server, the plan protocol and the tests all use this one class, so
the rules (ids, versions, staleness, history) live in exactly one place.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import re
import shutil
from pathlib import Path

from sciweave.schema import (
    BRANCH_STATUSES,
    EDGE_RELATIONS,
    MODES,
    NODE_TYPES,
    PROJECT_FOLDERS,
    SCHEMA_VERSION,
    is_scalar_param,
    now_iso,
)

STATE_DIR = ".sciweave"
MAX_SNAPSHOT_BYTES = 200 * 1024 * 1024   # bigger managed files are fingerprinted, not stored
MAX_HASH_BYTES = 512 * 1024 * 1024       # bigger ref files use size+mtime fingerprint
MAX_DIR_ENTRIES = 20000
PROCESS_TYPES = {"pipeline", "step", "script"}
SOFT_RELS = {"documents", "related", "variant"}   # informational links: never propagate staleness


class SciWeaveError(Exception):
    """A user-facing error: the message is printed as-is by the CLI."""


# ---------------------------------------------------------------- hashing ----

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# full-content hashes are expensive (seconds per file over a network share); remember them by
# (path, size, mtime) so a file is only re-read when it actually changed. Persisted per project
# in .sciweave/fpcache.json (Project.load_fp_cache / save_fp_cache).
_FP_MEMO: dict[str, str] = {}
_FP_DIRTY = [False]


def _memo_key(path: Path, st) -> str:
    return f"{os.path.normcase(str(path))}|{st.st_size}|{st.st_mtime_ns}"


def fingerprint(path: Path) -> dict:
    """Identity of a file or directory *now*: {hash, size, kind, exact}.

    exact=False means the hash is a cheap size/mtime fingerprint (huge files,
    directories): good enough to notice change, not to restore content.
    """
    if path.is_dir():
        h = hashlib.sha256()
        total = 0
        count = 0
        for p in sorted(path.rglob("*")):
            if count >= MAX_DIR_ENTRIES:
                break
            if p.is_file():
                st = p.stat()
                total += st.st_size
                h.update(f"{p.relative_to(path).as_posix()}|{st.st_size}|{int(st.st_mtime)}\n".encode())
                count += 1
        return {"hash": "dir:" + h.hexdigest(), "size": total, "kind": "dir", "exact": False}
    st = path.stat()
    if st.st_size > MAX_HASH_BYTES:
        raw = f"{st.st_size}|{int(st.st_mtime)}".encode()
        return {"hash": "fp:" + hashlib.sha256(raw).hexdigest(), "size": st.st_size, "kind": "file",
                "exact": False, "mtime": st.st_mtime_ns}
    key = _memo_key(path, st)
    # a file modified in the last seconds may change again within the same timestamp (same size, same
    # mtime): never trust or store a remembered hash for it ("racy" files, as git calls them)
    fresh = time.time() - st.st_mtime < 3
    digest = None if fresh else _FP_MEMO.get(key)
    if digest is None:
        digest = sha256_file(path)
        if not fresh:
            _FP_MEMO[key] = digest
            _FP_DIRTY[0] = True
    return {"hash": digest, "size": st.st_size, "kind": "file", "exact": True, "mtime": st.st_mtime_ns}


def unchanged_since(path: Path, version: dict) -> bool:
    """True if the file on disk still matches `version`. Cheap path first: same
    size and mtime as recorded -> unchanged without re-hashing (matters when a
    project tracks many GB of summary statistics)."""
    if path.is_file() and version.get("mtime") is not None:
        st = path.stat()
        if st.st_size == version.get("size") and st.st_mtime_ns == version["mtime"]:
            return True
    return fingerprint(path)["hash"] == version["hash"]


# ---------------------------------------------------------------- project ----

class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state = self.root / STATE_DIR
        self.graph_file = self.state / "graph.json"
        self.history_file = self.state / "history.jsonl"
        self.objects = self.state / "objects"
        self.plans_dir = self.state / "plans"
        self.graph: dict = {}
        if self.graph_file.exists():
            self.load()
            self.load_fp_cache()

    def load_fp_cache(self) -> None:
        f = self.state / "fpcache.json"
        if f.exists():
            try:
                _FP_MEMO.update(json.loads(f.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass

    def save_fp_cache(self) -> None:
        if not _FP_DIRTY[0]:
            return
        f = self.state / "fpcache.json"
        try:
            keep = {k: v for k, v in _FP_MEMO.items()}
            tmp = f.with_name(f"fpcache.json.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(keep), encoding="utf-8")
            self._retry(lambda: os.replace(tmp, f))
            _FP_DIRTY[0] = False
        except OSError:
            pass

    # ---------------------------------------------------------- lifecycle ----
    @classmethod
    def init(cls, root: Path, name: str | None = None, description: str = "", bare: bool = False) -> "Project":
        """bare=True adopts an existing project in place: only .sciweave/ and
        SCIWEAVE.md are added, no data/results/... folders are created."""
        root = Path(root).resolve()
        if (root / STATE_DIR / "graph.json").exists():
            raise SciWeaveError(f"{root} is already a SciWeave project")
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("objects", "plans"):
            (root / STATE_DIR / sub).mkdir(parents=True, exist_ok=True)
        if not bare:
            for folder in PROJECT_FOLDERS:
                (root / folder).mkdir(parents=True, exist_ok=True)
        p = cls(root)
        p.graph = {
            "schema": SCHEMA_VERSION,
            "project": {"name": name or root.name, "description": description, "created": now_iso()},
            "counters": {},
            "nodes": {},
            "edges": [],
            "groups": {},
            "steps": {},
            "analyses": {},
        }
        p.save_graph()
        p.log("project_created", detail=p.graph["project"]["name"])
        p.write_map()
        return p

    @classmethod
    def find(cls, start: Path | None = None) -> "Project":
        here = Path(start or os.getcwd()).resolve()
        for d in [here, *here.parents]:
            if (d / STATE_DIR / "graph.json").exists():
                return cls(d)
        raise SciWeaveError(
            f"no SciWeave project found at or above {here} (run `sciweave init` first, or pass --project)"
        )

    @staticmethod
    def _retry(fn, attempts: int = 40, wait: float = 0.05):
        """Windows: replacing or reading graph.json fails with PermissionError for a moment while
        another thread / process has it open (the dashboard reads it constantly). Retry briefly."""
        import time
        for i in range(attempts):
            try:
                return fn()
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(wait)

    def load(self) -> None:
        self.graph = json.loads(self._retry(lambda: self.graph_file.read_text(encoding="utf-8")))
        self.graph.setdefault("counters", {})
        self.graph.setdefault("groups", {})
        self.graph.setdefault("steps", {})
        self.graph.setdefault("analyses", {})

    def save_graph(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        import threading
        # one temp file per writer (process + thread), so two saves at once never share it
        tmp = self.graph_file.with_name(f"graph.json.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self.graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.graph_file.exists():
            self._retry(lambda: shutil.copy2(self.graph_file, self.graph_file.with_suffix(".json.bak")))
        self._retry(lambda: os.replace(tmp, self.graph_file))

    def commit(self) -> None:
        """Persist graph + regenerate SCIWEAVE.md. Call after any mutation.
        Organized copies follow their object's step / group first."""
        if self.destination is not None:
            self.sync_organized()
        self.save_graph()
        self.write_map()

    # ------------------------------------------------------------ access ----
    @property
    def nodes(self) -> dict:
        return self.graph["nodes"]

    @property
    def edges(self) -> list:
        return self.graph["edges"]

    def node(self, node_id: str) -> dict:
        n = self.nodes.get(node_id)
        if n is None:
            raise SciWeaveError(f"unknown node '{node_id}' (see `sciweave ls`)")
        return n

    def resolve(self, path_str: str | None) -> Path | None:
        if not path_str:
            return None
        p = Path(path_str)
        return p if p.is_absolute() else self.root / p

    def rel(self, path: Path) -> str:
        path = Path(path).resolve()
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def next_id(self, node_type: str) -> str:
        prefix = NODE_TYPES[node_type]["prefix"]
        n = self.graph["counters"].get(prefix, 0) + 1
        while f"{prefix}{n}" in self.nodes:
            n += 1
        self.graph["counters"][prefix] = n
        return f"{prefix}{n}"


    # ---------------------------------------------------------- organize ----
    # The organized copy: the project's *destination* folder (e.g. D:\study) mirrors the
    # network. Each object can be copied there ("save to destination"); its folder is
    #     <destination>/<NN_step label>/[<custom group>/]<name>
    # so the folder tree follows the network's grouping, and when an object moves to
    # another step or group its copy is moved too (on every commit). The record keeps
    # the source and its fingerprint at copy time, so a later change of the source is
    # reported ("source changed") and can be pulled in by copying again.

    BIG_BYTES = 4 * 1024 ** 3  # "large data": asked one by one before copying, never copied by "save all"

    def node_size(self, node_id: str) -> int | None:
        v = self.node(node_id).get("versions") or []
        return v[-1].get("size") if v else None

    def is_big(self, node_id: str) -> bool:
        return (self.node_size(node_id) or 0) > self.BIG_BYTES

    def set_organize_skip(self, node_id: str, skip: bool = True, actor: str = "user") -> None:
        """A "no" to copying a large object during "save all": remembered, so it is not asked again
        (it can still be copied on its own at any time)."""
        n = self.node(node_id)
        if skip:
            n["organize_skip"] = True
        else:
            n.pop("organize_skip", None)
        self.log("organize_skip" if skip else "organize_unskip", node=node_id, actor=actor)

    @property
    def destination(self) -> Path | None:
        d = self.graph["project"].get("destination")
        return Path(d) if d else None

    def set_destination(self, path: str | None, actor: str = "user") -> Path | None:
        if path:
            dest = Path(str(path).strip().strip('"')).expanduser()
            if not dest.is_absolute():
                raise SciWeaveError("give the destination as a full path, e.g. D:\\my_study")
            dest.mkdir(parents=True, exist_ok=True)
            self.graph["project"]["destination"] = str(dest)
        else:
            self.graph["project"].pop("destination", None)
        self.log("destination_set", detail=str(path or "(none)"), actor=actor)
        return self.destination

    @staticmethod
    def _folder_name(text: str) -> str:
        s = re.sub(r"[^\w\-]+", "_", text.strip()).strip("_")
        return s[:60] or "item"

    def organized_folder(self, node_id: str) -> str:
        """Relative folder inside the destination that matches the node's grouping now."""
        n = self.node(node_id)
        st = self.steps.get(n.get("step") or "")
        parts = [f"{int(st.get('order', 0)):02d}_{self._folder_name(st['label'])}" if st else "00_No_step"]
        if n.get("groups"):
            g = n["groups"][0]
            parts.append(self._folder_name((self.graph["groups"].get(g) or {}).get("label") or g))
        return "/".join(parts)

    def _organized_name(self, node_id: str, src: Path) -> str:
        n = self.node(node_id)
        name = f"{node_id}_{self._folder_name(n['label'])}" if src.is_dir() else src.name
        folder = self.organized_folder(node_id)
        taken = {(o.get("organized") or {}).get("path") for k, o in self.nodes.items() if k != node_id}
        if f"{folder}/{name}" in taken:  # two objects with the same file name in one folder
            name = f"{node_id}_{name}"
        return name

    def organize_copy(self, node_id: str, progress=None) -> dict:
        """Copy (or re-copy) one object into the destination. Returns the record; the caller
        stores it with organize_record() (split so a long copy can run without a lock)."""
        dest = self.destination
        if dest is None:
            raise SciWeaveError("no destination set for this project (sciweave destination <folder>)")
        n = self.node(node_id)
        if not n.get("path"):
            raise SciWeaveError(f"{node_id} has no file to copy (a conceptual object)")
        src = self.resolve(n["path"])
        if not src.exists():
            raise SciWeaveError(f"source not reachable: {src}")
        old = n.get("organized") or {}
        name = old.get("name") or self._organized_name(node_id, src)
        rel = f"{self.organized_folder(node_id)}/{name}"
        target = dest / rel
        try:
            target.resolve().relative_to(src.resolve())
            raise SciWeaveError(f"the destination lies inside the source {src}; choose another destination")
        except ValueError:
            pass
        if old.get("path") and old["path"] != rel and (dest / old["path"]).exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest / old["path"]), str(target))
            if old.get("sidecar") and (dest / old["sidecar"]).exists():
                shutil.move(str(dest / old["sidecar"]), str(self._sidecar_of(target)))
        target.parent.mkdir(parents=True, exist_ok=True)
        fp = fingerprint(src)
        lines = []
        files = sorted(x for x in src.rglob("*") if x.is_file()) if src.is_dir() else [src]
        total = 2 * sum(x.stat().st_size for x in files)  # every byte is read twice: copy + check
        done = [0]

        def tick(nbytes, phase):
            done[0] += nbytes
            if progress:
                progress(done[0], total, phase)
        if progress:
            progress(0, total, "copying")
        stale = target.with_name(target.name + ".sciweave-part")  # leftovers of an interrupted copy
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        elif stale.exists():
            stale.unlink()
        if src.is_dir():
            # copy into a temporary folder, verify every file, then swap it in (files removed at the source go too)
            tmp = target.with_name(target.name + ".sciweave-part")
            if tmp.exists():
                shutil.rmtree(tmp)
            for f in files:
                rel_f = f.relative_to(src).as_posix()
                (tmp / rel_f).parent.mkdir(parents=True, exist_ok=True)
                lines.append(f"{self._copy_verified(f, tmp / rel_f, tick)}  {target.name}/{rel_f}")
            if target.exists():
                old_dir = target.with_name(target.name + ".sciweave-old")
                os.replace(target, old_dir)
                os.replace(tmp, target)
                shutil.rmtree(old_dir, ignore_errors=True)
            else:
                os.replace(tmp, target)
        else:
            lines.append(f"{self._copy_verified(src, target, tick)}  {target.name}")
        manifest = "\n".join(lines) + "\n"
        side = self._sidecar_of(target)
        side.write_text(manifest, encoding="utf-8")  # sha256sum format: check with `sha256sum -c` from its folder
        return {"path": rel, "name": name, "source": str(src), "source_hash": fp["hash"], "size": fp["size"],
                "source_mtime": src.stat().st_mtime_ns if src.is_file() else None,
                "kind": fp["kind"], "copied": now_iso(), "sidecar": f"{self.organized_folder(node_id)}/{side.name}",
                "sha256": hashlib.sha256(manifest.encode()).hexdigest(), "files": len(lines), "verified": now_iso()}

    @staticmethod
    def _sidecar_of(target: Path) -> Path:
        return target.with_name(target.name + ".sha256")

    @staticmethod
    def _hash_file(path: Path, tick=None, phase: str = "checking") -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                h.update(chunk)
                if tick:
                    tick(len(chunk), phase)
        return h.hexdigest()

    @staticmethod
    def _copy_verified(src: Path, dst: Path, tick=None) -> str:
        """Copy one file while hashing the source, re-read the copy and compare; only a copy whose
        SHA-256 equals the source's replaces the old one. Returns the SHA-256."""
        tmp = dst.with_name(dst.name + ".sciweave-part")
        h = hashlib.sha256()
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            for chunk in iter(lambda: fi.read(8 * 1024 * 1024), b""):
                h.update(chunk)
                fo.write(chunk)
                if tick:
                    tick(len(chunk), "copying")
        shutil.copystat(src, tmp)
        digest = h.hexdigest()
        if Project._hash_file(tmp, tick, "checking") != digest:
            tmp.unlink(missing_ok=True)
            raise SciWeaveError(f"the copy of {src.name} does not match its source (SHA-256) and was not saved")
        os.replace(tmp, dst)
        return digest

    @staticmethod
    def _total_size(p: Path) -> tuple[int, int]:
        if p.is_file():
            return p.stat().st_size, 1
        if p.is_dir():
            fs = [f for f in p.rglob("*") if f.is_file()]
            return sum(f.stat().st_size for f in fs), len(fs)
        return 0, 0

    def organized_check(self, node_id: str) -> dict:
        """Quick check before saving again: sizes of source and copy (cheap), and whether the source
        changed since it was copied. No file content is read."""
        n = self.node(node_id)
        rec = n.get("organized")
        if not rec:
            return {"copied": False}
        dst = self.destination / rec["path"] if self.destination else None
        src = Path(rec["source"])
        ss, sn = self._total_size(src)
        ds, dn = self._total_size(dst) if dst else (0, 0)
        state = self.organized_state(node_id)
        return {"copied": True, "state": state, "source_size": ss, "copy_size": ds, "source_files": sn, "copy_files": dn,
                "same_size": ss == ds and sn == dn, "has_checksum": bool(rec.get("sidecar")),
                "copied_at": rec.get("copied"), "verified_at": rec.get("verified"),
                "up_to_date": state == "ok" and ss == ds and sn == dn}

    def verify_copy(self, node_id: str, progress=None) -> dict:
        """Re-read the copy at the destination and check it against its checksum file. A copy made
        before checksums existed is compared with its source instead, and gets a checksum file."""
        n = self.node(node_id)
        rec = n.get("organized")
        if not rec or self.destination is None:
            raise SciWeaveError(f"{node_id} has no organized copy")
        dest = self.destination
        target = dest / rec["path"]
        if not target.exists():
            return {"ok": False, "checked": 0, "bad": [], "missing": [rec["path"]], "message": "the copy is missing"}
        side = dest / rec["sidecar"] if rec.get("sidecar") else None
        done = [0]

        def tick(nbytes, phase):
            done[0] += nbytes
            if progress:
                progress(done[0], total[0], phase)
        total = [sum(f.stat().st_size for f in ([target] if target.is_file() else [x for x in target.rglob("*") if x.is_file()]))]
        if side and side.exists():
            bad, missing, checked = [], [], 0
            for line in side.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                digest, _, name = line.partition("  ")
                f = target.parent / name
                if not f.exists():
                    missing.append(name)
                    continue
                checked += 1
                if self._hash_file(f, tick) != digest:
                    bad.append(name)
            ok = not bad and not missing
            return {"ok": ok, "checked": checked, "bad": bad, "missing": missing, "wrote_checksum": False,
                    "message": f"{checked} file(s) match the checksum" if ok
                    else f"{len(bad)} changed, {len(missing)} missing (of {checked + len(missing)})"}
        # older copy without a checksum file: compare with the source, then write one
        src = Path(rec["source"])
        pairs = ([(src, target, target.name)] if target.is_file() else
                 [(s, target / s.relative_to(src), f"{target.name}/{s.relative_to(src).as_posix()}")
                  for s in sorted(x for x in src.rglob("*") if x.is_file())])
        lines, bad, missing = [], [], []
        for s, d, name in pairs:
            if not d.exists():
                missing.append(name)
                continue
            hd = self._hash_file(d, tick)
            if not s.exists() or sha256_file(s) != hd:
                bad.append(name)
            lines.append(f"{hd}  {name}")
        ok = not bad and not missing
        out = {"ok": ok, "checked": len(lines), "bad": bad, "missing": missing, "wrote_checksum": False}
        if ok:
            manifest = "\n".join(lines) + "\n"
            sc = self._sidecar_of(target)
            sc.write_text(manifest, encoding="utf-8")
            out.update(wrote_checksum=True, sidecar=f"{self.organized_folder(node_id)}/{sc.name}",
                       sha256=hashlib.sha256(manifest.encode()).hexdigest(),
                       message=f"{len(lines)} file(s) identical to the source; checksum file written")
        else:
            out["message"] = f"differs from the source: {len(bad)} changed, {len(missing)} missing"
        return out

    def record_verification(self, node_id: str, res: dict, actor: str = "user") -> None:
        rec = self.node(node_id).get("organized") or {}
        if res.get("ok"):
            rec["verified"] = now_iso()
            if res.get("wrote_checksum"):
                rec["sidecar"], rec["sha256"], rec["files"] = res["sidecar"], res["sha256"], res["checked"]
        self.log("organized_verified" if res.get("ok") else "organized_verify_failed", node=node_id,
                 detail=res.get("message", ""), actor=actor)

    def organize_record(self, node_id: str, rec: dict, actor: str = "user") -> None:
        n = self.node(node_id)
        again = bool(n.get("organized"))
        n["organized"] = rec
        n.pop("organize_skip", None)  # copied after all: no longer skipped
        self.log("organized_copy", node=node_id, detail=("updated " if again else "") + rec["path"], actor=actor)

    def organize(self, node_id: str, actor: str = "user") -> dict:
        rec = self.organize_copy(node_id)
        self.organize_record(node_id, rec, actor=actor)
        return rec

    def sync_organized(self) -> list[tuple[str, str, str]]:
        """Move organized copies whose step / group changed so the folders keep matching
        the network. Returns [(id, old, new)]; empty folders left behind are removed."""
        dest = self.destination
        moved = []
        if dest is None:
            return moved
        for nid, n in self.nodes.items():
            rec = n.get("organized")
            if not rec:
                continue
            want = f"{self.organized_folder(nid)}/{rec['name']}"
            if want == rec["path"]:
                continue
            src, dst = dest / rec["path"], dest / want
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                if rec.get("sidecar") and (dest / rec["sidecar"]).exists():
                    shutil.move(str(dest / rec["sidecar"]), str(self._sidecar_of(dst)))
                old_parent = src.parent
                while old_parent != dest and old_parent.exists() and not any(old_parent.iterdir()):
                    old_parent.rmdir()
                    old_parent = old_parent.parent
            moved.append((nid, rec["path"], want))
            rec["path"] = want
            if rec.get("sidecar"):
                rec["sidecar"] = f"{self.organized_folder(nid)}/{self._sidecar_of(Path(want)).name}"
        for nid, a, b in moved:
            self.log("organized_moved", node=nid, detail=f"{a} -> {b}", actor="sciweave")
        return moved

    def organized_state(self, node_id: str) -> str | None:
        """ok | source_changed | copy_missing | source_missing, or None if never copied."""
        n = self.node(node_id)
        rec = n.get("organized")
        if not rec:
            return None
        if self.destination is None or not (self.destination / rec["path"]).exists():
            return "copy_missing"
        src = Path(rec["source"])
        if not src.exists():
            return "source_missing"
        if rec.get("source_mtime") and src.is_file():  # cheap: same size and time as when copied = unchanged
            st = src.stat()
            if st.st_size == rec.get("size") and st.st_mtime_ns == rec["source_mtime"]:
                return "ok"
        return "ok" if fingerprint(src)["hash"] == rec["source_hash"] else "source_changed"

    # ---------------------------------------------------------- relocate ----
    def relocate(self, old: str, new: str, dry_run: bool = False, actor: str = "user") -> list[tuple[str, str, str]]:
        """Data moved (e.g. C:\\...\\study -> D:\\study): rewrite every node path and analysis path
        that starts with `old` to start with `new`. Slash style and (on Windows) case are ignored
        when matching; the rest of each path is kept. Returns [(where, before, after)]."""
        def norm(s: str) -> str:
            s = s.replace("\\", "/").rstrip("/")
            return s.lower() if os.name == "nt" else s
        o, nw = norm(old), new.replace("\\", "/").rstrip("/")

        def move(path: str):
            if not isinstance(path, str):
                return None
            p = path.replace("\\", "/")
            if norm(p) == o or norm(p).startswith(o + "/"):
                return nw + p[len(o):]
            return None
        changes = []
        for nid, n in self.nodes.items():
            to = move(n.get("path"))
            if to:
                changes.append((nid, n["path"], to))
                if not dry_run:
                    n["path"] = to
        for k, a in self.analyses.items():
            for i, ap in enumerate(a.get("paths") or []):
                to = move(ap)
                if to:
                    changes.append((f"analysis {k}", ap, to))
                    if not dry_run:
                        a["paths"][i] = to
        if changes and not dry_run:
            self.log("relocated", detail=f"{old} -> {new}: {len(changes)} path(s)", actor=actor)
        return changes

    # ----------------------------------------------------------- history ----
    def log(self, event: str, node: str | None = None, edge: str | None = None,
            detail: str = "", actor: str = "user", **extra) -> dict:
        rec = {"ts": now_iso(), "event": event, "actor": actor}
        if node:
            rec["node"] = node
        if edge:
            rec["edge"] = edge
        if detail:
            rec["detail"] = detail
        rec.update({k: v for k, v in extra.items() if v is not None})
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def history(self, node: str | None = None, limit: int | None = None) -> list[dict]:
        if not self.history_file.exists():
            return []
        out = []
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if node and rec.get("node") != node and node not in rec.get("nodes", []):
                continue
            out.append(rec)
        return out[-limit:] if limit else out

    # ------------------------------------------------------------- nodes ----
    def add_node(self, node_type: str, label: str, path: str | None = None, mode: str = "ref",
                 dest: str | None = None, description: str = "", groups=(), tags=(),
                 meta: dict | None = None, node_id: str | None = None, actor: str = "user",
                 message: str = "added", history: list | None = None, step: str | None = None) -> dict:
        """history: older copies of this item, oldest first, as
        [{"path": ..., "ts": "2026-09-04T18:19:00+00:00", "message": ...}]. They become
        v1..vN (content stored), and the current file becomes the latest version."""
        if node_type not in NODE_TYPES:
            raise SciWeaveError(f"unknown node type '{node_type}' (one of: {', '.join(NODE_TYPES)})")
        if mode not in MODES:
            raise SciWeaveError(f"mode must be one of {MODES}, got '{mode}'")
        if node_id and node_id in self.nodes:
            raise SciWeaveError(f"node id '{node_id}' already exists")
        nid = node_id or self.next_id(node_type)

        stored_path = None
        if path:
            src = Path(path).expanduser()
            if not src.is_absolute():
                src = (Path.cwd() / src)
            if not src.exists():
                raise SciWeaveError(f"path does not exist: {src}")
            src = src.resolve()
            if mode == "managed":
                inside = self._is_inside(src)
                if dest:
                    target = self.resolve(dest)
                elif inside:
                    target = src
                else:
                    target = self.root / NODE_TYPES[node_type]["folder"] / src.name
                target = Path(target).resolve()
                if target != src:
                    if target.exists():
                        raise SciWeaveError(f"destination already exists: {self.rel(target)} (choose --dest)")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_dir():
                        shutil.copytree(src, target)
                    else:
                        shutil.copy2(src, target)
                stored_path = self.rel(target)
                meta = {**(meta or {}), **({"origin": str(src)} if target != src else {})}
            else:
                stored_path = self.rel(src)

        node = {
            "id": nid,
            "type": node_type,
            "label": label,
            "path": stored_path,
            "mode": mode,
            "description": description,
            "step": self._ensure_step(step, actor) if step else None,
            "groups": list(groups),
            "tags": list(tags),
            "meta": meta or {},
            "created": now_iso(),
            "updated": now_iso(),
            "versions": [],
            "current_version": None,
            "final_version": None,
            "notes": [],
        }
        self.nodes[nid] = node
        for g in node["groups"]:
            self.graph["groups"].setdefault(g, {"label": g, "color": None})
        self.log("node_added", node=nid, detail=f"{node_type} '{label}'", actor=actor,
                 path=stored_path, mode=mode)
        for h in history or []:
            self._historical_version(node, Path(h["path"]).expanduser(), h.get("ts"), h.get("message", ""))
        if stored_path:
            self._new_version(node, message=message, actor=actor)
        return node

    def _historical_version(self, node: dict, src: Path, ts: str | None, message: str) -> dict | None:
        if not src.exists() or not src.is_file():
            raise SciWeaveError(f"{node['id']}: history file not found: {src}")
        fp = fingerprint(src)
        last = node["versions"][-1] if node["versions"] else None
        if last and last["hash"] == fp["hash"]:
            return None
        stored = self._store_object(src, fp["hash"]) if fp["exact"] and fp["size"] <= MAX_SNAPSHOT_BYTES else None
        v = {"v": (last["v"] + 1) if last else 1, "ts": ts or now_iso(), "hash": fp["hash"], "size": fp["size"],
             "kind": fp["kind"], "stored": stored, "message": message or f"historical copy: {src.name}",
             "actor": "import", "parents": {}, "edge_params": {}, "source_file": str(src)}
        node["versions"].append(v)
        node["current_version"] = v["v"]
        self.log("version_imported", node=node["id"], detail=f"v{v['v']} from {src.name}", actor="import",
                 version=v["v"])
        return v

    def update_node(self, node_id: str, actor: str = "user", **fields) -> dict:
        node = self.node(node_id)
        allowed = {"label", "description", "tags", "groups", "meta", "path"}
        if fields.get("step") is not None:
            fields["step"] = self._ensure_step(fields["step"], actor)
            allowed.add("step")
        changed = []
        for k, v in fields.items():
            if v is None or k not in allowed:
                continue
            if k == "meta":
                node["meta"].update(v)
            else:
                node[k] = v
            changed.append(k)
        for g in node["groups"]:
            self.graph["groups"].setdefault(g, {"label": g, "color": None})
        if changed:
            node["updated"] = now_iso()
            self.log("node_updated", node=node_id, detail=", ".join(changed), actor=actor)
        return node

    def remove_node(self, node_id: str, actor: str = "user") -> None:
        self.node(node_id)
        del self.nodes[node_id]
        removed = [e["id"] for e in self.edges if node_id in (e["source"], e["target"])]
        self.graph["edges"] = [e for e in self.edges if node_id not in (e["source"], e["target"])]
        self.log("node_removed", node=node_id, actor=actor, detail=f"and {len(removed)} edge(s)")

    def _is_inside(self, p: Path) -> bool:
        try:
            p.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    # ---------------------------------------------------------- versions ----
    def _store_object(self, path: Path, digest: str) -> str:
        rel = f"{digest[:2]}/{digest[2:]}"
        dst = self.objects / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)
        return rel

    def _changes_since(self, last: dict | None, parents: dict, edge_params: dict, fp: dict) -> list[dict]:
        """What differs from the previous version: inputs / scripts that moved to a new
        version, inputs added or dropped, parameters changed on incoming links, file size."""
        if not last:
            return []
        out: list[dict] = []
        before = last.get("parents", {})
        for pid, ver in parents.items():
            kind = "script" if self.nodes.get(pid, {}).get("type") in PROCESS_TYPES else "input"
            if pid not in before:
                out.append({"kind": "added_" + kind, "node": pid, "to": ver})
            elif before[pid] != ver:
                out.append({"kind": kind, "node": pid, "from": before[pid], "to": ver})
        for pid, ver in before.items():
            if pid not in parents:
                out.append({"kind": "dropped_input", "node": pid, "from": ver})
        old_params = last.get("edge_params", {})
        for eid, params in edge_params.items():
            prev = old_params.get(eid, {})
            src = next((e["source"] for e in self.edges if e["id"] == eid), None)
            for k in sorted(set(prev) | set(params)):
                if prev.get(k) != params.get(k):
                    out.append({"kind": "param", "edge": eid, "source": src, "key": k,
                                "from": prev.get(k), "to": params.get(k)})
        if last.get("size") is not None and fp["size"] != last["size"]:
            out.append({"kind": "content", "from": last["size"], "to": fp["size"]})
        return out

    def _new_version(self, node: dict, message: str = "", actor: str = "user",
                     refresh_edges: bool = True, why: str = "") -> dict | None:
        path = self.resolve(node["path"])
        if path is None or not path.exists():
            raise SciWeaveError(f"{node['id']}: file is missing on disk ({node['path']})")
        fp = fingerprint(path)
        last = node["versions"][-1] if node["versions"] else None
        if last and last["hash"] == fp["hash"]:
            return None
        stored = None
        if node["mode"] == "managed" and fp["kind"] == "file" and fp["exact"] and fp["size"] <= MAX_SNAPSHOT_BYTES:
            stored = self._store_object(path, fp["hash"])
        if refresh_edges or last is None:
            parents = {pid: self.nodes[pid]["current_version"] for pid in self.effective_parents(node["id"])}
            for e in self.incoming(node["id"]):
                if e["rel"] != "part_of" and e["source"] in self.nodes:
                    e["source_version"] = self.nodes[e["source"]]["current_version"]
        else:  # cosmetic change: still built from the same inputs as before
            parents = dict(last.get("parents", {}))
        edge_params = {e["id"]: dict(e["params"]) for e in self.incoming(node["id"]) if e.get("params")}
        v = {
            "v": (last["v"] + 1) if last else 1,
            "ts": now_iso(),
            "why": why,
            "changes": self._changes_since(last, parents, edge_params, fp),
            "hash": fp["hash"],
            "size": fp["size"],
            "mtime": fp.get("mtime"),
            "kind": fp["kind"],
            "stored": stored,
            "message": message,
            "actor": actor,
            "parents": parents,
            "edge_params": edge_params,
        }
        node["versions"].append(v)
        node["current_version"] = v["v"]
        node["updated"] = v["ts"]
        return v

    def save_version(self, node_id: str, message: str = "", actor: str = "user",
                     refresh_edges: bool = True, why: str = "") -> dict | None:
        """Snapshot the node's file as a new version. None if unchanged.

        message = what changed (short); why = the reason, kept with the version and shown
        in the dashboard. The concrete differences (inputs, scripts, params, size) are
        computed automatically into version["changes"]."""
        node = self.node(node_id)
        if not node["path"]:
            raise SciWeaveError(f"{node_id} has no file path to version")
        v = self._new_version(node, message=message, actor=actor, refresh_edges=refresh_edges, why=why)
        if v:
            self.log("version_saved", node=node_id, detail=message or f"v{v['v']}", actor=actor,
                     version=v["v"], why=why or None, changes=len(v["changes"]) or None)
        return v

    def mark_final(self, node_id: str, version: int | None = None, actor: str = "user") -> int:
        node = self.node(node_id)
        if not node["versions"]:
            raise SciWeaveError(f"{node_id} has no versions yet (save it first)")
        v = version or node["current_version"]
        if not any(x["v"] == v for x in node["versions"]):
            raise SciWeaveError(f"{node_id} has no version {v}")
        node["final_version"] = v
        self.log("marked_final", node=node_id, detail=f"v{v}", actor=actor, version=v)
        return v

    def get_version(self, node_id: str, version: int) -> dict:
        for v in self.node(node_id)["versions"]:
            if v["v"] == version:
                return v
        raise SciWeaveError(f"{node_id} has no version {version}")

    def object_path(self, version: dict) -> Path | None:
        return (self.objects / version["stored"]) if version.get("stored") else None

    def restore(self, node_id: str, version: int, out: str | None = None,
                force: bool = False, actor: str = "user") -> Path:
        node = self.node(node_id)
        v = self.get_version(node_id, version)
        obj = self.object_path(v)
        if obj is None or not obj.exists():
            raise SciWeaveError(
                f"{node_id} v{version} has no stored content (ref nodes and very large files are "
                f"fingerprinted only)")
        target = Path(out).resolve() if out else self.resolve(node["path"])
        if out is None and target.exists() and not force:
            cur = fingerprint(target)
            last = node["versions"][-1]
            if cur["hash"] != last["hash"]:
                raise SciWeaveError(
                    f"{node['path']} has unsaved changes; `sciweave save {node_id}` first or use --force")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(obj, target)
        if out is None:
            self._new_version(node, message=f"restored from v{version}", actor=actor, refresh_edges=False)
        self.log("restored", node=node_id, detail=f"v{version} -> {self.rel(target)}", actor=actor,
                 version=version)
        return target

    # ------------------------------------------------------------- edges ----
    def incoming(self, node_id: str) -> list[dict]:
        return [e for e in self.edges if e["target"] == node_id]

    def outgoing(self, node_id: str) -> list[dict]:
        return [e for e in self.edges if e["source"] == node_id]

    def find_edge(self, source: str, target: str, rel: str | None = None) -> dict | None:
        for e in self.edges:
            if e["source"] == source and e["target"] == target and (rel is None or e["rel"] == rel):
                return e
        return None

    def link(self, source: str, target: str, rel: str = "feeds", params: dict | None = None,
             script: str | None = None, command: str | None = None, note: str = "",
             label: str | None = None, params_file: str | None = None, actor: str = "user",
             why: str = "") -> dict:
        if rel not in EDGE_RELATIONS:
            raise SciWeaveError(f"unknown relation '{rel}' (one of: {', '.join(EDGE_RELATIONS)})")
        src, dst = self.node(source), self.node(target)
        if source == target:
            raise SciWeaveError("a node cannot link to itself")
        if self._reaches(target, source):
            raise SciWeaveError(f"linking {source} -> {target} would create a cycle")
        params = params or {}
        bad = [k for k, v in params.items() if not is_scalar_param(v)]
        if bad:
            raise SciWeaveError(f"parameters must be scalars or lists of scalars: {', '.join(bad)}")
        if script:
            sc = self.node(script)
            if sc["type"] not in ("script", "pipeline", "step"):
                raise SciWeaveError(f"--script must point to a script/pipeline/step node, {script} is a {sc['type']}")
        pf = None
        if params_file:
            pf_path = Path(params_file).expanduser().resolve()
            if not pf_path.exists():
                raise SciWeaveError(f"params file not found: {pf_path}")
            fp = fingerprint(pf_path)
            stored = self._store_object(pf_path, fp["hash"]) if fp["exact"] and fp["kind"] == "file" else None
            pf = {"path": self.rel(pf_path), "hash": fp["hash"], "stored": stored}

        existing = self.find_edge(source, target, rel)
        if existing:
            e = existing
            old = dict(e.get("params", {}))
            e["params"] = {**old, **params}
            diff = {k: [old.get(k), v] for k, v in params.items() if old.get(k) != v}
            if diff:
                e.setdefault("changes", []).append({"ts": now_iso(), "actor": actor, "why": why, "params": diff})
            for k, v in (("command", command), ("note", note or None), ("label", label), ("params_file", pf)):
                if v:
                    e[k] = v
            if script:
                e["script"] = script
                e["script_version"] = self.nodes[script]["current_version"]
            e["updated"] = now_iso()
            self.log("edge_updated", edge=e["id"], detail=f"{source} -{rel}-> {target}", actor=actor,
                     nodes=[source, target], params=params or None, why=why or None)
        else:
            self.graph["counters"]["E"] = self.graph["counters"].get("E", 0) + 1
            e = {
                "id": f"E{self.graph['counters']['E']}",
                "source": source,
                "target": target,
                "rel": rel,
                "params": params,
                "script": script,
                "script_version": self.nodes[script]["current_version"] if script else None,
                "command": command,
                "params_file": pf,
                "label": label,
                "note": note,
                "source_version": src["current_version"],
                "created": now_iso(),
                "updated": now_iso(),
            }
            self.edges.append(e)
            self.log("edge_added", edge=e["id"], detail=f"{source} -{rel}-> {target}", actor=actor,
                     nodes=[source, target], params=params or None, why=why or None)
        if script and not self.find_edge(script, target) and script != source:
            self.link(script, target, rel="code", actor=actor)
        affected = self.process_outputs(target) if dst["type"] in PROCESS_TYPES else [target]
        for nid in affected:
            self._adopt_parents(nid)
        dst["updated"] = now_iso()
        return e

    def unlink(self, edge_id: str, actor: str = "user") -> None:
        before = len(self.edges)
        self.graph["edges"] = [e for e in self.edges if e["id"] != edge_id]
        if len(self.edges) == before:
            raise SciWeaveError(f"unknown edge '{edge_id}'")
        self.log("edge_removed", edge=edge_id, actor=actor)

    def _reaches(self, start: str, goal: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(e["target"] for e in self.outgoing(cur))
        return False

    # --------------------------------------------------------------- steps ----
    # A step is a stage of the analysis (GWAS, meta-analysis, fine-mapping, ...,
    # article writing). Every object belongs to one step; steps have an order, so the
    # network can show each step as an oval and the links within / between steps.

    @property
    def steps(self) -> dict:
        return self.graph.setdefault("steps", {})

    def define_step(self, key: str, label: str | None = None, order: int | None = None,
                    description: str = "", actor: str = "user") -> dict:
        key = key.strip()
        if not key or " " in key:
            raise SciWeaveError("step key must be one word, e.g. 'gwas' or 'finemapping'")
        st = self.steps.get(key, {})
        is_new = not st
        st["label"] = label or st.get("label") or key.replace("_", " ").replace("-", " ").capitalize()
        if order is not None:
            st["order"] = order
        st.setdefault("order", max([s.get("order", 0) for s in self.steps.values()] + [0]) + 1)
        if description:
            st["description"] = description
        st.setdefault("description", "")
        self.steps[key] = st
        self.log("step_defined" if is_new else "step_updated", detail=f"{key}: {st['label']} (#{st['order']})",
                 actor=actor)
        return st

    def _ensure_step(self, key: str, actor: str = "user") -> str:
        if key not in self.steps:
            self.define_step(key, actor=actor)
        return key

    def set_step(self, node_ids, key: str | None, actor: str = "user") -> list[str]:
        if key:
            self._ensure_step(key, actor)
        done = []
        for nid in node_ids:
            n = self.node(nid)
            if n.get("step") != key:
                n["step"] = key
                done.append(nid)
        if done:
            self.log("step_assigned", detail=f"{key or '(none)'}: {', '.join(done)}", actor=actor, nodes=done)
        return done

    def step_summary(self) -> dict:
        """Per step: members by type, links within, and links in from / out to other steps."""
        out = {k: {"members": [], "within": 0, "in": {}, "out": {}} for k in self.steps}
        out.setdefault(None, {"members": [], "within": 0, "in": {}, "out": {}})
        for nid, n in self.nodes.items():
            out.setdefault(n.get("step"), {"members": [], "within": 0, "in": {}, "out": {}})["members"].append(nid)
        for e in self.edges:
            a = self.nodes.get(e["source"], {}).get("step")
            b = self.nodes.get(e["target"], {}).get("step")
            if a == b:
                out[a]["within"] += 1
            else:
                out[a]["out"][b] = out[a]["out"].get(b, 0) + 1
                out[b]["in"][a] = out[b]["in"].get(a, 0) + 1
        if not out[None]["members"]:
            del out[None]
        return out

    # ---------------------------------------------------------- analyses ----
    # The analysis history: a tree of what was done, starting from the raw / input
    # data, each analysis with its children (sub-analyses, reruns, fixes). It is the
    # guide for a project ("what did we do, in what order, and why"), independent of
    # whether its files are in the network yet. Each entry can point at network
    # objects (`nodes`) and at a step, and is marked `organized` once its objects
    # have been recorded.

    ANALYSIS_STATUS = ("done", "open", "superseded", "planned")
    ANALYSIS_FIELDS = ("title", "parent", "order", "summary", "status", "start", "end", "params",
                       "tools", "paths", "feeds", "issue", "group", "step", "nodes", "organized", "hidden", "history")

    @property
    def analyses(self) -> dict:
        return self.graph.setdefault("analyses", {})

    def analysis(self, key: str) -> dict:
        a = self.analyses.get(key)
        if a is None:
            raise SciWeaveError(f"unknown analysis '{key}' (see `sciweave analysis ls`)")
        return a

    def set_analysis(self, key: str, actor: str = "user", log: bool = True, **fields) -> dict:
        """Create or update one analysis. Unknown fields are refused; None leaves a field as is."""
        key = key.strip()
        if not key or any(c.isspace() for c in key):
            raise SciWeaveError("analysis key must be one word, e.g. 'raw' or 'meta.mrmega'")
        bad = set(fields) - set(self.ANALYSIS_FIELDS)
        if bad:
            raise SciWeaveError(f"unknown analysis field(s): {', '.join(sorted(bad))}")
        a = self.analyses.get(key)
        is_new = a is None
        if is_new:
            a = {"title": key, "parent": None, "summary": "", "status": "done", "history": [], "nodes": [],
                 "organized": False, "created": now_iso()}
        for k, v in fields.items():
            if v is None:
                continue
            if k == "status" and v not in self.ANALYSIS_STATUS:
                raise SciWeaveError(f"status must be one of {', '.join(self.ANALYSIS_STATUS)}")
            if k == "parent" and v:
                if v not in self.analyses:
                    raise SciWeaveError(f"unknown parent analysis '{v}'")
                cur = v
                while cur:
                    if cur == key:
                        raise SciWeaveError(f"'{v}' is '{key}' or inside it: that would make a loop")
                    cur = self.analyses.get(cur, {}).get("parent")
            if k == "nodes":
                for nid in v:
                    self.node(nid)
            a[k] = (v or None) if k == "parent" else v
        if "order" not in a:
            sib = [x.get("order", 0) for kk, x in self.analyses.items() if kk != key and x.get("parent") == a["parent"]]
            a["order"] = max(sib + [0]) + 1
        a["updated"] = now_iso()
        self.analyses[key] = a
        if log:
            self.log("analysis_added" if is_new else "analysis_updated", detail=f"{key}: {a['title']}", actor=actor,
                     analysis=key)
        return a

    def log_analysis(self, key: str, text: str, date: str | None = None, actor: str = "user") -> dict:
        """Add a dated event to an analysis' own history (kept sorted by date)."""
        a = self.analysis(key)
        ev = {"date": (date or now_iso()[:10]), "text": text.strip()}
        a.setdefault("history", []).append(ev)
        a["history"].sort(key=lambda e: e.get("date", ""))
        a["updated"] = now_iso()
        self.log("analysis_logged", detail=f"{key}: {text[:120]}", actor=actor, analysis=key)
        return ev

    def remove_analysis(self, key: str, actor: str = "user") -> dict:
        """Remove one analysis; its children move up to its parent."""
        a = self.analysis(key)
        for x in self.analyses.values():
            if x.get("parent") == key:
                x["parent"] = a.get("parent")
        del self.analyses[key]
        self.log("analysis_removed", detail=f"{key}: {a['title']}", actor=actor, analysis=key)
        return a

    def rename_analysis(self, old: str, new: str, subtree: bool = True, actor: str = "user") -> dict[str, str]:
        """Change an analysis key. With subtree=True, branches whose keys start with
        "<old>." are renamed to "<new>." too. Parents and every @key reference in any
        analysis text follow. Returns {old: new}."""
        self.analysis(old)
        new = new.strip()
        if not new or any(c.isspace() for c in new):
            raise SciWeaveError("analysis key must be one word")
        mapping = {old: new}
        if subtree:
            for k in self.analyses:
                if k.startswith(old + "."):
                    mapping[k] = new + k[len(old):]
        clash = [v for k, v in mapping.items() if v in self.analyses and v not in mapping]
        if clash:
            raise SciWeaveError(f"key(s) already in use: {', '.join(clash)}")
        self.graph["analyses"] = {mapping.get(k, k): v for k, v in self.analyses.items()}

        def sub(m):
            return "@" + mapping.get(m.group(1), m.group(1))
        for a in self.analyses.values():
            if a.get("parent") in mapping:
                a["parent"] = mapping[a["parent"]]
            for f in ("summary", "issue", "feeds", "title", "params"):
                if isinstance(a.get(f), str):
                    a[f] = self.REF.sub(sub, a[f])
            for h in a.get("history") or []:
                h["text"] = self.REF.sub(sub, h.get("text", ""))
        self.log("analysis_renamed", detail=", ".join(f"{k} → {v}" for k, v in mapping.items()), actor=actor)
        return mapping

    def analysis_children(self, key: str | None) -> list[str]:
        kids = [k for k, a in self.analyses.items() if (a.get("parent") or None) == key]
        return sorted(kids, key=lambda k: (self.analyses[k].get("order", 0), self.analyses[k].get("start") or "", k))

    def analysis_tree(self) -> list[tuple[str, str, int]]:
        """Depth-first (key, number like '2.3', depth) in guide order."""
        out: list[tuple[str, str, int]] = []

        def walk(parent, prefix, depth):
            kids = self.analysis_children(parent)
            # an entry with order 0 is numbered 0 ("before": e.g. an earlier round), the rest count from 1
            zero = 1 if kids and self.analyses[kids[0]].get("order") == 0 else 0
            for i, k in enumerate(kids, 1 - zero):
                num = f"{prefix}{i}"
                out.append((k, num, depth))
                walk(k, num + ".", depth + 1)
        walk(None, "", 0)
        return out

    # ------------------------------------------------------------- focus ----
    # Hiding is for focus, never deletion: a hidden analysis (and its branches) drops
    # out of view together with the objects that exist only for it. Objects are hidden
    # when (1) marked hidden themselves, (2) listed in a hidden analysis, or (3) every
    # consumer of them (outgoing links, or links that ran them as their script) is
    # hidden — so raw inputs and scripts used only by hidden steps disappear too.
    # Objects listed in a visible analysis, or explicitly unhidden, always stay visible.
    # Links touching a hidden object are hidden; what comes next stays. Staleness is
    # still computed over everything.

    FOCUS_IGNORE_RELS = ("documents", "related")

    def hidden_analyses(self) -> set[str]:
        out = set()
        for k in self.analyses:
            cur = k
            while cur:
                if self.analyses.get(cur, {}).get("hidden"):
                    out.add(k)
                    break
                cur = self.analyses.get(cur, {}).get("parent")
        return out

    def hide_analyses(self, keys, hidden: bool = True, actor: str = "user") -> list[str]:
        done = []
        for k in keys:
            a = self.analysis(k)
            if bool(a.get("hidden")) != hidden:
                a["hidden"] = hidden
                a["updated"] = now_iso()
                done.append(k)
        if done:
            self.log("analyses_hidden" if hidden else "analyses_unhidden", detail=", ".join(done), actor=actor)
        return done

    def hide_nodes(self, node_ids, hidden: bool = True, actor: str = "user") -> list[str]:
        """hidden=False pins an object visible even if a hidden analysis would hide it."""
        done = []
        for nid in node_ids:
            n = self.node(nid)
            if n.get("hidden") is not hidden:
                n["hidden"] = hidden
                done.append(nid)
        if done:
            self.log("nodes_hidden" if hidden else "nodes_unhidden", detail=", ".join(done), actor=actor, nodes=done)
        return done

    def hidden_nodes(self) -> dict[str, str]:
        """Object id -> why it is hidden from view."""
        hid_a = self.hidden_analyses()
        pinned = {nid for nid, n in self.nodes.items() if n.get("hidden") is False}
        for k, a in self.analyses.items():
            if k not in hid_a:
                pinned.update(a.get("nodes") or [])
        out: dict[str, str] = {}
        for nid, n in self.nodes.items():
            if n.get("hidden") is True:
                out[nid] = "hidden"
        for k in sorted(hid_a):
            for nid in self.analyses[k].get("nodes") or []:
                if nid in self.nodes and nid not in pinned:
                    out.setdefault(nid, f"part of hidden analysis {k}")
        consumers: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for e in self.edges:
            if e["rel"] in self.FOCUS_IGNORE_RELS:
                continue
            consumers.setdefault(e["source"], []).append(e["target"])
            if e.get("script") in consumers:
                consumers[e["script"]].append(e["target"])
        changed = True
        while changed:
            changed = False
            for nid, cons in consumers.items():
                if nid in out or nid in pinned or nid not in self.nodes or not cons:
                    continue
                if all(c in out for c in cons):
                    out[nid] = "only used by hidden items"
                    changed = True
        return out

    REF = re.compile(r"@([A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_])?)")

    def expand_refs(self, text: str) -> str:
        """Analyses refer to each other as @key in their text (stable when the tree is
        reordered); for reading, show the current number and title instead."""
        num = {k: n for k, n, _ in self.analysis_tree()}

        def sub(m):
            k = m.group(1)
            return f"{num[k]} “{self.analyses[k]['title']}”" if k in num else m.group(0)
        return self.REF.sub(sub, text or "")

    def import_analyses(self, entries: list[dict], replace: bool = False, actor: str = "user") -> int:
        """Bulk-load a guide. Entries may nest children under "children" or name a "parent";
        parents are created before children. replace=True clears the existing tree first.
        All or nothing: on any error the tree is left as it was."""
        flat: list[dict] = []

        def flatten(items, parent):
            for i, e in enumerate(items, 1):
                e = dict(e)
                kids = e.pop("children", []) or []
                if parent is not None and not e.get("parent"):
                    e["parent"] = parent
                flat.append(e)
                flatten(kids, e.get("key"))
        flatten(entries, None)
        # default order = position among its own siblings in the file (works for nested and flat lists)
        seen: dict = {}
        for e in flat:
            seen[e.get("parent")] = seen.get(e.get("parent"), 0) + 1
            e.setdefault("order", seen[e.get("parent")])
        backup = json.loads(json.dumps(self.analyses))
        try:
            if replace:
                self.graph["analyses"] = {}
            keys = {e.get("key") for e in flat}
            pending = list(flat)
            while pending:
                ready = [e for e in pending if not e.get("parent") or e["parent"] in self.analyses]
                if not ready:
                    missing = sorted({e["parent"] for e in pending} - keys)
                    raise SciWeaveError("analyses with unknown parent(s): " + ", ".join(missing or ["(loop)"]))
                for e in ready:
                    pending.remove(e)
                    e = dict(e)
                    if not e.get("key"):
                        raise SciWeaveError(f"every analysis needs a \"key\" (got {e.get('title', e)!r})")
                    history = e.pop("history", None)
                    k = e.pop("key")
                    a = self.set_analysis(k, actor=actor, log=False, **e)
                    if history is not None:
                        a["history"] = sorted(({"date": str(h.get("date", "")), "text": h.get("text", "")}
                                               if isinstance(h, dict) else {"date": "", "text": str(h)}
                                               for h in history), key=lambda h: h["date"])
        except Exception:
            self.graph["analyses"] = backup
            raise
        self.log("analyses_imported", detail=f"{len(flat)} analyses" + (" (replaced)" if replace else ""), actor=actor)
        return len(flat)

    # ------------------------------------------------------------ branches ----
    # A branch is an alternative version of an analysis that should live NEXT to the
    # original instead of overwriting it: other parameters (L=5 vs L=10), another
    # input set, another method. It copies the original's provenance links (with the
    # overridden parameters), is joined to it by a `variant` link, and carries
    # {"of", "name", "why", "status"}. One member of a family is "main".

    def branch_family(self, node_id: str) -> list[str]:
        root = node_id
        seen = {root}
        while (self.nodes[root].get("branch") or {}).get("of") and self.nodes[root]["branch"]["of"] in self.nodes:
            root = self.nodes[root]["branch"]["of"]
            if root in seen:
                break
            seen.add(root)
        fam, stack = [root], [root]
        while stack:
            cur = stack.pop()
            for nid, n in self.nodes.items():
                if (n.get("branch") or {}).get("of") == cur and nid not in fam:
                    fam.append(nid)
                    stack.append(nid)
        return fam

    def branch(self, source_id: str, label: str, path: str | None = None, mode: str | None = None,
               params: dict | None = None, why: str = "", name: str | None = None,
               actor: str = "user") -> dict:
        src = self.node(source_id)
        if not why:
            raise SciWeaveError("a branch needs a reason: say why this alternative exists (--why)")
        node = self.add_node(src["type"], label, path=path, mode=mode or src["mode"],
                             description=src.get("description", ""), groups=src["groups"], tags=src["tags"],
                             actor=actor, message=f"branch of {source_id}", step=src.get("step"))
        node["branch"] = {"of": source_id, "name": name or label, "why": why, "status": "alternative",
                          "created": now_iso()}
        src.setdefault("branch", {"of": None, "name": "original", "why": "", "status": "main",
                                  "created": now_iso()})
        params = params or {}
        prov = [e for e in self.incoming(source_id) if e["rel"] not in SOFT_RELS and e["rel"] != "part_of"]
        carriers = [e for e in prov if set(params) & set(e.get("params", {}))] or \
                   [e for e in prov if e["rel"] in ("produces", "derives")][:1]
        for e in prov:
            p = dict(e.get("params", {}))
            if e in carriers:
                p.update(params)
            if e["rel"] == "code":
                if not self.find_edge(e["source"], node["id"]):
                    self.link(e["source"], node["id"], rel="code", actor=actor)
                continue
            self.link(e["source"], node["id"], rel=e["rel"], params=p, script=e.get("script"),
                      command=e.get("command"), actor=actor)
        self.link(source_id, node["id"], rel="variant", label=name or label, note=why, actor=actor)
        if node["versions"]:
            node["versions"][-1]["why"] = why
            node["versions"][-1]["changes"] = [
                {"kind": "param", "edge": None, "source": None, "key": k,
                 "from": next((e["params"].get(k) for e in carriers if k in e.get("params", {})), None), "to": v}
                for k, v in params.items()]
        self.log("branch_created", node=node["id"], detail=f"branch of {source_id}: {why}", actor=actor,
                 nodes=[source_id, node["id"]], params=params or None)
        return node

    def set_branch_status(self, node_id: str, status: str, why: str = "", actor: str = "user") -> None:
        if status not in BRANCH_STATUSES:
            raise SciWeaveError(f"status must be one of {BRANCH_STATUSES}")
        n = self.node(node_id)
        fam = self.branch_family(node_id)
        if len(fam) < 2 and not n.get("branch"):
            raise SciWeaveError(f"{node_id} has no branches")
        if status == "main":
            for other in fam:
                b = self.nodes[other].setdefault("branch", {"of": None, "name": "original", "why": "",
                                                            "status": "main", "created": now_iso()})
                if other != node_id and b.get("status") == "main":
                    b["status"] = "alternative"
        n.setdefault("branch", {"of": None, "name": "original", "why": "", "created": now_iso()})["status"] = status
        n["branch"]["decision"] = {"ts": now_iso(), "why": why, "actor": actor, "status": status}
        self.log("branch_status", node=node_id, detail=f"{status}: {why}" if why else status, actor=actor,
                 nodes=fam, why=why or None)

    # ------------------------------------------------------ notes/groups ----
    def add_note(self, node_id: str, text: str, actor: str = "user") -> dict:
        node = self.node(node_id)
        note = {"ts": now_iso(), "text": text, "actor": actor}
        node["notes"].append(note)
        self.log("note_added", node=node_id, detail=text[:120], actor=actor)
        return note

    def set_group(self, name: str, nodes=(), color: str | None = None, label: str | None = None,
                  actor: str = "user") -> dict:
        g = self.graph["groups"].setdefault(name, {"label": name, "color": None})
        if color:
            g["color"] = color
        if label:
            g["label"] = label
        for nid in nodes:
            n = self.node(nid)
            if name not in n["groups"]:
                n["groups"].append(name)
        self.log("group_set", detail=f"{name}: {', '.join(nodes)}", actor=actor, nodes=list(nodes))
        return g

    # ------------------------------------------------------------ status ----
    def upstream(self, node_id: str) -> list[str]:
        out, seen, stack = [], set(), [node_id]
        while stack:
            cur = stack.pop()
            for e in self.incoming(cur):
                if e["source"] not in seen:
                    seen.add(e["source"])
                    out.append(e["source"])
                    stack.append(e["source"])
        return out

    def downstream(self, node_id: str) -> list[str]:
        out, seen, stack = [], set(), [node_id]
        while stack:
            cur = stack.pop()
            for e in self.outgoing(cur):
                if e["target"] not in seen:
                    seen.add(e["target"])
                    out.append(e["target"])
                    stack.append(e["target"])
        return out

    # Staleness model -----------------------------------------------------
    # A data node's version records the versions of its *effective parents*:
    # direct sources, and — through process nodes (pipeline/step/script) — the
    # inputs of the process that made it. It is stale when any of those parents
    # has moved on, or is itself stale. Process nodes are never "rebuilt"; they
    # are flagged when an input changed since one of their outputs was built.
    # part_of placements compare the placed version with the item's final
    # (else current) version. documents/related links never propagate.

    def effective_parents(self, node_id: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def visit(nid: str) -> None:
            for e in self.incoming(nid):
                if e["rel"] in SOFT_RELS or e["rel"] == "part_of":
                    continue
                s = e["source"]
                if s in seen or s not in self.nodes:
                    continue
                seen.add(s)
                out.append(s)
                if self.nodes[s]["type"] in PROCESS_TYPES:
                    visit(s)

        visit(node_id)
        return out

    def process_outputs(self, node_id: str) -> list[str]:
        """Data nodes built by this process node (directly or via nested processes)."""
        out, seen, stack = [], set(), [node_id]
        while stack:
            cur = stack.pop()
            for e in self.outgoing(cur):
                t = e["target"]
                if t in seen or e["rel"] in SOFT_RELS or e["rel"] == "part_of":
                    continue
                seen.add(t)
                if self.nodes[t]["type"] in PROCESS_TYPES:
                    stack.append(t)
                else:
                    out.append(t)
        return out

    def recorded_parents(self, node_id: str) -> dict:
        n = self.nodes[node_id]
        return n["versions"][-1].get("parents", {}) if n["versions"] else {}

    def _adopt_parents(self, node_id: str) -> None:
        """After a new link: the node's latest version was built from the current
        versions of any newly-connected effective parents."""
        n = self.nodes[node_id]
        if not n["versions"]:
            return
        latest = n["versions"][-1]
        rec = latest.setdefault("parents", {})
        for pid in self.effective_parents(node_id):
            rec.setdefault(pid, self.nodes[pid]["current_version"])
        for e in self.incoming(node_id):
            # only links that did not exist when this version was made; an existing link's
            # snapshot must keep the old values, or parameter changes become invisible
            if e.get("params") and e["id"] not in latest.setdefault("edge_params", {}):
                latest["edge_params"][e["id"]] = dict(e["params"])

    def edge_is_stale(self, e: dict) -> bool:
        src, tgt = self.nodes.get(e["source"]), self.nodes.get(e["target"])
        if src is None or tgt is None or src["current_version"] is None or e["rel"] in SOFT_RELS:
            return False
        if e["rel"] == "part_of":
            if e.get("placed_version") is None:
                return False
            return (src["final_version"] or src["current_version"]) != e["placed_version"]
        targets = self.process_outputs(tgt["id"]) if tgt["type"] in PROCESS_TYPES else [tgt["id"]]
        for t in targets:
            r = self.recorded_parents(t).get(src["id"])
            if r is not None and r != src["current_version"]:
                return True
        return False

    def status(self, check_disk: bool = True) -> dict[str, dict]:
        """Per node: {state, stale, modified, missing, reasons[]}.

        stale    = an input changed since this node was last built (or is itself stale)
        modified = file on disk differs from the last saved version
        missing  = path recorded but gone
        """
        out: dict[str, dict] = {}
        for nid, n in self.nodes.items():
            s = {"stale": False, "modified": False, "missing": False, "reasons": []}
            if check_disk and n["path"]:
                p = self.resolve(n["path"])
                if not p.exists():
                    s["missing"] = True
                    s["reasons"].append("file missing on disk")
                elif n["versions"]:
                    try:
                        if not unchanged_since(p, n["versions"][-1]):
                            s["modified"] = True
                            s["reasons"].append("changed on disk since last save")
                    except OSError as exc:
                        s["reasons"].append(f"cannot read: {exc}")
            out[nid] = s

        memo: dict[str, bool] = {}

        def is_data(nid: str) -> bool:
            return self.nodes[nid]["type"] not in PROCESS_TYPES

        def stale(nid: str, trail: frozenset) -> bool:
            if nid in memo:
                return memo[nid]
            if nid in trail:
                return False
            trail = trail | {nid}
            n = self.nodes[nid]
            reasons = out[nid]["reasons"]
            result = False
            if not is_data(nid):
                for e in self.incoming(nid):
                    if e["rel"] in SOFT_RELS or e["source"] not in self.nodes:
                        continue
                    if self.edge_is_stale(e):
                        reasons.append(f"input {e['source']} changed since outputs were built; re-run needed")
                        result = True
                    elif stale(e["source"], trail) and is_data(e["source"]):
                        reasons.append(f"upstream {e['source']} is stale")
                        result = True
            else:
                rec = self.recorded_parents(nid)
                for pid in self.effective_parents(nid):
                    cur = self.nodes[pid]["current_version"]
                    if rec.get(pid) is not None and cur is not None and rec[pid] != cur:
                        reasons.append(f"{pid} is now v{cur}, this was built from v{rec[pid]}")
                        result = True
                    elif is_data(pid) and stale(pid, trail):
                        reasons.append(f"upstream {pid} is stale")
                        result = True
                for e in self.incoming(nid):
                    if e["rel"] != "part_of" or e["source"] not in self.nodes:
                        continue
                    if self.edge_is_stale(e):
                        reasons.append(f"component {e['source']} has a newer version than the placed "
                                       f"v{e['placed_version']} ({e.get('label') or ''})")
                        result = True
                    elif stale(e["source"], trail):
                        reasons.append(f"component {e['source']} is stale")
                        result = True
            memo[nid] = result
            return result

        for nid in self.nodes:
            stale(nid, frozenset())
            out[nid]["stale"] = memo.get(nid, False)
        for nid, s in out.items():
            s["reasons"] = list(dict.fromkeys(s["reasons"]))
            s["state"] = ("missing" if s["missing"] else "stale" if s["stale"]
                          else "modified" if s["modified"] else "ok")
        return out

    def untracked_files(self, folders=("results", "articles", "scripts", "data/inputs")) -> list[str]:
        tracked = {self.resolve(n["path"]).resolve() for n in self.nodes.values() if n["path"]}
        tracked_dirs = [t for t in tracked if t.is_dir()]
        out = []
        for folder in folders:
            base = self.root / folder
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if not p.is_file() or p.name.startswith("."):
                    continue
                rp = p.resolve()
                if rp in tracked or any(d in rp.parents for d in tracked_dirs):
                    continue
                out.append(self.rel(rp))
        return sorted(out)

    # ------------------------------------------------------------ export ----
    def payload(self, check_disk: bool = True, history_limit: int = 2000) -> dict:
        """Everything the dashboard needs, in one JSON-able dict."""
        try:
            return self._payload(check_disk, history_limit)
        finally:
            self.save_fp_cache()

    def _payload(self, check_disk: bool = True, history_limit: int = 2000) -> dict:
        st = self.status(check_disk=check_disk)
        edges = [{**e, "stale": self.edge_is_stale(e)} for e in self.edges]
        return {
            "schema": self.graph["schema"],
            "project": self.graph["project"],
            "root": str(self.root),
            "nodes": list(self.nodes.values()),
            "edges": edges,
            "groups": self.graph["groups"],
            "steps": self.steps,
            "analyses": self.analyses,
            "hidden": self.hidden_nodes(),
            "destination": str(self.destination) if self.destination else None,
            "organized": {nid: {**n["organized"], "state": self.organized_state(nid),
                                "folder": self.organized_folder(nid)}
                          for nid, n in self.nodes.items() if n.get("organized")},
            "status": st,
            "types": NODE_TYPES,
            "relations": EDGE_RELATIONS,
            "history": self.history(limit=history_limit),
            "generated": now_iso(),
        }

    def write_map(self) -> Path:
        from sciweave.mapdoc import render_map
        path = self.root / "SCIWEAVE.md"
        path.write_text(render_map(self), encoding="utf-8")
        return path

