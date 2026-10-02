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

- **resource (RES, category "resources")**: an online service or database that was *used* but
  not received as data (imputation servers, gnomAD / Ensembl lookups). It has `meta.url` instead of
  a file, no "no file" red ring (that ring marks objects that still need a path), nothing to copy
  to the destination. Hue #c45bb0 / #c76bbd: passes the palette checks against blue / yellow / aqua
  with the protan pair to sources in the 6–8 band, legal because every node also carries a type
  icon (a globe) and its RES code.

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

## 8b. Monitor, updates and branches

**Monitor.** `sciweave/monitor.py::run_round` is one deterministic, metadata-only round:
- tracked files changed on disk;
- untracked files that are new since the last round, with a guessed type and a
  `version_of` / `near` hint;
- missing files and stale items.

Everything it has already reported is remembered in `.sciweave/monitor.json`, so each
round reports only what's new. The first round is a baseline (per-folder summary, no
flood).

Ways to run it:
- the `sciweave-monitor` Claude skill starts `/loop 60m /sciweave-monitor round`;
- each loop tick calls `sciweave monitor --due 50`, which prints nothing unless a round is
  due and something is new;
- `sciweave monitor` runs rounds in a terminal;
- the dashboard's 🔔 shows the latest round.

Design constraint, learned from brainny: a timed loop fires *inside* the Claude session
and can land mid-task. So a round is silent when there's nothing new, stays at most 3 lines
when it interrupts work, resumes the task, and never applies anything.

**Updates.** Each version stores `why` (given by the user or Claude) and `changes`, which
is computed automatically against the previous version:
- `input` / `script` vN → vM, plus added or dropped inputs;
- `param` key old → new on a given link;
- `content` size.

Parameter edits on an existing link are appended to `edge.changes` with their own `why`.
Snapshots are copies, so a later edit can never rewrite history.

**Branches.** `Project.branch()` creates an alternative node:
- it copies the original's provenance links, with the parameter overrides applied to the
  link that carries them;
- it adds a `variant` link (soft: no staleness) from the original;
- it records `branch = {of, name, why, status}`.

`set_branch_status(main|alternative|abandoned, why)` keeps exactly one `main` per family
and stores the decision's reason. The dashboard shows a family in the node panel and a
branch badge on alternatives.

## 8b2. The analysis history (guide)

- `graph["analyses"]`: `{key: {title, parent, order, summary, status (done/open/superseded/planned),
  start, end, params, tools, paths, feeds, step, nodes, organized, history: [{date, text}]}}`.
  A tree (parent keys; loops refused), numbered by position (1, 1.2, 1.2.1), independent of the
  network: it can describe work whose files are not recorded yet. `organized` + `nodes` track
  the move of each analysis into the network.
- `Project.set_analysis / log_analysis / remove_analysis (children move up) / analysis_tree /
  import_analyses` (all-or-nothing, nested `children` or `parent`), `rename_analysis` (branches `old.*`
  and every `@key` reference follow). A sibling with `order` 0 is numbered 0 ("before", e.g. an earlier
  round), the rest count from 1. CLI `sciweave analysis ...`.
- `group` is a label, not a parent: independent siblings that belong together (the five cohorts, the
  four external GWAS) share it and the column shows a small heading where the label changes. Each
  received dataset is its own entry; nesting is only for real sub-analyses (reruns, fixes, parts).
- Dashboard: History tab › **Analyses** (default when a history exists; an empty network with a
  history opens here). Drill-down columns (each click opens the branches in the next column) and
  a detail pane (summary, params, paths, objects, branches, dated history; live: mark organized,
  add a history line via `POST api/analysis`). `?page=analyses` deep-links. Home cards show
  "N / M organized".

## 8b3. Focus: hiding (never deleting)

- `analysis["hidden"]` hides an analysis and its subtree (`Project.hidden_analyses`).
  `node["hidden"]` True hides one object; False pins it visible.
- `Project.hidden_nodes()` → {id: reason}: objects hidden by hand, listed in a hidden analysis,
  or whose every consumer (outgoing non-`documents`/`related` link, or a link that ran it as its
  script) is hidden — iterated to a fixpoint, so raw inputs and scripts used only by a hidden
  step disappear. Objects listed in a visible analysis are pinned visible. Links touching a hidden
  object are hidden; downstream objects stay.
- Purely a view: staleness, history, plans and articles are unchanged. In `payload()["hidden"]`;
  the network filters it (toolbar "hidden items" to show), the guide hides those rows ("show
  hidden (N)" shows them dimmed; eye button / "Hide from view" toggles), numbering stays stable.
  CLI: `analysis hide|unhide`, `analysis ls --all`, `hide|unhide <IDs>`, and a focus line in
  `status`. `SCIWEAVE.md` still lists everything, marked hidden.
- In the guide, hidden analyses stay in the chain as **blue dots** at their position (consecutive
  ones share a strip with "N hidden"). Clicking a dot *peeks*: the item opens in place, faded, with
  its branches browsable, for this visit only; the dot at the row's start folds it back. Peeking
  never changes the saved `hidden` flag; the eye / Unhide does. `?analysis=<key>` peeks hidden
  ancestors on the way.

## 8b4. Organized copies (destination)

- `project.destination` (set with `sciweave destination <folder>` or on the home card): where the
  organized project's files live. A source working folder stays where it is.
- `organize <ID>` (dashboard: double-click a node, or "Save to destination") copies the object's
  file/folder to `<destination>/<NN_step label>/[<first custom group>/]<name>` — the folder tree
  mirrors the network. Files keep their names (prefixed by the id on a clash); folders become
  `<ID>_<label>`. The record `node["organized"]` = {path, name, source, source_hash, size, kind, copied}.
- `commit()` calls `sync_organized()`: when an object's step or group changes, its copy is moved to the
  matching folder and empty folders left behind are removed.
- `organized_state`: ok / source_changed (source fingerprint differs from copy time) / copy_missing /
  source_missing; shown in `status`, the node panel ("Update copy") and as a node badge (green / orange).
- In the server, copies run on a thread (`JOBS`, `GET api/jobs`); only recording takes the lock. The
  network's click handler waits 260 ms before selecting so a second click is caught as a double-click
  (selecting opens the panel and shifts the layout, so the browser's own dblclick would miss).

- **Project actions panel** (right side of the network whenever no node is selected; top-bar button
  "Project actions" reopens it): check for new results, analysis history, the destination (open
  folder, change, save all not copied) and every object by step with Save / Update / Show and its
  target folder. A node's panel replaces it; closing the node panel brings it back.
- `POST api/reveal` opens the OS file manager at a tracked copy or the destination (the server runs on
  the user's machine); never arbitrary paths.

- **Verified copies.** Every file is copied while its SHA-256 is computed, the copy is re-read and must
  match before it replaces the old one (folders are built in a temporary folder and swapped in). A
  checksum file `<name>.sha256` (sha256sum format; one line per file for a folder) is written next to
  the copy and moves with it. `organized_check` (sizes + source change, no content read) runs before
  any re-save; the dashboard always asks — "Already up to date: copy again anyway?" or "Update the
  copy? source N bytes / copy M bytes" — and Save all asks for each copy that needs updating.
  `verify_copy` / "Verify copy" / `organize --verify` re-reads the copy against its checksum file (a
  copy made before checksums is compared with its source and gets one). The CLI skips an up-to-date
  copy unless `--force`.
- **Large data** (> 4 GiB, `Project.BIG_BYTES`): elephant marker; never copied by Save all / `--all`
  without an explicit yes per object; a "no" sets `organize_skip` (red dashed ring, not asked again,
  cleared when the object is copied on its own).

- **Copies are jobs, not threads** (`sciweave/jobs.py`): each copy / verification is a JSON file in
  `.sciweave/jobs/` run by its own detached process (`sciweave -C <root> job <id>`), so closing or
  restarting the dashboard never stops it. The process writes progress (bytes done / total over the
  copy + check passes, phase, heartbeat every <= 0.5 s); `GET api/jobs` reads the files; a job without a
  heartbeat for 60 s is "interrupted" (retrying clears its `*.sciweave-part` leftovers). At most 2
  copies per project run at once, the rest are "queued". Writes to graph.json from a job and from the
  dashboard's POSTs are serialised by a lock file (`jobs.graph_lock`). `organize --clean` removes
  leftovers of interrupted copies when nothing is running. The dashboard shows per-object and overall
  progress bars with speed and time left.
- **Fast reloads**: full-content hashes are remembered by (path, size, mtime) in
  `.sciweave/fpcache.json` (never for files modified in the last 3 s — same size + same timestamp could
  hide a change); new copies record the source's mtime so "source changed?" is a stat, not a re-read.
- graph.json reads / replaces retry briefly on Windows PermissionError; each writer uses its own temp file.

## 8b5. One file, one node (dedup)

- `sciweave/dedup.py`: files of every object (a file, or all files under a folder; < 4 KB ignored)
  are grouped by size; only equal-size files are hashed (full SHA-256, cached in
  `.sciweave/hashcache.json` by path + size + mtime). Identical content across objects = "whole"
  (same set of file hashes) or "partial" (some shared).
- `plan check` errors on a node whose content is already in the network or appears twice in the
  plan; warns on partial overlap. `sciweave add` refuses (`--allow-duplicate` to override).
- `dedup merge keep dup`: re-points links (dropping self-loops / duplicates), version parents,
  analysis `nodes`; records `meta.also_at` (other locations) and `meta.used_by`; removes dup.

## 8b6. Live (no refresh)

- `GET api/stamp` returns only graph.json / history.jsonl mtime+size and running copy jobs; the dashboard
  polls it every 2 s (always on; the ↻ button pauses) and fetches `api/graph` only when it changed. The
  home page polls `api/projects/stamp` every 3 s. Polling stops while the tab is hidden and never
  redraws under someone typing in the panel or the guide.
- Growth without reshuffling: the force view remembers each group's centre and each object's offset
  from it; on a live redraw known objects are pinned while new ones settle (then released), groups keep
  their place and only a new group is placed. New objects get a pulsing ring for a few seconds and a toast.
  The project panel opens before the first drawing so the first layout uses the real width.
  A reload, a new "Group by" or shift+Fit gives a fresh layout.

## 8c. The SciWeave home (all projects)

- **Registry:** `~/Documents/SciWeave/` (`SCIWEAVE_HOME` overrides; falls back to
  `~/SciWeave` when there is no Documents folder). `projects.json` is the machine-readable list
  (id, name, path, added); `PROJECTS.md` is regenerated beside it for people and Claude.
  Projects are never moved; the home only points at them (`sciweave/registry.py`).
- **Registration:** `init` (unless `--no-register`), `serve`, `open -C <path>`,
  `projects add <path>`, or the home page's *Add project* dialog (which can also create one).
  Removing from the list never touches the folder.
- **Home server:** `sciweave open` runs the same handler in home mode: `/` is the home page
  (`dashboard/home.html`, data from `/api/projects`), and `/p/<id>/...` routes to that
  project's dashboard and API. The project dashboard only uses relative URLs, so it works
  unchanged under the prefix and shows a "Projects" link back.
- **Cards:** objects, links, figures and tables, steps, a category composition bar, and
  stale / modified / missing counts from the live status, sorted by last activity.

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
