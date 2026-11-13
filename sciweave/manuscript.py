"""LaTeX manuscript editing for article nodes: files, compile, and reviewable change proposals.

An article node's folder holds the LaTeX sources (`<folder>/latex/` for SciWeave's template,
else the folder itself). The user edits them in the dashboard's LaTeX editor; Claude never
writes them directly but *proposes* changes (`sciweave tex propose`). A proposal is a diff
against the file as it was; the user accepts or rejects it hunk by hunk, and accepted hunks
are applied to the current text (so the user can keep typing while a proposal waits).

State lives in `.sciweave/manuscripts/<article id>/`: `proposals/P<n>.json`, `snapshots/`
(the previous text of every file before each save or accepted change) and `log.jsonl`.
The graph is only touched by `attach` (article + section nodes); everything else is local
to the manuscript, so the editor never waits on the graph lock.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from sciweave.project import Project, SciWeaveError
from sciweave.schema import now_iso

EDITABLE_EXT = {".tex", ".bib", ".sty", ".cls", ".bst", ".md", ".txt"}
ASSET_EXT = {".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps"}
SKIP_DIRS = {"build", ".git", "__pycache__", "node_modules"}
SUBFOLDERS = ["latex/sections", "notes"]  # figures/tables stay where the author keeps them (\graphicspath)
# Working notes shown as their own tabs in the editor (Markdown, in <article folder>/notes/), never in the PDF.
REVISIONS = "revisions/revisions.tsv"  # revision register (R numbers), see Manuscript.revisions()
NOTES = [("claims", "Claims", "notes/claims.md"), ("structure", "Structure", "notes/structure.md"),
         ("suggestions", "Suggestions", "notes/suggestions.md"), ("ideas", "Ideas", "notes/ideas.md"),
         ("removed", "Removed", "notes/removed.md"),  # text taken out of the manuscript, kept with its evidence
         ("words", "Word count", "notes/wordcount.md")]  # generated at every compile (wordcount.py)
_LOCKS: dict[str, threading.Lock] = {}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _lock(key: str) -> threading.Lock:
    return _LOCKS.setdefault(key, threading.Lock())


def _perl_dir() -> str | None:
    """The folder of Git for Windows' perl when perl is not on PATH (None if on PATH or not found)."""
    if shutil.which("perl"):
        return None
    git = shutil.which("git")
    if not git:
        return None
    for up in Path(git).resolve().parents[:3]:  # <Git>/cmd/git.exe or <Git>/mingw64/bin/git.exe
        if (up / "usr" / "bin" / "perl.exe").exists():
            return str(up / "usr" / "bin")
    return None


def _tokens(s: str) -> list[str]:
    # words, whitespace runs and punctuation as separate tokens: diffs read like track changes
    return re.findall(r"\s+|\d+(?:[.,]\d+)*|\w+|[^\w\s]", s)  # 0.765 and 12,780 stay one token


def word_diff(old: str, new: str) -> list[list[str]]:
    """[[op, text], ...] with op in '=', '-', '+'; short unchanged runs between two changes are
    folded into the change so an edited sentence reads as one replacement."""
    a, b = _tokens(old), _tokens(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if old and new and sm.ratio() < 0.45:  # a rewrite, not an edit: show old struck, new added
        return [["-", old], ["+", new]]
    ops = sm.get_opcodes()
    out: list[list[str]] = []
    pend_old, pend_new = [], []

    def flush():
        if pend_old:
            out.append(["-", "".join(pend_old)])
        if pend_new:
            out.append(["+", "".join(pend_new)])
        pend_old.clear()
        pend_new.clear()

    for k, (op, i1, i2, j1, j2) in enumerate(ops):
        short = sum(1 for t in a[i1:i2] if not t.isspace()) < 3
        if op == "equal" and not (short and 0 < k < len(ops) - 1):
            flush()
            out.append(["=", "".join(a[i1:i2])])
        else:
            pend_old.extend(a[i1:i2])
            pend_new.extend(b[j1:j2])
    flush()
    return out


def _attach_nodes(p: Project, folder: Path, title: str, main: str, step: str | None,
                  node_id: str | None, actor: str) -> dict:
    tex_root = folder / "latex"
    art = p.add_node("article", title, path=str(tex_root / main), mode="ref", node_id=node_id,
                     meta={"folder": str(folder), "main": main, "editor": "latex"},
                     step=step, actor=actor, message="manuscript attached")
    secs = sorted((tex_root / "sections").glob("*.tex")) if (tex_root / "sections").exists() else []
    order = _section_order(tex_root / main, secs)
    for f in order:
        sec = p.add_node("section", f"{f.stem.replace('_', ' ').capitalize()} ({title[:24]})", path=str(f),
                         mode="ref", meta={"section": f.stem, "article": art["id"]}, step=step,
                         actor=actor, message="section attached")
        p.link(sec["id"], art["id"], rel="part_of", label=f.stem.replace("_", " ").capitalize(), actor=actor)
    p.log("article_attached", node=art["id"], detail=str(folder), actor=actor)
    return art


def _section_order(main: Path, secs: list[Path]) -> list[Path]:
    """Sections in the order main.tex inputs them (unlisted ones last, alphabetically)."""
    try:
        text = main.read_text(encoding="utf-8")
    except OSError:
        return secs
    names = re.findall(r"\\(?:input|include)\{sections/([^}]+?)(?:\.tex)?\}", text)
    rank = {n: i for i, n in enumerate(names)}
    return sorted(secs, key=lambda f: (rank.get(f.stem, len(rank)), f.stem))


def attach(p: Project, folder: str | Path, title: str, main: str = "main.tex", step: str | None = None,
           node_id: str | None = None, actor: str = "user") -> dict:
    """Register an existing (or new, empty) manuscript folder as an article node, in place.
    Unlike `article new` nothing is copied: the folder stays where the author keeps it."""
    folder = Path(folder).expanduser().resolve()
    for n in p.nodes.values():
        f = p.resolve(n["meta"].get("folder")) if n["type"] == "article" else None
        if f is not None and Path(f).resolve() == folder:
            raise SciWeaveError(f"{folder} is already article {n['id']}")
    for sub in SUBFOLDERS:
        (folder / sub).mkdir(parents=True, exist_ok=True)
    tex_main = folder / "latex" / main
    if not tex_main.exists():
        tex_main.write_text("\\documentclass[11pt]{article}\n\\begin{document}\n\n\\end{document}\n", encoding="utf-8")
    return _attach_nodes(p, folder, title, main, step, node_id, actor)


class Manuscript:
    def __init__(self, p: Project, article_id: str):
        art = p.node(article_id)
        if art["type"] != "article":
            raise SciWeaveError(f"{article_id} is not an article")
        self.p, self.art, self.id = p, art, article_id
        folder = p.resolve(art["meta"].get("folder"))
        if folder is None:
            raise SciWeaveError(f"{article_id} has no folder")
        self.folder = Path(folder).resolve()
        self.root = self.folder / "latex" if (self.folder / "latex").is_dir() else self.folder
        self.main = art["meta"].get("main", "main.tex")
        self.state = p.state / "manuscripts" / article_id
        self.lock = _lock(str(self.state))

    # ------------------------------------------------------------ files ----
    def _path(self, rel: str, must_exist: bool = True) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        f = (self.root / rel).resolve()
        if self.root.resolve() not in f.parents:
            raise SciWeaveError(f"{rel} is outside the manuscript")
        if f.suffix.lower() not in EDITABLE_EXT:
            raise SciWeaveError(f"{rel}: only {', '.join(sorted(EDITABLE_EXT))} files are editable")
        if must_exist and not f.is_file():
            raise SciWeaveError(f"{rel} does not exist")
        return f

    def rel(self, f: Path) -> str:
        return f.resolve().relative_to(self.root.resolve()).as_posix()

    def files(self) -> list[dict]:
        pending: dict[str, int] = {}
        for pr in self.proposals():
            pending[pr["file"]] = pending.get(pr["file"], 0) + 1
        nodes = {}
        for n in self.p.nodes.values():
            if n.get("path"):
                r = self.p.resolve(n["path"])
                if r is not None:
                    nodes[str(Path(r).resolve())] = n["id"]
        out = []
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                f = Path(dirpath) / name
                if f.suffix.lower() not in EDITABLE_EXT:
                    continue
                r = self.rel(f)
                text = f.read_text(encoding="utf-8", errors="replace")
                out.append({"path": r, "size": f.stat().st_size, "mtime": f.stat().st_mtime,
                            "hash": _hash(text), "pending": pending.get(r, 0), "node": nodes.get(str(f.resolve())),
                            "main": r == self.main, "generated": text.startswith("% GENERATED")})
        out.sort(key=lambda x: (not x["main"], x["path"].count("/"), x["path"]))
        return out

    def read(self, rel: str) -> dict:
        f = self._path(rel)
        text = f.read_text(encoding="utf-8")
        return {"path": self.rel(f), "text": text, "hash": _hash(text)}

    def _snapshot(self, f: Path, text: str, reason: str) -> None:
        d = self.state / "snapshots" / self.rel(f).replace("/", "__")
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        (d / f"{stamp}.tex").write_text(text, encoding="utf-8")
        snaps = sorted(d.iterdir())
        for old in snaps[:-200]:  # keep the last 200 per file
            old.unlink()

    def _log(self, event: str, actor: str, **extra) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now_iso(), "event": event, "actor": actor, **extra}
        with open(self.state / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _write(self, f: Path, text: str, reason: str, actor: str) -> str:
        if f.exists():
            old = f.read_text(encoding="utf-8")
            if old == text:
                return _hash(text)
            self._snapshot(f, old, reason)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, f)
        return _hash(text)

    def save(self, rel: str, text: str, base_hash: str | None = None, actor: str = "user") -> dict:
        """The editor's save. base_hash is the text the editor started from: if the file changed
        on disk since (another editor, an accepted proposal), the save is refused, not merged."""
        with self.lock:
            f = self._path(rel, must_exist=False)
            if f.exists() and base_hash:
                cur = _hash(f.read_text(encoding="utf-8"))
                if cur != base_hash:
                    raise SciWeaveError(f"{rel} changed on disk since you opened it; reload it (your text is kept in the editor)")
            h = self._write(f, text, "save", actor)
            self._log("save", actor, file=self.rel(f), hash=h)
            return {"ok": True, "hash": h}

    def snapshots(self, rel: str) -> list[dict]:
        d = self.state / "snapshots" / rel.replace("/", "__")
        if not d.exists():
            return []
        return [{"id": s.stem, "size": s.stat().st_size} for s in sorted(d.iterdir(), reverse=True)]

    def snapshot_text(self, rel: str, snap: str) -> str:
        f = self.state / "snapshots" / rel.replace("/", "__") / f"{snap}.tex"
        if not f.exists():
            raise SciWeaveError(f"no snapshot {snap} of {rel}")
        return f.read_text(encoding="utf-8")

    # ------------------------------------------------------------ notes ----
    # Claims, structure, suggestions and ideas: Markdown files next to the manuscript, edited in their
    # own editor tabs. The user saves them directly (no proposals); every save keeps a snapshot.
    def _note(self, key: str) -> Path:
        for k, _label, rel in NOTES:
            if k == key:
                return self.folder / rel
        raise SciWeaveError(f"unknown note {key!r} (one of {', '.join(k for k, _, _ in NOTES)})")

    def notes(self) -> list[dict]:
        out = []
        for k, label, rel in NOTES:
            f = self.folder / rel
            text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
            out.append({"key": k, "label": label, "path": rel, "exists": f.is_file(), "hash": _hash(text)})
        return out

    def note_read(self, key: str) -> dict:
        f = self._note(key)
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        return {"key": key, "path": f.relative_to(self.folder).as_posix(), "exists": f.is_file(),
                "text": text, "hash": _hash(text)}

    def note_save(self, key: str, text: str, base_hash: str | None = None, actor: str = "user") -> dict:
        with self.lock:
            f = self._note(key)
            old = f.read_text(encoding="utf-8") if f.is_file() else ""
            if base_hash and _hash(old) != base_hash:
                raise SciWeaveError(f"{f.name} changed on disk since you opened it; reload it (your text is kept in the editor)")
            if old != text:
                if f.is_file():
                    d = self.state / "snapshots" / ("notes__" + f.name)
                    d.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
                    (d / f"{stamp}.md").write_text(old, encoding="utf-8")
                    for s in sorted(d.iterdir())[:-200]:
                        s.unlink()
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
                tmp.write_text(text, encoding="utf-8", newline="")
                os.replace(tmp, f)
                self._log("note-save", actor, note=key)
            return {"ok": True, "hash": _hash(text)}

    # -------------------------------------------------------- proposals ----
    def _pdir(self) -> Path:
        d = self.state / "proposals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next_pid(self) -> str:
        nums = [int(m.group(1)) for f in self._pdir().glob("P*.json") if (m := re.match(r"P(\d+)$", f.stem))]
        return f"P{max(nums, default=0) + 1}"

    def proposal(self, pid: str) -> dict:
        f = self._pdir() / f"{pid}.json"
        if not f.exists():
            raise SciWeaveError(f"no proposal {pid} in {self.id}")
        return json.loads(f.read_text(encoding="utf-8"))

    def _store(self, pr: dict) -> None:
        f = self._pdir() / f"{pr['id']}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)

    def proposals(self, status: str | None = "open", file: str | None = None) -> list[dict]:
        out = []
        for f in self._pdir().glob("P*.json"):
            pr = json.loads(f.read_text(encoding="utf-8"))
            if (status is None or pr["status"] == status) and (file is None or pr["file"] == file):
                out.append(pr)
        return sorted(out, key=lambda x: int(x["id"][1:]))

    @staticmethod
    def _hunks(base: str, new: str, spans: list[tuple[int, int]] | None = None) -> list[dict]:
        """Line hunks base -> new. Hunks inside one find/replace edit (spans: base line ranges of the
        edits) are merged, so each edit is reviewed as one change, not as line-alignment artefacts."""
        a, b = base.splitlines(keepends=True), new.splitlines(keepends=True)
        raw = [[i1, i2, j1, j2] for op, i1, i2, j1, j2 in
               difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal"]
        merged: list[list[int]] = []
        for h in raw:
            if merged:
                p = merged[-1]
                if any(s <= p[0] < e and s <= h[0] < e for s, e in spans or []):
                    p[1], p[3] = h[1], h[3]
                    continue
            merged.append(list(h))
        hunks = []
        for i1, i2, j1, j2 in merged:
            old, nw = "".join(a[i1:i2]), "".join(b[j1:j2])
            hunks.append({"n": len(hunks) + 1, "start": i1, "end": i2, "old": a[i1:i2], "new": b[j1:j2],
                          "before": a[max(0, i1 - 2):i1], "after": a[i2:i2 + 2],
                          "words": word_diff(old, nw), "state": "open"})
        return hunks

    def propose(self, rel: str, new_text: str, message: str = "", actor: str = "claude",
                _spans: list[tuple[int, int]] | None = None) -> dict:
        with self.lock:
            f = self._path(rel, must_exist=False)
            base = f.read_text(encoding="utf-8") if f.exists() else ""
            if base == new_text:
                raise SciWeaveError(f"proposal for {rel} changes nothing")
            hunks = self._hunks(base, new_text, _spans)
            pr = {"id": self._next_pid(), "file": self.rel(f) if f.exists() else rel.replace("\\", "/"),
                  "new_file": not f.exists(), "message": message, "actor": actor, "created": now_iso(),
                  "status": "open", "base_hash": _hash(base), "hunks": hunks}
            self._store(pr)
            self._log("propose", actor, file=pr["file"], proposal=pr["id"], hunks=len(hunks), message=message)
            return pr

    def propose_edits(self, rel: str, edits: list[dict], message: str = "", actor: str = "claude") -> dict:
        """edits: [{"find": ..., "replace": ..., "nth": optional 1-based}] applied to the current text.
        Each find must occur exactly once (or name `nth`), so an edit can never land in the wrong place."""
        text = self.read(rel)["text"]
        base, spans = text, []
        for e in edits:  # each edit's line range in the base text: its lines review as one change
            pos = base.find(e["find"])
            for _ in range((e.get("nth") or 1) - 1):
                pos = base.find(e["find"], pos + 1) if pos >= 0 else -1
            if pos >= 0:
                spans.append((base.count("\n", 0, pos), base.count("\n", 0, pos + len(e["find"])) + 1))
        for i, e in enumerate(edits, 1):
            find, repl = e["find"], e.get("replace", "")
            count = text.count(find)
            if count == 0:
                raise SciWeaveError(f"edit {i}: text not found in {rel}: {find[:80]!r}")
            nth = e.get("nth")
            if count > 1 and not nth:
                raise SciWeaveError(f"edit {i}: text occurs {count} times in {rel}; add more context or \"nth\"")
            pos = -1
            for _ in range(nth or 1):
                pos = text.find(find, pos + 1)
                if pos < 0:
                    raise SciWeaveError(f"edit {i}: occurrence {nth} not found")
            text = text[:pos] + repl + text[pos + len(find):]
        pr = self.propose(rel, text, message=message, actor=actor, _spans=spans)
        # keep each hunk's own find/replace edits: if the author edits the same line (another proposal,
        # their own typing) the hunk can still be applied at text level when its find is still unique
        changed = False
        for h in pr["hunks"]:
            lo, hi = h["start"], h["start"] + max(len(h["old"]), 1)
            mine = [{"find": e["find"], "replace": e.get("replace", "")} for e, sp in zip(edits, spans)
                    if sp[0] < hi and lo < sp[1] and not e.get("nth")]
            if mine:
                h["edits"] = mine
                changed = True
        if changed:
            self._store(pr)
        return pr

    @staticmethod
    def _locate(lines: list[str], h: dict) -> int | None:
        """Where hunk h applies in the current lines, or None. Exact position first (nothing changed
        around it), else the unique place where its old lines (or, for an insertion, its context) sit."""
        old, before, after = h["old"], h["before"], h["after"]
        def fits(i: int) -> bool:
            return lines[i:i + len(old)] == old
        if old:
            if h["start"] + len(old) <= len(lines) and fits(h["start"]):
                return h["start"]
            hits = [i for i in range(len(lines) - len(old) + 1) if fits(i)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:  # several: the one whose preceding lines match the recorded context
                ctx = [i for i in hits if lines[max(0, i - len(before)):i] == before]
                return ctx[0] if len(ctx) == 1 else None
            return None
        # pure insertion: anchor on the context before (or after) it
        if before:
            hits = [i + len(before) for i in range(len(lines) - len(before) + 1) if lines[i:i + len(before)] == before]
        elif after:
            hits = [i for i in range(len(lines) - len(after) + 1) if lines[i:i + len(after)] == after]
        else:
            hits = [0] if not lines or h["start"] == 0 else [len(lines)]
        if h["start"] in hits:
            return h["start"]
        return hits[0] if len(hits) == 1 else None

    def resolve(self, pid: str, accept=(), reject=(), actor: str = "user") -> dict:
        """Accept and/or reject hunks (1-based numbers, or "all"). Accepted hunks are applied to the
        file as it is now; a hunk whose text has changed under it is marked 'conflict', never forced."""
        with self.lock:
            pr = self.proposal(pid)
            if pr["status"] != "open":
                raise SciWeaveError(f"{pid} is already {pr['status']}")
            open_n = [h["n"] for h in pr["hunks"] if h["state"] == "open"]
            acc = open_n if accept == "all" else [int(x) for x in accept]
            rej = open_n if reject == "all" else [int(x) for x in reject]
            if set(acc) & set(rej):
                raise SciWeaveError("a hunk cannot be both accepted and rejected")
            bad = [n for n in acc + rej if n not in open_n]
            if bad:
                raise SciWeaveError(f"{pid}: hunk(s) {bad} are not open (open: {open_n})")
            f = self._path(pr["file"], must_exist=not pr.get("new_file"))
            text = f.read_text(encoding="utf-8") if f.exists() else ""
            lines = text.splitlines(keepends=True)
            placed, conflicts = [], []
            for h in pr["hunks"]:
                if h["n"] in acc:
                    at = self._locate(lines, h)
                    (placed if at is not None else conflicts).append((at, h))
            # apply bottom-up so earlier positions stay valid; refuse overlapping placements
            placed.sort(key=lambda x: x[0], reverse=True)
            last = None
            for at, h in placed:
                end = at + len(h["old"])
                if last is not None and end > last:
                    conflicts.append((at, h))
                    continue
                lines[at:end] = h["new"]
                h["state"] = "accepted"
                last = at
            for h in pr["hunks"]:
                if h["n"] in rej:
                    h["state"] = "rejected"
            new_text = "".join(lines)
            # a hunk whose lines changed (e.g. another accepted proposal on the same paragraph) is still
            # applied when it carries its own edits and every find is still exactly once in the text
            still = []
            for at, h in conflicts:
                eds = h.get("edits") or []
                if eds and all(new_text.count(e["find"]) == 1 for e in eds):
                    for e in eds:
                        new_text = new_text.replace(e["find"], e["replace"], 1)
                    h["state"], h["rebased"] = "accepted", True
                else:
                    still.append((at, h))
            for _, h in still:
                h["state"] = "conflict"
            conflicts = still
            if any(h["state"] == "accepted" for h in pr["hunks"]) and new_text != text:
                self._write(f, new_text, f"accept {pid}", actor)
            if all(h["state"] != "open" for h in pr["hunks"]):
                pr["status"] = ("accepted" if all(h["state"] == "accepted" for h in pr["hunks"]) else
                                "rejected" if all(h["state"] == "rejected" for h in pr["hunks"]) else "resolved")
            pr.setdefault("decisions", []).append({"ts": now_iso(), "actor": actor, "accepted": acc, "rejected": rej,
                                                    "conflicts": [h["n"] for _, h in conflicts]})
            self._store(pr)
            self._log("resolve", actor, proposal=pid, file=pr["file"], accepted=acc, rejected=rej,
                      conflicts=[h["n"] for _, h in conflicts])
            return {"ok": True, "proposal": pr, "hash": _hash(new_text),
                    "conflicts": [h["n"] for _, h in conflicts]}

    def withdraw(self, pid: str, actor: str = "claude") -> dict:
        with self.lock:
            pr = self.proposal(pid)
            if pr["status"] != "open":
                raise SciWeaveError(f"{pid} is already {pr['status']}")
            pr["status"] = "withdrawn"
            self._store(pr)
            self._log("withdraw", actor, proposal=pid)
            return pr

    # ---------------------------------------------------------- compile ----
    @staticmethod
    def engine() -> list[str] | None:
        if shutil.which("latexmk"):
            return ["latexmk", "-pdf", "-interaction=nonstopmode", "-file-line-error", "-halt-on-error",
                    "-outdir=build"]
        if shutil.which("tectonic"):
            return ["tectonic", "--outdir", "build"]
        if shutil.which("pdflatex"):
            return ["pdflatex", "-interaction=nonstopmode", "-file-line-error", "-halt-on-error",
                    "-output-directory=build"]
        return None

    def pdf_path(self, job: str | None = None) -> Path:
        return self.root / "build" / ((job or Path(self.main).stem) + ".pdf")

    def shown_pdf(self) -> Path:
        """What the preview shows: the fresh PDF, else the last good one (a failed run removes it)."""
        pdf = self.pdf_path()
        return pdf if pdf.exists() else pdf.with_name(pdf.stem + ".last-good.pdf")

    def compile(self, timeout: int = 300, actor: str = "user", submission: bool = False,
                link_base: str | None = None) -> dict:
        """Build the PDF. `submission` builds `<main>_submission.pdf` (evidence markers hidden);
        `link_base` (the server's URL for this article's files) makes the working PDF's links open
        through SciWeave, so they work in the browser preview."""
        from sciweave.evidence import Evidence, SUBMISSION_JOB
        cmd = self.engine()
        if cmd is None:
            raise SciWeaveError("no LaTeX engine found (install MiKTeX/TeX Live with latexmk, or tectonic)")
        if not (self.root / self.main).exists():
            raise SciWeaveError(f"{self.main} not found in {self.root}")
        job = SUBMISSION_JOB if submission else Path(self.main).stem
        with _lock(str(self.state) + ":compile"):
            (self.root / "build").mkdir(exist_ok=True)
            t0 = time.time()
            ev = Evidence(self)
            evrep = ev.build() if ev.enabled() else None
            if not submission:
                try:
                    from sciweave.wordcount import build as wordcount
                    wordcount(self.root / self.main, self.folder / "notes" / "wordcount.md")
                except Exception as e:  # a count must never block the build
                    self._log("wordcount-failed", actor, error=str(e)[:200])
            mode = self.root / "build" / f"{job}.swmode.tex"
            want = "\\def\\swlinkbase{%s}\n" % link_base if link_base and not submission else ""
            prev_f = mode.with_suffix(".prev")
            changed = (prev_f.read_text(encoding="utf-8") if prev_f.exists() else "") != want
            if want:
                mode.write_text(want, encoding="utf-8")
            elif mode.exists():
                mode.unlink()
            prev_f.write_text(want, encoding="utf-8")
            jobarg = [f"-jobname={job}"] if submission and cmd[0] == "latexmk" else []
            if changed and cmd[0] == "latexmk":
                jobarg.append("-g")  # links changed (file vs server): latexmk cannot see that by itself
            try:
                # bibtex runs inside build/: let it find ibliography{dir/refs} relative to the sources
                env = dict(os.environ)
                for var in ("BIBINPUTS", "BSTINPUTS"):
                    env[var] = str(self.root) + os.pathsep + env.get(var, "")
                if cmd[0] == "latexmk":
                    perl_dir = _perl_dir()
                    if perl_dir:  # MiKTeX's latexmk needs perl; Git for Windows ships one
                        env["PATH"] = env.get("PATH", "") + os.pathsep + perl_dir
                r = subprocess.run(cmd + jobarg + [self.main], cwd=self.root, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout,
                                   stdin=subprocess.DEVNULL, env=env)
                out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
            except subprocess.TimeoutExpired:
                out, code = f"timed out after {timeout} s", -1
            pdf = self.pdf_path(job)
            if code == 0 and pdf.exists() and not submission:
                shutil.copy2(pdf, pdf.with_name(pdf.stem + ".last-good.pdf"))
            log_file = self.root / "build" / (job + ".log")
            log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else out
            res = {"ok": code == 0, "code": code, "seconds": round(time.time() - t0, 1), "ts": now_iso(),
                   "engine": cmd[0], "job": job, "pdf": pdf.exists(), "errors": parse_errors(log, self.root),
                   "warnings": parse_warnings(log), "tail": log[-4000:],
                   "evidence_problems": (evrep or {}).get("problems", [])}
            self.state.mkdir(parents=True, exist_ok=True)
            if not submission:
                (self.state / "compile.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            self._log("compile", actor, ok=res["ok"], seconds=res["seconds"], errors=len(res["errors"]), job=job)
            return res

    def last_compile(self) -> dict | None:
        f = self.state / "compile.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def stamp(self) -> dict:
        """Polled by the editor: file hashes + open proposals + last compile time."""
        pdf = self.shown_pdf()
        lc = self.last_compile()
        return {"files": {x["path"]: x["hash"] for x in self.files()},
                "notes": {x["key"]: x["hash"] for x in self.notes()},
                "proposals": [pr["id"] + ":" + str(sum(h["state"] == "open" for h in pr["hunks"]))
                              for pr in self.proposals()],
                "pdf": pdf.stat().st_mtime_ns if pdf.exists() else None,
                "compile": lc["ts"] if lc else None, "stale": self.pdf_stale()}

    def pdf_stale(self) -> bool:
        """True when a source (any .tex, or the evidence/asset registers) is newer than the working PDF."""
        pdf = self.pdf_path()
        if not pdf.exists():
            return False
        t = pdf.stat().st_mtime
        srcs = [f for f in self.root.rglob("*.tex") if "build" not in f.relative_to(self.root).parts]
        srcs += [self.folder / n for n in ("evidence.tsv", "assets.tsv", "evidence_gaps.tsv")]
        return any(f.exists() and f.stat().st_mtime > t + 1 for f in srcs)

    def summary(self) -> dict:
        return {"id": self.id, "title": self.art["label"], "folder": str(self.folder), "root": str(self.root),
                "main": self.main, "engine": (self.engine() or [None])[0], "files": self.files(),
                "proposals": self.proposals(), "compile": self.last_compile(), "notes": self.notes(),
                "revisions": (self.folder / REVISIONS).is_file()}

    # ------------------------------------------------------------ revisions ----
    # The revision register (<article>/revisions/revisions.tsv): every change a co-author or reviewer sent, with an
    # R number, before / reviewer text / final text, the decision, the reason and the proposal that carries it.
    # Written by the revision tools (scripts/manuscript/revision_register.py, skill sciweave-revisions); read-only here.
    def revisions(self) -> dict:
        import csv
        f = self.folder / REVISIONS
        if not f.is_file():
            return {"exists": False, "rows": []}
        rows = list(csv.DictReader(f.open(encoding="utf-8"), delimiter="\t"))
        st = {p["id"]: p.get("status") for p in self.proposals(status=None)} if rows else {}
        for r in rows:
            a, b = (r.get("before") or "").split(), (r.get("reviewer_text") or "").split()
            diff, changed = [], 0
            for op, i0, i1, j0, j1 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if op == "equal":
                    diff.append(["=", " ".join(a[i0:i1])])
                    continue
                if i1 > i0:
                    diff.append(["-", " ".join(a[i0:i1])])
                if j1 > j0:
                    diff.append(["+", " ".join(b[j0:j1])])
                changed += max(i1 - i0, j1 - j0)
            r["diff"], r["words_changed"] = diff, changed
            ps = [p.strip() for p in (r.get("proposal") or "").split(",") if p.strip()]
            if ps and r.get("status") in ("proposed", "accepted", "rejected"):  # live status from the proposals
                s = {st.get(p) for p in ps}
                if s == {"accepted"}:
                    r["status"] = "accepted"
                elif s and s <= {"rejected", "withdrawn"}:
                    r["status"] = "rejected"
        return {"exists": True, "path": REVISIONS, "rows": rows}


def parse_errors(log: str, root: Path) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r"^(?:\./)?([^\s:][^:\n]*\.(?:tex|sty|cls|bib)):(\d+): (.+)$", log, re.M):
        f = m.group(1).replace("\\", "/")
        try:
            f = Path(f).resolve().relative_to(root.resolve()).as_posix() if Path(f).is_absolute() else f.lstrip("./")
        except ValueError:
            pass
        key = (f, m.group(2), m.group(3))
        if key not in seen:
            seen.add(key)
            out.append({"file": f, "line": int(m.group(2)), "message": m.group(3).strip()})
    if not out:
        for m in re.finditer(r"^! (.+)$", log, re.M):
            ln = re.search(r"^l\.(\d+)", log[m.end():m.end() + 2000], re.M)
            out.append({"file": None, "line": int(ln.group(1)) if ln else None, "message": m.group(1).strip()})
    return out[:50]


def parse_warnings(log: str) -> list[str]:
    w = re.findall(r"^(?:LaTeX|Package \w+) Warning: (.+?)(?:\n\n|\.$)", log, re.M | re.S)
    return [" ".join(x.split())[:200] for x in w][:50]
