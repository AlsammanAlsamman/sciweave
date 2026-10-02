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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/Documents/SciWeave registry from tests."""
    monkeypatch.setenv("SCIWEAVE_HOME", str(tmp_path / "sciweave-home"))


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


def test_registry_register_get_unregister(tmp_path):
    from sciweave import registry
    a = Project.init(tmp_path / "a", name="SLE Hispanic GWAS")
    b = Project.init(tmp_path / "b", name="SLE Hispanic GWAS")
    ea, eb = registry.register(a), registry.register(b)
    assert ea["id"] == "sle-hispanic-gwas" and eb["id"] == "sle-hispanic-gwas-2"
    assert registry.register(a)["id"] == ea["id"]  # idempotent
    assert registry.get(str(a.root))["id"] == ea["id"]
    with pytest.raises(SciWeaveError):
        registry.get("hispanic")  # ambiguous
    index = (registry.home_dir() / "PROJECTS.md").read_text(encoding="utf-8")
    assert str(a.root) in index and "sle-hispanic-gwas-2" in index
    registry.unregister("sle-hispanic-gwas-2")
    assert [e["id"] for e in registry.load()["projects"]] == ["sle-hispanic-gwas"]
    assert b.root.exists()  # removing from the list never touches the folder


def test_registry_summary(chain, proj):
    from sciweave import registry
    e = registry.register(proj)
    sm = registry.summarize(e)
    assert sm["nodes"] == 6 and sm["links"] == 5 and sm["missing"] is False
    assert sm["categories"]["outputs"] == 4 and sm["last_activity"]
    import shutil
    shutil.rmtree(proj.root)
    assert registry.summarize(e)["missing"] is True


def test_cli_init_registers_and_projects(tmp_path, capsys):
    from sciweave import registry
    assert main(["init", str(tmp_path / "study"), "--name", "Study"]) == 0
    assert main(["init", str(tmp_path / "quiet"), "--no-register"]) == 0
    assert [e["id"] for e in registry.load()["projects"]] == ["study"]
    assert main(["projects", "add", str(tmp_path / "quiet")]) == 0
    capsys.readouterr()
    assert main(["projects"]) == 0
    out = capsys.readouterr().out
    assert "study" in out and "quiet" in out
    assert main(["projects", "rm", "quiet"]) == 0


def test_home_server_routes_projects(chain, proj):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from sciweave import registry
    from sciweave.server import Handler
    e = registry.register(proj)
    Handler.project_root = None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    try:
        get = lambda u: urllib.request.urlopen(base + u).read().decode("utf-8")
        assert "Your projects" in get("")
        listing = json.loads(get("api/projects"))
        assert listing["projects"][0]["id"] == e["id"] and listing["projects"][0]["nodes"] == 6
        page = get(f"p/{e['id']}")  # redirected to the trailing-slash URL
        assert 'class="home-link"' in page
        assert len(json.loads(get(f"p/{e['id']}/api/graph"))["nodes"]) == 6
        req = urllib.request.Request(base + "api/projects/add", method="POST",
                                     data=json.dumps({"path": str(proj.root.parent / "new"), "create": True,
                                                      "name": "New one"}).encode())
        assert json.loads(urllib.request.urlopen(req).read())["id"] == "new-one"
        assert (proj.root.parent / "new" / ".sciweave" / "graph.json").exists()
    finally:
        httpd.shutdown()
        Handler.project_root = Path(".")


def test_analysis_tree_import_and_numbering(proj):
    guide = [
        {"key": "raw", "title": "Raw & input data", "start": "2026-08-01", "children": [
            {"key": "qc", "title": "QC", "history": [{"date": "2026-08-03", "text": "b"}, {"date": "2026-08-02", "text": "a"}]},
            {"key": "pca", "title": "PCA", "params": {"n_pcs": 3}},
        ]},
        {"key": "gwas", "title": "Per-cohort GWAS", "children": [{"key": "gwas.rerun", "title": "Rerun"}]},
    ]
    assert proj.import_analyses(guide) == 5
    proj.commit()
    tree = proj.analysis_tree()
    assert [(k, n) for k, n, _ in tree] == [("raw", "1"), ("qc", "1.1"), ("pca", "1.2"), ("gwas", "2"), ("gwas.rerun", "2.1")]
    assert [h["text"] for h in proj.analyses["qc"]["history"]] == ["a", "b"]  # sorted by date
    assert "Analysis history" in (proj.root / "SCIWEAVE.md").read_text(encoding="utf-8")
    assert Project(proj.root).analyses["pca"]["params"] == {"n_pcs": 3}  # persisted
    assert "analyses" in proj.payload()


def test_analysis_rules(proj):
    proj.set_analysis("a", title="A")
    proj.set_analysis("b", title="B", parent="a")
    with pytest.raises(SciWeaveError):
        proj.set_analysis("a", parent="b")  # loop
    with pytest.raises(SciWeaveError):
        proj.set_analysis("c", parent="missing")
    with pytest.raises(SciWeaveError):
        proj.set_analysis("c", status="finished")
    with pytest.raises(SciWeaveError):
        proj.set_analysis("c", colour="red")
    before = json.dumps(proj.analyses, sort_keys=True)
    with pytest.raises(SciWeaveError):
        proj.import_analyses([{"key": "x", "parent": "nowhere"}])
    assert json.dumps(proj.analyses, sort_keys=True) == before  # all or nothing
    proj.set_analysis("c", title="C", parent="b")
    proj.remove_analysis("b")
    assert proj.analyses["c"]["parent"] == "a"  # children move up


def test_cli_analysis(tmp_path, capsys, monkeypatch, chain, proj):
    monkeypatch.chdir(proj.root)
    assert main(["analysis", "add", "raw", "Raw data", "--summary", "genotypes", "--start", "2026-08-01"]) == 0
    assert main(["analysis", "add", "gwas", "GWAS", "-p", "n_pcs=3", "--status", "open"]) == 0
    assert main(["analysis", "add", "gwas.fix", "Fix", "--parent", "gwas"]) == 0
    assert main(["analysis", "log", "gwas", "reran", "with", "3", "PCs", "--date", "2026-09-01"]) == 0
    assert main(["analysis", "link", "gwas", chain["ss"]]) == 0
    assert main(["analysis", "done", "raw"]) == 0
    assert main(["analysis", "set", "nope", "--title", "x"]) == 1
    capsys.readouterr()
    assert main(["analysis"]) == 0
    out = capsys.readouterr().out
    assert "2.1" in out and "1 organized" in out
    assert main(["analysis", "show", "gwas"]) == 0
    out = capsys.readouterr().out
    assert "reran with 3 PCs" in out and "n_pcs=3" in out and "Fix" in out
    g = tmp_path / "guide.json"
    g.write_text(json.dumps({"analyses": [{"key": "only", "title": "Only one"}]}), encoding="utf-8")
    assert main(["analysis", "import", str(g), "--replace"]) == 0
    assert list(Project(proj.root).analyses) == ["only"]


def test_import_flat_list_orders_among_siblings(proj):
    flat = [{"key": "a", "title": "A"}, {"key": "a.1", "parent": "a"}, {"key": "b", "title": "B"},
            {"key": "a.2", "parent": "a"}]
    proj.import_analyses(flat)
    assert proj.analyses["b"]["order"] == 2 and proj.analyses["a.2"]["order"] == 2
    assert [n for _, n, _ in proj.analysis_tree()] == ["1", "1.1", "1.2", "2"]


def test_analysis_refs_expand_to_current_numbers(proj):
    proj.import_analyses([{"key": "a", "title": "Alpha"}, {"key": "b", "title": "Beta", "summary": "redone in @a; see @nope"}])
    assert proj.expand_refs(proj.analyses["b"]["summary"]) == "redone in 1 “Alpha”; see @nope"
    proj.set_analysis("a", order=5)  # reorder: the reference follows
    assert "2 “Alpha”" in proj.expand_refs("@a")


def test_focus_hiding_keeps_what_comes_next(chain, proj, tmp_path):
    # cov -> PL1 -> sumstats -> table -> figure; PL1 also -> QC.  Hide the step that made the sumstats.
    sc = proj.add_node("script", "run_gwas.py", path=str(write(tmp_path / "ext" / "run.py", "x")), mode="ref")
    proj.link(sc["id"], chain["pl"], rel="feeds")
    proj.import_analyses([{"key": "prep", "title": "Prep", "nodes": [chain["pl"]], "children": [{"key": "prep.sub"}]},
                          {"key": "gwas", "title": "GWAS", "nodes": [chain["ss"]]}])
    assert proj.hidden_nodes() == {}
    proj.hide_analyses(["prep"])
    hid = proj.hidden_nodes()
    assert proj.hidden_analyses() == {"prep", "prep.sub"}  # branches go with it
    assert chain["pl"] in hid and hid[chain["pl"]].startswith("part of hidden analysis")
    assert chain["cov"] in hid and sc["id"] in hid           # only used by the hidden pipeline
    assert chain["qc"] not in hid                            # an output with no consumers is not swept up
    assert chain["ss"] not in hid and chain["tab"] not in hid  # the next analysis stays
    proj.hide_nodes([chain["cov"]], hidden=False)             # pin one back
    assert chain["cov"] not in proj.hidden_nodes()
    proj.hide_analyses(["prep"], hidden=False)
    assert proj.hidden_nodes() == {} and len(proj.nodes) == 7  # nothing was deleted
    assert "hidden" in proj.payload()


def test_cli_hide(tmp_path, capsys, monkeypatch, chain, proj):
    monkeypatch.chdir(proj.root)
    assert main(["analysis", "add", "prep", "Prep"]) == 0
    assert main(["analysis", "add", "gwas", "GWAS"]) == 0
    assert main(["analysis", "hide", "prep"]) == 0
    capsys.readouterr()
    assert main(["analysis"]) == 0
    out = capsys.readouterr().out
    assert "Prep" not in out and "1 hidden" in out
    assert main(["analysis", "ls", "--all"]) == 0
    assert "[hidden]" in capsys.readouterr().out
    assert main(["hide", chain["fig"]]) == 0
    assert chain["fig"] in Project(proj.root).hidden_nodes()
    assert main(["unhide", chain["fig"]]) == 0
    assert main(["status"]) == 0


def test_analysis_rename_moves_branches_and_refs(proj):
    proj.import_analyses([{"key": "input", "children": [{"key": "input.qc", "summary": "see @input.qc.x",
                                                         "children": [{"key": "input.qc.x"}]}]},
                          {"key": "gwas", "summary": "after @input.qc", "history": [{"date": "2026-01-01", "text": "@input.qc done"}]}])
    m = proj.rename_analysis("input.qc", "prep.qc")
    assert m == {"input.qc": "prep.qc", "input.qc.x": "prep.qc.x"}
    assert proj.analyses["prep.qc.x"]["parent"] == "prep.qc" and proj.analyses["prep.qc"]["parent"] == "input"
    assert proj.analyses["gwas"]["summary"] == "after @prep.qc"
    assert proj.analyses["gwas"]["history"][0]["text"] == "@prep.qc done"
    assert proj.analyses["prep.qc"]["summary"] == "see @prep.qc.x"
    with pytest.raises(SciWeaveError):
        proj.rename_analysis("gwas", "prep.qc")


def test_order_zero_is_numbered_zero(proj):
    proj.import_analyses([{"key": "before", "order": 0, "children": [{"key": "before.x"}]}, {"key": "input", "order": 1}, {"key": "gwas", "order": 2}])
    assert [(k, n) for k, n, _ in proj.analysis_tree()] == [("before", "0"), ("before.x", "0.1"), ("input", "1"), ("gwas", "2")]


def test_analysis_group_label_keeps_siblings_independent(tmp_path, capsys, monkeypatch, proj):
    monkeypatch.chdir(proj.root)
    assert main(["analysis", "add", "input", "Input"]) == 0
    for k in ("lamr", "mex"):
        assert main(["analysis", "add", f"input.{k}", k.upper(), "--parent", "input", "--group", "Hispanic cohorts"]) == 0
    p = Project(proj.root)
    assert [n for _, n, _ in p.analysis_tree()] == ["1", "1.1", "1.2"]  # siblings, not nested
    assert p.analyses["input.mex"]["group"] == "Hispanic cohorts"
    capsys.readouterr()
    assert main(["analysis", "show", "input.lamr"]) == 0
    assert "Hispanic cohorts" in capsys.readouterr().out


def test_relocate_rewrites_paths_under_a_moved_folder(proj, tmp_path):
    old = tmp_path / "old_place"
    a = proj.add_node("raw", "Data", path=str(write(old / "sub" / "x.tsv", "a\n")), mode="ref")
    b = proj.add_node("raw", "Other", path=str(write(tmp_path / "old_place_2" / "y.tsv", "b\n")), mode="ref")
    proj.set_analysis("an", paths=[str(old / "sub"), "relative/path"])
    new = tmp_path / "new_place"
    dry = proj.relocate(str(old).replace("/", "\\"), str(new), dry_run=True)
    assert len(dry) == 2 and proj.nodes[a["id"]]["path"].startswith(str(old))  # dry run writes nothing
    proj.relocate(str(old), str(new))
    assert proj.nodes[a["id"]]["path"].replace("\\", "/").endswith("new_place/sub/x.tsv")
    assert "old_place_2" in proj.nodes[b["id"]]["path"]  # a sibling with the same prefix text is not touched
    assert proj.analyses["an"]["paths"][1] == "relative/path"


def test_organized_copies_follow_the_network(chain, proj, tmp_path):
    dest = tmp_path / "organized"
    with pytest.raises(SciWeaveError):
        proj.organize(chain["tab"])  # no destination yet
    proj.set_destination(str(dest))
    proj.define_step("gwas", "Per-cohort GWAS", order=3)
    proj.set_step([chain["tab"], chain["pl"]], "gwas")
    rec = proj.organize(chain["tab"])
    proj.commit()
    assert rec["path"] == "03_Per-cohort_GWAS/lead.tsv" and (dest / rec["path"]).read_text() == "snp\nrs1\n"
    proj.organize(chain["pl"])  # a folder-like pipeline file
    assert proj.organized_state(chain["tab"]) == "ok"
    # the network changes: a custom group -> the copy moves into a sub-folder, old folder cleaned up
    proj.update_node(chain["tab"], groups=["METAL inputs"])
    proj.commit()
    moved = proj.nodes[chain["tab"]]["organized"]["path"]
    assert moved == "03_Per-cohort_GWAS/METAL_inputs/lead.tsv" and (dest / moved).exists()
    assert not (dest / "03_Per-cohort_GWAS/lead.tsv").exists()
    # the source changes -> reported, and copying again refreshes it
    write(Path(proj.resolve(proj.nodes[chain["tab"]]["path"])), "snp\nrs1\nrs2\n")
    assert proj.organized_state(chain["tab"]) == "source_changed"
    proj.organize(chain["tab"])
    assert proj.organized_state(chain["tab"]) == "ok" and (dest / moved).read_text().endswith("rs2\n")
    assert chain["tab"] in proj.payload()["organized"]


def test_cli_destination_and_organize(tmp_path, capsys, monkeypatch, chain, proj):
    monkeypatch.chdir(proj.root)
    assert main(["destination", str(tmp_path / "dst")]) == 0
    assert main(["organize", chain["fig"]]) == 0
    assert main(["organize", "--all"]) == 0
    assert main(["organize"]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "organized copies" in capsys.readouterr().out


def test_dedup_one_file_one_node(proj, tmp_path, capsys, monkeypatch):
    from sciweave import dedup
    blob = ("ACGT" * 3000) + "\n"  # > MIN_BYTES
    a = write(tmp_path / "fizi" / "refpanel" / "g1000_eur.bed", blob)
    b = write(tmp_path / "lynx" / "main1000.bed", blob)  # same bytes, other name and folder
    write(tmp_path / "other" / "x.bed", blob[:-2] + "Z\n")  # same size, different content
    na = proj.add_node("input", "1000G EUR (FIZI)", path=str(a.parent), mode="ref")
    nb = proj.add_node("input", "1000G panel (LYNXgwas)", path=str(b), mode="ref")
    nc = proj.add_node("input", "Other panel", path=str(tmp_path / "other" / "x.bed"), mode="ref")
    t = proj.add_node("result", "Clumped loci", path=str(write(tmp_path / "loci.tsv", "l\n")), mode="ref")
    proj.link(nb["id"], t["id"], rel="feeds")
    proj.set_analysis("lynx", nodes=[nb["id"]])
    r = dedup.scan(proj)
    pairs = {tuple(sorted((x["a"], x["b"]))) for x in r["whole"]}
    assert tuple(sorted((na["id"], nb["id"]))) in pairs                     # bit-identical, different names
    assert all(nc["id"] not in pr for pr in pairs)                         # same size, other bytes: not a duplicate
    assert dedup.check_new(proj, b)["same_as"] == sorted([na["id"], nb["id"]]) or nb["id"] in dedup.check_new(proj, b)["same_as"]
    k = dedup.merge(proj, na["id"], nb["id"], label="1000 Genomes EUR (FIZI, LYNXgwas)", used_by=["FIZI", "LYNXgwas"])
    proj.commit()
    assert nb["id"] not in proj.nodes and k["label"] == "1000 Genomes EUR (FIZI, LYNXgwas)"
    assert proj.edges[0]["source"] == na["id"]                             # the link moved to the kept node
    assert proj.analyses["lynx"]["nodes"] == [na["id"]]
    assert str(b) in k["meta"]["also_at"] and k["meta"]["used_by"] == ["FIZI", "LYNXgwas"]
    # adding the same bytes again is refused (CLI) and flagged (plan check)
    monkeypatch.chdir(proj.root)
    assert main(["add", "input", "again", str(b)]) == 1
    pl = {"sciweave_plan": 1, "nodes": [{"key": "dup", "type": "input", "label": "dup", "path": str(b), "mode": "ref", "step": None}]}
    rep = plan.check(proj, pl)
    assert any("identical content" in m for m in rep.errors)


def test_large_data_is_held_back_and_a_no_is_remembered(tmp_path, capsys, monkeypatch, chain, proj):
    monkeypatch.setattr(Project, "BIG_BYTES", -1)  # every file in the fixture counts as "large"
    monkeypatch.chdir(proj.root)
    assert main(["destination", str(tmp_path / "dst")]) == 0
    capsys.readouterr()
    assert main(["organize", "--all"]) == 0
    out = capsys.readouterr().out
    assert "0 object(s) copied" in out and "large data" in out          # large data is never copied by --all
    p = Project(proj.root)
    p.set_organize_skip(chain["tab"]); p.commit()
    assert Project(proj.root).nodes[chain["tab"]]["organize_skip"] is True
    assert main(["organize", "--all", "--include-large"]) == 0         # copies the large ones, not the skipped one
    p = Project(proj.root)
    assert not p.nodes[chain["tab"]].get("organized") and p.nodes[chain["fig"]].get("organized")
    assert main(["organize", chain["tab"]]) == 0                        # saving it on its own clears the skip
    assert "organize_skip" not in Project(proj.root).nodes[chain["tab"]]


def test_verified_copies_checksum_files_and_quick_check(chain, proj, tmp_path, capsys, monkeypatch):
    import hashlib as _h
    dest = tmp_path / "organized"
    proj.set_destination(str(dest))
    proj.define_step("gwas", "Per-cohort GWAS", order=3)
    proj.set_step([chain["tab"], chain["pl"]], "gwas")
    rec = proj.organize(chain["tab"]); proj.commit()
    side = dest / rec["sidecar"]
    digest, _, name = side.read_text(encoding="utf-8").strip().partition("  ")
    assert name == "lead.tsv" and digest == _h.sha256((dest / rec["path"]).read_bytes()).hexdigest()
    chk = proj.organized_check(chain["tab"])
    assert chk["up_to_date"] and chk["has_checksum"]
    assert proj.verify_copy(chain["tab"])["ok"]
    # a folder copy: one checksum file listing every file
    folder = tmp_path / "ext" / "pipe"
    d = proj.add_node("result", "Pipeline folder", path=str(folder), mode="ref", step="gwas")
    r2 = proj.organize(d["id"]); proj.commit()
    lines = (dest / r2["sidecar"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == r2["files"] >= 3 and all("  " + r2["name"] + "/" in l for l in lines)
    # the checksum file moves with its copy when the network changes
    proj.update_node(chain["tab"], groups=["METAL inputs"]); proj.commit()
    rec = proj.nodes[chain["tab"]]["organized"]
    assert (dest / rec["sidecar"]).exists() and rec["sidecar"].startswith("03_Per-cohort_GWAS/METAL_inputs/")
    # quick check sees a changed source (size differs)
    write(Path(proj.resolve(proj.nodes[chain["tab"]]["path"])), "snp\nrs1\nrs2\nrs3\n")
    chk = proj.organized_check(chain["tab"])
    assert not chk["up_to_date"] and not chk["same_size"]
    # a copy damaged at the destination is caught by verify
    proj.organize(chain["tab"]); proj.commit()
    (dest / proj.nodes[chain["tab"]]["organized"]["path"]).write_text("tampered\n", encoding="utf-8")
    res = proj.verify_copy(chain["tab"])
    assert not res["ok"] and res["bad"] == ["lead.tsv"]
    # CLI: an up-to-date copy is not copied again without --force
    proj.organize(chain["tab"]); proj.commit()
    monkeypatch.chdir(proj.root); capsys.readouterr()
    assert main(["organize", chain["tab"]]) == 0
    assert "already up to date" in capsys.readouterr().out
    assert main(["organize", "--verify", chain["tab"]]) == 0
    assert "OK" in capsys.readouterr().out


def test_jobs_interrupted_lock_and_clean(chain, proj, tmp_path, capsys, monkeypatch):
    import time as _t
    from sciweave import jobs
    proj.set_destination(str(tmp_path / "dst")); proj.commit()
    d = jobs.jobs_dir(proj)
    # a job whose process stopped beating is reported as interrupted
    jobs._write(d / "j1.json", {"id": "j1", "node": chain["tab"], "kind": "copy", "state": "copying",
                                "heartbeat": _t.time() - 3600, "created": "x"})
    assert [j["state"] for j in jobs.list_jobs(proj)] == ["interrupted"]
    # the run() body copies, checks and records (here in-process)
    jobs._write(d / "j2.json", {"id": "j2", "node": chain["fig"], "kind": "copy", "state": "queued",
                                "heartbeat": _t.time(), "created": "y", "bytes_done": 0, "bytes_total": 0})
    assert jobs.run(proj.root, "j2") == 0
    j2 = jobs._read(d / "j2.json")
    assert j2["state"] == "done" and j2["bytes_done"] == j2["bytes_total"] > 0
    assert Project(proj.root).nodes[chain["fig"]].get("organized")
    # the lock is exclusive and released
    with jobs.graph_lock(proj.state):
        assert (proj.state / "graph.lock").exists()
    assert not (proj.state / "graph.lock").exists()
    # leftovers of an interrupted copy are removed by --clean
    (tmp_path / "dst" / "x.bin.sciweave-part").write_bytes(b"0" * 10)
    (d / "j1.json").unlink()
    monkeypatch.chdir(proj.root); capsys.readouterr()
    assert main(["organize", "--clean"]) == 0
    assert "removed 1" in capsys.readouterr().out and not (tmp_path / "dst" / "x.bin.sciweave-part").exists()
