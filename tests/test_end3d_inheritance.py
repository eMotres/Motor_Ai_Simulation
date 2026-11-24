"""A passport of an earlier geometry is lent only at the same stack length; the other fixes of the 3-D factor lookup."""
from __future__ import annotations

import pytest

from motor_ai_sim import end3d_store
from motor_ai_sim.routes import simulation as S

DESC = "24s/28p OD 200"


def _rec(k, stack=None, curve=None, when="2026-09-01T00:00:00Z", desc=DESC, method="own_mid_ratio"):
    r = {"k_flux": k, "machine": desc, "adopted_utc": when, "stack_mm": stack, "k_flux_vs_L": curve}
    if curve and method:                                  # a table written after the 2026-11-24 stack-length fix carries it
        r["k_flux_vs_L_method"] = method
    return r


@pytest.fixture
def env(monkeypatch):
    state = {"store": {}, "stack": 40.0}
    monkeypatch.setattr(end3d_store, "load", lambda: state["store"])
    monkeypatch.setattr(S, "_machine_desc", lambda geo=None: DESC)
    monkeypatch.setattr(S, "_geo_stack_mm", lambda geo=None: state["stack"])
    return state


def test_exact_fingerprint_is_returned_unchanged(env):
    env["store"] = {"fp1": _rec(0.9, stack=200.0)}
    r = S._end3d_lookup("fp1", {})
    assert r["k_flux"] == 0.9 and not r.get("inherited")


def test_outside_the_measured_span_there_is_no_factor_and_the_note_names_the_span(env):
    env["store"] = {"old": _rec(0.9896, stack=200.0, curve={"40": 0.946, "200": 0.9896})}
    d = {}
    env["stack"] = 25.0
    assert S._end3d_lookup("new", {}, diag=d) is None
    assert "no 3-D factor" in d["note"] and "40-200" in d["note"]
    env["stack"] = 230.0
    assert S._end3d_lookup("new", {}) is None                           # never extrapolated


def test_a_measured_length_within_five_percent_is_used_as_it_is(env):
    env["store"] = {"old": _rec(0.95, stack=41.5)}
    r = S._end3d_lookup("new", {})
    assert r["inherited"] and r["k_flux"] == 0.95 and r["inherited_stack_basis"] == "point"
    env["store"] = {"old": _rec(0.95, stack=43.0)}                       # 7.5 % off and a single point: no span
    assert S._end3d_lookup("new", {}) is None


def test_one_curve_per_machine_is_merged_from_every_passport_and_interpolated(env):
    env["store"] = {
        "g2l40": _rec(0.94612, stack=40.0, when="2026-10-01T00:00:00Z"),
        "g2l65": _rec(0.96701, stack=65.0, when="2026-10-02T00:00:00Z"),
        "g1l170": _rec(0.98768, stack=170.0, when="2026-10-03T00:00:00Z"),
        "m1l200": _rec(0.98961, stack=200.0, when="2026-10-04T00:00:00Z"),
    }
    env["stack"] = 60.0                                                  # a customer L60: between 40 and 65, 8 % from 65
    r = S._end3d_lookup("new", {})
    assert r["inherited_stack_basis"] == "curve"
    assert r["k_flux"] == pytest.approx(0.94612 + (0.96701 - 0.94612) * 20.0 / 25.0)
    assert r["inherited_curve"] == {"span_mm": [40.0, 200.0], "points": 4}
    env["stack"] = 150.0
    assert S._end3d_lookup("new", {})["k_flux"] == pytest.approx(0.96701 + (0.98768 - 0.96701) * 85.0 / 105.0)
    env["stack"] = 66.0                                                  # within 5 % of a measured length: that point
    assert S._end3d_lookup("new", {})["k_flux"] == 0.96701
    env["stack"] = 300.0
    d = {}
    assert S._end3d_lookup("new", {}, diag=d) is None and "40-200" in d["note"]


def test_a_stale_multi_row_table_lends_only_its_reference_row(env):
    stale = {"40": 0.9461, "65": 0.9462, "170": 0.9463}              # flat in L: written before the stack-length fix
    assert S._end3d_rec_points(_rec(0.9461, stack=40.0, curve=stale, method=None)) == [(40.0, 0.9461)]
    r = _rec(0.9461, stack=40.0, curve=dict(stale))
    assert S._end3d_rec_points(r) == [(40.0, 0.9461), (65.0, 0.9462), (170.0, 0.9463)]


def test_the_newest_passport_wins_a_repeated_length_and_other_machines_are_ignored(env):
    env["store"] = {"a": _rec(0.90, stack=100.0, when="2026-08-01T00:00:00Z"),
                    "b": _rec(0.97, stack=100.0, when="2026-10-01T00:00:00Z"),
                    "x": _rec(0.50, stack=100.0, desc="12s/14p OD 40")}
    env["stack"] = 100.0
    assert S._end3d_lookup("new", {})["k_flux"] == 0.97


def test_a_non_monotonic_table_is_used_as_stored(env):
    env["store"] = {"c": _rec(0.97, stack=100.0, curve={"100": 0.97191, "110": 0.96955, "120": 0.97119})}
    env["stack"] = 111.0                                                 # a measured length (within 5 %): that point
    assert S._end3d_lookup("new", {})["k_flux"] == 0.96955
    env["store"] = {"c": _rec(0.97, stack=100.0, curve={"100": 0.97191, "130": 0.96955})}
    env["stack"] = 115.0                                                 # between two points: the straight line
    assert S._end3d_lookup("new", {})["k_flux"] == pytest.approx((0.97191 + 0.96955) / 2)


def test_unknown_stack_is_not_a_license_to_borrow(env):
    env["stack"] = 0.0
    env["store"] = {"old": _rec(0.95, stack=40.0)}
    d = {}
    assert S._end3d_lookup("new", {}, diag=d) is None and "stack length" in d["note"]


def test_stage_a_without_the_2d_check_is_marked_self_normalised():
    import inspect
    from motor_ai_sim.simulation.static3d import end_effect
    src = inspect.getsource(end_effect)
    assert "self-normalised (2-D check not run" in src


def test_adopt_entry_carries_the_basis():
    from motor_ai_sim.routes.static3d import adopt_entry
    sa = {"geometry_fingerprint": "f", "k_flux": 0.95, "k_flux_basis": "self-normalised (2-D check not run; k_flux = k_flux_self)",
          "machine": {"num_slots": 12, "num_poles": 14, "stator_od_mm": 30.0}}
    assert adopt_entry(sa)[1]["k_flux_basis"].startswith("self-normalised")
    sa.pop("k_flux_basis")
    assert "k_flux_basis" not in adopt_entry(sa)[1]


def test_charge_analytic_reads_the_scalar_factor_like_every_other_reader():
    from motor_ai_sim.charge_analytic import _k_end
    assert _k_end({"end3d": {"k_flux": 0.965, "k_flux_vs_L": {"12": 0.938, "40": 0.95}}, "L0_mm": 12}) == 0.965
    marked = {"k_flux_vs_L": {"12": 0.938, "40": 0.95}, "k_flux_vs_L_method": "own_mid_ratio"}
    assert _k_end({"end3d": dict(marked), "L0_mm": 12}) == 0.938
    # a multi-row table without the marker is stale: no scalar and no reference row -> the honest 2-D answer
    assert _k_end({"end3d": {"k_flux_vs_L": {"12": 0.938, "40": 0.95}}, "L0_mm": 12}) == 1.0
    assert _k_end({"end3d": {"k_flux": 7.0}}) == 1.0
    assert _k_end({}) == 1.0


def test_dry_list_old_and_new_on_a_tiny_catalog(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import end3d_inheritance_dry_list as D
    from motor_ai_sim.config import get_config
    g = get_config()["geometry"]
    ov = {"motor_length": 40.0}
    d = tmp_path / "dies" / "DIE"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text("geometry: {}\n", encoding="utf-8")
    (d / "L40.yaml").write_text("geometry_overrides: {motor_length: 40.0}\n", encoding="utf-8")
    desc = S._machine_desc(ov)
    st = tmp_path / "store.json"
    st.write_text(json.dumps({"x": _rec(0.9896, stack=200.0, desc=desc),
                              "y": _rec(0.946, stack=60.0, desc=desc, when="2026-10-01T00:00:00Z")}), encoding="utf-8")
    rep = D.run(tmp_path / "dies", st)
    assert rep["configs_checked"] == 1 and not rep["errors"], rep
    (row,) = rep["changed"]
    # the newest passport (60 mm) was the old pick; the 40 mm stack is within 5 % of nothing and below the span 60-200
    assert row["new"] is None and row["kind"] == "inherited -> no 3-D factor" and "60-200" in row["new_basis"]
    st.write_text(json.dumps({"x": _rec(0.9896, stack=200.0, desc=desc),
                              "y": _rec(0.946, stack=40.0, desc=desc, when="2026-10-01T00:00:00Z")}), encoding="utf-8")
    rep = D.run(tmp_path / "dies", st)
    assert rep["changed"] == []                       # L40 is a measured length: same factor as before
    # a customer L150 on the same two-point curve (40 .. 200): interpolated, not lent from the newest passport
    (d / "L150.yaml").write_text("geometry_overrides: {motor_length: 150.0}\n", encoding="utf-8")
    rep = D.run(tmp_path / "dies", st)
    (row,) = rep["changed"]
    assert row["config"] == "L150" and row["old"] == 0.946
    assert row["new"] == pytest.approx(0.946 + (0.9896 - 0.946) * 110.0 / 160.0) and row["new_basis"] == "curve"


def test_k_flux_basis_label_reads_the_verdict_not_the_usable_flag():
    from motor_ai_sim.simulation.static3d.end_effect import k_flux_basis_label as lab
    assert lab({"verdict": "PASS", "k_flux_usable": True}) == "2-D check passed"
    nc = lab({"verdict": "NOT CHECKED", "k_flux_usable": True})
    assert "not checked" in nc and "passed" not in nc
    assert lab({"verdict": "FAIL", "k_flux_usable": False}).startswith("2-D check failed")
    assert lab({"verdict": "INVALID", "k_flux_usable": False}).startswith("2-D check failed")
    assert lab({"k_flux_usable": True}) == "2-D check passed"           # no verdict: as before
    assert lab({"k_flux_usable": False}).startswith("2-D check failed")
    assert lab(None).startswith("self-normalised")
