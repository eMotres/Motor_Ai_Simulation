"""The compute pool across both regions (owner 2026-11-08: jobs of any account may run on either server, and a computation is not
personal data).

A simulation job of ANY account may run on EITHER server.  What crosses to the other region is the engineering input
only — never who it is for:

* NOT sent: e-mail, name, company, account / workspace id, run id, project id or title, die / configuration / duty
  names, file names or paths, agent or client names, notes.  ``job_payload`` drops those keys at every depth and then
  REFUSES (``CrossRegionPayloadError``) a payload in which any string still looks like an e-mail address, a file path or
  a file name — the job then simply runs at home.
* SENT: ``{v, token, kind, priority, inputs}`` — ``token`` is opaque (random, 144 bits); the home server alone keeps the
  map token -> (run id, owner, workspace, target) in ``<identity dir>/region_jobs_outbound.json`` and files the result
  under the right account when it comes back.
* The computing server keeps the inputs and the result only while the job runs and until the home server confirms it
  received the result; unconfirmed, it retries the hand-back and deletes everything after ``RETENTION_S`` (24 h).

Scheduling (``pick_region``): the less loaded server, the home server on a tie or when the peer is unknown; the peer
only when ``MOTRES_CROSS_REGION_COMPUTE=1`` and multi-region mode is configured.  Stage 1 ships the contract and its
tests; stage 2 wires it into the job queue (regions/foreign.py).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from motor_ai_sim.file_lock import store_lock
from motor_ai_sim.regions import config as RC

ENV_CROSS = "MOTRES_CROSS_REGION_COMPUTE"
#: How long the COMPUTING server may keep a foreign job's inputs/result after the job ended, when the home server has
#: not confirmed the hand-back.  Confirmed earlier -> deleted at once.
RETENTION_S = 24 * 3600
PAYLOAD_VERSION = 1

#: Keys never sent (compared lower-case; a key that ENDS with ``_name`` / ``_email`` / ``_path`` / ``_file`` / ``_title``
#: / ``_id`` is dropped too).
FORBIDDEN_KEYS = frozenset({
    "email", "e_mail", "mail", "owner", "user", "username", "name", "display_name", "company", "company_name", "org",
    "organisation", "organization", "customer", "customer_org", "account", "account_id", "ws_id", "workspace",
    "workspace_id", "run_id", "job_id", "project", "project_id", "project_title", "title", "die", "die_name", "config",
    "config_name", "cfg", "duty", "duty_name", "target", "file", "filename", "files", "path", "paths", "dir", "folder",
    "agent", "client_name", "client", "notes", "note", "comment", "comments", "label", "description", "ip", "user_agent",
    "token", "authorization", "session", "sid",
})
_FORBIDDEN_SUFFIX = ("_name", "_email", "_path", "_file", "_title", "_id", "_dir")

_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PATHLIKE = re.compile(r"(^[A-Za-z]:[\\/])|(^\\\\)|(^~?/)|(/srv/)|(\\[^\\]+\\)")
_FILENAME = re.compile(r"\.(ya?ml|json|step|stp|fcstd|stl|glb|brep|iges?|dxf|pdf|docx?|xlsx?|csv|txt|zip|npz|py|kicad_\w+)$",
                       re.IGNORECASE)

_OUT_LOCK = store_lock(lambda: outbound_path())


class CrossRegionPayloadError(ValueError):
    """The job input still identifies someone after scrubbing: it must not leave the home region."""


def cross_region_enabled() -> bool:
    return (str(os.environ.get(ENV_CROSS, "")).strip().lower() in ("1", "true", "yes", "on")) and RC.peer_configured()


def _forbidden(key: str) -> bool:
    k = str(key).strip().lower()
    return k in FORBIDDEN_KEYS or k.endswith(_FORBIDDEN_SUFFIX)


def scrub(value: Any) -> Any:
    """Drop the identifying keys at every depth (dicts in lists included).  Values are left as they are."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not _forbidden(k)}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def identifying_strings(value: Any, where: str = "$") -> list:
    """Every remaining string that looks like an e-mail address, a path or a file name -> ``[json path, ...]``."""
    hits = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and _bad_string(k):
                hits.append(f"{where}.<key>")
            hits += identifying_strings(v, f"{where}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            hits += identifying_strings(v, f"{where}[{i}]")
    elif isinstance(value, str) and _bad_string(value):
        hits.append(where)
    return hits


def _bad_string(s: str) -> bool:
    t = s.strip()
    return bool(_EMAIL.search(t) or _PATHLIKE.search(t) or _FILENAME.search(t))


def job_payload(kind: str, body: Dict[str, Any], *, priority: int = 0,
                token: Optional[str] = None, strict: bool = False) -> Tuple[str, Dict[str, Any]]:
    """-> ``(token, payload)``: what is sent to the computing region for one job.  Raises CrossRegionPayloadError.

    ``strict`` (the queue wiring, ``regions/foreign.py``): a body from which the scrub would drop ANY key is refused instead
    of being sent in a modified form - the job runs at home with the inputs the user gave, never with different ones."""
    tok = token or secrets.token_urlsafe(18)
    inputs = scrub(body if isinstance(body, dict) else {})
    if strict and inputs != (body if isinstance(body, dict) else {}):
        raise CrossRegionPayloadError("job input carries identifying keys; it is not sent in a modified form")
    bad = identifying_strings(inputs)
    if bad:
        raise CrossRegionPayloadError("job input still identifies someone at " + ", ".join(bad[:5]))
    if not re.fullmatch(r"[a-z0-9_.:-]{1,64}", str(kind or "")):
        raise CrossRegionPayloadError("job kind is not a plain identifier")
    return tok, {"v": PAYLOAD_VERSION, "token": tok, "kind": str(kind), "priority": int(priority), "inputs": inputs}


# ── scheduling ───────────────────────────────────────────────────────────────

def pressure(status: Optional[Dict[str, Any]]) -> Optional[float]:
    """Jobs waiting or running per core (lower = less loaded); None when unknown."""
    if not isinstance(status, dict):
        return None
    q = status.get("queue") or {}
    cores = ((status.get("load") or {}).get("cores") or 0)
    if q.get("running") is None or q.get("queued") is None or not cores:
        return None
    return (float(q["running"]) + float(q["queued"])) / float(cores)


def pick_region(home: str, home_status: Optional[Dict[str, Any]], peer_status: Optional[Dict[str, Any]],
                *, enabled: Optional[bool] = None) -> str:
    """Where one job runs: the peer only when cross-region compute is on, the peer is known and clearly less loaded
    (by more than one job per 4 cores, so jobs do not ping-pong on equal load)."""
    on = cross_region_enabled() if enabled is None else enabled
    h = RC.norm_region(home, RC.DEFAULT_REGION) or RC.DEFAULT_REGION
    if not on:
        return h
    ph, pp = pressure(home_status), pressure(peer_status)
    if pp is None or ph is None or not (peer_status or {}).get("ok", True):
        return h
    return RC.other_region(h) if pp + 0.25 < ph else h


# ── the home server's token map ──────────────────────────────────────────────

def outbound_path() -> Path:
    env = (os.environ.get("MOTRES_REGION_JOBS_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as U
    return Path(U._USERS_FILE).parent / "region_jobs_outbound.json"


def _load_out() -> Dict[str, Any]:
    try:
        d = json.loads(outbound_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_out(d: Dict[str, Any]) -> None:
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    p = outbound_path()
    ensure_private_dir(p.parent)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    with open_private(tmp, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, p)
    chmod_private(p)


def register_outbound(token: str, *, run_id: str, owner: str, ws_id: str, target: Optional[dict] = None,
                      region: str, now: Optional[float] = None) -> None:
    """The home server remembers whom a token's result belongs to (this file never leaves the home region)."""
    with _OUT_LOCK:
        d = _load_out()
        d[token] = {"run_id": run_id, "owner": owner, "ws_id": ws_id, "target": dict(target or {}),
                    "region": RC.norm_region(region, RC.other_region(RC.local_region())),
                    "sent_at": round(time.time() if now is None else now, 1)}
        _save_out(d)


def resolve_token(token: str) -> Optional[Dict[str, Any]]:
    v = _load_out().get(str(token or ""))
    return v if isinstance(v, dict) else None


def complete_token(token: str) -> Optional[Dict[str, Any]]:
    """The result is filed: forget the token (-> its entry, or None for an unknown token)."""
    with _OUT_LOCK:
        d = _load_out()
        v = d.pop(str(token or ""), None)
        if v is not None:
            _save_out(d)
    return v


def foreign_job_expired(ended_at: float, now: Optional[float] = None) -> bool:
    """On the COMPUTING server: may the inputs/result of a foreign job be deleted without a confirmation?"""
    return (time.time() if now is None else now) - float(ended_at) >= RETENTION_S
