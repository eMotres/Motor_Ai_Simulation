"""Six-phase winding = two three-phase SETS on one stator (owner 2026-09-28).

Owner decision: *«3 phases | 6 phases (two 3-phase sets)»* is a WINDING option.
We have no dual-three-phase machine yet; it is made by taking the PARALLEL coil
groups of every phase and splitting them into two sets, each connected as its
own star or delta (two stars or two deltas — the duty's Y/Delta applies to both
sets) and each fed by its own inverter.

THE SPLIT (owner 2026-09-28: 0 deg only, no spatial shift)
    The phase's parallel paths are divided between the sets.  Every path is a
    rotated copy of every other (validated below), so the two sets occupy the
    SAME phase belts: the sets are IN PHASE (0 deg electrical).  A set holds
    half the paths, so it carries half the phase current at the full phase
    voltage.

The unit that cannot be split is a COIL: two adjacent slots on a single-layer
winding (the builder's ``(ph, +s), (ph, -s)`` pair — ``inverter.topology``
reads it the same way), one slot on a double-layer winding (the FEM's own
per-slot phasor).

NEUTRALS are isolated between the sets by default (each set its own star
point; with delta there is no neutral).  ``common`` is accepted and reported;
it matters for the zero-sequence ripple path only (``inverter.ripple``).

Nothing here solves a field — it is the bookkeeping the EM solve
(``fem_solver_2d``, ``six_phase=``), the Controller topology and the web share,
so the three can never disagree about which coil is in which set.
"""
from __future__ import annotations

import cmath
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "SixPhaseError", "SPLITS", "NEUTRALS", "resolve_six_phase",
    "coil_units", "per_set_values", "vsd_basis",
]

SPLITS = ("parallel_paths",)
NEUTRALS = ("isolated", "common")


class SixPhaseError(ValueError):
    """A six-phase request that does not describe a winding anyone can wire."""

    def __init__(self, msg: str, fields: Sequence[str] = ()):
        super().__init__(msg)
        self.fields = list(fields)


# ---------------------------------------------------------------------------
# coil units and their phasors
# ---------------------------------------------------------------------------

def _layout(num_slots: int, num_poles: int, single_layer: bool,
            layout_str: Optional[str]) -> List[Tuple[str, int]]:
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    return [(str(p), int(s)) for p, s in build_winding_layout(
        int(num_slots), int(num_poles) // 2, single_layer=bool(single_layer),
        layout_str=layout_str or None)]


def coil_units(num_slots: int, num_poles: int, *, single_layer: bool = True,
               layout_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """The indivisible coil units: ``{phase, slots, phasor_deg}``.

    ``phasor_deg`` is the unit's EMF phasor in the builder's slot-angle frame
    (slot k at k * p * 360 / S, a negative side adds 180 deg) — the same frame
    ``inverter.topology.coils_from_winding`` reports.
    """
    S, P = int(num_slots), int(num_poles)
    lay = _layout(S, P, single_layer, layout_str)
    alpha = (P // 2) * 360.0 / S
    groups: List[List[int]]
    if single_layer:
        if S % 2:
            raise SixPhaseError("a single-layer winding needs an even slot count",
                                ["num_slots"])
        groups = [[2 * j, 2 * j + 1] for j in range(S // 2)]
    else:
        groups = [[k] for k in range(S)]
    units = []
    for g in groups:
        phs = {lay[k][0] for k in g}
        if len(phs) != 1:
            raise SixPhaseError(
                f"slots {', '.join(str(k + 1) for k in g)} form one coil but carry "
                f"phases {', '.join(sorted(phs))} — this layout cannot be split "
                "into sets", ["winding_layout"])
        z = sum(lay[k][1] * cmath.exp(1j * math.radians(k * alpha)) for k in g)
        units.append({"phase": lay[g[0]][0], "slots": list(g),
                      "phasor_deg": math.degrees(cmath.phase(z)) % 360.0})
    return units


def _path_of_units(units: Sequence[Dict[str, Any]], S: int, n_par: int,
                   lay: Sequence[Tuple[str, int]]) -> List[int]:
    """Path index (0-based) of every unit: the stator cut into n_par equal
    sectors, each path the coils of one sector.  Refused unless every sector
    is a rotated copy of the first (so every path has the same EMF)."""
    if S % n_par:
        raise SixPhaseError(
            f"{n_par} parallel paths do not divide the {S} slots into equal "
            "coil groups — the sets would not be identical", ["winding_n_parallel"])
    step = S // n_par
    for g in range(1, n_par):
        flips = set()
        for k in range(step):
            a, b = lay[k], lay[k + g * step]
            if a[0] != b[0]:
                raise SixPhaseError(
                    f"parallel path {g + 1} is not a copy of path 1 (slot "
                    f"{k + g * step + 1} is phase {b[0]}, slot {k + 1} is {a[0]}): "
                    f"the {n_par} coil groups do not split evenly for this "
                    "slot/pole combination", ["winding_n_parallel"])
            flips.add(a[1] * b[1])
        if len(flips) != 1:
            raise SixPhaseError(
                f"parallel path {g + 1} mixes polarities against path 1 — the "
                f"{n_par} coil groups do not split evenly", ["winding_n_parallel"])
    out = []
    for u in units:
        ps = {k // step for k in u["slots"]}
        if len(ps) != 1:
            raise SixPhaseError(
                f"a coil (slots {'/'.join(str(k + 1) for k in u['slots'])}) "
                f"straddles two of the {n_par} path sectors — the paths cannot "
                "be separated without cutting a coil", ["winding_n_parallel"])
        out.append(ps.pop())
    return out


def _parse_paths(v: Any, n_par: int) -> Optional[List[int]]:
    if v in (None, "", []):
        return None
    if isinstance(v, str):
        parts = [p for p in v.replace(";", ",").replace(" ", ",").split(",") if p]
    else:
        parts = list(v)
    try:
        out = sorted({int(p) for p in parts})
    except (TypeError, ValueError):
        raise SixPhaseError(f"set1_paths must be path numbers 1..{n_par}; got {v!r}",
                            ["set1_paths"])
    return out


def resolve_six_phase(winding: Dict[str, Any], *, num_slots: int, num_poles: int,
                      single_layer: bool = True,
                      layout_str: Optional[str] = None,
                      star_delta: str = "star") -> Optional[Dict[str, Any]]:
    """The validated six-phase spec of a winding block, or ``None`` (3 phases).

    Winding keys: ``phases`` (3 | 6), ``n_parallel`` (CONNECTION paths, not
    strands in hand), ``set1_paths`` ("1,2" — default the first half),
    ``set_neutrals`` (isolated | common).  The sets are in phase (0 deg).
    Every refusal is a :class:`SixPhaseError` naming the field.
    """
    w = dict(winding or {})
    try:
        phases = int(w.get("phases") or 3)
    except (TypeError, ValueError):
        raise SixPhaseError(f"phases must be 3 or 6; got {w.get('phases')!r}",
                            ["phases"])
    if phases == 3:
        return None
    if phases != 6:
        raise SixPhaseError(f"phases must be 3 or 6; got {phases}", ["phases"])
    S, P = int(num_slots), int(num_poles)
    n_par = int(w.get("n_parallel") or 1)
    if n_par < 2 or n_par % 2:
        raise SixPhaseError(
            f"6 phases split the parallel paths between two sets: this winding "
            f"has {n_par} parallel path(s) — an even number (2, 4, ...) is "
            "needed. Change the connection (e.g. 2S-2P) first.",
            ["phases", "connection"])
    neutrals = str(w.get("set_neutrals") or "isolated").strip().lower()
    if neutrals not in NEUTRALS:
        raise SixPhaseError("set_neutrals must be isolated or common",
                            ["set_neutrals"])
    lay = _layout(S, P, single_layer, layout_str)
    units = coil_units(S, P, single_layer=single_layer, layout_str=layout_str)
    per_phase = {ph: sum(1 for u in units if u["phase"] == ph) for ph in "ABC"}
    if len(set(per_phase.values())) != 1:
        raise SixPhaseError(f"unbalanced winding {per_phase}", ["winding_layout"])
    cpp = per_phase["A"]
    if cpp % n_par:
        raise SixPhaseError(
            f"{n_par} parallel paths do not divide the {cpp} coils per phase",
            ["connection"])
    paths = _path_of_units(units, S, n_par, lay)
    sd = "delta" if str(star_delta or "").lower().startswith("d") else "star"
    set1 = _parse_paths(w.get("set1_paths"), n_par) or list(range(1, n_par // 2 + 1))
    bad = [p for p in set1 if p < 1 or p > n_par]
    if bad:
        raise SixPhaseError(f"set1_paths names path(s) {bad}; this winding has "
                            f"paths 1..{n_par}", ["set1_paths"])
    if len(set1) != n_par // 2:
        raise SixPhaseError(
            f"set 1 must hold exactly half of the {n_par} paths ({n_par // 2}); "
            f"got {len(set1)} — unequal sets are not two identical 3-phase "
            "windings", ["set1_paths"])
    set_of_unit = [1 if (paths[i] + 1) in set1 else 2 for i in range(len(units))]
    slot_set = [0] * S
    for u, s in zip(units, set_of_unit):
        for k in u["slots"]:
            slot_set[k] = s
    return {
        "phases": 6, "split": "parallel_paths", "star_delta": sd,
        "set_connection": sd, "neutrals": neutrals,
        "n_parallel": n_par, "paths_per_set": n_par // 2,
        "set1_paths": set1,
        "set2_paths": [p for p in range(1, n_par + 1) if p not in set1],
        "shift_deg": 0.0,
        "slot_set": slot_set,
        "unit_path": [p + 1 for p in paths],
        "unit_set": set_of_unit,
        "note": ("two sets in the SAME phase belts, in phase (0 deg): each set = "
                 f"{n_par // 2} of the {n_par} parallel paths, half the phase "
                 "current at the full phase voltage"),
    }


# ---------------------------------------------------------------------------
# currents between the sets, and the VSD basis
# ---------------------------------------------------------------------------

def vsd_basis(th_dq: float, ipark) -> Dict[str, Any]:
    """Orthonormal 6-vectors of the two in-phase sets: d, q (the air-gap
    plane at the Park angle th_dq — both sets driven alike) and the x-y plane
    (the orthogonal complement of d, q and each set's zero sequence: the sets
    driven AGAINST each other).  ``ipark`` is ``drive.inverse_park``."""
    import numpy as np

    def six(d, q):
        v = np.array(ipark(d, q, th_dq))
        return np.concatenate([v, v])
    ed, eq = six(1.0, 0.0), six(0.0, 1.0)
    ed, eq = ed / np.linalg.norm(ed), eq / np.linalg.norm(eq)
    z1 = np.array([1, 1, 1, 0, 0, 0], float) / math.sqrt(3)
    z2 = np.array([0, 0, 0, 1, 1, 1], float) / math.sqrt(3)
    u, _s, _vt = np.linalg.svd(np.column_stack([ed, eq, z1, z2]),
                               full_matrices=True)
    return {"d": ed, "q": eq, "xy": u[:, 4:6]}


def per_set_values(summary: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Per-set terminal values off a solved summary.

    The sets are identical in-phase halves of the paths — each carries half
    the phase current at the full phase voltage, exact by symmetry (the
    validation above proved every path a rotated copy of every other)."""
    def _n(k):
        v = summary.get(k)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    i_ph = _n("I_phase_rms_A") or _n("I_phase_rms_solved_A")
    v1 = _n("V1_phase_V")
    vrms = _n("V_phase_rms_V")
    out = []
    for s in (1, 2):
        out.append({
            "set": s,
            "paths": (spec.get("set1_paths") if s == 1 else spec.get("set2_paths")),
            "connection": spec.get("set_connection"),
            "I_phase_rms_A": None if i_ph is None else round(i_ph / 2.0, 3),
            "V1_phase_peak_V": None if v1 is None else round(v1, 2),
            "V_phase_rms_V": None if vrms is None else round(vrms, 2),
        })
    return {"sets": out,
            "basis": ("identical in-phase halves of the parallel paths: half "
                      "the phase current, the full phase voltage (exact by "
                      "symmetry)")}
