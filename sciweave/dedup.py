"""One file, one node: content-identical files are found and merged, never duplicated.

The same reference panel, annotation file or input is often used by several tools
(e.g. a 1000 Genomes panel used both for FIZI summary-statistic imputation and for
LYNXgwas clumping), sometimes copied to several folders under other names. In the
network such a file must be ONE node, linked to every analysis that used it, with
the other locations remembered (`meta.also_at`) and its users listed (`meta.used_by`).

Identity is bit-by-bit: files are first matched by size (free), and only files of
equal size are hashed (SHA-256 of the full content, streamed). Hashes are cached in
`.sciweave/hashcache.json` by path + size + mtime, so each file is read once.
Tiny files (< MIN_BYTES, e.g. empty markers, short READMEs) are ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from sciweave.project import Project, SciWeaveError, now_iso

MIN_BYTES = 4096
MAX_FILES_PER_NODE = 5000


# ------------------------------------------------------------------ hashing --
class HashCache:
    def __init__(self, p: Project):
        self.file = p.state / "hashcache.json"
        self.data: dict = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}
        self.dirty = False

    def sha256(self, path: Path) -> str:
        st = path.stat()
        key = os.path.normcase(str(path.resolve()))
        hit = self.data.get(key)
        if hit and hit["size"] == st.st_size and hit["mtime"] == st.st_mtime_ns:
            return hit["sha256"]
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.data[key] = {"size": st.st_size, "mtime": st.st_mtime_ns, "sha256": digest}
        self.dirty = True
        return digest

    def save(self) -> None:
        if self.dirty:
            self.file.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            self.dirty = False


def files_of(path: Path) -> list[Path]:
    """The files that make up an object: the file itself, or every file under a folder."""
    if path.is_file():
        return [path]
    if path.is_dir():
        out = []
        for f in sorted(path.rglob("*")):
            if f.is_file():
                out.append(f)
                if len(out) >= MAX_FILES_PER_NODE:
                    break
        return out
    return []


def _node_files(p: Project, nid: str) -> list[tuple[Path, int]]:
    n = p.nodes[nid]
    if not n.get("path"):
        return []
    src = p.resolve(n["path"])
    out = []
    for f in files_of(src) if src.exists() else []:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size >= MIN_BYTES:
            out.append((f, size))
    return out


# ----------------------------------------------------------------- scanning --
def scan(p: Project, extra: list[Path] | None = None, cache: HashCache | None = None) -> dict:
    """Find content-identical files among the network's objects (plus `extra` paths, labelled
    'new'). Returns {"groups": [{sha256, size, members: [(owner, path)]}], "whole": [...],
    "partial": [...]} where whole = two owners whose whole content is identical, partial =
    owners sharing some identical files."""
    cache = cache or HashCache(p)
    owners: dict[str, list[tuple[Path, int]]] = {}
    for nid in p.nodes:
        fs = _node_files(p, nid)
        if fs:
            owners[nid] = fs
    for path in extra or []:
        fs = [(f, f.stat().st_size) for f in files_of(Path(path)) if f.stat().st_size >= MIN_BYTES]
        owners[f"new:{path}"] = fs
    by_size: dict[int, list[tuple[str, Path]]] = defaultdict(list)
    for owner, fs in owners.items():
        for f, size in fs:
            by_size[size].append((owner, f))
    groups: dict[str, dict] = {}
    for size, members in by_size.items():
        if len({o for o, _ in members}) < 2 and len(members) < 2:
            continue
        for owner, f in members:
            digest = cache.sha256(f)
            g = groups.setdefault(digest, {"sha256": digest, "size": size, "members": []})
            g["members"].append((owner, str(f)))
    cache.save()
    dup_groups = [g for g in groups.values() if len({str(Path(m[1]).resolve()).lower() for m in g["members"]}) > 1
                  or len({m[0] for m in g["members"]}) > 1]
    # owner-level relations
    content: dict[str, set] = {}
    for g in dup_groups:
        for owner, _ in g["members"]:
            content.setdefault(owner, set()).add(g["sha256"])
    owner_hashes = {o: {cache.sha256(f) for f, _ in fs} if o in content else set() for o, fs in owners.items()}
    whole, partial, seen = [], [], set()
    for g in dup_groups:
        os_ = sorted({m[0] for m in g["members"]})
        for i, a in enumerate(os_):
            for b in os_[i + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                ha, hb = owner_hashes[a], owner_hashes[b]
                shared = ha & hb
                rel = {"a": a, "b": b, "shared_files": len(shared), "a_files": len(ha), "b_files": len(hb)}
                (whole if ha == hb else partial).append(rel)
    return {"groups": dup_groups, "whole": whole, "partial": partial}


def check_new(p: Project, path: Path, cache: HashCache | None = None) -> dict:
    """Is the content of `path` already in the network? -> {"same_as": [ids], "overlaps": [ids]}"""
    path = Path(path)
    res = scan(p, extra=[path], cache=cache)
    key = f"new:{path}"
    same = [r["a"] if r["b"] == key else r["b"] for r in res["whole"] if key in (r["a"], r["b"])]
    over = [r["a"] if r["b"] == key else r["b"] for r in res["partial"] if key in (r["a"], r["b"])]
    return {"same_as": [x for x in same if not x.startswith("new:")],
            "overlaps": [x for x in over if not x.startswith("new:")]}


# ------------------------------------------------------------------ merging --
def merge(p: Project, keep: str, dup: str, label: str | None = None, used_by: list[str] | None = None,
          actor: str = "user") -> dict:
    """Fold `dup` into `keep`: links, analysis entries, groups, version parents and steps-of-use move to
    `keep`; dup's location goes to keep.meta.also_at; dup is removed from the network (no file is touched)."""
    if keep == dup:
        raise SciWeaveError("cannot merge a node into itself")
    k, d = p.node(keep), p.node(dup)
    # links: re-point, dropping self-loops and exact duplicates
    seen, edges = set(), []
    for e in p.edges:
        if e["source"] == dup:
            e["source"] = keep
        if e["target"] == dup:
            e["target"] = keep
        if e.get("script") == dup:
            e["script"] = keep
        sig = (e["source"], e["target"], e["rel"])
        if e["source"] == e["target"] or sig in seen:
            continue
        seen.add(sig)
        edges.append(e)
    p.graph["edges"] = edges
    # version records that name dup as a parent
    for n in p.nodes.values():
        for v in n.get("versions", []):
            par = v.get("parents") or {}
            if dup in par:
                par.setdefault(keep, par.pop(dup))
    # analyses that point at dup
    for a in p.analyses.values():
        if dup in (a.get("nodes") or []):
            a["nodes"] = [keep if x == dup else x for x in a["nodes"]]
            a["nodes"] = list(dict.fromkeys(a["nodes"]))
    # metadata: other locations, users, groups, tags
    meta = k.setdefault("meta", {})
    also = meta.setdefault("also_at", [])
    if d.get("path") and d["path"] not in also and d["path"] != k.get("path"):
        also.append(d["path"])
    for x in (d.get("meta") or {}).get("also_at", []):
        if x not in also and x != k.get("path"):
            also.append(x)
    users = meta.setdefault("used_by", [])
    for u in list((d.get("meta") or {}).get("used_by", [])) + list(used_by or []):
        if u not in users:
            users.append(u)
    k["groups"] = list(dict.fromkeys((k.get("groups") or []) + (d.get("groups") or [])))
    k["tags"] = list(dict.fromkeys((k.get("tags") or []) + (d.get("tags") or [])))
    if d.get("description") and d["description"] not in (k.get("description") or ""):
        k["description"] = ((k.get("description") or "") + f"\n[also, as {dup}] " + d["description"]).strip()
    if label:
        k["label"] = label
    k["updated"] = now_iso()
    del p.nodes[dup]
    p.log("nodes_merged", node=keep, detail=f"{dup} merged into {keep} (identical content); also at {d.get('path')}",
          actor=actor)
    return k
