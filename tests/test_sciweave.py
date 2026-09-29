import json
from pathlib import Path

import pytest

from sciweave import ai, article, plan
from sciweave.cli import main
from sciweave.export import render_dashboard
from sciweave.project import Project, SciWeaveError
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
    with pytest.raises(SciWeaveError):
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
    with pytest.raises(SciWeaveError, match="cycle"):
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
    with pytest.raises(SciWeaveError, match="unsaved"):
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
    with pytest.raises(SciWeaveError):
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
    with pytest.raises(SciWeaveError):
        plan.apply(proj, bad)
    assert len(proj.history()) == before and proj.nodes == {}


# ---------------------------------------------------------------- updates / branches / monitor

def test_version_records_why_and_changes(chain, proj):
    proj.link(chain["pl"], chain["ss"], rel="produces", params={"maf": 0.05}, why="stricter MAF after QC review")
    e = proj.find_edge(chain["pl"], chain["ss"], "produces")
    assert e["changes"][-1]["params"] == {"maf": [0.01, 0.05]} and e["changes"][-1]["why"].startswith("stricter")
    proj.resolve(proj.nodes[chain["cov"]]["path"]).write_text("a\n9\n", encoding="utf-8")
    proj.save_version(chain["cov"], message="PC3 added", why="residual stratification")
    proj.resolve(proj.nodes[chain["ss"]]["path"]).write_text("p\n0.3000\n", encoding="utf-8")
    v = proj.save_version(chain["ss"], message="re-run", why="new covariates + MAF")
    kinds = {(c["kind"], c.get("key") or c.get("node")) for c in v["changes"]}
    assert ("param", "maf") in kinds and ("input", chain["cov"]) in kinds and ("content", None) in kinds
    assert v["why"] == "new covariates + MAF"
    assert proj.history()[-1]["why"] == "new covariates + MAF"


def test_branch_create_and_choose(chain, proj, tmp_path):
    alt = write(tmp_path / "alt" / "lead_p1e-6.tsv", "snp\nrs1\nrs7\n")
    with pytest.raises(SciWeaveError, match="reason"):
        proj.branch(chain["tab"], "Lead loci (P<1e-6)", path=str(alt))
    b = proj.branch(chain["tab"], "Lead loci (P<1e-6)", path=str(alt), mode="managed", params={"p": 1e-6},
                    why="suggestive loci for replication")
    assert b["branch"]["of"] == chain["tab"] and b["branch"]["status"] == "alternative"
    assert proj.nodes[chain["tab"]]["branch"]["status"] == "main"
    e = proj.find_edge(chain["ss"], b["id"], "derives")
    assert e["params"] == {"p": 1e-6}                        # overridden on the carrying link
    assert proj.find_edge(chain["tab"], b["id"], "variant")
    assert proj.status()[b["id"]]["state"] == "ok"          # variant links never make things stale
    assert set(proj.branch_family(b["id"])) == {chain["tab"], b["id"]}
    proj.set_branch_status(b["id"], "main", why="replication cohort is large enough")
    assert proj.nodes[b["id"]]["branch"]["status"] == "main"
    assert proj.nodes[chain["tab"]]["branch"]["status"] == "alternative"
    assert proj.nodes[b["id"]]["branch"]["decision"]["why"].startswith("replication")


def test_monitor_rounds(chain, proj):
    from sciweave import monitor
    r0 = monitor.run_round(proj)
    assert r0["first_round"] and r0["new"] == []            # baseline: no flood
    # a new result next to a tracked table, a near-duplicate name of it, and noise
    tdir = proj.resolve(proj.nodes[chain["tab"]]["path"]).parent
    write(tdir / "lead_v2.tsv", "snp\nrs2\n")
    write(tdir / "brand_new_plot.png", "png")
    write(proj.root / "logs" / "run.log", "x")
    proj.resolve(proj.nodes[chain["fig"]]["path"]).write_text("<svg changed/>", encoding="utf-8")
    r1 = monitor.run_round(proj)
    paths = {n["path"].split("/")[-1]: n for n in r1["new"]}
    assert "lead_v2.tsv" in paths and paths["lead_v2.tsv"]["version_of"] == chain["tab"]
    assert paths["brand_new_plot.png"]["type"] == "figure" and "run.log" not in paths
    assert [u["node"] for u in r1["updated"]] == [chain["fig"]]
    text = monitor.render(proj, r1)
    assert "NEW" in text and "UPDATED" in text
    r2 = monitor.run_round(proj)                              # already reported -> quiet
    assert r2["new"] == []
    st = monitor.load_state(proj)
    st["ignore"].append("*.tsv")
    monitor.save_state(proj, st)
    write(tdir / "another.tsv", "x")
    assert monitor.run_round(proj)["new"] == []
    assert monitor.minutes_since_last(proj) < 1


def test_plan_branches_and_why(chain, proj, tmp_path):
    alt = write(tmp_path / "b" / "alt.tsv", "snp\nrs3\n")
    pl = {"sciweave_plan": 1, "summary": "sensitivity",
          "save": [{"node": chain["tab"], "message": "noop"}],
          "branches": [{"key": "alt", "of": chain["tab"], "label": "Lead loci (alt)", "path": str(alt),
                        "params": {"p": 1e-5}}]}
    rep = plan.check(proj, pl)
    assert not rep.passed and "needs a \"why\"" in rep.render()
    pl["branches"][0]["why"] = "check robustness"
    rep = plan.check(proj, pl)
    assert rep.passed and "no \"why\"" in rep.render()
    ids = plan.apply(proj, pl)
    assert proj.nodes[ids["alt"]]["branch"]["why"] == "check robustness"


def test_cli_suggest_and_branch(tmp_path, capsys, monkeypatch):
    root = tmp_path / "c2"
    main(["init", str(root)])
    monkeypatch.chdir(root)
    f = write(root / "results" / "tables" / "t.tsv", "a\n1\n")
    main(["add", "table", "T", str(f)])
    assert main(["suggest"]) == 0
    write(root / "results" / "tables" / "t_new.tsv", "a\n2\n")
    assert main(["suggest"]) == 0
    assert main(["monitor", "--due", "60"]) == 0               # not due: silent
    g = write(root / "results" / "tables" / "t_alt.tsv", "a\n3\n")
    assert main(["branch", "new", "T1", "T alt", str(g), "--why", "alt method"]) == 0
    assert main(["branch", "main", "T2", "--why", "better"]) == 0
    assert main(["branch", "ls"]) == 0
    out = capsys.readouterr().out
    assert "baseline" in out and "t_new.tsv" in out and "T2" in out and "main" in out


# ---------------------------------------------------------------- steps

def test_steps_define_assign_summary(chain, proj):
    proj.define_step("gwas", "GWAS", order=1)
    proj.define_step("plots", "Figures", order=2)
    proj.set_step([chain["cov"], chain["pl"], chain["ss"], chain["qc"], chain["tab"]], "gwas")
    proj.set_step([chain["fig"]], "plots")
    s = proj.step_summary()
    assert set(s["gwas"]["members"]) == {chain[k] for k in ("cov", "pl", "ss", "qc", "tab")}
    assert s["plots"]["in"] == {"gwas": 1} and s["gwas"]["out"] == {"plots": 1}
    assert s["gwas"]["within"] == 4
    assert proj.nodes[chain["fig"]]["step"] == "plots"
    assert "## Steps" in open(proj.write_map(), encoding="utf-8").read()
    n = proj.add_node("note", "x", step="newstep")                 # unknown step is created on the fly
    assert proj.steps["newstep"]["order"] == 3 and n["step"] == "newstep"
    with pytest.raises(SciWeaveError):
        proj.define_step("two words")


def test_plan_steps(chain, proj, tmp_path):
    f = write(tmp_path / "s" / "fm.tsv", "a\n1\n")
    pl = {"sciweave_plan": 1, "summary": "fm",
          "steps": [{"key": "finemapping", "label": "Fine-mapping", "order": 2}],
          "nodes": [{"key": "fm", "type": "table", "label": "Credible sets", "path": str(f), "mode": "managed",
                     "step": "finemapping"},
                    {"key": "nostep", "type": "table", "label": "Other", "path": str(f), "mode": "ref"}],
          "edges": [{"from": chain["ss"], "to": "fm", "rel": "derives", "params": {"L": 10}},
                    {"from": chain["ss"], "to": "nostep", "rel": "derives", "params": {"x": 1}}]}
    rep = plan.check(proj, pl)
    assert rep.passed and 'no "step"' in rep.render()
    ids = plan.apply(proj, pl)
    assert proj.nodes[ids["fm"]]["step"] == "finemapping" and proj.steps["finemapping"]["label"] == "Fine-mapping"


def test_branch_inherits_step_and_monitor_suggests_step(chain, proj, tmp_path):
    from sciweave import monitor
    proj.set_step([chain["tab"]], "gwas")
    alt = write(tmp_path / "br" / "alt.tsv", "x\n")
    b = proj.branch(chain["tab"], "alt", path=str(alt), why="test")
    assert b["step"] == "gwas"
    monitor.run_round(proj)
    tdir = proj.resolve(proj.nodes[chain["tab"]]["path"]).parent
    write(tdir / "lead_v9.tsv", "snp\n")
    r = monitor.run_round(proj)
    it = next(x for x in r["new"] if x["path"].endswith("lead_v9.tsv"))
    assert it["step"] == "gwas"
