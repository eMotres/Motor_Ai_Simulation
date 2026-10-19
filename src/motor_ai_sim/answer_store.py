"""Stored answers (owner 2026-10-19: «зачем её каждый раз собирать, она уже должна быть готова»).

A heavy answer - a Configure load, a propeller S1 limit - is computed ONCE per version of its inputs and kept: in
memory, and as a small JSON file under ``<shared>/.answers/<namespace>/`` so a restart (or the warm-up process,
``motor_ai_sim.configure_warm``) does not lose it.  A request checks the stored answer's STAMP - a cheap hash of its
inputs' file versions (``fscache.stamp_of``) - and serves it when it matches; a stale or missing answer is computed
once (concurrent requests for the same key wait for that one computation), stored and served.

What may be stored on disk is the caller's business: ``persist=False`` keeps an answer in this process only (a
workspace-private machine is never written to the shared layer).  Answers are stored WHOLE; the routes strip what a
caller may not see before they return one.  Nothing here fails a request: an unwritable or unreadable store
degrades to computing the answer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger(__name__)

ENV_ROOT = "MOTRES_ANSWER_STORE"
MAX_MEMORY = 512

_MEM: "OrderedDict[Tuple[str, str], Tuple[str, Any]]" = OrderedDict()
_MEM_LOCK = threading.Lock()
_FLIGHT: Dict[Tuple[str, str], threading.Lock] = {}
_FLIGHT_LOCK = threading.Lock()
_ROOT_CHECKED: Dict[str, Optional[Path]] = {}
_ROOT_LOCK = threading.Lock()


def store_root() -> Optional[Path]:
    """``$MOTRES_ANSWER_STORE``, else ``<shared>/.answers`` on a layered (server) install when it is writable, else
    None (memory only - a workstation or a test never writes into its config folder)."""
    raw = os.environ.get(ENV_ROOT, "").strip()
    if raw:
        cand = Path(raw).expanduser()
    else:
        try:
            from motor_ai_sim.workspace import layering, shared_root
            if not layering():
                return None
            cand = Path(str(shared_root())) / ".answers"
        except Exception:                                       # noqa: BLE001
            return None
    key = str(cand)
    with _ROOT_LOCK:                            # one probe per root, never two threads racing on one probe file
        if key in _ROOT_CHECKED:
            return _ROOT_CHECKED[key]
        ok: Optional[Path] = None
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / f".probe-{os.getpid()}-{threading.get_ident()}"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            ok = cand
        except OSError:
            log.info("answer store: %s is not writable - answers stay in memory", cand)
        _ROOT_CHECKED[key] = ok
        return ok


def key_hash(key: Dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def _path(ns: str, kh: str) -> Optional[Path]:
    root = store_root()
    return (root / ns / f"{kh}.json") if root is not None else None


def peek(ns: str, key: Dict[str, Any], stamp: str) -> Tuple[bool, Any]:
    """``(hit, value)`` without computing: memory, then the file."""
    kh = key_hash(key)
    with _MEM_LOCK:
        hit = _MEM.get((ns, kh))
        if hit is not None and hit[0] == stamp:
            _MEM.move_to_end((ns, kh))
            return True, hit[1]
    p = _path(ns, kh)
    if p is not None:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("stamp") == stamp:
                _remember(ns, kh, stamp, doc.get("value"))
                return True, doc.get("value")
        except (OSError, ValueError):
            pass
    return False, None


def _remember(ns: str, kh: str, stamp: str, value: Any) -> None:
    with _MEM_LOCK:
        _MEM[(ns, kh)] = (stamp, value)
        _MEM.move_to_end((ns, kh))
        while len(_MEM) > MAX_MEMORY:
            _MEM.popitem(last=False)


def put(ns: str, key: Dict[str, Any], stamp: str, value: Any, *, persist: bool = True) -> None:
    kh = key_hash(key)
    _remember(ns, kh, stamp, value)
    p = _path(ns, kh) if persist else None
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps({"stamp": stamp, "key": key, "stored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "value": value}, default=str), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        log.debug("answer store: could not write %s", p, exc_info=True)


def get_or_compute(ns: str, key: Dict[str, Any], stamp: str, compute: Callable[[], Any], *,
                   persist: bool = True) -> Any:
    """The stored answer for ``key`` at ``stamp``, else ``compute()`` once (single flight per key), stored."""
    hit, val = peek(ns, key, stamp)
    if hit:
        return val
    kh = key_hash(key)
    with _FLIGHT_LOCK:
        lock = _FLIGHT.setdefault((ns, kh), threading.Lock())
    with lock:                                  # a second request for the same key waits for the first, then reads it
        hit, val = peek(ns, key, stamp)
        if hit:
            return val
        val = compute()
        put(ns, key, stamp, val, persist=persist)
        return val


def clear_memory() -> None:
    with _MEM_LOCK:
        _MEM.clear()


def json_response(obj: Any):
    """A stored answer straight to the wire: ``json.dumps`` once, no ``jsonable_encoder`` walk over a few hundred kB
    of plain JSON data (it was most of a stored answer's serving time).  Same encoding as Starlette's JSONResponse."""
    from fastapi.responses import Response
    return Response(content=json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"),
                    media_type="application/json")
