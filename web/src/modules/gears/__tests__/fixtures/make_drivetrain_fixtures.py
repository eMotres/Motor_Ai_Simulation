"""Regenerates the drivetrain fixtures (POST /api/gears/drivetrain, schema gear_drivetrain-1) by calling the route.

The route is routes/gears_drivetrain.py (PR #907 and its follow-up); the motor side is the same kind of synthetic saved-points source its
tests use (read_saved_motor replaced), so nothing starts a solver and no motor data is read.
Run from a checkout that has the route on PYTHONPATH:  python make_drivetrain_fixtures.py <out_dir>
"""
import json
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, gear_store
from motor_ai_sim.routes import gears_drivetrain as gd
from motor_ai_sim.routes.gears import build_template, _humanoid_default

OUT = sys.argv[1]
auth.AUTH_ENFORCE = False
auth._ADMIN_EMAILS = set()
app = FastAPI()
app.include_router(gd.router)
client = TestClient(app)
URL = "/api/gears/drivetrain"


def point(name, torque, rpm, eff=0.85):
    return gd.saved_point({"name": name, "rpm": rpm, "result": {"T_em_avg_Nm": torque, "efficiency_pct": eff * 100.0, "mass_kg": 0.5}})


def marked(name, torque, rpm, eff=0.85):
    return gd.saved_point({"name": name, "rpm": rpm, "result": {"T_em_avg_Nm": torque * 1.1, "efficiency_pct": eff * 100.0},
                           "summary": {"T_shaft_Nm": torque, "shaft_convention": "fe_on_shaft_k3d_v1"}})


def motor(rated, peak=None, mass=0.5):
    duties, classes = {"rated": rated}, {"rated": {"duty_class": "continuous"}}
    if peak is not None:
        duties["pull"], classes["pull"] = peak, {"duty_class": "short_time"}
    return {"die": "D1", "configuration": "C1", "duties": duties, "classes": classes, "rated": "rated",
            "mass_kg": mass, "mass_source": "synthetic full mass", "mass_partial": False}


current = {}
gd.read_saved_motor = lambda die, cfg, authorization: current["motor"]


def dump(name, obj):
    with open(OUT + "/" + name + ".json", "w") as fh:
        json.dump(obj, fh, sort_keys=True, separators=(",", ":"), allow_nan=False)


def call(name, body, motor_src, status=200):
    current["motor"] = motor_src
    r = client.post(URL, json=body)
    assert r.status_code == status, r.text
    dump(name + ".request", body)
    dump(name + ".response", r.json())
    return r.json()


def body(inline, rows, **mt):
    return {"gearbox": {"request": inline}, "motor": {"die": "D1", "configuration": "C1", **mt}, "load": rows}


inline = build_template({})
rows = [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}, {"name": "peak", "torque_out_Nm": 5.0, "speed_out_rpm": 247.0}]
# legacy field torque, covered, no chain number (the required point differs from the saved one)
ok = call("drivetrain_ok", body(inline, rows, duties={"peak": "pull"}), motor(point("rated", 1.0, 3500.0), point("pull", 0.6, 3500.0, 0.8)))
# the saved point is exactly the required one and carries the shaft marker: chain efficiency = motor x gearbox, torque_basis shaft
g = ok["rows"][0]["gearbox"]
call("drivetrain_match", body(inline, rows[:1]), motor(marked("rated", g["torque_in_Nm"], g["speed_in_rpm"])))
# not covered, and a mode with no saved duty
call("drivetrain_missing", body(inline, [{"name": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247.0}, {"name": "peak", "torque_out_Nm": 40.0, "speed_out_rpm": 247.0}]),
     motor(point("rated", 0.5, 3500.0)))
# a gearbox the core marks failed (bearing records fail at these loads): the gearbox_failed row
st = _humanoid_default()["stages"][:1]
st[0]["bearings"] = {"input_bearing": "618/8-2Z", "output_bearing": "618/8-2Z"}
call("drivetrain_failed", body(build_template({"stages": st}), [{"name": "continuous", "torque_out_Nm": 2.0, "speed_out_rpm": 800.0}]),
     motor(point("rated", 1.0, 3500.0)))
# a marked record without a recognised shaft key: the motor point is missing, never the field torque
call("drivetrain_shaftkey", body(inline, rows[:1]), motor(gd.saved_point({"name": "rated", "rpm": 3500.0, "result": {"T_em_avg_Nm": 1.0, "efficiency_pct": 85.0},
                                                                         "summary": {"shaft_convention": "fe_on_shaft_k3d_v1", "T_other_Nm": 1.0}})))
# refused
bad = body(inline, [{"name": "continuous", "torque_out_Nm": 0, "speed_out_rpm": 247.0}])
call("drivetrain_422", bad, motor(point("rated", 1.0, 3500.0)), status=422)
