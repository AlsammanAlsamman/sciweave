"""`sciweave demo`: build a small, entirely synthetic GWAS -> fine-mapping ->
article project so the dashboard has something real to show.

All data is generated here (random numbers, generic names like "Cohort A");
nothing is derived from any real dataset. History is back-dated over ~7 weeks
so the growth chart and timeline look like a real project's life.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sciweave.project as project_mod
from sciweave import article as article_mod
from sciweave.project import Project, SciWeaveError


class _Clock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self) -> str:
        self.t += timedelta(minutes=7)
        return self.t.replace(microsecond=0).isoformat()

    def advance(self, days: float = 0, hours: float = 0):
        self.t += timedelta(days=days, hours=hours)


def _svg_scatter(title: str, pts, xlab: str, ylab: str, hline: float | None = None) -> str:
    w, h, m = 640, 320, 44
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y1 = min(xs), max(xs), max(ys) * 1.08

    def sx(x): return m + (x - x0) / (x1 - x0 or 1) * (w - 2 * m)
    def sy(y): return h - m - y / (y1 or 1) * (h - 2 * m)

    dots = "".join(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.2" fill="{c}"/>' for x, y, c in pts)
    line = (f'<line x1="{m}" x2="{w - m}" y1="{sy(hline):.1f}" y2="{sy(hline):.1f}" stroke="#d03b3b" '
            f'stroke-dasharray="4 3"/>') if hline else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="Helvetica" font-size="12">'
            f'<rect width="{w}" height="{h}" fill="#fff"/><text x="{m}" y="22" font-weight="bold">{title}</text>'
            f'<line x1="{m}" y1="{h - m}" x2="{w - m}" y2="{h - m}" stroke="#555"/>'
            f'<line x1="{m}" y1="{m}" x2="{m}" y2="{h - m}" stroke="#555"/>{line}{dots}'
            f'<text x="{w / 2}" y="{h - 10}" text-anchor="middle">{xlab}</text>'
            f'<text x="14" y="{h / 2}" transform="rotate(-90 14 {h / 2})" text-anchor="middle">{ylab}</text></svg>')


def _manhattan(seed: int, peak: float) -> str:
    rnd = random.Random(seed)
    pts = []
    for chrom in range(1, 23):
        col = "#2a78d6" if chrom % 2 else "#7a9cc9"
        for i in range(60):
            y = rnd.expovariate(1.2)
            if chrom == 6 and 25 < i < 35:
                y += peak * (1 - abs(i - 30) / 6)
            pts.append((chrom * 100 + i, y, col))
    return _svg_scatter("Manhattan plot (Cohort A, synthetic)", pts, "chromosome", "-log10(p)", hline=7.3)


def _pip(seed: int, n_sets: int) -> str:
    rnd = random.Random(seed)
    pts = []
    for i in range(120):
        y = rnd.random() * 0.05
        c = "#9a9a94"
        if i % 40 == 20 and i // 40 < n_sets:
            y, c = 0.6 + rnd.random() * 0.35, "#1baf7a"
        pts.append((i, y, c))
    return _svg_scatter("Posterior inclusion probability (SuSiE, synthetic)", pts, "variant position", "PIP")


def _tsv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\t".join(header) + "\n" + "\n".join("\t".join(map(str, r)) for r in rows) + "\n", encoding="utf-8")


def build_demo(root: Path) -> Project:
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise SciWeaveError(f"{root} is not empty; pick a new folder for the demo")
    clock = _Clock(datetime.now(timezone.utc) - timedelta(days=52))
    real_now = project_mod.now_iso
    project_mod.now_iso = clock
    try:
        return _build(root, clock)
    finally:
        project_mod.now_iso = real_now


def _build(root: Path, clock: _Clock) -> Project:
    rnd = random.Random(7)
    p = Project.init(root, name="Demo · GWAS to fine-mapping (synthetic)",
                     description="Synthetic demo project: GWAS in a fictional Cohort A, fine-mapping of the "
                                 "top locus, and the article built from it. All numbers are random.")
    ext = root / "external"   # stands in for data living elsewhere (e.g. on HPC storage)

    # --- raw data & inputs -------------------------------------------------
    for chrom in (1, 2, 6):
        f = ext / "raw" / "genotypes" / f"cohortA_chr{chrom}.pgen"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(bytes(rnd.getrandbits(8) for _ in range(512)))
    raw = p.add_node("raw", "Genotypes (Cohort A, imputed)", path=str(ext / "raw" / "genotypes"), mode="ref",
                     description="Imputed genotypes, PGEN per chromosome. Never copied; lives on project storage.",
                     groups=["gwas"])
    _tsv(ext / "pheno.tsv", ["IID", "case"], [[f"S{i:04d}", rnd.randint(0, 1)] for i in range(200)])
    _tsv(ext / "covars.tsv", ["IID", "sex", "age", "PC1", "PC2"],
         [[f"S{i:04d}", rnd.randint(0, 1), rnd.randint(20, 70), round(rnd.gauss(0, 1), 3), round(rnd.gauss(0, 1), 3)]
          for i in range(200)])
    pheno = p.add_node("input", "Phenotype (case/control)", path=str(ext / "pheno.tsv"), mode="managed",
                       groups=["gwas"], description="Case/control status after exclusions.")
    cov = p.add_node("input", "Covariates (sex, age, PCs)", path=str(ext / "covars.tsv"), mode="managed",
                     groups=["gwas"])
    ld_dir = ext / "ld_panel"
    ld_dir.mkdir(parents=True)
    (ld_dir / "panel_EUR.bed").write_bytes(b"\x00" * 256)
    ld = p.add_node("input", "LD reference panel (EUR)", path=str(ld_dir), mode="ref", groups=["finemapping"])
    clock.advance(days=3)

    # --- GWAS pipeline -----------------------------------------------------
    gw = root / "pipelines" / "gwas_snakemake"
    (gw / "output").mkdir(parents=True)
    (gw / "Snakefile").write_text("rule all:\n    input: 'output/sumstats.tsv'\n", encoding="utf-8")
    (gw / "config.yaml").write_text("maf: 0.01\ninfo: 0.8\nhwe: 1e-6\nmodel: logistic\n", encoding="utf-8")
    pl1 = p.add_node("pipeline", "GWAS pipeline (Snakemake)", path=str(gw / "Snakefile"), mode="ref",
                     groups=["gwas"], description="QC -> association (PLINK2 logistic) -> summary statistics.")
    for src in (raw, pheno, cov):
        p.link(src["id"], pl1["id"], rel="feeds")
    _tsv(gw / "output" / "qc_report.txt", ["step", "n_variants"], [["input", 812345], ["maf", 402111], ["info", 390876]])
    _tsv(gw / "output" / "sumstats.tsv", ["SNP", "CHR", "BP", "P"],
         [[f"rs{1000 + i}", 6, 32_000_000 + i * 900, f"{rnd.random() * 1e-3:.2e}"] for i in range(60)])
    qc = p.add_node("result", "QC report (GWAS)", path=str(gw / "output" / "qc_report.txt"), mode="ref", groups=["gwas"])
    ss = p.add_node("result", "Summary statistics (GWAS)", path=str(gw / "output" / "sumstats.tsv"), mode="ref",
                    groups=["gwas"], description="Intermediate output; stays in the pipeline folder.")
    p.link(pl1["id"], qc["id"], rel="produces", params={"maf": 0.01, "info": 0.8, "hwe": 1e-6},
           params_file=str(gw / "config.yaml"))
    p.link(pl1["id"], ss["id"], rel="produces",
           params={"model": "logistic", "maf": 0.01, "info": 0.8, "covariates": "sex,age,PC1-PC2"},
           command="snakemake -j 16 --profile slurm", params_file=str(gw / "config.yaml"))
    clock.advance(days=4)

    # --- scripts ------------------------------------------------------------
    sd = root / "scripts"
    (sd / "lead_loci.py").write_text("# clump summary stats into lead loci\nP_THRESHOLD = 5e-8\n", encoding="utf-8")
    (sd / "plot_manhattan.py").write_text("# Manhattan plot\nimport matplotlib\n", encoding="utf-8")
    (sd / "susie_run.R").write_text("# SuSiE fine-mapping\nlibrary(susieR)\nL <- 10\n", encoding="utf-8")
    (sd / "plot_pip.py").write_text("# PIP plot per credible set\n", encoding="utf-8")
    sc_lead = p.add_node("script", "lead_loci.py", path=str(sd / "lead_loci.py"), mode="managed", groups=["gwas"])
    sc_man = p.add_node("script", "plot_manhattan.py", path=str(sd / "plot_manhattan.py"), mode="managed", groups=["gwas"])

    # --- GWAS outputs -------------------------------------------------------
    rd = root / "results"
    _tsv(rd / "tables" / "lead_loci.tsv", ["locus", "lead_SNP", "CHR", "BP", "P", "OR"],
         [["6p21", "rs1042", 6, 32_041_000, "3.1e-12", 1.41], ["2q32", "rs2177", 2, 191_900_000, "8.0e-09", 1.22]])
    t1 = p.add_node("table", "Lead loci (GWAS)", path=str(rd / "tables" / "lead_loci.tsv"), mode="managed",
                    groups=["gwas"], description="Genome-wide significant loci after clumping.")
    p.link(ss["id"], t1["id"], rel="derives", params={"p_threshold": 5e-8, "clump_r2": 0.1, "clump_kb": 500},
           script=sc_lead["id"], command="python scripts/lead_loci.py sumstats.tsv")
    (rd / "figures" / "manhattan.svg").write_text(_manhattan(1, 9), encoding="utf-8")
    f1 = p.add_node("figure", "Manhattan (GWAS)", path=str(rd / "figures" / "manhattan.svg"), mode="managed",
                    groups=["gwas"])
    p.link(ss["id"], f1["id"], rel="derives", script=sc_man["id"], command="python scripts/plot_manhattan.py")
    p.save_graph()
    clock.advance(days=6)

    # --- fine-mapping -------------------------------------------------------
    fm = root / "pipelines" / "finemap"
    (fm / "work").mkdir(parents=True)
    (fm / "Snakefile").write_text("rule susie:\n    script: '../../scripts/susie_run.R'\n", encoding="utf-8")
    pl2 = p.add_node("pipeline", "Fine-mapping pipeline (SuSiE)", path=str(fm / "Snakefile"), mode="ref",
                     groups=["finemapping"])
    sc_susie = p.add_node("script", "susie_run.R", path=str(sd / "susie_run.R"), mode="managed", groups=["finemapping"])
    p.link(t1["id"], pl2["id"], rel="feeds")
    p.link(ss["id"], pl2["id"], rel="feeds")
    p.link(ld["id"], pl2["id"], rel="feeds")
    for i in range(3):
        _tsv(fm / "work" / f"ld_matrix_{i}.tsv", ["i", "j", "r"], [[i, i, 1.0]])
    work = p.add_node("result", "LD matrices (intermediate)", path=str(fm / "work"), mode="ref", groups=["finemapping"],
                      description="Per-locus LD matrices; kept in the pipeline, referenced only.")
    p.link(pl2["id"], work["id"], rel="produces", params={"ld_panel": "EUR", "window_kb": 500})
    _tsv(rd / "tables" / "credible_sets.tsv", ["locus", "cs", "SNP", "PIP"],
         [["6p21", 1, "rs1042", 0.81], ["6p21", 1, "rs1043", 0.12], ["6p21", 2, "rs1101", 0.66], ["2q32", 1, "rs2177", 0.93]])
    t2 = p.add_node("table", "Credible sets (finemapping)", path=str(rd / "tables" / "credible_sets.tsv"),
                    mode="managed", groups=["finemapping"], description="95% credible sets per locus.")
    p.link(pl2["id"], t2["id"], rel="produces", params={"L": 10, "coverage": 0.95, "ld_panel": "EUR", "min_abs_corr": 0.5},
           script=sc_susie["id"], command="snakemake -j 8 susie")
    _tsv(rd / "supplementary" / "all_credible_snps.tsv", ["locus", "cs", "SNP", "PIP", "BP"],
         [["6p21", 1, f"rs{1040 + i}", round(rnd.random() / 10, 3), 32_040_000 + i * 150] for i in range(25)])
    s1 = p.add_node("supplement", "All credible-set SNPs", path=str(rd / "supplementary" / "all_credible_snps.tsv"),
                    mode="managed", groups=["finemapping"])
    p.link(t2["id"], s1["id"], rel="derives")
    sc_pip = p.add_node("script", "plot_pip.py", path=str(sd / "plot_pip.py"), mode="managed", groups=["finemapping"])
    (rd / "figures" / "pip.svg").write_text(_pip(2, 1), encoding="utf-8")
    f2 = p.add_node("figure", "PIP plot (finemapping)", path=str(rd / "figures" / "pip.svg"), mode="managed",
                    groups=["finemapping"])
    p.link(t2["id"], f2["id"], rel="derives", params={"pip_threshold": 0.1}, script=sc_pip["id"],
           command="python scripts/plot_pip.py credible_sets.tsv")
    p.add_note(t2["id"], "HLA core region kept; checked that the credible set is not driven by a single imputed SNP.")
    clock.advance(days=5)
    # a second, improved version of the PIP plot, marked final
    (rd / "figures" / "pip.svg").write_text(_pip(3, 2), encoding="utf-8")
    p.save_version(f2["id"], message="show both credible sets; colour by set")
    p.mark_final(f2["id"])
    p.set_group("finemapping", [], color=None)
    p.save_graph()
    clock.advance(days=4)

    # --- article ------------------------------------------------------------
    art = article_mod.new_article(p, "Fine-mapping of the 6p21 locus", node_id=None)
    article_mod.place(p, f1["id"], art["id"], "Figure 1")
    article_mod.place(p, f2["id"], art["id"], "Figure 2")
    article_mod.place(p, t1["id"], art["id"], "Table 1")
    article_mod.place(p, s1["id"], art["id"], "Supplementary Table 1")
    methods = next(n for n in p.nodes.values() if n["type"] == "section" and n["meta"].get("section") == "methods")
    methods_path = p.resolve(methods["path"])
    methods_path.write_text(methods_path.read_text(encoding="utf-8") +
                            "Genotypes were imputed and filtered (MAF > 0.01, INFO > 0.8). Association used "
                            "logistic regression adjusted for sex, age and two PCs. Fine-mapping used SuSiE "
                            "(L = 10, 95\\% coverage) with an EUR LD panel.\n", encoding="utf-8")
    p.save_version(methods["id"], message="first methods draft")
    p.link(methods["id"], pl1["id"], rel="documents")
    p.link(methods["id"], pl2["id"], rel="documents")
    note = p.add_node("note", "Reviewer 2: add conditional analysis", groups=["finemapping"],
                      description="Consider conditional analysis at 6p21 before submission.")
    p.link(note["id"], t2["id"], rel="related")
    p.save_graph()
    clock.advance(days=9)

    # --- a change upstream: covariates gain PC3 -> everything downstream goes stale
    cov_path = p.resolve(cov["path"])
    lines = cov_path.read_text(encoding="utf-8").splitlines()
    lines = [lines[0] + "\tPC3"] + [ln + f"\t{round(rnd.gauss(0, 1), 3)}" for ln in lines[1:]]
    cov_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    p.save_version(cov["id"], message="add PC3 (residual stratification in QQ plot)")
    clock.advance(days=1)
    # GWAS re-run: new summary stats + lead loci regenerated; figures not yet
    (gw / "output" / "sumstats.tsv").write_text((gw / "output" / "sumstats.tsv").read_text(encoding="utf-8") +
                                                "rs9999\t6\t32100000\t1.0e-04\n", encoding="utf-8")
    e = p.find_edge(pl1["id"], ss["id"])
    e["params"]["covariates"] = "sex,age,PC1-PC3"
    p.save_version(ss["id"], message="re-run with PC3", actor="claude")
    (rd / "tables" / "lead_loci.tsv").write_text((rd / "tables" / "lead_loci.tsv").read_text(encoding="utf-8")
                                                 .replace("3.1e-12", "2.7e-12"), encoding="utf-8")
    p.save_version(t1["id"], message="regenerated after PC3 re-run", actor="claude")
    p.commit()
    return p
