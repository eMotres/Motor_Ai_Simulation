"""The catalogue 3-D passport: refuse only a sweep that clearly cannot finish inside the child's 7200 s (central
constants), fall back to the old 3-row sweep before sending it to a job.  Measured runs: payload-only us1 jobs."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from motor_ai_sim import end3d_cost as C

#: (n_tri, rows, n_stack, n_cap, measured wall s) of the seven measured runs
MEASURED = {
    "400 mm pilot, 1 quick row": (9484, 1, 4, 6, 3122),
    "400 mm run A, 3 rows": (9484, 3, 8, 10, 11491),
    "400 mm run B, 2 rows n_stack 12": (9484, 2, 12, 10, 12633),
    "40 mm full, 3 rows": (10277, 3, 8, 10, 11569),
    "40 mm proof, 1 quick row": (10277, 1, 4, 6, 1730),
    "80 mm, 3 rows (12 mm ref)": (7442, 3, 8, 10, 6668),
    "80 mm, 3 rows (8 mm ref)": (7452, 3, 8, 10, 5885),
}


def test_the_central_and_worst_rule():
    e = C.estimate_stage_a(9484, 4)
    assert e["layers"] == 12 and e["dofs"] == pytest.approx(1.4 * 3 * 9484 * 12, abs=1)
    assert e["seconds"] == pytest.approx(150 + 1.4e-4 * e["dofs"] * (45 + 18 * 3))
    assert e["seconds_worst"] == pytest.approx(300 + 2.1e-4 * e["dofs_worst"] * (46 * 2 + 25 * 2))
    assert e["seconds_worst"] > e["seconds"] and e["budget_s"] == 7200


@pytest.mark.parametrize("name", sorted(MEASURED))
def test_central_classifies_every_measured_run_right(name):
    tri, rows, ns, nc, measured = MEASURED[name]
    assert C.estimate_stage_a(tri, rows, ns, nc)["fits"] == (measured <= 7200), name


def test_false_refusals_and_accepts_on_the_measured_runs():
    central = [(C.estimate_stage_a(*v[:4])["fits"], v[4] <= 7200) for v in MEASURED.values()]
    worst = [(C.estimate_stage_a(*v[:4])["seconds_worst"] <= 7200, v[4] <= 7200) for v in MEASURED.values()]
    count = lambda xs: (sum(1 for p, m in xs if m and not p), sum(1 for p, m in xs if p and not m))  # noqa: E731
    assert count(central) == (0, 0)                                       # no false refusal, no false accept
    assert count(worst) == (2, 0)                                         # the worst case refuses the two 80 mm runs that fit


@pytest.mark.parametrize("rows, tri_fit", [(4, 10092), (5, 8539), (3, 12335)])
def test_the_central_boundary(rows, tri_fit):
    assert C.estimate_stage_a(tri_fit, rows)["fits"] is True
    assert C.estimate_stage_a(tri_fit + 2, rows)["fits"] is False


@pytest.mark.parametrize("tri, plan", [(7442, "auto"), (9484, "auto"), (10277, "three_row"), (13000, "job")])
def test_the_catalogue_plan(tri, plan):
    p = C.plan_catalogue(tri, [1.0, 1.5, 2.0, 0.75])
    assert p["plan"] == plan
    assert p["l_factors"] == ([0.75, 1.0, 1.5] if plan == "three_row" else [1.0, 1.5, 2.0, 0.75])
    assert set(p["estimates"]) == ({"auto"} if plan == "auto" else {"auto", "three_row"})


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
    assert e["plan"] == "auto" and e["fits"] is True                     # the 80 mm machine stays automatic


def test_child_answers_are_parsed():
    est = C.plan_catalogue(13000, [1.0, 1.5, 2.0, 0.75])
    assert C.parse_child_output("noise\nPASSPORT3D_JOB:" + json.dumps(est), 0) == ("job", est)
    assert C.parse_child_output('PASSPORT3D:{"k_flux": 0.95}', 0) == ("ok", {"k_flux": 0.95})
    assert C.parse_child_output('PASSPORT3D:{"k_flux": 0.95}', 1) == ("fail", None)
    assert C.parse_child_output("", 0) == ("fail", None)


def test_the_note_path():
    est = C.plan_catalogue(13000, [1.0, 1.5, 2.0, 0.75])
    f = C.job_note_fields(est)
    assert f["end3d"] is None and f["end3d_note"].startswith("3-D passport: run as a job")
    assert "the web route cannot" in f["end3d_note"]
    assert f["end3d_estimate"]["plan"] == "job" and f["end3d_estimate"]["seconds_worst"] > f["end3d_estimate"]["seconds"]


def test_the_catalog_child_runs_the_preflight_first_and_uses_its_plan():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/motor_ai_sim/routes/catalog.py").read_text(encoding="utf-8")
    i_pre, i_run = src.index("est = preflight(geo_override=g"), src.index("p = run_stage_a(geo_override=g")
    assert i_pre < i_run and "if est['plan'] == 'job':" in src and "l_factors=tuple(est['l_factors'])" in src
    assert "raise _Stage3DAsJob(_ans)" in src and 'result["passport"].update(job_note_fields(est))' in src
    assert "timeout=7200" in src


def test_the_job_script_estimate_only(monkeypatch, capsys, tmp_path):
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "stage_a_job.py"
    assert "root-only" in p.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("saj", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    mj = tmp_path / "m.json"
    mj.write_text(json.dumps({"geometry": {"motor_length": 12}, "materials": {}}), encoding="utf-8")
    monkeypatch.setattr(C, "preflight", lambda **kw: {"fits": True, "seconds": 1.0, **{k: kw[k] for k in ("n_stack", "n_cap")}})
    assert m.main(["--machine-json", str(mj), "--out", str(tmp_path / "o.json"), "--estimate-only",
                   "--fidelity", "default"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ESTIMATE ") and '"n_stack": 8' in out and not (tmp_path / "o.json").exists()
