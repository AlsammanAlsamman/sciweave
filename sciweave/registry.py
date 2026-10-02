"""The user's SciWeave home: one folder (default ~/Documents/SciWeave) that lists
every project on this machine.

  projects.json  machine-readable registry (id, name, path, added)
  PROJECTS.md    the same list for people and Claude: "which projects do I have,
                 and where?" — regenerated on every change

Projects stay where they are; the home only points at them. `sciweave init`
registers new projects, `sciweave serve` registers the one it serves, and
`sciweave projects add` adopts existing ones. `sciweave open` serves the home
page, from which any registered project's dashboard opens.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sciweave.project import STATE_DIR, Project, SciWeaveError
from sciweave.schema import NODE_TYPES, now_iso

REGISTRY = "projects.json"
INDEX = "PROJECTS.md"


def home_dir() -> Path:
    env = os.environ.get("SCIWEAVE_HOME")
    if env:
        return Path(env).expanduser()
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "SciWeave"


def load() -> dict:
    f = home_dir() / REGISTRY
    if not f.exists():
        return {"projects": []}
    return json.loads(f.read_text(encoding="utf-8"))


def _save(reg: dict) -> None:
    h = home_dir()
    h.mkdir(parents=True, exist_ok=True)
    reg["projects"].sort(key=lambda e: e["name"].lower())
    tmp = h / (REGISTRY + ".tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(h / REGISTRY)
    write_index(reg)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def register(p: Project) -> dict:
    """Add (or refresh the name of) a project. Idempotent; returns its entry."""
    reg = load()
    name = p.graph["project"]["name"]
    for e in reg["projects"]:
        if _same(e["path"], p.root):
            if e["name"] != name:
                e["name"] = name
                _save(reg)
            return e
    taken = {e["id"] for e in reg["projects"]}
    base = sid = _slug(name)
    i = 2
    while sid in taken:
        sid, i = f"{base}-{i}", i + 1
    e = {"id": sid, "name": name, "path": str(p.root), "added": now_iso()}
    reg["projects"].append(e)
    _save(reg)
    return e


def unregister(ref: str) -> dict:
    reg = load()
    e = get(ref, reg)
    reg["projects"] = [x for x in reg["projects"] if x["id"] != e["id"]]
    _save(reg)
    return e


def get(ref: str, reg: dict | None = None) -> dict:
    """Find a project by id, exact name, path, or unique name fragment."""
    reg = reg or load()
    ps = reg["projects"]
    for e in ps:
        if ref == e["id"] or ref.lower() == e["name"].lower():
            return e
    if os.sep in ref or "/" in ref or Path(ref).exists():
        for e in ps:
            if _same(e["path"], ref):
                return e
    hits = [e for e in ps if ref.lower() in e["name"].lower() or ref.lower() in e["id"]]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SciWeaveError(f"'{ref}' matches several projects: " + ", ".join(e["id"] for e in hits))
    raise SciWeaveError(f"no registered project '{ref}' (see `sciweave projects`)")


def _last_activity(p: Project) -> str | None:
    if not p.history_file.exists():
        return None
    with open(p.history_file, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 4096))
        tail = fh.read().decode("utf-8", errors="replace").strip().splitlines()
    for line in reversed(tail):
        try:
            return json.loads(line).get("ts")
        except json.JSONDecodeError:
            continue
    return None


def summarize(e: dict, check_disk: bool = True) -> dict:
    """Registry entry + a live summary for the home page (counts, status, steps)."""
    out = dict(e)
    root = Path(e["path"])
    if not (root / STATE_DIR / "graph.json").exists():
        out["missing"] = True
        return out
    p = Project(root)
    types: dict[str, int] = {}
    cats: dict[str, int] = {}
    for n in p.nodes.values():
        types[n["type"]] = types.get(n["type"], 0) + 1
        c = NODE_TYPES.get(n["type"], {}).get("category", "notes")
        cats[c] = cats.get(c, 0) + 1
    st = p.status(check_disk=check_disk)
    count = {"stale": 0, "modified": 0, "missing": 0}
    for s in st.values():
        for k in count:
            count[k] += bool(s.get(k))
    steps = sorted(p.graph.get("steps", {}).values(), key=lambda s: s.get("order", 0))
    out.update(
        missing=False,
        name=p.graph["project"]["name"],
        description=p.graph["project"].get("description", ""),
        created=p.graph["project"].get("created"),
        nodes=len(p.nodes),
        links=len(p.edges),
        types=types,
        categories=cats,
        status=count,
        steps=[s.get("label", "") for s in steps],
        analyses=len(p.analyses),
        destination=str(p.destination) if p.destination else None,
        copies=sum(1 for n in p.nodes.values() if n.get("organized")),
        organized=sum(1 for a in p.analyses.values() if a.get("organized")),
        last_activity=_last_activity(p),
    )
    return out


def write_index(reg: dict | None = None) -> Path:
    reg = reg or load()
    h = home_dir()
    lines = [
        "# SciWeave projects on this machine",
        "",
        "Generated by SciWeave from `projects.json`; do not edit by hand.",
        "Open the home dashboard with `sciweave open`; a single project with",
        "`sciweave open <id>`. Every project root below has its own `SCIWEAVE.md`",
        "(a readable map of its network) and a `.sciweave/` folder; run commands",
        "against one from anywhere with `sciweave -C <path> <command>`.",
        "",
        "| id | name | path |",
        "|---|---|---|",
    ]
    for e in reg["projects"]:
        lines.append(f"| `{e['id']}` | {e['name']} | `{e['path']}` |")
    if not reg["projects"]:
        lines.append("| | (none yet: `sciweave init` or `sciweave projects add <path>`) | |")
    f = h / INDEX
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f
