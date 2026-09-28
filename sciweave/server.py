"""`sciweave serve`: a local dashboard server (stdlib only, binds 127.0.0.1).

The same HTML is used by `sciweave export` (static, data embedded) — in serve
mode the page fetches /api/graph and the action buttons (save, final, note,
group, ask) become live.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sciweave import ai
from sciweave.article import article_summary
from sciweave.export import render_dashboard
from sciweave.project import Project, SciweaveError

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
        q = f"/api/file?node={node_id}" + (f"&version={version}" if version else "")
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


class Handler(BaseHTTPRequestHandler):
    project_root: Path = Path(".")
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # quiet
        pass

    def _project(self) -> Project:
        return Project(self.project_root)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
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
            if url.path in ("/", "/index.html"):
                html = render_dashboard(self._project(), live=True)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/api/graph":
                return self._json(self._project().payload())
            if url.path == "/api/preview":
                return self._json(preview(self._project(), q["node"], int(q["version"]) if q.get("version") else None))
            if url.path == "/api/article":
                return self._json(article_summary(self._project(), q["id"]))
            if url.path == "/api/file":
                return self._file(q)
            return self._json({"error": "not found"}, 404)
        except SciweaveError as exc:
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

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON"}, 400)
        try:
            with self.lock:
                p = self._project()
                if url.path == "/api/ask":
                    return self._json(ai.ask(p, body.get("question", ""), engine=body.get("engine", "auto")))
                if url.path == "/api/save":
                    v = p.save_version(body["node"], message=body.get("message", "saved from dashboard"))
                    p.commit()
                    return self._json({"ok": True, "version": v["v"] if v else None,
                                       "message": f"saved v{v['v']}" if v else "unchanged — nothing to save"})
                if url.path == "/api/final":
                    v = p.mark_final(body["node"], body.get("version"))
                    p.commit()
                    return self._json({"ok": True, "message": f"v{v} marked final"})
                if url.path == "/api/note":
                    p.add_note(body["node"], body["text"])
                    p.commit()
                    return self._json({"ok": True, "message": "note added"})
                if url.path == "/api/group":
                    p.set_group(body["name"], body.get("nodes", []), color=body.get("color"))
                    p.commit()
                    return self._json({"ok": True, "message": f"group {body['name']} updated"})
                if url.path == "/api/restore":
                    t = p.restore(body["node"], int(body["version"]), force=bool(body.get("force")))
                    p.commit()
                    return self._json({"ok": True, "message": f"restored to {p.rel(t)}"})
            return self._json({"error": "not found"}, 404)
        except SciweaveError as exc:
            return self._json({"error": str(exc)}, 400)
        except (KeyError, ValueError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        except Exception as exc:  # surface AI/network errors to the UI instead of a dead socket
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(p: Project, port: int = 8765, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    Handler.project_root = p.root
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"Sciweave dashboard for '{p.graph['project']['name']}' at {url}  (Ctrl+C to stop)")
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
