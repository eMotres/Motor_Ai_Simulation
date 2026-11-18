"""A passport of an earlier geometry is lent only at the same stack length; the other fixes of the 3-D factor lookup."""
from __future__ import annotations

import pytest

from motor_ai_sim import end3d_store
from motor_ai_sim.routes import simulation as S

DESC = "24s/28p OD 200"


def _rec(k, stack=None, curve=None, when="2026-09-01T00:00:00Z", desc=DESC):
    return {"k_flux": k, "machine": desc, "adopted_utc": when, "stack_mm": stack, "k_flux_vs_L": curve}


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


def test_long_stack_passport_is_not_lent_to_a_short_stack(env):
    env["store"] = {"old": _rec(0.9896, stack=200.0)}
    d = {}
    assert S._end3d_lookup("new", {}, diag=d) is None
    assert "no 3-D factor" in d["note"] and "200" in d["note"]


def test_same_stack_within_five_percent_is_lent(env):
    env["store"] = {"old": _rec(0.95, stack=41.5)}
    r = S._end3d_lookup("new", {})
    assert r["inherited"] and r["k_flux"] == 0.95 and r["inherited_stack_basis"] == "stack_mm"
    env["store"] = {"old": _rec(0.95, stack=43.0)}   # 7.5 % off
    assert S._end3d_lookup("new", {}) is None


def test_own_length_table_entry_wins_over_the_scalar(env):
    env["store"] = {"old": _rec(0.9896, stack=200.0, curve={"12": 0.938, "40": 0.946, "65": 0.967, "200": 0.9896})}
    r = S._end3d_lookup("new", {})
    assert r["k_flux"] == pytest.approx(0.946) and r["k_flux_passport"] == 0.9896
    env["stack"] = 52.5
    assert S._end3d_lookup("new", {})["k_flux"] == pytest.approx(0.946 + (0.967 - 0.946) * 12.5 / 25.0)


def test_table_does_not_extrapolate_beyond_five_percent(env):
    env["store"] = {"old": _rec(0.95, curve={"65": 0.967, "200": 0.9896})}
    env["stack"] = 40.0
    assert S._end3d_lookup("new", {}) is None


def test_newest_usable_passport_wins_and_unfit_ones_are_skipped(env):
    env["store"] = {"a": _rec(0.97, stack=40.0, when="2026-08-01T00:00:00Z"),
                    "b": _rec(0.99, stack=200.0, when="2026-10-01T00:00:00Z")}
    assert S._end3d_lookup("new", {})["inherited_from"] == "a"


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
    assert _k_end({"end3d": {"k_flux_vs_L": {"12": 0.938, "40": 0.95}}, "L0_mm": 12}) == 0.938
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
    st.write_text(json.dumps({"x": _rec(0.9896, stack=200.0, desc=desc)}), encoding="utf-8")
    rep = D.run(tmp_path / "dies", st)
    assert rep["configs_checked"] == 1 and not rep["errors"], rep
    (row,) = rep["changed"]
    assert row["old"] == 0.9896 and row["new"] is None and row["kind"] == "inherited -> no 3-D factor"
