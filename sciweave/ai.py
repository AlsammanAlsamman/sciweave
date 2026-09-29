"""The small integrated assistant: answer a question about the project and say
which nodes to highlight.

Two engines:
- "local": always available, instant. Scores nodes by keyword overlap
  (id, label, type, description, tags, groups, params, notes) and answers
  simple provenance questions ("how was F1 made", "what is stale").
- "claude": used when the `anthropic` package is installed and credentials
  resolve (ANTHROPIC_API_KEY, or an `ant auth login` profile). Sends a compact
  text rendering of the graph and gets back {answer, highlight[]} as
  structured JSON. Model: $SCIWEAVE_MODEL, default claude-opus-5.
"""

from __future__ import annotations

import json
import os
import re

from sciweave.project import Project
from sciweave.trace import describe, edge_line, fmt_params

DEFAULT_MODEL = "claude-opus-5"
ID_RE = re.compile(r"\b([A-Z]{1,4}\d+[a-z]?)\b")  # T1, SF7a, ST10

SYSTEM = """You are the assistant inside SciWeave, a provenance network for a research project
(bioinformatics / data analysis). You are given the whole project graph as text: nodes
(raw data, inputs, pipelines, scripts, tables, figures, supplements, article sections)
and directed edges (what fed what), each edge with the result-changing parameters, script
and command used. Staleness means an upstream item changed after the node was built.

Answer the user's question briefly and concretely (a few sentences or a short list),
citing node ids like T1 or F2. Only state what the graph supports; if the graph does not
record something, say so. In "highlight" list the ids of the nodes the user should look
at, most relevant first (empty if none)."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "highlight": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "highlight"],
    "additionalProperties": False,
}


def graph_context(p: Project) -> str:
    """Compact, deterministic text rendering of the project (stable for prompt caching)."""
    status = p.status(check_disk=False)
    lines = [f"PROJECT: {p.graph['project']['name']}"]
    if p.graph["project"].get("description"):
        lines.append(p.graph["project"]["description"])
    lines.append("")
    if p.steps:
        lines.append("STEPS (analysis stages, in order): " + " -> ".join(
            f"{k} ({v['label']})" for k, v in sorted(p.steps.items(), key=lambda kv: kv[1].get("order", 0))))
    lines.append("NODES (id | type | label | step | path | current/final version | state | groups | description)")
    for nid in sorted(p.nodes):
        n = p.nodes[nid]
        lines.append(" | ".join([
            nid, n["type"], n["label"], n.get("step") or "-", n["path"] or "-",
            f"v{n['current_version'] or '-'}/{'v' + str(n['final_version']) if n['final_version'] else '-'}",
            status[nid]["state"], ",".join(n["groups"]) or "-", (n.get("description") or "")[:200],
        ]))
        for note in n["notes"][-3:]:
            lines.append(f"    note: {note['text'][:200]}")
        for v in n["versions"][-3:]:
            if v.get("why"):
                lines.append(f"    v{v['v']} ({v['ts'][:10]}) why: {v['why'][:200]}")
        if n.get("branch"):
            b = n["branch"]
            lines.append(f"    branch: {b.get('status')} " + (f"of {b['of']} " if b.get("of") else "") +
                         (f"why: {b.get('why', '')[:200]}" if b.get("why") else ""))
    lines.append("")
    lines.append("EDGES (source -rel-> target [params] script cmd)")
    for e in sorted(p.edges, key=lambda x: x["id"]):
        lines.append(f"{e['id']}: {edge_line(p, e)}")
    return "\n".join(lines)


# ------------------------------------------------------------ local engine ---

def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9_.]+", text.lower()) if len(t) > 1}


STOP = {"the", "is", "was", "how", "what", "which", "of", "to", "in", "for", "and", "a", "it", "made",
        "created", "generated", "show", "me", "where", "did", "do", "does", "are", "with", "from", "used", "by"}


def local_answer(p: Project, question: str) -> dict:
    q = question.strip()
    ql = q.lower()
    status = p.status(check_disk=False)
    ids = [m for m in ID_RE.findall(q) if m in p.nodes]

    if any(w in ql for w in ("stale", "out of date", "outdated", "regenerate", "redo")):
        stale = [nid for nid, s in status.items() if s["stale"]]
        if not stale:
            return {"answer": "Nothing is stale: every node was built from the current version of its inputs.",
                    "highlight": [], "engine": "local"}
        lines = [f"{nid} · {p.nodes[nid]['label']}: {status[nid]['reasons'][0]}" for nid in stale]
        return {"answer": f"{len(stale)} stale node(s) need regeneration:\n" + "\n".join(lines),
                "highlight": stale, "engine": "local"}

    if ids:
        nid = ids[0]
        hl = [nid] + p.upstream(nid) if not any(w in ql for w in ("depend", "uses", "used by", "downstream"))\
            else [nid] + p.downstream(nid)
        return {"answer": describe(p, nid, status), "highlight": hl, "engine": "local"}

    if "param" in ql:
        qt = _tokens(q) - STOP - {"param", "params", "parameter", "parameters"}
        hits = []
        for e in p.edges:
            if not e.get("params"):
                continue
            blob = _tokens(" ".join(f"{k} {v}" for k, v in e["params"].items()) + " " +
                           p.nodes[e["source"]]["label"] + " " + p.nodes[e["target"]]["label"])
            if not qt or qt & blob:
                hits.append(e)
        if hits:
            return {"answer": "\n".join(f"{e['source']} -> {e['target']}: {fmt_params(e['params'])}" for e in hits[:15]),
                    "highlight": sorted({x for e in hits for x in (e["source"], e["target"])}), "engine": "local"}

    qt = _tokens(q) - STOP
    scored = []
    for nid, n in p.nodes.items():
        blob = " ".join([nid, n["type"], n["label"], n.get("description") or "", n["path"] or "",
                         " ".join(n["tags"]), " ".join(n["groups"]),
                         " ".join(x["text"] for x in n["notes"])])
        for e in p.incoming(nid):
            blob += " " + " ".join(f"{k} {v}" for k, v in (e.get("params") or {}).items())
        bt = _tokens(blob)
        score = sum(2 if t in _tokens(n["label"]) else 1 for t in qt if t in bt)
        score += sum(1 for t in qt for b in bt if len(t) > 3 and t != b and (t in b or b in t))
        if score:
            scored.append((score, nid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        return {"answer": "No node matches those words. Try an id (e.g. F1), a label word, or 'what is stale'.",
                "highlight": [], "engine": "local"}
    top = [nid for _, nid in scored[:12]]
    ans = "Matching nodes:\n" + "\n".join(f"{nid} · {p.nodes[nid]['label']} ({p.nodes[nid]['type']}, "
                                           f"{status[nid]['state']})" for nid in top)
    return {"answer": ans, "highlight": top, "engine": "local"}


# ----------------------------------------------------------- claude engine ---

def claude_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return os.environ.get("SCIWEAVE_AI", "auto") != "local"


def claude_answer(p: Project, question: str, model: str | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model or os.environ.get("SCIWEAVE_MODEL", DEFAULT_MODEL),
        max_tokens=4000,
        system=[
            {"type": "text", "text": SYSTEM},
            # graph text is stable between questions -> cache it
            {"type": "text", "text": graph_context(p), "cache_control": {"type": "ephemeral"}},
        ],
        output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        messages=[{"role": "user", "content": question}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to answer")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    data["highlight"] = [x for x in data.get("highlight", []) if x in p.nodes]
    data["engine"] = f"claude ({response.model})"
    return data


def ask(p: Project, question: str, engine: str = "auto") -> dict:
    if engine == "local" or (engine == "auto" and not claude_available()):
        return local_answer(p, question)
    try:
        return claude_answer(p, question)
    except Exception as exc:  # no credentials, network, rate limit... fall back, but say so
        if engine == "claude":
            raise
        res = local_answer(p, question)
        res["engine"] = f"local (claude unavailable: {type(exc).__name__})"
        return res
