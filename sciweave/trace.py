"""Provenance as text: "how was X made?" and "what depends on X?".

Used by `sciweave trace`, `sciweave show`, the AI context and SCIWEAVE.md.
"""

from __future__ import annotations

from sciweave.project import SOFT_RELS, Project


def fmt_params(params: dict) -> str:
    if not params:
        return ""
    return ", ".join(f"{k}={v}" for k, v in params.items())


def edge_line(p: Project, e: dict) -> str:
    bits = [f"{e['source']} -{e['rel']}-> {e['target']}"]
    if e.get("label"):
        bits.append(f'as "{e["label"]}"')
    if e.get("params"):
        bits.append(f"[{fmt_params(e['params'])}]")
    if e.get("script"):
        bits.append(f"script {e['script']}@v{e.get('script_version')}")
    if e.get("command"):
        bits.append(f"cmd: {e['command']}")
    if e.get("params_file"):
        bits.append(f"params file: {e['params_file']['path']}")
    if p.edge_is_stale(e):
        bits.append("(STALE)")
    return "  ".join(bits)


def trace_up(p: Project, node_id: str, max_depth: int = 50) -> list[str]:
    """Indented tree of everything upstream of node_id, with edge details."""
    lines: list[str] = []
    seen: set[str] = set()

    def walk(nid: str, depth: int) -> None:
        for e in sorted(p.incoming(nid), key=lambda x: x["source"]):
            src = p.nodes.get(e["source"])
            if src is None or e["rel"] in SOFT_RELS:
                continue
            pad = "  " * depth
            extra = []
            if e.get("params"):
                extra.append(f"params: {fmt_params(e['params'])}")
            if e.get("script") and e["rel"] != "code":
                extra.append(f"script {e['script']}@v{e.get('script_version')}")
            if e.get("command"):
                extra.append(f"cmd: {e['command']}")
            stale = "  STALE" if p.edge_is_stale(e) else ""
            lines.append(f"{pad}<- [{e['rel']}] {src['id']} · {src['label']} ({src['type']}, "
                         f"v{src['current_version']}){stale}")
            for x in extra:
                lines.append(f"{pad}     {x}")
            if src["id"] in seen:
                lines.append(f"{pad}     (see above)")
                continue
            seen.add(src["id"])
            if depth < max_depth:
                walk(src["id"], depth + 1)

    walk(node_id, 0)
    return lines


def trace_down(p: Project, node_id: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    def walk(nid: str, depth: int) -> None:
        for e in sorted(p.outgoing(nid), key=lambda x: x["target"]):
            dst = p.nodes.get(e["target"])
            if dst is None:
                continue
            lab = f' as "{e["label"]}"' if e.get("label") else ""
            lines.append(f"{'  ' * depth}-> [{e['rel']}] {dst['id']} · {dst['label']} ({dst['type']}){lab}")
            if dst["id"] not in seen:
                seen.add(dst["id"])
                walk(dst["id"], depth + 1)

    walk(node_id, 0)
    return lines


def describe(p: Project, node_id: str, status: dict | None = None) -> str:
    n = p.node(node_id)
    st = (status or p.status()).get(node_id, {})
    out = [f"{n['id']} · {n['label']}   [{n['type']}, {n['mode']}]"]
    if n.get("step"):
        st = p.steps.get(n["step"], {})
        out.append(f"  step: {st.get('label', n['step'])} (#{st.get('order', '?')} of {len(p.steps)})")
    if n.get("description"):
        out.append(f"  {n['description']}")
    if n.get("path"):
        out.append(f"  path: {n['path']}")
    out.append(f"  state: {st.get('state', '?')}" + (f"  ({'; '.join(st['reasons'])})" if st.get("reasons") else ""))
    if n["groups"]:
        out.append(f"  groups: {', '.join(n['groups'])}")
    if n["tags"]:
        out.append(f"  tags: {', '.join(n['tags'])}")
    if n["versions"]:
        out.append(f"  versions (current v{n['current_version']}, final "
                   f"{'v' + str(n['final_version']) if n['final_version'] else '-'}):")
        from sciweave.cli import describe_change
        for v in n["versions"][-8:]:
            mark = " *final*" if v["v"] == n["final_version"] else ""
            stored = "stored" if v.get("stored") else "fingerprint"
            out.append(f"    v{v['v']}  {v['ts'][:16]}  {v.get('actor', '')}  {stored}  {v.get('message', '')}{mark}")
            if v.get("why"):
                out.append(f"         why: {v['why']}")
            for c in v.get("changes", []):
                out.append(f"         - {describe_change(c)}")
    b = n.get("branch")
    fam = p.branch_family(node_id)
    if len(fam) > 1:
        out.append("  branches:")
        for x in fam:
            bx = p.nodes[x].get("branch") or {}
            out.append(f"    {'*' if bx.get('status') == 'main' else ' '} {x} {bx.get('status', 'main'):11} "
                       f"{p.nodes[x]['label']}" + (f"  (why: {bx['why']})" if bx.get("why") else ""))
    up = trace_up(p, node_id)
    if up:
        out.append("  how it was made:")
        out.extend("    " + x for x in up)
    down = trace_down(p, node_id)
    if down:
        out.append("  used by:")
        out.extend("    " + x for x in down)
    if n["notes"]:
        out.append("  notes:")
        for note in n["notes"][-5:]:
            out.append(f"    {note['ts'][:10]} ({note.get('actor', 'user')}): {note['text']}")
    return "\n".join(out)
