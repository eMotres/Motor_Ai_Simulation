"""Configure's temperatures and propeller current ARE the server's S1 model (owner 2026-10-19:
"one model only, the server is the truth").

* ``propeller_s1.thermal_at`` at a converged S1 point (its speed, current and film) returns that
  point's temperatures; ``propeller_s1.load_point`` at an S1 speed (its propeller torque and film)
  returns that row's current and temperatures wherever the pack's voltage re-check did not raise
  the current (the page has no psi-map);
* ``web/src/lib/s1Thermal.ts`` - what the page evaluates for every knob position - gives the same
  current (+-0.05 A) and temperatures (+-1 K) as the server, at the S1 rows and on a grid, Sine and
  a PWM drive variant;
* ``configure_thermal_model`` carries what the browser needs and not the voltage / demag side.

The synthetic model always runs; the closed machines (40 L12, 50 L15, 85 L15) run when the private
data repository holds their S1 models (``MOTOR_AI_SIM_PRIVATE_DATA``).  Needs Node >= 22.18 for the
TS side (skipped without it).  Closed-form, no FEM.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from tests.test_propeller_s1 import _model, _prop

REPO = Path(__file__).resolve().parents[1]
TS = REPO / "web" / "src" / "lib" / "s1Thermal.ts"
CLOSED = [("CIANO14 40 new", "L12", "motres_bench_hq1050_3"),
          ("CIANO14 50 edited", "L15", "tmotor_fpv_13x10"),
          ("CIANO28 85 20SW1200", "L15", "tmotor_g32x11")]
CAL = {"housing_film_factor": 0.4, "winding_air_area_m2": 0.002, "rotor_air_area_m2": 0.001}


def _node() -> str | None:
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip().lstrip("v")
        major, minor = (int(x) for x in v.split(".")[:2])
    except Exception:                                           # noqa: BLE001
        return None
    return exe if (major, minor) >= (22, 18) else None


def _ts_eval(tmp_path: Path, model: dict, points: list) -> list:
    """Run s1Thermal.ts (the page's code) on ``points``: ``q`` given = s1LoadPoint, else s1ThermalAt."""
    node = _node()
    if node is None:
        pytest.skip("needs Node >= 22.18 (type stripping) for the TS side")
    (tmp_path / "in.json").write_text(json.dumps({"model": model, "points": points}), encoding="utf-8")
    script = tmp_path / "eval.mjs"
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        f"const T = await import({json.dumps(TS.as_uri())});\n"
        f"const j = JSON.parse(readFileSync({json.dumps(str(tmp_path / 'in.json'))}, 'utf8'));\n"
        "const out = j.points.map((p) => (p.q != null\n"
        "  ? T.s1LoadPoint(j.model, p.rpm, p.q, p.h, p.ambient, p.drive)\n"
        "  : T.s1ThermalAt(j.model, p.rpm, p.I, p.h, p.ambient, p.drive)));\n"
        "process.stdout.write(JSON.stringify(out));\n", encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _points(m: dict, prop: pp.Propeller, drives: list) -> list:
    """Server S1 rows (converged) for every drive - as a given-current point AND as a load point - and a grid."""
    pts = []
    for d in drives:
        r = s1.s1_rating(m, prop, ambient_c=25.0, drive=d, n_points=21)
        for row in r["rows"]:
            if not (row.get("converged") and row.get("T_winding_C") is not None):
                continue
            drv = None if d == "sine" else d
            srv = [row["T_winding_C"], row["T_magnet_C"], row["T_housing_C"]]
            pts.append({"rpm": row["rpm"], "I": row["current_A"], "h": row["h_W_m2K"], "ambient": 25.0, "drive": drv,
                        "server": srv})
            if abs((row.get("current_grid_A") or 0.0) - row["current_A"]) < 1e-9:     # no voltage re-check raise
                pts.append({"rpm": row["rpm"], "q": row["torque_prop_Nm"], "h": row["h_W_m2K"], "ambient": 25.0,
                            "drive": drv, "server": srv, "server_I": row["current_A"]})
    g = m["grid"]
    for rpm in (g["rpm"][0] * 0.7, 0.5 * (g["rpm"][0] + g["rpm"][-1]), g["rpm"][-1]):
        for I in (g["I_A"][0] * 0.5, 0.5 * (g["I_A"][0] + g["I_A"][-1])):
            for h in (20.0, 80.0):
                for d in drives:
                    pts.append({"rpm": rpm, "I": I, "h": h, "ambient": 25.0, "drive": None if d == "sine" else d})
    return pts


KEYS = ("T_winding_C", "T_magnet_C", "T_housing_C")


def _check(tmp_path: Path, m: dict, pts: list) -> int:
    tm = s1.configure_thermal_model(m)
    ts = _ts_eval(tmp_path, tm, pts)
    n = 0
    for p, t in zip(pts, ts):
        drv = tm["drives"].get(p["drive"]) if p["drive"] else None
        if p.get("q") is not None:
            py = s1.load_point(m, p["rpm"], p["q"], p["h"], p["ambient"], drv)
            assert py and py["state"] == "converged", p
            assert t is not None, p
            assert abs(t["current_A"] - py["current_A"]) <= 0.05, (p, t["current_A"], py["current_A"])
            assert abs(t["current_A"] - p["server_I"]) <= 0.05, (p, t["current_A"], p["server_I"])
        else:
            py = s1.thermal_at(m, p["rpm"], p["I"], p["h"], p["ambient"], drv)
            if py is None or py.get("state") != "converged":
                assert t is None, (p, t)
                continue
            assert t is not None, p
        for k, key in enumerate(KEYS):
            assert abs(t[key] - py[key]) <= 1.0, (p, key, t[key], py[key])
            if "server" in p:                                   # the S1 row itself
                assert abs(t[key] - p["server"][k]) <= 1.0, (p, key, t[key], p["server"][k])
        n += 1
    return n


def test_thermal_at_and_load_point_are_the_s1_row(tmp_path):
    m = _model(thermal_calibration=CAL)
    r = s1.s1_rating(m, _prop(tmp_path), ambient_c=25.0, n_points=15, rpm_min=1000.0, rpm_max=12000.0)
    seen = 0
    for row in r["rows"]:
        if not row.get("converged"):
            continue
        py = s1.thermal_at(m, row["rpm"], row["current_A"], row["h_W_m2K"], 25.0)
        lp = s1.load_point(m, row["rpm"], row["torque_prop_Nm"], row["h_W_m2K"], 25.0)
        for k in KEYS:
            assert py[k] == pytest.approx(row[k], abs=0.05)
            assert lp[k] == pytest.approx(row[k], abs=0.05)
        assert lp["current_A"] == pytest.approx(row["current_A"], abs=0.01)
        seen += 1
    assert seen >= 5


def test_the_model_for_the_page_carries_the_thermal_and_load_side_only():
    m = _model(thermal_calibration={"housing_film_factor": 0.5, "slipstream_factor": 0.6})
    tm = s1.configure_thermal_model(m)
    assert tm["schema"] == "configure-thermal-1" and tm["calibrated"] is True
    assert tm["slipstream_factor"] == 0.6 and tm["calibration"]["housing_film_factor"] == 0.5
    assert set(tm) >= {"grid", "mech_W", "drives", "thermal_geom", "limits", "R_phase_ohm", "R_temp_C",
                       "torque_factor", "flux_temp_coeff_per_K", "magnet_ref_temp_C", "I_cap_A"}
    assert "psi_map" not in tm and "demag" not in tm and "voltage" not in tm     # no voltage / demag side
    json.dumps(tm)                                              # it travels as JSON


def test_page_equals_server_synthetic(tmp_path):
    m = _model(thermal_calibration=CAL)
    assert _check(tmp_path, m, _points(m, _prop(tmp_path), ["sine"])) >= 10


@pytest.mark.parametrize("die,cfg,prop_id", CLOSED)
def test_page_equals_server_closed_machines(tmp_path, die, cfg, prop_id):
    try:
        m = s1.load_model(die, cfg)
    except s1.S1ModelNotFound:
        pytest.skip(f"no S1 model for {die} {cfg} here (private data)")
    try:
        prop = pp.get_propeller(prop_id)
    except pp.PropellerNotFound:
        pytest.skip(f"no {prop_id} in this propeller catalogue")
    drives = ["sine"] + list((m.get("drives") or {}))[:1]       # Sine + the first PWM variant
    assert _check(tmp_path, m, _points(m, prop, drives)) >= 5
