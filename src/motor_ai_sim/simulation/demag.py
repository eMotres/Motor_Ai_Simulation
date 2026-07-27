"""Irreversible magnet demagnetisation — the state and the rule, in one place.

Pulled out of the transient because it is the one part of that function with
genuine STATE: a per-element remanence factor that only ever falls, plus the
field diagnostics that let a wrong map be told apart from a wrong rule. Inside a
3400-line function it was a closure over eight locals and could not be exercised
without running a full FEM solve; here it takes a field and returns whether the
magnet moved, so the load-line construction can be checked against a hand
calculation in milliseconds.

The physics, briefly. Reading the material curve at the element's present H is
wrong for a magnet that demagnetises itself: H is produced mostly by the
element's OWN magnetisation, so H and M collapse together and evaluating the
curve at the full-strength field fixes a state that cannot exist. It drove
Fe16N2 to Br 0.016 where the self-consistent answer is 0.26. The honest
operating point is the classic graphical one — where the element's load line
crosses the material's intrinsic curve:

    alpha = B_par / (mu0 * M * br)        effective permeance, from the FEM field
    H(br) = (alpha - 1) * M * br          load line
    =>  J  = mu0 * H / (alpha - 1)        a straight line through the origin

Only the IRREVERSIBLE part is a loss: follow the recoil line from that operating
point back to H = 0. An element that never left the reversible upper branch
lands exactly on Br0 and keeps its full strength. Taking J at the operating
point instead books the curve's own recoil slope as permanent damage and costs
every healthy element about 5 %.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from motor_ai_sim.simulation.field_ops import MU0, _smooth_demag_H

log = logging.getLogger(__name__)

# Convergence tolerance on the per-element Br factor: the magnet counts as
# settled once no element still moves by more than this. The de-rating is
# monotone, so this only decides when the coupling (magnet weakens -> its own
# demagnetising field weakens) has stopped moving.
DEMAG_TOL = 1e-4


def _descriptor(tag: int, idx: np.ndarray, mat: Any) -> Optional[Dict[str, Any]]:
    """Everything the rule needs about ONE magnet domain, or None if it is not a
    magnet with a usable 2nd-quadrant curve."""
    if mat is None or (abs(mat.Mx) + abs(mat.My)) <= 0:
        return None
    bh = getattr(mat, "bh_curve", None)
    if not bh or len(bh) < 2:
        return None
    Mm = math.hypot(mat.Mx, mat.My)
    knee = bh[1][0] if bh[0][1] <= 0 else bh[0][0]
    hs = np.array([pt[0] for pt in bh], float)
    bs = np.array([pt[1] for pt in bh], float)
    o = np.argsort(hs)
    hs, bs = hs[o], bs[o]
    # Recoil slope from the curve's OWN top segment rather than the declared
    # mu_rec: the two disagree by a few percent (curve 1.083 vs declared 1.05 for
    # F45SH), and the mismatch shows up as spurious PERMANENT loss on a magnet
    # that never left the reversible part of its curve.
    mu_rec_c = (abs(float(bs[-1] - bs[-2])) / max(abs(float(hs[-1] - hs[-2])), 1e-9) / MU0
                if hs.size >= 2 else float(mat.mu_r))
    return dict(
        tag=int(tag), idx=np.asarray(idx, int), Mx=mat.Mx, My=mat.My, Mm=Mm,
        knee=float(knee), mu_r=float(mat.mu_r), hs=hs, bs=bs,
        Js=bs - MU0 * hs,            # intrinsic curve; the load line lives in (H, J)
        mu_rec_c=mu_rec_c, Br0=Mm * MU0,
    )


class MagnetDemag:
    """Per-element remanence that only ever falls, plus its diagnostics.

    ``br`` is the caller's array and is mutated IN PLACE — the solver's magnet
    source term reads the same buffer, so there is one state, not two that can
    disagree.
    """

    def __init__(self, cells: Dict[int, np.ndarray], materials: Dict[int, Any],
                 rotor_mesh: Any, br: np.ndarray):
        self.mesh = rotor_mesh
        self.br = br
        self.mags: List[Dict[str, Any]] = []
        for tag, idx in cells.items():
            d = _descriptor(int(tag), idx, materials.get(int(tag)))
            if d is not None:
                self.mags.append(d)
        n = int(br.size)
        # H on the very first evaluation — the field of the PRISTINE magnet, pure
        # geometry with no feedback. Comparing it against the final Br map is what
        # separates a wrong field from a wrong rule.
        self.H_first = np.full(n, np.nan)
        self.H_worst = np.full(n, np.inf)      # running per-element minimum
        self.seen: Dict[int, float] = {}       # magnet tag -> worst smoothed H

    @property
    def active(self) -> bool:
        return bool(self.mags)

    def update(self, Bx: np.ndarray, By: np.ndarray) -> bool:
        """Apply a CONVERGED field. True if any element weakened.

        Must not be handed an intermediate Picard iterate: the early sweeps start
        from unsaturated iron and pass through fields that are pure numerical
        transients (measured H ~ -880 kA/m against a converged -550), and a
        monotone irreversible rule burns that artefact in permanently — it wiped
        NdFeB to Br 0.03 and cost a third of the torque.
        """
        moved = False
        for d in self.mags:
            ix = d["idx"]
            BdotM = Bx[ix] * d["Mx"] + By[ix] * d["My"]
            # H along M at the magnet's CURRENT strength: B_par/mu0 - M(now).
            # Only the magnetisation term scales with the de-rating.
            H_raw = BdotM / (MU0 * d["Mm"]) - d["Mm"] * self.br[ix]
            H = _smooth_demag_H(self.mesh, ix, H_raw)

            fresh = np.isnan(self.H_first[ix])
            if np.any(fresh):
                self.H_first[ix[fresh]] = H[fresh]
            np.minimum.at(self.H_worst, ix, H)
            hmin = float(H.min())
            if hmin < self.seen.get(d["tag"], np.inf):
                self.seen[d["tag"]] = hmin

            cur = self.br[ix]
            Bpar_mu0 = H + d["Mm"] * cur                      # A/m
            alpha = Bpar_mu0 / np.maximum(d["Mm"] * cur, 1e-9)
            # alpha >= 1 means the element is not being demagnetised at all.
            k = MU0 / np.minimum(alpha - 1.0, -1e-9)          # load-line slope < 0
            H_op, J_op = self._cross(d, k)
            # Recoil intercept at H = 0 — the irreversible part alone.
            Br_new = J_op - (d["mu_rec_c"] - 1.0) * MU0 * H_op
            new = np.minimum(cur, np.clip(Br_new / max(d["Br0"], 1e-12), 0.0, 1.0))
            if np.any(new < cur - DEMAG_TOL):
                moved = True
            self.br[ix] = new
        return moved

    @staticmethod
    def _cross(d: Dict[str, Any], k: np.ndarray):
        """Where each element's load line J = k*H crosses the intrinsic curve.

        Walks the tabulated segments from H = 0 downwards and takes the FIRST
        crossing — for a monotone curve that is the operating point. An element
        whose line never crosses keeps Br0 (it is on the reversible branch).
        """
        hs, Js = d["hs"], d["Js"]
        n = k.size
        J_op = np.full(n, d["Br0"])
        H_op = np.zeros(n)
        hit = np.zeros(n, bool)
        for i in range(len(hs) - 2, -1, -1):
            h0, h1 = float(hs[i]), float(hs[i + 1])
            if h1 <= h0:
                continue
            s = (float(Js[i + 1]) - float(Js[i])) / (h1 - h0)
            den = s - k
            Hc = np.where(np.abs(den) > 1e-30, (s * h0 - float(Js[i])) / den, np.nan)
            ok = (~hit) & np.isfinite(Hc) & (Hc >= h0 - 1e-6) & (Hc <= h1 + 1e-6)
            if np.any(ok):
                J_op[ok] = k[ok] * Hc[ok]
                H_op[ok] = Hc[ok]
                hit |= ok
        return H_op, J_op

    def report(self) -> List[Dict[str, Any]]:
        """Per-magnet summary for the UI panel — only the ones worth flagging."""
        out = []
        for d in self.mags:
            hmin = float(self.seen.get(d["tag"], 0.0))
            prox = hmin / d["knee"] if d["knee"] < 0 else 0.0
            if prox > 0.85:
                out.append({
                    "magnet_index": int(d["tag"]),
                    "H_min_kA_per_m": round(hmin * 1e-3, 1),
                    "H_knee_kA_per_m": round(d["knee"] * 1e-3, 1),
                    "knee_proximity": round(prox, 2),
                    "demagnetised": bool(prox > 1.0),
                    "Br_factor": round(float(np.min(self.br[d["idx"]])), 3),
                })
        return out

    @property
    def tags(self) -> List[int]:
        return sorted({int(d["tag"]) for d in self.mags})

    def payload(self, n_stator_tri: int, verts_mm: np.ndarray, tris: np.ndarray,
                domain_per_tri: np.ndarray, dump_H: bool = False):
        """(coef_per_tri, field_dict, report) for the UI map.

        Shared by both element orders — the map must not depend on which order
        produced it, or comparing a P1 demag run against a P2 one compares two
        different pictures as well as two different solves.
        """
        n_all = int(tris.shape[1])
        coef = np.ones(n_all)
        coef[n_stator_tri:] = self.br
        field = {
            "vertices": verts_mm.T.tolist(),
            "triangles": tris.T.astype(int).tolist(),
            "domain_per_tri": np.asarray(domain_per_tri).astype(int).tolist(),
            "demag_coef_per_tri": coef.tolist(),
            "mag_domains": self.tags,
            "extent": [float(verts_mm[0].min()), float(verts_mm[0].max()),
                       float(verts_mm[1].min()), float(verts_mm[1].max())],
        }
        if dump_H:
            # Raw per-element demagnetising field, for diagnosing the map against
            # a reference solver: H_first is the field on the PRISTINE magnet
            # (pure geometry, no feedback), H_worst the running minimum.
            field.update({
                "H_first_per_tri": np.concatenate(
                    [np.full(n_stator_tri, np.nan), self.H_first]).tolist(),
                "H_worst_per_tri": np.concatenate(
                    [np.full(n_stator_tri, np.nan),
                     np.where(np.isinf(self.H_worst), np.nan, self.H_worst)]).tolist(),
                "H_knee_per_mag": {str(int(d["tag"])): float(d["knee"])
                                   for d in self.mags},
            })
        return coef, field, self.report()
