"""Live progress for the ONE request that builds a client report.

WHY THIS EXISTS
---------------
User, 2026-09-16: *"нужно сделать ещё минимальный прогресс-ринг генерации
отчёта, чтобы было видно, что работает, а не висит"*.  A Word report of a
200 mm machine is ~50 s (twenty-odd matplotlib figures at print width, 43
pages) and the PDF is half a minute more — and for all of that the catalogue
row showed the button's own "… report" and nothing else.  A caption that does
not move is read as a hung server, which is the same bug :mod:`motor_ai_sim
.progress` was written to close for the solver tabs.

So: no second mechanism.  The report reports into the SAME
:class:`~motor_ai_sim.progress.ProgressRegistry`, keyed by the same kind of
``run_id``, and the payload the ring polls is the same invariant snapshot the
solve strips already parse — plus the three keys a download needs and a solve
does not (``stage``, ``done``, ``error``).

WHY A CONTEXTVAR AND NOT AN ARGUMENT
------------------------------------
Figures are produced sixty call frames below the route, from a dozen sections,
and half of them behind ``pair_duties`` helpers that take no state at all.
Threading a progress handle through that is a hundred-line diff across two
renderers for a bar.  The build is SYNCHRONOUS in the request thread (neither
renderer starts a thread — checked), so a ContextVar set by :func:`build` is
read by :func:`figure_done` wherever the figure happens to be drawn, and is
``None`` — every call a no-op — in every test and script that renders a report
without a route.

WHY THE STEP COUNT IS A BUDGET
------------------------------
Nobody knows how many figures a given machine's report has until it is built:
a configuration with one duty draws single pictures where one with two draws
pairs, a machine with no modal solve draws no gallery.  So the total is a
BUDGET — :data:`DEFAULT_FIGURES` the first time, and afterwards what this
exact (die, configuration, format) actually drew last time, which is the only
honest estimate available.  If the build overruns it the total GROWS (a bar
that sticks at 100 % while the server works is the bug again); if it comes in
under, the unused budget is handed back at the ``tables`` stage so the ring
reaches its end when the file does.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

from motor_ai_sim import progress as _PROG

__all__ = ["ROUTE", "DEFAULT_FIGURES", "ReportProgress", "build", "current",
           "figure_done", "stage", "snapshot", "remembered_figures"]

#: The registry's ``route`` for a report build.  Dotted kinds under it
#: (``report.docx`` / ``report.pdf``) are what ``GET /api/jobs`` prints, and
#: ``progress._route_matches`` already treats them as this route's own.
ROUTE = "report"

#: Figures budgeted before anything is known about this machine.  Twenty is
#: what the 43-page L155 report draws; the number only sets the FIRST build's
#: scale, because the real count is remembered below.
DEFAULT_FIGURES = 20

#: The non-figure ticks.  Reading every solver's store is one, the tables and
#: the story assembly one, and writing the file two — three for the PDF, whose
#: reportlab pass genuinely typesets 43 pages after the last figure is drawn.
TICKS_RECORDS = 1
TICKS_TABLES = 1
TICKS_WRITE = {"docx": 2, "pdf": 3}

#: The stage names the UI switches on, in the order they occur.
STAGES = ("records", "figures", "tables", "docx", "pdf", "done", "failed")

#: One short line each — the ring's tooltip, and nothing longer (the standing
#: "no text walls" rule: one short line + tooltip).
LABELS = {
    "records": "reading stored results",
    "figures": "figures",
    "tables": "tables",
    "docx": "writing the Word file",
    "pdf": "typesetting the PDF",
    "done": "done",
    "failed": "failed",
}

#: (die, cfg, format) → how many figures that report actually drew last time.
#: Process-wide and tiny; it only ever makes the NEXT bar of the same machine
#: honest, so a cold process losing it costs one slightly wrong first build.
_LAST_FIGURES: "Dict[str, int]" = {}
_LAST_LOCK = threading.Lock()


def remembered_figures(key: str) -> int:
    with _LAST_LOCK:
        return int(_LAST_FIGURES.get(str(key), 0)) or DEFAULT_FIGURES


def _remember_figures(key: str, n: int) -> None:
    if n > 0:
        with _LAST_LOCK:
            _LAST_FIGURES[str(key)] = int(n)


class ReportProgress:
    """One report build, publishing into the shared registry.

    Not a second tracker: it OWNS a :class:`~motor_ai_sim.progress
    .ProgressTracker` out of the process registry (the one keyed by run id,
    deliberately not per workspace — the poll is a different request, and under
    ``WorkspaceMiddleware`` that request is bound to its own workspace), and
    adds only what a download has that a solve has not: which stage, whether
    the file is on its way, and the sentence if it is not.
    """

    __slots__ = ("run_id", "fmt", "key", "_tracker", "_entry", "_figs",
                 "_budget", "_stage", "_t0")

    def __init__(self, run_id: str, fmt: str, key: str = "",
                 budget: Optional[int] = None) -> None:
        self.run_id = str(run_id)
        self.fmt = str(fmt or "docx")
        self.key = str(key or "")
        self._figs = 0
        self._budget = int(budget if budget is not None
                           else remembered_figures(self.key))
        self._stage = "records"
        self._t0 = time.time()
        reg = _PROG.registry()
        owner = ""
        try:
            from motor_ai_sim import jobs as _jobs
            owner = _jobs.current_owner()
        except Exception:                                   # noqa: BLE001
            owner = ""
        self._entry = reg.entry(self.run_id, create=True,
                                route="%s.%s" % (ROUTE, self.fmt), owner=owner)
        self._tracker = (self._entry.tracker if self._entry is not None
                         else _PROG.ProgressTracker())
        self._state()["format"] = self.fmt

    # ── the little state a download has and a solve has not ─────────────────
    def _state(self) -> Dict[str, Any]:
        """This run's report-shaped extras, living on the registry entry.

        ``_Entry.extra`` is exactly the slot ``static3d`` keeps its own solve
        state in — a per-run dict in the router's OWN shape — so the error text
        and the stage survive in the same place the counters do and are evicted
        with them by the same TTL.
        """
        if self._entry is None:
            return {}
        return self._entry.extra.setdefault(ROUTE, {})

    @property
    def total(self) -> int:
        return (TICKS_RECORDS + self._budget + TICKS_TABLES
                + TICKS_WRITE.get(self.fmt, 2))

    @property
    def figures(self) -> int:
        return self._figs

    @property
    def stage_name(self) -> str:
        return self._stage

    def _mark(self, stage: str, step: Optional[int] = None) -> None:
        self._stage = stage
        self._state()["stage"] = stage
        phase = LABELS.get(stage, stage)
        if stage == "figures":
            phase = "figure %d / %d" % (self._figs, self._budget)
        self._tracker.update(done=step, total=self.total, phase=phase)

    # ── the stages, in order ────────────────────────────────────────────────
    def start(self) -> None:
        """Open the bar BEFORE the stores are read — the first poll lands
        within a second of the click and must find something running."""
        self._tracker.start(self.total, LABELS["records"],
                            kind="%s.%s" % (ROUTE, self.fmt))
        self._state().update({"stage": "records", "done": False, "error": None,
                              "format": self.fmt})

    def records_done(self) -> None:
        self._mark("figures", TICKS_RECORDS)

    def figure_done(self) -> None:
        """One figure has become bytes.  THE tick — see :func:`figure_done`."""
        self._figs += 1
        if self._figs > self._budget:
            # Overrun: lengthen the bar rather than let it sit at its end while
            # the server is plainly still drawing.  A chunk at a time, so a
            # machine with a few more figures than last time does not make the
            # ring jump back by one twentieth twenty times over.
            self._budget = self._figs + 4
        self._mark("figures", TICKS_RECORDS + self._figs)

    def tables_done(self) -> None:
        """The story is assembled: every figure that will be drawn was drawn.

        The unused figure budget goes back here, which is what lets the ring
        reach its end on a machine whose report has twelve pictures and not
        stop at two thirds looking like a build that died.
        """
        if self._figs and self._figs < self._budget:
            self._budget = self._figs
        self._remember()
        self._mark("tables", TICKS_RECORDS + self._budget + TICKS_TABLES)

    def writing(self) -> None:
        self._mark(self.fmt, TICKS_RECORDS + self._budget + TICKS_TABLES)

    def done(self) -> None:
        self._remember()
        self._state().update({"stage": "done", "done": True, "error": None})
        self._stage = "done"
        self._tracker.update(phase=LABELS["done"])
        self._tracker.finish()

    def failed(self, msg: str) -> None:
        """The build raised.  The SENTENCE is what the ring turns into.

        Stored rather than only logged: the download's own response carries the
        detail too, but a client that lost the response (a navigation, a closed
        tab, a proxy timeout) otherwise has a ring that simply stops.
        """
        text = str(msg or "report build failed").strip()
        self._state().update({"stage": "failed", "done": True, "error": text})
        self._stage = "failed"
        self._tracker.update(phase=LABELS["failed"])
        self._tracker.finish()

    def _remember(self) -> None:
        if self.key:
            _remember_figures(self.key, self._figs)


# ── the build in flight ─────────────────────────────────────────────────────

_CURRENT: "ContextVar[Optional[ReportProgress]]" = ContextVar(
    "motor_ai_sim_report_progress", default=None)


def current() -> Optional[ReportProgress]:
    """The build this call is inside, or ``None`` — a report rendered from a
    test or a script has no bar and must cost nothing."""
    return _CURRENT.get()


@contextmanager
def build(run_id: str, fmt: str, key: str = "",
          budget: Optional[int] = None) -> Iterator[Optional[ReportProgress]]:
    """Publish this build's stages under ``run_id`` for as long as it runs.

    Leaves the entry FINALISED whatever happens: ``done`` on the way out, the
    sentence on the way out through an exception.  An entry left ``running``
    is a ring that spins for half an hour on a build that ended in a second.
    """
    rid = str(run_id or "").strip()
    if not rid:
        yield None
        return
    rp = ReportProgress(rid, fmt, key=key, budget=budget)
    rp.start()
    token = _CURRENT.set(rp)
    try:
        yield rp
    except BaseException as exc:                            # noqa: BLE001
        detail = getattr(exc, "detail", None)
        rp.failed(str(detail) if detail else (str(exc) or type(exc).__name__))
        raise
    else:
        rp.done()
    finally:
        _CURRENT.reset(token)


def figure_done() -> None:
    """One figure rendered.  Called from THE one place a matplotlib figure
    becomes report bytes (``report._png_bytes``), never from the twenty
    functions that draw them."""
    rp = _CURRENT.get()
    if rp is not None:
        try:
            rp.figure_done()
        except Exception:                                   # noqa: BLE001
            pass            # a bar must never break a build


def stage(name: str) -> None:
    """Move to a named stage — ``records`` done, ``tables``, ``writing``."""
    rp = _CURRENT.get()
    if rp is None:
        return
    try:
        if name == "records":
            rp.records_done()
        elif name == "tables":
            rp.tables_done()
        elif name == "writing":
            rp.writing()
    except Exception:                                       # noqa: BLE001
        pass


def snapshot(run_id: str) -> Dict[str, Any]:
    """What ``GET /api/family/report/progress?run_id=…`` answers.

    The solve strips' invariant shape (so the same client code reads it) plus
    ``stage`` / ``done`` / ``error`` / ``format``.  A run id the registry never
    saw is not an error: a poll that arrives before the build's first line is
    the NORMAL first poll, and the ring stays indeterminate on it.
    """
    rid = str(run_id or "").strip()
    out = dict(_PROG.poll(ROUTE, rid))
    out["run_id"] = rid
    e = _PROG.registry().entry(rid) if rid else None
    st = (e.extra.get(ROUTE) or {}) if e is not None else {}
    out["stage"] = str(st.get("stage") or ("unknown" if e is None else ""))
    out["done"] = bool(st.get("done"))
    out["error"] = st.get("error") or None
    out["format"] = st.get("format") or ""
    return out
