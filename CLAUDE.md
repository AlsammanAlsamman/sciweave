# SciWeave — notes for developing this repo

- `DESIGN.md` is the living plan and roadmap; keep it current when behavior changes.
- One contract: `.sciweave/graph.json`, mutated only through `sciweave/project.py::Project`.
  CLI (`cli.py`), plan protocol (`plan.py`), articles (`article.py`), server (`server.py`)
  and AI (`ai.py`) are thin layers over it. Call `Project.commit()` after mutations
  (saves graph + regenerates SCIWEAVE.md).
- Staleness model lives in `Project.effective_parents / edge_is_stale / status`:
  data nodes record the versions of their effective parents (through process nodes);
  process nodes are flagged when an input changed since their outputs were built;
  `part_of` compares placed version vs final/current; `documents`/`related` never propagate.
- The SciWeave home (`registry.py`, default `~/Documents/SciWeave/`) lists every project;
  `sciweave open` serves `dashboard/home.html` at `/` and each project under `/p/<id>/`.
  Tests isolate it with an autouse `SCIWEAVE_HOME` fixture; keep dashboard URLs relative.
- Core is stdlib-only. `anthropic` is optional (`sciweave[ai]`), imported lazily in `ai.py`.
- Dashboard = `sciweave/dashboard/{index.html,style.css,app.js}` + vendored D3, inlined by
  `export.py` (no CDN, works offline). Plain ES5, no build step. Colors are CSS tokens with
  light + dark values; category hues were validated for CVD (blue/yellow/aqua + neutrals).
- The Claude skill ships inside the package (`sciweave/skills/sciweave/`) so
  `sciweave install-skill` works from a wheel. Package data is declared in pyproject.
- Tests: `python -m pytest -q`. Visual check: `sciweave demo /tmp/d && cd /tmp/d && sciweave serve`,
  or headless Chrome screenshots of `http://127.0.0.1:8765/?view=radial&theme=dark`.
