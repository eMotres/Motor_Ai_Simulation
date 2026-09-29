"""User-owned compute nodes ("bring your own compute"), Stage 1.

Design: ``docs/BYO_COMPUTE.md``.  In one paragraph: a user rents a server,
adds it on the "My compute nodes" page, gets a node token (shown once), and
runs ``deploy/compute-worker`` there.  The worker connects OUTBOUND only: it
heartbeats, leases jobs that belong to ITS OWNER, runs the same solver code
(same platform version, or it is refused), streams progress and posts the
result + provenance back.  The platform files the result into the owner's
workspace exactly like a server-run job, and records the CPU time as
own-node time (not counted against fair use).

Pieces, all in this module so the routes stay thin:

* node registry (``user_nodes.json``): ``{id: {name, owner, token_hash,
  created, revoked, version, last_seen}}``.  Tokens are ``mcnode_...``, stored
  hashed (sha256), shown once.  A token maps to exactly ONE owner.
* routing preferences (``node_prefs.json``): per owner, one of
  :data:`PREFS`.
* remote job store (``remote_jobs.json``): records with lease + attempts.
  Every lease-path read reaps expired leases first (re-queue, then fail after
  :data:`MAX_ATTEMPTS`).
* bundle signing: HMAC-SHA256 over the canonical JSON bundle, keyed by the
  node token's sha256 (which both sides know and nobody else does).  The
  worker verifies before running; the platform verifies the result the same
  way.  Stage 2 moves to an Ed25519 platform key (see the design doc).
* audit log (``node_audit.jsonl``): one line per create/revoke/lease/
  complete/fail/cancel/refusal.

Storage lives next to the #34 cluster store (``cluster_monitor.data_dir()``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim import cluster_monitor as CM

TOKEN_PREFIX = "mcnode_"
#: Routing preference values (per user; a job may override).
PREF_PLATFORM = "platform_only"
PREF_OWN_FIRST = "own_first"
PREF_OWN_ONLY = "own_only"
PREFS = (PREF_PLATFORM, PREF_OWN_FIRST, PREF_OWN_ONLY)
DEFAULT_PREF = PREF_PLATFORM

LEASE_S = 120.0                 # a lease lapses unless progress renews it
MAX_ATTEMPTS = 3                # re-queues after a lapsed lease, then failed
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_NODES_PER_USER = 20
OFFLINE_AFTER_S = CM.OFFLINE_AFTER_S
#: The history key prefix, so a user node can never collide with an admin node.
HIST_PREFIX = "u:"
RESULTS_DIR = "remote_results"

# job states
QUEUED, LEASED, DONE, FAILED, CANCELLED = ("queued", "leased", "done",
                                           "failed", "cancelled")
_OPEN = (QUEUED, LEASED)

_LOCK = threading.RLock()
_LATEST: Dict[str, Dict[str, Any]] = {}


class Refused(Exception):
    """A node asked for something it may not have (→ 403/404/409)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ─────────────────────────────────────────────────────────────────────────────
#  version + storage
# ─────────────────────────────────────────────────────────────────────────────

def platform_version() -> str:
    """The repo-root VERSION (the same single source ``/api/version`` reads).
    ``MOTRES_PLATFORM_VERSION`` overrides it (tests, container builds)."""
    env = os.environ.get("MOTRES_PLATFORM_VERSION", "").strip()
    if env:
        return env
    try:
        return (Path(__file__).resolve().parent.parent.parent / "VERSION"
                ).read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def _f(name: str) -> Path:
    return CM.data_dir() / name


def _load(name: str) -> Any:
    try:
        return json.loads(_f(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(name: str, data: Any) -> None:
    p = _f(name)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def audit(event: str, **kw: Any) -> None:
    row = {"ts": time.time(), "event": event}
    row.update({k: v for k, v in kw.items() if v is not None})
    try:
        with open(_f("node_audit.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def audit_tail(owner: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    try:
        lines = _f("node_audit.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if owner is not None and r.get("owner") != owner:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def reset_memory() -> None:
    with _LOCK:
        _LATEST.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  node registry
# ─────────────────────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"[^a-z0-9_-]+")


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def create_node(owner: str, name: str) -> Tuple[str, str]:
    """Mint a node for ``owner``.  Returns (node_id, plaintext token)."""
    owner = str(owner or "").strip().lower()
    if not owner:
        raise ValueError("owner required")
    slug = _ID_RE.sub("-", (name or "").strip().lower()).strip("-")[:30]
    if not slug:
        raise ValueError("node name required")
    tok = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with _LOCK:
        nodes = _load("user_nodes.json")
        mine = [n for n in nodes.values() if n.get("owner") == owner and not n.get("revoked")]
        if len(mine) >= MAX_NODES_PER_USER:
            raise ValueError("node limit reached (%d)" % MAX_NODES_PER_USER)
        nid = "%s-%s" % (slug, secrets.token_hex(3))
        nodes[nid] = {"name": name.strip()[:60], "owner": owner,
                      "token_hash": _hash(tok), "created": time.time(),
                      "revoked": False, "version": "", "last_seen": None}
        _save("user_nodes.json", nodes)
    audit("node.create", owner=owner, node=nid)
    return nid, tok


def _node(nid: str) -> Optional[Dict[str, Any]]:
    return _load("user_nodes.json").get(nid)


def revoke_node(nid: str, owner: Optional[str]) -> bool:
    """Revoke; ``owner=None`` means an admin (any node).  Its leased jobs are
    put back in the queue at once."""
    with _LOCK:
        nodes = _load("user_nodes.json")
        n = nodes.get(nid)
        if n is None or (owner is not None and n.get("owner") != owner):
            return False
        n["revoked"] = True
        n["token_hash"] = ""
        _save("user_nodes.json", nodes)
        _LATEST.pop(nid, None)
        jobs = _load("remote_jobs.json")
        for j in jobs.values():
            if j.get("node") == nid and j.get("state") == LEASED:
                _requeue_locked(j, "node revoked")
        _save("remote_jobs.json", jobs)
    audit("node.revoke", owner=n.get("owner"), node=nid)
    return True


def delete_node(nid: str, owner: Optional[str]) -> bool:
    if not revoke_node(nid, owner):
        return False
    with _LOCK:
        nodes = _load("user_nodes.json")
        nodes.pop(nid, None)
        _save("user_nodes.json", nodes)
    return True


def verify_token(authorization: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Bearer -> (node_id, node) or None.  Only ``mcnode_`` tokens; the admin
    ``mnode_`` metrics tokens are a different credential and never lease."""
    if not isinstance(authorization, str):
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    if not tok.startswith(TOKEN_PREFIX):
        return None
    h = _hash(tok)
    for nid, n in _load("user_nodes.json").items():
        th = n.get("token_hash") or ""
        if th and not n.get("revoked") and hmac.compare_digest(th, h):
            return nid, n
    return None


def heartbeat(nid: str, sample: Dict[str, Any], version: str = "",
              now: Optional[float] = None) -> Dict[str, Any]:
    """A node's periodic sample: the #34 pipeline, keyed ``u:<id>``."""
    t = time.time() if now is None else float(now)
    s = CM.sanitize_sample(sample if isinstance(sample, dict) else {})
    s["received"] = t
    with _LOCK:
        _LATEST[nid] = s
        nodes = _load("user_nodes.json")
        if nid in nodes:
            nodes[nid]["last_seen"] = t
            nodes[nid]["version"] = str(version or "")[:40]
            _save("user_nodes.json", nodes)
    try:
        CM.record_history(HIST_PREFIX + nid, s, t)
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, "platform_version": platform_version(),
            "version_ok": str(version or "") == platform_version()}


def _status(n: Dict[str, Any], now: float) -> str:
    if n.get("revoked"):
        return "revoked"
    return CM.status_of(n.get("last_seen"), now)


def list_nodes(owner: Optional[str], now: Optional[float] = None) -> List[Dict[str, Any]]:
    """``owner=None`` → every node (admin view)."""
    t = time.time() if now is None else now
    with _LOCK:
        latest = dict(_LATEST)
    out = []
    for nid, n in sorted(_load("user_nodes.json").items()):
        if owner is not None and n.get("owner") != owner:
            continue
        s = latest.get(nid) or {}
        mem = s.get("mem") or {}
        out.append({"id": nid, "name": n.get("name") or nid, "owner": n.get("owner"),
                    "status": _status(n, t), "created": n.get("created"),
                    "last_seen": n.get("last_seen"), "version": n.get("version") or "",
                    "version_ok": (n.get("version") or "") == platform_version(),
                    "cores": s.get("cores") or len((s.get("cpu") or {}).get("per_core") or []),
                    "cpu_pct": (s.get("cpu") or {}).get("total"),
                    "mem_used": mem.get("used"), "mem_total": mem.get("total")})
    return out


def online_nodes(owner: str, now: Optional[float] = None) -> List[str]:
    return [n["id"] for n in list_nodes(owner, now) if n["status"] == "online"
            and n["version_ok"]]


# ─────────────────────────────────────────────────────────────────────────────
#  preferences + routing
# ─────────────────────────────────────────────────────────────────────────────

def get_pref(owner: str) -> str:
    p = _load("node_prefs.json").get(str(owner or ""))
    return p if p in PREFS else DEFAULT_PREF


def set_pref(owner: str, pref: str) -> str:
    if pref not in PREFS:
        raise ValueError("preference must be one of %s" % ", ".join(PREFS))
    with _LOCK:
        d = _load("node_prefs.json")
        d[str(owner)] = pref
        _save("node_prefs.json", d)
    audit("pref.set", owner=owner, pref=pref)
    return pref


def remotable_kinds() -> List[str]:
    from motor_ai_sim import jobs as _J
    return sorted(set(_J.HANDLERS))


def route(owner: str, kind: str, pref: Optional[str] = None,
          now: Optional[float] = None) -> str:
    """``"remote"`` or ``"platform"`` for one job.

    * platform_only → platform;
    * own_only → remote (waits in the queue until one of the owner's nodes
      leases it, even if none is online now);
    * own_first → remote if an own node is online with the right version,
      else platform (fair use).
    A kind without a registered handler can only run on the platform.
    """
    p = pref if pref in PREFS else get_pref(owner)
    if kind not in remotable_kinds() or p == PREF_PLATFORM:
        return "platform"
    if p == PREF_OWN_ONLY:
        return "remote"
    return "remote" if online_nodes(owner, now) else "platform"


# ─────────────────────────────────────────────────────────────────────────────
#  remote job store
# ─────────────────────────────────────────────────────────────────────────────

def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(token_hash: str, obj: Any) -> str:
    return hmac.new(token_hash.encode("ascii"), _canon(obj), hashlib.sha256).hexdigest()


def submit(owner: str, ws_id: str, ws_root: str, kind: str, body: Dict[str, Any],
           run_id: str = "", priority: int = 2) -> Dict[str, Any]:
    """Queue a job for the owner's nodes.  ``body`` must be pure JSON (it is
    the bundle: machine description / config / duty inputs of THIS owner)."""
    from motor_ai_sim import jobs as _J
    rid = run_id or _J.new_run_id(str(kind).split(".")[0])
    json.dumps(body)                                    # must be JSON
    rec = {"run_id": rid, "owner": owner, "ws_id": ws_id, "ws_root": ws_root,
           "kind": kind, "body": body, "priority": int(priority),
           "state": QUEUED, "queued_at": time.time(), "started_at": 0.0,
           "finished_at": 0.0, "node": "", "lease_expires": 0.0, "attempts": 0,
           "progress": {}, "error": "", "provenance": {},
           "platform_version": platform_version(), "cancel": False}
    with _LOCK:
        jobs = _load("remote_jobs.json")
        jobs[rid] = rec
        _save("remote_jobs.json", jobs)
    audit("job.submit", owner=owner, run_id=rid, kind=kind)
    return rec


def _requeue_locked(j: Dict[str, Any], why: str) -> None:
    if j["attempts"] >= MAX_ATTEMPTS:
        j.update(state=FAILED, finished_at=time.time(),
                 error="lease lost %d times (%s)" % (j["attempts"], why))
        audit("job.fail", owner=j["owner"], run_id=j["run_id"], node=j.get("node"),
              reason=j["error"])
        return
    audit("job.requeue", owner=j["owner"], run_id=j["run_id"], node=j.get("node"),
          reason=why)
    j.update(state=QUEUED, node="", lease_expires=0.0)


def reap(now: Optional[float] = None) -> int:
    """Put lapsed leases back in the queue.  Returns how many moved."""
    t = time.time() if now is None else now
    n = 0
    with _LOCK:
        jobs = _load("remote_jobs.json")
        for j in jobs.values():
            if j["state"] == LEASED and j["lease_expires"] < t:
                if j.get("cancel"):
                    j.update(state=CANCELLED, finished_at=t)
                else:
                    _requeue_locked(j, "lease timeout")
                n += 1
        if n:
            _save("remote_jobs.json", jobs)
    return n


def lease(nid: str, node: Dict[str, Any], version: str,
          now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Hand the node the best queued job of ITS OWNER, or None.

    Raises :class:`Refused` (409) on a version mismatch — the node is running
    different solver code and its answers would not be the platform's.
    """
    t = time.time() if now is None else now
    owner = node.get("owner")
    if str(version or "") != platform_version():
        audit("lease.refused", owner=owner, node=nid,
              reason="version %s != %s" % (version, platform_version()))
        raise Refused(409, "version mismatch: worker %s, platform %s — update "
                      "the worker image" % (version or "?", platform_version()))
    reap(t)
    with _LOCK:
        jobs = _load("remote_jobs.json")
        cands = [j for j in jobs.values() if j["state"] == QUEUED
                 and j["owner"] == owner and not j.get("cancel")
                 and j.get("platform_version") == platform_version()]
        if not cands:
            return None
        j = sorted(cands, key=lambda r: (r["priority"], r["queued_at"]))[0]
        j.update(state=LEASED, node=nid, lease_expires=t + LEASE_S,
                 attempts=j["attempts"] + 1, started_at=j["started_at"] or t)
        _save("remote_jobs.json", jobs)
    audit("job.lease", owner=owner, run_id=j["run_id"], node=nid)
    bundle = {"run_id": j["run_id"], "kind": j["kind"], "body": j["body"],
              "platform_version": j["platform_version"], "lease_s": LEASE_S,
              "attempt": j["attempts"]}
    return {"bundle": bundle, "signature": sign(node["token_hash"], bundle)}


def _own_leased(nid: str, node: Dict[str, Any], rid: str,
                jobs: Dict[str, Any]) -> Dict[str, Any]:
    j = jobs.get(rid)
    # 404, never 403: a node must not learn that another owner's run exists.
    if j is None or j["owner"] != node.get("owner"):
        raise Refused(404, "no such job")
    if j["node"] != nid or j["state"] != LEASED:
        raise Refused(409, "job is not leased to this node (state %s)" % j["state"])
    return j


def progress(nid: str, node: Dict[str, Any], rid: str, prog: Dict[str, Any],
             now: Optional[float] = None) -> Dict[str, Any]:
    """Progress + lease renewal.  The answer carries ``cancel`` — that is how
    Stop reaches a worker that has no inbound port."""
    t = time.time() if now is None else now
    with _LOCK:
        jobs = _load("remote_jobs.json")
        j = _own_leased(nid, node, rid, jobs)
        clean = {}
        for k in ("frac", "phase", "eta_s", "cpu_s"):
            v = (prog or {}).get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                clean[k] = float(v)
            elif isinstance(v, str):
                clean[k] = CM.redact(v[:120])
        j["progress"] = clean
        j["lease_expires"] = t + LEASE_S
        cancel = bool(j.get("cancel"))
        _save("remote_jobs.json", jobs)
    return {"ok": True, "cancel": cancel, "lease_s": LEASE_S}


def validate_result(payload: Any) -> Tuple[Any, Dict[str, Any]]:
    """Schema + size check of a result upload.  Never executes anything."""
    if not isinstance(payload, dict):
        raise Refused(400, "result must be an object")
    raw = _canon(payload.get("result"))
    if len(raw) > MAX_RESULT_BYTES:
        raise Refused(413, "result too large")
    prov = payload.get("provenance")
    if not isinstance(prov, dict):
        raise Refused(400, "provenance required")
    cpu = prov.get("cpu_s")
    wall = prov.get("wall_s")
    for name, v in (("cpu_s", cpu), ("wall_s", wall)):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v > 1e8:
            raise Refused(400, "provenance.%s must be a non-negative number" % name)
    clean = {"cpu_s": float(cpu), "wall_s": float(wall),
             "image_digest": str(prov.get("image_digest") or "")[:120],
             "version": str(prov.get("version") or "")[:40],
             "peak_rss": int(prov.get("peak_rss") or 0)
             if isinstance(prov.get("peak_rss"), (int, float)) else 0}
    return payload.get("result"), clean


def complete(nid: str, node: Dict[str, Any], rid: str, payload: Dict[str, Any],
             now: Optional[float] = None) -> Dict[str, Any]:
    t = time.time() if now is None else now
    result, prov = validate_result(payload)
    sig = str(payload.get("signature") or "")
    if not hmac.compare_digest(sig, sign(node["token_hash"],
                                         {"run_id": rid, "result": result})):
        audit("job.bad_signature", owner=node.get("owner"), run_id=rid, node=nid)
        raise Refused(400, "bad result signature")
    if prov["version"] != platform_version():
        raise Refused(409, "result produced by a different platform version")
    with _LOCK:
        jobs = _load("remote_jobs.json")
        j = _own_leased(nid, node, rid, jobs)
        prov.update(node_id=nid, node_name=node.get("name") or nid)
        j.update(state=CANCELLED if j.get("cancel") else DONE, finished_at=t,
                 provenance=prov, lease_expires=0.0)
        rec = dict(j)
        _save("remote_jobs.json", jobs)
    if rec["state"] == DONE:
        _file_result(rec, result)
    _account(rec)
    audit("job.complete", owner=rec["owner"], run_id=rid, node=nid,
          cpu_s=prov["cpu_s"])
    return {"ok": True, "state": rec["state"]}


def fail(nid: str, node: Dict[str, Any], rid: str, error: str,
         cpu_s: float = 0.0, now: Optional[float] = None) -> Dict[str, Any]:
    t = time.time() if now is None else now
    with _LOCK:
        jobs = _load("remote_jobs.json")
        j = _own_leased(nid, node, rid, jobs)
        j.update(state=CANCELLED if j.get("cancel") else FAILED, finished_at=t,
                 error=CM.redact(str(error or "failed")[:400]), lease_expires=0.0,
                 provenance={"node_id": nid, "cpu_s": max(0.0, float(cpu_s or 0)),
                             "wall_s": t - (j["started_at"] or t)})
        rec = dict(j)
        _save("remote_jobs.json", jobs)
    _account(rec)
    audit("job.fail", owner=rec["owner"], run_id=rid, node=nid, reason=rec["error"])
    return {"ok": True, "state": rec["state"]}


def cancel(rid: str, requester: str, is_admin: bool = False) -> Optional[Dict[str, Any]]:
    """Stop a remote job.  None if the id is not a remote job.  A queued job
    is cancelled at once; a leased one on its worker's next progress call."""
    from motor_ai_sim import jobs as _J
    with _LOCK:
        jobs = _load("remote_jobs.json")
        j = jobs.get(rid)
        if j is None:
            return None
        if not is_admin and j["owner"] != requester:
            raise _J.NotOwner(rid)
        if j["state"] == QUEUED:
            j.update(state=CANCELLED, finished_at=time.time(), cancel=True)
        elif j["state"] == LEASED:
            j["cancel"] = True
        _save("remote_jobs.json", jobs)
        state = j["state"]
    audit("job.cancel", owner=j["owner"], run_id=rid, node=j.get("node") or None)
    return {"cancelled": True, "run_id": rid, "found": True, "state": state,
            "node": j.get("node") or ""}


def get_job(rid: str) -> Optional[Dict[str, Any]]:
    return _load("remote_jobs.json").get(rid)


def public(j: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: j.get(k) for k in ("run_id", "owner", "ws_id", "kind", "priority",
                                 "state", "queued_at", "started_at", "finished_at",
                                 "error", "progress", "provenance", "attempts")}
    out["node"] = j.get("node") or ""
    out["where"] = "node:%s" % j["node"] if j.get("node") else (
        "waiting for your node" if j.get("state") == QUEUED else "")
    out["remote"] = True
    return out


def list_jobs(owner: Optional[str], limit: int = 50) -> List[Dict[str, Any]]:
    reap()
    rows = [j for j in _load("remote_jobs.json").values()
            if owner is None or j["owner"] == owner]
    rows.sort(key=lambda r: r["queued_at"], reverse=True)
    return [public(j) for j in rows[:max(1, int(limit))]]


def result_path(ws_root: str, rid: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", rid)
    return Path(ws_root) / RESULTS_DIR / (safe + ".json")


def _file_result(rec: Dict[str, Any], result: Any) -> None:
    """Into the OWNER's workspace, where a server-run job's answer would go.
    The root was resolved at submit time from the owner's identity; the node
    never names a path."""
    p = result_path(rec["ws_root"], rec["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"run_id": rec["run_id"], "kind": rec["kind"],
                               "result": result, "provenance": rec["provenance"],
                               "finished_at": rec["finished_at"]}), encoding="utf-8")
    os.replace(tmp, p)


def _account(rec: Dict[str, Any]) -> None:
    from motor_ai_sim import job_usage as _U
    prov = rec.get("provenance") or {}
    try:
        _U.record({"run_id": rec["run_id"], "ts_start": rec["started_at"] or rec["queued_at"],
                   "ts_end": rec["finished_at"], "user": rec["owner"], "client": "node",
                   "kind": rec["kind"], "machine": "", "node": HIST_PREFIX + str(prov.get("node_id") or rec.get("node") or ""),
                   "wall_s": round(float(prov.get("wall_s") or 0), 2),
                   "cpu_s": round(float(prov.get("cpu_s") or 0), 2),
                   "peak_rss": int(prov.get("peak_rss") or 0),
                   "status": _U.STATUS.get(rec["state"], rec["state"]),
                   "cpu_method": "reported", "wait_s": 0.0, "own_node": 1})
    except Exception:                                   # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  jobs.run_job hook + a demo kind
# ─────────────────────────────────────────────────────────────────────────────

def remote_router(rec: Any) -> bool:
    """``jobs.run_job``'s hook: True if ``rec`` was dispatched to a node."""
    try:
        if route(rec.owner, rec.kind) != "remote":
            return False
        from motor_ai_sim import workspace as _WSP
        ws = _WSP.workspace()
        submit(rec.owner, rec.ws_id, str(ws.root), rec.kind, rec.body,
               run_id=rec.run_id, priority=rec.priority)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _selftest(body: Dict[str, Any]) -> Dict[str, Any]:
    """``selftest.cpu``: a bounded pure-Python loop, so a new node can prove the
    whole lease → run → result path without a solver licence of patience."""
    n = max(1, min(int(body.get("n") or 200000), 20_000_000))
    return {"n": n, "sum_sq": sum(i * i for i in range(n)) % 1_000_000_007}


def install() -> None:
    from motor_ai_sim import jobs as _J
    _J.register_handler("selftest.cpu", _selftest)
    _J.REMOTE_ROUTER = remote_router


install()
