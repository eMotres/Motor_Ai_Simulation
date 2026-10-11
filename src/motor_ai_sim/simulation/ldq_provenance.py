"""What a stored Ld/Lq IS — the one rule every consumer reads it by.

``inc_ldq`` (``fem_solver_2d.frozen_permeability_ldq``) has written three kinds
of value under the same key names:

* ``differential`` — since 2026-10-11: unit d/q currents solved on the tangent
  reluctivity of the converged field, the small-signal inductance a current
  loop, a PWM ripple or a bench LCR meter sees.
* ``regularized`` — the same probe where the B-H table is not monotone at the
  operating field (dH/dB <= 0 somewhere): the Newton's clamped tangent was
  used instead, so the value is NOT dψ/di of the material law.  Withheld.
* ``secant`` — frozen-permeability secant ν (every run before 2026-10-11, and
  the ``*_fp_secant_mH`` keys since): over-reads the small-signal value
  1.7-2.4× on saturated machines (docs/BUG_LDLQ_REPORTED_2026-10-05.md).

Anything that USES an inductance as small-signal (controller ripple, current
loop) must call :func:`is_differential` and refuse the rest with
:data:`RERUN_NOTE`.  All of them are 2-D magnetostatic, per winding: no end-
winding inductance, no 3-D (k_3d) correction, no frequency-dependent eddy
effect — :data:`SCOPE_NOTE` says so where they are printed.

Pure module, no solver imports: the report, the datasheet and the controller
read it without loading the FEM stack.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

LDQ_METHOD_DIFFERENTIAL = "differential (tangent reluctivity)"
LDQ_METHOD_REGULARIZED = ("regularized tangent (B-H curve not monotone at the "
                          "operating field) — not a differential")
LDQ_METHOD_SECANT = "frozen-permeability secant"

#: Where a printed Ld/Lq comes from, in one clause.
SCOPE_NOTE = ("2-D magnetostatic, per winding — no end-winding inductance, "
              "no 3-D correction")

#: Why a stored inductance is not used as small-signal.
RERUN_NOTE = ("the stored Ld/Lq are frozen-permeability secant values, not the "
              "small-signal inductance — re-run the duty's electromagnetic "
              "solve to measure the differential ones")
REGULARIZED_NOTE = ("the B-H curve is not monotone at the operating field, so "
                    "the differential Ld/Lq could not be measured (regularized "
                    "tangent) — check the steel's B-H table")


def method_status(method: Optional[str]) -> str:
    """``differential`` | ``regularized`` | ``secant`` | ``none`` for an
    ``ldq_method`` / ``ldq0_method`` string.  Any non-empty label that is not
    one of the first two (every pre-2026-10-11 spelling: "frozen-permeability
    incremental …") is a secant value."""
    m = str(method or "").strip()
    if not m:
        return "none"
    if m.startswith("regularized"):
        return "regularized"
    if m.startswith("differential"):
        return "differential"
    return "secant"


def record_status(rec: Optional[Mapping[str, Any]],
                  key: str = "ldq_method") -> str:
    """:func:`method_status` of a summary / em record, with the regularized
    flag honoured even if a label were to say otherwise."""
    rec = rec or {}
    if rec.get("ldq_tangent_clamped") or rec.get("tangent_clamped"):
        return "regularized"
    return method_status(rec.get(key))


def is_differential(rec: Optional[Mapping[str, Any]],
                    key: str = "ldq_method") -> bool:
    return record_status(rec, key) == "differential"


def not_usable_note(rec: Optional[Mapping[str, Any]],
                    key: str = "ldq_method") -> str:
    """The sentence a consumer prints when it refuses a stored inductance."""
    return (REGULARIZED_NOTE if record_status(rec, key) == "regularized"
            else RERUN_NOTE)


def catalogue_ldq0_view(c: Optional[Mapping[str, Any]]) -> tuple:
    """``(status, note)`` of a STORED catalogue block's Ld0/Lq0 (the cold
    pass's ``constants_20c``), rendered from its own saved provenance — a block
    written before 2026-10-11 carries a secant value and must say so, the v2
    probe cache does not rewrite it."""
    c = c or {}
    st = str(c.get("ldq0_status") or method_status(c.get("ldq0_method")))
    if st == "none" and c.get("Ld0_mH") is not None:
        st = "secant"                       # unlabelled = the old probe
    if st == "differential":
        note = ("differential (small-signal) at zero current and 20 °C, "
                + SCOPE_NOTE)
    elif st == "regularized":
        note = REGULARIZED_NOTE
    else:
        note = ("frozen-permeability SECANT at zero current and 20 °C — "
                "over-reads the small-signal value; re-run the cold pass to "
                "measure the differential one")
    return st, note
