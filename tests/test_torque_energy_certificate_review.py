"""No-FEM tests for the archived energy-certificate completeness report."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import json
import pytest
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "torque_energy_certificate_review.py"
spec = importlib.util.spec_from_file_location("torque_energy_certificate_review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_report_keeps_endpoint_evidence_separate_from_certificate():
    inventory = {
        "run15": {
            "missing_full_state_frames_in_first_period": list(range(2, 48)),
            "raw_waveform_sha256_actual": "wave-hash",
        },
        "run16": {"frozen_states_persisted": 96, "all_frozen_states_converged": True},
    }
    report = review.build_report(
        {"status": "uncertified_archived_diagnostic", "period_path_terminal_work_Nm": 1.2},
        {"reproduction": "blocked_by_provenance_or_archive", "error": "old solver hash"},
        {"reproduction": "blocked_by_provenance_or_archive", "error": "old solver hash"},
        inventory,
    )
    assert report["certified"] is False
    assert report["open_requirements"]["periodic_stored_energy"]["endpoint_pairs"] == [
        "run15 frames 0→48", "run15 frames 1→49"
    ]
    assert report["open_requirements"]["periodic_stored_energy"]["missing_full_state_frames_first_period"] == list(range(2, 48))
    assert report["open_requirements"]["moving_weld"]["pair_count"] == 48
    assert report["open_requirements"]["moving_weld"]["finite_frozen_current_pairs_reproduced"] is False
    assert report["open_requirements"]["periodic_stored_energy"]["endpoint_pair_values_reproduced"] is False
    assert report["open_requirements"]["periodic_stored_energy"]["previously_documented_differences_J"] == [1.0902078498853385e-6, 8.860720643322217e-7]
    assert "Do not change" in report["decision"]


def test_other_archive_maxwell_gap_is_context_only():
    inventory = {
        "run15": {"missing_full_state_frames_in_first_period": []},
        "run16": {},
    }
    result = review.build_report({}, {}, {}, inventory)
    context = result["gap_context"]
    assert context["run13_run14_terminal_work_minus_raw_maxwell_Nm"] == 0.2650477869242
    assert "not an uncertainty" in context["qualification"]
    assert "different GEO30" in context["qualification"]


def test_historical_energy_values_require_numeric_reproduction():
    inventory = {"run15": {"missing_full_state_frames_in_first_period": []},
                 "run16": {"missing_frozen_state_file_pairs": []}}
    historical = {"potential_period_delta_0_to_48_J": 1.0902078498853385e-6,
                  "potential_period_delta_1_to_49_J": 8.860720643322217e-7}
    report = review.build_report({}, {"reproduction": "passed", "result": historical}, {}, inventory)
    assert report["open_requirements"]["periodic_stored_energy"]["previous_values_currently_reproduced"]
    assert report["open_requirements"]["periodic_stored_energy"]["first_period_full_state_capture_complete"]
    assert not report["certified"]
    changed = dict(historical, potential_period_delta_1_to_49_J=1e-5)
    report = review.build_report({}, {"reproduction": "passed", "result": changed}, {}, inventory)
    assert not report["open_requirements"]["periodic_stored_energy"]["previous_values_currently_reproduced"]


def test_inventory_checks_actual_state_file_pairs(tmp_path):
    run15, run16 = tmp_path / "run15", tmp_path / "run16"
    run15.mkdir(); run16.mkdir()
    (run15 / "capture_review.json").write_text(json.dumps({
        "selected_frames_captured": [0, 1, 48, 49], "n_samples": 96,
        "raw_waveforms_sha256": "unused"}), encoding="utf-8")
    (run15 / "provenance.json").write_text("{}", encoding="utf-8")
    (run15 / "raw_waveforms.npz").write_bytes(b"synthetic")
    (run16 / "capture_review.json").write_text(json.dumps({
        "frames_persisted": 96, "all_selected_newton_converged": True,
        "source_hashes_unchanged": True}), encoding="utf-8")
    for k in (0, 1, 48):
        (run15 / f"frame_{k:02d}.npz").write_bytes(b"")
        (run15 / f"frame_{k:02d}.json").write_text("{}", encoding="utf-8")
    (run16 / "frame_00.npz").write_bytes(b"")
    (run16 / "frame_00.json").write_text("{}", encoding="utf-8")
    inv = review._capture_inventory(run15, run16)
    assert inv["run15"]["first_period_endpoint_pair_0_48_available"]
    assert not inv["run15"]["first_period_endpoint_pair_1_49_available"]
    assert inv["run15"]["missing_full_state_frames_in_first_period"] == list(range(2, 48))
    assert inv["run16"]["frozen_state_file_pairs_present"] == 1
    assert inv["run16"]["missing_frozen_state_file_pairs"] == list(range(1, 96))


def test_cli_returns_nonzero_for_unresolved_or_preflight_error(monkeypatch, capsys):
    monkeypatch.setattr(review, "analyze", lambda: {"certified": False, "status": "incomplete"})
    assert review.main([]) == 2
    assert '"certified": false' in capsys.readouterr().out

    def fail():
        raise ValueError("synthetic hash mismatch")

    monkeypatch.setattr(review, "analyze", fail)
    assert review.main([]) == 2
    assert '"error": "synthetic hash mismatch"' in capsys.readouterr().out


def test_current_archives_report_missing_certificate_data_without_physics_failure():
    run15 = review.ROOT / "scratchpad/torque_corrected_state_20260923_run15"
    run16 = review.ROOT / "scratchpad/torque_frozenmean_20260923_run16"
    if not run15.is_dir() or not run16.is_dir():
        pytest.skip("local archived physics records are unavailable")
    report = review.analyze()
    assert report["status"] == "incomplete_physical_certificate"
    assert report["certified"] is False
    assert report["physics_validation_status"] == "not_established_by_saved_evidence"
    assert report["archive_inventory"]["run15"]["saved_selected_full_state_file_pairs"] == [0, 1, 48, 49]
    assert report["archive_inventory"]["run15"]["missing_full_state_frames_in_first_period"] == list(range(2, 48))
    assert report["archive_inventory"]["run16"]["frozen_state_file_pairs_present"] == 96
    assert report["available_checks"]["run04_legacy_closure_diagnostic"]["reproduction"] == "passed"
    for key in ("run15_corrected_saved_state_energy_linkage",
                "run16_full_period_frozen_current_secants"):
        entry = report["available_checks"][key]
        assert entry["reproduction"] in ("passed", "not_reproduced")
        if entry["reproduction"] == "not_reproduced":
            assert entry["blocker_category"] in ("provenance_guard", "review_error_unclassified")
    assert report["gap_context"]["value_status"].startswith("historical_separate")


def test_waveform_hash_tamper_is_a_preflight_error(monkeypatch, tmp_path):
    run15, run16 = tmp_path / "run15", tmp_path / "run16"
    run15.mkdir(); run16.mkdir()
    (run15 / "provenance.json").write_text('{"source_hashes": {}}', encoding="utf-8")
    (run16 / "provenance.json").write_text('{"run15_source_hashes": {}}', encoding="utf-8")
    monkeypatch.setattr(review, "_load_script", lambda *_: SimpleNamespace(analyze=lambda *_: {}))
    monkeypatch.setattr(review, "_capture_inventory", lambda *_: {
        "run15": {"raw_waveform_sha256_actual": "tampered", "raw_waveform_sha256_review": "expected",
                  "missing_full_state_frames_in_first_period": []},
        "run16": {"missing_frozen_state_file_pairs": []},
    })
    with pytest.raises(ValueError, match="waveform bytes disagree"):
        review.analyze(run04=tmp_path, run06=tmp_path, run09=tmp_path,
                       run15=run15, run16=run16)
