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
    from motor_ai_sim.simulation.static3d.end_effect import k_flux_basis_label
    assert k_flux_basis_label(None) == "self-normalised (2-D check not run; k_flux = k_flux_self)"


def test_adopt_entry_carries_the_basis():
    from motor_ai_sim.routes.static3d import adopt_entry
    sa = {"geometry_fingerprint": "f", "k_flux": 0.95, "k_flux_basis": "self-normalised (2-D check not run; k_flux = k_flux_self)",
          "machine": {"num_slots": 12, "num_poles": 14, "stator_od_mm": 30.0}}
    assert adopt_entry(sa)[1]["k_flux_basis"].startswith("self-normalised")
    sa.pop("k_flux_basis")
    assert "k_flux_basis" not in adopt_entry(sa)[1]


def test_charge_analytic_k_end_follows_the_passport_length():
    from motor_ai_sim.charge_analytic import k_end_info, _k_end
    from motor_ai_sim.end3d_factors import STALE_KL_FLAG
    # a CONSISTENT marked passport: the scalar is the table's row at its own stack_mm
    tbl = {"12": 0.938, "40": 0.965, "80": 0.975}
    e3 = {"k_flux": 0.965, "stack_mm": 40, "k_flux_vs_L": tbl, "k_flux_vs_L_method": "own_mid_ratio"}
    assert k_end_info({"end3d": e3, "L0_mm": 40}) == (0.965, None)             # at its own length: the scalar
    assert k_end_info({"end3d": e3, "L0_mm": 41}) == (0.965, None)             # within 5 %
    k, flag = k_end_info({"end3d": e3, "L0_mm": 12})                           # another length: the marked table
    assert k == 0.938 and flag is None
    k, _ = k_end_info({"end3d": e3, "L0_mm": 60})
    assert k == pytest.approx(0.97)
    assert _k_end({"end3d": e3, "L0_mm": 12}) == 0.938
    # a stale table (no marker): scalar with the flag; its rows are not read
    stale = {"k_flux": 0.965, "stack_mm": 40, "k_flux_vs_L": tbl}
    assert k_end_info({"end3d": stale, "L0_mm": 12}) == (0.965, STALE_KL_FLAG)
    # stale, no scalar, no stack_mm: not a silent 1.0 - the flag says so
    assert k_end_info({"end3d": {"k_flux_vs_L": tbl}, "L0_mm": 12}) == (1.0, STALE_KL_FLAG)
    # no measurement at all: the honest 2-D answer, no flag
    assert k_end_info({}) == (1.0, None)
    assert k_end_info({"end3d": {"k_flux": 7.0}}) == (1.0, None)


def test_machine_point_flags_a_refused_stale_table():
    from motor_ai_sim.charge_analytic import machine_point
    pp = {"I0_A": 10.0, "rpm0": 1000.0, "R0_ohm": 0.1, "T0_Nm": 1.0, "Vemf0_peak_V": 10.0, "L0_mm": 12,
          "end3d": {"k_flux_vs_L": {"12": 0.938, "40": 0.95}}}
    assert machine_point(pp, 5.0, 1000.0).get("k_end_stale") == 1.0
    pp["end3d"]["k_flux_vs_L_method"] = "own_mid_ratio"
    assert "k_end_stale" not in machine_point(pp, 5.0, 1000.0)


def test_a_stale_table_without_stack_mm_lends_one_row_or_none():
    stale = {"40": 0.9461, "65": 0.9462, "170": 0.9463}
    assert S._end3d_rec_points({"k_flux": 0.9461, "k_flux_vs_L": stale}) == [(40.0, 0.9461)]   # the row equal to the scalar
    assert S._end3d_rec_points({"k_flux": 0.90, "k_flux_vs_L": stale}) == []                  # none identified


def test_the_torque_factor_of_another_length_is_not_lent(env, monkeypatch):
    from motor_ai_sim import end3d_factors as F
    monkeypatch.setattr(F, "_records", lambda: [
        {"fingerprint": "g200", "k_T": 0.91, "slots": 24, "poles": 28, "od_mm": 200.0, "stack_mm": 200.0, "source": "Stage D"}])
    env["store"] = {"g200": _rec(0.9896, stack=200.0), "g40": _rec(0.946, stack=40.0)}
    env["stack"] = 120.0                                  # between the two measured lengths: the curve
    r = S._end3d_lookup("new", {})
    assert r["inherited_stack_basis"] == "curve"
    assert r.get("k_T") is None and r["torque_basis"] != "measured"       # k_T of the 200 mm stack is not lent
    assert F.torque_factor(r)[0] == pytest.approx(r["k_flux"])
    # at the length the k_T was measured at (within 5 %): lent, flagged inherited
    env["stack"] = 201.0
    r = S._end3d_lookup("new", {})
    assert r["k_T"] == 0.91 and r["k_T_inherited"] is True
    # the measured k_T of an exact passport is untouched (no stack rule for the machine's own passport)
    env["store"]["new"] = _rec(0.95, stack=60.0)
    monkeypatch.setattr(F, "_records", lambda: [
        {"fingerprint": "new", "k_T": 0.9, "slots": 24, "poles": 28, "od_mm": 200.0, "stack_mm": 60.0, "source": "Stage D"}])
    r = S._end3d_lookup("new", {})
    assert r["k_T"] == 0.9 and not r.get("k_T_inherited")


def test_the_lent_note_names_what_is_not_compared(env):
    env["store"] = {"a": _rec(0.946, stack=40.0), "b": _rec(0.99, stack=200.0)}
    env["stack"] = 120.0
    note = S._end3d_lookup("new", {})["inherited_note"]
    for w in ("magnet", "air gap", "materials", "temperature", "die"):
        assert w in note


def test_the_failed_label_says_what_is_applied():
    from motor_ai_sim.simulation.static3d.end_effect import k_flux_basis_label as lab
    assert "applied as stored" in lab({"verdict": "FAIL"}) and "use k_flux_self" not in lab({"verdict": "FAIL"})


def test_family_payload_says_why_a_passport_was_not_lent(tmp_path, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / "TD").mkdir(parents=True)
    (root / "TD" / "die.yaml").write_text(yaml.safe_dump({
        "name": "TD", "locked": False, "created": "2026-09-22T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 200.0, "motor_length": 300.0}}), encoding="utf-8")
    (root / "TD" / "C1.yaml").write_text(yaml.safe_dump({
        "name": "C1", "die": "TD", "role": "motor", "geometry_overrides": {"motor_length": 300.0},
        "winding": {"connection": "star"},
        "duties": [{"name": "d", "mode": "motor", "current_arms": 10.0, "rpm": 100.0,
                    "summary": {"T_em_avg_Nm": 5.0, "end3d": None}}]}), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(end3d_store, "load", lambda: {"x": _rec(0.95, stack=40.0), "y": _rec(0.99, stack=200.0)})
    monkeypatch.setattr(S, "_machine_desc", lambda geo=None: DESC)
    uf = tmp_path / "users.json"
    monkeypatch.setattr(U, "_USERS_FILE", uf)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user("admin@example.com", "password-admin", role="admin", name="Admin")
    h = {"Authorization": f"Bearer {U.issue_token('admin@example.com')}"}
    r = TestClient(app).get("/api/family/payload/TD/C1", headers=h, params={"duty": "d"})
    assert r.status_code == 200, r.text
    summ = r.json()["duty"]["summary"]
    assert summ["end3d"] is None and "outside the measured span" in summ["end3d_note"]


def _base_src_with_legacy_lookup(tmp_path):
    """A 'base' source tree: the real package by symlinks, except routes/simulation.py whose lookup picks the newest passport
    of the machine at any length (the pre-#764 rule)."""
    import os
    from pathlib import Path
    src = Path(S.__file__).resolve().parents[2]                      # .../src
    root = tmp_path / "base_src"
    pkg = root / "motor_ai_sim"
    (pkg / "routes").mkdir(parents=True)
    for e in (src / "motor_ai_sim").iterdir():
        if e.name != "routes":
            os.symlink(e, pkg / e.name)
    for e in (src / "motor_ai_sim" / "routes").iterdir():
        if e.name != "simulation.py":
            os.symlink(e, pkg / "routes" / e.name)
    txt = (src / "motor_ai_sim" / "routes" / "simulation.py").read_text(encoding="utf-8")
    marker = "        stack = _geo_stack_mm(geo)\n        _when = "
    assert marker in txt
    txt = txt.replace(marker, "        stack = _geo_stack_mm(geo)\n"
                      "        _nk, _nv = max(cands, key=lambda kv: str(kv[1].get('adopted_utc') or kv[1].get('generated_utc') or ''))\n"
                      "        stack = float(_nv.get('stack_mm') or stack)\n        _when = ", 1)
    (pkg / "routes" / "simulation.py").write_text(txt, encoding="utf-8")
    return root


def test_dry_list_base_lookup_against_pr_lookup_on_a_tiny_catalog(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    scr = Path(__file__).resolve().parents[1] / "scripts" / "end3d_inheritance_dry_list.py"
    # a real die the way the catalog stores it: explicit counts, no segment keys
    d = tmp_path / "dies" / "DIE"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text("geometry: {num_slots: 24, num_poles: 28, stator_diameter: 200.0}\n", encoding="utf-8")
    for L in (40, 150):
        (d / f"L{L}.yaml").write_text("geometry_overrides: {motor_length: %s.0}\n" % L, encoding="utf-8")
    from motor_ai_sim.config import get_config
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    desc = S._machine_desc(merge_geo_override(dict(get_config()["geometry"]), {"num_slots": 24, "num_poles": 28, "stator_diameter": 200.0}))
    assert desc == "24s/28p OD 200", desc
    st = tmp_path / "store.json"
    st.write_text(json.dumps({"x": _rec(0.9896, stack=200.0, desc=desc),
                              "y": _rec(0.946, stack=60.0, desc=desc, when="2026-10-01T00:00:00Z")}), encoding="utf-8")
    base = _base_src_with_legacy_lookup(tmp_path)
    out = tmp_path / "o.json"
    cp = subprocess.run([sys.executable, str(scr), "--root", str(tmp_path / "dies"), "--store", str(st),
                         "--base-src", str(base), "--pr-src", str(Path(S.__file__).resolve().parents[2]),
                         "--json", str(out)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr[-2000:]
    rep = json.loads(out.read_text())
    rows = {r["config"]: r for r in rep["rows"]}
    assert rep["configs_checked"] == 2 and not rep["errors"], rep
    # L40: the old pick was the newest passport (60 mm, 0.946); 40 mm is below the span 60-200: no factor now
    assert rows["L40"]["changed"] and rows["L40"]["k_flux_old"] == 0.946 and rows["L40"]["k_flux_new"] is None
    # L150: interpolated on the 60..200 curve, not lent from the 60 mm passport
    assert rows["L150"]["changed"] and rows["L150"]["k_flux_old"] == 0.946
    assert rows["L150"]["k_flux_new"] == pytest.approx(0.946 + (0.9896 - 0.946) * 90.0 / 140.0)
    assert rows["L150"]["new_basis"] == "curve" and rows["L150"]["length_ratio"] is not None
    assert "passports in the store (2)" in cp.stdout and "L150" in cp.stdout


def test_dry_list_compare_lists_a_k_T_cut_and_honours_the_threshold_and_all():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import end3d_inheritance_dry_list as D
    def row(kf, kt):
        return {"die": "D", "config": "C", "stack_mm": 60.0, "own": False, "machine": "m", "k_flux": kf, "k_T": kt,
                "from_key": "k", "from_stack_mm": 40.0, "basis": "curve", "note": None}
    same = D.compare({"rows": [row(0.95, 0.9)]}, {"rows": [row(0.95, 0.9)]}, 0.0005)
    assert not same[0]["changed"]
    cut = D.compare({"rows": [row(0.95, 0.9)]}, {"rows": [row(0.95, None)]}, 0.0005)     # k_T no longer lent
    assert cut[0]["changed"] and cut[0]["k_T_new"] is None
    small = D.compare({"rows": [row(0.95, 0.9)]}, {"rows": [row(0.9503, 0.9)]}, 0.0005)
    assert not small[0]["changed"]
    big = D.compare({"rows": [row(0.95, 0.9)]}, {"rows": [row(0.9503, 0.9)]}, 0.0001)
    assert big[0]["changed"] and big[0]["k_flux_diff"] == pytest.approx(0.0003)


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
