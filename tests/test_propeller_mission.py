"""Propellers > Mission: the model (``propeller_mission``), its API logic (``propeller_mission_api``) and the opt-in mission objective of
``propeller_optimize``.  Synthetic S1 model, synthetic propeller curves (no private model data, no UIUC / APC files); the blade-element
model is replaced by a closed-form curve where a test is not about it.  Fast: no FEM."""
from __future__ import annotations

import copy
import json
import math

import pytest

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
         "flight": {"tw_cont": 1.2, "tw_peak": 1.5}, "kv": {"mode": "as_built"},
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


@pytest.fixture()
def stub(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "own_curve", _stub_curve)
    d = tmp_path / "s1" / "TEST DIE"
    d.mkdir(parents=True)
    (d / "L1.json").write_text(json.dumps(_raw()), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    monkeypatch.setattr(pma, "_runs_dir", lambda: tmp_path / "runs")
    return tmp_path


def test_run_ranks_flags_failures_and_switches_objective(stub):
    cfg = pma.validate(_body())
    raw, sha = pma.load_raw_model("TEST DIE", "L1")
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
    r = pma.start_run(_body(), [], background=False)
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
        pma.start_run(_body(motor={"die": "NOPE", "config": "X"}), [], background=False)
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
    rows = {r["name"]: r for r in pma.motor_entries([good, no_kv, no_pass, s1_named])}
    assert rows["TEST EST 40"]["kind"] == "estimate" and rows["TEST EST 40"]["reason"] is None
    assert rows["NO KV 30"]["kind"] == "none" and "Kv" in rows["NO KV 30"]["reason"]
    assert rows["NO PASSPORT"]["kind"] == "none" and "passport" in rows["NO PASSPORT"]["reason"]
    assert rows["TEST DIE L1"]["kind"] == "s1" and rows["TEST DIE L1"]["die"] == "TEST DIE" and rows["TEST DIE L1"]["id"] == "cat_s"
    # the greyed motor is refused with its reason; the estimate one runs and the run says which model was used
    with pytest.raises(ApiProblem) as e:
        pma.start_run(_body(motor={"card_id": "cat_y", "die": None, "config": None}), [good, no_kv], background=False)
    assert e.value.status == 422 and "cannot be used" in str(e.value.detail) and "Kv" in str(e.value.detail)
    r = pma.start_run(_body(motor={"card_id": "cat_x", "die": None, "config": None}, battery={"cells_s": 12}, kv={"mode": "free"}), [good], background=False)
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
    raw, _ = pma.load_raw_model("TEST DIE", "L1")
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
    raw, _ = pma.load_raw_model("TEST DIE", "L1")
    rf = pm.run_selection(cfg, raw)["refined"][0]
    monkeypatch.setattr(pda, "_draft_dir", lambda: stub / "drafts")
    saved = pda.put_draft("mission_abc_1", rf["best_vector"], "L12")
    assert saved["saved"] and pda.get_draft("mission_abc_1")["vector"]["diameter_in"] == rf["best_vector"]["diameter_in"]
