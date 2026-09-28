"""The project model: graph.json, version store, history log, staleness.

Everything that changes a project goes through `Project`. The CLI, the
dashboard server, the plan protocol and the tests all use this one class, so
the rules (ids, versions, staleness, history) live in exactly one place.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from sciweave.schema import (
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
SOFT_RELS = {"documents", "related"}   # informational links: never propagate staleness


class SciweaveError(Exception):
    """A user-facing error: the message is printed as-is by the CLI."""


# ---------------------------------------------------------------- hashing ----

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    return {"hash": sha256_file(path), "size": st.st_size, "kind": "file", "exact": True, "mtime": st.st_mtime_ns}


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

    # ---------------------------------------------------------- lifecycle ----
    @classmethod
    def init(cls, root: Path, name: str | None = None, description: str = "", bare: bool = False) -> "Project":
        """bare=True adopts an existing project in place: only .sciweave/ and
        SCIWEAVE.md are added, no data/results/... folders are created."""
        root = Path(root).resolve()
        if (root / STATE_DIR / "graph.json").exists():
            raise SciweaveError(f"{root} is already a Sciweave project")
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
        raise SciweaveError(
            f"no Sciweave project found at or above {here} (run `sciweave init` first, or pass --project)"
        )

    def load(self) -> None:
        self.graph = json.loads(self.graph_file.read_text(encoding="utf-8"))
        self.graph.setdefault("counters", {})
        self.graph.setdefault("groups", {})

    def save_graph(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        tmp = self.graph_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.graph_file.exists():
            shutil.copy2(self.graph_file, self.graph_file.with_suffix(".json.bak"))
        os.replace(tmp, self.graph_file)

    def commit(self) -> None:
        """Persist graph + regenerate SCIWEAVE.md. Call after any mutation."""
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
            raise SciweaveError(f"unknown node '{node_id}' (see `sciweave ls`)")
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
                 message: str = "added", history: list | None = None) -> dict:
        """history: older copies of this item, oldest first, as
        [{"path": ..., "ts": "2026-09-04T18:19:00+00:00", "message": ...}]. They become
        v1..vN (content stored), and the current file becomes the latest version."""
        if node_type not in NODE_TYPES:
            raise SciweaveError(f"unknown node type '{node_type}' (one of: {', '.join(NODE_TYPES)})")
        if mode not in MODES:
            raise SciweaveError(f"mode must be one of {MODES}, got '{mode}'")
        if node_id and node_id in self.nodes:
            raise SciweaveError(f"node id '{node_id}' already exists")
        nid = node_id or self.next_id(node_type)

        stored_path = None
        if path:
            src = Path(path).expanduser()
            if not src.is_absolute():
                src = (Path.cwd() / src)
            if not src.exists():
                raise SciweaveError(f"path does not exist: {src}")
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
                        raise SciweaveError(f"destination already exists: {self.rel(target)} (choose --dest)")
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
            raise SciweaveError(f"{node['id']}: history file not found: {src}")
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

    def _new_version(self, node: dict, message: str = "", actor: str = "user",
                     refresh_edges: bool = True) -> dict | None:
        path = self.resolve(node["path"])
        if path is None or not path.exists():
            raise SciweaveError(f"{node['id']}: file is missing on disk ({node['path']})")
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
        v = {
            "v": (last["v"] + 1) if last else 1,
            "ts": now_iso(),
            "hash": fp["hash"],
            "size": fp["size"],
            "mtime": fp.get("mtime"),
            "kind": fp["kind"],
            "stored": stored,
            "message": message,
            "actor": actor,
            "parents": parents,
            "edge_params": {e["id"]: e.get("params", {}) for e in self.incoming(node["id"]) if e.get("params")},
        }
        node["versions"].append(v)
        node["current_version"] = v["v"]
        node["updated"] = v["ts"]
        return v

    def save_version(self, node_id: str, message: str = "", actor: str = "user",
                     refresh_edges: bool = True) -> dict | None:
        """Snapshot the node's file as a new version. None if unchanged."""
        node = self.node(node_id)
        if not node["path"]:
            raise SciweaveError(f"{node_id} has no file path to version")
        v = self._new_version(node, message=message, actor=actor, refresh_edges=refresh_edges)
        if v:
            self.log("version_saved", node=node_id, detail=message or f"v{v['v']}", actor=actor,
                     version=v["v"])
        return v

    def mark_final(self, node_id: str, version: int | None = None, actor: str = "user") -> int:
        node = self.node(node_id)
        if not node["versions"]:
            raise SciweaveError(f"{node_id} has no versions yet (save it first)")
        v = version or node["current_version"]
        if not any(x["v"] == v for x in node["versions"]):
            raise SciweaveError(f"{node_id} has no version {v}")
        node["final_version"] = v
        self.log("marked_final", node=node_id, detail=f"v{v}", actor=actor, version=v)
        return v

    def get_version(self, node_id: str, version: int) -> dict:
        for v in self.node(node_id)["versions"]:
            if v["v"] == version:
                return v
        raise SciweaveError(f"{node_id} has no version {version}")

    def object_path(self, version: dict) -> Path | None:
        return (self.objects / version["stored"]) if version.get("stored") else None

    def restore(self, node_id: str, version: int, out: str | None = None,
                force: bool = False, actor: str = "user") -> Path:
        node = self.node(node_id)
        v = self.get_version(node_id, version)
        obj = self.object_path(v)
        if obj is None or not obj.exists():
            raise SciweaveError(
                f"{node_id} v{version} has no stored content (ref nodes and very large files are "
                f"fingerprinted only)")
        target = Path(out).resolve() if out else self.resolve(node["path"])
        if out is None and target.exists() and not force:
            cur = fingerprint(target)
            last = node["versions"][-1]
            if cur["hash"] != last["hash"]:
                raise SciweaveError(
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
             label: str | None = None, params_file: str | None = None, actor: str = "user") -> dict:
        if rel not in EDGE_RELATIONS:
            raise SciweaveError(f"unknown relation '{rel}' (one of: {', '.join(EDGE_RELATIONS)})")
        src, dst = self.node(source), self.node(target)
        if source == target:
            raise SciweaveError("a node cannot link to itself")
        if self._reaches(target, source):
            raise SciweaveError(f"linking {source} -> {target} would create a cycle")
        params = params or {}
        bad = [k for k, v in params.items() if not is_scalar_param(v)]
        if bad:
            raise SciweaveError(f"parameters must be scalars or lists of scalars: {', '.join(bad)}")
        if script:
            sc = self.node(script)
            if sc["type"] not in ("script", "pipeline", "step"):
                raise SciweaveError(f"--script must point to a script/pipeline/step node, {script} is a {sc['type']}")
        pf = None
        if params_file:
            pf_path = Path(params_file).expanduser().resolve()
            if not pf_path.exists():
                raise SciweaveError(f"params file not found: {pf_path}")
            fp = fingerprint(pf_path)
            stored = self._store_object(pf_path, fp["hash"]) if fp["exact"] and fp["kind"] == "file" else None
            pf = {"path": self.rel(pf_path), "hash": fp["hash"], "stored": stored}

        existing = self.find_edge(source, target, rel)
        if existing:
            e = existing
            e["params"] = {**e.get("params", {}), **params}
            for k, v in (("command", command), ("note", note or None), ("label", label), ("params_file", pf)):
                if v:
                    e[k] = v
            if script:
                e["script"] = script
                e["script_version"] = self.nodes[script]["current_version"]
            e["updated"] = now_iso()
            self.log("edge_updated", edge=e["id"], detail=f"{source} -{rel}-> {target}", actor=actor,
                     nodes=[source, target], params=params or None)
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
                     nodes=[source, target], params=params or None)
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
            raise SciweaveError(f"unknown edge '{edge_id}'")
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
            if e.get("params"):
                latest.setdefault("edge_params", {})[e["id"]] = e["params"]

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
        st = self.status(check_disk=check_disk)
        edges = [{**e, "stale": self.edge_is_stale(e)} for e in self.edges]
        return {
            "schema": self.graph["schema"],
            "project": self.graph["project"],
            "root": str(self.root),
            "nodes": list(self.nodes.values()),
            "edges": edges,
            "groups": self.graph["groups"],
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

