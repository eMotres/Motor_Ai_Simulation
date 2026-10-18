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
that matters is on disk (``WorkspaceState.evict`` documents it), so a missed bump is a stale cache for the lifetime of
the entry - which is what the single-process server already has when an optimizer subprocess edits the YAML, and the
caches' keys carry the geometry fingerprint for exactly that reason.

Off (a no-op) unless :func:`motor_ai_sim.roles.multi_process`: the workstation and the suite are untouched.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from motor_ai_sim import roles

log = logging.getLogger(__name__)

EPOCH_FILE = ".state_epoch"
_FLAG = "state_epoch.seen"


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
        tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
        tmp.write_text(token, encoding="utf-8")
        os.replace(str(tmp), str(p))
        ws.state.set_flag(_FLAG, token)           # our own bump is not news to us
        log.debug("state epoch of %s -> %s (%s)", getattr(ws, "id", "?"), token, reason)
        return token
    except Exception as exc:                            # noqa: BLE001 - a missed bump must never fail a request
        log.warning("state epoch not bumped: %s", exc)
        return None


def check(ws) -> bool:
    """Evict ``ws``'s in-memory state if another process moved its epoch.  True if it was evicted.

    Called once per request of a workspace (``workspace.WorkspaceMiddleware``); one small file read.
    """
    if not roles.multi_process():
        return False
    try:
        now = _read(_file(ws))
        seen = ws.state.flag(_FLAG)
        if seen is None:
            ws.state.set_flag(_FLAG, now)             # first look at this workspace in this process: nothing to drop
            return False
        if now == seen:
            return False
        ws.state.evict()                              # also clears the flag
        ws.state.set_flag(_FLAG, now)
        log.info("workspace %s: another process changed the machine (epoch %s -> %s) - in-memory state dropped",
                 getattr(ws, "id", "?"), seen or "-", now or "-")
        return True
    except Exception as exc:                            # noqa: BLE001
        log.warning("state epoch check failed: %s", exc)
        return False
