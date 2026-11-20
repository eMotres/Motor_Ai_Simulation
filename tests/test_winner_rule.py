"""The owner's winner rule (2026-11-18): the highest efficiency vs N*m/kg line first, then the lowest ripple inside that group; ripple
above the requirement is marked; lines that cross say where each wins."""
from __future__ import annotations

import pytest

from motor_ai_sim.optimization import winner_rule as W


def pts(geom, a, b, ripple=(2.0, 2.0)):
    """Two points (two currents) of one design: a = (td, eff), b = (td, eff)."""
    return [{"geom": geom, "td": a[0], "eff": a[1], "ripple": ripple[0], "current_a": 80.0},
            {"geom": geom, "td": b[0], "eff": b[1], "ripple": ripple[1], "current_a": 100.0}]


def test_the_higher_line_wins_whatever_the_ripple_when_it_is_clearly_higher():
    p = pts("A", (10, 0.95), (14, 0.94), ripple=(4.0, 4.5)) + pts("B", (10, 0.93), (14, 0.92), ripple=(0.5, 0.5))
    r = W.rank(p)
    assert r["winner"] == "A" and [d["geom"] for d in r["ranking"]] == ["A", "B"] and [d["group"] for d in r["ranking"]] == [1, 2]


def test_inside_the_top_group_the_lowest_ripple_wins():
    p = pts("A", (10, 0.950), (14, 0.940), ripple=(3.0, 4.0)) + pts("B", (10, 0.9495), (14, 0.9405), ripple=(1.0, 1.5))
    r = W.rank(p)
    assert [d["group"] for d in r["ranking"]] == [1, 1] and r["winner"] == "B"           # a 0.05 point difference is a tie


def test_a_design_over_the_ripple_requirement_is_marked_and_ranks_behind_inside_its_group():
    p = pts("A", (10, 0.950), (14, 0.940), ripple=(6.5, 6.0)) + pts("B", (10, 0.9495), (14, 0.9405), ripple=(4.5, 4.9))
    r = W.rank(p, ripple_max_pct=5.0)
    by = {d["geom"]: d for d in r["ranking"]}
    assert by["A"]["ripple_fail"] is True and by["B"]["ripple_fail"] is False and r["winner"] == "B"
    assert by["A"]["ripple_max_pct"] == 6.5


def test_an_unknown_ripple_never_passes():
    p = pts("A", (10, 0.95), (14, 0.94), ripple=(None, None))
    assert W.rank(p)["ranking"][0]["ripple_fail"] is True


def test_lines_are_read_at_equal_torque_per_mass_not_at_equal_current():
    # A is the better design at the same N*m/kg although its points sit at other torque densities
    p = pts("A", (12, 0.950), (16, 0.940)) + pts("B", (10, 0.9400), (14, 0.9300))
    r = W.rank(p)
    assert r["common_range"] is True and r["x_range"] == [12, 14] and r["winner"] == "A"


def test_crossing_lines_say_where_each_wins():
    p = pts("A", (10, 0.96), (14, 0.90)) + pts("B", (10, 0.92), (14, 0.94))
    r = W.rank(p)
    (c,) = r["crossings"]
    higher = [(x["higher"]) for x in c["ranges"]]
    assert set(higher) >= {"A", "B"} and c["ranges"][0]["higher"] == "A" and c["ranges"][-1]["higher"] == "B"
    assert r["winner"] in ("A", "B")


def test_no_common_range_is_said_and_a_single_point_has_no_line():
    p = pts("A", (10, 0.95), (12, 0.94)) + pts("B", (20, 0.90), (22, 0.89)) + [{"geom": "C", "td": 15, "eff": 0.99, "ripple": 1}]
    r = W.rank(p)
    assert r["common_range"] is False and r["winner"] == "A"
    assert [d["geom"] for d in r["incomplete"]] == ["C"] and all(d["geom"] != "C" for d in r["ranking"])


def test_nothing_to_rank():
    assert W.rank([])["winner"] is None and W.rank([{"geom": "A", "td": None, "eff": None}])["incomplete"][0]["geom"] == "A"


def test_the_route_and_the_sweep_flag():
    from motor_ai_sim.routes import optimization as O
    out = O.winner_rank(O.WinnerRankRequest(points=pts("A", (10, 0.95), (14, 0.94)) + pts("B", (10, 0.93), (14, 0.92)), tie_band_pct=0.2))
    assert out["winner"] == "A" and out["tie_band"] == pytest.approx(0.002)
    sweep_points = [{"feasible": True, "geom_id": g, "current_a": i, "torque_per_mass_Nm_kg": td, "efficiency": e, "T_ripple_pct": 2.0}
                    for g, rows in (("0", [(10, 0.95), (14, 0.94)]), ("1", [(10, 0.93), (14, 0.92)])) for i, (td, e) in enumerate(rows)]
    sweep_points.append({"feasible": False, "geom_id": "2", "current_a": 1})
    r = O._winner_rule_of(sweep_points, ripple_max=100.0)
    assert r["winner"] == "0" and r["ripple_max_pct"] == 5.0                        # the requirement is never looser than 5 %
    assert "winner_rule" not in O.prepare_scan(O.ScanRequest(variables=[]))["kwargs"]   # opt-in
    assert O.prepare_scan(O.ScanRequest(variables=[], rank_winner=True))["kwargs"]["rank_winner"] is True
