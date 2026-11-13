"""Evidence numbers and linked assets for a LaTeX manuscript (an article node's folder).

The article folder holds, next to `latex/`:

    Figures/ Tables/ Supplementary/   synced copies of the article's items (`assets.tsv` says from where)
    CitedArticles/                    the cited papers' PDFs (`index.tsv`: key r1.. -> file); highlighted
                                      copies are written to `CitedArticles/annotated/`
    MetaData/                         files that support a specific claim (a check, a parameter list)
    assets.tsv                        key  label  source  file          (F6  Figure 6  ../Figures/x.png  Figures/x.png)
    evidence.tsv                      id  target  page  quote  claim  note
                                      (E1  r1  4  "7 regions met Tier 1"  "9 regions reported ...")

`build()` syncs the copies, highlights each quoted passage in its cited PDF (a yellow highlight plus a
note "E1: claim"), and writes `latex/evidence.tex`, which gives the text short, readable markers:

    \\ev{E1}            superscript E1 linking to the evidence (the highlighted page of the paper)
    \\figref{6} \\figref[A]{6} \\tabref{8} \\supref{ST10}   "Figure 6A", linked to the file
    each \\bibitem gets a [PDF] link when the paper is in CitedArticles/

Three builds of the same sources (decided by the job name, so plain `latexmk` keeps working):
`main` (working: links to local files, or to the SciWeave server when it compiles), and
`main_submission` (every marker disappears, links are plain text): the journal copy.
PDF highlighting needs PyMuPDF; without it links go to the unmarked PDF and the report says so.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

from sciweave.project import SciWeaveError
from sciweave.schema import now_iso

ASSETS, EVIDENCE, CITED, INDEX = "assets.tsv", "evidence.tsv", "CitedArticles", "CitedArticles/index.tsv"
ANNOTATED = "CitedArticles/annotated"
FOLDERS = ["Figures", "Tables", "Supplementary", "CitedArticles", "MetaData"]
ASSET_COLS = ["key", "label", "source", "file"]
EVIDENCE_COLS = ["id", "target", "page", "where", "quote", "claim", "note"]
SUBMISSION_JOB = "main_submission"
SAFE_PATH = re.compile(r"^[A-Za-z0-9_.\-/]+$")  # goes into \href unescaped
SAFE_KEY = re.compile(r"^[A-Za-z0-9.\-]+$")
CITE_KEY = re.compile(r"^r\d+$")
ANNOTATE_VERSION = "3"  # bump when the highlight matching changes, so cached results are redone
NO_EVIDENCE = "sciweave: no-evidence"  # first-line mark of a planning page (claims, structure): never audited
GAPS = "evidence_gaps.tsv"
GAP_COLS = ["file", "anchor", "kind", "claim", "note", "cause"]
EV_USE = re.compile(r"\\ev\{([^}]*)\}")
# not claims: comments, citations, refs, locus/SNP/chromosome names, gene names with digits, section commands
SENT_IGNORE = re.compile(r"(?<!\\)%[^\n]*|\\(?:cite|ref|label|input|(?:fig|tab|sup)ref(?:\[[^\]]*\])?|"
                         r"(?:sub)*section\*?|textbf|hl)\{[^}]*\}|Loc-\d+|\brs\d+|\bchr\w+|\b\d+[pq]\d+(?:\.\d+)?"
                         r"|\b[A-Z][A-Z0-9-]*\d[A-Z0-9-]*\b|\b[A-Za-z]+\d+[A-Za-z]*\b")
SKIP_LINE = re.compile(r"^(?:\\bibitem|\\begin|\\end|\\(?:sub)*section|\\item|%|\\textbf\{(?:Figure|Table|ST|SF))"
                       r"|^\\(?:documentclass|usepackage|RequirePackage|newcommand|renewcommand|providecommand|def|"
                       r"setlength|setcounter|graphicspath|input|include|geometry|hypersetup|title|author|date)\b")
# (\title is skipped: markers inside it are fragile; the title's claims are evidenced where the text states them)
REF_USE = re.compile(r"\\(figref|tabref|supref)(?:\[([^\]]*)\])?\{([^}]*)\}")
TEX_SPECIAL = str.maketrans({"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#", "%": r"\%",
                             "&": r"\&", "_": r"\_", "$": r"\$", "^": r"\^{}", "~": r"\~{}"})


def read_tsv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rows
            if any((v or "").strip() for v in r.values())]


def read_tsv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [c.strip() for c in fh.readline().rstrip("\r\n").split("\t")]


def write_tsv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _sha(f: Path) -> str:
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tex_text(s: str) -> str:
    return s.translate(TEX_SPECIAL)


def _norm(s: str) -> str:
    """Quote matching ignores line breaks, hyphenation at line ends, ligatures and quote styles."""
    s = s.replace("\u00ad", "").replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    s = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", s).replace("\u2212", "-").replace("\u2013", "-")
    s = re.sub(r"-\s*\n\s*", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


class Evidence:
    def __init__(self, ms):
        self.ms = ms
        self.folder: Path = ms.folder
        self.root: Path = ms.root
        self.state: Path = ms.state

    # ------------------------------------------------------------ registry ----
    def enabled(self) -> bool:
        return any((self.folder / f).is_file() for f in (ASSETS, EVIDENCE))

    def init(self) -> dict:
        """Create the folders and empty registries (never overwrites)."""
        made = []
        for d in FOLDERS:
            if not (self.folder / d).is_dir():
                (self.folder / d).mkdir(parents=True)
                made.append(d + "/")
        for name, cols in ((ASSETS, ASSET_COLS), (EVIDENCE, EVIDENCE_COLS)):
            if not (self.folder / name).exists():
                write_tsv(self.folder / name, [], cols)
                made.append(name)
        return {"created": made}

    def assets(self) -> list[dict]:
        return read_tsv(self.folder / ASSETS)

    def entries(self) -> list[dict]:
        return read_tsv(self.folder / EVIDENCE)

    def cited(self) -> dict[str, dict]:
        return {r["key"]: r for r in read_tsv(self.folder / INDEX) if r.get("key")}

    def _inside(self, rel: str) -> Path:
        f = (self.folder / rel).resolve()
        if f != self.folder and self.folder not in f.parents:
            raise SciWeaveError(f"{rel} is outside the article folder")
        return f

    # ---------------------------------------------------------------- sync ----
    def sync(self) -> dict:
        """Copy every asset whose source changed since the last sync (the source stays the original)."""
        st_file = self.state / "assets_state.json"
        st = json.loads(st_file.read_text(encoding="utf-8")) if st_file.exists() else {}
        copied, missing, problems, same = [], [], [], 0
        for a in self.assets():
            key, src_rel, dst_rel = a.get("key", ""), a.get("source", ""), a.get("file", "")
            if not SAFE_KEY.match(key) or not SAFE_PATH.match(dst_rel) or ".." in dst_rel.split("/"):
                problems.append(f"{key or '?'}: key/file may only use letters, digits and _ . - / (inside the folder)")
                continue
            if not src_rel:  # lives only in the article folder
                if not self._inside(dst_rel).is_file():
                    missing.append(f"{key}: {dst_rel}")
                continue
            src = (self.folder / src_rel).resolve()
            dst = self._inside(dst_rel)
            if not src.is_file():
                missing.append(f"{key}: source {src_rel}")
                continue
            sha = _sha(src)
            if dst.is_file() and st.get(dst_rel, {}).get("sha") == sha and _sha(dst) == sha:
                same += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            st[dst_rel] = {"sha": sha, "source": src_rel, "synced": now_iso()}
            copied.append(key)
        self.state.mkdir(parents=True, exist_ok=True)
        st_file.write_text(json.dumps(st, indent=1), encoding="utf-8")
        return {"copied": copied, "unchanged": same, "missing": missing, "problems": problems}

    # ------------------------------------------------------------ targets ----
    def _cited_file(self, key: str, annotated: bool = True) -> str | None:
        row = self.cited().get(key)
        if not row or not row.get("file"):
            return None
        rel = f"{CITED}/{row['file']}"
        ann = f"{ANNOTATED}/{row['file']}"
        if annotated and (self.folder / ann).is_file():
            return ann
        return rel if (self.folder / rel).is_file() else None

    def resolve(self, e: dict, assets: dict[str, dict]) -> dict:
        """-> {path, page, kind ('cite'|'asset'|'file'), problem}; path is relative to the article folder."""
        t = e.get("target", "")
        page = e.get("page") or "1"
        if CITE_KEY.match(t):
            f = self._cited_file(t)
            return {"kind": "cite", "path": f, "page": page,
                    "problem": None if f else f"{t}: PDF not in {CITED}/ (index.tsv)"}
        if t in assets:
            f = assets[t].get("file", "")
            return {"kind": "asset", "path": f, "page": page,
                    "problem": None if (self.folder / f).is_file() else f"{t}: {f} missing (sync?)"}
        if not t:
            return {"kind": "file", "path": None, "page": page, "problem": "no target"}
        try:
            ok = bool(SAFE_PATH.match(t)) and self._inside(t).is_file()
        except SciWeaveError as exc:
            return {"kind": "file", "path": None, "page": page, "problem": str(exc)}
        return {"kind": "file", "path": t if ok else None, "page": page,
                "problem": None if ok else f"{t}: not a file in the article folder (or unsafe name)"}

    # ------------------------------------------------------- new papers ----
    def pick_up_pdfs(self) -> list[dict]:
        """Register PDFs the author dropped into the article folder or CitedArticles/ under any name:
        a PDF whose first pages contain the DOI of exactly one reference without a file is moved to
        CitedArticles/rNN_AuthorYear.pdf and recorded in index.tsv. Unmatched PDFs are left untouched."""
        index_f = self.folder / INDEX
        rows = read_tsv(index_f)
        if not rows:
            return []
        waiting = {r["key"]: r for r in rows if not r.get("file") and r.get("doi")}
        known = {r.get("file") for r in rows if r.get("file")}
        cands = [f for f in list(self.folder.glob("*.pdf")) + list((self.folder / CITED).glob("*.pdf"))
                 if f.name not in known]
        if not waiting or not cands:
            return []
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf
            except ImportError:
                return [{"file": f.name, "status": "PyMuPDF not installed: cannot read the PDF"} for f in cands]
        authors = self._first_authors()
        out = []
        for f in cands:
            try:
                with f.open("rb") as fh:
                    if fh.read(5) != b"%PDF-":
                        continue
                doc = pymupdf.open(f)
                text = " ".join(doc[i].get_text() for i in range(min(3, len(doc)))).lower()
                doc.close()
            except Exception as exc:  # an unreadable or still-downloading file: leave it alone
                out.append({"file": f.name, "status": f"unreadable: {exc}"})
                continue
            text = re.sub(r"\s+", "", text)
            hits = [k for k, r in waiting.items() if re.sub(r"\s+", "", r["doi"].lower()) in text]
            if len(hits) != 1:
                out.append({"file": f.name, "status": "no reference matched by DOI" if not hits else f"several matched: {hits}"})
                continue
            k = hits[0]
            r = waiting.pop(k)
            num = int(k[1:]) if k[1:].isdigit() else 0
            name = f"r{num:02d}_{authors.get(k, 'Paper')}{(r.get('year') or '')[:4]}.pdf"
            dst = self.folder / CITED / name
            if dst.exists():
                out.append({"file": f.name, "status": f"{name} already exists; left as is"})
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            r["file"], r["status"] = name, f"added by the author ({f.name})"
            out.append({"file": f.name, "status": f"registered as {k}: {name}"})
        if any(o["status"].startswith("registered") for o in out):
            write_tsv(index_f, rows, read_tsv_header(index_f))
        return out

    def _first_authors(self) -> dict[str, str]:
        """rN -> first author's surname (ASCII letters) from the \\bibitem entries."""
        import unicodedata
        out = {}
        for f in self.root.rglob("*.tex"):
            if "build" in f.relative_to(self.root).parts:
                continue
            for m in re.finditer(r"\\bibitem\{(r\d+)\}\s*([^\s,.]+)", f.read_text(encoding="utf-8", errors="replace")):
                name = unicodedata.normalize("NFKD", m.group(2)).encode("ascii", "ignore").decode()
                out[m.group(1)] = re.sub(r"[^A-Za-z]", "", name) or "Paper"
        return out

    # ------------------------------------------------------------ annotate ----
    def annotate(self) -> dict[str, dict]:
        """Highlight each cited quote in a copy of its PDF. -> {evidence id: {page, found, problem}}."""
        by_file: dict[str, list[dict]] = {}
        for e in self.entries():
            if CITE_KEY.match(e.get("target", "")) and e.get("quote"):
                src = self._cited_file(e["target"], annotated=False)
                if src:
                    by_file.setdefault(src, []).append(e)
        out: dict[str, dict] = {}
        if not by_file:
            return out
        try:
            import pymupdf  # optional: only for highlighting
        except ImportError:
            try:
                import fitz as pymupdf  # older PyMuPDF
            except ImportError:
                for es in by_file.values():
                    for e in es:
                        out[e["id"]] = {"page": e.get("page"), "found": None,
                                        "problem": "PyMuPDF not installed: PDF not highlighted"}
                return out
        cache_f = self.state / "annotate_cache.json"
        cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
        for src, es in by_file.items():
            dst = self.folder / ANNOTATED / Path(src).name
            sig = hashlib.sha256((ANNOTATE_VERSION + _sha(self.folder / src) + json.dumps(es, sort_keys=True)).encode()).hexdigest()
            if dst.is_file() and cache.get(src, {}).get("sig") == sig:
                out.update(cache[src]["result"])
                continue
            doc = pymupdf.open(self.folder / src)
            res = {}
            for e in es:
                res[e["id"]] = self._highlight(pymupdf, doc, e)
            dst.parent.mkdir(parents=True, exist_ok=True)
            doc.save(dst, garbage=3, deflate=True)
            doc.close()
            cache[src] = {"sig": sig, "result": res}
            out.update(res)
        self.state.mkdir(parents=True, exist_ok=True)
        cache_f.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        return out

    @staticmethod
    def _highlight(pymupdf, doc, e: dict) -> dict:
        quote = e["quote"]
        want = int(e["page"]) if (e.get("page") or "").isdigit() else None
        pages = ([want - 1] if want and 0 < want <= len(doc) else []) + \
            [i for i in range(len(doc)) if not want or i != want - 1]
        nq = _norm(quote)
        for i in pages:
            page = doc[i]
            if nq not in _norm(page.get_text()):
                continue
            quads = page.search_for(quote, quads=True)
            if not quads:  # the PDF's text differs in spacing/hyphens: highlight by words
                words = [w for w in page.get_text("words")]
                quads = _word_span(pymupdf, words, nq)
            if not quads:
                continue
            h = page.add_highlight_annot(quads)
            h.set_info(title=e["id"], content=f"{e['id']}: {e.get('claim', '')}".strip(": "))
            h.update()
            r = quads[0].rect if hasattr(quads[0], "rect") else quads[0]
            tag = page.add_freetext_annot(
                pymupdf.Rect(4, max(r.y0 - 1, 0), 40, max(r.y0 - 1, 0) + 13), e["id"], fontsize=8,
                text_color=(0.75, 0.1, 0.1), fill_color=(1, 1, 0.75))
            tag.set_info(title=e["id"], content=e.get("claim", ""))
            tag.update()
            return {"page": str(i + 1), "found": True, "problem": None}
        return {"page": e.get("page"), "found": False,
                "problem": f"{e['id']}: quote not found in the PDF text (check the wording)"}

    # ------------------------------------------------------------- the TeX ----
    def uses(self) -> dict[str, list[str]]:
        """Evidence ids and asset keys used in the .tex files -> ['file:line', ...]."""
        found: dict[str, list[str]] = {}
        for f in sorted(self.root.rglob("*.tex")):
            if "build" in f.relative_to(self.root).parts or f.name == "evidence.tex":
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                code = line.split("%")[0] if "\\%" not in line else line
                where = f"{f.relative_to(self.root).as_posix()}:{n}"
                for m in EV_USE.finditer(code):
                    for i in m.group(1).split(","):
                        found.setdefault(i.strip(), []).append(where)
                for m in REF_USE.finditer(code):
                    prefix = {"figref": "F", "tabref": "T", "supref": ""}[m.group(1)]
                    found.setdefault("@" + prefix + m.group(3).strip(), []).append(where)
        return found

    def build_tex(self, links: dict[str, dict], assets: list[dict]) -> str:
        L = [
            "% GENERATED by SciWeave (sciweave tex evidence build) from ../evidence.tsv, ../assets.tsv and",
            "% ../CitedArticles/index.tsv. Do not edit: change those files and rebuild (compiling rebuilds it).",
            "% In the text: \\ev{E1} or \\ev{E1,E2}; \\figref{6} \\figref[A]{6} \\tabref{8} \\tabref[b]{2} \\supref{ST10}.",
            "% Job 'main_submission' (or \\def\\swsubmission{} before \\input) hides every marker.",
            r"\makeatletter",
            r"\providecommand\swlinkbase{}% the SciWeave server sets it in build/<job>.swmode.tex",
            r"\def\swarticle{" + self.ms.id + "}",
            r"\providecommand\swroot{../../}% the PDF is in latex/build/, the article folder two levels up",
            r"\InputIfFileExists{build/\jobname.swmode.tex}{}{}",
            r"\edef\sw@job{\jobname}\edef\sw@subjob{\detokenize{" + SUBMISSION_JOB + "}}",
            r"\ifx\sw@job\sw@subjob\def\swsubmission{}\fi",
            r"\definecolor{swev}{RGB}{0,105,150}",
            r"\def\sw@pdfkind{pdf}",
            r"\def\sw@link#1#2#3#4{%  path, page, pdf | the viewer path (ev/E7, asset/F6), text",
            r"  \ifx\swlinkbase\@empty\def\sw@k{#3}%",
            r"    \ifx\sw@k\sw@pdfkind\href[page=#2]{\swroot#1}{#4}\else\href{run:\swroot#1}{#4}\fi",
            r"  \else\def\sw@k{#3}\ifx\sw@k\sw@pdfkind\href{\swlinkbase tex-asset/\swarticle/#1\#page=#2}{#4}%",
            r"    \else\href{\swlinkbase tex-view/\swarticle/#3}{#4}\fi\fi}",
            r"\newcommand\ev[1]{\ifdefined\swsubmission\else\textsuperscript{\sffamily\scriptsize\color{swev}\sw@evlist#1,\@nil}\fi}",
            r"\def\sw@evlist#1,#2\@nil{\sw@evone{#1}\ifx\relax#2\relax\else,\sw@evlist#2\@nil\fi}",
            r"\def\sw@evone#1{\edef\sw@id{\zap@space#1 \@empty}%",
            r"  \@ifundefined{sw@ev@\sw@id}{\@ifundefined{sw@evmiss@\sw@id}%",
            r"    {\textcolor{red}{\sw@id?}\PackageWarning{evidence}{Evidence \sw@id\space is not in evidence.tsv}}%",
            r"    {\textcolor{orange}{\sw@id}\PackageWarning{evidence}{Evidence \sw@id: its file is not available yet}}}%",
            r"  {\expandafter\expandafter\expandafter\sw@link\csname sw@ev@\sw@id\endcsname{\sw@id}}}",
            r"\def\sw@asset#1#2{% key, panel letter(s)",
            r"  \@ifundefined{sw@as@#1#2}{\sw@assetone{#1}{#2}}{\sw@assetone{#1#2}{}}}",
            r"\def\sw@assetone#1#2{\@ifundefined{sw@as@#1}%",
            r"  {\textcolor{red}{#1#2?}\PackageWarning{evidence}{Item #1 is not in assets.tsv}}%",
            r"  {\expandafter\expandafter\expandafter\sw@assetlink\csname sw@as@#1\endcsname{#2}}}",
            r"\def\sw@assetlink#1#2#3#4{% label, path, kind, panel",
            r"  \ifdefined\swsubmission#1#4\else\sw@link{#2}{1}{#3}{#1#4}\fi}",
            r"\newcommand\figref[2][]{\sw@asset{F#2}{#1}}",
            r"\newcommand\tabref[2][]{\sw@asset{T#2}{#1}}",
            r"\newcommand\supref[2][]{\sw@asset{#2}{#1}}",
            r"\def\sw@bibpdf#1{\ifdefined\swsubmission\else\@ifundefined{sw@bib@#1}{}%",
            r"  {\expandafter\expandafter\expandafter\sw@link\csname sw@bib@#1\endcsname{1}{pdf}{\textsuperscript{\sffamily\scriptsize\color{swev}[PDF]}}\ }\fi}",
            r"\AtBeginDocument{\let\sw@bibitem@orig\@bibitem\def\@bibitem#1{\sw@bibitem@orig{#1}\sw@bibpdf{#1}}}",
            "",
        ]
        kind = lambda path, view: "pdf" if path.lower().endswith(".pdf") else view
        L.append("% --- items (assets.tsv) ---")
        for a in assets:
            k, f = a.get("key", ""), a.get("file", "")
            if SAFE_KEY.match(k) and SAFE_PATH.match(f):
                L.append(rf"\@namedef{{sw@as@{k}}}{{{{{tex_text(a.get('label') or k)}}}{{{f}}}{{{kind(f, 'asset/' + k)}}}}}")
        L.append("% --- evidence (evidence.tsv) ---")
        for eid, ln in links.items():
            if ln.get("path"):
                L.append(rf"\@namedef{{sw@ev@{eid}}}{{{{{ln['path']}}}{{{ln['page']}}}{{{kind(ln['path'], 'ev/' + eid)}}}}}"
                         f"  % {ln['target']}")
            else:
                L.append(rf"\@namedef{{sw@evmiss@{eid}}}{{}}  % {ln['target']}: file not available")
        L.append("% --- cited papers (CitedArticles/index.tsv) ---")
        for key in sorted(self.cited(), key=lambda k: int(k[1:]) if k[1:].isdigit() else 0):
            f = self._cited_file(key)
            if f and SAFE_PATH.match(f):
                L.append(rf"\@namedef{{sw@bib@{key}}}{{{{{f}}}}}")
        L.append("% --- \\swevidencelist: the evidence register (working PDF only) ---")
        L.append(r"\newcommand\swevidencelist{\ifdefined\swsubmission\else\clearpage\section*{Evidence register}"
                 r"\begin{description}\setlength\itemsep{0pt}\small")
        for eid, ln in links.items():
            item = rf"\ev{{{eid}}}" if ln.get("path") else eid
            L.append(rf"\item[{eid}] {tex_text(ln.get('claim', ''))} \textit{{({tex_text(ln['target'])}"
                     rf"{', p. ' + ln['page'] if ln['kind'] == 'cite' and ln.get('path') else ''})}}"
                     rf"{' ' + item if ln.get('path') else r' \textcolor{red}{(missing)}'}")
        L.append(r"\end{description}\fi}")
        L += self.build_rev_tex()
        L.append(r"\makeatother")
        return "\n".join(L) + "\n"

    def build_rev_tex(self) -> list[str]:
        """\\rev{R1.05}: a revision marker (revisions/revisions.tsv) linking to that change in the editor's Revisions
        tab (or, without the server, to the file the reviewer sent); \\swrevisionlist: the register (working PDF)."""
        rows = read_tsv(self.folder / "revisions" / "revisions.tsv")
        L = ["% --- revisions (revisions/revisions.tsv): \\rev{R1.05} or \\rev{R1.05,R1.06} ---",
             r"\definecolor{swrev}{RGB}{130,60,150}",
             r"\newcommand\rev[1]{\ifdefined\swsubmission\else\textsuperscript{\sffamily\scriptsize\color{swrev}\sw@rvlist#1,\@nil}\fi}",
             r"\def\sw@rvlist#1,#2\@nil{\sw@rvone{#1}\ifx\relax#2\relax\else,\sw@rvlist#2\@nil\fi}",
             r"\def\sw@rvone#1{\edef\sw@id{\zap@space#1 \@empty}\@ifundefined{sw@rv@\sw@id}%",
             r"  {\textcolor{red}{\sw@id?}\PackageWarning{evidence}{Revision \sw@id\space is not in revisions.tsv}}%",
             r"  {\expandafter\expandafter\expandafter\sw@rvlink\csname sw@rv@\sw@id\endcsname{\sw@id}}}",
             r"\def\sw@rvlink#1#2#3{% reviewer, the file the reviewer returned, id",
             r"  \ifx\swlinkbase\@empty\href{run:\swroot#2}{#3}%",
             r"  \else\href{\swlinkbase latex.html\string?article=\swarticle\string&tab=revisions\string&r=#3}{#3}\fi}"]
        items = []
        for r in rows:
            rid, src = r.get("r_id", ""), r.get("source_file", "")
            if not SAFE_KEY.match(rid):
                continue
            src = src if SAFE_PATH.match(src) else ""
            L.append(rf"\@namedef{{sw@rv@{rid}}}{{{{{tex_text(r.get('reviewer', ''))}}}{{{src}}}}}")
            items.append(rf"\item[{rid}] \rev{{{rid}}} {tex_text(r.get('reviewer', ''))}, {tex_text(r.get('received', ''))}: "
                         rf"{tex_text(r.get('section', ''))} --- {tex_text(r.get('decision', ''))}"
                         + (rf" ({tex_text(r.get('proposal', ''))})" if r.get("proposal") else ""))
        L.append(r"\newcommand\swrevisionlist{\ifdefined\swsubmission\else" + (
            r"\clearpage\section*{Revision register}\begin{description}\setlength\itemsep{0pt}\small" if items else ""))
        L += items
        L.append((r"\end{description}" if items else "") + r"\fi}")
        return L

    # --------------------------------------------------------------- build ----
    def build(self, sync: bool = True) -> dict:
        if not self.enabled():
            return {"enabled": False}
        rep = {"enabled": True, "ts": now_iso(), "sync": self.sync() if sync else None,
               "picked_up": self.pick_up_pdfs()}
        assets = self.assets()
        amap = {a["key"]: a for a in assets if a.get("key")}
        ann = self.annotate()
        links, problems, seen = {}, [], set()
        for e in self.entries():
            eid = e.get("id", "")
            if not re.match(r"^E\d+$", eid):
                problems.append(f"bad evidence id {eid!r} (use E1, E2, ...)")
                continue
            if eid in seen:
                problems.append(f"{eid} is listed twice in evidence.tsv")
                continue
            seen.add(eid)
            r = self.resolve(e, amap)
            a = ann.get(eid)
            if a:
                if a.get("page"):
                    r["page"] = a["page"]
                if a.get("problem"):
                    problems.append(a["problem"])
            if r.get("problem"):
                problems.append(r["problem"])
            links[eid] = {**r, "target": e.get("target", ""), "claim": e.get("claim", ""),
                          "quote": e.get("quote", ""), "note": e.get("note", ""), "where": e.get("where", ""),
                          "highlighted": bool(a and a.get("found"))}
        uses = self.uses()
        for name, where in uses.items():
            if name.startswith("@"):
                key = name[1:]
                if key not in amap and not any(k.startswith(key) for k in amap):
                    problems.append(f"{key} used at {where[0]} is not in assets.tsv")
            elif name not in links:
                problems.append(f"{name} used at {where[0]} is not in evidence.tsv")
        for eid in links:
            links[eid]["used"] = uses.get(eid, [])
        text = self.build_tex(links, assets)
        out = self.root / "evidence.tex"
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            out.write_text(text, encoding="utf-8")
        rep.update({"evidence": links, "assets": assets, "problems": problems,
                    "unused": [i for i in links if not uses.get(i)], "missing": self.missing(links)})
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "evidence.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        return rep

    # ------------------------------------------------------------- missing ----
    def gaps(self) -> list[dict]:
        return read_tsv(self.folder / GAPS)

    def missing(self, links: dict[str, dict]) -> list[dict]:
        """What still lacks evidence, for the editor's Missing tab:
        gap      a claim recorded in evidence_gaps.tsv (no source found, or it overreaches), located by its anchor;
                 with a `cause` (the accepted reason it has no evidence) it is "explained", not an open problem
        mismatch an evidence entry whose source disagrees with the text (note starts with MISMATCH)
        nofile   an evidence entry whose file is not in the folder yet (e.g. a paper to add)
        unmarked a sentence with a number and no \\ev marker (found automatically)"""
        texts = {}
        for f in sorted(self.root.rglob("*.tex")):
            rel = f.relative_to(self.root).as_posix()
            if "build" in f.relative_to(self.root).parts:
                continue
            t = f.read_text(encoding="utf-8", errors="replace")
            if t.startswith("% GENERATED") or NO_EVIDENCE in t.split("\n", 1)[0]:
                continue  # generated files and evidence-less planning pages (key claims, structure) are not audited
            texts[rel] = t
        out = []

        def line_of(rel: str, needle: str) -> int | None:
            t = texts.get(rel)
            if not t or not needle or t.count(needle) != 1:
                return None
            return t[:t.index(needle)].count("\n") + 1

        gap_spans: dict[str, list[tuple[int, int]]] = {}
        for g in self.gaps():
            rel = g.get("file", "")
            ln = line_of(rel, g.get("anchor", ""))
            if ln is not None:
                p = texts[rel].index(g["anchor"])
                gap_spans.setdefault(rel, []).append((p, p + len(g["anchor"])))
            out.append({"kind": "gap", "status": g.get("kind", ""), "file": rel, "line": ln, "anchor": g.get("anchor", ""),
                        "claim": g.get("claim", ""), "note": g.get("note", ""),
                        "cause": g.get("cause", ""), "explained": bool(g.get("cause", "").strip()),
                        "stale": ln is None})
        for eid, e in links.items():
            where = (e.get("used") or [None])[0]
            f, ln = (where.rsplit(":", 1)[0], int(where.rsplit(":", 1)[1])) if where else (None, None)
            if (e.get("note") or "").startswith("MISMATCH"):
                out.append({"kind": "mismatch", "id": eid, "file": f, "line": ln, "claim": e.get("claim", ""),
                            "note": e["note"]})
            if not e.get("path"):
                out.append({"kind": "nofile", "id": eid, "file": f, "line": ln, "claim": e.get("claim", ""),
                            "note": e.get("problem") or "file not available"})
        for rel, t in texts.items():
            if "\\ev{" not in t:  # files that do not use evidence markers at all are not audited
                continue
            # a tabular with one \ev marker (e.g. in its header row) is evidenced as a whole: its rows are not audited
            covered = [(m.start(), m.end()) for m in re.finditer(r"(?s)\\begin\{tabular\*?\}.*?\\end\{tabular\*?\}", t)
                       if "\\ev{" in m.group(0)]
            pos = 0
            for ln, line in enumerate(t.split("\n"), 1):
                start = pos
                pos += len(line) + 1
                if SKIP_LINE.match(line.strip()) or any(x <= start < y for x, y in covered):
                    continue
                off = 0
                for s in re.split(r"(?<=[.?!])\s+(?=[A-Z\\(])", line):
                    a = start + line.index(s, off)
                    off = a - start + len(s)
                    body = SENT_IGNORE.sub(" ", s)
                    if "\\ev{" in s or not re.search(r"\d", body):
                        continue
                    if any(x < a + len(s) and a < y for x, y in gap_spans.get(rel, [])):
                        continue
                    out.append({"kind": "unmarked", "file": rel, "line": ln, "anchor": s.strip(),
                                "claim": " ".join(s.split())[:220], "note": "a number with no evidence marker"})
        order = {"gap": 0, "mismatch": 1, "nofile": 2, "unmarked": 3}
        # gaps with a recorded cause (known, accepted reason) go last: they are not open problems
        return sorted(out, key=lambda x: (bool(x.get("explained")), order[x["kind"]], x.get("file") or "", x.get("line") or 0))

    def report(self) -> dict:
        f = self.state / "evidence.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"enabled": self.enabled()}


def _word_span(pymupdf, words: list, nq: str) -> list:
    """Rects of the consecutive words whose normalised text equals the quote (fallback search)."""
    toks: list[tuple[str, list]] = []  # (normalised word, its rects); a word hyphenated at a line end is joined
    for w in words:
        t = _norm(w[4])
        if toks and toks[-1][0].endswith("-") and len(toks[-1][0]) > 1 and t:
            prev, rects = toks[-1]
            toks[-1] = (prev[:-1] + t, rects + [w])
        else:
            toks.append((t, [w]))
    strip = lambda x: x.strip(".,;:()[]'\"")  # punctuation glued to a word does not decide a match
    target = [strip(t) for t in nq.split(" ")]
    n = len(target)
    for i in range(len(toks) - n + 1):
        if [strip(t) for t, _ in toks[i:i + n]] == target:
            return [pymupdf.Rect(w[:4]) for _, ws in toks[i:i + n] for w in ws]
    return []
