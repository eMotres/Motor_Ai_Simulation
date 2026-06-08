"""Bounded FEM coordinate-descent: minimise torque ripple on the Ø200 20 kW
motor while holding rated torque (T_avg >= 95 N.m). Operates on the live
backend (PUT geometry, GET torque_sweep). The Ø200 baseline is already saved as
preset 'm200_20kw_base', so the working copy can be mutated freely; the best
result is written to opt_200_20kw_result.json and saved as preset
'm200_20kw_opt' at the end.
"""
import json
import urllib.request
import urllib.parse

API = "http://127.0.0.1:8001"
T_FLOOR = 95.0          # hold rated torque
N_ROTOR = 24            # ripple-sweep resolution (speed vs accuracy)
RESULT = "scripts/opt_200_20kw_result.json"
LOG = []


def _get(url, timeout=400):
    return json.loads(urllib.request.urlopen(url, timeout=timeout).read())


def put_geom(params):
    req = urllib.request.Request(
        API + "/api/geometry",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=180).read()


def sweep(gamma):
    url = f"{API}/api/simulation/physics/torque_sweep?gamma_deg={gamma}&n_rotor={N_ROTOR}&n_ag=360"
    d = _get(url)
    return float(d["T_avg_Nm"]), float(d["T_ripple_pct"])


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


# ── starting point (current Ø200 working copy) ──────────────────────────────
best = {
    "magnet_fill_up": 0.44, "magnet_fill_down": 0.9,
    "magnet_up_gap": 2.67, "rotor_fill_r": 2.13,
}
best_gamma = 0.0
T0, R0 = sweep(0.0)
best_T, best_R = T0, R0
log(f"baseline: T_avg={T0:.2f} N.m  ripple={R0:.2f}%")

# ── 1) MTPA gamma at the baseline geometry ──────────────────────────────────
for g in (-5.0, -10.0, -15.0, -20.0):
    T, R = sweep(g)
    log(f"  gamma={g:6.1f}  T={T:.2f}  R={R:.2f}")
    if T >= T_FLOOR and (R < best_R or (abs(R - best_R) < 0.05 and T > best_T)):
        best_R, best_T, best_gamma = R, T, g
log(f"-> best gamma={best_gamma}  T={best_T:.2f}  R={best_R:.2f}")

# ── 2) coordinate descent over geometry, each tested at best_gamma ──────────
GRID = {
    "magnet_fill_up":   [0.44, 0.50, 0.56, 0.62],
    "magnet_up_gap":    [2.0, 2.67, 3.2],
    "magnet_fill_down": [0.9, 0.95, 1.0],
    "rotor_fill_r":     [1.6, 2.13, 2.6],
}
for param, vals in GRID.items():
    local_best_v = best[param]
    for v in vals:
        if v == best[param]:
            continue
        trial = dict(best)
        trial[param] = v
        put_geom({param: v})
        T, R = sweep(best_gamma)
        log(f"  {param}={v}  T={T:.2f}  R={R:.2f}")
        if T >= T_FLOOR and R < best_R - 0.02:
            best_R, best_T, local_best_v = R, T, v
            best = trial
    # lock this param to its best before moving on
    put_geom({param: local_best_v})
    best[param] = local_best_v
    log(f"-> lock {param}={local_best_v}  (best R={best_R:.2f}, T={best_T:.2f})")

# ── apply best geometry + gamma, persist result ─────────────────────────────
put_geom(best)
req = urllib.request.Request(
    API + "/api/simulation/config",
    data=json.dumps({"phase_offset_deg": best_gamma}).encode(),
    headers={"Content-Type": "application/json"},
    method="PATCH",
)
urllib.request.urlopen(req, timeout=60).read()

result = {
    "best_geometry": best, "best_gamma": best_gamma,
    "T_avg_Nm": round(best_T, 3), "ripple_pct": round(best_R, 3),
    "baseline": {"T_avg_Nm": round(T0, 3), "ripple_pct": round(R0, 3)},
    "log": LOG,
}
with open(RESULT, "w") as f:
    json.dump(result, f, indent=2)
log(f"\nDONE. best ripple {best_R:.2f}% (was {R0:.2f}%), T_avg {best_T:.2f} N.m, gamma {best_gamma}")

# ── save optimized preset ───────────────────────────────────────────────────
payload = {
    "id": "m200_20kw_opt",
    "name": "200_45 - 20 kW (optimized)",
    "description": f"Ripple-optimized Ø200 20 kW: ripple {best_R:.2f}% (was {R0:.2f}%), "
                   f"T_avg {best_T:.1f} Nm @ 2000 rpm, gamma {best_gamma}.",
    "metrics": {"T_avg_Nm": round(best_T, 2), "ripple_pct": round(best_R, 2),
                "power_kW": 20, "diameter_mm": 200, "stack_mm": 45,
                "current_A": 168, "rpm": 2000, "gamma_deg": best_gamma},
}
req = urllib.request.Request(
    API + "/api/presets", data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
try:
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    log(f"saved preset: {r.get('id')}")
except Exception as e:
    log(f"preset save failed: {e}")
