"""Propeller-loaded continuous (S1) rating — ``motor_ai_sim.propeller_s1`` and its route.

A synthetic passport model (known torque constant, known loss laws) and a synthetic
propeller (known C_T / C_P) pin: the thermal fixed point at one speed, the
bisection to the limit, the binding constraint, the propeller's extrapolation flags,
the non-monotone case, the grid interpolation rule, and the route's gates.
Closed-form, no FEM: the file runs in a few seconds.
"""
from __future__ import annotations

import copy
import json
import math

import pytest
import yaml

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim import report

G0 = 9.80665
RPM = [1000.0, 2000.0, 4000.0, 8000.0, 12000.0]
CUR = [5.0, 10.0, 20.0, 40.0, 60.0]
KT = 0.03


def _model(**over):
    fe = [[0.8 * (n / 1000.0) ** 1.5 * (1 + i / 60.0) for i in CUR] for n in RPM]
    m = {
        "schema": s1.SCHEMA, "die": "TEST DIE", "config": "L1",
        "R_phase_ohm": 0.012, "R_temp_C": 20.0, "magnet_ref_temp_C": 20.0,
        "flux_temp_coeff_per_K": -0.001, "torque_factor": 1.0,
        "grid": {"rpm": RPM, "I_A": CUR,
                 "T_Nm": [[KT * i for i in CUR] for _ in RPM],
                 "loss_W": {"fe_stator": fe,
                            "cu_ac": [[0.02 * (i / 10.0) ** 2 * (n / 1000.0) for i in CUR] for n in RPM],
                            "rotor_eddy": [[0.01 * (n / 1000.0) ** 2 * (i / 10.0) for i in CUR] for n in RPM]}},
        "mech_W": {"rpm": RPM, "W": [0.01 * n / 1000.0 for n in RPM]},
        "limits": {"I_max_A": 60.0, "rpm_max": 12000.0, "winding_C": 200.0, "magnet_C": 150.0},
        "thermal_geom": {"statorOD_mm": 40.0, "stackLength_mm": 15.0, "numSlots": 12, "slotHeight_mm": 6.0,
                         "slotWidth_mm": 2.8, "insulation_mm": 0.06, "coreThickness_mm": 2.0,
                         "airGap_mm": 0.3, "magnetOD_mm": 23.0},
    }
    for k, v in over.items():
        m[k] = v
    return s1.validate_model(m)


def _prop(tmp_path, *, ct=0.10, cp=0.045, d_m=0.25, estimate=False, name="syn_s1"):
    rho = 1.225
    rows = []
    for rpm in (3000, 4000, 5000, 6000, 7000, 8000):
        n = rpm / 60.0
        T = ct * rho * n ** 2 * d_m ** 4
        P = cp * rho * n ** 3 * d_m ** 5
        rows.append({"thr": 50, "rpm": rpm, "thrust_g": round(T / G0 * 1000.0, 4),
                     "torque_Nm": None if estimate else round(P / (2 * math.pi * n), 7)})
    raw = {"schema": "propeller-1", "id": name, "vendor": "TEST", "model": name,
           "geometry": {"diameter_in": d_m / 0.0254, "diameter_mm": d_m * 1000.0, "pitch_in": 4.0, "blades": 2},
           "performance": {"data_quality": "thrust_measured_power_estimated" if estimate else "torque_measured",
                           "test_density_kg_m3": rho,
                           "tables": [{"id": "t1", "use_in_fit": True, "torque_resolution_Nm": 1e-6, "rows": rows}]}}
    if estimate:
        raw["performance"]["power_estimate"] = {"estimated": True, "cp_ref": cp, "method": "test", "rel_spread": 0.1}
    return pp._build(raw, str(tmp_path / f"{name}.yaml"))


def _run(m, prop, **kw):
    kw.setdefault("rpm_min", 1000.0)
    kw.setdefault("rpm_max", 12000.0)
    return s1.s1_rating(m, prop, **kw)


# ---------------------------------------------------------------------------
# grid interpolation
# ---------------------------------------------------------------------------

def test_loss_grid_power_law_between_speeds_and_linear_in_current():
    m = _model()
    g = m["grid"]
    v, below = s1.grid_at(g["rpm"], g["I_A"], g["loss_W"]["fe_stator"], 3000.0, 20.0, kind="loss")
    assert not below
    assert v == pytest.approx(0.8 * 3.0 ** 1.5 * (1 + 20 / 60.0), rel=1e-9)
    v, _ = s1.grid_at(g["rpm"], g["I_A"], g["loss_W"]["fe_stator"], 4000.0, 15.0, kind="loss")
    assert v == pytest.approx(0.8 * 8.0 * (1 + 15 / 60.0), rel=1e-9)
    # below the first speed: the first interval's law continues, and says so
    v, below = s1.grid_at(g["rpm"], g["I_A"], g["loss_W"]["fe_stator"], 500.0, 20.0, kind="loss")
    assert below and v == pytest.approx(0.8 * 0.5 ** 1.5 * (1 + 20 / 60.0), rel=1e-9)
    # beyond the grid: refused
    assert s1.grid_at(g["rpm"], g["I_A"], g["T_Nm"], 13000.0, 10.0, kind="torque")[0] is None
    assert s1.grid_at(g["rpm"], g["I_A"], g["T_Nm"], 3000.0, 61.0, kind="torque")[0] is None
    # torque goes to zero with the current below the first row
    assert s1.grid_at(g["rpm"], g["I_A"], g["T_Nm"], 3000.0, 2.5, kind="torque")[0] == pytest.approx(KT * 2.5)


def test_resistance_and_flux_follow_temperature():
    m = _model()
    assert s1.resistance(m, 120.0) == pytest.approx(0.012 * (1 + 0.00393 * 100))
    assert s1.flux_factor(m, 120.0) == pytest.approx(0.9)
    I, st = s1.current_for_torque(m, 3000.0, 0.6, 120.0)
    assert st == "ok" and I == pytest.approx(0.6 / (KT * 0.9), rel=1e-6)
    assert s1.current_for_torque(m, 3000.0, 5.0, 20.0) == (None, "current")


def test_infeasible_cells_give_voltage_not_a_wrong_current():
    m = _model()
    t = m["grid"]["T_Nm"]
    t[-1] = [None, None, KT * 20, KT * 40, KT * 60 * 0.2]       # 12000 rpm: FW collapse at the top
    t[-2] = [None, None, KT * 20, KT * 40, KT * 60 * 0.2]
    I, st = s1.current_for_torque(m, 10000.0, 0.9, 20.0)
    assert I == pytest.approx(0.9 / KT, rel=1e-6) and st == "ok"
    assert s1.current_for_torque(m, 10000.0, 1.3, 20.0) == (None, "voltage")   # peaks below, not at the cap
    assert s1.current_for_torque(m, 10000.0, 0.1, 20.0) == (None, "voltage")   # low-current cells infeasible


# ---------------------------------------------------------------------------
# one speed: the fixed point
# ---------------------------------------------------------------------------

def test_fixed_point_is_self_consistent(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    lims = s1.limits_of(m)
    row = s1.solve_point(m, prop, 6000.0, ambient_c=25.0, rho=1.2, lims=lims)
    assert row["converged"] and row["iterations"] > 1
    # the current makes the propeller torque plus the mechanical loss at the magnet temperature
    q = pp.torque_Nm(prop, 6000.0, 1.2) + 0.01 * 6.0 / (2 * math.pi * 100.0)
    assert row["torque_em_Nm"] == pytest.approx(q, rel=1e-9)
    assert s1.em_torque(m, 6000.0, row["current_A"], row["T_magnet_C"]) == pytest.approx(q, rel=1e-6)
    # the temperatures reproduce themselves through the losses at those temperatures
    ls = s1.losses(m, 6000.0, row["current_A"], row["T_winding_C"])
    th = s1.temperatures(m["thermal_geom"], ls["cu_dc"] + ls["cu_ac"], ls["fe_rotor"] + ls["rotor_eddy"],
                         ls["motor_total"], row["h_W_m2K"], 25.0)
    assert th["winding"] == pytest.approx(row["T_winding_C"], abs=0.05)
    assert th["magnet"] == pytest.approx(row["T_magnet_C"], abs=0.05)
    assert row["losses_W"]["cu_dc"] == pytest.approx(3 * row["current_A"] ** 2 * s1.resistance(m, row["T_winding_C"]))
    # the cooling air is the propeller's
    assert row["air_speed_ms"] == pytest.approx(pp.cooling_air_speed_ms(prop, 6000.0, 1.2))
    assert row["electrical_power_W"] == pytest.approx(row["shaft_power_W"] + row["losses_W"]["motor_total"])


def test_hotter_ambient_runs_hotter_and_needs_more_current(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    lims = s1.limits_of(m)
    a = s1.solve_point(m, prop, 6000.0, ambient_c=10.0, rho=1.2, lims=lims)
    b = s1.solve_point(m, prop, 6000.0, ambient_c=40.0, rho=1.2, lims=lims)
    assert b["T_winding_C"] > a["T_winding_C"] + 25 and b["current_A"] > a["current_A"]


# ---------------------------------------------------------------------------
# the limit
# ---------------------------------------------------------------------------

def test_bisection_lands_on_the_binding_limit(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    r = _run(m, prop)
    L = r["limit"]
    assert r["monotone"] and L is not None
    assert L["binding"] in ("winding", "magnet")
    key = "T_winding_C" if L["binding"] == "winding" else "T_magnet_C"
    lim = 200.0 if L["binding"] == "winding" else 150.0
    assert L[key] <= lim and L[key] > lim - 1.0               # bisected to 1 rpm: within a kelvin
    rho = r["rho_kg_m3"]
    above = s1.solve_point(m, prop, L["rpm"] + 2.0, ambient_c=25.0, rho=rho, lims=r["limits"])
    assert not above["ok"]
    assert L["thrust_N"] == pytest.approx(pp.thrust_N(prop, L["rpm"], rho))
    assert L["shaft_power_W"] == pytest.approx(pp.shaft_power_W(prop, L["rpm"], rho))
    assert L["thrust_g"] == pytest.approx(L["thrust_N"] / G0 * 1000.0)


def test_binding_constraint_follows_the_tightest_limit(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    w = _run(m, prop, magnet_limit_c=400.0)["limit"]
    assert w["binding"] == "winding" and w["T_winding_C"] == pytest.approx(200.0, abs=1.0)
    g = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=90.0)["limit"]
    assert g["binding"] == "magnet" and g["T_magnet_C"] == pytest.approx(90.0, abs=1.0)
    assert g["rpm"] < w["rpm"]
    lim = copy.deepcopy(m)
    lim["limits"]["I_max_A"] = 10.0
    c = _run(lim, prop, winding_limit_c=400.0, magnet_limit_c=400.0)["limit"]
    assert c["binding"] == "current" and c["current_A"] == pytest.approx(10.0, rel=0.02)
    sp = _run(m, prop, rpm_max=4000.0, winding_limit_c=400.0, magnet_limit_c=400.0)["limit"]
    assert sp["binding"] == "speed_range" and sp["rpm"] == pytest.approx(4000.0)


def test_extrapolation_and_estimate_flags_follow_the_catalogue(tmp_path):
    m = _model()
    r = _run(m, _prop(tmp_path))
    tested = r["rpm_range_tested"]
    for row in r["rows"]:
        inside = tested[0] <= row["rpm"] <= tested[1]
        assert row["extrapolated"] is (not inside), row["rpm"]
        assert row["power_estimated"] is False
    assert r["power_data"] == "measured_torque"
    e = _run(m, _prop(tmp_path, estimate=True, name="est"))
    assert e["power_data"] == "estimated" and all(x["power_estimated"] for x in e["rows"])
    assert e["limit"]["power_estimated"] is True


def test_no_continuous_point_and_non_monotone_band(tmp_path):
    prop = _prop(tmp_path)
    # a no-load loss that is huge at low speed and vanishes by 4000 rpm: low speeds overheat,
    # the middle passes, the top overheats again
    fe = [[(400.0 if n <= 1000 else 120.0 if n <= 2000 else 0.5 * (n / 1000.0) ** 1.5) for _ in CUR] for n in RPM]
    m = _model()
    m["grid"]["loss_W"]["fe_stator"] = fe
    r = _run(m, prop)
    assert not r["monotone"]
    assert r["rows"][0]["ok"] is False and len(r["bands"]) >= 1
    assert r["limit"] is not None and r["limit"]["rpm"] >= r["bands"][-1][1] - 1e-6
    hot = _run(m, prop, winding_limit_c=30.0, magnet_limit_c=30.0)
    assert hot["limit"] is None and hot["no_continuous_point"] and hot["first_failure"] in ("winding", "magnet", "runaway")


def test_runaway_rows_print_no_temperature(tmp_path):
    m = _model(flux_temp_coeff_per_K=-0.004)
    m["grid"]["loss_W"]["fe_stator"] = [[50.0 * (n / 1000.0) for _ in CUR] for n in RPM]
    r = _run(m, _prop(tmp_path), winding_limit_c=2000.0, magnet_limit_c=2000.0)
    bad = [x for x in r["rows"] if x["binding"] == "runaway"]
    assert bad and all(x["T_winding_C"] is None for x in bad)


def test_model_validation_refuses_bad_input():
    with pytest.raises(s1.S1Error):
        _model(schema="nope")
    with pytest.raises(s1.S1Error):
        m = _model()
        raw = {**m, "grid": {**m["grid"], "rpm": [3000.0, 1000.0, 4000.0, 8000.0, 12000.0]}}
        s1.validate_model(raw)
    with pytest.raises(s1.S1Error):
        m = _model()
        s1.validate_model({**m, "grid": {**m["grid"], "loss_W": {"steel": m["grid"]["loss_W"]["fe_stator"]}}})
    with pytest.raises(s1.S1Error):
        s1.load_model("../etc", "L1")


def test_field_weakening_floor_needs_current_even_for_a_light_load(tmp_path):
    """Above the no-load voltage limit the low-current cells are infeasible and the
    model states the floor: below it nothing runs, a load lighter than the floor's
    torque runs AT the floor (flagged), torque goes to 0 at the floor, losses
    follow the first feasible interval's line."""
    rpm = RPM + [16000.0]
    T = [[KT * i for i in CUR] for _ in RPM] + [[None, None, KT * 20 * 0.5, KT * 40 * 0.7, KT * 60 * 0.8]]
    fe = [[0.8 * (n / 1000.0) ** 1.5 * (1 + i / 60.0) for i in CUR] for n in RPM]
    fe.append([None, None, 30.0, 40.0, 55.0])
    m = _model(grid={"rpm": rpm, "I_A": CUR, "T_Nm": T, "loss_W": {"fe_stator": fe},
                     "I_floor_A": [0, 0, 0, 0, 0, 12.0]},
               limits={"I_max_A": 60.0, "rpm_max": 16000.0, "winding_C": 200.0, "magnet_C": 150.0})
    assert s1.floor_current(m, 12000.0) == 0.0
    assert s1.floor_current(m, 16000.0) == 12.0
    assert s1.floor_current(m, 14000.0) == 12.0                  # the larger of the two rows
    assert s1.em_torque(m, 16000.0, 10.0, 20.0) is None
    assert s1.em_torque(m, 16000.0, 12.0, 20.0) == pytest.approx(0.0, abs=1e-12)
    assert s1.em_torque(m, 16000.0, 16.0, 20.0) == pytest.approx(KT * 10 * 4 / 8)
    loss, _ = s1.grid_at(m["grid"]["rpm"], CUR, fe, 16000.0, 15.0, kind="loss", floors=m["grid"]["I_floor_A"])
    assert loss == pytest.approx(30.0 + (40.0 - 30.0) * (15.0 - 20.0) / 20.0)
    I, st = s1.current_for_torque(m, 16000.0, 0.01, 20.0)
    assert st == "ok" and 12.0 < I < 12.5                      # a light load still needs the floor
    assert s1.current_for_torque(m, 16000.0, 0.0, 20.0) == (12.0, "fw_floor")
    I, st = s1.current_for_torque(m, 16000.0, KT * 30.0, 20.0)
    assert st == "ok" and 40.0 < I < 60.0
    # no floor declared: the old behaviour (the infeasible cells give no current)
    g = {k: v for k, v in m["grid"].items() if k != "I_floor_A"}
    m2 = s1.validate_model({**m, "grid": g})
    assert s1.current_for_torque(m2, 16000.0, 0.01, 20.0)[0] is None
    with pytest.raises(s1.S1Error):
        s1.validate_model({**m, "grid": {**m["grid"], "I_floor_A": [0, 0]}})
    with pytest.raises(s1.S1Error):
        s1.validate_model({**m, "grid": {**m["grid"], "I_floor_A": [0, 0, 0, 0, 0, -1]}})


GEOM = {"statorOD_mm": 40.0, "stackLength_mm": 15.0, "numSlots": 12, "slotHeight_mm": 6.0,
        "slotWidth_mm": 2.8, "insulation_mm": 0.06, "coreThickness_mm": 2.0,
        "airGap_mm": 0.3, "magnetOD_mm": 23.0}


def test_calibration_defaults_are_configures_network_exactly():
    a = s1.temperatures(GEOM, 12.0, 2.0, 30.0, 55.0, 25.0)
    b = s1.temperatures(GEOM, 12.0, 2.0, 30.0, 55.0, 25.0, dict(s1.CALIBRATION_KEYS))
    t_h = 25.0 + 30.0 / (55.0 * a["A_surface_m2"])
    for k in ("housing", "winding", "magnet"):
        assert a[k] == pytest.approx(b[k], abs=1e-12)
    assert a["housing"] == pytest.approx(t_h, rel=1e-12)
    assert a["winding"] == pytest.approx(t_h + 12.0 * a["R_winding_K_W"], rel=1e-12)
    assert a["magnet"] == pytest.approx(t_h + 2.0 * a["R_gap_K_W"], rel=1e-12)


def test_calibrated_network_balances_and_cools_through_the_new_paths():
    cal = {"housing_film_factor": 1.3, "winding_air_area_m2": 4e-3, "rotor_air_area_m2": 1.5e-3}
    h, amb = 55.0, 25.0
    t = s1.temperatures(GEOM, 12.0, 2.0, 30.0, h, amb, cal)
    base = s1.temperatures(GEOM, 12.0, 2.0, 30.0, h, amb)
    # every watt leaves through one of the three surfaces
    out = (t["G_housing_W_K"] * (t["housing"] - amb) + t["G_winding_air_W_K"] * (t["winding"] - amb)
           + t["G_rotor_air_W_K"] * (t["magnet"] - amb))
    assert out == pytest.approx(30.0, rel=1e-9)
    # the winding and rotor node balances hold
    assert (t["winding"] - t["housing"]) / t["R_winding_K_W"] \
        + t["G_winding_air_W_K"] * (t["winding"] - amb) == pytest.approx(12.0, rel=1e-9)
    assert (t["magnet"] - t["housing"]) / t["R_gap_K_W"] \
        + t["G_rotor_air_W_K"] * (t["magnet"] - amb) == pytest.approx(2.0, rel=1e-9)
    assert t["winding"] < base["winding"] and t["magnet"] < base["magnet"]
    # the rotor path alone cools the magnet more than the winding
    r = s1.temperatures(GEOM, 12.0, 2.0, 30.0, h, amb, {"rotor_air_area_m2": 3e-3})
    assert base["magnet"] - r["magnet"] > base["winding"] - r["winding"] > 0


def test_model_calibration_is_validated_and_used(tmp_path):
    with pytest.raises(s1.S1Error):
        _model(thermal_calibration={"rotor_air_area_m2": -1.0})
    with pytest.raises(s1.S1Error):
        _model(thermal_calibration={"housing_film_factor": 0.0})
    with pytest.raises(s1.S1Error):
        _model(thermal_calibration=[1, 2])
    prop = _prop(tmp_path)
    plain = _run(_model(), prop)
    cool = _run(_model(thermal_calibration={"housing_film_factor": 1.5, "winding_air_area_m2": 5e-3,
                                            "rotor_air_area_m2": 2e-3, "basis": "test"}), prop)
    assert cool["limit"]["rpm"] > plain["limit"]["rpm"]
    assert cool["model"]["thermal_calibration"]["basis"] == "test"


def test_shipped_models_load_and_rate_with_their_default_propellers():
    """The three extracted models load; L12 gives a limit inside the grid."""
    for die, cfg in (("CIANO14 40 new", "L12"), ("CIANO14 40 new", "L20"), ("CIANO28 85 20SW1200", "L13")):
        m = s1.load_model(die, cfg)
        assert m["limits"]["magnet_C"] == 150.0
        assert "winding_C" not in m["limits"]            # the project's ONE class applies, not a model number
        lims = s1.limits_of(m)
        assert lims["winding_C"] == report.PROJECT_INSULATION_C == 200.0
        assert "class N" in lims["winding_basis"]
    m = s1.load_model("CIANO14 40 new", "L12")
    r = s1.s1_rating(m, pp.get_propeller("tmotor_fpv_10x5"))
    # a system rating since 2026-10-11: the die's controller binds first — at the board
    # model's limit for the S1 air temperature (25 °C here), which is above the 45 °C card
    assert r["limit"] and r["limit"]["binding"] == "controller"
    assert 3000.0 < r["limit"]["rpm"] < 20000.0
    card = m["sine_controller"]["i_board_limit_A"]["favourable"]
    assert card == pytest.approx(26.1, abs=0.01)         # the 45 °C card, unchanged
    assert r["limit"]["current_A"] <= r["limit"]["controller_limit_A"] + 1e-6
    assert r["limit"]["controller_limit_A"] > card       # 25 °C air on the board


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------

@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A model dir, a propeller catalogue dir and a cooling-options file, all in tmp."""
    from motor_ai_sim import cooling_options as co
    prop = _prop(tmp_path)
    (tmp_path / "props" / "v").mkdir(parents=True)
    (tmp_path / "props" / "v" / "syn_s1.yaml").write_text(yaml.safe_dump(prop.raw), encoding="utf-8")
    monkeypatch.setattr(pp, "_DIR", tmp_path / "props")
    (tmp_path / "s1" / "TEST DIE").mkdir(parents=True)
    m = _model()
    (tmp_path / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"TEST DIE": {
        "cooling_options": ["propeller_air"], "propellers": ["syn_s1"]}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    return tmp_path


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    return TestClient(app)


def test_route_answers_for_an_allowed_propeller(client, wired):
    r = client.get("/api/propellers/syn_s1/s1", params={"die": "TEST DIE", "config": "L1", "n": 21})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["rows"]) == 21 and j["limit"]["binding"] in ("winding", "magnet", "speed_range")
    assert j["die"] == "TEST DIE" and j["config"] == "L1"


def test_route_refuses_what_it_must(client, wired, monkeypatch):
    from motor_ai_sim import cooling_options as co
    from motor_ai_sim import motor_access as ma
    assert client.get("/api/propellers/syn_s1/s1", params={"die": "TEST DIE", "config": "L9"}).status_code == 404
    f = wired / "co2.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"TEST DIE": {
        "cooling_options": ["propeller_air"], "propellers": ["other"]}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    assert client.get("/api/propellers/syn_s1/s1", params={"die": "TEST DIE", "config": "L1"}).status_code == 422
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ANONYMOUS, "dies": frozenset()})
    assert client.get("/api/propellers/syn_s1/s1", params={"die": "TEST DIE", "config": "L1"}).status_code == 401
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_GRANTED, "dies": frozenset()})
    monkeypatch.setattr(ma, "may_see_die", lambda acc, die: False)
    assert client.get("/api/propellers/syn_s1/s1", params={"die": "TEST DIE", "config": "L1"}).status_code == 404


# ---------------------------------------------------------------------------
# the calibration fit (scripts/export_propeller_s1_model.py calibrate)
# ---------------------------------------------------------------------------

def _exporter():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "export_propeller_s1_model.py"
    spec = importlib.util.spec_from_file_location("export_propeller_s1_model", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_calibration_fit_recovers_known_constants(tmp_path):
    """Anchors made by a known calibrated network: the fit gives the constants back
    and the 'after' temperatures land on the anchors."""
    ex = _exporter()
    m = s1.load_model("CIANO14 40 new", "L12")
    model = {k: v for k, v in m.items() if k != "_path"}
    mf = tmp_path / "L12.json"
    mf.write_text(json.dumps(model), encoding="utf-8")
    true = {"housing_film_factor": 1.4, "winding_air_area_m2": 3.0e-3, "rotor_air_area_m2": 1.2e-3}
    prop = pp.get_propeller("tmotor_fpv_10x5")
    files = []
    for n, p_cu, p_r, p_t in ((6000.0, 3.2, 0.8, 6.8), (8500.0, 9.5, 1.4, 16.0), (9800.0, 15.0, 1.8, 24.0)):
        op = pp.operating_point(prop, n, pp.air_density(temp_c=25.0))
        h = s1.housing_film(op["air_speed_ms"], 25.0, 0.040)
        t = s1.temperatures(model["thermal_geom"], p_cu, p_r, p_t, h, 25.0, true)
        a = {"rpm": n, "ambient_c": 25.0, "propeller": "tmotor_fpv_10x5", "passes": [{
            "I_rms": 10.0, "error": None, "torque_miss": 0.0,
            "components": {"winding": {"max": t["winding"], "avg": t["winding"] - 0.5},
                           "magnet": {"max": t["magnet"], "avg": t["magnet"]},
                           "stator": {"avg": t["housing"]}},
            "coupling": {"em": {"P_stranded_W": p_cu}},
            "cooling": {"heat_budget": {"losses_W": p_t, "rotor_heat_split": {"rotor_W": p_r}}}}]}
        f = tmp_path / f"anchor_{n:.0f}.json"
        f.write_text(json.dumps(a), encoding="utf-8")
        files.append(str(f))
    out = tmp_path / "out.json"
    fit, check = files, files[2]          # the check row: reported, not fitted
    assert ex.main(["calibrate", str(mf), "--out", str(out), "--check", check]
                   + sum((["--anchor", f] for f in fit), [])) == 0
    cal = json.loads(out.read_text(encoding="utf-8"))["thermal_calibration"]
    for k, v in true.items():
        assert cal[k] == pytest.approx(v, rel=0.02)
    for r in cal["anchors"]:
        for k in ("winding", "magnet", "housing"):
            assert r["lumped_after_C"][k] == pytest.approx(r["fem_C"][k], abs=0.05)
    assert [r["role"] for r in cal["anchors"]] == ["fit", "fit", "fit", "check"]
    assert cal["anchor_rpm"] == [6000.0, 8500.0, 9800.0]
    lo, hi = cal["anchor_air_speed_ms"]
    assert 0 < lo < hi
    assert "basis" in cal and "slipstream_factor_basis" in cal
    m2 = json.loads(out.read_text(encoding="utf-8"))
    s1.validate_model(m2)
    # the Configure flag's applicability: inside the fitted / validated slipstream range, no margin
    assert cal["fit_domain"]["air_speed_ms"] == [lo, hi] and cal["validation_domain"]["n"] == 1
    assert s1.calibration_covers(m2, 0.5 * (lo + hi)) is True
    assert s1.calibration_covers(m2, 1.02 * hi) is False
    assert s1.calibration_covers(m2, 0.98 * lo) is False
    assert s1.thermal_domain(m2, 1.02 * hi) == "extrapolated"
    assert s1.calibration_covers(_model(), 5.0) is None       # uncalibrated: no claim


def test_housed_calibration_form_fits_the_gap_resistance(tmp_path):
    """The housed frame (Ø85) has no rotor-to-air path: its fit scales the gap
    resistance, which can make the magnet HOTTER than the uncalibrated network —
    the one direction the open form's rotor air area cannot reach."""
    geom = s1.load_model("CIANO28 85 20SW1200", "L13")["thermal_geom"]
    base = s1.temperatures(geom, 11.0, 1.2, 16.0, 25.0, 25.0, None)
    hot = s1.temperatures(geom, 11.0, 1.2, 16.0, 25.0, 25.0, {"gap_resistance_factor": 3.0})
    assert hot["magnet"] - hot["housing"] == pytest.approx(3.0 * (base["magnet"] - base["housing"]), rel=1e-9)
    with pytest.raises(s1.S1Error):
        _model(thermal_calibration={"gap_resistance_factor": 0.0})
    ex = _exporter()
    m = s1.load_model("CIANO28 85 20SW1200", "L13")
    model = {k: v for k, v in m.items() if k != "_path"}
    mf = tmp_path / "L13.json"
    mf.write_text(json.dumps(model), encoding="utf-8")
    true = {"housing_film_factor": 0.7, "winding_air_area_m2": 1.0e-3, "gap_resistance_factor": 3.0}
    prop = pp.get_propeller("tmotor_g32x11")
    files = []
    for n, p_cu, p_r, p_t in ((1250.0, 8.0, 1.0, 12.0), (1600.0, 16.0, 1.5, 22.0), (1800.0, 24.0, 2.0, 31.0)):
        op = pp.operating_point(prop, n, pp.air_density(temp_c=25.0))
        h = s1.housing_film(op["air_speed_ms"], 25.0, 0.085)
        t = s1.temperatures(geom, p_cu, p_r, p_t, h, 25.0, true)
        a = {"rpm": n, "ambient_c": 25.0, "propeller": "tmotor_g32x11", "passes": [{
            "I_rms": 8.0, "error": None, "torque_miss": 0.0,
            "components": {"winding": {"max": t["winding"], "avg": t["winding"] - 0.5},
                           "magnet": {"max": t["magnet"], "avg": t["magnet"]},
                           "stator": {"avg": t["housing"]}},
            "coupling": {"em": {"P_stranded_W": p_cu}},
            "cooling": {"heat_budget": {"losses_W": p_t, "rotor_heat_split": {"rotor_W": p_r}}}}]}
        f = tmp_path / f"anchor_{n:.0f}.json"
        f.write_text(json.dumps(a), encoding="utf-8")
        files.append(str(f))
    out = tmp_path / "out.json"
    assert ex.main(["calibrate", str(mf), "--out", str(out), "--form", "housed"]
                   + sum((["--anchor", f] for f in files), [])) == 0
    cal = json.loads(out.read_text(encoding="utf-8"))["thermal_calibration"]
    assert cal["form"] == "housed" and "rotor_air_area_m2" not in cal
    for k, v in true.items():
        assert cal[k] == pytest.approx(v, rel=0.03)
    s1.validate_model(json.loads(out.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 2026-10-11 review fixes: a SYSTEM (motor + controller + battery) rating
# ---------------------------------------------------------------------------

PP_PAIRS, PSI_PM, L_DQ = 7, 0.01, 1.0e-4


def _psi_table():
    """A linear surface-magnet psi-map: psi_d = psi_PM + L i_d, psi_q = L i_q."""
    cur = [0.0, 10.0, 20.0, 40.0, 60.0]
    gam = [-5.0 + 2.5 * k for k in range(35)]
    pd, pq, tq = [], [], []
    for I in cur:
        rd, rq, rt = [], [], []
        for g in gam:
            i_d, i_q = -math.sqrt(2) * I * math.sin(math.radians(g)), math.sqrt(2) * I * math.cos(math.radians(g))
            d, q = PSI_PM + L_DQ * i_d, L_DQ * i_q
            rd.append(d)
            rq.append(q)
            rt.append(1.5 * PP_PAIRS * (d * i_q - q * i_d))
        pd.append(rd)
        pq.append(rq)
        tq.append(rt)
    return {"I_A": cur, "gamma_deg": gam, "psi_d_Wb": pd, "psi_q_Wb": pq, "T_Nm": tq,
            "pole_pairs": PP_PAIRS, "psi_pm_Wb": PSI_PM, "magnet_C": 20.0, "gamma_max_deg": 80.0}


def _sys_model(**over):
    m = _model()
    raw = dict(m)
    raw["grid"] = {**m["grid"], "gamma_deg": [[0.0] * len(CUR) for _ in RPM]}
    raw.update(psi_map=_psi_table(), voltage={"m": 0.9, "v_dc_grid": 1000.0},
               battery={"v_min": 300.0, "v_nom": 350.0, "v_max": 400.0},
               sine_controller={"i_board_limit_A": {"favourable": 50.0, "weak": 12.0, "hot_motor": 8.0},
                                "i_board_basis": "test board", "bus_v": {"min": 250.0, "max": 450.0}},
               drives={"pwm_a": {"rpm": RPM, "I_A": CUR, "i_board_limit_A": {"favourable": 14.0},
                                 "bus_v": {"min": 250.0, "nom": 350.0, "max": 450.0},
                                 "controller_loss_W": [[0.5 * i for i in CUR] for _ in RPM],
                                 "pwm_extra_W": [[0.2 * i for i in CUR] for _ in RPM]}},
               demag={"I_limit_A": 60.0, "ref_magnet_C": 20.0})
    raw.update(over)
    return s1.validate_model(raw)


def test_controller_limit_binds_and_drive_variant_is_selected(tmp_path):
    prop = _prop(tmp_path)
    m = _sys_model()
    free = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0)["limit"]
    weak = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0, board_cooling="weak")
    L = weak["limit"]
    assert L["binding"] == "controller" and L["current_A"] <= 12.0 + 1e-6 and L["rpm"] < free["rpm"]
    assert weak["drive"]["I_cont_A"] == 12.0 and weak["drive"]["kind"] == "sine"
    assert L["qualification"]["checks"]["controller"] == "checked"
    pw = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0, drive="pwm_a")
    assert pw["limit"]["binding"] == "controller" and pw["limit"]["current_A"] <= 14.0 + 1e-6
    # the variant's controller loss reaches the battery power, its PWM extra the motor
    row = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m),
                         drive=s1.resolve_drive(m, "pwm_a"))
    assert row["controller_loss_W"] == pytest.approx(0.5 * row["current_A"], rel=1e-6)
    assert row["losses_W"]["pwm_extra"] == pytest.approx(0.2 * row["current_A"], rel=1e-6)
    assert row["battery_power_W"] == pytest.approx(row["electrical_power_W"] + row["controller_loss_W"])
    sine = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m))
    assert sine["losses_W"]["pwm_extra"] == 0.0 and sine["controller_loss_W"] is None
    with pytest.raises(s1.S1Error):
        s1.resolve_drive(m, "nope")
    with pytest.raises(s1.S1Error):
        s1.resolve_drive(m, "sine", "arctic")


def _syn_board_block():
    """A hand-made ``board`` block: P_inverter = 0.2*I, board copper 0.004*I^2,
    heat budget 20 W -> a 50 A favourable limit at the 45 C calibration (the
    loss table covers 0..120 A).  R_j-hs is small: only the heat budget binds."""
    return {"T_amb_cal_C": 45.0, "T_hs_limit_C": 95.0, "T_j_at_limit_C": 110.0,
            "T_j_derate_C": 140.0, "R_jhs_K_per_W": 1.0, "K_board_cu_W_per_A2": 0.004,
            "devices_per_switch": 1, "board_scale": 1.0,
            "classes": {"favourable": {"Q_budget_W": 20.0, "R_sys_K_per_W": 2.5},
                        "weak": {"Q_budget_W": 12.0, "R_sys_K_per_W": 50.0 / 12.0},
                        "hot_motor": {"Q_budget_W": 8.0, "R_sys_K_per_W": 6.25}},
            "loss_table": {"I_A": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0],
                           "P_inverter_W": [0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0],
                           "P_device_W": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
            "slipstream": {"class": "slipstream", "favourable_air_speed_ms": 2.0,
                           "film_diameter_m": 0.03, "limit_cap_factor": 2.0}}


def _sys_model_with_board(**over):
    """The system model with the hand-made board block on the sine controller."""
    return _sys_model(sine_controller={"i_board_limit_A": {"favourable": 50.0, "weak": 12.0,
                                                           "hot_motor": 8.0},
                                       "i_board_basis": "test board",
                                       "bus_v": {"min": 250.0, "max": 450.0},
                                       "board": _syn_board_block()}, **over)


def test_the_board_limit_is_re_evaluated_at_the_s1_air_temperature(tmp_path):
    """A model with a ``board`` block: every point's controller limit is the
    board model at the S1 air temperature (and the wash speed for ``slipstream``);
    the drive keeps the precomputed 45 C card."""
    prop = _prop(tmp_path)
    m = _sys_model_with_board()
    d = s1.resolve_drive(m, "sine", "favourable")
    assert d["I_cont_A"] == 50.0                       # the precomputed card, untouched
    l45, _ = s1.board_limit_A(d, ambient_c=45.0)
    l25, basis = s1.board_limit_A(d, ambient_c=25.0)
    assert l45 == pytest.approx(50.0, abs=0.1) and l25 > l45
    assert "board model" in basis and "25" in basis
    # slipstream: no precomputed card, evaluated per point at the wash speed
    ds = s1.resolve_drive(m, "sine", "slipstream")
    assert ds["I_cont_A"] is None and "per operating point" in ds["I_cont_basis"]
    fav = s1.board_limit_A(s1.resolve_drive(m, "sine", "favourable"), ambient_c=25.0)[0]
    w = s1.board_limit_A(ds, ambient_c=25.0, air_speed_ms=10.0)[0]
    assert fav < w <= 2.0 * fav + 1e-9
    with pytest.raises(s1.S1Error):
        s1.board_limit_A(ds, ambient_c=25.0)           # no wash speed to evaluate at
    row = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m), drive=ds)
    assert row["controller_limit_A"] == pytest.approx(
        s1.board_limit_A(ds, ambient_c=25.0, air_speed_ms=row["air_speed_ms"])[0], rel=1e-12)
    assert "wash" in row["controller_limit_basis"]
    # a model without board data refuses the class instead of guessing
    with pytest.raises(s1.S1Error):
        s1.resolve_drive(_sys_model(), "sine", "slipstream")


def test_the_response_lists_the_board_cooling_classes_it_supports(tmp_path):
    """``board_cooling_classes`` — what the UI may offer: the classes the controller
    block carries, and ``slipstream`` ONLY where a ``board`` block exists (without
    one the route refuses that class, so it must never be offered)."""
    prop, m = _prop(tmp_path), _sys_model()
    assert s1.board_cooling_classes(m["sine_controller"]) == ["favourable", "weak", "hot_motor"]
    assert s1.resolve_drive(m, "sine")["board_cooling_classes"] == ["favourable", "weak", "hot_motor"]
    assert s1.resolve_drive(m, "pwm_a")["board_cooling_classes"] == ["favourable"]  # that variant's one class
    assert s1.resolve_drive(m, s1.EXTERNAL_ESC)["board_cooling_classes"] == []     # an external ESC has no board
    assert _run(m, prop)["drive"]["board_cooling_classes"] == ["favourable", "weak", "hot_motor"]
    mb = _sys_model_with_board()
    assert s1.board_cooling_classes(mb["sine_controller"]) == list(s1.BOARD_COOLING)
    assert s1.resolve_drive(mb, "sine", "slipstream")["board_cooling_classes"] == list(s1.BOARD_COOLING)
    assert _run(mb, prop, board_cooling="slipstream")["drive"]["board_cooling_classes"] == list(s1.BOARD_COOLING)
    # no controller block at all: no class is supported
    assert s1.board_cooling_classes({}) == [] and s1.board_cooling_classes(None) == []


def test_unknown_controller_is_a_motor_only_limit_and_says_so(tmp_path):
    L = _run(_model(), _prop(tmp_path))["limit"]
    assert L["drive_feasibility"] == "unknown"
    assert "drive_unknown" in L["flags"] and "voltage_unverified" in L["flags"]
    assert set(L["qualification"]["unverified"]) >= {"controller", "voltage_min_pack", "battery_current"}


def test_pack_outside_the_drive_bus_range_fails_the_drive(tmp_path):
    m = _sys_model()
    r = _run(m, _prop(tmp_path), pack_v_min=300.0, pack_v_max=500.0)
    assert r["drive"]["pack_fits"] is False
    assert r["limit"]["drive_feasibility"] == "failed" and "drive_failed" in r["limit"]["flags"]
    ok = _run(m, _prop(tmp_path))
    assert ok["drive"]["pack_fits"] is True and ok["limit"]["drive_feasibility"] == "checked"


def test_battery_current_binds_only_when_given(tmp_path):
    m, prop = _sys_model(), _prop(tmp_path)
    a = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0)
    assert "battery_current_unknown" in a["limit"]["flags"]
    lim_A = 0.5 * a["limit"]["battery_current_A"]
    b = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0, pack_i_max_A=lim_A)["limit"]
    assert b["binding"] == "battery_current" and b["battery_current_A"] <= lim_A + 1e-6
    # sine: no controller loss known, the motor input over the minimum pack voltage
    assert b["battery_current_A"] == pytest.approx(b["electrical_power_W"] / 300.0, rel=1e-9)


def test_external_esc_bus_current_binds_and_says_what_it_omits(tmp_path):
    """An ESC that is none of the passport's boards: its continuous BUS current (the
    user's, else the model's bench ESC) is checked against the battery current at the
    pack minimum; its losses and the motor PWM extra are unknown and flagged."""
    prop = _prop(tmp_path)
    m = _sys_model(external_esc={"name": "bench ESC", "i_bus_cont_A": 1000.0, "basis": "bench run"})
    hot = dict(winding_limit_c=400.0, magnet_limit_c=400.0)
    free = _run(m, prop, drive=s1.EXTERNAL_ESC, **hot)
    F = free["limit"]
    assert free["drive"]["kind"] == "external" and free["drive"]["I_bus_cont_A"] == 1000.0
    assert free["drive"]["source"] == "model" and free["drive"]["name"] == "bench ESC"
    assert F["binding"] != "controller" and F["controller_limit_A"] is None
    assert {"esc_external", "esc_loss_omitted", "pwm_omitted"} <= set(F["flags"])
    assert "drive_unknown" not in F["flags"] and F["qualification"]["checks"]["controller"] == "checked"
    assert F["qualification"]["losses"]["controller"] == "unknown (external ESC)"
    # the bus current: motor input over the pack minimum (no ESC loss known)
    assert F["battery_current_A"] == pytest.approx(F["electrical_power_W"] / 300.0, rel=1e-9)
    lim = 0.5 * F["battery_current_A"]
    r = _run(m, prop, drive=s1.EXTERNAL_ESC, esc_i_bus_A=lim, **hot)
    L = r["limit"]
    assert r["drive"]["source"] == "caller" and r["drive"]["I_bus_cont_A"] == lim
    assert L["binding"] == "controller" and L["battery_current_A"] <= lim + 1e-6
    assert L["controller_bus_limit_A"] == lim and L["rpm"] < F["rpm"]
    row = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m),
                         drive=s1.resolve_drive(m, s1.EXTERNAL_ESC))
    assert row["controller_loss_W"] is None and row["losses_W"]["pwm_extra"] == 0.0
    # a phase-current rating alone binds on the phase current
    ph = _run(m, prop, drive=s1.EXTERNAL_ESC, esc_i_phase_A=10.0, **hot)["limit"]
    assert ph["binding"] == "controller" and ph["current_A"] <= 10.0 + 1e-6
    # no ESC current anywhere: the limit is motor-only and says so
    bare = _run(_sys_model(), prop, drive=s1.EXTERNAL_ESC, **hot)
    assert bare["drive"]["I_bus_cont_A"] is None and "drive_unknown" in bare["limit"]["flags"]
    with pytest.raises(s1.S1Error):
        s1.resolve_drive(m, s1.EXTERNAL_ESC, esc_i_bus_A=-5.0)
    with pytest.raises(s1.S1Error):
        _sys_model(external_esc={"i_bus_cont_A": 0.0})
    assert free["drives_available"][-1] == s1.EXTERNAL_ESC and free["external_esc_default"]["name"] == "bench ESC"


def test_model_slipstream_factor_is_the_default_air_and_is_qualified(tmp_path):
    prop = _prop(tmp_path)
    base = {"housing_film_factor": 1.0}
    cal = {**base, "slipstream_factor": 0.8, "slipstream_factor_status": "bench",
           "slipstream_factor_basis": "fitted to two bench runs"}
    m, plain = _model(thermal_calibration=cal), _model(thermal_calibration=base)
    kw = dict(ambient_c=25.0, rho=1.2, lims=s1.limits_of(m))
    row = s1.solve_point(m, prop, 6000.0, **kw)
    assert row["slipstream_factor"] == 0.8 and row["slipstream_source"] == "model"
    assert row["slipstream_position"] == "calibrated"
    ref = s1.solve_point(plain, prop, 6000.0, **kw)
    assert ref["slipstream_factor"] == pp.POSITION_FACTORS[pp.DEFAULT_POSITION] and ref["slipstream_source"] == "default"
    assert row["air_speed_ms"] == pytest.approx(ref["air_speed_ms"] * 0.8 / 0.4, rel=1e-9)
    assert row["T_winding_C"] < ref["T_winding_C"]
    # the caller's factor or position wins over the model's
    assert s1.solve_point(m, prop, 6000.0, factor=0.3, **kw)["slipstream_factor"] == 0.3
    assert s1.solve_point(m, prop, 6000.0, position="disc_plane", **kw)["slipstream_factor"] == 0.5
    L = _run(m, prop)["limit"]
    assert "slipstream_bench" in L["flags"] and "slipstream_uncalibrated" not in L["flags"]
    q = L["qualification"]["slipstream"]
    assert q["calibrated"] is True and q["status"] == "bench" and q["basis"] == "fitted to two bench runs"
    assert L["cooling"]["slipstream_source"] == "model"
    t = _run(_model(thermal_calibration={**cal, "slipstream_factor_status": "transferred"}), prop)["limit"]
    assert "slipstream_transferred" in t["flags"] and "slipstream_bench" not in t["flags"]
    assert "slipstream_uncalibrated" in _run(plain, prop)["limit"]["flags"]
    with pytest.raises(s1.S1Error):
        _model(thermal_calibration={**base, "slipstream_factor": 2.0})


def test_voltage_is_rechecked_at_minimum_pack_voltage_and_hot_flux():
    m = _sys_model()
    r = s1.resistance(m, 20.0)
    w = 2 * math.pi * 6000.0 / 60.0 * PP_PAIRS
    i_q = math.sqrt(2) * 20.0
    v = math.hypot(-w * L_DQ * i_q, r * i_q + w * PSI_PM)
    assert s1.phase_voltage(m, 6000.0, 20.0, 0.0, 20.0, r) == pytest.approx(v, rel=1e-9)
    # hot magnets: less PM flux, less voltage (k_psi = 1 - 0.001 (T - 20))
    hot = s1.phase_voltage(m, 6000.0, 20.0, 0.0, 120.0, r)
    assert hot == pytest.approx(math.hypot(-w * L_DQ * i_q, r * i_q + w * PSI_PM * 0.9), rel=1e-9)
    drv = s1.resolve_drive(m)
    vd = lambda vl: vl * math.sqrt(3) / 0.9                               # noqa: E731
    assert s1.voltage_check(m, drv, 6000.0, 20.0, 20.0, 20.0, vd(v * 1.01))["status"] == "ok"
    deep = s1.voltage_check(m, drv, 6000.0, 20.0, 20.0, 20.0, vd(v * 0.99))
    assert deep["status"] == "deeper_fw" and deep["I_A"] > 20.0 and deep["gamma_deg"] > 0.0
    st = s1.dq_state(m, deep["I_A"], deep["gamma_deg"], 20.0)
    assert st["T"] >= s1.dq_state(m, 20.0, 0.0, 20.0)["T"] - 1e-6
    assert s1.phase_voltage(m, 6000.0, deep["I_A"], deep["gamma_deg"], 20.0, r) <= deep["V_limit_V"] + 1e-9
    # the same pack voltage holds with hot magnets
    assert s1.voltage_check(m, drv, 6000.0, 20.0, 120.0, 20.0, vd(v * 0.99))["status"] == "ok"
    assert s1.voltage_check(m, drv, 6000.0, 20.0, 20.0, 20.0, vd(5.0))["status"] == "infeasible"
    assert s1.voltage_check(_model(), drv, 6000.0, 20.0, 20.0, 20.0, 300.0)["status"] == "unverified"


def test_voltage_binds_the_s1_point_at_a_low_pack(tmp_path):
    m, prop = _sys_model(), _prop(tmp_path)
    w = 2 * math.pi * 4000.0 / 60.0 * PP_PAIRS
    low = 0.6 * w * PSI_PM * math.sqrt(3) / 0.9           # the no-load voltage reaches it below 4000 rpm
    r = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0, pack_v_min=low, pack_v_max=low * 1.05)
    rows = [x for x in r["rows"] if x["voltage"]]
    assert any(x["voltage"]["status"] == "deeper_fw" for x in rows)
    high = _run(m, prop, winding_limit_c=400.0, magnet_limit_c=400.0)
    assert r["limit"]["rpm"] < high["limit"]["rpm"]              # the low pack costs speed
    deep = [x for x in rows if x["ok"] and x["voltage"]["status"] == "deeper_fw"]
    assert deep
    for x in deep:
        assert x["current_A"] > x["current_grid_A"]
        assert x["losses_W"]["cu_dc"] == pytest.approx(3 * x["current_A"] ** 2 * s1.resistance(m, x["T_winding_C"]),
                                                       rel=1e-4)        # at the iterate, within the 0.01 K tolerance


def test_demag_limit_follows_the_magnet_temperature(tmp_path):
    card = {"name": "T", "T_ref_C": 20.0, "Br_ref_T": 1.44, "alpha_br_pct_per_K": -0.12,
            "Hcj_ref_A_m": 1.592e6, "beta_hcj_pct_per_K": -0.55, "mu_rec": 1.05, "knee_fraction": 0.97}
    m = _sys_model(demag={"I_limit_A": 40.0, "ref_magnet_C": 50.0, "magnet": card, "permeance_coeff": 2.0})
    assert s1.demag_limit_at(m, 30.0)[0] == 40.0                   # held below the probe temperature
    i_hot, basis = s1.demag_limit_at(m, 120.0)
    assert 0 < i_hot < 40.0 and basis.startswith("reference temperature only")

    def margin(t):
        dt = t - 20.0
        return 0.97 * 1.592e6 * (1 - 0.0055 * dt) - 1.44 * (1 - 0.0012 * dt) / (4e-7 * math.pi * 3.05)
    assert i_hot == pytest.approx(40.0 * margin(120.0) / margin(50.0), rel=1e-9)
    # scaling only the coercivity would NOT be conservative: the self field eats the margin first
    assert i_hot < 40.0 * (1 - 0.0055 * 100) / (1 - 0.0055 * 30)
    bare = _sys_model(demag={"I_limit_A": 40.0, "ref_magnet_C": 50.0})
    assert s1.demag_limit_at(bare, 120.0)[0] is None
    r = _run(_sys_model(demag={"I_limit_A": 8.0, "ref_magnet_C": 20.0, "magnet": card}), _prop(tmp_path),
             winding_limit_c=400.0, magnet_limit_c=400.0)
    assert r["limit"]["binding"] == "demag" and "demag_reference_only" in r["limit"]["flags"]
    assert r["limit"]["current_A"] <= r["limit"]["demag_limit_A"] + 1e-6


def test_missing_loss_cell_refuses_the_point_and_current_extrapolation_is_flagged(tmp_path):
    m = _model()
    m["grid"]["loss_W"]["fe_stator"][2] = [None] * len(CUR)      # the 4000 rpm row: no value
    ls = s1.losses(m, 4000.0, 20.0, 50.0)
    assert ls["unavailable"] == ["fe_stator"]
    row = s1.solve_point(m, _prop(tmp_path), 4000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m))
    assert row["binding"] == "loss_data" and row["losses_unavailable"] == ["fe_stator"] and not row["ok"]
    full = _model()
    a = s1.losses(full, 3000.0, 2.0, 50.0)                          # below the 5 A first current
    assert a["below_loss_grid_current"] and not a["below_loss_grid_speed"] and a["below_loss_grid"]
    b = s1.losses(full, 500.0, 20.0, 50.0)
    assert b["below_loss_grid_speed"] and not b["below_loss_grid_current"]
    c = s1.losses(full, 3000.0, 20.0, 50.0)
    assert not c["below_loss_grid"] and c["unavailable"] == []


def test_shipped_l12_with_a_null_loss_cell_is_refused_not_zeroed():
    m = s1.load_model("CIANO14 40 new", "L12")
    ok = s1.losses(m, 6500.0, 21.39, 100.0)
    assert ok["fe_stator"] > 1.0 and not ok["unavailable"]
    m["grid"]["loss_W"]["fe_stator"][1][1] = None
    assert s1.losses(m, 6500.0, 21.39, 100.0)["unavailable"] == ["fe_stator"]
    # and the shipped model flags the review's 3.70 A point below its 10.7 A grid current
    low = s1.losses(s1.load_model("CIANO14 40 new", "L12"), 3720.0, 3.70, 40.0)
    assert low["below_loss_grid_current"]


def test_nonconvergence_is_not_runaway(tmp_path, monkeypatch):
    m, prop = _model(), _prop(tmp_path)
    monkeypatch.setattr(s1, "FIXED_POINT_MAX_IT", 2)
    row = s1.solve_point(m, prop, 6000.0, ambient_c=25.0, rho=1.2, lims=s1.limits_of(m))
    assert row["binding"] == "nonconverged" and row["T_winding_C"] is None and row["converged"] is False
    r = _run(m, prop)
    assert r["nonconverged_rpm"] and not any(x["binding"] == "runaway" for x in r["rows"])


def test_refinement_finds_a_band_between_coarse_samples(tmp_path):
    """Losses are huge except at the 2000 rpm grid row: the coarse 5-point scan
    (1000, 3750, 6500 ...) misses the cool band; the row breakpoints find it."""
    m = _model()
    m["grid"]["loss_W"]["fe_stator"] = [[(5.0 if n == 2000.0 else 400.0) for _ in CUR] for n in RPM]
    r = _run(m, _prop(tmp_path), n_points=5)
    assert not any(x["ok"] for x in r["rows"])
    assert r["limit"] is not None and 2000.0 < r["limit"]["rpm"] < 3750.0
    assert r["limit"]["binding"] in ("winding", "magnet") and r["bands"]


def test_scan_end_is_a_lower_bound_with_its_reason(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    r = _run(m, prop, rpm_max=4000.0, winding_limit_c=400.0, magnet_limit_c=400.0)["limit"]
    assert r["lower_bound"] is True and r["range_end"] == "requested"
    g = m["grid"]
    wide = _model(grid={**g, "rpm": RPM + [16000.0], "T_Nm": g["T_Nm"] + [g["T_Nm"][-1]],
                        "loss_W": {k: t + [t[-1]] for k, t in g["loss_W"].items()}},
                  limits={**m["limits"], "rpm_max": 16000.0})
    r2 = s1.s1_rating(wide, prop, rpm_min=1000.0, winding_limit_c=400.0, magnet_limit_c=400.0)["limit"]
    assert r2["binding"] == "speed_range" and r2["lower_bound"] and r2["range_end"] == "propeller_data"
    assert r2["rpm"] == pytest.approx(1.5 * prop.rpm_range[1])


def test_flux_slope_is_normalised_to_the_hot_reference():
    ex = _exporter()
    psi_h, t_h, psi_c, t_c = 0.00102108, 53.5, 0.00103042, 20.0
    c = ex.flux_slope(psi_h, t_h, psi_c, t_c)
    m = _model(flux_temp_coeff_per_K=c, magnet_ref_temp_C=t_h)
    assert s1.flux_factor(m, t_h) == 1.0
    assert psi_h * s1.flux_factor(m, t_c) == pytest.approx(psi_c, rel=1e-12)
    for die, cfg in (("CIANO14 40 new", "L12"), ("CIANO14 40 new", "L20")):
        sm = s1.load_model(die, cfg)
        basis = sm["flux_coeff_basis"]
        hot = float(basis.split("hot ")[1].split(" Wb @ ")[0])
        cold = float(basis.split("cold ")[1].split(" Wb @ ")[0])
        assert cold == pytest.approx(hot * s1.flux_factor(sm, 20.0), rel=1e-5)


def test_exporter_loss_group_never_turns_missing_into_zero():
    ex = _exporter()
    assert ex.loss_group({"groups_W": {"P_mag_W": 1.5, "P_shaft_W": 0.25}},
                         ("P_mag_W",), ("P_shaft_W", "P_sleeve_W")) == 1.75
    assert ex.loss_group({"groups_W": {"P_shaft_W": 0.25}}, ("P_mag_W",), ("P_shaft_W",)) is None
    assert ex.loss_group({"groups_W": {"P_fe_stator_W": 0.0}}, ("P_fe_stator_W",), ()) == 0.0


def test_thermal_domain_fit_validated_extrapolated():
    cal = {"housing_film_factor": 1.0, "fit_domain": {"air_speed_ms": [6.0, 10.0]},
           "validation_domain": {"air_speed_ms": [12.0, 12.0]}}
    m = _model(thermal_calibration=cal)
    assert s1.thermal_domain(m, 8.0) == "fit"
    assert s1.thermal_domain(m, 11.0) == "validated" and s1.calibration_covers(m, 11.0) is True
    assert s1.thermal_domain(m, 12.3) == "extrapolated" and s1.calibration_covers(m, 12.3) is False
    assert s1.thermal_domain(_model(), 8.0) is None


def test_shipped_models_carry_the_system_data_and_qualify_their_limits():
    l12 = s1.load_model("CIANO14 40 new", "L12")
    assert l12["schema"] == s1.SCHEMA and "si_48k" in l12["drives"]
    assert l12["drives"]["si_48k"]["i_board_limit_A"]["favourable"] == pytest.approx(26.1, abs=0.01)
    # the board block (the passport's calibration + an analytical loss table) and the
    # two-devices-per-switch ESTIMATE variants, re-exported 2026-10-12
    from motor_ai_sim.passport_v1 import stage3 as S3
    blk = l12["drives"]["si_48k"]["board"]
    assert blk["T_amb_cal_C"] == 45.0 and blk["devices_per_switch"] == 1 and blk["board_scale"] == 1.0
    assert S3.board_block_limit(blk, t_amb_c=45.0) == pytest.approx(26.1, abs=0.5)
    assert S3.board_block_limit(blk, t_amb_c=25.0) > 26.1
    assert S3.board_block_limit(blk, t_amb_c=25.0, cls="slipstream", air_speed_ms=15.0) \
        > S3.board_block_limit(blk, t_amb_c=25.0)
    assert l12["sine_controller"]["board"]["devices_per_switch"] == 1
    base = l12["drives"]["si_48k"]["i_board_limit_A"]["favourable"]
    for vid in ("si_48k_x2", "si_48k_x2_board2"):
        v = l12["drives"][vid]
        assert v["n_parallel"] == 2 and v["board"]["devices_per_switch"] == 2
        assert v["i_board_limit_A"]["favourable"] > base
        assert "estimate" in " ".join(v["labels"])
    assert l12["drives"]["si_48k_x2"]["board_scale"] == 1.0
    assert l12["drives"]["si_48k_x2_board2"]["board_scale"] == 2.0
    assert l12["drives"]["si_48k_x2_board2"]["i_board_limit_A"]["favourable"] \
        > l12["drives"]["si_48k_x2"]["i_board_limit_A"]["favourable"]
    cal = l12["thermal_calibration"]
    # 2026-10-12: the network refitted with 4 FEM anchors at the two bench points (up to 19.8 m/s)
    assert cal["fit_domain"]["rpm"] == [5935.0, 14400.0] and cal["validation_domain"]["rpm"] == [11370.0, 11370.0]
    assert cal["fit_domain"]["air_speed_ms"][1] == pytest.approx(19.8, abs=0.1)
    # the slipstream factor fitted to the two bench runs is the S1's default air
    assert cal["slipstream_factor"] == pytest.approx(0.53) and cal["slipstream_factor_status"] == "bench"
    assert [p["T_measured_C"] for p in cal["bench"]["points"]] == [150.0, 201.0]
    assert all(abs(p["model_T_winding_C"] - p["T_measured_C"]) < 5.0 for p in cal["bench"]["points"])
    prop = pp.get_propeller("tmotor_fpv_10x5")
    for drive in ("sine", "si_48k"):
        L = s1.s1_rating(l12, prop, drive=drive)["limit"]
        # the controller binds at the board model's 25 °C limit (above the 45 °C card)
        assert L["binding"] == "controller" and 25.0 < L["current_A"] <= L["controller_limit_A"] + 1e-6
        assert L["controller_limit_A"] > 26.1
        assert L["drive_feasibility"] == "checked" and "slipstream_bench" in L["flags"]
        assert "slipstream_uncalibrated" not in L["flags"] and L["cooling"]["slipstream_factor"] == pytest.approx(0.53)
        assert L["qualification"]["checks"]["voltage_min_pack"] == "checked"
    # motor-only (no controller): beyond the held-out 11 370 rpm check, inside the refitted domain
    bare = {k: v for k, v in l12.items() if k not in ("sine_controller", "external_esc")}
    mo = s1.s1_rating(bare, prop)["limit"]
    assert mo["rpm"] > 11370.0 and "thermal_extrapolated" not in mo["flags"] and "drive_unknown" in mo["flags"]
    # the bench system: propeller HQ1050-3, the bench's ESC (55 A bus), 24.6 V -> near the 1 kW record
    assert l12["external_esc"]["i_bus_cont_A"] == 55.0
    b = s1.s1_rating(l12, pp.get_propeller("motres_bench_hq1050_3"), drive=s1.EXTERNAL_ESC,
                     pack_v_min=24.6, pack_v_max=25.2)
    B = b["limit"]
    assert b["drive"]["source"] == "model" and 13500.0 < B["rpm"] < 14800.0
    assert 900.0 < B["shaft_power_W"] < 1100.0 and B["battery_current_A"] < 55.0
    l20m = s1.load_model("CIANO14 40 new", "L20")
    assert l20m["thermal_calibration"]["slipstream_factor_status"] == "transferred" and "external_esc" not in l20m
    l20 = s1.s1_rating(l20m, pp.get_propeller("tmotor_p13x4_4"))["limit"]
    assert l20["lower_bound"] and l20["range_end"] == "propeller_data" and "slipstream_transferred" in l20["flags"]
    l13 = s1.load_model("CIANO28 85 20SW1200", "L13")
    assert l13["loss_groups"]["mech"]["status"] == "omitted"
    L = s1.s1_rating(l13, pp.get_propeller("tmotor_g32x11"))["limit"]
    assert "mech_omitted" in L["flags"] and L["voltage"]["v_dc"] == 36.0


def test_the_l20_and_l13_models_carry_board_blocks_and_offer_their_classes():
    """Both re-exported models (2026-10-12) carry a ``board`` block on every
    controller block, so ``slipstream`` is offered and runs there too.  The L13's
    Ø85 board keeps its OWN x2 scale (the Ø40 12S board scaled x2 in the passport)
    — its heat budget is x2, never x4."""
    ms = {cfg: s1.load_model(die, cfg) for die, cfg in
          (("CIANO14 40 new", "L20"), ("CIANO28 85 20SW1200", "L13"))}
    for cfg, m in ms.items():
        for ctrl in [m["sine_controller"]] + list(m["drives"].values()):
            assert ctrl.get("board"), (cfg, ctrl.get("id") or "sine")
            assert s1.board_cooling_classes(ctrl) == list(s1.BOARD_COOLING)
            assert set(ctrl["board"]["classes"]) >= {"favourable", "weak", "hot_motor"}
        assert s1.board_cooling_classes(m["sine_controller"]) == list(s1.BOARD_COOLING)
    assert ms["L20"]["sine_controller"]["board"]["board_scale"] == 1.0
    assert ms["L13"]["sine_controller"]["board"]["board_scale"] == 2.0
    assert ms["L13"]["sine_controller"]["board"]["devices_per_switch"] == 2
    q20 = ms["L20"]["sine_controller"]["board"]["classes"]
    for k, v in ms["L13"]["sine_controller"]["board"]["classes"].items():
        # the blocks carry the numbers sig-rounded to 6 digits (board_export_block)
        assert v["Q_budget_W"] == pytest.approx(2.0 * q20[k]["Q_budget_W"], rel=1e-5)
        assert v["R_sys_K_per_W"] == pytest.approx(q20[k]["R_sys_K_per_W"] / 2.0, rel=1e-5)


def test_route_takes_the_drive_and_the_pack(client, wired):
    m = _sys_model()
    (wired / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(m), encoding="utf-8")
    q = {"die": "TEST DIE", "config": "L1", "n": 11}
    j = client.get("/api/propellers/syn_s1/s1", params={**q, "drive": "pwm_a", "pack_v_min": 280,
                                                         "pack_v_max": 400, "pack_i_max_A": 50}).json()
    assert j["drive"]["id"] == "pwm_a" and j["pack"]["v_min"] == 280.0 and j["pack"]["i_max_A"] == 50.0
    assert j["drives_available"] == [s1.SINE, "pwm_a", s1.EXTERNAL_ESC]
    assert j["drive"]["board_cooling_classes"] == ["favourable"]   # pwm_a carries one class
    e = client.get("/api/propellers/syn_s1/s1", params={**q, "drive": s1.EXTERNAL_ESC,
                                                        "esc_i_bus_max_A": 42.5}).json()
    assert e["drive"]["kind"] == "external" and e["drive"]["I_bus_cont_A"] == 42.5
    assert client.get("/api/propellers/syn_s1/s1", params={**q, "drive": s1.EXTERNAL_ESC,
                                                           "esc_i_bus_max_A": -1}).status_code == 422
    assert client.get("/api/propellers/syn_s1/s1", params={**q, "drive": "nope"}).status_code == 422
    assert client.get("/api/propellers/syn_s1/s1", params={**q, "board_cooling": "arctic"}).status_code == 422


def test_route_takes_the_board_cooling_class(client, wired):
    q = {"die": "TEST DIE", "config": "L1", "n": 11}
    # slipstream on a model without board data: refused with the reason, not guessed
    r = client.get("/api/propellers/syn_s1/s1", params={**q, "board_cooling": "slipstream"})
    assert r.status_code == 422 and "board" in r.json()["detail"]
    # with a board block it is evaluated per point at the S1 air temperature
    (wired / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(_sys_model_with_board()),
                                                       encoding="utf-8")
    r2 = client.get("/api/propellers/syn_s1/s1", params={**q, "board_cooling": "slipstream"})
    assert r2.status_code == 200, r2.text
    j = r2.json()
    assert j["drive"]["board_cooling_classes"] == list(s1.BOARD_COOLING)   # slipstream is on offer here
    assert all(row["controller_limit_A"] is not None for row in j["rows"])
    assert any("wash" in row["controller_limit_basis"] for row in j["rows"])
    assert j["limit"]["controller_limit_A"] is not None
