# SciWeave import protocol — full reference

## Node types

| type | id | use for | default mode |
|---|---|---|---|
| raw | RAW | raw data (FASTQ, genotypes, VCFs) | ref |
| input | IN | prepared inputs: phenotypes, covariates, sample lists, reference panels | managed if small, ref if big |
| pipeline | PL | a workflow (Snakemake / Nextflow / a driver script folder) — path = its entry file | ref |
| step | ST | one rule/step of a pipeline when you need that granularity | ref |
| script | SC | analysis / plotting script | managed |
| result | R | any other output (sumstats, logs, models, RDS) | ref for intermediates |
| table | T | a result table meant to be read | managed |
| figure | F | a plot | managed |
| supplement | S | supplementary table / figure / file | managed |
| section | TX | a text section (methods, results) — usually created by `article new` | managed |
| article | A | an article folder — created by `article new` | managed |
| note | N | a free-standing note / decision / reviewer comment | – |

## Steps (analysis stages)

Every node has a `step`: the stage of the analysis that produced it, in project order,
for example `gwas` (1) → `meta` (2) → `finemapping` (3) → `functional` (4) → `article` (5).
- Reuse existing steps (`sciweave step ls`). Only add a new one when it really is a new stage.
  Define it in the plan's `"steps"` with `key`, `label`, `order` and optional `description`.
- Where a node belongs: the step that *made* it, not the one that uses it. A Manhattan plot
  made by the GWAS step is `gwas`, even if the article uses it. The article's own numbered
  items and its sections are `article`.
- Links within a step, and links between steps (e.g. `meta` → `finemapping`), come out of the
  graph automatically. Don't create anything extra for them.
- The monitor suggests a step for new files, taken from their neighbour (`[step gwas]`).
  Confirm it, don't blindly copy it.

## Link relations (direction = data flow)

| rel | from → to | params? |
|---|---|---|
| feeds | input/data → pipeline/step/script | rarely |
| produces | pipeline/step/script → output | **yes, the run's parameters** |
| derives | output → output (table → figure) | **yes if any filter/threshold** |
| code | script → output (auto-added when a link has `script`) | no |
| part_of | component → article (`article place` does this) | no |
| documents | section → what it describes (methods → pipeline) | no |
| related | loose association (note → table) | no |

Staleness follows feeds/produces/derives/code (through pipelines and scripts to
their inputs). `documents` and `related` never make anything stale.

## What counts as a result-changing parameter

Put it on the link if changing it would change a number, a set of hits, or a
conclusion a reader could check:

- statistical thresholds (p < 5e-8, FDR 0.05, PIP > 0.1, r² 0.1, window 500 kb)
- models and methods (logistic vs linear, SuSiE L = 10, coverage 0.95)
- filters (MAF 0.01, INFO 0.8, HWE 1e-6, call rate)
- reference data (LD panel EUR, genome build GRCh38, annotation release)
- covariates, number of PCs, random seeds, iterations, software versions

Leave out: colours, fonts, figure size, dpi, themes, axis labels, titles,
output paths, thread counts, memory.

If the pipeline has a config file, pass it as `params_file` (it is
snapshotted) *and* still lift the key parameters onto the link, so they are
visible in the network.

## Keeper vs intermediate

Keeper (managed): anything that will be looked at again, cited, plotted,
placed in the article, or sent to a collaborator.

Intermediate: per-chromosome chunks, temp files, LD matrices, logs, `.done`
sentinels, caches. Reference one representative node (often the folder, mode
`ref`) only if it helps explain how a keeper was made. Don't list them all.

## plan.json

```json
{
  "sciweave_plan": 1,
  "summary": "SuSiE fine-mapping of the 6p21 locus (credible sets + PIP plot)",
  "steps": [{"key": "finemapping", "label": "Fine-mapping", "order": 3,
             "description": "SuSiE / SuSiEx credible sets at the lead loci"}],
  "nodes": [
    {"key": "fm_pipe", "type": "pipeline", "label": "Fine-mapping pipeline (SuSiE)",
     "path": "/work/finemap/Snakefile", "mode": "ref", "step": "finemapping"},
    {"key": "susie", "type": "script", "label": "susie_run.R",
     "path": "/work/finemap/scripts/susie_run.R", "mode": "managed", "step": "finemapping"},
    {"key": "cs", "type": "table", "label": "Credible sets (finemapping)",
     "path": "/work/finemap/out/credible_sets.tsv", "mode": "managed", "step": "finemapping",
     "description": "95% credible sets per locus", "groups": ["finemapping"]},
    {"key": "pip", "type": "figure", "label": "PIP plot (finemapping)",
     "path": "/work/finemap/out/pip.png", "mode": "managed", "step": "finemapping", "groups": ["finemapping"]}
  ],
  "edges": [
    {"from": "T1", "to": "fm_pipe", "rel": "feeds"},
    {"from": "IN3", "to": "fm_pipe", "rel": "feeds"},
    {"from": "fm_pipe", "to": "cs", "rel": "produces",
     "params": {"L": 10, "coverage": 0.95, "ld_panel": "EUR", "min_abs_corr": 0.5},
     "script": "susie", "command": "snakemake -j 8 susie",
     "params_file": "/work/finemap/config.yaml"},
    {"from": "cs", "to": "pip", "rel": "derives", "params": {"pip_threshold": 0.1}}
  ],
  "notes": [{"node": "cs", "text": "HLA core region kept after sensitivity check"}],
  "groups": [{"name": "finemapping", "nodes": ["cs", "pip"]}],
  "final": []
}
```

- `key` is a local handle; refer to existing nodes by their id (`T1`, `IN3`).
- `dest` (optional) chooses where a managed file lands inside the project;
  default is the type folder (`results/tables/`, `results/figures/`, `scripts/` …).
- `save` re-versions existing nodes: `{"node": "T1", "message": "re-run with PC3"}`.
- `final` marks versions final: `{"node": "F2"}` (current) or with `"version": 2`.

## Reading the check report

- `ERROR` — plan will not apply: missing paths, unknown types/ids, cycles,
  destination collisions, non-scalar params.
- `WARN` — apply is allowed but think: cosmetic-looking params, outputs with no
  incoming link (unknown provenance), big managed files, raw data being copied,
  green links into outputs (did you forget the parameters?).

Resolve every WARN either by fixing the plan or by telling the user why it is fine.

## After applying

- `sciweave status` — new stale items are expected when you re-saved an
  upstream node; list them for the user in upstream-first order.
- If a figure/table went into an article: `sciweave article place <id> A1 --as "Figure N"`.
- Offer `sciweave serve` (dashboard) or `sciweave export` (static HTML).
