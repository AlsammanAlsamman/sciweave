"""Build the dashboard HTML: one self-contained file (CSS, D3, app inline).

live=True  -> served by `sciweave serve`, data fetched from /api/graph.
live=False -> static export, project data embedded; works offline from disk.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from sciweave.project import Project

HERE = Path(__file__).parent


def _read(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8")


def _script_safe(text: str) -> str:
    # a literal "</script>" (or any "</") inside inline JS/JSON would end the tag early
    return text.replace("</", "<\\/")


def render_dashboard(p: Project, live: bool) -> str:
    html = _read("dashboard/index.html")
    data = "null" if live else _script_safe(json.dumps(p.payload(), ensure_ascii=False))
    parts = {
        "__TITLE__": p.graph["project"]["name"].replace("<", "&lt;"),
        "__LOGO__": "data:image/png;base64," + base64.b64encode((HERE / "dashboard" / "logo.png").read_bytes()).decode(),
        "__CSS__": _read("dashboard/style.css"),
        "__D3__": _script_safe(_read("vendor/d3.v7.9.0.min.js")),
        "__LIVE__": "true" if live else "false",
        "__DATA__": data,
        "__APP__": _script_safe(_read("dashboard/app.js")),
    }
    # replace in document order so injected content is never re-scanned for later placeholders
    out, pos = [], 0
    while True:
        hits = [(html.find(k, pos), k) for k in parts if html.find(k, pos) >= 0]
        if not hits:
            out.append(html[pos:])
            break
        i, k = min(hits)
        out.append(html[pos:i])
        out.append(parts[k])
        pos = i + len(k)
    return "".join(out)


def export_html(p: Project, out: str | Path | None = None) -> Path:
    path = Path(out) if out else p.root / "sciweave.html"
    path.write_text(render_dashboard(p, live=False), encoding="utf-8")
    return path
