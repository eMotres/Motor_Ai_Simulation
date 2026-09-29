"""Retention: personal data is kept for a stated time, then dropped.

Audit 2026-09-29 finding #17: sessions, auth events, MCP audit and tickets
grew without limit and api.log had no deletion policy.  One daily job
(``python -m motor_ai_sim.retention``, run by ``deploy/systemd/motres-retention.timer``)
applies the table below and then purges account-deletion requests whose grace
period ended (:func:`motor_ai_sim.account_lifecycle.run_due`).

=========================  ==============================  =======  ==========
data                       where                           env      default
=========================  ==============================  =======  ==========
sessions (expired/revoked)  identity/.sessions.json         RETENTION_SESSIONS_DAYS       30 after expiry
auth events (raw)           logs/auth_events.jsonl          RETENTION_AUTH_EVENTS_DAYS    90
MCP audit (raw)             identity/mcp_audit.jsonl        RETENTION_MCP_AUDIT_DAYS      90
usage per-job rows (raw)    history.sqlite ``usage``        RETENTION_USAGE_RAW_DAYS      90
usage daily aggregates      history.sqlite activity/storage RETENTION_USAGE_AGG_DAYS      400
api logs (rotated)          logs/api.log.*                  RETENTION_LOGS_DAYS           30
visitor chats               support/visitor_chats/*.jsonl   RETENTION_VISITOR_CHATS_DAYS  90
admin audit                 identity/admin_audit.jsonl      RETENTION_ADMIN_AUDIT_DAYS    730
=========================  ==============================  =======  ==========

Raw event logs are short-lived (90 d is enough for any incident or billing
dispute we have had); aggregates that pricing needs are kept 13 months; the
admin audit is kept two years because it is the accountability record.
Deleting a line is final: backups expire on their own schedule (restic
24 h / 30 d / 12 mo, ``deploy/backup``), so the longest a pruned line can
survive is 12 months in a monthly snapshot.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim.private_files import chmod_private, open_private

log = logging.getLogger(__name__)

DAY_S = 86400

DEFAULTS = {
    "RETENTION_SESSIONS_DAYS": 30,
    "RETENTION_AUTH_EVENTS_DAYS": 90,
    "RETENTION_MCP_AUDIT_DAYS": 90,
    "RETENTION_USAGE_RAW_DAYS": 90,
    "RETENTION_USAGE_AGG_DAYS": 400,
    "RETENTION_LOGS_DAYS": 30,
    "RETENTION_VISITOR_CHATS_DAYS": 90,
    "RETENTION_ADMIN_AUDIT_DAYS": 730,
}


def days(name: str) -> int:
    """Configured retention in days (env), never below 1."""
    try:
        return max(1, int(os.environ.get(name, "") or DEFAULTS[name]))
    except ValueError:
        return DEFAULTS[name]


def _ts_of(rec: Dict[str, Any]) -> Optional[float]:
    for k in ("t", "ts"):
        v = rec.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    iso = rec.get("iso") or rec.get("ts")
    if isinstance(iso, str):
        import calendar
        try:
            return float(calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            return None
    return None


def prune_jsonl(path: Path, cutoff: float, *, dry_run: bool = False) -> int:
    """Drop lines whose timestamp is older than ``cutoff``.  Lines without a
    readable timestamp are KEPT (never delete what we cannot date).  Returns
    how many lines were (or would be) dropped."""
    p = Path(path)
    if not p.is_file():
        return 0
    from motor_ai_sim.json_store import lock_for
    with lock_for(p):
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        keep: List[str] = []
        dropped = 0
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                keep.append(ln)
                continue
            ts = _ts_of(rec) if isinstance(rec, dict) else None
            if ts is not None and ts < cutoff:
                dropped += 1
            else:
                keep.append(ln)
        if dropped and not dry_run:
            tmp = p.with_suffix(p.suffix + ".tmp")
            with open_private(tmp, "w") as f:
                f.write("\n".join(keep) + ("\n" if keep else ""))
            os.replace(tmp, p)
            chmod_private(p)
    return dropped


def prune_sessions(now: float, *, dry_run: bool = False) -> int:
    from motor_ai_sim import sessions as S
    keep_s = days("RETENTION_SESSIONS_DAYS") * DAY_S
    with S._LOCK:
        try:
            d = S._load()
        except S.StoreUnavailable:
            return 0
        gone = []
        for sid, r in d.items():
            if not isinstance(r, dict):
                continue
            end = float(r.get("expires") or 0)
            if r.get("revoked"):
                end = min(end, float(r.get("revoked_at") or end))
            if end + keep_s < now:
                gone.append(sid)
        if gone and not dry_run:
            for sid in gone:
                d.pop(sid, None)
            S._save(d)
    return len(gone)


def prune_usage(now: float, *, dry_run: bool = False) -> Dict[str, int]:
    from motor_ai_sim import cluster_monitor as CM
    if not CM._db_file().is_file():
        return {}
    from motor_ai_sim import usage_stats as US
    raw_cut = now - days("RETENTION_USAGE_RAW_DAYS") * DAY_S
    agg_cut_day = time.strftime("%Y-%m-%d", time.gmtime(
        now - days("RETENTION_USAGE_AGG_DAYS") * DAY_S))
    out: Dict[str, int] = {}
    con = US._db()
    try:
        q = [("usage", "COALESCE(ts_end, ts_start) < ?", raw_cut),
             ("activity", "day < ?", agg_cut_day),
             ("storage", "day < ?", agg_cut_day)]
        for tbl, where, arg in q:
            if dry_run:
                out[tbl] = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {where}",
                                       (arg,)).fetchone()[0]
            else:
                out[tbl] = con.execute(f"DELETE FROM {tbl} WHERE {where}", (arg,)).rowcount
        if not dry_run:
            con.commit()
    finally:
        con.close()
    return out


def _logs_dir() -> Path:
    env = (os.environ.get("MOTOR_AI_SIM_LOG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "logs"


def prune_rotated_logs(now: float, *, dry_run: bool = False) -> int:
    """Delete rotated ``api.log.*`` files older than the retention (the live
    ``api.log`` is never touched)."""
    d = _logs_dir()
    if not d.is_dir():
        return 0
    cut = now - days("RETENTION_LOGS_DAYS") * DAY_S
    n = 0
    for p in d.glob("api.log.*"):
        try:
            if p.is_file() and p.stat().st_mtime < cut:
                n += 1
                if not dry_run:
                    p.unlink()
        except OSError:
            continue
    return n


def prune_visitor_chats(now: float, *, dry_run: bool = False) -> int:
    from motor_ai_sim import support_store as SS
    d = SS.chats_dir()
    if not d.is_dir():
        return 0
    cut_day = time.strftime("%Y-%m-%d", time.gmtime(
        now - days("RETENTION_VISITOR_CHATS_DAYS") * DAY_S))
    n = 0
    for p in d.glob("*.jsonl"):
        if p.stem < cut_day:
            n += 1
            if not dry_run:
                try:
                    p.unlink()
                except OSError:
                    n -= 1
    return n


def run(now: Optional[float] = None, *, dry_run: bool = False) -> Dict[str, Any]:
    """One retention pass.  Every step is independent: a failure is reported
    and the rest still runs."""
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import sessions as S
    t = time.time() if now is None else float(now)
    report: Dict[str, Any] = {"dry_run": dry_run, "at": t, "steps": {}}

    def step(name: str, fn: Callable[[], Any]) -> None:
        try:
            report["steps"][name] = fn()
        except Exception as e:                              # noqa: BLE001
            report["steps"][name] = f"FAILED: {type(e).__name__}: {e}"
            log.error("retention step %s failed (%s: %s)", name, type(e).__name__, e)

    step("sessions", lambda: prune_sessions(t, dry_run=dry_run))
    step("auth_events", lambda: prune_jsonl(
        S._EVENTS_FILE, t - days("RETENTION_AUTH_EVENTS_DAYS") * DAY_S, dry_run=dry_run))
    step("mcp_audit", lambda: prune_jsonl(
        K.audit_path(), t - days("RETENTION_MCP_AUDIT_DAYS") * DAY_S, dry_run=dry_run))
    step("admin_audit", lambda: prune_jsonl(
        AA.audit_path(), t - days("RETENTION_ADMIN_AUDIT_DAYS") * DAY_S, dry_run=dry_run))
    step("usage", lambda: prune_usage(t, dry_run=dry_run))
    step("api_logs", lambda: prune_rotated_logs(t, dry_run=dry_run))
    step("visitor_chats", lambda: prune_visitor_chats(t, dry_run=dry_run))
    if not dry_run:
        from motor_ai_sim import account_lifecycle as AL
        step("account_deletions_due", lambda: AL.run_due(t))
        AA.record("retention", "retention.run", details={
            k: (v if isinstance(v, (int, float, str)) else json.dumps(v))
            for k, v in report["steps"].items()})
    return report


def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.retention")
    ap.add_argument("--dry-run", action="store_true",
                    help="count what would be dropped, change nothing")
    a = ap.parse_args(argv)
    print(json.dumps(run(dry_run=a.dry_run), default=str))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
