---
name: sciweave-article
description: Write and edit a research manuscript kept as a SciWeave article (LaTeX), where every change Claude makes is a reviewable proposal the author accepts or rejects in the SciWeave LaTeX editor. Use whenever the task touches the paper's text — drafting or revising a section, abstract, title, figure/table legends, methods, results, discussion, references, responding to reviewers, checking the text against the latest results, or the user asks for "edits", "find/replace", "track changes" on the article.
---

# Writing a SciWeave article

The manuscript lives in an article node's folder (`<folder>/latex/main.tex` + `sections/*.tex`).
The author edits it in the SciWeave LaTeX editor (dashboard → Articles → **Open LaTeX editor**).
**Claude never writes the .tex files directly.** Every edit is a *proposal*; the author sees it as
track-changes and accepts or rejects each change. That is the whole contract — keep it.

## 1. Orient (every session that touches the text)

```bash
sciweave projects                         # which project; then work with -C <path>
sciweave -C <proj> tex files A1           # the article's files, which are nodes, what is waiting
sciweave -C <proj> tex proposals A1       # open proposals (don't pile new ones on the same text)
```
Then read, in the article folder: `README.md` and **`notes/claims.md`** (what the paper may and may not
claim, with sources). Read the section files you will touch — always from disk, right before proposing,
because the author may have edited them since you last looked.

## 1b. The working notes: four editor tabs (Claims, Structure, Suggestions, Ideas)

Next to **Manuscript**, the LaTeX editor has one tab per working note. They are Markdown files in the
article folder's `notes/`, shown rendered (Preview), as source (Edit) or side by side (Split). They are
**not part of the article**: never `\input` them into `main.tex` or put their content in the PDF.

| Tab | File | Holds |
|---|---|---|
| Claims | `notes/claims.md` | what the paper argues, in priority order, with the headline numbers and sources; supporting points; a **Do not claim** list |
| Structure | `notes/structure.md` | an outline of every part (title, abstract, introduction, each Methods/Results subsection, discussion, supplement): heading, one paragraph on what it must deliver, one line with items, state and SciWeave analyses; a **Gaps in the current draft** list |
| Suggestions | `notes/suggestions.md` | the evidence-backed backlog (1c) |
| Ideas | `notes/ideas.md` | free notes: framings, analyses to try, things to check; no evidence needed yet (move an idea to Suggestions once a result backs it) |
| Removed | `notes/removed.md` | text taken out of the manuscript at the author's request: the exact LaTeX, where it was, why, and its evidence rows (kept in evidence.tsv), so it can be put back |

Rules:
- Notes are not the manuscript: write them directly (Edit/Write tools), no proposals. The author edits them
  in the tab; the editor reloads when you change a file, and warns them if both of you edited it.
- Read a note from disk right before changing it (the author may have edited it in the tab).
- Create claims/structure when an article is set up; update them whenever a claim, a section, or the set of
  figures/tables changes, and after any review or fix round. Recompute the word counts and the "never
  cited" items from the text before updating Structure.
- **How Structure is written.** Not a table: an outline that reads as headings. Every part of the article is a
  heading (`##` for Title, Abstract, Introduction, Material and Methods, Results, Discussion, Supplemental
  Material; `###` for each subsection), and under each heading comes **one paragraph** saying what that part
  must deliver (the claim, the analyses, the key numbers it rests on), then **one line**:
  `*Items:* <figures/tables/supplements it cites> · *Now:* <word count, what is missing> · *SciWeave:* <analysis keys>`.
  A parent heading (e.g. Material and Methods) gets its own summary paragraph before its subsections. End with
  a `## Gaps in the current draft` list.
- **Structure describes the accepted text, not proposals.** Every time you start work, and before you report,
  check `tex proposals A1`: when proposals you made have been accepted (or rejected), recompute the word counts
  per subsection, the cited / never-cited items and the gaps from the current .tex files, and rewrite every
  affected `*Now:*` line and the parent summaries. Never leave "proposed" wording for text that is accepted.
- **Build Methods from SciWeave, not from the current text.** List the analyses with `sciweave analysis`
  (and `analysis show <key>` for summaries and parameters); every analysis whose numbers reach Results,
  a table or a figure needs a Methods subsection (or a named place in the Supplemental Methods). Mark the
  ones the text leaves out or buries, so missing Methods are visible in the Structure tab.
- An older article may still `\input` `sections/claims.tex` / `sections/structure.tex` planning pages:
  move their content into the notes and propose removing the `\input` block from `main.tex`.

## 1c. The suggestions backlog (`notes/suggestions.md`)

While writing, checking or fixing, you will notice things the article *could* add: a claim the data
supports, an analysis worth one sentence, a table never cited, a better framing, an open question. **Do not
put them in the text.** Write each one to `notes/suggestions.md` (create it if missing; it is a notes file,
not the manuscript, so write it directly) and keep going.

- One entry per idea: **Claim** (one or two sentences, with the numbers), **Strength** (strong = direct
  result; moderate = needs careful wording; weak = only with caveats), **Where it would go**, **Evidence**
  (asset key or file, sheet/columns, a `where` locator; say if a MetaData extract script is still needed),
  **Rank** (left for the author), **Status** (open / ranked / drafted as Pnn / declined).
- Check every number against its source before writing the entry; mark anything approximate as such.
- Questions for the author go at the end under "Open questions", not as claims.
- At the end of a working round, show the open entries as a short table and let the author rank them.
  Only the chosen ones are drafted, as proposals with `\ev` markers; update their Status. Never add an
  entry to the text without that ranking step, and never delete entries (mark them declined).

## 2. Propose, never write

```bash
# exact edits (preferred): each "find" must occur exactly once, or give "nth"
sciweave -C <proj> --actor claude tex propose A1 sections/results.tex --edits edits.json -m "why + source"
# a short one-off edit
sciweave -C <proj> --actor claude tex propose A1 sections/abstract.tex --replace "old text" "new text" -m "..."
# a new or fully rewritten file
sciweave -C <proj> --actor claude tex propose A1 sections/discussion.tex --from draft.tex -m "..."
```
`edits.json`: `[{"find": "...", "replace": "..."}, {"find": "...", "replace": "...", "nth": 2}]`
(write it to the scratchpad, not into the article folder).

- **One proposal = one reason.** Group edits by topic and file ("update PRS numbers to Table 8",
  "local ancestry in both directions"), so accepting or rejecting one doesn't force the other.
- The `-m` message is what the author reads first: say *why* and *from where* ("Table 8 / ST9: AUC CI is DeLong").
- If `propose` says *not found* / *occurs N times*: re-read the file and fix the find text. Never loosen it
  to something that could match the wrong place.
- Never run `tex accept` / `tex reject` / `tex withdraw` on the author's behalf unless they say so in this
  conversation. After proposing, tell them the ids waiting (`P7, P8`) and what each does.
- Don't use the Edit/Write tools on files under `latex/` (except `numbers.tex` via its generator script).

## 3. Numbers and claims

- Every number in the text must come from a results table, figure legend, or `numbers.tex`. Check it
  against the source before proposing; never round differently from the table without saying so.
- Prefer the macros in `latex/numbers.tex` (`\AUCJoint{}`, `$\PJointvsEur$`). It is **generated** from the
  results tables by a script named at its top: if a number you need is missing, extend that script and
  rerun it; never hand-edit `numbers.tex`.
- Check every claim against `notes/claims.md`. If the text contradicts it, propose the fix; if the results
  have moved on and claims.md is outdated, tell the author rather than silently following either.
- Citations: only `\cite{rN}` keys that exist in `sections/references.tex`. A new reference goes in as its
  own proposal with a complete, real citation (authors, title, journal, year, DOI you have verified).
  Never invent a reference or DOI.
- Figure/table numbers must match the files in the figures/tables folders and their legends.

## 3b. Evidence numbers and linked items

The article folder keeps what supports the text next to it: `Figures/ Tables/ Supplementary/` (synced
copies, mapped in `assets.tsv`), `CitedArticles/` (the papers' PDFs, `index.tsv` maps r1.. to files),
`MetaData/` (files made to back one claim) and `evidence.tsv` (the register). See it with
`sciweave -C <proj> tex evidence A1 status`.

- **Every new claim that rests on something gets an evidence number.** Add a row to `evidence.tsv`
  (`id  target  page  where  quote  claim  note`) and put `\ev{E7}` right after the claim in your proposal.
  `where` points the viewer at the supporting rows: `sheet=<name>; find=Loc-56|rs123` (tables, TSV),
  `row=12`, or `find=B` (figure panel). Clicking the marker opens the file in the SciWeave viewer with
  those rows highlighted (PDFs open at the page, with the quote highlighted).
  `evidence.tsv`, `assets.tsv` and MetaData files are not the manuscript: you may write them directly.
  - from a cited paper: `target` = `r1`, `quote` = the exact sentence from the PDF (copied from its text,
    so the highlight finds it), `page` if known. The build highlights it in `CitedArticles/annotated/`
    with a note "E7: claim"; it never alters the original PDF.
  - from our results: `target` = an asset key (`T8`, `ST10`) and the row/sheet in `note`; or a file in
    `MetaData/` made by a script in `scripts/manuscript/evidence/` (so it can be rerun). Never type
    numbers into a MetaData file by hand except values copied from a cited paper (say so in the file).
- A claim you cannot back (no source found, or it says more than the evidence shows) gets **no** marker:
  record it in `evidence_gaps.tsv` (`file  anchor  kind  claim  note  cause`; kind = unsourced | unsupported).
  When the author gives an accepted reason it will stay without evidence (records not kept, author's own
  knowledge), put it in `cause`: the item turns green ('Missing · cause known') and no longer counts as open
  so it shows in the editor's **Missing** tab, next to mismatches, evidence whose file is missing, and
  sentences with numbers but no marker (found automatically). Never mark a claim just to empty that tab.
- Figure/table mentions: `\figref{6}`, `\figref[B]{6}`, `\tabref{8}`, `\tabref[b]{2}`, `\supref{ST10}`
  print the label from `assets.tsv` and link the file. A key missing from assets.tsv is an error.
- A typeset table (`tabular`) is evidenced as a whole by one marker in any of its rows (put it in the header
  row, e.g. `Cohort\ev{E201} & ...`); its other rows are then not listed as unmarked.
- Not inside `\hl{...}` (soul cannot take macros with arguments); leave those as plain text.
- Run `tex evidence A1 build` and read every `!` line: a quote not found, a missing PDF, an id used in
  the text but not registered. Fix them or tell the author; never leave a red `E?` in the PDF.
- The submission build (`tex compile A1 --submission`, button "Submission PDF") drops every marker and
  link: the journal copy. Never remove `\ev` markers to "clean" the text.
- Paywalled papers: never fetch them from shadow libraries; list them for the author to add by hand.

## 3c. Working through problems (the Missing tab, a review, a list of issues)

Before fixing anything, sort the list into tiers by **time and data readiness**, show the tiers to the author
in one short table, then work front to back:

1. **Quick fixes**: wording, a marker, a pointer, hedging an overreach, a number already in a results
   table, a quote from a PDF already in `CitedArticles/`. Minutes each; do these first, in batches.
2. **Data on hand**: needs a small script over files already on disk (a MetaData extract, recomputing a
   statistic from a local result file, reading the cluster share). Do next.
3. **Needs the author**: a decision only they can make (a counting rule, which claim to keep), a missing
   record, a PDF to download by hand. Ask these together, not one per turn, and keep going with others.
4. **Long or heavy**: a new analysis, a rerun, a cluster job, a long script, regenerating tables/figures
   with their downstream (archive, SciWeave save, stale items). Leave these to the end, say how long
   they take before starting, and never let them block the quick fixes.

Within a tier, group items that touch the same sentence or source so one proposal fixes them together.
If an item turns out to be heavier than its tier, move it down and say so.

## 4. LaTeX hygiene (the proposal must compile)

- `\%` for percent, `--` for ranges, `$...$` for math: `P = $1.2 \times 10^{-8}$`, `I$^2$`, `$\lambda_{\mathrm{GC}}$`.
- Balanced braces; no Unicode math characters (× ≤ ⁻ ²) — pdflatex chokes on them.
- Keep the author's `\hl{...}` to-do highlights unless the edit resolves that to-do.
- After the author accepts, compile and report errors: `sciweave -C <proj> tex compile A1`.

## 4b. Writing or improving any part (section, paragraph, sentence, legend)

When asked to write, fix or improve any part of the text, the job is not only the wording. For every
sentence of the new version, check and add what it needs:
1. **Citations.** Every cohort, dataset, method, tool, reference panel, criterion and prior finding gets
   its `\cite{rN}`. A missing reference is added (own proposal on `references.tex`, complete citation, DOI
   verified, e.g. against Crossref) and its PDF listed for `CitedArticles/` if it cannot be downloaded.
2. **Evidence.** Every number and factual claim gets `\ev{E#}`: reuse an existing row when it already
   covers the claim (check its `claim`, `where` and `note`), otherwise add a row; what cannot be backed goes
   to `evidence_gaps.tsv` (with `cause` if the author has given one).
3. **Figures and tables.** Mention the item that shows the claim (`\figref`, `\tabref`, `\supref`) at
   the first sentence it supports; prefer items not yet cited when they fit, and keep first citations in
   numerical order.
4. **Detail level.** Detail the journal will not take goes to the supplement with a pointer (see the
   `sciweave-supplement` skill).
5. **Writing.** Audit the draft with the `avoid-ai-writing` skill (technical voice) before proposing (5).
6. **Consistency.** Check against `notes/claims.md`, the other sections (no repeated paragraphs between
   Methods and Results) and the Structure tab; update Structure afterwards.
Report in the reply which citations, evidence rows, item mentions and gaps were added, and anything left
for the author (a missing source, an ethics statement, a decision).

## 5. Style

Follow the target journal (see the article README; AJHG: concise, past tense for results, numbered
citations). Use the project's terminology consistently (locus names, cohort names, analysis names in
claims.md). Prefer short sentences; one claim per sentence with its number.

**No AI fingerprints.** Before proposing any new or rewritten prose (a section, abstract, legend, cover
letter, reviewer response, a backlog entry that will be pasted into the text), load the `avoid-ai-writing`
skill (https://github.com/conorbronsdon/avoid-ai-writing; installed at `~/.claude/skills/avoid-ai-writing`)
and audit your draft with it (`technical` voice) before proposing. In particular:
- no "delve, leverage, robust, pivotal, crucial, underscore, landscape, intricate, notably, furthermore,
  moreover, additionally"; no "serves as / stands as" where "is" will do;
- no significance inflation ("a major step", "sheds new light"), no formulaic openers or closers
  ("In recent years...", "Taken together, these findings pave the way..."), no rule-of-three lists for rhythm;
- em dashes rarely (use commas, parentheses or a new sentence); no bold or italics for emphasis in prose;
- keep the source's level of certainty: hedge as much as the evidence needs, no stacked hedges
  ("may potentially").
The skill's guardrails apply too: never invent a number, citation or detail to make a sentence read better,
and treat its pattern list as signals, not rules. Terms the field uses with a precise meaning ("robust to",
"significant", "enrichment") stay. Audit the author's own text only when asked (detect mode: report findings;
any change still goes through `tex propose`).

**Methods.** Whenever the work touches the Material and Methods (writing, improving, merging, auditing a subsection), load the `sciweave-methods` skill: it decides what stays in Methods, what goes to the Supplemental Methods and what belongs to Results or Discussion (no results, gene names or result figures in Methods; equations and per-item detail in the supplement).

**The data.** Whenever text mentions cohorts, samples or sample sizes, load `sciweave-data-description`:
the data are one final dataset, with no timeline of cohorts added, removed or run separately.

**Main figure and table legends.** Load `sciweave-legends`: they describe only what is shown (panels, encodings,
thresholds, abbreviations), with no results or discussion, and stay short.

**The supplement.** The Supplemental Material is its own PDF (detailed methods, supplemental results,
supplementary figures and tables with legends, evidence, references). Whenever the work touches it, or a
main-text sentence needs more detail than the journal allows, load the `sciweave-supplement` skill: it
sets the supplement's structure and how it links to the main text.

## 6. Keeping SciWeave in sync

- When results the text depends on are rerun: regenerate `numbers.tex`, compile, and propose text changes
  for anything whose wording (not just number) changed.
- New figure/table in the article: record it in the network with the import protocol (`sciweave` skill:
  plan → check → user approval → apply), linking it `part_of` the article.
- For co-authors who need Word: `pandoc main.tex -o build/main.docx` from the latex folder (math and
  citations survive; layout does not).
