import json
from pathlib import Path

import pytest

from sciweave import ai, article, plan
from sciweave.cli import main
from sciweave.export import render_dashboard
from sciweave.project import Project, SciweaveError
from sciweave.server import preview


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def proj(tmp_path):
    return Project.init(tmp_path / "proj", name="Test")


@pytest.fixture
def chain(proj, tmp_path):
    """cov -> PL1 -> sumstats -> table -> figure, with a second pipeline output."""
    ext = tmp_path / "ext"
    cov = proj.add_node("input", "Covariates", path=str(write(ext / "cov.tsv", "a\n1\n")), mode="managed")
    pl = proj.add_node("pipeline", "GWAS", path=str(write(ext / "pipe" / "Snakefile", "rule all")), mode="ref")
    ss = proj.add_node("result", "Sumstats", path=str(write(ext / "pipe" / "ss.tsv", "p\n0.1\n")), mode="ref")
    qc = proj.add_node("result", "QC", path=str(write(ext / "pipe" / "qc.txt", "ok")), mode="ref")
    tab = proj.add_node("table", "Lead loci", path=str(write(ext / "lead.tsv", "snp\nrs1\n")), mode="managed")
    fig = proj.add_node("figure", "Manhattan", path=str(write(ext / "man.svg", "<svg/>")), mode="managed")
    proj.link(cov["id"], pl["id"], rel="feeds")
    proj.link(pl["id"], ss["id"], rel="produces", params={"maf": 0.01})
    proj.link(pl["id"], qc["id"], rel="produces")
    proj.link(ss["id"], tab["id"], rel="derives", params={"p": 5e-8})
    proj.link(tab["id"], fig["id"], rel="derives")
    proj.commit()
    return {k: v["id"] for k, v in dict(cov=cov, pl=pl, ss=ss, qc=qc, tab=tab, fig=fig).items()}


def test_init_layout(proj):
    assert (proj.root / ".sciweave" / "graph.json").exists()
    assert (proj.root / "results" / "figures").is_dir()
    assert (proj.root / "SCIWEAVE.md").exists()
    with pytest.raises(SciweaveError):
        Project.init(proj.root)


def test_find_walks_up(proj):
    sub = proj.root / "results" / "tables"
    assert Project.find(sub).root == proj.root


def test_managed_copy_and_ids(proj, tmp_path):
    src = write(tmp_path / "x" / "cs.tsv", "a\tb\n1\t2\n")
    n = proj.add_node("table", "Credible sets", path=str(src), mode="managed")
    assert n["id"] == "T1"
    assert n["path"] == "results/tables/cs.tsv"
    assert (proj.root / n["path"]).read_text() == "a\tb\n1\t2\n"
    assert n["versions"][0]["stored"]
    assert proj.add_node("table", "Other")["id"] == "T2"


def test_ref_is_not_copied(proj, tmp_path):
    src = write(tmp_path / "big" / "raw.vcf", "x")
    n = proj.add_node("raw", "Raw", path=str(src), mode="ref")
    assert Path(n["path"]) == src.resolve()
    assert n["versions"][0]["stored"] is None


def test_cycle_rejected(chain, proj):
    with pytest.raises(SciweaveError, match="cycle"):
        proj.link(chain["fig"], chain["cov"])


def test_fresh_chain_is_ok(chain, proj):
    st = proj.status()
    assert all(s["state"] == "ok" for s in st.values()), st


def test_staleness_through_pipeline(chain, proj):
    cov_path = proj.resolve(proj.nodes[chain["cov"]]["path"])
    cov_path.write_text("a\n2\n", encoding="utf-8")
    assert proj.status()[chain["cov"]]["state"] == "modified"
    proj.save_version(chain["cov"], message="add PC3")
    st = proj.status()
    for k in ("pl", "ss", "qc", "tab", "fig"):
        assert st[chain[k]]["stale"], k
    # re-run: only sumstats regenerated -> qc (other output of the same run) stays stale
    proj.resolve(proj.nodes[chain["ss"]]["path"]).write_text("p\n0.2\n", encoding="utf-8")
    proj.save_version(chain["ss"])
    st = proj.status()
    assert not st[chain["ss"]]["stale"]
    assert st[chain["qc"]]["stale"]
    assert st[chain["tab"]]["stale"]  # built from ss v1
    proj.resolve(proj.nodes[chain["tab"]]["path"]).write_text("snp\nrs2\n", encoding="utf-8")
    proj.save_version(chain["tab"])
    assert not proj.status()[chain["tab"]]["stale"]
    assert proj.status()[chain["fig"]]["stale"]


def test_cosmetic_save_keeps_stale(chain, proj):
    proj.resolve(proj.nodes[chain["tab"]]["path"]).write_text("snp\nrs9\n", encoding="utf-8")
    proj.save_version(chain["tab"])
    assert proj.status()[chain["fig"]]["stale"]
    proj.resolve(proj.nodes[chain["fig"]]["path"]).write_text("<svg font='big'/>", encoding="utf-8")
    proj.save_version(chain["fig"], refresh_edges=False)
    assert proj.status()[chain["fig"]]["stale"]
    proj.resolve(proj.nodes[chain["fig"]]["path"]).write_text("<svg regenerated/>", encoding="utf-8")
    proj.save_version(chain["fig"])
    assert not proj.status()[chain["fig"]]["stale"]


def test_unchanged_save_is_noop(chain, proj):
    assert proj.save_version(chain["tab"]) is None


def test_final_and_restore(chain, proj):
    fid = chain["fig"]
    path = proj.resolve(proj.nodes[fid]["path"])
    path.write_text("<svg v2/>", encoding="utf-8")
    proj.save_version(fid)
    proj.mark_final(fid, 1)
    assert proj.nodes[fid]["final_version"] == 1
    path.write_text("<svg unsaved/>", encoding="utf-8")
    with pytest.raises(SciweaveError, match="unsaved"):
        proj.restore(fid, 1)
    out = proj.restore(fid, 1, out=str(proj.root / "old.svg"))
    assert out.read_text() == "<svg/>"
    proj.restore(fid, 1, force=True)
    assert path.read_text() == "<svg/>"
    assert proj.nodes[fid]["current_version"] == 3


def test_history_log(chain, proj):
    events = [h["event"] for h in proj.history()]
    assert events[0] == "project_created"
    assert "edge_added" in events and "node_added" in events
    assert all(h.get("node") == chain["tab"] or chain["tab"] in h.get("nodes", []) for h in proj.history(chain["tab"]))


def test_article_place_and_sync(chain, proj):
    a = article.new_article(proj, "My Paper")
    folder = proj.root / a["meta"]["folder"]
    assert (folder / "latex" / "main.tex").exists()
    assert (folder / "latex" / "sections" / "methods.tex").exists()
    sections = [n for n in proj.nodes.values() if n["type"] == "section"]
    assert len(sections) == 5
    e = article.place(proj, chain["fig"], a["id"], "Figure 1")
    assert (proj.root / e["placed_path"]).exists()
    assert not proj.edge_is_stale(e)
    proj.resolve(proj.nodes[chain["fig"]]["path"]).write_text("<svg new/>", encoding="utf-8")
    proj.save_version(chain["fig"])
    assert proj.edge_is_stale(e)
    assert proj.status()[a["id"]]["stale"]
    done = article.sync(proj, a["id"])
    assert done and (proj.root / e["placed_path"]).read_text() == "<svg new/>"
    assert not proj.edge_is_stale(proj.find_edge(chain["fig"], a["id"], "part_of"))


def _plan(tmp_path, **over):
    src = write(tmp_path / "work" / "cs.tsv", "a\n1\n")
    p = {
        "sciweave_plan": 1,
        "summary": "fine-mapping",
        "nodes": [{"key": "cs", "type": "table", "label": "Credible sets", "path": str(src), "mode": "managed"}],
        "edges": [{"from": "PL1", "to": "cs", "rel": "produces", "params": {"L": 10}}],
    }
    p.update(over)
    return p


def test_plan_check_and_apply(chain, proj, tmp_path):
    pl = _plan(tmp_path)
    rep = plan.check(proj, pl)
    assert rep.passed, rep.render()
    ids = plan.apply(proj, pl, actor="claude")
    new = proj.nodes[ids["cs"]]
    assert new["type"] == "table" and new["path"].startswith("results/tables/")
    assert proj.find_edge("PL1", ids["cs"])["params"] == {"L": 10}
    assert list(proj.plans_dir.glob("*.json"))
    assert proj.history()[-1]["event"] == "plan_applied"
    assert proj.status()[ids["cs"]]["state"] == "ok"


def test_plan_errors_block_apply(proj, tmp_path):
    bad = _plan(tmp_path, edges=[{"from": "cs", "to": "NOPE", "rel": "weird"}])
    bad["nodes"].append({"key": "x", "type": "chart", "label": "", "path": "/does/not/exist"})
    rep = plan.check(proj, bad)
    text = rep.render()
    assert not rep.passed
    for needle in ("unknown type", "needs a label", "does not exist", "NOPE", "unknown rel"):
        assert needle in text
    with pytest.raises(SciweaveError):
        plan.apply(proj, bad)
    assert proj.nodes == {}


def test_plan_warnings(proj, tmp_path):
    pl = _plan(tmp_path, edges=[])
    pl["nodes"].append({"key": "f", "type": "figure", "label": "Plot", "path": pl["nodes"][0]["path"]})
    pl["edges"] = [{"from": "cs", "to": "f", "rel": "derives", "params": {"font_size": 12}}]
    rep = plan.check(proj, pl)
    text = rep.render()
    assert rep.passed
    assert "looks cosmetic" in text
    assert "no incoming edge" in text  # cs has no provenance


def test_plan_cycle_detected(chain, proj, tmp_path):
    pl = _plan(tmp_path, edges=[{"from": chain["fig"], "to": chain["cov"], "rel": "derives"}])
    assert "cycle" in plan.check(proj, pl).render()


def test_local_ai(chain, proj):
    res = ai.local_answer(proj, "how was F1 made?")
    assert chain["fig"] in res["highlight"] and chain["tab"] in res["highlight"]
    assert "Nothing is stale" in ai.local_answer(proj, "what is stale")["answer"]
    res = ai.local_answer(proj, "manhattan")
    assert res["highlight"][0] == chain["fig"]
    assert chain["ss"] in ai.local_answer(proj, "which parameters maf")["highlight"]
    assert "EDGES" in ai.graph_context(proj)


def test_dashboard_html_is_self_contained(chain, proj):
    proj.add_note(chain["tab"], "tricky </script> text")
    html = render_dashboard(proj, live=False)
    assert "__DATA__" not in html and "__D3__" not in html
    assert "cdn" not in html.lower().split("<script>")[0]
    data_block = html.split("window.SCIWEAVE_DATA = ", 1)[1].split(";\n</script>", 1)[0]
    assert "</script>" not in data_block
    payload = json.loads(data_block.replace("<\\/", "</"))
    assert {n["id"] for n in payload["nodes"]} == set(chain.values())


def test_preview_table_and_text(chain, proj):
    p = preview(proj, chain["tab"])
    assert p["kind"] == "table" and p["header"] == ["snp"]
    assert preview(proj, chain["fig"])["kind"] == "image"


def test_cli_smoke(tmp_path, capsys, monkeypatch):
    root = tmp_path / "cli"
    assert main(["init", str(root), "--name", "CLI"]) == 0
    f = write(tmp_path / "t.tsv", "a\n1\n")
    monkeypatch.chdir(root)
    assert main(["add", "table", "My table", str(f)]) == 0
    assert main(["add", "pipeline", "Pipe"]) == 0
    assert main(["link", "PL1", "T1", "--rel", "produces", "-p", "L=10", "-p", "panel=EUR"]) == 0
    p = Project(root)
    assert p.find_edge("PL1", "T1")["params"] == {"L": 10, "panel": "EUR"}
    assert main(["trace", "T1"]) == 0
    assert main(["status"]) == 0
    assert main(["article", "new", "Paper"]) == 0
    assert main(["article", "place", "T1", "A1", "--as", "Table 1"]) == 0
    assert main(["show", "NOPE"]) == 1
    out = capsys.readouterr().out
    assert "L=10" in out and "Table 1" in out


def test_demo_builds(tmp_path):
    from sciweave.demo import build_demo
    p = build_demo(tmp_path / "demo")
    st = p.status()
    assert len(p.nodes) > 20
    assert st["F1"]["stale"] and not st["T1"]["stale"]
    assert p.nodes["F2"]["final_version"] == 2


def test_history_import(proj, tmp_path):
    old1 = write(tmp_path / "arch" / "fig_2026-09-04.png", "v-old-1")
    old2 = write(tmp_path / "arch" / "fig_2026-09-11.png", "v-old-2")
    cur = write(proj.root / "figs" / "fig.png", "v-current")
    n = proj.add_node("figure", "Fig", path=str(cur), mode="managed",
                      history=[{"path": str(old1), "ts": "2026-09-04T18:19:00+00:00", "message": "first"},
                               {"path": str(old2), "ts": "2026-09-11T17:53:00+00:00"}])
    assert [v["v"] for v in n["versions"]] == [1, 2, 3] and n["current_version"] == 3
    assert n["versions"][0]["ts"].startswith("2026-09-04")
    out = proj.restore(n["id"], 1, out=str(tmp_path / "back.png"))
    assert out.read_text() == "v-old-1"
    assert proj.status()[n["id"]]["state"] == "ok"


def test_plan_explicit_ids_win_over_auto_ids(proj, tmp_path):
    f = write(tmp_path / "w" / "st.xlsx", "x")
    pl = {"sciweave_plan": 1, "summary": "ids",
          "nodes": [{"key": "loo", "type": "step", "label": "LOO"},             # auto prefix ST
                    {"key": "st1", "id": "ST1", "type": "supplement", "label": "Supp Table 1", "path": str(f)}],
          "edges": [{"from": "loo", "to": "st1", "rel": "produces", "params": {"x": 1}}]}
    ids = plan.apply(proj, pl)
    assert ids["st1"] == "ST1" and ids["loo"] == "SP1"
    dup = {"sciweave_plan": 1, "summary": "d", "nodes": [{"key": "a", "id": "Q1", "type": "note", "label": "a"},
                                                        {"key": "b", "id": "Q1", "type": "note", "label": "b"}]}
    assert "more than one node" in plan.check(proj, dup).render()


def test_failed_apply_rolls_back_history(proj, tmp_path):
    before = len(proj.history())
    bad = {"sciweave_plan": 1, "summary": "x", "nodes": [{"key": "a", "type": "note", "label": "a"}],
           "final": [{"node": "a"}]}   # passes check, fails at apply (no versions)
    with pytest.raises(SciweaveError):
        plan.apply(proj, bad)
    assert len(proj.history()) == before and proj.nodes == {}
