"""Frozen-current co-energy virtual-work reference at a few rotor angles,
recorded beside the Coulomb, Maxwell and L2-mortar torques of the SAME frames.

Adapted (method unchanged) from the paused study's ripple_ref.py
(branch study/torque-ripple-validation, read only): the production P2
sliding-band transient, magnetostatic, imposed current, frame loop
source-patched in memory so each scheduled frame sits at any slip node with
a frozen current vector.  At every frame the discrete functional the
pointwise Newton minimises is evaluated,

    Phi(A) = 1/2 A.K_const.A + sum_sat sum_q w(|B_q|) dx_q - f.A ,
    W' = -N_s L Phi ,

and the reference torque at a centre is the 4th-order Richardson of the
central differences of W' at +-1 and +-2 slip nodes (same currents).

usage: python coul_ref.py --inp /work/in --machine d40 --duty rated
          --centres 0,11,22,... --out /work/out/x [--noload] [--ring R]
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coul_common import scale_mesh, setup  # noqa: E402


class ScheduleDone(Exception):
    pass


class EnergyTable:
    """w(B) = int_0^B H_eff(s) ds, H_eff = B/(mu0 mu_r(B)) (solver's law)."""

    def __init__(self, curve, mu_r_fn, mu0, bmax):
        import numpy as np
        bs = np.array([pt[1] for pt in curve], float)
        grid = np.unique(np.concatenate([np.linspace(0.0, bmax, 400001),
                                         bs[(bs > 0) & (bs < bmax)]]))
        mur = np.maximum(mu_r_fn(curve, grid), 1.0)
        h = np.where(grid > 0, grid / (mu0 * mur), 0.0)
        w = np.concatenate([[0.0], np.cumsum(0.5 * (h[1:] + h[:-1]) * np.diff(grid))])
        self.grid, self.h, self.w, self.bmax = grid, h, w, bmax

    def __call__(self, b):
        import numpy as np
        j = np.clip(np.searchsorted(self.grid, b) - 1, 0, self.grid.size - 2)
        b0 = self.grid[j]; h0 = self.h[j]
        slope = (self.h[j + 1] - h0) / (self.grid[j + 1] - b0)
        db = b - b0
        return self.w[j] + h0 * db + 0.5 * slope * db * db


def _solver_frame():
    f = sys._getframe(2)
    while f is not None:
        if "_p2" in f.f_locals and "k" in f.f_locals and "_ftq2" in f.f_locals:
            return f.f_locals
        f = f.f_back
    raise RuntimeError("solver frame not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--centres", required=True, help="comma list of slip-node indices")
    ap.add_argument("--ring", type=int, default=0)
    ap.add_argument("--noload", action="store_true")
    ap.add_argument("--settle", type=int, default=36)
    ap.add_argument("--gl", type=float, default=None)
    ap.add_argument("--mesh-scale", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.ring:
        os.environ["SB_SLIP_PER_PERIOD"] = str(int(args.ring))
    os.environ["SB_P2_VIRTUAL_WORK"] = "1"
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kw, info = setup(args.inp, args.machine, args.duty)
    kw = scale_mesh(kw, args.mesh_scale)
    if args.gl is not None:
        kw["gap_layers"] = float(args.gl)
    import numpy as np
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim.simulation import field_ops as fo

    centres = [int(c) for c in args.centres.split(",")]
    sched = []
    for j, c in enumerate(centres):
        for dd in (-2, -1, 0, 1, 2):
            sched.append((c, dd, j))
    state = {"k0": None, "rows": [], "tables": {}, "t0": time.perf_counter()}

    class RV:
        @staticmethod
        def active():
            return not bool(getattr(fs._DAXIS_TLS, "calibrating", False))

        @staticmethod
        def centre_theta(k, spacing, k0):
            if state["k0"] is None:
                state["k0"] = int(k0)
            j = k - state["k0"]
            if j >= len(sched):
                raise ScheduleDone()
            return sched[j][0] * spacing

        @staticmethod
        def adjust(k, Ist, m_shift):
            c, dd, _ = sched[k - state["k0"]]
            if args.noload:
                Ist = {"A": 0.0, "B": 0.0, "C": 0.0}
            return Ist, int(m_shift) + int(dd)

    src = inspect.getsource(fs.fem_transient_sliding_band)
    a1 = "        m_shift = int(round(theta / spacing))\n"
    a2 = "            Ist = _src.mean_over(_fb)\n"
    for a in (a1, a2):
        if src.count(a) != 1:
            raise RuntimeError("injection anchor changed: %r" % a)
    src = src.replace(a1, (
        "        if k >= int(_vskip) + int(_dmskip) and _RVX is not None and _RVX.active():\n"
        "            theta = _RVX.centre_theta(k, spacing, int(_vskip) + int(_dmskip))\n" + a1))
    src = src.replace(a2, a2 + (
        "            if k >= int(_vskip) + int(_dmskip) and _RVX is not None and _RVX.active():\n"
        "                Ist, m_shift = _RVX.adjust(k, Ist, m_shift)\n"
        "                theta_eff = m_shift * spacing\n"
        "                _dm_ratchet = False\n"))
    fs.__dict__["_RVX"] = RV
    exec(compile(src, inspect.getfile(fs), "exec"), fs.__dict__)

    orig_factory = fs._prepare_arkkio_torque_p2

    def factory(mesh, basis, *a, **k):
        torque = orig_factory(mesh, basis, *a, **k)
        if not RV.active():
            return torque

        def observe(A):
            val = float(torque(A))
            fr = _solver_frame()
            kk = int(fr["k"])
            if state["k0"] is None or kk < state["k0"]:
                return val
            j = kk - state["k0"]
            p2 = fr["_p2"]
            f = np.asarray(fr["f"], float)
            A = np.asarray(A, float)
            e_lin = 0.5 * float(A @ (p2.K_const @ A))
            e_sat = 0.0
            for (_sb2, _sb02, _ids2, _c2) in p2.sat_sub:
                gA = fo._grad_at_quad(_sb2, A)
                Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
                tab = state["tables"].get(id(_c2))
                if tab is None or float(Bm.max()) > tab.bmax:
                    tab = EnergyTable(_c2, fo._mu_r_from_bh_vec, fo.MU0,
                                      max(4.0, 1.5 * float(Bm.max())))
                    state["tables"][id(_c2)] = tab
                e_sat += float(np.sum(tab(Bm) * _sb2.dx))
            ftq = fr["_ftq2"]
            t0c = time.perf_counter()
            tr = float(ftq.coulomb["rotor_side"](A)) if ftq.coulomb else float("nan")
            ts = float(ftq.coulomb["stator_side"](A)) if ftq.coulomb else float("nan")
            tc = time.perf_counter() - t0c
            vw = fr.get("_vw_torque")
            c, dd, jc = sched[j]
            Ist = fr["Ist"]
            state["rows"].append(dict(
                j=j, centre=c, offset=dd, centre_idx=jc,
                m_shift=int(fr["m_shift"]), iA=float(Ist["A"]), iB=float(Ist["B"]),
                iC=float(Ist["C"]), phi=e_lin + e_sat - float(f @ A),
                t_maxwell=val * int(fr["NS"]), t_coul_r=tr, t_coul_s=ts,
                t_vw_diag=(float("nan") if vw is None else float(vw)),
                newton_ok=bool(fr["_newton_ok"]), res=float(fr["_res"]), coul_s=tc))
            if j == 0:
                state.update(NS=int(fr["NS"]), L=float(fr["p"].stack_length),
                             spacing_deg=float(fr["spacing"]),
                             pole_pairs=int(fr["pole_pairs"]), Nring=int(fr["Nring"]),
                             n_elem=int(mesh.t.shape[1]), n_dof=int(basis.N),
                             layers=ftq.layers_info, coul_reason=ftq.unavailable_reason)
            return val
        return observe

    fs._prepare_arkkio_torque_p2 = factory
    settle = int(args.settle)
    n_per = int(math.ceil(len(sched) / float(settle))) + 1
    kw.update(n_steps_per_period=settle, n_periods=float(n_per),
              sampling_purpose="internal_probe", rotor_eddy=False, eddy=False, demag=False)
    t0 = time.perf_counter()
    finished = False
    try:
        fs.em_transient_eval(**kw)
    except ScheduleDone:
        finished = True
    wall = time.perf_counter() - t0
    rows = state["rows"]
    NS, L = state["NS"], state["L"]
    h = math.radians(state["spacing_deg"])
    res = []
    for jc, c in enumerate(centres):
        R = {r["offset"]: r for r in rows if r["centre_idx"] == jc}
        if set(R) != {-2, -1, 0, 1, 2}:
            continue
        W = {d: -NS * L * R[d]["phi"] for d in R}
        t1 = (W[1] - W[-1]) / (2 * h)
        t2 = (W[2] - W[-2]) / (4 * h)
        r0 = R[0]
        res.append(dict(centre=c, T_fd_richardson=(4 * t1 - t2) / 3.0, T_fd_1=t1,
                        T_maxwell=r0["t_maxwell"], T_coul_rotor=r0["t_coul_r"],
                        T_coul_stator=r0["t_coul_s"],
                        T_coul=0.5 * (r0["t_coul_r"] + r0["t_coul_s"]),
                        T_mortar=r0["t_vw_diag"], newton_ok=all(x["newton_ok"] for x in R.values()),
                        res_max=max(x["res"] for x in R.values())))
    meta = dict(machine=args.machine, duty=args.duty, info=info, noload=args.noload,
                ring=args.ring, centres=centres, finished=finished, wall_s=wall,
                n_frames=len(rows), coul_s_per_frame=(float(np.mean([r["coul_s"] for r in rows]))
                                                      if rows else None),
                **{k: state.get(k) for k in ("NS", "L", "spacing_deg", "pole_pairs", "Nring",
                                             "n_elem", "n_dof", "layers", "coul_reason")},
                results=res, rows=rows)
    with open(args.out + ".json", "w") as f:
        json.dump(meta, f, default=str)
    for r in res:
        print("c=%4d FD=%.6f coul=%.6f (r %.6f s %.6f) mx=%.6f mortar=%.6f" % (
            r["centre"], r["T_fd_richardson"], r["T_coul"], r["T_coul_rotor"],
            r["T_coul_stator"], r["T_maxwell"], r["T_mortar"]))
    print(json.dumps({"machine": args.machine, "finished": finished, "wall_s": wall,
                      "n": len(res)}))


if __name__ == "__main__":
    main()
