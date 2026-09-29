"""Is THIS solve the active duty's answer, or somebody else's errand?

WHY (a live defect, 2026-09-15)
===============================
The duty-cycle editor has an escape hatch: when the cycle route refuses with
``no_electromagnetic_run``, the Thermal tab offers to MAKE that run — at the
CALIBRATION duty's point (14.7 A, 1 000 rpm, coil 30 °C), through
``POST /api/coupled/run`` with ``max_iter = 1``.  The machine loaded in the
editor at that moment is a different duty entirely (the L13 ``peak 200С wire
120C NdFeB``, 45.96 A at 200 °C), and the backend had no way to tell the two
apart: every solve route files its answer under whatever duty
``duty_fields.active_context()`` names.  So a calibration run overwrote the peak
duty's ``em`` and ``thermal`` field sidecars with 14.7 A / 30 °C maps, and
``GET /api/thermal/last`` came back 59 °C / 47.6 W where the peak's own answer
was 409 °C / 680 W.

The run itself is legitimate and its RESULT is needed — the duty-cycle route
looks the loss map up in the field-snapshot store by an exact physics key, and
that store is the entire point of making the run.  What is illegitimate is the
BOOKKEEPING: filing the run as "what this machine was last shown to be".

WHAT THIS MODULE IS
-------------------
One ContextVar and two guards' worth of vocabulary.  With recording suppressed:

  * ``duty_fields.save_active`` stores nothing (no per-duty field sidecar);
  * ``duty_results.record`` / ``record_alt_carrier`` write nothing (no per-duty
    compact row — that is every ``note_*`` seam at once);
  * ``routes.thermal._remember_last``, ``routes.mechanical._remember_last`` and
    ``routes.coupled._remember_last`` return immediately (no ``_LAST[...]``, no
    ``config/.last_thermal.pkl`` / ``.last_mech.pkl`` / ``.last_coupled.json``).

and NOTHING else changes: the solve runs, the transient run store and the field
snapshot store are written exactly as always, and the answer is returned.

WHY NOT ``_BACKGROUND_RUN``
---------------------------
``routes.simulation._BACKGROUND_RUN`` already means "not the user's run", but it
means MORE than that: it also serves the transient memo cache and, decisively,
it clears ``_is_live_machine`` — which suppresses the field snapshot itself.
A calibration run with no field snapshot is a six-minute run that answers
nothing.  So this is a separate, narrower flag: *solve and snapshot as usual,
just do not file it under anybody's name*.

A ContextVar and not a module global for the usual reason: the solve routes run
in FastAPI's threadpool, several requests at a time, and a global would leak one
request's errand into another's answer.  The suppression is set around one
request in one thread and every nested call — the EM run, the thermal solve, the
mechanical step — sees it because they are all that same call stack.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

__all__ = ["recording", "suppressed", "suppress", "restore", "no_record"]

# True = this solve IS the active duty's answer and may be filed under it.
# Default True: every path that does not say otherwise keeps today's behaviour,
# bit for bit.
_RECORD: "ContextVar[bool]" = ContextVar("motor_ai_sim_record_run", default=True)


def recording() -> bool:
    """May this solve be filed under the active duty?"""
    return bool(_RECORD.get())


def suppressed() -> bool:
    """The negation, spelled the way the guards read."""
    return not _RECORD.get()


def suppress() -> Token:
    """Stop filing from here until :func:`restore` is given the token back."""
    return _RECORD.set(False)


def restore(token: Optional[Token]) -> None:
    """Undo one :func:`suppress`.  ``None`` is a no-op, so callers that only
    sometimes suppressed do not need a branch."""
    if token is not None:
        try:
            _RECORD.reset(token)
        except ValueError:      # a token from another context — nothing to undo
            _RECORD.set(True)


@contextmanager
def no_record() -> Iterator[None]:
    """``with no_record():`` — solve, snapshot, but file nothing."""
    tok = suppress()
    try:
        yield
    finally:
        restore(tok)
