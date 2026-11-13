---
name: sciweave-legends
description: Write or revise the legends of the MAIN-ARTICLE figures and tables of a SciWeave article (figure legends, table titles and table footnotes) so they describe only what is shown - panels, data, encodings, symbols, thresholds, abbreviations - with no results, no discussion and no repetition of the Methods; enough to read the figure, not short, not long. Use whenever a main figure legend or main table title/footnote is drafted, revised, shortened or audited. Does not apply to supplementary figures and tables.
---

# Legends of the main-article figures and tables

Everything in `sciweave-article` applies (deliverable files are built by scripts, the manuscript changes only by
proposals, avoid-ai-writing, `sciweave-data-description`). Scope: the figures and tables that appear in the main
article only. Supplementary legends keep their own, longer style (`sciweave-supplement`).

## 1. What a legend is

A legend tells the reader how to read the figure or table, not what it shows. It answers: what data and which
analysis, what each panel is, what each axis, color, symbol, line and threshold means, which items are
labeled and why, and what the abbreviations stand for. The Results say what was found.

| In the legend | Not in the legend (it goes to the Results or Discussion) |
|---|---|
| "Manhattan plot of His-METAL"; "(B) Miami plot of METAL-9 (top) and MR-MEGA-9 (bottom)" | "His-METAL identified 30 loci, 4 of them novel" |
| encodings: "red dashed line, P = 5 × 10⁻⁸"; "filled points pass both tests" | counts of what passed: "42 of 109 loci", "two cell types reached FDR < 0.05" |
| definitions of groups or rules: "attenuated, significant in His-METAL only" | which loci fell in a group, unless they are the items the panel labels |
| the items shown, named to identify them: "Loc-2 (RERE) and Loc-68 (CDHR5)" | what they turned out to be: "whose links point to IRF7", "supports an Indigenous American risk locus" |
| the input of a panel when needed to read it: "held-out LAMR cohort, excluded from all training data" | outcome statistics: λ values, AUCs, P values, h², power at the current N |
| where the full numbers are: "per-cohort values in Supplementary Table ST14" | interpretation, comparisons, "notably", "suggesting" |

Numbers that are part of the design (thresholds, window sizes, permutation counts, score cut-offs) stay. Sample
sizes appear once, in Table 1 or Figure 1, not in every legend (see `sciweave-data-description`).

## 2. Shape and length

- Title: one line stating the content, not the finding ("Hispanic-dominated loci: significance, allele frequency,
  local ancestry, power and polygenic prediction", not "Hispanic-dominated loci are missed by cross-ancestry
  meta-analysis").
- Then one short paragraph per panel, "(A) ...", in panel order; a shared sentence for panels with the same
  encoding ("In (A) and (B), ..."); the abbreviation list last.
- Aim for about 120-200 words per figure (more only for a multi-panel figure whose encodings need it); table
  footnotes as short as the definitions allow. Cut every word that does not help reading the figure.
- AJHG / Cell Press style: past tense is not needed (describe what is shown); define every abbreviation used in
  the figure or table; P written "P < 5 × 10⁻⁸"; locus labels "Loc-N (GENE)".

## 2b. Enough, not short, not long (author rule, 2026-11-11)

The legend must be enough to read the figure on its own, and no more:
- **Keep** what the reader needs to read what is drawn: what each panel plots, what every color, outline, line,
  symbol, label style and threshold means, and the one input a panel cannot be read without.
- **Do not repeat the Methods.** How the data were made (score cut-offs, window lists, permutation and correction
  procedures, model specifications, file-level definitions such as "best score, the highest score of a cell
  type's links whose element overlaps the window") is in the Methods; the legend names the quantity in plain
  words ("fold enrichment", "best scE2G score", "specificity q") and stops. A shared opening paragraph that only
  restates the Methods is cut.
- **Every visual encoding is named once**: if a cell is outlined, say the outline's color and meaning ("red
  outline, specificity q < 0.05 in that window"); if a figure is changed (a color, a line removed), the legend
  changes with it in the same proposal.
- **Panel labels are explicit**: "Top panel:" / "Bottom panel:" rather than a bare "Top," / "Bottom,".
- Test: delete a sentence; if the reader can still read every mark in the figure, the sentence goes.

## 3. Tables

- Title above the table: content only.
- Footnotes: what the rows are and how they are ordered, what each non-obvious column means, the rules behind
  bold or marked cells, footnote letters for specific cells, then abbreviations. No sentences that report a result
  ("most loci were ...").

## 4. Applying it

1. Read the legend source (the file the build script installs, never the article copy only) and mark each
   sentence: keep (how to read), move (result -> Results, if the Results do not already say it), or cut.
2. Rewrite in the source, rebuild the deliverable files with their script (archive first), regenerate the
   manuscript section with its generator, and propose it.
3. Run the AI-writing check and report the word count before and after for each legend.
4. If a result removed from a legend is not in the Results, tell the author (do not drop findings silently).
