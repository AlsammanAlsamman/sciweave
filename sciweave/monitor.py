"""The monitor round: what has changed in the project since the last look?

One round is cheap and deterministic (file metadata only, hashing only files that
changed): it never applies anything, it only *suggests*. The Claude skill
`sciweave-monitor` runs a round about once an hour and turns the suggestions into
questions / an import plan; `sciweave monitor` runs rounds in a terminal;
`sciweave suggest` runs one round now.

State lives in .sciweave/monitor.json:
  last_round   ISO time of the last round
  seen         {path: [size, mtime_ns]} of every untracked file already reported (or baselined)
  ignore       glob patterns never to report
  roots        extra folders to watch (e.g. a pipeline's results folder outside the project)
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sciweave.project import Project
from sciweave.schema import now_iso

DEFAULT_IGNORE = [
    ".sciweave/*", ".git/*", "*/.git/*", "*/__pycache__/*", "*.pyc", "*/.ipynb_checkpoints/*", "*/node_modules/*",
    "*.tmp", "*.swp", "~$*", "*/~$*", ".DS_Store", "*/.DS_Store", "Thumbs.db", "*.lock",
    "*.done", "*/.snakemake/*", "*/logs/*", "*.log", "*.bak", "brainny-out/*", "SCIWEAVE.md", "sciweave.html",
]
TYPE_BY_EXT = {
    "figure": {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".tif", ".tiff", ".gif", ".webp"},
    "table": {".tsv", ".csv", ".xlsx", ".xls", ".txt", ".tab", ".parquet"},
    "script": {".py", ".r", ".R", ".sh", ".smk", ".nf", ".ipynb", ".jl", ".pl", ".sbatch", ".wdl"},
    "section": {".tex", ".md", ".docx", ".rmd", ".qmd"},
}
MAX_FILES = 200_000
STATE = "monitor.json"


def _guess_type(path: Path) -> str:
    ext = path.suffix.lower() if path.suffix != ".R" else ".R"
    name = path.name.lower()
    if name.endswith((".tsv.gz", ".csv.gz", ".txt.gz")):
        return "table"
    for t, exts in TYPE_BY_EXT.items():
        if ext in exts or path.suffix in exts:
            return t
    return "result"


def load_state(p: Project) -> dict:
    f = p.state / STATE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_round": None, "seen": {}, "ignore": [], "roots": [], "rounds": []}


def save_state(p: Project, st: dict) -> None:
    (p.state / STATE).write_text(json.dumps(st, indent=1), encoding="utf-8")


def _ignored(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) for pat in patterns)


def _tracked(p: Project) -> tuple[dict[Path, str], list[tuple[Path, str]]]:
    files, dirs = {}, []
    for nid, n in p.nodes.items():
        if not n["path"]:
            continue
        path = p.resolve(n["path"]).resolve()
        if path.is_dir():
            dirs.append((path, nid))
        else:
            files[path] = nid
    return files, dirs


def _walk(base: Path, patterns: list[str], rel_to: Path, skip_dirs: set[Path], budget: list[int]):
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if not x.startswith(".") and (d / x).resolve() not in skip_dirs
                       and not _ignored(_rel(d / x, rel_to) + "/x", patterns)]
        for fn in filenames:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            f = d / fn
            if fn.startswith(".") or _ignored(_rel(f, rel_to), patterns):
                continue
            yield f


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _nearest_node(p: Project, f: Path, files: dict[Path, str]) -> str | None:
    """Tracked node in the same folder or the closest ancestor folder."""
    best, best_depth = None, -1
    for tp, nid in files.items():
        try:
            f.resolve().relative_to(tp.parent)
        except ValueError:
            continue
        depth = len(tp.parent.parts)
        if depth > best_depth:
            best, best_depth = nid, depth
    return best


def _looks_like_version_of(p: Project, f: Path, files: dict[Path, str]) -> str | None:
    """An untracked file whose name is close to a tracked file of the same kind in the same
    folder (Figure_2_v2.png next to Figure_2.png) is probably a new version of it."""
    stem = f.stem.lower()
    best, score = None, 0.0
    for tp, nid in files.items():
        if tp.parent != f.parent.resolve() or tp.suffix.lower() != f.suffix.lower():
            continue
        r = difflib.SequenceMatcher(None, stem, tp.stem.lower()).ratio()
        if r > score:
            best, score = nid, r
    return best if score >= 0.72 else None


def run_round(p: Project, update: bool = True, include_stale: bool = True) -> dict:
    """One monitor round. Returns {"updated", "new", "stale", "missing", "baseline", ...}.

    With update=True the untracked files found are remembered as seen, so the next round
    reports only what appeared after this one. The first round ever is a baseline: it
    remembers everything and reports only a per-folder summary, never a flood.
    """
    st = load_state(p)
    first = st.get("last_round") is None
    patterns = DEFAULT_IGNORE + st.get("ignore", [])
    files, dirs = _tracked(p)
    status = p.status()

    updated = [{"node": nid, "label": p.nodes[nid]["label"], "type": p.nodes[nid]["type"],
                "path": p.nodes[nid]["path"], "reasons": s["reasons"]}
               for nid, s in status.items() if s["modified"]]
    missing = [{"node": nid, "label": p.nodes[nid]["label"], "path": p.nodes[nid]["path"]}
               for nid, s in status.items() if s["missing"]]
    stale = [{"node": nid, "label": p.nodes[nid]["label"], "reasons": s["reasons"][:3]}
             for nid, s in status.items() if s["stale"]] if include_stale else []

    # folders to scan: the project itself + extra roots + folders of tracked files outside it
    roots = [p.root] + [Path(r) for r in st.get("roots", []) if Path(r).exists()]
    outside = {tp.parent for tp in files if not str(tp).startswith(str(p.root))}
    skip = {d for d, _ in dirs}            # a ref folder node is one unit: changes show as "updated"
    seen = st.get("seen", {})
    budget = [MAX_FILES]
    new, now_seen = [], {}
    candidates: list[Path] = []
    for r in roots:
        candidates.extend(_walk(r, patterns, p.root, skip, budget))
    for d in outside:
        if d.exists():
            candidates.extend(f for f in d.iterdir() if f.is_file() and not _ignored(f.name, patterns))
    for f in candidates:
        rf = f.resolve()
        if rf in files:
            continue
        key = _rel(rf, p.root)
        try:
            stt = rf.stat()
        except OSError:
            continue
        sig = [stt.st_size, stt.st_mtime_ns]
        now_seen[key] = sig
        if seen.get(key) == sig:
            continue
        item = {"path": key, "size": stt.st_size, "modified": datetime.fromtimestamp(stt.st_mtime, timezone.utc)
                .replace(microsecond=0).isoformat(), "type": _guess_type(rf),
                "changed_since_seen": key in seen}
        v = _looks_like_version_of(p, rf, files)
        if v:
            item["version_of"] = v
        else:
            near = _nearest_node(p, rf, files)
            if near:
                item["near"] = near
        anchor = item.get("version_of") or item.get("near")
        if anchor and p.nodes[anchor].get("step"):
            item["step"] = p.nodes[anchor]["step"]      # suggested analysis step
        new.append(item)
    new.sort(key=lambda x: x["modified"], reverse=True)

    result = {"ts": now_iso(), "first_round": first, "updated": updated, "missing": missing, "stale": stale}
    if first:
        by_folder: dict[str, int] = {}
        for it in new:
            folder = str(Path(it["path"]).parent).replace("\\", "/")
            by_folder[folder] = by_folder.get(folder, 0) + 1
        result["baseline"] = {"untracked": len(new),
                              "top_folders": sorted(by_folder.items(), key=lambda x: -x[1])[:12]}
        result["new"] = []
    else:
        result["new"] = new
    if budget[0] <= 0:
        result["truncated"] = True

    if update:
        seen.update(now_seen)
        st["seen"] = seen
        st["last_round"] = result["ts"]
        summary = {"ts": result["ts"], "updated": len(updated), "new": len(result["new"]), "stale": len(stale),
                   "missing": len(missing)}
        st["rounds"] = (st.get("rounds", []) + [summary])[-100:]
        save_state(p, st)
        (p.state / "monitor_latest.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        if updated or result["new"] or missing:
            p.log("monitor_round", actor="monitor",
                  detail=f"{len(updated)} updated, {len(result['new'])} new, {len(stale)} stale, {len(missing)} missing")
    return result


def minutes_since_last(p: Project) -> float | None:
    st = load_state(p)
    if not st.get("last_round"):
        return None
    t = datetime.fromisoformat(st["last_round"])
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def has_news(r: dict) -> bool:
    return bool(r["updated"] or r["new"] or r["missing"])


def render(p: Project, r: dict, limit: int = 25) -> str:
    """Human / Claude readable round report. Empty string when there is nothing to say."""
    out = []
    if r.get("first_round"):
        b = r["baseline"]
        out.append(f"SciWeave monitor: baseline taken, {b['untracked']} untracked file(s) remembered; "
                   f"from now on only new or changed files are reported.")
        for folder, n in b["top_folders"][:8]:
            out.append(f"  {n:5}  {folder}/")
    if r["updated"]:
        out.append(f"UPDATED ({len(r['updated'])}): tracked files changed on disk. Save a new version, and say why")
        for u in r["updated"][:limit]:
            out.append(f"  {u['node']:6} {u['label'][:48]:48}  sciweave save {u['node']} -m \"...\" --why \"...\"")
    if r["new"]:
        out.append(f"NEW ({len(r['new'])}): results not in the network yet")
        for it in r["new"][:limit]:
            hint = (f"new version of {it['version_of']}?" if it.get("version_of")
                    else f"near {it['near']}" if it.get("near") else "")
            if it.get("step"):
                hint += f"  [step {it['step']}]"
            out.append(f"  {it['type']:8} {it['path'][:70]:70} {hint}")
        if len(r["new"]) > limit:
            out.append(f"  ... and {len(r['new']) - limit} more (sciweave suggest --json)")
    if r["missing"]:
        out.append(f"MISSING ({len(r['missing'])}): tracked files no longer on disk")
        for m in r["missing"][:limit]:
            out.append(f"  {m['node']:6} {m['path']}")
    if (r["updated"] or r["new"] or r["missing"]) and r["stale"]:
        out.append(f"STALE ({len(r['stale'])}): built from inputs that changed since, regenerate when ready")
        for s in r["stale"][:10]:
            out.append(f"  {s['node']:6} {s['label'][:48]:48} {s['reasons'][0] if s['reasons'] else ''}")
    if r.get("truncated"):
        out.append(f"(stopped after {MAX_FILES} files; add ignore patterns with `sciweave ignore`)")
    return "\n".join(out)
