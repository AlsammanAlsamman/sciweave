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
    if not args.no_register:
        from sciweave import registry
        e = registry.register(p)
        print(f"  listed in your SciWeave home as '{e['id']}' ({registry.home_dir()})")
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
    if p.destination is not None:
        org = {nid: p.organized_state(nid) for nid, n in p.nodes.items() if n.get("organized")}
        changed = [nid for nid, s in org.items() if s != "ok"]
        print(f"  destination: {p.destination}  ({len(org)} organized copies"
              + (f"; needs attention: {', '.join(f'{k} {org[k]}' for k in changed)}" if changed else "") + ")")
    hidden = p.hidden_nodes()
    if hidden or p.hidden_analyses():
        print(f"  focus: {len(p.hidden_analyses())} analyses and {len(hidden)} objects hidden from view "
              "(`sciweave analysis ls --all`; nothing deleted)")
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
    if args.path and not args.allow_duplicate and Path(args.path).expanduser().exists():
        from sciweave import dedup
        same = dedup.check_new(p, Path(args.path).expanduser())["same_as"]
        if same:  # one file, one node
            raise SciWeaveError(f"identical content (bit by bit) is already in the network as {', '.join(same)}: link to "
                                f"it instead (or --allow-duplicate)")
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


def _analysis_fields(args) -> dict:
    f = {"title": args.title, "parent": args.parent, "order": args.order, "summary": args.summary, "group": args.group,
         "status": args.status, "start": args.start, "end": args.end, "step": args.step_key,
         "tools": args.tools, "feeds": args.feeds}
    if args.parent == "none":
        f["parent"] = ""
    if args.paths:
        f["paths"] = args.paths
    if args.param:
        f["params"] = parse_params(args.param)
    if args.organized is not None:
        f["organized"] = args.organized
    return f


def cmd_analysis(args):
    p = get_project(args)
    a = args.args
    if args.action in ("add", "set"):
        if not a:
            raise SciWeaveError('usage: sciweave analysis add <key> ["title"] [--parent K] [--summary "..."] ...')
        f = _analysis_fields(args)
        if len(a) > 1:
            f["title"] = a[1]
        if args.action == "set" and a[0] not in p.analyses:
            raise SciWeaveError(f"unknown analysis '{a[0]}' (use `analysis add` to create it)")
        e = p.set_analysis(a[0], actor=args.actor, **f)
        p.commit()
        print(f"analysis {a[0]}: {e['title']}")
    elif args.action == "log":
        if len(a) < 2:
            raise SciWeaveError('usage: sciweave analysis log <key> "what happened" [--date YYYY-MM-DD]')
        ev = p.log_analysis(a[0], " ".join(a[1:]), date=args.date, actor=args.actor)
        p.commit()
        print(f"{a[0]}: {ev['date']} {ev['text']}")
    elif args.action == "link":
        if len(a) < 2:
            raise SciWeaveError("usage: sciweave analysis link <key> <ID> [<ID> ...]")
        cur = p.analysis(a[0]).get("nodes", [])
        p.set_analysis(a[0], actor=args.actor, nodes=cur + [x for x in a[1:] if x not in cur])
        p.commit()
        print(f"{a[0]}: linked {', '.join(a[1:])}")
    elif args.action == "done":  # shorthand: mark organized
        for k in a:
            p.set_analysis(k, actor=args.actor, organized=True)
        p.commit()
        print(f"organized: {', '.join(a)}")
    elif args.action in ("hide", "unhide"):
        if not a:
            raise SciWeaveError(f"usage: sciweave analysis {args.action} <key> [<key> ...]")
        done = p.hide_analyses(a, hidden=args.action == "hide", actor=args.actor)
        p.commit()
        hid = p.hidden_analyses()
        print(f"{args.action}: {', '.join(done) if done else 'nothing changed'}  "
              f"({len(hid)} analyses and {len(p.hidden_nodes())} objects hidden from view; nothing deleted)")
    elif args.action == "rename":
        if len(a) != 2:
            raise SciWeaveError("usage: sciweave analysis rename <old-key> <new-key>   (branches old.* follow)")
        m = p.rename_analysis(a[0], a[1], actor=args.actor)
        p.commit()
        print("renamed " + ", ".join(f"{k} → {v}" for k, v in m.items()))
    elif args.action == "rm":
        for k in a:
            p.remove_analysis(k, actor=args.actor)
        p.commit()
        print(f"removed: {', '.join(a)}")
    elif args.action == "import":
        if len(a) != 1:
            raise SciWeaveError("usage: sciweave analysis import <file.json> [--replace]")
        data = json.loads(Path(a[0]).read_text(encoding="utf-8"))
        n = p.import_analyses(data.get("analyses", data) if isinstance(data, dict) else data,
                              replace=args.replace, actor=args.actor)
        p.commit()
        print(f"imported {n} analyses")
    elif args.action == "show":
        if len(a) != 1:
            raise SciWeaveError("usage: sciweave analysis show <key>")
        e = p.analysis(a[0])
        num = {k: n for k, n, _ in p.analysis_tree()}.get(a[0], "")
        print(f"{num} {e['title']}  ({a[0]})  · {e.get('status', '')}" + ("  · organized" if e.get("organized") else ""))
        when = " → ".join(x for x in (e.get("start"), e.get("end")) if x)
        if when:
            print(f"  when:    {when}")
        if e.get("parent"):
            print(f"  part of: {e['parent']} · {p.analyses[e['parent']]['title']}")
        for label, key in (("group", "group"), ("summary", "summary"), ("issue", "issue"), ("tools", "tools"),
                           ("feeds", "feeds"), ("step", "step")):
            if e.get(key):
                print(f"  {label + ':':8} {p.expand_refs(str(e[key]))}")
        if e.get("params"):
            pr = e["params"]
            print("  params:  " + (", ".join(f"{k}={v}" for k, v in pr.items()) if isinstance(pr, dict) else str(pr)))
        for path in e.get("paths") or []:
            print(f"  path:    {path}")
        if e.get("nodes"):
            print("  objects: " + ", ".join(f"{n} · {p.nodes[n]['label']}" for n in e["nodes"] if n in p.nodes))
        kids = p.analysis_children(a[0])
        if kids:
            print("  branches: " + ", ".join(f"{k} · {p.analyses[k]['title']}" for k in kids))
        if e.get("history"):
            print("  history:")
            for h in e["history"]:
                print(f"    {h.get('date', ''):10}  {p.expand_refs(h.get('text', ''))}")
    else:  # ls
        tree = p.analysis_tree()
        if not tree:
            print("no analyses yet: sciweave analysis add raw \"Raw & input data\"  ·  sciweave analysis import guide.json")
            return
        hid = p.hidden_analyses()
        for k, num, depth in tree:
            if args.depth is not None and depth >= args.depth:
                continue
            if k in hid and not args.all:
                continue
            e = p.analyses[k]
            when = " → ".join(x for x in (e.get("start"), e.get("end")) if x)
            mark = "✓" if e.get("organized") else "·"
            print(f"{mark} {'  ' * depth}{num:<7} {e['title'][:60]}  [{k}]" + (f"  {{{e['group']}}}" if e.get("group") else "")
                  + (f"  {when}" if when else "")
                  + ("" if e.get("status") == "done" else f"  ({e.get('status')})") + ("  [hidden]" if k in hid else ""))
        shown = [k for k, _, _ in tree if k not in hid]
        done = sum(1 for k in shown if p.analyses[k].get("organized"))
        print(f"{len(shown)} analyses · {done} organized (✓)"
              + (f" · {len(hid)} hidden ({'shown above' if args.all else 'ls --all to see them'})" if hid else ""))


def cmd_hide(args):
    p = get_project(args)
    done = p.hide_nodes(args.ids, hidden=args.cmd == "hide", actor=args.actor)
    p.commit()
    print(f"{args.cmd}: {', '.join(done) if done else 'nothing changed'}  ({len(p.hidden_nodes())} objects hidden from view)")


def cmd_destination(args):
    p = get_project(args)
    if args.path or args.clear:
        d = p.set_destination(None if args.clear else args.path, actor=args.actor)
        p.commit()
        print(f"destination: {d}" if d else "destination cleared")
    else:
        print(p.destination or "no destination set (sciweave destination <folder>)")


def cmd_organize(args):
    p = get_project(args)
    if args.clean:
        from sciweave import jobs
        running = [j for j in jobs.list_jobs(p) if j["state"] in jobs.RUNNING]
        if running:
            raise SciWeaveError(f"{len(running)} copy job(s) still running; clean up when they are done")
        if p.destination is None:
            raise SciWeaveError("no destination set")
        gone, freed = [], 0
        for x in sorted(p.destination.rglob("*")):
            if x.name.endswith((".sciweave-part", ".sciweave-old")) and x.exists():
                size = sum(f.stat().st_size for f in x.rglob("*") if f.is_file()) if x.is_dir() else x.stat().st_size
                shutil.rmtree(x) if x.is_dir() else x.unlink()
                gone.append(x)
                freed += size
        for x in gone:
            print(f"  removed {x}")
        print(f"removed {len(gone)} unfinished copy leftover(s), {freed / 1e9:.1f} GB freed")
        return
    ids = list(p.nodes) if args.all else args.ids
    if not ids:
        moved = p.sync_organized()
        p.commit()
        for nid, a, b in moved:
            print(f"  {nid}: {a} -> {b}")
        print(f"{len(moved)} organized copy(ies) moved to match the network")
        return
    done, held = 0, []
    for nid in ids:
        n = p.node(nid)
        if args.all and not n.get("path"):
            continue
        if args.all and (n.get("organize_skip") or (p.is_big(nid) and not args.include_large)):
            if p.organized_state(nid) != "ok":
                held.append(f"{nid} ({(p.node_size(nid) or 0) / 1e9:.1f} GB{', skipped' if n.get('organize_skip') else ''})")
            continue
        state = p.organized_state(nid)
        if args.verify:
            res = p.verify_copy(nid)
            p.record_verification(nid, res, actor=args.actor)
            p.commit()
            print(f"  {nid}: {'OK' if res['ok'] else 'PROBLEM'}  {res['message']}")
            continue
        if args.all and state == "ok":
            continue
        if not args.all and n.get("organized") and not args.force:
            chk = p.organized_check(nid)
            if chk["up_to_date"]:
                print(f"  {nid}: already up to date (same size, source unchanged) — --force to copy again")
                continue
            print(f"  {nid}: source {chk['source_size']:,} bytes vs copy {chk['copy_size']:,} bytes"
                  f"{' (source changed)' if chk['state'] == 'source_changed' else ''}: updating the copy")
        rec = p.organize(nid, actor=args.actor)
        p.commit()  # record each copy as soon as it is done
        done += 1
        print(f"  {nid} -> {p.destination}{'/'}{rec['path']}")
    print(f"{done} object(s) copied to the destination")
    if held:
        print(f"not copied (large data, > 4 GB, or skipped): {', '.join(held)}  -> sciweave organize <ID>, or --include-large")


def cmd_dedup(args):
    from sciweave import dedup
    p = get_project(args)
    a = args.args
    if args.action == "check":
        if len(a) != 1:
            raise SciWeaveError("usage: sciweave dedup check <path>")
        r = dedup.check_new(p, Path(a[0]))
        if r["same_as"]:
            print(f"already in the network: identical content to {', '.join(r['same_as'])} — link to it, don't add it again")
        elif r["overlaps"]:
            print(f"shares identical files with {', '.join(r['overlaps'])} (partial overlap)")
        else:
            print("not in the network (no identical content found)")
        return
    if args.action == "merge":
        if len(a) != 2:
            raise SciWeaveError('usage: sciweave dedup merge <keep-ID> <duplicate-ID> [--label "..."] [--used-by A B]')
        if not args.force:
            r = dedup.scan(p)
            pair = {tuple(sorted((x["a"], x["b"]))) for x in r["whole"]}
            if tuple(sorted(a)) not in pair:
                raise SciWeaveError(f"{a[0]} and {a[1]} are not bit-by-bit identical (use --force to merge anyway)")
        k = dedup.merge(p, a[0], a[1], label=args.label, used_by=args.used_by, actor=args.actor)
        p.commit()
        print(f"merged {a[1]} into {k['id']} · {k['label']}  (also at: {', '.join(k['meta'].get('also_at', []))})")
        return
    r = dedup.scan(p)
    if not r["whole"] and not r["partial"]:
        print(f"no duplicated content among {len(p.nodes)} objects")
        return
    for x in r["whole"]:
        print(f"  SAME CONTENT  {x['a']} · {p.nodes[x['a']]['label']}  ==  {x['b']} · {p.nodes[x['b']]['label']}"
              f"   -> sciweave dedup merge {x['a']} {x['b']}")
    for x in r["partial"]:
        print(f"  overlap       {x['a']} and {x['b']} share {x['shared_files']} identical file(s)"
              f" ({x['a_files']} vs {x['b_files']} files)")
    for g in r["groups"][:args.limit]:
        print(f"    {g['size']:>14,} bytes  sha256 {g['sha256'][:12]}  " + "  |  ".join(f"{o}: {Path(f).name}" for o, f in g["members"]))


def cmd_job(args):
    from sciweave import jobs
    sys.exit(jobs.run(get_project(args).root, args.id))


def cmd_relocate(args):
    p = get_project(args)
    ch = p.relocate(args.old, args.new, dry_run=args.dry_run, actor=args.actor)
    for where, a, b in ch[:50]:
        print(f"  {where}: {a}\n      -> {b}")
    if len(ch) > 50:
        print(f"  ... and {len(ch) - 50} more")
    if args.dry_run:
        print(f"{len(ch)} path(s) would change (dry run; nothing written)")
        return
    p.commit()
    print(f"relocated {len(ch)} path(s)")


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
    from sciweave import registry
    from sciweave.server import serve
    p = get_project(args)
    registry.register(p)  # so it also shows on the home page (`sciweave open`)
    serve(p, port=args.port, open_browser=not args.no_browser)


def cmd_open(args):
    """The home dashboard: every registered project, each one clickable."""
    from sciweave import registry
    from sciweave.server import serve
    start = ""
    if args.name:
        start = f"p/{registry.get(args.name)['id']}/"
    elif args.project:  # `sciweave -C <path> open` opens that project inside the home
        start = f"p/{registry.register(get_project(args))['id']}/"
    serve(None, port=args.port, open_browser=not args.no_browser, start=start)


def cmd_projects(args):
    from sciweave import registry
    if args.action == "where":
        print(registry.home_dir())
        return
    if args.action == "add":
        if not args.target:
            raise SciWeaveError("projects add expects a project folder")
        e = registry.register(Project.find(Path(args.target)))
        print(f"listed '{e['name']}' as '{e['id']}' -> {e['path']}")
        return
    if args.action == "rm":
        if not args.target:
            raise SciWeaveError("projects rm expects a project id or name")
        e = registry.unregister(args.target)
        print(f"removed '{e['id']}' from the list (folder untouched: {e['path']})")
        return
    reg = registry.load()
    if args.json:
        print(json.dumps([registry.summarize(e) for e in reg["projects"]], indent=2, ensure_ascii=False))
        return
    print(f"SciWeave home: {registry.home_dir()}")
    if not reg["projects"]:
        print("  no projects yet: sciweave init <folder>  ·  sciweave projects add <folder>")
    w = max((len(e["id"]) for e in reg["projects"]), default=0)
    for e in reg["projects"]:
        sm = registry.summarize(e, check_disk=False)
        info = "folder not found" if sm.get("missing") else f"{sm['nodes']} objects · {sm['links']} links"
        print(f"  {e['id']:{w}}  {e['name']}  ({info})")
        print(f"  {'':{w}}  {e['path']}")


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
    s.add_argument("--no-register", action="store_true", help="don't list it in your SciWeave home")

    for name in ("open", "dashboard"):
        s = cmd(name, cmd_open, "home dashboard: all your projects, pick one to open"
                + ("" if name == "open" else " (same as `open`)"))
        s.add_argument("name", nargs="?", help="open this project directly (id or name)")
        s.add_argument("--port", type=int, default=8765)
        s.add_argument("--no-browser", action="store_true")

    s = cmd("projects", cmd_projects, "list / add / remove projects in your SciWeave home")
    s.add_argument("action", nargs="?", default="ls", choices=["ls", "add", "rm", "where"])
    s.add_argument("target", nargs="?", help="folder (add) or id/name (rm)")
    s.add_argument("--json", action="store_true")

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
    s.add_argument("--allow-duplicate", action="store_true", help="add even if identical content is already in the network")
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

    s = cmd("analysis", cmd_analysis,
            "the analysis history (a guide tree): ls / show / add / set / log / link / done / rm / import")
    s.add_argument("action", nargs="?", default="ls",
                   choices=["ls", "show", "add", "set", "log", "link", "done", "hide", "unhide", "rename", "rm", "import"])
    s.add_argument("args", nargs="*", help='add/set: key ["title"] · log: key "text" · link: key IDs · '
                                           'done/rm: keys · import: file.json · show: key')
    s.add_argument("--title")
    s.add_argument("--parent", help="key of the analysis this one belongs under ('none' = top level)")
    s.add_argument("--order", type=int, help="position among its siblings")
    s.add_argument("--summary", help="what was done and why, the key result (1-3 sentences)")
    s.add_argument("--status", choices=["done", "open", "superseded", "planned"])
    s.add_argument("--start", help="first day worked on (YYYY-MM-DD)")
    s.add_argument("--end", help="last day worked on (YYYY-MM-DD)")
    s.add_argument("--step", dest="step_key", help="the network step it belongs to")
    s.add_argument("--tools")
    s.add_argument("--group", help='a label shared by independent siblings, e.g. "Hispanic cohorts" (not a parent)')
    s.add_argument("--feeds", help="article items it feeds, e.g. 'Figure 2, ST4'")
    s.add_argument("--paths", nargs="+")
    s.add_argument("-p", "--param", action="append", help="key=value (result-changing parameter)")
    s.add_argument("--organized", dest="organized", action="store_true", default=None)
    s.add_argument("--not-organized", dest="organized", action="store_false")
    s.add_argument("--date", help="log: date of the event (default today)")
    s.add_argument("--replace", action="store_true", help="import: replace the whole tree")
    s.add_argument("--depth", type=int, help="ls: show only this many levels")
    s.add_argument("--all", action="store_true", help="ls: include hidden analyses")

    s = cmd("destination", cmd_destination, "show or set the folder where organized copies live")
    s.add_argument("path", nargs="?")
    s.add_argument("--clear", action="store_true")

    s = cmd("organize", cmd_organize,
            "copy objects to the destination in folders matching the network (no ids: move copies to match)")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true", help="every object with a file that has no up-to-date copy (large data excluded)")
    s.add_argument("--force", action="store_true", help="copy again even if the copy is up to date")
    s.add_argument("--clean", action="store_true", help="remove leftovers of interrupted copies (*.sciweave-part / -old)")
    s.add_argument("--verify", action="store_true", help="re-read copies and check them against their checksum files")
    s.add_argument("--include-large", action="store_true", help="--all: also copy objects over 4 GB (not those you skipped)")

    s = cmd("dedup", cmd_dedup, "one file, one node: find identical content (scan), check a path, merge duplicates")
    s.add_argument("action", nargs="?", default="scan", choices=["scan", "check", "merge"])
    s.add_argument("args", nargs="*", help="check: path · merge: KEEP-ID DUPLICATE-ID")
    s.add_argument("--label", help="merge: new label for the kept node, e.g. '1000 Genomes EUR (FIZI, LYNXgwas)'")
    s.add_argument("--used-by", nargs="+", help="merge: tools / analyses that use it")
    s.add_argument("--force", action="store_true", help="merge even if the content is not identical")
    s.add_argument("--limit", type=int, default=30, help="scan: how many identical-file groups to list")

    s = cmd("job", cmd_job, "(internal) run one background copy / verification job; started by the dashboard")
    s.add_argument("id")

    s = cmd("relocate", cmd_relocate, "data moved: rewrite node and analysis paths under OLD to NEW")
    s.add_argument("old", help="the folder's previous location")
    s.add_argument("new", help="its new location")
    s.add_argument("--dry-run", action="store_true")

    s = cmd("hide", cmd_hide, "hide objects from view (focus); nothing is deleted. `unhide` brings them back")
    s.add_argument("ids", nargs="+")
    s = cmd("unhide", cmd_hide, "show hidden objects again (also pins them visible inside a hidden analysis)")
    s.add_argument("ids", nargs="+")

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
