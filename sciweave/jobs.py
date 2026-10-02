"""Background copies / verifications that survive the dashboard.

A job is a small JSON file in `.sciweave/jobs/<id>.json`, run by its own detached process
(`sciweave -C <project> job <id>`), so closing or restarting `sciweave serve/open` does not
stop it. The process writes its progress (bytes done / total, phase, a heartbeat) to the
file; the dashboard only reads these files. A job whose heartbeat stops is "interrupted"
(its leftovers are cleared when it is retried). At most MAX_PARALLEL copies run at once per
project; the others wait as "queued". Writes to graph.json from a job and from the dashboard
are serialised with a lock file (`graph_lock`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sciweave.project import Project, SciWeaveError, now_iso

MAX_PARALLEL = 2
HEARTBEAT_STALE = 60  # seconds without news -> interrupted
RUNNING = ("queued", "copying", "verifying")


def jobs_dir(p: Project) -> Path:
    d = p.state / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, job: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(job), encoding="utf-8")
    Project._retry(lambda: os.replace(tmp, path))


def _read(path: Path) -> dict | None:
    try:
        return json.loads(Project._retry(lambda: path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- the lock --
@contextmanager
def graph_lock(state: Path, timeout: float = 60, stale: float = 120):
    """Cross-process lock around read-modify-write of graph.json (stdlib only: an exclusive lock file)."""
    lock = Path(state) / "graph.lock"
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {now_iso()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:  # a crashed holder
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() - t0 > timeout:
                raise SciWeaveError("the project is busy (graph.lock); try again in a moment")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


# ------------------------------------------------------------ list / start --
def list_jobs(p: Project, max_age_done: float = 3600) -> list[dict]:
    out = []
    now = time.time()
    for f in sorted(jobs_dir(p).glob("*.json")):
        j = _read(f)
        if not j:
            continue
        if j["state"] in RUNNING and now - j.get("heartbeat", 0) > HEARTBEAT_STALE:
            j["state"] = "interrupted"  # the process went away (killed, machine slept / shut down)
        if j["state"] not in RUNNING and j["state"] != "interrupted" and now - j.get("heartbeat", now) > max_age_done:
            continue
        out.append(j)
    return out


def start(p: Project, node_id: str, kind: str = "copy") -> dict:
    n = p.node(node_id)
    for j in list_jobs(p):
        if j["node"] == node_id and j["state"] in RUNNING and j["kind"] == kind:
            return j  # already running / queued
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    job = {"id": jid, "node": node_id, "label": n["label"], "kind": kind, "state": "queued", "phase": "queued",
           "bytes_total": 0, "bytes_done": 0, "created": now_iso(), "started": None, "heartbeat": time.time(),
           "pid": None, "path": None, "error": None, "message": None, "ok": None}
    f = jobs_dir(p) / f"{jid}.json"
    _write(f, job)
    log = open(jobs_dir(p) / f"{jid}.log", "w", encoding="utf-8")
    cmd = [sys.executable, "-m", "sciweave", "-C", str(p.root), "job", jid]
    kw = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":  # detached, no console window, not killed with the dashboard
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    job["pid"] = proc.pid
    cur = _read(f) or job  # the runner may already have updated it
    cur["pid"] = proc.pid
    _write(f, cur)
    return cur


# -------------------------------------------------------------------- run --
def run(root: Path, jid: str) -> int:
    """Body of the detached process."""
    p = Project(root)
    f = jobs_dir(p) / f"{jid}.json"
    job = _read(f)
    if not job:
        return 1
    last = [0.0]

    def beat(**fields):
        job.update(fields)
        job["heartbeat"] = time.time()
        if time.time() - last[0] > 0.5 or fields.get("state") in ("done", "error"):
            last[0] = time.time()
            _write(f, job)

    # wait for a free slot (keeps the share and the disk from thrashing on "save all")
    while True:
        active = [j for j in list_jobs(p) if j["id"] != jid and j["state"] in ("copying", "verifying")]
        if len(active) < MAX_PARALLEL:
            break
        beat(state="queued", phase="queued")
        time.sleep(2)
    try:
        beat(state="copying" if job["kind"] == "copy" else "verifying", started=now_iso(), pid=os.getpid())

        def progress(done, total, phase):
            beat(bytes_done=done, bytes_total=total, phase=phase)
        if job["kind"] == "copy":
            rec = p.organize_copy(job["node"], progress=progress)
            with graph_lock(p.state):
                q = Project(root)
                q.organize_record(job["node"], rec, actor="user")
                q.commit()
            beat(state="done", phase="done", path=rec["path"], ok=True,
                 message=f"copied and checked (SHA-256): {rec['path']}")
        else:
            res = p.verify_copy(job["node"], progress=progress)
            with graph_lock(p.state):
                q = Project(root)
                q.record_verification(job["node"], res, actor="user")
                q.commit()
            beat(state="done", phase="done", ok=res["ok"], message=res["message"])
        return 0
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard through the job file
        beat(state="error", phase="error", error=f"{type(exc).__name__}: {exc}")
        return 1
