"""Frozen, private six-case P2 angular-sampling benchmark; no API or live config.

Only ``run-all`` starts FEM. ``analyze`` reads saved JSON. The worker is an
internal child process with one case, one thread, and a 300 s parent timeout.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scratch_perf"
RESUME_NAMES = (".sweep_journal.json", ".jobs.json", ".last_descent.json")


@dataclass(frozen=True)
class Case:
    name: str
    magnet_fill_up: float
    current_a: float
    purpose: str
    expected_steps: int


CASES = tuple(
    Case(f"{design}_{load}_{purpose}", fill, current, purpose,
         36 if purpose == "optimization" else 72)
    for design, fill, load, current in (
        ("A", 0.4, "loaded", 43.8),
        ("B", 0.3, "loaded", 43.8),
        ("A", 0.4, "noload", 0.0),
    )
    for purpose in ("optimization", "standard")
)
CASE_BY_NAME = {case.name: case for case in CASES}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def geometry_sha256(geometry: dict[str, Any]) -> str:
    raw = json.dumps(geometry, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False,
                               ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not __import__("math").isfinite(value):
            raise ValueError("non-finite solver result cannot be serialized honestly")
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "item"):
        return _jsonable(value.item())
    raise TypeError(f"unsupported result type: {type(value).__name__}")


def snapshot_inputs(output: Path, *, repo: Path = ROOT,
                    fixture: Path = FIXTURE) -> dict[str, Any]:
    """Copy only frozen inputs and source, never the live config directory."""
    output = output.resolve()
    live_config = (repo / "config").resolve()
    if output == live_config or live_config in output.parents:
        raise ValueError("benchmark output may not be under live config/")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"private output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "snapshot"
    snapshot.mkdir()
    source_before = sha256_tree(repo / "src")
    shutil.copytree(repo / "src", snapshot / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    source_after = sha256_tree(repo / "src")
    source_copy = sha256_tree(snapshot / "src")
    if source_before != source_after or source_before != source_copy:
        raise RuntimeError("source changed while freezing benchmark inputs")
    files = {
        "config": fixture / "motor_config_frozen.yaml",
        "geometry": fixture / "geo_frozen.json",
        "materials": repo / "config" / "materials_library.yaml",
    }
    copied = {}
    for name, source in files.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        target = snapshot / source.name
        before = sha256_file(source)
        shutil.copy2(source, target)
        if sha256_file(source) != before or sha256_file(target) != before:
            raise RuntimeError(f"{name} changed while freezing inputs")
        copied[name] = {"file": target.name, "sha256": before}
    # The snapshot is assembled from three named files, not a config tree.
    # Still remove these if a previous/foreign preparer put one there.
    for name in RESUME_NAMES:
        (snapshot / name).unlink(missing_ok=True)
    geo = json.loads((snapshot / copied["geometry"]["file"]).read_text(
        encoding="utf-8"))
    if (int(geo["num_slots"]), int(geo["num_poles"]),
            float(geo["stator_diameter"]), float(geo["magnet_fill_up"])) != (
            12, 14, 40.0, 0.4):
        raise ValueError("frozen fixture is no longer the 40 mm 12s14p base A")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=repo, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    manifest = {
        "git_head": head,
        "source_tree_sha256": source_copy,
        "runner_sha256": sha256_file(Path(__file__)),
        "inputs": copied,
        "base_geometry_sha256": geometry_sha256(geo),
        "cases": [case.__dict__ for case in CASES],
        "resume_state_copied": False,
    }
    _write_json(output / "manifest.json", manifest)
    return manifest


def _verify_snapshot(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    snapshot = output / "snapshot"
    if sha256_file(Path(__file__)) != manifest["runner_sha256"]:
        raise RuntimeError("benchmark runner changed after snapshot")
    if sha256_tree(snapshot / "src") != manifest["source_tree_sha256"]:
        raise RuntimeError("frozen source hash changed")
    for record in manifest["inputs"].values():
        if sha256_file(snapshot / record["file"]) != record["sha256"]:
            raise RuntimeError("frozen input hash changed")
    return manifest


def _case_geometry(output: Path, case: Case) -> dict[str, Any]:
    geometry = json.loads((output / "snapshot" / "geo_frozen.json").read_text(
        encoding="utf-8"))
    geometry["magnet_fill_up"] = case.magnet_fill_up
    return geometry


def _worker_environment(output: Path, case: Case) -> dict[str, str]:
    workspace = output / "cases" / case.name / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("motor_config_frozen.yaml", "materials_library.yaml"):
        shutil.copy2(output / "snapshot" / name, workspace / name)
    for name in RESUME_NAMES:
        (workspace / name).unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SB_") and k not in (
               "WORKSPACES_ROOT", "SHARED_ROOT", "PUBLISHED_ROOT")}
    env.update({
        "MOTOR_AI_SIM_CONFIG": str(workspace / "motor_config_frozen.yaml"),
        "MKL_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
        "GMSH_NUM_THREADS": "1",
        "SB_NO_WARM_CACHE": "1",
        "PYTHONPATH": str(output / "snapshot" / "src"),
    })
    return env


def _timer_wrappers():
    """Install non-mutating, inclusive timing around existing public methods."""
    from collections import defaultdict
    from motor_ai_sim import cadquery_geometry
    from motor_ai_sim.simulation import fem_solver_2d, p2_nonlinear, p2_projection

    elapsed: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    def wrap(cls, method: str, name: str):
        original = getattr(cls, method)

        @functools.wraps(original)
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed[name] += time.perf_counter() - start
                counts[name] += 1

        setattr(cls, method, measured)

    wrap(cadquery_geometry.CadQueryMotor, "get_2d_polygons", "cad_polygons")
    wrap(fem_solver_2d, "_build_sliding_band_meshes", "mesh_build")
    wrap(p2_projection.SlipProjection, "build", "slip_projection")
    for method in ("Kpw", "tangent2", "solve_ff", "asmK", "elemB"):
        wrap(p2_nonlinear.P2Nonlinear, method, method)
    return elapsed, counts


def _solver_args(case: Case, geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        "n_steps_per_period": 12,
        "sampling_purpose": case.purpose,
        "n_periods": 1.0, "gamma_deg": 10.0,
        "I_phase_rms": case.current_a, "rpm": 13000.0,
        "n_parallel": 1, "connection": "2S",
        "mesh_size_mm": 0.6, "min_size_mm": 0.3,
        "outer_air_factor": 1.2, "gap_layers": 1.0,
        "n_sectors": 2, "stator_fillet_mm": 0.0,
        "coil_temp_c": 120.0, "end_winding_factor": 1.406,
        "rotor_eddy": False, "demag": False, "eddy": False,
        "torque_filter": False, "pole_copy": False,
        "iron_template": True, "geo_mesh": True,
        "structured_gap": True, "airgap_macro": False,
        "hi_fidelity": False, "drive": "current", "element_order": 2,
        "geo_override": geometry,
    }


def _saved_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep every raw torque/harmonic/loss channel, with no sample trimming."""
    exact = {"time_s", "rotor_angle_deg", "P2_transient_sample_history",
             "picard_iterations", "picard_fallback_frames",
             "picard_unconverged_frames", "mesh_build_events",
             "mesh_build_notes"}
    keep = {k: v for k, v in result.items() if k in exact or k.startswith(
        ("T_", "P_", "torque_", "loss_", "psi_"))}
    for key in ("T_em_raw_Nm", "T_em_maxwell_Nm", "time_s"):
        if key not in keep or len(keep[key]) != int(result["n_steps"]):
            raise ValueError(f"raw channel {key} missing or truncated")
    return _jsonable(keep)


def timing_summary(wall_s: float, inclusive: dict[str, float]) -> dict[str, Any]:
    """Report the arithmetic remainder without pretending nested calls partition wall."""
    return {
        "wall_minus_sum_inclusive_s": wall_s - sum(inclusive.values()),
        "inclusive_timers_are_not_a_wall_partition": True,
        "timing_note": ("timers may overlap through nested calls; this remainder "
                        "is not preparation or postprocessing time; use cProfile "
                        "or explicit stage boundaries for attribution"),
    }


def run_worker(output: Path, case_name: str, profile_case: str = "") -> None:
    case = CASE_BY_NAME[case_name]
    manifest = _verify_snapshot(output)
    case_dir = output / "cases" / case.name
    expected_config = (case_dir / "workspace" / "motor_config_frozen.yaml").resolve()
    if Path(os.environ.get("MOTOR_AI_SIM_CONFIG", "")).resolve() != expected_config:
        raise RuntimeError("worker is not pointed at its private config snapshot")
    if not expected_config.is_file():
        raise FileNotFoundError(expected_config)
    if (sha256_file(expected_config) != manifest["inputs"]["config"]["sha256"] or
            sha256_file(case_dir / "workspace" / "materials_library.yaml") !=
            manifest["inputs"]["materials"]["sha256"]):
        raise RuntimeError("private config or materials copy differs from snapshot")
    sys.path.insert(0, str(output / "snapshot" / "src"))
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

    elapsed, counts = _timer_wrappers()
    geometry = _case_geometry(output, case)
    kwargs = _solver_args(case, geometry)
    progress_marks = []

    def progress(done, total, phase=None, *_extra):
        # Calibration uses its own labelled 24-frame callback. Only the main
        # unlabelled 36/72-frame schedule enters this timing trace.
        if phase is None and int(total) == case.expected_steps:
            progress_marks.append({"frame_start": int(done),
                                   "elapsed_s": time.perf_counter() - started})

    kwargs["progress_cb"] = progress
    started = time.perf_counter()
    profiler = None
    if profile_case == case.name:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    try:
        result = em_transient_eval(**kwargs)
    finally:
        if profiler is not None:
            profiler.disable()
            profiler.dump_stats(str(case_dir / "profile.pstats"))
    wall_s = time.perf_counter() - started
    if profiler is not None:
        import pstats
        with (case_dir / "profile_top.txt").open("w", encoding="utf-8") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(60)
    scalars = {key: result.get(key) for key in (
        "n_steps", "n_steps_per_period", "n_steps_per_period_requested",
        "n_frames_solved", "solve_wall_s", "cogging_sampling_purpose",
        "cogging_target_raw_samples_per_cycle", "cogging_raw_samples_per_cycle",
        "cogging_sampling_sufficient",
        "cogging_sampling_final_quality_sufficient", "steps_snapped",
        "picard_converged", "picard_iters_mean", "picard_iters_max",
        "picard_resid_max", "structured_gap_effective",
        "slip_nodes_per_period", "T_avg_Nm", "T_ripple_raw_pct",
        "T_ripple_raw_pp_Nm", "T_avg_maxwell_Nm")}
    record = {
        "case": case.name, "git_head": manifest["git_head"],
        "source_tree_sha256": manifest["source_tree_sha256"],
        "runner_sha256": manifest["runner_sha256"],
        "input_hashes": manifest["inputs"],
        "geometry_sha256": geometry_sha256(geometry),
        "arguments": _jsonable({k: v for k, v in kwargs.items()
                                 if k != "progress_cb"}),
        "progress_callback": "timing-only, no solver-state changes",
        "wall_s": wall_s,
        "stage_timing_s": _jsonable(dict(elapsed)),
        "stage_calls": dict(counts),
        **timing_summary(wall_s, elapsed),
        "main_frame_start_marks": progress_marks,
        "time_to_first_main_frame_s": (progress_marks[0]["elapsed_s"]
                                       if progress_marks else None),
        "scalars": _jsonable(scalars),
        "raw": _saved_result(result),
    }
    _write_json(case_dir / "result.json", record)
    if (int(result["n_steps_per_period"]) != case.expected_steps or
            result.get("cogging_sampling_purpose") != case.purpose or
            not result.get("cogging_sampling_sufficient") or
            not result.get("picard_converged") or
            result.get("mesh_build_events")):
        raise RuntimeError(f"{case.name}: output saved but count/purpose/"
                           "convergence/mesh provenance did not match fixture")


def run_all(output: Path, timeout_s: int, profile_case: str = "") -> None:
    if timeout_s < 1 or timeout_s > 300:
        raise ValueError("per-case timeout must be 1..300 seconds")
    if profile_case and profile_case not in CASE_BY_NAME:
        raise ValueError("unknown profile case")
    manifest = snapshot_inputs(output)
    print(f"private benchmark: {output.resolve()}\nsource: {manifest['source_tree_sha256']}",
          flush=True)
    for case in CASES:
        _verify_snapshot(output)
        case_dir = output / "cases" / case.name
        case_dir.mkdir(parents=True, exist_ok=True)
        env = _worker_environment(output, case)
        command = [sys.executable, str(Path(__file__).resolve()), "worker",
                   "--output-dir", str(output.resolve()), "--case", case.name,
                   "--profile-case", profile_case]
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
                 if os.name == "nt" else 0)
        print(f"starting {case.name}: {case.expected_steps} frames, cap {timeout_s}s",
              flush=True)
        try:
            with (case_dir / "stdout.log").open("w", encoding="utf-8") as stdout, \
                 (case_dir / "stderr.log").open("w", encoding="utf-8") as stderr:
                process = subprocess.run(command, cwd=ROOT, env=env,
                                         stdout=stdout, stderr=stderr,
                                         timeout=timeout_s, creationflags=flags,
                                         check=False)
        except subprocess.TimeoutExpired as exc:
            _write_json(case_dir / "status.json",
                        {"status": "timeout", "limit_s": timeout_s})
            raise RuntimeError(f"{case.name} exceeded {timeout_s}s") from exc
        if process.returncode:
            _write_json(case_dir / "status.json",
                        {"status": "failed", "returncode": process.returncode})
            raise RuntimeError(f"{case.name} failed; inspect {case_dir / 'stderr.log'}")
        _write_json(case_dir / "status.json", {"status": "complete"})
    _write_json(output / "analysis.json", analyze_directory(output))


def relative_pct(value: float, reference: float) -> float | None:
    if abs(reference) <= 1e-12:
        return None
    return 100.0 * (value - reference) / abs(reference)


def _order(a: float, b: float) -> str:
    return "A>B" if a > b else "A<B" if a < b else "A=B"


def analyze_records(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare saved unfiltered samples; labels are screening, not certification."""
    comparisons = {}
    for design, load in (("A", "loaded"), ("B", "loaded"), ("A", "noload")):
        opt = records[f"{design}_{load}_optimization"]
        std = records[f"{design}_{load}_standard"]
        os, ss = opt["scalars"], std["scalars"]
        om = opt["raw"]["T_em_maxwell_Nm"]
        sm = std["raw"]["T_em_maxwell_Nm"]
        if len(om) != os["n_steps"] or len(sm) != ss["n_steps"]:
            raise ValueError("raw Maxwell samples were truncated")
        opp = max(om) - min(om)
        spp = max(sm) - min(sm)
        mean_delta = (None if load == "noload" else relative_pct(
            float(os["T_avg_Nm"]), float(ss["T_avg_Nm"])))
        pp_delta = relative_pct(opp, spp)
        oripple, sripple = os.get("T_ripple_raw_pct"), ss.get("T_ripple_raw_pct")
        ripple_delta = (None if oripple is None or sripple is None else
                        relative_pct(float(oripple), float(sripple)))
        losses = {}
        for key in sorted(set(opt["raw"]) & set(std["raw"])):
            if key.startswith("P_") and key.endswith("_W"):
                ov, sv = opt["raw"][key], std["raw"][key]
                if (isinstance(ov, (int, float)) and not isinstance(ov, bool)
                        and isinstance(sv, (int, float)) and not isinstance(sv, bool)):
                    losses[key] = {"optimization": ov, "standard": sv,
                                   "delta_pct": relative_pct(ov, sv)}
                elif isinstance(ov, list) and isinstance(sv, list):
                    losses[key] = {"optimization_mean": sum(ov) / len(ov) if ov else None,
                                   "standard_mean": sum(sv) / len(sv) if sv else None}
        gates = {
            "mean_within_0_5_pct": (None if mean_delta is None else
                                    abs(mean_delta) <= 0.5),
            "maxwell_pp_within_1_pct": (None if pp_delta is None else
                                        abs(pp_delta) <= 1.0),
            "reported_raw_ripple_within_1_pct": (
                None if ripple_delta is None else abs(ripple_delta) <= 1.0),
        }
        comparisons[f"{design}_{load}"] = {
            "optimization_frames": len(om), "standard_frames": len(sm),
            "mean_Nm": {"optimization": os["T_avg_Nm"],
                        "standard": ss["T_avg_Nm"]},
            "mean_delta_pct": mean_delta,
            "raw_maxwell_pp_Nm": {"optimization": opp, "standard": spp},
            "raw_maxwell_pp_delta_pct": pp_delta,
            "reported_raw_ripple_pct": {"optimization": oripple,
                                         "standard": sripple},
            "reported_raw_ripple_delta_pct": ripple_delta,
            "losses": losses, "screening_gates": gates,
            "undefined_gate_note": ("no-load mean gate is inapplicable near zero"
                                    if load == "noload" else None),
        }
    ordering = {}
    for metric, getter in (
        ("mean_torque_higher", lambda r: float(r["scalars"]["T_avg_Nm"])),
        ("raw_maxwell_pp_lower", lambda r: max(r["raw"]["T_em_maxwell_Nm"])
         - min(r["raw"]["T_em_maxwell_Nm"])),
        ("reported_raw_ripple_lower", lambda r: float(
            r["scalars"]["T_ripple_raw_pct"])),
        ("total_loss_lower", lambda r: float(
            r["raw"]["P_loss_total_avg_W"])),
    ):
        opt_order = _order(getter(records["A_loaded_optimization"]),
                           getter(records["B_loaded_optimization"]))
        std_order = _order(getter(records["A_loaded_standard"]),
                           getter(records["B_loaded_standard"]))
        ordering[metric] = {"optimization": opt_order, "standard": std_order,
                            "unchanged": opt_order == std_order}
    loaded_gates = [v for name in ("A_loaded", "B_loaded")
                    for v in comparisons[name]["screening_gates"].values()]
    noload_pp_gate = comparisons["A_noload"]["screening_gates"][
        "maxwell_pp_within_1_pct"]
    passed = (all(v is True for v in loaded_gates) and
              noload_pp_gate is True and
              all(v["unchanged"] for v in ordering.values()))
    return {"status": "coarse_screening_only", "screening_pass": passed,
            "gates_are_not_final_quality_certification": True,
            "comparisons": comparisons, "candidate_ordering": ordering}


def analyze_directory(output: Path) -> dict[str, Any]:
    manifest = _verify_snapshot(output)
    records = {}
    for case in CASES:
        status_path = output / "cases" / case.name / "status.json"
        if json.loads(status_path.read_text(encoding="utf-8")) != {
                "status": "complete"}:
            raise ValueError(f"{case.name}: run was not marked complete")
        record = json.loads((output / "cases" / case.name / "result.json").read_text(
            encoding="utf-8"))
        if (record["source_tree_sha256"] != manifest["source_tree_sha256"] or
                record["git_head"] != manifest["git_head"] or
                record["input_hashes"] != manifest["inputs"] or
                record["geometry_sha256"] != geometry_sha256(
                    _case_geometry(output, case)) or
                record["scalars"]["cogging_sampling_purpose"] != case.purpose or
                int(record["scalars"]["n_steps_per_period"]) != case.expected_steps or
                not record["scalars"]["picard_converged"] or
                not record["scalars"]["cogging_sampling_sufficient"] or
                record["raw"].get("mesh_build_events")):
            raise ValueError(f"{case.name}: incompatible provenance or convergence")
        records[case.name] = record
    return analyze_records(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("run-all", "worker", "analyze"):
        sub = commands.add_parser(command)
        sub.add_argument("--output-dir", type=Path, required=True)
        if command == "run-all":
            sub.add_argument("--timeout-s", type=int, default=300)
            sub.add_argument("--profile-case", default="", choices=("", *CASE_BY_NAME))
        if command == "worker":
            sub.add_argument("--case", required=True, choices=CASE_BY_NAME)
            sub.add_argument("--profile-case", default="")
    args = parser.parse_args(argv)
    if args.command == "run-all":
        run_all(args.output_dir, args.timeout_s, args.profile_case)
    elif args.command == "worker":
        run_worker(args.output_dir.resolve(), args.case, args.profile_case)
    else:
        print(json.dumps(analyze_directory(args.output_dir.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
