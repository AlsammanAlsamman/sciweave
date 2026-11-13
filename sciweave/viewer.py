"""Read-only viewers for the files that back a manuscript's evidence (stdlib only).

`/tex-view/<article>/ev/<E7>` opens an evidence entry, `/tex-view/<article>/asset/<F6>` an item; both pick
the viewer by file type: spreadsheets (.xlsx) and delimited text (.tsv/.csv) as tables with the rows
that support the claim highlighted and scrolled into view, images with their legend, text as text.
PDFs are left to the browser's own viewer (the caller redirects to the file with #page=).

Where to look is the evidence's `where` column: `sheet=<name or number>; find=<text>|<text>` (rows
containing any of the texts, case-insensitive, are highlighted; `row=12` highlights a row by number).
"""

from __future__ import annotations

import csv
import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAX_ROWS = 4000
TEXT_EXT = {".txt", ".md", ".tex", ".json", ".log", ".py", ".r", ".R", ".sh", ".java", ".smk", ".yaml", ".yml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".svg", ".gif"}


def parse_where(where: str) -> dict:
    out = {"sheet": None, "find": [], "rows": []}
    for part in re.split(r"\s*;\s*", (where or "").strip()):
        if "=" not in part:
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        if k == "sheet":
            out["sheet"] = v
        elif k == "find":
            out["find"] = [x.strip() for x in v.split("|") if x.strip()]
        elif k == "row":
            out["rows"] = [int(x) for x in re.findall(r"\d+", v)]
    return out


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.4g}" if (abs(f) < 1e-3 or abs(f) >= 1e6) else f"{f:.6g}"


def read_xlsx(path: Path) -> list[tuple[str, list[list[str]]]]:
    """[(sheet name, rows)] -- values as shown (numbers in general format, no styles)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        rels = {r.get("Id"): r.get("Target") for r in
                ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("pr:Relationship", NS)}
        out = []
        for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS):
            target = rels.get(sh.get(f"{{{NS['r']}}}id"), "")
            target = target.lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            rows: list[list[str]] = []
            if target in names:
                for row in ET.fromstring(z.read(target)).iter(f"{{{NS['m']}}}row"):
                    vals: dict[int, str] = {}
                    for c in row.findall("m:c", NS):
                        t, v = c.get("t"), c.find("m:v", NS)
                        if t == "s" and v is not None:
                            val = shared[int(v.text)]
                        elif t == "inlineStr":
                            val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                        elif v is not None and v.text is not None:
                            val = v.text if t in ("str", "e", "b") else _num(v.text)
                        else:
                            continue
                        vals[_col(c.get("r", "A1"))] = val
                    if vals:
                        r = [""] * (max(vals) + 1)
                        for i, x in vals.items():
                            r[i] = x
                        rows.append(r)
                    if len(rows) >= MAX_ROWS:
                        break
            out.append((sh.get("name", "?"), rows))
        return out


def read_delimited(path: Path) -> list[tuple[str, list[list[str]]]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delim = "\t" if path.suffix.lower() == ".tsv" or text.count("\t") > text.count(",") else ","
    rows = [r for r in csv.reader(text.splitlines(), delimiter=delim)][:MAX_ROWS]
    return [(path.name, rows)]


def _hits(rows: list[list[str]], w: dict) -> set[int]:
    """Rows matching any find term; a term `a&b` needs both in the same row."""
    terms = [[p.strip().lower() for p in t.split("&") if p.strip()] for t in w["find"]]
    hit = {r - 1 for r in w["rows"]}
    if terms:
        for i, r in enumerate(rows):
            line = "\t".join(r).lower()
            if any(all(p in line for p in t) for t in terms):
                hit.add(i)
    return hit


def _table_html(sheets, w: dict) -> tuple[str, str]:
    """-> (tabs+tables html, summary line)."""
    want = w["sheet"]
    pick = 0
    if want is not None:
        for i, (name, _) in enumerate(sheets):
            if name.lower() == want.lower() or (want.isdigit() and int(want) == i + 1):
                pick = i
                break
    elif w["find"] or w["rows"]:  # open the first sheet that has a match
        for i, (_, rows) in enumerate(sheets):
            if _hits(rows, w):
                pick = i
                break
    tabs, bodies, summary = [], [], ""
    terms = [p.strip() for t in w["find"] for p in t.split("&") if p.strip()]  # marked inside hit rows
    term_re = re.compile("(" + "|".join(re.escape(html.escape(t)) for t in terms) + ")", re.I) if terms else None
    for i, (name, rows) in enumerate(sheets):
        hits = _hits(rows, w)
        if i == pick:
            summary = (f"{len(hits)} matching row{'s' if len(hits) != 1 else ''} highlighted" if (terms or w["rows"])
                       else f"{len(rows)} rows")
        tabs.append(f'<button class="tab{" on" if i == pick else ""}" data-tab="{i}">{html.escape(name)}'
                    f'{f" <b>{len(hits)}</b>" if hits else ""}</button>')
        width = max((len(r) for r in rows), default=0)
        trs = []
        for j, r in enumerate(rows):
            filled = [x for x in r if x]
            if len(filled) == 1 and len(filled[0]) > 120:  # a caption/note row: one cell across the table
                trs.append(f'<tr class="cap"><th>{j + 1}</th><td colspan="{max(width, 1)}">'
                           f'<div class="captxt">{html.escape(filled[0])}</div></td></tr>')
                continue
            cells = []
            for x in r + [""] * (width - len(r)):
                e = html.escape(x)
                if term_re is not None and j in hits:
                    e = term_re.sub(r"<mark>\1</mark>", e)
                cells.append(f"<td{' class=long' if len(x) > 120 else ''}>{e}</td>")
            trs.append(f'<tr class="{"hit" if j in hits else ""}"><th>{j + 1}</th>{"".join(cells)}</tr>')
        bodies.append(f'<div class="sheet" data-sheet="{i}"{"" if i == pick else " hidden"}>'
                      f'<table>{"".join(trs)}</table>'
                      f'{"<p class=more>(first %d rows)</p>" % MAX_ROWS if len(rows) >= MAX_ROWS else ""}</div>')
    tabbar = f'<div class="tabs">{"".join(tabs)}</div>' if len(sheets) > 1 else ""
    return tabbar + "".join(bodies), summary


CSS = """
:root{--bg:#fcfcfb;--fg:#0b0b0b;--mut:#6b6a65;--line:rgba(20,20,18,.12);--hit:#fff3b0;--hd:#efeee8;--acc:#2a78d6}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--fg:#f4f4f2;--mut:#a3a29a;--line:rgba(255,255,255,.12);--hit:#5a4d12;--hd:#232321;--acc:#5aa0f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:3;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 12px}
h1{font-size:14px;margin:0}.sub{color:var(--mut);font-size:12px;margin-top:2px}.claim{margin-top:4px}
.quote{color:var(--mut);font-style:italic;margin-top:2px}
.tabs{display:flex;gap:4px;flex-wrap:wrap;padding:6px 12px;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);background:var(--hd);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer;font:inherit}
.tab.on{border-color:var(--acc);box-shadow:inset 0 -2px 0 var(--acc)}.tab b{color:var(--acc)}
.wrap{overflow:auto;padding:0 0 30px}table{border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{border:1px solid var(--line);padding:3px 7px;vertical-align:top;white-space:nowrap}
td.long{white-space:normal;min-width:320px;max-width:560px;color:var(--mut)}
tr.cap td{white-space:normal;color:var(--mut);font-size:12px}.captxt{max-width:1100px;position:sticky;left:40px}
th{position:sticky;left:0;background:var(--hd);color:var(--mut);font-weight:500;text-align:right}
tr.hit td{background:var(--hit)}tr.hit th{color:var(--fg);font-weight:700}mark{background:#ffd84d;color:#000;border-radius:2px}
.more{color:var(--mut);padding:8px 12px}pre{white-space:pre-wrap;padding:12px;margin:0}
.img{padding:12px}.img img{max-width:100%;height:auto;background:#fff}.legend{padding:0 12px 20px;max-width:1000px;white-space:pre-wrap}
a{color:var(--acc)}
"""
JS = """
document.addEventListener('click',function(e){var t=e.target.closest('[data-tab]');if(!t)return;
var i=t.getAttribute('data-tab');document.querySelectorAll('.tab').forEach(function(b){b.classList.toggle('on',b===t)});
document.querySelectorAll('.sheet').forEach(function(s){s.hidden=s.getAttribute('data-sheet')!==i});});
var h=document.querySelector('.sheet:not([hidden]) tr.hit');if(h)h.scrollIntoView({block:'center'});
"""


def page(title: str, sub: str, body: str, claim: str = "", quote: str = "", file_url: str = "") -> str:
    head = (f"<h1>{html.escape(title)}</h1><div class=sub>{html.escape(sub)}"
            f"{f' · <a href=\"{html.escape(file_url)}\" target=_blank rel=noopener>open the file</a>' if file_url else ''}</div>"
            + (f"<div class=claim>{html.escape(claim)}</div>" if claim else "")
            + (f"<div class=quote>{html.escape(quote)}</div>" if quote else ""))
    return (f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><header>{head}</header>{body}"
            f"<script>{JS}</script></body></html>")


def render(path: Path, rel: str, where: str = "", title: str = "", claim: str = "", quote: str = "",
           legend: str = "", file_url: str = "") -> str:
    w = parse_where(where)
    ext = path.suffix.lower()
    title = title or rel
    if ext == ".xlsx":
        body, summ = _table_html(read_xlsx(path), w)
        return page(title, f"{rel} · {summ}", f"<div class=wrap>{body}</div>", claim, quote, file_url)
    if ext in (".tsv", ".csv"):
        body, summ = _table_html(read_delimited(path), w)
        return page(title, f"{rel} · {summ}", f"<div class=wrap>{body}</div>", claim, quote, file_url)
    if ext in IMAGE_EXT:
        body = (f"<div class=img><img src=\"{html.escape(file_url)}\" alt=\"{html.escape(title)}\"></div>"
                + (f"<div class=legend>{html.escape(legend)}</div>" if legend else ""))
        return page(title, rel + (f" · panel {w['find'][0]}" if w["find"] else ""), body, claim, quote, file_url)
    if ext in TEXT_EXT:
        text = html.escape(path.read_text(encoding="utf-8", errors="replace"))
        for t in w["find"]:
            text = re.sub("(" + re.escape(html.escape(t)) + ")", r"<mark>\1</mark>", text, flags=re.I)
        return page(title, rel, f"<pre>{text}</pre>", claim, quote, file_url)
    return page(title, rel, "<p class=more>No viewer for this file type here: open the file.</p>", claim, quote, file_url)
