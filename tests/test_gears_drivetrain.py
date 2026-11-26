"""Stage 4 of the gear module: POST /api/gears/drivetrain (routes/gears_drivetrain.py).

A throwaway app with only the drivetrain router.  The motor side is a small synthetic saved-points source (the route's
``read_saved_motor`` is replaced) except in the reader tests, which feed the real reader synthetic catalogue documents.
Nothing here starts a solver.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import auth, gear_store
from motor_ai_sim.gears import GearboxRequest
from motor_ai_sim.routes import gears_drivetrain as gd
from motor_ai_sim.routes.gears import build_template
from motor_ai_sim.routes.gears_drivetrain import router

URL = "/api/gears/drivetrain"


def point(name, torque, rpm, eff=0.85, basis="T_em_avg_Nm"):
    return gd.saved_point({"name": name, "rpm": rpm,
                           "result": {basis: torque, "efficiency_pct": eff * 100.0, "mass_kg": 0.5}})


def marked_point(name, torque, rpm, eff=0.85, key="T_shaft_Nm"):
    return gd.saved_point({"name": name, "rpm": rpm, "result": {"T_em_avg_Nm": torque * 1.1, "efficiency_pct": eff * 100.0},
                           "summary": {key: torque, "shaft_convention": "fe_on_shaft_k3d_v1"}})


def synthetic_motor(peak=True, mass=0.5):
    duties = {"rated": point("rated", 1.0, 3500.0)}
    classes = {"rated": {"duty_class": "continuous"}}
    if peak:
        duties["pull"] = point("pull", 0.6, 3500.0, eff=0.8)
        classes["pull"] = {"duty_class": "short_time"}
    return {"die": "D1", "configuration": "C1", "duties": duties, "classes": classes, "rated": "rated",
            "mass_kg": mass, "mass_source": "synthetic full mass", "mass_partial": False}


@pytest.fixture
def inline():
    return build_template({})


@pytest.fixture
def motor_src(monkeypatch):
    holder = {"motor": synthetic_motor(), "calls": []}

    def fake(die, cfg, authorization):
        holder["calls"].append((die, cfg))
        return holder["motor"]
    monkeypatch.setattr(gd, "read_saved_motor", fake)
    return holder


@pytest.fixture
def client(monkeypatch, tmp_path, motor_src):
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    monkeypatch.setattr(gear_store, "root_dir", lambda: tmp_path / "gearboxes")
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    c.root = tmp_path / "gearboxes"
    return c


def body(inline, rows=None, **motor):
    rows = rows or [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0},
                    {"name": "peak", "torque_out_Nm": 5.0, "speed_out_rpm": 247.0}]
    return {"gearbox": {"request": inline}, "motor": {"die": "D1", "configuration": "C1", **motor}, "load": rows}


def post(client, b, **kw):
    r = client.post(URL, json=b, **kw)
    return r


def errors(r):
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["error"] == "validation"
    return d["errors"]


# ------------------------------------------------------------------------------------------------------- results

def test_covered_row_numbers(client, inline):
    r = post(client, body(inline))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["schema"] == "gear_drivetrain-1" and j["motoring_only"] is True and j["reverse_not_computed"] is True
    assert j["read_only"] is True and j["core_limitations"] and j["assumptions"]
    assert abs(j["gearbox"]["ratio_abs"] - 12.8842) < 1e-3 and j["gearbox"]["kind"] == "inline"
    row = j["rows"][0]
    assert row["status"] == "ok" and row["covered_by_saved_point"] is True and row["limited_by"] is None
    g = row["gearbox"]
    assert abs(g["speed_in_rpm"] - 247.0 * j["gearbox"]["ratio_abs"]) < 1e-6
    assert 0.70 < g["torque_in_Nm"] < 0.85                                  # 9.8 N*m / 12.88 / ~0.99
    assert row["motor_required"]["torque_Nm"] == g["torque_in_Nm"]
    # loss by stage: two stages, parts sum to the stage total, stages sum to the gearbox loss
    stages = g["loss_by_stage"]
    assert len(stages) == 2
    for s in stages:                      # the humanoid template has no bearing definitions and no grease drag: null, named, partial
        assert s["bearings_W"] is None and s["churning_W"] is None and s["total_partial"] is True
        assert s["loss_inputs_missing"] == ["bearing_definitions", "grease_drag_torque_Nm"]
        assert abs(s["mesh_W"] - s["total_W"]) < 1e-9
    assert g["loss_partial"] is True and g["efficiency_partial"] is True
    assert g["loss_inputs_missing"] == ["bearing_definitions", "grease_drag_torque_Nm"]
    assert abs(sum(s["total_W"] for s in stages) - g["loss_W"]) < 1e-9
    assert abs(g["power_balance_residual_W"]) < 1e-6 * g["power_in_W"]
    # margin against the saved point 1.0 N*m / 3500 rpm
    assert abs(row["margin"]["torque_Nm"] - (1.0 - g["torque_in_Nm"])) < 1e-12
    assert abs(row["margin"]["speed_rpm"] - (3500.0 - g["speed_in_rpm"])) < 1e-9
    # the required point differs from the saved one: no chain number, the two parts stay separate, each with its own point
    assert row["motor_point_matches"] is False and row["chain_efficiency"] is None
    assert row["chain_efficiency_reason"] == "motor_point_differs"
    parts = row["efficiency_parts"]
    assert parts["gearbox"]["efficiency"] == g["efficiency"] and parts["gearbox"]["at"]["torque_in_Nm"] == g["torque_in_Nm"]
    assert parts["motor"]["efficiency"] == 0.85 and parts["motor"]["at"] == {"torque_Nm": 1.0, "speed_rpm": 3500.0}
    # total mass and N*m/kg
    m = row["mass"]
    assert m["motor_kg"] == 0.5 and m["gearbox_kg"] == g["mass_kg"] and abs(m["total_kg"] - (0.5 + g["mass_kg"])) < 1e-12
    assert abs(row["output_torque_per_total_mass_Nm_per_kg"] - 9.8 / m["total_kg"]) < 1e-12
    assert m["sources"]["motor"] == "synthetic full mass" and "gearbox" in m["sources"]
    assert j["motor"]["mass_kg"] == 0.5 and j["motor"]["die"] == "D1"


def test_peak_row_uses_the_short_time_duty(client, inline):
    j = post(client, body(inline)).json()
    peak = j["rows"][1]
    assert peak["motor_duty"] == "pull" and peak["motor_saved_point"]["torque_Nm"] == 0.6
    assert peak["covered_by_saved_point"] is True and peak["chain_efficiency"] is None
    assert peak["efficiency_parts"]["motor"]["efficiency"] == 0.8


def test_not_covered_torque_is_named_and_not_clamped(client, inline):
    rows = [{"name": "peak", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}]      # needs ~0.77 N*m, saved 0.6
    row = post(client, body(inline, rows)).json()["rows"][0]
    assert row["covered_by_saved_point"] is False and row["limited_by"] == "motor_torque" and row["limits_exceeded"] == ["motor_torque"]
    assert row["margin"]["torque_Nm"] < 0 and row["margin"]["speed_rpm"] > 0
    assert row["motor_required"]["torque_Nm"] > 0.7                              # the requirement is reported as is
    assert row["chain_efficiency"] is None and row["chain_efficiency_reason"] == "motor_point_differs"
    assert row["status"] == "ok"


def test_not_covered_speed(client, inline):
    rows = [{"name": "continuous", "torque_out_Nm": 2.0, "speed_out_rpm": 300.0}]  # 3865 rpm at the motor, saved 3500
    row = post(client, body(inline, rows)).json()["rows"][0]
    assert row["covered_by_saved_point"] is False and row["limited_by"] == "motor_speed" and row["limits_exceeded"] == ["motor_speed"]
    assert row["margin"]["speed_rpm"] < 0 and row["margin"]["torque_Nm"] > 0


def test_both_limits_name_the_larger_shortfall(client, inline, motor_src):
    motor_src["motor"]["duties"]["rated"] = point("rated", 0.3, 3000.0)
    rows = [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}]   # 0.77 vs 0.3 and 3182 vs 3000
    row = post(client, body(inline, rows)).json()["rows"][0]
    assert row["covered_by_saved_point"] is False and set(row["limits_exceeded"]) == {"motor_torque", "motor_speed"}
    assert row["limited_by"] == "motor_torque"


def test_matching_point_is_flagged(client, inline, motor_src):
    base = post(client, body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}])).json()["rows"][0]
    g = base["gearbox"]
    motor_src["motor"]["duties"]["rated"] = point("rated", g["torque_in_Nm"] * 1.01, g["speed_in_rpm"])
    row = post(client, body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}])).json()["rows"][0]
    assert row["motor_point_matches"] is True and row["chain_efficiency"] is not None
    g2 = row["gearbox"]
    assert abs(row["chain_efficiency"] - 0.85 * g2["efficiency"]) < 1e-12 and row["chain_efficiency_partial_losses"] is True
    assert row["efficiency_parts"]["motor"]["at"]["torque_Nm"] == g["torque_in_Nm"] * 1.01
    # outside the 2 % window in either quantity: null again
    motor_src["motor"]["duties"]["rated"] = point("rated", g["torque_in_Nm"] * 1.05, g["speed_in_rpm"])
    r2 = post(client, body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}])).json()["rows"][0]
    assert r2["chain_efficiency"] is None and r2["chain_efficiency_reason"] == "motor_point_differs"
    motor_src["motor"]["duties"]["rated"] = point("rated", g["torque_in_Nm"], g["speed_in_rpm"] * 1.05)
    r3 = post(client, body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}])).json()["rows"][0]
    assert r3["chain_efficiency"] is None and r3["chain_efficiency_reason"] == "motor_point_differs"


def test_missing_mode_is_returned_not_computed(client, inline, motor_src):
    motor_src["motor"] = synthetic_motor(peak=False)
    j = post(client, body(inline)).json()
    ok, miss = j["rows"]
    assert ok["status"] == "ok" and miss["status"] == "missing" and miss["covered_by_saved_point"] is None
    assert "peak" in miss["reason"] and miss["chain_efficiency"] is None and "margin" not in miss
    assert miss["chain_efficiency_reason"] == "no_saved_point" and miss["efficiency_parts"]["motor"] is None
    assert miss["efficiency_parts"]["gearbox"]["efficiency"] > 0
    assert miss["gearbox"]["torque_in_Nm"] > 0                                      # what the gearbox needs is still shown


def test_saved_duty_without_result_is_missing(client, inline, motor_src):
    motor_src["motor"]["duties"]["pull"] = gd.saved_point({"name": "pull", "rpm": 3500})
    row = post(client, body(inline)).json()["rows"][1]
    assert row["status"] == "missing" and row["reason"] == "no saved result for this duty" and row["motor_duty"] == "pull"


def test_explicit_duty_mapping_and_other_modes(client, inline, motor_src):
    motor_src["motor"]["duties"]["boost"] = point("boost", 0.9, 4000.0)
    rows = [{"name": "boost", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}]
    assert post(client, body(inline, rows)).json()["rows"][0]["status"] == "missing"         # nothing maps "boost"
    row = post(client, body(inline, rows, duties={"boost": "boost"})).json()["rows"][0]
    assert row["status"] == "ok" and row["motor_duty"] == "boost" and row["covered_by_saved_point"] is True
    row = post(client, body(inline, rows, duties={"boost": "nope"})).json()["rows"][0]
    assert row["status"] == "missing" and "does not exist" in row["reason"]


def test_no_motor_mass_gives_no_per_mass_number(client, inline, motor_src):
    motor_src["motor"]["mass_kg"] = None
    row = post(client, body(inline)).json()["rows"][0]
    assert row["output_torque_per_total_mass_Nm_per_kg"] is None and row["mass"]["total_kg"] is None


def test_legacy_torque_is_labelled(client, inline, motor_src):
    motor_src["motor"]["duties"]["rated"] = point("rated", 1.0, 3500.0, basis="T_em_avg_Nm")
    row = post(client, body(inline)).json()["rows"][0]
    assert row["motor_saved_point"]["torque_basis"] == "field_legacy"


def test_marked_summary_uses_shaft_quantities(client, inline, motor_src):
    motor_src["motor"]["duties"]["rated"] = marked_point("rated", 1.0, 3500.0)
    row = post(client, body(inline)).json()["rows"][0]
    sp = row["motor_saved_point"]
    assert sp["torque_basis"] == "shaft" and sp["torque_Nm"] == 1.0 and sp["shaft_convention"] == "fe_on_shaft_k3d_v1"


def _marked(summary, rpm=100, result=None):
    return gd.saved_point({"name": "a", "rpm": rpm, "result": result or {"efficiency_pct": 90},
                           "summary": {**summary, "shaft_convention": "x"}})


def test_marked_point_prefers_coupling_torque():
    # P_shaft_net_W / omega wins over T_shaft_Nm; the speed is the saved one (blocks first)
    both = _marked({"T_shaft_Nm": 2.0, "P_shaft_net_W": 1000.0, "T_em_avg_Nm": 2.2, "rpm": 1000})
    assert abs(both["torque_Nm"] - 1000.0 / (1000 * math.pi / 30)) < 1e-9
    assert both["torque_basis"] == "shaft" and both["torque_key"] == "P_shaft_net_W"
    # only the shaft torque
    only_t = _marked({"T_shaft_Nm": 2.0, "T_em_avg_Nm": 2.2})
    assert only_t["torque_Nm"] == 2.0 and only_t["torque_basis"] == "shaft" and only_t["torque_key"] == "T_shaft_Nm"
    # a negative net power is no motoring point
    neg = _marked({"P_shaft_net_W": -50.0, "rpm": 1000})
    assert "missing" in neg and neg["missing"] != "shaft_key_unrecognised"
    # omega 0: no division, falls to T_shaft_Nm, or is unrecognised without it
    zero = _marked({"P_shaft_net_W": 100.0, "T_shaft_Nm": 1.5, "rpm": 0}, rpm=0)
    assert "torque_Nm" not in zero and "missing" in zero
    zero_t = _marked({"P_shaft_net_W": 100.0, "T_shaft_Nm": 1.5}, rpm=0, result={"rpm": 0})
    assert "missing" in zero_t
    only_p0 = gd.saved_point({"name": "a", "rpm": 0, "summary": {"P_shaft_net_W": 100.0, "shaft_convention": "x"}})
    assert only_p0 == {"duty": "a", "missing": "shaft_key_unrecognised"}
    # the removed names are not read
    dead = _marked({"T_coupling_Nm": 1.8, "T_shaft_em_Nm": 1.7})
    assert dead == {"duty": "a", "missing": "shaft_key_unrecognised"}
    no_marker = gd.saved_point({"name": "a", "rpm": 100, "result": {"efficiency_pct": 90},
                                "summary": {"T_shaft_Nm": 2.0, "P_shaft_net_W": 1000.0, "T_em_avg_Nm": 2.2}})
    assert no_marker["torque_Nm"] == 2.2 and no_marker["torque_basis"] == "field_legacy"
    marked_without_shaft = gd.saved_point({"name": "a", "rpm": 100, "result": {"T_em_avg_Nm": 2.2},
                                           "summary": {"shaft_convention": "x"}})
    assert marked_without_shaft == {"duty": "a", "missing": "shaft_key_unrecognised"}   # a marked record never falls back to field torque


def test_computed_losses_are_numbers_and_unpartial():
    class S:
        stage_name, stage_id, efficiency, state = "s", "sid", 0.99, "estimated"
        power_loss_mesh_W, power_loss_bearings_W, power_loss_churning_drag_W, power_loss_total_W = 1.0, 0.5, 0.25, 1.75
        bearing_loads, bearing_ratings, flags, missing_inputs = [], [object()], [], []

    class R:
        stage_results = [S()]
    st = gd._stage_losses(R())[0]
    assert st["bearings_W"] == 0.5 and st["churning_W"] == 0.25 and st["total_W"] == 1.75
    assert st["total_partial"] is False and st["loss_inputs_missing"] == []
    S.flags = ["grease_drag_not_provided_assumed_zero"]
    st = gd._stage_losses(R())[0]
    assert st["churning_W"] is None and st["total_W"] == 1.5 and st["loss_inputs_missing"] == ["grease_drag_torque_Nm"]


def test_assumptions_state_the_coverage_basis(client, inline):
    a = " ".join(post(client, body(inline)).json()["assumptions"])
    assert "covered_by_saved_point" in a and "one saved motor point" in a and "motor_point_differs" in a


def test_gearbox_failure_is_a_row_status(client, inline, monkeypatch):
    def boom(self, **kw):
        raise ValueError("synthetic failure")
    from motor_ai_sim.gears.gearbox import Gearbox
    monkeypatch.setattr(Gearbox, "evaluate_point", boom)
    row = post(client, body(inline)).json()["rows"][0]
    assert row["status"] == "gearbox_failed" and row["reason"] == "synthetic failure" and "margin" not in row


# --------------------------------------------------------------------------------------------- saved gearbox record

def save_record(client, inline):
    r = GearboxRequest.from_dict(inline)
    rec = gear_store.create("proj1", "Joint", r.to_dict(), None, "tester")
    return rec["id"]


def test_record_id_gives_the_same_numbers_as_inline(client, inline):
    rid = save_record(client, inline)
    a = post(client, body(inline)).json()
    b = body(inline)
    b["gearbox"] = {"record_id": rid, "project": "proj1"}
    j = post(client, b).json()
    assert j["gearbox"]["kind"] == "record" and j["gearbox"]["record_id"] == rid and j["gearbox"]["project"] == "proj1"
    assert j["gearbox"]["record_flags"] == [] and j["gearbox"]["record_name"] == "Joint"
    assert j["rows"] == a["rows"]


def test_unknown_record_and_wrong_scope(client, inline):
    rid = save_record(client, inline)
    b = body(inline)
    b["gearbox"] = {"record_id": rid}                                               # saved under proj1, not the workspace
    assert post(client, b).status_code == 404
    b["gearbox"] = {"record_id": "gb_0123456789abcdef", "project": "proj1"}
    assert post(client, b).status_code == 404
    b["gearbox"] = {"record_id": rid, "project": "../x"}
    assert errors(post(client, b))[0]["path"] == "/gearbox/project"


# ------------------------------------------------------------------------------------------------------ 422 paths

@pytest.mark.parametrize("edit,path", [
    (lambda b: b.pop("load"), "/load"),
    (lambda b: b.pop("motor"), "/motor"),
    (lambda b: b.pop("gearbox"), "/gearbox"),
    (lambda b: b.update(extra=1), "/extra"),
    (lambda b: b["load"].__setitem__(0, {"name": "x", "speed_out_rpm": 1}), "/load/0/torque_out_Nm"),
    (lambda b: b["load"][0].update(torque_out_Nm=-1), "/load/0/torque_out_Nm"),
    (lambda b: b["load"][1].update(speed_out_rpm=0), "/load/1/speed_out_rpm"),
    (lambda b: b["load"][0].update(torque_out_Nm=True), "/load/0/torque_out_Nm"),
    (lambda b: b["load"][0].update(speed_out_rpm="fast"), "/load/0/speed_out_rpm"),
    (lambda b: b["load"][1].update(name="continuous"), "/load/1/name"),
    (lambda b: b["load"][0].update(color="red"), "/load/0/color"),
    (lambda b: b["load"].__setitem__(0, {"torque_out_Nm": 1, "speed_out_rpm": 1}), "/load/0/name"),
    (lambda b: b.update(load=[]), "/load"),
    (lambda b: b["motor"].pop("die"), "/motor/die"),
    (lambda b: b["motor"].update(configuration=""), "/motor/configuration"),
    (lambda b: b["motor"].update(duties={"peak": 5}), "/motor/duties/peak"),
    (lambda b: b["gearbox"].update(record_id="gb_x"), "/gearbox"),
    (lambda b: b["gearbox"].update(project="p"), "/gearbox/project"),
    (lambda b: b.update(gearbox={}), "/gearbox"),
    (lambda b: b.update(gearbox={"request": "x"}), "/gearbox/request"),
])
def test_validation_paths(client, inline, edit, path):
    b = body(inline)
    edit(b)
    assert errors(post(client, b))[0]["path"] == path


def test_body_must_be_an_object(client):
    assert errors(client.post(URL, json=[1]))[0]["path"] == ""


def test_core_rejection_keeps_the_nested_path(client, inline):
    bad = copy.deepcopy(inline)
    bad["stages"] = []
    e = errors(post(client, body(bad)))
    assert e[0]["path"].startswith("/gearbox/request/stages")


def test_inline_operation_is_overridden(client, inline):
    other = copy.deepcopy(inline)
    other["operation"] = "assembly"
    assert post(client, body(other)).status_code == 200


# --------------------------------------------------------------------------------------------------- access, no write

def tree_hash(root: Path):
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_nothing_is_written(client, inline, motor_src):
    rid = save_record(client, inline)
    before = tree_hash(client.root)
    b = body(inline)
    b["gearbox"] = {"record_id": rid, "project": "proj1"}
    assert post(client, b).status_code == 200
    assert post(client, body(inline)).status_code == 200
    assert tree_hash(client.root) == before and len(before) == 1


def test_only_post_exists(client):
    assert client.get(URL).status_code == 405
    assert client.put(URL, json={}).status_code == 405
    assert client.delete(URL).status_code == 405
    assert all(r.methods == {"POST"} for r in router.routes)


@pytest.fixture
def enforced(monkeypatch, tmp_path, motor_src):
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **kw: (
        {"uid": "u", "email": authorization.split()[-1] + "@example.com", "role": authorization.split()[-1]}
        if authorization else None))
    monkeypatch.setattr(gear_store, "root_dir", lambda: tmp_path / "gearboxes")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_staff_only(enforced, inline, motor_src):
    b = body(inline)
    assert post(enforced, b).status_code == 401
    assert post(enforced, b, headers={"Authorization": "Bearer client"}).status_code == 403
    assert motor_src["calls"] == []                                                    # refused before any motor read
    assert post(enforced, b, headers={"Authorization": "Bearer engineer"}).status_code == 200
    assert motor_src["calls"] == [("D1", "C1")]


# ------------------------------------------------------------------------------------------------- reader, pure parts

def test_saved_point_reads_result_then_summary():
    p = gd.saved_point({"name": "a", "rpm": 1000, "result": {"T_em_avg_Nm": 2.0, "efficiency_pct": 90.0},
                        "summary": {"T_shaft_Nm": 1.9, "shaft_convention": "fe_on_shaft_k3d_v1"}})
    assert p["torque_Nm"] == 1.9 and p["torque_basis"] == "shaft" and p["shaft_convention"] == "fe_on_shaft_k3d_v1"
    assert p["speed_rpm"] == 1000 and abs(p["efficiency"] - 0.9) < 1e-12
    assert gd.saved_point({"name": "a"})["missing"] == "no saved result for this duty"
    assert "missing" in gd.saved_point({"name": "a", "result": {"efficiency_pct": 90}})
    assert gd.saved_point({"name": "a", "rpm": 1, "result": {"T_em_avg_Nm": 1, "efficiency_pct": 140}})["efficiency"] is None
    assert gd.saved_point({"name": "a", "rpm": 1, "result": {"T_em_avg_Nm": 1, "eta_shaft": 0.7}})["efficiency"] == 0.7


def test_resolve_duty_rules():
    m = {"duties": {"rated": {}, "short": {}, "peak 2": {}, "cont": {}},
         "classes": {"rated": {"duty_class": "short_time"}, "short": {"duty_class": "short_time"}, "cont": {"duty_class": "continuous"}},
         "rated": "rated"}
    assert gd.resolve_duty(m, "continuous", None) == ("cont", None)                   # rated is short-time: not used
    assert gd.resolve_duty(m, "Peak", None) == ("rated", None)
    m["classes"] = {}
    m["rated"] = "rated"
    assert gd.resolve_duty(m, "continuous", None)[0] == "rated"
    assert gd.resolve_duty(m, "peak", None)[0] == "peak 2"
    assert gd.resolve_duty(m, "peak", "cont") == ("cont", None)
    assert gd.resolve_duty(m, "x", None)[0] is None and gd.resolve_duty({"duties": {}}, "continuous", None)[0] is None


def test_motor_from_docs_skips_test_duties_and_names_mass():
    doc = {"duties": [{"name": "rated", "rpm": 100, "result": {"T_em_avg_Nm": 1.0}},
                      {"name": "noload_1", "rpm": 100, "result": {"T_em_avg_Nm": 0.0}}],
           "mass": {"full_kg": 0.9, "active_kg": 0.5, "partial": False}}
    m = gd.motor_from_docs("D", "C", doc, {}, "rated")
    assert list(m["duties"]) == ["rated"] and m["mass_kg"] == 0.9 and "full" in m["mass_source"] and m["mass_partial"] is False
    doc["mass"] = {"full_kg": None, "active_kg": 0.5, "partial": True}
    m = gd.motor_from_docs("D", "C", doc, {}, "rated")
    assert m["mass_kg"] == 0.5 and m["mass_partial"] is True and "partial" in m["mass_source"]
    m = gd.motor_from_docs("D", "C", {"duties": []}, {}, None)
    assert m["mass_kg"] is None and m["mass_source"] is None


# ----------------------------------------------------------------------------------- reader against catalogue stand-ins

def test_reader_access_denied_is_404_and_reads_nothing(monkeypatch, inline, tmp_path):
    from motor_ai_sim.routes import family
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    seen = []
    monkeypatch.setattr(family, "_require_die_access",
                        lambda die, authorization, cfg=None, style="file": (_ for _ in ()).throw(
                            HTTPException(404, detail="configuration 'D1/C1' not found")))
    monkeypatch.setattr(family, "_cfg_file", lambda *a: seen.append(a))
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(URL, json=body(inline))
    assert r.status_code == 404 and seen == []


def test_reader_uses_saved_numbers_only_and_leaks_nothing(monkeypatch, inline, tmp_path):
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import family
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    cfg = tmp_path / "C1.yaml"
    cfg.write_text("name: C1\nwinding: {secret_turns: SECRET_MARK_1}\nduties:\n"
                   "- {name: rated, rpm: 3500, result: {T_em_avg_Nm: 1.0, efficiency_pct: 85.0, mass_kg: 0.4, note: SECRET_MARK_2}}\n"
                   "- {name: noload_3021, rpm: 3500, result: {T_em_avg_Nm: 0.0}}\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(family, "_require_die_access", lambda die, authorization, cfg=None, style="file": called.append((die, cfg)) or {})
    monkeypatch.setattr(family, "_cfg_file", lambda die, c: cfg)
    monkeypatch.setattr(family, "_overlay_mass", lambda die, c: {**c, "mass": {"full_kg": 0.9, "active_kg": 0.4, "partial": False,
                                                                                  "source": "stored"}})
    monkeypatch.setattr(duty_results, "get", lambda die, c: {})
    before = cfg.read_bytes()
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(URL, json=body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}]))
    assert r.status_code == 200, r.text
    assert called == [("D1", "C1")] and cfg.read_bytes() == before
    j = r.json()
    assert "SECRET_MARK" not in json.dumps(j)
    row = j["rows"][0]
    assert row["motor_duty"] == "rated" and row["covered_by_saved_point"] is True and j["motor"]["mass_kg"] == 0.9
    assert set(row["motor_saved_point"]) == {"torque_Nm", "speed_rpm", "efficiency", "torque_basis", "shaft_convention"}


# ----------------------------------------------------------------------------------------------- review #907 regressions

def test_failed_gearbox_is_a_failed_row_before_any_comparison(client, inline, motor_src):
    ok = post(client, body(inline)).json()["rows"][0]["gearbox"]
    bad = copy.deepcopy(inline)
    bad["stages"][0]["n_planets"] = 10                          # neighbour_planet_interference: the assembly fails
    motor_src["motor"]["duties"]["rated"] = point("rated", ok["torque_in_Nm"], ok["speed_in_rpm"])
    r = post(client, body(bad, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}]))
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["status"] == "gearbox_failed" and row["chain_efficiency"] is None
    assert row["chain_efficiency_reason"] == "gearbox_failed" and row["gearbox_state"] == "failed"
    assert "covered_by_saved_point" in row and row["covered_by_saved_point"] is None and "margin" not in row
    assert "gearbox" not in row and isinstance(row["gearbox_flags"], list)


def test_saved_speed_beats_the_editable_duty_speed():
    p = gd.saved_point({"name": "rated", "rpm": 2000, "summary": {"T_em_avg_Nm": 1, "speed_rpm": 1000, "efficiency_pct": 85}})
    assert p["speed_rpm"] == 1000
    p = gd.saved_point({"name": "rated", "rpm": 2000, "result": {"T_em_avg_Nm": 1, "efficiency_pct": 85}})
    assert p["speed_rpm"] == 2000                                # no saved speed: the duty's field is the fallback


@pytest.mark.parametrize("value", [10 ** 400, -10 ** 400, 10 ** 309])
def test_huge_integers_are_422_not_500(client, inline, value):
    b = body(inline)
    b["load"][0]["torque_out_Nm"] = value
    assert errors(post(client, b))[0]["path"] == "/load/0/torque_out_Nm"
    b = body(inline)
    b["load"][1]["speed_out_rpm"] = value
    assert errors(post(client, b))[0]["path"] == "/load/1/speed_out_rpm"


def test_huge_saved_integer_is_not_a_number():
    assert gd._num(10 ** 400) is None
    assert gd.saved_point({"name": "a", "rpm": 10 ** 400, "result": {"T_em_avg_Nm": 1}})["missing"]


def test_row_count_limits(client, inline):
    rows = [{"name": f"r{i}", "torque_out_Nm": 1.0, "speed_out_rpm": 100.0} for i in range(20)]
    assert len(post(client, body(inline, rows)).json()["rows"]) == 20
    rows.append({"name": "r20", "torque_out_Nm": 1.0, "speed_out_rpm": 100.0})
    assert errors(post(client, body(inline, rows)))[0]["path"] == "/load"


def test_archived_record_is_labelled_and_still_read(client, inline):
    rid = save_record(client, inline)
    gear_store.set_state("proj1", rid, "archived", "tester", None)
    b = body(inline)
    b["gearbox"] = {"record_id": rid, "project": "proj1"}
    j = post(client, b).json()
    assert j["gearbox"]["record_state"] == "archived" and j["rows"][0]["status"] == "ok"


def test_independent_hand_check_numbers(client, inline):
    """Hand calculation (not read back from the endpoint): ratio (1+104/40)*(1+98/38) = 12.884210526; n_in = 247 * ratio =
    3182.4 rpm; mesh losses 3.163751 W on 253.484639 W useful output give T_in = 0.77011425 N*m; mass 0.2184477343 + 0.5 kg."""
    j = post(client, body(inline)).json()
    row = j["rows"][0]
    assert abs(j["gearbox"]["ratio_abs"] - 12.8842105263) < 1e-9
    assert abs(row["gearbox"]["speed_in_rpm"] - 3182.4) < 1e-6 * 3182.4
    assert abs(row["gearbox"]["torque_in_Nm"] - 0.7701142525) < 1e-6
    assert abs(row["gearbox"]["power_out_W"] - 253.484639243) < 1e-5 and abs(row["gearbox"]["loss_W"] - 3.163751057) < 1e-5
    assert abs(row["margin"]["torque_Nm"] - 0.2298857475) < 1e-6 and abs(row["margin"]["speed_rpm"] - 317.6) < 1e-3
    assert abs(row["mass"]["total_kg"] - 0.7184477343) < 1e-6
    assert abs(row["output_torque_per_total_mass_Nm_per_kg"] - 13.6405190417) < 1e-5


# ----------------------------------------------------------------------------------------- page PR follow-ups

def test_marked_record_without_a_shaft_key_is_missing_not_field_torque(client, inline, motor_src):
    pt = gd.saved_point({"name": "rated", "rpm": 3500.0, "result": {"T_em_avg_Nm": 1.0, "efficiency_pct": 85.0},
                         "summary": {"shaft_convention": "fe_on_shaft_k3d_v1", "T_other_Nm": 1.0}})
    assert pt == {"duty": "rated", "missing": "shaft_key_unrecognised"}
    # an unmarked record keeps the field torque and says so
    legacy = gd.saved_point({"name": "rated", "rpm": 3500.0, "result": {"T_em_avg_Nm": 1.0}})
    assert legacy["torque_basis"] == "field_legacy" and legacy["torque_Nm"] == 1.0
    # a marked record with a recognised key is still a shaft point
    assert gd.saved_point({"name": "r", "rpm": 10.0, "summary": {"T_shaft_Nm": 2.0, "shaft_convention": "m"}})["torque_basis"] == "shaft"
    motor_src["motor"]["duties"]["rated"] = pt
    row = post(client, body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}])).json()["rows"][0]
    assert row["status"] == "missing" and row["reason"] == "shaft_key_unrecognised"
    assert row["chain_efficiency"] is None and row["chain_efficiency_reason"] == "no_saved_point"
    assert row["gearbox"]["torque_in_Nm"] > 0                                  # the gearbox side is still computed


def test_mass_overlay_with_a_cold_cache_reads_only_and_writes_nothing(monkeypatch, tmp_path):
    from motor_ai_sim import fscache
    from motor_ai_sim.routes import family
    dies = tmp_path / "dies"
    (dies / "D1").mkdir(parents=True)
    monkeypatch.setattr(family, "_die_dir", lambda die: dies / die)
    fscache.clear()
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    cfg = {"name": "C1", "duties": [{"name": "rated", "result": {"mass_kg": 0.4}}]}
    out = family._overlay_mass("D1", cfg)
    assert out is not cfg and cfg == {"name": "C1", "duties": [{"name": "rated", "result": {"mass_kg": 0.4}}]}   # a copy, the input untouched
    assert out["mass"]["partial"] is True and out["duties"][0]["result"]["mass_kg"] == 0.4
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before     # nothing written
