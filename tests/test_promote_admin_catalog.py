from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
import yaml
import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "promote_admin_catalog.py"


def _tool():
    spec = importlib.util.spec_from_file_location("promote_admin_catalog", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: Path):
    tool = _tool()
    email = "admin@example.com"
    workspaces = tmp_path / "workspaces"
    shared = tmp_path / "shared"
    source = workspaces / tool.workspace_id(email)
    source_die = source / "dies" / "TEST MOTOR 40"
    target_die = shared / "dies" / "TEST MOTOR 40"
    source_die.mkdir(parents=True)
    target_die.mkdir(parents=True)
    die_doc = {"name": "TEST MOTOR 40", "geometry": {"num_slots": 12, "num_poles": 14}}
    cfg_doc = {"name": "L12", "die": "TEST MOTOR 40", "geometry_overrides": {"motor_length": 12},
               "winding": {"connection": "star"}, "materials": {}, "duties": []}
    (source_die / "die.yaml").write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    (target_die / "die.yaml").write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    src_cfg = source_die / "L12.yaml"
    dst_cfg = target_die / "L12.yaml"
    src_cfg.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    dst_cfg.write_text(yaml.safe_dump({**cfg_doc, "geometry_overrides": {"motor_length": 10}}),
                       encoding="utf-8")
    os.utime(dst_cfg, (1_600_000_000, 1_600_000_000))
    return tool, email, workspaces, shared, source, source_die, target_die, cfg_doc


def _write_field(path: Path, *, die: str, config: str, duty: str,
                 geometry_fingerprint: str, kind: str = "em") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"die": die, "config": config, "duty": duty, "kind": kind,
            "geometry_fingerprint": geometry_fingerprint}
    np.savez_compressed(path, meta=np.array(json.dumps(meta)), field=np.array([1.0]))


def test_admin_source_workspace_is_derived_from_configured_admin_identity(tmp_path):
    tool, email, workspaces, shared, *_ = _tree(tmp_path)
    files, warnings, conflicts = tool.build_plan(
        email, [email], workspaces, shared)
    assert files
    assert not warnings
    assert not conflicts
    with pytest.raises(PermissionError):
        tool.build_plan("other@example.com", [email], workspaces, shared)


def test_dry_run_does_not_touch_source_or_shared_tree_and_apply_backs_up(tmp_path):
    tool, email, workspaces, shared, source, source_die, target_die, _ = _tree(tmp_path)
    src_cfg = source_die / "L12.yaml"
    dst_cfg = target_die / "L12.yaml"
    src_before = src_cfg.read_bytes()
    target_before = dst_cfg.read_bytes()
    files, _, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert not conflicts
    assert src_cfg.read_bytes() == src_before
    assert dst_cfg.read_bytes() == target_before
    assert any(item.destination == dst_cfg for item in files)

    backup = tool.apply_plan(files, shared)
    assert dst_cfg.read_bytes() == src_before
    assert src_cfg.read_bytes() == src_before
    assert backup is not None
    assert (backup / dst_cfg.relative_to(shared)).read_bytes() == target_before


def test_newer_shared_conflict_and_geometry_mismatch_are_never_overwritten(tmp_path):
    tool, email, workspaces, shared, _, source_die, target_die, _ = _tree(tmp_path)
    src_cfg, dst_cfg = source_die / "L12.yaml", target_die / "L12.yaml"
    os.utime(src_cfg, (1_500_000_000, 1_500_000_000))
    os.utime(dst_cfg, (1_700_000_000, 1_700_000_000))
    _, _, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert any("shared file differs" in item for item in conflicts)
    before = dst_cfg.read_bytes()

    (target_die / "die.yaml").write_text(yaml.safe_dump({
        "name": "TEST MOTOR 40", "geometry": {"num_slots": 24, "num_poles": 28}}),
        encoding="utf-8")
    _, _, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert any("geometry differs" in item for item in conflicts)
    assert dst_cfg.read_bytes() == before


def test_authorized_geometry_replacement_preserves_shared_config_and_migrates_current_l20_evidence(tmp_path):
    tool, email, workspaces, shared, _, source_die, target_die, l12_cfg = _tree(tmp_path)
    shared_l12 = target_die / "L12.yaml"
    # Production L12 is already byte-identical between the admin workspace
    # and shared catalog, so authoritative die geometry must leave it alone.
    shared_l12.write_bytes((source_die / "L12.yaml").read_bytes())
    shared_l12_before = shared_l12.read_bytes()
    die_doc = yaml.safe_load((source_die / "die.yaml").read_text(encoding="utf-8"))
    die_doc["geometry"]["fillet_r1"] = 0.15
    (source_die / "die.yaml").write_text(yaml.safe_dump(die_doc), encoding="utf-8")

    l20 = {"name": "L20", "die": "TEST MOTOR 40", "geometry_overrides": {},
           "winding": {}, "materials": {}, "duties": []}
    build_sig = tool._fingerprint(die_doc, l20)
    l20["duties"] = [{"name": "rated", "runs": {"current": {
        "build_sig": build_sig, "payload_file": "runs/L20/rated-abcd.current.json.gz"}}}]
    (source_die / "L20.yaml").write_text(yaml.safe_dump(l20), encoding="utf-8")
    payload = source_die / "runs" / "L20" / "rated-abcd.current.json.gz"
    fields = source_die / "runs" / "L20" / "rated-abcd" / "fields"
    fields.mkdir(parents=True)
    with gzip.open(payload, "wt", encoding="utf-8") as f:
        json.dump({"name": "rated", "drive": "current", "payload": {
            "rpm": 13000, "geo_fingerprint": "geo-current"}}, f)
    _write_field(fields / "em.npz", die="TEST MOTOR 40", config="L20",
                 duty="rated", geometry_fingerprint="geo-current")

    files, warnings, conflicts = tool.build_plan(
        email, [email], workspaces, shared,
        die_names={"TEST MOTOR 40"}, authoritative_dies={"TEST MOTOR 40"})
    assert not conflicts
    assert not warnings
    destinations = {item.destination for item in files}
    assert target_die / "die.yaml" in destinations
    assert target_die / "L20.yaml" in destinations
    assert target_die / "runs" / "L20" / "rated-abcd.current.json.gz" in destinations
    assert target_die / "runs" / "L20" / "rated-abcd" / "fields" / "em.npz" in destinations
    assert shared_l12 not in destinations
    assert shared_l12.read_bytes() == shared_l12_before


def test_only_current_build_runs_and_fields_are_eligible_for_promotion(tmp_path):
    tool = _tool()
    die_doc = {"name": "TEST MOTOR 40", "geometry": {"num_slots": 12}}
    cfg_doc = {"name": "L12", "die": "TEST MOTOR 40", "geometry_overrides": {},
               "winding": {}, "materials": {}, "duties": []}
    build_sig = tool._fingerprint(die_doc, cfg_doc)
    # Some valid legacy/current config documents omit a duty-level signature;
    # the referenced run itself is still authoritative when its signature matches.
    cfg_doc["duties"] = [{"name": "rated",
                          "runs": {"sine": {"build_sig": build_sig,
                                              "payload_file": "runs/L12/rated.sine.json.gz"}}}]
    source_die = tmp_path / "motor"
    sidecar = source_die / "runs" / "L12" / "rated.sine.json.gz"
    fields = source_die / "runs" / "L12" / "rated" / "fields"
    fields.mkdir(parents=True)
    with gzip.open(sidecar, "wt", encoding="utf-8") as f:
        json.dump({"name": "rated", "drive": "sine", "payload": {
            "rpm": 9000, "geo_fingerprint": "geo-current"}}, f)
    _write_field(fields / "em.npz", die="motor", config="L12", duty="rated",
                 geometry_fingerprint="geo-current")
    conflicts: list[str] = []
    anchors: list[dict] = []
    eligible, complete = tool._evidence_files(
        source_die, "L12", cfg_doc, build_sig, conflicts, anchors)
    assert set(eligible) == {sidecar, fields / "em.npz"}
    assert complete
    assert not conflicts
    assert anchors[0]["geometry_fingerprint"] == "geo-current"

    cfg_doc["duties"][0]["build_sig"] = "stale-build"
    conflicts = []
    skipped, complete = tool._evidence_files(
        source_die, "L12", cfg_doc, build_sig, conflicts)
    assert skipped == []
    assert not complete
    assert any("fingerprint mismatch" in item for item in conflicts)


def test_incomplete_payload_reference_prevents_config_promotion(tmp_path):
    tool, email, workspaces, shared, _, source_die, target_die, cfg_doc = _tree(tmp_path)
    die_doc = yaml.safe_load((source_die / "die.yaml").read_text(encoding="utf-8"))
    build_sig = tool._fingerprint(die_doc, cfg_doc)
    cfg_doc["duties"] = [{"name": "rated", "build_sig": build_sig,
                          "runs": {"sine": {"build_sig": build_sig,
                                              "payload_file": "runs/L12/missing.sine.json.gz"}}}]
    (source_die / "L12.yaml").write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    destination = target_die / "L12.yaml"
    before = destination.read_bytes()
    files, warnings, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert not conflicts
    assert any("incomplete" in item for item in warnings)
    assert all(item.destination != destination for item in files)
    assert destination.read_bytes() == before


def test_result_merge_keeps_only_build_matched_admin_rows(tmp_path):
    tool, email, workspaces, shared, source, source_die, target_die, cfg_doc = _tree(tmp_path)
    build_sig = tool._fingerprint(
        yaml.safe_load((source_die / "die.yaml").read_text(encoding="utf-8")), cfg_doc)
    cfg_doc["duties"] = [{"name": "rated", "build_sig": build_sig}]
    (source_die / "L12.yaml").write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    rows = {"version": 1, "results": {"TEST MOTOR 40": {"L12": {
        "rated": {"thermal": {"build_sig": build_sig, "T_max": 60},
                  "modes": {"first_hz": 20}},
        "deleted duty": {"thermal": {"build_sig": build_sig, "T_max": 1}},
    }}}}
    (source / ".duty_results.json").write_text(json.dumps(rows), encoding="utf-8")
    (shared / ".duty_results.json").write_text(json.dumps({"version": 1, "results": {
        "UNRELATED": {"L1": {"rated": {"thermal": {"T_max": 99}}}}
    }}), encoding="utf-8")
    files, _, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert not conflicts
    ledger = next(item for item in files if item.destination == shared / ".duty_results.json")
    result_doc = json.loads(ledger.content or b"{}")
    assert result_doc["results"]["UNRELATED"]["L1"]["rated"]["thermal"]["T_max"] == 99
    assert result_doc["results"]["TEST MOTOR 40"]["L12"]["rated"]["thermal"]["T_max"] == 60
    assert "modes" not in result_doc["results"]["TEST MOTOR 40"]["L12"]["rated"]
    assert "deleted duty" not in result_doc["results"]["TEST MOTOR 40"]["L12"]


def test_legacy_scalar_results_require_current_run_geometry_catalog_and_point(tmp_path):
    tool = _tool()
    die = "CIANO14 40 new"
    die_doc = {"name": die, "geometry": {"num_slots": 12}}
    cfg = {"name": "L20", "die": die, "materials": {
        "stator_core": "20SW1200", "rotor_core": "20SW1200",
        "magnet": "F52SH_120C", "shaft": "Steel_42CrMo4_QT",
        "sleeve": "M40X_UD_60", "slot": "copper",
        "slot_insulation": "Nomex", "wire_insulation": "polyimide"},
        "duties": []}
    build_sig = tool._fingerprint(die_doc, cfg)
    cfg["duties"] = [{"name": "rated", "runs": {"current": {
        "build_sig": build_sig,
        "payload_file": "runs/L20/rated-9c0f977d.current.json.gz"}}}]
    refs = [
        {"kind": "dies", "id": die, "source": None, "revision": None,
         "error": "not found"},
        {"kind": "materials", "id": "steel/20SW1200", "source": "shared",
         "revision": "s1"},
        {"kind": "materials", "id": "magnet/F52SH_120C", "source": "shared",
         "revision": "m1"},
        {"kind": "materials", "id": "insulator/M40X_UD_60", "source": "shared",
         "revision": "i1"},
        {"kind": "materials", "id": "steel/Steel_42CrMo4_QT", "source": "shared",
         "revision": "s2"},
        {"kind": "materials", "id": "conductor/copper", "source": "shared",
         "revision": "c1"},
        {"kind": "materials", "id": "insulator/Nomex", "source": "shared",
         "revision": "i2"},
        {"kind": "materials", "id": "insulator/polyimide", "source": "shared",
         "revision": "i3"},
        {"kind": "materials", "id": "coolant/air", "source": "shared",
         "revision": "a1"},
    ]
    point = {"rpm": 13000, "I_phase_rms": 52.554, "gamma_deg": 10,
             "coil_temp_c": 199.6, "magnet_temp_c": 113.6, "ambient_temp": 30}
    source_die = tmp_path / die
    sidecar = source_die / "runs" / "L20" / "rated-9c0f977d.current.json.gz"
    sidecar.parent.mkdir(parents=True)
    with gzip.open(sidecar, "wt", encoding="utf-8") as f:
        json.dump({"name": "rated", "drive": "current", "payload": {
            "geo_fingerprint": "geo-current", "computed_at": "2026-09-30T18:28:03",
            "rpm": 13000, "_summary_args": {"I_phase_rms": 52.554,
                                                "gamma_deg": 10,
                                                "coil_temp_c": 199.6,
                                                "magnet_temp_c": 113.6,
                                                "ambient_temp": 30}}}, f)
    anchors: list[dict] = []
    evidence, complete = tool._evidence_files(source_die, "L20", cfg, build_sig,
                                               [], anchors)
    assert evidence == [sidecar] and complete
    assert anchors[0]["die"] == die
    thermal = {"kind": "thermal", "geometry_fingerprint": "geo-current",
               "geometry_fingerprint_v2": "geo-v2", "catalog_refs": refs,
               "computed_at": "2026-09-30T18:29:00", "point": point,
               "T_max": 82.0}
    coupled = {"kind": "coupled", "geometry_fingerprint": "geo-current",
               "geometry_fingerprint_v2": "geo-v2", "catalog_refs": refs,
               "computed_at": anchors[0]["computed_at"], "point": None, "loss_W": 4.2}
    ledger = tmp_path / ".duty_results.json"
    ledger.write_text(json.dumps({"results": {die: {"L20": {
        "rated": {"thermal": thermal, "coupled": coupled}}}}}), encoding="utf-8")
    selected = tool._result_rows(ledger, die_doc, {"L20": cfg}, {"L20"}, [], [],
                                 {"L20": anchors})
    assert set(selected["L20"]["rated"]) == {"thermal", "coupled"}

    bad_thermal = {**thermal, "point": {**point, "rpm": 25000}}
    bad_refs = {**thermal, "catalog_refs": [*refs[:2],
        {"kind": "materials", "id": "magnet/OTHER", "source": "shared",
         "revision": "m1"}, *refs[3:]]}
    bad_air = {**thermal, "catalog_refs": [*refs[:-1],
        {"kind": "materials", "id": "coolant/OTHER", "source": "shared",
         "revision": "a1"}]}
    unresolved_air = {**thermal, "catalog_refs": [*refs[:-1],
        {"kind": "materials", "id": "coolant/air", "source": None,
         "revision": None, "error": "not found"}]}
    for bad in (bad_thermal, bad_refs, bad_air, unresolved_air,
                {**thermal, "geometry_fingerprint": "stale-geometry"}):
        ledger.write_text(json.dumps({"results": {die: {"L20": {
            "rated": {"thermal": bad}}}}}), encoding="utf-8")
        rejected = tool._result_rows(ledger, die_doc, {"L20": cfg}, {"L20"}, [], [],
                                      {"L20": anchors})
        assert "thermal" not in rejected.get("L20", {}).get("rated", {})


def test_field_metadata_must_match_validated_run_identity_and_geometry(tmp_path):
    tool = _tool()
    path = tmp_path / "em.npz"
    _write_field(path, die="D", config="C", duty="rated",
                 geometry_fingerprint="geo-current")
    anchor = {"die": "D", "config": "C", "duty": "rated",
              "geometry_fingerprint": "geo-current"}
    assert tool._valid_field_metadata(path, anchor)
    assert not tool._valid_field_metadata(path, {**anchor, "die": "OTHER"})
    assert not tool._valid_field_metadata(path, {**anchor,
                                                   "geometry_fingerprint": "stale"})


def test_field_metadata_mismatch_blocks_the_configuration_evidence_set(tmp_path):
    tool = _tool()
    source_die = tmp_path / "D"
    cfg = {"name": "C", "die": "D", "materials": {},
           "duties": [{"name": "rated", "runs": {"current": {
               "build_sig": "sig", "payload_file": "runs/C/rated.current.json.gz"}}}]}
    sidecar = source_die / "runs" / "C" / "rated.current.json.gz"
    sidecar.parent.mkdir(parents=True)
    with gzip.open(sidecar, "wt", encoding="utf-8") as f:
        json.dump({"name": "rated", "drive": "current", "payload": {
            "geo_fingerprint": "geo-current"}}, f)
    field = source_die / "runs" / "C" / "rated" / "fields" / "thermal.npz"
    _write_field(field, die="D", config="C", duty="rated",
                 geometry_fingerprint="stale", kind="thermal")
    warnings: list[str] = []
    evidence, complete = tool._evidence_files(source_die, "C", cfg, "sig", warnings)
    assert sidecar in evidence
    assert field not in evidence
    assert not complete
    assert any("field metadata" in warning for warning in warnings)


def test_different_build_result_in_shared_ledger_blocks_promotion(tmp_path):
    tool, email, workspaces, shared, source, source_die, _, cfg_doc = _tree(tmp_path)
    die_doc = yaml.safe_load((source_die / "die.yaml").read_text(encoding="utf-8"))
    build_sig = tool._fingerprint(die_doc, cfg_doc)
    cfg_doc["duties"] = [{"name": "rated", "build_sig": build_sig}]
    (source_die / "L12.yaml").write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    (source / ".duty_results.json").write_text(json.dumps({"version": 1, "results": {
        "TEST MOTOR 40": {"L12": {"rated": {
            "thermal": {"build_sig": build_sig, "T_max": 60}}}}
    }}), encoding="utf-8")
    shared_ledger = shared / ".duty_results.json"
    old = {"version": 1, "results": {"TEST MOTOR 40": {"L12": {"rated": {
        "thermal": {"build_sig": "older-build", "T_max": 90}}}}}}
    shared_ledger.write_text(json.dumps(old), encoding="utf-8")
    before = shared_ledger.read_bytes()
    _, _, conflicts = tool.build_plan(email, [email], workspaces, shared)
    assert any("different build fingerprint" in item for item in conflicts)
    assert shared_ledger.read_bytes() == before


def test_symlinked_admin_dies_root_is_refused(tmp_path):
    tool = _tool()
    email = "admin@example.com"
    workspaces, shared = tmp_path / "workspaces", tmp_path / "shared"
    source = workspaces / tool.workspace_id(email)
    source.mkdir(parents=True)
    external = tmp_path / "external-dies"
    external.mkdir()
    (shared / "dies").mkdir(parents=True)
    try:
        (source / "dies").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(FileNotFoundError):
        tool.build_plan(email, [email], workspaces, shared)


def test_symlinked_backup_root_is_refused_without_outside_write(tmp_path):
    tool = _tool()
    shared, outside = tmp_path / "shared", tmp_path / "outside"
    shared.mkdir()
    outside.mkdir()
    source = tmp_path / "source.yaml"
    source.write_text("safe source", encoding="utf-8")
    destination = shared / "destination.yaml"
    destination.write_text("old shared", encoding="utf-8")
    try:
        (shared / ".admin-catalog-backups").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="backup destination"):
        tool.apply_plan([tool.PlannedFile(source, destination, None, "test")], shared)
    assert destination.read_text(encoding="utf-8") == "old shared"
    assert list(outside.iterdir()) == []
