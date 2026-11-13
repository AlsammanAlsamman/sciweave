import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from sciweave import manuscript as ms_mod
from sciweave.evidence import Evidence, write_tsv
from sciweave.manuscript import Manuscript
from sciweave.project import Project


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIWEAVE_HOME", str(tmp_path / "sciweave-home"))


MAIN = ("\\documentclass{article}\n\\usepackage{xcolor}\n\\usepackage[hidelinks]{hyperref}\n\\input{evidence}\n"
        "\\begin{document}\n\\input{sections/results}\n\\begin{thebibliography}{9}\n"
        "\\bibitem{r1} Earlier study.\n\\end{thebibliography}\n\\swevidencelist\n\\end{document}\n")
RESULTS = ("\\section{Results}\n"
           "Seven of nine regions were recovered\\ev{E1,E2} (\\tabref{2}; \\figref[A]{1}; \\supref{ST3})~\\cite{r1}.\n")
QUOTE = "In total, 7 regions met Tier 1 criteria"


def _pdf(path, lines):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in lines:
        doc.new_page().insert_text((72, 100), text, fontsize=11)
    doc.save(path)


@pytest.fixture
def ev(tmp_path):
    p = Project.init(tmp_path / "proj", name="Test")
    work = tmp_path / "results"
    work.mkdir()
    (work / "fig1.png").write_bytes(b"\x89PNG one")
    (work / "table2.tsv").write_text("locus\tP\nLoc-10\t6.8e-17\n", encoding="utf-8")
    folder = tmp_path / "paper"
    (folder / "latex" / "sections").mkdir(parents=True)
    (folder / "latex" / "main.tex").write_text(MAIN, encoding="utf-8")
    (folder / "latex" / "sections" / "results.tex").write_text(RESULTS, encoding="utf-8")
    art = ms_mod.attach(p, folder, "SLE paper", step="article")
    p.commit()
    e = Evidence(Manuscript(Project(p.root), art["id"]))
    e.init()
    write_tsv(folder / "assets.tsv", [
        {"key": "F1", "label": "Figure 1", "source": "../results/fig1.png", "file": "Figures/fig1.png"},
        {"key": "T2", "label": "Table 2", "source": "../results/table2.tsv", "file": "Tables/table2.tsv"},
        {"key": "ST3", "label": "Supplementary Table ST3", "source": "", "file": "Supplementary/st3.tsv"},
    ], ["key", "label", "source", "file"])
    (folder / "Supplementary" / "st3.tsv").write_text("x\n", encoding="utf-8")
    (folder / "MetaData" / "replication.tsv").write_text("region\tstatus\n", encoding="utf-8")
    write_tsv(folder / "CitedArticles" / "index.tsv", [{"key": "r1", "file": "r01_Study2016.pdf"}], ["key", "file"])
    write_tsv(folder / "evidence.tsv", [
        {"id": "E1", "target": "r1", "page": "", "quote": QUOTE, "claim": "nine regions reported"},
        {"id": "E2", "target": "MetaData/replication.tsv", "claim": "seven recovered"},
        {"id": "E3", "target": "T2", "claim": "unused here"},
    ], ["id", "target", "page", "quote", "claim", "note"])
    return e


def test_sync_copies_only_changed_sources(ev, tmp_path):
    r = ev.sync()
    assert sorted(r["copied"]) == ["F1", "T2"] and not r["missing"]
    assert (ev.folder / "Figures" / "fig1.png").read_bytes() == b"\x89PNG one"
    assert ev.sync()["copied"] == []  # nothing changed
    (tmp_path / "results" / "fig1.png").write_bytes(b"\x89PNG two")
    assert ev.sync()["copied"] == ["F1"]
    assert (ev.folder / "Figures" / "fig1.png").read_bytes() == b"\x89PNG two"


def test_build_highlights_quote_and_writes_macros(ev):
    _pdf(ev.folder / "CitedArticles" / "r01_Study2016.pdf", ["Abstract page.", "Results. " + QUOTE + ", and more."])
    rep = ev.build()
    e1 = rep["evidence"]["E1"]
    assert e1["highlighted"] and e1["page"] == "2" and e1["path"] == "CitedArticles/annotated/r01_Study2016.pdf"
    assert rep["evidence"]["E2"]["used"] == ["sections/results.tex:2"]
    assert rep["unused"] == ["E3"] and rep["problems"] == []
    import pymupdf
    doc = pymupdf.open(ev.folder / e1["path"])
    kinds = [a.type[1] for a in doc[1].annots()]
    assert "Highlight" in kinds and "FreeText" in kinds
    assert next(a for a in doc[1].annots() if a.type[1] == "Highlight").info["content"].startswith("E1:")
    tex = (ev.root / "evidence.tex").read_text(encoding="utf-8")
    assert r"\@namedef{sw@ev@E1}{{CitedArticles/annotated/r01_Study2016.pdf}{2}{pdf}}" in tex
    assert r"\@namedef{sw@as@F1}{{Figure 1}{Figures/fig1.png}{asset/F1}}" in tex
    assert tex.startswith("% GENERATED")
    # the original PDF is never modified
    assert not list(pymupdf.open(ev.folder / "CitedArticles" / "r01_Study2016.pdf")[1].annots())


def test_problems_are_reported_not_hidden(ev):
    _pdf(ev.folder / "CitedArticles" / "r01_Study2016.pdf", ["Nothing relevant here."])
    write_tsv(ev.folder / "evidence.tsv", [
        {"id": "E1", "target": "r1", "quote": QUOTE},
        {"id": "E2", "target": "../../proj/.sciweave/graph.json"},
        {"id": "E2", "target": "MetaData/replication.tsv"},
    ], ["id", "target", "page", "quote", "claim", "note"])
    (ev.root / "sections" / "results.tex").write_text(RESULTS + "More\\ev{E9}; \\figref{7}.\n", encoding="utf-8")
    probs = " | ".join(ev.build()["problems"])
    assert "E1: quote not found" in probs
    assert "outside the article folder" in probs or "not a file in the article folder" in probs
    assert "E2 is listed twice" in probs
    assert "E9 used at sections/results.tex:3 is not in evidence.tsv" in probs
    assert "F7 used at" in probs


@pytest.mark.skipif(not shutil.which("latexmk"), reason="needs latexmk")
def test_compile_working_and_submission(ev):
    pymupdf = pytest.importorskip("pymupdf")
    _pdf(ev.folder / "CitedArticles" / "r01_Study2016.pdf", [QUOTE])
    ms = ev.ms
    r = ms.compile()
    assert r["ok"], r["errors"]
    doc = pymupdf.open(ms.pdf_path())
    text = doc[0].get_text()
    assert "E1,E2" in text and "Table 2" in text and "Figure 1A" in text and "[PDF]" in text
    files = {ln.get("file") for pg in doc for ln in pg.get_links()}
    assert "../../CitedArticles/annotated/r01_Study2016.pdf" in files and "../../Tables/table2.tsv" in files
    r = ms.compile(link_base="http://127.0.0.1:9/")
    uris = {ln.get("uri") for pg in pymupdf.open(ms.pdf_path()) for ln in pg.get_links()}
    a = ms.id
    assert f"http://127.0.0.1:9/tex-asset/{a}/CitedArticles/annotated/r01_Study2016.pdf#page=1" in uris
    assert f"http://127.0.0.1:9/tex-view/{a}/ev/E2" in uris and f"http://127.0.0.1:9/tex-view/{a}/asset/T2" in uris
    s = ms.compile(submission=True)
    assert s["ok"] and s["job"] == "main_submission"
    sub = pymupdf.open(ms.pdf_path("main_submission"))
    stext = "".join(pg.get_text() for pg in sub)
    assert "E1" not in stext and "[PDF]" not in stext and "Evidence register" not in stext
    assert "Table 2" in stext
    assert not [ln for pg in sub for ln in pg.get_links() if ln.get("file") or ln.get("uri")]  # internal \cite links only


def test_server_serves_article_files_only(ev):
    ev.build()
    from sciweave import server
    server.Handler.project_root = ev.ms.p.root
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    try:
        a = ev.ms.id
        body = urllib.request.urlopen(base + f"tex-asset/{a}/MetaData/replication.tsv?raw=1")
        assert body.headers["Content-Type"].startswith("text/plain") and body.read().startswith(b"region")
        # without ?raw a table opens in the viewer instead of being offered for download
        body = urllib.request.urlopen(base + f"tex-asset/{a}/MetaData/replication.tsv")
        page = body.read().decode()
        assert body.headers["Content-Type"].startswith("text/html") and "<table>" in page and "?raw=1" in page
        for bad in (f"tex-asset/{a}/../proj/.sciweave/graph.json", f"tex-asset/{a}/%2e%2e/proj/.sciweave/graph.json"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + bad)
        rep = json.loads(urllib.request.urlopen(base + f"api/tex/evidence?article={a}").read())
        assert "E2" in rep["evidence"]
    finally:
        httpd.shutdown()


def test_viewer_tables_highlight_supporting_rows(tmp_path):
    from sciweave import viewer
    t = tmp_path / "x.tsv"
    t.write_text("locus\tP\nLoc-10\t6.8e-17\nLoc-12\t3.0e-15\n", encoding="utf-8")
    html = viewer.render(t, "MetaData/x.tsv", where="find=Loc-12", claim="NCF2 recovered")
    assert '<tr class="hit"><th>3</th>' in html and "<mark>Loc-12</mark>" in html and "NCF2 recovered" in html
    assert "1 matching row highlighted" in html
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Caption"
    wb.active["A1"] = "Table 8. <script>alert(1)</script>"
    ws = wb.create_sheet("Data")
    ws.append(["Method", "AUC"])
    ws.append(["PRS-CSx joint", 0.7661])
    ws.append(["C+T AMR", 0.703])
    wb.save(tmp_path / "t.xlsx")
    sheets = viewer.read_xlsx(tmp_path / "t.xlsx")
    assert [n for n, _ in sheets] == ["Caption", "Data"] and sheets[1][1][1] == ["PRS-CSx joint", "0.7661"]
    html = viewer.render(tmp_path / "t.xlsx", "Tables/t.xlsx", where="find=PRS-CSx joint")
    assert 'data-sheet="1">' in html and '<div class="sheet" data-sheet="0" hidden>' in html  # opens the matching sheet
    assert "<script>alert" not in html  # cell text is escaped


def test_tex_view_endpoint(ev):
    _pdf(ev.folder / "CitedArticles" / "r01_Study2016.pdf", [QUOTE])
    write_tsv(ev.folder / "evidence.tsv", [
        {"id": "E1", "target": "r1", "quote": QUOTE, "claim": "nine regions"},
        {"id": "E2", "target": "T2", "where": "find=Loc-10", "claim": "Loc-10 recovered"},
    ], ["id", "target", "page", "where", "quote", "claim", "note"])
    ev.build()
    from sciweave import server
    server.Handler.project_root = ev.ms.p.root
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    a = ev.ms.id
    try:
        html = urllib.request.urlopen(base + f"tex-view/{a}/ev/E2").read().decode()
        assert "<mark>Loc-10</mark>" in html and "Loc-10 recovered" in html
        r = urllib.request.urlopen(base + f"tex-view/{a}/ev/E1")  # PDF: redirected to the file at its page
        assert r.url.endswith(f"tex-asset/{a}/CitedArticles/annotated/r01_Study2016.pdf#page=1") and r.read(4) == b"%PDF"
        html = urllib.request.urlopen(base + f"tex-view/{a}/asset/F1").read().decode()
        assert f'src="/tex-asset/{a}/Figures/fig1.png"' in html
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + f"tex-view/{a}/ev/E9")
    finally:
        httpd.shutdown()


def test_missing_lists_gaps_mismatches_missing_files_and_unmarked_numbers(ev):
    (ev.root / "sections" / "results.tex").write_text(
        RESULTS + "The cohort had 88 cases. Lupus is common in women.\nIt affects 72 per 100,000\\ev{E3}.\n",
        encoding="utf-8")
    write_tsv(ev.folder / "evidence.tsv", [
        {"id": "E1", "target": "r1", "claim": "nine regions"},  # its PDF is not in the folder
        {"id": "E2", "target": "MetaData/replication.tsv", "note": "MISMATCH: text says 7, source says 6."},
        {"id": "E3", "target": "T2"},
    ], ["id", "target", "page", "where", "quote", "claim", "note"])
    write_tsv(ev.folder / "evidence_gaps.tsv", [
        {"file": "sections/results.tex", "anchor": "Lupus is common in women", "kind": "unsourced",
         "claim": "SLE is common in women", "note": "needs a citation"},
    ], ["file", "anchor", "kind", "claim", "note"])
    miss = ev.build()["missing"]
    kinds = [(m["kind"], m.get("id"), m.get("line")) for m in miss]
    assert ("gap", None, 3) in kinds and ("mismatch", "E2", 2) in kinds and ("nofile", "E1", 2) in kinds
    unmarked = [m for m in miss if m["kind"] == "unmarked"]
    assert [m["claim"] for m in unmarked] == ["The cohort had 88 cases."]  # 72 per 100,000 is marked; Loc/rs names ignored


def test_table_with_one_marker_is_evidenced_as_a_whole(ev):
    (ev.root / "sections" / "results.tex").write_text(
        RESULTS + "\\begin{tabular}{lr}\nCohort\\ev{E3} & N \\\\\nA & 120 \\\\\nB & 88 \\\\\n\\end{tabular}\n\n"
        "\\begin{tabular}{lr}\nCohort & N \\\\\nC & 77 \\\\\n\\end{tabular}\n", encoding="utf-8")
    write_tsv(ev.folder / "evidence.tsv", [{"id": "E3", "target": "T2"}], ["id", "target", "page", "where", "quote", "claim", "note"])
    unmarked = [m["claim"] for m in ev.build()["missing"] if m["kind"] == "unmarked"]
    assert "A & 120 \\\\" not in unmarked and "B & 88 \\\\" not in unmarked  # covered by the header marker
    assert "C & 77 \\\\" in unmarked                                           # a table without any marker is still audited


def test_gap_with_a_cause_is_explained_and_listed_last(ev):
    write_tsv(ev.folder / "evidence_gaps.tsv", [
        {"file": "sections/results.tex", "anchor": "Seven of nine regions", "kind": "unsourced", "claim": "open one"},
        {"file": "sections/results.tex", "anchor": "were recovered", "kind": "unsourced", "claim": "known one",
         "cause": "records not kept (author)"},
    ], ["file", "anchor", "kind", "claim", "note", "cause"])
    miss = [m for m in ev.build()["missing"] if m["kind"] == "gap"]
    assert [(m["claim"], m["explained"]) for m in miss] == [("open one", False), ("known one", True)]
    assert miss[1]["cause"] == "records not kept (author)"


def test_pdf_dropped_in_folder_is_registered_by_doi(ev):
    pytest.importorskip("pymupdf")
    write_tsv(ev.folder / "CitedArticles" / "index.tsv", [
        {"key": "r1", "file": "", "doi": "10.1002/art.39504", "year": "2016"},
        {"key": "r2", "file": "", "doi": "10.1038/ng.3656", "year": "2016"}], ["key", "file", "doi", "year", "status"])
    _pdf(ev.folder / "Arthritis Rheumatology - 2016 - download.pdf", ["Some article. doi: 10.1002/art.39504"])
    _pdf(ev.folder / "unrelated.pdf", ["Nothing to see"])
    picked = {x["file"]: x["status"] for x in ev.build()["picked_up"]}
    assert picked["Arthritis Rheumatology - 2016 - download.pdf"] == "registered as r1: r01_Earlier2016.pdf"
    assert picked["unrelated.pdf"] == "no reference matched by DOI" and (ev.folder / "unrelated.pdf").exists()
    assert (ev.folder / "CitedArticles" / "r01_Earlier2016.pdf").exists()
    from sciweave.evidence import read_tsv
    assert {r["key"]: r["file"] for r in read_tsv(ev.folder / "CitedArticles" / "index.tsv")}["r1"] == "r01_Earlier2016.pdf"


def test_no_evidence_planning_page_is_not_audited(ev):
    (ev.root / "sections" / "claims.tex").write_text(
        "% sciweave: no-evidence  (planning page)\nWe find 30 loci and 4 novel ones.\n", encoding="utf-8")
    (ev.root / "sections" / "other.tex").write_text("We find 30 loci\\ev{E2}. There are 12 more.\n", encoding="utf-8")
    miss = ev.build()["missing"]
    files = {m["file"] for m in miss if m["kind"] == "unmarked"}
    assert "sections/claims.tex" not in files and "sections/other.tex" in files
