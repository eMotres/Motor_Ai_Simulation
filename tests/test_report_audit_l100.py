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
