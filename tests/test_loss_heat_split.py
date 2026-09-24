"""Two numbers a cooling engineer can act on: STATOR heat and ROTOR heat.

Every watt the card reports has to leave the machine through one of two paths —
the stator (housing, jacket, fan) or the rotor (air gap, shaft, windage) — and
until now the loss breakdown was by PHYSICS (copper / iron / solid) with no way
to say which side of the air gap the iron loss was in.  The split is:

    stator = stator iron + ALL copper   (I^2R incl. end-winding + AC/proximity)
    rotor  = rotor iron + magnet eddy + shaft eddy + sleeve eddy

The one property that makes it trustworthy is that NOTHING is dropped: the two
sides add up to the reported total loss, exactly, at the card's own resolution.
That is what this file pins — on a real solve, not on a hand-written dict, so a
term that grows a new home in the solver cannot slip past it.

The second class is the FALLBACK.  A stored run from before the solver reported
its per-half Bertotti terms cannot be split honestly; the whole core loss then
goes to the stator (where all but a few percent of it physically is on this
machine) and the summary SAYS so, instead of quoting a number nobody measured.

Solver-direct throughout (``fem_transient_sliding_band`` + the summary builder
called on its result).  Neither writes: no route, no config mutation, no cache.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CONNECTION, GEO_30MM, OVERRIDE, RPM)

WEB = pathlib.Path(__file__).resolve().parents[1] / "web" / "src"

# The regression machine at its own operating point, coarse in time (this is an
# accounting test, not a physics pin) and with the conducting rotor ON — the
# magnet/shaft eddy terms are half of what the rotor side even IS, so solving
# without them would test an identity with the interesting terms at zero.
RUN = dict(n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=True, rotor_eddy=True,
           I_phase_rms=60.0, gamma_deg=0.0, rpm=RPM, connection=CONNECTION)


def _summary(res: Dict[str, Any]) -> Dict[str, Any]:
    """The card's own builder, called directly on a solver result."""
    from motor_ai_sim.routes.simulation import _build_transient_summary
    set_request_materials(OVERRIDE)
    try:
        return _build_transient_summary(
            res, I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def solved() -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), **RUN)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def summary(solved) -> Dict[str, Any]:
    return _summary(solved)


class TestTheTwoSidesAddUpToTheWholeLoss:
    """No watt is dropped, and none is counted twice."""

    def test_stator_plus_rotor_is_the_total_loss(self, summary):
        s = float(summary["P_loss_stator_W"])
        r = float(summary["P_loss_rotor_W"])
        tot = float(summary["P_loss_total_W"])
        assert s + r == pytest.approx(tot, abs=1e-6), (
            "stator %.3f + rotor %.3f = %.3f W against a reported total of "
            "%.3f W — the split is leaking (or double-counting) watts"
            % (s, r, s + r, tot))
        assert s > 0.0 and r > 0.0, (s, r)

    def test_the_stator_side_is_its_iron_plus_all_the_copper(self, summary):
        assert (float(summary["P_loss_stator_W"])
                == pytest.approx(float(summary["P_core_stator_W"])
                                 + float(summary["P_stranded_W"]), abs=0.15))

    def test_the_rotor_side_is_its_iron_plus_every_solid_loss(self, summary):
        assert (float(summary["P_loss_rotor_W"])
                == pytest.approx(float(summary["P_core_rotor_W"])
                                 + float(summary["P_solid_W"]), abs=0.15))

    def test_the_core_loss_halves_add_up_to_the_core_tile(self, summary):
        assert (float(summary["P_core_stator_W"])
                + float(summary["P_core_rotor_W"])
                == pytest.approx(float(summary["P_core_W"]), abs=0.15))

    def test_the_split_came_from_the_solver_not_from_an_assumption(self, summary):
        assert summary["P_loss_split_measured"] is True
        assert float(summary["P_core_rotor_W"]) > 0.0, (
            "a spinning rotor's back iron sees the slot-passing harmonics — a "
            "rotor iron loss of exactly zero means the per-half terms never "
            "reached the summary")

    def test_the_halves_are_the_solver_s_own_per_half_terms(self, solved, summary):
        """Not a re-derivation: the ratio IS ``P_fe_terms``, renormalised onto
        the reported core loss (the terms are rounded, the series is clipped)."""
        terms = solved["P_fe_terms"]
        half = {h: sum(float(terms[h][k]) for k in
                       ("hysteresis_W", "eddy_W", "excess_W")) for h in terms}
        want = half["stator"] / (half["stator"] + half["rotor"])
        got = (float(summary["P_core_stator_W"])
               / max(float(summary["P_core_W"]), 1e-9))
        # The summary rounds P_core_stator_W to 0.1 W; on this ~4.6 W fixture
        # that alone is up to ±0.05/4.6 ≈ 1.1 % of the ratio, so a 2e-3 bound
        # only held by rounding luck (it broke when the rotor iron moved onto a
        # commensurate window, 2026-09-24, with the split itself exact). The
        # tolerance is the rounding half-step plus the old 2e-3.
        tol = 0.05 / max(float(summary["P_core_W"]), 1e-9) + 2e-3 * want
        assert got == pytest.approx(want, abs=tol), (half, got, want)

    def test_the_solid_terms_all_land_on_the_rotor(self, solved, summary):
        """Magnet + shaft + sleeve eddy — the loss map's own series, meaned the
        way the card means them."""
        def _mean(k):
            v = solved.get(k) or [0.0]
            return float(np.mean(np.asarray(v, float))) if len(v) else 0.0
        solid = (_mean("P_mag_eddy_W") + _mean("P_shaft_eddy_W")
                 + _mean("P_sleeve_eddy_W"))
        assert solid > 0.0, "rotor_eddy was on — the magnets must dissipate"
        assert float(summary["P_loss_rotor_W"]) >= solid - 0.15


class TestThePassportScalingFallback:
    """A run — or a passport — without the per-half split must not crash, and
    must not pretend it has one."""

    def test_a_run_without_the_per_half_terms_bills_the_iron_to_the_stator(
            self, solved):
        stale = dict(solved)
        stale.pop("P_fe_terms", None)          # a summary from before the split
        s = _summary(stale)
        assert s["P_loss_split_measured"] is False
        assert float(s["P_core_rotor_W"]) == pytest.approx(0.0, abs=0.05)
        assert (float(s["P_core_stator_W"])
                == pytest.approx(float(s["P_core_W"]), abs=0.05))
        # …and the identity still holds, which is the whole point of putting
        # the unattributable watts SOMEWHERE rather than dropping them.
        assert (float(s["P_loss_stator_W"]) + float(s["P_loss_rotor_W"])
                == pytest.approx(float(s["P_loss_total_W"]), abs=1e-6))
        assert float(s["P_loss_rotor_W"]) >= 0.0

    def test_an_empty_terms_block_is_treated_as_no_split(self, solved):
        s = _summary(dict(solved, P_fe_terms={}))
        assert s["P_loss_split_measured"] is False
        assert (float(s["P_loss_stator_W"]) + float(s["P_loss_rotor_W"])
                == pytest.approx(float(s["P_loss_total_W"]), abs=1e-6))

    def test_the_passport_records_the_split_only_when_it_has_one(self):
        """Presence-gated: an old machine's passport grows no key at all, so
        the tuner can tell 'not measured' from 'measured as zero'."""
        import inspect
        from motor_ai_sim import passport as _pp
        src = inspect.getsource(_pp.generate_passport)
        assert '"P_fe_stator0_W"' in src and '"P_fe_rotor0_W"' in src
        i = src.index('"P_fe_stator0_W"')
        assert 'P_core_stator_W' in src[max(0, i - 300):i], \
            "the split must come from the summary's own per-half cells"

    def test_the_tuner_falls_back_to_the_whole_iron_on_the_stator(self):
        src = (WEB / "lib" / "motorScaling.ts").read_text(encoding="utf-8")
        assert "P_fe_stator0_W" in src and "P_loss_stator_W" in src
        i = src.index("const feSplit")
        block = src[i:i + 400]
        assert "feRatio" in block and ": 1;" in block, \
            "a passport without the split must scale with ratio 1 (all stator)"
        assert "loss_split_measured" in src

    def test_the_configure_tiles_say_how_the_split_was_made(self):
        src = (WEB / "components" / "compare"
               / "ConfiguratorPanel.tsx").read_text(encoding="utf-8")
        assert "Stator heat" in src and "Rotor heat" in src
        assert "iron split unknown" in src and "regenerate the passport" in src

    def test_the_simulation_card_carries_both_cells(self):
        src = (WEB / "components" / "simulation"
               / "SummaryTable.tsx").read_text(encoding="utf-8")
        assert "P_loss_stator_W" in src and "P_loss_rotor_W" in src
        assert 'label="Stator heat"' in src and 'label="Rotor heat"' in src
        # the split is a LOSS, and losses stay 2-D under the 3D toggle
        i = src.index("const s = (() => {")
        assert "P_loss_stator_W" not in src[i:src.index("as TransientSummary;", i)], \
            "the 3D toggle must not rescale a loss"

    def test_the_datasheet_has_a_row_per_side(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "src"
               / "motor_ai_sim" / "datasheet.py").read_text(encoding="utf-8")
        assert "Stator heat to remove (W)" in src
        assert "Rotor heat to remove (W)" in src
        assert "summary.P_loss_" in src


def test_the_summary_shape_version_was_bumped_for_the_new_cells():
    """A stored summary from before the split has neither cell; the shape stamp
    is what makes a restore rebuild it instead of showing empty tiles."""
    from motor_ai_sim.routes import simulation as _sim
    assert _sim._SUMMARY_SHAPE_V >= 15
