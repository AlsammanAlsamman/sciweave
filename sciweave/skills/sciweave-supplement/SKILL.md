---
name: sciweave-supplement
description: Structure, write and link the Supplemental Material of a SciWeave LaTeX article as its own PDF (detailed methods, supplemental results, supplementary figures with legends, supplementary tables, references, evidence markers). Use whenever the task touches the supplement - "supplementary methods", "supplemental material", "supplementary figure/table legend", moving detail out of the main text, adding a detail to the supplement while writing, or checking that main text and supplement agree.
---

# The Supplemental Material (a second PDF next to the article)

The supplement is a **separate document** with its own PDF. It holds what the main article points to but
cannot carry: the full methods, detailed results, and every supplementary figure and table with its legend.
It grows while the article is written: whenever a sentence in the main text needs more detail than the
journal allows, the detail goes to the supplement and the main text points to it.

Everything in the `sciweave-article` skill applies here too: Claude **proposes** (`sciweave tex propose`),
never writes the .tex files; every number has an evidence marker; citations use the article's `\cite{rN}`
keys; audit new prose with `avoid-ai-writing`; the author's rule "change article" covers the supplement too.

## 1. Files and build

```
latex/supplement.tex              the supplement's own main file (title, contents, \input of the parts)
latex/supplement/methods.tex      S1 ... Supplemental Methods
latex/supplement/results.tex      Supplemental Results (detailed results, sensitivity analyses)
latex/supplement/figures.tex      Supplemental Figures, one per page, each with its full legend
latex/supplement/tables.tex       Supplemental Tables (typeset small ones; describe the Excel ones)
latex/supplement/references.tex   references cited in the supplement only
```
- `supplement.tex` uses the same preamble as `main.tex` (packages, `\graphicspath`) and inputs the same
  `evidence` and `numbers` files, so `\ev{E7}`, `\supref{ST10}`, `\figref{6}` and the number macros work
  unchanged. No line numbers needed; single spacing is fine unless the journal says otherwise.
- Numbering: sections `S1, S2, ...` (`\renewcommand\thesection{S\arabic{section}}`), figures and tables
  with the labels in `assets.tsv` (e.g. Figure S4, Table ST10). The printed number of every figure and
  table must match its `assets.tsv` label; check it after each change.
- Build: the PDF is `latex/build/supplement.pdf`. The journal copy hides the evidence markers (job
  `supplement_submission`). If SciWeave's editor cannot compile it as its own job yet, say so and offer to
  add that to SciWeave rather than working around it (never compile by hand into the article folder).
- Until the supplement exists as its own file, an older article may keep a `sections/supplemental.tex`
  inside `main.tex`: setting it up means creating the files above (as proposals) and proposing removal of
  `\input{sections/supplemental}` from `main.tex`, as separate proposals.

## 2. What goes in each part

**Title page.** The article title, "Supplemental Material", authors as in the article, and a table of
contents (`\tableofcontents`) listing every section, figure and table.

**Supplemental Methods (S1, S2, ...).** One section per Material and Methods subsection of the main text,
**same order and same heading**, so a reader can go from one to the other. Each section:
- opens with one sentence saying which main-text subsection it extends;
- then `\subsection`s (and `\subsubsection`s where useful) for the detail: data and samples (with counts),
  tools and versions, every result-changing parameter and threshold, the statistical model and test, the
  multiple-testing rule, sensitivity analyses and what they changed, and the code location;
- takes its content from SciWeave: `sciweave analysis show <key>` gives the summary, parameters, tools and
  history of each analysis; the Structure tab lists which analyses belong to which subsection;
- describes only what was used for the reported results. Superseded runs appear only when a reader needs
  them to understand a choice (e.g. why 3 PCs instead of the 80%-variance default), and are named as such.

**Supplemental Results.** Detailed results that support a main-text claim but do not fit there:
per-locus results, sensitivity and robustness analyses, negative results the reader may ask about. One
section per main Results subsection it extends, in the same order, opening with that link; each paragraph
cites the supplementary figure or table that shows it.

**Supplemental Figures.** One figure per page: `\includegraphics` of the synced file, then the legend. The
legend has a one-sentence title, one sentence per panel (what is plotted, axes, colours, n), the test and
threshold behind any star or line, and the data source (the table or analysis). If the figure has a
`.legend` asset, that file is the legend's single source: input or copy it, never write a second version.
A figure that the main text cites only as a figure (no supplemental text) still gets its full legend here.

**Supplemental Tables.** Small tables are typeset (booktabs). Large ones stay Excel files (`ST*.xlsx`):
the PDF gives the table number, title, a legend describing every column and abbreviation, and the file
name, so the PDF and the spreadsheets together are complete.

**Supplemental References.** Only the references cited in the supplement, with the same `rN` keys as the
article. A reference new to the supplement is added as its own proposal with a complete, verified citation.

## 3. Linking main text and supplement

- **Main text to supplement.** Every Methods subsection that has a supplement section ends with a pointer:
  "Details are in Supplemental Methods S3." Use cross-document references (`xr-hyper`,
  `\externaldocument[sm-]{build/supplement}`) and labels such as `\label{sm:local-ancestry}` so the
  number follows the supplement; never type "S3" by hand. Figures and tables are cited with `\supref{KEY}`.
- **Every supplementary item is cited in the main text at least once**, and numbered in order of first
  citation (most journals require both). After any change, recompute which items are never cited and which
  are out of order, and record it in the Structure tab's gaps.
- **No copied text.** The main text gives the essentials in its own words; the supplement gives the detail.
  When a detail moves out of the main text, the move is two proposals (one per file) with the same `-m`
  reason, so the author sees both halves.
- **Numbers agree.** A number that appears in both documents uses the same `numbers.tex` macro or the same
  evidence row.

## 4. Evidence and citations

- Same rules as the main text: every factual claim with a number gets `\ev{E#}` pointing at its table,
  sheet and row (`evidence.tsv`); unbacked claims go to `evidence_gaps.tsv`. The Missing tab audits the
  supplement files too (they live under `latex/`).
- Methods details (versions, parameters) are evidenced by the SciWeave analysis or the script/config that
  set them: add an evidence row whose target is that file (or a MetaData extract) when the value is not in a
  results table.
- Cite every software tool and reference panel at first use in the supplement.

## 5. Adding to the supplement while writing

- While drafting the main text, when a sentence would need detail the journal will not take (a parameter
  list, a sensitivity analysis, a per-cohort breakdown), propose the short main-text sentence plus the
  pointer, and propose the detail in the right supplement section. Do not leave detail out because the
  supplement section is not written yet: create it.
- When a new supplementary figure or table is made: register it in `assets.tsv` (key, label, file,
  legend), place it in `figures.tex` / `tables.tex` in citation order, cite it from the main text, and record
  it in SciWeave (`sciweave` skill import protocol, linked `part_of` the article).
- Keep the Structure tab (`notes/structure.md`) in step: its `## Supplemental Material` part has one `###`
  per supplement section (heading, one paragraph on what it holds, one line with items, state and SciWeave
  analysis keys), like the rest of the outline.

## 6. Before calling it done

- Compile both PDFs; read every `!` line of `tex evidence A1 build`.
- Check: section order mirrors the main Methods and Results; every main-text pointer resolves; every
  figure/table number matches `assets.tsv` and the citation order; every legend is complete; no orphan
  items; the submission build hides all markers.
