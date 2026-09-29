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

No project yet? Ask where it should live, then `sciweave init <path> --name "..."`.

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
