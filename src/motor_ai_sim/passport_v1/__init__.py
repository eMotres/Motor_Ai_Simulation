"""Motor passport v1 — pilot implementation (docs/PASSPORT_ALGORITHM.md v1.1).

Stage 1 of the Ø40 pilot (CIANO14 40 new / L12 and L20): plain 2-D, sine
current drive, no 3-D end corrections, no PWM, no controller losses.  The
record carries the stage-2 / stage-3 blocks as explicit "not computed in
stage 1" placeholders so they can be filled later without a schema change.

Modules
-------
snapshot  M0: freeze + hash every input before any run (P28, P29, G30).
jobs      solver-direct job builders and the worker that runs them through
          ``fem_solver_2d.em_transient_eval`` (the canonical entry point the
          Simulation route, the optimizer and the kernel all use).
psimap    the static psi-map in (i_d, i_q): MTPA bracketing, Clough-Tocher
          interpolation, torque/voltage from psi, the operating envelope.
losses    the loss trajectory: grid plan, per-group frequency-law
          interpolation, analytic mechanical losses.
card      derived card values and the passport record.

Conventions (spec, "Conventions" [P03]): gamma from the q-axis, gamma > 0 =
negative i_d; amplitude-invariant PEAK dq frame in the motoring equivalent
star: i_d = -sqrt(2) I sin(gamma), i_q = sqrt(2) I cos(gamma);
T = 3/2 p (psi_d i_q - psi_q i_d).
"""
from __future__ import annotations

#: Record schema version of the passport written by this package.
SCHEMA_VERSION = "passport-v1-pilot-1"

#: dq / terminal convention revision (spec P03 `convention_rev`).
CONVENTION_REV = "p03-1: gamma from q-axis, amplitude-invariant peak dq, motoring star"

#: The labels every stage-1 value carries.
STAGE1_LABELS = ("2-D", "no 3-D end correction", "sine current drive",
                 "no PWM", "no controller losses")

#: Owner defaults that are NOT owner decisions yet — stamped on the record.
PENDING_DEFAULTS = {
    "voltage_margin_m": {
        "value": 0.95,
        "status": "placeholder (spec P24) — not an owner decision",
    },
    "materials_library": {
        "value": "deployed library (/srv/motres/shared/materials_library.yaml)",
        "status": "PR #51 not merged — owner decision pending (B8)",
    },
    "settle_rule": {
        "value": "spec 3.0.1 tail-bound proposal",
        "status": "proposal — owner decision pending (B4/P06)",
    },
}

NOT_COMPUTED_STAGE1 = "not computed in stage 1"
