"""Node types, edge relations and small helpers shared by every layer.

This is the vocabulary of the graph contract (`.sciweave/graph.json`). Adding
a node type = one entry in NODE_TYPES (plus an icon in dashboard/app.js).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

SCHEMA_VERSION = "sciweave/1"

# type -> code prefix, category, default folder for managed copies, blurb
NODE_TYPES: dict[str, dict] = {
    "raw":        {"prefix": "RAW", "category": "sources", "folder": "data/raw",             "desc": "raw data"},
    "input":      {"prefix": "IN",  "category": "sources", "folder": "data/inputs",          "desc": "prepared input"},
    "pipeline":   {"prefix": "PL",  "category": "process", "folder": "pipelines",            "desc": "pipeline / workflow"},
    "step":       {"prefix": "SP",  "category": "process", "folder": "pipelines",            "desc": "pipeline step / rule"},
    "script":     {"prefix": "SC",  "category": "process", "folder": "scripts",              "desc": "analysis or plotting script"},
    "result":     {"prefix": "R",   "category": "outputs", "folder": "results/other",        "desc": "output file"},
    "table":      {"prefix": "T",   "category": "outputs", "folder": "results/tables",       "desc": "result table"},
    "figure":     {"prefix": "F",   "category": "outputs", "folder": "results/figures",      "desc": "plot / figure"},
    "supplement": {"prefix": "S",   "category": "writing", "folder": "results/supplementary", "desc": "supplementary item"},
    "section":    {"prefix": "TX",  "category": "writing", "folder": "articles",             "desc": "text section (methods, results, LaTeX)"},
    "article":    {"prefix": "A",   "category": "writing", "folder": "articles",             "desc": "article"},
    "note":       {"prefix": "N",   "category": "notes",   "folder": "notes",                "desc": "note"},
}

CATEGORIES = ["sources", "process", "outputs", "writing", "notes"]

EDGE_RELATIONS: dict[str, str] = {
    "feeds": "input consumed by a process",
    "produces": "process wrote this output",
    "derives": "output computed from another output (e.g. table -> figure)",
    "code": "script that implements / generated the target",
    "part_of": "component of an article",
    "documents": "text section that describes the target",
    "related": "loose association",
}

PROJECT_FOLDERS = [
    "data/raw", "data/inputs", "pipelines", "scripts",
    "results/tables", "results/figures", "results/supplementary", "results/other",
    "articles", "notes",
]

MODES = ("managed", "ref")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return (s or "item")[:maxlen].strip("-")


def display_name(node: dict) -> str:
    return f"{node['id']} · {node['label']}"


def is_scalar_param(v) -> bool:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return True
    if isinstance(v, list):
        return all(isinstance(x, (str, int, float, bool)) for x in v)
    return False
