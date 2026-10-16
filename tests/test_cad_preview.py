"""Preview builds are isolated from released CAD and promote only checked assets."""
import json
import os
from pathlib import Path

import pytest

from motor_ai_sim.cad import build, runner


PROFILE = Path(__file__).parents[1] / "cad" / "reference" / "CIANO14_40_12.json"


def _fake_preview_fc(monkeypatch, *, code=0):
    def run(script, env, args, gui=False):
        assert script == "build_model.py"
        assert env["FC_PREVIEW"] == "1"
        assert env["FC_PREVIEW_DIR"] == os.path.dirname(env["FC_REPORT"])
        assert env["FC_SET"] == "" or env["FC_SET"] == "motor_length=21 mm"
        if code:
            return code, {"ok": False, "message": "synthetic gate failure"}
        out = Path(env["FC_PREVIEW_DIR"])
        mid = json.loads(Path(env["FC_PROFILE"]).read_text(encoding="utf-8"))["profile"]["motor_id"]
        asset = mid + "_preview.glb"
        (out / asset).write_bytes(b"checked-preview-glb")
        (out / "build_report.json").write_text(json.dumps({
            "freecad_version": "1.1.3",
            "preview": {"ok": True, "glb": asset, "freecad_version": "1.1.3", "triangles": 1234,
                        "tessellation_mm": 0.5, "worst_mesh_volume_error": 0.02},
        }), encoding="utf-8")
        return 0, {"ok": True, "message": "preview complete"}

    monkeypatch.setattr(build, "_fc", run)


def test_preview_writes_only_preview_subtree_and_preserves_released_files(tmp_path, monkeypatch):
    _fake_preview_fc(monkeypatch)
    out = tmp_path / "motor-output"
    out.mkdir()
    released = out / "released.step"
    released.write_bytes(b"released bytes")
    released_hash = build.store.sha256_file(str(released))
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    base_snapshot = build.input_fingerprint.fingerprint_profile(profile)
    expected_hash = build.store.canonical_hash({**base_snapshot["build_inputs"],
                                                "what_if_overrides": {"motor_length": "21 mm"}})

    result = build.main(["build", "--preview", "--profile-file", str(PROFILE), "--out-dir", str(out),
                         "--set", "motor_length=21 mm"])

    assert result == 0
    assert build.store.sha256_file(str(released)) == released_hash
    preview = out / "preview"
    manifest = json.loads((preview / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "preview" and manifest["ready"] and manifest["gate_passed"]
    assert manifest["build_inputs_hash"] == expected_hash
    asset = manifest["motor_id"] + "_preview.glb"
    assert manifest["files"][asset]["bytes"] == len(b"checked-preview-glb")
    assert {p.name for p in preview.iterdir()} == {asset, "preview.json", "manifest.json"}


def test_failed_preview_keeps_previous_preview_and_released_files(tmp_path, monkeypatch):
    _fake_preview_fc(monkeypatch, code=3)
    out = tmp_path / "motor-output"
    preview = out / "preview"
    preview.mkdir(parents=True)
    old_asset = preview / "old_preview.glb"
    old_asset.write_bytes(b"previous checked preview")
    released = out / "released.step"
    released.write_bytes(b"released bytes")

    result = build.main(["build", "--preview", "--profile-file", str(PROFILE), "--out-dir", str(out)])

    assert result == 3
    assert old_asset.read_bytes() == b"previous checked preview"
    assert released.read_bytes() == b"released bytes"
    assert not list(out.glob("preview.building-*"))


def test_preview_dry_run_is_rejected_without_starting_freecad(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build, "_fc", lambda *args, **kwargs: calls.append(args))
    result = build.main(["build", "--preview", "--dry-run", "--profile-file", str(PROFILE),
                         "--out-dir", str(tmp_path / "out")])
    assert result == 2
    assert calls == []


def test_full_build_swap_carries_preview_forward(tmp_path, monkeypatch):
    """A full output transaction retains the preview the worker published first."""
    out = tmp_path / "released"
    old_preview = out / "preview"
    old_preview.mkdir(parents=True)
    (old_preview / "manifest.json").write_text('{"ready": true}', encoding="utf-8")
    def fake_full_fc(script, env, args, gui=False):
        Path(env["FC_OUT"]).write_bytes(b"fcstd")
        Path(env["FC_REPORT"]).write_text(json.dumps({"gate": {"ok": True}, "freecad_version": "1.1.3",
                                                       "sketches_fully_constrained": 1, "sketches_total": 1}),
                                            encoding="utf-8")
        return 0, {"ok": True, "message": "full model built"}

    monkeypatch.setattr(build, "_fc", fake_full_fc)
    result = build.main(["build", "--profile-file", str(PROFILE), "--out-dir", str(out), "--no-gui", "--no-step",
                         "--no-bom", "--no-simplified"])
    assert result == 0
    assert json.loads((out / "preview" / "manifest.json").read_text(encoding="utf-8"))["ready"]
    mid = json.loads(PROFILE.read_text(encoding="utf-8"))["profile"]["motor_id"]
    assert (out / (mid + ".FCStd")).read_bytes() == b"fcstd"
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["build_inputs_hash"]


def test_preview_cli_and_freecad_preview_are_explicitly_gated():
    assert "--preview" in build.__doc__
    script = Path(build.__file__).parents[3] / "cad" / "fc_scripts" / "build_model.py"
    assert "FC_PREVIEW" in script.read_text(encoding="utf-8")


def test_full_glb_export_uses_explicit_freecad_entry_flag(tmp_path, monkeypatch):
    def fake_fc(script, env, args, gui=False):
        assert script == "export_models.py"
        assert env["FC_EXPORT_MODELS_MAIN"] == "1"
        Path(env["FC_REPORT"]).write_text(json.dumps({"glb": "m_full.glb", "triangles": 12,
                                                       "tessellation_mm": 0.1, "worst_mesh_volume_error": 0.001}),
                                            encoding="utf-8")
        return 0, {"ok": True}

    monkeypatch.setattr(build, "_fc", fake_fc)
    args = type("Args", (), {"freecadcmd": None, "timeout": 1, "normal_priority": False, "verbose": False})()
    code, info = build._models3d(str(tmp_path), "m", "m.FCStd", args)
    assert code == 0
    assert info["full_glb"] == "m_full.glb"


def test_freecad_preview_build_when_explicitly_enabled(tmp_path):
    try:
        freecadcmd = runner.find_freecadcmd()
    except FileNotFoundError:
        pytest.skip("freecadcmd is not installed")
    if os.environ.get("MOTRES_RUN_FREECAD_PREVIEW_TEST") != "1":
        pytest.skip("set MOTRES_RUN_FREECAD_PREVIEW_TEST=1 to run the opt-in FreeCAD integration test")
    out = tmp_path / "freecad-preview"
    out.mkdir()
    sentinel = out / "released_active.step"
    sentinel.write_bytes(b"released model sentinel")
    sentinel_hash = build.store.sha256_file(str(sentinel))
    result = build.main(["build", "--preview", "--profile-file", str(PROFILE), "--out-dir", str(out),
                         "--freecadcmd", freecadcmd, "--timeout", "900"])
    assert result == 0
    assert build.store.sha256_file(str(sentinel)) == sentinel_hash
    assert {p.name for p in out.iterdir()} == {"released_active.step", "preview"}
    manifest = json.loads((out / "preview" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ready"] and manifest["files"]
    assert all(name.endswith("_preview.glb") for name in manifest["files"])
