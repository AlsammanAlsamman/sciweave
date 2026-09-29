---
name: sciweave-monitor
description: Start, run, or stop SciWeave's hourly monitor, which looks for new or changed analysis results that are not yet in the project's provenance network and suggests updating it. Use when the user says "sciweave monitor", "/sciweave-monitor", "monitor my project", "start / stop the sciweave monitor", or when invoked with the argument "round" by the monitor's own hourly loop.
---

# SciWeave monitor: an hourly look for new results

The user keeps working (with you, on the cluster, in R or Python) and results pile up:
new tables, re-run figures, a sensitivity analysis. The monitor keeps the SciWeave
network honest without interrupting: **about once an hour it looks, and if something is
new it suggests, briefly. It never applies anything on its own.**

All the detection is done by the CLI, deterministically and cheaply:

```bash
sciweave suggest            # one round now: changed / new / missing / stale
sciweave monitor --due 50   # a round only if the last one was >= 50 min ago; prints nothing when there is nothing new
sciweave monitor --status   # when was the last round, what is remembered
sciweave ignore "*.bam" "tmp/*"          # never report these
sciweave watch /path/to/pipeline/results # also watch a folder outside the project
```

The first round ever is a **baseline**: it remembers every untracked file and only
summarises per folder, so the user is never flooded with old files.

## Start: the user says "sciweave monitor" (no argument)

1. `sciweave status`. If no project is found, ask where it is (or offer `sciweave init . --bare`).
2. Run a round now: `sciweave suggest`. Report it as described in "Reporting" below.
3. Start the hourly loop with the loop skill:
   `Skill({skill: "loop", args: "60m /sciweave-monitor round"})`
4. Tell the user in one or two lines: the monitor is on, it looks every hour, it only
   suggests, and "stop the sciweave monitor" turns it off.

## Round: invoked with the argument "round" (the hourly wakeup)

1. Run `sciweave monitor --due 50`.
2. **It printed nothing → say nothing at all** and end the turn. That is the common,
   correct outcome. Never report "checked, nothing new".
3. It printed something:
   - **If you were in the middle of a task** when this fired, do not take over the turn.
     Write at most 3 lines ("SciWeave: 2 new results and 1 changed table since the last
     look, I'll go through them when you're ready"), then **continue the interrupted
     task exactly where you left off.** (Lesson learned from brainny: a timed loop runs
     inside the same session and can land mid-task. It must never derail the work.)
   - **If the session was idle**, report (see below) and ask one question: *"Shall I add
     these to SciWeave?"*

## Reporting (short, grouped, with a recommendation)

- **Changed tracked files** → "T2 changed. Save it as a new version? Why did it change?"
- **New results** → group them, say which look like *keepers* (final tables, figures)
  and which look like *intermediates* (chunks, logs, per-chromosome files; suggest
  `sciweave ignore` for noise). Use the hints: `new version of F2?` means the file is
  probably an update of an existing item; `near T1` suggests where it links.
- **Stale** → mention only if something else is being reported, in one line.
- Keep it under ~12 lines. Offer, don't lecture.

On a "yes", follow the main **sciweave** skill's import protocol (plan → `plan check` →
show the report → apply only after approval), including the update rules below.

## Updating carefully: replace or branch?

Every update must say **why** (the dashboard shows it next to the automatic "what changed"
list: parameters old → new, which input moved to which version, script version, size).
Never invent a reason: ask the user.

| situation | do |
|---|---|
| same analysis re-run, the new result **replaces** the old | `sciweave save T2 -m "what" --why "reason"` (plan: `save` with `why`) |
| a parameter changed for good | `sciweave link PL2 T2 --rel produces -p L=5 --why "reason"`, then save T2 after the re-run |
| cosmetic re-render (fonts, colours) | `sciweave save F2 --no-refresh -m "bigger font"` (stays stale if it was) |
| an **alternative** to keep next to the original (sensitivity, other panel, other method, other cohort set) | `sciweave branch new T2 "Credible sets (L=5)" path -p L=5 --why "sensitivity to L"` (plan: `branches`) |
| the user decides which alternative is the real one | `sciweave branch main T2b --why "reason"` (others become alternative) |
| an alternative is dropped | `sciweave branch abandoned T2c --why "reason"` |

If it is not obvious whether a new result replaces the old one or is an alternative,
**ask**: "Is this a new version of T2 (replace), or an alternative to keep next to it (branch)?"

## Stop: the user says "stop the sciweave monitor"

Stop the loop that runs `/sciweave-monitor round` (delete its scheduled job), then show
`sciweave monitor --status` in one line.

## Guardrails

- Only suggest. Never save, add, branch, ignore or apply without an explicit yes.
- Never run heavy commands in a round (no pipelines, nothing on a cluster, no re-hashing
  by hand); `sciweave monitor --due` is the whole round.
- Silent when there is nothing new. At most one round per hour; `--due` enforces it even
  if the loop fires early or twice.
