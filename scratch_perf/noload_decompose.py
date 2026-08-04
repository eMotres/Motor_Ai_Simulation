"""Physics-structured decomposition of the MEASURED free-run power of the real
150 mm 24s/28p machine (steel B15AHV950M, 35 mm stack).

    P_total(n) = P_bearing(n) + P_windage(n) + k_build * P_fe_computed(n)
                                             + P_rotor_eddy_computed(n)

* P_bearing  — SKF friction model for 2 x 61811-2RS1.  Every constant is pinned
               from the SKF catalogue; nothing here is fitted unless asked.
* P_windage  — laminar Taylor-Couette air gap + two rotor end faces, real
               dimensions.  No free constant.
* P_fe       — OUR computed no-load iron loss (scratch_perf/noload_fe_sweep_*.json),
               used as a SHAPE and scaled by the one unknown k_build.
* P_rotor_eddy — computed magnet + shaft eddy loss.  In the measurement, so it is
               on the balance sheet; NOT scaled (magnets and an aluminium shaft
               are not punched laminations).

Outputs: fitted parameters + CIs, per-point residuals, leverage / studentised
residuals, the 4000 rpm extrapolation, and a PNG of the fit.
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# ─────────────────────────────────────────────────────────────────────────────
# 1.  THE MEASUREMENT  (rpm_actual, P_noload [W])
# ─────────────────────────────────────────────────────────────────────────────
MEAS = np.array([
    (1037.0,  83.0), (1168.0,  90.0), (1477.0, 128.0), (1635.0, 144.0),
    (1869.0, 169.0), (1994.0, 190.0), (2175.0, 248.0), (2395.0, 250.0),
    (2611.0, 290.0), (2799.0, 319.0),
])
n_meas, P_meas = MEAS[:, 0], MEAS[:, 1]
w_meas = n_meas * 2.0 * math.pi / 60.0                      # rad/s

# ─────────────────────────────────────────────────────────────────────────────
# 2.  BEARINGS — SKF model, 2 x 61811-2RS1
#     Source: SKF "The SKF model for calculating the frictional moment"
#     (cdn.skfmediahub.skf.com/api/public/0901d1968065e9e7), tables 2 and 3.
# ─────────────────────────────────────────────────────────────────────────────
BRG = dict(
    d_mm=55.0, D_mm=72.0, B_mm=9.0, n_bearings=2,
    # Table 3, RS1 seals on deep groove ball bearings, 62 < D <= 80 mm:
    beta=2.25, KS1=0.018, KS2=20.0,        # Mseal [Nmm], ds in mm; BOTH seals
    ds_mm=59.5,                            # seal counterface diameter d1.  Not
                                           # published for 61811; taken as
                                           # d + 0.25*(D-d).  +-2 mm is +-8 % on
                                           # Mseal — carried as an uncertainty.
    # Table 2, series 617/618/628/637/638 (61811 is series 618):
    R1=4.7e-7, S1=6.50e-3,
    nu_mm2s=30.0,      # grease base-oil viscosity at running temperature
    mu_sl=0.05,        # sliding coefficient, grease-lubricated ball bearing
)


def bearing_torque_Nm(n_rpm, Fr_N_per_bearing, brg=BRG, ds_mm=None):
    """SKF total bearing friction torque [Nm] for the whole pair.

    Returns (M_total, breakdown-dict).  Mseal is speed-INDEPENDENT (=> P ~ n);
    Mrr ~ n^0.6; Msl is constant.  Everything in Nmm internally, as SKF states it.
    """
    n_rpm = np.asarray(n_rpm, float)
    d, D = brg["d_mm"], brg["D_mm"]
    dm = 0.5 * (d + D)
    ds = brg["ds_mm"] if ds_mm is None else ds_mm
    Fr = max(float(Fr_N_per_bearing), 1e-9)

    M_seal = brg["KS1"] * ds ** brg["beta"] + brg["KS2"]           # per bearing
    Grr = brg["R1"] * dm ** 1.96 * Fr ** 0.54
    M_rr = Grr * (n_rpm * brg["nu_mm2s"]) ** 0.6
    Gsl = brg["S1"] * dm ** -0.26 * Fr ** (5.0 / 3.0)
    M_sl = Gsl * brg["mu_sl"]

    M_one = M_seal + M_rr + M_sl                                   # Nmm
    M_tot = brg["n_bearings"] * M_one * 1e-3                       # Nm
    return M_tot, {"M_seal_Nm_per_brg": M_seal * 1e-3,
                   "M_rr_Nm_per_brg": np.asarray(M_rr * 1e-3),
                   "M_sl_Nm_per_brg": M_sl * 1e-3,
                   "dm_mm": dm, "ds_mm": ds, "Fr_N": Fr}


# ─────────────────────────────────────────────────────────────────────────────
# 3.  WINDAGE — real dimensions, no free constant
# ─────────────────────────────────────────────────────────────────────────────
RHO_AIR = 1.10      # kg/m3, warm machine (~50 C)
MU_AIR = 1.95e-5    # Pa.s at ~50 C


def windage_W(n_rpm, r_ro, r_si, L, rho=RHO_AIR, mu=MU_AIR):
    """Air-gap Couette drag + two rotor end faces [W].

    Air gap: laminar concentric-cylinder torque  M = 4*pi*mu*w*r_i^2*r_o^2*L /
    (r_o^2 - r_i^2), multiplied by a Taylor-vortex enhancement
    (Ta/Ta_c)^0.5 once Ta > Ta_c = 1700 (Taylor 1923 / Bilgen-Boulos).
    End faces: enclosed rotating disc,  M = 0.5*C_M*rho*w^2*r^5 per face with
    C_M = 0.146*Re^-0.2 (free-disc turbulent; an UPPER bound for an enclosed one).
    """
    n_rpm = np.asarray(n_rpm, float)
    w = n_rpm * 2.0 * math.pi / 60.0
    delta = r_si - r_ro

    M_gap = 4.0 * math.pi * mu * w * r_ro ** 2 * r_si ** 2 * L / (r_si ** 2 - r_ro ** 2)
    Re_d = rho * w * r_ro * delta / mu
    Ta = Re_d ** 2 * (delta / r_ro)
    enh = np.where(Ta > 1700.0, np.sqrt(np.maximum(Ta, 1.0) / 1700.0), 1.0)
    M_gap = M_gap * enh

    Re_r = np.maximum(rho * w * r_ro ** 2 / mu, 1.0)
    C_M = 0.146 * Re_r ** -0.2
    M_disc = 2.0 * 0.5 * C_M * rho * w ** 2 * r_ro ** 5      # two faces

    return (M_gap + M_disc) * w, {"M_gap_Nm": M_gap, "M_disc_Nm": M_disc,
                                  "Ta": Ta, "Re_delta": Re_d}


# ─────────────────────────────────────────────────────────────────────────────
# 4.  COMPUTED no-load electromagnetic loss curve  → smooth power-law shape
# ─────────────────────────────────────────────────────────────────────────────
def load_computed(steps: int = 72):
    f = ROOT / "scratch_perf" / f"noload_fe_sweep_{steps}.json"
    rows = sorted(json.loads(f.read_text()), key=lambda r: r["rpm"])
    n = np.array([r["rpm"] for r in rows], float)
    fe = np.array([r["P_fe_avg_W"] for r in rows], float)
    mg = np.array([r.get("P_mag_avg_W") or 0.0 for r in rows], float)
    sh = np.array([r.get("P_shaft_avg_W") or 0.0 for r in rows], float)
    # copper PROXIMITY loss from the open-circuit magnet field: no net current
    # flows at I=0, but the field still drives eddies in the strands, so this is
    # in the measured free-run power too.
    cx = np.array([r.get("P_cu_ac_prox_W") or 0.0 for r in rows], float)
    terms = [r.get("P_fe_terms") or {} for r in rows]
    return rows, n, fe, mg, sh, cx, terms


def fe_split(terms):
    """Per-speed (hyst+excess, classical-eddy) split of the computed iron loss.

    P_fe_terms is {stator|rotor: {hyst, eddy, excess, ...}} in the solver's own
    units; the two groups are returned normalised so their SUM reproduces the
    reported P_fe_avg_W when multiplied by that total.
    """
    he, ed = [], []
    for t in terms:
        h = e = x = 0.0
        for part in ("stator", "rotor"):
            d = (t or {}).get(part) or {}
            h += float(d.get("hysteresis_W", 0.0) or 0.0)
            e += float(d.get("eddy_W", 0.0) or 0.0)
            x += float(d.get("excess_W", 0.0) or 0.0)
        tot = h + e + x
        if tot <= 0:
            he.append(np.nan); ed.append(np.nan); continue
        he.append((h + x) / tot); ed.append(e / tot)
    return np.array(he), np.array(ed)


def powerlaw_interp(n_ref, P_ref):
    """log-log linear interpolation/extrapolation — a loss curve is a sum of
    powers of n, so log P vs log n is nearly straight and this neither invents
    wiggles nor clips at the ends the way a spline does."""
    lx, ly = np.log(n_ref), np.log(np.maximum(P_ref, 1e-12))

    def f(n):
        return np.exp(np.interp(np.log(np.asarray(n, float)), lx, ly,
                                left=np.nan, right=np.nan)) if False else \
            np.exp(_lin_extrap(np.log(np.asarray(n, float)), lx, ly))
    return f


def _lin_extrap(x, xp, fp):
    x = np.atleast_1d(np.asarray(x, float))
    y = np.interp(x, xp, fp)
    lo = x < xp[0]
    hi = x > xp[-1]
    if lo.any():
        s = (fp[1] - fp[0]) / (xp[1] - xp[0])
        y[lo] = fp[0] + s * (x[lo] - xp[0])
    if hi.any():
        s = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
        y[hi] = fp[-1] + s * (x[hi] - xp[-1])
    return y


# ─────────────────────────────────────────────────────────────────────────────
# 5.  FIT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 72
    geo = json.loads((ROOT / "scratch_perf" / "noload_geom.json").read_text())
    dims = geo["dims"]
    m_rot = geo["rotating_mass_kg"]

    rows, n_c, fe_c, mg_c, sh_c, cx_c, terms = load_computed(steps)
    f_fe = powerlaw_interp(n_c, fe_c)
    # everything electromagnetic that is NOT punched lamination: magnet eddy,
    # shaft eddy, copper proximity.  Pinned at 1.0 — punching cannot degrade it.
    f_rot = powerlaw_interp(n_c, mg_c + sh_c + cx_c)

    # ── bearing load: rotor weight, split over 2 bearings ────────────────────
    # The 2-D CAD rotor (iron + magnets + shaft tube) is a LOWER bound on the
    # real rotating assembly; the seal term does not depend on load at all and
    # Mrr ~ Fr^0.54, so a 3x mass error moves the bearing torque by <1 %.
    Fr = m_rot * 9.81 / 2.0

    def model(n, k_build, M_brg_Nm, k_rot=1.0):
        w = np.asarray(n, float) * 2.0 * math.pi / 60.0
        # bearing SPEED SHAPE from SKF (seal + Mrr(n^0.6) + Msl), normalised so
        # M_brg_Nm is the torque at 2000 rpm — that keeps the fitted parameter
        # interpretable and the n^0.6 rolling curvature physical.
        M0, _ = bearing_torque_Nm(n, Fr)
        Mref, _ = bearing_torque_Nm(2000.0, Fr)
        P_b = M_brg_Nm * (M0 / float(Mref)) * w
        P_w, _ = windage_W(n, dims["r_rotor_out_m"], dims["r_stator_in_m"],
                           dims["stack_length_m"])
        return P_b + P_w + k_build * f_fe(n) + k_rot * f_rot(n), P_b, P_w

    # SKF prior for the bearing torque at 2000 rpm
    M_skf, brk = bearing_torque_Nm(2000.0, Fr)
    M_skf = float(M_skf)

    # design matrix for the two free linear parameters (k_build, M_brg)
    P_w_meas, _ = windage_W(n_meas, dims["r_rotor_out_m"], dims["r_stator_in_m"],
                            dims["stack_length_m"])
    P_rot_meas = f_rot(n_meas)
    M_shape = np.array([float(bearing_torque_Nm(x, Fr)[0]) for x in n_meas]) / M_skf
    A = np.column_stack([f_fe(n_meas), M_shape * w_meas * M_skf])
    y = P_meas - P_w_meas - P_rot_meas

    def lsq(A, y, mask=None):
        m = np.ones(len(y), bool) if mask is None else mask
        beta, *_ = np.linalg.lstsq(A[m], y[m], rcond=None)
        r = y - A @ beta
        dof = int(m.sum()) - A.shape[1]
        s2 = float((r[m] ** 2).sum()) / dof
        cov = s2 * np.linalg.inv(A[m].T @ A[m])
        H = A[m] @ np.linalg.inv(A[m].T @ A[m]) @ A[m].T
        return beta, r, s2, cov, np.diag(H), dof

    res = {}

    # (a) BOTH free
    beta, r, s2, cov, lev, dof = lsq(A, y)
    res["free"] = dict(k_build=beta[0], M_brg_ratio=beta[1], dof=dof,
                       sigma=math.sqrt(s2),
                       se=[math.sqrt(cov[0, 0]), math.sqrt(cov[1, 1])],
                       corr=float(cov[0, 1] / math.sqrt(cov[0, 0] * cov[1, 1])),
                       resid=r.tolist(), leverage=lev.tolist())

    # (b) bearing PINNED to SKF -> k_build alone
    y2 = y - A[:, 1]
    a2 = A[:, :1]
    beta2, r2, s22, cov2, lev2, dof2 = lsq(a2, y2)
    res["pinned"] = dict(k_build=float(beta2[0]), dof=dof2, sigma=math.sqrt(s22),
                         se=[math.sqrt(cov2[0, 0])], resid=r2.tolist(),
                         leverage=lev2.tolist())

    # (c) bearing pinned, drop the suspected outlier at 2175 rpm
    i_out = int(np.argmin(np.abs(n_meas - 2175.0)))
    mask = np.ones(len(y), bool); mask[i_out] = False
    beta3, r3, s23, cov3, lev3, dof3 = lsq(a2, y2, mask)
    res["pinned_no2175"] = dict(k_build=float(beta3[0]), dof=dof3,
                                sigma=math.sqrt(s23),
                                se=[math.sqrt(cov3[0, 0])], resid=r3.tolist())

    # (d) two-k diagnostic: (hyst+excess) and classical eddy scale separately
    he_f, ed_f = fe_split(terms)
    f_he = powerlaw_interp(n_c, fe_c * he_f)
    f_ed = powerlaw_interp(n_c, fe_c * ed_f)
    A4 = np.column_stack([f_he(n_meas), f_ed(n_meas)])
    beta4, r4, s24, cov4, lev4, dof4 = lsq(A4, y2)
    res["two_k_pinned_brg"] = dict(
        k_hyst_exc=float(beta4[0]), k_eddy=float(beta4[1]), dof=dof4,
        sigma=math.sqrt(s24),
        se=[math.sqrt(cov4[0, 0]), math.sqrt(cov4[1, 1])],
        corr=float(cov4[0, 1] / math.sqrt(cov4[0, 0] * cov4[1, 1])),
        resid=r4.tolist())

    # (e) ONE k on the WHOLE computed electromagnetic loss (iron + magnet +
    #     shaft + Cu proximity).  A free-run test cannot separate those four, so
    #     this is what the measurement can honestly resolve; the iron-only k in
    #     (b) leans on the assumption that the other three are exactly right.
    P_em_c = f_fe(n_meas) + P_rot_meas
    A5 = P_em_c.reshape(-1, 1)
    y5 = P_meas - P_w_meas - A[:, 1]
    beta5, r5, s25, cov5, lev5, dof5 = lsq(A5, y5)
    res["k_em_all"] = dict(k_em=float(beta5[0]), dof=dof5, sigma=math.sqrt(s25),
                           se=[math.sqrt(cov5[0, 0])], resid=r5.tolist())

    # (f) PWM signature test: add a speed-INDEPENDENT offset C.  Inverter
    #     switching-ripple iron/copper loss at a no-load spin is set by the DC
    #     bus and f_sw, not by speed (the ripple flux is ~Vdc*D(1-D)*Ts and
    #     D(1-D) SHRINKS as the modulation index rises with speed), so PWM shows
    #     up as a flat-or-falling offset, never as extra n^2.
    A6 = np.column_stack([f_fe(n_meas), np.ones(len(n_meas))])
    beta6, r6, s26, cov6, lev6, dof6 = lsq(A6, y2)
    res["k_plus_offset"] = dict(k_build=float(beta6[0]), C_W=float(beta6[1]),
                                dof=dof6, sigma=math.sqrt(s26),
                                se=[math.sqrt(cov6[0, 0]), math.sqrt(cov6[1, 1])],
                                resid=r6.tolist())

    # (g) SENSITIVITY of k_build to the bearing torque (the SKF seal constant is
    #     a run-in steady-state value; a new, freshly greased 2RS pair runs high)
    sens = {}
    for f in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        yy = y - f * A[:, 1]
        bb, rr, ss, cc, _, dd = lsq(a2, yy)
        sens[f"{f:.2f}"] = dict(k_build=float(bb[0]), sigma=math.sqrt(ss),
                                se=float(math.sqrt(cc[0, 0])))
    res["k_vs_bearing_scale"] = sens

    # (i) THE WINNING MODEL — every computed loss at k = 1 (nothing scaled) plus
    #     ONE free term C2*n^2.  Same parameter count as (b)/(e); it wins on
    #     residual scatter, and it is the model the DATA points at: the excess
    #     over our computed electromagnetic loss goes as n^1.96, i.e. a
    #     classical eddy-current mechanism the 2-D sinusoidal model does not
    #     have (inter-laminar shorting / conductive end-region structure /
    #     a thicker lamination than the datasheet), NOT a punching-degraded
    #     hysteresis term (which would be n^1) and NOT PWM (flat or falling).
    A7 = (n_meas ** 2).reshape(-1, 1)
    y7 = P_meas - P_w_meas - A[:, 1] - P_em_c
    beta7, r7, s27, cov7, lev7, dof7 = lsq(A7, y7)
    mask7 = np.ones(len(y7), bool); mask7[i_out] = False
    beta7b, _r, s27b, cov7b, _l, dof7b = lsq(A7, y7, mask7)
    res["k1_plus_n2"] = dict(
        C2_W_per_rpm2=float(beta7[0]), dof=dof7, sigma=math.sqrt(s27),
        se=[math.sqrt(cov7[0, 0])], resid=r7.tolist(),
        C2_no2175=float(beta7b[0]), sigma_no2175=math.sqrt(s27b),
        se_no2175=math.sqrt(cov7b[0, 0]),
        excess_exponent=float(np.polyfit(np.log(n_meas),
                                         np.log(np.maximum(y7, 1e-9)), 1)[0]))

    # (j) BOTH free in the winning parameterisation: bearing torque AND the n^2
    #     term.  Unlike (a) — where k_build fights the bearing for the same
    #     n-linear power and the fit collapses (corr -0.998) — n and n^2 are
    #     well separated, so this fit is conditioned and the MEASUREMENT itself
    #     says what the bearing torque is.  Compare with the SKF prediction.
    A8 = np.column_stack([M_shape * w_meas * M_skf, n_meas ** 2])
    y8 = P_meas - P_w_meas - P_em_c
    beta8, r8, s28, cov8, lev8, dof8 = lsq(A8, y8, mask7)
    res["brg_and_n2_free"] = dict(
        M_brg_scale=float(beta8[0]), C2_W_per_rpm2=float(beta8[1]),
        M_brg_fitted_Nm_at_2000=float(beta8[0]) * M_skf,
        M_brg_SKF_Nm_at_2000=M_skf,
        dof=dof8, sigma=math.sqrt(s28),
        se=[math.sqrt(cov8[0, 0]), math.sqrt(cov8[1, 1])],
        corr=float(cov8[0, 1] / math.sqrt(cov8[0, 0] * cov8[1, 1])),
        resid=r8.tolist(), note="2175 rpm excluded")

    # (h) SHAPE: local k at each measured speed + effective log-log exponents.
    P_b_meas = A[:, 1]
    P_em_meas = P_meas - P_w_meas - P_b_meas            # measured EM loss
    res["shape"] = dict(
        rpm=n_meas.tolist(),
        P_em_measured_W=P_em_meas.tolist(),
        P_em_computed_W=P_em_c.tolist(),
        k_local_iron_only=((P_em_meas - P_rot_meas) / f_fe(n_meas)).tolist(),
        k_local_all_em=(P_em_meas / P_em_c).tolist(),
        exp_measured_EM=float(np.polyfit(np.log(n_meas),
                                         np.log(np.maximum(P_em_meas, 1e-9)), 1)[0]),
        exp_computed_iron=float(np.polyfit(np.log(n_c), np.log(fe_c), 1)[0]),
        exp_computed_all_EM=float(np.polyfit(
            np.log(n_c), np.log(fe_c + mg_c + sh_c + cx_c), 1)[0]),
        exp_measured_total=float(np.polyfit(np.log(n_meas), np.log(P_meas), 1)[0]),
    )

    # ── studentised residuals + Cook's D on the headline (b) fit ─────────────
    s = math.sqrt(s22)
    stud = r2 / (s * np.sqrt(np.maximum(1.0 - lev2, 1e-9)))
    cook = (r2 ** 2 / (1 * s22)) * (lev2 / (1.0 - lev2) ** 2)
    res["pinned"]["studentised"] = stud.tolist()
    res["pinned"]["cooks_D"] = cook.tolist()

    # ── the 4000 rpm answer ─────────────────────────────────────────────────
    k = res["pinned"]["k_build"]; sk = res["pinned"]["se"][0]
    n4 = 4000.0; w4 = n4 * 2 * math.pi / 60.0
    M4 = float(bearing_torque_Nm(n4, Fr)[0])
    P_b4 = M4 * w4
    P_w4 = float(windage_W(n4, dims["r_rotor_out_m"], dims["r_stator_in_m"],
                           dims["stack_length_m"])[0])
    P_fe_c4 = float(np.ravel(f_fe(n4))[0]); P_rot4 = float(np.ravel(f_rot(n4))[0])
    res["at_4000"] = dict(
        P_bearing_W=P_b4, M_bearing_Nm=M4, P_windage_W=P_w4,
        P_fe_computed_W=P_fe_c4, P_rotor_eddy_computed_W=P_rot4,
        P_fe_measured_W=k * P_fe_c4,
        P_fe_measured_lo_W=(k - 1.96 * sk) * P_fe_c4,
        P_fe_measured_hi_W=(k + 1.96 * sk) * P_fe_c4,
        P_total_W=P_b4 + P_w4 + k * P_fe_c4 + P_rot4,
        # the SHAPE-honest band: k is not speed-flat (see res["shape"]), so the
        # 4000 rpm iron is bracketed by the LOCAL k at the fastest measured
        # point (a lower bound if k keeps rising) and the fitted k.
        k_local_top=float(res["shape"]["k_local_iron_only"][-1]),
        P_fe_measured_localk_W=float(res["shape"]["k_local_iron_only"][-1]) * P_fe_c4,
        # same three numbers with ONE k on the whole EM loss
        k_em=res["k_em_all"]["k_em"],
        P_em_measured_W=res["k_em_all"]["k_em"] * (P_fe_c4 + P_rot4),
        P_total_kem_W=P_b4 + P_w4 + res["k_em_all"]["k_em"] * (P_fe_c4 + P_rot4))

    # ── the WINNING model at 4000 rpm, with an honest error budget ───────────
    # HEADLINE C2 excludes the 2175 rpm point (8 sigma off the clean fit)
    C2 = res["k1_plus_n2"]["C2_no2175"]
    sC2 = res["k1_plus_n2"]["se_no2175"]
    ex4 = C2 * n4 ** 2
    # how many sigma the 2175 point is, measured against the CLEAN fit
    res["k1_plus_n2"]["resid_2175_vs_clean_W"] = float(
        y7[i_out] - C2 * n_meas[i_out] ** 2)
    res["k1_plus_n2"]["sigma_2175_vs_clean"] = float(
        (y7[i_out] - C2 * n_meas[i_out] ** 2) / res["k1_plus_n2"]["sigma_no2175"])
    band = {}
    for f in (0.75, 1.0, 1.25):
        Pb = f * P_b4
        # re-fit C2 with the bearing scaled, then re-evaluate at 4000.  The
        # 2175 rpm point is OUT: against the clean 9-point fit it sits ~8 sigma
        # off, so it is a measurement error, not information about C2.
        yy = P_meas - P_w_meas - f * A[:, 1] - P_em_c
        bb, _r, ss, cc, _l, _d = lsq(A7, yy, mask7)
        band[f"brg_x{f:.2f}"] = dict(
            P_bearing_4000_W=Pb, C2=float(bb[0]),
            P_excess_4000_W=float(bb[0]) * n4 ** 2,
            P_fe_if_all_excess_is_iron_W=P_fe_c4 + float(bb[0]) * n4 ** 2,
            sigma=math.sqrt(ss))
    lo = min(v["P_fe_if_all_excess_is_iron_W"] for v in band.values())
    hi = max(v["P_fe_if_all_excess_is_iron_W"] for v in band.values())
    res["at_4000_best"] = dict(
        model="all computed losses at k=1  +  C2*n^2",
        P_bearing_W=P_b4, P_windage_W=P_w4,
        P_fe_computed_W=P_fe_c4,
        P_magnet_shaft_cuprox_computed_W=P_rot4,
        P_excess_n2_W=ex4,
        P_excess_n2_95CI_W=[(C2 - 1.96 * sC2) * n4 ** 2, (C2 + 1.96 * sC2) * n4 ** 2],
        P_total_W=P_b4 + P_w4 + P_fe_c4 + P_rot4 + ex4,
        P_fe_measured_if_excess_is_iron_W=P_fe_c4 + ex4,
        k_build_at_4000_if_excess_is_iron=(P_fe_c4 + ex4) / P_fe_c4,
        band_over_bearing_model_W=[lo, hi],
        bearing_sensitivity=band)

    res["inputs"] = dict(
        steps=steps, rotating_mass_kg=m_rot, Fr_N_per_bearing=Fr,
        M_bearing_pair_at_2000rpm_Nm=M_skf,
        M_seal_per_bearing_Nm=float(brk["M_seal_Nm_per_brg"]),
        M_rr_per_bearing_at_2000_Nm=float(np.asarray(brk["M_rr_Nm_per_brg"])),
        M_sl_per_bearing_Nm=float(brk["M_sl_Nm_per_brg"]),
        computed_rpm=n_c.tolist(), computed_P_fe_W=fe_c.tolist(),
        computed_P_mag_W=mg_c.tolist(), computed_P_shaft_W=sh_c.tolist(),
        computed_P_cu_prox_W=cx_c.tolist(),
        computed_V_peak_V=[r.get("V_peak_V") for r in rows],
        fe_hyst_exc_frac=he_f.tolist(), fe_eddy_frac=ed_f.tolist(),
        air_gap_mm=(dims["r_stator_in_m"] - dims["r_rotor_out_m"]) * 1e3)

    # per-point table for the report
    P_fit, P_b, P_w = model(n_meas, k, M_skf)
    res["table"] = [dict(rpm=float(a), P_meas_W=float(b), P_fit_W=float(c),
                         resid_W=float(b - c), resid_pct=float(100 * (b - c) / b),
                         P_bearing_W=float(d), P_windage_W=float(e),
                         P_fe_fit_W=float(k * g), P_rotor_eddy_W=float(h),
                         leverage=float(l), studentised=float(sr))
                    for a, b, c, d, e, g, h, l, sr in
                    zip(n_meas, P_meas, P_fit, P_b, P_w, f_fe(n_meas),
                        P_rot_meas, lev2, stud)]

    out = ROOT / "scratch_perf" / f"noload_decomposition_{steps}.json"
    out.write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps({kk: res[kk] for kk in
                      ("inputs", "free", "pinned", "pinned_no2175",
                       "two_k_pinned_brg", "at_4000")}, indent=2, default=float))
    print("\nrpm  P_meas  P_fit  resid  %   |  brg   wind   iron   rotor-eddy  lev  stud")
    for t in res["table"]:
        print(f"{t['rpm']:6.0f} {t['P_meas_W']:7.1f} {t['P_fit_W']:7.1f} "
              f"{t['resid_W']:7.1f} {t['resid_pct']:6.1f} | "
              f"{t['P_bearing_W']:6.1f} {t['P_windage_W']:5.2f} "
              f"{t['P_fe_fit_W']:7.1f} {t['P_rotor_eddy_W']:7.1f} "
              f"{t['leverage']:5.2f} {t['studentised']:6.2f}")
    print("WROTE", out)

    # ── plot ────────────────────────────────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ng = np.linspace(800, 4200, 200)
        Pg, Pbg, Pwg = model(ng, k, M_skf)
        fe_g = f_fe(ng); rot_g = f_rot(ng); ex_g = C2 * ng ** 2
        Pg3 = Pbg + Pwg + fe_g + rot_g + ex_g
        fig, ax = plt.subplots(1, 3, figsize=(18.5, 5.4))
        a0 = ax[0]
        a0.fill_between(ng, 0, Pbg, color="#8fb3d9",
                        label="bearings 2x61811-2RS1 (SKF model, pinned)")
        a0.fill_between(ng, Pbg, Pbg + Pwg, color="#cfe3f5", label="windage (computed)")
        a0.fill_between(ng, Pbg + Pwg, Pbg + Pwg + fe_g, color="#e9a08c",
                        label="iron, OUR computed curve at k = 1")
        a0.fill_between(ng, Pbg + Pwg + fe_g, Pbg + Pwg + fe_g + rot_g, color="#f2c48d",
                        label="magnet + shaft eddy + Cu proximity (computed, k = 1)")
        a0.fill_between(ng, Pbg + Pwg + fe_g + rot_g, Pg3, color="#9d4e3f",
                        label=f"UNMODELLED n^2 term, C2 = {C2*1e5:.2f}e-5 W/rpm^2")
        a0.plot(ng, Pg3, "k-", lw=1.8, label="model total  (sigma = %.1f W on the 9 clean points)"
                % res["k1_plus_n2"]["sigma_no2175"])
        a0.plot(ng, Pg, "k--", lw=1.2,
                label=f"alt: iron x k_build={k:.2f}, no n^2 term (sigma = {res['pinned']['sigma']:.1f} W)")
        a0.plot(n_meas, P_meas, "ko", ms=7, mfc="w", mew=1.6, label="MEASURED")
        a0.axvline(4000, color="0.6", ls=":", lw=1)
        a0.annotate("4000 rpm", (4000, 20), fontsize=8, rotation=90, color="0.4")
        a0.set_xlabel("speed [rpm]"); a0.set_ylabel("no-load power [W]")
        a0.set_title("Measured free-run power, decomposed\n150 mm 24s/28p, B15AHV950M, 35 mm stack, I = 0")
        a0.legend(fontsize=7.5, loc="upper left"); a0.grid(alpha=.3)

        a1 = ax[1]
        a1.axhline(0, color="k", lw=.8)
        r_best = np.asarray(res["k1_plus_n2"]["resid"], float)
        r_k = np.array([t["resid_W"] for t in res["table"]])
        a1.plot(n_meas, r_k, "s--", color="#b0b0b0", ms=6,
                label=f"iron x k_build={k:.2f}   (sigma = {res['pinned']['sigma']:.1f} W, "
                      "systematic curvature)")
        a1.plot(n_meas, r_best, "o-", color="#9d4e3f", ms=7,
                label="k=1 + C2 n^2   (sigma = %.1f W on the 9 clean points)"
                      % res["k1_plus_n2"]["sigma_no2175"])
        a1.annotate("2175 rpm: +26.6 W\n(the one bad point)", (2175, r_best[6]),
                    xytext=(1750, r_best[6] + 4), fontsize=8,
                    arrowprops=dict(arrowstyle="->", color="0.4"))
        a1.set_xlabel("speed [rpm]"); a1.set_ylabel("measured - model [W]")
        a1.legend(fontsize=8)
        a1.set_title("residuals: one free parameter each")
        a1.grid(alpha=.3)

        # panel 3 — WHAT the missing loss is: its speed exponent names it
        a2 = ax[2]
        sh = res["shape"]
        ex_m = P_meas - P_w_meas - A[:, 1] - P_em_c        # measured - everything computed
        a2.loglog(n_meas, ex_m, "o", color="#9d4e3f", ms=9, mfc="w", mew=2,
                  label="MEASURED minus (bearings + windage + all computed EM)")
        a2.loglog(n_meas[i_out], ex_m[i_out], "x", color="k", ms=13, mew=2.5,
                  label="2175 rpm (8.9 sigma outlier, excluded)")
        nn = np.array([950.0, 4200.0])
        anch = ex_m[-1] / n_meas[-1] ** 2
        for p, col, lab in ((1.0, "#2c6fad", "n^1.0  hysteresis / punching-edge degradation"),
                            (1.5, "#7a9e3a", "n^1.5  excess (anomalous) loss"),
                            (2.0, "#c0392b", "n^2.0  CLASSICAL EDDY CURRENTS")):
            a2.loglog(nn, ex_m[-1] * (nn / n_meas[-1]) ** p, "--", color=col,
                      lw=1.6 if p == 2 else 1.1, label=lab)
        a2.set_xlabel("speed [rpm]"); a2.set_ylabel("unexplained no-load power [W]")
        a2.set_title("What is missing?  Its speed exponent names it.\n"
                     f"fitted slope = n^{res['k1_plus_n2']['excess_exponent']:.2f}"
                     "  ->  a CLASSICAL EDDY path the 2-D model has no element for")
        a2.legend(fontsize=7.5, loc="upper left"); a2.grid(alpha=.3, which="both")

        fig.tight_layout()
        png = ROOT / "docs" / "measurements" / "noload_decomposition_150mm.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(png, dpi=140)
        print("WROTE", png)
    except Exception as e:      # noqa: BLE001
        print("plot failed:", e)


if __name__ == "__main__":
    main()
