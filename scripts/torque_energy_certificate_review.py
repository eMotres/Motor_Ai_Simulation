"""Offline completeness review for archived terminal-work torque evidence.

This tool never solves a field problem and never certifies torque. It reruns
the existing saved-state closure and frozen-current diagnostics, then states
which pieces of a physical certificate are and are not present.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load offline review {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_inventory(run15: Path, run16: Path) -> dict[str, Any]:
    capture15 = json.loads((run15 / "capture_review.json").read_text())
    capture16 = json.loads((run16 / "capture_review.json").read_text())
    selected = [int(x) for x in capture15.get("selected_frames_captured", [])]
    expected_first_period = list(range(49))
    saved_first_period = [
        k for k in expected_first_period
        if (run15 / f"frame_{k:02d}.npz").is_file()
        and (run15 / f"frame_{k:02d}.json").is_file()
    ]
    saved_selected = [
        k for k in selected
        if (run15 / f"frame_{k:02d}.npz").is_file()
        and (run15 / f"frame_{k:02d}.json").is_file()
    ]
    saved_frozen = [
        k for k in range(96)
        if (run16 / f"frame_{k:02d}.npz").is_file()
        and (run16 / f"frame_{k:02d}.json").is_file()
    ]
    return {
        "run15": {
            "raw_waveform_sha256_actual": _sha256(run15 / "raw_waveforms.npz"),
            "raw_waveform_sha256_review": capture15.get("raw_waveforms_sha256"),
            "raw_samples": int(capture15.get("n_samples", 0)),
            "full_p2_states_captured": selected,
            "saved_selected_full_state_file_pairs": saved_selected,
            "first_period_endpoint_pair_0_48_available": 0 in saved_selected and 48 in saved_selected,
            "first_period_endpoint_pair_1_49_available": 1 in saved_selected and 49 in saved_selected,
            "saved_full_state_frames_in_first_period": saved_first_period,
            "missing_full_state_frames_in_first_period": sorted(set(expected_first_period) - set(saved_first_period)),
            "provenance_git_head": json.loads((run15 / "provenance.json").read_text()).get("git_head"),
        },
        "run16": {
            "capture_status": capture16.get("status"),
            "frozen_states_persisted": int(capture16.get("frames_persisted", 0)),
            "frozen_state_file_pairs_present": len(saved_frozen),
            "missing_frozen_state_file_pairs": sorted(set(range(96)) - set(saved_frozen)),
            "all_frozen_states_converged": bool(capture16.get("all_selected_newton_converged", False)),
            "source_hashes_unchanged": bool(capture16.get("source_hashes_unchanged", False)),
            "interpretation": "96 states are 48 frozen-current ±1-slip-cell equilibria, not a transient-period P2 state trajectory",
        },
    }


def build_report(
    closure: dict[str, Any],
    corrected: dict[str, Any],
    frozen: dict[str, Any],
    inventory: dict[str, Any],
    *,
    terminal_sampling_maxwell_gap_nm: float = 0.2650477869242,
) -> dict[str, Any]:
    run15 = inventory["run15"]
    run16 = inventory["run16"]
    historical_deltas = (1.0902078498853385e-6, 8.860720643322217e-7)
    corrected_result = corrected.get("result", {}) if corrected.get("reproduction") == "passed" else {}
    reproduced_deltas = [corrected_result.get("potential_period_delta_0_to_48_J"),
                         corrected_result.get("potential_period_delta_1_to_49_J")]
    historical_values_match = all(
        isinstance(value, (int, float)) and abs(value - expected) <= 1e-12
        for value, expected in zip(reproduced_deltas, historical_deltas)
    )
    return {
        "status": "incomplete_physical_certificate",
        "certified": False,
        "physics_validation_status": "not_established_by_saved_evidence",
        "available_checks": {
            "run04_legacy_closure_diagnostic": closure,
            "run15_corrected_saved_state_energy_linkage": corrected,
            "run16_full_period_frozen_current_secants": frozen,
        },
        "archive_inventory": inventory,
        "open_requirements": {
            "periodic_stored_energy": {
                "endpoint_pair_values_reproduced": historical_values_match,
                "endpoint_pairs": ["run15 frames 0→48", "run15 frames 1→49"],
                "previously_documented_differences_J": list(historical_deltas),
                "currently_reproduced_differences_J": reproduced_deltas,
                "previous_values_currently_reproduced": historical_values_match,
                "first_period_full_state_capture_complete": not run15["missing_full_state_frames_in_first_period"],
                "missing_full_state_frames_first_period": run15["missing_full_state_frames_in_first_period"],
                "why_not_certified": "Only two endpoint pairs have full P2 states; no declared energy/field periodicity tolerance or mapped-state error bound. Endpoint energy differences are measurements, not a pass criterion.",
            },
            "moving_weld": {
                "finite_frozen_current_pairs_reproduced": frozen.get("reproduction") == "passed",
                "pair_count": 48,
                "saved_pair_state_files_complete": not run16.get("missing_frozen_state_file_pairs", []),
                "step": "±1 integer slip cell per center",
                "why_not_certified": "These are finite discrete secants across separately rebuilt integer welds. There is no all-period step-refinement sequence or justified bound from secants to a continuous shape derivative.",
            },
            "port_energy_identity": {
                "certified": False,
                "why_not_certified": "No independent complete-period identity with PM reference, stored-energy change, source work and mechanical work has a declared residual tolerance and bound.",
            },
        },
        "gap_context": {
            "run13_run14_terminal_work_minus_raw_maxwell_Nm": float(terminal_sampling_maxwell_gap_nm),
            "value_status": "historical_separate_run13_run14_result_not_recomputed_here",
            "qualification": "This 0.2650477869 N·m gap is from the separate merged 120-position run13/run14 archive. It is a raw mean discrepancy, not an uncertainty or a closure residual; run15/run16 are a different GEO30 operating-point study.",
        },
        "decision": "Do not change the production terminal-work selector from these incomplete certificates.",
    }


def analyze(
    run04: Path | None = None,
    run06: Path | None = None,
    run09: Path | None = None,
    run15: Path | None = None,
    run16: Path | None = None,
) -> dict[str, Any]:
    run04 = run04 or ROOT / "scratchpad/torque_full_state_20260923_run04"
    run06 = run06 or ROOT / "scratchpad/torque_material_20260923_run06"
    run09 = run09 or ROOT / "scratchpad/torque_frozen_current_20260923_run09"
    run15 = run15 or ROOT / "scratchpad/torque_corrected_state_20260923_run15"
    run16 = run16 or ROOT / "scratchpad/torque_frozenmean_20260923_run16"
    scripts = ROOT / "scripts"
    closure_mod = _load_script("torque_offline_closure_for_certificate", scripts / "torque_offline_closure.py")
    corrected_mod = _load_script("torque_corrected_state_for_certificate", scripts / "torque_corrected_state_review.py")
    frozen_mod = _load_script("torque_frozen_mean_for_certificate", scripts / "torque_frozen_mean_review.py")
    def reproduce(callback):
        try:
            return {"reproduction": "passed", "result": callback()}
        except (OSError, KeyError, ValueError, ImportError, RuntimeError) as exc:
            message = str(exc)
            category = "provenance_guard" if message in (
                "Production B-H law source changed since capture",
                "Production B-H law differs from captured source",
            ) else "review_error_unclassified"
            return {"reproduction": "not_reproduced", "blocker_category": category,
                    "error": message}

    closure = reproduce(lambda: closure_mod.analyze(run04, run06, run09))
    corrected = reproduce(lambda: corrected_mod.analyze(run15))
    frozen = reproduce(lambda: frozen_mod.analyze(run16, run15))
    inventory = _capture_inventory(Path(run15), Path(run16))
    provenance15 = json.loads((Path(run15) / "provenance.json").read_text())
    provenance16 = json.loads((Path(run16) / "provenance.json").read_text())
    source_map = {
        "fem_solver_2d.py": ROOT / "src/motor_ai_sim/simulation/fem_solver_2d.py",
        "field_ops.py": ROOT / "src/motor_ai_sim/simulation/field_ops.py",
        "sb_postproc.py": ROOT / "src/motor_ai_sim/simulation/sb_postproc.py",
        "p2_nonlinear.py": ROOT / "src/motor_ai_sim/simulation/p2_nonlinear.py",
    }
    expected_sources = provenance15.get("source_hashes", {})
    expected_frozen_sources = provenance16.get("run15_source_hashes", {})
    inventory["source_hash_comparison"] = {
        name: {
            "expected_at_run15_capture": expected_sources.get(name),
            "expected_at_run16_capture": expected_frozen_sources.get(name),
            "current_sha256": _sha256(path),
            "matches_run15_capture": expected_sources.get(name) == _sha256(path),
            "matches_run16_capture": expected_frozen_sources.get(name) == _sha256(path),
        }
        for name, path in source_map.items()
    }
    if inventory["run15"]["raw_waveform_sha256_actual"] != inventory["run15"]["raw_waveform_sha256_review"]:
        raise ValueError("Run15 waveform bytes disagree with capture review")
    return build_report(closure, corrected, frozen, inventory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON (the default output format)")
    args = parser.parse_args(argv)
    del args
    try:
        report = analyze()
    except (OSError, KeyError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({"status": "preflight_error", "certified": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 2 if not report["certified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
