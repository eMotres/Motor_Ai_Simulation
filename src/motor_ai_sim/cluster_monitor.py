"""Admin -> Servers: cluster load monitor (node agents + app-level metrics).

Every host runs ``deploy/node-agent/motres_node_agent.py`` (stdlib only), which
POSTs one sample every ~15 s to ``POST /api/admin/nodes/metrics`` with a
PER-NODE bearer token.  Tokens are minted/revoked by an admin in the panel and
stored HASHED (sha256) -- the plaintext is shown once at creation.

Storage (``<identity dir>/cluster/`` or ``CLUSTER_MONITOR_DIR``):
  nodes.json      node registry {id: {name, token_hash, created, revoked}}
  history.sqlite  two rolling tables: 1-min buckets kept 24 h, 15-min kept 7 d
The latest full sample per node lives in memory (a restart loses at most one
tick; the agent refills it within 15 s).

App-level metrics (API latency, MCP calls, 429s) are in-memory rings of the last
5 min, fed by :func:`install` (HTTP middleware) and :func:`note_mcp_call`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OFFLINE_AFTER_S = 60.0
FINE_BUCKET_S = 60
FINE_KEEP_S = 24 * 3600
COARSE_BUCKET_S = 15 * 60
COARSE_KEEP_S = 7 * 24 * 3600
APP_WINDOW_S = 300.0
MAX_PROCS = 25
MAX_CONTAINERS = 60
MAX_CORES = 1024
MAX_DISKS = 32
TOKEN_PREFIX = "mnode_"

_LOCK = threading.RLock()
_LATEST: Dict[str, Dict[str, Any]] = {}


# ── storage paths ────────────────────────────────────────────────────────────
def data_dir() -> Path:
    env = os.environ.get("CLUSTER_MONITOR_DIR", "").strip()
    if env:
        p = Path(env)
    else:
        from motor_ai_sim import agent_keys as _ak
        p = _ak._config_dir() / "cluster"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nodes_file() -> Path:
    return data_dir() / "nodes.json"


def _db_file() -> Path:
    return data_dir() / "history.sqlite"


def reset_memory() -> None:
    """Tests: forget the in-memory latest samples and app rings."""
    with _LOCK:
        _LATEST.clear()
        _LAT.clear()
        _MCP.clear()


# ── node registry + tokens ───────────────────────────────────────────────────
def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _load_nodes() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(_nodes_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_nodes(nodes: Dict[str, Dict[str, Any]]) -> None:
    p = _nodes_file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(nodes, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


_ID_RE = re.compile(r"[^a-z0-9_-]+")


def create_node(name: str) -> Tuple[str, str]:
    """Register (or re-key) a node.  Returns (node_id, plaintext token)."""
    nid = _ID_RE.sub("-", (name or "").strip().lower()).strip("-")[:40]
    if not nid:
        raise ValueError("node name required")
    tok = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with _LOCK:
        nodes = _load_nodes()
        nodes[nid] = {"name": name.strip()[:60], "token_hash": _hash(tok),
                      "created": time.time(), "revoked": False}
        _save_nodes(nodes)
    return nid, tok


def revoke_node(nid: str) -> bool:
    with _LOCK:
        nodes = _load_nodes()
        if nid not in nodes:
            return False
        nodes[nid]["revoked"] = True
        nodes[nid]["token_hash"] = ""
        _save_nodes(nodes)
        _LATEST.pop(nid, None)
    return True


def delete_node(nid: str) -> bool:
    with _LOCK:
        nodes = _load_nodes()
        if nid not in nodes:
            return False
        nodes.pop(nid)
        _save_nodes(nodes)
        _LATEST.pop(nid, None)
    return True


def verify_token(authorization: Optional[str]) -> Optional[str]:
    """Bearer -> node id, or None (missing, unknown, revoked)."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    if not tok.startswith(TOKEN_PREFIX):
        return None
    h = _hash(tok)
    for nid, n in _load_nodes().items():
        th = n.get("token_hash") or ""
        if th and not n.get("revoked") and hmac.compare_digest(th, h):
            return nid
    return None


# ── redaction ────────────────────────────────────────────────────────────────
_SECRET_KV = re.compile(
    r"(?i)((?:--?|\b)[\w.-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|apikey|"
    r"auth|credential|private[_-]?key)[\w.-]*\s*[=:\s]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_URL_CRED = re.compile(r"(://[^/\s:@]+:)[^@\s/]+@")
_LONG_BLOB = re.compile(r"\b(?=[A-Za-z0-9_\-+/=]{32,}\b)(?=[^\s]*\d)[A-Za-z0-9_\-+/=]{32,}")
_DROP_KEYS = {"env", "environ", "environment", "cmdline", "args", "argv", "cmd"}


def redact(text: str) -> str:
    s = str(text)
    s = _BEARER.sub(r"\1[redacted]", s)
    s = _URL_CRED.sub(r"\1[redacted]@", s)
    s = _SECRET_KV.sub(r"\1[redacted]", s)
    s = _LONG_BLOB.sub("[redacted]", s)
    return s


def _clean(v: Any, depth: int = 0) -> Any:
    """Recursively drop env/cmdline keys and redact every string."""
    if depth > 6:
        return None
    if isinstance(v, dict):
        return {str(k)[:40]: _clean(x, depth + 1) for k, x in v.items()
                if str(k).lower() not in _DROP_KEYS}
    if isinstance(v, list):
        return [_clean(x, depth + 1) for x in v[:MAX_CORES]]
    if isinstance(v, str):
        return redact(v[:200])
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v if v == v else None   # drop NaN
    return None


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def sanitize_sample(raw: Dict[str, Any]) -> Dict[str, Any]:
    s = _clean(raw if isinstance(raw, dict) else {})
    procs = s.get("procs") if isinstance(s.get("procs"), list) else []
    s["procs"] = sorted([p for p in procs if isinstance(p, dict)],
                        key=lambda p: -_num(p.get("cpu")))[:MAX_PROCS]
    cont = s.get("containers") if isinstance(s.get("containers"), list) else []
    s["containers"] = [c for c in cont if isinstance(c, dict)][:MAX_CONTAINERS]
    disks = s.get("disks") if isinstance(s.get("disks"), list) else []
    s["disks"] = [d for d in disks if isinstance(d, dict)][:MAX_DISKS]
    return s


# ── history (SQLite, two resolutions) ────────────────────────────────────────
_FIELDS = ("cpu", "mem", "load1", "disk", "rx", "tx")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(_db_file()), timeout=5)
    for t in ("fine", "coarse"):
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {t} (node TEXT, ts INTEGER, n INTEGER, "
            + ", ".join(f"{f} REAL" for f in _FIELDS)
            + ", PRIMARY KEY(node, ts))")
    return con


def bucket(ts: float, width: int) -> int:
    return int(ts // width) * width


def point_of(sample: Dict[str, Any]) -> Dict[str, float]:
    """The six numbers a chart needs out of one full sample."""
    mem = sample.get("mem") or {}
    tot = _num(mem.get("total"))
    disks = sample.get("disks") or []
    root = next((d for d in disks if d.get("mount") == "/"), disks[0] if disks else {})
    dt = _num(root.get("total"))
    load = sample.get("load") or [0]
    net = sample.get("net") or {}
    return {
        "cpu": _num((sample.get("cpu") or {}).get("total")),
        "mem": 100.0 * _num(mem.get("used")) / tot if tot else 0.0,
        "load1": _num(load[0] if load else 0),
        "disk": 100.0 * _num(root.get("used")) / dt if dt else 0.0,
        "rx": _num(net.get("rx_bps")),
        "tx": _num(net.get("tx_bps")),
    }


def _merge(con: sqlite3.Connection, table: str, node: str, ts: int,
           pt: Dict[str, float]) -> None:
    """Running mean inside a bucket: new = (old*n + x) / (n+1)."""
    row = con.execute(f"SELECT n, {', '.join(_FIELDS)} FROM {table} "
                      "WHERE node=? AND ts=?", (node, ts)).fetchone()
    if row is None:
        con.execute(f"INSERT INTO {table} VALUES (?,?,?,{','.join('?' * len(_FIELDS))})",
                    (node, ts, 1, *[pt[f] for f in _FIELDS]))
        return
    n = row[0]
    vals = [(row[i + 1] * n + pt[f]) / (n + 1) for i, f in enumerate(_FIELDS)]
    con.execute(f"UPDATE {table} SET n=?, {', '.join(f + '=?' for f in _FIELDS)} "
                "WHERE node=? AND ts=?", (n + 1, *vals, node, ts))


def record_history(node: str, sample: Dict[str, Any], ts: Optional[float] = None) -> None:
    t = time.time() if ts is None else float(ts)
    pt = point_of(sample)
    with _LOCK:
        con = _db()
        try:
            _merge(con, "fine", node, bucket(t, FINE_BUCKET_S), pt)
            _merge(con, "coarse", node, bucket(t, COARSE_BUCKET_S), pt)
            con.execute("DELETE FROM fine WHERE ts < ?", (int(t - FINE_KEEP_S),))
            con.execute("DELETE FROM coarse WHERE ts < ?", (int(t - COARSE_KEEP_S),))
            con.commit()
        finally:
            con.close()


def history(node: str, rng: str = "24h") -> List[Dict[str, float]]:
    table, keep = ("coarse", COARSE_KEEP_S) if rng == "7d" else ("fine", FINE_KEEP_S)
    with _LOCK:
        con = _db()
        try:
            rows = con.execute(
                f"SELECT ts, {', '.join(_FIELDS)} FROM {table} WHERE node=? AND ts>=? "
                "ORDER BY ts", (node, int(time.time() - keep))).fetchall()
        finally:
            con.close()
    return [{"ts": r[0], **{f: round(r[i + 1], 2) for i, f in enumerate(_FIELDS)}}
            for r in rows]


# ── ingest + listing ─────────────────────────────────────────────────────────
def ingest(node: str, raw: Dict[str, Any], now: Optional[float] = None) -> None:
    t = time.time() if now is None else float(now)
    s = sanitize_sample(raw)
    s["received"] = t
    with _LOCK:
        _LATEST[node] = s
    record_history(node, s, t)


def status_of(last: Optional[float], now: Optional[float] = None) -> str:
    if not last:
        return "never"
    t = time.time() if now is None else now
    return "online" if (t - last) <= OFFLINE_AFTER_S else "offline"


def list_nodes(now: Optional[float] = None) -> Dict[str, Any]:
    t = time.time() if now is None else now
    nodes = _load_nodes()
    out: List[Dict[str, Any]] = []
    tot = {"nodes": 0, "online": 0, "cores": 0, "cpu_used_cores": 0.0,
           "mem_used": 0.0, "mem_total": 0.0}
    with _LOCK:
        latest = dict(_LATEST)
    for nid, meta in sorted(nodes.items()):
        s = latest.get(nid)
        st = "revoked" if meta.get("revoked") else status_of(
            s.get("received") if s else None, t)
        item = {"id": nid, "name": meta.get("name") or nid, "status": st,
                "created": meta.get("created"), "sample": s,
                "last_seen": s.get("received") if s else None}
        out.append(item)
        if meta.get("revoked"):
            continue
        tot["nodes"] += 1
        if st == "online" and s:
            tot["online"] += 1
            cpu = s.get("cpu") or {}
            cores = len(cpu.get("per_core") or []) or int(_num(s.get("cores")))
            tot["cores"] += cores
            tot["cpu_used_cores"] += cores * _num(cpu.get("total")) / 100.0
            tot["mem_used"] += _num((s.get("mem") or {}).get("used"))
            tot["mem_total"] += _num((s.get("mem") or {}).get("total"))
    tot["cpu_used_cores"] = round(tot["cpu_used_cores"], 2)
    return {"nodes": out, "cluster": tot, "offline_after_s": OFFLINE_AFTER_S}


# ── app-level metrics ────────────────────────────────────────────────────────
_LAT: "deque[Tuple[float, float]]" = deque(maxlen=50_000)
_MCP: "deque[Tuple[float, int]]" = deque(maxlen=50_000)


def note_latency(ms: float, now: Optional[float] = None) -> None:
    _LAT.append((time.time() if now is None else now, float(ms)))


def note_mcp_call(status: int, now: Optional[float] = None) -> None:
    _MCP.append((time.time() if now is None else now, int(status)))


def _pct(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return round(v[k], 1)


def app_metrics(now: Optional[float] = None) -> Dict[str, Any]:
    t = time.time() if now is None else now
    lat = [ms for ts, ms in list(_LAT) if t - ts <= APP_WINDOW_S]
    mcp = [st for ts, st in list(_MCP) if t - ts <= APP_WINDOW_S]
    mins = APP_WINDOW_S / 60.0
    return {"window_s": APP_WINDOW_S,
            "api": {"requests": len(lat), "p50_ms": _pct(lat, 0.5),
                    "p95_ms": _pct(lat, 0.95)},
            "mcp": {"calls_per_min": round(len(mcp) / mins, 2),
                    "throttled_429": sum(1 for s in mcp if s == 429)}}


def jobs_view() -> Dict[str, Any]:
    """Queue view for the admin: every account's running/queued jobs."""
    from motor_ai_sim import jobs as _J
    from motor_ai_sim import progress as _P
    q = _J.queue()
    snap = q.snapshot() if hasattr(q, "snapshot") else {"items": []}
    items = []
    now = time.time()
    per_user: Dict[str, Dict[str, int]] = {}
    oldest_wait = 0.0
    for it in snap.get("items") or []:
        body = it.get("body") if isinstance(it.get("body"), dict) else {}
        agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
        row = {"run_id": it.get("run_id"), "owner": it.get("owner"),
               "kind": it.get("kind"), "state": it.get("state"),
               "position": it.get("position"), "elapsed_s": it.get("elapsed_s"),
               "waited_s": it.get("waited_s"),
               "agent": (agent or {}).get("client_name") if agent else None}
        e = _P.registry().entry(str(it.get("run_id") or ""))
        if e is not None:
            p = e.snapshot()
            row["eta_s"] = p.get("eta_s")
            row["frac"] = p.get("frac")
            row["phase"] = redact(str(p.get("phase") or ""))
        try:
            from motor_ai_sim import job_usage as _U
            lv = _U.live(str(it.get("run_id") or ""))
            if lv:
                row["cpu_s"] = lv["cpu_s"]
        except Exception:                               # noqa: BLE001
            pass
        u = per_user.setdefault(str(it.get("owner")), {"running": 0, "queued": 0, "agent": 0})
        if it.get("state") == "running":
            u["running"] += 1
        else:
            u["queued"] += 1
            if it.get("queued_at"):
                oldest_wait = max(oldest_wait, now - float(it["queued_at"]))
        if agent:
            u["agent"] += 1
        items.append(row)
    return {"running": snap.get("running", 0), "queued": snap.get("queued", 0),
            "workers": snap.get("workers"), "oldest_wait_s": round(oldest_wait, 1),
            "per_user": per_user, "items": items}


def install(app) -> None:
    """Latency middleware: time every /api and /mcp request (not the ingest)."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class _LatencyMW(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            p = request.url.path
            if not (p.startswith("/api") or p.startswith("/mcp")) \
                    or p == "/api/admin/nodes/metrics":
                return await call_next(request)
            t0 = time.perf_counter()
            resp = await call_next(request)
            note_latency((time.perf_counter() - t0) * 1000.0)
            if request.method in ("GET", "POST") and 200 <= resp.status_code < 300:
                try:  # pricing data: aggregate activity counters only
                    from motor_ai_sim import usage_stats as _US
                    if _US.event_for(request.method, p) is not None:
                        import asyncio
                        await asyncio.to_thread(
                            _US.note_request, request.method, p, resp.status_code,
                            request.headers.get("authorization"))
                except Exception:                       # noqa: BLE001
                    pass
            return resp

    app.add_middleware(_LatencyMW)
