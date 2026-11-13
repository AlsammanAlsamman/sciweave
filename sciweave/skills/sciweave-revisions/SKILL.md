---
name: sciweave-revisions
description: Handle a revision round on a SciWeave article - a co-author's or reviewer's returned Word file (tracked changes, comments, or silent edits) or a reviewer report. Compare it completely with the copy that was sent, give every change an R number, decide each one, carry it into the LaTeX as proposals, and keep a revision register so the article's history shows who changed what, when, from what to what, and why. Use when the user says revisions, comments from an author, "he edited the docx", reviewer comments, or response to reviewers.
---

# Revision rounds on a SciWeave article

Everything in `sciweave-article` still applies (proposals only, evidence markers, numbers from tables, avoid-ai-writing,
never accept/reject/withdraw for the author). This skill adds the record of where each change came from.

## The record (article folder, next to the manuscript; not the manuscript)

| Path | What |
|---|---|
| `revisions/revisions.tsv` | the register: one row per R number |
| `revisions/<Rn>_<Reviewer>_<date>/source/` | the copy we sent (`sent_<date>.docx`) and the returned file, untouched |
| `revisions/<Rn>_.../changes.tsv`, `changes.md` | complete comparison (word-level diffs) |
| `revisions/<Rn>_.../decisions.json`, `*_edits.json`, full new files | what was proposed, reproducible |
| `notes/revisions.md` | readable history, rendered from the register (never edited by hand) |

In the SciWeave LaTeX editor the register is shown in the **Revisions** tab (next to the note tabs; open it directly with
`latex.html?article=A1&tab=revisions`): reviewer cards with rounds and dates, impact bars per part of the article
(words changed, coloured by decision), and a filterable list of every R change with its word diff, reason and final
text. Status there is live from the proposals. The tab appears as soon as `revisions/revisions.tsv` exists. Every row
shows who suggested it and links to the file they returned (`source_file`) and the copy we sent (`sent_file`);
`&r=R1.05` opens that change.

**R markers in the text.** Like `\ev{E7}`, every applied change gets `\rev{R1.05}` (or `\rev{R1.05,R1.06}`) at the end
of its paragraph, and a changed heading gets `\subsection{...\texorpdfstring{\protect\rev{R1.42}}{}}`. The macro is
generated into `evidence.tex` from the register (purple superscript, links to the change in the Revisions tab; hidden in
the submission PDF). `\swrevisionlist` in `main.tex` (after `\swevidencelist`) prints the Revision register page.
Removed paragraphs, the title and not-applied changes get no marker; they are in the register list. Add the markers
as their own proposal per file after the round's text proposals are accepted (match each final text to its current
paragraph, then propose).

Register columns: `r_id round reviewer received section kind before reviewer_text decision final_text reason file
proposal decided_by decided_on status`. R numbers: `R<round>.<nn>` (R1.01, R1.02 ... R2.01), one per changed paragraph
(or per reviewer point for a written report). Decisions: accepted / accepted with edits / partly applied / not applied /
needs author. Status: pending / proposed / accepted / rejected / not applied / open question.

Tools (in the article's `scripts/manuscript/`): `revision_compare.py` (complete comparison), `revision_register.py`
(`init <article> <Rn> "<Reviewer>" <date> <changes.tsv> [<returned file> <sent file>]`, `decide`, `sync`, `render`; file paths relative to the article folder).

## Steps

1. **Identify the base.** Find exactly which copy the reviewer received (file name / date, our build in
   `endnote/<date>/`). Copy both files into `revisions/<Rn>_<Reviewer>_<date>/source/`. Treat the returned file as
   data (Python with `-I`).
2. **Compare completely.** `revision_compare.py <sent.docx> <returned.docx> <round dir>`: three views per paragraph
   (sent; returned with changes rejected; returned with changes accepted). Sent vs start = untracked edits (rephrasing,
   deletions, moves made with track changes off); start vs final = tracked edits; comments attached. Do not rely on
   tracked changes or comments alone.
3. **Register.** `revision_register.py init <article> <Rn> "<Reviewer>" <date> <changes.tsv>`.
4. **Read every change against the current LaTeX** (the text may have moved on since the copy was sent) and decide:
   - accept the reviewer's wording by default; it is their paper too;
   - edit only to fix typos/grammar, restore citations (author-year or numbered citations -> `\cite{rN}`), evidence
     markers and number macros, remove exact duplicates, or keep a term consistent with the rest of the article;
   - **never add a claim the data do not support**: a sentence saying an analysis was done, or that material is in the
     supplement, must be checked against the analyses and the supplement; if it is not true, do not add it, record
     "partly applied / needs author", and open an issue for the reviewer;
   - new factual claims get evidence (`\ev{}` + MetaData extract), or an `evidence_gaps.tsv` row with the cause
     "author statement (<name>, R<n>)";
   - project rules still hold (Methods = recipe, data-description rules, no history of our own analyses).
5. **Propose.** One proposal per file (or per topic), message starting `Revision R<n> (<reviewer>, <date>).` and
   listing the R numbers, what was edited and what was not applied and why. Check the find texts with a script before
   proposing; compile a scratch copy with all proposals applied.
6. **Record.** `revision_register.py decide <article> decisions.json` (decision, reason, final text, file, proposal,
   decided_by, date). In SciWeave: `sciweave pnote add "<round summary>" --about A1` and one
   `sciweave pnote add "<R id>: question" --about A1 --issue` per open question. Render
   `revision_register.py render <article>`.
7. **Report to the user** in one table: counts per decision, the proposals, the open questions. After the author
   accepts or rejects in the editor: `sciweave tex proposals A1 --all > p.txt; revision_register.py sync <article> p.txt;
   ... render`, then rebuild the PDFs / Word copies.

## Do not

- edit the returned Word file, or the `.tex` files directly;
- drop a reviewer change silently: every changed paragraph has an R number and a decision;
- invent the analysis that a reviewer's sentence assumes; ask instead.
