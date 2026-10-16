"""Sweep persistence and resumption across API restarts.

A running parameter sweep's progress lives in memory during execution. This module
persists sweep state to disk so that:

1. If the API crashes/restarts mid-sweep, the sweep can be resumed automatically
2. Already-cached points are not recomputed
3. The web UI shows the resume with a timestamp and progress count

The journal is stored per workspace in `<config_dir>/.sweep_journal.json` and
updated atomically as each point completes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

__all__ = [
    "SweepJournal", "SweepJournalRecord", "SweepResumeInfo",
    "create_sweep_journal", "update_sweep_journal", "finish_sweep_journal",
    "cancel_sweep_journal", "mark_stale_sweep_journal", "save_sweep_journal",
    "load_sweep_journal", "should_resume_sweep",
    "JOURNAL_FILE",
]

JOURNAL_FILE = ".sweep_journal.json"


@dataclass
class SweepResumeInfo:
    """Information about a resumed sweep (shown in the UI)."""
    at: str  # ISO timestamp when resume was triggered
    done_before: int  # number of points already done before resume
    total: int  # total points in the sweep


@dataclass
class SweepJournalRecord:
    """A sweep's persistence record on disk."""
    sweep_id: str
    started_at: str  # ISO timestamp
    owner: str  # workspace id
    machine_fp: str  # config fingerprint for stale detection
    request_body: Dict[str, Any]  # full ScanRequest as dict
    total_points: int
    done_points: List[int] = field(default_factory=list)  # indices of computed points
    state: str = "running"  # running|cancelled|finished|stale
    resumed_from_restart: Optional[SweepResumeInfo] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.resumed_from_restart:
            d["resumed_from_restart"] = asdict(self.resumed_from_restart)
        else:
            d["resumed_from_restart"] = None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SweepJournalRecord:
        resume_d = d.get("resumed_from_restart")
        resume = None
        if resume_d and isinstance(resume_d, dict):
            resume = SweepResumeInfo(**resume_d)
        return cls(
            sweep_id=d.get("sweep_id", ""),
            started_at=d.get("started_at", ""),
            owner=d.get("owner", ""),
            machine_fp=d.get("machine_fp", ""),
            request_body=d.get("request_body", {}),
            total_points=int(d.get("total_points", 0)),
            done_points=list(d.get("done_points", [])),
            state=d.get("state", "running"),
            resumed_from_restart=resume,
            error=d.get("error"),
        )


def _journal_path(config_dir: str) -> str:
    """Full path to the sweep journal in a workspace config directory."""
    return os.path.abspath(os.path.join(config_dir, JOURNAL_FILE))


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """Write JSON atomically to a file via temp+rename."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str)
        os.replace(tmp, path)
    except Exception as e:
        log.warning("failed to write sweep journal: %s", e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def create_sweep_journal(
    config_dir: str,
    sweep_id: str,
    request_body: Dict[str, Any],
    total_points: int,
    owner: str,
    machine_fp: str,
) -> SweepJournalRecord:
    """Create a new sweep journal at the start of a sweep.

    Args:
        config_dir: workspace config directory
        sweep_id: unique id for this sweep (run_id)
        request_body: the full ScanRequest as a dict
        total_points: total number of points to evaluate
        owner: workspace id (for resumption attribution)
        machine_fp: config fingerprint (to detect stale machines)

    Returns:
        SweepJournalRecord that was written
    """
    record = SweepJournalRecord(
        sweep_id=sweep_id,
        started_at=datetime.utcnow().isoformat() + "Z",
        owner=owner,
        machine_fp=machine_fp,
        request_body=request_body,
        total_points=total_points,
        done_points=[],
        state="running",
    )
    _atomic_write_json(_journal_path(config_dir), record.to_dict())
    log.info("created sweep journal: id=%s total=%d", sweep_id, total_points)
    return record


def update_sweep_journal(
    config_dir: str,
    sweep_id: str,
    point_index: int,
) -> None:
    """Mark one more point as done in the journal.

    Args:
        config_dir: workspace config directory
        sweep_id: id of the sweep being updated
        point_index: index of the point that just completed
    """
    try:
        path = _journal_path(config_dir)
        if not os.path.exists(path):
            return  # no journal, skip
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        record = SweepJournalRecord.from_dict(data)
        if record.sweep_id != sweep_id or record.state != "running":
            return  # not our sweep or already finished
        if point_index not in record.done_points:
            record.done_points.append(point_index)
            _atomic_write_json(path, record.to_dict())
    except Exception as e:
        log.debug("could not update sweep journal: %s", e)


def finish_sweep_journal(config_dir: str, sweep_id: str) -> None:
    """Mark the sweep as finished."""
    try:
        path = _journal_path(config_dir)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        record = SweepJournalRecord.from_dict(data)
        if record.sweep_id != sweep_id:
            return  # not our sweep
        record.state = "finished"
        _atomic_write_json(path, record.to_dict())
        log.info("finished sweep journal: id=%s", sweep_id)
    except Exception as e:
        log.debug("could not finish sweep journal: %s", e)


def cancel_sweep_journal(config_dir: str, sweep_id: str) -> None:
    """Mark the sweep as cancelled (do not resume)."""
    try:
        path = _journal_path(config_dir)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        record = SweepJournalRecord.from_dict(data)
        if record.sweep_id != sweep_id:
            return
        record.state = "cancelled"
        _atomic_write_json(path, record.to_dict())
        log.info("cancelled sweep journal: id=%s", sweep_id)
    except Exception as e:
        log.debug("could not cancel sweep journal: %s", e)


def mark_stale_sweep_journal(config_dir: str, reason: str) -> None:
    """Mark the current journal as stale (do not resume)."""
    try:
        path = _journal_path(config_dir)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        record = SweepJournalRecord.from_dict(data)
        record.state = "stale"
        record.error = reason
        _atomic_write_json(path, record.to_dict())
        log.info("marked sweep journal as stale: %s", reason)
    except Exception as e:
        log.debug("could not mark sweep journal as stale: %s", e)


def save_sweep_journal(config_dir: str, record: SweepJournalRecord) -> None:
    """Persist an already-built record verbatim, atomically.

    Used by the resume path to stamp ``resumed_from_restart`` on a loaded
    journal before re-enqueuing its worker — a plain ``open(...).write()``
    there defeated the whole point of this module (surviving a restart
    mid-write) the moment IT got interrupted mid-write.
    """
    _atomic_write_json(_journal_path(config_dir), record.to_dict())


def load_sweep_journal(config_dir: str) -> Optional[SweepJournalRecord]:
    """Load the journal for a workspace, or None if no incomplete sweep."""
    try:
        path = _journal_path(config_dir)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return SweepJournalRecord.from_dict(data)
    except Exception as e:
        log.debug("could not load sweep journal: %s", e)
        return None


def should_resume_sweep(
    journal: SweepJournalRecord,
    current_machine_fp: str,
    max_age_days: int = 7,
) -> tuple[bool, str]:
    """Decide whether a persisted sweep should be resumed.

    Args:
        journal: the loaded journal record
        current_machine_fp: the current config's fingerprint
        max_age_days: don't resume sweeps older than this

    Returns:
        (should_resume, reason) where reason explains the decision
    """
    if journal.state != "running":
        return False, f"sweep state is {journal.state}, not running"
    if journal.machine_fp != current_machine_fp:
        return False, f"machine fingerprint mismatch (was {journal.machine_fp[:8]}…, now {current_machine_fp[:8]}…)"
    try:
        started = datetime.fromisoformat(journal.started_at.rstrip("Z"))
        age_days = (datetime.utcnow() - started).days
        if age_days > max_age_days:
            return False, f"sweep is {age_days} days old (limit: {max_age_days})"
    except Exception:
        return False, "could not parse sweep timestamp"
    if not journal.total_points or not journal.request_body:
        return False, "incomplete journal record"
    return True, ""


# For testing
class SweepJournal:
    """Test-friendly wrapper for sweep journal operations."""

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        self.path = _journal_path(config_dir)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def read(self) -> Optional[SweepJournalRecord]:
        return load_sweep_journal(self.config_dir)

    def delete(self) -> None:
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception:
            pass
