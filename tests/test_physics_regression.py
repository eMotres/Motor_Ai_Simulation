"""Physics regression: pin the numbers the solver produces today.

This suite does NOT judge whether a number is physically right — it judges
whether a code change moved it. That is the gate every refactoring step needs:
the solver is one 3400-line function full of closures over mesh, slip pairing
and saturation state, and the only reason the ~35 % torque inflation on the P1
path survived so long is that nothing was watching the numbers.

Design notes, in case a case starts failing for the wrong reason:

* **Config-independent geometry.** Every case passes its geometry through
  ``geo_override`` and materials are forced on the cached config in-process, so
  editing ``config/motor_config.yaml`` (which the user does constantly) cannot
  turn a red test into a code problem.
* **Coarse on purpose.** 12 steps/period on a 1.4 mm mesh. These are not
  publication numbers; they only have to be *reproducible*. Keeping the suite
  near five minutes is what makes it get run.
* **Both element orders.** P2 is the default basis, but P1 is still the only
  path that implements irreversible demag, coupled eddy and voltage drive, so
  it has to stay pinned too.

Regenerate the baseline deliberately, never casually — a diff here is the whole
point of the file::

    UPDATE_PHYSICS_BASELINE=1 python -m pytest tests/test_physics_regression.py

Then READ the printed diff and justify every line of it in the commit message.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.config import get_config
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

BASELINE = Path(__file__).with_name("physics_baseline.json")
UPDATE = os.environ.get("UPDATE_PHYSICS_BASELINE") == "1"

# Relative tolerance. 0.5 % is wide enough to absorb BLAS/threading jitter in
# the sparse solve and narrow enough that any real physics change trips it.
RTOL = 5e-3
# Quantities that legitimately sit near zero (cogging ripple, shaft loss) need an
# absolute floor or the relative test divides noise by noise.
ATOL = {"T_ripple_pct": 0.05, "P_shaft_W": 0.05, "P_solid_W": 0.05,
        "P_core_W": 0.05, "demag_br_min": 0.01, "demag_br_mean": 0.01}

# A 30 mm 12s14p spoke machine, pinned field by field so the suite does not
# inherit whatever is in the working config.
GEO_30MM: Dict[str, float] = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}

COMMON = dict(
    n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4, min_size_mm=0.35,
    gap_layers=1.0, n_sectors=2, structured_gap=True, iron_template=True,
    geo_mesh=True, coil_temp_c=120.0, rotor_eddy=False,
)

CASES = {
    # The default basis: energy-consistent mean torque, mesh-convergent ripple.
    "p2_load": dict(COMMON, element_order=2, demag=False,
                    I_phase_rms=60.0, gamma_deg=0.0),
    # P1 under load. Pins the hybrid torque that P1 was missing — if someone
    # removes it, this case jumps ~35 % and the suite says so immediately.
    "p1_load": dict(COMMON, element_order=1, demag=False,
                    I_phase_rms=60.0, gamma_deg=0.0),
    # No-load cogging: the one number the flux-linkage torque cannot see, so it
    # comes from the raw Maxwell series and guards that path separately.
    "p1_noload": dict(COMMON, element_order=1, demag=False,
                      I_phase_rms=0.0, gamma_deg=0.0),
    # Irreversible demagnetisation, P1 only (P2 raises NotImplementedError).
    # Pins the load-line construction AND the settling pass.
    "p1_demag": dict(COMMON, element_order=1, demag=True,
                     I_phase_rms=60.0, gamma_deg=0.0),
}

MAGNET = "F45SH_120C"


def _scalar(v: Any) -> float:
    """T_em_Nm comes back as a per-frame series; the pinned value is its mean."""
    if isinstance(v, (list, tuple)):
        a = np.asarray(v, float)
        return float(a.mean()) if a.size else 0.0
    return float(v)


def _metrics(d: Dict[str, Any]) -> Dict[str, float]:
    out = {
        "T_avg_Nm": _scalar(d.get("T_avg_Nm", d.get("T_em_Nm", 0.0))),
        "T_ripple_pct": float(d.get("T_ripple_pct", 0.0)),
        "P_cu_W": float(np.mean(d.get("P_cu_W", 0.0)) if isinstance(d.get("P_cu_W"), list)
                        else d.get("P_cu_W", 0.0)),
        "P_fe_W": float(np.mean(d.get("P_fe_W", 0.0)) if isinstance(d.get("P_fe_W"), list)
                        else d.get("P_fe_W", 0.0)),
        "V_peak": float(d.get("V_peak", 0.0)),
    }
    # The raw Maxwell mean is pinned separately: it is the diagnostic that makes
    # a regression in the hybrid distinguishable from one in the field solve.
    if d.get("T_avg_maxwell_Nm") is not None:
        out["T_avg_maxwell_Nm"] = float(d["T_avg_maxwell_Nm"])
    f = d.get("demag_field")
    if f:
        br = np.asarray(f["demag_coef_per_tri"], float)
        dom = np.asarray(f["domain_per_tri"], int)
        m = np.isin(dom, f["mag_domains"])
        if m.any():
            out["demag_br_min"] = float(br[m].min())
            out["demag_br_mean"] = float(br[m].mean())
    return out


def _run(case: str) -> Dict[str, float]:
    cfg = get_config(reload=True)
    cfg["materials"]["magnet"] = MAGNET      # in-process only, never the YAML
    kw = dict(CASES[case])
    d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), **kw)
    return _metrics(d)


@pytest.fixture(scope="module")
def baseline() -> Dict[str, Dict[str, float]]:
    if BASELINE.exists():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    if not UPDATE:
        pytest.skip("no baseline — run with UPDATE_PHYSICS_BASELINE=1 to create it")
    return {}


@pytest.mark.slow
@pytest.mark.parametrize("case", sorted(CASES))
def test_case_matches_baseline(case: str, baseline: Dict[str, Dict[str, float]]):
    got = _run(case)
    if UPDATE:
        pytest.skip("baseline update run — see regenerate_baseline")
    assert case in baseline, (
        f"case {case!r} has no baseline; regenerate with "
        f"UPDATE_PHYSICS_BASELINE=1")
    want = baseline[case]
    bad = []
    for k, wv in want.items():
        gv = got.get(k)
        if gv is None:
            bad.append(f"  {k}: MISSING (was {wv:.6g})")
            continue
        tol = max(abs(wv) * RTOL, ATOL.get(k, 0.0))
        if abs(gv - wv) > tol:
            drift = (gv - wv) / wv * 100 if wv else float("inf")
            bad.append(f"  {k}: {wv:.6g} -> {gv:.6g}  ({drift:+.2f} %)")
    for k in got:
        if k not in want:
            bad.append(f"  {k}: NEW ({got[k]:.6g})")
    assert not bad, (
        f"physics moved in case {case!r}:\n" + "\n".join(bad) +
        "\n\nIf the change is intended, regenerate with "
        "UPDATE_PHYSICS_BASELINE=1 and justify every line above in the commit.")


def regenerate_baseline() -> None:
    """Recompute every case and write the baseline file, printing the diff."""
    old = (json.loads(BASELINE.read_text(encoding="utf-8"))
           if BASELINE.exists() else {})
    new: Dict[str, Dict[str, float]] = {}
    for case in sorted(CASES):
        print(f"running {case} ...", flush=True)
        new[case] = _run(case)
        for k, v in sorted(new[case].items()):
            ov = old.get(case, {}).get(k)
            if ov is None:
                print(f"    {k:22s} {v:12.6g}   (new)")
            elif abs(v - ov) > max(abs(ov) * RTOL, ATOL.get(k, 0.0)):
                print(f"    {k:22s} {ov:12.6g} -> {v:12.6g}  "
                      f"({(v - ov) / ov * 100 if ov else float('inf'):+.2f} %)")
            else:
                print(f"    {k:22s} {v:12.6g}")
    BASELINE.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"\nwrote {BASELINE}")


if __name__ == "__main__":
    regenerate_baseline()
