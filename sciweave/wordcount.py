"""Word counts of a LaTeX article, per section file and per (sub)section, written to notes/wordcount.md.

Rewritten at every compile, so the editor's Word count tab always matches the last build. Counting follows
the usual journal convention: the main text is everything except the abstract, references, figure/table
legends and the supplement, which are counted separately. Commands that print no prose (\\ev, \\cite, \\label)
are dropped, a cross-reference (\\figref, \\ref...) counts as one word, inline math as one word, and table
bodies (tabular) are not counted, only their captions.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

# file stem -> part of the article it is counted under (anything else is main text)
PART = {"abstract": "Abstract", "references": "References", "figures_tables": "Legends",
        "supplemental": "Supplement", "supplement": "Supplement"}
SKIP = {"claims", "structure", "evidence", "numbers"}  # planning pages and generated macro files
HEAD = re.compile(r"\\(section|subsection)\*?\{([^}]*)\}")


def _strip(t: str) -> str:
    t = re.sub(r"(?<!\\)%.*", "", t)  # comments
    t = re.sub(r"\\begin\{(tabular\*?|tabularx|longtable)\}.*?\\end\{\1\}", " ", t, flags=re.S)
    t = re.sub(r"\\(ev|cite|citep|citet|label|includegraphics|input|include|hspace|vspace|setlength|bibitem|"
               r"usepackage|documentclass|graphicspath|newcommand|renewcommand|def)\*?(\[[^\]]*\])*(\{[^{}]*\})*", " ", t)
    t = re.sub(r"\\(figref|tabref|supref|ref|eqref|autoref|cref|Cref)(\[[^\]]*\])?\{[^}]*\}", " X ", t)
    t = re.sub(r"\$[^$]*\$", " X ", t)  # inline math: one word
    t = re.sub(r"\\[A-Za-z]+\{\}", " X ", t)  # number macros (\AUCJoint{}): one word
    t = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", " ", t)
    t = re.sub(r"\\[A-Za-z@]+\*?", " ", t)  # remaining commands; their braced arguments stay as text
    return t.replace("~", " ")


def count(t: str) -> int:
    return sum(1 for w in re.split(r"[\s{}\[\]]+|--+", _strip(t)) if re.search(r"[A-Za-z0-9]", w))


def _parts(t: str) -> list[tuple[str, str]]:
    """[(heading, text)] split at \\section / \\subsection; text before the first heading is '(opening)'."""
    out, last, name = [], 0, "(opening)"
    for m in HEAD.finditer(t):
        out.append((name, t[last:m.start()]))
        name = ("" if m.group(1) == "section" else "\u2003") + m.group(2).strip()
        last = m.end()
    out.append((name, t[last:]))
    return [(n, s) for n, s in out if count(s) or n != "(opening)"]


def _inputs(main: Path) -> list[Path]:
    text = re.sub(r"(?<!\\)%.*", "", main.read_text(encoding="utf-8", errors="replace"))
    files = []
    for name in re.findall(r"\\(?:input|include)\{([^}]+)\}", text):
        f = main.parent / (name if name.endswith(".tex") else name + ".tex")
        if f.is_file() and f.stem not in SKIP:
            files.append(f)
    return files


def build(main: Path, out: Path) -> dict:
    files = _inputs(main)
    totals: dict[str, int] = {}
    rows, detail = [], []
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        part = PART.get(f.stem, "Main text")
        if part == "References":
            n_refs = len(re.findall(r"\\bibitem\{", t))
            rows.append((f.stem, part, f"{n_refs} references"))
            continue
        n = count(t)
        totals[part] = totals.get(part, 0) + n
        rows.append((f.stem, part, f"{n:,}"))
        sub = _parts(t)
        if len(sub) > 1:
            detail.append(f"\n### {f.stem}\n\n| Section | Words |\n|---|---:|")
            detail += [f"| {h} | {count(s):,} |" for h, s in sub]
    order = ["Abstract", "Main text", "Legends", "Supplement"]
    md = ["# Word count", "",
          f"*Generated at every compile ({time.strftime('%Y-%m-%d %H:%M')}); edits here are overwritten.* "
          "Main text excludes the abstract, references, figure/table legends and the supplement. "
          "A cross-reference, an inline formula or a number macro counts as one word; table bodies are not counted.",
          "", "## Totals", "", "| Part | Words |", "|---|---:|"]
    md += [f"| {p} | {totals[p]:,} |" for p in order if p in totals]
    md += [f"| **Article (abstract + main text + legends)** | **{sum(totals.get(p, 0) for p in order[:3]):,}** |",
           "", "## By file", "", "| File | Counted as | Words |", "|---|---|---:|"]
    md += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    md += ["", "## By section"] + detail + [""]
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(md)
    old = out.read_text(encoding="utf-8") if out.is_file() else None
    if old is None or old.split("\n", 3)[3:] != text.split("\n", 3)[3:]:  # rewrite only when a count changed
        out.write_text(text, encoding="utf-8", newline="")
    return totals
