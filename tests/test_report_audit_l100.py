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
