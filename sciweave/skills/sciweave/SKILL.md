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

## Other everyday actions (always pass `--actor claude`)

| user intent | command |
|---|---|
| a file was regenerated (replaces the old) | `sciweave --actor claude save T1 -m "re-run with PC3" --why "residual stratification in QQ plot"` |
| a parameter changed for good | `sciweave --actor claude link PL2 T2 --rel produces -p L=5 --why "..."`, then save T2 after the re-run |
| an alternative to keep next to the original | `sciweave --actor claude branch new T2 "Credible sets (L=5)" path -p L=5 --why "sensitivity to L"` |
| the user picks the real one | `sciweave --actor claude branch main T2b --why "..."` |
| what changed since the last look | `sciweave suggest` (the `sciweave-monitor` skill runs this hourly) |
| cosmetic re-render only (fonts, colours) | `sciweave --actor claude save F1 --no-refresh -m "bigger font"` |
| this is the final version | `sciweave --actor claude final F2` |
| start an article | `sciweave --actor claude article new "Title"` |
| put a figure/table in the article | `sciweave --actor claude article place F2 A1 --as "Figure 2"` |
| refresh placed items after updates | `sciweave --actor claude article sync A1` |
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
