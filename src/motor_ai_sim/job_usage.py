"""Per-job machine-time accounting (Admin -> Servers -> Usage).

Every job the queue runs (``jobs.InProcessQueue._run_admitted``) is metered:
wall time, CPU-seconds, peak RSS, and who asked (account + client: the MCP
key / OAuth client name, or "web").  One row per finished job goes into the
cluster store (``history.sqlite``, table ``usage``); nothing is backfilled.

CPU-seconds are MEASURED, not estimated.  Solvers run inside the API process
(threads + BLAS threads + worker subprocesses), so the meter reads the CPU
times of the whole API process tree (psutil: self + live descendants + reaped
children).  A background sampler (every ``SAMPLE_S``) takes the tree's CPU
delta and gives it to the jobs running in that interval:

* one job running -> it gets the whole delta (``cpu_method = "exclusive"``);
* several -> the delta is split in proportion to each job thread's own CPU
  time in the interval (equal split if all are ~0) (``"apportioned"``).
  A solve-pool child (``solve_pool``, ``SOLVE_POOL=1``) counts towards the job
  it solves for; a child of an un-metered job goes straight to its account.

Peak RSS is the tree's peak resident memory seen while the job ran (shared by
concurrent jobs -- it is the machine's footprint, not a private one).
"""
from __future__ import annotations

import csv
import io
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional

SAMPLE_S = 2.0
#: Admin -> Overview live load: how often the (minute, node, user, client)
#: CPU-seconds accumulator is flushed to the store. The per-job weights come
#: from the existing 2 s _tick(); this just controls write frequency.
ATTRIB_FLUSH_S = 12.0
_LOCK = threading.RLock()
_ACTIVE: Dict[str, "Meter"] = {}
_SAMPLER: Optional[threading.Thread] = None
_ATTRIB_FLUSHER: Optional[threading.Thread] = None
_LAST_TREE: Optional[float] = None
_SEEN_CHILD: Dict[int, float] = {}
#: (minute_bucket, node, user, client) -> accumulated CPU-seconds not yet flushed.
_USER_ACC: Dict[tuple, float] = {}

STATUS = {"done": "done", "cancelled": "stopped", "failed": "failed"}


def node_name() -> str:
    return os.environ.get("NODE_NAME", "").strip() or socket.gethostname()


# ── process-tree probes (psutil; zero when unavailable) ─────────────────────
_PROC: Any = None
_LIVE_CHILD_CPU = 0.0
_LAST_RSS = 0


def _proc():
    global _PROC
    if _PROC is None or getattr(_PROC, "pid", None) != os.getpid():
        try:
            import psutil
            _PROC = psutil.Process()
        except Exception:                               # noqa: BLE001
            return None
    return _PROC


def tree_cpu_s(fast: bool = False) -> float:
    """Monotonic CPU-seconds of this process + all descendants, ever.

    ``fast`` (the job-start/finish path) skips enumerating descendants and uses
    the live-children sum of the last full sample: cheap enough to sit on
    every job, exact for the process itself."""
    global _LIVE_CHILD_CPU
    p = _proc()
    if p is None:
        t = os.times()
        return t.user + t.system
    try:
        ct = p.cpu_times()
        total = ct.user + ct.system + getattr(ct, "children_user", 0.0) \
            + getattr(ct, "children_system", 0.0)
    except Exception:                                   # noqa: BLE001
        return 0.0
    if fast:
        return total + _LIVE_CHILD_CPU
    # live descendants are not in children_* until reaped: add their own times,
    # and keep the last value of each so an exit between samples is not lost
    # twice (once reaped it moves into children_*).
    live: Dict[int, float] = {}
    try:
        for c in p.children(recursive=True):
            try:
                cc = c.cpu_times()
                live[c.pid] = cc.user + cc.system
            except Exception:                           # noqa: BLE001
                continue
    except Exception:                                   # noqa: BLE001
        pass
    _SEEN_CHILD.clear()
    _SEEN_CHILD.update(live)
    _LIVE_CHILD_CPU = sum(live.values())
    return total + _LIVE_CHILD_CPU


def tree_rss() -> int:
    global _LAST_RSS
    p = _proc()
    if p is None:
        return 0
    try:
        rss = p.memory_info().rss
        for c in p.children(recursive=True):
            try:
                rss += c.memory_info().rss
            except Exception:                           # noqa: BLE001
                continue
        _LAST_RSS = int(rss)
        return _LAST_RSS
    except Exception:                                   # noqa: BLE001
        return 0


def _own_rss() -> int:
    """This process's RSS (cheap) or the last full tree reading, whichever is larger."""
    p = _proc()
    try:
        return max(_LAST_RSS, int(p.memory_info().rss)) if p is not None else _LAST_RSS
    except Exception:                                   # noqa: BLE001
        return _LAST_RSS


def _thread_cpu(native_id: int) -> float:
    p = _proc()
    if p is None:
        return 0.0
    try:
        for th in p.threads():
            if th.id == native_id:
                return th.user_time + th.system_time
    except Exception:                                   # noqa: BLE001
        pass
    return 0.0


# ── the meter ────────────────────────────────────────────────────────────────
class Meter:
    def __init__(self, rec: Any) -> None:
        body = rec.body if isinstance(getattr(rec, "body", None), dict) else {}
        agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
        self.run_id = str(rec.run_id)
        self.user = str(rec.owner or "")
        self.client = str((agent or {}).get("client_name") or "agent") if agent else "web"
        self.kind = str(rec.kind or "")
        self.machine = str(body.get("duty") or body.get("machine") or body.get("config")
                           or body.get("die") or body.get("design_id") or "")[:80]
        self.node = node_name()
        self.t0 = time.time()
        self.wait_s = max(0.0, float(getattr(rec, "started_at", 0) or self.t0)
                          - float(getattr(rec, "queued_at", 0) or self.t0))
        self.native_id = threading.get_native_id()
        self.thread_cpu_last = time.thread_time()   # we are ON the job thread
        self.cpu_s = 0.0
        self.shared = False
        self.peak_rss = _own_rss()

    def live(self) -> Dict[str, Any]:
        return {"cpu_s": round(self.cpu_s, 1), "peak_rss": self.peak_rss,
                "wall_s": round(time.time() - self.t0, 1)}


# ── solve-pool children (motor_ai_sim.solve_pool) ────────────────────────────
# A pooled solve runs in a CHILD process while the job's own thread only waits,
# so the job thread's CPU time no longer says whose work the tree's CPU was.
# The pool registers every child with the run and the account it solves for
# and reports its CPU-seconds as it samples them; a full tick then weights each
# job by its thread time PLUS its children's, and a child whose run has no
# meter (a campaign admitted with ``jobs.admit``) is credited to its account
# directly.  pid -> {run_id, user, client, cpu, credited, done}
_CHILDREN: Dict[int, Dict[str, Any]] = {}


def child_started(pid: int, run_id: str, user: str, client: str = "web",
                  sampler: bool = True) -> None:
    with _LOCK:
        _CHILDREN[int(pid)] = {"run_id": str(run_id or ""), "user": str(user or ""),
                               "client": str(client or "web"), "cpu": 0.0,
                               "credited": 0.0, "done": False}
    if sampler:
        _ensure_sampler()


def child_cpu(pid: int, cpu_s: float) -> None:
    with _LOCK:
        c = _CHILDREN.get(int(pid))
        if c is not None:
            c["cpu"] = max(c["cpu"], float(cpu_s or 0.0))


def child_finished(pid: int, cpu_s: Optional[float] = None) -> None:
    """The child exited; its last CPU reading is credited at the next tick."""
    with _LOCK:
        c = _CHILDREN.get(int(pid))
        if c is not None:
            if cpu_s is not None:
                c["cpu"] = max(c["cpu"], float(cpu_s))
            c["done"] = True


def _children_split(delta: float, minute: int) -> tuple:
    """(cpu per metered run id, CPU credited directly to un-metered owners)."""
    by_run: Dict[str, float] = {}
    orphan = 0.0
    for pid, c in list(_CHILDREN.items()):
        d = max(0.0, c["cpu"] - c["credited"])
        if c["done"]:
            _CHILDREN.pop(pid, None)
        rid = c["run_id"]
        if rid and rid in _ACTIVE:
            c["credited"] = c["cpu"]
            if d > 0.0:
                by_run[rid] = by_run.get(rid, 0.0) + d
            continue
        # Never credit more than the tree actually measured in this interval;
        # what does not fit yet (the pool sampled the child after the tree
        # did) is carried to the next tick while the child lives.
        got = min(d, max(0.0, delta - orphan))
        c["credited"] += got
        if got > 0.0:
            orphan += got
            key = (minute, node_name(), c["user"], c["client"])
            _USER_ACC[key] = _USER_ACC.get(key, 0.0) + got
    return by_run, orphan


def _tick(fast: bool = False, own: Optional["Meter"] = None) -> None:
    """Hand the tree's CPU delta since the last tick to the active jobs.

    The sampler does full ticks (descendants, RSS, per-thread weights, and the
    solve-pool children's own CPU, see ``_CHILDREN``).  A job start/finish
    does a FAST tick on the job's own thread: process CPU only, its own thread
    time from ``time.thread_time``; the other jobs' weights fall back to an
    equal split of that (at most ``SAMPLE_S``-long) slice."""
    global _LAST_TREE
    with _LOCK:
        now_tree = tree_cpu_s(fast=fast)
        rss = _own_rss() if fast else tree_rss()
        delta = 0.0 if _LAST_TREE is None else max(0.0, now_tree - _LAST_TREE)
        _LAST_TREE = now_tree
        minute = int(time.time() // 60) * 60
        child_by_run: Dict[str, float] = {}
        if not fast and _CHILDREN:
            child_by_run, orphan = _children_split(delta, minute)
            delta = max(0.0, delta - orphan)
        ms = list(_ACTIVE.values())
        if not ms:
            return
        weights = []
        for m in ms:
            if fast:
                if m is own:
                    tc = time.thread_time()
                    weights.append(max(0.0, tc - m.thread_cpu_last))
                    m.thread_cpu_last = tc
                else:
                    weights.append(0.0)
                m.peak_rss = max(m.peak_rss, rss)
                continue
            tc = _thread_cpu(m.native_id)
            weights.append(max(0.0, tc - m.thread_cpu_last)
                           + child_by_run.get(m.run_id, 0.0))
            m.thread_cpu_last = tc
            m.peak_rss = max(m.peak_rss, rss)
        tot = sum(weights)
        if fast and len(ms) > 1:
            tot = 0.0                                   # unknown weights -> equal
        for m, w in zip(ms, weights):
            share = (w / tot) if tot > 1e-6 else 1.0 / len(ms)
            got = delta * share
            m.cpu_s += got
            if len(ms) > 1:
                m.shared = True
            if got > 0:
                key = (minute, m.node, m.user, m.client)
                _USER_ACC[key] = _USER_ACC.get(key, 0.0) + got


def _sampler_loop() -> None:
    while True:
        time.sleep(SAMPLE_S)
        try:
            _tick()
        except Exception:                               # noqa: BLE001
            pass


def _ensure_sampler() -> None:
    global _SAMPLER
    if _SAMPLER is None or not _SAMPLER.is_alive():
        _SAMPLER = threading.Thread(target=_sampler_loop, daemon=True, name="job-usage")
        _SAMPLER.start()
    _ensure_attrib_flusher()


# ── per-minute (node, user, client) CPU attribution (Admin -> Overview live load) ──
LOAD_FINE_BUCKET_S = 60
LOAD_FINE_KEEP_S = 24 * 3600
LOAD_COARSE_BUCKET_S = 15 * 60
LOAD_COARSE_KEEP_S = 7 * 24 * 3600


def flush_user_attribution(now: Optional[float] = None) -> int:
    """Drain the in-memory (minute, node, user, client) accumulator to the store."""
    t = time.time() if now is None else now
    with _LOCK:
        acc = dict(_USER_ACC)
        _USER_ACC.clear()
    if not acc:
        return 0
    from motor_ai_sim import cluster_monitor as CM
    con = _db()
    try:
        for (minute, node, user, client), cpu_s in acc.items():
            coarse = int(minute // LOAD_COARSE_BUCKET_S) * LOAD_COARSE_BUCKET_S
            for table, bucket in (("load_by_user_fine", minute),
                                  ("load_by_user_coarse", coarse)):
                con.execute(
                    f"INSERT INTO {table} (bucket, node, user, client, cpu_s) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(bucket, node, user, client) "
                    "DO UPDATE SET cpu_s = cpu_s + excluded.cpu_s",
                    (bucket, node, user, client, cpu_s))
        con.execute("DELETE FROM load_by_user_fine WHERE bucket < ?",
                    (int(t - LOAD_FINE_KEEP_S),))
        con.execute("DELETE FROM load_by_user_coarse WHERE bucket < ?",
                    (int(t - LOAD_COARSE_KEEP_S),))
        con.commit()
    finally:
        con.close()
    return len(acc)


def _attrib_flush_loop() -> None:
    while True:
        time.sleep(ATTRIB_FLUSH_S)
        try:
            flush_user_attribution()
        except Exception:                               # noqa: BLE001
            pass


def _ensure_attrib_flusher() -> None:
    global _ATTRIB_FLUSHER
    if _ATTRIB_FLUSHER is None or not _ATTRIB_FLUSHER.is_alive():
        _ATTRIB_FLUSHER = threading.Thread(target=_attrib_flush_loop, daemon=True,
                                           name="job-usage-attrib")
        _ATTRIB_FLUSHER.start()


def user_load_series(start: float, end: float, top_n: int = 8) -> Dict[str, Any]:
    """Stacked CPU-seconds series for Admin -> Overview live load, grouped by
    (user, client), top ``top_n`` by total CPU + an "other" bucket."""
    fine_cutoff = time.time() - LOAD_FINE_BUCKET_S * 1440   # 24 h of fine data
    table = "load_by_user_fine" if start >= fine_cutoff else "load_by_user_coarse"
    width = LOAD_FINE_BUCKET_S if table == "load_by_user_fine" else LOAD_COARSE_BUCKET_S
    con = _db()
    try:
        rows = con.execute(
            f"SELECT bucket, node, user, client, cpu_s FROM {table} "
            "WHERE bucket >= ? AND bucket < ? ORDER BY bucket",
            (int(start), int(end))).fetchall()
    finally:
        con.close()
    totals: Dict[str, float] = {}
    for _b, _n, user, client, cpu_s in rows:
        totals[user] = totals.get(user, 0.0) + cpu_s
    top = sorted(totals, key=lambda u: -totals[u])[:max(0, int(top_n))]
    top_set = set(top)
    buckets: Dict[int, Dict[str, float]] = {}
    for bucket, _node, user, _client, cpu_s in rows:
        key = user if user in top_set else "other"
        b = buckets.setdefault(int(bucket), {})
        b[key] = b.get(key, 0.0) + cpu_s
    series = []
    for b in sorted(buckets):
        pt: Dict[str, Any] = {"ts": b}
        for u, s in buckets[b].items():
            pt[u] = round(100.0 * s / width, 2)   # CPU-seconds -> CPU %
        series.append(pt)
    return {"users": top, "bucket_s": width, "series": series}


def node_bucket_cpu_s(start: float, end: float) -> Dict[tuple, float]:
    """(node, bucket) -> summed app job CPU-seconds in [start, end).

    Used by cluster_monitor.outside_app_series to subtract the CPU already
    attributed to users from the app container's own docker-stats CPU, so the
    remainder ("app-overhead") is not double-counted as a user's or an
    outside-app container's load."""
    fine_cutoff = time.time() - LOAD_FINE_BUCKET_S * 1440
    table = "load_by_user_fine" if start >= fine_cutoff else "load_by_user_coarse"
    con = _db()
    try:
        rows = con.execute(f"SELECT bucket, node, cpu_s FROM {table} WHERE bucket >= ? AND bucket < ?",
                           (int(start), int(end))).fetchall()
    finally:
        con.close()
    out: Dict[tuple, float] = {}
    for bkt, node, cpu_s in rows:
        key = (node, int(bkt))
        out[key] = out.get(key, 0.0) + (cpu_s or 0.0)
    return out


def start(rec: Any, sampler: bool = True) -> Optional[Meter]:
    try:
        with _LOCK:
            _tick(fast=True)        # close the previous interval before joining
            m = Meter(rec)
            _ACTIVE[m.run_id] = m
        if sampler:
            _ensure_sampler()
        return m
    except Exception:                                   # noqa: BLE001
        return None


def finish(m: Optional[Meter], state: str) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    try:
        with _LOCK:
            _tick(fast=True, own=m)
            _ACTIVE.pop(m.run_id, None)
        row = {"run_id": m.run_id, "ts_start": m.t0, "ts_end": time.time(),
               "user": m.user, "client": m.client, "kind": m.kind,
               "machine": m.machine, "node": m.node,
               "wall_s": round(time.time() - m.t0, 2), "cpu_s": round(m.cpu_s, 2),
               "peak_rss": int(m.peak_rss),
               "status": STATUS.get(str(state), str(state)),
               "cpu_method": "apportioned" if m.shared else "exclusive",
               "wait_s": round(m.wait_s, 2)}
        record(row)
        return row
    except Exception:                                   # noqa: BLE001
        return None


def live(run_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        m = _ACTIVE.get(str(run_id))
        return m.live() if m else None


def reset() -> None:
    global _LAST_TREE
    with _LOCK:
        _ACTIVE.clear()
        _LAST_TREE = None
        _USER_ACC.clear()
        _CHILDREN.clear()


# ── store ────────────────────────────────────────────────────────────────────
#: ``own_node`` = 1 for a job that ran on a USER-OWNED compute node
#: (compute_nodes / docs/BYO_COMPUTE.md): recorded for the owner's own view,
#: never counted against fair use (:func:`cpu_hours`).
_COLS = ("run_id", "ts_start", "ts_end", "user", "client", "kind", "machine",
         "node", "wall_s", "cpu_s", "peak_rss", "status", "cpu_method", "wait_s",
         "own_node")


def _db():
    from motor_ai_sim import cluster_monitor as CM
    con = CM._db()
    con.execute("CREATE TABLE IF NOT EXISTS usage (run_id TEXT PRIMARY KEY, ts_start REAL, "
                "ts_end REAL, user TEXT, client TEXT, kind TEXT, machine TEXT, node TEXT, "
                "wall_s REAL, cpu_s REAL, peak_rss INTEGER, status TEXT, cpu_method TEXT, "
                "wait_s REAL DEFAULT 0, own_node INTEGER DEFAULT 0)")
    have = [r[1] for r in con.execute("PRAGMA table_info(usage)")]
    if "wait_s" not in have:
        con.execute("ALTER TABLE usage ADD COLUMN wait_s REAL DEFAULT 0")
    if "own_node" not in have:
        con.execute("ALTER TABLE usage ADD COLUMN own_node INTEGER DEFAULT 0")
    con.execute("CREATE INDEX IF NOT EXISTS usage_end ON usage(ts_end)")
    for table in ("load_by_user_fine", "load_by_user_coarse"):
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (bucket INTEGER, node TEXT, user TEXT, "
            "client TEXT, cpu_s REAL DEFAULT 0, PRIMARY KEY(bucket, node, user, client))")
    return con


def record(row: Dict[str, Any]) -> None:
    con = _db()
    vals = tuple(int(row.get(c) or 0) if c == "own_node" else row.get(c) for c in _COLS)
    try:
        con.execute(f"INSERT OR REPLACE INTO usage ({','.join(_COLS)}) "
                    f"VALUES ({','.join('?' * len(_COLS))})", vals)
        con.commit()
    finally:
        con.close()


def jobs(start: float, end: float, user: Optional[str] = None,
         client: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
    q = f"SELECT {','.join(_COLS)} FROM usage WHERE ts_end >= ? AND ts_end < ?"
    a: List[Any] = [start, end]
    if user is not None:
        q += " AND user = ?"
        a.append(user)
    if client is not None:
        q += " AND client = ?"
        a.append(client)
    q += " ORDER BY cpu_s DESC LIMIT ?"
    a.append(int(limit))
    con = _db()
    try:
        rows = con.execute(q, a).fetchall()
    finally:
        con.close()
    return [dict(zip(_COLS, r)) for r in rows]


def cluster_cores() -> int:
    try:
        from motor_ai_sim import cluster_monitor as CM
        c = sum(int((n.get("sample") or {}).get("cores") or 0)
                for n in CM.list_nodes()["nodes"] if n["status"] != "revoked")
        if c:
            return c
    except Exception:                                   # noqa: BLE001
        pass
    return os.cpu_count() or 1


def summary(start: float, end: float, by: str = "user",
            cores: Optional[int] = None) -> Dict[str, Any]:
    key = "client" if by == "client" else "user"
    # platform capacity only: user-owned node CPU is not the cluster's
    rows = [r for r in jobs(start, end, limit=10 ** 7) if not r.get("own_node")]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = r[key] or "—"
        a = agg.setdefault(k, {"key": k, "jobs": 0, "cpu_s": 0.0, "wall_s": 0.0,
                               "peak_rss": 0})
        a["jobs"] += 1
        a["cpu_s"] += r["cpu_s"] or 0.0
        a["wall_s"] += r["wall_s"] or 0.0
        a["peak_rss"] = max(a["peak_rss"], r["peak_rss"] or 0)
    ncores = cores or cluster_cores()
    capacity = max(1e-9, ncores * max(1.0, end - start))
    tot_cpu = sum(a["cpu_s"] for a in agg.values())
    out = []
    for a in agg.values():
        out.append({"key": a["key"], "jobs": a["jobs"],
                    "cpu_h": round(a["cpu_s"] / 3600, 4),
                    "wall_h": round(a["wall_s"] / 3600, 4),
                    "peak_rss": a["peak_rss"],
                    "share_cluster_pct": round(100 * a["cpu_s"] / capacity, 3),
                    "share_jobs_pct": round(100 * a["cpu_s"] / tot_cpu, 2) if tot_cpu else 0.0})
    out.sort(key=lambda x: -x["cpu_h"])
    return {"by": key, "start": start, "end": end, "cores": ncores, "rows": out,
            "total_cpu_h": round(tot_cpu / 3600, 4)}


def cpu_hours(user: str, start: float, end: float, fair_use: bool = True) -> float:
    """Usage-statistics groundwork: one account's CPU-hours in [start, end).

    ``fair_use=True`` (the fair-use question) leaves out the jobs that ran on the
    user's OWN compute nodes: that CPU was theirs, not the platform's.
    ``fair_use=False`` counts everything (the owner's "how much did I compute").
    """
    return round(sum(r["cpu_s"] or 0.0 for r in jobs(start, end, user=user,
                                                     limit=10 ** 7)
                     if not (fair_use and r.get("own_node"))) / 3600, 6)


def to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v)
                        for k, v in r.items()})
    return buf.getvalue()
