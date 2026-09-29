"""sciweave command line. Run `sciweave -h` or `sciweave <command> -h`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from sciweave import __version__
from sciweave import article as article_mod
from sciweave import plan as plan_mod
from sciweave.project import Project, SciWeaveError
from sciweave.schema import EDGE_RELATIONS, NODE_TYPES
from sciweave.trace import describe, edge_line, trace_down, trace_up


def parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_params(items) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SciWeaveError(f"parameter must be key=value, got '{item}'")
        k, v = item.split("=", 1)
        out[k.strip()] = parse_value(v.strip())
    return out


def get_project(args) -> Project:
    return Project.find(Path(args.project) if args.project else None)


# ----------------------------------------------------------------- commands --

def cmd_init(args):
    p = Project.init(Path(args.path), name=args.name, description=args.description or "", bare=args.bare)
    print(f"initialised SciWeave project '{p.graph['project']['name']}' at {p.root}")
    print("  next: sciweave add <type> <label> <path>   ·   sciweave article new \"Title\"   ·   sciweave serve")


def cmd_status(args):
    p = get_project(args)
    st = p.status()
    if args.json:
        print(json.dumps(st, indent=2))
        return
    g = p.graph["project"]
    counts = {}
    for n in p.nodes.values():
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    print(f"{g['name']}  ({p.root})")
    print(f"  {len(p.nodes)} nodes · {len(p.edges)} links · " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    bad = {k: v for k, v in st.items() if v["state"] != "ok"}
    if not bad:
        print("  everything up to date")
    for nid, s in bad.items():
        print(f"  {s['state'].upper():9} {nid} · {p.nodes[nid]['label']} — {'; '.join(s['reasons'][:2])}")
    if args.untracked:
        un = p.untracked_files()
        if un:
            print(f"  {len(un)} untracked file(s) inside project folders:")
            for f in un[:40]:
                print(f"    {f}")


def cmd_ls(args):
    p = get_project(args)
    st = p.status(check_disk=False)
    rows = [n for n in p.nodes.values() if not args.type or n["type"] == args.type]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for n in sorted(rows, key=lambda x: (list(NODE_TYPES).index(x["type"]), x["id"])):
        ver = f"v{n['current_version']}" if n["current_version"] else "-"
        fin = f" final v{n['final_version']}" if n["final_version"] else ""
        flag = "" if st[n["id"]]["state"] == "ok" else f"  [{st[n['id']]['state']}]"
        print(f"{n['id']:6} {n['type']:10} {n['label'][:44]:44} {ver:4}{fin}  {n['path'] or ''}{flag}")


def cmd_add(args):
    p = get_project(args)
    mode = "managed" if args.copy else "ref" if args.ref else ("managed" if args.type in ("table", "figure", "supplement", "script", "section") else "ref")
    n = p.add_node(args.type, args.label, path=args.path, mode=mode, dest=args.dest,
                   description=args.desc or "", groups=args.group or [], tags=args.tag or [],
                   node_id=args.id, message=args.message or "added", actor=args.actor, step=args.step)
    p.commit()
    print(f"added {n['id']} · {n['label']}  ({n['type']}, {n['mode']}{', ' + n['path'] if n['path'] else ''})")


def cmd_link(args):
    p = get_project(args)
    e = p.link(args.source, args.target, rel=args.rel, params=parse_params(args.param), script=args.script,
               command=args.cmd, note=args.note or "", label=args.label, params_file=args.params_file,
               actor=args.actor, why=args.why or "")
    p.commit()
    print(f"{e['id']}: {edge_line(p, e)}")


def cmd_unlink(args):
    p = get_project(args)
    p.unlink(args.edge, actor=args.actor)
    p.commit()
    print(f"removed {args.edge}")


def cmd_save(args):
    p = get_project(args)
    ids = list(args.ids)
    if args.all:
        st = p.status()
        ids += [nid for nid, s in st.items() if s["modified"] and nid not in ids]
    if not ids:
        print("nothing to save (no ids given, and --all found no modified files)")
        return
    for nid in ids:
        v = p.save_version(nid, message=args.message or "", actor=args.actor, refresh_edges=not args.no_refresh,
                           why=args.why or "")
        print(f"{nid}: " + (f"saved v{v['v']}" if v else "unchanged"))
        if v:
            for c in v["changes"]:
                print(f"    {describe_change(c)}")
    p.commit()
    st = p.status(check_disk=False)
    down = sorted({d for nid in ids for d in p.downstream(nid) if st[d]["stale"]})
    if down:
        print(f"now stale downstream: {', '.join(down)}")


def cmd_final(args):
    p = get_project(args)
    v = p.mark_final(args.id, args.version, actor=args.actor)
    p.commit()
    print(f"{args.id}: v{v} marked final")


def cmd_restore(args):
    p = get_project(args)
    t = p.restore(args.id, args.version, out=args.out, force=args.force, actor=args.actor)
    p.commit()
    print(f"{args.id}: v{args.version} restored to {p.rel(t)}")


def cmd_note(args):
    p = get_project(args)
    p.add_note(args.id, " ".join(args.text), actor=args.actor)
    p.commit()
    print(f"note added to {args.id}")


def cmd_group(args):
    p = get_project(args)
    p.set_group(args.name, args.ids, color=args.color, label=args.label, actor=args.actor)
    p.commit()
    print(f"group '{args.name}': {', '.join(args.ids) or '(metadata only)'}")


def cmd_edit(args):
    p = get_project(args)
    n = p.update_node(args.id, label=args.label, description=args.desc, tags=args.tag, groups=args.group,
                      step=args.step, actor=args.actor)
    p.commit()
    print(f"updated {n['id']} · {n['label']}")


def cmd_rm(args):
    p = get_project(args)
    p.remove_node(args.id, actor=args.actor)
    p.commit()
    print(f"removed {args.id} (files on disk and stored versions are kept)")


def cmd_trace(args):
    p = get_project(args)
    n = p.node(args.id)
    print(f"{n['id']} · {n['label']}")
    lines = trace_down(p, args.id) if args.down else trace_up(p, args.id)
    print("\n".join(lines) if lines else ("  (nothing depends on it)" if args.down else "  (no recorded inputs)"))


def cmd_show(args):
    p = get_project(args)
    if args.json:
        print(json.dumps(p.node(args.id), indent=2, ensure_ascii=False))
    else:
        print(describe(p, args.id))


def cmd_history(args):
    p = get_project(args)
    for h in p.history(node=args.id, limit=args.n):
        who = h.get("node") or h.get("edge") or ""
        print(f"{h['ts'][:16].replace('T', ' ')}  {h['actor']:6} {h['event']:18} {who:6} {h.get('detail', '')}")


def cmd_watch(args):
    from sciweave.server import watch
    watch(get_project(args), interval=args.interval, autosave=args.autosave, once=args.once)


def cmd_article(args):
    p = get_project(args)
    if args.action == "new":
        secs = [s.strip() for s in args.sections.split(",")] if args.sections else article_mod.DEFAULT_SECTIONS
        a = article_mod.new_article(p, args.title, node_id=args.id, sections=secs, actor=args.actor)
        p.commit()
        print(f"created article {a['id']} · {a['label']} in {a['meta']['folder']}/")
    elif args.action == "place":
        if not args.as_label:
            raise SciWeaveError('article place needs --as "Figure 2"')
        e = article_mod.place(p, args.item, args.article, args.as_label, version=args.version, actor=args.actor)
        p.commit()
        print(f"placed {args.item} v{e['placed_version']} as '{args.as_label}' -> {e['placed_path']}")
    elif args.action == "sync":
        done = article_mod.sync(p, args.article, actor=args.actor)
        p.commit()
        print("\n".join(done) if done else "article is up to date")
    elif args.action == "show":
        s = article_mod.article_summary(p, args.article)
        a = s["article"]
        print(f"{a['id']} · {a['label']}  ({a['meta'].get('folder')})")
        for c in s["components"]:
            flag = " STALE" if c["edge_stale"] or c["state"] == "stale" else ""
            print(f"  {str(c['as']):22} {c['id']:6} {c['label'][:40]:40} placed v{c['placed_version'] or '-'} "
                  f"current v{c['current_version'] or '-'} final v{c['final_version'] or '-'}{flag}")


def cmd_plan(args):
    if args.action == "template":
        print(json.dumps(plan_mod.TEMPLATE, indent=2))
        return
    if not args.file:
        raise SciWeaveError("plan check/apply needs a plan file")
    p = get_project(args)
    plan = plan_mod.load_plan(args.file)
    if args.action == "check":
        rep = plan_mod.check(p, plan)
        print(rep.render())
        sys.exit(0 if rep.passed else 1)
    ids = plan_mod.apply(p, plan, actor=args.actor)
    print("applied plan: " + (plan.get("summary") or ""))
    for k, v in ids.items():
        print(f"  {k} -> {v} · {p.nodes[v]['label']}")


def describe_change(c: dict) -> str:
    k = c["kind"]
    if k == "param":
        src = f" (from {c['source']})" if c.get("source") else ""
        return f"param {c['key']}: {c.get('from')} -> {c.get('to')}{src}"
    if k in ("input", "script"):
        return f"{k} {c['node']}: v{c['from']} -> v{c['to']}"
    if k.startswith("added_"):
        return f"new {k[6:]} {c['node']} (v{c['to']})"
    if k == "dropped_input":
        return f"input {c['node']} no longer used"
    if k == "content":
        return f"file size {c['from']:,} -> {c['to']:,} bytes"
    return str(c)


def cmd_step(args):
    p = get_project(args)
    a = args.args
    if args.action == "define":
        if not a:
            raise SciWeaveError('usage: sciweave step define <key> "<label>" [--order N] [--desc "..."]')
        st = p.define_step(a[0], a[1] if len(a) > 1 else None, order=args.order, description=args.desc or "",
                           actor=args.actor)
        p.commit()
        print(f"step {a[0]}: {st['label']} (#{st['order']})")
    elif args.action == "set":
        if len(a) < 2:
            raise SciWeaveError("usage: sciweave step set <key> <ID> [<ID> ...]   (key 'none' clears)")
        done = p.set_step(a[1:], None if a[0] == "none" else a[0], actor=args.actor)
        p.commit()
        print(f"{a[0]}: {', '.join(done) if done else 'nothing changed'}")
    else:  # ls
        summ = p.step_summary()
        steps = sorted(p.steps.items(), key=lambda kv: kv[1].get("order", 0))
        label = {k: v["label"] for k, v in steps}
        for k, st in steps + ([(None, {"label": "(no step)", "order": ""})] if None in summ else []):
            s = summ.get(k, {"members": [], "within": 0, "in": {}, "out": {}})
            types = {}
            for nid in s["members"]:
                types[p.nodes[nid]["type"]] = types.get(p.nodes[nid]["type"], 0) + 1
            print(f"{str(st.get('order', '')):>3}  {k or '-':14} {st['label'][:30]:30} {len(s['members']):3} objects  "
                  + ", ".join(f"{v} {t}" for t, v in sorted(types.items())))
            if s["within"] or s["in"] or s["out"]:
                ins = ", ".join(f"{label.get(x, '(no step)')} {n}" for x, n in s["in"].items())
                outs = ", ".join(f"{label.get(x, '(no step)')} {n}" for x, n in s["out"].items())
                print(f"       links: {s['within']} within" + (f" | in from {ins}" if ins else "")
                      + (f" | out to {outs}" if outs else ""))


def cmd_branch(args):
    p = get_project(args)
    if args.action == "new":
        if len(args.args) < 2:
            raise SciWeaveError('usage: sciweave branch new <ID> "<label>" [path] -p key=value --why "..."')
        src, label = args.args[0], args.args[1]
        path = args.args[2] if len(args.args) > 2 else None
        mode = "managed" if args.copy else ("ref" if args.ref else None)
        n = p.branch(src, label, path=path, mode=mode, params=parse_params(args.param), why=args.why or "",
                     name=args.name, actor=args.actor)
        p.commit()
        print(f"branch {n['id']} · {n['label']}  (variant of {src}: {args.why})")
    elif args.action in ("main", "alternative", "abandoned"):
        if not args.args:
            raise SciWeaveError(f"usage: sciweave branch {args.action} <ID> --why \"...\"")
        p.set_branch_status(args.args[0], args.action, why=args.why or "", actor=args.actor)
        p.commit()
        print(f"{args.args[0]} is now {args.action}")
    else:  # ls
        nid = args.args[0] if args.args else None
        fams = [p.branch_family(nid)] if nid else []
        if not nid:
            roots = {p.branch_family(x)[0] for x, n in p.nodes.items() if n.get("branch")}
            fams = [p.branch_family(r) for r in sorted(roots)]
        if not fams:
            print("no branches yet (sciweave branch new <ID> ...)")
        for fam in fams:
            for x in fam:
                b = p.nodes[x].get("branch") or {}
                mark = "*" if b.get("status") == "main" else " "
                print(f" {mark} {x:6} {b.get('status', 'main'):11} {p.nodes[x]['label'][:44]:44} "
                      f"{('why: ' + b['why']) if b.get('why') else ''}")
            print()


def cmd_suggest(args):
    from sciweave import monitor
    p = get_project(args)
    r = monitor.run_round(p, update=not args.peek)
    if args.json:
        print(json.dumps(r, indent=1))
        return
    text = monitor.render(p, r)
    print(text if text else "nothing new since the last round")


def cmd_monitor(args):
    import time
    from sciweave import monitor
    p = get_project(args)
    if args.status:
        m = monitor.minutes_since_last(p)
        st = monitor.load_state(p)
        print(f"last round: {st.get('last_round') or 'never'}" + (f" ({m:.0f} min ago)" if m is not None else ""))
        print(f"remembered files: {len(st.get('seen', {}))} · ignore: {st.get('ignore') or '-'} · "
              f"extra roots: {st.get('roots') or '-'}")
        for rd in st.get("rounds", [])[-5:]:
            print(f"  {rd['ts'][:16]}  updated {rd['updated']} · new {rd['new']} · stale {rd['stale']}")
        return
    if args.due is not None:  # used by the Claude skill: exit 0 and print the round only when due
        m = monitor.minutes_since_last(p)
        if m is not None and m < args.due:
            return
        r = monitor.run_round(p)
        text = monitor.render(p, r)
        if text:
            print(text)
        return
    print(f"SciWeave monitor on '{p.graph['project']['name']}': a round every {args.every} min (Ctrl+C to stop)")
    try:
        while True:
            p.load()
            r = monitor.run_round(p)
            text = monitor.render(p, r)
            stamp = r["ts"][11:16]
            print(f"\n[{stamp}] " + (text if text else "nothing new"), flush=True)
            if args.once:
                return
            time.sleep(args.every * 60)
    except KeyboardInterrupt:
        print("\nmonitor stopped")


def cmd_ignore(args):
    from sciweave import monitor
    p = get_project(args)
    st = monitor.load_state(p)
    for pat in args.patterns:
        if pat not in st["ignore"]:
            st["ignore"].append(pat)
    monitor.save_state(p, st)
    print("ignoring: " + ", ".join(st["ignore"]))


def cmd_watch_root(args):
    from sciweave import monitor
    p = get_project(args)
    st = monitor.load_state(p)
    path = str(Path(args.path).expanduser().resolve())
    if not Path(path).is_dir():
        raise SciWeaveError(f"not a folder: {path}")
    if path not in st["roots"]:
        st["roots"].append(path)
    monitor.save_state(p, st)
    print("extra folders watched: " + ", ".join(st["roots"]))


def cmd_context(args):
    from sciweave.ai import graph_context
    print(graph_context(get_project(args)))


def cmd_ask(args):
    from sciweave.ai import ask
    res = ask(get_project(args), " ".join(args.question), engine=args.engine)
    print(res["answer"])
    if res.get("highlight"):
        print(f"\n[look at: {', '.join(res['highlight'])}]  ({res['engine']})")


def cmd_serve(args):
    from sciweave.server import serve
    serve(get_project(args), port=args.port, open_browser=not args.no_browser)


def cmd_export(args):
    from sciweave.export import export_html
    path = export_html(get_project(args), args.out)
    print(f"wrote {path}")


def cmd_demo(args):
    from sciweave.demo import build_demo
    p = build_demo(Path(args.path))
    print(f"demo project at {p.root}: {len(p.nodes)} nodes, {len(p.edges)} links")
    print(f"  cd {p.root} && sciweave serve")


def cmd_types(args):
    print("node types:")
    for t, m in NODE_TYPES.items():
        print(f"  {t:11} {m['prefix']:4} {m['category']:8} -> {m['folder']:22} {m['desc']}")
    print("edge relations:")
    for r, d in EDGE_RELATIONS.items():
        print(f"  {r:10} {d}")


def cmd_install_skill(args):
    base = Path(args.dest).expanduser() if args.dest else Path.home() / ".claude" / "skills"
    for src in sorted((Path(__file__).parent / "skills").iterdir()):
        if not src.is_dir():
            continue
        dest = base / src.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        print(f"installed skill {src.name} -> {dest}")
    print("  in Claude Code: \"add these results to my SciWeave project\" · /sciweave-monitor to start hourly rounds")


# -------------------------------------------------------------------- parser --

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sciweave", description="Provenance network for research projects.")
    ap.add_argument("--version", action="version", version=f"sciweave {__version__}")
    ap.add_argument("-C", "--project", help="project root (default: search upward from cwd)")
    ap.add_argument("--actor", default="user", help="who is acting (user / claude), recorded in history")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def cmd(name, fn, help_):
        s = sub.add_parser(name, help=help_, description=help_)
        s.set_defaults(fn=fn)
        return s

    s = cmd("init", cmd_init, "create a SciWeave project (folders + .sciweave/)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--name")
    s.add_argument("--description")
    s.add_argument("--bare", action="store_true",
                   help="adopt an existing project in place: add only .sciweave/ and SCIWEAVE.md, no folders")

    s = cmd("status", cmd_status, "what is stale, modified or missing")
    s.add_argument("--json", action="store_true")
    s.add_argument("--untracked", action="store_true", help="also list untracked files in project folders")

    s = cmd("ls", cmd_ls, "list nodes")
    s.add_argument("--type", choices=list(NODE_TYPES))
    s.add_argument("--json", action="store_true")

    s = cmd("add", cmd_add, "add a node (file, folder, or conceptual)")
    s.add_argument("type", choices=list(NODE_TYPES))
    s.add_argument("label", help='short name, e.g. "Credible sets (finemapping)"')
    s.add_argument("path", nargs="?")
    m = s.add_mutually_exclusive_group()
    m.add_argument("--copy", action="store_true", help="copy into the project and snapshot versions (managed)")
    m.add_argument("--ref", action="store_true", help="reference in place, fingerprint only")
    s.add_argument("--dest", help="where to copy inside the project (default: type folder)")
    s.add_argument("--desc")
    s.add_argument("--group", action="append")
    s.add_argument("--tag", action="append")
    s.add_argument("--id", help="explicit id (default: next T1/F2/...)")
    s.add_argument("--step", help="analysis step this object belongs to (e.g. gwas, finemapping, article)")
    s.add_argument("-m", "--message")

    s = cmd("link", cmd_link, "record provenance: SOURCE -rel-> TARGET")
    s.add_argument("source")
    s.add_argument("target")
    s.add_argument("--rel", default="feeds", choices=list(EDGE_RELATIONS))
    s.add_argument("-p", "--param", action="append", help="result-changing parameter key=value (repeatable)")
    s.add_argument("--script", help="script/pipeline node that did it (its version is recorded)")
    s.add_argument("--cmd", help="command line used")
    s.add_argument("--label", help='e.g. "Figure 2" for part_of links')
    s.add_argument("--note")
    s.add_argument("--params-file", help="config file to snapshot with the link (e.g. config.yaml)")
    s.add_argument("--why", help="reason for a parameter change (kept in the link's change log)")

    s = cmd("unlink", cmd_unlink, "remove a link by id (E12)")
    s.add_argument("edge")

    s = cmd("save", cmd_save, "snapshot new version(s) of node files")
    s.add_argument("ids", nargs="*")
    s.add_argument("-m", "--message")
    s.add_argument("--all", action="store_true", help="save every node modified on disk")
    s.add_argument("--why", help="the reason for this update (kept with the version, shown in the dashboard)")
    s.add_argument("--no-refresh", action="store_true",
                   help="cosmetic change: do not mark the node as rebuilt from current inputs")

    s = cmd("final", cmd_final, "mark a version as final")
    s.add_argument("id")
    s.add_argument("--version", type=int)

    s = cmd("restore", cmd_restore, "restore an old version")
    s.add_argument("id")
    s.add_argument("version", type=int)
    s.add_argument("--out", help="write to this path instead of overwriting the node's file")
    s.add_argument("--force", action="store_true")

    s = cmd("note", cmd_note, "attach a note to a node")
    s.add_argument("id")
    s.add_argument("text", nargs="+")

    s = cmd("group", cmd_group, "put nodes in a named group (halos in the dashboard)")
    s.add_argument("name")
    s.add_argument("ids", nargs="*")
    s.add_argument("--color")
    s.add_argument("--label")

    s = cmd("edit", cmd_edit, "edit a node's label/description/tags/groups")
    s.add_argument("id")
    s.add_argument("--label")
    s.add_argument("--desc")
    s.add_argument("--tag", action="append")
    s.add_argument("--group", action="append")
    s.add_argument("--step")

    s = cmd("rm", cmd_rm, "remove a node (and its links) from the graph")
    s.add_argument("id")

    s = cmd("trace", cmd_trace, "how was it made (or --down: what depends on it)")
    s.add_argument("id")
    s.add_argument("--down", action="store_true")

    s = cmd("show", cmd_show, "everything about one node")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")

    s = cmd("history", cmd_history, "event log (optionally for one node)")
    s.add_argument("id", nargs="?")
    s.add_argument("-n", type=int, default=50)

    s = cmd("autosave", cmd_watch, "poll tracked files every few seconds; log changes (or --save them)")
    s.add_argument("--interval", type=float, default=5.0)
    s.add_argument("--save", dest="autosave", action="store_true", help="save managed files automatically")
    s.add_argument("--once", action="store_true")

    s = cmd("article", cmd_article, "article templates: new / place / sync / show")
    s.add_argument("action", choices=["new", "place", "sync", "show"])
    s.add_argument("args", nargs="*", help='new: "Title" · place: ITEM ARTICLE · sync/show: ARTICLE')
    s.add_argument("--as", dest="as_label", help='placement label, e.g. "Figure 2"')
    s.add_argument("--version", type=int)
    s.add_argument("--id")
    s.add_argument("--sections", help="comma list (default: abstract,introduction,methods,results,discussion)")

    s = cmd("plan", cmd_plan, "import protocol: template / check / apply a plan.json")
    s.add_argument("action", choices=["template", "check", "apply"])
    s.add_argument("file", nargs="?")

    s = cmd("step", cmd_step, "analysis steps (GWAS, fine-mapping, article...): define / set / ls")
    s.add_argument("action", choices=["define", "set", "ls"])
    s.add_argument("args", nargs="*", help='define: key "label" · set: key ID [ID ...] · ls')
    s.add_argument("--order", type=int, help="position of the step in the analysis (1, 2, 3 ...)")
    s.add_argument("--desc")

    s = cmd("branch", cmd_branch, "alternative versions of an analysis: new / main / alternative / abandoned / ls")
    s.add_argument("action", choices=["new", "main", "alternative", "abandoned", "ls"])
    s.add_argument("args", nargs="*", help='new: ID "label" [path] · main/alternative/abandoned: ID · ls: [ID]')
    s.add_argument("-p", "--param", action="append", help="overridden parameter key=value (repeatable)")
    s.add_argument("--why", help="why this branch exists / why this decision")
    s.add_argument("--name", help="short branch name, e.g. 'L=5 sensitivity'")
    m = s.add_mutually_exclusive_group()
    m.add_argument("--copy", action="store_true")
    m.add_argument("--ref", action="store_true")

    s = cmd("suggest", cmd_suggest, "one monitor round now: changed, new, missing and stale results")
    s.add_argument("--json", action="store_true")
    s.add_argument("--peek", action="store_true", help="look without remembering (next round reports it again)")

    s = cmd("monitor", cmd_monitor, "look for new analysis results every hour and suggest updates")
    s.add_argument("--every", type=float, default=60, help="minutes between rounds (default 60)")
    s.add_argument("--once", action="store_true")
    s.add_argument("--status", action="store_true", help="when was the last round, what is remembered")
    s.add_argument("--due", type=float, metavar="MIN",
                   help="run a round only if the last one was at least MIN minutes ago (for the Claude skill)")

    s = cmd("ignore", cmd_ignore, "never report files matching these glob patterns")
    s.add_argument("patterns", nargs="+")

    s = cmd("watch", cmd_watch_root, "also monitor a folder outside the project (e.g. a pipeline's results)")
    s.add_argument("path")

    cmd("context", cmd_context, "print the whole project as compact text (for AI)")

    s = cmd("ask", cmd_ask, "ask a question about the project")
    s.add_argument("question", nargs="+")
    s.add_argument("--engine", choices=["auto", "local", "claude"], default="auto")

    s = cmd("serve", cmd_serve, "open the dashboard (local server)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")

    s = cmd("export", cmd_export, "write a static, offline sciweave.html")
    s.add_argument("--out")

    s = cmd("demo", cmd_demo, "create a demo project to explore")
    s.add_argument("path", nargs="?", default="sciweave-demo")

    cmd("types", cmd_types, "list node types and link relations")

    s = cmd("install-skill", cmd_install_skill, "install the Claude Code skill (~/.claude/skills/sciweave)")
    s.add_argument("--dest")
    return ap


def _article_args(args):
    a = args.args
    need = {"new": 1, "place": 2, "sync": 1, "show": 1}[args.action]
    if len(a) != need:
        raise SciWeaveError(f"article {args.action} expects {need} argument(s), got {len(a)}")
    if args.action == "new":
        args.title = a[0]
    elif args.action == "place":
        args.item, args.article = a
    else:
        args.article = a[0]


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "article":
            _article_args(args)
        args.fn(args)
    except SciWeaveError as exc:
        print(f"sciweave: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
