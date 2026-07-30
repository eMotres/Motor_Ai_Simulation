"""The winding CONNECTION label, and the one place it is parsed.

A three-phase winding of C coils per phase can be wired as any factor pair
``n_series x n_parallel = C``.  The label the UI, the presets and the catalog all
store is a string — ``"4S"`` (all series), ``"4P"`` (all parallel), ``"2S-2P"``
(series-parallel), plus the legacy ``"2P2S"`` spelling.

Only ``n_parallel`` reaches the physics, and it reaches it as a DIVIDER: the FEM
sees ``I_coil = I_phase / n_parallel``.  Getting it wrong is a factor-n_parallel
error in the coil MMF and therefore in the torque — measured at +95.7 % on
``ciano20_150_35`` (its stored ``2S-2P`` unapplied), and -1.1 % against its
stored torque once applied (docs/SOLVER_TRIALS_2026-07-30.md F3).

The parser lived in ``api.py`` only, so the solver had no way to honour a
connection that was not already written into the shared config.  It lives here
now: one definition, importable without dragging FastAPI in.
"""
from __future__ import annotations

import re
from typing import Tuple

__all__ = ["parse_connection", "n_parallel_from_connection", "connection_label"]

_PATTERNS = (
    (re.compile(r"^(\d+)S-(\d+)P$"), lambda m: (int(m.group(2)), int(m.group(1)))),
    (re.compile(r"^(\d+)P(\d+)S$"), lambda m: (int(m.group(1)), int(m.group(2)))),
    (re.compile(r"^(\d+)S$"),       lambda m: (1, int(m.group(1)))),
    (re.compile(r"^(\d+)P$"),       lambda m: (int(m.group(1)), 1)),
)


def parse_connection(conn: str) -> Tuple[int, int]:
    """``"2S-2P"`` -> ``(n_parallel, n_series)``.

    Raises ``ValueError`` on anything it cannot read.  It RAISES rather than
    falling back to 1 on purpose: a connection nobody can parse silently driving
    the whole phase current through one path is exactly the failure this
    function exists to make impossible.
    """
    c = (conn or "").strip()
    for pat, take in _PATTERNS:
        m = pat.match(c)
        if m:
            n_par, n_ser = take(m)
            if n_par < 1 or n_ser < 1:
                raise ValueError(f"connection {conn!r}: counts must be >= 1")
            return n_par, n_ser
    raise ValueError(
        f"unknown winding connection {conn!r}; expected forms like "
        f"'4S' (all series), '4P' (all parallel), '2S-2P' (series-parallel)")


def n_parallel_from_connection(conn: str) -> int:
    """Parallel paths in ``conn``.  Raises ``ValueError`` on an unreadable label."""
    return parse_connection(conn)[0]


def connection_label(n_series: int, n_parallel: int) -> str:
    """Canonical label: all-series ``'{C}S'``, all-parallel ``'{C}P'``, else
    ``'{nS}S-{nP}P'``."""
    if n_parallel <= 1:
        return f"{int(n_series)}S"
    if n_series <= 1:
        return f"{int(n_parallel)}P"
    return f"{int(n_series)}S-{int(n_parallel)}P"
