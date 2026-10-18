"""Cross-process invalidation of the in-memory workbench state (deploy stage 2).

WHY
===
A workspace keeps its live machine in MEMORY: the geometry singleton, the mesh / field / transient caches, the last-result
slots (``workspace.WorkspaceState``).  When one process changes the machine it clears its own copy (``routes.simulation.
clear_simulation_caches``, ``geometry_service.update_current_geometry`` ...).  With the API and the compute worker as
separate processes, the OTHER process still holds the old copy - a preset applied through the API would leave the
worker solving the previous machine.

The fix is a counter per workspace on disk: ``<workspace>/.state_epoch``.  Whoever changes the machine bumps it (the same
call sites that already clear their own caches); every process compares it on each request of that workspace and, if it
moved and the move was not its own, evicts that workspace's in-memory state.  Evicting costs time, never data: everything
that matters is on disk, so a missed bump is a stale cache for the lifetime of the entry - which is what the
single-process server already has when an optimizer subprocess edits the YAML, and the caches' keys carry the geometry
fingerprint for exactly that reason.

WHAT IS DROPPED, AND WHEN NOT
=============================
Only the CACHES - through the same funnels a single-process config write already uses (``clear_simulation_caches`` without
a new bump, the geometry singleton, the mesh cache, the config cache).  ``WorkspaceState.evict()`` is NOT used: it also
wipes the optimizer campaign slots (``_scan`` / ``_descent`` / ``_doe`` / ``_refine_state``), the per-workspace ``ws_lock``
RLocks and the static-3D slots, i.e. the state of a job that is running RIGHT NOW in this process.  And a workspace that has
a queued or running job in this process is not touched at all: ``seen`` stays as it was, so the drop happens on the first
request after the job ended.

Off (a no-op) unless :func:`motor_ai_sim.roles.multi_process`: the workstation and the suite are untouched.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Optional

from motor_ai_sim import roles

log = logging.getLogger(__name__)

EPOCH_FILE = ".state_epoch"
_FLAG = "state_epoch.seen"

#: Makes "write the file, remember the token" one step, so two bumps of this process (two request threads) can never
#: leave the file with one token and the flag with the other - which the next check would read as somebody else's change.
_LOCK = threading.Lock()


def _file(ws) -> Path:
    return Path(str(ws.root)) / EPOCH_FILE


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def bump(ws=None, reason: str = "") -> Optional[str]:
    """Announce "this workspace's machine changed" to the other processes.  Returns the new token (None if off)."""
    if not roles.multi_process():
        return None
    try:
        if ws is None:
            from motor_ai_sim import workspace as _WS
            ws = _WS.workspace()
        token = "%s-%s" % (os.getpid(), secrets.token_hex(6))
        p = _file(ws)
        p.parent.mkdir(parents=True, exist_ok=True)
        # unique per call: pid + thread + random (two threads of one process, or two processes of one container pid
        # namespace each, never share a half-written file)
        tmp = p.with_name("%s.%d.%d.%s.tmp" % (p.name, os.getpid(), threading.get_ident(), secrets.token_hex(4)))
        try:
            tmp.write_text(token, encoding="utf-8")
            with _LOCK:
                os.replace(str(tmp), str(p))
                ws.state.set_flag(_FLAG, token)       # our own bump is not news to us
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        log.debug("state epoch of %s -> %s (%s)", getattr(ws, "id", "?"), token, reason)
        return token
    except Exception as exc:                            # noqa: BLE001 - a missed bump must never fail a request
        log.warning("state epoch not bumped: %s", exc)
        return None


def _has_work(ws) -> bool:
    """Does this process have a queued or running job of ``ws``?  (Then its in-memory state is not ours to drop.)

    Unknown counts as busy: leaving a cache stale for a while is cheap, wiping a four-hour campaign is not.
    """
    try:
        from motor_ai_sim import jobs as _J
        wid = str(getattr(ws, "id", ""))
        for it in _J.queue().snapshot().get("items") or []:
            if str(it.get("ws_id")) == wid and str(it.get("state") or "") in ("queued", "running"):
                return True
        return False
    except Exception as exc:                            # noqa: BLE001
        log.warning("state epoch: cannot tell whether workspace %s has a job (%s) - not dropping its caches",
                    getattr(ws, "id", "?"), exc)
        return True


def _drop_caches(ws) -> None:
    """Forget the cached machine of ``ws`` (geometry, mesh, field / transient / thermal / mechanical caches, config).

    The same funnels a single-process config write calls, run in the context of ``ws``.  NOT ``ws.state.evict()``: that
    would also wipe the optimizer campaign state, the ``ws_lock`` RLocks and the static-3D slots (see the module docstring).
    ``announce=False``: this is the reaction to somebody else's bump, it must not bump again.
    """
    from motor_ai_sim import config as _cfg
    from motor_ai_sim import workspace as _WS
    from motor_ai_sim.routes import simulation as _sim
    from motor_ai_sim.services import geometry_service as _geo
    with _WS.use_workspace(ws):
        _sim.clear_simulation_caches("another process changed the machine", announce=False)
        _geo._geom_set(None)
        _geo.invalidate_mesh_cache()
    _cfg.clear_config_cache()


def check(ws) -> bool:
    """Drop ``ws``'s cached machine if another process moved its epoch.  True if the caches were dropped.

    Called once per request of a workspace (``workspace.WorkspaceMiddleware``); one small file read.  A workspace with a
    queued or running job in this process is skipped and ``seen`` is left alone, so the drop happens later.
    """
    if not roles.multi_process():
        return False
    try:
        with _LOCK:                                   # (a bump of this process in flight is not a foreign change)
            now = _read(_file(ws))
            seen = ws.state.flag(_FLAG)
            if seen is None:
                ws.state.set_flag(_FLAG, now)         # first look at this workspace in this process: nothing to drop
                return False
            if now == seen:
                return False
        if _has_work(ws):
            log.info("workspace %s: another process changed the machine (epoch %s -> %s) but a job runs here - caches kept",
                     getattr(ws, "id", "?"), seen or "-", now or "-")
            return False
        _drop_caches(ws)
        ws.state.set_flag(_FLAG, now)
        log.info("workspace %s: another process changed the machine (epoch %s -> %s) - cached machine dropped",
                 getattr(ws, "id", "?"), seen or "-", now or "-")
        return True
    except Exception as exc:                            # noqa: BLE001
        log.warning("state epoch check failed: %s", exc)
        return False
