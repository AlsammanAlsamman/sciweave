"""Article nodes: a folder template + LaTeX skeleton, and placing results into it."""

from __future__ import annotations

import shutil
from pathlib import Path

from sciweave.project import Project, SciweaveError, fingerprint
from sciweave.schema import slugify

ARTICLE_SUBFOLDERS = ["figures", "tables", "supplementary", "scripts", "data", "notes", "latex/sections"]
DEFAULT_SECTIONS = ["abstract", "introduction", "methods", "results", "discussion"]
PLACE_FOLDER = {"figure": "figures", "table": "tables", "supplement": "supplementary", "script": "scripts"}

MAIN_TEX = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{hyperref}
\graphicspath{{../figures/}}

\title{%(title)s}
\author{}
\date{}

\begin{document}
\maketitle

%(inputs)s

\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""

README = """# %(title)s

Article folder managed by Sciweave (node `%(id)s`).

| folder | what goes here |
|---|---|
| `figures/` | final figures placed with `sciweave article place <F-id> %(id)s --as "Figure N"` |
| `tables/` | final tables (same command with a table node) |
| `supplementary/` | supplementary tables / figures / files |
| `scripts/` | article-specific scripts (e.g. final figure assembly) |
| `data/` | small data files quoted in the text |
| `latex/` | `main.tex`, one file per section in `sections/`, `references.bib` |
| `notes/` | reviewer comments, TODOs |

Every placed file is linked in the Sciweave network to the result it came from,
so `sciweave trace <id>` always shows how it was generated.
"""


def new_article(p: Project, title: str, node_id: str | None = None,
                sections=DEFAULT_SECTIONS, actor: str = "user") -> dict:
    slug = slugify(title)
    folder = p.root / "articles" / slug
    if folder.exists() and any(folder.iterdir()):
        raise SciweaveError(f"articles/{slug} already exists")
    for sub in ARTICLE_SUBFOLDERS:
        (folder / sub).mkdir(parents=True, exist_ok=True)
    inputs = "\n".join(f"\\input{{sections/{s}}}" for s in sections)
    (folder / "latex" / "main.tex").write_text(MAIN_TEX % {"title": title, "inputs": inputs}, encoding="utf-8")
    (folder / "latex" / "references.bib").write_text("% BibTeX references\n", encoding="utf-8")
    for s in sections:
        head = "\\begin{abstract}\n\n\\end{abstract}\n" if s == "abstract" else f"\\section{{{s.capitalize()}}}\n\n"
        (folder / "latex" / "sections" / f"{s}.tex").write_text(head, encoding="utf-8")

    art = p.add_node("article", title, path=str(folder / "latex" / "main.tex"), mode="managed",
                     node_id=node_id, meta={"folder": p.rel(folder), "slug": slug}, groups=[slug],
                     actor=actor, message="article created")
    (folder / "README.md").write_text(README % {"title": title, "id": art["id"]}, encoding="utf-8")
    for s in sections:
        sec = p.add_node("section", f"{s.capitalize()} ({title[:24]})",
                         path=str(folder / "latex" / "sections" / f"{s}.tex"), mode="managed",
                         meta={"section": s, "article": art["id"]}, groups=[slug], actor=actor,
                         message="section created")
        p.link(sec["id"], art["id"], rel="part_of", label=s.capitalize(), actor=actor)
    p.log("article_created", node=art["id"], detail=f"articles/{slug}", actor=actor)
    return art


def _version_content(p: Project, node: dict, version: int) -> Path:
    v = p.get_version(node["id"], version)
    obj = p.object_path(v)
    if obj and obj.exists():
        return obj
    cur = p.resolve(node["path"])
    if cur and cur.exists() and fingerprint(cur)["hash"] == v["hash"]:
        return cur
    raise SciweaveError(f"content of {node['id']} v{version} is not available (not stored)")


def place(p: Project, item_id: str, article_id: str, as_label: str, version: int | None = None,
          actor: str = "user") -> dict:
    """Copy item's final (else current) version into the article folder and link it part_of."""
    item, art = p.node(item_id), p.node(article_id)
    if art["type"] != "article":
        raise SciweaveError(f"{article_id} is not an article")
    if not item["versions"]:
        raise SciweaveError(f"{item_id} has no saved version to place")
    ver = version or item["final_version"] or item["current_version"]
    src = _version_content(p, item, ver)
    ext = Path(item["path"]).suffix
    sub = PLACE_FOLDER.get(item["type"], "data")
    dst = p.root / art["meta"]["folder"] / sub / (slugify(as_label).replace("-", "_").capitalize() + ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    e = p.link(item_id, article_id, rel="part_of", label=as_label, actor=actor)
    e["placed_path"] = p.rel(dst)
    e["placed_version"] = ver
    e["source_version"] = ver
    p.log("placed_in_article", node=item_id, detail=f"v{ver} as '{as_label}' in {article_id} -> {p.rel(dst)}",
          actor=actor, nodes=[item_id, article_id])
    return e


def sync(p: Project, article_id: str, actor: str = "user") -> list[str]:
    """Re-place every stale placement with the item's final (else current) version."""
    p.node(article_id)
    done = []
    for e in p.incoming(article_id):
        if e["rel"] != "part_of" or not e.get("placed_path"):
            continue
        item = p.nodes[e["source"]]
        want = item["final_version"] or item["current_version"]
        if want != e.get("placed_version"):
            place(p, item["id"], article_id, e["label"], version=want, actor=actor)
            done.append(f"{item['id']} -> {e['label']} (v{want})")
    return done


def article_summary(p: Project, article_id: str) -> dict:
    status = p.status()
    comps = []
    for e in p.incoming(article_id):
        if e["rel"] != "part_of":
            continue
        n = p.nodes[e["source"]]
        comps.append({"id": n["id"], "type": n["type"], "label": n["label"], "as": e.get("label"),
                      "placed_version": e.get("placed_version"), "current_version": n["current_version"],
                      "final_version": n["final_version"], "state": status[n["id"]]["state"],
                      "edge_stale": p.edge_is_stale(e)})
    return {"article": p.nodes[article_id], "components": comps}
