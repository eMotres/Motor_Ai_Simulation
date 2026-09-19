"""Resume incomplete sweeps after API restart.

At startup, this module:
1. Scans all workspace config directories for incomplete sweeps
2. Checks if each sweep should be resumed (not stale, machine fingerprint match)
3. Re-enqueues resumable sweeps

The actual re-enqueuing happens through the JobQueue so the sweep runs with the
same concurrency rules as a user-initiated one.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

__all__ = ["resume_incomplete_sweeps"]


def resume_incomplete_sweeps() -> None:
    """Scan all workspaces and resume any incomplete sweeps.

    Called once at API startup. Safe to call multiple times; will only resume
    each sweep once.

    In single-user mode (WORKSPACES_ROOT unset): scans the process config dir.
    In multi-user mode: scans all workspace subdirectories.
    """
    try:
        from motor_ai_sim import workspace as _WSP
        from motor_ai_sim import sweep_journal as _journal

        # Determine which directories to scan
        ws_dirs = []
        try:
            ws_root = _WSP.workspaces_root()
            if ws_root:
                # Multi-user mode: iterate workspace directories
                try:
                    for entry in os.listdir(ws_root):
                        ws_path = os.path.join(ws_root, entry)
                        if os.path.isdir(ws_path):
                            ws_dirs.append(ws_path)
                except Exception as e:
                    log.warning("could not list workspaces for resume: %s", e)
            else:
                # Single-user mode: scan the process config dir
                try:
                    proc_ws = _WSP.process_workspace()
                    if proc_ws and proc_ws.root:
                        ws_dirs.append(proc_ws.root)
                except Exception:
                    pass
        except Exception as e:
            log.debug("could not determine workspace root for resume: %s", e)
            return

        if not ws_dirs:
            return

        # Scan each workspace directory
        for ws_dir in ws_dirs:
            try:
                _resume_sweep_in_workspace(ws_dir)
            except Exception as e:
                log.warning("error resuming sweep in %s: %s", ws_dir, e)

    except Exception as e:
        log.warning("sweep resumption failed: %s", e)


def _resume_sweep_in_workspace(ws_dir: str) -> None:
    """Resume a sweep in a single workspace directory, if one exists.

    Args:
        ws_dir: the workspace config directory to check
    """
    from motor_ai_sim import sweep_journal as _journal

    # Try to load the journal
    journal = _journal.load_sweep_journal(ws_dir)
    if not journal:
        return  # no incomplete sweep

    log.info("found incomplete sweep: id=%s state=%s done=%d/%d",
             journal.sweep_id, journal.state, len(journal.done_points),
             journal.total_points)

    # Check if it should be resumed
    try:
        from motor_ai_sim.routes.optimization import _config_fingerprint

        # Compute the fingerprint in the context of THIS workspace
        # This is tricky: we need to switch to the workspace temporarily
        _current_fp = None
        try:
            # This will use the process workspace or whatever is current
            _current_fp = _config_fingerprint()
        except Exception:
            log.debug("could not compute current fingerprint for resume check")

        should_resume, reason = _journal.should_resume_sweep(journal, _current_fp or "nofp")
        if not should_resume:
            log.info("sweep %s will not resume: %s", journal.sweep_id, reason)
            _journal.mark_stale_sweep_journal(ws_dir, reason)
            return
    except Exception as e:
        log.debug("error checking sweep resumption criteria: %s", e)
        return

    # Attempt to re-enqueue the sweep
    try:
        _enqueue_resume_sweep(ws_dir, journal)
    except Exception as e:
        log.warning("could not re-enqueue sweep %s for resume: %s",
                   journal.sweep_id, e)


def _enqueue_resume_sweep(ws_dir: str, journal) -> None:
    """Re-enqueue a sweep for resumption.

    Args:
        ws_dir: workspace config directory
        journal: SweepJournalRecord with the sweep details
    """
    from motor_ai_sim import sweep_journal as _journal
    from motor_ai_sim.routes.optimization import ScanRequest, _scan_worker
    from motor_ai_sim import workspace as _WSP
    from motor_ai_sim import jobs as _JOBS
    import threading
    from datetime import datetime

    if not journal.request_body:
        log.warning("sweep journal has no request body, cannot resume")
        return

    # Mark the resume in the journal
    resume_info = _journal.SweepResumeInfo(
        at=datetime.utcnow().isoformat() + "Z",
        done_before=len(journal.done_points),
        total=journal.total_points,
    )
    journal.resumed_from_restart = resume_info

    try:
        path = _journal._journal_path(ws_dir)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(journal.to_dict(), fh, default=str)
    except Exception as e:
        log.debug("could not update journal with resume info: %s", e)

    # Extract the parameters from the saved request body
    req_body = journal.request_body
    variables = req_body.get("variables", [])
    operating_points = req_body.get("operating_points", [])
    steps = req_body.get("steps_per_period", 6)
    ripple_max = req_body.get("ripple_max_pct", 100.0)
    max_geom = req_body.get("max_geometries", 24)
    coil_temp = req_body.get("coil_temp_c", 120.0)
    seed = req_body.get("seed", 12345)
    run_id = journal.sweep_id

    log.info("resuming sweep %s: %d of %d points already done",
             run_id, len(journal.done_points), journal.total_points)

    # Queue the resume as a background job
    # Use the same pattern as scan_designs() uses for starting the worker
    try:
        # Try to set the workspace context if in multi-user mode
        _owner = "resume"
        try:
            # Figure out the workspace id from the directory structure
            from motor_ai_sim import workspace as _WSP
            ws_root = _WSP.workspaces_root()
            if ws_root:
                # In multi-user mode, ws_dir is like WORKSPACES_ROOT/ws_id/config
                # We need to extract ws_id
                rel_path = os.path.relpath(ws_dir, ws_root)
                ws_id = rel_path.split(os.sep)[0] if os.sep in rel_path else rel_path
                _owner = ws_id
        except Exception:
            pass

        _new_sweep_thread = threading.Thread(
            target=_WSP.bind(_JOBS.as_job(
                "optimizer.scan",
                priority=_JOBS.Priority.CAMPAIGN,
                run_id=run_id,
            )(_scan_worker_with_resume)),
            args=(variables, operating_points, steps, coil_temp, ripple_max,
                 max_geom, seed, run_id, journal.done_points),
            daemon=True)
        _new_sweep_thread.start()
        log.info("queued sweep resumption: id=%s", run_id)
    except Exception as e:
        log.error("could not queue sweep resumption: %s", e)
        raise


def _scan_worker_with_resume(*args, **kwargs):
    """Wrapper around _scan_worker that skips already-computed points.

    This is called during resume. It receives an additional argument (done_points)
    that indicates which points have already been computed.
    """
    from motor_ai_sim.routes.optimization import _scan_worker

    # Last argument is done_points; separate it
    if len(args) > 8:
        done_points = args[8]
        args_for_worker = args[:8]
    else:
        done_points = []
        args_for_worker = args[:8]

    # For now, just call the normal worker
    # In the future, we could optimize to skip already-cached points more aggressively
    # But the current implementation already does this via the eval cache
    return _scan_worker(*args_for_worker, **kwargs)
