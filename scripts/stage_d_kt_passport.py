"""Stage D — the loaded 3D torque factor k_T for THIS motor.

Every k-factor the project quotes was measured on the 40 mm 12s/14p machine.
The one being designed is the 150 mm 24s/28p CIANO28, and an end-effect factor
is a property of a machine's proportions, not a constant — a 35 mm stack on a
150 mm bore is a different aspect ratio from a 12 mm stack on 40 mm, and there
is no honest way to carry 0.9795 across.

What this measures, and how:

  T = dW'/dtheta at CONSTANT CURRENT — the co-energy derivative, central
  differenced over the sliding band's own legal rotor angles so the two states
  sit on bit-identical meshes.  Maxwell stress appears nowhere.  The 2D
  DENOMINATOR is computed the same way on the same cross-section the 3D tets
  are extruded from (band2d), driven by the same winding field through the same
  functional, so the window mean, the frozen-current torque-angle average and
  the choice of functional cancel instead of being corrected for.  That is what
  turned k_T on the 40 mm from a 3.5 %-wide bracket into 0.97947 +/- 0.31 %.

COST IS THE POINT OF THE --probe MODE.  One converged nonlinear position on the
40 mm took 2660 s at 162 k edge dofs.  This machine is bigger.  So `--probe`
solves the CENTRE position only, reports what it cost and what the full set will
cost, and stops — a quote measured on this machine rather than extrapolated from
another one.

  python scripts/stage_d_kt_passport.py --probe          # one position, a quote
  python scripts/stage_d_kt_passport.py --shifts -2,-1,0,1,2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _geo_fingerprint(geo: dict) -> str:
    import hashlib
    return hashlib.md5(json.dumps(geo, sort_keys=True, default=str).encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="solve the centre position only and quote the rest")
    ap.add_argument("--mesh-only", action="store_true",
                    help="build the banded cross-section and the 3D mesh, report "
                         "their size, and stop — the cheapest thing that can tell "
                         "you what a solve on this machine will cost")
    ap.add_argument("--shifts", default="-2,-1,0,1,2")
    ap.add_argument("--n-ring", type=int, default=336)
    ap.add_argument("--box-factor", type=float, default=2.5)
    ap.add_argument("--h-gap", type=float, default=0.55)
    ap.add_argument("--h-solid", type=float, default=1.7)
    ap.add_argument("--tol", type=float, default=3e-3)
    ap.add_argument("--max-iter", type=int, default=70)
    ap.add_argument("--out", default=str(_ROOT / "config" / "stage_d_kt.json"))
    args = ap.parse_args()

    import numpy as np  # noqa: F401
    from motor_ai_sim.config import get_config
    from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
    from motor_ai_sim.simulation.static3d import band, band2d, torque3d, loaded
    from motor_ai_sim.simulation.static3d.winding3d import build_winding_T, phase_currents

    cfg = get_config()
    geo = dict(cfg.get("geometry", {}))
    sim = dict(cfg.get("simulation", {}))
    fp = _geo_fingerprint(geo)

    section = load_motor_section()
    print("machine: %d slots / %d poles, OD %.1f mm, stack %.1f mm, sector %.1f deg"
          % (section.num_slots, section.num_poles, 2 * section.r_stator_out_mm,
             section.stack_mm, section.sector_deg), flush=True)

    # The operating point is the one the Simulation tab is set to — the whole
    # value of a per-motor passport is that it belongs to the design AND the
    # point the user actually runs.
    I_rms = float(sim.get("current_a") or sim.get("max_current") or 0.0)
    gamma = float(sim.get("gamma_deg") or sim.get("phase_offset_deg") or 0.0)
    conn = str(sim.get("connection") or "")
    # THE q-AXIS IS NOT GUESSABLE.  phase_currents refuses without it, and it is
    # right to: gamma is measured from the q-axis, the q-axis comes from the
    # per-machine calibration, and a 3D leg placed on a different axis than the
    # 2D leg would compare two operating points, not two models.  Take the SAME
    # calibration the 2D solver uses, for THIS cross-section — a neighbouring
    # geometry's cached angle is exactly what that cache key exists to refuse.
    from motor_ai_sim.simulation.geometry_2d import params_from_config
    from motor_ai_sim.simulation.fem_solver_2d import _resolve_daxis_shift
    _p = params_from_config()
    _wind = dict(cfg.get("winding", {}))
    daxis = float(_resolve_daxis_shift(_p, geo, _wind,
                                       int(section.num_poles) // 2, None,
                                       int(section.n_sectors)))
    print("d-axis for this cross-section: DAXIS = %.4f deg" % daxis, flush=True)
    I_ph, exc_diag = phase_currents(section, i_phase_rms=I_rms, gamma_deg=gamma,
                                    connection=conn or None, daxis_deg=daxis)
    print("operating point: %.1f A rms, gamma %.1f deg, connection %s -> I_ph %s"
          % (I_rms, gamma, conn or "(config)",
             {k: round(v, 2) for k, v in I_ph.items()}), flush=True)

    t0 = time.perf_counter()
    banded = band.build_banded_section(section, n_ring=args.n_ring,
                                       box_factor=args.box_factor,
                                       h_gap=args.h_gap, h_solid=args.h_solid,
                                       verbose=True)
    print("banded cross-section: %.0f s" % (time.perf_counter() - t0), flush=True)

    if args.mesh_only:
        sect = banded.sect
        n_tri = int(getattr(sect, "t", getattr(sect, "tri", [])).shape[-1])             if hasattr(sect, "t") or hasattr(sect, "tri") else -1
        print("banded cross-section: %d triangles" % n_tri, flush=True)
        from motor_ai_sim.simulation.static3d.motor_mesh import build_motor_mesh
        t = time.perf_counter()
        tm, _ = build_motor_mesh(section, sect=sect, n_stack=5, n_cap=6,
                                 h_ew_mm=(0.5 * float(section.geo["tooth_width"])
                                          + 0.5 * float(section.geo["wire_width"])),
                                 n_ew=4)
        import numpy as _np
        print("3D mesh: %d tets, %d nodes, built in %.0f s"
              % (_np.asarray(tm.mesh.t).shape[1], _np.asarray(tm.mesh.p).shape[1],
                 time.perf_counter() - t), flush=True)
        print("(40 mm reference: 162 k EDGE dofs, 2660 s per converged position)",
              flush=True)
        return 0

    wt = build_winding_T(section, dict(I_ph))
    model = torque3d.BandedModel(section=section, banded=banded, I_ph=dict(I_ph),
                                 winding_field=wt, verbose=True)

    shifts = [int(x) for x in args.shifts.split(",") if x.strip()]
    if args.probe:
        shifts = [0]

    t1 = time.perf_counter()
    curve = torque3d.torque_from_energy_curve(model, shifts, tol=args.tol,
                                              max_iter=args.max_iter)
    wall3d = time.perf_counter() - t1
    per_pos = wall3d / max(len(shifts), 1)

    out = {
        "version": "static3d-stageD-kT-1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "geometry_fingerprint": fp,
        "machine": {"num_slots": section.num_slots, "num_poles": section.num_poles,
                    "stator_od_mm": 2 * section.r_stator_out_mm,
                    "stack_mm": section.stack_mm, "sector_deg": section.sector_deg},
        "operating_point": {"I_phase_rms_A": I_rms, "gamma_deg": gamma,
                            "connection": conn, "daxis_deg": daxis, "I_ph": I_ph,
                            "excitation": exc_diag},
        "mesh": {"n_ring": args.n_ring, "box_factor": args.box_factor,
                 "h_gap": args.h_gap, "h_solid": args.h_solid},
        "curve_3d": curve,
        "wall_s_3d": round(wall3d, 1),
        "s_per_position": round(per_pos, 1),
    }

    def _write(doc):
        Path(args.out).write_text(json.dumps(doc, indent=1, default=float),
                                  encoding="utf-8")

    # ── ON DISK BEFORE ANY ARITHMETIC ────────────────────────────────────────
    # Five converged nonlinear positions are an hour of solving; the k_T that
    # follows is two divisions.  The first version of this script did the
    # divisions first and wrote afterwards — and a KeyError in them (the 3D leg
    # calls its column T_Nm, the 2D leg calls it T_coenergy_Nm) destroyed the
    # whole hour.  What was expensive to compute is written down the instant it
    # exists; everything cheap after it is allowed to fail.
    _write(out)
    print("3D curve written to %s (%d positions, %.0f s)"
          % (args.out, len(shifts), wall3d), flush=True)

    if args.probe:
        full = [-2, -1, 0, 1, 2]
        out["quote"] = {
            "positions_for_k_T": len(full),
            "already_solved": 1,
            "s_per_position_measured": round(per_pos, 1),
            "estimate_s_remaining": round(per_pos * (len(full) - 1), 1),
            "reading": ("one position took %.0f s on THIS machine; the four more "
                        "a central difference needs are about %.1f h, warm-started "
                        "(the 40 mm's positions warm-started ~2x cheaper than the "
                        "first)." % (per_pos, per_pos * (len(full) - 1) / 3600.0)),
        }
        print("\n" + out["quote"]["reading"], flush=True)
    else:
      try:
        # the MATCHED 2D leg — same cross-section, same winding, same functional
        t2 = time.perf_counter()
        m2 = band2d.Banded2D(section=section, banded=banded, element_order=1,
                             I_ph=dict(I_ph), winding=wt, nu_pointwise=True)
        units = loaded.unit_phase_windings(section)
        c2 = band2d.co_energy_curve(m2, shifts, units=units, tol=3e-4, max_iter=120)
        rows2 = band2d.central_differences(c2, shifts.index(0))
        wall2d = time.perf_counter() - t2
        rows3 = torque3d.delta_sweep(curve, shifts.index(0))
        out["curve_2d"] = c2
        out["wall_s_2d"] = round(wall2d, 1)
        out["rows_3d"] = rows3
        out["rows_2d"] = rows2
        # The two legs name one quantity differently: torque3d.delta_sweep
        # returns T_Nm, band2d.central_differences returns T_coenergy_Nm.  Try
        # both rather than assume — assuming is what cost the first run.
        def _T(r):
            for k in ("T_Nm", "T_coenergy_Nm"):
                if k in r:
                    return float(r[k])
            raise KeyError("no torque column in %s" % sorted(r))
        by_dm3 = {r["dm_total"]: _T(r) for r in rows3 if "dm_total" in r}
        by_dm2 = {r["dm_total"]: _T(r) for r in rows2 if "dm_total" in r}
        kt = {dm: (by_dm3[dm] / by_dm2[dm]) for dm in sorted(set(by_dm3) & set(by_dm2))
              if by_dm2.get(dm)}
        out["k_T_by_dm"] = kt
        if kt:
            dm_q = min(kt)
            spread = (max(kt.values()) - min(kt.values())) / min(kt.values())
            out["k_T"] = kt[dm_q]
            out["k_T_quoted_dm_total"] = dm_q
            out["k_T_spread_pct"] = 100 * spread
            print("\nk_T = %.5f at dm_total = %d (spread over dm: %.2f %%)"
                  % (kt[dm_q], dm_q, 100 * spread), flush=True)
      except Exception as _e:          # noqa: BLE001
        out["k_T_error"] = "%s: %s" % (type(_e).__name__, _e)
        print("\nthe 2D leg / k_T step FAILED: %s\nthe 3D energies are already in "
              "%s — k_T can be finished from them without re-solving."
              % (_e, args.out), flush=True)

    _write(out)
    print("wrote %s" % args.out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
