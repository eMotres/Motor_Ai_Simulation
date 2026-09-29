"""The mean flux density in the AIR GAP, and where it comes from.

User 2026-09-10: *"для электромагнитного анализа надо ещё рассчитывать среднее
поле в зазоре и писать это число в таблицу"*.  The mean |B| over the clearance
is the first number a machine is sized on and the summary never carried it.

What is pinned here is not a value — the machine under the test can change —
but the DEFINITION, because every way of getting this number wrong is silent:

  * it is the CLEARANCE, not the air-gap DOMAIN.  gmsh paints the rotor half's
    leftover area with ``DOM_AIRGAP`` as a default, so the tag alone reaches
    down to the shaft and its mean would be mostly bore air — a number that
    looks plausible and is half the real one;
  * it is AREA-weighted.  The gap is meshed finer at the motion band than at
    the slot openings, so an unweighted element mean is a mean of the mesh;
  * it is the PERIOD mean of the per-frame means, the same path ``P_core_W``
    takes, not the first frame's;
  * and it is absent, not zero, on a result solved before the feature — a
    stale card printing 0.000 T would be read as a broken magnet.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CONNECTION, GEO_30MM, OVERRIDE, RPM)

#: Coarse in time and space on purpose: this is a definition test, not a
#: physics pin.  Four frames give a period mean with something to average.
RUN = dict(n_steps_per_period=4, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=False, rotor_eddy=False,
           I_phase_rms=60.0, gamma_deg=0.0, rpm=RPM, connection=CONNECTION,
           # the LAST frame's element field, for the independent recomputation
           # in test (d) — the snapshot and `B_gap_mean_T[-1]` are one frame
           return_field=True)


@pytest.fixture(scope="module")
def solved() -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), **RUN)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def summary(solved) -> Dict[str, Any]:
    from motor_ai_sim.routes.simulation import _build_transient_summary
    set_request_materials(OVERRIDE)
    try:
        return _build_transient_summary(
            solved, I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)


def test_a_there_is_one_value_per_frame(solved):
    ser = solved.get("B_gap_mean_T")
    assert isinstance(ser, list) and ser, "no air-gap series in the result"
    assert len(ser) == int(RUN["n_steps_per_period"] * RUN["n_periods"]), len(ser)
    for v in ser:
        assert math.isfinite(v) and v > 0.0, ser


def test_b_the_summary_carries_the_period_mean(solved, summary):
    """The same averaging `P_core_W` gets, and no other."""
    ser = np.asarray(solved["B_gap_mean_T"], float)
    assert summary["B_gap_mean_T"] == pytest.approx(float(ser.mean()), abs=5e-5)


def test_c_it_is_a_gap_field_and_not_bore_air(summary):
    """A real machine's gap runs a few tenths of a tesla to something over one.

    The failure this catches is the DOMAIN-tag mistake: including the rotor
    half's default-painted air would drag the mean down towards the bore, where
    |B| is a few millitesla, and the number would still look like a number.
    """
    b = summary["B_gap_mean_T"]
    assert 0.15 < b < 2.5, b


def test_d_it_is_area_weighted_over_the_clearance_annulus(solved):
    """Recompute it from the snapshot field and land on the same number.

    An independent path: the last frame's element field, its own areas, and a
    mask built from RADIUS alone — no domain tag at all — over the annulus
    between the outermost rotating metal and the stator bore.  If the solver's
    element set were the tagged domain rather than the clearance, this would
    not match.
    """
    fld = solved.get("field")
    if not fld or fld.get("Bx") is None:
        pytest.skip("this run returned no field snapshot")
    P = np.asarray(fld["P_mm"], float) * 1e-3
    T = np.asarray(fld["T"], int)
    bx = np.asarray(fld["Bx"], float)
    by = np.asarray(fld["By"], float)
    tags = np.asarray(fld["tags"], int)

    from motor_ai_sim.simulation.sb_domains import (DOM_COIL_BASE,
                                                    DOM_MAG_BASE, DOM_MAG_N,
                                                    DOM_MAG_S, DOM_ROTOR,
                                                    DOM_SLEEVE, DOM_STATOR)
    # `P_mm` is (2, n_nodes) and `T` is (3, n_el) — the mesh's own layout.
    x = P[0][T]                                 # (3, n_el)
    y = P[1][T]
    rn = np.hypot(x, y)
    rc = rn.mean(axis=0)
    # Per-magnet tags are DOM_MAG_BASE + i — and STOP at DOM_COIL_BASE, which
    # is the stator's coils and sits at a bigger radius than the whole rotor.
    rot = np.isin(tags, (DOM_ROTOR, DOM_SLEEVE, DOM_MAG_N, DOM_MAG_S)) \
        | ((tags >= DOM_MAG_BASE) & (tags < DOM_COIL_BASE))
    sta = tags == DOM_STATOR
    if not rot.any() or not sta.any():
        pytest.skip("this snapshot names neither rotor nor stator")
    r_in = float(rn[:, rot].max())
    r_out = float(rn[:, sta].min())
    m = (rc > r_in) & (rc < r_out)
    assert m.any(), (r_in, r_out)

    ar = 0.5 * np.abs((x[1] - x[0]) * (y[2] - y[0])
                      - (x[2] - x[0]) * (y[1] - y[0]))

    def _mean_over(mask):
        b = np.hypot(bx[mask], by[mask])
        return float((b * ar[mask]).sum() / ar[mask].sum())

    got = float(solved["B_gap_mean_T"][-1])     # the snapshot IS the last frame

    # (i) the same set the solver builds — radius AND the gap-air tags — has to
    #     reproduce it to the last digit.  This is the arithmetic check.
    from motor_ai_sim.simulation.sb_domains import DOM_AIRGAP, DOM_BAND
    tagged = m & np.isin(tags, (DOM_AIRGAP, DOM_BAND))
    assert tagged.any()
    assert got == pytest.approx(_mean_over(tagged), rel=1e-3), got

    # (ii) …and dropping the tags entirely — every element whose centroid falls
    #      in the clearance, whatever it is called — must give the same answer
    #      to a few per cent.  If it did not, the tag filter would be choosing
    #      the number rather than just tidying the set.
    assert got == pytest.approx(_mean_over(m), rel=0.05), (got, _mean_over(m))


def test_e_a_result_without_the_series_reports_no_number_at_all(solved):
    """Absent, never zero — a stale card must not print 0.000 T."""
    from motor_ai_sim.routes.simulation import _build_transient_summary
    old = {k: v for k, v in solved.items() if k != "B_gap_mean_T"}
    set_request_materials(OVERRIDE)
    try:
        s = _build_transient_summary(
            old, I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)
    assert "B_gap_mean_T" not in s
