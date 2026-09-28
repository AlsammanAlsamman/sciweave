# SciWeave — design & plan

> **SciWeave weaves a research project's raw data, pipelines, parameters, scripts,
> tables, figures and article into one provenance network — so you can always
> answer "how was this made, with what, and is it still up to date?"**

This file is the living plan. When a section becomes real code with tests, it
thins down to a pointer.

---

## 1. The problem

A bioinformatics project is a web, not a folder: raw data → inputs → pipelines
(with parameters) → intermediate files → tables → figures → article figures,
tables, supplements and methods text. Months later nobody remembers:

- which script / parameter set / pipeline branch produced *Figure 2*;
- whether *Table 1* is still valid after the QC threshold changed;
- which of five `final_v3_REAL.png` files went into the submission;
- where the numbers quoted in the Methods section came from.

## 2. The idea in one picture

```
 RAW1 raw genotypes ──feeds──▶ PL1 fine-mapping pipeline ──produces [L=10, cov=0.95]──▶ T1 credible sets
                                   ▲                                               │ derives
                         SC1 susie_run.R (v3) ──code──┘                             ▼
                                                             SC2 plot_pip.py ─code─▶ F1 PIP plot ──part_of "Figure 2"──▶ A1 paper
```

- **Nodes** are typed things (raw, input, pipeline, step, script, result,
  table, figure, supplement, section, article, note) with an icon and a short
  code (`T1`, `F2`, `PL1`) plus a human label: `T1 · Credible sets (finemapping)`.
- **Edges** are the provenance: *what fed what*. An edge carries the
  **result-changing parameters** (red edge) or none (green edge), the script and
  the script's version, and the command.
- **Versions**: every node has a version history. Managed files are
  snapshotted into a content-addressed store (`.sciweave/objects/`), so any old
  version can be restored. One version can be marked **final**.
- **Staleness**: every edge remembers which version of its source the target
  was built from. When a parent gets a new version, everything downstream turns
  **stale** until it is regenerated and saved — the network shows exactly what
  must be redone.
- **History**: an append-only event log (`.sciweave/history.jsonl`) records
  every add / link / save / final / note / restore, by who (`user`, `claude`,
  `watch`). The project's growth can be replayed over time.

## 3. Principles (don't violate)

1. **One contract.** `.sciweave/graph.json` is the single source of truth.
   CLI, dashboard, AI and skill all read/write through `sciweave.project`.
   Human-readable `SCIWEAVE.md` is *generated* from it, never edited.
2. **Copy the keepers, reference the rest.** Final results that belong to the
   project are *managed* (copied in + snapshotted). Pipeline intermediates stay
   where they are as *ref* nodes (fingerprinted, never copied, never blocking).
3. **Parameters live on the edge, and only the ones that matter.** A parameter
   belongs on an edge if changing it would change a reported result (thresholds,
   models, reference panels, seeds) — not font sizes or colours.
4. **Claude proposes a plan; the CLI validates and applies it.** The AI never
   edits `graph.json` by hand. It writes a small `plan.json`, runs
   `sciweave plan check` (human-readable report, errors are loud), shows it to
   the user, then `sciweave plan apply`. Easy to review, easy to spot mistakes.
5. **Offline & dependency-free core.** Python stdlib only; D3 is vendored
   inline (no CDN — a lesson from brainny). The AI is optional (`sciweave[ai]`).
6. **Nothing destructive without asking.** Restores refuse to overwrite unsaved
   changes; `apply` backs up `graph.json` first.

## 4. Project layout created by `sciweave init`

```
my-project/
├── .sciweave/                 # machine state (commit it to git)
│   ├── graph.json             # nodes, edges, groups — THE contract
│   ├── history.jsonl          # append-only event log
│   ├── objects/ab/cdef…       # content-addressed snapshots of managed files
│   └── plans/                 # applied import plans (audit trail)
├── SCIWEAVE.md                # generated map of the project (for humans & AI)
├── data/raw/  data/inputs/
├── pipelines/  scripts/
├── results/tables/  results/figures/  results/other/
├── articles/                  # one folder per article (see §6)
└── notes/
```

## 5. Node types

| type | code | category | meaning |
|---|---|---|---|
| raw | RAW | sources | raw data (usually ref, never copied) |
| input | IN | sources | prepared inputs (phenotypes, covariates, configs) |
| pipeline | PL | process | a pipeline / workflow (Snakemake, Nextflow…) |
| step | SP | process | one rule/step inside a pipeline |
| script | SC | process | an analysis / plotting script |
| result | R | outputs | any other output file |
| table | T | outputs | a result table |
| figure | F | outputs | a plot |
| supplement | S | writing | supplementary table/figure/file |
| section | TX | writing | a text part: methods, results, LaTeX section |
| article | A | writing | an article (a folder template) |
| note | N | notes | a free note |

Edge relations: `feeds` (input → process), `produces` (process → output),
`derives` (output → output, e.g. table → figure), `code` (script → anything),
`part_of` (component → article), `documents` (section → what it describes),
`related`.

## 6. Articles

`sciweave article new "Fine-mapping paper"` creates

```
articles/fine-mapping-paper/
├── figures/ tables/ supplementary/ scripts/ data/ notes/
└── latex/main.tex  latex/sections/{abstract,introduction,methods,results,discussion}.tex  latex/references.bib
```

plus an **article node** and one **section node per LaTeX section**.
`sciweave article place F1 A1 --as "Figure 2"` copies the figure's final (or
current) version into `figures/Figure_2.png` and links it `part_of` the article.
When F1 changes, the placement turns stale; `sciweave article sync A1` refreshes.

## 7. The Claude protocol (skill)

Installed with `sciweave install-skill` into `~/.claude/skills/sciweave/`.
When the user says *"add these results to the SciWeave project"*:

1. **Locate** the project (`sciweave status`), read `SCIWEAVE.md`.
2. **Inspect** what was just done in the session: outputs, scripts, commands,
   configs.
3. **Ask** the user the few questions that matter (which outputs are keepers,
   which article/figure number, anything ambiguous about parameters).
4. **Classify** each file: *keeper* (copy, managed) vs *intermediate* (ref) vs
   *ignore*.
5. **Extract parameters** — only result-changing ones.
6. **Write `plan.json`**, run `sciweave plan check`, show the report, fix
   errors, get a yes, run `sciweave plan apply`.
7. **Verify** with `sciweave status`, report what was added.

Full text: `sciweave/skills/sciweave/SKILL.md` and `protocol.md`.

## 8. Dashboard (`sciweave serve`)

- **Views:** force network with group halos · radial tree with provenance
  bundles · lineage (layered left→right DAG of the selected node).
- **Group by:** category (sources / process / outputs / writing), type,
  custom group, article, status.
- **Nodes:** type icon + short code; orange ring = stale, dashed = modified on
  disk, gold star = has a final version.
- **Edges:** red = carries parameters, green = none; dashed = stale.
- **Side panel:** description, path, versions (restore / mark final), how it
  was made (upstream chain with params & scripts), what depends on it, notes,
  history, preview (images, table head, text).
- **Tabs:** Network · Articles (every article's figures/tables/supplements with
  status) · History (timeline + growth).
- **Ask:** fast keyword highlight always; if `anthropic` is installed and
  credentials exist, a real Claude answer that also highlights nodes.
- **Save button** on a node = snapshot a new version.
- `sciweave export` writes a static, offline `sciweave.html` to share.

## 9. Roadmap

- **v0.1 (this):** model, versions, staleness, history, CLI, plan protocol,
  article template, dashboard (3 views, 3 tabs), local + Claude ask, watch
  (polling), demo project, skill, tests.
- **v0.2:** Snakemake/Nextflow importers (read the DAG + config into steps and
  params automatically); `sciweave diff F1 2 3` (image / table diffs); LaTeX
  hooks (`\sciweave{F1}` → figure path + caption provenance footnote).
- **v0.3:** MCP server so any assistant can query the graph; brainny bridge
  (captured precautions attached to nodes).
- **later:** multi-user merge of `graph.json`, remote/HPC refs over SSH.
