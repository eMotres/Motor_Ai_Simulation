"""The "J — Source current density" view must never read zero on an eddy run.

Owner report, production, 2026-09-20 20:40 CEST: the field view showed
``max 0.0 · min 0.0``, every slot painted the same colour, on a run whose
server log said ``eddy=True`` — "очень странно выглядит график токов, тут
должны быть и + −".

Root cause: ``fem_transient_sliding_band``'s field-snapshot builder wrote
EITHER ``Jeddy`` (the coupled solve's nodal eddy density, when ``eddy`` was
on) OR ``Jtri_src`` (the applied per-element source density, only in the
``else`` branch) — never both. ``routes/simulation.py::_fem_field2d_impl``
serves the "J" (source) view from ``Jtri_src`` and the "J⟳" (eddy) view from
``Jeddy``; a request for "J" against a snapshot from an eddy run therefore
read a key that was never written and fell back to an all-zero array.

Two layers, two tests:

  * (a) the SOLVER now writes ``Jtri_src`` on every snapshot, eddy run or
    not, in addition to ``Jeddy`` when the coupled solve ran — a real (small)
    coupled-eddy transient, checked end to end;
  * (b) the ROUTE picks the quantity the VIEW asked for (the ``eddy`` request
    flag), not whatever the snapshot happens to carry, and degrades to an
    explicit "no data yet" (NaN + a header note) rather than painting zeros
    when an old snapshot predating this fix is all a probe has to serve.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import CONNECTION, GEO_30MM, OVERRIDE, RPM

# Coarse in time and space on purpose (same convention as
# tests/test_airgap_mean_b.py's RUN, not @pytest.mark.slow there either):
# this is a snapshot-wiring test, not a physics pin, and the coupled eddy
# solve only needs ONE frame with current in the coils to exercise both
# branches.
RUN = dict(n_steps_per_period=2, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=True, rotor_eddy=False,
           I_phase_rms=60.0, gamma_deg=0.0, rpm=RPM, connection=CONNECTION,
           return_field=True, field_first=False)


@pytest.fixture(scope="module")
def eddy_field() -> Dict[str, Any]:
    """One small coupled-eddy transient, its LAST frame's field snapshot."""
    set_request_materials(OVERRIDE)
    try:
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), **RUN)
    finally:
        set_request_materials(None)
    fld = d.get("field")
    assert fld is not None, "return_field=True produced no snapshot at all"
    return fld


class TestTheSolverSnapshotCarriesBoth:
    """(a) end-to-end: a real coupled-eddy run's snapshot has BOTH keys."""

    def test_jtri_src_is_present_and_signed(self, eddy_field):
        js = eddy_field.get("Jtri_src")
        assert js is not None, (
            "Jtri_src missing from an eddy run's snapshot — the applied "
            "source density is only written in the 'else' (non-eddy) branch")
        js = np.asarray(js, float)
        assert js.size > 0
        # Windings carry current at 60 A rms and gamma=0: a symmetric 3-phase
        # set never has all three legs the same sign, so the applied source
        # density must show both + and - (the owner's "должны быть и + −").
        assert (js > 0).any(), "no positive source-current elements at all"
        assert (js < 0).any(), "no negative source-current elements at all"

    def test_jeddy_is_also_present(self, eddy_field):
        je = eddy_field.get("Jeddy")
        assert je is not None
        assert np.asarray(je, float).size > 0

    def test_jtri_src_is_not_a_stand_in_zero_array(self, eddy_field):
        """The bug's own symptom: max 0.0 / min 0.0, every slot identical."""
        js = np.asarray(eddy_field["Jtri_src"], float)
        assert float(np.abs(js).max()) > 0.0


def _synthetic_snapshot(*, with_jtri_src: bool, with_jeddy: bool):
    """A 6-triangle strip, the same shape convention as test_losses.py's.

    P has 14 nodes (0..13); triangle i touches nodes i, i+1, i+2, so
    Jeddy[T].mean(axis=0) is computable by hand for the assertions below.
    """
    n = 6
    P = np.array([[float(i // 2) for i in range(2 * (n + 1))],
                  [float(i % 2) for i in range(2 * (n + 1))]])
    T = np.array([[i, i + 1, i + 2] for i in range(n)]).T
    tags = np.zeros(n, int)
    fld: Dict[str, Any] = {
        "P_mm": P, "T": T,
        "A": np.zeros(P.shape[1]),
        "Bx": np.full(n, 0.5), "By": np.zeros(n),
        "tags": tags,
    }
    if with_jtri_src:
        # Alternating sign, distinct magnitudes from the Jeddy pattern below
        # so a test that reads the wrong key cannot pass by accident.
        fld["Jtri_src"] = np.array(
            [1e6, -1e6, 2e6, -2e6, 3e6, -3e6], float)
    if with_jeddy:
        # Nodal, alternating sign too — the route averages this onto
        # elements via T, so it is NOT the same numbers as Jtri_src above.
        fld["Jeddy"] = np.array(
            [((-1) ** j) * (j + 1) * 1e5 for j in range(P.shape[1])], float)
    return fld


def _serve(monkeypatch, fld: Dict[str, Any], *, eddy: bool):
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim import cadquery_geometry as cq

    snap = {"field": fld, "scalars": {},
            "meta": {"eddy": True, "n_steps_per_period": 24, "n_periods": 1.0,
                     "computed_at": "now", "solve_time_s": 1.0,
                     "key_fields": {"I_phase_rms": 90.0, "gamma_deg": 0.0}}}

    class _AlwaysHit(dict):
        def get(self, *a, **k):
            return snap
    monkeypatch.setattr(sim, "_transient_field_snap", _AlwaysHit())
    monkeypatch.setattr(sim, "_fem_field_cache", {})
    monkeypatch.setattr(cq.CadQueryMotor, "get_2d_polygons",
                        lambda self, **kw: {"magnets": []})
    monkeypatch.setattr(fs, "_simplify_polys", lambda polys, **kw: polys)
    return sim.get_fem_field2d(n_steps_per_period=24, n_periods=1.0,
                               eddy=eddy, rotor_eddy=True,
                               I_phase_rms=90.0, snapshot_only=True)


class TestTheRouteServesTheViewNotTheRunFlag:
    """(b) the SAME snapshot must answer the "J" and "J⟳" requests differently."""

    def test_the_source_view_reads_jtri_src(self, monkeypatch):
        fld = _synthetic_snapshot(with_jtri_src=True, with_jeddy=True)
        out = _serve(monkeypatch, fld, eddy=False)
        assert out["ok"] and out.get("from_transient")
        j = out["J_z_per_tri"]
        assert j == pytest.approx(fld["Jtri_src"].tolist())
        # Sign kept — this is the owner's literal complaint ("должны быть и + −").
        assert any(v > 0 for v in j) and any(v < 0 for v in j)
        assert not out.get("j_view_stale")

    def test_the_eddy_view_reads_jeddy_not_jtri_src(self, monkeypatch):
        fld = _synthetic_snapshot(with_jtri_src=True, with_jeddy=True)
        out = _serve(monkeypatch, fld, eddy=True)
        assert out["ok"] and out.get("from_transient")
        j = out["J_z_per_tri"]
        expected = np.asarray(fld["Jeddy"])[fld["T"]].mean(axis=0)
        assert j == pytest.approx(expected.tolist())
        # Different key, different numbers — proves it did not just re-serve
        # the source density under the eddy request.
        assert j != pytest.approx(fld["Jtri_src"].tolist())

    def test_a_stale_pre_fix_snapshot_does_not_paint_zeros(self, monkeypatch):
        """An eddy-only snapshot from before this fix: Jeddy present, no
        Jtri_src. The "J" (source) view must say "no data", never 0.0
        everywhere (that reads as "no current", the bug's own symptom)."""
        fld = _synthetic_snapshot(with_jtri_src=False, with_jeddy=True)
        out = _serve(monkeypatch, fld, eddy=False)
        assert out["ok"] and out.get("from_transient")
        j = out["J_z_per_tri"]
        assert all(v != v for v in j), "expected NaN, not a silent zero fill"  # v != v <=> NaN
        assert out.get("j_view_stale") is True
        assert "predates" in str(out.get("source_label", ""))

    def test_a_fresh_snapshot_is_never_flagged_stale(self, monkeypatch):
        fld = _synthetic_snapshot(with_jtri_src=True, with_jeddy=True)
        out = _serve(monkeypatch, fld, eddy=False)
        assert not out.get("j_view_stale")
        assert all(v == v for v in out["J_z_per_tri"])  # no NaN
