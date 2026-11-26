"""shaft_conv follow-up of PR #900: the stored result carries it only beside a marked summary; the project motors block
shows the shaft fields of marked records only; a one-off repair lists / restores the flag.  Temp stores only."""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import shaft_convention as SC
from motor_ai_sim import shaft_conv_repair as repair
from motor_ai_sim.api import app
from motor_ai_sim.development import motors

DIE, CFG, DUTY = "SYNTH DIE 40", "L40", "rated"
client = TestClient(app)
MARKED = {"shaft_convention": SC.SHAFT_CONVENTION, "T_shaft_Nm": 4.1, "shaft_k3d": 0.97, "op_mode": "motor",
          "rpm": 6000.0, "I_phase_rms_A": 85.0, "gamma_deg": 12.0, "T_em_avg_Nm": 4.2}
UNMARKED = {"rpm": 6000.0, "I_phase_rms_A": 85.0, "gamma_deg": 12.0, "T_em_avg_Nm": 4.2}


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0, "magnet_height": 3.0,
                     "motor_length": 40.0}}, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor", "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor", "saved_at": "2026-09-02T10:00:00", "current_arms": 85.0,
                    "rpm": 6000.0, "gamma_deg": 12.0, "note": ""}]}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


def _duty(dies):
    doc = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    return next(d for d in doc["duties"] if d["name"] == DUTY)


def _save_duty(summary):
    body = {"name": DUTY, "mode": "motor", "from_current": False, "current_arms": 85.0, "rpm": 6000.0,
            "gamma_deg": 12.0, "drive": "current", "mesh": {"sim.drive": "current"}, "summary": dict(summary)}
    assert client.post("/api/family/duty", json={"die": DIE, "config": CFG, "duty": body}).status_code == 200


def _result(flag=None, marker=None, **extra):
    body = {"die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
            "result": {"efficiency_pct": 90.0, "mass_kg": 1.0, **({"shaft_conv": flag} if flag is not None else {})}}
    if marker:
        body["shaft_convention"] = marker
    return client.post("/api/family/duty_result", json=body)


def _run(summary):
    return client.post("/api/family/duty_run", json={"die": DIE, "config": CFG, "duty": DUTY, "drive": "current",
                                                     "summary": dict(summary), "allow_unverified": True})


def test_marked_summary_and_client_flag_store_shaft_conv(dies):
    _save_duty(MARKED)
    assert _result(flag=1).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1


def test_unmarked_summary_and_client_flag_do_not_store_it(dies):
    _save_duty(UNMARKED)
    assert _result(flag=1).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]
    # ... and the client's own marker string alone cannot be wrong about the summary it saves later
    assert _run(UNMARKED).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]


def test_result_before_run_with_marker_string_then_run_decides(dies):
    # the real order: /duty_result first (the stored summary is still the old run's), /duty_run after
    _save_duty(UNMARKED)
    assert _result(flag=1, marker=SC.SHAFT_CONVENTION).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1
    assert _run(MARKED).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1
    assert _duty(dies)["runs"]["current"]["result"]["shaft_conv"] == 1


def test_no_flag_with_marked_summary_is_derived_by_the_run_save(dies):
    _save_duty(UNMARKED)
    assert _result().status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]
    assert _run(MARKED).status_code == 200
    assert _duty(dies)["result"]["shaft_conv"] == 1


def test_run_with_unmarked_summary_drops_a_stale_flag(dies):
    _save_duty(MARKED)
    _result(flag=1)
    assert _run(UNMARKED).status_code == 200
    assert "shaft_conv" not in _duty(dies)["result"]


def test_unmarked_result_keeps_its_old_shape(dies):
    _save_duty(UNMARKED)
    assert _result().status_code == 200
    res = _duty(dies)["result"]
    assert set(res) == {"efficiency_pct", "mass_kg", "recorded_at", "build_sig"} or set(res) == {
        "efficiency_pct", "mass_kg", "recorded_at"}


# ── the project motors block ────────────────────────────────────────────────────────────────

def test_motors_block_shaft_fields_on_marked_records_only():
    cfg = {"duties": [
        {"name": "m", "rpm": 1000, "result": {"t_em_avg_nm": 8, "p_mech_w": 700, "shaft_conv": 1},
         "summary": {**MARKED, "P_mech_W": 700.0}},
        {"name": "u", "rpm": 1000, "result": {"t_em_avg_nm": 8, "p_mech_w": 700},
         "summary": {"T_em_avg_Nm": 8.0, "P_mech_W": 700.0, "T_shaft_Nm": 7.9, "shaft_k3d": 0.9}}]}
    rows = motors._results("SYNTH", "A", cfg)
    em_m, em_u = rows[0]["electromagnetic"], rows[1]["electromagnetic"]
    assert em_m["t_shaft_nm"] == 4.1 and em_m["shaft_k3d"] == 0.97 and em_m["shaft_convention"] == SC.SHAFT_CONVENTION
    assert em_m["t_em_avg_nm"] == 8 and em_m["p_mech_w"] == 700
    assert not {"t_shaft_nm", "shaft_k3d", "shaft_convention"} & set(em_u)      # unmarked: exact old shape


def test_missing_shaft_number_is_absent_not_zero():
    out = motors.shaft_fields({"shaft_convention": SC.SHAFT_CONVENTION, "shaft_k3d": None})
    assert out == {"shaft_convention": SC.SHAFT_CONVENTION}
    assert motors.shaft_fields(None) == {} and motors.shaft_fields({"T_shaft_Nm": 1.0}) == {}


def test_safe_projection_passes_only_the_marker_and_finite_numbers():
    assert motors.safe_shaft_fields({"shaft_convention": "other", "t_shaft_nm": 1}) == {}
    assert motors.safe_shaft_fields({"shaft_convention": SC.SHAFT_CONVENTION, "t_shaft_nm": float("nan"),
                                     "shaft_k3d": 0.9}) == {"shaft_convention": SC.SHAFT_CONVENTION, "shaft_k3d": 0.9}


# ── the repair tool ─────────────────────────────────────────────────────────────────────────

def _doc():
    return {"duties": [
        {"name": "a", "summary": dict(MARKED), "result": {"efficiency_pct": 90.0},
         "runs": {"pwm_voltage": {"summary": dict(MARKED), "result": {"efficiency_pct": 88.0}}}},
        {"name": "b", "summary": dict(MARKED), "result": {"efficiency_pct": 90.0, "shaft_conv": 1}},
        {"name": "c", "summary": dict(UNMARKED), "result": {"efficiency_pct": 90.0}},
        {"name": "d", "summary": dict(MARKED)}]}


def test_plan_lists_duty_and_drive_without_values():
    plan = repair.plan_config(_doc())
    assert plan == [{"duty": "a", "drive": None}, {"duty": "a", "drive": "pwm_voltage"}]


def test_apply_sets_only_the_one_key_and_is_idempotent():
    doc = _doc()
    before = yaml.safe_dump(doc)
    assert repair.apply_config(doc) == 2
    assert doc["duties"][0]["result"] == {"efficiency_pct": 90.0, "shaft_conv": 1.0}
    after = yaml.safe_load(yaml.safe_dump(doc))
    del after["duties"][0]["result"]["shaft_conv"]
    del after["duties"][0]["runs"]["pwm_voltage"]["result"]["shaft_conv"]
    assert yaml.safe_dump(after) == before
    assert repair.apply_config(doc) == 0 and repair.plan_config(doc) == []


def test_repair_dry_run_writes_nothing_and_apply_backs_up(dies, tmp_path):
    p = dies / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["duties"][0].update({"summary": dict(MARKED), "result": {"efficiency_pct": 90.0}})
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    original = p.read_text(encoding="utf-8")
    dry = repair.repair()
    assert dry["applied"] is False and [(r["die"], r["config"], r["duty"]) for r in dry["rows"]] == [(DIE, CFG, DUTY)]
    assert p.read_text(encoding="utf-8") == original
    with pytest.raises(ValueError):
        repair.repair(apply=True)
    done = repair.repair(apply=True, backup_dir=tmp_path / "bak")
    assert done["saved"] == 1 and not done["failed"]
    assert _duty(dies)["result"]["shaft_conv"] == 1
    backups = list((tmp_path / "bak" / DIE).glob(f"{CFG}.yaml.bak-shaftconv-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    assert repair.scan() == []
