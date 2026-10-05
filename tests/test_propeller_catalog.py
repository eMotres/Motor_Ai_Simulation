"""Propeller catalogue, thrust/torque model, slipstream cooling air, thermal hook.

Pins what ``motor_ai_sim.propeller`` promises (docs/PROPELLER_CATALOG_2026-10-05.md):

  * every catalogue YAML loads, and a prop with torque data reproduces its own
    published points within their scatter;
  * shaft power comes from torque, never from the electrical column;
  * the slipstream speed is momentum theory: sqrt(2T/(rho A)) times a stated factor;
  * the operating point (rpm for a torque, equilibrium with a motor curve);
  * the thermal hook is the identity for every non-propeller source;
  * the routes and the per-die cooling options.

Local, closed-form, no FEM: the whole file runs in about a second.
"""
from __future__ import annotations

import math

import pytest
import yaml

from motor_ai_sim import propeller as pp

IN = 25.4
G0 = 9.80665


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _synthetic(tmp_path, *, ct=0.10, cp=0.04, d_m=0.30, torque=True, estimate=None, name="syn"):
    """A catalogue entry generated from KNOWN constants, so the fit must return them."""
    rho = 1.225
    rows = []
    for rpm in (3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000):
        n = rpm / 60.0
        T = ct * rho * n ** 2 * d_m ** 4
        P = cp * rho * n ** 3 * d_m ** 5
        rows.append({"thr": 50, "V": 20.0, "I": 5.0, "Pel": 100.0, "rpm": rpm,
                     "thrust_g": round(T / G0 * 1000.0, 3),
                     "torque_Nm": (round(P / (2 * math.pi * n), 6) if torque else None)})
    raw = {
        "schema": "propeller-1", "id": name, "vendor": "TEST", "model": name,
        "geometry": {"diameter_in": d_m / 0.0254, "diameter_mm": d_m * 1000.0, "pitch_in": 4.0, "blades": 2},
        "performance": {
            "data_quality": "torque_measured" if torque else "thrust_measured_power_estimated",
            "test_density_kg_m3": rho,
            "tables": [{"id": "t1", "source_url": "x", "accessed": "2026-10-05", "use_in_fit": True,
                        "torque_resolution_Nm": 1e-6, "rows": rows}],
        },
    }
    if estimate:
        raw["performance"]["power_estimate"] = estimate
    return pp._build(raw, str(tmp_path / (name + ".yaml")))


@pytest.fixture()
def catalog():
    return pp.load_catalog()


# ---------------------------------------------------------------------------
# 1. the catalogue
# ---------------------------------------------------------------------------

def test_catalog_loads_with_expected_entries(catalog):
    ids = set(catalog)
    assert {"tmotor_p12x4", "tmotor_p13x4_4", "tmotor_fpv_10x5", "tmotor_fpv_13x10",
            "tmotor_fpv_13x12", "tmotor_cf10x3_3", "tmotor_cf11x3_7", "tmotor_ms1101",
            "tmotor_ms1302", "tmotor_mf1302", "tmotor_t12x6", "tmotor_t13x6_5"} <= ids
    sizes = {round(p.diameter_nominal_in) for p in catalog.values()}
    assert {10, 11, 12, 13} <= sizes


def test_every_entry_documents_its_sources(catalog):
    for p in catalog.values():
        assert p.raw.get("source_urls"), p.id
        assert p.raw.get("accessed") == "2026-10-05", p.id
        for t in (p.raw.get("performance") or {}).get("tables") or []:
            assert t.get("source_url", "").startswith("https://"), (p.id, t.get("id"))
            assert t.get("accessed"), (p.id, t.get("id"))


def test_data_quality_is_consistent_with_how_cp_was_obtained(catalog):
    for p in catalog.values():
        if p.data_quality == "torque_measured":
            assert p.power_data == "measured_torque" and p.cp.n_used >= 6 and not p.cp.estimated, p.id
        elif p.data_quality == "thrust_measured_power_estimated":
            assert p.power_data == "estimated" and p.cp.estimated and p.cp.rel_uncertainty >= 0.15, p.id
            est = p.raw["performance"]["power_estimate"]
            assert est["estimated"] is True and est["method"] and est["basis"], p.id
        else:
            assert p.data_quality == "geometry_only" and not p.selectable and p.power_data == "none", p.id


@pytest.mark.parametrize("pid", ["tmotor_ms1101", "tmotor_ms1302", "tmotor_mf1302",
                                 "tmotor_t12x6", "tmotor_t13x6_5"])
def test_geometry_only_props_refuse_to_compute(pid):
    p = pp.get_propeller(pid)
    assert not p.selectable
    for fn in (lambda: pp.thrust_N(p, 5000), lambda: pp.torque_Nm(p, 5000),
               lambda: pp.shaft_power_W(p, 5000), lambda: pp.cooling_air_speed_ms(p, 5000)):
        with pytest.raises(pp.PropellerDataError):
            fn()


def test_unknown_id_is_not_found():
    with pytest.raises(pp.PropellerNotFound):
        pp.get_propeller("nope")


# ---------------------------------------------------------------------------
# 2. the fit reproduces the published points within their scatter
# ---------------------------------------------------------------------------

TORQUE_PROPS = ["tmotor_p12x4", "tmotor_p13x4_4", "tmotor_fpv_10x5", "tmotor_fpv_13x10", "tmotor_fpv_13x12"]


@pytest.mark.parametrize("pid", TORQUE_PROPS)
def test_fit_reproduces_published_thrust_and_torque(pid):
    p = pp.get_propeller(pid)
    # the fitted coefficients themselves: small residual over the used points
    assert p.ct.rms_rel < 0.03 and p.ct.max_abs_rel < 0.08, (pid, p.ct.summary())
    assert p.cp.rms_rel < 0.06 and p.cp.max_abs_rel < 0.15, (pid, p.cp.summary())
    # and the model against the raw table rows it was built from (inside the tested range)
    lo, hi = p.rpm_range
    n_thr = ok_thr = n_tq = ok_tq = 0
    for t in p.raw["performance"]["tables"]:
        if not t.get("use_in_fit", True):
            continue
        for r in t["rows"]:
            if not (lo <= r["rpm"] <= hi):
                continue
            n_thr += 1
            pred = pp.thrust_N(p, r["rpm"]) / G0 * 1000.0
            ok_thr += abs(pred / r["thrust_g"] - 1.0) <= 0.08
            if r.get("torque_Nm") and r["torque_Nm"] >= 0.05:
                n_tq += 1
                ok_tq += abs(pp.torque_Nm(p, r["rpm"]) / r["torque_Nm"] - 1.0) <= 0.15
    assert n_thr >= 6 and ok_thr / n_thr >= 0.95, (pid, ok_thr, n_thr)
    assert n_tq >= 6 and ok_tq / n_tq >= 0.95, (pid, ok_tq, n_tq)


@pytest.mark.parametrize("pid", TORQUE_PROPS)
def test_shaft_power_is_below_published_electrical_power(pid):
    """T-Motor's 'Power' column is electrical input.  Shaft power (from torque)
    must come out BELOW it on every row — 60-80 % is the motor+ESC+rig efficiency —
    and never be equal to it (which would mean the electrical column was used)."""
    p = pp.get_propeller(pid)
    lo, hi = p.rpm_range
    ratios = []
    for t in p.raw["performance"]["tables"]:
        if not t.get("use_in_fit", True):
            continue
        for r in t["rows"]:
            if lo <= r["rpm"] <= hi and r.get("Pel") and not r.get("Pel_suspect") and r["Pel"] > 20:
                ratios.append(pp.shaft_power_W(p, r["rpm"]) / r["Pel"])
    assert len(ratios) >= 6
    assert max(ratios) < 0.90, (pid, max(ratios))
    assert min(ratios) > 0.45, (pid, min(ratios))


def test_published_source_errors_are_flagged_not_used(catalog):
    p = catalog["tmotor_fpv_10x5"]
    bad = [r for t in p.raw["performance"]["tables"] for r in t["rows"] if r.get("Pel_suspect")]
    assert len(bad) == 3 and all(r["Pel"] < 20 for r in bad)


def test_estimated_power_props_have_measured_thrust(catalog):
    for pid in ("tmotor_cf10x3_3", "tmotor_cf11x3_7"):
        p = catalog[pid]
        assert p.ct.n_used >= 10 and p.ct.rms_rel < 0.09
        assert p.cp.estimated and 0.02 < p.cp.c_ref < 0.03
        assert pp.coefficients(p, 6000)["power_estimated"] is True
        assert pp.operating_point(p, 6000)["power_estimated"] is True


def test_synthetic_constants_are_recovered_exactly(tmp_path):
    p = _synthetic(tmp_path, ct=0.10, cp=0.04, d_m=0.30)
    assert p.ct.k == 0.0 and p.cp.k == 0.0
    assert p.ct.c_ref == pytest.approx(0.10, rel=1e-3)
    assert p.cp.c_ref == pytest.approx(0.04, rel=1e-3)
    n = 6000 / 60.0
    assert pp.thrust_N(p, 6000) == pytest.approx(0.10 * 1.225 * n ** 2 * 0.30 ** 4, rel=1e-3)
    assert pp.shaft_power_W(p, 6000) == pytest.approx(0.04 * 1.225 * n ** 3 * 0.30 ** 5, rel=1e-3)
    assert pp.torque_Nm(p, 6000) == pytest.approx(pp.shaft_power_W(p, 6000) / (2 * math.pi * n), rel=1e-9)


def test_fit_ignores_a_misrecorded_row(tmp_path):
    p = _synthetic(tmp_path)
    raw = dict(p.raw)
    rows = raw["performance"]["tables"][0]["rows"]
    rows[4] = {**rows[4], "thrust_g": rows[4]["thrust_g"] * 1.8}    # 80 % wrong, like a typo in a vendor table
    q = pp._build(raw, "x.yaml")
    assert q.ct.n_rejected >= 1
    assert q.ct.c_ref == pytest.approx(0.10, rel=0.01)


def test_coefficient_is_held_outside_the_tested_range(tmp_path):
    p = _synthetic(tmp_path)
    assert pp.coefficients(p, 5000)["extrapolated"] is False
    assert pp.coefficients(p, 20000)["extrapolated"] is True
    assert pp.coefficients(p, 20000)["ct"] == pytest.approx(pp.coefficients(p, 10000)["ct"])
    assert pp.thrust_N(p, 0) == 0.0 and pp.torque_Nm(p, 0) == 0.0


# ---------------------------------------------------------------------------
# 3. air density
# ---------------------------------------------------------------------------

def test_air_density_isa_and_variations():
    assert pp.air_density() == pytest.approx(1.2250, abs=1e-3)
    assert pp.RHO_ISA == pytest.approx(1.2250, abs=1e-3)
    assert pp.air_density(altitude_m=2000.0) == pytest.approx(1.0066, abs=2e-3)       # ISA table
    assert pp.air_density(temp_c=35.0) == pytest.approx(101325 / (287.058 * 308.15), rel=1e-9)
    assert pp.air_density(temp_c=35.0) < pp.air_density(temp_c=15.0)
    assert pp.air_density(temp_c=15.0, pressure_pa=90000.0) < pp.RHO_ISA
    with pytest.raises(ValueError):
        pp.air_density(altitude_m=20000.0)


def test_thrust_scales_with_density(tmp_path):
    p = _synthetic(tmp_path)
    assert pp.thrust_N(p, 6000, rho=0.9 * 1.225) == pytest.approx(0.9 * pp.thrust_N(p, 6000, rho=1.225))


# ---------------------------------------------------------------------------
# 4. slipstream cooling air vs a hand calculation
# ---------------------------------------------------------------------------

def test_wake_velocity_matches_momentum_theory_hand_calc(tmp_path):
    p = _synthetic(tmp_path, ct=0.10, cp=0.04, d_m=0.30)
    rpm, rho = 6000.0, 1.225
    n = rpm / 60.0                                   # 100 rev/s
    T = 0.10 * rho * n ** 2 * 0.30 ** 4              # 0.10 * 1.225 * 1e4 * 0.0081 = 9.92 N
    A = math.pi * 0.30 ** 2 / 4.0                    # 0.070686 m^2
    v_hand = math.sqrt(2.0 * T / (rho * A))          # developed wake
    assert T == pytest.approx(9.9225, rel=1e-6)
    assert v_hand == pytest.approx(15.14, abs=0.01)
    assert pp.thrust_N(p, rpm, rho) == pytest.approx(T, rel=1e-3)
    assert pp.wake_velocity_ms(p, rpm, rho) == pytest.approx(v_hand, rel=1e-3)
    # the documented factors
    assert pp.cooling_air_speed_ms(p, rpm, rho, position="developed_wake") == pytest.approx(v_hand, rel=1e-3)
    assert pp.cooling_air_speed_ms(p, rpm, rho, position="disc_plane") == pytest.approx(0.5 * v_hand, rel=1e-3)
    assert pp.cooling_air_speed_ms(p, rpm, rho) == pytest.approx(0.4 * v_hand, rel=1e-3)   # default behind_hub
    assert pp.cooling_air_speed_ms(p, rpm, rho, factor=0.25) == pytest.approx(0.25 * v_hand, rel=1e-3)


def test_cooling_air_grows_linearly_with_rpm_for_constant_ct(tmp_path):
    """T ~ n^2 so v_wake ~ n: doubling rpm doubles the air speed (constant C_T)."""
    p = _synthetic(tmp_path)
    assert pp.cooling_air_speed_ms(p, 8000) == pytest.approx(2.0 * pp.cooling_air_speed_ms(p, 4000), rel=1e-6)


def test_wake_speed_is_density_independent_at_fixed_rpm_but_power_is_not(tmp_path):
    """T ~ rho and v = sqrt(2T/(rho A)): at a fixed rpm the wake SPEED does not
    change with density (the film coefficient then follows the thin-air
    properties inside the thermal model), while the power the prop absorbs does."""
    p = _synthetic(tmp_path)
    rho_hot = pp.air_density(temp_c=45.0, altitude_m=2500.0)
    assert rho_hot < 0.85 * pp.RHO_ISA
    assert pp.cooling_air_speed_ms(p, 6000, rho=rho_hot) == pytest.approx(pp.cooling_air_speed_ms(p, 6000), rel=1e-6)
    assert pp.shaft_power_W(p, 6000, rho=rho_hot) == pytest.approx(
        pp.shaft_power_W(p, 6000) * rho_hot / pp.RHO_ISA, rel=1e-9)


def test_real_prop_air_speed_is_in_a_sane_range(catalog):
    p = catalog["tmotor_p12x4"]
    v = pp.cooling_air_speed_ms(p, 6000)
    assert 4.0 < v < 8.0                      # ~5.6 m/s at the hub for a 12x4 at 6000 rpm
    assert pp.wake_velocity_ms(p, 6000) == pytest.approx(14.0, abs=0.7)


def test_bad_position_and_factor():
    with pytest.raises(ValueError):
        pp.position_factor("somewhere")
    with pytest.raises(ValueError):
        pp.position_factor(factor=0.0)
    assert pp.position_factor() == (0.4, "behind_hub")


# ---------------------------------------------------------------------------
# 5. operating point from the propeller
# ---------------------------------------------------------------------------

def test_rpm_for_torque_round_trip(catalog):
    for pid in ("tmotor_p12x4", "tmotor_fpv_13x10"):
        p = catalog[pid]
        for rpm in (3000.0, 5000.0, 8000.0):
            assert pp.rpm_for_torque(p, pp.torque_Nm(p, rpm)) == pytest.approx(rpm, rel=1e-6)
    assert pp.rpm_for_torque(catalog["tmotor_p12x4"], 0.0) == 0.0
    with pytest.raises(pp.PropellerDataError):
        pp.rpm_for_torque(catalog["tmotor_p12x4"], 1e6)


def test_equilibrium_rpm_crossing(tmp_path):
    p = _synthetic(tmp_path)                          # tau_p = 0.04*1.225*n^2*0.3^5/(2 pi)
    # a motor with constant 0.30 N*m up to 12000 rpm; the prop reaches 0.30 N*m at:
    tau = lambda r: pp.torque_Nm(p, r)               # noqa: E731
    r_expected = pp.rpm_for_torque(p, 0.30)
    eq = pp.equilibrium_rpm(p, [0, 12000], [0.30, 0.30])
    assert eq["limited_by"] == "torque_equilibrium"
    assert eq["rpm"] == pytest.approx(r_expected, rel=1e-4)
    assert eq["torque_Nm"] == pytest.approx(0.30, rel=1e-3)
    assert eq["motor_torque_at_point_Nm"] == pytest.approx(0.30, rel=1e-6)
    assert eq["thrust_N"] == pytest.approx(pp.thrust_N(p, eq["rpm"]))
    assert eq["air_speed_ms"] == pytest.approx(pp.cooling_air_speed_ms(p, eq["rpm"]))
    assert tau(eq["rpm"]) == pytest.approx(0.30, rel=1e-3)


def test_equilibrium_with_falling_motor_curve(tmp_path):
    p = _synthetic(tmp_path)
    eq = pp.equilibrium_rpm(p, [0, 4000, 8000, 12000], [0.6, 0.5, 0.3, 0.05])
    assert eq["limited_by"] == "torque_equilibrium"
    assert 4000 < eq["rpm"] < 8000
    assert eq["torque_Nm"] == pytest.approx(eq["motor_torque_at_point_Nm"], rel=1e-3)


def test_equilibrium_limited_by_motor_speed_or_no_torque(tmp_path):
    p = _synthetic(tmp_path)
    fast = pp.equilibrium_rpm(p, [0, 3000], [5.0, 5.0])                 # strong motor, short table
    assert fast["limited_by"] == "motor_speed_limit" and fast["rpm"] == pytest.approx(3000.0)
    weak = pp.equilibrium_rpm(p, [0, 3000], [1e-6, 1e-6])             # tiny torque: crosses almost at once
    assert weak["limited_by"] == "torque_equilibrium" and 0.0 < weak["rpm"] < 100.0
    none = pp.equilibrium_rpm(p, [0, 3000], [0.0, 0.0])
    assert none["limited_by"] == "no_motor_torque" and none["rpm"] == 0.0 and none["thrust_N"] == 0.0
    with pytest.raises(ValueError):
        pp.equilibrium_rpm(p, [1000], [1.0])


# ---------------------------------------------------------------------------
# 6. the thermal hook is the identity for everything but 'propeller'
# ---------------------------------------------------------------------------

def test_thermal_default_source_is_the_identity():
    from motor_ai_sim.routes import thermal as th
    for mode in ("manual", "air", "liquid", "none", "robotics"):
        for src in ("manual", "", None, "  MANUAL "):
            v, info = th._apply_air_speed_source(src, "tmotor_p12x4", "behind_hub", rpm=8000.0,
                                                 ambient_temp=25.0, cooling_mode=mode, air_speed_mps=3.7)
            assert v == 3.7 and info is None
    # the propeller id is ignored unless the source asks for it, even when it is garbage
    v, info = th._apply_air_speed_source("manual", "not-a-prop", "bogus", rpm=1e9, ambient_temp=25.0,
                                         cooling_mode="liquid", air_speed_mps=0.0)
    assert v == 0.0 and info is None


def test_thermal_propeller_source_uses_rpm_and_ambient():
    from motor_ai_sim.routes import thermal as th
    v, info = th._apply_air_speed_source("propeller", "tmotor_p12x4", "", rpm=6000.0, ambient_temp=25.0,
                                         cooling_mode="air", air_speed_mps=99.0)
    p = pp.get_propeller("tmotor_p12x4")
    assert v == pytest.approx(pp.cooling_air_speed_ms(p, 6000.0, rho=pp.air_density(temp_c=25.0)))
    assert v != 99.0 and info["propeller_id"] == "tmotor_p12x4" and info["slipstream_factor"] == 0.4
    v2, _ = th._apply_air_speed_source("propeller", "tmotor_p12x4", "developed_wake", rpm=6000.0,
                                       ambient_temp=25.0, cooling_mode="air", air_speed_mps=0.0)
    assert v2 == pytest.approx(v / 0.4)
    v3, _ = th._apply_air_speed_source("propeller", "tmotor_p12x4", "", rpm=9000.0, ambient_temp=25.0,
                                       cooling_mode="air", air_speed_mps=0.0)
    assert v3 > v


@pytest.mark.parametrize("kw", [
    dict(air_speed_source="propeller", propeller_id="tmotor_p12x4", cooling_mode="liquid"),
    dict(air_speed_source="propeller", propeller_id="", cooling_mode="air"),
    dict(air_speed_source="propeller", propeller_id="nope", cooling_mode="air"),
    dict(air_speed_source="propeller", propeller_id="tmotor_ms1101", cooling_mode="air"),
    dict(air_speed_source="fan", propeller_id="tmotor_p12x4", cooling_mode="air"),
    dict(air_speed_source="propeller", propeller_id="tmotor_p12x4", cooling_mode="air", propeller_position="x"),
])
def test_thermal_propeller_source_refuses_bad_requests(kw):
    from fastapi import HTTPException
    from motor_ai_sim.routes import thermal as th
    with pytest.raises(HTTPException) as e:
        th._apply_air_speed_source(kw["air_speed_source"], kw["propeller_id"], kw.get("propeller_position", ""),
                                   rpm=6000.0, ambient_temp=25.0, cooling_mode=kw["cooling_mode"], air_speed_mps=0.0)
    assert e.value.status_code == 422
    assert e.value.detail["invalid_parameters"]


def test_solve_thermal_field_signature_defaults_keep_old_calls_valid():
    import inspect
    from motor_ai_sim.routes.thermal import solve_thermal_field
    prm = inspect.signature(solve_thermal_field).parameters
    assert prm["air_speed_source"].default == "manual"
    assert prm["propeller_id"].default == "" and prm["propeller_position"].default == ""
    assert prm["air_speed_mps"].default == 0.0 and prm["cooling_mode"].default == "manual"


def test_air_mode_film_from_propeller_speed_matches_outer_air():
    """The speed handed to the existing air mode produces the existing film — no
    second correlation anywhere."""
    from motor_ai_sim.simulation import cooling_models as cm
    p = pp.get_propeller("tmotor_p12x4")
    v = pp.cooling_air_speed_ms(p, 6000.0)
    rep = cm.outer_air(air_speed_mps=v, t_ambient_c=25.0, d_housing_m=0.040)
    assert rep["air_speed_mps"] == pytest.approx(v, abs=1e-3)
    assert rep["h_conv"] > cm.NATURAL_CONVECTION_H


# ---------------------------------------------------------------------------
# 7. per-die cooling options
# ---------------------------------------------------------------------------

def test_repo_cooling_options_for_the_d40_family():
    from motor_ai_sim.cooling_options import cooling_options
    cat = pp.load_catalog()
    for cfg in (None, "L12", "L20"):
        o = cooling_options("CIANO14 40 new", cfg)
        assert o["restricted"] and o["cooling_options"] == ["propeller_air"]
        assert o["propellers"] and all(i in cat and cat[i].selectable for i in o["propellers"])
        assert {round(cat[i].diameter_nominal_in) for i in o["propellers"]} == {10, 11, 12, 13}
    # nothing else is restricted
    o = cooling_options("CIANO14 40_12")
    assert o["restricted"] is False and o["cooling_options"] is None and o["propellers"] is None


def test_cooling_options_config_override(tmp_path, monkeypatch):
    from motor_ai_sim import cooling_options as co
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"D": {
        "cooling_options": ["propeller_air"], "propellers": ["a", "b"],
        "configs": {"L20": {"propellers": ["c"]}}}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    assert co.cooling_options("D", "L12")["propellers"] == ["a", "b"]
    assert co.cooling_options("D", "L20")["propellers"] == ["c"]
    assert co.cooling_options("D", "L20")["cooling_options"] == ["propeller_air"]
    assert co.cooling_options("other")["restricted"] is False
    monkeypatch.setattr(co, "_FILE", tmp_path / "missing.yaml")
    assert co.cooling_options("D")["restricted"] is False


# ---------------------------------------------------------------------------
# 8. the server layer wins per id
# ---------------------------------------------------------------------------

def test_catalog_directory_override_and_bad_file_is_skipped(tmp_path, monkeypatch):
    p = _synthetic(tmp_path, name="mine")
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "mine.yaml").write_text(yaml.safe_dump(p.raw), encoding="utf-8")
    (tmp_path / "v" / "broken.yaml").write_text("schema: nope\n", encoding="utf-8")
    monkeypatch.setattr(pp, "_DIR", tmp_path)
    cat = pp.load_catalog()
    assert set(cat) == {"mine"} and cat["mine"].selectable


# ---------------------------------------------------------------------------
# 9. the routes
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    return TestClient(app)


def test_routes_are_not_gated():
    from motor_ai_sim.auth import required_role
    for path in ("/api/propellers", "/api/propellers/cooling-options", "/api/propellers/tmotor_p12x4",
                 "/api/propellers/tmotor_p12x4/point"):
        assert required_role("GET", path) is None, path


def test_list_route(client):
    r = client.get("/api/propellers")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == len(j["propellers"]) >= 12
    by = {p["id"]: p for p in j["propellers"]}
    assert by["tmotor_p12x4"]["data_quality"] == "torque_measured" and by["tmotor_p12x4"]["selectable"]
    assert by["tmotor_cf11x3_7"]["power_data"] == "estimated"
    assert by["tmotor_ms1101"]["selectable"] is False and by["tmotor_ms1101"]["power_data"] == "none"
    assert [p["diameter_in"] for p in j["propellers"]] == sorted(p["diameter_in"] for p in j["propellers"])
    assert j["slipstream"]["default_position"] == "behind_hub"
    only = client.get("/api/propellers", params={"selectable_only": True}).json()
    assert 0 < only["count"] < j["count"] and all(p["selectable"] for p in only["propellers"])


def test_detail_route_has_data_fit_and_curves(client):
    j = client.get("/api/propellers/tmotor_fpv_13x10").json()
    assert j["geometry"]["blades"] == 3 and j["hub"]["bore_mm"] == 6.0
    assert j["fit"]["ct"]["c_ref"] == pytest.approx(0.164, abs=0.005)
    assert j["fit"]["cp"]["estimated"] is False
    assert len(j["performance"]["tables"][0]["rows"]) == 10
    c = j["curves"]
    assert len(c["rpm"]) == len(c["thrust_N"]) == len(c["torque_Nm"]) == len(c["air_speed_ms"])
    assert all(b > a for a, b in zip(c["thrust_N"], c["thrust_N"][1:]))
    ref = client.get("/api/propellers/tmotor_ms1302").json()
    assert ref["curves"] is None and ref["fit"]["ct"] is None
    assert client.get("/api/propellers/nope").status_code == 404


def test_point_route(client):
    p = pp.get_propeller("tmotor_p12x4")
    r = client.get("/api/propellers/tmotor_p12x4/point", params={"rpm": 6000, "housing_d_mm": 40})
    assert r.status_code == 200
    j = r.json()
    assert j["thrust_N"] == pytest.approx(pp.thrust_N(p, 6000))
    assert j["torque_Nm"] == pytest.approx(pp.torque_Nm(p, 6000))
    assert j["air_speed_ms"] == pytest.approx(0.4 * j["wake_velocity_ms"])
    assert j["film"]["h_conv_W_m2K"] > 7.0 and j["film"]["housing_d_mm"] == 40.0
    hot = client.get("/api/propellers/tmotor_p12x4/point", params={"rpm": 6000, "temp_c": 40, "altitude_m": 2000}).json()
    assert hot["rho_kg_m3"] < j["rho_kg_m3"] and hot["shaft_power_W"] < j["shaft_power_W"]
    assert "film" not in hot
    wake = client.get("/api/propellers/tmotor_p12x4/point", params={"rpm": 6000, "position": "developed_wake"}).json()
    assert wake["air_speed_ms"] == pytest.approx(wake["wake_velocity_ms"])
    assert client.get("/api/propellers/tmotor_ms1101/point", params={"rpm": 6000}).status_code == 422
    assert client.get("/api/propellers/tmotor_p12x4/point", params={"rpm": 6000, "position": "x"}).status_code == 422
    assert client.get("/api/propellers/tmotor_p12x4/point", params={"rpm": 6000, "altitude_m": 99999}).status_code == 422
    assert client.get("/api/propellers/nope/point", params={"rpm": 6000}).status_code == 404


def test_cooling_options_route(client):
    j = client.get("/api/propellers/cooling-options", params={"die": "CIANO14 40 new", "config": "L12"}).json()
    assert j["restricted"] and j["cooling_options"] == ["propeller_air"]
    assert j["unknown_propellers"] == []
    assert {d["id"] for d in j["propeller_details"]} == set(j["propellers"])
    free = client.get("/api/propellers/cooling-options", params={"die": "CILN28"}).json()
    assert free["restricted"] is False and free["propeller_details"] == []


def test_series_route_is_the_point_route_on_a_grid(client):
    """Configure's one request per (propeller, ambient, housing): every sample equals what
    /point says at that rpm — the browser interpolates, it never re-derives the physics."""
    p = pp.get_propeller("tmotor_p12x4")
    s = client.get("/api/propellers/tmotor_p12x4/series",
                   params={"rpm_max": 8000, "n": 41, "temp_c": 30, "housing_d_mm": 40}).json()
    assert len(s["rpm"]) == 41 and s["rpm"][0] == 0 and s["rpm"][-1] == pytest.approx(8000)
    for i in (0, 7, 20, 40):
        pt = client.get("/api/propellers/tmotor_p12x4/point",
                        params={"rpm": s["rpm"][i], "temp_c": 30, "housing_d_mm": 40}).json()
        assert s["torque_Nm"][i] == pytest.approx(pt["torque_Nm"], rel=1e-9, abs=1e-12)
        assert s["thrust_N"][i] == pytest.approx(pt["thrust_N"], rel=1e-9, abs=1e-12)
        assert s["air_speed_ms"][i] == pytest.approx(pt["air_speed_ms"], rel=1e-9, abs=1e-12)
        assert s["h_W_m2K"][i] == pytest.approx(pt["film"]["h_conv_W_m2K"], rel=1e-9)
    # the tested range is reported and samples outside it are flagged (the UI says "beyond tested rpm")
    lo, hi = s["rpm_range_tested"]
    assert [bool(e) for e in s["extrapolated"]] == [bool(r > 0 and (r < lo or r > hi)) for r in s["rpm"]]
    assert s["torque_Nm"] == sorted(s["torque_Nm"])             # monotone: the browser inverts it
    assert s["propeller_id"] == "tmotor_p12x4" and s["t_ambient_c"] == 30.0
    # default top = 1.5 x the tested maximum; no housing = no film
    d = client.get("/api/propellers/tmotor_p12x4/series").json()
    assert d["rpm"][-1] == pytest.approx(1.5 * p.rpm_range[1]) and "h_W_m2K" not in d
    # a geometry-only prop refuses, an unknown one is not found, a bad grid is a 422
    assert client.get("/api/propellers/tmotor_ms1101/series").status_code == 422
    assert client.get("/api/propellers/nope/series").status_code == 404
    assert client.get("/api/propellers/tmotor_p12x4/series", params={"n": 2}).status_code == 422


# ---------------------------------------------------------------------------
# 10. default propeller per configuration (owner 2026-10-05: L12 -> FPV 10x5, L20 -> P13x4.4)
# ---------------------------------------------------------------------------

def test_repo_defaults_are_the_owners_and_are_allowed_and_computable():
    from motor_ai_sim.cooling_options import cooling_options
    cat = pp.load_catalog()
    assert cooling_options("CIANO14 40 new", "L12")["default_propeller"] == "tmotor_fpv_10x5"
    assert cooling_options("CIANO14 40 new", "L20")["default_propeller"] == "tmotor_p13x4_4"
    o = cooling_options("CIANO14 40 new", "L12")
    assert o["defaults"] == {"L12": "tmotor_fpv_10x5", "L20": "tmotor_p13x4_4"} and o["bad_defaults"] == {}
    for pid in o["defaults"].values():
        assert pid in o["propellers"] and cat[pid].selectable
    assert cat["tmotor_fpv_10x5"].diameter_nominal_in == 10 and cat["tmotor_fpv_10x5"].blades == 3
    # no configuration asked: no single default (the map is still there); an unrestricted die has none
    assert cooling_options("CIANO14 40 new")["default_propeller"] is None
    free = cooling_options("CILN28", "G2-L40")
    assert free["default_propeller"] is None and free["defaults"] == {}


def test_default_must_be_allowed_for_its_own_configuration(tmp_path, monkeypatch):
    from motor_ai_sim import cooling_options as co
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"D": {
        "cooling_options": ["propeller_air"], "propellers": ["a", "b"],
        "defaults": {"L12": "a", "L20": "a", "L30": "zzz"},
        "configs": {"L20": {"propellers": ["c"]}}}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    o = co.cooling_options("D", "L12")
    assert o["default_propeller"] == "a"
    # L20 may only use "c": its default "a" is withheld and named; a typo is named too
    assert o["defaults"] == {"L12": "a"}
    assert o["bad_defaults"] == {"L20": "a", "L30": "zzz"}
    assert co.cooling_options("D", "L20")["default_propeller"] is None
    assert co.cooling_options("D", "L30")["default_propeller"] is None


def test_cooling_options_route_serves_default_propeller(client, tmp_path, monkeypatch):
    j = client.get("/api/propellers/cooling-options", params={"die": "CIANO14 40 new", "config": "L12"}).json()
    assert j["default_propeller"] == "tmotor_fpv_10x5" and j["bad_defaults"] == {}
    assert j["default_propeller"] in {d["id"] for d in j["propeller_details"]}
    j20 = client.get("/api/propellers/cooling-options", params={"die": "CIANO14 40 new", "config": "L20"}).json()
    assert j20["default_propeller"] == "tmotor_p13x4_4"
    assert j20["defaults"] == {"L12": "tmotor_fpv_10x5", "L20": "tmotor_p13x4_4"}
    nocfg = client.get("/api/propellers/cooling-options", params={"die": "CIANO14 40 new"}).json()
    assert nocfg["default_propeller"] is None
    free = client.get("/api/propellers/cooling-options", params={"die": "CILN28", "config": "G2-L40"}).json()
    assert free["default_propeller"] is None
    # a default the catalogue cannot compute (geometry only) is withheld by the route
    from motor_ai_sim import cooling_options as co
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"D": {
        "cooling_options": ["propeller_air"], "propellers": ["tmotor_ms1101", "tmotor_p12x4"],
        "defaults": {"L1": "tmotor_ms1101", "L2": "tmotor_p12x4"}}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    bad = client.get("/api/propellers/cooling-options", params={"die": "D", "config": "L1"}).json()
    assert bad["default_propeller"] is None and bad["bad_defaults"] == {"L1": "tmotor_ms1101"}
    assert bad["defaults"] == {"L2": "tmotor_p12x4"}
    good = client.get("/api/propellers/cooling-options", params={"die": "D", "config": "L2"}).json()
    assert good["default_propeller"] == "tmotor_p12x4"


def test_configure_context_carries_the_defaults():
    from motor_ai_sim import configure_limits as cl
    c = cl.context({"name": "CIANO14 40 new"}, {"die": "CIANO14 40 new", "name": "L20"})
    assert c["cooling"]["default_propeller"] == "tmotor_p13x4_4"
    assert c["cooling"]["defaults"]["L12"] == "tmotor_fpv_10x5"
