"""The catalogue 3-D passport is not started when it cannot finish inside the child's 7200 s; the estimate rule."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from motor_ai_sim import end3d_cost as C


def test_the_rule():
    e = C.estimate_stage_a(9484, 1)                                       # the 400 mm pilot: quick, one row
    assert e["layers"] == 12 and e["dofs"] == pytest.approx(1.4 * 3 * 9484 * 12, abs=1)
    assert e["seconds"] == pytest.approx(300 + 2.1e-4 * e["dofs"] * 46)
    e3 = C.estimate_stage_a(9484, 3, 8, 10)
    assert e3["seconds"] == pytest.approx(300 + 2.1e-4 * e3["dofs"] * (46 * 2 + 25))
    assert e3["budget_s"] == pytest.approx(0.8 * 7200)


@pytest.mark.parametrize("tri, rows, ns, nc, measured_s", [
    (9484, 1, 4, 6, 3122), (9484, 3, 8, 10, 11491), (9484, 2, 12, 10, 12633), (10277, 3, 8, 10, 11569),
    (10277, 1, 4, 6, 1730), (7442, 3, 8, 10, 6668), (7452, 3, 8, 10, 5885)])
def test_the_rule_over_predicts_every_measured_run(tri, rows, ns, nc, measured_s):
    assert C.estimate_stage_a(tri, rows, ns, nc)["seconds"] >= 1.45 * measured_s


@pytest.mark.parametrize("rows, tri_fit", [(4, 3632), (5, 3089), (1, 11214)])
def test_the_boundary(rows, tri_fit):
    assert C.estimate_stage_a(tri_fit, rows)["fits"] is True
    assert C.estimate_stage_a(tri_fit + 2, rows)["fits"] is False


def test_bad_input_is_refused():
    with pytest.raises(ValueError):
        C.estimate_stage_a(0, 3)


def test_preflight_builds_only_the_cross_section(monkeypatch):
    from motor_ai_sim.simulation.static3d import motor_geometry as MG
    from motor_ai_sim.simulation.static3d import motor_mesh as MM
    sec = SimpleNamespace(stack_mm=12.0, mid_r_mm=24.8, pole_pairs=14)
    monkeypatch.setattr(MG, "load_motor_section", lambda geo_override=None: sec)
    monkeypatch.setattr(MM, "physical_mesh_sizes", lambda s: {"h_gap": 0.1, "h_solid": 0.3})
    monkeypatch.setattr(MM, "build_section_mesh_2d", lambda *a, **kw: SimpleNamespace(n_tri=7442))
    from motor_ai_sim.simulation.static3d import end_effect as EE
    monkeypatch.setattr(EE, "solve_sector", lambda *a, **kw: pytest.fail("preflight solved"))
    e = C.preflight(geo_override={}, n_stack=4, n_cap=6, auto_extra=(0.75,))
    assert e["l_factors"] == [1.0, 1.5, 2.0, 0.75] and e["rows"] == 4 and e["n_tri"] == 7442
    assert e["fits"] is False                                             # the 80 mm machine goes to a job


def test_child_answers_are_parsed():
    est = C.estimate_stage_a(7442, 4)
    assert C.parse_child_output("noise\nPASSPORT3D_JOB:" + json.dumps(est), 0) == ("job", est)
    assert C.parse_child_output('PASSPORT3D:{"k_flux": 0.95}', 0) == ("ok", {"k_flux": 0.95})
    assert C.parse_child_output('PASSPORT3D:{"k_flux": 0.95}', 1) == ("fail", None)
    assert C.parse_child_output("", 0) == ("fail", None)


def test_the_note_path():
    est = C.estimate_stage_a(7442, 4)
    f = C.job_note_fields(dict(est, l_factors=[1.0, 1.5, 2.0, 0.75]))
    assert f["end3d"] is None and f["end3d_note"] == "3-D passport: run as a job"
    assert f["end3d_estimate"]["seconds"] == est["seconds"] and f["end3d_estimate"]["rows"] == 4


def test_the_catalog_child_runs_the_preflight_first_and_the_route_takes_the_job_answer():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/motor_ai_sim/routes/catalog.py").read_text(encoding="utf-8")
    i_pre, i_run = src.index("est = preflight(geo_override=g"), src.index("p = run_stage_a(geo_override=g")
    assert i_pre < i_run and "PASSPORT3D_JOB:" in src and "raise _Stage3DAsJob(_ans)" in src
    assert 'result["passport"].update(job_note_fields(est))' in src and "timeout=7200" in src


def test_the_job_script_estimate_only(monkeypatch, capsys, tmp_path):
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "stage_a_job.py"
    spec = importlib.util.spec_from_file_location("saj", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    mj = tmp_path / "m.json"
    mj.write_text(json.dumps({"geometry": {"motor_length": 12}, "materials": {}}), encoding="utf-8")
    monkeypatch.setattr(C, "preflight", lambda **kw: {"fits": False, "seconds": 1.0, **{k: kw[k] for k in ("n_stack", "n_cap")}})
    assert m.main(["--machine-json", str(mj), "--out", str(tmp_path / "o.json"), "--estimate-only",
                   "--fidelity", "default"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ESTIMATE ") and '"n_stack": 8' in out and not (tmp_path / "o.json").exists()
