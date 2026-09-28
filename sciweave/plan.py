"""The import-plan protocol: Claude (or a human) writes plan.json, SciWeave
checks it loudly, then applies it atomically.

    sciweave plan template > plan.json     # skeleton
    sciweave plan check plan.json          # report: ERROR / WARN / ok lines
    sciweave plan apply plan.json          # refuses if any ERROR

Plan format (version 1) — every list is optional:

{
  "sciweave_plan": 1,
  "summary": "Fine-mapping results for chr6 locus",
  "nodes":  [{"key": "fm_table", "type": "table", "label": "Credible sets (finemapping)",
              "path": "/abs/path/credible_sets.tsv", "mode": "managed",
              "dest": "results/tables/credible_sets.tsv", "description": "...",
              "groups": ["finemapping"], "tags": [], "meta": {}}],
  "save":   [{"node": "T1", "message": "re-run with new LD panel"}],
  "edges":  [{"from": "PL1", "to": "fm_table", "rel": "produces",
              "params": {"L": 10, "coverage": 0.95}, "script": "SC1",
              "command": "snakemake -j8 finemap", "label": null, "note": ""}],
  "notes":  [{"node": "fm_table", "text": "locus HLA excluded"}],
  "groups": [{"name": "finemapping", "nodes": ["fm_table"], "color": "#3b6fd6"}],
  "final":  [{"node": "F2", "version": null}]
}

`key` is a plan-local handle; edges/notes/groups may reference either keys or
existing node ids. Keys become real ids (T3, F2 ...) on apply unless an "id"
is given explicitly.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sciweave.project import MAX_SNAPSHOT_BYTES, Project, SciWeaveError
from sciweave.schema import EDGE_RELATIONS, MODES, NODE_TYPES, is_scalar_param, now_iso, slugify

# "alpha" deliberately absent: in statistics it is the significance level, not plot opacity
COSMETIC_HINTS = ("font", "color", "colour", "width", "height", "dpi", "theme", "opacity", "title",
                  "legend", "linewidth", "palette", "fontsize", "figsize", "label_size", "marker")
OUTPUT_TYPES = ("table", "figure", "result", "supplement")

TEMPLATE = {
    "sciweave_plan": 1,
    "summary": "what this import adds, in one line",
    "nodes": [
        {"key": "my_table", "type": "table", "label": "Short name (topic)", "path": "/abs/path/to/file.tsv",
         "mode": "managed", "description": "", "groups": [], "tags": []}
    ],
    "save": [],
    "edges": [
        {"from": "PL1", "to": "my_table", "rel": "produces", "params": {"threshold": 5e-8},
         "script": None, "command": None}
    ],
    "notes": [],
    "groups": [],
    "final": [],
}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg): self.errors.append(msg)
    def warn(self, msg): self.warnings.append(msg)
    def ok(self, msg): self.info.append(msg)

    @property
    def passed(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = []
        lines += [f"  ok     {m}" for m in self.info]
        lines += [f"  WARN   {m}" for m in self.warnings]
        lines += [f"  ERROR  {m}" for m in self.errors]
        verdict = ("PASSED" if self.passed else "FAILED") + \
            f"  ({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        return "\n".join(lines + ["", f"plan check {verdict}"])


def load_plan(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SciWeaveError(f"plan file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SciWeaveError(f"plan is not valid JSON: {exc}")


def check(p: Project, plan: dict) -> Report:
    r = Report()
    if plan.get("sciweave_plan") != 1:
        r.error('missing or unsupported "sciweave_plan" (expected 1)')
        return r
    if not plan.get("summary"):
        r.warn('no "summary": say in one line what this import adds (it goes into history)')

    keys: dict[str, dict] = {}
    explicit = [n["id"] for n in plan.get("nodes", []) if n.get("id")]
    for dup in sorted({x for x in explicit if explicit.count(x) > 1}):
        r.error(f"explicit id {dup} is used by more than one node")
    for i, n in enumerate(plan.get("nodes", [])):
        where = f"nodes[{i}]"
        key = n.get("key") or n.get("id")
        if not key:
            r.error(f"{where}: needs a \"key\" (plan-local handle)")
            continue
        where = f"node '{key}'"
        if key in keys:
            r.error(f"{where}: duplicate key")
        if key in p.nodes and not n.get("id"):
            r.error(f"{where}: key collides with existing node id {key}; use another key")
        if n.get("id") and n["id"] in p.nodes:
            r.error(f"{where}: explicit id {n['id']} already exists")
        keys[key] = n
        t = n.get("type")
        if t not in NODE_TYPES:
            r.error(f"{where}: unknown type '{t}' (one of {', '.join(NODE_TYPES)})")
        label = (n.get("label") or "").strip()
        if not label:
            r.error(f"{where}: needs a label")
        elif len(label) > 60:
            r.warn(f"{where}: label is {len(label)} chars; keep it short, e.g. 'Credible sets (finemapping)'")
        mode = n.get("mode", "ref")
        if mode not in MODES:
            r.error(f"{where}: mode must be one of {MODES}")
        path = n.get("path")
        if path:
            src = Path(path).expanduser()
            if not src.exists():
                r.error(f"{where}: path does not exist: {path}")
            else:
                if mode == "managed":
                    if src.is_dir():
                        r.warn(f"{where}: managed directory will be copied but only fingerprinted, "
                               f"not versioned file-by-file; consider mode 'ref' or individual files")
                    elif src.stat().st_size > MAX_SNAPSHOT_BYTES:
                        r.warn(f"{where}: {src.stat().st_size / 1e6:.0f} MB is too big to snapshot; "
                               f"it will be fingerprinted only (consider mode 'ref')")
                    dest = n.get("dest")
                    target = p.resolve(dest) if dest else None
                    if target is None:
                        try:
                            src.resolve().relative_to(p.root)
                        except ValueError:
                            target = p.root / NODE_TYPES.get(t, {"folder": "results/other"})["folder"] / src.name
                    if target is not None and target.exists() and target.resolve() != src.resolve():
                        r.error(f"{where}: destination already exists: {p.rel(target)} (set \"dest\")")
                if t in ("raw",) and mode == "managed" and src.is_file() and src.stat().st_size > 50e6:
                    r.warn(f"{where}: raw data is usually referenced (mode 'ref'), not copied")
                r.ok(f"{where}: {t} '{label}' <- {path} ({mode})")
        elif t not in ("note", "article", "pipeline", "step") and not (n.get("meta") or {}).get("location"):
            r.warn(f"{where}: no path — it will be a conceptual node without versions "
                   f"(set meta.location if it lives elsewhere, e.g. on a cluster)")
        for j, h in enumerate(n.get("history", [])):
            if not isinstance(h, dict) or not h.get("path"):
                r.error(f"{where}: history[{j}] needs a path")
            elif not Path(h["path"]).expanduser().is_file():
                r.error(f"{where}: history file not found: {h['path']}")
        if n.get("history"):
            r.ok(f"{where}: {len(n['history'])} older version(s) imported into its history")
        for g in n.get("groups", []):
            if not isinstance(g, str):
                r.error(f"{where}: groups must be strings")

    def resolve(ref, where) -> str | None:
        if ref in keys:
            return f"@{ref}"
        if ref in p.nodes:
            return ref
        r.error(f"{where}: '{ref}' is neither a plan key nor an existing node id")
        return None

    def type_of(ref):
        if ref in keys:
            return keys[ref].get("type")
        return p.nodes[ref]["type"] if ref in p.nodes else None

    for i, s in enumerate(plan.get("save", [])):
        nid = s.get("node")
        if nid not in p.nodes:
            r.error(f"save[{i}]: unknown node '{nid}'")
        elif not p.nodes[nid]["path"]:
            r.error(f"save[{i}]: {nid} has no path to version")
        else:
            r.ok(f"save {nid}: new version ({s.get('message', '')})")

    adj: dict[str, set] = {}
    for e in p.edges:
        adj.setdefault(e["source"], set()).add(e["target"])
    has_incoming = {e["target"] for e in p.edges}
    green_into: list[tuple[str, str]] = []
    # an output whose provenance already carries parameters on some edge is fine with extra green inputs
    param_targets = {e["target"] for e in p.edges if e.get("params")}
    param_targets |= {e.get("to") for e in plan.get("edges", []) if e.get("params")}
    for i, e in enumerate(plan.get("edges", [])):
        where = f"edges[{i}] {e.get('from')}->{e.get('to')}"
        a, b = resolve(e.get("from"), where), resolve(e.get("to"), where)
        rel = e.get("rel")
        if rel not in EDGE_RELATIONS:
            r.error(f"{where}: unknown rel '{rel}' (one of {', '.join(EDGE_RELATIONS)})")
        params = e.get("params") or {}
        if not isinstance(params, dict):
            r.error(f"{where}: params must be an object")
            params = {}
        for k, v in params.items():
            if not is_scalar_param(v):
                r.error(f"{where}: param '{k}' must be a scalar or list of scalars")
            if any(h in k.lower() for h in COSMETIC_HINTS):
                r.warn(f"{where}: param '{k}' looks cosmetic — keep only result-changing parameters")
        if params and rel in ("code", "part_of", "related", "documents"):
            r.warn(f"{where}: params on a '{rel}' edge are unusual; put them on the feeds/produces/derives edge")
        if rel in ("produces", "derives") and not params and type_of(e.get("to")) in OUTPUT_TYPES:
            green_into.append((where, e.get("to")))
        if e.get("script"):
            sref = e["script"]
            if resolve(sref, where + " script") and type_of(sref) not in ("script", "pipeline", "step"):
                r.error(f"{where}: script '{sref}' must be a script/pipeline/step node")
        if e.get("params_file") and not Path(e["params_file"]).expanduser().exists():
            r.error(f"{where}: params_file not found: {e['params_file']}")
        if a and b:
            if a == b:
                r.error(f"{where}: self-link")
            adj.setdefault(a, set()).add(b)
            has_incoming.add(b)
            if e.get("script"):
                s = resolve(e["script"], where)
                if s:
                    has_incoming.add(b)
                    adj.setdefault(s, set()).add(b)
            r.ok(f"{where}: {rel}" + (f" [{', '.join(f'{k}={v}' for k, v in params.items())}]" if params else ""))

    for where, tgt in green_into:
        if tgt not in param_targets:
            r.warn(f"{where}: green edge (no params) into an output — confirm nothing result-changing was set")

    # cycles over existing + planned edges
    color: dict[str, int] = {}

    def dfs(u) -> bool:
        color[u] = 1
        for v in adj.get(u, ()):
            if color.get(v) == 1 or (color.get(v) is None and dfs(v)):
                return True
        color[u] = 2
        return False

    if any(color.get(u) is None and dfs(u) for u in list(adj)):
        r.error("edges would create a cycle — provenance must be a DAG")

    for key, n in keys.items():
        if n.get("type") in OUTPUT_TYPES and f"@{key}" not in has_incoming:
            r.warn(f"node '{key}': output with no incoming edge — how was it made? add a produces/derives edge")

    for i, nt in enumerate(plan.get("notes", [])):
        resolve(nt.get("node"), f"notes[{i}]")
        if not nt.get("text"):
            r.error(f"notes[{i}]: empty text")
    for i, g in enumerate(plan.get("groups", [])):
        if not g.get("name"):
            r.error(f"groups[{i}]: needs a name")
        for ref in g.get("nodes", []):
            resolve(ref, f"groups[{i}]")
    for i, f in enumerate(plan.get("final", [])):
        resolve(f.get("node"), f"final[{i}]")
    return r


def apply(p: Project, plan: dict, actor: str = "claude") -> dict[str, str]:
    """Apply a checked plan. Returns {plan key -> new node id}."""
    rep = check(p, plan)
    if not rep.passed:
        raise SciWeaveError("plan has errors, not applied:\n" + rep.render())
    backup = json.loads(json.dumps(p.graph))
    hist_size = p.history_file.stat().st_size if p.history_file.exists() else 0
    created_files: list[Path] = []
    ids: dict[str, str] = {}

    def rid(ref: str) -> str:
        return ids.get(ref, ref)

    try:
        # explicit ids first, so an auto id (e.g. step -> ST1) can never take a code the plan reserved
        for n in sorted(plan.get("nodes", []), key=lambda x: not x.get("id")):
            key = n.get("key") or n.get("id")
            node = p.add_node(n["type"], n["label"].strip(), path=n.get("path"), mode=n.get("mode", "ref"),
                              dest=n.get("dest"), description=n.get("description", ""),
                              groups=n.get("groups", []), tags=n.get("tags", []), meta=n.get("meta"),
                              node_id=n.get("id"), actor=actor, message=n.get("message") or plan.get("summary", "imported"),
                              history=n.get("history"))
            if node["meta"].get("origin"):  # add_node copied the file into the project
                created_files.append(p.resolve(node["path"]))
            ids[key] = node["id"]
        for e in plan.get("edges", []):
            p.link(rid(e["from"]), rid(e["to"]), rel=e["rel"], params=e.get("params") or {},
                   script=rid(e["script"]) if e.get("script") else None, command=e.get("command"),
                   note=e.get("note", ""), label=e.get("label"), params_file=e.get("params_file"), actor=actor)
        for s in plan.get("save", []):
            p.save_version(s["node"], message=s.get("message", ""), actor=actor)
        for nt in plan.get("notes", []):
            p.add_note(rid(nt["node"]), nt["text"], actor=actor)
        for g in plan.get("groups", []):
            p.set_group(g["name"], [rid(x) for x in g.get("nodes", [])], color=g.get("color"), actor=actor)
        for f in plan.get("final", []):
            p.mark_final(rid(f["node"]), f.get("version"), actor=actor)
    except Exception:
        p.graph = backup
        if p.history_file.exists():
            with open(p.history_file, "r+b") as fh:
                fh.truncate(hist_size)
        for f in created_files:
            try:
                shutil.rmtree(f) if f.is_dir() else f.unlink()
            except OSError:
                pass
        raise

    p.plans_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("+0000", "Z")
    saved = p.plans_dir / f"{stamp}-{slugify(plan.get('summary', 'plan'), 32)}.json"
    saved.write_text(json.dumps({**plan, "_applied": {"ts": now_iso(), "actor": actor, "ids": ids}},
                                indent=2, ensure_ascii=False), encoding="utf-8")
    p.log("plan_applied", detail=plan.get("summary", ""), actor=actor, nodes=list(ids.values()),
          plan=p.rel(saved))
    p.commit()
    return ids
