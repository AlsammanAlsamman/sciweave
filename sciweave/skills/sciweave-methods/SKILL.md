---
name: sciweave-methods
description: Write, revise or audit the Material and Methods of a SciWeave article and decide what goes in the main Methods, the Supplemental Methods, the Results or the Discussion. Use whenever the task touches Methods text - drafting or improving a Methods subsection, "add the missing parameters", merging or splitting Methods subsections, moving detail to the supplement, or checking Methods for result content.
---

# Writing Methods (main text and Supplemental Methods)

Everything in `sciweave-article` applies (proposals only, evidence markers, citations, avoid-ai-writing).
This skill decides **what belongs where**. Methods answer one question: *what was done, to which data,
with which tool and settings*, so a reader can judge and repeat it. They never say what came out.

## 1. The placement test (apply to every sentence)

| The sentence states... | It goes to |
|---|---|
| data used (cohorts, sample sizes **as inputs**, reference panels, genome build) | Methods |
| tool, version, model, test, threshold, multiple-testing rule, software | Methods |
| a key, result-changing parameter a reviewer would check (K, window, r², α, prevalence) | Methods |
| a rule that selects things ("loci with 5 × 10⁻⁸ < P < 5 × 10⁻⁶", "variants present in ≥ 2 cohorts") | Methods |
| an equation, a full parameter list, per-cohort / per-locus settings, file-level provenance, convergence, CV errors | **Supplemental Methods** |
| why a choice was made, sensitivity analyses and what they changed, reruns and fixes | **Supplemental Methods** (one Methods sentence may name the sensitivity analysis) |
| what the analysis found: counts of loci, AUCs, λ values, rg, h², P-values, "K = 5 fitted best" values, per-locus power | **Results** (or Supplemental Results) |
| which genes / loci showed something | **Results** |
| interpretation, implications, limitations | **Discussion** |
| the history of working lists ("selected from 116 candidate regions") | nowhere; state the final rule |

## 2. Specific rules

- **No results in Methods.** No outcome numbers, no "the axes accounted for 57%...", no "K = 5 had CV
  error 0.053". Write the rule ("the K with the lowest cross-validation error was used") and put the
  values in the Supplemental Methods or Results.
- **No gene or locus names** unless the gene is itself an input of the method (a candidate gene chosen
  before the analysis). Refer to sets by their defining rule or by the name the Results defines
  ("the Hispanic-dominated loci", "the novel loci"), never by listing what was found.
- **No result figures in Methods.** Do not cite main-text figures or tables that show results
  (`\figref{3}`, Table 8 AUCs). Allowed: items that document inputs or settings (Table 1 cohort
  description, a supplementary table of parameters), and the pointer to the Supplemental Methods.
  The Results cite the figure when they report the outcome.
- **Equations go to the supplement.** The main text names the model in words with its key parameters
  ("a one-degree-of-freedom additive-model Wald approximation with the His-METAL odds ratio, minor allele
  frequency and case fraction"); the formula, constants (Z_α = 5.45) and evaluation range go to the
  Supplemental Methods.
- **Input sample sizes are allowed** (the N an analysis was run on), derived counts are not (how many
  passed, were significant, were removed by a filter: those are Supplemental Methods or Results).
- **Recipe, not history**: see section 2b.
- **Past tense, passive or "we", one procedure per sentence**; name the tool before its settings.

## 2b. Recipe, not history

Methods (main and supplementary) describe the final analysis as it was run: data, tools, versions,
parameters, filters and models that regenerate the reported results. They are a recipe, not a lab notebook.

**The test for every sentence:** could someone regenerate the result from it without knowing what happened
before? If the sentence only makes sense as an event in the project's past, rewrite it as the rule that was
finally applied, or delete it.

Out (never in Methods or Supplemental Methods):
- order and timing of work: "earlier", "later", "initially", "first ... then", "at the time";
- what was removed, fixed, corrected, adjusted, re-run, replaced, relabelled, restored along the way;
- earlier versions, working lists and their sizes ("116 candidate regions", "112 curated loci used during
  the analysis"), decisions "after review", bugs found and fixed.

Stays, written as a rule (only what is needed to get the same numbers):
- filters and exclusions, as criteria: "variants with MAF < 0.01 were excluded" (a filter is a rule, not
  an event);
- handling of input-file properties, as a processing step: "the reported frequency is that of the other
  allele, so 1 - EAF was used", not "the EAF column was corrected";
- analyses fitted differently at some loci, as a condition: "where the lead variant was missing from some
  cohorts and the regression was ill-conditioned, MR-MEGA was fitted on the cohorts carrying it", not
  "MR-MEGA was rerun"; headings follow ("MR-MEGA at incompletely covered loci", not "MR-MEGA reruns");
- a final set chosen without a stateable rule: name the set and where it is listed ("the 110 regions in the
  110-locus table"), without the story of how it was reached;
- sensitivity analyses only when they justify a methodological choice, described as a comparison ("three
  PCs were compared with the 80%-variance default"), not as a change made;
- QC counts (individuals or variants excluded per cohort) in the Supplemental Methods, as counts, not as
  before/after sequences ("3,708 to 3,702").

Trigger words to check: removed / excluded after review, corrected, fixed, re-run / rerun, earlier, later,
initially, previously (except for prior studies), replaced, instead of, no longer, curated during, working
list, candidate regions, was changed, updated.

The data themselves follow the same rule: describe them as one final dataset, never as a timeline of
cohorts added, removed or run separately (skill `sciweave-data-description`).

## 3. Shape of a Methods subsection

One paragraph per analysis, in this order: data → preprocessing → model/tool (version, citation) →
key parameters → test and threshold → how the output was used downstream. End the subsection with one
pointer: "Details are in the Supplemental Methods." Keep each subsection to what a reviewer needs to
judge validity; if a paragraph needs more than ~6 parameters, move the rest to the supplement.

## 4. Shape of the matching Supplemental Methods section

Same heading as the Methods subsection (or "<heading>: <topic>"), opening with "This section extends the
Methods subsection on ...". Then: equations; full parameter lists; per-cohort / per-locus input tables;
software commands or pipeline targets; sensitivity analyses that justify a choice (section 2b); the
evidence markers for each (MetaData extract scripts). Per-locus *outcomes* (power at the current N,
required N) belong to Supplemental Results or to the figure legend, not here.

## 5. Evidence

Every parameter in Methods gets an `\ev{}` pointing to a MetaData extract built by a script in
`scripts/manuscript/evidence/` that reads the run's config, log or output (never typed). Every tool gets
its citation (verified DOI). Write the extract first, then the text.

## 6. Auditing existing Methods

When asked to check Methods (or before proposing a Methods rewrite), scan the subsection for: outcome
numbers, gene / locus names, `\figref` / `\tabref` / `\supref` of result items, equations, the
history trigger words of section 2b, and per-item lists. For each hit decide with the
table in section 1 and propose the move (Methods sentence rewritten as the rule; detail to the
Supplemental Methods; outcome to Results) as one proposal per subsection. Record what was moved in
the Structure tab.
