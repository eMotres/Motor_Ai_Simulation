"""Resume incomplete sweeps after API restart.

At startup, this module:
1. Scans the workspace config directory (or directories) for an incomplete
   sweep journal.
2. Checks if the sweep should be resumed (not stale, machine fingerprint
   match, not already resumed by this process).
3. Re-enqueues the resumable sweep through the same code path a live "Run"
   uses (``_scan_worker``), so the resumed sweep obeys the same queue slot,
   cache and progress-reporting rules as a user-initiated one.

LAYERED-MODE DECISION (2026-09-19, review of the original Haiku-4.5 patch):
Automatic resume is active ONLY when ``WORKSPACES_ROOT`` is unset (single-
process / single-user mode). It is a deliberate no-op when layering is on
(the production server: ``WORKSPACES_ROOT`` set, ``QUEUE_WORKERS=2``), and
logs one line saying so. Reasons this is gated off rather than "made to
work" for multi-user:

* Workspace identity here would come from iterating workspace directories
  on disk, not from an authenticated caller — there is no HTTP request at
  process startup to carry ``vadim@motresres.com`` or any other identity.
  ``motor_ai_sim.jobs.current_owner()`` (what the queue admission and the
  cancel-ownership check use) falls back to the ambient workspace id when
  no caller is bound, so a startup-triggered resume would file every user's
  resumed job under a fabricated identity, not theirs.
* ``workspace.bind()`` — the mechanism every other background campaign
  thread uses to carry a user's config directory into its worker thread —
  captures the CURRENT context var at the moment it is called. At startup
  there is no request, so no workspace context is ever set, and every
  fingerprint / config read done from the startup path (e.g.
  ``routes.optimization._config_fingerprint``) resolves to the PROCESS
  workspace regardless of which user's directory is being scanned. Looping
  over ``WORKSPACES_ROOT`` subdirectories and calling that ambient-context
  code per directory computes the WRONG fingerprint for every workspace but
  the process one — silently comparing one user's machine against another's
  "current" config.
* Fixing this properly needs each resumed sweep to run inside an admitted
  JobQueue slot AS the owning user (workspace bound explicitly per
  directory, caller identity carried through, QUEUE_WORKERS=2 respected the
  same way a live request is), which is a real feature, not a bugfix — and
  starting FEM work for N users unattended at boot, on a server other agents
  and the owner are actively using, is exactly the kind of silent state
  mutation the project rules forbid without saying so first.

If multi-user auto-resume is wanted, the correct shape is: at startup, for
each workspace with a resumable journal, bind that workspace's identity
explicitly (not the ambient context) and submit through
``jobs.admit()``/``as_job`` exactly as a live scan does, so it queues behind
QUEUE_WORKERS like everyone else's work — and it should probably ask before
doing FEM work on N people's machines unattended, not merely queue quietly.
That is out of scope here; single-user resume (this file, today) is correct
and safe as implemented.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

log = logging.getLogger(__name__)

__all__ = ["resume_incomplete_sweeps"]

#: Sweep ids this PROCESS has already attempted to resume once. Startup calls
#: ``resume_incomplete_sweeps()`` exactly once in normal operation, but the
#: docstring promises "safe to call twice" — without this, a second call
#: (a test harness, a future lifespan change) would re-read a journal whose
#: state is still "running" (nothing marks it otherwise until the resumed
#: worker finishes) and spin up a SECOND worker thread for the same run_id,
#: racing the first one over ``_scan_state`` and the journal file.
_RESUMED_THIS_PROCESS: set = set()


def resume_incomplete_sweeps() -> None:
    """Scan for an incomplete sweep and resume it, single-user mode only.

    Called once at API startup. Safe to call multiple times — a sweep id
    already resumed by this process is not resumed again (see
    ``_RESUMED_THIS_PROCESS``).

    In multi-user mode (``WORKSPACES_ROOT`` set) this is a deliberate no-op;
    see the module docstring for why.
    """
    try:
        from motor_ai_sim import workspace as _WSP

        if _WSP.layering():
            log.info("sweep resumption skipped: WORKSPACES_ROOT is set "
                     "(layered/multi-user mode) — see sweep_resume module "
                     "docstring for why automatic resume is gated off here")
            return

        try:
            proc_ws = _WSP.process_workspace()
            ws_dir = proc_ws.root if proc_ws and proc_ws.root else None
        except Exception:
            ws_dir = None
        if not ws_dir:
            return

        _resume_sweep_in_workspace(str(ws_dir))

    except Exception as e:  # noqa: BLE001 — startup must never fail on this
        log.warning("sweep resumption failed: %s", e)


def _resume_sweep_in_workspace(ws_dir: str) -> None:
    """Resume a sweep in a single workspace directory, if one exists.

    Args:
        ws_dir: the workspace config directory to check
    """
    from motor_ai_sim import sweep_journal as _journal

    journal = _journal.load_sweep_journal(ws_dir)
    if not journal:
        return  # no journal at all

    if journal.sweep_id in _RESUMED_THIS_PROCESS:
        log.debug("sweep %s already resumed by this process, skipping",
                  journal.sweep_id)
        return

    log.info("found sweep journal: id=%s state=%s done=%d/%d",
             journal.sweep_id, journal.state, len(journal.done_points),
             journal.total_points)

    try:
        from motor_ai_sim.routes.optimization import _config_fingerprint
        current_fp = _config_fingerprint()
    except Exception:
        log.debug("could not compute current fingerprint for resume check")
        current_fp = "nofp"

    should_resume, reason = _journal.should_resume_sweep(journal, current_fp)
    if not should_resume:
        log.info("sweep %s will not resume: %s", journal.sweep_id, reason)
        # Only a journal that WAS "running" (i.e. genuinely interrupted but
        # disqualified by fingerprint/age) becomes "stale". A journal that is
        # already "finished" or "cancelled" is left exactly as it is — it
        # already recorded its own outcome, and overwriting that with "stale"
        # on every subsequent restart erased the real history for no reason.
        if journal.state == "running":
            _journal.mark_stale_sweep_journal(ws_dir, reason)
        return

    # Concurrent-resume guard: don't clobber a scan that is (somehow)
    # already running for this workspace — e.g. a fast double-call of
    # resume_incomplete_sweeps(), or a user's own Run racing startup.
    try:
        from motor_ai_sim.routes.optimization import _scan_state, _scan_lock
        with _scan_lock:
            if _scan_state.get("running"):
                log.info("sweep %s not resumed: a scan is already running "
                         "in this workspace", journal.sweep_id)
                return
    except Exception as e:
        log.debug("could not check scan state before resume: %s", e)
        return

    try:
        _enqueue_resume_sweep(ws_dir, journal)
        _RESUMED_THIS_PROCESS.add(journal.sweep_id)
    except Exception as e:
        log.warning("could not re-enqueue sweep %s for resume: %s",
                   journal.sweep_id, e)


def _enqueue_resume_sweep(ws_dir: str, journal) -> None:
    """Re-enqueue a sweep for resumption, through the normal scan-worker path.

    Args:
        ws_dir: workspace config directory
        journal: SweepJournalRecord with the sweep details
    """
    from motor_ai_sim import sweep_journal as _journal
    from motor_ai_sim.routes.optimization import (
        _scan_worker, _scan_state, _scan_lock, _scan_thread_set)
    from motor_ai_sim import workspace as _WSP
    from motor_ai_sim import jobs as _JOBS
    import threading

    if not journal.request_body:
        log.warning("sweep journal has no request body, cannot resume")
        return

    # Stamp the resume marker on the journal BEFORE starting the worker, and
    # write it atomically (temp+rename) — the whole point of this file is
    # surviving a restart mid-write, so the resume marker itself must not be
    # written with a bare open()/write() that a crash could truncate.
    resume_info = _journal.SweepResumeInfo(
        at=datetime.utcnow().isoformat() + "Z",
        done_before=len(journal.done_points),
        total=journal.total_points,
    )
    journal.resumed_from_restart = resume_info
    _journal.save_sweep_journal(ws_dir, journal)

    req_body = journal.request_body
    variables = req_body.get("variables", [])
    operating_points = req_body.get("operating_points", [])
    steps = req_body.get("steps_per_period", 6)
    ripple_max = req_body.get("ripple_max_pct", 100.0)
    max_geom = req_body.get("max_geometries", 24)
    coil_temp = req_body.get("coil_temp_c", 120.0)
    seed = req_body.get("seed", 12345)
    run_id = journal.sweep_id
    # The rest of _scan_worker's solver knobs — see the comment where
    # _req_body is built in routes/optimization.py: an older journal (written
    # before these keys existed) falls back to _scan_worker's own defaults,
    # a newer one reproduces the exact physics the original sweep used.
    worker_kwargs: Dict[str, Any] = {
        "mesh_size_mm": req_body.get("mesh_size_mm", 4.0),
        "min_size_mm": req_body.get("min_size_mm", 0.3),
        "pole_copy": req_body.get("pole_copy"),
        "torque_filter": req_body.get("torque_filter", False),
        "n_sectors": req_body.get("n_sectors", 1),
        "gap_layers": req_body.get("gap_layers", 3.0),
        "end_winding": req_body.get("end_winding", 0.0),
        "rotor_eddy": req_body.get("rotor_eddy", False),
        "hi_fidelity": req_body.get("hi_fidelity", False),
        "structured_gap": req_body.get("structured_gap", False),
        "airgap_macro": req_body.get("airgap_macro", False),
        "iron_template": req_body.get("iron_template", True),
        "geo_mesh": req_body.get("geo_mesh", True),
        "element_order": req_body.get("element_order", 2),
        "demag": req_body.get("demag", False),
        "with_baseline": req_body.get("with_baseline", False),
    }

    log.info("resuming sweep %s: %d of %d points already done",
             run_id, len(journal.done_points), journal.total_points)

    # ESTABLISH OWNERSHIP before starting the worker: `_scan_worker` bails
    # out immediately (`_scan_owns(run_id)` false) unless `_scan_state` already
    # names THIS run_id as the current one — exactly what `scan_designs()`
    # does for a live "Run" click, right before it starts the thread. Without
    # this the resumed worker returns on its first ownership check having
    # done nothing at all: no points computed, no journal update, silently.
    with _scan_lock:
        _scan_state.update({
            "running": True, "done": len(journal.done_points),
            "total": journal.total_points, "result": None, "points": [],
            "run_id": run_id, "error": None, "cancel": False,
            "cached": 0,
            "resumed_from_restart": {
                "at": resume_info.at,
                "done_before": resume_info.done_before,
                "total": resume_info.total,
            },
        })

    _new_sweep_thread = threading.Thread(
        target=_WSP.bind(_JOBS.as_job(
            "optimizer.scan",
            priority=_JOBS.Priority.CAMPAIGN,
            run_id=run_id,
        )(_scan_worker)),
        args=(variables, operating_points, steps, coil_temp, ripple_max,
             max_geom, seed, run_id),
        kwargs=worker_kwargs,
        daemon=True)
    _scan_thread_set(_new_sweep_thread)
    _new_sweep_thread.start()
    log.info("queued sweep resumption: id=%s", run_id)
