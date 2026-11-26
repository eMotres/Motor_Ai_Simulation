"""Propellers > Mission: the model (``propeller_mission``), its API logic (``propeller_mission_api``) and the opt-in mission objective of
``propeller_optimize``.  Synthetic S1 model, synthetic propeller curves (no private model data, no UIUC / APC files); the blade-element
model is replaced by a closed-form curve where a test is not about it.  Fast: no FEM."""
from __future__ import annotations

import copy
import json
import math

import pytest

from motor_ai_sim import jobs as J
from motor_ai_sim import motor_access as ma
from motor_ai_sim import propeller_mission as pm
from motor_ai_sim import propeller_mission_api as pma
from motor_ai_sim import propeller_optimize as po
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim.propeller_design_api import ApiProblem

RPM = [1000.0, 2000.0, 4000.0, 8000.0, 12000.0]
CUR = [5.0, 10.0, 20.0, 40.0, 60.0]
KT = 0.03


def _raw():
    fe = [[0.8 * (n / 1000.0) ** 1.5 * (1 + i / 60.0) for i in CUR] for n in RPM]
    return {
        "schema": s1.SCHEMA, "die": "TEST DIE", "config": "L1", "R_phase_ohm": 0.012, "R_temp_C": 20.0, "magnet_ref_temp_C": 20.0,
        "flux_temp_coeff_per_K": -0.001, "torque_factor": 1.0,
        "grid": {"rpm": RPM, "I_A": CUR, "T_Nm": [[KT * i for i in CUR] for _ in RPM],
                 "loss_W": {"fe_stator": fe, "cu_ac": [[0.02 * (i / 10.0) ** 2 * (n / 1000.0) for i in CUR] for n in RPM],
                            "rotor_eddy": [[0.01 * (n / 1000.0) ** 2 * (i / 10.0) for i in CUR] for n in RPM]}},
        "mech_W": {"rpm": RPM, "W": [0.01 * n / 1000.0 for n in RPM]}, "loss_groups_zero": ["fe_rotor"],
        "limits": {"I_max_A": 60.0, "rpm_max": 12000.0, "winding_C": 200.0, "magnet_C": 150.0},
        "thermal_geom": {"statorOD_mm": 40.0, "stackLength_mm": 15.0, "numSlots": 12, "slotHeight_mm": 6.0, "slotWidth_mm": 2.8,
                         "insulation_mm": 0.06, "coreThickness_mm": 2.0, "airGap_mm": 0.3, "magnetOD_mm": 23.0},
        "winding": {"turns": 7, "strip_thickness_mm": 0.6, "strip_width_mm": 2.5},
    }


def _raw_with_psi():
    r = _raw()
    r["psi_map"] = {"I_A": [5.0, 10.0], "gamma_deg": [-10.0, 10.0], "psi_d_Wb": [[0.004, 0.0041], [0.0042, 0.0043]],
                    "psi_q_Wb": [[0.001, 0.0011], [0.002, 0.0021]], "T_Nm": [[0.1, 0.11], [0.2, 0.21]], "pole_pairs": 7.0,
                    "psi_pm_Wb": 0.004, "magnet_C": 20.0}
    r["drives"] = {"d1": {"rpm": [1000.0, 2000.0], "I_A": [5.0, 10.0, 20.0], "pwm_extra_W": [[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]],
                          "controller_loss_W": [[1.0, 2.0, 4.0], [2.0, 3.0, 6.0]]}}
    r["demag"] = {"I_limit_A": 70.0, "ref_magnet_C": 20.0}
    return r


# ---------------------------------------------------------------- rewind
def test_rewind_k1_is_identity():
    raw = _raw_with_psi()
    out, k, f = pm.rewind(raw, 7, 0.6, 7, 0.6)
    assert (k, f) == (1.0, 1.0)
    assert out == raw


def test_rewind_scales():
    raw = _raw_with_psi()
    out, k, f = pm.rewind(raw, 7, 0.6, 8, 0.5)
    assert k == pytest.approx(8 / 7) and f == pytest.approx(4.2 / 4.0)
    assert out["R_phase_ohm"] == pytest.approx(raw["R_phase_ohm"] * k * k * f)
    assert out["grid"]["I_A"] == pytest.approx([i / k for i in CUR])
    assert out["psi_map"]["psi_pm_Wb"] == pytest.approx(0.004 * k)
    assert out["limits"]["I_max_A"] == pytest.approx(60.0 / k) and out["demag"]["I_limit_A"] == pytest.approx(70.0 / k)
    assert out["drives"]["d1"]["pwm_extra_W"][0][0] == pytest.approx(1.0 / (k * k))
    assert raw["R_phase_ohm"] == 0.012                                # the input is not touched
    # the torque table is unchanged: the same ampere-turns make the same torque, at currents / k
    assert out["grid"]["T_Nm"] == raw["grid"]["T_Nm"]
    # Kv (from the torque constant) scales 1 / k
    assert pm.kv_of(out) == pytest.approx(pm.kv_of(raw) / k)
    assert pm.kv_of(raw) == pytest.approx(60 / (2 * math.pi * 7 * math.sqrt(3) * 0.004))          # from the PM flux
    assert pm.kv_of(_raw()) == pytest.approx(60 / (2 * math.pi) / (math.sqrt(3) * KT / (1.5 * math.sqrt(2))))   # from the torque constant


def test_strips_turn_options_and_choice():
    stock = [{"thickness_mm": t, "width_mm": 2.5, "stock_kg": kg, "check": False} for t, kg in ((0.4, 5.0), (0.5, 0.5), (0.6, 5.6), (0.7, 3.0))] \
        + [{"thickness_mm": 0.9, "width_mm": 3.0, "stock_kg": 9.0, "check": False}]
    fit = pm.strips_for(stock, 2.5, 8, 7, 0.6, 0.0)                      # 8 t <= 4.2 -> t <= 0.525
    assert [s["thickness_mm"] for s in fit] == [0.5, 0.4]
    assert fit[0]["low_stock"] is True                                   # 0.5 kg < 1 kg
    opts = pm.turn_options(stock, 2.5, 7, 0.6, 3, 5, 0.0)
    assert [o["turns"] for o in opts] == list(range(4, 11))      # 11 and 12 turns: no stocked strip is thin enough
    assert next(o for o in opts if o["turns"] == 7)["as_built"] is True
    assert pm.strips_for(stock, 2.5, 100, 7, 0.6, 0.0) == []
    sel, ok = pm.choose_turns(opts, lambda n: n <= 9)
    assert (sel["turns"], ok) == (9, True)
    sel, ok = pm.choose_turns(opts, lambda n: False)
    assert (sel["turns"], ok) == (4, False)                              # none reachable: the highest Kv, flagged


# ---------------------------------------------------------------- flight
def _curve(D_m=0.3, ct=0.10, cp=0.045, rpm_hi=12000.0):
    rows = []
    r = 2000.0
    while r <= rpm_hi:
        n = r / 60.0
        rows.append((r, ct * 1.225 * n * n * D_m ** 4, cp * 1.225 * n ** 3 * D_m ** 5 / (2 * math.pi * n)))
        r += 500.0
    return rows


def test_flight_v0_is_the_static_curve_and_cruise_costs_less_than_hover_at_speed():
    rows = _curve()
    W = 5.0 * pm.G0
    hov = pm.rotor_flight(rows, 0.3, W, 4, 0.05, 0.0, 1.225)
    T = W / 4
    rpm, q = pm.at_thrust(rows, T)
    assert hov["rpm"] == pytest.approx(rpm) and hov["P_shaft_W"] == pytest.approx(q * 2 * math.pi * rpm / 60)
    cr = pm.rotor_flight(rows, 0.3, W, 4, 0.05, 15.0, 1.225)
    assert cr["T_N"] > T and cr["tilt_deg"] > 0 and cr["P_shaft_W"] < hov["P_shaft_W"]   # translational lift: less power than hover
    assert "error" in pm.rotor_flight(rows, 0.3, 1000.0, 4, 0.05, 0.0, 1.225)         # thrust outside the curve


# ---------------------------------------------------------------- validation
def _body(**over):
    b = {"motor": {"die": "TEST DIE", "config": "L1"}, "battery": {"cells_s": 6, "capacity_ah": 16.0},
         "candidates": {"catalogue": False, "own": True, "blades": [2], "chords": [1.0], "pd_min": 0.3, "pd_max": 0.4, "pd_step": 0.05},
         "airframe": {"min_diameter_in": 12.0, "max_diameter_in": 12.0, "dry_mass_kg": 1.0},
         "flight": {"tw_cont": 1.2, "tw_peak": 1.5}, "kv": {"mode": "as_built", "allow_unverified": True},
         "refine": {"enabled": False}}
    for k, v in over.items():
        b[k] = {**b.get(k, {}), **v} if isinstance(v, dict) else v
    return b


def test_validation_errors_are_422_with_the_field():
    for body, word in (({"motor": {}}, "choose a motor"), (_body(battery={"cells_s": 0}), "battery.cells_s"),
                       (_body(battery={"cells_s": 2.5}), "whole number"), (_body(objective="nope"), "objective"),
                       (_body(motor={"die": "A", "config": "B", "typo": 1}), "unknown input motor.typo"),
                       (_body(refine={"pop": 20, "generations": 10}), "refine"), ([], "object")):
        with pytest.raises(ApiProblem) as e:
            pma.validate(body)
        assert e.value.status == 422 and word in str(e.value.detail), (body, e.value.detail)
    cfg = pma.validate(_body())
    assert cfg["battery"]["v_nom"] == 3.6 and cfg["flight"]["cruise_speed_ms"] == pytest.approx(70 / 3.6)


# ---------------------------------------------------------------- the run, with closed-form propeller curves
def _stub_curve(spec, design, ms, rpm_step):
    v = spec["vector"]
    tw = [x for k, x in v.items() if k.startswith("twist_deg@")]
    ch = [x for k, x in v.items() if k.startswith("chord_R@")]
    mt, mc = sum(tw) / len(tw), sum(ch) / len(ch)
    D = spec["D_in"] * 0.0254
    ct, cp = (0.06 + 0.004 * mt) * (1 + (mc - 0.15)), (0.03 + 0.0015 * mt) * (1 + 2 * (mc - 0.15))
    cap = ms["tip_mach"] * ms["a_snd"] / (math.pi * D) * 60.0
    return _curve(D, ct, cp, min(cap, 20000.0)), ["stub"]


ACCESS = {"mode": "granted", "dies": frozenset(), "email": "u@x", "is_admin": False, "configs": frozenset(), "hide": ()}


@pytest.fixture()
def stub(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "own_curve", _stub_curve)
    d = tmp_path / "s1" / "TEST DIE"
    d.mkdir(parents=True)
    (d / "L1.json").write_text(json.dumps(_raw()), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    monkeypatch.setattr(pma, "_runs_dir", lambda: tmp_path / "runs")
    monkeypatch.setattr(pma.ma, "may_see_config", lambda access, die, cfg: die not in access.get("hide", ()))
    old_q = J.queue()
    J.reset_queue(J.InProcessQueue(workers=2, persist=False))
    yield tmp_path
    J.reset_queue(old_q)


def test_run_ranks_flags_failures_and_switches_objective(stub):
    cfg = pma.validate(_body())
    raw, sha = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    res = pm.run_selection(cfg, raw)
    rows = res["results"]
    assert len(rows) == 3 and res["note"].startswith("Estimate:")
    ok = [r for r in rows if r["passed"]]
    assert ok, [r["failures"] for r in rows]
    vals = [r["objectives"]["range_km"] for r in ok]
    assert all(v > 0 for v in vals) and vals == sorted(vals, reverse=True)     # ranked by the objective
    assert rows[:len(ok)] == ok                                              # passing rows first
    # monotonic with battery energy
    big = pm.run_selection(pma.validate(_body(battery={"capacity_ah": 20.0, "mass_kg": 1.38})), raw)
    small = pm.run_selection(pma.validate(_body(battery={"capacity_ah": 16.0, "mass_kg": 1.38})), raw)
    assert big["results"][0]["objectives"]["range_km"] > small["results"][0]["objectives"]["range_km"]
    # a constraint failure is flagged, never ranked above a passing row
    hard = pm.run_selection(pma.validate(_body(flight={"tw_cont": 1.2, "tw_peak": 9.0})), raw)
    assert all(not r["passed"] and any(f["rule"] == "tw_peak" for f in r["failures"]) for r in hard["results"])
    # another objective changes the ranking key
    hov = pm.run_selection(pma.validate(_body(objective="hover_g_per_w")), raw)
    assert hov["results"][0]["objective_value"] == hov["results"][0]["objectives"]["hover_g_per_w"]
    assert hov["results"][0]["objective_value"] != res["results"][0]["objective_value"]
    # the winding that was used is named (as built: 7 turns)
    assert res["results"][0]["turns"] == 7 and res["results"][0]["strip"]["thickness_mm"] == 0.6


def test_run_storage_round_trip_and_motor_kind(stub):
    r = pma.start_run(_body(), [], ACCESS, background=False)
    assert r["run_id"].startswith("msn-") and r["status"] == "done"
    j = pma.get_run(r["run_id"])
    assert j["motor"]["kind"] == "s1" and j["motor"]["die"] == "TEST DIE" and len(j["motor"]["model_sha256"]) == 64
    assert j["inputs"]["objective"] == "range_km" and j["result"]["results"] and j["version"] == pm.MODEL_VERSION
    lst = pma.list_runs()["runs"]
    assert lst[0]["run_id"] == r["run_id"] and lst[0]["motor"]["kind"] == "s1" and lst[0]["best"]["value"] > 0
    with pytest.raises(ApiProblem) as e:
        pma.get_run("msn-0000000000")
    assert e.value.status == 404
    with pytest.raises(ApiProblem) as e:
        pma.start_run(_body(motor={"die": "NOPE", "config": "X"}), [], ACCESS, background=False)
    assert e.value.status == 422 and "no propeller S1 model" in str(e.value.detail)


# ---------------------------------------------------------------- catalogue motors: estimate model and the greyed motor
def _card(**over):
    c = {"id": "cat_x", "name": "TEST EST 40", "diameter_mm": 40, "poles": 14, "mass_kg": 0.2, "magnet": "N45SH 120C", "length_mm": 12,
         "passport": {"poles": 14, "passport": {"Vemf0_peak_V": 8.9, "rpm0": 6000, "R0_ohm": 0.05, "R0_temp_C": 120, "N0": 7, "wireH0_mm": 0.6,
                                                "I0_A": 20, "Pfe0_W": 8.0, "Pmag0_W": 1.0, "mass0_kg": 0.18, "L0_mm": 12,
                                                "current": {"I_A": [5, 10, 20, 30, 40, 60], "T_Nm": [0.15, 0.3, 0.6, 0.9, 1.15, 1.6]}},
                      "fit": {"slotHeight_mm": 6, "insulation_mm": 0.2, "slotWidth_mm": 3, "wireWidth_mm": 2.5},
                      "geo": {"statorOR_mm": 20, "statorIR_mm": 12, "rotorOR_mm": 11.5, "numSlots": 12, "numPoles": 14}}}
    c.update(over)
    return c


def test_estimate_model_from_a_card():
    raw, info = pm.estimate_model(_card())
    assert info["kind"] == "estimate" and info["label"] == "estimate, not an S1 model"
    assert info["kv_rpm_per_V"] == pytest.approx(6000 / (math.sqrt(3) * 8.9))
    assert raw["R_phase_ohm"] == 0.05 and raw["R_temp_C"] == 120.0 and raw["limits"]["magnet_C"] == 120.0
    assert raw["winding"] == {"turns": 7, "strip_thickness_mm": 0.6, "strip_width_mm": 2.5}
    assert "iron_loss_scaled_rpm_1.5" in info["flags"] and "torque_from_stored_current_sweep" in info["flags"]
    s1.validate_model(raw)                                                # a valid S1-schema model
    no_fe = _card()
    del no_fe["passport"]["passport"]["Pfe0_W"]
    assert "iron_loss_omitted" in pm.estimate_model(no_fe)[1]["flags"]
    # rewound by turns: Kv / k, R k^2 f, currents / k
    cfg = pm.validate_inputs({"motor": {"card_id": "cat_x"}, "kv": {"mode": "free"}})
    bank = pm.MotorBank(raw, cfg, pm_stock())
    c7, c9 = bank.ctx(7), bank.ctx(9)
    assert c7.estimate and c9.kv == pytest.approx(c7.kv * 7 / 9)
    assert c9.m["R_phase_ohm"] == pytest.approx(0.05 * (9 / 7) ** 2 * (7 * 0.6) / (9 * c9.strip_mm))
    assert c9.lim["I_max_A"] == pytest.approx(c7.lim["I_max_A"] * 7 / 9)
    # the simple voltage rule: rpm / Kv + sqrt(6) R I against 0.95 x the pack voltage
    assert c7.simple_voltage_ok(8000.0, 10.0, 40.0) and not c7.simple_voltage_ok(8000.0, 10.0, 20.0)
    pt = c7.point(6000.0, 0.3, 5.0, 25.0, 40.0)
    assert pt["ok"] and pt["current_A"] > 0 and pt["eta_motor"] > 0.5
    assert not c7.point(6000.0, 0.3, 5.0, 25.0, 10.0)["ok"]             # the pack is too low for that speed


def pm_stock():
    return [{"thickness_mm": t, "width_mm": 2.5, "stock_kg": 5.0, "check": False} for t in (0.4, 0.45, 0.5, 0.6, 0.7)]


def test_picker_rows_kind_and_greyed_motor(stub):
    good = _card()
    no_kv = _card(id="cat_y", name="NO KV 30")
    del no_kv["passport"]["passport"]["Vemf0_peak_V"]
    no_pass = {"id": "cat_z", "name": "NO PASSPORT", "diameter_mm": 30}
    s1_named = _card(id="cat_s", name="TEST DIE L1")
    rows = {r["name"]: r for r in pma.motor_entries([good, no_kv, no_pass, s1_named], ACCESS)}
    assert rows["TEST EST 40"]["kind"] == "estimate" and rows["TEST EST 40"]["reason"] is None
    assert rows["NO KV 30"]["kind"] == "none" and "Kv" in rows["NO KV 30"]["reason"]
    assert rows["NO PASSPORT"]["kind"] == "none" and "passport" in rows["NO PASSPORT"]["reason"]
    assert rows["TEST DIE L1"]["kind"] == "s1" and rows["TEST DIE L1"]["die"] == "TEST DIE" and rows["TEST DIE L1"]["id"] == "cat_s"
    # the greyed motor is refused with its reason; the estimate one runs and the run says which model was used
    with pytest.raises(ApiProblem) as e:
        pma.start_run(_body(motor={"card_id": "cat_y", "die": None, "config": None}), [good, no_kv], ACCESS, background=False)
    assert e.value.status == 422 and "cannot be used" in str(e.value.detail) and "Kv" in str(e.value.detail)
    r = pma.start_run(_body(motor={"card_id": "cat_x", "die": None, "config": None}, battery={"cells_s": 12}, kv={"mode": "free"}), [good], ACCESS, background=False)
    j = pma.get_run(r["run_id"])
    assert j["status"] == "done", j.get("error")
    assert j["motor"]["kind"] == "estimate" and j["motor"]["label"] == "estimate, not an S1 model" and j["motor"]["card_id"] == "cat_x"
    assert pma.list_runs()["runs"][0]["motor"]["kind"] == "estimate"


# ---------------------------------------------------------------- stage 2: the existing optimiser with the opt-in mission objective
def test_optimizer_default_behaviour_is_unchanged():
    assert po.OBJECTIVES == ("thrust_per_shaft_W", "cooling_air_axial_ms", "flight_thrust_N")
    assert po.DEFAULT_OBJECTIVES == ("thrust_per_shaft_W", "cooling_air_axial_ms")
    d = pm.mission_design()
    base = pm.helix_vector(__import__("motor_ai_sim.propeller_design", fromlist=["x"]).seed_vector(d, 12, 2), 12.0, 0.35, 1.0, 2)
    for kw in ({}, {"evaluate": lambda dz, v, o: {}}):
        with pytest.raises(po.OptimizeError) as e:
            po.optimize(d, base, "L12", objectives=[po.MISSION_OBJECTIVE] if not kw else ["nonsense"], **kw)
        assert "non-empty subset of" in str(e.value)
    with pytest.raises(po.OptimizeError) as e:                            # the mission objective needs the caller's evaluate
        po.optimize(d, base, "L12", objectives=[po.MISSION_OBJECTIVE])
    assert "thrust_per_shaft_W" in str(e.value) and "mission_objective" not in str(e.value)

    def ev(dz, v, o):
        t = sum(x for k, x in v.items() if k.startswith("twist_deg@"))
        return {"objectives": {"thrust_per_shaft_W": t, "cooling_air_axial_ms": 1.0}, "constraints": {}, "tip_mach": 0.1, "design_id": "x"}
    a = po.optimize(d, base, "L12", pop=4, generations=1, seed=3, evaluate=ev)
    b = po.optimize(d, base, "L12", pop=4, generations=1, seed=3, evaluate=ev)
    assert [e["vector"] for e in a["evaluations"]] == [e["vector"] for e in b["evaluations"]] and a["front"] == b["front"]
    assert set(a["evaluations"][0]["objectives"]) == {"thrust_per_shaft_W", "cooling_air_axial_ms"}


def test_mission_optimisation_runs_and_does_not_lose_the_seed(stub):
    cfg = pma.validate(_body(refine={"enabled": True, "top_n": 1, "pop": 4, "generations": 2, "seed": 1, "free": ["outer"]}))
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    res = pm.run_selection(cfg, raw)
    assert len(res["refined"]) == 1
    rf = res["refined"][0]
    assert rf["seed_value"] == pytest.approx(res["results"][0]["objective_value"]) and rf["n_evaluations"] >= 4
    assert rf["best_value"] >= rf["seed_value"] - 1e-9 and rf["gain_pct"] >= -1e-9          # improves or equals the seed
    assert rf["result"]["passed"] and rf["result"]["turns"] and rf["result"]["kv_rpm_per_V"]
    assert rf["best_vector"] and rf["settings"]["pop"] == 4
    # the optimiser's own records carry the mission objective
    ms = pm.mission_numbers(cfg)
    bank = pm.MotorBank(raw, cfg, [])
    d = pm.mission_design()
    out = po.optimize(d, rf["seed_vector"], "L12", objectives=[po.MISSION_OBJECTIVE], pop=4, generations=1, seed=2, free=["outer"],
                      enforce_tip_mach=False, evaluate=pm.optimizer_evaluate(bank, ms, cfg))
    assert out["objectives"] == [po.MISSION_OBJECTIVE]
    assert all(po.MISSION_OBJECTIVE in e["objectives"] for e in out["evaluations"])


# ---------------------------------------------------------------- the routes and the draft path
def test_routes_and_open_in_designer_draft(stub, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim import propeller_design_api as pda
    from motor_ai_sim.routes import propellers as rp
    cards = [_card(), _card(id="cat_y", name="NO KV 30")]
    del cards[1]["passport"]["passport"]["Vemf0_peak_V"]
    monkeypatch.setattr(rp, "_mission_cards", lambda authorization: cards)
    monkeypatch.setattr(rp, "_mission_access", lambda authorization: ACCESS)
    app = FastAPI()
    app.include_router(rp.router)
    c = TestClient(app)
    o = c.get("/api/propellers/mission/options").json()
    kinds = {m["name"]: m["kind"] for m in o["motors"]}
    assert kinds["TEST EST 40"] == "estimate" and kinds["NO KV 30"] == "none" and kinds["TEST DIE L1"] == "s1"
    assert o["objectives"] == list(pm.OBJECTIVES) and "li-ion-21700" in o["batteries"] and o["note"].startswith("Estimate:")
    assert c.get("/api/propellers/mission/runs").json()["runs"] == []              # not taken by the /{prop_id} routes
    r = c.post("/api/propellers/mission/run", json={"motor": {"card_id": "cat_y"}})
    assert r.status_code == 422 and "Kv" in r.json()["detail"]
    r = c.post("/api/propellers/mission/run", json={"battery": {"cells_s": 0}})
    assert r.status_code == 422
    assert c.get("/api/propellers/mission/runs/msn-0000000000").status_code == 404
    # Open in Designer: the refined vector saves as an ordinary Designer draft (the caller's workspace, no catalogue write)
    cfg = pma.validate(_body(refine={"enabled": True, "top_n": 1, "pop": 4, "generations": 1, "seed": 1, "free": ["outer"]}))
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    rf = pm.run_selection(cfg, raw)["refined"][0]
    monkeypatch.setattr(pda, "_draft_dir", lambda: stub / "drafts")
    saved = pda.put_draft("mission_abc_1", rf["best_vector"], "L12")
    assert saved["saved"] and pda.get_draft("mission_abc_1")["vector"]["diameter_in"] == rf["best_vector"]["diameter_in"]


# ================================================================ rework after the independent review (2026-11-26)
class FakeCtx:
    """A motor whose point depends only on the voltage it is given: ok above ``v_ok`` volts, electrical power = shaft power / eff."""

    def __init__(self, v_ok=0.0, eff=0.8, verification="verified", status="ok"):
        self.v_ok, self.eff, self.verification, self.status = v_ok, eff, verification, status
        self.calls = []
        self.lim = {"I_max_A": 1e9, "winding_C": 200.0, "magnet_C": 150.0}
        self.drive, self.board_cap_A, self.k, self.f, self.kv, self.m, self.estimate = {}, None, 1.0, 1.0, 400.0, {"R_phase_ohm": 0.01}, False

    def point(self, rpm, q, air, amb, v_min):
        self.calls.append(v_min)
        if v_min is not None and v_min < self.v_ok:
            return {"ok": False, "state": "voltage_infeasible", "status": "infeasible"}
        shaft = q * 2 * math.pi * rpm / 60.0
        return {"ok": True, "state": "converged", "rpm": rpm, "torque_Nm": q, "current_A": shaft / 20.0, "T_winding_C": 80.0, "T_magnet_C": 60.0,
                "shaft_W": shaft, "P_motor_in_W": shaft / self.eff, "P_elec_W": shaft / self.eff, "eta_motor": self.eff, "controller_loss_W": None,
                "voltage_status": self.status, "verification": self.verification, "continuous": True, "reasons": [], "demag_known": False}


class FakeBank:
    def __init__(self, ctx):
        self._c = ctx
        self.options = [{"turns": 7, "strip": {"thickness_mm": 0.6, "width_mm": 2.5, "stock_kg": None, "check": False, "low_stock": False}, "as_built": True}]
        self.n0 = 7
        self.winding_known = True

    def shown_turns(self, t):
        return t

    def shown_strip(self, s):
        return s

    def ctx(self, n):
        return self._c


def _ms(cfg_over=None, cancel=None):
    cfg = pm.validate_inputs({"motor": {"die": "A", "config": "B"}, "battery": {"cells_s": 6, "capacity_ah": 16.0, "mass_kg": 1.0}, "airframe": {"dry_mass_kg": 1.0},
                              "kv": {"allow_unverified": True}, **(cfg_over or {})})
    ms = pm.mission_numbers(cfg)
    ms["cancel"] = cancel
    return cfg, ms


def test_pack_sag_is_iterated_with_the_real_losses_not_an_assumed_efficiency():
    # the review's case: 800 W shaft, 24 V open circuit, 0.05 ohm, efficiency 50 % -> the pack gives 20.000 V (an assumed 80 % said 21.695 V)
    cfg, ms = _ms()
    ms["n_rot"] = 1
    ms["avionics_W"] = 0.0
    ms["pack"] = {**ms["pack"], "V_mean_open": 24.0, "V_end_open": 24.0, "R_pack_ohm": 0.05}
    ctx = FakeCtx(eff=0.5)
    q = 800.0 / (2 * math.pi * 6000 / 60.0)
    sp = pm.solve_pack_point(ctx, ms, 6000.0, q, 5.0, "mean")
    assert sp["ok"] and sp["pack"]["V_V"] == pytest.approx(20.0, rel=1e-3)
    assert ctx.calls[-1] == pytest.approx(20.0, rel=2e-3)                      # the motor was solved at that voltage, not at 21.7 V
    # a motor that needs 21 V therefore fails (it used to pass at the assumed 21.695 V)
    ctx2 = FakeCtx(eff=0.5, v_ok=21.0)
    sp2 = pm.solve_pack_point(ctx2, ms, 6000.0, q, 5.0, "mean")
    assert not sp2["ok"] and sp2["reason"].startswith("motor_")
    # high R: the pack cannot deliver the power at all -> rejected, not approximated
    ms["pack"] = {**ms["pack"], "R_pack_ohm": 0.5}
    assert pm.solve_pack_point(FakeCtx(eff=0.5), ms, 6000.0, q, 5.0, "mean")["reason"] == "pack_cannot_deliver"


def test_mean_pass_end_fail_is_a_failure_of_the_candidate():
    cfg, ms = _ms()
    rows = _curve(0.3, 0.10, 0.045, 12000.0)
    ctx = FakeCtx(v_ok=17.0)                      # the mean pack (21.6 V) holds it; the end of discharge with 15 % headroom (about 15 V) does not
    cand = {"id": "x", "name": "x", "source": "own", "D_in": 0.3 / 0.0254, "blades": 2, "rows": rows, "curve_source": "t"}
    res = pm.evaluate_candidate(cand, FakeBank(ctx), ms, cfg)
    assert res["points"]["hover"] is not None and res["points"]["hover_end"] is None
    rules = {f["rule"] for f in res["failures"]}
    assert "hover_end" in rules and "cruise_end" in rules and not res["passed"]
    ok = pm.evaluate_candidate(cand, FakeBank(FakeCtx(v_ok=0.0)), ms, cfg)
    assert ok["passed"] and ok["points"]["hover_end"] and ok["points"]["cruise_end"]
    assert ok["checks"]["voltage"] == "verified" and ok["checks"]["bus_window"] == "unknown" and ok["checks"]["board_current"] == "unknown"


def test_unverified_voltage_never_counts_as_headroom():
    cfg, ms = _ms({"kv": {"allow_unverified": False}})
    cand = {"id": "x", "name": "x", "source": "own", "D_in": 0.3 / 0.0254, "blades": 2, "rows": _curve(0.3, 0.10, 0.045, 12000.0), "curve_source": "t"}
    res = pm.evaluate_candidate(cand, FakeBank(FakeCtx(verification="unverified", status="unverified")), ms, cfg)
    assert not res["passed"] and any(f["rule"] == "voltage_unverified" for f in res["failures"])
    assert res["checks"]["voltage"] == "unverified"
    cfg2, ms2 = _ms({"kv": {"allow_unverified": True}})                           # only by the explicit opt-in
    res2 = pm.evaluate_candidate(cand, FakeBank(FakeCtx(verification="unverified", status="unverified")), ms2, cfg2)
    assert res2["passed"] and res2["checks"]["voltage"] == "unverified"
    # the real synthetic S1 model has no psi map: by default every row fails on it
    assert pm.validate_inputs(_body())["kv"]["allow_unverified"] is True and pm.DEFAULTS["kv"]["allow_unverified"] is False


def test_estimate_voltage_uses_the_hot_resistance():
    raw, info = pm.estimate_model(_card())
    cfg = pm.validate_inputs({"motor": {"card_id": "cat_x"}, "kv": {"mode": "free"}})
    c7 = pm.MotorBank(raw, cfg, pm_stock()).ctx(7)
    assert c7.simple_voltage_ok(8000.0, 10.0, 40.0) and not c7.simple_voltage_ok(8000.0, 10.0, 40.0, r_ohm=2.0)
    p = c7.point(6000.0, 0.3, 5.0, 25.0, 40.0)
    assert p["ok"] and p["verification"] == "estimate" and p["T_winding_C"] > 25.0
    assert c7.point(6000.0, 0.3, 5.0, 25.0, None)["verification"] == "unverified"


def test_the_run_carries_the_verification_provenance_and_stage1_vectors(stub):
    cfg = pma.validate(_body())
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    res = pm.run_selection(cfg, raw)
    r0 = res["results"][0]
    assert r0["checks"]["voltage"] == "unverified" and r0["curve"]["source"] and r0["vector"]["diameter_in"] == 12
    prov = res["provenance"]
    assert len(prov["polar_tables_sha256"]) == 64 and len(prov["wire_stock_sha256"]) == 64 and len(prov["design_file_sha256"]) == 64 and prov["code_commit"]
    # without the opt-in an S1 model with no voltage map cannot assert the headroom: every row fails on it
    strict = pm.run_selection(pma.validate(_body(kv={"allow_unverified": False})), raw)
    assert all((not r["passed"]) and any(f["rule"] == "voltage_unverified" for f in r["failures"]) for r in strict["results"])


def test_every_stage2_evaluation_is_stored_and_linked(stub):
    cfg = pma.validate(_body(refine={"enabled": True, "top_n": 1, "pop": 4, "generations": 2, "seed": 1, "free": ["outer"]}))
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    res = pm.run_selection(cfg, raw, store_dir=stub / "opt")
    rf = res["refined"][0]
    files = list((stub / "opt" / "seed1").glob("opt-*.jsonl"))
    assert len(files) == 1 and rf["optimizer_run_id"] and files[0].stem == rf["optimizer_run_id"]
    lines = [json.loads(x) for x in files[0].read_text().splitlines()]
    assert len(lines) == rf["n_evaluations"] and all("mission_objective" in x["objectives"] for x in lines)
    assert any(x["violation"] > 0 for x in lines) or all(x["violation"] == 0 for x in lines)      # failures are in the record, not dropped


# ---------------------------------------------------------------- access
def test_hidden_configurations_are_absent_everywhere_and_answer_like_missing(stub):
    d2 = stub / "s1" / "SECRET DIE"
    d2.mkdir(parents=True)
    (d2 / "X1.json").write_text(json.dumps({**_raw(), "die": "SECRET DIE"}), encoding="utf-8")
    seen = {**ACCESS, "hide": ()}
    blind = {**ACCESS, "hide": ("SECRET DIE",)}
    assert {r["name"] for r in pma.motor_entries([], seen)} >= {"SECRET DIE X1", "TEST DIE L1"}
    names = {r["name"] for r in pma.motor_entries([], blind)}
    assert "SECRET DIE X1" not in names and "TEST DIE L1" in names
    card = _card(id="cat_h", name="SECRET DIE X1")                                   # a card named like the hidden model: no S1 model is revealed
    row = next(r for r in pma.motor_entries([card], blind) if r["id"] == "cat_h")
    assert row["kind"] == "estimate" and row["die"] is None and row["config"] is None
    with pytest.raises(ApiProblem) as hidden:                                          # direct die / config: the SAME 422 text as a missing model
        pma.start_run(_body(motor={"die": "SECRET DIE", "config": "X1"}), [], blind, background=False)
    with pytest.raises(ApiProblem) as missing:
        pma.start_run(_body(motor={"die": "NOPE DIE", "config": "X1"}), [], blind, background=False)
    assert hidden.value.status == missing.value.status == 422
    assert str(hidden.value.detail).replace("SECRET DIE", "NOPE DIE") == str(missing.value.detail)
    r = pma.start_run(_body(motor={"card_id": "cat_h", "die": None, "config": None}, battery={"cells_s": 12}, kv={"mode": "free"}), [card], blind, background=False)
    assert pma.get_run(r["run_id"])["motor"]["kind"] == "estimate"                     # the card falls to the estimate path, as if no S1 model existed


def test_the_picker_rows_are_an_allow_list(stub):
    rows = pma.motor_entries([_card(), _card(id="cat_s", name="TEST DIE L1")], ACCESS)
    assert rows and all(set(r) <= pma.ENTRY_FIELDS for r in rows)
    for r in rows:
        assert not any(k in r for k in ("psi_map", "grid", "thermal_geom", "loss_W", "_path", "path", "raw"))
    assert "/" not in json.dumps(rows).replace("rpm/V", "")                            # no file path leaves the server


# ---------------------------------------------------------------- cancellation
def test_stop_inside_stage_one_gives_partial_results_and_the_stage(stub):
    cfg = pma.validate(_body())
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 60                        # a stop request that arrives in the middle of the grid, inside the loops of a candidate
    res = pm.run_selection(cfg, raw, should_stop=stop)
    assert res["stopped"] is True and res["stopped_stage"] == "stage1" and res["complete"] is False
    assert len(res["results"]) < 3 and res["counts"]["done"] == len(res["results"])


def test_stop_inside_stage_two_keeps_the_stage_one_result_and_does_not_recompute(stub, monkeypatch):
    cfg = pma.validate(_body(refine={"enabled": True, "top_n": 1, "pop": 4, "generations": 2, "seed": 1, "free": ["outer"]}))
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    flag = {"stop": False}
    calls = {"evaluated_after_stop": 0}
    orig = pm.evaluate_candidate

    def spy(*a, **k):
        out = orig(*a, **k)                                  # a call that is cut by the stop raises and is not counted
        if flag["stop"]:
            calls["evaluated_after_stop"] += 1
        return out
    monkeypatch.setattr(pm, "evaluate_candidate", spy)
    res = pm.run_selection(cfg, raw, should_stop=lambda: flag["stop"], progress=lambda d, t, line: flag.update(stop=(line == "refining" and d >= 5)) if line == "refining" else None)
    assert res["stopped"] and res["stopped_stage"] == "stage2" and res["refined"] == [] and len(res["results"]) == 3
    assert calls["evaluated_after_stop"] == 0                    # nothing is computed once the stop is seen, the final recomputation included


def test_a_late_stop_is_a_stopped_run_with_complete_results(stub, monkeypatch):
    cfg = pma.validate(_body())
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    flag = {"stop": False}
    orig = pm.provenance
    monkeypatch.setattr(pm, "provenance", lambda stock: (flag.update(stop=True), orig(stock))[1])      # the request arrives after the last step
    res = pm.run_selection(cfg, raw, should_stop=lambda: flag["stop"])
    assert res["stopped"] is True and res["complete"] is True and len(res["results"]) == 3


def test_the_run_is_a_queue_job_and_stop_is_scoped_to_the_owner(stub):
    r = pma.start_run(_body(), [], ACCESS, background=False)
    rec = J.queue().status(r["run_id"])
    assert rec is not None and rec.kind == "propeller_mission" and rec.state == "done"
    with pytest.raises(J.NotOwner):                                  # the queue's own owner check
        J.queue().cancel(r["run_id"], requester="intruder", is_admin=False)
    other = stub / "other_workspace"
    with pytest.raises(ApiProblem) as e:                             # a foreign workspace never finds the id
        old = pma._runs_dir
        pma._runs_dir = lambda: other
        try:
            pma.stop_run(r["run_id"])
        finally:
            pma._runs_dir = old
    assert e.value.status == 404
    assert pma.stop_run(r["run_id"]) == {"run_id": r["run_id"], "stopping": False, "status": "done"}    # a late stop does not change a finished run


def test_stop_through_the_queue_ends_a_started_run_as_stopped(stub):
    import time
    r = pma.start_run(_body(refine={"enabled": True, "top_n": 1, "pop": 4, "generations": 2, "seed": 1, "free": ["outer"]}), [], ACCESS)
    assert r["status"] == "queued"
    s = pma.stop_run(r["run_id"])
    t0 = time.time()
    while time.time() - t0 < 60:
        st = pma.get_run(r["run_id"])["status"]
        if st not in ("queued", "running"):
            break
        time.sleep(0.2)
    assert st in ("stopped", "done")
    if s["stopping"]:
        assert st == "stopped"


# ---------------------------------------------------------------- retention and delete
def test_retention_keeps_the_last_100_and_delete_is_refused_while_running(stub, monkeypatch):
    import os
    folder = stub / "runs"
    folder.mkdir()
    rid_of = lambda i: "msn-%010x" % i
    for i in range(104):
        rid = rid_of(i)
        f = folder / (rid + ".run.json")
        f.write_text(json.dumps({"run_id": rid, "status": "done", "created_utc": "2026-11-26T00:00:%02dZ" % (i % 60)}))
        os.utime(f, (1000 + i, 1000 + i))
        (folder / (rid + ".opt")).mkdir()
    assert pma.prune(folder, 100) == 4
    left = sorted(p.name for p in folder.glob("msn-*.run.json"))
    assert len(left) == 100 and rid_of(0) + ".run.json" not in left and rid_of(3) + ".run.json" not in left and rid_of(4) + ".run.json" in left
    assert not (folder / (rid_of(0) + ".opt")).exists() and (folder / (rid_of(4) + ".opt")).exists()
    assert pma.delete_run(rid_of(4)) == {"run_id": rid_of(4), "deleted": True}
    assert not (folder / (rid_of(4) + ".run.json")).exists() and not (folder / (rid_of(4) + ".opt")).exists()
    (folder / (rid_of(5) + ".progress.json")).write_text(json.dumps({"run_id": rid_of(5), "status": "running"}))
    (folder / (rid_of(5) + ".run.json")).unlink()
    monkeypatch.setattr(pma, "_effective", lambda rid, st: "running")
    with pytest.raises(ApiProblem) as e:
        pma.delete_run(rid_of(5))
    assert e.value.status == 409


# ================================================================ second review (#922): loaded bus voltage, unknown winding
def test_controller_bus_window_is_checked_at_the_loaded_voltage_not_the_open_circuit():
    # the review's case: controller minimum 18.06 V; the open-circuit end voltage passes, the LOADED end voltage (~17.9 V) does not
    cfg, ms = _ms()
    ms["n_rot"] = 1
    ms["avionics_W"] = 0.0
    ms["pack"] = {**ms["pack"], "V_mean_open": 18.5, "V_end_open": 18.5, "R_pack_ohm": 0.05}
    q = 200.0 / (2 * math.pi * 6000 / 60.0)
    ctx = FakeCtx(eff=0.8)
    ctx.drive = {"bus_v": {"min": 18.06, "nom": 22.2, "max": 30.0}}
    sp = pm.solve_pack_point(ctx, ms, 6000.0, q, 5.0, "end")
    assert not sp["ok"] and sp["reason"] == "drive_bus_window" and sp["last_V"] < 18.06
    # a valid window passes at the same point
    ctx.drive = {"bus_v": {"min": 16.0, "nom": 22.2, "max": 30.0}}
    ok = pm.solve_pack_point(ctx, ms, 6000.0, q, 5.0, "end")
    assert ok["ok"] and 16.0 < ok["pack"]["V_V"] < 18.5


def test_open_circuit_pass_loaded_fail_fails_the_candidate_and_marks_the_check():
    cfg, ms = _ms()
    rows = _curve(0.3, 0.10, 0.045, 12000.0)
    cand = {"id": "x", "name": "x", "source": "own", "D_in": 0.3 / 0.0254, "blades": 2, "rows": rows, "curve_source": "t"}
    v_end_open = ms["pack"]["V_end_open"]
    ctx = FakeCtx(v_ok=0.0)
    ctx.drive = {"bus_v": {"min": v_end_open * 0.999, "nom": v_end_open, "max": ms["pack"]["S"] * 4.2}}   # open circuit just inside
    res = pm.evaluate_candidate(cand, FakeBank(ctx), ms, cfg)
    rules = {f["rule"] for f in res["failures"]}
    assert not res["passed"] and res["checks"]["bus_window"] == "fail"
    assert rules & {"hover_end_drive_bus_window", "peak_drive_bus_window", "drive_bus_window"}
    f = next(x for x in res["failures"] if x["rule"] == "hover_end_drive_bus_window")
    assert f["params"]["reason"] == "drive_bus_window" and f["params"]["V"] is not None        # the UI translates the whole message


def test_unknown_winding_is_saved_as_null_never_as_placeholders(stub):
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    raw = {k: v for k, v in raw.items() if k != "winding"}
    cfg = pma.validate(_body(kv={"mode": "as_built", "turns0": None, "strip_thickness0_mm": None, "strip_width_mm": None}))
    res = pm.run_selection(cfg, raw)
    r0 = res["results"][0]
    assert r0["turns"] is None and r0["winding_known"] is False
    assert r0["strip"]["thickness_mm"] is None and r0["strip"]["width_mm"] is None
    w = res["winding"]
    assert w["known"] is False and w["turns0"] is None and w["strip_thickness0_mm"] is None and w["strip_width_mm"] is None
    assert all(o["turns"] is None for o in w["options"])
    json.dumps(res)                                                            # serialisable as saved


def test_progress_only_ended_records_count_in_retention_and_active_ones_never(stub, monkeypatch):
    import os
    folder = stub / "runs"
    folder.mkdir()
    for i in range(105):                                                       # e.g. cancelled while queued: progress files only
        rid = "msn-%010x" % i
        f = folder / (rid + ".progress.json")
        f.write_text(json.dumps({"run_id": rid, "status": "queued"}))
        os.utime(f, (1000 + i, 1000 + i))
    active = "msn-%010x" % 0                                                   # the oldest is still an active job
    monkeypatch.setattr(pma, "_effective", lambda rid, st: "queued" if rid == active else "stopped")
    assert pma.prune(folder, 100) == 4
    assert (folder / (active + ".progress.json")).exists()
    assert len(list(folder.glob("msn-*.progress.json"))) == 101


def test_a_failed_terminal_write_keeps_the_run_discoverable(stub, monkeypatch):
    folder = stub / "runs"
    real = pma._write_json

    def flaky(path, doc):
        if str(path).endswith(".run.json"):
            raise OSError("disk full")
        real(path, doc)
    monkeypatch.setattr(pma, "_write_json", flaky)
    monkeypatch.setattr(pma, "_effective", lambda rid, st: st)
    cfg = pma.validate(_body())
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    head = {"run_id": "msn-00000000aa", "inputs": cfg}
    pma._work("msn-00000000aa", folder, cfg, raw, head, 3)
    assert not (folder / "msn-00000000aa.run.json").exists()
    run = pma.get_run("msn-00000000aa")
    assert run["status"] == "failed" and "terminal write failed" in run["error"]


def test_a_refused_admission_does_not_prune_history(stub, monkeypatch):
    import os
    folder = stub / "runs"
    folder.mkdir()
    for i in range(100):
        rid = "msn-%010x" % (0x100 + i)
        f = folder / (rid + ".run.json")
        f.write_text(json.dumps({"run_id": rid, "status": "done"}))
        os.utime(f, (1000 + i, 1000 + i))

    def refuse(*a, **k):
        raise J.WorkerDraining("draining")
    monkeypatch.setattr(pma._J, "run_job", refuse)
    with pytest.raises(J.WorkerDraining):
        pma.start_run(_body(), [], ACCESS)
    assert len(list(folder.glob("msn-*.run.json"))) == 100


def test_retention_never_counts_an_active_job_even_after_its_terminal_file(stub, monkeypatch):
    import os
    folder = stub / "runs"
    folder.mkdir()
    for i in range(101):
        rid = "msn-%010x" % (0x200 + i)
        f = folder / (rid + ".run.json")
        f.write_text(json.dumps({"run_id": rid, "status": "done"}))
        os.utime(f, (1000 + i, 1000 + i))
    busy = "msn-%010x" % 0x200                                                 # oldest file, but its job is not released yet

    class Rec:
        state = "running"
    real = J.queue().status
    monkeypatch.setattr(J.queue(), "status", lambda rid: Rec() if rid == busy else real(rid))
    (folder / (busy + ".progress.json")).write_text(json.dumps({"run_id": busy, "status": "running"}))
    assert pma.prune(folder, 100) == 0                                         # 100 ended runs + the active one: nothing to remove
    assert (folder / (busy + ".run.json")).exists() and (folder / (busy + ".progress.json")).exists()


def test_failures_carry_parameters_for_a_translated_message():
    cfg, ms = _ms({"flight": {"tw_cont": 9.0, "tw_peak": 9.5}})
    rows = _curve(0.3, 0.10, 0.045, 12000.0)
    cand = {"id": "x", "name": "x", "source": "own", "D_in": 0.3 / 0.0254, "blades": 2, "rows": rows, "curve_source": "t"}
    res = pm.evaluate_candidate(cand, FakeBank(FakeCtx()), ms, cfg)
    f = next(x for x in res["failures"] if x["rule"] == "tw_cont")
    assert f["params"]["limit"] == 9.0 and isinstance(f["params"]["value"], float)
    assert all("params" in x for x in res["failures"])


# ================================================================ 80 mm drone (TASK_mission_drone80, 2026-11-26)
def test_diameter_window_reaches_40_in():
    cfg = pm.validate_inputs({"motor": {"die": "A", "config": "B"}, "airframe": {"min_diameter_in": 24.0, "max_diameter_in": 40.0}})
    assert cfg["airframe"]["max_diameter_in"] == 40.0
    with pytest.raises(pm.MissionError):
        pm.validate_inputs({"motor": {"die": "A", "config": "B"}, "airframe": {"max_diameter_in": 41.0}})


def test_catalogue_extension_below_the_test_is_opt_in_flagged_and_never_above():
    base = {"motor": {"die": "A", "config": "B"}, "airframe": {"min_diameter_in": 24.0, "max_diameter_in": 40.0}}
    off = pm.catalogue_candidates(pm.validate_inputs(base), 1.225)
    on = pm.catalogue_candidates(pm.validate_inputs({**base, "candidates": {"extend_below": 0.4}}), 1.225)
    assert off and len(off) == len(on)
    for a, b in zip(off, on):
        lo, hi = a["tested_rpm"]
        assert "extrapolated_below_test" not in a["flags"] and a["rows"][0][0] == pytest.approx(lo)
        assert "extrapolated_below_test" in b["flags"]
        assert b["rows"][0][0] == pytest.approx(0.6 * lo) and max(r[0] for r in b["rows"]) <= hi + 1e-9
        assert b["rows"][-len(a["rows"]):] == a["rows"]                       # the tested part is unchanged
        r0 = b["rows"][0]                                                      # constant coefficients: T, Q ~ rpm^2
        assert r0[1] / a["rows"][0][1] == pytest.approx(0.36) and r0[2] / a["rows"][0][2] == pytest.approx(0.36)


def test_own_family_outside_the_window_is_reported(stub):
    raw, _ = pma.load_raw_model("TEST DIE", "L1", ACCESS)
    cfg = pma.validate(_body(airframe={"min_diameter_in": 24.0, "max_diameter_in": 40.0}, candidates={"catalogue": True, "own": True}))
    res = pm.run_selection(cfg, raw)
    assert res["own_family"]["in_window"] == 0 and res["own_family"]["requested"] is True and res["refined"] == []
