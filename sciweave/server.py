"""`sciweave serve` / `sciweave open`: a local dashboard server (stdlib only, binds 127.0.0.1).

The same HTML is used by `sciweave export` (static, data embedded) — in serve
mode the page fetches api/graph and the action buttons (save, final, note,
group, ask) become live.

Two modes share one handler:
  single project (`serve`)  /            -> that project's dashboard, /api/... its API
  home (`open`)             /            -> home page listing every registered project
                            /p/<id>/     -> that project's dashboard, /p/<id>/api/... its API
The dashboard only uses relative URLs, so it works unchanged under either prefix.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sciweave import ai, registry
from sciweave.article import article_summary
from sciweave.export import render_dashboard
from sciweave.project import Project, SciWeaveError

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
TABLE_EXT = {".csv", ".tsv", ".txt", ".tab"}
TEXT_EXT = {".tex", ".md", ".py", ".r", ".R", ".sh", ".smk", ".yaml", ".yml", ".json", ".nf", ".log", ".bib", ".cfg", ".ini", ".toml"}


def preview(p: Project, node_id: str, version: int | None = None, rows: int = 25) -> dict:
    n = p.node(node_id)
    if not n["path"]:
        return {"kind": "none"}
    path = p.resolve(n["path"])
    if version:
        v = p.get_version(node_id, version)
        obj = p.object_path(v)
        if obj is None or not obj.exists():
            return {"kind": "none", "reason": f"v{version} not stored"}
        path_for_read = obj
    else:
        path_for_read = path
    if not path_for_read.exists():
        return {"kind": "none", "reason": "file missing"}
    if path_for_read.is_dir():
        items = sorted(x.name + ("/" if x.is_dir() else "") for x in path_for_read.iterdir())[:60]
        return {"kind": "dir", "items": items}
    ext = path.suffix.lower()
    if ext in IMAGE_EXT or ext == ".pdf":
        q = f"api/file?node={node_id}" + (f"&version={version}" if version else "")
        return {"kind": "pdf" if ext == ".pdf" else "image", "url": q}
    if ext in TABLE_EXT or path.name.endswith((".tsv.gz", ".csv.gz")):
        if path.name.endswith(".gz"):
            import gzip
            fh = gzip.open(path_for_read, "rt", encoding="utf-8", errors="replace")
        else:
            fh = open(path_for_read, encoding="utf-8", errors="replace")
        with fh:
            head = [fh.readline() for _ in range(rows + 1)]
        head = [h for h in head if h]
        delim = "\t" if (ext != ".csv" and "\t" in (head[0] if head else "")) else ","
        parsed = list(csv.reader(head, delimiter=delim))
        return {"kind": "table", "header": parsed[0] if parsed else [], "rows": parsed[1:]}
    if ext in TEXT_EXT or path_for_read.stat().st_size < 200_000:
        try:
            text = path_for_read.read_text(encoding="utf-8")[:20000]
            return {"kind": "text", "text": text}
        except UnicodeDecodeError:
            pass
    return {"kind": "none", "reason": f"no preview for {ext or 'this file'}"}


def latest_suggestions(p: Project) -> dict:
    """Last monitor round (written by `sciweave monitor`, the Claude skill or the dashboard),
    re-filtered against the current graph so items already handled disappear."""
    from sciweave import monitor
    f = p.state / "monitor_latest.json"
    st = monitor.load_state(p)
    if not f.exists():
        return {"ts": None, "updated": [], "new": [], "missing": [], "stale": [], "last_round": st.get("last_round")}
    r = json.loads(f.read_text(encoding="utf-8"))
    status = p.status()
    tracked = {n["path"] for n in p.nodes.values() if n["path"]}
    r["updated"] = [u for u in r.get("updated", []) if u["node"] in status and status[u["node"]]["modified"]]
    r["missing"] = [m for m in r.get("missing", []) if m["node"] in status and status[m["node"]]["missing"]]
    ign = monitor.DEFAULT_IGNORE + st.get("ignore", [])
    r["new"] = [n for n in r.get("new", []) if n["path"] not in tracked and not monitor._ignored(n["path"], ign)]
    r["stale"] = [{"node": k, "label": p.nodes[k]["label"], "reasons": v["reasons"][:3]}
                  for k, v in status.items() if v["stale"]]
    r["last_round"] = st.get("last_round")
    return r


def reveal(path: Path) -> None:
    """Open the OS file manager at `path` (a file is selected in its folder). The server runs on
    the user's own machine, so this is how the dashboard can 'open' a local folder."""
    import subprocess
    import sys
    path = Path(path)
    if not path.exists():
        raise SciWeaveError(f"not found: {path}")
    if sys.platform.startswith("win"):
        if path.is_dir():
            os.startfile(str(path))  # noqa: S606 - local, tracked paths only
        else:
            subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])


# files the browser cannot show: /tex-asset/ opens them in the SciWeave viewer unless ?raw=1
VIEW_EXT = {".xlsx", ".tsv", ".csv"}

class Handler(BaseHTTPRequestHandler):
    project_root: Path | None = Path(".")  # None = home mode (every registered project)
    lock = threading.Lock()
    _root: Path | None = None
    _home_link = False

    def log_message(self, fmt, *args):  # quiet
        pass

    def _project(self) -> Project:
        return Project(self._root)

    def _route(self, path: str) -> str | None:
        """Pick the project this request is about and return the path inside it.
        None means the request is for the home itself; "" means it was already answered."""
        if self.project_root is not None:
            self._root = self.project_root
            return path
        m = re.match(r"^/p/([^/]+)(/.*)?$", path)
        if not m:
            return None
        if m.group(2) is None:  # /p/id -> /p/id/ so the page's relative URLs resolve inside it
            self.send_response(301)
            self.send_header("Location", f"/p/{m.group(1)}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return ""
        e = registry.get(m.group(1))
        self._root = Path(e["path"])
        self._home_link = True
        return m.group(2)

    def _home_get(self, path: str, q: dict):
        from sciweave.export import render_home
        if path in ("/", "/index.html"):
            return self._send(200, render_home().encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/projects/stamp":  # the home page polls this to stay current
            out = {}
            for e in registry.load()["projects"]:
                g = Path(e["path"]) / ".sciweave" / "graph.json"
                out[e["id"]] = g.stat().st_mtime_ns if g.exists() else None
            return self._json(out)
        if path == "/api/projects":
            reg = registry.load()
            return self._json({"home": str(registry.home_dir()),
                               "projects": [registry.summarize(e) for e in reg["projects"]]})
        return self._json({"error": "not found"}, 404)

    def _home_post(self, path: str, body: dict):
        if path == "/api/projects/add":
            root = Path(str(body.get("path", "")).strip().strip('"')).expanduser()
            if not root.is_absolute():
                raise SciWeaveError("give the full path of the project folder")
            if (root / ".sciweave" / "graph.json").exists():
                p = Project(root)
                verb = "added"
            elif body.get("create"):
                p = Project.init(root, name=(body.get("name") or "").strip() or None,
                                 description=body.get("description", ""), bare=bool(body.get("bare")))
                verb = "created"
            else:
                raise SciWeaveError(f"{root} is not a SciWeave project yet (tick 'create' to start one there)")
            e = registry.register(p)
            return self._json({"ok": True, "id": e["id"], "message": f"{verb} {e['name']}"})
        if path == "/api/projects/reveal":
            p = Project(Path(registry.get(body["id"])["path"]))
            if p.destination is None:
                raise SciWeaveError("no destination set for this project yet")
            reveal(p.destination)
            return self._json({"ok": True, "message": f"opened {p.destination}"})
        if path == "/api/projects/destination":
            e = registry.get(body["id"])
            p = Project(Path(e["path"]))
            d = p.set_destination(body.get("path") or None, actor="user")
            p.commit()
            return self._json({"ok": True, "message": f"{e['name']}: destination {d}" if d else "destination cleared"})
        if path == "/api/projects/remove":
            e = registry.unregister(body["id"])
            return self._json({"ok": True, "message": f"removed {e['name']} from the list (its folder is untouched)"})
        return self._json({"error": "not found"}, 404)

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # the browser went away mid-answer (reload, auto-refresh): nothing to report

    def _release_glock(self) -> None:
        g = getattr(self, "_glock", None)
        if g is not None:
            self._glock = None
            g.__exit__(None, None, None)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self._release_glock()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            path = self._route(url.path)
            if path is None:
                return self._home_get(url.path, q)
            if path == "":
                return
            if path in ("/", "/index.html"):
                html = render_dashboard(self._project(), live=True, home_link=self._home_link)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/graph":
                return self._json(self._project().payload())
            if path == "/api/preview":
                return self._json(preview(self._project(), q["node"], int(q["version"]) if q.get("version") else None))
            if path == "/api/suggest":
                return self._json(latest_suggestions(self._project()))
            if path == "/api/stamp":  # polled every ~2 s by the dashboard: file times only, no graph work
                from sciweave import jobs
                p = self._project()
                st = lambda f: [f.stat().st_mtime_ns, f.stat().st_size] if f.exists() else None
                running = sum(1 for j in jobs.list_jobs(p) if j["state"] in jobs.RUNNING)
                return self._json({"graph": st(p.graph_file), "history": st(p.history_file), "jobs": running})
            if path == "/api/jobs":
                from sciweave import jobs
                return self._json(jobs.list_jobs(self._project()))
            if path == "/api/article":
                return self._json(article_summary(self._project(), q["id"]))
            if path == "/api/file":
                return self._file(q)
            if path == "/latex-pdf.html":
                from sciweave.export import render_latex_pdf
                html = render_latex_pdf(self._project(), q.get("article", ""))
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path in ("/latex", "/latex.html"):
                from sciweave.export import render_latex
                html = render_latex(self._project(), q.get("article", ""), home_link=self._home_link)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/api/tex/"):
                return self._tex_get(path, q)
            if path.startswith("/tex-asset/"):
                return self._tex_asset(path, q)
            if path.startswith("/tex-view/"):
                return self._tex_view(path)
            return self._json({"error": "not found"}, 404)
        except SciWeaveError as exc:
            return self._json({"error": str(exc)}, 400)
        except (KeyError, ValueError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)

    def _file(self, q: dict):
        p = self._project()
        n = p.node(q["node"])  # only files that belong to a tracked node are served
        if q.get("version"):
            obj = p.object_path(p.get_version(n["id"], int(q["version"])))
            path = obj if obj and obj.exists() else None
        else:
            path = p.resolve(n["path"]) if n["path"] else None
        if path is None or not path.is_file():
            return self._json({"error": "no file"}, 404)
        ctype = mimetypes.guess_type(n["path"])[0] or "application/octet-stream"
        return self._send(200, path.read_bytes(), ctype)

    # ------------------------------------------------------- LaTeX editor ----
    def _ms(self, q: dict):
        from sciweave.manuscript import Manuscript
        return Manuscript(self._project(), q["article"])

    def _tex_asset(self, path: str, q: dict | None = None):
        """/tex-asset/<article>/<file>: a file of the article folder (figures, tables, cited PDFs,
        MetaData) -- what the evidence links in the working PDF open. Spreadsheets and tables open in
        the viewer (a browser cannot show them and would only offer to save); `?raw=1` gets the file."""
        from urllib.parse import unquote, quote
        parts = path.split("/", 3)
        if len(parts) < 4 or not parts[3]:
            return self._json({"error": "not found"}, 404)
        ms = self._ms({"article": parts[2]})
        f = (ms.folder / unquote(parts[3])).resolve()
        if ms.folder not in f.parents or not f.is_file() or any(x.startswith(".") for x in f.relative_to(ms.folder).parts):
            return self._json({"error": "not found"}, 404)
        if f.suffix.lower() in VIEW_EXT and not (q or {}).get("raw"):
            from sciweave import viewer
            from sciweave.evidence import read_tsv, ASSETS
            rel = f.relative_to(ms.folder).as_posix()
            assets = {a["key"]: a for a in read_tsv(ms.folder / ASSETS) if a.get("key")}
            key = next((k for k, a in assets.items() if a.get("file") == rel), None)
            legend = ""
            if key and key + ".legend" in assets and (ms.folder / assets[key + ".legend"]["file"]).is_file():
                legend = (ms.folder / assets[key + ".legend"]["file"]).read_text(encoding="utf-8", errors="replace")
            raw = f"{self._prefix()}/tex-asset/{parts[2]}/{quote(rel)}?raw=1"
            html = viewer.render(f, rel, where=(q or {}).get("where", ""), title=assets[key]["label"] if key else rel,
                                 legend=legend, file_url=raw)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or f.suffix.lower() in (".tsv", ".csv", ".tex", ".md", ".json", ".txt"):
            ctype = "text/plain; charset=utf-8"  # shown, never run as a page
        return self._send(200, f.read_bytes(), ctype)

    def _prefix(self) -> str:
        """'' for a single-project server, '/p/<id>' under the home server."""
        full = urlparse(self.path).path
        route = self._route(full) or ""
        return full[: len(full) - len(route)] if route and full.endswith(route) else ""

    def _link_base(self, article: str) -> str | None:
        """The server root as the browser sees it, for the working PDF's links (None if unusable in an href)."""
        base = f"http://{self.headers.get('Host', '')}{self._prefix()}/"
        return base if re.match(r"^http://[A-Za-z0-9.\-:\[\]]+[A-Za-z0-9_./\-]*/$", base) else None

    def _tex_view(self, path: str):
        """/tex-view/<article>/ev/<E7> or /asset/<F6>: the evidence opened in a viewer for its file type
        (tables with the supporting rows highlighted, figures with their legend); PDFs go to the
        browser's viewer at the page."""
        from urllib.parse import quote
        from sciweave.evidence import Evidence, read_tsv, ASSETS
        from sciweave import viewer
        parts = path.split("/")
        if len(parts) != 5 or parts[3] not in ("ev", "asset"):
            return self._json({"error": "not found"}, 404)
        ms = self._ms({"article": parts[2]})
        ev = Evidence(ms)
        assets = {a["key"]: a for a in read_tsv(ms.folder / ASSETS) if a.get("key")}
        title, claim, quote_, where, page, legend = parts[4], "", "", "", "1", ""
        if parts[3] == "ev":
            e = (ev.report().get("evidence") or {}).get(parts[4])
            if not e or not e.get("path"):
                return self._json({"error": f"{parts[4]}: no file for this evidence (see the Evidence panel)"}, 404)
            rel, claim, quote_, where, page = e["path"], e.get("claim", ""), e.get("quote", ""), e.get("where", ""), e.get("page") or "1"
            title = f"{parts[4]} · {assets[e['target']]['label'] if e['target'] in assets else e['target']}"
            key = e["target"] if e["target"] in assets else None
        else:
            a = assets.get(parts[4])
            if not a:
                return self._json({"error": f"{parts[4]} is not in assets.tsv"}, 404)
            rel, title, key = a["file"], a.get("label") or parts[4], parts[4]
        f = (ms.folder / rel).resolve()
        if ms.folder not in f.parents or not f.is_file():
            return self._json({"error": f"{rel} missing"}, 404)
        file_url = f"{self._prefix()}/tex-asset/{parts[2]}/" + quote(rel)
        raw_url = file_url + "?raw=1"
        if f.suffix.lower() == ".pdf":
            self.send_response(302)
            self.send_header("Location", f"{file_url}#page={page}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if key and key + ".legend" in assets:
            lf = ms.folder / assets[key + ".legend"]["file"]
            legend = lf.read_text(encoding="utf-8", errors="replace") if lf.is_file() else ""
        html = viewer.render(f, rel, where=where, title=title, claim=claim, quote=quote_, legend=legend,
                             file_url=file_url if f.suffix.lower() not in VIEW_EXT else raw_url)
        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _tex_get(self, path: str, q: dict):
        if path == "/api/tex/articles":
            p = self._project()
            return self._json([{"id": n["id"], "label": n["label"], "folder": n["meta"].get("folder")}
                               for n in p.nodes.values() if n["type"] == "article"])
        ms = self._ms(q)
        if path == "/api/tex/summary":
            return self._json(ms.summary())
        if path == "/api/tex/file":
            return self._json(ms.read(q["path"]))
        if path == "/api/tex/stamp":
            return self._json(ms.stamp())
        if path == "/api/tex/note":
            return self._json(ms.note_read(q["key"]))
        if path == "/api/tex/revisions":
            return self._json(ms.revisions())
        if path == "/api/tex/proposals":
            status = q.get("status", "open")
            return self._json(ms.proposals(status=None if status == "all" else status, file=q.get("file") or None))
        if path == "/api/tex/snapshots":
            return self._json(ms.snapshots(q["path"]))
        if path == "/api/tex/snapshot":
            return self._json({"text": ms.snapshot_text(q["path"], q["id"])})
        if path == "/api/tex/pdf":
            pdf = ms.shown_pdf()
            if not pdf.exists():
                return self._json({"error": "not compiled yet"}, 404)
            return self._send(200, pdf.read_bytes(), "application/pdf")
        if path == "/api/tex/log":
            return self._json(ms.last_compile() or {})
        if path == "/api/tex/evidence":
            from sciweave.evidence import Evidence
            return self._json(Evidence(ms).report())
        if path == "/api/tex/submission-pdf":
            pdf = ms.pdf_path("main_submission")
            if not pdf.exists():
                return self._json({"error": "no submission PDF yet"}, 404)
            return self._send(200, pdf.read_bytes(), "application/pdf")
        return self._json({"error": "not found"}, 404)

    def _tex_post(self, path: str, body: dict):
        ms = self._ms(body)
        if path == "/api/tex/save":
            return self._json(ms.save(body["path"], body["text"], base_hash=body.get("base_hash"), actor="user"))
        if path == "/api/tex/note-save":
            return self._json(ms.note_save(body["key"], body["text"], base_hash=body.get("base_hash"), actor="user"))
        if path == "/api/tex/compile":
            if body.get("save"):
                ms.save(body["save"]["path"], body["save"]["text"], base_hash=body["save"].get("base_hash"))
            return self._json(ms.compile(actor="user", submission=bool(body.get("submission")),
                                         link_base=self._link_base(ms.id)))
        if path == "/api/tex/evidence":
            from sciweave.evidence import Evidence
            return self._json(Evidence(ms).build())
        if path == "/api/tex/resolve":
            return self._json(ms.resolve(body["id"], accept=body.get("accept", []), reject=body.get("reject", []),
                                         actor="user"))
        if path == "/api/tex/propose":
            actor = body.get("actor", "claude")
            if body.get("edits"):
                pr = ms.propose_edits(body["path"], body["edits"], message=body.get("message", ""), actor=actor)
            else:
                pr = ms.propose(body["path"], body["text"], message=body.get("message", ""), actor=actor)
            return self._json({"ok": True, "proposal": pr})
        if path == "/api/tex/withdraw":
            return self._json({"ok": True, "proposal": ms.withdraw(body["id"], actor=body.get("actor", "user"))})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON"}, 400)
        try:
            if "/api/tex/" in url.path:  # the LaTeX editor: own per-manuscript locks, never the graph lock
                path = self._route(url.path)
                if path and path.startswith("/api/tex/"):
                    return self._tex_post(path, body)
            with self.lock:
                path = self._route(url.path)
                if path is None:
                    return self._home_post(url.path, body)
                from sciweave import jobs
                self._glock = jobs.graph_lock(Path(self._root) / ".sciweave")
                self._glock.__enter__()  # released in _send (every answer goes through it)
                p = self._project()
                if path == "/api/ask":
                    return self._json(ai.ask(p, body.get("question", ""), engine=body.get("engine", "auto")))
                if path == "/api/save":
                    v = p.save_version(body["node"], message=body.get("message", "saved from dashboard"),
                                       why=body.get("why", ""))
                    p.commit()
                    return self._json({"ok": True, "version": v["v"] if v else None,
                                       "message": f"saved v{v['v']}" if v else "unchanged — nothing to save"})
                if path == "/api/final":
                    v = p.mark_final(body["node"], body.get("version"))
                    p.commit()
                    return self._json({"ok": True, "message": f"v{v} marked final"})
                if path == "/api/note":
                    p.add_note(body["node"], body["text"])
                    p.commit()
                    return self._json({"ok": True, "message": "note added"})
                if path == "/api/group":
                    p.set_group(body["name"], body.get("nodes", []), color=body.get("color"))
                    p.commit()
                    return self._json({"ok": True, "message": f"group {body['name']} updated"})
                if path == "/api/suggest":
                    from sciweave import monitor
                    r = monitor.run_round(p)
                    p.commit()
                    return self._json({**r, "ok": True, "text": monitor.render(p, r)})
                if path == "/api/ignore":
                    from sciweave import monitor
                    st = monitor.load_state(p)
                    if body["pattern"] not in st["ignore"]:
                        st["ignore"].append(body["pattern"])
                    monitor.save_state(p, st)
                    return self._json({"ok": True, "message": f"ignoring {body['pattern']}"})
                if path == "/api/branch":
                    p.set_branch_status(body["node"], body["status"], why=body.get("why", ""))
                    p.commit()
                    return self._json({"ok": True, "message": f"{body['node']} is now {body['status']}"})
                if path == "/api/analysis":
                    k = body["key"]
                    if "organized" in body:
                        p.set_analysis(k, actor="user", organized=bool(body["organized"]))
                    if "hidden" in body:
                        p.hide_analyses([k], hidden=bool(body["hidden"]), actor="user")
                    if body.get("log"):
                        p.log_analysis(k, body["log"], date=body.get("date") or None, actor="user")
                    p.commit()
                    return self._json({"ok": True, "analysis": p.analysis(k), "message": f"{k} updated"})
                if path == "/api/reveal":
                    if p.destination is None:
                        raise SciWeaveError("no destination set for this project yet")
                    if body.get("node"):  # only tracked copies can be opened, never arbitrary paths
                        rec = p.node(body["node"]).get("organized")
                        if not rec:
                            raise SciWeaveError(f"{body['node']} has no organized copy yet")
                        target = p.destination / rec["path"]
                    else:
                        target = p.destination
                    reveal(target)
                    return self._json({"ok": True, "message": f"opened {target}"})
                if path == "/api/destination":
                    d = p.set_destination(body.get("path") or None, actor="user")
                    p.commit()
                    return self._json({"ok": True, "destination": str(d) if d else None,
                                       "message": f"destination: {d}" if d else "destination cleared"})
                if path == "/api/organize-check":
                    return self._json({"ok": True, **p.organized_check(body["node"])})
                if path == "/api/verify":
                    from sciweave import jobs
                    n = p.node(body["node"])
                    job = jobs.start(p, n["id"], kind="verify")
                    return self._json({"ok": True, "job": job["id"], "message": f"verifying the copy of {n['id']}\u2026"})
                if path == "/api/organize-skip":
                    p.set_organize_skip(body["node"], bool(body.get("skip", True)), actor="user")
                    p.commit()
                    return self._json({"ok": True, "message": f"{body['node']}: " + ("skipped for 'save all'" if body.get("skip", True) else "back in 'save all'")})
                if path == "/api/organize":
                    if p.destination is None:
                        raise SciWeaveError("no destination set for this project yet")
                    n = p.node(body["node"])
                    if not n.get("path"):
                        raise SciWeaveError(f"{n['id']} has no file to copy (a conceptual object)")
                    from sciweave import jobs
                    job = jobs.start(p, n["id"], kind="copy")
                    return self._json({"ok": True, "job": job["id"],
                                       "message": f"copying {n['id']} to {p.destination}/{p.organized_folder(n['id'])}/"})
                if path == "/api/pnote":
                    if body.get("id"):
                        x = p.update_project_note(body["id"], status=body.get("status"), text=body.get("text"),
                                                  comment=body.get("comment"), actor="user")
                    else:
                        x = p.add_project_note(body["text"], nodes=body.get("nodes", []),
                                               kind=body.get("kind", "note"), actor="user")
                    p.commit()
                    return self._json({"ok": True, "note": x, "message": f"{x['id']} saved"})
                if path == "/api/remove-preview":
                    return self._json({"ok": True, **p.removal_plan(body["node"])})
                if path == "/api/remove":
                    plan = p.removal_plan(body["node"])  # recomputed here: what the user saw may be stale
                    if sorted(plan["also"]) != sorted(body.get("expect_also", plan["also"])):
                        raise SciWeaveError("the network changed since the preview; open Delete again to see the new list")
                    p.remove_nodes(plan, actor="user")
                    p.commit()
                    n = 1 + len(plan["also"])
                    return self._json({"ok": True, "removed": [plan["node"], *plan["also"]],
                                       "message": f"deleted {n} object(s) and {len(plan['edges'])} link(s); files and saved copies kept"})
                if path == "/api/hide":
                    p.hide_nodes([body["node"]], hidden=bool(body["hidden"]), actor="user")
                    p.commit()
                    return self._json({"ok": True, "message": f"{body['node']} {'hidden' if body['hidden'] else 'shown'}"})
                if path == "/api/restore":
                    t = p.restore(body["node"], int(body["version"]), force=bool(body.get("force")))
                    p.commit()
                    return self._json({"ok": True, "message": f"restored to {p.rel(t)}"})
            return self._json({"error": "not found"}, 404)
        except SciWeaveError as exc:
            return self._json({"error": str(exc)}, 400)
        except (KeyError, ValueError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        except Exception as exc:  # surface AI/network errors to the UI instead of a dead socket
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(p: Project | None, port: int = 8765, open_browser: bool = True, host: str = "127.0.0.1",
          start: str = "") -> None:
    """Serve one project, or (p=None) the home page with every registered project.
    `start` is the page opened in the browser, e.g. "p/sle/"."""
    Handler.project_root = p.root if p else None
    httpd = None
    for candidate in range(port, port + 20):  # the port may be held by another dashboard: take the next free one
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SciWeaveError(f"ports {port}-{port + 19} are all in use; pass --port")
    if candidate != port:
        print(f"port {port} is in use; using {candidate}")
        port = candidate
    url = f"http://{host}:{port}/"
    if p:
        print(f"SciWeave dashboard for '{p.graph['project']['name']}' at {url}  (Ctrl+C to stop)")
    else:
        print(f"SciWeave home ({registry.home_dir()}) at {url}  (Ctrl+C to stop)")
        url += start
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def watch(p: Project, interval: float = 5.0, autosave: bool = False, once: bool = False) -> None:
    """Poll tracked files; log changes to history (and optionally save versions)."""
    seen: dict[str, str] = {}
    print(f"watching {len(p.nodes)} node(s) every {interval}s (Ctrl+C to stop)"
          + ("  [autosave]" if autosave else ""))
    try:
        while True:
            p.load()
            st = p.status()
            changed = False
            for nid, s in st.items():
                key = s["state"]
                if seen.get(nid) == key:
                    continue
                prev = seen.get(nid)
                seen[nid] = key
                if prev is None and key == "ok":
                    continue
                if s["modified"]:
                    if autosave and p.nodes[nid]["mode"] == "managed":
                        v = p.save_version(nid, message="autosave (watch)", actor="watch")
                        if v:
                            print(f"  {nid}: saved v{v['v']}")
                            seen[nid] = "ok"
                            changed = True
                    else:
                        p.log("modified_on_disk", node=nid, actor="watch")
                        print(f"  {nid}: modified on disk (run `sciweave save {nid}`)")
                        changed = True
                elif s["missing"]:
                    p.log("missing_on_disk", node=nid, actor="watch")
                    print(f"  {nid}: missing on disk")
                    changed = True
            if changed:
                p.commit()
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
