"""Independent torque-ripple reference: frozen-current co-energy virtual work.

Study 2026-09-30 (docs/TORQUE_RIPPLE_VALIDATION_2026-09-30.md).  Sandbox only;
never touches the live API or a workspace.  Solver-direct: the production P2
sliding-band transient (magnetostatic, imposed current) is run with an
instrumented frame schedule.  Nothing in ``src/`` is edited; the frame loop is
patched in memory (source transform of ``fem_transient_sliding_band``, the same
technique as scratchpad/torque_frozen_current_probe_20260923.py).

For every CENTRE rotor position c (slip-ring node index; one electrical period
= R nodes) the frame is solved at the trajectory current i(c).  For a subset
of centres the SAME current vector i(c) is also imposed with the rotor moved
by d = -2, -1, +1, +2 slip nodes (frozen current).  At every solved frame the
discrete magnetostatic functional that the pointwise Newton actually
minimises is evaluated:

    Phi(A) = 1/2 A.K_const.A + sum_sat sum_q w_c(|B_q|) dx_q - f.A,
    w_c(B) = int_0^B H_c(s) ds   (H_c = the solver's own mu_r(B) law)

and the machine co-energy is W' = -N_s L Phi.  Torque references:

  (A) frozen-current virtual work, 4th-order Richardson of the central
      differences at +-1 and +-2 nodes:  T = dW'/dtheta_m at fixed i;
  (B) trajectory energy balance at every centre (no extra solve):
      T = dW'/dtheta_m (along the path, spectral) - n_par sum_p psi_p di_p/dtheta_m.

Raw Maxwell (Arkkio) torque, the flux linkages, currents and the existing
L2-mortar virtual-work diagnostic (SB_P2_VIRTUAL_WORK=1) are recorded at the
same frames.  Output: one NPZ with every frame + one JSON summary of the run.

usage: python ripple_ref.py --inp /work/in --machine d40 --duty rated
          --ring 432 --stride 1 --vw-every 4 --out /work/out/x
          [--gl 1] [--noload] [--demag] [--settle-steps 144] [--mesh-scale 1]
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import sys
import time
from pathlib import Path

import yaml

MACHINES = {
    "d40": ("d40", "L12"),
    "l13": ("l13", "L13"),
    "l155": ("l155", "L155 motor"),
}
DUTY_NAMES = {("d40", "rated"): "rated", ("d40", "peak"): "peak",
              ("l13", "rated"): "rated", ("l155", "rated"): "rated 1x9 mm"}
ABSENT_MEANS = {"sleeve_thickness": 0.0, "wire_parallel": 1, "wire_split": 1}
ABSENT_MATERIALS = {"slot_insulation": "Nomex", "wire_insulation": "polyimide"}
OFFSETS = (-2, -1, 1, 2)


class ScheduleDone(Exception):
    """Raised from inside the frame loop after the last scheduled frame."""


def compose(inp, machine, duty_key):
    sub, cfg = MACHINES[machine]
    d = yaml.safe_load(open(os.path.join(inp, sub, "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(inp, sub, cfg + ".yaml"), encoding="utf-8"))
    geo = dict(d.get("geometry") or {})
    geo.update({k: v for k, v in (y.get("geometry_overrides") or {}).items()
                if v is not None})
    for k, v in ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    ns = float(geo["num_seg"])
    pps, sps = float(geo["num_poles_per_segment"]), float(geo["num_slots_per_segment"])
    geo["num_poles"] = int(round(ns * pps)); geo["angle_pole"] = 360.0 / (ns * pps)
    geo["num_slots"] = int(round(ns * sps)); geo["angle_slot"] = 360.0 / (ns * sps)
    duty = next(x for x in y["duties"] if x["name"] == DUTY_NAMES[(machine, duty_key)])
    mats = {k: v for k, v in (y.get("materials") or {}).items() if v}
    for k, v in (duty.get("materials") or {}).items():
        if v:
            mats[k] = v
    for k, v in ABSENT_MATERIALS.items():
        if not mats.get(k):
            mats[k] = v
    st = dict(duty.get("mesh") or {})
    return d, y, geo, duty, mats, st


# ── the solver's own B-H law, integrated to an energy density ────────────────
class EnergyTable:
    """w(B) = int_0^B H_eff(s) ds for the H_eff(B) = B/(mu0 mu_r(B)) the
    pointwise Newton uses (mu_r from field_ops._mu_r_from_bh_vec, clamp >= 1).
    H_eff is piecewise linear in B, so a trapezoid on a grid that contains the
    curve's breakpoints plus a fine uniform grid is exact to O(h^2) at the
    single clamp crossing."""

    def __init__(self, curve, mu_r_fn, mu0, bmax):
        import numpy as np
        bs = np.array([pt[1] for pt in curve], float)
        grid = np.unique(np.concatenate([
            np.linspace(0.0, bmax, 400001), bs[(bs > 0) & (bs < bmax)]]))
        mur = np.maximum(mu_r_fn(curve, grid), 1.0)
        h = np.where(grid > 0, grid / (mu0 * mur), 0.0)
        w = np.concatenate([[0.0], np.cumsum(0.5 * (h[1:] + h[:-1]) * np.diff(grid))])
        self.grid, self.h, self.w, self.bmax = grid, h, w, bmax

    def __call__(self, b):
        import numpy as np
        if float(np.max(b)) > self.bmax:
            raise RuntimeError("B above the tabulated range")
        # exact for piecewise-linear H between grid points
        j = np.clip(np.searchsorted(self.grid, b) - 1, 0, self.grid.size - 2)
        b0 = self.grid[j]; h0 = self.h[j]
        slope = (self.h[j + 1] - h0) / (self.grid[j + 1] - b0)
        db = b - b0
        return self.w[j] + h0 * db + 0.5 * slope * db * db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True, choices=sorted(MACHINES))
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--ring", type=int, required=True,
                    help="slip-ring nodes per electrical period (SB_SLIP_PER_PERIOD)")
    ap.add_argument("--stride", type=int, default=1, help="centre spacing in nodes")
    ap.add_argument("--vw-every", type=int, default=0,
                    help="frozen-current +-1,+-2 neighbours for every q-th centre (0 = none)")
    ap.add_argument("--centres", type=str, default="",
                    help="optional explicit comma list of centre nodes (overrides stride)")
    ap.add_argument("--gl", type=float, default=None, help="gap layers (default: duty)")
    ap.add_argument("--noload", action="store_true")
    ap.add_argument("--demag", action="store_true")
    ap.add_argument("--settle-steps", type=int, default=0,
                    help="steps/period of the demag settling period (default: ring)")
    ap.add_argument("--mesh-scale", type=float, default=1.0)
    ap.add_argument("--current-check", action="store_true",
                    help="also solve +-1 %% current perturbations at 2 centres "
                         "(dW'/di = psi self-check)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.environ["SB_SLIP_PER_PERIOD"] = str(int(args.ring))
    os.environ.setdefault("SB_P2_VIRTUAL_WORK", "1")
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("ripple_ref")

    d, y, geo, duty, mats, st = compose(args.inp, args.machine, args.duty)
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base.setdefault("materials", {}).update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = {"shaft": "included"}
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta") or st.get("sim.starDelta") or "star")
    base.setdefault("simulation", {}).update(
        {"rpm": float(duty["rpm"]), "gamma_deg": float(duty["gamma_deg"]), "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)

    import numpy as np
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim.simulation import field_ops as fo

    R = int(args.ring)
    if args.centres:
        centres = [int(c) for c in args.centres.split(",")]
    else:
        centres = list(range(0, R, int(args.stride)))
    sched = []                      # (centre node, offset, centre index)
    for j, c in enumerate(centres):
        want_nb = (args.vw_every and (j % int(args.vw_every) == 0))
        if want_nb:
            for dd in (-2, -1):
                sched.append((c, dd, j))
        sched.append((c, 0, j))
        if want_nb:
            for dd in (1, 2):
                sched.append((c, dd, j))
    cur_scale = [1.0] * len(sched)
    if args.current_check and not args.noload:
        for jc in (0, len(centres) // 3):
            c = centres[jc]
            for sc in (1.01, 0.99):
                sched.append((c, 0, jc)); cur_scale.append(sc)
    n_sched = len(sched)

    # ── install the instrumentation ──────────────────────────────────────────
    state = {"k0": None, "rows": [], "tables": {}, "mesh_hash": None,
             "n_sched": n_sched, "t0": time.perf_counter()}

    class RV:
        @staticmethod
        def active():
            return not bool(getattr(fs._DAXIS_TLS, "calibrating", False))

        @staticmethod
        def centre_theta(k, spacing, k0):
            if state["k0"] is None:
                state["k0"] = int(k0)
            j = k - state["k0"]
            if j >= n_sched:
                raise ScheduleDone()
            return sched[j][0] * spacing

        @staticmethod
        def adjust(k, Ist, m_shift):
            j = k - state["k0"]
            c, dd, _ = sched[j]
            s = cur_scale[j]
            if args.noload:
                Ist = {"A": 0.0, "B": 0.0, "C": 0.0}
            elif s != 1.0:
                Ist = {ph: float(v) * s for ph, v in Ist.items()}
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

    def factory(mesh, basis, *a, **kw):
        torque = orig_factory(mesh, basis, *a, **kw)
        if not RV.active():
            return torque
        state["mesh_hash"] = hashlib.sha256(
            np.ascontiguousarray(mesh.p).tobytes()
            + np.ascontiguousarray(mesh.t).tobytes()).hexdigest()
        state["n_dof"] = int(basis.N)

        def observe(A):
            fr = sys._getframe(1).f_locals
            val = float(torque(A))
            k = int(fr["k"])
            if state["k0"] is None or k < state["k0"]:
                return val          # demag settling frame: not ours
            j = k - state["k0"]
            p2 = fr["_p2"]
            f = np.asarray(fr["f"], float)
            A = np.asarray(A, float)
            e_lin = 0.5 * float(A @ (p2.K_const @ A))
            e_sat = 0.0
            bmax_seen = 0.0
            for (_sb2, _sb02, _ids2, _c2) in p2.sat_sub:
                gA = fo._grad_at_quad(_sb2, A)
                Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
                bmax_seen = max(bmax_seen, float(Bm.max()))
                key = id(_c2)
                tab = state["tables"].get(key)
                if tab is None or float(Bm.max()) > tab.bmax:
                    tab = EnergyTable(_c2, fo._mu_r_from_bh_vec, fo.MU0,
                                      max(4.0, 1.5 * float(Bm.max())))
                    state["tables"][key] = tab
                e_sat += float(np.sum(tab(Bm) * _sb2.dx))
            e_src = float(f @ A)
            Ist = fr["Ist"]
            psi = fr["_psi2"](A)
            vw = fr.get("_vw_torque")
            c, dd, jc = sched[j]
            state["rows"].append(dict(
                j=j, k=k, centre=c, offset=dd, centre_idx=jc, cur_scale=cur_scale[j],
                m_shift=int(fr["m_shift"]), theta_eff_deg=float(fr["theta_eff"]),
                iA=float(Ist["A"]), iB=float(Ist["B"]), iC=float(Ist["C"]),
                psiA=float(psi[0]), psiB=float(psi[1]), psiC=float(psi[2]),
                e_lin=e_lin, e_sat=e_sat, e_src=e_src,
                phi=e_lin + e_sat - e_src,
                t_maxwell_sector=val,
                t_vw_diag=(None if vw is None else float(vw)),
                newton_ok=bool(fr["_newton_ok"]), res=float(fr["_res"]),
                nit=int(fr["_nit"]), bmax=bmax_seen))
            if j == 0:
                state["NS"] = int(fr["NS"])
                state["L"] = float(fr["p"].stack_length)
                state["spacing_deg"] = float(fr["spacing"])
                state["pole_pairs"] = int(fr["pole_pairs"])
                state["n_parallel"] = int(fr["n_parallel"])
                state["Nring"] = int(fr["Nring"])
                state["n_elem"] = int(mesh.t.shape[1])
            if (j + 1) % 50 == 0:
                log.info("RV %d/%d frames, %.1f s", j + 1, n_sched,
                         time.perf_counter() - state["t0"])
            return val
        return observe

    fs._prepare_arkkio_torque_p2 = factory

    # ── the call (magnetostatic, imposed current) ────────────────────────────
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    s = float(args.mesh_scale)
    cm = {}
    for k_, v in dict(st.get("mesh.componentMesh") or {}).items():
        cm[k_] = float(v) if "rel" in k_ else float(v) * s
    mt = st.get("sim.magnetTempC")
    settle = int(args.settle_steps or R)
    n_per = int(math.ceil(n_sched / float(settle))) + 1
    gl = float(args.gl if args.gl is not None else st.get("mesh.gapLayers", 1))
    kw = dict(
        n_steps_per_period=settle, n_periods=float(n_per),
        sampling_purpose="internal_probe",
        gamma_deg=float(duty["gamma_deg"]), I_phase_rms=I_wind, rpm=float(duty["rpm"]),
        n_parallel=int(wnd.get("n_parallel") or 1), connection=wnd.get("connection"),
        star_delta=sd,
        daxis_deg=(float(d["daxis_deg"]) if d.get("daxis_deg") is not None else None),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)) * s,
        min_size_mm=float(st.get("mesh.minSize", 0.3)) * s,
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=gl,
        n_sectors=int(st.get("mesh.nSectors", 2)),
        stator_fillet_mm=0.0,
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=False, demag=bool(args.demag),
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=cm, geo_override=dict(geo),
        drive="current", element_order=2, eddy=False)
    t0 = time.perf_counter()
    finished = False
    try:
        fs.em_transient_eval(**kw)
    except ScheduleDone:
        finished = True
    wall = time.perf_counter() - t0
    rows = state["rows"]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = sorted(rows[0].keys()) if rows else []
    np.savez_compressed(str(out) + ".npz", **{
        c: np.asarray([(np.nan if r[c] is None else r[c]) for r in rows], float)
        for c in cols})
    meta = dict(machine=args.machine, duty=args.duty, duty_name=DUTY_NAMES[(args.machine, args.duty)],
                ring=R, stride=args.stride, vw_every=args.vw_every, noload=args.noload,
                demag=args.demag, gap_layers=gl, mesh_scale=s, settle_steps=settle,
                n_sched=n_sched, n_rows=len(rows), finished=finished, wall_s=wall,
                I_winding_rms=I_wind, star_delta=sd, gamma_deg=float(duty["gamma_deg"]),
                rpm=float(duty["rpm"]), magnet_temp_c=kw["magnet_temp_c"],
                coil_temp_c=kw["coil_temp_c"], mesh_size_mm=kw["mesh_size_mm"],
                min_size_mm=kw["min_size_mm"], n_sectors=kw["n_sectors"],
                component_mesh_mm=cm, mesh_hash=state.get("mesh_hash"),
                n_dof=state.get("n_dof"),
                NS=state.get("NS"), L=state.get("L"), spacing_deg=state.get("spacing_deg"),
                pole_pairs=state.get("pole_pairs"), n_parallel=state.get("n_parallel"),
                Nring=state.get("Nring"), n_elem=state.get("n_elem"),
                newton_all_ok=all(r["newton_ok"] for r in rows),
                res_max=max((r["res"] for r in rows), default=None),
                materials=mats)
    Path(str(out) + ".json").write_text(json.dumps(meta, indent=1, default=str))
    print(json.dumps({k: meta[k] for k in ("machine", "duty", "ring", "n_rows",
                                           "finished", "wall_s", "newton_all_ok")}))


if __name__ == "__main__":
    main()
