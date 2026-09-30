"""Analyse ripple_ref.py runs: energy references vs Maxwell vs the shipped hybrid.

usage: python analyse.py RUN [RUN ...]    (RUN = path without .npz/.json)
Prints one JSON object per run.  Import ``analyse_run`` for the tables.
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np

HARM = (6, 12, 18, 24, 36)


def _load(run):
    meta = json.load(open(run + ".json"))
    z = np.load(run + ".npz")
    return meta, {k: z[k] for k in z.files}


def spectral_derivative(y, dtheta):
    """d/dtheta of a uniformly sampled PERIODIC series (full DFT, Nyquist bin
    zeroed for an even length — its derivative is not defined)."""
    n = y.size
    Y = np.fft.fft(y)
    k = np.fft.fftfreq(n, d=1.0 / n)
    if n % 2 == 0:
        Y[n // 2] = 0.0
    return np.real(np.fft.ifft(1j * k * Y)) * (2.0 * math.pi / (n * dtheta))


def harmonics(t, periods=1):
    """Amplitude (N m, one-sided) of the electrical orders in HARM for a
    uniform series spanning `periods` electrical periods."""
    n = t.size
    Y = np.fft.rfft(t) / n
    out = {}
    for h in HARM:
        b = int(round(h * periods))
        if b < Y.size and (b < n / 2):
            out[h] = float(2 * abs(Y[b]))
    return out


def metrics(t, periods=1):
    t = np.asarray(t, float)
    m = float(t.mean())
    pp = float(np.ptp(t))
    return {"mean": m, "pp": pp, "pp_pct": (100.0 * pp / abs(m) if abs(m) > 1e-12 else None),
            "harm": harmonics(t, periods)}


def terminal_work_mean(iA, iB, iC, pA, pB, pC, dth, npar):
    return float(npar * np.mean(iA * spectral_derivative(pA, dth)
                                + iB * spectral_derivative(pB, dth)
                                + iC * spectral_derivative(pC, dth)))


def space_vector_mean(iA, iB, iC, pA, pB, pC, pp, npar):
    s = 2.0 / 3.0; kc = math.sqrt(3.0) / 2.0
    pal = s * (pA - 0.5 * pB - 0.5 * pC); pbe = s * kc * (pB - pC)
    ial = s * (iA - 0.5 * iB - 0.5 * iC); ibe = s * kc * (iB - iC)
    return float(np.mean(1.5 * pp * npar * (pal * ibe - pbe * ial)))


def analyse_run(run):
    meta, z = _load(run)
    NS, L, pp, npar = meta["NS"], meta["L"], meta["pole_pairs"], meta["n_parallel"]
    R = int(meta["ring"])
    dth = math.radians(meta["spacing_deg"])          # one slip node, mech rad
    wprime = -NS * L * z["phi"]                      # machine co-energy [J]
    off = z["offset"].astype(int); cs = z["cur_scale"]
    base = (cs == 1.0)
    cen = base & (off == 0)
    order = np.argsort(z["centre"][cen])
    C = {k: v[cen][order] for k, v in z.items()}
    Wc = wprime[cen][order]
    ncen = C["centre"].size
    uniform = (ncen >= 2 and np.all(np.diff(C["centre"]) == C["centre"][1] - C["centre"][0])
               and (C["centre"][1] - C["centre"][0]) * ncen == R)
    res = {"run": run.split("/")[-1], "machine": meta["machine"], "duty": meta["duty"],
           "ring": R, "gap_layers": meta["gap_layers"], "mesh_scale": meta["mesh_scale"],
           "noload": meta["noload"], "demag": meta["demag"], "n_centres": int(ncen),
           "newton_all_ok": meta["newton_all_ok"], "res_max": meta["res_max"],
           "wall_s": meta["wall_s"], "n_rows": meta["n_rows"], "n_elem": meta.get("n_elem"),
           "n_dof": meta.get("n_dof"), "mesh_hash": (meta.get("mesh_hash") or "")[:12]}
    Tmx = C["t_maxwell_sector"] * NS
    res["maxwell"] = metrics(Tmx) if uniform else {"mean": float(Tmx.mean())}
    stride = int(C["centre"][1] - C["centre"][0]) if ncen > 1 else R
    dcen = stride * dth
    iA, iB, iC = C["iA"], C["iB"], C["iC"]
    pA, pB, pC = C["psiA"], C["psiB"], C["psiC"]
    if uniform:
        # (B) trajectory energy balance: T = dW'/dth - npar*sum psi di/dth
        dW = spectral_derivative(Wc, dcen)
        corr = npar * (pA * spectral_derivative(iA, dcen) + pB * spectral_derivative(iB, dcen)
                       + pC * spectral_derivative(iC, dcen))
        TB = dW - corr
        res["energy_traj"] = metrics(TB)
        tw = terminal_work_mean(iA, iB, iC, pA, pB, pC, dcen, npar)
        sv = space_vector_mean(iA, iB, iC, pA, pB, pC, pp, npar)
        res["terminal_work_mean"] = tw
        res["space_vector_mean"] = sv
        # shipped = replacement mean + raw Maxwell AC
        mean_ship = sv if meta["demag"] else tw
        res["shipped_mean_source"] = "space_vector" if meta["demag"] else "terminal_work"
        Tship = Tmx - Tmx.mean() + (mean_ship if not meta["noload"] else Tmx.mean())
        res["shipped"] = metrics(Tship)
        # the existing L2-mortar virtual-work diagnostic
        vwd = C["t_vw_diag"]
        if np.all(np.isfinite(vwd)):
            res["vw_mortar_diag"] = metrics(vwd)
        res["_series"] = {"theta_e_deg": (C["centre"] * 360.0 / R).tolist(),
                          "T_maxwell": Tmx.tolist(), "T_energy_traj": TB.tolist(),
                          "T_shipped": Tship.tolist()}
        # 60 deg-el symmetry check on the energy reference
        if R % 6 == 0 and (R // 6) % stride == 0:
            sh = (R // 6) // stride
            res["sym60_max_abs_diff_Nm"] = float(np.max(np.abs(TB - np.roll(TB, -sh))))
            res["sym60_max_abs_diff_maxwell_Nm"] = float(np.max(np.abs(Tmx - np.roll(Tmx, -sh))))
    # (A) frozen-current Richardson virtual work at centres that have neighbours
    TA = []
    for jc in np.unique(z["centre_idx"][base]):
        sel = base & (z["centre_idx"] == jc)
        offs = dict(zip(off[sel].tolist(), wprime[sel].tolist()))
        if all(o in offs for o in (-2, -1, 1, 2)):
            d1 = (offs[1] - offs[-1]) / (2 * dth)
            d2 = (offs[2] - offs[-2]) / (4 * dth)
            c = int(z["centre"][sel][0])
            TA.append((c, (4 * d1 - d2) / 3.0, d1, d2))
    if TA:
        TA = np.asarray(TA)
        res["vw_frozen_n"] = int(TA.shape[0])
        cen_map = {int(c): i for i, c in enumerate(C["centre"])}
        idx = [cen_map[int(c)] for c in TA[:, 0]]
        if uniform:
            dAB = TA[:, 1] - TB[idx]
            res["vw_frozen_vs_traj_max_abs_Nm"] = float(np.max(np.abs(dAB)))
            res["vw_frozen_vs_traj_rms_Nm"] = float(np.sqrt(np.mean(dAB ** 2)))
            res["vw_frozen_vs_maxwell_rms_Nm"] = float(np.sqrt(np.mean((TA[:, 1] - Tmx[idx]) ** 2)))
            res["vw_d1_vs_richardson_max_abs_Nm"] = float(np.max(np.abs(TA[:, 2] - TA[:, 1])))
        if TA.shape[0] == ncen and uniform:
            res["vw_frozen"] = metrics(TA[:, 1])
        res["_vw_frozen_series"] = TA[:, :2].tolist()
    # current-perturbation self-check: dW'/ds = npar * sum i psi
    chk = []
    for jc in np.unique(z["centre_idx"][~base]):
        s_up = (~base) & (z["centre_idx"] == jc) & (cs > 1)
        s_dn = (~base) & (z["centre_idx"] == jc) & (cs < 1)
        s0 = cen & (z["centre_idx"] == jc)
        if s_up.any() and s_dn.any() and s0.any():
            num = (wprime[s_up][0] - wprime[s_dn][0]) / (cs[s_up][0] - cs[s_dn][0])
            ana = npar * float(z["iA"][s0][0] * z["psiA"][s0][0] + z["iB"][s0][0] * z["psiB"][s0][0]
                               + z["iC"][s0][0] * z["psiC"][s0][0])
            chk.append({"centre": int(z["centre"][s0][0]), "dWds_numeric_J": float(num),
                        "npar_sum_i_psi_J": ana, "rel_diff": float((num - ana) / ana)})
    if chk:
        res["dWdi_check"] = chk
    return res


def subsample_maxwell(run, samples_per_cycle, cycles=12):
    """p-p of the shipped-style waveform when only every s-th centre is kept,
    for every phase offset (min / max over the offsets)."""
    meta, z = _load(run)
    R = int(meta["ring"]); NS = meta["NS"]
    cen = (z["offset"] == 0) & (z["cur_scale"] == 1.0)
    o = np.argsort(z["centre"][cen])
    Tmx = z["t_maxwell_sector"][cen][o] * NS
    n = Tmx.size
    per = samples_per_cycle * cycles
    if n % per:
        return None
    s = n // per
    pps = [float(np.ptp(Tmx[ph::s])) for ph in range(s)]
    return {"steps_per_period": per, "pp_min": min(pps), "pp_max": max(pps), "n_offsets": s}


if __name__ == "__main__":
    for r in sys.argv[1:]:
        out = analyse_run(r)
        out.pop("_series", None); out.pop("_vw_frozen_series", None)
        print(json.dumps(out, indent=1))
