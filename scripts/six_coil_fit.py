"""Stage-3 optimiser of the six-coil study: fit the FEM probes, solve, propose.

    python scripts/six_coil_fit.py <workdir> [<out.json>]

A response-surface optimiser with FEW, FEM-evaluated variables â€” the per-coil
current harmonics z_h = a_hÂ·e^{jÏˆ_h} (relative to the fundamental, at EQUAL
coil rms, i.e. equal IÂ²R copper loss):

* mean torque   T(z3) = T0 + g_cÂ·c + g_sÂ·s + qÂ·|z3|Â²   (4 probes at Ïˆ = 0/90/180/270)
                -> z3* = -(g_c + j g_s)/(2q): the most torque per copper loss;
* ripple        R_k(z) = R_k0 + Î£_h (Î±_kh z_h + Î²_kh conj z_h), k = 6, 12
                (2 probes per harmonic at Ïˆ = 0/90; the 3rd's from its 4)
                -> the minimum-norm z_5, z_7, z_11, z_13 that cancel R_6 and
                R_12 (with z3* in place): least squares.

Every candidate is then RE-SOLVED by the FEM (plan written next to the fit);
nothing here is reported as a result until that confirmation exists.
"""
import cmath
import json
import math
import os
import sys

import numpy as np


def load(wd, tag):
    p = os.path.join(wd, f"{tag}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def tharm(r, k):
    T = np.asarray(r["series"]["T_em_Nm"], float)
    n = int(r["n_steps_per_period"] or len(T))
    T = T[-n:]
    return complex(2 * np.fft.rfft(T)[k] / n)


def vpk(r):
    return max(max(abs(x) for x in r["series"][k]) for k in ("V_A", "V_B", "V_C"))


def main(wd, out=None):
    r0 = load(wd, "s_pc0")
    T0 = r0["T_avg_Nm"]
    ks = (6, 12)
    R0 = {k: tharm(r0, k) for k in ks}
    # â”€â”€ 3rd harmonic: torque quadratic + ripple linear â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    a3 = 0.10
    p3 = {ps: load(wd, f"s_h3_{ps}") for ps in (0, 90, 180, 270)}
    gc = (p3[0]["T_avg_Nm"] - p3[180]["T_avg_Nm"]) / (2 * a3)
    gs = (p3[90]["T_avg_Nm"] - p3[270]["T_avg_Nm"]) / (2 * a3)
    q = (np.mean([p3[ps]["T_avg_Nm"] for ps in p3]) - T0) / a3 ** 2
    z3 = -complex(gc, gs) / (2 * q) if q < 0 else None
    fit = {"T0_Nm": T0, "h3": {"g_c": gc, "g_s": gs, "q": q,
                               "a_opt": abs(z3) if z3 else None,
                               "psi_opt_deg": math.degrees(cmath.phase(z3)) if z3 else None,
                               "dT_opt_Nm": (-(gc ** 2 + gs ** 2) / (4 * q)) if z3 else None}}
    # linear ripple model per harmonic
    lin = {}
    for h, a in ((3, a3), (5, 0.02), (7, 0.02), (11, 0.01), (13, 0.01)):
        r_0, r_90 = load(wd, f"s_h{h}_0"), load(wd, f"s_h{h}_90")
        if r_0 is None or r_90 is None:
            continue
        lin[h] = {}
        for k in ks:
            d0 = tharm(r_0, k) - R0[k]
            d90 = tharm(r_90, k) - R0[k]
            lin[h][k] = ((d0 - 1j * d90) / (2 * a), (d0 + 1j * d90) / (2 * a))
    fit["ripple_lin"] = {str(h): {str(k): [[v[0].real, v[0].imag], [v[1].real, v[1].imag]]
                                  for k, v in d.items()} for h, d in lin.items()}
    # 3rd-harmonic ripple model check against the 180/270 probes (linearity)
    if 3 in lin:
        chk = {}
        for ps in (180, 270):
            z = a3 * cmath.exp(1j * math.radians(ps))
            for k in ks:
                pred = R0[k] + lin[3][k][0] * z + lin[3][k][1] * z.conjugate()
                chk[f"{ps}_{k}"] = [abs(pred), abs(tharm(p3[ps], k))]
        fit["h3_ripple_linearity"] = chk

    def ripple_after(zs):
        return {k: R0[k] + sum(lin[h][k][0] * z + lin[h][k][1] * z.conjugate()
                               for h, z in zs.items() if h in lin) for k in ks}

    # minimum-norm cancellation of R6, R12 with z3 fixed (z3* or 0)
    def cancel(z3fix, use=(5, 7, 11, 13), targets=ks):
        base = ripple_after({3: z3fix} if z3fix else {})
        hs = [h for h in use if h in lin]
        # unknowns: c_h, s_h per harmonic (real); equations: Re/Im of R6, R12 = 0
        A = np.zeros((2 * len(targets), 2 * len(hs)))
        b = np.zeros(2 * len(targets))
        for i, k in enumerate(targets):
            b[2 * i], b[2 * i + 1] = -base[k].real, -base[k].imag
            for j, h in enumerate(hs):
                al, be = lin[h][k]
                col_c = al + be           # d/dc of (al z + be conj z)
                col_s = 1j * (al - be)    # d/ds
                A[2 * i, 2 * j], A[2 * i + 1, 2 * j] = col_c.real, col_c.imag
                A[2 * i, 2 * j + 1], A[2 * i + 1, 2 * j + 1] = col_s.real, col_s.imag
        x, *_ = np.linalg.lstsq(A, b, rcond=None)
        zs = {h: complex(x[2 * j], x[2 * j + 1]) for j, h in enumerate(hs)}
        if z3fix:
            zs[3] = z3fix
        return zs, ripple_after(zs)

    cands = {}
    if z3:
        cands["c3"] = {3: z3}
    zs, rr = cancel(None)
    cands["rip"] = zs
    # 5th/7th only (the 11th/13th sit at 13-15 kHz, above any current loop a
    # 24 kHz carrier can close): cancel R6 alone
    cands["rip57"] = cancel(None, use=(5, 7), targets=(6,))[0]
    if z3:
        zs2, rr2 = cancel(z3)
        cands["all"] = zs2
    fit["candidates"] = {
        name: {"harmonics": [[h, abs(z), math.degrees(cmath.phase(z)) % 360.0]
                             for h, z in sorted(zs.items())],
               "pred_R_Nm": {str(k): abs(v) for k, v in ripple_after(zs).items()}}
        for name, zs in cands.items()}
    fit["R0_Nm"] = {str(k): abs(v) for k, v in R0.items()}
    fit["Vpk_ref_V"] = vpk(r0)
    fit["probes"] = {}
    for fn in sorted(os.listdir(wd)):
        if fn.startswith("s_") and fn.endswith(".json") and not fn.endswith(".spec.json"):
            r = json.load(open(os.path.join(wd, fn)))
            fit["probes"][r["tag"]] = {
                "T_avg_Nm": r["T_avg_Nm"], "T_ripple_pct": r["T_ripple_pct"],
                "R6_Nm": abs(tharm(r, 6)), "R12_Nm": abs(tharm(r, 12)),
                "Vpk_V": vpk(r), "P_cu_dc_W": r["P_cu_dc_W"], "P_cu_ac_W": r["P_cu_ac_W"],
                "P_fe_W": r["P_fe_W"]}
    s = json.dumps(fit, indent=1, default=str)
    if out:
        open(out, "w").write(s)
    print(s)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
