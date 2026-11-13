---
name: sciweave
description: Record research results into a SciWeave provenance project — raw data, inputs, pipelines, scripts, parameters, tables, figures, supplements and article sections, linked as a versioned network. Use when the user says "add this / these results to the (sciweave) project", "save this figure/table to the project", "record the parameters", "link this to the article", "what is stale", "how was Figure N made", or mentions SciWeave; also when starting a SciWeave project or an article template.
---

# SciWeave — recording results into a provenance network

SciWeave keeps a research project as a network: **nodes** (raw data, inputs,
pipelines, steps, scripts, results, tables, figures, supplements, sections,
articles, notes) and **links** that say what was made from what, carrying the
**result-changing parameters**, the script (and its version) and the command.
Every managed file is versioned; when a parent changes, everything downstream
turns *stale*.

The `sciweave` CLI is the only way to change a project. **Never edit
`.sciweave/graph.json` or `SCIWEAVE.md` by hand.**

Read `protocol.md` (next to this file) before your first import in a session —
it has the full decision rules and a worked example.

## Quick orientation

```bash
sciweave status              # where am I, what is stale / modified / missing
cat SCIWEAVE.md              # generated map: every node, link, parameter
sciweave show F2             # one node: versions, how it was made, what uses it
sciweave trace F2            # upstream provenance tree   (--down: dependents)
sciweave types               # node types & link relations
```

No project yet? Ask where it should live, then `sciweave init <path> --name "..."`
(it is listed in the user's SciWeave home automatically).

## Which project? The SciWeave home

Every project on this machine is listed in the user's **SciWeave home**,
`~/Documents/SciWeave/` (`sciweave projects where` prints it; `SCIWEAVE_HOME`
overrides it):

- `PROJECTS.md` there is a readable table of id, name and path. Read it (or run
  `sciweave projects`) whenever the user names a project ("the SLE project")
  or you are not inside one, rather than searching the disk.
- Act on a project from anywhere with `sciweave -C <path> <command>`.
- `sciweave open` serves the home dashboard (every project as a card, each one
  clicks through to its network); `sciweave open <id>` jumps straight to one.
  `sciweave serve` inside a project still serves just that project.
- A project that exists but isn't listed: `sciweave projects add <path>`.

## The import protocol (the core job)

When the user asks to add results from the current work to the project:

1. **Locate & read.** `sciweave status`, read `SCIWEAVE.md`. Know which nodes
   already exist (pipelines, scripts, inputs) so you link to them instead of
   duplicating.
2. **Inventory the session.** List what was produced and how: output files,
   the scripts/pipelines and commands that produced them, config files,
   parameters actually used (from the command line, config, or code).
3. **Ask only what matters** (one short message, numbered questions):
   - which outputs are *keepers* (final results) vs intermediates;
   - labels / numbering if it is going into an article ("Figure 2?");
   - anything ambiguous about parameters (e.g. which LD panel was really used).
   Don't ask what you can read from the files or the session.
4. **Classify every file:**
   - *keeper* → `mode: managed` (copied into the project, versioned);
   - *intermediate* (pipeline work files, logs, per-chunk outputs) → `mode: ref`
     only if it helps explain provenance, otherwise leave it out entirely;
   - raw data / big reference panels → `mode: ref` (never copy).
5. **Pick parameters for the links — only result-changing ones.** Thresholds,
   models, filters, reference panels, seeds, software versions that change
   numbers. *Not* font sizes, colours, dpi, figure size, titles. A link with
   parameters is red in the dashboard; without, green.
6. **Write a plan** (`sciweave plan template` shows the skeleton) to a
   temporary file, then:
   ```bash
   sciweave plan check plan.json     # read every WARN / ERROR
   ```
   Fix errors, show the user the check report + a 3–6 line summary, and
   **apply only after they agree**:
   ```bash
   sciweave --actor claude plan apply plan.json
   ```
7. **Verify & report.** `sciweave status`; tell the user the new ids
   (`T3 · Credible sets (finemapping)`), what is now stale, and suggest
   `sciweave serve` to see it.

## Adding a pipeline: code to GitHub, used results to the destination

A pipeline step records **only** the pipeline's pure code, its configuration and
parameters, and the results that later analyses actually used. When a step's
pipeline is added (Snakemake, Nextflow, a driver-script folder):

1. **Ask for the pipeline's GitHub repository** (one question; none yet → offer a
   new *private* repo). Clone it to a temporary folder.
2. **Compare local vs GitHub**: file list, content diff, last commit date vs the
   local files' dates. Same → record the repo commit. **Local newer** → prepare a
   cleaned push (step 3). GitHub newer → tell the user; never overwrite local.
3. **Clean, then push (only after the user agrees; private repos only):**
   - keep: entry file (`Snakefile`), rules, the scripts the rules actually call, helper
     modules, config loader, small *public* references the rules read;
   - turn study configs into generic `*.example.yaml` (parameters kept; dataset
     names, paths, sample counts, notes removed) and git-ignore the real ones;
   - never push: input data, results, sample-ID files (`.fam`, `.psam`, keep
     lists), logs, local paths, cohort / disease names, rsIDs or findings in
     comments, reports, ad hoc study scripts not called by any rule;
   - scan the cleaned copy for study names, IDs, paths and dates before committing;
     tag the previous remote version (`v<YYYY.MM>-legacy`) when replacing it.
4. **In the network:** one `pipeline` node (path = local entry file,
   `meta: {"repo": url, "commit": sha, "visibility": "private",
   "code": ["Snakefile", "rules", "scripts", "config"]}`). `code` is what "save to
   destination" copies for a pipeline (paths relative to the entry file's folder,
   caches skipped), so the pipeline is both **pushed** (GitHub, clean) and **saved**
   (destination, the local code incl. real configs); the dashboard shows both on the
   node (↑ badge, chips). Then `organize` the pipeline too. The real config snapshotted as a managed
   `input` (it stays in the project, never on GitHub); the run's result-changing
   parameters on each `produces` link, plus `params_file` = the config.
5. **Results: only what was used.** For every output, ask "did a later step or the
   article use it?" Keep those, plus the pipeline's own QC / run reports and small
   derived lists that define the run (e.g. the LD-pruned SNP set behind the PCA).
   Leave out intermediate filesets (QC'd / pruned genotypes), raw tool outputs that
   a formatted file replaces, logs and `.done` files.
   For example, gwassnakemake keeps the PCA (eigenvec / eigenval / covariates /
   selected-PC JSON / variance table and plots), the formatted summary
   statistics, the pruned SNP list, the QC/GWAS report and the Manhattan plot.
6. **Back up what is not backed up.** For each kept result check whether a
   verified copy already exists (`organized` in the destination, or managed in
   the project). If not, `organize` it into the destination. Say how big before
   copying gigabytes.

## Other everyday actions (always pass `--actor claude`)

| user intent | command |
|---|---|
| a file was regenerated (replaces the old) | `sciweave --actor claude save T1 -m "re-run with PC3" --why "residual stratification in QQ plot"` |
| a parameter changed for good | `sciweave --actor claude link PL2 T2 --rel produces -p L=5 --why "..."`, then save T2 after the re-run |
| an alternative to keep next to the original | `sciweave --actor claude branch new T2 "Credible sets (L=5)" path -p L=5 --why "sensitivity to L"` |
| the user picks the real one | `sciweave --actor claude branch main T2b --why "..."` |
| what changed since the last look | `sciweave suggest` (the `sciweave-monitor` skill runs this hourly) |
| cosmetic re-render only (fonts, colours) | `sciweave --actor claude save F1 --no-refresh -m "bigger font"` |
| stale, but checked: the changed input does not affect it (shared config gained another target; it was rebuilt from that input before the input was saved) | `sciweave --actor claude confirm R8 --parents IN34 --why "what was checked"` (no re-run, no new version; never for a real change) |
| this is the final version | `sciweave --actor claude final F2` |
| start an article | `sciweave --actor claude article new "Title"` |
| put a figure/table in the article | `sciweave --actor claude article place F2 A1 --as "Figure 2"` |
| refresh placed items after updates | `sciweave --actor claude article sync A1` |
| the author's LaTeX folder should become the article (kept in place) | `sciweave --actor claude tex attach <folder> "Title" --step article` |
| change the article's text | never edit the .tex: propose (`sciweave --actor claude tex propose A1 FILE --edits e.json -m "why"`); follow the **sciweave-article** skill |
| an object should not be in the network | `sciweave rm ID --dry-run`, show the user the list, then `sciweave --actor claude rm ID` (`--yes` if others go too) |
| a finding, caveat or open question about the project | `sciweave --actor claude pnote add "text" --about ID ... [--issue]` (issue = red ! on the network until resolved); `pnote resolve PN<n> "why"` |
| remember something about a node | `sciweave --actor claude note T2 "HLA excluded"` |
| what needs redoing | `sciweave status` (then regenerate in upstream order) |
| old version back | `sciweave restore F2 1 --out /tmp/F2_v1.png` |

## The analysis history (the guide)

Besides the network, a project keeps an **analysis history**: a tree of what
was done, starting from the raw / input data, each analysis with its branches
(sub-analyses, reruns, fixes), a 1–3 sentence summary, dates, result-changing
parameters, paths, the article items it feeds, and its own dated history. It is
the map you and the user walk through while organising a project, and it can
exist before any file is in the network.

```bash
sciweave analysis                      # the tree, numbered (1, 1.2, 1.2.1), ✓ = organized
sciweave analysis show meta.mrmega     # one entry: summary, params, paths, branches, history
sciweave analysis add qc "Genotype QC" --parent raw --summary "..." --start 2026-08-01 -p maf=0.01
sciweave analysis log qc "sex-check failures removed (n=12)" --date 2026-08-03
sciweave analysis link qc R3 T1        # its results in the network
sciweave analysis done qc              # mark organized once its results are recorded
sciweave analysis import guide.json    # bulk load (nested "children" or "parent" keys)
sciweave analysis rename input.qc prep.qc   # re-key (branches and @refs follow); --parent moves it
```

- One entry per independent thing: each received dataset (every cohort, every external GWAS,
  every reference) is its own input. Use `--group "Hispanic cohorts"` to label siblings that
  belong together; nest only real sub-analyses (reruns, fixes, parts of one analysis).
- Reconstruct it from evidence (session transcripts, logs, READMEs, file dates);
  mark anything inferred as such in the text. Keep reruns and bug fixes as their
  own branches: they *are* the history.
- When results of an analysis are added to the network, `analysis link` them and
  mark it `done` (organized). The dashboard's History › Analyses view shows it.
- **Focus (hide, never delete):** `sciweave analysis hide <key>` hides an analysis,
  its branches, and every object that exists only for it (listed in it, or used
  only by hidden items) plus their links; what comes next stays visible.
  `analysis unhide`, `analysis ls --all`, and `sciweave hide|unhide <IDs>` for single
  objects. When recording results of a hidden analysis, say so: they will not show
  in the network until it is unhidden. Hidden things still count for staleness.

## One file, one node (no duplicates)

The same file is often used by several tools (a 1000 Genomes panel used by FIZI *and* by
LYNXgwas), sometimes copied into several folders under other names. In the network it is
**one node**, linked to every analysis that used it — never one node per tool.

- Identity is **bit by bit** (SHA-256 of the content), not the file name or folder.
- Before adding a file: `sciweave dedup check <path>`. `plan check` does this for every plan
  node and **errors** on identical content ("use that node in edges instead"); `sciweave add`
  refuses it. A plan that contains the same file twice is also an error.
- Already duplicated? `sciweave dedup` lists identical content; merge with
  `sciweave --actor claude dedup merge <keep> <dup> --label "1000 Genomes EUR (FIZI, LYNXgwas)" --used-by FIZI LYNXgwas`:
  links, analysis entries and version parents move to the kept node, the other location is kept in
  `meta.also_at`, the users in `meta.used_by`; no file is touched.
- Partial overlap (a folder that contains a file already in the network) is a warning: prefer one
  node per shared file set (e.g. per-ancestry panels) so each analysis links to exactly what it used.

## Organized copies: the destination folder

A project can have a **destination** (`sciweave destination D:\my_study`): the clean, organized
copy of the work, while the messy working folder stays the source. `sciweave organize <IDs>`
(or double-click in the dashboard) copies an object to `<destination>/<NN_step>/[<group>/]`; the
folders follow the network, so moving an object to another step/group moves its copy. `status`
reports copies whose **source changed** since copying; `organize <ID>` again refreshes it.
Large data (genotypes, panels) copies run in the background from the dashboard; from the CLI they
block — say how big before copying gigabytes.

## Steps: every object belongs to an analysis step

The project is organised in ordered **steps** (e.g. `gwas` → `meta` → `finemapping` →
`functional` → `article`). The dashboard draws each step as an oval holding its objects,
with links drawn within and between steps.
- Check the existing steps first: `sciweave step ls` (or the Steps section of SCIWEAVE.md).
- Give **every node you add a `"step"`**, the stage that *produced* it. A figure made in
  the fine-mapping step belongs to `finemapping`. The article's own items (the final numbered
  figures/tables/supplements, sections) belong to the article-writing step.
- A new stage → define it in the plan's `"steps"` with a label and order (ask the user where
  it sits in the sequence if unclear). Never leave objects without a step.
- Fix an existing object: `sciweave --actor claude step set <step> <ID> [<ID>...]`.

## Sub-groups: sets inside a step

A step is the main group; inside it, **objects of one kind that belong together form a
sub-group** (a node's first group). Grouping is by what binds them, not only by
analysis: the 5 cohort genotypes that all feed the GWAS pipeline, the 4 external GWAS
sumstats, the 15 eQTL sets, the per-cohort Manhattan plots, the per-cohort pruned SNP
lists, a pipeline's config files. The dashboard draws each as an inner halo in the step
oval ("Sub-groups" selector: sets / same node type / off) and the destination copies
land in a sub-folder per sub-group.

- **Every import:** give each set of 2+ new objects of one kind a group in the plan
  (`"groups": ["Manhattan plots (per cohort)"]`); `plan check` warns about a set left
  ungrouped. Singletons need no group.
- **After any import or for an existing project:** `sciweave group --suggest` proposes
  sub-groups for ungrouped objects: first from the analysis history's `--group` labels
  (inputs received together), then *sets*: same step, same type and file kind, tied the
  same way to one process (all feed PL1 / PL1 produces all). Review the names, rename
  where the shared label prefix is not the real bond, then apply
  (`sciweave group "<name>" <IDs>`, or `--suggest --apply` when every name is right).
- Name the bond, plural and specific: `Hispanic cohorts`, `LD-pruned SNP sets (per
  cohort)`, `gwassnakemake configs`; not `misc`, `files`, `results`.
- Keep a set homogeneous: a summary folder of all cohorts (one PCA folder) is not the
  same kind as the 5 per-cohort files next to it; give each its own sub-group or none.
- Moving an object to another sub-group moves its organized copy too (same drive: a rename).

## Updates: always with a reason, and replace vs branch

Every saved version and every parameter change carries a **why** (the dashboard shows it
beside the automatic "what changed" list). Never invent it: ask. When a new result could
either replace an existing item or be an alternative to keep next to it (sensitivity
analysis, other LD panel, other cohort set), ask which, then `save` (replace) or
`branch new` (keep both). In plans, `save` entries take `"why"` and alternatives go in
`"branches": [{"of", "label", "path", "params", "why"}]`.

## Guardrails

- Never apply a plan the user hasn't seen. Never `--force` a restore without asking.
- Never copy raw data or huge files into the project (`plan check` warns; heed it).
- Labels are short and specific: `Credible sets (finemapping)`, not
  `final_table_v3_new`. The id (T3) is assigned by SciWeave.
- If a result's provenance is unknown, say so and link what *is* known;
  don't invent parameters.
- Keep parameters on the edge that actually used them (produces/derives), not
  on `code` or `part_of` links.
