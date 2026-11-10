"""Cross-region compute, both halves (docs/regions/DESIGN.md section 9).  OFF unless ``MOTRES_CROSS_REGION_COMPUTE=1``
on BOTH servers and a peer is configured.

HOME server (the account's):  ``route(rec)`` is called by ``jobs.run_job`` (so by every ``@jobs.queued`` route) for a
job whose kind has a registered handler (``jobs.HANDLERS`` - the pure ``body -> result`` kinds; a route closure cannot be
shipped).  It sends the job to the peer only when ``compute.pick_region`` says the peer is clearly less loaded, and only
if the payload is clean:

  1. ``compute.job_payload`` strips every identifying key at every depth and REFUSES a payload in which a string still
     looks like an e-mail, a path or a file name -> the job runs at home;
  2. the opaque token -> (run, owner, workspace, target) map is written to ``region_jobs_outbound.json`` BEFORE anything
     is sent; only this server can file the result under an account;
  3. the peer says yes (``POST /foreign/submit``) or the job runs at home - an unreachable or refusing peer is never an
     error to the user;
  4. the home job record lives in ``compute_nodes`` (``/api/jobs`` shows it, the result lands in the owner's workspace
     like a BYO node's), leased to the virtual node ``region:<peer>``.

COMPUTING server (the peer):  ``accept`` stores ``{token, kind, priority, inputs}`` under ``run/foreign_jobs/<token>.json``
(never in the database, never in a workspace, EXCLUDED from backups), the worker's ``tick`` runs it (one at a time by
default, and only while this server's own queue has a free slot) and HANDS THE RESULT BACK with ``POST /foreign/result``
to the home server, retrying with a growing delay.  A confirmed hand-back deletes the file at once; an unconfirmed job is
deleted ``compute.RETENTION_S`` (24 h) after it ended.  The file never holds an e-mail, a name or an account: the peer
cannot tell whom the job is for.

HOME ``home_sweep``: a token that never came back within 24 h is dropped and the job is run at home instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim.regions import compute as CP
from motor_ai_sim.regions import config as RC

log = logging.getLogger(__name__)

ENV_DIR = "MOTRES_FOREIGN_JOBS_DIR"
ENV_SLOTS = "MOTRES_FOREIGN_SLOTS"
MAX_PENDING = 4                  # queued + running foreign jobs this server holds at once (back-pressure to the home server)
MAX_INPUT_BYTES = 1 << 20
MAX_RESULT_BYTES = 8 << 20
TICK_S = 20.0
HANDBACK_FIRST_S = 15.0
HANDBACK_MAX_S = 900.0
PEER_STATUS_TTL_S = 10.0
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")

_LOCK = threading.RLock()
_THREADS: Dict[str, threading.Thread] = {}
_PEER_STATUS: Dict[str, Any] = {"ts": 0.0, "value": None}


# ── the foreign-jobs directory ───────────────────────────────────────────────

def foreign_dir() -> Path:
    """``run/foreign_jobs`` of the data root (``/srv/motres/run/foreign_jobs``); never under shared / workspaces / identity,
    so the backups that cover those never see it (``deploy/backup`` excludes it by name as well)."""
    env = (os.environ.get(ENV_DIR) or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as U
    p = Path(U._USERS_FILE).parent
    return (p.parent if p.name == "identity" else p) / "run" / "foreign_jobs"


def _path(token: str) -> Path:
    return foreign_dir() / (token + ".json")


def _write(job: Dict[str, Any]) -> None:
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    p = _path(job["token"])
    ensure_private_dir(p.parent)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    with open_private(tmp, "w") as f:
        json.dump(job, f, separators=(",", ":"))
    os.replace(tmp, p)
    chmod_private(p)


def _read(p: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and _TOKEN.fullmatch(str(d.get("token") or "")) else None


def list_jobs() -> List[Dict[str, Any]]:
    d = foreign_dir()
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return []
    return [j for j in (_read(p) for p in files) if j]


def _delete(token: str) -> None:
    try:
        _path(token).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("foreign jobs: cannot delete a job file (%s)", type(exc).__name__)


# ── the computing server ─────────────────────────────────────────────────────

def accept(payload: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """``POST /foreign/submit``: validate and store one foreign job.  -> ``{ok: True, token}`` or ``{ok: False, code}``."""
    from motor_ai_sim import jobs as J
    t = time.time() if now is None else now
    if not CP.cross_region_enabled():
        return {"ok": False, "code": "off"}
    if not isinstance(payload, dict) or payload.get("v") != CP.PAYLOAD_VERSION:
        return {"ok": False, "code": "bad_payload"}
    token, kind, inputs = str(payload.get("token") or ""), str(payload.get("kind") or ""), payload.get("inputs")
    if not _TOKEN.fullmatch(token) or not re.fullmatch(r"[a-z0-9_.:-]{1,64}", kind) or not isinstance(inputs, dict):
        return {"ok": False, "code": "bad_payload"}
    if kind not in J.HANDLERS:
        return {"ok": False, "code": "unknown_kind"}
    try:
        size = len(json.dumps(inputs, separators=(",", ":")))
    except (TypeError, ValueError):
        return {"ok": False, "code": "bad_payload"}
    if size > MAX_INPUT_BYTES:
        return {"ok": False, "code": "too_large"}
    if CP.identifying_strings(inputs) or CP.scrub(inputs) != inputs:
        return {"ok": False, "code": "identifying"}          # defence in depth: the sender should have scrubbed already
    with _LOCK:
        if _path(token).exists():
            return {"ok": True, "token": token}              # a repeated submit is the same job
        if sum(1 for j in list_jobs() if j.get("state") in ("queued", "running")) >= MAX_PENDING:
            return {"ok": False, "code": "busy"}
        _write({"v": 1, "token": token, "kind": kind, "priority": int(payload.get("priority") or 0), "inputs": inputs,
                "state": "queued", "created_at": round(t, 1), "started_at": 0.0, "ended_at": 0.0, "result": None,
                "error": "", "tries": 0, "next_at": 0.0})
    return {"ok": True, "token": token}


def _slots() -> int:
    try:
        return max(1, int(os.environ.get(ENV_SLOTS, "") or 1))
    except ValueError:
        return 1


def _local_queue_free() -> bool:
    """May a foreign job start now?  Only while this server's own queue has a free slot (its own users come first)."""
    try:
        from motor_ai_sim import jobs as J
        snap = J.queue().snapshot()
        return int(snap.get("running") or 0) < int(snap.get("workers") or 1) and int(snap.get("queued") or 0) == 0
    except Exception:                                       # noqa: BLE001
        return True


def _run(token: str) -> None:
    """Run one foreign job to its end (own thread), then try the hand-back once."""
    from motor_ai_sim import jobs as J
    job = _read(_path(token))
    if not job or job.get("state") != "queued":
        return
    t0 = time.time()
    job.update(state="running", started_at=round(t0, 1))
    _write(job)
    ok, result, err = True, None, ""
    try:
        fn = J.HANDLERS[job["kind"]]
        result = fn(dict(job.get("inputs") or {}))
        if len(json.dumps(result, separators=(",", ":"), default=str)) > MAX_RESULT_BYTES:
            ok, result, err = False, None, "result too large"
    except Exception as exc:                                # noqa: BLE001 - the job failed; say so, never crash the loop
        ok, result, err = False, None, type(exc).__name__ + ": " + str(exc)[:200]
    end = time.time()
    job.update(state="done" if ok else "failed", ended_at=round(end, 1), result=result, error=err,
               wall_s=round(end - t0, 2), tries=0, next_at=0.0)
    _write(job)
    handback(job)


def handback(job: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Send the result of a finished foreign job to the home server.  True (and the file is deleted) when it confirmed."""
    from motor_ai_sim.regions import peer as P
    t = time.time() if now is None else now
    body = {"token": job["token"], "ok": job.get("state") == "done", "wall_s": job.get("wall_s") or 0.0}
    if body["ok"]:
        body["result"] = job.get("result")
    else:
        body["error"] = str(job.get("error") or "failed")[:300]
    got = P.post("/foreign/result", body, timeout=30.0)
    if isinstance(got, dict) and got.get("ok"):
        _delete(job["token"])
        return True
    job["tries"] = int(job.get("tries") or 0) + 1
    job["next_at"] = round(t + min(HANDBACK_MAX_S, HANDBACK_FIRST_S * (2 ** min(job["tries"], 10))), 1)
    if _path(job["token"]).exists():
        _write(job)
    return False


def tick(now: Optional[float] = None) -> Dict[str, int]:
    """One pass of the worker loop (also callable from a test): delete expired jobs, retry due hand-backs, start queued
    jobs while there is room, and sweep the home server's outbound tokens."""
    t = time.time() if now is None else now
    out = {"expired": 0, "handbacks": 0, "started": 0, "swept": 0}
    with _LOCK:
        for tok in [k for k, th in _THREADS.items() if not th.is_alive()]:
            _THREADS.pop(tok, None)
    for job in list_jobs():
        tok = job["token"]
        ended = float(job.get("ended_at") or 0.0) or float(job.get("created_at") or 0.0)
        if job.get("state") in ("done", "failed") or job.get("state") == "queued":
            if CP.foreign_job_expired(ended, t):
                _delete(tok)
                out["expired"] += 1
                continue
        if job.get("state") == "running" and tok not in _THREADS and CP.foreign_job_expired(float(job.get("started_at") or 0), t):
            _delete(tok)                                     # a worker restart killed it long ago
            out["expired"] += 1
            continue
        if job.get("state") in ("done", "failed") and float(job.get("next_at") or 0.0) <= t:
            if handback(job, t):
                out["handbacks"] += 1
    if CP.cross_region_enabled():
        for job in sorted((j for j in list_jobs() if j.get("state") == "queued"), key=lambda j: j.get("created_at") or 0):
            with _LOCK:
                busy = sum(1 for th in _THREADS.values() if th.is_alive())
                if busy >= _slots() or not _local_queue_free():
                    break
                th = threading.Thread(target=_run, args=(job["token"],), daemon=True, name="foreign-" + job["token"][:6])
                _THREADS[job["token"]] = th
                th.start()
                out["started"] += 1
    out["swept"] = home_sweep(t)
    return out


# ── the home server ──────────────────────────────────────────────────────────

def _peer_status() -> Optional[Dict[str, Any]]:
    from motor_ai_sim.regions import peer as P
    now = time.time()
    with _LOCK:
        if now - _PEER_STATUS["ts"] < PEER_STATUS_TTL_S:
            return _PEER_STATUS["value"]
    v = P.get("/health", timeout=2.0)
    v = v if isinstance(v, dict) else None
    with _LOCK:
        _PEER_STATUS.update(ts=now, value=v)
    return v


def route(rec: Any) -> bool:
    """``jobs.run_job`` hook (called only for a kind that has a handler): True = the job was sent to the peer region and
    its record lives in ``compute_nodes``; False = run it here (every doubt, every error)."""
    from motor_ai_sim import compute_nodes as CN
    from motor_ai_sim.regions import peer as P
    try:
        if not CP.cross_region_enabled() or getattr(rec, "node", ""):
            return False
        home = RC.local_region()
        from motor_ai_sim.regions import status as ST
        where = CP.pick_region(home, ST.local_status(), _peer_status())
        if where == home:
            return False
        body = dict(rec.body or {})
        json.dumps(body)
        token, payload = CP.job_payload(rec.kind, body, priority=int(rec.priority))
    except CP.CrossRegionPayloadError as exc:
        log.info("cross-region compute: a %s job stays at home (%s)", getattr(rec, "kind", "?"), exc)
        return False
    except Exception as exc:                                # noqa: BLE001 - never let scheduling break a submission
        log.info("cross-region compute: not used (%s)", type(exc).__name__)
        return False
    try:
        CP.register_outbound(token, run_id=rec.run_id, owner=rec.owner, ws_id=rec.ws_id, target=rec.target,
                             region=where)
        got = P.post("/foreign/submit", payload, timeout=10.0)
        if not (isinstance(got, dict) and got.get("ok")):
            CP.complete_token(token)
            return False
        from motor_ai_sim import workspace as _WSP
        CN.submit(rec.owner, rec.ws_id, str(_WSP.workspace().root), rec.kind, body, run_id=rec.run_id,
                  priority=int(rec.priority))
        CN.lease_to_region(rec.run_id, CN.REGION_NODE_PREFIX + where)
        return True
    except Exception as exc:                                # noqa: BLE001
        log.warning("cross-region compute: dispatch failed (%s); the job runs at home", type(exc).__name__)
        CP.complete_token(token)
        return False


def receive_result(payload: Any) -> Dict[str, Any]:
    """``POST /foreign/result`` on the HOME server: file the peer's result under the account the token belongs to.
    ``{ok: True}`` also for a token this server no longer knows (done, timed out): the peer then deletes its copy."""
    from motor_ai_sim import compute_nodes as CN
    if not isinstance(payload, dict):
        return {"ok": False, "code": "bad_payload"}
    token = str(payload.get("token") or "")
    ent = CP.resolve_token(token) if _TOKEN.fullmatch(token) else None
    if ent is None:
        return {"ok": True, "known": False}
    ok = bool(payload.get("ok"))
    result = payload.get("result") if ok else None
    try:
        if ok and len(json.dumps(result, separators=(",", ":"), default=str)) > MAX_RESULT_BYTES:
            ok, result = False, None
            payload = {**payload, "error": "result too large"}
    except (TypeError, ValueError):
        ok, result = False, None
    if CN.get_job(ent["run_id"]) is None:
        return {"ok": False, "code": "not_yet"}              # the home record is written right after the peer said yes: retry
    wall = payload.get("wall_s")
    CN.finish_region(ent["run_id"], ok, result=result, error=str(payload.get("error") or ""),
                     wall_s=float(wall) if isinstance(wall, (int, float)) and not isinstance(wall, bool) else 0.0)
    CP.complete_token(token)
    return {"ok": True, "known": True}


def home_sweep(now: Optional[float] = None) -> int:
    """Tokens that never came back within the retention: forget them and run the job at home (a lost foreign job)."""
    from motor_ai_sim import compute_nodes as CN
    from motor_ai_sim import jobs as J
    t = time.time() if now is None else now
    n = 0
    for token, ent in list(CP._load_out().items()):
        if not isinstance(ent, dict) or t - float(ent.get("sent_at") or 0.0) < CP.RETENTION_S:
            continue
        CP.complete_token(token)
        j = CN.get_job(ent.get("run_id", ""))
        if j is None or j.get("state") not in CN._OPEN:
            continue
        n += 1
        fn = J.HANDLERS.get(j.get("kind"))
        if fn is None:
            CN.finish_region(j["run_id"], False, error="the computing region did not answer")
            continue

        def _again(rid=j["run_id"], body=dict(j.get("body") or {}), fn=fn):
            t0 = time.time()
            try:
                CN.finish_region(rid, True, result=fn(body), wall_s=time.time() - t0)
            except Exception as exc:                        # noqa: BLE001
                CN.finish_region(rid, False, error=type(exc).__name__, wall_s=time.time() - t0)

        threading.Thread(target=_again, daemon=True, name="foreign-rerun").start()
    return n


# ── the worker's loop ────────────────────────────────────────────────────────

_LOOP: Optional[threading.Thread] = None


def start_loop(interval_s: float = TICK_S) -> bool:
    """Start the daemon loop (worker process).  A no-op unless cross-region compute is on.  -> whether it was started."""
    global _LOOP
    if not CP.cross_region_enabled():
        return False
    with _LOCK:
        if _LOOP is not None and _LOOP.is_alive():
            return False

        def _loop():
            while True:
                try:
                    tick()
                except Exception:                           # noqa: BLE001
                    log.exception("foreign jobs: tick failed")
                time.sleep(interval_s)

        _LOOP = threading.Thread(target=_loop, daemon=True, name="foreign-jobs")
        _LOOP.start()
    return True
