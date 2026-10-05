"""The static psi-map in (i_d, i_q) and everything derived from it (spec §3).

Conventions [P03]: gamma from the q-axis, gamma > 0 = negative i_d;
amplitude-invariant PEAK dq frame, motoring star:
    i_d = -sqrt(2)·I·sin(gamma),  i_q = sqrt(2)·I·cos(gamma)
    T_psi = 3/2·p·(psi_d·i_q - psi_q·i_d)
    V_ph,peak = sqrt((R·i_d - w·psi_q)^2 + (R·i_q + w·psi_d)^2)
Interpolation (spec 3.4 decision): C¹ Clough–Tocher on the scattered grid in
(i_d, i_q), one isotropic scale for both axes; torque both from psi and from
the stored (Coulomb) torque, with the self-check between them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

SQ2 = math.sqrt(2.0)
SQ3 = math.sqrt(3.0)


def id_iq(I_rms: float, gamma_deg: float) -> Tuple[float, float]:
    g = math.radians(gamma_deg)
    return -SQ2 * I_rms * math.sin(g), SQ2 * I_rms * math.cos(g)


def I_gamma(i_d: float, i_q: float) -> Tuple[float, float]:
    I = math.hypot(i_d, i_q) / SQ2
    g = math.degrees(math.atan2(-i_d, i_q))
    return I, g


def omega_e(rpm: float, pole_pairs: int) -> float:
    return 2.0 * math.pi * float(rpm) / 60.0 * int(pole_pairs)


def v_phase_limit(v_dc: float, m: float) -> float:
    """Linear-SVPWM phase-voltage PEAK limit: m·V_dc/√3 (spec 3.5)."""
    return float(m) * float(v_dc) / SQ3


# ─────────────────────────────────────────────────────────────────────────────
#  MTPA bracket (spec 3.1, P04)
# ─────────────────────────────────────────────────────────────────────────────

def parabola_vertex(g: Sequence[float], T: Sequence[float]) -> Tuple[float, float, bool]:
    """Vertex of the parabola through the sampled maximum and its two
    neighbours.  Returns (gamma*, T*, bracketed) — bracketed False when the
    maximum is at an end of the sampled set (extend the bracket)."""
    g = np.asarray(g, float)
    T = np.asarray(T, float)
    o = np.argsort(g)
    g, T = g[o], T[o]
    k = int(np.argmax(T))
    if k == 0 or k == len(g) - 1:
        return float(g[k]), float(T[k]), False
    a, b, c = np.polyfit(g[k - 1:k + 2], T[k - 1:k + 2], 2)
    if a >= 0:
        return float(g[k]), float(T[k]), False
    gv = -b / (2.0 * a)
    return float(gv), float(np.polyval([a, b, c], gv)), True


def bracket_next(g: Sequence[float], T: Sequence[float], step: float) -> Optional[float]:
    """The next gamma to sample when the max is not bracketed, else None."""
    g = np.asarray(g, float)
    T = np.asarray(T, float)
    o = np.argsort(g)
    g, T = g[o], T[o]
    k = int(np.argmax(T))
    if k == 0:
        return float(g[0] - step)
    if k == len(g) - 1:
        return float(g[-1] + step)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  The map
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PsiMap:
    """Clough–Tocher interpolants of psi_d, psi_q and the stored torque."""
    i_d: np.ndarray
    i_q: np.ndarray
    psi_d: np.ndarray
    psi_q: np.ndarray
    T: np.ndarray
    ripple_pp: np.ndarray
    pole_pairs: int
    scale: float
    mtpa: List[Tuple[float, float, float]] = field(default_factory=list)  # (I, gamma*, T*)
    _f: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, pts: Iterable[Mapping[str, float]], pole_pairs: int,
              mtpa: Optional[List[Tuple[float, float, float]]] = None) -> "PsiMap":
        P = list(pts)
        arr = lambda k: np.array([float(p[k]) for p in P], float)   # noqa: E731
        i_d, i_q = arr("i_d"), arr("i_q")
        scale = float(max(np.max(np.hypot(i_d, i_q)), 1e-9))
        m = cls(i_d=i_d, i_q=i_q, psi_d=arr("psi_d"), psi_q=arr("psi_q"), T=arr("T"),
                ripple_pp=np.array([float(p.get("ripple_pp") or 0.0) for p in P]),
                pole_pairs=int(pole_pairs), scale=scale,
                mtpa=sorted(mtpa or []))
        from scipy.interpolate import CloughTocher2DInterpolator
        X = np.column_stack([i_d, i_q]) / scale
        for k in ("psi_d", "psi_q", "T", "ripple_pp"):
            m._f[k] = CloughTocher2DInterpolator(X, getattr(m, k))
        return m

    def at(self, i_d: float, i_q: float) -> Dict[str, float]:
        x = np.array([[i_d / self.scale, i_q / self.scale]])
        out = {k: float(f(x)[0]) for k, f in self._f.items()}
        out["T_psi"] = 1.5 * self.pole_pairs * (out["psi_d"] * i_q - out["psi_q"] * i_d)
        out["inside"] = bool(np.isfinite(out["psi_d"]))
        return out

    def at_Ig(self, I_rms: float, gamma_deg: float) -> Dict[str, float]:
        d, q = id_iq(I_rms, gamma_deg)
        o = self.at(d, q)
        o.update(i_d=d, i_q=q, I=I_rms, gamma=gamma_deg)
        return o

    # ── differential inductances (map derivative, P20) ────────────────────
    def diff_L(self, i_d: float, i_q: float, h_rel: float = 0.01) -> Dict[str, float]:
        h = h_rel * self.scale

        def f(k, a, b):
            return self.at(a, b)[k]
        Ldd = (f("psi_d", i_d + h, i_q) - f("psi_d", i_d - h, i_q)) / (2 * h)
        Lqq = (f("psi_q", i_d, i_q + h) - f("psi_q", i_d, i_q - h)) / (2 * h)
        Ldq = (f("psi_d", i_d, i_q + h) - f("psi_d", i_d, i_q - h)) / (2 * h)
        Lqd = (f("psi_q", i_d + h, i_q) - f("psi_q", i_d - h, i_q)) / (2 * h)
        asym = abs(Ldq - Lqd) / max(abs(Ldd), abs(Lqq), 1e-30)
        return {"Ld_diff_H": Ldd, "Lq_diff_H": Lqq, "Ldq_H": Ldq, "Lqd_H": Lqd,
                "reciprocity_asym": asym}

    # ── MTPA line ─────────────────────────────────────────────────────────
    def gamma_mtpa(self, I_rms: float) -> float:
        """gamma_MTPA(I): linear between the FEM-confirmed vertices, 0 at I=0
        (the q-axis at zero current), held beyond the last level."""
        if not self.mtpa:
            return 0.0
        I = [0.0] + [m[0] for m in self.mtpa]
        g = [self.mtpa[0][1] * 0.0] + [m[1] for m in self.mtpa]
        if I_rms >= I[-1]:
            return float(g[-1])
        return float(np.interp(I_rms, I, g))

    # ── voltage ───────────────────────────────────────────────────────────
    def v_phase(self, I_rms: float, gamma_deg: float, rpm: float, R_ph: float) -> float:
        o = self.at_Ig(I_rms, gamma_deg)
        w = omega_e(rpm, self.pole_pairs)
        return math.hypot(R_ph * o["i_d"] - w * o["psi_q"], R_ph * o["i_q"] + w * o["psi_d"])

    def operating_gamma(self, I_rms: float, rpm: float, R_ph: float, v_lim: float,
                        gamma_max: float = 80.0) -> Tuple[Optional[float], str]:
        """The operating angle at (I, n) under the voltage limit (spec 3.5):
        MTPA when its voltage fits, else the smallest gamma in (MTPA, gamma_max]
        that fits (fixed-current FW).  (None, reason) beyond the grid."""
        g0 = self.gamma_mtpa(I_rms)
        if self.v_phase(I_rms, g0, rpm, R_ph) <= v_lim:
            return g0, "mtpa"
        if self.v_phase(I_rms, gamma_max, rpm, R_ph) > v_lim:
            return None, "needs deeper field weakening than characterised (gamma > %g°)" % gamma_max
        lo, hi = g0, gamma_max
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self.v_phase(I_rms, mid, rpm, R_ph) <= v_lim:
                hi = mid
            else:
                lo = mid
        return hi, "field weakening"

    def max_torque_at(self, rpm: float, R_ph: float, v_lim: float, I_lim: float,
                      gamma_max: float = 80.0) -> Dict[str, Any]:
        """Max torque at a speed: scan the current up to I_lim on the
        voltage-limited trajectory (monotone in I for these SPM machines; the
        scan makes no such assumption)."""
        best = None
        for I in np.linspace(0.02 * I_lim, I_lim, 50):
            g, how = self.operating_gamma(float(I), rpm, R_ph, v_lim, gamma_max)
            if g is None:
                continue
            T = self.at_Ig(float(I), g)["T"]
            if best is None or T > best["T"]:
                best = {"T": T, "I": float(I), "gamma": g, "mode": how}
        return best or {"T": None, "reason": "no feasible point"}

    def max_speed(self, R_ph: float, v_lim: float, I_rms: float, gamma_max: float = 80.0,
                  n_hi: float = 2.0e5) -> float:
        """Highest speed at which current I_rms still fits the voltage limit
        inside the characterised FW range (positive torque there)."""
        def ok(n):
            g, _ = self.operating_gamma(I_rms, n, R_ph, v_lim, gamma_max)
            return g is not None and self.at_Ig(I_rms, g)["T"] > 0
        lo, hi = 0.0, n_hi
        if ok(hi):
            return hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo


def points_from_records(recs: Iterable[Mapping[str, Any]], set_name: str,
                        roles: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """Map points of one set (hot / cold) from the run records: the SOLVED
    dq currents and flux linkages (full precision), the Coulomb torque."""
    pts = []
    for rec in recs:
        meta = rec.get("meta") or {}
        if not rec.get("ok") or meta.get("set") != set_name:
            continue
        if roles is not None and not str(meta.get("role", "")).startswith(roles):
            continue
        r = rec["r"]
        pts.append({"id": rec["id"], "I": float(rec["kw"]["I_phase_rms"]),
                    "gamma": float(rec["kw"]["gamma_deg"]),
                    "i_d": float(r["i_d_A"]), "i_q": float(r["i_q_A"]),
                    "psi_d": float(r["psi_d_Wb"]), "psi_q": float(r["psi_q_Wb"]),
                    "T": float(r["T_avg_Nm"]),
                    "ripple_pp": float(r.get("T_ripple_pp_Nm") or 0.0),
                    "role": meta.get("role")})
    return pts


def mtpa_table(st_mtpa: Sequence[Mapping[str, Any]]) -> List[Tuple[float, float, float]]:
    return sorted((float(m["I"]), float(m["gamma_mtpa"]), float(m["T_fem_vertex"]))
                  for m in st_mtpa)


def short_circuit_steady(psi_pm: float, Ld: float, Lq: float, R: float,
                         rpm: float, pole_pairs: int) -> Dict[str, float]:
    """Steady three-phase short circuit from BOTH dq equations with R
    (spec 8.2 e), linear model psi_d = psi_pm + Ld·i_d, psi_q = Lq·i_q:
        0 = R·i_d - w·Lq·i_q ;  0 = R·i_q + w·(psi_pm + Ld·i_d)."""
    w = omega_e(rpm, pole_pairs)
    den = R * R + w * w * Ld * Lq
    i_d = -w * w * Lq * psi_pm / den
    i_q = -w * R * psi_pm / den
    return {"i_d_A": i_d, "i_q_A": i_q, "I_rms_A": math.hypot(i_d, i_q) / SQ2,
            "I_char_rms_A": abs(psi_pm / Ld) / SQ2 if Ld else None}
