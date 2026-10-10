"""MTPA / field-weakening grid study from the gamma sweeps (NOT committed).
usage: python mtpa.py RES.jsonl I0
Prints gamma_MTPA per current (3-point parabola around the best sampled gamma),
and the interpolation error at off-grid check points for (I, gamma) vs (id, iq)
interpolation of T, psi_d, psi_q."""
import json
import math
import sys

import numpy as np
from scipy.interpolate import griddata, RegularGridInterpolator, CloughTocher2DInterpolator

I0 = float(sys.argv[2])
pts = []
for ln in open(sys.argv[1], encoding="utf-8"):
    d = json.loads(ln)
    if not d.get("ok") or not d["meta"].get("mtpa"):
        continue
    r = d["r"]
    pts.append(dict(fI=d["meta"]["fI"], g=d["meta"]["gamma"], T=abs(r["T_avg_Nm"]),
                    pd=r["psi_d_Wb"], pq=r["psi_q_Wb"], id=r["i_d_A"], iq=r["i_q_A"],
                    rip=r.get("s_T_ripple_pct"), check=bool(d["meta"].get("check")),
                    wall=d["wall_s"]))
print("points", len(pts), "mean wall", round(np.mean([p["wall"] for p in pts]), 1), "s")
out = {}
for fI in sorted({p["fI"] for p in pts if not p["check"]}):
    row = sorted([p for p in pts if p["fI"] == fI and not p["check"]], key=lambda p: p["g"])
    gs = np.array([p["g"] for p in row]); Ts = np.array([p["T"] for p in row])
    k = int(np.argmax(Ts[gs <= 35.0 + 1e-9]))
    k = min(max(k, 1), len(gs[gs <= 35.0 + 1e-9]) - 2)
    a, b, c = np.polyfit(gs[k - 1:k + 2], Ts[k - 1:k + 2], 2)
    gm = -b / (2 * a)
    Tm = np.polyval([a, b, c], gm)
    out[fI] = (gm, Tm)
    print(f"I={fI:.2f}xI0  gamma_MTPA={gm:6.2f} deg  T_MTPA={Tm:.4g}  "
          f"T(g)={[round(t, 4) for t in Ts]} at g={list(gs)}  "
          f"T@g0(-5..35 sampled)")

# interpolation test: grid = non-check points; targets = check points
grid = [p for p in pts if not p["check"]]
chk = [p for p in pts if p["check"]]
for name, X, Xc in (
        ("(I,gamma)", np.array([[p["fI"], p["g"]] for p in grid]),
         np.array([[p["fI"], p["g"]] for p in chk])),
        ("(id,iq)", np.array([[p["id"], p["iq"]] for p in grid]),
         np.array([[p["id"], p["iq"]] for p in chk]))):
    sc = X.std(axis=0)
    for q in ("T", "pd", "pq"):
        y = np.array([p[q] for p in grid])
        f = CloughTocher2DInterpolator(X / sc, y)
        yc = f(Xc / sc)
        yl = griddata(X / sc, y, Xc / sc, method="linear")
        errs = []
        for p, v, vl in zip(chk, yc, yl):
            ref = p[q]
            scale = max(abs(ref), 1e-12) if q == "T" else max(math.hypot(p["pd"], p["pq"]), 1e-12)
            errs.append((p["fI"], p["g"], round(100 * (v - ref) / scale, 2),
                         round(100 * (vl - ref) / scale, 2)))
        print(name, q, "err% [cubic, linear] at (fI,g):", errs)
