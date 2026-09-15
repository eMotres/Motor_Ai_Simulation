"""The carbon-fibre retaining sleeve — a ring ON the rotor, INSIDE the air gap.

Four things have to be true, and each of them has bitten some other part of
this codebase before:

1. **It is refused when it does not fit.**  The sleeve eats the air gap, so a
   ring as thick as the gap is a machine that cannot turn — and, worse, a ring
   that leaves 0.05 mm is geometrically fine and leaves the sliding band
   nowhere to be meshed.  Both are errors, with different sentences, at every
   door: the Geometry tab's value check and the polygon validator that gates
   every solve.

2. **The ring is where the parameters say it is**, and it belongs to the ROTOR
   half of the sliding band — the half that turns.  A rotating solid that ends
   up on the stationary side gets sheared by the band every step.

3. **It weighs what a ring of that material weighs** — the closed form, not a
   number that happens to come out of the CAD — and it carries the inertia of
   metal at the largest radius on the rotor.

4. **Absent (0, the default) it changes NOTHING.**  Every assertion in the last
   class is "the sleeved machine and the sleeveless one agree", because the
   sleeve is non-magnetic: the field it sits in is the field of the same
   machine without it, and torque must say so.

Everything here is SOLVER-DIRECT (``fem_transient_sliding_band`` with a
``geo_override``) or pure-function.  Nothing goes through a route that writes:
a test run once clobbered the user's live transient by taking a persisting path.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.geometry_constraints import MIN_MECH_GAP_MM
from motor_ai_sim.geometry_validation import (
    sleeve_gap_error,
    validate_geometry,
    validate_parameter_values,
)

# The regression suite's 30 mm 12s14p machine with the air gap opened from
# 0.2 mm to 1.0 mm.  0.2 mm is not a gap a retaining sleeve can live in — the
# band alone needs 0.12 mm of it — so the sleeve cases would all be REFUSALS,
# which tests the refusal and nothing else.  1.0 mm leaves room for a 0.4 mm
# ring and still 0.6 mm of mechanical gap.
GEO: Dict[str, float] = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 1.0, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "wire_parallel": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}
SLEEVE_MM = 0.4


def _geo(**over) -> Dict[str, float]:
    return dict(GEO, **over)


def _polys(**over):
    m = CadQueryMotor()
    m.set_parameters(_geo(**over))
    return m, m.get_2d_polygons(rotor_angle_deg=0.0)


# ─────────────────────────────────────────────────────────────────────────────
class TestItIsRefusedWhenItDoesNotFit:
    """Loud, engineer-readable, and at every door — never a silent solve."""

    def test_a_sleeve_thicker_than_the_gap_names_both_numbers(self):
        g = _geo(sleeve_thickness=1.2)          # 1.2 mm ring in a 1.0 mm gap
        bad = validate_parameter_values(g)
        recs = [r for r in bad if r["field"] == "sleeve_thickness"]
        assert recs, "a 1.2 mm sleeve in a 1.0 mm air gap must be refused"
        msg = recs[0]["message"]
        # BOTH numbers, in the message, so the engineer does not have to guess
        # which of the two knobs to move.
        assert "sleeve_thickness" in msg and "1.2" in msg
        assert "air_gap" in msg and "1 mm" in msg
        # …and it is a DERIVED rule: the pair is broken, not one field, so a
        # partial edit of either half is still judged.
        assert recs[0]["kind"] == "derived"

    def test_a_sleeve_that_starves_the_band_names_the_minimum(self):
        g = _geo(sleeve_thickness=0.9)          # fits, but leaves only 0.1 mm
        bad = validate_parameter_values(g)
        recs = [r for r in bad if r["field"] == "sleeve_thickness"]
        assert recs, ("0.9 mm of sleeve in a 1.0 mm gap leaves 0.1 mm — under "
                      "the sliding band's minimum — and must be refused")
        msg = recs[0]["message"]
        assert "{:g}".format(MIN_MECH_GAP_MM) in msg, (
            "the message must name the minimum mechanical gap, not just say no")
        assert "0.100 mm" in msg, "…and what the design actually leaves"
        assert "sliding band" in msg

    @pytest.mark.parametrize("t", [0.0, 0.4, 0.88])
    def test_a_sleeve_that_fits_is_not_refused(self, t):
        # 0.88 == air_gap − MIN_MECH_GAP_MM exactly: the published bound has to
        # be a legal value (the off-by-one at the boundary the sweep lands on).
        assert sleeve_gap_error(_geo(sleeve_thickness=t)) is None

    def test_the_solve_gate_refuses_it_with_the_same_sentence(self):
        """validate_geometry is what routes/simulation gates every solve on."""
        res = validate_geometry(_geo(sleeve_thickness=0.9))
        assert not res.ok, "a solve of this cross-section must be refused"
        msgs = [v.message for v in res.errors]
        assert any("sleeve_thickness" in m for m in msgs), msgs
        # Verbatim the same wording the Geometry tab refuses with — one design,
        # one explanation, wherever it is rejected.
        shared = sleeve_gap_error(_geo(sleeve_thickness=0.9))[2]
        assert shared in msgs

    def test_a_thick_sleeve_also_trips_the_polygon_checks(self):
        """Belt and braces: the ring physically reaches the stator."""
        res = validate_geometry(_geo(sleeve_thickness=1.2))
        assert not res.ok
        codes = {v.code for v in res.errors}
        assert "sleeve_fills_air_gap" in codes
        # the geometric witness of the same fact
        assert {"sleeve_overlaps_stator", "rotor_crosses_air_gap",
                "air_gap_not_positive"} & codes, codes

    def test_the_default_machine_is_untouched(self):
        assert validate_parameter_values(_geo()) == []
        assert validate_geometry(_geo()).ok


# ─────────────────────────────────────────────────────────────────────────────
class TestThePolygon:

    def test_the_ring_has_the_radii_the_parameters_ask_for(self):
        m, P = _polys(sleeve_thickness=SLEEVE_MM)
        sl = P["sleeve"]
        assert sl is not None and not sl.is_empty
        r_ro = float(m.parameters["rotor_outer_radius"])
        assert P["sleeve_r_mm"] == pytest.approx([r_ro, r_ro + SLEEVE_MM])
        exact = math.pi * ((r_ro + SLEEVE_MM) ** 2 - r_ro ** 2)
        # 256-gon rings: the polygon is inscribed, so it is a hair under the
        # true annulus.  0.1 % is the discretisation, not a modelling error.
        assert sl.area == pytest.approx(exact, rel=2e-3)

    def test_there_is_no_ring_without_the_parameter(self):
        for g in ({}, {"sleeve_thickness": 0.0}):
            _m, P = _polys(**g)
            assert P.get("sleeve") is None
            assert P.get("sleeve_r_mm") is None

    def test_it_turns_with_the_rotor(self):
        """The ring is on the ROTOR side of the slip surface, in both senses:
        it lies entirely below mid_r, and the sliding-band split hands it to
        the half that rotates."""
        from motor_ai_sim.simulation.mesher import _split_polys_for_sliding_band
        _m, P = _polys(sleeve_thickness=SLEEVE_MM)
        _r_in, r_out = P["sleeve_r_mm"]
        assert r_out <= float(P["mid_r_mm"]) + 1e-9, (
            "the sleeve OD must not cross the slip surface — the band would "
            "shear the ring every step")
        polys_s, polys_r = _split_polys_for_sliding_band(P)
        assert "sleeve" in polys_r and polys_r["sleeve"] is not None
        assert polys_s.get("sleeve") is None

    def test_the_slip_surface_moves_into_the_remaining_gap(self):
        """mid_r is the middle of the MECHANICAL gap, not of the magnetic one."""
        m0, P0 = _polys()
        m1, P1 = _polys(sleeve_thickness=SLEEVE_MM)
        r_ro = float(m0.parameters["rotor_outer_radius"])
        r_si = float(m0.parameters["stator_inner_radius"])
        assert P0["mid_r_mm"] == pytest.approx(0.5 * (r_ro + r_si))
        assert P1["mid_r_mm"] == pytest.approx(0.5 * (r_ro + SLEEVE_MM + r_si))

    def test_the_ring_is_disjoint_from_the_air_domains_and_the_stator(self):
        _m, P = _polys(sleeve_thickness=SLEEVE_MM)
        sl = P["sleeve"]
        for k in ("in_band", "out_band", "stator", "air_gap"):
            assert P[k].intersection(sl).area < 1e-4, (
                "%s and the sleeve share plane — whichever wins the mesh, the "
                "other loses its material" % k)

    def test_the_viewer_gets_the_ring_too(self):
        m = CadQueryMotor()
        m.set_parameters(_geo(sleeve_thickness=SLEEVE_MM))
        assert "sleeve" in m.get_2d_mesh_data()
        m2 = CadQueryMotor()
        m2.set_parameters(_geo())
        assert "sleeve" not in m2.get_2d_mesh_data()


# ─────────────────────────────────────────────────────────────────────────────
class TestMassAndInertia:

    def _p(self, geo):
        from motor_ai_sim.simulation.fem_solver_2d import _params_from_geo_dict
        return _params_from_geo_dict(geo)

    def test_the_ring_weighs_rho_times_its_own_annulus(self):
        from motor_ai_sim.masses import compute_masses, part_material
        g = _geo(sleeve_thickness=SLEEVE_MM)
        p = self._p(g)
        rho, _kf, name = part_material("sleeve")
        # The CONFIGURED band, not a hard-coded one: this reads the live
        # materials map, and the user changes the band while working (it was
        # T800_UD_60 until they tried M40X_UD_60 on 2026-09-10).  What is pinned
        # here is that the ring weighs rho x its own annulus, whichever card
        # rho comes from.
        assert name, "no sleeve material is assigned"
        assert rho > 0.0, name
        m = compute_masses(p, g)
        r1 = float(p.r_rotor_out)
        r2 = r1 + SLEEVE_MM * 1e-3
        expect = rho * math.pi * (r2 ** 2 - r1 ** 2) * float(p.stack_length)
        assert m["sleeve"] == pytest.approx(expect, rel=3e-3)
        assert m["MAT"]["sleeve"] == name          # the card `rho` came from

    def test_it_is_in_the_active_mass_and_in_the_total(self):
        """The ring counts as ACTIVE mass since 2026-09-10.

        It used to be kept out of `active` so that number stayed the one ANSYS
        prints under the same name.  The user gave that up on purpose ("пусть
        будет одна активная масса вместе с бандажом, так будет проще, чтобы не
        запутаться"): two masses differing by a quarter of a kilo, one of which
        silently omits a part visible in the 3-D view, cost more than the
        comparison was worth.  `total` is unchanged — it always held the ring —
        and so is every N·m/kg, which divides by it.

        `rel` is 1e-6, not the 1e-12 identity it was until 2026-09-06: the rotor
        side is now welded as ONE set (`_weld_group_geoms`), and the air band —
        which IS cut around the sleeve — is a member of that set, so a machine
        with a ring nodes its rotor and magnet rings a few float-ulps
        differently from one without.  The measured drift is 9e-9 relative
        (3.6e-10 kg on 0.040 kg).  1e-6 is still four orders of magnitude below
        the sleeve's own mass, so it catches the thing this pins — the ring
        leaking into `active` — just as flatly."""
        from motor_ai_sim.masses import compute_masses
        g0, g1 = _geo(), _geo(sleeve_thickness=SLEEVE_MM)
        m0 = compute_masses(self._p(g0), g0)
        m1 = compute_masses(self._p(g1), g1)
        assert m1["sleeve"] > 0.0
        # 1e-6, not an identity: the rotor side is welded as ONE set and the air
        # band is cut around the ring, so a machine with a ring nodes its rotor
        # and magnet rings a few float-ulps differently from one without
        # (measured drift 9e-9 relative).  Far below the ring's own mass either
        # way, so it still catches the thing this pins — WHICH total the ring
        # lands in.
        assert m1["active"] == pytest.approx(m0["active"] + m1["sleeve"], rel=1e-6)
        assert m1["total"] == pytest.approx(m0["total"] + m1["sleeve"], rel=1e-6)
        # …and the two differ by the shaft alone, on both machines.
        for m in (m0, m1):
            assert m["total"] == pytest.approx(m["active"] + m["shaft"], rel=1e-9)
        assert m1["sleeve"] > 0.0

    def test_no_sleeve_weighs_nothing_and_moves_no_total(self):
        from motor_ai_sim.masses import compute_masses
        g = _geo()
        m = compute_masses(self._p(g), g)
        assert m["sleeve"] == 0.0
        assert m["total"] == pytest.approx(m["active"] + m["shaft"], rel=1e-12)

    def test_the_ring_carries_the_inertia_of_metal_at_the_rotor_od(self):
        from motor_ai_sim.masses import rotor_inertia_kg_m2, part_material
        g = _geo(sleeve_thickness=SLEEVE_MM)
        p = self._p(g)
        rho, _kf, _n = part_material("sleeve")
        J = rotor_inertia_kg_m2(p, g)
        r1 = float(p.r_rotor_out)
        r2 = r1 + SLEEVE_MM * 1e-3
        # thin ring about its own axis: J = rho*L*(pi/2)*(r2^4 - r1^4)
        expect = rho * float(p.stack_length) * math.pi / 2.0 * (r2 ** 4 - r1 ** 4)
        assert J["sleeve"] == pytest.approx(expect, rel=5e-3)
        J0 = rotor_inertia_kg_m2(p, _geo())
        assert J["total"] == pytest.approx(J0["total"] + J["sleeve"], rel=1e-6)

    def test_an_excluded_sleeve_leaves_the_mass_and_the_inertia(self, monkeypatch):
        import motor_ai_sim.masses as M
        from motor_ai_sim.masses import compute_masses, rotor_inertia_kg_m2
        g = _geo(sleeve_thickness=SLEEVE_MM)
        p = self._p(g)
        monkeypatch.setattr(M, "_part_states", lambda: {"sleeve": "excluded"})
        m = compute_masses(p, g)
        assert m["sleeve"] == 0.0
        assert m["PHYS"]["sleeve"] > 0.0, (
            "what it WOULD weigh has to stay reportable, or the total just "
            "shrinks with no explanation")
        J = rotor_inertia_kg_m2(p, g)
        assert J["sleeve"] == 0.0
        assert J["J_modelled"]["sleeve"] > 0.0


# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
class TestTheSolve:
    """One coupled-eddy solve per state, straight into the solver.

    Coarse on purpose (12 steps, 1.4 mm mesh, 1/2 sector): these are not
    publication numbers, they only have to answer three questions — does the
    torque stay put, does the sleeve get a loss of its own, and does excluding
    it put the machine back exactly.
    """

    COMMON = dict(n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4,
                  min_size_mm=0.35, gap_layers=1.0, n_sectors=2,
                  structured_gap=True, iron_template=True, geo_mesh=True,
                  coil_temp_c=120.0, element_order=2, demag=False, eddy=True,
                  rotor_eddy=True, I_phase_rms=60.0, gamma_deg=0.0, rpm=15000.0,
                  # rpm AND connection are arguments for the reason
                  # test_physics_regression spells out at length (F2/F3): the
                  # solver used to read both off the shared config, so a test
                  # answered whatever machine the user happened to be editing.
                  # Measured here on 2026-09-03: the live config's `2P` made
                  # the d-axis calibration return psi_A == 0 at every sampled
                  # angle and the run died before solving anything.
                  connection="2S")
    OVERRIDE = {"assignment": {"magnet": "F45SH_120C",
                               "stator_core": "B15AHV950M",
                               "rotor_core": "B15AHV950M"},
                "materials": {},
                "parts": {"stator_core": "included", "rotor_core": "included",
                          "magnet": "included", "slot": "included",
                          "shaft": "included"}}

    def _run(self, geo, parts=None):
        from motor_ai_sim.material_context import set_request_materials
        from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
        ov = dict(self.OVERRIDE)
        ov["parts"] = dict(self.OVERRIDE["parts"], **(parts or {}))
        set_request_materials(ov)
        try:
            return fem_transient_sliding_band(geo_override=geo, **self.COMMON)
        finally:
            set_request_materials(None)

    @pytest.fixture(scope="class")
    def runs(self):
        out = {}
        out["bare"] = self._run(_geo())
        out["sleeved"] = self._run(_geo(sleeve_thickness=SLEEVE_MM))
        out["excluded"] = self._run(_geo(sleeve_thickness=SLEEVE_MM),
                                    parts={"sleeve": "excluded"})
        return out

    @staticmethod
    def _mean(d: Dict[str, Any], key: str) -> float:
        v = d.get(key) or [0.0]
        return float(np.mean(np.asarray(v, float))) if len(v) else 0.0

    def test_the_magnetic_gap_is_unchanged_so_the_torque_barely_moves(self, runs):
        """Carbon fibre is mu_r = 1: the ring IS the air it displaces.  The
        torque may drift by the mesh (the gap is now three regions where it was
        one), but not by the physics."""
        t0 = float(runs["bare"]["T_avg_Nm"])
        t1 = float(runs["sleeved"]["T_avg_Nm"])
        assert t1 == pytest.approx(t0, rel=0.02), (
            "a NON-MAGNETIC ring moved the torque by %.2f %% — it is not "
            "displacing air, it is changing the machine"
            % (100.0 * (t1 - t0) / t0))

    def test_the_sleeve_gets_a_loss_row_of_its_own_and_it_is_small(self, runs):
        p_sl = self._mean(runs["sleeved"], "P_sleeve_eddy_W")
        assert p_sl > 0.0, "a conductor in a changing field dissipates"
        p_mag = self._mean(runs["sleeved"], "P_mag_eddy_W")
        assert p_sl < 0.01 * max(p_mag, 1e-9), (
            "%.4g W of sleeve loss against %.4g W of magnet loss — CFRP "
            "transverse to its fibres is ~80 S/m against the magnet's 6.7e5, "
            "so anything near the magnets' order means the FIBRE-direction "
            "conductivity leaked into the solve" % (p_sl, p_mag))
        assert runs["sleeved"]["P_sleeve_solve_W"] > 0.0

    def test_a_machine_without_a_sleeve_reports_no_sleeve_loss(self, runs):
        assert self._mean(runs["bare"], "P_sleeve_eddy_W") == 0.0
        assert runs["bare"]["P_sleeve_solve_W"] == 0.0

    def test_excluding_it_takes_it_out_of_the_field(self, runs):
        assert self._mean(runs["excluded"], "P_sleeve_eddy_W") == 0.0, (
            "an excluded part is AIR — air that dissipates is the bug this "
            "state exists to make impossible")
        assert float(runs["excluded"]["T_avg_Nm"]) == pytest.approx(
            float(runs["sleeved"]["T_avg_Nm"]), rel=1e-3), (
            "the ring is non-magnetic, so excluding it must not move the field")

    def test_the_summary_carries_the_mass_row_and_the_burst_check(self):
        """The card's own builder, called directly — it writes nothing."""
        from motor_ai_sim.routes.simulation import _compute_masses, _sleeve_hoop
        from motor_ai_sim.simulation.fem_solver_2d import _params_from_geo_dict
        g = _geo(sleeve_thickness=SLEEVE_MM)
        rows = _compute_masses(_params_from_geo_dict(g), g)["components"]
        names = [r["name"] for r in rows]
        # …named with whatever band is assigned, not a fixed card (see above).
        assert any(n.startswith("Sleeve (") for n in names), names
        # …and no phantom row on a machine that has none
        g0 = _geo()
        names0 = [r["name"] for r in
                  _compute_masses(_params_from_geo_dict(g0), g0)["components"]]
        assert not any(n.startswith("Sleeve") for n in names0), names0

        h = _sleeve_hoop(g, 15000.0)
        # The strength of the card that is ASSIGNED, not a fixed 2500: the band
        # is changed while designing (T800_UD_60 -> M40X_UD_60 on 2026-09-10),
        # and what this pins is that the burst check reads its own card.
        assert h is not None
        from motor_ai_sim.masses import part_material
        _rho, _kf, _name = part_material("sleeve")
        assert h["material"] == _name
        assert h["strength_MPa"] > 0.0
        # sigma = rho * omega^2 * r_mean^2, closed form
        rho = h["density_kg_m3"]
        w = 2.0 * math.pi * 15000.0 / 60.0
        r_mean = h["r_mean_mm"] * 1e-3
        # the payload rounds to 3 dp on purpose (it is a card number)
        assert h["sigma_hoop_MPa"] == pytest.approx(
            rho * w * w * r_mean * r_mean * 1e-6, abs=5e-4)
        assert _sleeve_hoop(_geo(), 15000.0) is None
