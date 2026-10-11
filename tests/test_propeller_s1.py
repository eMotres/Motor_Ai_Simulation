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
    """The three extracted models load; the Ø40 ones give a thermal limit inside the grid."""
    for die, cfg in (("CIANO14 40 new", "L12"), ("CIANO14 40 new", "L20"), ("CIANO28 85 20SW1200", "L13")):
        m = s1.load_model(die, cfg)
        assert m["limits"]["magnet_C"] == 150.0
        assert "winding_C" not in m["limits"]            # the project's ONE class applies, not a model number
        lims = s1.limits_of(m)
        assert lims["winding_C"] == report.PROJECT_INSULATION_C == 200.0
        assert "class N" in lims["winding_basis"]
    r = s1.s1_rating(s1.load_model("CIANO14 40 new", "L12"), pp.get_propeller("tmotor_fpv_10x5"))
    assert r["limit"] and r["limit"]["binding"] in ("winding", "magnet")
    assert 3000.0 < r["limit"]["rpm"] < 20000.0


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
    # the Configure flag's applicability: inside the anchors' slipstream range ± 20 %
    assert s1.calibration_covers(m2, 0.5 * (lo + hi)) is True
    assert s1.calibration_covers(m2, 1.19 * hi) is True
    assert s1.calibration_covers(m2, 1.25 * hi) is False
    assert s1.calibration_covers(m2, 0.75 * lo) is False
    assert s1.calibration_covers(model, 5.0) is None          # no range stated: no claim


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
