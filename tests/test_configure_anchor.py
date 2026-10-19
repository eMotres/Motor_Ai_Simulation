"""Configure's lumped network ANCHORED to a duty's coupled FEM record (owner 2026-10-19).

* ``anchor_from_record`` reads the record's temperatures, ambient and losses and fits the three paths;
  fed the record's own losses at the record's ambient the network returns the record (+-2 K, here
  exactly);
* a record that is not a steady answer (runaway, at a limit) or lacks a number anchors nothing;
* ``web/src/lib/thermalAnchor.ts`` is the same arithmetic (+-0.01 K, runs the TS under Node);
* ``configure_load`` carries the anchor of the target duty.
Synthetic records only (no machine data), no FEM.
"""
from __future__ import annotations

import json
import random

import pytest

from motor_ai_sim import configure_anchor as ca
from tests.test_configure_thermal_parity import _node

REC = {
    "coupled": {"mode": "steady", "converged": True, "runaway": False, "drive": "sine",
                "coil_temp_c": 75.0, "magnet_temp_c": 69.0, "magnet_temp_max_c": 70.0,
                "em": {"P_stranded_W": 16.0, "P_loss_total_W": 20.0, "P_mag_W": 0.6, "P_shaft_W": 0.9, "P_solid_W": 1.5},
                "time_to_limit": {"at_point_c": {"winding": 74.0, "magnet": 70.5, "housing": 69.5}},
                "computed_at": "2026-10-19T10:36:53"},
    "thermal": {"point": {"rpm": 1000.0, "I_phase_rms": 6.0, "cooling_mode": "robotics", "ambient_temp": 40.0},
                "P_loss_total_W": 19.5, "P_cu_W": 16.0,
                "components": {"winding": {"max": 74.0}, "magnet": {"max": 70.5}, "stator": {"avg": 71.5}},
                "cooling": {"outer": {"mode": "robotics", "air_speed_mps": 0.0, "t_sink_c": 40.0, "t_wall_c": 69.5},
                            "heat_budget": {"losses_W": 20.2, "rotor_W": 1.6}}},
}


def test_the_anchor_reproduces_its_record():
    a = ca.anchor_from_record("rated", REC)
    assert a["duty"] == "rated" and a["cooling_mode"] == "robotics" and a["ambient_C"] == 40.0
    assert a["point"] == {"rpm": 1000.0, "I_A": 6.0}
    r = a["record"]
    t = ca.anchored_temperatures(a, r["P_cu_W"], r["P_rotor_W"], r["P_total_W"], 7.0, 7.0, a["ambient_C"])
    assert t["winding"] == pytest.approx(74.0, abs=1e-9)
    assert t["magnet"] == pytest.approx(70.5, abs=1e-9)
    assert t["housing"] == pytest.approx(69.5, abs=1e-9)
    # the record/page ratios make the stored point the record whatever the page's own losses there
    k = {"k_cu": 16.0 / 14.0, "k_rotor": 1.6 / 0.5, "k_total": 20.2 / 18.0}
    t2 = ca.anchored_temperatures(a, 14.0, 0.5, 18.0, 7.0, 7.0, 40.0, **k)
    assert abs(t2["winding"] - 74.0) <= 2.0 and abs(t2["magnet"] - 70.5) <= 2.0 and abs(t2["housing"] - 69.5) <= 2.0
    # 25 C air, more current: same conductances, the change carried by the losses
    t3 = ca.anchored_temperatures(a, 30.0, 0.8, 34.0, 7.0, 7.0, 25.0)
    assert t3["winding"] > t3["housing"] > 25.0


def test_what_anchors_nothing():
    assert ca.anchor_from_record("rated", None) is None
    assert ca.anchor_from_record("rated", {}) is None
    hot = json.loads(json.dumps(REC)); hot["coupled"]["runaway"] = True
    assert ca.anchor_from_record("rated", hot) is None
    lim = json.loads(json.dumps(REC)); lim["coupled"]["mode"] = "limited"
    assert ca.anchor_from_record("rated", lim) is None
    bare = json.loads(json.dumps(REC)); bare["thermal"]["point"].pop("ambient_temp")
    bare["thermal"]["cooling"]["outer"].pop("t_sink_c")
    assert ca.anchor_from_record("rated", bare) is None


def test_page_equals_server(tmp_path):
    node = _node()
    if node is None:
        pytest.skip("needs Node >= 22.18 (type stripping) for the TS side")
    import subprocess
    from pathlib import Path
    a = ca.anchor_from_record("rated", REC)
    rnd = random.Random(7)
    pts = [{"cu": rnd.uniform(1, 60), "rot": rnd.uniform(0, 5), "tot": rnd.uniform(5, 90), "h": rnd.uniform(5, 80),
            "href": rnd.choice([7.0, 30.0]), "amb": rnd.uniform(-10, 45),
            "k": {"cu": rnd.uniform(0.8, 1.2), "rotor": rnd.uniform(0.5, 3), "total": rnd.uniform(0.8, 1.2)},
            "extra": rnd.choice([0.0, 3.5])} for _ in range(40)]
    ts_mod = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "thermalAnchor.ts"
    (tmp_path / "in.json").write_text(json.dumps({"a": a, "pts": pts}), encoding="utf-8")
    script = tmp_path / "eval.mjs"
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        f"const T = await import({json.dumps(ts_mod.as_uri())});\n"
        f"const j = JSON.parse(readFileSync({json.dumps(str(tmp_path / 'in.json'))}, 'utf8'));\n"
        "process.stdout.write(JSON.stringify(j.pts.map((p) => T.anchoredTemperatures(j.a, p.cu, p.rot, p.tot, p.h, p.href, p.amb, p.k, p.extra))));\n",
        encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    for p, t in zip(pts, json.loads(r.stdout)):
        py = ca.anchored_temperatures(a, p["cu"], p["rot"], p["tot"], p["h"], p["href"], p["amb"],
                                      k_cu=p["k"]["cu"], k_rotor=p["k"]["rotor"], k_total=p["k"]["total"], p_extra=p["extra"])
        for k in ("winding", "magnet", "housing"):
            assert abs(t[k] - py[k]) <= 0.01, (p, k)


def test_configure_load_carries_the_anchor(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim import duty_results as dres
    from motor_ai_sim import motor_access as ma
    from motor_ai_sim.routes import catalog as cat_mod
    ctx = {"motor_id": "cat_r", "cooling": {"die": "R", "cooling_options": ["robotics"], "propellers": []},
           "presets": [{"config": "L15", "die": "R", "duty": "rated", "knobs": {}, "pwm_variants": [],
                        "drive_variant": None, "battery": None,
                        "duty_points": [{"name": "rated", "current_A": 6.0, "rpm": 1000.0}]}]}
    monkeypatch.setattr(cat_mod, "get_configure_context", lambda mid, authorization=None: json.loads(json.dumps(ctx)))
    monkeypatch.setattr(dres, "get", lambda die, cfg: {"rated": REC} if (die, cfg) == ("R", "L15") else {})
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ALL, "dies": frozenset()})
    monkeypatch.setattr(ma, "may_see_config", lambda acc, die, cfg: True)
    app = FastAPI()
    app.include_router(cat_mod.router)
    j = TestClient(app).get("/api/catalog/cat_r/configure_load", params={"config": "L15"}).json()
    assert j["duty"] == "rated" and j["thermal_anchor"]["schema"] == "configure-thermal-anchor-1"
    assert j["thermal_anchor"]["record"]["T_winding_C"] == 74.0
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ANONYMOUS, "dies": frozenset()})
    assert TestClient(app).get("/api/catalog/cat_r/configure_load", params={"config": "L15"}).json()["thermal_anchor"] is None
