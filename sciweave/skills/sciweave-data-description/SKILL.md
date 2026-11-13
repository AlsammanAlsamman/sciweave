---
name: sciweave-data-description
description: Describe a study's data in a SciWeave article as one final dataset, with no timeline of how cohorts or samples were added, removed, rerun or analysed separately. Use whenever writing, revising or auditing any article text that mentions cohorts, samples, sample sizes or how an analysis was run on them - Methods, Supplemental Methods, Results, legends, table captions, notes - and before proposing any such text.
---

# Describing the data: one final dataset, no timeline

Everything in `sciweave-article` applies (proposals only, evidence markers, avoid-ai-writing). This skill
extends section 2b of `sciweave-methods` ("recipe, not history") to how the **data** are named, and it
applies to all article text: main text, Supplemental Material, figure legends, table captions.

## 1. Name the data as one group first

Define the dataset once, as it finally is, then refer to it by that name in every analysis:

> the five Hispanic/Latino cohorts (8,484 individuals; 2,968 cases, 5,516 controls)

An analysis is then "run on the five Hispanic/Latino cohorts" or "on the 8,484 individuals", not on
"the four array cohorts and YUC". Every analysis of the same data uses the same set and the same N.

Give the sample size once per part (Abstract, Introduction, the Methods study-population paragraph, the first
Results paragraph), and again only where the number matters to that point: an input parameter of a method
(the N of a summary-statistics method, the N and case fraction of a power formula), or a comparison (vs. a
previous study). Elsewhere write "the individuals of the association analyses" or "the five cohorts".
Pre-QC counts are not needed. Each figure legend stands alone and may give the N it plots.

## 2. No timeline of the data

Never write when or in what order a cohort or sample set entered or left the work:
- "added in this study", "newly genotyped", "not in this run", "was not included at the time";
- "the four array cohorts ... YUC was analysed separately", "in a second run", "then merged";
- older sample sizes or sets (8,519, 8,254, "before deduplication"), "rerun on", "restricted to";
- "deduplicated" or similar working labels for the final set.

## 3. Split a cohort out only for a real, result-changing difference

Mention one cohort on its own only if it got **different settings** that change the numbers (another
reference panel, imputation server, genome build, model, covariate), and write it as a rule:

> For YUC, imputation used the TOPMed panel.

If a cohort went through the **same** reference, maps, software and settings and was only run as a
separate job, batch or chunk, that is an implementation detail: leave it out of the text. Its record
(job ids, pipeline checks, offsets between batches) goes in the evidence note or the SciWeave network,
not in the prose.

Cohort-level results (per-cohort ORs, per-cohort means, heterogeneity) are fine in Results and tables:
that is the data, not its history.

## 4. Applying it

- Before proposing, scan the draft for: added, separately, this run, array cohorts, not in, at the time,
  earlier, later, rerun, restricted to, before, after, deduplicated, merged, newly, second, and any old N.
- Auditing existing text: grep the sections for the same words and any sample size that is not the
  final one; propose one rewrite per section, each passage stated for the final dataset.
- Never fix this by deleting a real, result-changing per-cohort setting; move it into rule form instead.
- Applies to messages to the author too: describe the data the same way.
