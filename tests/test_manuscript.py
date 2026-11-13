import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sciweave import manuscript as ms_mod
from sciweave.cli import main
from sciweave.manuscript import Manuscript, word_diff
from sciweave.project import Project, SciWeaveError


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIWEAVE_HOME", str(tmp_path / "sciweave-home"))


MAIN = "\\documentclass{article}\n\\begin{document}\n\\input{sections/intro}\n\\input{sections/results}\n\\end{document}\n"
RESULTS = ("\\section{Results}\n"
           "We found 30 loci, including four novel loci near LINC00502, GPT2, N4BP1 and ZNF423.\n"
           "\n"
           "The PRS reached AUC 0.765 in LAMR.\n"
           "\n"
           "Genomic inflation was modest.\n")


@pytest.fixture
def ms(tmp_path):
    p = Project.init(tmp_path / "proj", name="Test")
    folder = tmp_path / "paper"
    (folder / "latex" / "sections").mkdir(parents=True)
    (folder / "latex" / "main.tex").write_text(MAIN, encoding="utf-8")
    (folder / "latex" / "sections" / "results.tex").write_text(RESULTS, encoding="utf-8")
    (folder / "latex" / "sections" / "intro.tex").write_text("\\section{Introduction}\nLupus.\n", encoding="utf-8")
    art = ms_mod.attach(p, folder, "SLE paper", step="article")
    p.commit()
    return Manuscript(Project(p.root), art["id"])


def test_attach_registers_article_and_sections_in_main_order(ms):
    p = ms.p
    assert ms.art["mode"] == "ref" and ms.root.name == "latex"
    secs = [p.nodes[e["source"]]["meta"]["section"] for e in p.incoming(ms.id) if e["rel"] == "part_of"]
    assert secs == ["intro", "results"]  # main.tex order, not alphabetical
    with pytest.raises(SciWeaveError, match="already article"):
        ms_mod.attach(p, ms.folder, "again")
    files = {f["path"]: f for f in ms.files()}
    assert files["main.tex"]["main"] and files["sections/results.tex"]["node"]


def test_save_refuses_stale_base(ms):
    f = ms.read("sections/intro.tex")
    ms.save("sections/intro.tex", f["text"] + "More.\n", base_hash=f["hash"])
    with pytest.raises(SciWeaveError, match="changed on disk"):
        ms.save("sections/intro.tex", "lost edit", base_hash=f["hash"])
    assert len(ms.snapshots("sections/intro.tex")) == 1
    with pytest.raises(SciWeaveError, match="outside"):
        ms.read("../../proj/.sciweave/graph.json")
    with pytest.raises(SciWeaveError, match="editable"):
        ms.read("build/main.pdf")


def test_propose_edits_and_partial_accept_while_user_keeps_editing(ms):
    pr = ms.propose_edits("sections/results.tex", [
        {"find": "LINC00502", "replace": "PCGF5"},
        {"find": "AUC 0.765", "replace": "AUC 0.766 (95% CI 0.750--0.782)"},
    ], message="update to the latest analysis")
    assert pr["id"] == "P1" and len(pr["hunks"]) == 2
    assert ms.read("sections/results.tex")["text"] == RESULTS  # nothing written yet
    # the user edits elsewhere in the file meanwhile
    cur = ms.read("sections/results.tex")
    ms.save("sections/results.tex", cur["text"].replace("modest", "well controlled"), base_hash=cur["hash"])
    r = ms.resolve("P1", accept=[2])
    text = ms.read("sections/results.tex")["text"]
    assert "AUC 0.766 (95% CI" in text and "LINC00502" in text and "well controlled" in text
    assert r["proposal"]["status"] == "open"
    r = ms.resolve("P1", reject=[1])
    assert r["proposal"]["status"] == "resolved" and ms.proposals() == []
    assert "LINC00502" in ms.read("sections/results.tex")["text"]


def test_conflicting_hunk_is_never_forced(ms):
    ms.propose_edits("sections/results.tex", [{"find": "AUC 0.765", "replace": "AUC 0.766"}])
    cur = ms.read("sections/results.tex")
    ms.save("sections/results.tex", cur["text"].replace("The PRS reached AUC 0.765 in LAMR.", "The PRS was good."),
            base_hash=cur["hash"])
    r = ms.resolve("P1", accept="all")
    assert r["conflicts"] == [1] and r["proposal"]["hunks"][0]["state"] == "conflict"
    assert "The PRS was good." in ms.read("sections/results.tex")["text"]


def test_ambiguous_or_missing_find_is_refused(ms):
    with pytest.raises(SciWeaveError, match="not found"):
        ms.propose_edits("sections/results.tex", [{"find": "nonexistent", "replace": "x"}])
    with pytest.raises(SciWeaveError, match="occurs 2 times"):
        ms.propose_edits("sections/results.tex", [{"find": "loci", "replace": "regions"}])
    pr = ms.propose_edits("sections/results.tex", [{"find": "loci", "replace": "regions", "nth": 2}])
    ms.resolve(pr["id"], accept="all")
    assert "30 loci, including four novel regions" in ms.read("sections/results.tex")["text"]


def test_one_edit_is_one_change_even_across_lines(ms):
    lst = "\\textbf{Figure 1. A}\n\n\\textbf{Figure 2. B}\n\n\\textbf{Figure 3. C}\n"
    ms.save("sections/intro.tex", ms.read("sections/intro.tex")["text"] + lst)
    pr = ms.propose_edits("sections/intro.tex", [{"find": lst, "replace": "\\subsection*{Figures}\n\n\\textbf{Figure 1. New}\n"}])
    assert len(pr["hunks"]) == 1
    assert [w[0] for w in pr["hunks"][0]["words"]].count("-") >= 1
    ms.resolve(pr["id"], accept="all")
    assert ms.read("sections/intro.tex")["text"].endswith("\\textbf{Figure 1. New}\n")


def test_whole_file_proposal_and_new_file(ms):
    pr = ms.propose("sections/discussion.tex", "\\section{Discussion}\nNew.\n", message="draft")
    assert pr["new_file"]
    ms.resolve(pr["id"], accept="all")
    assert ms.read("sections/discussion.tex")["text"].startswith("\\section{Discussion}")


def test_word_diff_groups_a_rewritten_phrase():
    w = word_diff("the AUC was 0.765 in LAMR", "the AUC was 0.766 in LAMR")
    assert ["-", "0.765"] in w and ["+", "0.766"] in w
    joined_old = "".join(t for op, t in w if op != "+")
    joined_new = "".join(t for op, t in w if op != "-")
    assert joined_old == "the AUC was 0.765 in LAMR" and joined_new == "the AUC was 0.766 in LAMR"


def test_cli_propose_diff_accept(ms, capsys):
    root = str(ms.p.root)
    assert main(["-C", root, "--actor", "claude", "tex", "propose", ms.id, "sections/intro.tex",
                 "--replace", "Lupus.", "Systemic lupus erythematosus.", "-m", "expand"]) == 0
    assert main(["-C", root, "tex", "diff", ms.id, "P1"]) == 0
    out = capsys.readouterr().out
    assert "[-Lupus." in out and "{+Systemic lupus" in out
    assert main(["-C", root, "tex", "accept", ms.id, "P1"]) == 0
    assert "Systemic lupus" in ms.read("sections/intro.tex")["text"]


def test_server_endpoints(ms):
    from sciweave import server
    server.Handler.project_root = ms.p.root
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    try:
        html = urllib.request.urlopen(base + f"latex.html?article={ms.id}").read().decode()
        assert "LaTeX" in html and "__JS__" not in html
        summ = json.loads(urllib.request.urlopen(base + f"api/tex/summary?article={ms.id}").read())
        assert any(f["path"] == "sections/results.tex" for f in summ["files"])

        def post(path, body):
            req = urllib.request.Request(base + path, data=json.dumps({**body, "article": ms.id}).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req).read())
        r = post("api/tex/propose", {"path": "sections/intro.tex", "edits": [{"find": "Lupus.", "replace": "SLE."}]})
        r = post("api/tex/resolve", {"id": r["proposal"]["id"], "accept": "all"})
        assert r["proposal"]["status"] == "accepted"
        f = json.loads(urllib.request.urlopen(base + f"api/tex/file?article={ms.id}&path=sections/intro.tex").read())
        assert "SLE." in f["text"]
    finally:
        httpd.shutdown()


def test_notes_tabs_read_save_and_refuse_stale_base(ms):
    keys = [n["key"] for n in ms.summary()["notes"]]
    assert keys == ["claims", "structure", "suggestions", "ideas", "removed", "words"]
    r = ms.note_read("ideas")
    assert not r["exists"] and r["text"] == ""
    h = ms.note_save("ideas", "# Ideas\n- one\n", base_hash=r["hash"])["hash"]
    assert (ms.folder / "notes" / "ideas.md").read_text(encoding="utf-8") == "# Ideas\n- one\n"
    assert ms.stamp()["notes"]["ideas"] == h
    ms.note_save("ideas", "# Ideas\n- two\n", base_hash=h)
    assert len(list((ms.state / "snapshots" / "notes__ideas.md").iterdir())) == 1
    with pytest.raises(SciWeaveError, match="changed on disk"):
        ms.note_save("ideas", "mine", base_hash=h)
    with pytest.raises(SciWeaveError, match="unknown note"):
        ms.note_read("../latex/main")
    assert all(f["path"].startswith(("main", "sections/")) for f in ms.files())  # notes are not manuscript files


def test_two_proposals_on_the_same_line_both_apply(ms):
    a = ms.propose_edits("sections/results.tex", [{"find": "30 loci", "replace": "31 loci"}], message="a")
    b = ms.propose_edits("sections/results.tex", [{"find": "near LINC00502", "replace": "near PCGF5"}], message="b")
    assert ms.resolve(a["id"], accept="all")["proposal"]["status"] == "accepted"
    r = ms.resolve(b["id"], accept="all")  # its line changed under it: re-applied by its own find/replace
    assert r["proposal"]["status"] == "accepted" and not r["conflicts"]
    text = ms.read("sections/results.tex")["text"]
    assert "We found 31 loci, including four novel loci near PCGF5, GPT2" in text
    c = ms.propose_edits("sections/results.tex", [{"find": "PCGF5, GPT2", "replace": "X, GPT2"}], message="c")
    ms.save("sections/results.tex", text.replace("PCGF5, GPT2", "PCGF5 and GPT2"), base_hash=ms.read("sections/results.tex")["hash"])
    assert ms.resolve(c["id"], accept="all")["conflicts"] == [1]  # its find is gone: still a conflict, never forced


def test_pdf_window_page(ms):
    from sciweave.export import render_latex_pdf
    html = render_latex_pdf(ms.p, ms.id)
    assert "api/tex/stamp" in html and "__CSS__" not in html and "__ARTICLE__" not in html
    assert f'window.SW_ARTICLE = "{ms.id}"' in html


def test_perl_dir_falls_back_to_git_for_windows(tmp_path, monkeypatch):
    # MiKTeX's latexmk needs perl; when it is not on PATH, Git for Windows' copy is used
    (tmp_path / "Git" / "cmd").mkdir(parents=True)
    (tmp_path / "Git" / "usr" / "bin").mkdir(parents=True)
    (tmp_path / "Git" / "usr" / "bin" / "perl.exe").write_text("")
    git = tmp_path / "Git" / "cmd" / "git.exe"
    git.write_text("")
    monkeypatch.setattr(ms_mod.shutil, "which", lambda name: str(git) if name == "git" else None)
    assert ms_mod._perl_dir() == str((tmp_path / "Git" / "usr" / "bin").resolve())
    monkeypatch.setattr(ms_mod.shutil, "which", lambda name: "/usr/bin/perl" if name == "perl" else None)
    assert ms_mod._perl_dir() is None
