"""Regression tests of the report audit of 2026-10-27 (a 400 mm, 48-slot / 56-pole six-phase variant).

One class per finding; every test builds the stored shapes by hand, no solve.
"""
from __future__ import annotations

from motor_ai_sim import report as R


def _limited_map():
    """A translated (``limited``) thermal record whose level shift pushed the cold parts under the ambient."""
    return {
        "state": "limited",
        "T_max": 200.0, "T_min": -72.7,
        "point": {"ambient_temp": 45.0},
        "cooling": {"outer": {"mode": "air", "t_sink_c": 45.0, "air_speed_mps": 24.6}},
        "components": {"winding": {"max": 200.0, "avg": 184.6},
                       "gap_air": {"max": 82.4, "avg": 40.1}},
    }


class TestClaim1TranslatedMapTemperatures:
    def test_a_translated_map_prints_no_part_table(self):
        t = _limited_map()
        assert R.thermal_temp_rows(t, t) == [["Part", "max / avg [°C]"]]

    def test_a_translated_map_prints_no_extremes(self):
        t = _limited_map()
        txt = R.thermal_extremes_text(t, t)
        assert txt == R.TRANSLATED_TEMPS_WORDS
        assert "-72.7" not in txt and "−72.7" not in txt

    def test_a_steady_map_never_prints_a_node_under_its_coldest_boundary(self):
        t = _limited_map()
        t.pop("state")
        txt = R.thermal_extremes_text(t, t)
        assert "72.7" not in txt
        assert "not printed" in txt
        rows = R.thermal_temp_rows(t, t)
        gap = [r for r in rows if r[0] == "Air gap"][0]
        assert "40.1" not in gap[1]           # an average under the 45 degC ambient is not printed
        win = [r for r in rows if r[0].startswith("Winding")][0]
        assert "184.6" in win[1]

    def test_the_floor_is_the_coldest_sink(self):
        t = {"cooling": {"outer": {"mode": "liquid", "t_sink_c": 30.0}}, "point": {"ambient_temp": 45.0}}
        assert R.thermal_floor_c(t) == 30.0
        assert R.thermal_floor_c({}) is None


class TestClaim2OneSolvePerCoupledColumn:
    def _s1(self, **kw):
        rec = {"coil_temp_c": 180.0, "magnet_temp_c": 92.3, "magnet_temp_max_c": 86.7,
               "continuous_rating": {"record_is_s1": True, "verified": True}}
        rec.update(kw)
        return rec

    def test_a_legacy_s1_record_prints_no_hottest_magnet_of_another_solve(self):
        assert R.coupled_magnet_max_c(self._s1()) is None

    def test_the_s1_passs_own_map_is_printed(self):
        assert R.coupled_magnet_max_c(self._s1(magnet_temp_max_c=95.1,
                                               magnet_temp_max_pass="s1_verify")) == 95.1

    def test_a_record_the_loop_settled_keeps_its_hottest_magnet(self):
        assert R.coupled_magnet_max_c({"magnet_temp_c": 55.6, "magnet_temp_max_c": 61.0}) == 61.0

    def test_the_compare_row_and_the_warning_context_read_the_same_rule(self):
        col = {"duty": "cruise", "res": {"coupled": self._s1()}, "em": {}, "d": {}}
        _hdr, rows = R.coupled_compare_rows([col])
        row = [r for r in rows if r[0].startswith("Magnet temperature, hottest")]
        assert not row or "86.7" not in str(row[0][1])

    def test_the_duty_store_keeps_which_pass_the_hottest_magnet_is(self):
        from motor_ai_sim import duty_results as dr
        out = dr.compact_coupled({"coupling": {"magnet_temp_max_c": 95.1,
                                               "magnet_temp_max_pass": "s1_verify"}})
        assert out["magnet_temp_max_pass"] == "s1_verify"


class TestClaim3PreliminaryTimeInTheRequirements:
    def _col(self, pct):
        ttl = {"within_limits": False, "limiting_part": "winding", "limits_c": {"winding": 200.0},
               "starts": {"cold": {"time_to_limit_s": 47.1}},
               "network": {"available": True, "worst_residual_W": 5000.0, "worst_residual_pct_of_losses": pct}}
        return {"duty": "peak", "d": {"rpm": 1000}, "em": {"rpm": 1000, "T_em_avg_Nm": 1587.6, "coil_temp_C": 150},
                "res": {"coupled": {"time_to_limit": ttl}}}

    def _row(self, pct):
        from motor_ai_sim import report_customer as rc
        req = {"peak": {"torque_Nm": 1500.0, "speed_rpm": 1000.0, "duration_s": 60.0}}
        rows = rc.requirements_rows(req, [self._col(pct)], winding_limit_c=180.0, winding_peak_limit_c=200.0)
        return [r for r in rows if r[0].startswith("Peak held")][0]

    def test_a_time_the_report_calls_preliminary_is_not_a_firm_verdict(self):
        row = self._row(51.3)
        assert row[3] == "not met (preliminary)"
        assert "preliminary estimate" in row[2] and "51.3" in row[2]

    def test_a_balanced_network_keeps_the_plain_verdict(self):
        row = self._row(0.4)
        assert row[3] == "not met"
        assert "preliminary" not in row[2]


class TestClaim4DemagSentencesFromOneSolve:
    RUN = {"demag": {"br_kept_vol_pct": 98.884, "bh_loss_pct": 2.122,
                     "br_corner": {"flag": "corner", "br_pct": 30.5}}}
    MAP = {"n_elements": 1302, "n_below_80": 0, "min_pct": 98.4}

    def test_no_field_statistics_beside_another_solves_summary(self, monkeypatch):
        monkeypatch.setattr(R, "demag_corner_stats", lambda *a, **k: dict(self.MAP))
        assert R.demag_corner_stats_if_same_solve("d", "c", "cruise", self.RUN) is None
        rec = {"demag": dict(self.RUN["demag"], recomputed_from_field=True)}
        assert R.demag_corner_stats_if_same_solve("d", "c", "cruise", rec) == self.MAP

    def test_the_paragraph_then_has_one_worst_element(self):
        txt = R.em_demag_text(self.RUN, None)
        assert "30.5" in txt and "98.4" not in txt

    def test_the_map_caption_reads_the_map_it_captions(self):
        left = {"em": self.RUN, "demag": dict(self.MAP)}
        right = {"em": self.RUN, "demag": dict(self.MAP, min_pct=97.9)}
        cap = R.em_map_numbers("demag", {"demag_min_pct": 98.4}, {"demag_min_pct": 97.9}, left, right)
        assert "98.4" in cap and "97.9" in cap and "30.5" not in cap


class TestClaim5DemagAtTheTemperatureItWasSolvedAt:
    DEM = {"br_kept_vol_pct": 97.842, "loss_pct": 2.158, "bh_loss_pct": 3.62, "area_derated_pct": 100.0,
           "magnet_name": "N52UH_150C", "br_corner": {"flag": "corner", "br_pct": 4.8}}

    def test_an_em_run_at_the_card_is_stated_at_the_card(self):
        left = {"duty": "peak", "em": {"demag": dict(self.DEM)}, "coupled": {"magnet_temp_c": 55.6}}
        right = {"duty": "cruise", "em": {"demag": dict(self.DEM, loss_pct=1.116)},
                 "coupled": {"magnet_temp_c": 92.3}}
        note = R.demag_pair_note(left, right)
        assert "duty 'peak': magnets 150" in note and "duty 'cruise': magnets 150" in note
        assert "56" not in note and "92" not in note
        assert "worse" not in note            # one card temperature: no temperature explanation

    def test_a_field_recomputed_block_keeps_the_loops_temperature(self):
        side = {"duty": "peak", "em": {"demag": dict(self.DEM, recomputed_from_field=True)},
                "coupled": {"magnet_temp_c": 55.6}}
        assert R.demag_magnet_temp_c(side) == 55.6

    def test_section_4_does_not_label_the_loops_magnets_as_the_em_runs(self):
        rows = R.em_operating_rows({"rpm": 850}, {}, {}, None, {"magnet": "N52UH_150C"},
                                   cp={"magnet_temp_c": 92.3})
        mag = next(r for r in rows if r[2] == "Magnet temperature")
        assert "150" in mag[3] and "coupled loop" not in mag[3]


def _ctx(col, mats=None, **kw):
    return R._warning_context(col, mats=mats or {"magnet": "N52UH_150C"}, batt={}, brg=None, max_speed_rpm=None,
                              mag_lim=180.0, mag_note="the card", ins_lim=180.0, ins_note="180", cold_k=1.0,
                              cold_note="", **kw)


class TestClaim6InputsAreNotResults:
    def _unsolved(self):
        return {"duty": "takeoff", "d": {"rpm": 900, "mesh": {"sim.magnetTempC": 150}},
                "em": {"coil_temp_C": 150.0, "rpm": 900}, "res": {}}

    def test_an_unsolved_duty_has_no_temperature_to_judge(self):
        ctx = _ctx(self._unsolved())
        assert ctx["magnet_temp_c"] is None and ctx["winding_temp_c"] is None
        assert ctx["magnet_temp_input_c"] == 150.0 and ctx["winding_temp_input_c"] == 150.0
        rules = {w["rule"] for w in R.duty_warnings(ctx)}
        assert "magnet_temperature" not in rules and "winding_temperature" not in rules

    def test_the_hottest_magnets_sentence_reads_solved_values_only(self):
        ctxs = {"takeoff": _ctx(self._unsolved()),
                "cruise": {"magnet_temp_c": 92.3, "magnet_limit_c": 180.0}}
        txt = R.magnet_text({"magnet": "N52UH_150C"}, {}, ctxs)
        assert "hottest at 92.3" in txt and "'cruise'" in txt and "takeoff" not in txt

    def test_a_solved_loop_is_still_judged(self):
        col = self._unsolved()
        col["res"] = {"coupled": {"coil_temp_c": 180.0, "magnet_temp_c": 92.3}}
        ctx = _ctx(col)
        assert ctx["magnet_temp_c"] == 92.3 and ctx["winding_temp_c"] == 180.0


class TestClaim7CurrentDensityLimitFromTheCoolingSet:
    COOL = {"cooling_mode": "air", "air_speed_mps": 25.0, "frame": "open", "open_air_speed_mps": 25.0,
            "ambient_temp": 45.0}

    def _col(self, duty, res):
        return {"duty": duty, "d": {"rpm": 850}, "em": {"J_coil_A_per_mm2": 15.8, "rpm": 850}, "res": res}

    def test_the_coupled_loops_cooling_decides_the_band(self):
        ctx = _ctx(self._col("cruise", {"coupled": {"continuous_rating": {"cooling": dict(self.COOL)}}}))
        assert ctx["cooling_kind"] == "air" and ctx["frame_open"] is True
        j = [w for w in R.duty_warnings(ctx) if w["rule"] == "current_density"][0]
        assert j["limit"] == 15.0

    def test_a_translated_map_does_not_hide_the_cooling(self):
        res = {"thermal": {"state": "limited", "cooling": {"outer": {"mode": "air"}}},
               "coupled": {"limited": {"cooling": dict(self.COOL)}}}
        ctx = _ctx(self._col("peak", res))
        assert ctx["frame_open"] is True

    def test_an_unsolved_duty_takes_the_configurations_cooling(self):
        ctxs = {"cruise": _ctx(self._col("cruise", {"coupled": {"continuous_rating": {"cooling": dict(self.COOL)}}})),
                "takeoff": _ctx(self._col("takeoff", {}))}
        assert ctxs["takeoff"]["cooling_note"].startswith("ASSUMED")
        R.share_configuration_cooling(ctxs)
        assert ctxs["takeoff"]["frame_open"] is True and "cruise" in ctxs["takeoff"]["cooling_note"]
        rows = R._limit_rules_rows_full(ctxs["takeoff"])
        jrow = [r for r in rows if r[0] == "Current density"][0]
        assert "15" in jrow[1] and "open" in jrow[2]

    def test_nothing_set_anywhere_stays_the_assumed_closed_band(self):
        ctx = _ctx(self._col("takeoff", {}))
        R.share_configuration_cooling({"takeoff": ctx})
        assert ctx["cooling_note"].startswith("ASSUMED") and not ctx["frame_open"]


class TestClaim8PerSetInductancesOnTheMachinesOperator:
    def test_the_six_phase_probe_is_differential_with_the_secant_beside_it(self):
        import numpy as np
        import scipy.sparse as sp
        from motor_ai_sim.simulation import fem_solver_2d as F
        from motor_ai_sim.winding_sets import vsd_basis

        rng = np.random.default_rng(3)
        M = rng.normal(size=(6, 6))
        Kd = M @ M.T + 6.0 * np.eye(6)          # secant operator (SPD)
        Td = 0.8 * Kd                            # tangent addition: J = 1.8 K -> L_diff = L_fp / 1.8

        class P2:
            last_dhdb_min_rel = 0.5

            def Kpw(self, A2):
                return sp.csr_matrix(Kd), "info"

            def tangent2(self, info, clamp=False):
                return sp.csr_matrix(Td)

            def solve_ff(self, A, F_, spd=True):
                return np.linalg.solve(A.toarray(), F_)

            def pad2(self, Pro, free, x):
                return np.asarray(x).ravel()

        f = {s: {ph: np.eye(6)[3 * (s - 1) + "ABC".index(ph)] for ph in "ABC"} for s in (1, 2)}
        out = F.frozen_permeability_vsd(P2(), sp.identity(6, format="csr"), np.arange(6), None, f, 1.0, 0.3, 1)
        ed = vsd_basis(0.3, F._ipark_dq)["d"]
        assert out["basis"] == "differential"
        assert abs(out["Ld_set_H"] - ed @ np.linalg.inv(Kd + Td) @ ed) < 1e-12
        assert abs(out["Ld_set_fp_H"] - ed @ np.linalg.inv(Kd) @ ed) < 1e-12
        assert abs(out["Ld_set_fp_H"] / out["Ld_set_H"] - 1.8) < 1e-9

    def _em(self, ind):
        return {"Ld_mH": 0.1499, "Lq_mH": 0.1618, "Ld_inc_mH": 0.1499, "Lq_inc_mH": 0.1618, "rpm": 850.0,
                "six_phase": {"phases": 6, "n_parallel": 2, "inductances": ind}}

    def test_a_legacy_secant_probe_prints_twice_the_machines_values(self):
        rows = {r[0]: r for r in R.em_constant_rows(self._em({"Ld_set_mH": 0.4442, "Lq_set_mH": 0.4745,
                                                               "Lxy_mH": 0.4615, "Lxy_pct_of_Ld": 103.9}))}
        ld = [v for k, v in rows.items() if k.startswith("Ld per set")][0]
        assert ld[1].startswith("0.2998") and "twice the machine" in ld[2]
        lxy = [v for k, v in rows.items() if k.startswith("L_xy")][0]
        assert "secant" in lxy[2]

    def test_a_differential_probe_prints_its_own_values(self):
        rows = {r[0]: r for r in R.em_constant_rows(self._em({"Ld_set_mH": 0.3001, "Lq_set_mH": 0.3240,
                                                               "Lxy_mH": 0.2, "Lxy_pct_of_Ld": 66.6,
                                                               "basis": "differential"}))}
        ld = [v for k, v in rows.items() if k.startswith("Ld per set")][0]
        assert ld[1].startswith("0.3001")


class TestClaim9RingModeSpatialOrder:
    MODES = {"modes": [{"index": 1, "f_hz": 488.0, "order": 2}, {"index": 3, "f_hz": 1362.0, "order": 3},
                       {"index": 9, "f_hz": 4685.0, "order": 0}, {"index": 13, "f_hz": 1365.0, "order": 8}]}

    def test_gcd(self):
        assert R.slot_force_order_gcd(48, 56) == 8
        assert R.slot_force_order_gcd(48, None) is None

    def test_an_order_3_mode_is_not_driven_by_slot_passing(self):
        res = {"modes": [m for m in self.MODES["modes"] if m["order"] != 8]}
        t = R.tightest_ring_mode(res, rpm=850, slots=48, poles=56)
        assert t is None or t["f_hz"] != 1362.0
        rows = R.mode_rows(res, rpm=850, slots=48, poles=56)
        r3 = [r for r in rows if r[0] == "3"][0]
        assert r3[4] == "not excited" and "orders 0, 8" in r3[3]

    def test_an_order_8_mode_on_the_line_is_still_flagged(self):
        t = R.tightest_ring_mode(self.MODES, rpm=850, slots=48, poles=56)
        assert t["f_hz"] == 1365.0 and t["excitation"] == "slot passing 2×"
        assert abs(t["margin_pct"]) < 1.0

    def test_without_a_pole_count_the_frequency_decides_as_before(self):
        t = R.tightest_ring_mode({"modes": self.MODES["modes"][:2]}, rpm=850, slots=48)
        assert t["f_hz"] == 1362.0


class TestClaim10PowerAccountingWords:
    def test_the_em_note_says_what_the_accounting_row_is(self):
        assert "end-winding copper" in R.EM_COMPARE_NOTE and "terminals" in R.EM_COMPARE_NOTE

    def test_no_solver_jargon_in_the_eddy_row(self):
        col = {"duty": "cruise", "d": {}, "em": {"eddy_settled": True, "eddy_settled_via_accelerator": True}}
        _h, rows = R.em_compare_rows([col], {})
        row = [r for r in rows if r[0] == "…eddy currents"][0]
        assert row[1] == "settled (accelerated warm-up)"
