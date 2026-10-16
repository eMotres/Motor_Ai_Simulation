"""No-FEM tests for the frozen angular benchmark and its screening analyzer."""
import json
from pathlib import Path

import pytest

from scripts import bench_angular_sampling as bench


def _record(name, mean, pp, ripple, loss, frames):
    return {
        "scalars": {"T_avg_Nm": mean, "T_ripple_raw_pct": ripple,
                    "n_steps": frames},
        "raw": {"T_em_maxwell_Nm": [0.0, pp] + [0.0] * (frames - 2),
                "P_loss_total_avg_W": loss,
                "P_fe_W": [loss / frames] * frames},
    }


def _records():
    values = {
        "A_loaded_optimization": (1.002, 0.1005, 4.01, 20.05, 36),
        "A_loaded_standard": (1.0, 0.1, 4.0, 20.0, 72),
        "B_loaded_optimization": (0.901, 0.1206, 5.01, 21.05, 36),
        "B_loaded_standard": (0.9, 0.12, 5.0, 21.0, 72),
        "A_noload_optimization": (0.0, 0.01005, None, 5.05, 36),
        "A_noload_standard": (0.0, 0.01, None, 5.0, 72),
    }
    return {name: _record(name, *values[name]) for name in values}


def test_six_cases_and_explicit_three_vs_six_cycle_counts():
    assert len(bench.CASES) == 6
    assert {case.expected_steps for case in bench.CASES
            if case.purpose == "optimization"} == {36}
    assert {case.expected_steps for case in bench.CASES
            if case.purpose == "standard"} == {72}
    assert {case.magnet_fill_up for case in bench.CASES} == {0.4, 0.3}
    assert {case.current_a for case in bench.CASES} == {43.8, 0.0}
    for case in bench.CASES:
        args = bench._solver_args(case, {"magnet_fill_up": case.magnet_fill_up})
        assert args["n_steps_per_period"] == 12
        assert args["sampling_purpose"] == case.purpose
        assert args["eddy"] is args["demag"] is args["rotor_eddy"] is False
        assert args["torque_filter"] is False


def test_analyzer_uses_full_raw_maxwell_series_and_labeled_gates():
    report = bench.analyze_records(_records())
    assert report["screening_pass"] is True
    assert report["status"] == "coarse_screening_only"
    assert report["comparisons"]["A_loaded"]["optimization_frames"] == 36
    assert report["comparisons"]["A_loaded"]["standard_frames"] == 72
    assert report["comparisons"]["A_loaded"]["screening_gates"][
        "mean_within_0_5_pct"] is True
    assert report["comparisons"]["A_loaded"]["losses"][
        "P_loss_total_avg_W"]["optimization"] == 20.05
    assert report["comparisons"]["A_noload"]["screening_gates"][
        "mean_within_0_5_pct"] is None
    assert all(row["unchanged"] for row in report["candidate_ordering"].values())


def test_analyzer_detects_aliased_peak_and_ranking_flip():
    records = _records()
    records["A_loaded_optimization"]["raw"]["T_em_maxwell_Nm"][35] = 0.2
    records["A_loaded_optimization"]["scalars"]["T_avg_Nm"] = 0.85
    report = bench.analyze_records(records)
    assert report["screening_pass"] is False
    assert report["comparisons"]["A_loaded"]["screening_gates"][
        "maxwell_pp_within_1_pct"] is False
    assert report["candidate_ordering"]["mean_torque_higher"][
        "unchanged"] is False


def test_raw_result_capture_retains_every_torque_and_loss_sample():
    result = {"n_steps": 3, "time_s": [0.0, 0.1, 0.2],
              "T_em_raw_Nm": [1, 2, 3], "T_em_maxwell_Nm": [4, 5, 6],
              "T_harm_order": [1, 2], "T_harm_amp": [0.1, 0.2],
              "P_fe_W": [7, 8, 9], "P_fe_terms": {"stator": {"eddy_W": 2}},
              "P_loss_total_avg_W": 10, "field": {"huge": "omitted"}}
    kept = bench._saved_result(result)
    assert kept["T_em_maxwell_Nm"] == [4, 5, 6]
    assert kept["P_fe_W"] == [7, 8, 9]
    assert kept["T_harm_amp"] == [0.1, 0.2]
    assert kept["P_fe_terms"]["stator"]["eddy_W"] == 2
    assert "field" not in kept
    result["T_em_maxwell_Nm"] = [4, 5]
    with pytest.raises(ValueError, match="truncated"):
        bench._saved_result(result)


def test_inclusive_timing_remainder_is_not_labeled_as_a_stage():
    record = bench.timing_summary(10.0, {"outer": 8.0, "nested": 5.0})
    assert record["wall_minus_sum_inclusive_s"] == -3.0
    assert record["inclusive_timers_are_not_a_wall_partition"] is True
    assert "unassigned_wall_s" not in record


def test_snapshot_is_private_and_has_no_resume_state(tmp_path):
    repo = tmp_path / "repo"
    fixture = tmp_path / "fixture"
    (repo / "src").mkdir(parents=True)
    (repo / "config").mkdir()
    fixture.mkdir()
    (repo / "src" / "fake.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "config" / "materials_library.yaml").write_text("steel: {}\n")
    (fixture / "motor_config_frozen.yaml").write_text("geometry: {}\n")
    (fixture / "geo_frozen.json").write_text(json.dumps({
        "num_slots": 12, "num_poles": 14, "stator_diameter": 40,
        "magnet_fill_up": 0.4}))
    (fixture / ".jobs.json").write_text("do not copy")
    output = tmp_path / "private"
    manifest = bench.snapshot_inputs(output, repo=repo, fixture=fixture)
    assert bench._verify_snapshot(output) == manifest
    assert not (output / "snapshot" / ".jobs.json").exists()
    assert (output / "snapshot" / "src" / "fake.py").is_file()
    env = bench._worker_environment(output, bench.CASE_BY_NAME[
        "A_loaded_optimization"])
    assert env["MOTOR_AI_SIM_CONFIG"].startswith(str(output))
    assert env["PYTHONPATH"] == str(output / "snapshot" / "src")
    assert all(env[key] == "1" for key in (
        "MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "GMSH_NUM_THREADS"))
    assert not (output / "cases" / "A_loaded_optimization" / "workspace"
                / ".jobs.json").exists()
    with pytest.raises(ValueError, match="live config"):
        bench.snapshot_inputs(repo / "config" / "profile", repo=repo,
                              fixture=fixture)
    with pytest.raises(FileExistsError, match="empty"):
        bench.snapshot_inputs(output, repo=repo, fixture=fixture)


def test_cli_refuses_more_than_five_minutes_without_starting_fem(tmp_path):
    with pytest.raises(ValueError, match="300"):
        bench.main(["run-all", "--output-dir", str(tmp_path / "out"),
                    "--timeout-s", "301"])
    assert not (tmp_path / "out").exists()
