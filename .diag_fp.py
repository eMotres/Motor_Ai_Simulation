from motor_ai_sim.routes.optimization import _config_fingerprint as F
from motor_ai_sim.config import get_config

print("fp_a:", F())
print("fp_b:", F())   # same process, twice → must be identical
cfg = get_config()
phys = {k: cfg.get(k) for k in ("geometry", "winding", "materials", "magnet", "rotor", "stator")}


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, path + "." + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, path + "[" + str(i) + "]")
    elif not isinstance(o, (str, int, float, bool, type(None))):
        print("NON-NATIVE:", path, "=", type(o).__name__, repr(o)[:60])


walk(phys)
print("sim keys:", list((cfg.get("simulation") or {}).keys()))
