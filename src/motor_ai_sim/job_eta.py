"""WHERE IS MY RUN IN THE QUEUE, AND WHEN WILL IT BE DONE?  (owner 2026-10-27)

«Нужно предусмотреть очередь расчётов на серверах, если вдруг много клиентов запустят проекты, чтобы они видели, в каком
состоянии их проекты и когда примерно можно ожидать результат.»

Three small, pure pieces (nothing here touches HTTP or a live queue, so every rule is testable with a frozen clock):

1. :class:`DurationStats` - what a run of each kind has taken so far.  Learned from FINISHED runs of the registry: the last
   ``KEEP`` (50) durations per key, median and 80th percentile.  Two keys per finished run: the kind alone (``transient``) and
   the kind with a size class (``transient|s7``: ``ceil(log2(total steps))`` of the run's own progress bar - the only size
   the registry records).  Persisted as one compact JSON file (``MOTRES_JOB_STATS``, default
   ``<shared>/compute/job_duration_stats.json``); an unwritable file only means the statistics restart empty.
2. :func:`simulate` - replays the scheduler (``jobs.InProcessQueue``: slots per class, priority, then the account that has
   STARTED the fewest runs, then arrival order, at most ``per_user`` runs of one account at once) on the expected remaining
   times of the running runs.  The result is, for every live run, its place in the order the scheduler will START the
   waiting runs (``position``/``ahead``) and an expected start and finish.  A queued run starts when a slot is freed, so
   its start is NOT "now + sum of the runs ahead": it is the moment the replayed scheduler gives it a slot, which respects
   workers, priorities and the fairness rule together.
3. Honest numbers: no history for a kind = no time at all (``basis: "no_history"``, the page says "estimate after the first
   runs"); times are rounded to 5 minutes under an hour away and to 15 minutes beyond; confidence is ``low|medium|high``
   from the sample count and spread (or from the run's own progress).

Customer-safe by construction: the inputs are anonymous (a workspace id is only used to count, never returned).
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ENV_STATS = "MOTRES_JOB_STATS"
#: durations kept per key (rolling window)
KEEP = 50
#: a size-class key is trusted over the kind's own once it has this many samples
MIN_SIZE_SAMPLES = 3
#: progress is "meaningful" from this fraction (and this much elapsed time): the mesh build is not progress
MIN_FRAC = 0.05
MIN_ELAPSED_S = 20.0
#: a run past its p80 is "overdue": remaining is assumed to be this share of the median
OVERDUE_SHARE = 0.25
#: a run still inside its median never claims less than this share of it remaining
FLOOR_SHARE = 0.10
#: ordering-only placeholder (seconds) for a run whose duration nobody knows
_UNKNOWN_S = 600.0

CONFIDENCE = ("low", "medium", "high")


def _min_conf(*levels: str) -> str:
    return CONFIDENCE[min(CONFIDENCE.index(x) if x in CONFIDENCE else 0 for x in levels)] if levels else "low"


# ── statistics ───────────────────────────────────────────────────────────────

def percentile(values: List[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in 0..1) of ``values`` (non-empty)."""
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def size_class(total_steps: Any) -> Optional[str]:
    """``s<k>`` with ``k = ceil(log2(total))`` of the run's own step count; ``None`` when the run recorded none."""
    try:
        n = float(total_steps)
    except (TypeError, ValueError):
        return None
    if not (n >= 1.0) or math.isinf(n):
        return None
    return "s%d" % int(math.ceil(math.log2(n)))


def _key(kind: str, size: Optional[str]) -> str:
    kind = str(kind or "").strip().lower()
    return "%s|%s" % (kind, size) if size else kind


def default_stats_path() -> Path:
    raw = os.environ.get(ENV_STATS, "").strip()
    if raw:
        return Path(raw).expanduser()
    from motor_ai_sim import workspace as _WSP
    return Path(str(_WSP.shared_root())) / "compute" / "job_duration_stats.json"


class DurationStats:
    """Rolling duration statistics per run kind (+ size class).  Thread-safe; every failure degrades to 'no history'."""

    def __init__(self, path: Optional[Path] = None, keep: int = KEEP) -> None:
        self._path = Path(path) if path is not None else None
        self._keep = int(keep)
        self._lock = threading.Lock()
        self._data: Dict[str, List[float]] = {}
        self._loaded = False
        self._mtime = 0.0

    def _file(self) -> Optional[Path]:
        try:
            return self._path if self._path is not None else default_stats_path()
        except Exception:                                   # noqa: BLE001
            return None

    def _load_locked(self) -> None:
        p = self._file()
        if p is None:
            self._loaded = True
            return
        try:
            mt = p.stat().st_mtime
            if self._loaded and mt == self._mtime:
                return
            doc = json.loads(p.read_text(encoding="utf-8"))
            keys = doc.get("keys") if isinstance(doc, dict) else None
            data: Dict[str, List[float]] = {}
            for k, v in (keys or {}).items():
                if isinstance(v, list):
                    vals = [float(x) for x in v if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
                    if vals:
                        data[str(k)] = vals[-self._keep:]
            self._data, self._mtime = data, mt
        except (OSError, ValueError, TypeError):
            pass                                            # missing / unreadable = keep what is in memory
        self._loaded = True

    def _save_locked(self) -> None:
        p = self._file()
        if p is None:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
            tmp.write_text(json.dumps({"version": 1, "keep": self._keep,
                                       "keys": {k: [round(x, 1) for x in v] for k, v in sorted(self._data.items())}},
                                      separators=(",", ":")), encoding="utf-8")
            os.replace(str(tmp), str(p))
            self._mtime = p.stat().st_mtime
        except OSError as exc:
            log.debug("job duration stats not persisted: %s", exc)

    def record(self, kind: str, seconds: float, size: Optional[str] = None) -> None:
        """File one FINISHED run's duration under its kind and (when known) its kind + size class."""
        try:
            sec = float(seconds)
        except (TypeError, ValueError):
            return
        if not (sec > 0.0) or math.isinf(sec) or not str(kind or "").strip():
            return
        with self._lock:
            self._load_locked()
            for k in {_key(kind, None), _key(kind, size)}:
                self._data.setdefault(k, []).append(sec)
                del self._data[k][:-self._keep]
            self._save_locked()

    def lookup(self, kind: str, size: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """``{median, p80, n, confidence, key}`` for the run kind, or ``None`` when nothing was ever recorded."""
        with self._lock:
            self._load_locked()
            vals = None
            key = _key(kind, None)
            if size:
                sized = self._data.get(_key(kind, size))
                if sized and len(sized) >= MIN_SIZE_SAMPLES:
                    vals, key = list(sized), _key(kind, size)
            if vals is None:
                vals = list(self._data.get(_key(kind, None)) or [])
        if not vals:
            return None
        med, p80 = percentile(vals, 0.5), percentile(vals, 0.8)
        return {"median": med, "p80": p80, "n": len(vals), "key": key, "confidence": _stats_confidence(len(vals), med, p80)}


def _stats_confidence(n: int, median: float, p80: float) -> str:
    spread = (p80 / median) if median > 0 else 99.0
    if n >= 20 and spread <= 1.5:
        return "high"
    if n >= 5 and spread <= 3.0:
        return "medium"
    return "low"


_STATS: Optional[DurationStats] = None
_STATS_GUARD = threading.Lock()


def stats() -> DurationStats:
    global _STATS
    with _STATS_GUARD:
        if _STATS is None:
            _STATS = DurationStats()
        return _STATS


def reset_stats(s: Optional[DurationStats] = None) -> DurationStats:
    global _STATS
    with _STATS_GUARD:
        _STATS = s or DurationStats()
        return _STATS


# ── one run ──────────────────────────────────────────────────────────────────

def running_remaining(kind: str, elapsed_s: float, frac: Optional[float], total: Any,
                      st: DurationStats) -> Tuple[Optional[float], str, str]:
    """Expected seconds left of a RUNNING run: ``(remaining | None, confidence, basis)``.

    From its own progress when that is meaningful (fraction >= 5 % and 20 s elapsed): ``elapsed * (1 - f) / f``.  Otherwise
    from the kind's statistics: ``median - elapsed`` (never below 10 % of the median); past the 80th percentile the run is
    overdue and gets a quarter of the median at low confidence.  No statistics = unknown.
    """
    elapsed = max(0.0, float(elapsed_s or 0.0))
    if frac is not None and frac >= MIN_FRAC and elapsed >= MIN_ELAPSED_S:
        f = min(float(frac), 1.0)
        return elapsed * (1.0 - f) / f, ("high" if f >= 0.5 else "medium"), "progress"
    stat = st.lookup(kind, size_class(total))
    if stat is None:
        return None, "low", "no_history"
    med, p80 = stat["median"], stat["p80"]
    if elapsed > max(p80, med):
        return med * OVERDUE_SHARE, "low", "history"
    return max(med - elapsed, med * FLOOR_SHARE), stat["confidence"], "history"


def queued_duration(kind: str, st: DurationStats) -> Tuple[Optional[float], str]:
    stat = st.lookup(kind, None)
    return (stat["median"], stat["confidence"]) if stat else (None, "low")


# ── the scheduler, replayed ──────────────────────────────────────────────────

def simulate(runs: List[Dict[str, Any]], env: Dict[str, Any], now: float, st: DurationStats) -> Dict[str, Dict[str, Any]]:
    """Replay :class:`jobs.InProcessQueue` on expected durations.  ``{run_id: schedule}`` for every live run.

    ``runs``: ``{run_id, state running|queued, priority, ws, seq, kind, started_at, frac, total}``.
    ``env``: ``{workers, field_limit, per_user, field_priority, served: {ws: n}}``.

    A running run's entry has ``expected_finish``; a queued run's has ``position`` (1 = the next to start), ``ahead``
    (queued runs that start before it), ``expected_start`` and ``expected_finish``.  Times are unrounded epoch seconds or
    ``None`` when a run on the way to a slot has no history; ``confidence`` and ``basis`` say how far to trust them.
    """
    workers = max(1, int(env.get("workers") or 1))
    field_limit = max(1, int(env.get("field_limit") or 1))
    per_user = int(env.get("per_user") or 0)
    field_prio = int(env.get("field_priority", 1))
    served = {str(k): int(v) for k, v in (env.get("served") or {}).items()}
    out: Dict[str, Dict[str, Any]] = {}

    # active = [end_t, end_known, conf, run]
    active: List[List[Any]] = []
    for r in runs:
        if r.get("state") != "running":
            continue
        elapsed = max(0.0, now - float(r.get("started_at") or now))
        rem, conf, basis = running_remaining(str(r.get("kind") or ""), elapsed, r.get("frac"), r.get("total"), st)
        if rem is None:
            active.append([now + max(1.0, _UNKNOWN_S - elapsed), False, "low", r])
            out[r["run_id"]] = {"state": "running", "expected_finish": None, "confidence": "low", "basis": "no_history"}
        else:
            active.append([now + rem, True, conf, r])
            out[r["run_id"]] = {"state": "running", "expected_finish": now + rem, "confidence": conf, "basis": basis}

    waiting = [r for r in runs if r.get("state") == "queued"]
    t, t_known, t_conf = now, True, "high"
    order = 0

    def is_field(r: Dict[str, Any]) -> bool:
        return int(r.get("priority", 2)) == field_prio

    def pick() -> Optional[Dict[str, Any]]:
        n_field = sum(1 for a in active if is_field(a[3]))
        n_other = len(active) - n_field
        per_ws: Dict[str, int] = {}
        for a in active:
            if not is_field(a[3]):
                per_ws[str(a[3].get("ws"))] = per_ws.get(str(a[3].get("ws")), 0) + 1
        for w in sorted(waiting, key=lambda r: (int(r.get("priority", 2)), served.get(str(r.get("ws")), 0), int(r.get("seq", 0)))):
            if is_field(w):
                if n_field >= field_limit:
                    continue
            else:
                if n_other >= workers:
                    continue
                if per_user and per_ws.get(str(w.get("ws")), 0) >= per_user:
                    continue
            return w
        return None

    while waiting:
        w = pick()
        if w is None:
            if not active:                                  # nothing can ever start (a misconfigured cap): order only
                for r in sorted(waiting, key=lambda x: (int(x.get("priority", 2)), int(x.get("seq", 0)))):
                    order += 1
                    out[r["run_id"]] = {"state": "queued", "position": order, "ahead": order - 1, "expected_start": None,
                                        "expected_finish": None, "confidence": "low", "basis": "no_history"}
                break
            active.sort(key=lambda a: a[0])
            end_t, known, conf, _ = active.pop(0)
            t = max(t, end_t)
            t_known = t_known and known
            t_conf = _min_conf(t_conf, conf)
            continue
        waiting.remove(w)
        order += 1
        dur, dconf = queued_duration(str(w.get("kind") or ""), st)
        start_known = t_known
        res: Dict[str, Any] = {"state": "queued", "position": order, "ahead": order - 1}
        if start_known and dur is not None:
            res.update(expected_start=t, expected_finish=t + dur, confidence=_min_conf(t_conf, dconf, "medium"), basis="queue")
        elif start_known:
            res.update(expected_start=t, expected_finish=None, confidence="low", basis="no_history")
        else:
            res.update(expected_start=None, expected_finish=None, confidence="low", basis="no_history")
        out[w["run_id"]] = res
        served[str(w.get("ws"))] = served.get(str(w.get("ws")), 0) + 1
        active.append([t + (dur if dur is not None else _UNKNOWN_S), bool(start_known and dur is not None),
                       _min_conf(t_conf, dconf), w])
    return out


# ── honest display ───────────────────────────────────────────────────────────

def round_epoch(ts: Optional[float], now: float) -> Optional[float]:
    """Round a clock time to 5 minutes when it is under an hour away, to 15 minutes beyond (never into the past)."""
    if ts is None:
        return None
    step = 300.0 if (ts - now) < 3600.0 else 900.0
    return max(round(ts / step) * step, math.floor(now / 300.0) * 300.0)


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def public_eta(entry: Dict[str, Any], now: float) -> Dict[str, Any]:
    """The customer-facing ``eta`` object of one schedule entry (rounded ISO times, or ``None`` + ``no_history``)."""
    start = round_epoch(entry.get("expected_start"), now)
    finish = round_epoch(entry.get("expected_finish"), now)
    if finish is None:
        return {"expected_start": iso(start), "expected_finish": None,
                "confidence": "low", "basis": "no_history" if entry.get("basis") in (None, "no_history") else entry["basis"]}
    return {"expected_start": iso(start), "expected_finish": iso(finish),
            "confidence": entry.get("confidence") or "low", "basis": entry.get("basis") or "history"}


def note_finished(kind: str, started_at: float, finished_at: float, total: Any = None,
                  st: Optional[DurationStats] = None) -> None:
    """Hook for the queue: file a DONE run's duration.  Never raises."""
    try:
        if started_at and finished_at > started_at:
            (st or stats()).record(kind, finished_at - started_at, size_class(total))
    except Exception as exc:                                # noqa: BLE001
        log.debug("job duration not recorded: %s", exc)
