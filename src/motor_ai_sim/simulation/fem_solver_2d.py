"""2-D finite-element magnetostatics solver — pure Python (scikit-fem + gmsh).

Solves   ∇·(ν ∇A_z) = -J_z + ∇·(M × ẑ)    on a triangle mesh of the motor
cross-section.  Used as a real-FEM alternative to the analytical Green's
function solver in routes/simulation.py.

Domain-specific data (matches the CadQuery polygon classes):
    air-gap        ν = 1/μ₀                     (free space)
    stator steel   ν = 1/(μ₀·5000)              (silicon steel, linear)
    rotor steel    ν = 1/(μ₀·5000)
    shaft          ν = 1/(μ₀·1000)              (aluminium-ish)
    magnets        ν = 1/(μ₀·1.05),  M = ±(Br/μ₀)·φ̂   (tangential, alternating)
    coils          ν = 1/μ₀,  J_z = direction · I_phase · n_wires / area

Boundary: A_z = 0 on the outer stator circle.

The magnet constitutive law
---------------------------
    B = μ₀·μ_rec·H + μ₀·M ,     remanence  Br = μ₀·M ,   M = Br/μ₀

That is what ``build_materials`` stores in ``Mx``/``My``, what this file's own
demag pass and ``simulation/demag.py`` read the full-strength remanence back as
(``Br0 = μ₀·|M|``), and what the 3D solver in ``simulation/static3d`` is handed.
Substituting it into curl H = J gives  H = ν·B − ν·μ₀·M = ν·B − M/μ_rec, so the
SOURCE this weak form integrates is M/μ_rec — the equivalent coercivity
H_c = Br/(μ₀·μ_rec) — NOT M.  Assembling M itself models a magnet of remanence
μ_rec·Br, i.e. 5 % strong for NdFeB.  It did, until the 3D end-effect work
measured a flat 4.71 % disagreement on an iron-free cross-section and
``tests/test_static3d_stage_a.py`` pinned the law against the closed-form field
of a transversely magnetised cylinder.  Every benchmark that had ever exercised
a magnet here ran at μ_r = 1, the one value where the two conventions agree.

Method: linear FE on a P1-triangle mesh; gmsh builds a conforming mesh from
the real CadQuery exterior+interior contours so the same geometry the
canvas displays is the geometry we solve on.

Time budget for one solve at the default mesh density (~25k triangles):
roughly 1-2 s on a modern CPU.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple, Optional

import numpy as np

# ── Lower layers ─────────────────────────────────────────────────────────────
# Domain tags / mesh flags and the whole geometry->mesh stage now live in their
# own modules.  Re-exported here rather than merely imported: routes.simulation,
# modules.mesh and geo_mesh all reach for these off fem_solver_2d, and moving a
# file should not break a caller.
from motor_ai_sim.simulation.sb_domains import (  # noqa: F401  (re-export)
    DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_COIL, DOM_COIL_BASE, DOM_MAG_BASE,
    DOM_MAG_N, DOM_MAG_S, DOM_OUTER, DOM_ROTOR, DOM_SHAFT, DOM_STATOR,
    _GMSH_LOCK, _N_SLIP, _SB_BAND_DELTA_FRAC, _SB_BELT, _SB_GEO_MESH,
    _SB_GEO_SECTOR, _SB_IRON_RESAMPLE, _SB_IRON_TEMPLATE, _SB_POLE_COPY_ROTOR,
    _SB_POLE_COPY_STATOR, _SB_ROT_PERIODICITY, _SB_STRUCTURED_GAP,
    _SB_STRUCTURED_STRIPS, _SG_EPS_OVERRIDE, _SG_M_TARGET,
)

# Physics primitives on a solved field (torque integrals, per-element B, B-H
# lookups, winding helpers).  Pure functions, no solver state — re-exported so
# existing imports off fem_solver_2d keep working.
from motor_ai_sim.simulation.demag import MagnetDemag as _MagnetDemag
from motor_ai_sim.simulation.drive import (
    Excitation as _Excitation,
    park as _park_dq,
    inverse_park as _ipark_dq,
    aitken_flux_anchor as _aitken_flux_anchor,
)
from motor_ai_sim.simulation.losses import (
    rotor_eddy_tags as _rotor_eddy_tags,
    rotor_mu_lookup as _rotor_mu_lookup,
    copper_ac_dims as _copper_ac_dims,
    proximity_loss_series as _proximity_loss_series,
    TWO_PI_SQ as _TWO_PI_SQ,
    central_difference as _central_difference,
    iron_loss_series as _iron_loss_series,
    loss_density_map as _loss_density_map,
    magnet_segmentation as _magnet_segmentation,
)
from motor_ai_sim.simulation.sb_postproc import (
    drop_settling_frames as _drop_settling_frames,
    eddy_settle_resid as _eddy_settle_resid,
    hybrid_torque as _hybrid_torque,
    torque_harmonics as _torque_harmonics,
)
from motor_ai_sim.simulation.moving_band import slip_ring_nodes as _slip_ring_nodes
from motor_ai_sim.simulation.field_ops import (  # noqa: F401  (re-export)
    MU0, RHO_CU_20, ALPHA_CU,
    _snap_steps_to_nodes, _build_magnet_bh_curve_payload, _b_from_bh_at_H,
    _mu_r_from_bh, _mu_r_from_bh_vec, _smooth_demag_H,
    _per_triangle_B, _triangle_areas, _maxwell_stress_torque,
    _arkkio_torque, _p2_B_at_quad, _arkkio_torque_p2,
    band_limit_torque, end_winding_factor_geom, copper_loss_W,
    coil_slot_index, coil_copper_areas, coil_copper_area_total_m2,
)

from motor_ai_sim.simulation.mesher import (  # noqa: F401  (re-export)
    _fillet_polygon, _decimate_ring_by_angle, _decimate_poly_by_angle,
    _simplify_polys, _add_background_air, _add_motion_band,
    _clip_polys_to_sector, _split_polys_for_sliding_band, _replicate_periodic_half,
    _build_sliding_band_meshes, _find_ring_nodes, _band_master_slave_pairing,
    _rotate_mesh_points, _structured_band_mesh, _structured_rect_mesh,
    _mesh_single_polygon, build_periodic_magnet_mesh, build_periodic_coil_mesh,
    _split_ring_pinches, _split_geom_pinches, _resample_ring_arcs,
    _weld_belt_into_half, _structured_gap_sm, _iron_arc_ring_occ,
    _build_structured_gap_cells, build_mesh_from_polygons, _weld_coincident_nodes,
    _read_cell_tags_by_dom, _pair_sector_cut_nodes, _apply_anti_periodic,
    _stitch_full_half,
)

log = logging.getLogger(__name__)


# MU0 / RHO_CU_20 / ALPHA_CU live in field_ops (imported below) — ONE definition,
# so a constant can never drift between the primitives and the solver that calls
# them.  Kept as module attributes here because most of this file reads them
# unqualified and every caller off fem_solver_2d expects them.

# Saturation-Picard honest stopping: worst (over saturable tags) RELATIVE L2
# fixed-point residual of the nu(|B|) update (measured BEFORE damping) below
# which a frame's nonlinear iteration is converged (two consecutive sweeps).
# Replaces the old fixed "14 iterations" recipe, which did not converge and left
# a 5-8 Nm p-p no-load torque floor (see PARITY_FINDINGS_band_mode.md).
_PIC_TOL = 1e-3

# The magnet Br convergence tolerance moved to simulation/demag.py with the rule
# that uses it (DEMAG_TOL) — one definition, next to the code it governs.

# Single source of truth for the d-axis phase offset: the electrical angle added
# to (rotor_angle·pole_pairs + γ) so that γ=0 lands on the q-axis.  MUST be
# identical across every solve path (transient currents, static field, eddy) —
# otherwise the field/torque would be at a different phase per path.
#
# = ideal 90° d→q rotation  +  the motor's rotor-d-axis-vs-phase-A GEOMETRIC
# offset.  That offset is TOPOLOGY-dependent (pole/slot/winding) and must be
# calibrated per motor: a hardcoded 90 left γ=0 ~18° off the q-axis for the
# 20-pole/24-slot motor, so torque peaked at γ≈+38° and "kept rising with γ"
# instead of peaking at a small-+ MTPA.
# Calibrated 2026-06-16 for the 20p/24s topology via a no-load (I=0) run:
# psi_A(θ) peaks at θ*=34.2° mech (342° el) ⇒ DAXIS = (90 − 342) ≡ 108° (mod 360).
# ...AND THAT CALIBRATION WAS ITSELF WRONG.  Re-measured 2026-07-31 on a solve
# that CONVERGED (ciano20_150_35, 24s/20p, structured gap, 24/24 frames, worst
# residual 8.9e-08): θ* = 32.999° mech, i.e. 33.0 = pole pitch 18° + slot pitch
# 15° exactly ⇒ DAXIS = 120.000°, not 108°.  The 1.2° error in θ* is 12° of
# electrical angle, so the constant below was ~12° off even for the ONE topology
# it was ever meant for.  Same root cause as the auto-calibration bug fixed in
# `_resolve_daxis_shift`: a no-load solve that had not converged.
# RECALIBRATE only if the pole/slot/winding TOPOLOGY changes (dimension sweeps
# don't move it): run fem_transient_sliding_band(I_phase_rms=0); θ* = rotor angle
# of max psi_A_Wb; DAXIS_SHIFT_DEG = (90 − θ*·pole_pairs) mod 360.  Converged
# values measured 2026-07-31, each on its own cross-section:
#     12s/14p  θ* = 30/7  mech ⇒ DAXIS =  60.000°   (three cross-sections,
#                                          59.9996 / 60.0107 / 60.0145)
#     24s/28p  θ* = 30/14 mech ⇒ DAXIS =  60.000°   (59.9468 / 59.9319)
#     24s/20p  θ* = 33    mech ⇒ DAXIS = 120.000°   (120.0096)
DAXIS_SHIFT_DEG = 108.0   # LEGACY CONSTANT, and wrong for EVERY topology
                          # including 20p/24s (see above: that one is 120°).  The
                          # transient AUTO-calibrates per machine
                          # (_resolve_daxis_shift) and RAISES if that fails — this
                          # value is NOT a fallback: it is ~48° wrong for 12s14p
                          # and 24s28p and ~12° wrong for 24s20p, and silently
                          # answering with it puts the whole run at a load angle
                          # the user never asked for.
                          # Still read by the legacy static field path below.

# ── Per-motor d-axis auto-calibration ────────────────────────────────────────
# The geometric rotor-d-axis-vs-phase-A offset is TOPOLOGY-dependent (pole/slot/
# winding), so a single hard-coded DAXIS_SHIFT_DEG is only right for ONE motor
# (it was calibrated for 20p/24s and was ~48° wrong for 14p/12s → the UI's γ was
# 48° off the true q-axis).  We now compute θ* (rotor angle of peak no-load ψ_A)
# from a cheap I=0 run and set DAXIS = (90 − θ*·pole_pairs) mod 360, so γ=0 is the
# TRUE q-axis and the γ the user enters equals the PHYSICAL current angle from the
# q-axis (identical to ANSYS's el_deg).  Cached per topology; θ* is invariant to
# dimension sweeps.
_DAXIS_CACHE: Dict[tuple, float] = {}
# RECURSION GUARD — PER THREAD, and a lock so two threads cannot calibrate at
# once.  It was a plain module global, which made it a guard against the wrong
# thing: the no-load calibration is itself a transient, so the flag has to
# suppress the d-axis lookup INSIDE that nested solve — but a process-wide flag
# suppresses it for every OTHER solve running at the same time too, and those
# get the legacy 108° constant instead of their own q-axis.
#
# Measured on the 200 mm 24s/28p with the panel open (its field view solves on
# its own): change wire_width 8.1 -> 8.0, and the runs after the calibration
# came back at daxis 108.0000 and T = 401.88 Nm against the correct 59.99 and
# 821.52 — the torque halves because γ sits 48° off the q-axis, and nothing on
# screen says so.  One user-visible symptom, two solves racing.
#
# Thread-local: only the thread that IS calibrating skips the lookup.  The lock:
# a second thread that wants the same machine waits and then reads the cache
# the first one filled, instead of starting a duplicate 39-second calibration.
_DAXIS_TLS = threading.local()
_DAXIS_LOCK = threading.RLock()

def _daxis_disk_path():
    """Shared on-disk DAXIS cache so sweep subprocesses (fresh interpreters, empty
    in-memory cache) don't each re-run the calibration → per-design timeout."""
    try:
        import os
        from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _cp
        return os.path.join(os.path.dirname(str(_cp)), ".daxis_cache.json")
    except Exception:
        return None

def _daxis_geo_fingerprint(geo) -> str:
    """md5 of the MERGED geometry this solve actually builds on.

    The same question `routes.simulation._geometry_fingerprint` answers ("is
    this still the same MOTOR?"), asked of the dict in hand rather than of the
    shared config: by the time the calibration runs, `geo` has already been
    through `merge_geo_override`, so it IS the machine — including a per-request
    `geo=` override that the process-global config knows nothing about.  A
    fingerprint taken off the live config would collapse every candidate of a
    sweep onto whatever the config happened to hold.
    """
    try:
        import hashlib as _hl, json as _jl
        return _hl.md5(_jl.dumps(dict(geo or {}), sort_keys=True,
                                 default=str).encode()).hexdigest()[:16]
    except Exception:
        return "nofp"


def _daxis_topology_key(p, geo, wind, geo_fp=None) -> tuple:
    """Cache key for the calibrated d-axis: topology AND cross-section.

    The topology tuple alone (poles/slots/layers/connection) was the key until
    it was caught sharing ONE entry between two different 12s14p cross-sections
    — a value measured on one machine was then handed to the other, and γ=0
    landed off that machine's q-axis with nothing on screen to say so.  θ* is
    ARGUED to be a symmetry property (invariant to dimensions), and the measured
    12s14p angles do sit on 60.0° across three cross-sections — but an argument
    is not a licence for a cache to answer for a machine it never measured, so
    the geometry fingerprint is part of the key and a new cross-section
    re-calibrates.
    """
    return (int(getattr(p, "num_poles", 0) or 0),
            int((geo or {}).get("num_slots") or 0),
            int((wind or {}).get("layers", 1) or 1),
            str((wind or {}).get("connection", "")),
            str(geo_fp or _daxis_geo_fingerprint(geo)))

def _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override, n_sectors,
                         progress_cb=None) -> float:
    """DAXIS (elec deg) that makes γ=0 the true q-axis for THIS machine.

    Computed from a no-load (I=0) run — ψ_A(θ) peaks at the d-axis, so
    DAXIS = (90 − θ*·pole_pairs) mod 360.  Recursion-guarded (the I=0
    calibration run has no current, so DAXIS is irrelevant inside it).

    Two things this used to get wrong, both of which put γ on the wrong axis
    with nothing on screen to say so, and both fixed here:
      * it accepted the ANSWER OF AN UNCONVERGED SOLVE (see the gate below);
      * it cached that answer under a key that named only the TOPOLOGY, so one
        cross-section's angle was served to a different machine with the same
        pole/slot/layer/connection counts (see `_daxis_topology_key`).
    """
    # Only the thread that is INSIDE its own calibration skips the lookup (the
    # no-load solve has no current, so its d-axis is irrelevant).  A solve on
    # another thread waits at the lock below and gets the real answer.
    if getattr(_DAXIS_TLS, "calibrating", False):
        return DAXIS_SHIFT_DEG
    _geo_fp = _daxis_geo_fingerprint(geo)
    key = _daxis_topology_key(p, geo, wind, _geo_fp)
    if key in _DAXIS_CACHE:
        return _DAXIS_CACHE[key]
    # ONE CALIBRATION AT A TIME, and the runner-up reads the answer instead of
    # repeating it.  Two solves that arrive together on a machine the cache has
    # not seen would otherwise each spend 39 s measuring the same angle.  The
    # lock is re-entrant, so the nested no-load solve (same thread) walks
    # straight through it.
    with _DAXIS_LOCK:
        if key in _DAXIS_CACHE:       # filled while this thread waited
            return _DAXIS_CACHE[key]
        return _calibrate_daxis(p, geo, wind, pole_pairs, geo_override,
                                n_sectors, key, _geo_fp, progress_cb)


def _calibrate_daxis(p, geo, wind, pole_pairs, geo_override, n_sectors,
                     key, _geo_fp, progress_cb=None) -> float:
    """The measurement itself — called with _DAXIS_LOCK held and the cache
    already checked twice (before the lock and after it)."""
    skey = "_".join(str(x) for x in key)
    _dp = _daxis_disk_path()
    if _dp:                           # shared disk cache (across sweep subprocesses)
        try:
            import os, json
            if os.path.exists(_dp):
                with open(_dp) as _f:
                    _v = json.load(_f).get(skey)
                if _v is not None:
                    _DAXIS_CACHE[key] = float(_v)
                    return float(_v)
        except Exception:
            pass
    daxis = None                      # None ⇒ calibration did not succeed
    _cal_err = None
    try:
        _DAXIS_TLS.calibrating = True
        # THE CALIBRATION SOLVE MUST CONVERGE.  It used to be run on the
        # cheapest mesh that would build — 2.5 mm elements, min 0.7 mm, ONE
        # layer across a 0.2 mm air gap, unstructured band — on the argument
        # that θ* is "a bulk geometric quantity".  Measured on the user's 40 mm
        # 12s14p (scratch cal_probe.py, variant A_current): Newton's line search
        # collapsed on ALL 24 frames, the damped-Picard fallback then hit its
        # 100-sweep cap on 19 of them, worst ν residual 2.8e-1 against a 1e-3
        # tolerance — and the θ* read off that garbage field gave DAXIS 47.45°
        # instead of 60.00°, i.e. γ=0 sat 12.7° off the q-axis on every solve
        # that inherited the cached value.  The sliver elements a 2.5 mm mesh
        # leaves across a 0.2 mm gap are what break the Newton tangent; the fix
        # is a mesh the solve can actually converge on, not a looser gate.
        #
        # These are the solver_trials protocol's mesh settings (the ones 15
        # machines were re-baselined on, all with gate a_nonlinear_converged
        # true): structured gap + iron template + geo mesh, the 1.4 mm·D/150
        # rule clamped to [0.5, 2.0], 2 layers per half-gap.  Same case,
        # variant P_ship: Newton converged on every frame, ZERO fallbacks,
        # worst residual ~1e-7 — and it is FASTER than the coarse run it
        # replaces (129 s vs 342 s), because 19 Newton iterations cost less
        # than 100 stalled Picard sweeps.
        #
        # These settings are deliberately FIXED rather than inherited from the
        # caller: the cache key names the machine, not the mesh, so a value
        # computed under one caller's mesh must not be handed to another's.
        # `connection` is likewise NOT forwarded even though it is in the key:
        # it only sets n_parallel, which scales ψ_A by a constant and cannot
        # move the ANGLE of its peak.  Measured, on the pre-fix cache: the same
        # 12s14p came out 60.0219 at 2S and 60.0168 at 2P — mesh noise, not a
        # winding effect.  It stays in the key because the key is a promise
        # about the machine, not because θ* depends on it.
        import math as _mth
        _cal_ns = _mth.gcd(int((geo or {}).get("num_slots") or 1),
                           int(getattr(p, "num_poles", 1) or 1)) or 1
        _cal_D = float((geo or {}).get("stator_diameter") or 0.0) or 40.0
        _cal_mesh = round(min(2.0, max(0.5, 1.4 * _cal_D / 150.0)), 2)
        # The bar says WHICH stage is running.  Its own frame count, its own
        # label; the caller's `total` belongs to the reported window and would
        # be a lie here.
        _CAL_FRAMES = 24
        def _cal_progress(_done, _total, _phase=None):
            if progress_cb is None:
                return
            try:
                progress_cb(int(_done), _CAL_FRAMES,
                            "calibrating the d-axis reference (no-load, "
                            "%d frames) — once per geometry" % _CAL_FRAMES)
            except TypeError:
                pass                      # a caller from before the phase arg
            except Exception:
                pass
        cal = em_transient_eval(
            progress_cb=_cal_progress,
            n_steps_per_period=_CAL_FRAMES, n_periods=1.0, gamma_deg=0.0,
            I_phase_rms=0.0, mesh_size_mm=_cal_mesh, min_size_mm=0.3,
            outer_air_factor=1.2, gap_layers=2.0,
            n_sectors=_cal_ns if _cal_ns >= 2 else -1,
            coil_temp_c=120.0, rotor_eddy=False,
            iron_template=True, structured_gap=True, geo_mesh=True,
            geo_override=geo_override, element_order=2)
        psi = np.asarray(cal.get("psi_A_Wb") or [], float)
        ang = np.asarray(cal.get("rotor_angle_deg") or [], float)
        # CONVERGENCE GATE.  ψ_A of an unconverged field is not a measurement of
        # anything, and the peak of it is not θ*.  The solver already reports,
        # per frame, whether the path that solved it met that path's tolerance
        # (Newton 1e-7 on the field residual, Picard _PIC_TOL2 on the ν fixed
        # point) — read it, and treat a MISSING flag as failure rather than as
        # consent.
        _cal_conv = bool(cal.get("picard_converged", False))
        _cal_unconv = list(cal.get("picard_unconverged_frames") or [])
        _cal_fb = len(cal.get("picard_fallback_frames") or [])
        if not _cal_conv:
            _cal_err = (
                f"the no-load calibration solve did NOT converge — "
                f"{len(_cal_unconv)} of {int(psi.size) or '?'} frames "
                f"{_cal_unconv} above tolerance {cal.get('picard_tol')}, worst "
                f"residual {cal.get('picard_resid_max')} "
                f"(mesh {_cal_mesh} mm, {_cal_fb} Newton fallbacks).  "
                f"θ* read off an unconverged field is not θ*")
        elif psi.size >= 4 and ang.size == psi.size:
            n = psi.size
            k0 = int(np.argmax(psi))
            ym1, y0, yp1 = psi[(k0 - 1) % n], psi[k0], psi[(k0 + 1) % n]
            den = (ym1 - 2.0 * y0 + yp1)
            frac = 0.5 * (ym1 - yp1) / den if abs(den) > 1e-30 else 0.0
            dstep = float(ang[1] - ang[0]) if n > 1 else 0.0
            theta_star = (k0 + frac) * dstep            # mech deg of ψ_A peak
            daxis = (90.0 - theta_star * float(pole_pairs)) % 360.0
            # ── the peak has to BE a peak ────────────────────────────────────
            # One sample of this curve is 15° ELECTRICAL on a 28-pole machine
            # (24 samples over one electrical period).  Landing one sample off
            # puts γ 15° from where the user set it; three samples off puts it
            # 45° away — a different operating point, not a rounding error.  A
            # run of the 150 mm machine reported 12.65 N·m where the same
            # geometry, current and mesh give 27.33: fitting T and V_peak
            # against a γ sweep placed that run at ~61° effective, i.e. exactly
            # three samples.  So refuse anything that is not an unambiguous
            # interior maximum — both neighbours strictly below, the parabolic
            # vertex inside the sample it was fitted around, real negative
            # curvature, and a runner-up that is not within the solve's own
            # noise.  A calibration that cannot be trusted stops the run; it
            # never quietly rotates the current vector.
            # Margin against the curve's OWN swing, not against y0: ψ_A is a
            # signed flux linkage, so a ratio is meaningless where it crosses
            # zero (and division by y0≈0 turns a healthy triangular peak into a
            # "flat" one — which is exactly how this guard first broke the
            # estimator tests).
            _span = float(np.max(psi) - np.min(psi))
            _margin = (float(y0 - max(ym1, yp1)) / _span) if _span > 0 else 0.0
            _elec_step = abs(dstep) * float(pole_pairs)
            if not (y0 > ym1 and y0 > yp1):
                _cal_err = ("ψ_A has no interior maximum at the sampled "
                            "resolution: k={} is not above both neighbours "
                            "({:.6e}, {:.6e}, {:.6e})".format(k0, ym1, y0, yp1))
                daxis = None
            elif den >= 0.0 or abs(frac) > 0.5:
                _cal_err = ("the parabolic vertex of ψ_A does not sit inside "
                            "sample k={}: frac={:+.3f} (must be within ±0.5), "
                            "curvature={:.3e} (must be < 0)".format(k0, frac, den))
                daxis = None
            elif _margin < 1e-3:
                _cal_err = ("the ψ_A peak is flat to the solve's own noise: the "
                            "runner-up sample is within {:.4f} % of the peak-to-"
                            "peak swing, so which sample wins is numerical luck "
                            "— and one sample is {:.1f}° electrical of load angle"
                            .format(100.0 * _margin, _elec_step))
                daxis = None
            if daxis is not None:
                log.info("d-axis auto-cal: theta*=%.4f deg mech -> DAXIS=%.4f deg "
                         "(poles=%d slots=%d conn=%s geo=%s, converged: %d frames, "
                         "worst resid %s; peak margin %.3f %% of swing)",
                         theta_star, daxis, key[0], key[1], key[3], _geo_fp,
                         int(psi.size), cal.get("picard_resid_max"),
                         100.0 * _margin)
        else:
            _cal_err = (f"calibration run returned no usable ψ_A "
                        f"(psi {psi.size} samples, angles {ang.size})")
    except Exception as _e:
        _cal_err = repr(_e)
    finally:
        _DAXIS_TLS.calibrating = False
    if daxis is None:
        # NO FALLBACK.  This used to return DAXIS_SHIFT_DEG (108°), which is the
        # calibrated value for 20p/24s and ~48° wrong for 12s14p — so a failed
        # calibration silently ran the WHOLE simulation at a load angle the user
        # never asked for, and every torque/efficiency number downstream was for
        # a different operating point than the one on screen.  A γ that is 48°
        # off is not a degraded answer, it is a wrong one; say so.
        raise RuntimeError(
            f"d-axis auto-calibration failed for poles={key[0]} slots={key[1]} "
            f"layers={key[2]} conn={key[3]!r} geometry={_geo_fp}: "
            f"{_cal_err}.  γ=0 cannot be placed on the q-axis without it, and "
            f"the legacy {DAXIS_SHIFT_DEG:.0f}° constant is not a fallback: it "
            f"is wrong for every topology measured so far, including the "
            f"20p/24s one it was written for (that machine calibrates to 120°).")
    _DAXIS_CACHE[key] = daxis
    if _dp:                           # persist so sweep subprocesses reuse it
        try:
            import os, json
            _disk = {}
            if os.path.exists(_dp):
                try:
                    with open(_dp) as _f:
                        _disk = json.load(_f) or {}
                except Exception:
                    _disk = {}
            # Drop any PRE-FINGERPRINT entry on the way past.  Those keys have
            # 4 fields (poles_slots_layers_conn) and no geometry, i.e. they are
            # exactly the entries that answered for machines they were never
            # measured on.  They are already unreachable — the lookup builds a
            # 5-field key — but leaving them in the file leaves a wrong number
            # sitting somewhere a human might read it as a record.
            _disk = {_k: _v for _k, _v in _disk.items()
                     if len(str(_k).split("_")) >= 5}
            _disk[skey] = float(daxis)
            with open(_dp, "w") as _f:
                json.dump(_disk, _f)
        except Exception:
            pass
    return daxis





# Template-copy each pole (rotor) / slot (stator): mesh ONE period, then rotate-
# copy + weld it so every period has a BIT-IDENTICAL interior, not just matched
# boundaries (setPeriodic).  Kills the pole-to-pole mesh-discretisation variance
# that leaves a ~1.2-1.6x residual on the loss waveform.  Per-half opt-in so the
# rotor can be validated before the (winding-phase-aware) stator.
import os as _os_sb

# True moving band (two uniform rings + closed-form re-stitched strip) vs the
# legacy merged single ring.  Diagnostic result: ord6 identical in both (the
# artifact is NOT the coupling); the one-row strip biases frame-local torque
# (frame0 -2.8 vs -0.1 N*m at I=0), so MERGED stays the default until the
# two-mesh gap bias is resolved.
_SB_MOVING_BAND = False

# Harmonic air-gap macroelement (Davat): replace the single-layer moving-band strip
# with the ANALYTIC Laplace solution of the gap annulus (block-circulant coupling,
# DFT-diagonal 2×2 per-harmonic blocks; rotor rotation = smooth phase e^{ikφ}).
# Implemented + validated (per-harmonic stiffness vs FEM annulus; mean torque matches
# the band).  RESULT (2026-06-30, #141): it does NOT reduce torque ripple — measured
# WORSE than the band at the same mesh (raw 25.6 % vs 17.6 %), because the dominant
# ripple is the FEM half-mesh teeth/slot discretisation (present in the field, seen by
# every torque contour), NOT the gap coupling; the band's coarse strip happens to
# low-pass it while the macroelement faithfully captures + (via virtual work) amplifies
# it.  Kept behind this flag (default OFF → production uses the band) for reference.
# 2026-07-08: env-gated (SB_AIRGAP_MACRO=1) so it can be re-tested ON TOP OF the
# structured (mapped) gap — the 2026-06-30 verdict above was measured on the FREE
# mesh, where the macroelement faithfully passed the tooth-discretisation noise;
# on the clean structured field the two are complementary (cells clean the field,
# the macro gives a smooth, continuous-angle coupling).
_SB_AIRGAP_MACRO = _os_sb.environ.get("SB_AIRGAP_MACRO", "0") == "1"
# 0 = adaptive formula (slip density scales with gap_layers); >0 forces slip
# nodes/period.  Env-gated so convergence studies can vary ONE discretisation
# at a time — the adaptive coupling (n_slip ~ gap_layers) otherwise changes the
# slip ring, the seam grid AND the air mesh together, making gl-sweeps impure.
_SLIP_PER_PERIOD_OVERRIDE = int(_os_sb.environ.get("SB_SLIP_PER_PERIOD", "0") or 0)

# ── Torque-band diagnostic (off by default; set ['on']=True before a solve to
# collect the per-frame Arkkio torque over radial sub-bands of the gap, to
# localise where parasitic ripple comes from — e.g. the slip-ring interface).
_TORQUE_DIAG = {"on": False, "full": [], "rotor": [], "stator": [],
                "iface": [], "rinner": [], "router": [],
                # per-frame angular profile of the Arkkio integrand (PHYSICAL
                # angle bins; rotor-half elements shifted by +θ_eff) — to see
                # WHERE around the gap the parasitic torque is generated.
                "ang_bins": 36, "ang_prof": []}






@dataclass
class FEMMaterial:
    name:  str
    mu_r:  float
    J_z:   float = 0.0   # [A/m²]  external current density
    Mx:    float = 0.0   # [A/m]   magnetization x-component
    My:    float = 0.0   # [A/m]   magnetization y-component
    sigma: float = 0.0   # [S/m]   electrical conductivity — solid-conductor eddy
                         #         currents (copper, magnet, shaft).  0 = no eddy
                         #         (air / laminated iron treated as σ=0).
    # Optional measured B-H curve (list of (H_A_per_m, B_T) pairs).
    # When set, the non-linear Picard iteration uses it to derive μ_r(|B|)
    # at each iteration instead of the analytic Fröhlich roll-off.
    bh_curve: Optional[List[Tuple[float, float]]] = None


@dataclass
class FEMResult:
    """Sampled result on a regular Cartesian grid."""
    grid_size:   int
    extent:      Tuple[float, float, float, float]   # xmin xmax ymin ymax [m]
    A_z:         np.ndarray   # (gs, gs)
    B_x:         np.ndarray
    B_y:         np.ndarray
    B_mag:       np.ndarray
    J_z:         np.ndarray   # source J_z on grid
    domain:      np.ndarray   # int8, domain ids on grid
    n_triangles: int
    n_nodes:     int
    solve_time_s: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Build a triangle mesh from CadQuery Shapely polygons (using gmsh)
# ─────────────────────────────────────────────────────────────────────────────















# ─────────────────────────────────────────────────────────────────────────────
# Periodic coil meshing — one wire → all wires → all coils
# ─────────────────────────────────────────────────────────────────────────────













































# ─────────────────────────────────────────────────────────────────────────────
# 2.  Assemble + solve the linear magnetostatics problem
# ─────────────────────────────────────────────────────────────────────────────













# ---------------------------------------------------------------------------
# the B-H fixed point
# ---------------------------------------------------------------------------

def _picard_relax(nu_old, nu_curve, d):
    """One damped Picard step on the reluctivity, GEOMETRIC in nu.

    Arithmetic damping averages nu = 1/mu, so a step that halves mu and one that
    doubles it are not the same size; the iteration then crawls where the curve
    is flat and overshoots at the knee.  Relaxing log(nu) makes the step
    scale-free, which is what lets one fixed 0.35 work from mu_r 5000 down to
    mu_r 5 without per-geometry tuning.
    """
    return np.exp((1.0 - d) * np.log(nu_old) + d * np.log(nu_curve))


def _constitutive_residual(Bm, w, nu_used, nu_curve):
    """|| |B| (nu_curve(|B|) - nu_used) || / || |B| nu_curve(|B|) ||.

    The relative error in H between the B-H curve and the reluctivity the solve
    actually used, area weighted.  It is a property of the STATE, not of the
    step, so damping harder cannot make it small — which is precisely the
    failure mode of the step-size test it replaces.  Measured on the 40 mm spoke
    machine at I = 0: the step test stopped with this residual at 0.87, and no
    sweep count from 16 to 640 left ~0.8, while the gap fundamental limit-cycled
    over a 3 % band 11 % below the true fixed point (1.354 vs 1.515 T) and the
    field broke an exact anti-periodicity by 3 %.
    """
    num = float((((Bm * (nu_curve - nu_used)) ** 2) * w).sum())
    den = float((((Bm * nu_curve) ** 2) * w).sum())
    return math.sqrt(num / max(den, 1e-300))


def solve_magnetostatics(
    mesh,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    n_sectors: int = 1,
    pole_pairs_per_sector_is_half_integer: bool = True,
    nonlinear_iterations: int = 60,
) -> np.ndarray:
    """Linear 2-D magnetostatics solve.

    Returns the nodal A_z vector (shape n_nodes,) for the P1 basis.

    Equation:   ∫ ν ∇A_z·∇v  dΩ  =  ∫ J_z v dΩ  +  ∫ (Mx ∂v/∂y − My ∂v/∂x) dΩ

    When `n_sectors > 1`, anti-periodic master-slave boundary conditions are
    enforced on the two radial cuts of the sector:
        A_z(r, θ=0) = -A_z(r, θ=2π/n_sectors)
    The sign flips because each sector covers an ODD number of poles for
    the 24-slot / 28-pole motor (7 poles per quarter = 3.5 pole pairs).
    """
    import time as _t
    from skfem import (
        Basis, ElementTriP1, BilinearForm, LinearForm,
        asm, condense, solve,
    )
    from skfem.helpers import dot, grad

    from skfem import ElementTriP0
    basis = Basis(mesh, ElementTriP1())

    @BilinearForm
    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    @BilinearForm
    def stiffness_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]   # ∂v/∂y

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]   # ∂v/∂x

    t0 = _t.time()
    n = basis.N
    from scipy.sparse import csr_matrix

    # Pre-compute the per-tag stiffness factors so the Picard iteration can
    # cheaply re-scale them when μ_r is updated.
    unique_tags = np.unique(cell_tags)
    tag_K: Dict[int, "csr_matrix"] = {}
    tag_mat: Dict[int, FEMMaterial] = {}
    tag_cells: Dict[int, np.ndarray] = {}
    tag_basis: Dict[int, "Basis"] = {}

    # Pre-assemble per-tag CURRENT and MAGNETISATION source vectors so the
    # Picard loop can re-scale each magnet's contribution when its Br_eff
    # drops due to demagnetisation, without re-running asm() every step.
    f_current = np.zeros(n)                  # J_z contribution (independent of M)
    tag_fMx: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂y dΩ
    tag_fMy: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂x dΩ
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        cells_idx = np.where(cell_tags == tag)[0]
        if cells_idx.size == 0:
            continue
        sub_basis = Basis(mesh, ElementTriP1(), elements=cells_idx)
        tag_K[int(tag)]    = asm(stiffness, sub_basis)
        tag_mat[int(tag)]  = mat
        tag_cells[int(tag)] = cells_idx
        tag_basis[int(tag)] = sub_basis
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub_basis) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub_basis)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub_basis)

    SATURABLE_TAGS = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    mu_r_eff: Dict[int, float] = {tag: tag_mat[tag].mu_r for tag in tag_mat}
    # ── PER-ELEMENT saturation state for the iron domains ────────────────
    # A per-DOMAIN μ from the B p90 NEVER saturates a spoke rotor's bridges
    # (they are a tiny fraction of the rotor area), so the magnets short
    # through the unsaturated bridges and the gap field collapses ~10×
    # (0.11 T instead of ~1 T — the "chaotic iso-lines / dead cogging"
    # symptom).  Mirror the transient: every iron triangle gets its own
    # ν(|B|), updated by damped Picard.
    sat_basis: Dict[int, "Basis"] = {}
    sat_b0:    Dict[int, "Basis"] = {}
    nu_el:     Dict[int, np.ndarray] = {}
    for tag in SATURABLE_TAGS:
        if tag in tag_cells:
            sat_basis[tag] = tag_basis[tag]
            sat_b0[tag] = tag_basis[tag].with_element(ElementTriP0())
            nu_el[tag] = np.full(tag_cells[tag].size,
                                 1.0 / (MU0 * max(tag_mat[tag].mu_r, 1.0)))
    # Br factor — starts at 1.0 (full strength) per magnet; the demag
    # iteration drops it below 1.0 when the operating point crosses the knee.
    br_factor: Dict[int, float] = {
        tag: 1.0 for tag in tag_mat if tag >= DOM_MAG_BASE}

    def _assemble_K() -> "csr_matrix":
        K = csr_matrix((n, n))
        for tag, K_dom in tag_K.items():
            if tag in sat_basis:
                continue                       # assembled per element below
            K = K + K_dom * (1.0 / (MU0 * mu_r_eff[tag]))
        for tag, sb in sat_basis.items():
            b0 = sat_b0[tag]
            nf = b0.zeros()
            nf[tag_cells[tag]] = nu_el[tag]    # P0 dof == global element id
            K = K + asm(stiffness_nu, sb, nu=b0.interpolate(nf))
        return K

    def _assemble_f() -> np.ndarray:
        f = f_current.copy()
        # 1/μ_r: the A-formulation source is the equivalent coercivity
        # H_c = Br/(μ₀·μ_rec) = M/μ_rec, not M.  See the module docstring.
        for tag, fMx in tag_fMx.items():
            scale = br_factor.get(tag, 1.0) / max(tag_mat[tag].mu_r, 1.0)
            f += fMx * (tag_mat[tag].Mx * scale)
        for tag, fMy in tag_fMy.items():
            scale = br_factor.get(tag, 1.0) / max(tag_mat[tag].mu_r, 1.0)
            f -= fMy * (tag_mat[tag].My * scale)
        return f

    f_const = _assemble_f()              # initial source, also referenced below

    f_total = f_const                                       # for compatibility

    # ── Picard iteration for iron saturation ─────────────────────────────
    # Linear iron (μ_r=5000 everywhere) lets the rotor back-iron act as a
    # short-circuit and absorb all the magnet flux instead of pushing it
    # through the air gap into the stator.  Real iron saturates at ~1.8 T,
    # so we iterate: solve linearly → check mean |B| in each iron domain →
    # roll μ_r down for over-saturated domains → resolve.  3–4 iterations
    # converge to a self-consistent saturated picture.
    A = np.zeros(n)
    outer_nodes = _outer_boundary_nodes(mesh)

    for it in range(max(1, nonlinear_iterations)):
        K_csr = _assemble_K().tocsr()
        f_iter = _assemble_f()           # picks up updated br_factor
        A = _solve_with_bc(K_csr, f_iter, outer_nodes, mesh, n_sectors,
                            pole_pairs_per_sector_is_half_integer)
        # Per-ELEMENT ν(|B|) update in every saturable (iron) domain — each
        # triangle saturates on its own, so thin features (spoke bridges)
        # saturate correctly even though the domain average stays low.
        Bx_tri, By_tri = _per_triangle_B(mesh, A)
        Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
        _nu_curve = {}
        _num = _den = 0.0
        for tag, sb in sat_basis.items():
            idx = tag_cells[tag]
            Bm = Bmag_tri[idx]
            mat_t = tag_mat[tag]
            if mat_t.bh_curve and len(mat_t.bh_curve) >= 2:
                mu_new = _mu_r_from_bh_vec(mat_t.bh_curve, Bm)
            else:
                ratio = np.where(Bm > 1.8, (1.8 / np.maximum(Bm, 1e-9)) ** 3, 1.0)
                mu_new = mat_t.mu_r * ratio + 5.0 * (1.0 - ratio)
            nu_c = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
            _nu_curve[tag] = nu_c
            _num += float((((Bm * (nu_c - nu_el[tag])) ** 2)).sum())
            _den += float((((Bm * nu_c) ** 2)).sum())
        # the STATE's violation of the B-H curve, not the size of the last step
        _res = math.sqrt(_num / max(_den, 1e-300))
        changed = _res > _PIC_TOL
        if changed:
            for tag, nu_c in _nu_curve.items():
                nu_el[tag] = _picard_relax(nu_el[tag], nu_c, 0.35)
            log.info("FEM iter %d: tag=%d B_max=%.2fT μ_r median %.0f (per-elem)",
                     it, tag, float(Bm.max()) if Bm.size else 0.0,
                     float(np.median(1.0 / (MU0 * nu_el[tag]))))

        # ── Self-consistent demagnetisation update ────────────────────
        # When a magnet's operating point falls BELOW its BH-curve knee,
        # the effective Br drops to the value defined by the recoil line
        # passing through the operating point.  We reduce br_factor so
        # the next iteration's source term reflects the lost magnetisation
        # — and the reported torque/losses include the demag penalty.
        for tag in [t for t in tag_mat if t >= DOM_MAG_BASE]:
            mat_t = tag_mat[tag]
            if not mat_t.bh_curve or len(mat_t.bh_curve) < 2:
                continue
            Mmag = math.hypot(mat_t.Mx, mat_t.My)
            if Mmag < 1e-9:
                continue
            idx = tag_cells.get(tag)
            if idx is None or idx.size == 0:
                continue
            # Per-cell H projected onto +M̂  (along magnetisation direction),
            # obtained by INVERTING the law this solve actually assembled:
            # the magnet domain carries mu_r = mu_rec and `_assemble_f` divides
            # its coercivity source by that same mu_rec, so
            #     B = mu0*mu_rec*H + Br*br_factor
            #  => H = (B.M̂ - Br*br_factor) / (mu0*mu_rec).
            # Reading it back as B/mu0 - M*br treats the magnet as air and
            # over-reads |H| by exactly mu_rec — ~5 %, always toward "more
            # demagnetised".  Same inversion as simulation/demag.py.
            B_dot_M = (Bx_tri[idx] * mat_t.Mx + By_tri[idx] * mat_t.My)
            _mu_rec = max(float(mat_t.mu_r), 1.0)
            H_along_M = (B_dot_M / Mmag - MU0 * Mmag * br_factor[tag]) / (MU0 * _mu_rec)
            H_worst = float(np.min(H_along_M))
            H_knee = mat_t.bh_curve[1][0] if mat_t.bh_curve[0][1] <= 0 \
                       else mat_t.bh_curve[0][0]
            if H_worst < H_knee:
                # On the BH curve at H_worst, B is below the recoil line
                # → effective Br must drop.  New Br = B_op - μ_rec·μ₀·H_op
                # where (H_op, B_op) is read from the measured curve.
                B_op = _b_from_bh_at_H(mat_t.bh_curve, H_worst)
                Br_new = B_op - mat_t.mu_r * MU0 * H_worst
                Br_orig = Mmag * MU0      # current full-strength Br
                ratio = max(0.0, min(1.0, Br_new / max(Br_orig, 1e-12)))
                new_factor = 0.5 * (br_factor[tag] + ratio)   # damped
                if abs(new_factor - br_factor[tag]) > 0.01:
                    changed = True
                log.warning("FEM iter %d: magnet tag=%d demag — "
                             "H_min=%.0f A/m, H_knee=%.0f A/m, Br_factor %.3f→%.3f",
                             it, tag, H_worst, H_knee,
                             br_factor[tag], new_factor)
                br_factor[tag] = new_factor
        if not changed:
            break
    log.info("FEM solve: %d nodes, %d triangles, %d Picard iters, %.2fs",
             basis.N, mesh.t.shape[1], it + 1, _t.time() - t0)
    return A


def _solve_with_bc(K_csr, f, outer_nodes, mesh, n_sectors,
                    pole_pairs_per_sector_is_half_integer):
    """Apply Dirichlet outer BC + optional anti-periodic sector BC, then solve.
    Returns the nodal A_z vector at FULL mesh resolution."""
    from skfem import condense, solve

    # ── Anti-periodic master-slave BC on the radial cuts (sector mode) ──
    if n_sectors > 1:
        masters, slaves = _pair_sector_cut_nodes(mesh, n_sectors)
        if masters.size:
            sign = -1.0 if pole_pairs_per_sector_is_half_integer else +1.0
            K_red, f_red, T = _apply_anti_periodic(K_csr, f,
                                                     masters, slaves, sign)
            n_full = mesh.p.shape[1]
            is_slave = np.zeros(n_full, dtype=bool); is_slave[slaves] = True
            free_ids = np.where(~is_slave)[0]
            full2red = -np.ones(n_full, dtype=int)
            full2red[free_ids] = np.arange(free_ids.size)
            outer_red = full2red[outer_nodes]
            outer_red = outer_red[outer_red >= 0]
            A_red = solve(*condense(K_red, f_red, D=outer_red))
            return (T @ A_red).A.ravel() if hasattr(T @ A_red, 'A') \
                else np.asarray(T @ A_red).ravel()

    return solve(*condense(K_csr, f, D=outer_nodes))


def _outer_boundary_nodes(mesh) -> np.ndarray:
    """Return node ids on the outermost circular boundary (highest r)."""
    coords = mesh.p
    r = np.sqrt(coords[0] ** 2 + coords[1] ** 2)
    r_max = r.max()
    # Outer boundary = nodes within 0.5 mm of r_max
    return np.where(r >= r_max - 5e-4)[0]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Sample A_z and derived B onto a regular grid (for canvas rendering)
# ─────────────────────────────────────────────────────────────────────────────

def sample_to_grid(
    mesh,
    A_nodal: np.ndarray,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    grid_size: int,
    extent_m: Tuple[float, float, float, float],
    classify_fn=None,   # optional (x_mm, y_mm) → domain_id
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate (A_z) onto a regular gs×gs grid; derive B = curl(A);
    reclassify domain on grid directly via classify_fn (recommended) or
    nearest-neighbour from mesh cells."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    xmin, xmax, ymin, ymax = extent_m
    xs = np.linspace(xmin, xmax, grid_size)
    ys = np.linspace(ymin, ymax, grid_size)
    XX, YY = np.meshgrid(xs, ys)

    pts = mesh.p.T              # (n_nodes, 2)
    interp_A = LinearNDInterpolator(pts, A_nodal, fill_value=0.0)
    A_z = interp_A(XX, YY)

    dy = ys[1] - ys[0]
    dx = xs[1] - xs[0]
    B_x =  np.gradient(A_z, dy, axis=0)
    B_y = -np.gradient(A_z, dx, axis=1)
    B_mag = np.sqrt(B_x ** 2 + B_y ** 2)

    if classify_fn is not None:
        # Grid-level classification (mm coordinates)
        domain = np.zeros((grid_size, grid_size), dtype=np.int8)
        # Vectorise: vectorize the per-pixel classifier
        flat_x = (XX * 1e3).ravel()    # m → mm
        flat_y = (YY * 1e3).ravel()
        out = np.array([classify_fn(fx, fy) for fx, fy in zip(flat_x, flat_y)],
                       dtype=np.int8)
        domain = out.reshape(grid_size, grid_size)
    else:
        cell_centroids = mesh.p[:, mesh.t].mean(axis=1).T
        dom_interp = NearestNDInterpolator(cell_centroids, cell_tags.astype(np.int32))
        domain = dom_interp(XX, YY).astype(np.int8)

    # J_z grid: directly from the material map by domain id
    J_z = np.zeros_like(A_z)
    for d in np.unique(domain):
        if int(d) in materials:
            J_z[domain == d] = materials[int(d)].J_z

    return A_z, B_x, B_y, B_mag, J_z, domain


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Top-level convenience: build materials dict, solve, sample
#
# ``coil_slot_index`` / ``coil_copper_areas`` (and the whole-machine copper
# section ``coil_copper_area_total_m2``) moved to simulation/field_ops.py — the
# DC copper loss needs the same measurement and the physics layer must not
# import the solver.  They are re-exported above, so `from ...fem_solver_2d
# import coil_copper_areas` still resolves.
# ─────────────────────────────────────────────────────────────────────────────

def build_materials(
    I_ph: Dict[str, float],
    winding_layout: List[Tuple[str, int]],
    polys: dict,
    rotor_angle_deg: float,
    slot_area_m2: float,
    n_wires: int,
    Br: float = 1.19,
    mu_r_steel: float = 5000.0,
    coil_area_m2: Optional[Mapping[int, float]] = None,
) -> Dict[int, FEMMaterial]:
    """Build the per-domain material map for the FEM solve.

    Each magnet (DOM_MAG_BASE+i) and each coil (DOM_COIL_BASE+i) gets its
    own material entry with the polygon-specific source term.  Bulk
    materials (air, iron, etc.) share fixed ids.

    ``coil_area_m2`` (coil domain tag → MESHED copper area [m²]) is how a caller
    that already has the mesh hands over the area the source will actually be
    integrated over; see ``coil_copper_areas``.  ``slot_area_m2`` is no longer
    the winding normaliser — it is kept only as the last-resort divisor for a
    geometry that carries no usable coil polygons at all.
    """
    n_slot = len(winding_layout)
    # Tangential M magnitude (alternating per pole)
    M_mag = Br / MU0

    # ── Resolve material assignments from motor_config.yaml ─────────────
    # Each motor part may be linked to a library material (with a BH curve);
    # if not, we keep the analytic μ_r above.
    try:
        from motor_ai_sim.config import get_material_assignments
        from motor_ai_sim import materials as mat_lib
        assignments = get_material_assignments() or {}
    except Exception:
        assignments = {}

    # ── Per-request material override (multi-user, Stage 2b) ─────────────
    # The signed-in user's own assignment + resolved props (mine / global),
    # sent with the request. The override WINS over the shared config; when
    # absent, everything below behaves EXACTLY as before (built-in / global
    # via mat_lib, which already resolves the admin global layer itself).
    try:
        from motor_ai_sim.material_context import get_request_materials
        _ov = get_request_materials() or {}
    except Exception:
        _ov = {}
    _ov_assign = _ov.get("assignment") or {}
    _ov_mats = _ov.get("materials") or {}
    if _ov_assign:
        assignments = {**assignments, **{k: v for k, v in _ov_assign.items() if v}}

    # ── Every assigned name must RESOLVE, or the solve fails here ────────────
    # A name the library does not have used to log one WARNING and fall through
    # to the analytic defaults — no BH curve and, for a magnet, no demag knee.
    # Measured (docs/SOLVER_TRIALS_2026-07-30.md F6): a config holding the
    # non-existent `magnet: N42SH` returned an EMPTY demag map and a shaft eddy
    # loss of 63.9 W against the 7.0 W the real magnet gives — a factor 9, with
    # every number on screen looking plausible.  Silently answering a different
    # question is the one thing this solver must not do, so it raises: the route
    # turns it into a 400 naming the material, and every eval path (optimizer,
    # trials harness, regression) fails the candidate instead of scoring it.
    # Names carried by the per-request override are real by definition — their
    # props travel with the request and never go through the library.
    from motor_ai_sim.materials import validate_assignment as _validate_assign
    _validate_assign(assignments, known_extra=set(_ov_mats or ()))

    def _resolve_mat(category: str, name: str):
        """Material dataclass: per-request override props first, else the library."""
        from motor_ai_sim import materials as _ml
        if name and name in _ov_mats:
            try:
                cat = (_ov_mats[name] or {}).get("category") or category
                return _ml.material_from_dict(cat, name, _ov_mats[name])
            except Exception:
                pass
        return _ml.get_material(category, name)

    def _bh_for(part_key: str, category: str = "steel"):
        name = assignments.get(part_key)
        if not name:
            return None
        try:
            m = _resolve_mat(category, name)
            bh = getattr(m, "bh_curve", None)
            if bh and len(bh) >= 2:
                return [(float(h), float(b)) for (h, b) in bh]
        except Exception:
            # Silently — shafts are often Aluminium (not in steel) which is fine
            pass
        return None

    def _stack_factor_for(part_key: str) -> float:
        """Lamination fill factor k_f of a laminated part, from its own material.

        Only laminated cores get one — the shaft is solid, magnets and copper are
        not stacks."""
        name = assignments.get(part_key)
        if not name:
            return 1.0
        try:
            kf = float(getattr(_resolve_mat("steel", name), "stacking_factor", 1.0))
            return kf if 0.0 < kf <= 1.0 else 1.0
        except Exception:
            return 1.0

    def _laminate(bh, kf: float):
        """Fold the stack's fill factor into the B-H curve.

        A lamination stack is steel and insulation in parallel across the
        GEOMETRIC cross-section the 2D model draws.  Only k_f of that area is
        steel, so the flux the geometric section can carry at a given H is

            B_geom(H) = k_f * B_steel(H) + (1 - k_f) * mu0 * H

        — the steel's own curve scaled down, plus the little the insulation
        carries as air.  Transforming the curve here means EVERY consumer picks
        it up automatically: the saturation Picard, the static solve, P1 and P2
        alike.  There is nothing to remember at the call sites.

        Doing it this way is also why the torque integral is left alone.  Torque
        is computed in the AIR GAP, whose axial length is the full stack — a
        blanket k_f on that integral would be a fudge.  The physical effect is
        that thinner effective iron saturates sooner, the field answers, and the
        torque follows on its own.  Expect the change to be small on this machine
        (the gap dominates the reluctance), which is the honest answer, not a
        disappointing one.
        """
        if bh is None or kf >= 1.0:
            return bh
        return [(float(h), kf * float(b) + (1.0 - kf) * MU0 * float(h))
                for (h, b) in bh]

    def _mu_r_for(part_key: str, default: float = 1.0) -> float:
        """Resolve a part's relative permeability from its linked material,
        searching all library categories.  A non-magnetic material (e.g.
        Aluminium_6061, whose mu_r is None) returns 1.0 — NOT the old 1000
        steel default that turned the aluminium shaft into a spurious flux
        path (it showed ~2.4 T; aluminium is non-magnetic, μ_r≈1)."""
        name = assignments.get(part_key)
        if not name:
            return default
        if name in _ov_mats:                     # per-request override (known category)
            try:
                cat = (_ov_mats[name] or {}).get("category") or "steel"
                mu = getattr(_resolve_mat(cat, name), "mu_r", None)
                return float(mu) if (mu is not None and float(mu) > 1.0) else 1.0
            except Exception:
                return default
        for cat in ("steel", "metal", "conductor", "magnet", "other", "custom"):
            try:
                m = mat_lib.get_material(cat, name)
            except Exception:
                continue
            mu = getattr(m, "mu_r", None)
            if mu is not None and float(mu) > 1.0:
                return float(mu)
            return 1.0          # found, but non-magnetic
        return default

    # Laminated cores: the material's own stacking_factor now enters the MAGNETIC
    # model, not just the loss volume.  Until this, k_f was applied to the iron
    # loss integral only, so the solve behaved as if the core were solid steel —
    # it carried more flux than the real stack can and saturated later.
    _kf_s = _stack_factor_for("stator_core")
    _kf_r = _stack_factor_for("rotor_core")
    bh_stator = _laminate(_bh_for("stator_core", "steel"), _kf_s)
    bh_rotor  = _laminate(_bh_for("rotor_core",  "steel"), _kf_r)
    if _kf_s < 1.0 or _kf_r < 1.0:
        log.info("lamination in the magnetic model: stator k_f=%.3f, rotor k_f=%.3f",
                 _kf_s, _kf_r)
    # Shaft is typically aluminium (conductor) or steel — try steel silently;
    # missing entry means no BH curve, which is fine for the air-like Al case.
    try:
        bh_shaft = _bh_for("shaft", "steel")
    except Exception:
        bh_shaft = None
    # μ_r for the shaft: a measured steel BH curve → mu_r_steel; otherwise the
    # linked material's own μ_r (Aluminium → 1.0), never a hard-coded 1000.
    shaft_mu_r = mu_r_steel if bh_shaft is not None else _mu_r_for("shaft", 1.0)
    # Magnet recoil μ_r, Br and BH curve (2nd-quadrant demag curve) from
    # the linked magnet material.
    mag_name = assignments.get("magnet")
    bh_magnet: Optional[List[Tuple[float, float]]] = None
    if mag_name:
        try:
            mat_mag = _resolve_mat("magnet", mag_name)
            Br      = float(getattr(mat_mag, "Br",     Br))
            mu_rec  = float(getattr(mat_mag, "mu_rec", 1.05))
            M_mag   = Br / MU0
            bh = getattr(mat_mag, "bh_curve", None)
            if bh and len(bh) >= 2:
                bh_magnet = [(float(h), float(b)) for (h, b) in bh]
        except Exception as e:
            # The assignment already passed validate_assignment above, so
            # reaching here means the entry exists but cannot be PARSED as a
            # magnet.  Either way the fallback is the analytic magnet with no
            # BH curve and no demag knee — different physics under the same
            # name.  Raise (F6): the old log.warning is what let a 9x wrong
            # shaft eddy loss out of the door looking plausible.
            from motor_ai_sim.materials import UnknownMaterialError
            raise UnknownMaterialError(
                f"magnet material {mag_name!r} could not be resolved: {e}") from e
    else:
        mu_rec = 1.05

    mats: Dict[int, FEMMaterial] = {
        DOM_AIR:    FEMMaterial("air",    mu_r=1.0),
        DOM_AIRGAP: FEMMaterial("airgap", mu_r=1.0),
        DOM_BAND:   FEMMaterial("band",   mu_r=1.0),
        DOM_OUTER:  FEMMaterial("outer",  mu_r=1.0),
        # Linear permeability gets the same treatment as the curve: steel and
        # insulation in parallel, mu_eff = k_f*mu_r + (1 - k_f).  Matters on its
        # own for a core with no B-H curve, where mu_r IS the whole model.
        DOM_STATOR: FEMMaterial("stator", mu_r=_kf_s * mu_r_steel + (1.0 - _kf_s),
                                bh_curve=bh_stator),
        DOM_ROTOR:  FEMMaterial("rotor",  mu_r=_kf_r * mu_r_steel + (1.0 - _kf_r),
                                bh_curve=bh_rotor),
        # Solid (non-laminated) conductors carry σ for the eddy-current solve.
        DOM_SHAFT:  FEMMaterial("shaft",  mu_r=shaft_mu_r, bh_curve=bh_shaft,
                                sigma=SIGMA_SHAFT),
        DOM_COIL:   FEMMaterial("coil",   mu_r=1.0, sigma=SIGMA_CU_20),
        DOM_MAG_N:  FEMMaterial("mag_N",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
        DOM_MAG_S:  FEMMaterial("mag_S",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
    }

    # ── Per-magnet tangential magnetization (SPOKE-PM topology) ──────────
    # M is tangent to the rotor at each magnet's angular position; sign
    # alternates per pole.  The iron tooth between adjacent magnets
    # becomes a virtual pole — flux concentrates there and exits radially
    # into the air gap, then closes through the STATOR YOKE.
    #
    # Convention (CCW tangent):
    #   tangent_CCW(centroid) = (-c_y, +c_x) / |centroid|     in WORLD frame
    #   N magnet (pol = +1):  M = +M_mag · tangent_CCW
    #   S magnet (pol = -1):  M = −M_mag · tangent_CCW
    for i, (mp, polarity) in enumerate(polys.get("magnets", [])):
        if mp is None or mp.is_empty:
            continue
        try:
            cx, cy = mp.centroid.x, mp.centroid.y
            cr = math.hypot(cx, cy)
            if cr < 1e-9:
                continue
            tx, ty = -cy / cr, cx / cr     # CCW tangent
        except Exception:
            continue
        sign = +1.0 if polarity > 0 else -1.0
        # Per-magnet material uses the assigned magnet's recoil permeability
        # and full demagnetisation BH curve so the Picard iteration can
        # detect under-knee operation and warn / reduce Br_eff.
        mats[DOM_MAG_BASE + i] = FEMMaterial(
            name=f"mag_{i}_{('N' if polarity>0 else 'S')}",
            mu_r=mu_rec,
            Mx= sign * M_mag * tx,
            My= sign * M_mag * ty,
            bh_curve=bh_magnet,                # full 2nd-quadrant demag curve
        )

    # ── Per-coil current density ─────────────────────────────────────────
    # cadquery_geometry now emits 24 coil polygons (one per slot, alternating
    # +x / -x side of each tooth).  We look up the slot's (phase, direction)
    # from winding_layout via centroid angle so the indexing is robust to
    # any clipping / re-ordering done downstream.
    coil_list = polys.get("coils", [])
    if coil_list and n_slot > 0:
        # The copper area each slot ACTUALLY has — meshed when the caller knows
        # the mesh, polygon otherwise.  Dividing by it is what makes the field be
        # excited at exactly n_wires·I_coil ampere-turns per slot instead of
        # k·n_wires·I_coil with k = A_copper/(slot_w·slot_h·0.6) ∈ 0.909…1.265.
        _areas = coil_copper_areas(polys, n_slot, coil_area_m2)
        for i, cp in enumerate(coil_list):
            slot_idx = coil_slot_index(cp, n_slot)
            if slot_idx is None:
                continue
            tag = DOM_COIL_BASE + i
            _a = _areas.get(tag)
            if _a is None:
                # No copper for this tag in this mesh (sector clipping): it has
                # no elements to carry a source, so it gets no material entry.
                # A geometry with no usable coil polygons at all falls back to
                # the nominal slot rectangle — the only remaining use of it.
                if coil_area_m2 is not None or _areas:
                    continue
                a_slot = max(float(slot_area_m2), 1e-12)
            else:
                a_slot = _a[1]
            phase, direction = winding_layout[slot_idx]
            # J_z = direction · I_coil_peak · n_wires_per_slot / A_copper_of_slot
            J_z = float(direction) * I_ph[phase] * n_wires / max(a_slot, 1e-12)
            mats[tag] = FEMMaterial(
                name=f"coil_{i}_slot{slot_idx}_{phase}{'+' if direction>0 else '-'}",
                mu_r=1.0, J_z=J_z,
            )
    return mats


def _field2d_static_inputs(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
):
    """Config-derived inputs for the magnetostatic field solve.

    SHARED by fem_field2d (which builds its own mesh) and solve_field2d_on_mesh
    (which consumes a prebuilt mesh from the `mesh` module). Single source for the
    operating point + per-domain materials, so the self-meshing path and the
    mesh -> solver handoff path can never drift apart.

    Returns (polys_simplified, materials, params).
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating-point currents (γ=0 → q-axis, +π/2 shift) ──────────────
    if I_phase_rms is None:
        I_phase_rms = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = geo.get("num_wires_per_slot", 14)
    pole_pairs   = p.num_poles // 2
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    # ── Real CadQuery polygons at this rotor angle (simplified to match mesh) ──
    motor = CadQueryMotor()
    polys = _simplify_polys(motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg), tol_mm=0.3)

    # The winding is normalised by the COIL POLYGONS' own copper area inside
    # build_materials (this path has no mesh yet).  The coil sections are
    # rectangles, so a conforming triangulation reproduces that area exactly and
    # the static viewer is excited at the same n_wires·I as the transient solve.
    # The nominal rectangle below is only the no-coil-polygons fallback.
    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg, slot_area, n_wires)
    return polys, mats, p


def fem_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    grid_size: int = 150,
    mesh_size_mm: float = 1.6,
) -> FEMResult:
    """Top-level: build mesh + assemble + solve + sample.

    Mirrors the signature of routes/simulation.py::get_field2d so the FEM
    endpoint can drop in as a swap.
    """
    import time as _t

    t_start = _t.time()
    polys, mats, p = _field2d_static_inputs(rotor_angle_deg, gamma_deg)

    log.info("FEM: building triangle mesh (h=%.2f mm)…", mesh_size_mm)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(polys, rotor_angle_deg, mesh_size_mm)

    # Reclassify each triangle by its centroid (mm) — robust against gmsh tag loss
    tri_centroids_m = mesh.p[:, mesh.t].mean(axis=1)   # (2, n_tri) in metres
    cell_tags = np.array(
        [classify_fn(tri_centroids_m[0, i] * 1e3, tri_centroids_m[1, i] * 1e3)
         for i in range(tri_centroids_m.shape[1])],
        dtype=np.int8,
    )
    log.info("FEM: reclassified cells — %s", dict(zip(*np.unique(cell_tags, return_counts=True))))
    log.info("FEM: mesh has %d nodes, %d triangles", mesh.p.shape[1], mesh.t.shape[1])

    # Solve
    A = solve_magnetostatics(mesh, cell_tags, mats)

    # Sample onto regular grid for canvas
    R = p.r_stator_out * 1.02
    extent = (-R, R, -R, R)
    A_g, Bx_g, By_g, Bmag_g, Jz_g, dom_g = sample_to_grid(
        mesh, A, cell_tags, mats, grid_size, extent, classify_fn=classify_fn,
    )

    return FEMResult(
        grid_size=grid_size,
        extent=extent,
        A_z=A_g, B_x=Bx_g, B_y=By_g, B_mag=Bmag_g,
        J_z=Jz_g, domain=dom_g,
        n_triangles=mesh.t.shape[1],
        n_nodes=mesh.p.shape[1],
        solve_time_s=_t.time() - t_start,
    )


def solve_field2d_on_mesh(
    mesh,
    cell_tags: np.ndarray,
    *,
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
) -> Dict[str, Any]:
    """Magnetostatic field solve on a PREBUILT mesh — the end-to-end
    mesh -> solver handoff.

    The static solver consumes exactly the discretization the `mesh` module
    produced (its MeshIR vertices/triangles/cell_tags), instead of meshing
    again. The operating point + per-domain materials come from
    _field2d_static_inputs, SHARED with the self-meshing fem_field2d, so the
    physics is identical — only the mesh provenance differs.

    Returns a JSON-friendly field summary (no per-node arrays); callers that
    need the full field still use fem_field2d / the field route.
    """
    import time as _t
    t0 = _t.time()
    _polys, mats, _p = _field2d_static_inputs(rotor_angle_deg, gamma_deg, I_phase_rms)
    cell_tags = np.asarray(cell_tags).astype(int)

    A = solve_magnetostatics(mesh, cell_tags, mats)
    Bx, By = _per_triangle_B(mesh, A)
    Bmag = np.sqrt(Bx ** 2 + By ** 2)
    return {
        "n_nodes":     int(mesh.p.shape[1]),
        "n_cells":     int(mesh.t.shape[1]),
        "A_z_min":     float(A.min()),
        "A_z_max":     float(A.max()),
        "B_mag_max_T": float(Bmag.max()),
        "B_mag_mean_T": float(Bmag.mean()),
        "solve_time_s": round(_t.time() - t0, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Full FEM pipeline with torque + losses (Simulation tab endpoint)
# ─────────────────────────────────────────────────────────────────────────────















def solve_magnetostatics_fem(mesh, cell_tags: np.ndarray,
                             materials: Dict[int, FEMMaterial],
                             element_order: int = 2,
                             nonlinear_iterations: int = 60):
    """Magnetostatic solve on ElementTriP1 (order 1) or ElementTriP2 (order 2).

    Same weak form  ∫ ν ∇A·∇v = ∫ J_z v + ∫(Hcx ∂v/∂y − Hcy ∂v/∂x),
    Hc = M/μ_rec (module docstring), and the same
    per-element BH Picard (_mu_r_from_bh_vec, 0.5 damping) for BOTH orders — the
    ONLY difference is the element (order 2 ⇒ A quadratic ⇒ B linear per element,
    smooth torque; order 1 ⇒ A linear ⇒ B piecewise-constant, staircase).  Using
    ONE code path for both makes P1-vs-P2 a controlled comparison (identical mesh,
    sources, Picard, and — importantly — the same robust facet-based outer
    Dirichlet BC, which the legacy vertex-id `_outer_boundary_nodes` gets wrong on
    some meshes → singular matrix).

    Returns (A_vec, basis).  A_vec length = basis.N (order 1: n_nodes; order 2:
    n_nodes + n_edges); `basis` is needed to extract B / torque (_arkkio_torque_p2
    works for both — it evaluates ∇A at the element quadrature points).

    Iron saturation is iterated per element; magnet irreversible-demag is NOT
    modelled here (a second-order correction for cogging/ripple studies).
    """
    from skfem import (Basis, ElementTriP1, ElementTriP2, ElementTriP0,
                       BilinearForm, LinearForm, asm, condense, solve)
    from skfem.helpers import dot, grad
    import time as _t

    if int(element_order) not in (1, 2):
        raise ValueError(f"element_order must be 1 or 2, got {element_order}")
    Elem = ElementTriP1 if int(element_order) == 1 else ElementTriP2

    t0 = _t.time()
    basis = Basis(mesh, Elem())
    b0 = basis.with_element(ElementTriP0())        # P0 on the SAME quadrature rule
    n = basis.N
    n_tri = mesh.t.shape[1]

    @BilinearForm
    def stiffness_nu(u, v, w):
        return w["nu"] * dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]

    unique_tags = np.unique(cell_tags)
    tag_mat: Dict[int, FEMMaterial] = {}
    tag_cells: Dict[int, np.ndarray] = {}
    f_current = np.zeros(n)
    tag_fMx: Dict[int, np.ndarray] = {}
    tag_fMy: Dict[int, np.ndarray] = {}
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        tag_mat[int(tag)] = mat
        tag_cells[int(tag)] = idx
        sub = Basis(mesh, Elem(), elements=idx)
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub)

    # Per-element reluctivity ν (P0 dof == element id), init from μ_r.
    nu_all = np.empty(n_tri)
    for tag in unique_tags:
        mat = materials.get(int(tag))
        idx = np.where(cell_tags == tag)[0]
        mur = mat.mu_r if mat is not None else 1.0
        nu_all[idx] = 1.0 / (MU0 * max(mur, 1.0))

    br_factor = {t: 1.0 for t in tag_mat if t >= DOM_MAG_BASE}

    def _assemble_K():
        nf = b0.zeros()
        nf[:] = nu_all
        return asm(stiffness_nu, basis, nu=b0.interpolate(nf)).tocsr()

    def _assemble_f():
        f = f_current.copy()
        # 1/μ_r: source is H_c = M/μ_rec, not M (module docstring).
        for tag, fMx in tag_fMx.items():
            f += fMx * (tag_mat[tag].Mx * br_factor.get(tag, 1.0)
                        / max(tag_mat[tag].mu_r, 1.0))
        for tag, fMy in tag_fMy.items():
            f -= fMy * (tag_mat[tag].My * br_factor.get(tag, 1.0)
                        / max(tag_mat[tag].mu_r, 1.0))
        return f

    # Dirichlet DOFs on the outer circle — P2 must include EDGE-MIDPOINT dofs,
    # not just vertices, so select by boundary facet and let get_dofs expand.
    r_nodes = np.sqrt(mesh.p[0] ** 2 + mesh.p[1] ** 2)
    r_max = r_nodes.max()
    out_facets = mesh.facets_satisfying(
        lambda x: np.sqrt(x[0] ** 2 + x[1] ** 2) >= r_max - 5e-4)
    D = basis.get_dofs(facets=out_facets)

    A = np.zeros(n)
    it = 0
    d_cur, good, prev_res, res = 0.35, 0, float("inf"), 0.0
    for it in range(max(1, nonlinear_iterations)):
        K = _assemble_K()
        f = _assemble_f()
        A = solve(*condense(K, f, D=D))
        Bx_q, By_q, dx = _p2_B_at_quad(basis, A)
        area = dx.sum(axis=1)
        Bmag_q = np.sqrt(Bx_q ** 2 + By_q ** 2)
        Bmag_el = (Bmag_q * dx).sum(axis=1) / np.maximum(area, 1e-30)
        nu_curve = nu_all.copy()
        sat = []
        for tag in (DOM_STATOR, DOM_ROTOR, DOM_SHAFT):
            idx = tag_cells.get(tag)
            if idx is None:
                continue
            mat = tag_mat[tag]
            Bm = Bmag_el[idx]
            if mat.bh_curve and len(mat.bh_curve) >= 2:
                mu_new = _mu_r_from_bh_vec(mat.bh_curve, Bm)
            else:
                ratio = np.where(Bm > 1.8, (1.8 / np.maximum(Bm, 1e-9)) ** 3, 1.0)
                mu_new = mat.mu_r * ratio + 5.0 * (1.0 - ratio)
            nu_curve[idx] = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
            sat.append(idx)
        if not sat:
            break
        sidx = np.concatenate(sat)
        res = _constitutive_residual(Bmag_el[sidx], area[sidx],
                                     nu_all[sidx], nu_curve[sidx])
        if res < _PIC_TOL:
            break
        # adaptive step: back off on a worse residual, give it back after three
        # clean sweeps (halving alone is a ratchet — the saturating bridges make
        # the residual bounce early and strand the loop at d ~ 0.04 afterwards)
        if res > prev_res:
            d_cur, good = max(0.5 * d_cur, 0.02), 0
        else:
            good += 1
            if good >= 3:
                d_cur, good = min(1.6 * d_cur, 0.35), 0
        prev_res = res
        nu_all[sidx] = _picard_relax(nu_all[sidx], nu_curve[sidx], d_cur)
    log.info("FEM P%d solve: %d dofs, %d triangles, %d Picard iters, "
             "constitutive residual %.2e (tol %.1e), %.2fs",
             int(element_order), n, n_tri, it + 1, res, _PIC_TOL, _t.time() - t0)
    if res > _PIC_TOL:
        log.warning("FEM P%d solve did NOT reach the B-H fixed point: residual "
                    "%.2e against tol %.1e after %d sweeps. The saturation "
                    "state is not a solution of the machine that was asked "
                    "for; on a saturating spoke rotor the measured error in "
                    "the gap fundamental is 8-11 %%. Raise "
                    "nonlinear_iterations.",
                    int(element_order), res, _PIC_TOL, it + 1)
    return A, basis


def solve_magnetostatics_p2(mesh, cell_tags: np.ndarray,
                            materials: Dict[int, FEMMaterial],
                            nonlinear_iterations: int = 8):
    """Quadratic (P2) magnetostatic solve — thin wrapper over
    solve_magnetostatics_fem(element_order=2).  Returns (A_vec, basis)."""
    return solve_magnetostatics_fem(mesh, cell_tags, materials,
                                    element_order=2,
                                    nonlinear_iterations=nonlinear_iterations)




# Copper electrical properties (annealed Cu, IEC 60028) — see field_ops.

# Conductivities of the SOLID (non-laminated) conductor regions for the
# eddy-current (magnetodynamic) solver [S/m].  σ=0 ⇒ no eddy (air, laminated
# iron).  These move the eddy loss INTO the field solve (J = −σ ∂A/∂t).
SIGMA_CU_20  = 1.0 / RHO_CU_20   # ≈ 5.80e7  (temperature-corrected at use)
SIGMA_NDFEB  = 6.7e5             # sintered NdFeB
SIGMA_SHAFT  = 4.5e6             # carbon-steel shaft






def _params_from_geo_dict(g: dict):
    """Build MotorDomainParams from a geometry dict (mirror of
    geometry_2d.params_from_config, but from an in-memory dict so a candidate
    design can be evaluated WITHOUT touching the global config file)."""
    from motor_ai_sim.simulation.geometry_2d import MotorDomainParams
    mm = 1e-3
    r_so = g["stator_diameter"] / 2 * mm
    r_si = r_so - g["core_thickness"] * mm - g["slot_height"] * mm
    r_ro = r_si - g["air_gap"] * mm
    r_ri = r_ro - g["magnet_height"] * mm - g["rotor_house_height"] * mm
    r_sh = r_ri - g["shaft_height"] * mm
    # Pole/slot COUNT is defined by the geometry (magnets/slots) — num_poles/num_slots
    # are authoritative; the segment product is only a fallback (a stale num_seg from a
    # different motor must not override the real count).
    num_slots = int(g.get("num_slots") or round(g["num_seg"] * g["num_slots_per_segment"]))
    num_poles = int(g.get("num_poles") or round(g["num_seg"] * g["num_poles_per_segment"]))
    slot_width_m = (g["wire_width"] + 2 * g["wire_spacing_x"]
                    + 2 * g["insulation_thickness"]) * mm
    return MotorDomainParams(
        r_stator_out=r_so, r_stator_in=r_si, r_rotor_out=r_ro, r_rotor_in=r_ri,
        r_shaft_in=r_sh, r_air_out=r_si, r_air_in=r_ro,
        num_poles=num_poles, num_slots=num_slots,
        stack_length=g.get("motor_length", 30) * mm,
        magnet_fill_fraction=g.get("magnet_fill_down", 0.9),
        slot_width_m=slot_width_m, slot_height_m=g["slot_height"] * mm,
        wire_width_m=g["wire_width"] * mm, wire_height_m=g["wire_height"] * mm,
        num_wires_per_slot=int(g["num_wires_per_slot"]))


def _spectral_ddt_series(x, kmax, dt):
    """Periodic time-derivative of a one-period series, truncated to `kmax`
    harmonics.

    The rotor advances in DISCRETE slip-node steps, so psi(t) carries a
    frame-to-frame quantisation jitter that a raw finite difference amplifies
    into a jagged back-EMF.  Reconstructing the derivative from the LOW
    harmonics keeps the genuine fundamental + slot-ripple content and drops the
    quantisation floor near Nyquist.  It is also PERIODIC by construction,
    which np.gradient is not: np.gradient falls back to a ONE-SIDED difference
    at the two end frames, and V_peak is a max over the series, so those two
    frames set the reported peak (this is why the P1 and P2 branches reported
    V_peak ~11 % apart on the same physics).  ONE implementation, shared by
    both element orders.
    """
    x = np.asarray(x, float); N = x.size
    if N < 4:
        return np.array([(x[(i + 1) % N] - x[(i - 1) % N]) / (2 * dt)
                         for i in range(N)])
    F = np.fft.rfft(x)
    if kmax + 1 < F.size:
        F[kmax + 1:] = 0.0
    return np.fft.irfft(F * (1j * 2 * np.pi * np.fft.rfftfreq(N, d=dt)), n=N)


def _vdrive_copper_loss(p, geo, IA, IB, IC, n_parallel, coil_temp_c,
                        end_winding_factor, copper_area_m2=None):
    """DC copper loss from the SOLVED phase currents (voltage drive only).

    Under voltage drive the current is the ANSWER, not the input: the
    `copper_loss_W` call at the top of the transient runs on the CONFIG
    `I_phase_rms` long before the circuit has solved anything, and nothing
    recomputed it — so P_cu (and through it P_loss_total and the efficiency)
    was wrong by (I_solved/I_config)^2.  Recompute it here, on the settled
    reported window, through the SAME physical model (rho(T)*J^2*V_cu*k_end).

    IA/IB/IC are the PER-BRANCH conductor currents (see `_currents`), so the
    phase rms is the branch rms times n_parallel.  ``copper_area_m2`` is the
    MEASURED conductor section (see `copper_loss_W`) — the same one the current-
    drive call used, so the two differ only in the current.  Returns
    (P_cu_W, k_end, R_phase_solved, I_phase_rms_solved).
    """
    _a = np.asarray(IA, float); _b = np.asarray(IB, float)
    _c = np.asarray(IC, float)
    _rms_branch = math.sqrt(float(np.mean(_a ** 2 + _b ** 2 + _c ** 2)) / 3.0)
    _I_ph = _rms_branch * float(max(n_parallel, 1))
    _P, _k_end, _R = copper_loss_W(
        p, geo, _I_ph, n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor,
        copper_area_m2=copper_area_m2)
    return float(_P), float(_k_end), float(_R), float(_I_ph)


def fem_transient_sliding_band(
    n_steps_per_period: int = 12,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    rpm: Optional[float] = None,  # MECHANICAL SPEED [rpm].  None = read the global
                                 # config's simulation.rpm (so nothing that omits it
                                 # changes).  Give it explicitly and the solve is
                                 # speed-independent of the shared config: f_elec,
                                 # dB/dt, iron loss, magnet/shaft eddy and the
                                 # back-EMF all follow THIS number.  Before this
                                 # existed, geo_override could move the geometry to
                                 # another machine while the speed stayed whatever
                                 # the shared config held — measured cost on the
                                 # 30 mm control: P_fe -20 %, V_peak -12.5 %,
                                 # eta -0.86 pp (docs/SOLVER_TRIALS_2026-07-30.md F2).
    n_parallel: Optional[int] = None,   # PARALLEL PATHS of the winding.  None = the
                                 # global config's winding.n_parallel.  The FEM only
                                 # ever sees I_coil = I_phase / n_parallel, so a wrong
                                 # value is a factor-n_parallel error in the coil MMF:
                                 # measured +95.7 % torque on ciano20_150_35 (stored
                                 # 2S-2P, unapplied) and -1.1 % against its own stored
                                 # torque once applied (F3).
    connection: Optional[str] = None,   # WINDING CONNECTION label ("4S" / "2S-2P" /
                                 # "4P").  Supplies n_parallel when that is not given
                                 # explicitly, AND enters the d-axis calibration
                                 # topology key — so a stored machine is evaluated on
                                 # its OWN connection, not the shared config's.  An
                                 # unreadable label RAISES (motor_ai_sim.winding).
    mesh_size_mm: float = 3.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,     # element layers across the air gap (Mesh-tab slider)
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    nonlinear_iterations: int = 100,  # CAP on the saturation Picard; the loop
                                 # exits EARLY on the fixed-point residual
                                 # (_PIC_TOL) — no fixed-recipe iteration counts.
                                 # 14 was a tuned recipe that did NOT converge
                                 # and left a 5-8 Nm no-load torque floor.
    frozen_nu: bool = False,     # FROZEN PERMEABILITY: converge saturation ONCE
                                 # (frame 0, extended Picard), then freeze the
                                 # per-element nu for every rotor position.  The
                                 # damped Picard PLATEAUS (no-load torque floor
                                 # 5-8 Nm p-p even at 60 iters — each frame lands
                                 # in a different nu-state); with a linear/fixed
                                 # nu the whole chain is clean to 0.004 Nm.  The
                                 # industry-standard method for honest cogging /
                                 # ripple.  Current drive only.
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    geo_override: dict = None,
    eddy: bool = False,          # opt-in: time-coupled σ·∂A/∂t eddy-current solve
    rotor_eddy: bool = False,    # field-based magnet/shaft eddy losses (stranded coils)
    demag: bool = False,         # opt-in: per-element irreversible demagnetisation
    component_mesh_mm: dict = None,  # per-part target element size {comp: mm}
    return_field: bool = False,  # also return a field snapshot for the viewer
    return_frames: int = 0,      # >0: ALSO return that many evenly-spaced per-frame
                                 # field snapshots for the animation viewer.  The
                                 # sliding band solves every frame on ONE mesh, so
                                 # the frames share it and each carries only A(t),
                                 # B(t) and the rotor angle — the remesh-per-frame
                                 # path this replaces built a full gmsh mesh per
                                 # frame (~11 s of ~15 s) and needed a 24-process
                                 # worker pool to hide it.
    field_first: bool = False,   # snapshot the FIRST frame (rotor at angle0) instead
                                 # of the last — used by the magnetostatic field view
                                 # so the picture matches the requested rotor angle
    torque_filter: bool = False,  # band-limit T(t) to the physical 6·k orders — OFF by default: the headline ripple is the RAW one (no filters)
                                 # (False = raw per-frame Maxwell-stress torque)
    pole_copy: Optional[bool] = None,  # bit-identical pole/slot mesh; None=env default
    iron_template: Optional[bool] = None,  # deterministic template iron; None=env default
    geo_mesh: Optional[bool] = None,   # geometry-driven CDT mesh; None=env default
    progress_cb=None,            # optional callback(done:int, total:int) per frame
    magnet_scale: float = 1.0,   # scale ALL magnet Br (0 → PMs off = reluctance torque)
    rotor_angle0_deg: float = 0.0,   # DIAGNOSTIC: build the rotor PHYSICALLY rotated
                                     # in CAD (magnets+pockets rotated before meshing);
                                     # with 1 step this is a true static solve at that
                                     # angle using the SB machinery minus the sliding.
    hi_fidelity: bool = False,       # "High-fidelity torque": 2× slip-ring nodes + finer
                                     # feature mesh (÷8 not ÷4) → pushes the numerical
                                     # picket-fence torque hash to higher orders + halves
                                     # the over-resolved cogging.  ~2× slower; mean torque
                                     # unchanged.  Off by default (speed).
    honest_eddy: bool = False,       # ADDITIVE diagnostic: ALSO compute the coupled
                                     # (reaction-included) rotor eddy via eddy_solver_2d
                                     # for comparison vs the resistance-limited post-
                                     # process.  Captures rotor-node A history; fail-safe
                                     # (any error leaves the production numbers intact).
    structured_gap: bool = False,    # ANSYS-style concentric-ring air-gap mesh (experimental
                                     # Mesh-tab toggle; default off = free gmsh gap).
    airgap_macro: bool = False,      # harmonic air-gap macroelement (Mesh-tab "Harmonic gap"):
                                     # replaces the node re-pairing slip coupling with a smooth
                                     # analytic per-harmonic rotor↔stator link → RAW T(t) becomes
                                     # step-count independent (honest unfiltered ripple).  Works
                                     # on the full ring AND sector wedges (half-integer harmonic
                                     # ladder / skew-circulant for anti-periodic wedges).  ORs
                                     # with the SB_AIRGAP_MACRO env flag.
    drive: str = "current",          # "current" = imposed sinusoidal phase currents (default);
                                     # "voltage" = imposed sinusoidal phase VOLTAGE — the phase
                                     # currents become circuit STATE solved from V = R·i + dψ/dt
                                     # each frame, so non-sinusoidal back-EMF drives REAL
                                     # parasitic harmonic currents (FOC-drive verification mode).
    v_phase_peak: float = 0.0,       # voltage drive: phase-voltage amplitude [V, peak]
    v_delta_deg: float = 0.0,        # voltage drive: voltage angle [°el] in the SAME frame as γ
    element_order: int = 2,          # 2 = P2 quadratic elements — THE basis.  B is linear per
                                     # element → smooth Arkkio torque, an energy-consistent mean
                                     # AND a mesh-convergent ripple.  P2 runs the FULL
                                     # magnetostatic sliding-band transient on the merged
                                     # structured belt — full ring (n_sectors≤1) AND anti-periodic
                                     # sector wedge (n_sectors≥2) — via edge-midpoint DOF stitching
                                     # across the moving slip cut and the radial cuts (see the P2
                                     # branch below and P2_NOTES.md).  Voltage drive, irreversible
                                     # demag and the coupled σ·∂A/∂t eddy solve all run on P2,
                                     # INCLUDING eddy + voltage drive together (one bordered
                                     # (A, U, i_A, i_B) Newton).  Still gated (raises
                                     # NotImplementedError): the moving / harmonic-macro band.
                                     # P1 (1) is GONE and passing it RAISES: it over-read the mean
                                     # torque ~35 % (its Maxwell integral is radius-inconsistent
                                     # under load) and its ripple was a mesh staircase, so every P1
                                     # number needed a correction nobody could state.
) -> dict:
    """Sliding-band transient: mesh the stator + rotor halves ONCE, then sweep
    the rotor by shifting the slip-ring node pairing (no remeshing) so the
    mesh topology is IDENTICAL every frame.  That removes the per-frame
    remesh noise → smooth T(t) and clean back-EMF V(t) = R·I + dψ/dt.

    Fixed-mesh formulation: both halves stay in the [0, 360/n_sectors] wedge;
    the rotor rotation θ = m·slip_spacing is encoded ONLY in the slip pairing
    (shift by m nodes, sign −1 on every wrap past the sector edge — anti-
    periodic).  A signed union-find merges the slip pairing with the radial-cut
    anti-periodic BC.  Iron saturation via per-domain Picard.

    Returns the same dict shape as the parallel transient endpoint expects.
    """
    import time as _t
    from skfem import (Basis, ElementTriP1, ElementTriP0, BilinearForm,
                       LinearForm, asm, MeshTri)
    from skfem.helpers import dot as _dot, grad as _grad
    from scipy.sparse import csr_matrix as _csr, coo_matrix as _coo
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    t0 = _t.time()
    # Mesh density is driven ENTIRELY by the Mesh-tab sliders now (mesh_size,
    # min_size, gap_layers, normal_deviation) — no hidden clamp.  Earlier this
    # path hard-clamped iron to 2 mm and the gap floor to 0.1 mm "for smooth
    # T(t)", but that silently overrode the sliders (they looked dead).  The
    # air-gap is resolved by gap_layers (element size = gap/gap_layers, applied
    # under min_size in build_mesh_from_polygons), so torque accuracy is the
    # user's choice: finer mesh + more gap layers = smoother T(t), coarser =
    # faster.  Defaults (mesh 4 mm clamped→… no: now literally 4 mm; gap_layers
    # 3) reproduce the previous behaviour closely; drag to mesh≈2 mm / gap≈3-4
    # for the cleanest torque.
    # P2 is the only basis.  This used to accept 1 as well and branch on it;
    # the P1 branch is gone, so an explicit 1 must FAIL rather than be quietly
    # promoted to 2 — a caller that asks for P1 is asking for the ~35 % torque
    # over-read and the staircase ripple, and it needs to hear that it cannot
    # have them, not receive different numbers than it asked for.
    element_order = int(element_order)
    if element_order != 2:
        raise ValueError(
            f"element_order must be 2 (P2); got {element_order}. The P1 basis "
            f"was removed: its Maxwell-stress mean torque is radius-"
            f"inconsistent under load (~35 % high) and its ripple is a mesh "
            f"staircase.")
    # The whole transient below is the P2 magnetostatic sliding band on the
    # merged structured belt: moving-cut edge-midpoint DOF stitching (the
    # historical blocker) for the full ring AND anti-periodic sector wedges.
    # See P2_NOTES.md.  The one thing it still refuses is the moving /
    # harmonic-macro air-gap band (raised where the band radii are read).
    mesh_size_mm = float(mesh_size_mm)
    min_size_mm = float(min_size_mm)
    cfg = get_config(); sim = cfg.get("simulation", {})
    geo = dict(cfg.get("geometry", {}))
    # The winding block is COPIED, never referenced: the per-request connection /
    # n_parallel overlay it below, and evaluating a catalog machine must not move
    # the user's shared config (F3).
    wind = dict(cfg.get("winding", {}) or {})
    if connection is not None:
        from motor_ai_sim.winding import parse_connection as _parse_conn
        _np_conn, _ns_conn = _parse_conn(connection)   # raises on an unreadable label
        wind["connection"] = str(connection)
        wind["n_series"] = _ns_conn
        if n_parallel is None:
            n_parallel = _np_conn
    if n_parallel is not None:
        if int(n_parallel) < 1:
            raise ValueError(f"n_parallel must be >= 1; got {n_parallel!r}")
        wind["n_parallel"] = int(n_parallel)
    # Candidate-design evaluation (optimization refine): overlay a geometry
    # override in-memory so the global config / Simulation state is untouched.
    #
    # The merge runs UNCONDITIONALLY, override or not.  It is what makes this
    # dict self-consistent: the slot/pole counts must describe the SAME motor the
    # CAD meshes (override explicit counts > override segment form > config
    # segment form — the exact CadQueryMotor resolution), so the winding layout,
    # pole-pair drive and sector BC sign are phased against the meshed magnets;
    # and every DERIVED field it carries (slot_width, the radii, the angles) must
    # be recomputed from the primaries it ended up with.  Both halves are
    # leaks in the no-override direction too: motor_config.yaml stores those
    # derived values and the app rewrites them, so a file whose stored
    # slot_width has not caught up with its own wire_width (HEAD's config: 2.5
    # stored, 2.3 derived) meshed the config's OWN machine at the wrong element
    # size.  See merge_geo_override / geometry.motor_geometry.derived_geometry.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    geo = merge_geo_override(geo, geo_override)
    if geo_override:
        p = _params_from_geo_dict(geo)
    else:
        p = params_from_config()
    dom = MotorDomains2D(p)
    # ── Feature-relative mesh refinement (real element sizing, not a fudge) ──
    # mesh_size_mm is an ABSOLUTE target.  On a small motor the UI default
    # (4 mm) leaves ~1 element across a 2.8 mm slot → the field, torque and
    # back-EMF are grossly under-resolved AND mesh-dependent.  Convergence study
    # (12s/14p 40 mm, I=38, γ=−32): at mesh 1.5 mm the result is garbage
    # (T 0.52, KV 484, 175 % ripple); it only plateaus at mesh ≲ slot_width/3
    # (T≈0.565, KV≈585, 11 % ripple from 1.0→0.5 mm).  So clamp the target to
    # resolve the smallest in-plane feature (slot or tooth) with ≥4 elements.
    # This only ever REFINES (min) — motors with large features (e.g. 200 mm)
    # keep their coarser mesh.  Radial air-gap resolution is separate
    # (gap_layers).  No operating-point tuning — pure geometric element sizing.
    # slot_width is DERIVED (wire pitch); it is honest here only because `geo`
    # went through merge_geo_override above, which recomputes it from THIS
    # request's primaries.  Read it off an unmerged config dict and the element
    # size becomes a function of whatever design the shared config holds.
    try:
        _feat_mm = min(float(geo.get("slot_width", 1e9) or 1e9),
                       float(geo.get("tooth_width", 1e9) or 1e9))
        if 0.0 < _feat_mm < 1e8:
            _elem_per_feat = 4.0 if hi_fidelity else 2.0   # normal: 2 elem/feature ceiling (÷4 hi-fi)
            _mesh_feat = max(float(min_size_mm), _feat_mm / _elem_per_feat)
            if _mesh_feat < mesh_size_mm - 1e-9:
                log.info("mesh auto-refined %.2f → %.2f mm (smallest feature "
                         "%.2f mm ÷ %g) — small-motor resolution",
                         mesh_size_mm, _mesh_feat, _feat_mm, _elem_per_feat)
                mesh_size_mm = _mesh_feat
    except Exception as _e:
        log.warning("mesh feature-refine skipped: %s", _e)
    # n_sectors == -1: DIAGNOSTIC full ring — no sector cuts at all (the moving
    # band makes a closed 360° pair of halves feasible: the halves are open
    # annuli, not the historically OCC-double-meshed full cross-section).
    # n_sectors ≤ 1 → FULL RING (NS=1).  -1 was the historical "full ring" flag;
    # 1 ("Full" from the UI) must mean the same — NOT fall through to NS=4, which is
    # an invalid 90° wedge for any motor whose pole count is not a multiple of 4
    # (e.g. 14 poles → 3.5/sector → corrupt anti-periodic BC → spurious torque/ripple).
    _full_ring = (int(n_sectors) <= 1)
    # Geometry-driven mesh currently ships the FULL-RING build only (the CDT
    # is not periodic, so a sector wedge would need clone-identical radial cuts
    # for the anti-periodic master-slave pairing — not yet built).  Force the
    # full ring so a 1/N request still gets the real-fillet geo mesh with sound
    # physics (full disk is the reference anyway) instead of silently reverting
    # to the tensor wedge.
    # geo mesh builds the 1/N wedge directly, but the sector ripple is still WIP
    # → default a geo sector request to the validated FULL RING (correct, just
    # slower); SB_GEO_SECTOR=1 opts into the experimental wedge.
    _use_geo_tr = _SB_GEO_MESH if geo_mesh is None else bool(geo_mesh)
    _tpl_on = (iron_template is None and _SB_IRON_TEMPLATE) or bool(iron_template)
    if (_use_geo_tr and _tpl_on and not _SB_GEO_SECTOR and not _full_ring):
        log.info("geo mesh: full ring for a 1/%d request (sector WIP)", int(n_sectors))
        _full_ring = True
    NS = 1 if _full_ring else int(n_sectors)
    sector_deg = 360.0 / NS
    pole_pairs = p.num_poles // 2
    # Sector boundary sign: ANTI-periodic (−1) only when the sector spans an
    # ODD number of poles (e.g. NS=4 → 7 poles); PERIODIC (+1) for an EVEN pole
    # count (NS=2 → 14 poles).  Mirrors the static solve's `anti_periodic =
    # (poles_per_sector % 2 == 1)`.  Hard-coding −1 here corrupted the 1/2-sector
    # field → 40 %-unbalanced phase-A flux linkage + 70 % torque ripple.
    _poles_per_sector = p.num_poles // NS
    _bc_sign = -1 if (_poles_per_sector % 2 == 1) else 1
    n_parallel = wind.get("n_parallel", 2)
    # The connection LABEL this run is entitled to be reported under: only the
    # one whose parallel-path count matches the paths actually solved.  An
    # explicit n_parallel overrides the label (above), and a stale config label
    # left over from a previous selection would otherwise travel out with the
    # result and name the wrong winding on the summary card.
    _conn_label_used = ""
    try:
        from motor_ai_sim.winding import parse_connection as _pc_lbl
        _lbl = str(wind.get("connection") or "")
        if _lbl and int(_pc_lbl(_lbl)[0]) == int(n_parallel):
            _conn_label_used = _lbl
    except Exception:
        _conn_label_used = ""
    n_wires = int(geo.get("num_wires_per_slot", 14))
    # Physical copper loss (ρ_Cu(coil_temp)·J²·V_cu·k_end, end-winding the 2-D
    # field never sees) is computed a few dozen lines DOWN, right after the CAD
    # polygons exist: the conductor section it divides by is MEASURED on those
    # polygons, not taken from num_wires·wire_width·wire_height, which the CAD
    # does not always deliver (clipped stacks, interpenetrating wires — see
    # `coil_copper_area_total_m2`).  Nothing between here and there reads P_cu
    # or R_phase.
    # Synchronous machine: rpm and f_elec are LOCKED (f = rpm·pp/60).  The
    # config can carry a stale pair (preset-apply wrote rpm but not frequency)
    # — and using the mismatched rpm in ω_mech scaled dB/dt (→ iron/magnet
    # losses) by the wrong speed (×4 at 3950-vs-2000).  rpm is the master
    # (it's what presets/UI write); the frequency is DERIVED, never read.
    # An explicit rpm= argument WINS over the config: that is the per-request
    # channel a candidate/catalog/preset evaluation needs so it does not inherit
    # the shared config's speed (F2).  rpm is read ONCE, here, before the frame
    # loop, so a config reload mid-solve cannot move it.
    _rpm_from_arg = rpm is not None
    rpm = float(rpm) if _rpm_from_arg else float(sim.get("rpm", 3950))
    if rpm <= 0.0:
        raise ValueError(f"rpm must be > 0; got {rpm!r}")
    f_elec = rpm * (p.num_poles // 2) / 60.0
    # The stored frequency is only a cross-check on the CONFIG's own pair.  When
    # the caller passed rpm explicitly, the config's frequency describes a
    # different operating point and comparing against it would warn on every
    # per-request evaluation.
    _f_cfg = 0.0 if _rpm_from_arg else float(sim.get("frequency", 0.0) or 0.0)
    if _f_cfg > 0 and abs(_f_cfg - f_elec) / max(f_elec, 1e-9) > 0.01:
        log.warning("config frequency=%.2f Hz inconsistent with rpm=%.0f "
                    "(→ %.2f Hz); using the rpm-derived frequency",
                    _f_cfg, rpm, f_elec)
    slot_area_m2 = p.slot_width_m * p.slot_height_m * p.fill_factor
    mid = 0.5 * (p.r_rotor_out + p.r_stator_in)

    # d-axis phase offset AUTO-CALIBRATED for this motor topology so γ=0 is the
    # true q-axis and γ equals the physical current angle from the q-axis (=ANSYS
    # el_deg).  Cached per topology; the I=0 calibration run is recursion-guarded.
    # …and it is REPORTED while it runs.  On a geometry the cache has not seen
    # it is a 24-frame no-load solve — measured 39 s on the 200 mm 24s/28p —
    # during which the progress bar showed nothing at all, so pressing Run
    # looked like pressing nothing.  It is not overhead to hide: it is what
    # gives the user's γ a zero to be measured from.
    daxis_eff = _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override,
                                     n_sectors, progress_cb=progress_cb)

    # Imposed excitation (both drives) — simulation/drive.py.  One object carries
    # the electrical frame both the current and the voltage waveform live in, so
    # they cannot drift apart.  It was written TWICE, once per element order, and
    # the two copies disagreed (see simulation/drive.py) — one definition now.
    _exc = _Excitation(pole_pairs=pole_pairs, daxis_deg=daxis_eff,
                       i_peak=float(I_phase_rms) / n_parallel * math.sqrt(2),
                       gamma_deg=gamma_deg,
                       v_peak=float(v_phase_peak), v_delta_deg=v_delta_deg)
    _currents = _exc.currents

    # Voltage drive: imposed sinusoidal PHASE voltage in the same electrical
    # frame as the currents (v_delta_deg is directly comparable to γ), so a
    # clean back-EMF yields near-sinusoidal currents and a distorted one shows
    # its real parasitic harmonic currents + their losses.
    _vdrive = str(drive or "current").strip().lower().startswith("v")
    if _vdrive and rotor_eddy:
        # Voltage drive + conducting-rotor dynamics is NOT implemented: the
        # eddy path imposes coil currents via integral constraints while the
        # voltage circuit needs them as unknowns.  Before this guard the frame
        # loop silently took the no-eddy branch — the vdrive orbit then solved
        # DIFFERENT physics (no magnet-eddy screening, ψ off by ~5 %) than the
        # rotor_eddy current-drive it was compared against, skewing the
        # round-trip fundamental ~10 %/19° and ΔP_harm by the whole P_mag.
        # Explicitly drop eddy on BOTH (the route mirrors this in harm_ref) so
        # voltage runs and their references always share the same physics.
        log.warning("voltage drive: rotor_eddy not supported yet — running "
                    "without conducting-rotor dynamics (magnet/shaft eddy "
                    "losses excluded; ΔP_harm = copper+iron harmonic cost)")
        rotor_eddy = False

    _voltages = _exc.voltages

    # ── High-fidelity = genuinely higher resolution EVERYWHERE the noise lives ─
    # The raw torque ripple of the sliding-band transient carries a BROADBAND
    # numerical floor at the non-6·k orders a balanced 3-φ machine cannot produce.
    # Measured (40 mm 12s/14p) it is a FIELD-level artifact: IDENTICAL on every
    # torque contour (band strip / rotor-surface / stator-surface / whole gap) and
    # NOT removed by any single knob — gap_layers ALONE even makes it worse
    # (20.8 %→25.5 %), steps don't move it (→21.8 %), pole_copy doesn't (→20.3 %).
    # Only RAISING ALL THREE TOGETHER pulls the floor down: tangential band
    # density (slip nodes), radial gap density (gap_layers) and the global mesh.
    # So hi_fidelity bundles all three (mesh ÷8 vs ÷4 above; slip 2× below;
    # gap_layers≥4 here) → measured raw 20.8 %→~14 %, RMS 4.7 %→3.0 %.  This is the
    # honest "spend compute for accuracy" mode, NOT a filter — the real DC torque
    # is unchanged and the 6·k physical ripple already matches Ansys.  gap_layers
    # is bumped ONLY inside this bundle (it is counter-productive on its own).
    if hi_fidelity:
        gap_layers = max(float(gap_layers), 4.0)
    # ── Slip-ring resolution (ADAPTIVE to pole count) ─────────────────────
    # Nodes per electrical period = a multiple of 24 (so 24/30/40/60/120 are all
    # valid step counts) and ≥120, scaled so the full-ring node count stays
    # ≥~1008 (fine tangential spacing → accurate ripple).  n_slip_eff =
    # pole_pairs·per_period is divisible by pole_pairs BY CONSTRUCTION → the rotor
    # advances a whole number of nodes each step (strictly periodic torque) and
    # the electrical period tiles EXACTLY (vs the old fixed 1008 → 100.8/period).
    # Air-gap layers is the SINGLE fidelity regulator (the UI "Air-gap layer" slider):
    # more layers -> more tangential slip nodes -> less node-identification jitter in the
    # eddy loss (the same knob also sets the radial gap density above).  Calibrated so
    # gap_layers=1 -> 1008 (fast) and gap_layers=4 -> 2016 (= the retired hi-fidelity slip);
    # the _slip_per_period rounding below keeps the count pole-pair-divisible for any motor.
    # MEASURED (scripts/_filter_ablation.py, 2026-07-29, p2_load, gap_layers=1 →
    # 144 nodes/period, 505 in the 2-sector wedge).  Forcing a denser ring with
    # SB_SLIP_PER_PERIOD:
    #
    #     ring/period   T_avg        T_ripple        P_fe
    #        144 (dflt)  0.41740      0.53486 %      3.1226 W
    #        216         +0.12 %      -8.55 %        +0.15 %
    #        288         +0.09 %      -4.92 %        -0.04 %
    #        432         +0.13 %      -10.86 %       +0.28 %
    #
    # Mean torque and iron loss are INSENSITIVE to the ring density (≤ 0.3 % over
    # a 3× denser ring, inside the regression's 0.5 % tolerance), so the 1008
    # calibration is not buying accuracy there and is not costing any either.
    # The RIPPLE is a different story: it scatters -5 to -11 % and does not
    # converge monotonically with density, so the reported T_ripple_pct carries a
    # ~10 % ring-density uncertainty that nothing was stating.  Kept as-is
    # (changing the default would move every pinned ripple with no accuracy
    # argument to justify it) — but the number is now on the record instead of
    # implied to be converged.
    _slip_base = int(round(1008.0 * (max(1.0, float(gap_layers)) + 2.0) / 3.0))
    _slip_per_period = 24 * max(5, math.ceil(_slip_base / (24 * pole_pairs)))
    n_slip_eff = pole_pairs * _slip_per_period
    if bool(_SB_AIRGAP_MACRO) or bool(airgap_macro):
        # The harmonic macroelement is ANALYTIC between ring nodes, so a COARSE ring
        # is enough — and its coupling is a DENSE N×N block, so a small N is wanted.
        # 48 nodes/period resolves angular harmonics to 24·pole_pairs (≫ the
        # significant slot/pole orders); the node-identification band needed the
        # fine ≥120/period purely to keep the re-pairing quiet — the macroelement
        # does not.  This is the efficiency lever that makes the dense block cheap.
        # Denser rings are WORSE, not better: they admit high spatial harmonics the
        # real gap damps as e^{−k·g} (measured: ring-48 14.2%, ring-216 15.6±0.7,
        # ring-432 25.7% — ring-48 acts as the physical gap filter).  Applies to
        # sector models too (the wedge ring is n_slip/n_sectors of these nodes);
        # bumped to the next n_sectors multiple so wedge node counts stay integral.
        _slip_per_period = 48
        _ns_abs = max(1, abs(int(n_sectors)))
        while (pole_pairs * _slip_per_period) % _ns_abs:
            _slip_per_period += 1
        n_slip_eff = pole_pairs * _slip_per_period
    if _SLIP_PER_PERIOD_OVERRIDE:        # advanced: force ring density (dev flag —
        # decouples ring-count/mesh convergence studies from the adaptive
        # slip(gap_layers) coupling).  Snapped UP to the 24k grid so the wedge
        # node counts (n_slip/n_sectors) stay integral for sector models too.
        _spo = int(_SLIP_PER_PERIOD_OVERRIDE)
        _slip_per_period = 24 * max(1, math.ceil(_spo / 24.0))
        n_slip_eff = pole_pairs * _slip_per_period

    # ── Snap steps/period so the rotor lands on whole slip nodes ──────────
    # For a uniform (periodic, non-chaotic) rotor advance, n_steps must divide
    # the nodes-per-period.
    _nodes_per_period = _slip_per_period
    _req_steps = int(n_steps_per_period)
    # HARMONIC MACRO: the gap coupling is an ANALYTIC phase e^{i k φ} — valid at
    # ANY rotor angle, no node re-pairing — so the whole-node snap (a node-merge
    # constraint) does not apply: honour the requested steps exactly (the rotor
    # advances a FRACTIONAL number of slip nodes per step; m is float).
    _macro_free_m = bool(airgap_macro) or bool(_SB_AIRGAP_MACRO)
    if _macro_free_m:
        n_steps_per_period = max(1, _req_steps)
    else:
        n_steps_per_period = _snap_steps_to_nodes(_req_steps, _nodes_per_period)
        if n_steps_per_period != _req_steps:
            # WARNING, not INFO: this is a silent substitution of the caller's
            # time resolution.  A log line nobody reads is how "requested 40,
            # ran 36" became invisible to the optimizer (refine_proc computes
            # nspp = round(steps / n_periods), which lands off the divisor grid
            # constantly) and to any API client that is not the Simulation tab
            # (whose picker only offers divisors).  The substitution is also
            # REPORTED in the result dict now — see n_steps_per_period_requested
            # / steps_snapped at the bottom of this function.
            log.warning("SB: snapped steps/period %d -> %d (divisor of %d slip "
                        "nodes/period -> whole-node rotor steps, periodic "
                        "torque); the run is at the SNAPPED resolution",
                        _req_steps, n_steps_per_period, _nodes_per_period)

    # ── Build the two halves ONCE ────────────────────────────────────────
    motor = CadQueryMotor()
    if geo_override:
        motor.set_parameters(geo_override)   # in-memory candidate geometry
    polys = motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle0_deg))
    # ── Physical copper loss, on the copper the CAD actually built ───────
    # The conductor section comes from THESE polygons (the union — the mesher
    # gives every triangle to exactly one wire), so P_cu_dc and the R_phase
    # derived from it describe the same copper the winding source, R_2d and the
    # AC loss run on.  The nominal num_wires·wire_width·wire_height is only the
    # fallback: this CAD clips the stack to fit the slot on some machines
    # (motor_40mm keeps 74.17 %) and lets the wires interpenetrate on others
    # (the 37 mm 24s/28p: union 91.54 % of the sum), and the DC arithmetic used
    # to be wrong by exactly that factor (gate (c),
    # docs/SOLVER_TRIALS_2026-07-30.md).  R_phase stays derived from P_cu so the
    # R·I voltage drop is temperature-consistent — no hard-coded resistance.
    _cu_area_m2 = coil_copper_area_total_m2(polys)
    P_cu, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor,
        copper_area_m2=_cu_area_m2)
    _cu_area_nom_m2 = (float(p.num_slots) * float(n_wires)
                       * float(geo.get("wire_width", 0.0) or 0.0) * 1e-3
                       * float(geo.get("wire_height", 0.0) or 0.0) * 1e-3)
    if _cu_area_nom_m2 > 0 and _cu_area_m2 > 0 and abs(
            _cu_area_m2 / _cu_area_nom_m2 - 1.0) > 1e-3:
        log.warning("conductor section measured on the CAD polygons is %.2f %% "
                    "of nominal (%.4f vs %.4f mm2) -> P_cu_dc / R_phase scale by "
                    "%.4f; the machine that was BUILT has less copper than the "
                    "parameters describe",
                    100.0 * _cu_area_m2 / _cu_area_nom_m2, _cu_area_m2 * 1e6,
                    _cu_area_nom_m2 * 1e6, _cu_area_nom_m2 / _cu_area_m2)
    # STRUCTURED (mapped) gap uses the MERGED band: the route-A cells own the
    # whole gap r_ro→mid→r_si with the SINGLE shared slip ring at mid_r (uniform
    # S·M grid).  The moving-band split (mid±δ, empty re-stitched strip) is
    # incompatible with the cells, so force merged when structured_gap is on —
    # EXCEPT when the harmonic macro is requested: the macro only exists on the
    # MOVING band (K_gap couples the R1/R2 rings analytically), and forcing
    # merged here was exactly why the product's "Harmonic gap" toggle never ran
    # the macroelement (it silently degraded to node-merge on a coarse ring).
    _use_macro_req = bool(airgap_macro) or bool(_SB_AIRGAP_MACRO)
    # Band mode must NOT depend on n_sectors: the full ring used to force
    # "moving" while sectors solved "merged" — two different gap couplings, so
    # ns=1 vs ns=4 disagreed systematically (T +6.7 %, V_peak +22 % on 24s20p;
    # with a shared merged band they match to 0.3 %).  The moving band's
    # one-row strip biases the flux linkage (see _SB_MOVING_BAND note) — keep
    # MERGED as the sole default for every sector count; the macro (analytic
    # gap) and the _SB_MOVING_BAND env flag still opt into "moving" explicitly.
    _band_mode = ("moving" if _use_macro_req
                  else ("merged" if structured_gap
                        else ("moving" if _SB_MOVING_BAND else "merged")))
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=stator_fillet_mm,
                            n_slip=n_slip_eff, gap_layers=gap_layers,
                            structured_gap=structured_gap,
                            band_mode=_band_mode)
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers,
        component_mesh_mm=component_mesh_mm,
        full_ring=_full_ring, pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh)
    Ps, Tts = ms.p.copy(), ms.t.copy(); Pr, Ttr = mr.p.copy(), mr.t.copy()
    nsn = Ps.shape[1]
    Pall = np.hstack([Ps, Pr]); Tall = np.hstack([Tts, Ttr + nsn])
    mesh_all = MeshTri(Pall, Tall)

    def _ring(P, r_at):
        # Slip-ring node selection — simulation/moving_band.py.
        return _slip_ring_nodes(P, r_at, n_slip_eff)
    # MOVING BAND: the halves would end at two DIFFERENT uniform rings — rotor
    # at R1 = mid−δ (rotating rigidly with the rotor mesh), stator at R2 = mid+δ
    # (stationary) — with the annulus between them re-stitched every frame in
    # closed form, or replaced by the analytic harmonic macroelement.
    # NOT IMPLEMENTED ON P2, and P2 is the only basis: the macroelement's
    # per-harmonic rotor↔stator coupling has no edge-midpoint counterpart yet,
    # so there is nothing to stitch the P2 belt's edge DOFs across.  Raised HERE,
    # before the ring selection, rather than after a full mesh build — the answer
    # is the same and it costs nothing to find out.
    _band_radii = polys.get("band_radii_mm")
    if bool(_band_radii) and len(_band_radii) == 2:
        raise NotImplementedError(
            "the moving / harmonic-macro air-gap band is not implemented on P2 "
            "(element_order=2, the only basis); run the merged structured belt "
            "instead: structured_gap=True, airgap_macro=False.")
    rring = _ring(Pr, mid)
    sring = _ring(Ps, mid)
    Nring = min(sring.size, rring.size)
    if sring.size != rring.size:
        log.warning("band ring node counts differ: stator=%d rotor=%d — "
                    "truncating to %d", sring.size, rring.size, Nring)
    sring = sring[:Nring]; rring = rring[:Nring]
    if _full_ring:
        spacing = 360.0 / Nring          # CLOSED ring: N nodes, N intervals
    else:
        spacing = sector_deg / (Nring - 1)

    # Constant radial-cut anti-periodic pairs on the combined mesh.
    if _full_ring:
        Mn, Sn = np.array([], int), np.array([], int)   # no cuts at all
    else:
        Mn, Sn = _pair_sector_cut_nodes(mesh_all, NS)

    # Forms
    @BilinearForm
    def _stiff(u, v, w): return _dot(_grad(u), _grad(v))
    @BilinearForm
    def _stiff_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * _dot(_grad(u), _grad(v))
    @BilinearForm
    def _massform(u, v, w):            # ∫ u·v  — for the σ·∂A/∂t eddy term
        return u * v
    @LinearForm
    def _f1(v, w): return 1.0 * v
    @LinearForm
    def _fdy(v, w): return _grad(v)[1]
    @LinearForm
    def _fdx(v, w): return _grad(v)[0]
    @LinearForm
    def _msrc(v, w):            # magnet source with PER-ELEMENT M (P0 fields):
        return w["mx"] * _grad(v)[1] - w["my"] * _grad(v)[0]   # ∫(Mx·∂v/∂y − My·∂v/∂x)

    # ── Pre-assemble per-tag stiffness K0 + constant magnet source ───────
    matr0 = build_materials(_currents(0.0), dom.winding_layout,
                            getattr(cr, "polys", polys), 0.0, slot_area_m2, n_wires)
    # unit-current stator sources (per phase), magnet source is in rotor half
    # σ per domain tag for the eddy-current mass matrix (temperature-corrected
    # copper).  Solid conductors only — air / laminated iron stay σ=0.
    _sig_cu_T = SIGMA_CU_20 / (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))

    # ── Assigned materials (library) — REAL σ / BH / loss curves ──────────
    # Fetched BEFORE the σ-mass assembly so the eddy solve uses the σ of the
    # materials actually assigned in the UI (e.g. an ALUMINIUM shaft is 2.6e7
    # S/m — 5.7× the hardcoded carbon-steel value).  Falls back to the generic
    # constants when a lookup fails.
    from motor_ai_sim import materials as _mat_lib
    from motor_ai_sim.config import get_material_assignments as _gma
    _ma = _gma() or {}
    try:
        _steel_s = _mat_lib.get_steel(_ma.get("stator_core", "20SW1200"))
        _steel_r = _mat_lib.get_steel(_ma.get("rotor_core",  "20SW1200"))
    except Exception:
        _steel_s = _steel_r = None
    try:
        _magnet_mat = _mat_lib.get_magnet(_ma.get("magnet")) if _ma.get("magnet") else None
    except Exception:
        _magnet_mat = None

    def _lookup_sigma(name: str) -> float:
        for _cat in ("conductor", "steel", "magnet"):
            try:
                return float(getattr(_mat_lib.get_material(_cat, name), "sigma", 0.0) or 0.0)
            except Exception:
                continue
        return 0.0

    _sigma_mag_lib = (float(getattr(_magnet_mat, "sigma", 0.0) or 0.0)
                      if _magnet_mat else 0.0) or SIGMA_NDFEB
    _sigma_shaft_lib = _lookup_sigma(str(_ma.get("shaft", ""))) or SIGMA_SHAFT

    def _sigma_of_tag(t: int) -> float:
        t = int(t)
        if t >= DOM_COIL_BASE or t == DOM_COIL:      return _sig_cu_T
        if t >= DOM_MAG_BASE or t in (DOM_MAG_N, DOM_MAG_S): return _sigma_mag_lib
        if t == DOM_SHAFT:                           return _sigma_shaft_lib
        return 0.0

    half = {}
    for name, (P, T, tags, mats) in (
        ("s", (Ps, Tts, ts, None)), ("r", (Pr, Ttr, tr, matr0))):
        mesh = MeshTri(P, T); b = Basis(mesh, ElementTriP1()); nh = b.N
        K0 = {}; cells = {}; mu0 = {}
        Msig = _csr((nh, nh))            # σ-weighted mass (eddy term), 0 in air/iron
        for tag in np.unique(tags):
            idx = np.where(tags == tag)[0]; cells[int(tag)] = idx
            sb = Basis(mesh, ElementTriP1(), elements=idx)
            K0[int(tag)] = asm(_stiff, sb)
            _sig = _sigma_of_tag(int(tag))
            if _sig > 0.0:
                Msig = Msig + asm(_massform, sb) * _sig
        half[name] = dict(mesh=mesh, b=b, n=nh, K0=K0, cells=cells,
                          Msig=Msig.tocsr())
    # ── magnet source (rotor half, constant — magnets fixed at angle 0) ──
    # Built from a PER-ELEMENT magnetisation field (P0) so it can be de-rated
    # element-by-element by the demag pass below (_br_glob, 1.0 = full Br).
    # With _br_glob ≡ 1 this is numerically identical to the old per-tag sum.
    _nt_r = half["r"]["mesh"].t.shape[1]
    _Mx_glob = np.zeros(_nt_r); _My_glob = np.zeros(_nt_r)
    for tag, idx in half["r"]["cells"].items():
        m = matr0.get(int(tag))
        if m is None or (abs(m.Mx) + abs(m.My)) <= 0:
            continue
        # Stored as the A-formulation SOURCE, i.e. the equivalent coercivity
        # H_c = M/μ_rec = Br/(μ₀·μ_rec) — not M.  See the module docstring;
        # _Mx_glob/_My_glob feed nothing but ``_msrc``.
        _inv_mu = 1.0 / max(float(m.mu_r), 1.0)
        _Mx_glob[idx] = m.Mx * _inv_mu; _My_glob[idx] = m.My * _inv_mu
    # magnet_scale lets the torque decomposition turn the PMs OFF (=0 →
    # reluctance-only torque) or weaken them, without touching geometry.
    _br_glob = np.full(_nt_r, float(magnet_scale))   # per-element Br factor (demag de-rating × magnet_scale)
    # per-phase unit-current stator source vectors
    f_coil = {'A': np.zeros(half["s"]["n"]), 'B': np.zeros(half["s"]["n"]),
              'C': np.zeros(half["s"]["n"])}
    coil_info = []   # (idx, areas, dir, phase, slot_copper_area_m2) for ψ / J-view
    areas_s = _triangle_areas(half["s"]["mesh"])
    # ── The copper the source is ACTUALLY integrated over ────────────────
    # Per coil domain tag, the area its elements cover in THIS mesh.  Handed to
    # build_materials so J_z = dir·I·n_wires / A_copper_of_slot and the machine
    # is excited at exactly n_wires·I ampere-turns per slot.  Until this, the
    # divisor was slot_width_m·slot_height_m·0.6 (a wire-pitch rectangle times a
    # dataclass default no config path set), so every solve ran at k·N·I with
    # k = A_copper/that ∈ 0.909…1.265 and T_maxwell/T_energy was exactly k
    # (docs/SOLVER_TRIALS_2026-07-30.md, F4+F5).
    _coil_area_meshed = {int(tag): float(areas_s[idx].sum())
                         for tag, idx in half["s"]["cells"].items()
                         if int(tag) >= DOM_COIL_BASE}
    _coil_areas = coil_copper_areas(getattr(cs, "polys", polys),
                                    len(dom.winding_layout), _coil_area_meshed)
    if _coil_areas:
        _a_slot_max = max(s for _, s in _coil_areas.values())
        log.info("winding excitation: %d meshed coil tags, slot copper "
                 "%.4g mm² (nominal rectangle %.4g mm², old k=%.4f)",
                 len(_coil_areas), 1e6 * _a_slot_max, 1e6 * slot_area_m2,
                 _a_slot_max / max(slot_area_m2, 1e-12))
    for ph in ('A', 'B', 'C'):
        Iunit = {'A': 0.0, 'B': 0.0, 'C': 0.0}; Iunit[ph] = 1.0
        mats_u = build_materials(Iunit, dom.winding_layout,
                                 getattr(cs, "polys", polys), 0.0, slot_area_m2,
                                 n_wires, coil_area_m2=_coil_area_meshed)
        for tag, idx in half["s"]["cells"].items():
            mu = mats_u.get(int(tag))
            if mu is None or mu.J_z == 0.0:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            f_coil[ph] += asm(_f1, sb) * mu.J_z
    # ψ coil map (phase, dir) per coil tag — from a full-current material build
    mats_full = build_materials(_currents(0.0), dom.winding_layout,
                                getattr(cs, "polys", polys), 0.0, slot_area_m2,
                                n_wires, coil_area_m2=_coil_area_meshed)
    for tag, idx in half["s"]["cells"].items():
        _mt = mats_full.get(int(tag))
        if _mt is None:
            if int(tag) >= 200:      # a coil tag the material map does not know
                log.warning("psi map: unknown coil tag %d (not in material map)", int(tag))
            continue
        nm = _mt.name
        if not nm.startswith("coil_"):
            continue
        # name = "coil_<i>_slot<j>_<phase><+|->"  → phase is the char before +/-
        ph = nm[-2] if nm[-1] in "+-" else nm[-1]
        direction = 1.0 if nm.endswith("+") else -1.0
        if ph in "ABC":
            coil_info.append((idx, areas_s[idx], direction, ph,
                              (_coil_areas.get(int(tag))
                               or (0.0, float(areas_s[idx].sum())))[1]))

    # ── Stage 2: solid-copper current-constrained eddy data ──────────────────
    # Each coil is a SOLID bar: J = σ(−∂A/∂t + U_c) with ∫J dA = I_c imposed.
    # Per coil store: g_c (σ-lumped load, full DOF space), S_c = ∫σ dA, and the
    # imposed-current coefficient I_c_unit = dir·n_wires·(area_c/A_copper_of_slot)
    # so that I_c = Ist[phase]·I_c_unit exactly matches the magnetostatic
    # ampere-turns — the SAME divisor build_materials normalises J_z by, so the
    # two excitation channels cannot drift apart (they did: both carried the
    # nominal slot rectangle and were therefore both off by k).
    _coil_con = []
    if eddy:
        _ones_s = np.ones(half["s"]["n"])
        _nr0 = half["r"]["n"]
        for tag, idx in half["s"]["cells"].items():
            if int(tag) < DOM_COIL_BASE:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            g_s = np.asarray((asm(_massform, sb) * _sig_cu_T) @ _ones_s)
            nm = (mats_full.get(int(tag)) or FEMMaterial("x")).name
            ph = nm[-2] if nm.endswith(("+", "-")) else "A"
            dr = 1.0 if nm.endswith("+") else -1.0
            area_c = float(areas_s[idx].sum())
            _a_slot_c = (_coil_areas.get(int(tag)) or (area_c, area_c))[1]
            _coil_con.append({
                "tag": int(tag),          # domain tag — the P2 branch rebuilds g/S
                                          # on ITS basis and needs the identity of
                                          # the wire this (phase, Iunit) belongs to.
                "g": np.concatenate([g_s, np.zeros(_nr0)]),
                "S": float(g_s.sum()),
                "Iunit": dr * n_wires * area_c / max(_a_slot_c, 1e-12),
                "phase": ph,
                "nodes": np.unique(half["s"]["mesh"].t[:, idx]),   # stator-local node ids
            })

    # ── Rotor-eddy stage: FIELD-BASED magnet eddy losses ─────────────────────
    # The rotor mesh is the rotor's MATERIAL frame (rotation lives in the slip
    # pairing), so dA/dt at a rotor node IS the material ∂A/∂t — no convective
    # term.  Each isolated magnet carries J = σ(−∂A/∂t + U_m) with ∫J dA = 0;
    # U_m is the per-magnet area-mean of ∂A/∂t (uniform σ).  Magnet halves
    # bisected by the sector cut take U = 0 — their (anti)periodic image
    # cancels the net axial current by symmetry.
    #
    # IMPLEMENTATION: the rotor-frame A(t) history is post-processed by
    # eddy_solver_2d.honest_rotor_eddy — a frequency-domain solve that INCLUDES
    # the eddy reaction.  A naive in-loop σ|∂A/∂t|² integral was tried first and
    # rejected: the raw frame-to-frame ∂A/∂t rides on the slip-ring node-merge
    # jitter and the square AMPLIFIES it with the step count (P_mag tripled
    # going 24→72 steps).  The retired P1 path answered that with a smoothed
    # angle-derivative over the unique slip-node positions (simulation/
    # angle_ddt.py); on P2 the field is smooth enough in time that the harmonic
    # solve is driven directly.
    # σ comes from the ASSIGNED magnet material (library), not a constant.
    _rot_con = []            # bordered ∫J=0 rows — only for the eddy J-VIEW mode
    _rot_sig_nodes = []      # (nodes_global, σ) per rotor group — J snapshot
    _mag_groups = []         # per magnet: element triplets/areas for the loss
    _shaftnode_glob = np.array([], int) # global DOF ids of the shaft nodes
    if rotor_eddy:
        _ones_r = np.ones(half["r"]["n"])
        _areas_r_re = _triangle_areas(half["r"]["mesh"])
        _mag_tags = [int(t) for t in half["r"]["cells"]
                     if (matr0.get(int(t)) is not None
                         and (abs(matr0[int(t)].Mx) + abs(matr0[int(t)].My)) > 0)]
        _mag_area = {t: float(_areas_r_re[half["r"]["cells"][t]].sum())
                     for t in _mag_tags}
        _med_area = float(np.median(list(_mag_area.values()))) if _mag_area else 0.0
        _magnode_loc = (np.unique(np.concatenate(
            [half["r"]["mesh"].t[:, half["r"]["cells"][t]].ravel()
             for t in _mag_tags])) if _mag_tags else np.array([], int))
        _n_interior = _n_halves = 0
        for t in _mag_tags:
            idx = np.asarray(half["r"]["cells"][t], int)
            tri = half["r"]["mesh"].t[:, idx]                  # (3, E) rotor-local
            is_half = bool(_med_area > 0 and _mag_area[t] < 0.6 * _med_area)
            _mag_groups.append({
                "tri": np.searchsorted(_magnode_loc, tri),     # → magnet-node idx
                "areas": _areas_r_re[idx].astype(float),
                "half": is_half,
            })
            nds = np.unique(tri)
            _rot_sig_nodes.append((nds + nsn, _sigma_mag_lib))
            if is_half:
                _n_halves += 1               # edge half-magnet → U = 0
                continue
            _n_interior += 1
            # ∫J=0 constraint row — used only by the coupled eddy J-VIEW mode.
            sb = Basis(half["r"]["mesh"], ElementTriP1(), elements=idx)
            g_r = np.asarray((asm(_massform, sb) * _sigma_mag_lib) @ _ones_r)
            _rot_con.append({"tag": int(t),     # see _coil_con["tag"] — the P2
                                                # branch reads the INTERIOR-magnet
                                                # tag set from here (the cut halves
                                                # are the complement, U = 0).
                             "g": np.concatenate([np.zeros(nsn), g_r]),
                             "S": float(g_r.sum()),
                             "nodes": nds + nsn})
        _sh_idx = np.asarray(half["r"]["cells"].get(int(DOM_SHAFT),
                                                    np.array([], int)), int)
        if _sh_idx.size:
            _sh_tri = half["r"]["mesh"].t[:, _sh_idx]            # (3, E) rotor-local
            _shaftnode_loc = np.unique(_sh_tri)
            _shaftnode_glob = _shaftnode_loc + nsn
            _rot_sig_nodes.append((_shaftnode_glob, _sigma_shaft_lib))
        log.info("rotor-eddy: %d interior magnets (∫J=0), %d edge halves (U=0) | "
                 "σ_mag=%.3g σ_shaft=%.3g S/m (library)",
                 _n_interior, _n_halves, _sigma_mag_lib, _sigma_shaft_lib)

    # ── Loss bookkeeping — iron Bertotti from the ACTUAL B(t) ────────────────
    # The sliding-band run gives a clean B(t) per element over a full electrical
    # period, so instead of the remesh path's single-snapshot Bertotti we use
    # the genuine time-derivative of the field:
    #   • classical eddy  ∝ ⟨(dB/dt)²⟩  (frequency-correct for ALL harmonics —
    #     slot ripple included — because faster flux ⇒ larger dB/dt ⇒ ∝ f²)
    #   • hysteresis      ∝ f·B_ac²     (B_ac = AC excursion, so a DC-biased
    #     rotor tooth contributes only its ripple, not its standing flux)
    # The Bertotti coefficients (kh,kc,ke) are FITTED to the material's measured
    # loss-vs-frequency curves at runtime (materials.effective_bertotti), so this
    # IS the real frequency-dependent loss model.  (Steel/magnet materials were
    # already fetched above, before the σ-mass assembly.)
    # The MAGNET eddy slab model (σ·d²/12·⟨(dB/dt)²⟩ over the magnet's tangential
    # width) went with the P1 loss chain: magnet and shaft loss come from the
    # reaction-included rotor solve (eddy_solver_2d.honest_rotor_eddy) or, with
    # eddy=True, from ∫σE² of the coupled field — both measure the loss instead
    # of estimating it, so there is no slab dimension left to choose.
    areas_r = _triangle_areas(half["r"]["mesh"])
    _iron_s_idx = np.asarray(half["s"]["cells"].get(int(DOM_STATOR), np.array([], int)), int)
    _iron_r_idx = np.asarray(half["r"]["cells"].get(int(DOM_ROTOR),  np.array([], int)), int)
    _mag_parts = []
    for _tag, _idx in half["r"]["cells"].items():
        _m = matr0.get(int(_tag))
        if _m is not None and (abs(_m.Mx) + abs(_m.My)) > 0:
            _mag_parts.append(np.asarray(_idx, int))
    _mag_idx = np.concatenate(_mag_parts) if _mag_parts else np.array([], int)
    # Irreversible demagnetisation state — built HERE, before either element
    # order branches, because both need it.  P2 used to raise NotImplementedError
    # on demag purely because this object was constructed further down, past the
    # point where the P2 branch returns.  Nothing about the physics was in the
    # way: the P2 magnet source below already multiplies by _br_glob, so a
    # weakened magnet was always going to be honoured once something weakened it.
    _dmst = (_MagnetDemag(half["r"]["cells"], matr0, half["r"]["mesh"], _br_glob)
             if (demag and _mag_idx.size) else None)
    if _dmst is not None and not _dmst.active:
        _dmst = None                      # no magnet carries a usable curve
    # Non-laminated solid conductors that ALSO carry rotating-field eddy losses
    # (in addition to magnets): the COILS (solid copper bars, stator side) and
    # the SHAFT (solid steel, rotor side).
    _coil_parts = [np.asarray(_i, int) for _t, _i in half["s"]["cells"].items()
                   if int(_t) >= DOM_COIL_BASE or int(_t) == int(DOM_COIL)]
    _coil_idx = np.concatenate(_coil_parts) if _coil_parts else np.array([], int)

    SAT = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    # Per-tag base μ_r (air=1, coil=1, magnet=μ_rec, iron=μ_steel) + BH curves
    # for the saturable iron tags only.
    mu0 = {"s": {}, "r": {}}
    sat_bh = {"s": {}, "r": {}}
    for hn, md in (("s", mats_full), ("r", matr0)):
        for tag in half[hn]["cells"]:
            m = md.get(int(tag))
            mu0[hn][int(tag)] = max(float(m.mu_r), 1.0) if m else 1.0
            if (tag in SAT) and m and m.bh_curve and len(m.bh_curve) >= 2:
                sat_bh[hn][int(tag)] = m.bh_curve

    # Per-element saturation: the CONSTANT (non-iron) stiffness is pre-summed
    # once; the saturable iron tags are re-assembled each Picard iteration with
    # an element-wise reluctivity ν(x) so every triangle gets its own μ(|B|)
    # from the B-H curve — no single lumped μ that over- or under-saturates the
    # whole domain.
    K_const = {}; sb_sat = {"s": {}, "r": {}}
    b0_sat = {"s": {}, "r": {}}; nu_el = {"s": {}, "r": {}}
    for hn in ("s", "r"):
        h = half[hn]
        Kc = _csr((h["n"], h["n"]))
        for tag, Kd in h["K0"].items():
            if tag in sat_bh[hn]:
                idx = h["cells"][tag]
                _sbi = Basis(h["mesh"], ElementTriP1(), elements=idx)
                sb_sat[hn][tag] = _sbi
                b0_sat[hn][tag] = _sbi.with_element(ElementTriP0())
                nu_el[hn][tag] = np.full(
                    idx.size, 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
            else:
                Kc = Kc + Kd * (1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
        K_const[hn] = Kc.tocsr()

    r_all = np.hypot(Pall[0], Pall[1])

    # ── Frame loop ───────────────────────────────────────────────────────
    n_total = max(1, int(round(n_steps_per_period * n_periods)))
    # Voltage drive: the currents are STATE (start at 0), so the run has an
    # electrical start-up transient.  Run ONE extra settling period and discard
    # its frames after the loop — every reported series/metric is steady-state.
    # dt = T_elec·n_periods/n_total is invariant under the dual bump.
    _vskip = 0
    _v_nspp = int(round(n_steps_per_period))
    if _vdrive:
        # TEN settling periods with ITERATED Aitken.  The electrical time
        # constant L/R spans many periods on a low-R machine, so a marched DC
        # start-up decays too slowly to shed by brute force.  Instead: the
        # phasor init lands near the orbit, then the period-boundary flux
        # (which converges GEOMETRICALLY) is Δ²-extrapolated to its limit at
        # every 3rd boundary (anchors at periods 3, 6, 9 — each application
        # cuts the residual DC ~3×), and the final settling period runs after
        # the last anchor so the reported window starts on a clean orbit.
        # Settling frames use a REDUCED Picard depth (the DC dynamics only
        # need L roughly right); the last settling period + the reported
        # window run at full depth.
        _v_settle_periods = 10
        _vskip = _v_settle_periods * max(2, _v_nspp)
        n_periods = float(n_periods) + float(_v_settle_periods)
        n_total += _vskip
    # Demagnetisation: SETTLING pass, then a clean measurement pass.
    # The magnet weakens THROUGH the run, so a single pass measures a machine
    # whose magnets are still changing: torque decays across the window and the
    # reported "ripple" is mostly that decay (NdFeB measured 19 % with demag vs
    # 9 % without — the extra 10 points were the magnet dying, not a physical
    # oscillation).  Run one extra period first so the de-rating settles, then
    # discard those frames.  Every reported number then comes from the second
    # pass, on a magnet that has stopped moving.
    _dmskip = 0
    if demag and _mag_idx.size and _v_nspp > 1 and n_total > 1:
        _dmskip = _v_nspp
        n_periods = float(n_periods) + 1.0
        n_total += _dmskip
    period_mech = 360.0 / pole_pairs                      # one electrical period [deg mech]
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total

    # ═══════════════════════════════════════════════════════════════════════
    #  SLIDING-BAND TRANSIENT — P2 (quadratic) elements
    # ═══════════════════════════════════════════════════════════════════════
    # B = curl A is LINEAR per element instead of piecewise-constant, so the
    # Arkkio air-gap torque is SMOOTH where the retired P1 basis staircased.
    # The blocker solved here is the moving-cut EDGE-MIDPOINT DOF stitching:
    # P2 puts a dof on every
    # element edge, including the belt's rotor/stator interface edges, so the
    # signed union-find that welds the slip cut must pair those edge midpoints
    # (not just the vertices) as the rotor shifts by m slip nodes.  Assembled on
    # the SINGLE stitched mesh (mesh_all) — simplest correct P2 assembly — with a
    # per-frame P2 projection Pro2 that welds ring vertices AND ring-edge
    # midpoints (and, for a SECTOR wedge, the radial-cut vertices + cut-edge
    # midpoints with the anti-periodic _bc_sign).  Works for the full ring AND
    # anti-periodic sector wedges (validated: sector T_avg == full-ring T_avg to
    # 0.3 %).  Magnetostatic per frame by DEFAULT — that is the physics the
    # cogging / ripple goal needs; eddy=True adds the coupled σ·∂A/∂t term on the
    # solid conductors (see the two COUPLED EDDY blocks below) and the frame then
    # solves a genuine magnetodynamic step instead.
    from skfem import ElementTriP2 as _P2E
    from motor_ai_sim.simulation.p2_nonlinear import (
        P2Nonlinear as _P2Nonlinear, stiff_nu2 as _stiff_nu2,
    )
    from motor_ai_sim.simulation.p2_drive import P2Drive as _P2Drive
    from motor_ai_sim.simulation.p2_projection import (
        SlipProjection as _SlipProjection,
    )
    # ONE persistent MKL PARDISO solver for the whole run: it caches the
    # symbolic factorization and reuses it across the same-pattern Picard
    # sweeps of a frame (re-analysing only when the pattern changes — new
    # frame / new slip pairing).  None ⇒ pypardiso unavailable ⇒ SuperLU.
    try:
        if _os_sb.environ.get("SB_NO_PARDISO") == "1":
            raise ImportError("disabled via SB_NO_PARDISO")
        import pypardiso as _pypard2
        # PERF — 12.5 s per transient, spent looking for a file.
        # PyPardisoSolver.__init__ locates mkl_rt with ctypes.util.find_library
        # and, when that returns None (it does on this Windows/CPython layout —
        # measured, both 'mkl_rt' and 'mkl_rt.1' come back None in 0.01 s), it
        # falls back to a RECURSIVE glob of sys.prefix/[Ll]ib*/**.  cProfile on
        # a 4-step demag+eddy run: 211 290 directory reads, 12.5 s, on EVERY
        # solve, before one element is assembled — 30 % of a short run and
        # 12.5 s × N of any optimizer batch that evaluates in-process.
        # The library path is a process constant, and the module-level solver
        # pypardiso builds at import time has already paid for the search, so
        # publish its answer through the env var __init__ consults FIRST.  Every
        # later construction then goes straight to ctypes.CDLL (measured 0.00 s).
        # Nothing about the solve changes: same class, same one-instance-per-run
        # lifetime, same MKL library — only the search for it is skipped.
        if not _os_sb.environ.get("PYPARDISO_MKL_RT"):
            try:
                _mkl_rt_path = _pypard2.scipy_aliases.pypardiso_solver.libmkl._name
                if _mkl_rt_path:
                    _os_sb.environ["PYPARDISO_MKL_RT"] = str(_mkl_rt_path)
            except Exception:   # older/rearranged pypardiso — just pay the glob
                pass
        _pardiso2 = _pypard2.PyPardisoSolver()
    except Exception as _pae:
        log.info("pypardiso unavailable (%s) — using SuperLU for P2", _pae)
        _pardiso2 = None
    b2 = Basis(mesh_all, _P2E())
    b2_0 = b2.with_element(ElementTriP0())      # P0 for per-element ν interpolate
    N2 = b2.N
    # Early-stop tolerance on the ν fixed point for every successive-
    # substitution loop in this branch: the magnetostatic Picard FALLBACK, the
    # voltage-drive p2_drive.v_picard fallback, and the dq phasor initialiser.
    #
    # It was 6e-3, six times looser than the retired P1 module's 1e-3, on the
    # argument that "T_avg is flat to <0.3 % between residual 0.03 and 0.007".
    # That was true and beside the point: T_avg is an integral of B, while the
    # RIPPLE and the dB/dt-derived losses are differences of B, and differences
    # do not inherit an integral's insensitivity.  MEASURED — the pinned
    # p2_load case (30 mm 12s14p, 60 A, F45SH_120C, 12 steps, 1.4 mm mesh) with
    # SB_NO_NEWTON=1 so this loop IS the solver on every frame, each row read
    # against the converged 1e-4 column:
    #
    #   tol     T_avg [Nm]   ripple [%]   P_fe [W]   P_cu_ac [W]  sweeps  s
    #   6e-3    0.419736     1.2349       3.18078    3.42931       41.0   53
    #   1e-3    0.419978     1.1286       3.17800    3.44071       60.7   72
    #   3e-4    0.420014     1.1223       3.17917    3.44250       81.5   92
    #   1e-4    0.420020     1.1268       3.17959    3.44283       95.5  111
    #
    #   error vs 1e-4:  T_avg    ripple    P_fe      P_cu_ac
    #     6e-3          -0.063 %  +9.60 %  +0.037 %  -0.394 %
    #     1e-3          -0.010 %  +0.16 %  -0.050 %  -0.061 %
    #     3e-4          -0.002 %  -0.40 %  -0.013 %  -0.009 %
    #
    # So 6e-3 costs ~10 % of the TORQUE RIPPLE — the quantity every design run
    # here is optimised against — and 0.4 % of the AC copper, for 20 saved
    # sweeps on a path that (since the cold-frame Newton seed below) no longer
    # runs on a healthy magnetostatic solve at all.  1e-3 buys ripple back to
    # 0.2 % and every loss to 0.06 %, and still finishes inside the iteration
    # cap (max 75 sweeps of 100); 3e-4 does NOT — it hits the cap and reports
    # picard_converged=False, which is the opposite of the point.  Hence 1e-3,
    # and the number is now measured rather than asserted.
    _PIC_TOL2 = 1e-3
    # COLD-FRAME NEWTON SEED.  Frame 0 starts from A = 0, where ∇A = 0 makes
    # the Newton tangent identically zero — so its first "Newton" step IS the
    # unsaturated linear solve, a ~30× residual overshoot into deep saturation
    # that six backtracks cannot walk back (measured at 32 A: it1 accepted
    # λ=1/32 for rrel 1.00→0.96, it2 exhausted the line search and the frame
    # fell into the Picard fallback at 5.1e-3 while every warm-started frame
    # reached 1e-7).  A handful of damped-Picard sweeps — globally convergent,
    # no tangent to be wrong about — puts the guess inside Newton's basin for
    # LESS work than the fallback it replaces.  The seed is a starting point
    # only: the frame is still accepted on the Newton field residual.
    _PIC_SEED_MAX = 12          # sweeps; the seed does not have to converge
    _PIC_SEED_TOL = 5e-2        # ...it only has to reach Newton's basin
    nst = int(Tts.shape[1])                      # rotor elems in mesh_all are +nst
    n_all_el = int(mesh_all.t.shape[1])

    # ── vertex & edge dof maps ───────────────────────────────────────────
    vdof = b2.nodal_dofs[0]                       # global vertex id -> P2 dof
    fdof = b2.facet_dofs[0]                       # facet (edge) id  -> P2 dof
    # ── P2 sources on the stitched mesh ──────────────────────────────────
    # magnet: per-element M over ALL elements (rotor block offset by nst)
    _mx_all = np.zeros(n_all_el); _my_all = np.zeros(n_all_el)
    _mx_all[nst:] = _Mx_glob * _br_glob          # _br_glob folds magnet_scale
    _my_all[nst:] = _My_glob * _br_glob
    f_mag2 = asm(_msrc, b2, mx=b2_0.interpolate(_mx_all),
                 my=b2_0.interpolate(_my_all))
    # per-phase UNIT-current stator coil sources
    f_coil2 = {'A': np.zeros(N2), 'B': np.zeros(N2), 'C': np.zeros(N2)}
    for _ph in ('A', 'B', 'C'):
        _Iu = {'A': 0.0, 'B': 0.0, 'C': 0.0}; _Iu[_ph] = 1.0
        _mu = build_materials(_Iu, dom.winding_layout,
                              getattr(cs, "polys", polys), 0.0,
                              slot_area_m2, n_wires,
                              coil_area_m2=_coil_area_meshed)
        for tag, idx in half["s"]["cells"].items():
            m_ = _mu.get(int(tag))
            if m_ is None or m_.J_z == 0.0:
                continue
            sb = Basis(mesh_all, _P2E(), elements=np.asarray(idx, int))
            f_coil2[_ph] += asm(_f1, sb) * m_.J_z

    # ── per-element base ν + saturable element sets (mesh_all element ids) ─
    nu_base2 = np.empty(n_all_el)
    for _hn, _off in (("s", 0), ("r", nst)):
        for tag, idx in half[_hn]["cells"].items():
            nu_base2[np.asarray(idx, int) + _off] = 1.0 / (
                MU0 * max(mu0[_hn].get(int(tag), 1.0), 1.0))
    _sat2 = []          # (elem_ids_in_mesh_all, bh_curve)
    for _hn, _off in (("s", 0), ("r", nst)):
        for tag, curve in sat_bh[_hn].items():
            _sat2.append((np.asarray(half[_hn]["cells"][tag], int) + _off, curve))

    # ── CONSTANT/VARIABLE stiffness split (perf) ─────────────────────────
    # The non-saturable ν (air, magnet, coil, shaft, non-iron) never changes
    # across frames OR Picard sweeps → assemble that whole-mesh stiffness ONCE
    # (K_const2, iron elements zeroed).  Each Picard sweep then re-assembles
    # ONLY the saturable-iron tags on their element sub-bases and adds them —
    # exactly like the P1 K_const + per-tag path.  Cuts the per-sweep assembly
    # from the whole mesh to the iron fraction.
    _nu_const2 = nu_base2.copy()
    for _ids, _c in _sat2:
        _nu_const2[_ids] = 0.0
    K_const2 = asm(_stiff_nu2, b2, nu=b2_0.interpolate(_nu_const2)).tocsr()
    _sat_sub2 = []       # (sub_basis, sub_P0_basis, elem_ids, bh_curve)
    for _ids, _c in _sat2:
        _sb2 = Basis(mesh_all, _P2E(), elements=_ids)
        _sat_sub2.append((_sb2, _sb2.with_element(ElementTriP0()), _ids, _c))

    # ── frame-independent solver helpers ─────────────────────────────────
    # The saturable-iron assembly, the Newton tangent and the damped-Picard
    # sweep — simulation/p2_nonlinear.py.  ONE object so the magnetostatic,
    # voltage-drive, eddy and phasor paths below cannot end up on different
    # nonlinearities; it owns the PARDISO handle for the same reason.
    # These used to be defined INSIDE the frame loop; they close over nothing
    # frame-specific (K_const2, _sat_sub2, _sat2, b2), and the voltage-drive
    # phasor initialiser below has to call them BEFORE the loop starts.
    _p2 = _P2Nonlinear(basis=b2, n_dof=N2, K_const=K_const2, sat=_sat2,
                       sat_sub=_sat_sub2, pardiso=_pardiso2, log=log)

    # ── outer Dirichlet: facet-based so P2 edge midpoints are pinned too ──
    _out_fac2 = mesh_all.facets_satisfying(
        lambda x: np.hypot(x[0], x[1]) >= r_all.max() - 5e-4)
    _D2_ids = np.asarray(b2.get_dofs(facets=_out_fac2).flatten(), int)

    # The slip pairing and the per-frame projection Pro live in
    # simulation/p2_projection.py: the rotor moves in exactly one place in this
    # solver, and a sign or an off-by-one in the weld is indistinguishable
    # downstream from a physics error.
    _proj = _SlipProjection(
        n_dof=N2, facets=mesh_all.facets, vdof=vdof, fdof=fdof, rring=rring,
        sring=sring, nsn=nsn, n_ring=Nring, full_ring=_full_ring,
        bc_sign=_bc_sign, Mn=Mn, Sn=Sn, dirichlet_dofs=_D2_ids)
    log.info("P2 belt: N2=%d dofs, %s, ring=%d nodes, %d/%d ring-edge + "
             "%d cut-vertex + %d cut-edge midpoints paired",
             N2, "full ring" if _full_ring else "sector",
             Nring, _proj.n_redge, _proj.n_re_pairs, _proj.n_cut_v,
             _proj.n_cut_e)

    # ═══════════════════════════════════════════════════════════════════
    #  COUPLED EDDY-CURRENT DATA (σ·∂A/∂t) — SOLID CONDUCTORS, P2
    # ═══════════════════════════════════════════════════════════════════
    # Every SOLID (non-laminated) conductor is meshed and solved as a solid
    # bar carrying  J = σ(−∂A/∂t + U_b), one unknown voltage U_b per body:
    #
    #   • stator copper — ONE body per WIRE (tags ≥ DOM_COIL_BASE), each with
    #     its share of the phase current imposed exactly (∫J dΩ = I_b) while
    #     the eddy reaction redistributes J inside the wire.  I_b uses the
    #     SAME Iunit the P1 eddy path uses (read off _coil_con), so the two
    #     orders drive identical ampere-turns.
    #   • magnets / shaft (rotor_eddy) — net ZERO axial current per connected
    #     body (∫J dΩ = 0).  A body CUT by the anti-periodic radial boundary
    #     is exempt with U_b ≡ 0, and that is EXACT rather than a convenience:
    #     its image across the cut carries −A, so the full body's ∮J already
    #     vanishes identically and the constraint would over-determine the
    #     wedge.  P1 makes the same split (cut magnet halves + the centred
    #     shaft) and the interior/half classification is read off _rot_con so
    #     the two orders never disagree about which body is which.
    #   • laminated iron stays σ = 0 — a 2-D model cannot resolve eddies at
    #     the laminate scale, so its loss is Bertotti (material data), not a
    #     field solve.  Air is σ = 0 by construction.
    #
    # Assembled on the SAME stitched mesh as the field (stator elements
    # 0…nst−1, rotor elements +nst), so no interpolation ever enters.
    _ed_con = []             # constrained bodies: dicts(key, tag, g, S, …)
    _Msig2 = _csr((N2, N2))  # Σ σ·∫u·v over ALL conductors (backward-Euler term)
    _Msig_grp = {}           # loss split: "cu" / "mag" / "shaft" → σ-mass block
    _Msd2 = _csr((N2, N2))   # Msig/dt
    _G2 = _csr((N2, 0))      # columns g_b = σ·M_b·1  (∫σ·u over body b)
    _Sdt2 = np.zeros(0)      # S_b·dt with S_b = ∫σ dΩ
    if eddy:
        from scipy.sparse import bmat as _bmat2, diags as _diags2
        _ones_e = np.ones(N2)
        _coil_meta = {int(c["tag"]): c for c in _coil_con}
        _int_mag = {int(c["tag"]) for c in _rot_con} if rotor_eddy else set()
        _bodies = []          # (group_key, tag, mesh_all element ids, σ)
        for _tg, _ix in half["s"]["cells"].items():
            _sg = _sigma_of_tag(int(_tg))
            if _sg > 0.0:
                _bodies.append(("cu", int(_tg), np.asarray(_ix, int), _sg))
        if rotor_eddy:
            for _tg, _ix in half["r"]["cells"].items():
                _sg = _sigma_of_tag(int(_tg))
                if _sg <= 0.0:
                    continue
                _bodies.append(
                    ("shaft" if int(_tg) == int(DOM_SHAFT) else "mag",
                     int(_tg), np.asarray(_ix, int) + nst, _sg))
        _n_free_b = 0
        _gcols = []
        for _ky, _tg, _ids, _sg in _bodies:
            _Mb = (asm(_massform, Basis(mesh_all, _P2E(), elements=_ids))
                   * float(_sg)).tocsr()
            _Msig2 = _Msig2 + _Mb
            _Msig_grp[_ky] = (_Mb if _ky not in _Msig_grp
                              else _Msig_grp[_ky] + _Mb)
            # U ≡ 0 bodies: cut by the anti-periodic boundary (their image
            # cancels the net current identically) — no constraint row.
            if _ky == "mag" and _tg not in _int_mag:
                _n_free_b += 1
                continue
            if _ky == "shaft" and not _full_ring:
                _n_free_b += 1
                continue
            _g = np.asarray(_Mb @ _ones_e).ravel()
            _cm = _coil_meta.get(_tg) if _ky == "cu" else None
            _ed_con.append({
                "key": _ky, "tag": _tg, "g": _g, "S": float(_g.sum()),
                "Iunit": float(_cm["Iunit"]) if _cm else 0.0,
                "phase": (_cm["phase"] if _cm else None),
            })
            _gcols.append(_g)
        if _gcols:
            _G2 = _csr(np.column_stack(_gcols))
            _Sdt2 = np.array([c["S"] for c in _ed_con], float) * dt
        _Msd2 = (_Msig2 * (1.0 / dt)).tocsr()
        log.info("P2 eddy: %d constrained bodies (%d copper wires, %d rotor "
                 "∫J=0), %d U=0 cut bodies | σ_cu=%.3g σ_mag=%.3g "
                 "σ_shaft=%.3g S/m",
                 len(_ed_con), sum(1 for c in _ed_con if c["key"] == "cu"),
                 sum(1 for c in _ed_con if c["key"] != "cu"), _n_free_b,
                 _sig_cu_T, _sigma_mag_lib, _sigma_shaft_lib)
        if not _ed_con:
            raise RuntimeError(
                "eddy=True but no solid conductor was found on the P2 mesh "
                "— nothing to constrain (check the coil domain tags).")
        # ── PER-ELEMENT σE² — the loss MAP's copper/magnet/shaft ─────────────
        # The block above collapses the Joule loss to ONE number per body
        # group; the Loss map needs the same integrand kept per element.  The
        # eddy current does not fill a conductor uniformly — it crowds at the
        # corners and edges facing the changing field, which is what an Ansys
        # Total-Loss plot shows at every magnet corner and what the slab
        # |dB/dt|² model, normalised to an average, can never show: that model
        # is smooth by construction.
        #
        # SAME quadrature and SAME σ as the mass matrix above, so the per-
        # element numbers sum EXACTLY to the per-body watts (cross-checked in a
        # log line after the loop) — this is the same integral, not a second
        # model of it.
        _ed_elems = np.sort(np.unique(np.concatenate(
            [_ids for _ky, _tg, _ids, _sg in _bodies])))
        _ed_basis = Basis(mesh_all, _P2E(), elements=_ed_elems)
        _ed_dx = _ed_basis.dx                       # (n_cond_elem, n_qp)
        _ed_area = _ed_dx.sum(axis=1)
        _ed_sig_e = np.zeros(_ed_elems.size)
        _ed_key_e = np.empty(_ed_elems.size, dtype=object)
        _ed_loc_by_tag = {}
        for _ky, _tg, _ids, _sg in _bodies:
            _loc = np.searchsorted(_ed_elems, np.asarray(_ids, int))
            _ed_loc_by_tag[int(_tg)] = _loc
            _ed_sig_e[_loc] = float(_sg)
            _ed_key_e[_loc] = _ky
        # For each constraint, WHICH rows of the conductor-element arrays carry
        # its U_b.  A body with no constraint row (a magnet half cut by the
        # anti-periodic boundary) appears in no list, so its U stays 0 — which
        # is exactly what "U ≡ 0 cut body" means.
        _ed_uloc = [_ed_loc_by_tag[int(_c["tag"])] for _c in _ed_con]
        _ed_gmask = {_k: (_ed_key_e == _k) for _k in ("cu", "mag", "shaft")}

    # ── P2 flux linkage (EXACT area-average per stator coil element) ─────
    # A P2 field's area average over a triangle is the mean of its three
    # EDGE-MIDPOINT dofs, NOT the mean of its vertex dofs: the quadratic
    # vertex shape function N_i = λ_i(2λ_i−1) integrates to EXACTLY ZERO over
    # the element, while each edge bubble 4λ_jλ_k integrates to area/3.  So
    #     (1/Ω)∫A dΩ = (A_e1 + A_e2 + A_e3)/3.
    # Using the P1 centroid formula (vertex mean) on a P2 field is not an
    # approximation, it is the wrong quadrature (checked against a 6th-order
    # quadrature on an analytic quadratic: the edge rule is exact to 4e-16
    # relative, the vertex rule is off by ~5 % even on a uniform mesh).
    #
    # This is also the ONLY choice that keeps ψ energy-consistent with the
    # circuit: f_coil2 = ∫ N_i J_z dΩ puts EXACTLY ZERO on the vertex dofs
    # (asm of the unit load on ElementTriP2 returns 3e-17 there) and area/3
    # on each edge dof, so f_coil2·A ≡ J_z·Σ_e area_e·(edge mean).  ψ and the
    # coil source therefore integrate the SAME functional; the old ψ was
    # built from precisely the dofs the source cannot excite.
    _As_e = fdof[mesh_all.t2f[:, :nst]]            # (3, nst) stator edge dofs
    _sc_psi2 = p.stack_length * NS / float(n_parallel)

    def _psi2(A2):
        Ae_ = A2[_As_e]
        A_tri = (Ae_[0] + Ae_[1] + Ae_[2]) / 3.0
        pa = pb = pc = 0.0
        for idx_, ar_, dir_, ph_, _as_ in coil_info:
            sa_ = float(np.sum(ar_))
            if sa_ <= 0:
                continue
            v_ = dir_ * float(np.sum(A_tri[idx_] * ar_)) / sa_
            if ph_ == 'A':   pa += v_
            elif ph_ == 'B': pb += v_
            else:            pc += v_
        return _sc_psi2 * pa, _sc_psi2 * pb, _sc_psi2 * pc

    # ═══════════════════════════════════════════════════════════════════
    #  VOLTAGE DRIVE on P2 — field ↔ circuit coupled solve
    # ═══════════════════════════════════════════════════════════════════
    # A circuit is physics: it cannot depend on the element order, so this is
    # a PORT of the P1 formulation, not a second model.  Everything that was
    # paid for in P1 debugging is kept verbatim in form:
    #   * LINE-TO-LINE equations (floating neutral).  Phase-voltage equations
    #     pin the machine neutral to the source's, short the zero-sequence
    #     back-EMF (large on this concentrated winding) through the tiny
    #     zero-sequence inductance and produce ~43 % fake triplen current.
    #   * Crank–Nicolson in ROTOR TIME: Δt_k = Δθ_eff/ω with V sampled at the
    #     midpoint of the ACTUAL (slip-node-snapped) motion.
    #   * A dq phasor initialiser whose inductances are measured AT the
    #     operating point (Lq moves ~5× from no-load to full load).
    #   * 10 settling periods with iterated Aitken anchoring of the
    #     period-boundary flux.
    # What is NEW here is only the field solve: P1 gets the exact
    # superposition A = A_pm + i_A·xa + i_B·xb for free because its Picard
    # freezes ν within a sweep.  P2 solves by POINTWISE Newton, where that
    # superposition is not exact, so the circuit is closed on the ACTUAL
    # ψ(A) of the converged field and (A, i_A, i_B) are solved as ONE coupled
    # Newton system:  ∂A/∂i is the tangent back-solve J⁻¹·P, i.e. the
    # DIFFERENTIAL inductance — which is the correct Jacobian of ψ(A(i)).
    _Pa2 = f_coil2['A'] - f_coil2['C']    # unit-i_A source column (i_C folded)
    _Pb2 = f_coil2['B'] - f_coil2['C']    # unit-i_B source column
    _iv_state = {'A': 0.0, 'B': 0.0, 'C': 0.0}
    _psi_prev = None
    _th_eff_prev = None      # previous frame's SNAPPED rotor angle (rotor time)
    _dt_k = dt
    _v_diag = {"iters": [], "resid": []}
    _v_bpsi = []             # period-boundary flux samples for the Aitken anchor
    # How often the Δ²-anchor was TRIED vs actually APPLIED.  Reported, because
    # the ablation (see drive.aitken_flux_anchor) found that on this machine the
    # guards skip every single attempt — the anchor is a no-op here, and that was
    # only discoverable by removing it and comparing.  Now it is a number.
    _v_anchor_tries = 0
    _v_anchor_applied = 0

    # The voltage-drive, eddy and coupled eddy+voltage Newtons —
    # simulation/p2_drive.py.  ONE object rather than three closures: the
    # third solver exists precisely because the first two must not be allowed
    # to answer for each other, and that is a property of the code layout as
    # much as of the physics.  R_phase, psi and the coil source columns are
    # bound here because they are run-dependent; Pro/free stay per-call.
    _drv = _P2Drive(
        p2=_p2, psi=_psi2, f_mag=f_mag2, Pa=_Pa2, Pb=_Pb2, R_phase=R_phase,
        v_phase_peak=v_phase_peak, n_dof=N2, pic_tol=_PIC_TOL2, dt=dt, log=log,
        ed_con=(_ed_con if eddy else None), G=_G2, Msig=_Msig2, Msd=_Msd2,
        Sdt=_Sdt2)

    if _vdrive:
        # ── dq phasor steady-state initialiser (P2) ──────────────────────
        # τ = L/R spans ~20 electrical periods on a low-R machine, so a
        # marched start-up would need ~100 periods to shed its DC.  Measure
        # the PM flux and the dq inductances AT the operating point (coupled
        # phasor↔saturation Picard) and place i(0), ψ(−dt) directly on the
        # periodic orbit.  Lq changes ~5× between i=0 and full load, so an
        # i=0 estimate would leave a large DC for the settling to grind off.
        import math as _m
        _Pro0v, _out0v = _proj.build(0)
        _free0v = np.setdiff1d(np.arange(_Pro0v.shape[1]), _out0v)
        _Pt0 = lambda v: np.asarray(_Pro0v.T @ v).ravel()[_free0v]  # noqa: E731
        _RHS0 = np.column_stack([_Pt0(f_mag2), _Pt0(_Pa2), _Pt0(_Pb2)])
        _nu_ph = nu_base2.copy()
        _w = 2.0 * _m.pi * f_elec
        _V0 = _voltages(0.0)
        # θ_eff = 0 electrical.  The absolute offset CANCELS: _align below
        # measures the PM-flux angle relative to it, so _thal is the true
        # PM-flux angle in the ABC frame whatever reference is used here.
        _the0 = _m.radians(0.0 * pole_pairs + daxis_eff)

        _park = _park_dq; _ipark = _ipark_dq   # simulation/drive.py
        _id0 = _iq0 = 0.0; _thal = _the0
        _psi_pm_d = 0.0; _Ldd = _Lqq = _Ldq = _Lqd = 1e-6
        _Aop = np.zeros(N2)
        for _it in range(max(int(nonlinear_iterations), 20)):
            _Kph = _p2.asmK(_nu_ph)
            _Kff0 = (_Pro0v.T @ _Kph @ _Pro0v).tocsr()[_free0v][:, _free0v].tocsc()
            _X0 = _p2.solve_ff(_Kff0, _RHS0)
            _A0 = _p2.pad2(_Pro0v, _free0v, _X0[:, 0])
            _xa = _p2.pad2(_Pro0v, _free0v, _X0[:, 1])
            _xb = _p2.pad2(_Pro0v, _free0v, _X0[:, 2])
            _pm = _psi2(_A0); _qa = _psi2(_xa); _qb = _psi2(_xb)
            _pd0, _pq0 = _park(_pm[0], _pm[1], _pm[2], _the0)
            _thal = _the0 + _m.atan2(_pq0, _pd0)
            _psi_pm_d = _m.hypot(_pd0, _pq0)
            _Laa, _Lba = _qa[0], _qa[1]
            _Lab, _Lbb = _qb[0], _qb[1]
            _idA, _idB, _idC = _ipark(1.0, 0.0, _thal)
            _iqA, _iqB, _iqC = _ipark(0.0, 1.0, _thal)
            _Ldd, _Lqd = _park(_Laa * _idA + _Lab * _idB,
                               _Lba * _idA + _Lbb * _idB,
                               -((_Laa + _Lba) * _idA + (_Lab + _Lbb) * _idB),
                               _thal)
            _Ldq, _Lqq = _park(_Laa * _iqA + _Lab * _iqB,
                               _Lba * _iqA + _Lbb * _iqB,
                               -((_Laa + _Lba) * _iqA + (_Lab + _Lbb) * _iqB),
                               _thal)
            _Vd, _Vq = _park(_V0['A'], _V0['B'], _V0['C'], _thal)
            _Mdq = np.array([[R_phase - _w * _Lqd, -_w * _Lqq],
                             [_w * _Ldd, R_phase + _w * _Ldq]])
            try:
                _idq = np.linalg.solve(
                    _Mdq, np.array([_Vd, _Vq - _w * _psi_pm_d]))
            except np.linalg.LinAlgError:
                _idq = np.array([0.0, 0.0])
            _id0, _iq0 = float(_idq[0]), float(_idq[1])
            _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
            _Aop = _A0 + _iA0 * _xa + _iB0 * _xb
            _nu_new = _p2.nu_of(_p2.elemB(_Aop), _nu_ph)
            _vo0 = np.concatenate([_nu_ph[_ids] for _ids, _ in _sat2]) \
                if _sat2 else np.zeros(0)
            _vn0 = np.concatenate([_nu_new[_ids] for _ids, _ in _sat2]) \
                if _sat2 else np.zeros(0)
            _pres0 = (float(np.linalg.norm(_vn0 - _vo0)
                            / max(np.linalg.norm(_vo0), 1e-30))
                      if _vo0.size else 0.0)
            _al = 0.5 if _it < 6 else max(0.05, 3.0 / (_it + 1))
            _nu_ph = (1.0 - _al) * _nu_ph + _al * _nu_new
            if _pres0 < _PIC_TOL2:
                break
        _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
        _iv_state = {'A': _iA0, 'B': _iB0, 'C': _iC0}
        # ψ at t = −dt on the orbit: dq are constant in steady state, so the
        # previous-step flux is the SAME dq vector mapped one FRAME back —
        # the park angle rotated by ω·dt (NOT one slip node; a frame spans
        # many).  Getting this wrong injects a spurious rotational EMF at
        # frame 0 → a decaying DC current.
        _psd = _psi_pm_d + _Ldd * _id0 + _Ldq * _iq0
        _psq = _Lqd * _id0 + _Lqq * _iq0
        _psi_prev = dict(zip(('A', 'B', 'C'),
                             _ipark(_psd, _psq, _thal - _w * dt)))
        log.info("P2 vdrive phasor init: Ld=%.4g Lq=%.4g H |psi_pm|=%.4g Wb "
                 "i_dq=(%.1f, %.1f) A i0=(%.1f, %.1f, %.1f)",
                 _Ldd, _Lqq, _psi_pm_d, _id0, _iq0, _iA0, _iB0, _iC0)

    # ── frame loop ───────────────────────────────────────────────────────
    _T2 = []; _psiA = []; _psiB = []; _psiC = []; _tt = []
    _IA = []; _IB = []; _IC = []
    _pic_iters = []; _pic_res_max = 0.0
    _pic_fallback = []      # frames Newton did not solve (fell back to Picard)
    _pic_unconv = []        # frames that met NEITHER path's tolerance
    _snap2 = None
    # Animation keyframes: n evenly-spaced frames across the marched window.
    # Settling frames (voltage drive / demag) are stripped from them afterwards
    # like every other per-frame series, so they stay index-aligned with T/I/psi.
    _frames2 = []
    _anim_idx = set()
    _anim_k0 = 0                 # first REPORTED frame index (settling stripped)
    if int(return_frames) > 0 and n_total > 1:
        # Span the REPORTED window only.  The voltage drive and demag prepend
        # settling frames to n_total; animating those would show the machine
        # starting up, not running.  Picked here rather than trimmed later
        # because _frames2 holds keyframes, not one entry per frame, so the
        # settling-frame strip has nothing to line it up against.
        _anim_k0 = int(_vskip) + int(_dmskip)
        _nf = max(2, min(int(return_frames), n_total - _anim_k0))
        _anim_idx = {_anim_k0 + int(round(i * (n_total - 1 - _anim_k0) / (_nf - 1)))
                     for i in range(_nf)}
    # ── rotor-eddy / iron-loss histories (post-processed after the loop,
    #    exactly as the P1 path does — magnetostatic field + honest coupled
    #    rotor-eddy solve + Bertotti iron; no σ∂A/∂t in the main solve) ─────
    _nr2 = int(half["r"]["n"])                    # rotor vertex count
    _rot_vdof = vdof[nsn:nsn + _nr2]              # rotor vertex -> P2 dof
    _histA_rot2 = []                              # (N, n_rotor_nodes) rotor A
    _hsx2 = []; _hsy2 = []; _hrx2 = []; _hry2 = []  # stator/rotor iron B(t)
    _hcx2 = []; _hcy2 = []                        # coil B(t) for AC copper
    _hmx2 = []; _hmy2 = []                        # magnet B(t) — loss-density map
    # FROZEN PERMEABILITY (frozen_nu): converge the saturation ONCE at frame
    # 0 (extended Picard) then hold the per-element ν fixed for every rotor
    # position — the industry-standard honest cogging/ripple method.  It
    # removes the per-frame saturation-Picard jitter (which does NOT converge
    # to _PIC_TOL on a coarse mesh and otherwise MASKS the discretisation
    # ripple that P2 actually fixes), so the remaining T(θ) variation is
    # purely geometric: P1 staircases, P2 is smooth.
    nu_all2 = nu_base2.copy()      # persists across frames when frozen_nu
    # NEWTON–RAPHSON for the BH nonlinearity (default; SB_NO_NEWTON=1 forces
    # the damped-Picard path).  Cross-frame warm starts: the previous frame's
    # converged field A and ν (a near-perfect Newton initial guess).
    _use_newton = (_os_sb.environ.get("SB_NO_NEWTON") != "1")
    _A2_prev = None; _nu_conv2 = None
    # ── coupled-eddy state ───────────────────────────────────────────────
    # A_prev starts at ZERO, which is not a field the machine was ever in, so
    # the first step carries a fake ∂A/∂t.  The eddy run therefore gets its own
    # SETTLING frames at negative rotor angles (real solves at θ<0, discarded)
    # instead of averaging the transient away like the P1 path does, so the
    # reported window is a clean period on every frame.  Voltage drive already
    # marches ten settling periods, so it needs none.
    #
    # HOW MANY is NOT a constant.  It used to be 2, tuned on a 40 mm machine
    # whose slow conductors are all small: measured cold-start solid σE² there
    # is 4488 → 14.1 → 2.49 W, i.e. gone by the second frame.  The 150 mm
    # 24s28p machine takes far longer: 2776 → 1628 → 1089 → … → 70 W, still
    # decaying 22 frames in.  With a FIXED 2 the un-settled tail sat INSIDE the
    # reported window and the cycle mean of the solid loss read 262 W against a
    # settled 68 W — a reporting-window bug, not a physics one, but it poisons
    # P_shaft/P_mag and through them η.
    #
    # WHY the big machine is slow.  This comment used to say "a 78 mm-diameter
    # solid rotor/shaft assembly" — that object never existed.  It was the
    # meshing bug fixed in 707d2a1: _tag_rotor gave the whole bore disc
    # DOM_SHAFT, so the solve saw 7× the shaft metal the CAD builds, and the
    # numbers above were measured under that phantom.  Nothing in this model
    # conducts across a solid rotor body in any case — _sigma_of_tag gives σ to
    # coils, magnets and the shaft tag ONLY; the rotor iron is σ = 0.
    # The real mechanism is the slowest conducting LOOP in the cross-section.
    # On the 150 mm that is the shaft tube: a closed ring at r ≈ 39 mm, whose
    # L/R grows with the ring's RADIUS (and its axial length) rather than with
    # its 3 mm wall — ~1.5 ms against ~0.2 ms for σ·μ·L² diffusion across a
    # 16 mm magnet, and against a shaft ring an order of magnitude smaller in
    # radius on the 40 mm.  That is also why removing the phantom disc RAISED
    # the shaft leg (31.6 → 39.5 W): the disc was shorting the loop, not adding
    # to it.  None of this has to be right for the code below to be right —
    # which is the point of the next paragraph.
    #
    # So: SETTLE UNTIL QUIET.  The PROBE is the same 2 frames as before, and
    # the THIRD sample it needs is the first reported frame itself — three
    # samples is the minimum that can separate the settled LEVEL from the
    # decay, and taking the third one for free is what keeps every machine
    # that was already settled bit-identical to before this became adaptive.
    # If _eddy_settle_resid says the transient is above _EDDY_SETTLE_TOL, that
    # first frame is thrown away, a WHOLE electrical period of warm-up (the
    # hard cap) is spliced in front of it, and the residual is measured again
    # at the new handoff.  The abort happens BEFORE anything is recorded for
    # the frame, so there is nothing to un-append.
    #
    # Why the extension is a whole period and not "as many as it takes": a
    # march must END at θ = −dθ to hand frame 0 a field exactly one dt old, so
    # its LENGTH has to be known before it starts.  It cannot be discovered on
    # the way, and it cannot be extrapolated either — the probe's early decay
    # ratio on the 150 mm is 0.59 while its tail runs at 0.9, so a fit off the
    # first frames under-warms by 3×.  n_warmup and the measured residual
    # travel out in the result dict either way.
    _EDDY_SETTLE_TOL = 0.02
    _eddy_probe = 2 if (eddy and not _vdrive) else 0
    _eddy_cap = int(n_steps_per_period) if (eddy and not _vdrive) else 0
    # Benchmarks / tests: pin the warm-up to a fixed count and take whatever
    # it gives (SB_EDDY_WARM=2 is the pre-adaptive behaviour, =0 the raw cold
    # start).  The residual is still measured and still reported.
    _ew_env = _os_sb.environ.get("SB_EDDY_WARM")
    if _ew_env not in (None, "") and eddy and not _vdrive:
        _eddy_probe = max(0, int(_ew_env)); _eddy_cap = 0
    _warm_solid: List[float] = []   # solid σE² per frame of the CURRENT march
    _n_warm = 0                     # warm-up frames actually solved (all marches)
    _warm_resid = None              # remaining transient at the handoff [rel]
    _warm_tau_s = None              # fitted settling time constant [s], if clean
    _warm_extended = False          # the probe was not enough → cap march ran
    _warm_done = not (eddy and not _vdrive)     # handoff accepted
    # previous-frame A per dof (material frame).  Zero is not a field the
    # machine was ever in, so under voltage drive — where the phasor
    # initialiser already produced the operating-point field at θ_eff = 0 —
    # seed it with that instead of a fake step from nothing.  Either seed is
    # inside the discarded settling window; this one just does not spend the
    # first frames unwinding an ∂A/∂t that never happened.
    _Aed_prev = (_Aop.copy() if (eddy and _vdrive) else np.zeros(N2))
    _Ued = np.zeros(len(_ed_con))         # per-body conductor voltages
    _ed_cu = []; _ed_mag = []; _ed_sh = []   # σ∫E² per frame [W, machine]
    _ed_dc2d = []                         # 2-D DC I²R of the same bars [W]
    # Per-frame per-element σE² over the conductor elements [W/m³] — the Loss
    # map's copper/magnet/shaft.  A LIST (one array per frame), not a running
    # sum, so the settling-frame trim below drops its frames with everyone
    # else's: averaging over a window that still contains the start-up ∂A/∂t
    # would put the transient straight into the picture.
    _ed_dens_hist = []
    # The frame ORDER is a list, not a range, because the warm-up can grow: a
    # march that did not settle splices a longer one in front of the reported
    # window (see the handoff test at the end of the loop).  Every march ends
    # at k = −1 so the field handed to frame 0 is always exactly one dt old.
    _fseq = list(range(-_eddy_probe, 0)) + list(range(n_total))
    _fi = 0
    while _fi < len(_fseq):
        k = _fseq[_fi]; _fi += 1
        if progress_cb is not None:
            # THE WARM-UP IS WORK, AND IT MUST LOOK LIKE WORK.  Its frames carry
            # NEGATIVE k, and this used to report max(k, 0) — so the bar stood at
            # 0/40 for as long as the σ·∂A/∂t transient took to go quiet
            # (measured on the 200 mm 24s/28p: 73 s of "nothing happening"
            # before the counter moved, i.e. 43 real solved frames reported as
            # zero).  A progress bar that does not move is read as a hung run.
            # The warm-up gets its own counter and its own phase; its total is
            # honestly a moving target, because a march that has not settled
            # splices a longer one in front (the count grows, the bar steps
            # back — that IS what adaptive means).
            _warm_n = sum(1 for _x in _fseq if _x < 0)
            try:
                if k < 0:
                    progress_cb(_warm_n + k, _warm_n,
                                "eddy warm-up (adaptive) — settling the "
                                "start-up transient")
                else:
                    progress_cb(k, n_total, None)
            except TypeError:
                # a caller from before the phase argument (refine_proc, tests)
                try: progress_cb(max(k, 0), n_total)
                except Exception: pass
            except Exception: pass
        theta = (k / n_total) * period_mech * n_periods
        m_shift = int(round(theta / spacing))
        theta_eff = m_shift * spacing
        # Voltage drive: Crank–Nicolson in ROTOR TIME.  The field only exists
        # at SNAPPED slip-node angles θ_eff, so Δt_k = Δθ_eff/ω and V is
        # sampled at the midpoint of the ACTUAL motion.  Dividing a snapped
        # Δψ by the UNIFORM dt instead modulates dψ/dt by the node-
        # quantisation sawtooth (fake volts ≫ |V−E|) and CN rings undamped
        # at Nyquist → monster harmonic currents.
        _dth_frame = period_mech * n_periods / n_total     # mech deg / frame
        if _vdrive:
            if _th_eff_prev is None:        # very first frame: nominal step
                _th_eff_prev = theta_eff - _dth_frame
            _dth_eff = theta_eff - _th_eff_prev
            _dt_k = dt * (_dth_eff / _dth_frame) if _dth_eff > 1e-12 else dt
            _Vt = _voltages(0.5 * (theta_eff + _th_eff_prev))
            _th_eff_prev = theta_eff
            _iv_prev = dict(_iv_state)      # i_{k−1} for the R/2 term
            Ist = dict(_iv_state)           # warm start for the coupled solve
        else:
            _Vt = None; _iv_prev = None
            Ist = _currents(theta_eff)
        # eddy: the winding current is an integral CONSTRAINT, not a source —
        # putting it in f as well would drive the ampere-turns twice.
        f = f_mag2 if eddy else (f_mag2 + Ist['A'] * f_coil2['A']
                                 + Ist['B'] * f_coil2['B']
                                 + Ist['C'] * f_coil2['C'])
        Pro, outer_red = _proj.build(m_shift)
        # WARM-START ν across frames (perf): only frame 0 converges from the
        # unsaturated base (~60–70 sweeps cold); every later frame starts from
        # the PREVIOUS frame's CONVERGED ν and reaches the fixed point in ~40
        # sweeps instead of a full cold ~70.  (The BH-knee Picard is genuinely
        # slow — P1's main loop needs ~55 sweeps too — so warm-start trims the
        # cold-start tax, it does not make it "a few".)  This is SOUND and does
        # NOT bias the mean torque because the Picard early-stops on the
        # residual (_PIC_TOL2, two consecutive sweeps): the initial guess
        # changes the PATH, never the fixed point.  The cap is raised so frame
        # 0 reaches _PIC_TOL2 from cold and warm-started frames have headroom.
        # Same strategy as the P1 main loop.
        if _fi == 1:
            nu_all2 = nu_base2.copy()          # frozen path: base at frame 0
        _Ptf = np.asarray(Pro.T @ f).ravel()
        # free (non-Dirichlet) reduced DOFs — CONSTANT within a frame (Pro and
        # the outer Dirichlet set are fixed), so precompute the slice once.
        _free2 = np.setdiff1d(np.arange(Pro.shape[1]), outer_red)
        _bff2 = _Ptf[_free2]
        # cross-frame warm starts (previous converged field + ν).  The slip
        # pairing (Pro) changes every frame, so the previous field A_prev lives
        # on the PREVIOUS constraint manifold range(Pro_prev); PROJECT it onto
        # the current range(Pro) (least-squares, average each paired group) so
        # the Newton start is constraint-consistent — otherwise Newton drifts
        # off-manifold and diverges frame-to-frame.
        if _A2_prev is None or _nu_conv2 is None:
            _nu_start = nu_base2.copy()
            # Voltage drive: the phasor initialiser already produced the
            # operating-point field at θ_eff=0 — that IS frame 0's warm start.
            _A_start = (_Aop.copy() if (_vdrive and k == 0)
                        else np.zeros(N2))
            if _vdrive and k == 0:
                _nu_start = _nu_ph.copy()
            elif (not eddy) and _use_newton and not frozen_nu and _sat2:
                # COLD FRAME → seed Newton with damped-Picard sweeps.  A = 0
                # has ∇A = 0, i.e. a ZERO Newton tangent, so the first step is
                # the unsaturated linear solve and the line search cannot walk
                # the overshoot back (see _PIC_SEED_MAX).  Cheaper than the
                # fallback it replaces, and the frame is still ACCEPTED on the
                # Newton field residual, never on the seed's ν residual.
                _A_start, _nu_start, _sres, _snit = _p2.pic2_sweeps(
                    Pro, _free2, _bff2, _nu_start,
                    _PIC_SEED_MAX, _PIC_SEED_TOL)
                log.debug("P2 frame %d: cold Newton seed, %d picard sweeps, "
                          "nu-res=%.2e", k, _snit, _sres)
        else:
            _nu_start = _nu_conv2.copy()
            _pd = np.asarray(Pro.multiply(Pro).sum(axis=0)).ravel()
            _A_start = Pro @ (np.asarray(Pro.T @ _A2_prev).ravel()
                              / np.maximum(_pd, 1.0))

        # Demag makes the frame re-enterable: solve, check the magnet, and
        # if it weakened, rebuild its source and solve again.  The de-rating
        # is monotone and self-arresting — a weaker magnet makes a weaker
        # demagnetising field — so this settles in a few passes; the cap is a
        # backstop, not a schedule.
        _dm_pass = 0
        while True:
            _res = 0.0; _nit = 0; _newton_ok = False
            # ── COUPLED EDDY: bordered magnetodynamic Newton ──────────────────
            # Replaces the magnetostatic solve for this frame; every other
            # per-frame quantity (torque, ψ, B histories, demag) is taken from
            # its A2 unchanged, so the eddy run differs from the magnetostatic
            # one ONLY by the physics that was added.
            if eddy:
                # ν frozen ⇒ the bordered system is LINEAR (one exact solve);
                # the first frame of a frozen_nu run still converges the
                # saturation, which is what "frozen at the reference frame"
                # means.  No saturable iron ⇒ always linear.
                _nu_fix = (nu_all2 if ((frozen_nu and _A2_prev is not None)
                                       or not _sat2) else None)
            if eddy and not _vdrive:
                _I_vec = np.array(
                    [Ist[c["phase"]] * c["Iunit"] if c["key"] == "cu" else 0.0
                     for c in _ed_con], float)
                (_eok, A2, _Ued, _res, _nit) = _drv.eddy_solve(
                    Pro, _free2, _A_start, _Ued, _I_vec, _Aed_prev,
                    _nu_fix, max(int(nonlinear_iterations), 20))
                if not _eok:
                    # No silent fallback: the magnetostatic Picard below would
                    # solve DIFFERENT physics (no σ·∂A/∂t, coil current back as
                    # a source) and report it as an eddy run.
                    raise RuntimeError(
                        f"P2 coupled eddy: bordered Newton did not converge at "
                        f"frame {k} ({_nit} its, rrel={_res:.2e})")
                _newton_ok = True
                if _nu_fix is None:
                    # element-mean ν for the loss post-processing
                    nu_all2 = _p2.nu_of(_p2.elemB(A2), nu_all2)
            elif eddy and _vdrive:
                # ── EDDY **AND** VOLTAGE DRIVE: one (A, U, i_A, i_B) Newton ──
                # The winding current is the constraint VALUE and a circuit
                # unknown at once — see p2_drive.ve_newton.  There is deliberately NO
                # fallback: the magnetostatic Picard solves different physics
                # and the current-drive eddy solve ignores the circuit, so
                # either one would report a different machine as this run.
                (_eok, A2, _Ued, _viA, _viB, _res, _nit,
                 _vrc) = _drv.ve_newton(
                    Pro, _free2, _A_start, _Ued, (Ist['A'], Ist['B']),
                    _Aed_prev, _Vt, _dt_k, _iv_prev, _psi_prev,
                    _nu_fix, max(int(nonlinear_iterations), 25))
                if not _eok:
                    raise RuntimeError(
                        f"P2 coupled eddy + voltage drive: bordered (A, U, i) "
                        f"Newton did not converge at frame {k} ({_nit} its, "
                        f"rrel={_res:.2e}, circuit resid="
                        f"{float(np.max(np.abs(_vrc))):.2e} V)")
                _newton_ok = True
                if _nu_fix is None:
                    nu_all2 = _p2.nu_of(_p2.elemB(A2), nu_all2)
                # The solved currents ARE this frame's excitation from here on
                # (torque, ψ, the I²R reference of the eddy loss split).  f is
                # NOT rebuilt with them: under eddy the ampere-turns enter
                # through the constraint rows only.
                Ist = {'A': _viA, 'B': _viB, 'C': -_viA - _viB}
            # ── VOLTAGE DRIVE: coupled field + circuit solve ──────────────────
            # The phase currents are UNKNOWNS solved together with the field.
            # Primary path: the coupled Newton (field residual + line-to-line
            # circuit residual on the ACTUAL ψ(A), Jacobian = the tangent
            # back-solve ∂A/∂i).  Fallback: the frozen-ν superposition Picard,
            # which is exactly the P1 recipe.
            if _vdrive and not eddy:
                _vok = False
                if _use_newton and not frozen_nu and _sat2:
                    (_vok, _A2v, _viA, _viB, _res, _nit,
                     _vrc) = _drv.v_newton(
                        Pro, _free2, _A_start, (Ist['A'], Ist['B']),
                        _Vt, _dt_k, _iv_prev, _psi_prev,
                        max(int(nonlinear_iterations), 20))
                if _vok:
                    A2 = _A2v; _newton_ok = True
                    # element-mean ν for the loss post-processing
                    nu_all2 = _p2.nu_of(_p2.elemB(A2), nu_all2)
                else:
                    if _use_newton and not frozen_nu and _sat2:
                        log.info("P2 vdrive Newton not converged at frame %d "
                                 "(%d its, rrel=%.1e) — Picard fallback",
                                 k, _nit, _res)
                    if frozen_nu:
                        _n_pic2 = (max(nonlinear_iterations, 40) if k == 0
                                   else 1)
                        _nu_in = nu_all2
                    else:
                        _n_pic2 = max(nonlinear_iterations,
                                      70 if k == 0 else 45)
                        _nu_in = _nu_start
                    (A2, _viA, _viB, nu_all2, _res,
                     _nit) = _drv.v_picard(
                        Pro, _free2, _nu_in, _Vt, _dt_k, _iv_prev,
                        _psi_prev, _n_pic2, bool(frozen_nu and k > 0))
                Ist = {'A': _viA, 'B': _viB, 'C': -_viA - _viB}
                f = (f_mag2 + Ist['A'] * f_coil2['A']
                     + Ist['B'] * f_coil2['B'] + Ist['C'] * f_coil2['C'])
                _bff2 = np.asarray(Pro.T @ f).ravel()[_free2]
            # ── NEWTON–RAPHSON (differential-reluctivity tangent) ─────────────
            # Residual R(A)=K(ν(|B|))·A−f driven to |R|/|f| < 1e-7 — a statement
            # about the FIELD, not about how much ν moved on the last sweep.
            # Jacobian J=K+T with the tangent T=2(dν/dB²)(∇A·∇u)(∇A·∇v).
            # Line-search on |R| globalises the BH knee; if it collapses, this
            # frame falls back to damped Picard (never returns garbage) — and
            # says so, per frame, in picard_fallback_frames.
            if ((not _vdrive) and (not eddy) and _use_newton
                    and not frozen_nu and _sat2):
                # POINTWISE ν(|B|²) at quadrature points (_p2.Kpw, in
                # simulation/p2_nonlinear.py) — the residual and the tangent then use the
                # SAME nonlinearity, giving a TRUE (quadratic) Newton step.
                # (An element-mean ν residual with a pointwise tangent is
                # inconsistent → no acceleration.)  For P2, B is linear per
                # element, so pointwise ν is also the more accurate model.
                #
                # It is a DIFFERENT model from the element-mean ν the Picard
                # fallback iterates, not merely a faster route to the same
                # answer — an earlier version of this comment claimed the two
                # fixed points coincide, and they do not.  Measured on the
                # pinned p2_load case (60 A, 12 steps), Newton at 1e-7 against
                # the same case run with SB_NO_NEWTON=1 and the Picard driven
                # to 1e-4, i.e. both converged:
                #
                #             T_avg [Nm]  ripple [%]  P_fe [W]  P_cu_ac [W]
                #   pointwise   0.417401     0.535     3.1226      3.4960
                #   elem-mean   0.420020     1.127     3.1796      3.4428
                #
                # The gap (0.6 % of torque, 2× of ripple) is the element-mean
                # ν smearing the BH knee across an element, which on a coarse
                # belt mesh is exactly where the ripple lives.  Pointwise is
                # the primary path because it is the better model; the Picard
                # is a fallback for when Newton cannot start, and a run that
                # used it is flagged frame by frame in the result dict.
                def _rfree_pw(Avec, K):
                    return np.asarray(Pro.T @ (K @ Avec - f)).ravel()[_free2]

                A2 = _A_start.copy(); _fail = False; _rrel = 1.0
                _bnrm = max(float(np.linalg.norm(_bff2)), 1e-30)
                for it in range(max(int(nonlinear_iterations), 20)):
                    _nit = it + 1
                    K, _info = _p2.Kpw(A2)
                    r_free = _rfree_pw(A2, K)
                    _rrel = float(np.linalg.norm(r_free)) / _bnrm
                    if _rrel < 1e-7:
                        break
                    # tangent T = 2(dν/dB²)(∇A·∇u)(∇A·∇v), pointwise & consistent
                    T = _p2.tangent2(_info)
                    J = (K + T).tocsr() if T is not None else K
                    Jff = (Pro.T @ J @ Pro).tocsr()[_free2][:, _free2].tocsc()
                    try:
                        _du = _p2.solve_ff(Jff, -r_free)
                    except Exception as _je:
                        log.info("P2 Newton solve failed (%s) — Picard fallback", _je)
                        _fail = True; break
                    _duf = np.zeros(Pro.shape[1]); _duf[_free2] = _du
                    dA = Pro @ _duf
                    # backtracking line-search on the residual norm (BH-knee safety)
                    _r0 = float(np.linalg.norm(r_free)); _lam = 1.0; _acc = False
                    for _ls in range(6):
                        A_try = A2 + _lam * dA
                        _Kt, _ = _p2.Kpw(A_try)
                        if float(np.linalg.norm(_rfree_pw(A_try, _Kt))) < _r0:
                            A2 = A_try; _acc = True; break
                        _lam *= 0.5
                    if not _acc:
                        _fail = True; break
                # accept only if the field residual actually reached tol
                if (not _fail) and (_rrel < 1e-7):
                    _newton_ok = True; _res = _rrel
                    # element-mean ν for the loss post-processing (loss code uses
                    # per-element ν); negligible vs the pointwise field solve.
                    nu_all2 = _p2.nu_of(_p2.elemB(A2), nu_all2)
                else:
                    log.info("P2 Newton not converged at frame %d (%d its, rrel=%.1e)"
                             " — Picard fallback", k, _nit, _rrel)
            # ── DAMPED PICARD (SB_NO_NEWTON, frozen_nu, or Newton fell back) ──
            if (not _vdrive) and (not eddy) and not _newton_ok:
                if not frozen_nu:
                    nu_all2 = _nu_start.copy()     # warm-start (base at k=0)
                if frozen_nu:
                    _n_pic2 = max(nonlinear_iterations, 40) if k == 0 else 1
                else:
                    _n_pic2 = max(nonlinear_iterations, 70 if k == 0 else 45)
                A2, nu_all2, _res, _nit = _p2.pic2_sweeps(
                    Pro, _free2, _bff2, nu_all2, _n_pic2, _PIC_TOL2,
                    linear_only=bool(frozen_nu and k > 0))
            # ── Irreversible demagnetisation ─────────────────────────────────
            # HERE, after the frame's nonlinear solve has converged by EITHER
            # path — the pointwise Newton (primary) or the damped Picard
            # (fallback).  Putting it inside the Picard was wrong: Newton
            # converges on this machine, so the fallback never runs and the hook
            # never fired.  It must also never see an intermediate iterate: the
            # early sweeps start from unsaturated iron and pass through fields
            # that are numerical transients, and a monotone irreversible rule
            # burns those in permanently.
            #
            # If the magnet moved, its own field must be re-solved with the
            # weaker source, so the frame is redone.  The de-rating is monotone
            # and self-arresting (a weaker magnet makes a weaker demagnetising
            # field), so this converges in a handful of passes; the cap is a
            # backstop, not a schedule.
            #
            # ── DO NOT move this rule inside the Newton loop ─────────────────
            # Tried on branch `demag-in-newton` (3aaf8d1, 2acdeb5), reverted in
            # ece72ea, and then measured to a conclusion.  30 mm 12s14p, 60 A,
            # F45SH_120C, P2, 24 frames — the pinned p2_demag case:
            #
            #   judged at        restart   de-rated  Br_mean   T_avg     s
            #   rrel<1e-7 (here) cold        241/490  0.85383  0.40562  798
            #   rrel<1e-7        warm A2     241/490  0.85383  0.40562  288
            #   rrel<1e-6 in-loop warm       219/490  0.87037  0.41756  190
            #   rrel<1e-6, cap 60 warm       219/490  0.86958  0.41756  244
            #
            # It is NOT path dependence and NOT the cold restart.  Warm-starting
            # the re-solve from the field just converged (see below) reproduces
            # this code to 5e-8 on the whole Br map, with the SAME 277 rule
            # evaluations — so where the re-solve starts provably cannot reach
            # the answer.  Nor is either number a cap artefact on the magnet
            # side: the in-loop scheme gives the same 219 at a settling cap of
            # 24 and of 60.
            #
            # What moves the answer is WHEN the rule is allowed to look.  A
            # GLOBAL relative residual of 1e-6 is not a converged field INSIDE a
            # magnet corner — the corner is the last place Newton resolves.
            # Measured at the FIRST look of each frame, which is the look that
            # decides everything, because the magnet is still at its strongest
            # there and that is where the frame's worst demagnetising field is:
            #
            #   frame   worst H, rrel<1e-7   worst H, rrel 3e-7..1e-6
            #     1        -1072 kA/m            -993 kA/m
            #     6         -934                 -669
            #     8         -853                 -650
            #    11         -858                 -728
            #
            # Same rotor position, same incoming magnet (Br_mean agrees to
            # 0.2 %), 8-30 % less demagnetising field — always in that
            # direction, because a warm-started Newton builds the armature
            # reaction UP toward the answer and an early iterate has not got
            # there yet.  The rule is monotone: a worst-case field missed at the
            # first look is missed for good, since every later pass of that
            # frame sees an already-weakened magnet and therefore a milder
            # field.  That is the whole of the "in-Newton reports less
            # demagnetisation and more torque", and why it grows with load
            # (3 elements at 32 A, 24 at 60 A).
            #
            # So the rule may only see a field that has passed the frame's OWN
            # convergence test.  The 3.3x that change bought is available
            # without it, from the warm start below.
            if _dmst is not None:
                _bxq_d, _byq_d, _dxq_d = _p2_B_at_quad(b2, A2)
                _ar_d = _dxq_d.sum(axis=1)
                _bxe_d = (_bxq_d * _dxq_d).sum(axis=1) / np.maximum(_ar_d, 1e-30)
                _bye_d = (_byq_d * _dxq_d).sum(axis=1) / np.maximum(_ar_d, 1e-30)
                if _dmst.update(_bxe_d[nst:], _bye_d[nst:]) and _dm_pass < 11:
                    _mx_all[nst:] = _Mx_glob * _br_glob
                    _my_all[nst:] = _My_glob * _br_glob
                    f_mag2 = asm(_msrc, b2, mx=b2_0.interpolate(_mx_all),
                                 my=b2_0.interpolate(_my_all))
                    f = f_mag2 if eddy else (
                        f_mag2 + Ist['A'] * f_coil2['A']
                        + Ist['B'] * f_coil2['B'] + Ist['C'] * f_coil2['C'])
                    _bff2 = np.asarray(Pro.T @ f).ravel()[_free2]
                    # ── and into the OTHER solvers' magnet source ────────────
                    # _bff2 is the RHS of the plain magnetostatic system only.
                    # The bordered eddy Newton, the voltage-drive Newton and its
                    # Picard fallback all build their own right-hand side from
                    # _drv.f_mag (eddy: rhs = f_mag + (Msig/dt)·A_prev, with the
                    # coil current entering as a CONSTRAINT, so f_mag is the
                    # whole of the magnet drive there).  _drv was constructed
                    # once, before the frame loop, with the PRISTINE f_mag2 —
                    # rebinding the local name here left it holding the strong
                    # magnet for the rest of the run.  Effect, measured on the
                    # 40 mm Fe16N2 machine (docs/SOLVER_TRIALS_2026-07-30.md
                    # F1): with eddy on, demag on/off moved the torque by 0.0 %
                    # while the demag map reported 98 % of the magnet de-rated;
                    # with eddy off the same de-rating costs 69 % of the torque.
                    # One assignment, because the re-entry below already re-runs
                    # whichever solver this frame uses, warm-started.
                    _drv.f_mag = f_mag2
                    _dm_pass += 1
                    # Re-solve from the field we just converged, not from the
                    # previous FRAME's.  The magnet moved by a fraction of a
                    # per cent, so this start is far closer than _A_start and
                    # Newton needs 3-5 iterations instead of 12-19.  The rule
                    # still only ever judges a field that passed the frame's
                    # convergence test, so the judging sequence is untouched —
                    # measured identical to the cold restart (max |dBr| 5e-8
                    # over 490 elements, same 241/490, T_avg to 8 digits) at
                    # 288 s against 798 s.
                    _A_start = A2.copy()
                    continue                     # re-solve THIS frame
            break

        _A2_prev = A2.copy(); _nu_conv2 = nu_all2.copy()
        if eddy:
            # ── Joule loss straight from the coupled solution ─────────────
            #   P = ∫σ E² = ∫σ(−∂A/∂t + U_b)²
            #     = Ȧᵀ·M̃_b·Ȧ − 2·U_b·(g_b·Ȧ) + U_b²·S_b     per body,
            # evaluated EXACTLY on the FEM field (M̃ = σ-weighted mass, g and S
            # the same vectors the constraint rows use).  U_b ≡ 0 bodies keep
            # only the first term — which is what U = 0 means.  Done per body
            # rather than by smearing U onto nodes, so a node shared by two
            # conductors cannot pick up the wrong voltage.
            # Δt of the step that was actually SOLVED: the rotor-time Δt_k
            # under voltage drive (see p2_drive.ve_newton), the nominal dt otherwise.
            # Dividing by a different Δt than the solve used would put the
            # slip-node sawtooth straight into E = −∂A/∂t + U.
            _dt_e = _dt_k if _vdrive else dt
            _dAe = (A2 - _Aed_prev) * (1.0 / _dt_e)       # ∂A/∂t [V/m]
            _pg = {_kk: float(_dAe @ (_Mg @ _dAe))
                   for _kk, _Mg in _Msig_grp.items()}
            for _ci, _c in enumerate(_ed_con):
                _u = float(_Ued[_ci])
                _pg[_c["key"]] += _u * (_u * _c["S"]
                                        - 2.0 * float(_c["g"] @ _dAe))
            _wsc = float(NS) * float(p.stack_length)   # sector·2-D → machine
            # ── has the σ·∂A/∂t start-up transient gone quiet? ─────────────
            # Gauge = the SOLID conductors (magnet + shaft): the slow ones, and
            # the ones whose cycle mean the un-settled tail corrupts.  With
            # rotor_eddy off they are not in the system at all and the copper —
            # which settles inside one step — is all there is to watch.
            # This sits BEFORE every per-frame append below, so the abort path
            # can throw this frame away without un-recording anything.
            if not _warm_done:
                if k < 0:
                    _n_warm += 1          # every frame at θ<0 is warm-up
                _warm_solid.append(
                    (_pg.get("mag", 0.0) + _pg.get("shaft", 0.0)
                     if ("mag" in _Msig_grp or "shaft" in _Msig_grp)
                     else _pg.get("cu", 0.0)) * _wsc)
                # Decision point: the probe's third sample IS frame 0; an
                # extension march decides at its own last frame (θ = −dθ),
                # where it already has a whole period of samples.
                if (k == -1) if _warm_extended else (k >= 0):
                    _warm_resid, _warm_tau_s = _eddy_settle_resid(
                        _warm_solid, int(n_steps_per_period), dt)
                    _quiet = _warm_resid <= _EDDY_SETTLE_TOL
                    log.info("P2 eddy warm-up: %d frame(s) solved, remaining "
                             "start-up transient %.3g %% of the settled solid "
                             "loss (tol %.1f %%)%s", _n_warm,
                             100.0 * _warm_resid, 100.0 * _EDDY_SETTLE_TOL,
                             "" if _warm_tau_s is None
                             # LOCAL slope of the three samples this decision
                             # saw — not the settling time (the decay is
                             # multi-mode: 35 us at the head, 680 us at the
                             # tail, ~0.4 ms end to end on the 150 mm).
                             else ", local tail fit tau=%.3g s" % _warm_tau_s)
                    if (not _quiet) and (not _warm_extended) and _eddy_cap >= 3:
                        # Not settled → THIS frame is warm-up too, and a whole
                        # electrical period of warm-up goes in front of the
                        # window before frame 0 is solved again.  The eddy
                        # history is NOT reset: everything solved so far keeps
                        # its settling; the march only repositions the rotor.
                        _warm_extended = True
                        _n_warm += 1
                        _fseq[_fi:_fi] = list(range(-_eddy_cap, 0)) + [0]
                        _warm_solid = []
                        _Aed_prev = A2.copy()
                        log.info("P2 eddy warm-up: not settled — extending by "
                                 "one electrical period (%d frames)", _eddy_cap)
                        continue
                    _warm_done = True
                    if not _quiet:
                        # Loud: the reported window still contains start-up
                        # decay, so the solid-loss cycle means below — and the
                        # efficiency built on them — are over-read by about
                        # this much.  Nothing left to spend: the cap is a whole
                        # electrical period.
                        log.warning(
                            "P2 eddy warm-up: STILL NOT SETTLED after %d "
                            "frame(s) — %.3g %% of the solid loss at the "
                            "handoff is decaying start-up transient (tol "
                            "%.1f %%).  P_mag / P_shaft and the efficiency are "
                            "over-read by roughly that much.", _n_warm,
                            100.0 * _warm_resid, 100.0 * _EDDY_SETTLE_TOL)
            # ── the SAME integrand, kept per element (Loss map) ───────────
            # E = −∂A/∂t + U_b at this element's quadrature points; σE²
            # integrated over the element and divided by its area is the
            # element's loss DENSITY [W/m³] — intensive, so no sector or
            # stack-length factor belongs here (the volume integral below
            # puts them back).
            if _ed_elems.size and k >= 0:
                _Uel = np.zeros(_ed_elems.size)
                for _ci, _c in enumerate(_ed_con):
                    _Uel[_ed_uloc[_ci]] = float(_Ued[_ci])
                _Eq = -np.asarray(_ed_basis.interpolate(_dAe)) + _Uel[:, None]
                _ed_dens_hist.append(
                    _ed_sig_e * np.sum(_Eq ** 2 * _ed_dx, axis=1)
                    / np.maximum(_ed_area, 1e-30))
            _Aed_prev = A2.copy()
            if k >= 0:
                _ed_cu.append(_pg.get("cu", 0.0) * _wsc)
                _ed_mag.append(_pg.get("mag", 0.0) * _wsc)
                _ed_sh.append(_pg.get("shaft", 0.0) * _wsc)
                # DC reference of the SAME bars: with ∂A/∂t = 0 the constraint
                # gives U_b = I_b/S_b and P = ΣI_b²/S_b — the 2-D (active
                # length only) I²R, so total − this is the honest AC increment
                # to add to the end-winding-corrected DC below.
                _ed_dc2d.append(float(np.sum(
                    [(Ist[c["phase"]] * c["Iunit"]) ** 2 / max(c["S"], 1e-30)
                     for c in _ed_con if c["key"] == "cu"])) * _wsc)
        if k < 0:
            continue          # eddy warm-up frame: solved, not reported (it
                              # was counted where the settling gauge read it)
        _pic_iters.append(_nit); _pic_res_max = max(_pic_res_max, _res)
        # HONEST per-frame convergence bookkeeping.  The two paths measure
        # DIFFERENT residuals — Newton the field residual |R(A)|/|f| against
        # 1e-7, the Picard fallback a ν fixed-point change against _PIC_TOL2 —
        # so a frame is judged against the tolerance of the path that actually
        # solved it, and the ones that failed are listed by INDEX.  A single
        # scalar max over two different metrics cannot say which frame was
        # unconverged, and an unconverged frame inside a reported window is
        # not an honest average.
        if not _newton_ok:
            _pic_fallback.append(k)
            if not (_res < _PIC_TOL2):
                _pic_unconv.append(k)
        # Per-frame convergence trace.  picard_resid_max alone says only THAT
        # some frame was the worst; when it lands near the tol you need to know
        # WHICH frame and by WHICH path, so log it (DEBUG — one line per frame).
        log.debug("P2 frame %d: %s, %d its, res=%.3e",
                  k, "newton" if _newton_ok else "picard", _nit, _res)
        Tq = _arkkio_torque_p2(mesh_all, A2, b2, p.r_rotor_out,
                               p.r_stator_in, p.stack_length) * NS
        _T2.append(Tq)
        _pa, _pb, _pc = _psi2(A2)
        _psiA.append(_pa); _psiB.append(_pb); _psiC.append(_pc)
        _IA.append(Ist['A']); _IB.append(Ist['B']); _IC.append(Ist['C'])
        _tt.append(k * dt)
        if _vdrive:
            # ── circuit bookkeeping on the CONVERGED field ───────────────
            # Residual of the line-to-line equations evaluated with the
            # ACTUAL ψ(A) of the converged frame (health check: ~0 when the
            # coupled solve converged).  The phase-A equation alone is
            # legitimately non-zero — that is the zero-sequence EMF the
            # floating neutral absorbs.
            _rc_c = _drv.circ_r((_pa, _pb, _pc), Ist['A'], Ist['B'],
                            _iv_prev, _psi_prev, _Vt, _dt_k)
            _v_diag["iters"].append(int(_nit))
            _v_diag["resid"].append(float(np.max(np.abs(_rc_c))))
            _psi_prev = {'A': _pa, 'B': _pb, 'C': _pc}
            _iv_state = dict(Ist)
            # ITERATED Aitken DC-mode removal: the period-boundary flux
            # converges geometrically toward the steady orbit; sample it at
            # each period end and Δ²-extrapolate the limit whenever 3 fresh
            # samples exist since the last anchor (anchors at periods 3, 6,
            # 9 inside the settling window — each cuts the residual DC ~3×).
            # Samples must share the cycle phase (period spacing) so the
            # periodic flux content cancels exactly in the differences.
            if _v_nspp > 0 and ((k + 1) % _v_nspp == 0) and (k + 1) < _vskip:
                _v_bpsi.append((_pa, _pb))
                if len(_v_bpsi) >= 3:
                    # Δ²-extrapolation + its "already converged / unstable"
                    # guard: simulation/drive.py, shared with the P1 path.
                    _new, _corr, _drift = _aitken_flux_anchor(_v_bpsi)
                    _v_anchor_tries += 1
                    if _new is None:
                        log.info("P2 vdrive Aitken anchor SKIPPED at period %d "
                                 "(drift %.3g, corr %.3g Wb — converged/unstable)",
                                 (k + 1) // _v_nspp, _drift, _corr)
                        _v_bpsi.clear()
                    else:
                        _v_anchor_applied += 1
                        _psi_prev = _new
                        _v_bpsi.clear()   # fresh samples only after re-anchor
                        log.info("P2 vdrive Aitken anchor at period %d: psiA "
                                 "%.4g -> %.4g (|corr| %.3g Wb)",
                                 (k + 1) // _v_nspp, _pa, _new['A'], _corr)
        # Element-mean B in the iron and the coils, captured on EVERY frame —
        # the same thing the P1 path does unconditionally.  This used to sit
        # inside `if rotor_eddy:`, which meant P2 reported ZERO core loss and
        # zero AC copper loss whenever that flag was off, and overstated the
        # efficiency by exactly those terms.  `rotor_eddy` means "magnet and
        # shaft eddy losses" — it has no business gating the iron.  Harmless
        # while P1 was the default; a silent error the moment P2 became it.
        _bx_q, _by_q, _dxq = _p2_B_at_quad(b2, A2)
        _ar_el = _dxq.sum(axis=1)
        _bx_el = (_bx_q * _dxq).sum(axis=1) / np.maximum(_ar_el, 1e-30)
        _by_el = (_by_q * _dxq).sum(axis=1) / np.maximum(_ar_el, 1e-30)
        _hsx2.append(_bx_el[_iron_s_idx]); _hsy2.append(_by_el[_iron_s_idx])
        _hrx2.append(_bx_el[_iron_r_idx + nst])
        _hry2.append(_by_el[_iron_r_idx + nst])
        if _coil_idx.size:                     # coil B for AC copper loss
            _hcx2.append(_bx_el[_coil_idx]); _hcy2.append(_by_el[_coil_idx])
        if _mag_idx.size:                      # magnet B for the loss-density map
            _hmx2.append(_bx_el[_mag_idx + nst])
            _hmy2.append(_by_el[_mag_idx + nst])
        if rotor_eddy:
            # rotor-frame nodal A (magnet/shaft eddy via honest_rotor_eddy)
            _histA_rot2.append(A2[_rot_vdof].copy())
        if _anim_idx and k in _anim_idx:
            # Animation keyframe.  The band encodes rotation in the slip
            # PAIRING, not in coordinates, so the rotor half sits at angle 0 in
            # mesh_all on EVERY frame; rotate its node block by this frame's
            # SNAPPED angle θ_eff for display, or the field would animate under
            # a rotor that never moves.  θ_eff, not θ — that is the angle the
            # field was actually solved at.
            _c_a = math.cos(math.radians(theta_eff))
            _s_a = math.sin(math.radians(theta_eff))
            _Pf = mesh_all.p.copy()
            _xr = _Pf[0, nsn:].copy(); _yr = _Pf[1, nsn:].copy()
            _Pf[0, nsn:] = _c_a * _xr - _s_a * _yr
            _Pf[1, nsn:] = _s_a * _xr + _c_a * _yr
            _frames2.append({
                # Index within the REPORTED window, so it addresses the trimmed
                # T/I/psi series directly.  A global k would be off by the
                # settling prefix on voltage-drive and demag runs and the
                # animation would label each frame with someone else's torque.
                "step_idx": int(k - _anim_k0),
                "time_s": float((k - _anim_k0) * dt),
                "rotor_angle_deg": float(theta_eff),
                "P_mm": _Pf * 1e3,
                "A": A2[vdof].copy(),
                "Bx": _bx_el.copy(), "By": _by_el.copy(),
            })
        if return_field and ((field_first and k == 0)
                             or (not field_first and k == n_total - 1)):
            # Same payload shape as the P1 snapshot (per-element B + domain
            # tags beside A), so a viewer needs no second case.  _bx_el /
            # _by_el above are already the element means over mesh_all.
            _snap2 = {"P_mm": (mesh_all.p * 1e3).copy(),
                      "T": mesh_all.t.copy(),
                      "A": A2[vdof].copy(),
                      "Bx": _bx_el.copy(), "By": _by_el.copy(),
                      "tags": np.concatenate(
                          [np.asarray(ts), np.asarray(tr)]).astype(int),
                      "nsn": int(nsn)}
            if eddy:
                # J-VIEW: the eddy current density J = σ(−∂A/∂t + U_b) the
                # coupled solve produces, sampled at the mesh VERTICES (the
                # P1 snapshot is nodal too, so the viewer needs no new case).
                def _bdofs(ids):
                    return np.concatenate([
                        vdof[np.unique(mesh_all.t[:, ids])],
                        fdof[np.unique(mesh_all.t2f[:, ids])]]).astype(int)
                _sig_n = np.zeros(N2); _u_n = np.zeros(N2)
                _elm = {}
                for _ky, _tg, _ids, _sg in _bodies:
                    _elm[_tg] = _ids
                    _sig_n[_bdofs(_ids)] = _sg
                for _ci, _c in enumerate(_ed_con):
                    _u_n[_bdofs(_elm[_c["tag"]])] = float(_Ued[_ci])
                _snap2["Jeddy"] = (_sig_n * (-_dAe + _u_n))[vdof].copy()
            else:
                # Magnetostatic view: the APPLIED per-element source current
                # density, so the "J" view shows the winding currents at this
                # rotor position.  Same key and same construction as the P1
                # snapshot — a viewer must not need a per-order case, and the
                # J view rendered EMPTY on P2 for exactly as long as this key
                # was missing here.
                _Js2 = np.zeros(int(mesh_all.t.shape[1]))
                for _ix, _ar, _dir, _ph, _as in coil_info:
                    # Same divisor as the assembled source (the slot's REAL
                    # copper area), so the J card reads the density the solve
                    # actually used, not a nominal-rectangle one.
                    _Js2[_ix] = (_dir * Ist[_ph] * n_wires / max(_as, 1e-12))
                _snap2["Jtri_src"] = _Js2
    # ── What the run COST, captured before the settling frames are stripped ──
    # `n_total` is about to be decremented back to the REPORTED window, and both
    # the log line and the result dict below read it — so both understated the
    # work by every settling frame that was actually solved.  A demag run solves
    # a WHOLE EXTRA PERIOD (_dmskip), a voltage run ten of them (_vskip), and an
    # eddy run as many warm-up frames at negative rotor angle as it took to
    # settle (_n_warm — the probe, plus a whole electrical period if the probe
    # was not enough): "36 frames in 419.7 s" was really 74 frames solved, and
    # nothing on the way to the user's screen could say why a 36-step run takes
    # seven minutes.  The Simulation tab quotes n_frames_solved / solve_wall_s
    # back as seconds-per-frame in its pre-run cost line, so the estimate is a
    # rate THIS machine produced rather than a guess.  Captured HERE, where
    # n_total is still the loop bound that ran.
    _n_solved = int(n_total) + int(_n_warm)

    # ── Voltage drive: drop the SETTLING periods ─────────────────────────
    # The currents are STATE, so the run carries an electrical start-up
    # transient; _vskip frames were prepended for it (n_total/n_periods were
    # bumped by the same amount before this branch, so dt is unchanged).
    # This is a SEPARATE, longer skip from the demag one below — they are
    # applied in sequence, never double-counted: each strips its OWN prefix
    # and decrements n_total/n_periods by its own amount.
    # v_drive_diag is per-FRAME like every other series here, so it must be
    # trimmed with them — otherwise its indices are offset by _vskip against
    # I/psi/T and the reported max residual is the SETTLING residual, not
    # the steady-state one.
    _v2_lists = (_T2, _psiA, _psiB, _psiC, _IA, _IB, _IC, _tt,
                 _hsx2, _hsy2, _hrx2, _hry2, _hcx2, _hcy2, _hmx2, _hmy2,
                 _histA_rot2, _pic_iters,
                 _ed_cu, _ed_mag, _ed_sh, _ed_dc2d, _ed_dens_hist,
                 _v_diag["iters"], _v_diag["resid"])
    if _vdrive and _vskip:
        n_total -= _vskip
        n_periods = float(n_periods) - float(_v_settle_periods)
        _drop_settling_frames(_v2_lists, _vskip, _tt)
    # ── Demag: drop the SETTLING period ──────────────────────────────────
    # The P1 path has done this since the settling pass was added; the P2
    # branch returns before that code and never got it.  The magnet weakens
    # THROUGH the run, so reporting both periods measures a machine whose
    # magnets are still dying: Fe16N2 came out at 69.5 % ripple against P1's
    # 9.3 % on identical physics, which is the decay, not an oscillation.
    # The magnet KEEPS its de-rated state (_br_glob is cumulative) — only the
    # frames are discarded, so the reported window is one clean period.
    if _dmskip and demag and _dmst is not None:
        n_total -= _dmskip
        n_periods = float(n_periods) - 1.0
        _drop_settling_frames(_v2_lists, _dmskip, _tt)

    # ── Voltage drive: copper loss from the SOLVED current ───────────────
    # `copper_loss_W` ran near the top of this function on the CONFIG
    # I_phase_rms — but under voltage drive the current is the ANSWER, not
    # the input, and nothing recomputed it.  P_cu (and through it
    # P_loss_total_W and the efficiency) was wrong by (I_solved/I_config)².
    # Recompute on the SETTLED reported window through the same
    # ρ(T)·J²·V_cu·k_end model.  Same fix, same guard, as the P1 path below:
    # CURRENT drive is untouched, so those results stay bit-identical.  Only
    # the DC I²R part is config-dependent; the AC (proximity) part is taken
    # from the SOLVED coil field and was already right.
    if _vdrive and _IA:
        _P_cu_old = float(P_cu)
        P_cu, _k_end_used, _R_solved, _I_ph_solved = _vdrive_copper_loss(
            p, geo, _IA, _IB, _IC, n_parallel, coil_temp_c,
            end_winding_factor, copper_area_m2=_cu_area_m2)
        log.info("P2 vdrive copper: I_phase_solved=%.2f A rms (config %.2f) "
                 "-> P_cuDC %.1f -> %.1f W (R_phase=%.6g ohm)",
                 _I_ph_solved, float(I_phase_rms), _P_cu_old, float(P_cu),
                 _R_solved)

    # ── LOSS post-processing (rotor_eddy) ─────────────────────────────────
    # Same architecture as the P1 app path: the field is magnetostatic per
    # frame; the eddy-current LOSSES come from post-processing the A(t)/B(t)
    # histories — magnet + shaft via the honest (reaction-included) frequency-
    # domain rotor solve `honest_rotor_eddy`, iron via Bertotti on dB/dt.
    # (P1's default transient uses eddy=False too — the coupled σ∂A/∂t J-view
    # solve is NOT the app loss path.)  Current drive → copper = I²R (DC).
    # Iron (Bertotti) and AC copper are always in; rotor_eddy only adds the
    # coupled magnet/shaft term, which upgrades this label below.
    _lm2 = "field (P2 magnetostatic + Bertotti iron + AC copper)"
    P_cu_dc2 = float(P_cu)                              # from copper_loss_W (I²R)
    P_cu_ac_ser2 = [0.0] * n_total; P_cu_ac_avg2 = 0.0
    P_cu_ser2 = [P_cu_dc2] * n_total
    P_fe_ser2 = [0.0] * n_total; P_fe_avg2 = 0.0
    P_mag_ser2 = [0.0] * n_total; P_mag_avg2 = 0.0
    P_shaft_ser2 = [0.0] * n_total; P_shaft_avg2 = 0.0
    # Losses from the captured B(t).  Copper AC and iron are computed
    # UNCONDITIONALLY, matching the P1 path — they do not depend on the
    # rotor-eddy model.  The magnet/shaft eddy below is the part rotor_eddy
    # actually selects, and it is already gated by `if _histA_rot2:`, which
    # is only populated when the flag is on.

    # ── AC copper (proximity/skin) — MUST match P1: DC I²R is already
    #    element-order-independent (same copper_loss_W); the AC part is the
    #    coil proximity loss σ/12·Σ(d_r²·dBr² + d_t²·dBt²), field split into
    #    radial/tangential (same _prox_eddy_split model P1 uses).  Periodic
    #    central-difference dB/dt (P2 field is smooth).
    _sig_cu2, _w_cu2, _h_cu2 = _copper_ac_dims(
        geo, coil_temp_c, f_elec, RHO_CU_20, ALPHA_CU, MU0)
    if _coil_idx.size and _hcx2:
        _smp = half["s"]["mesh"]
        _cc = (_smp.p[:, _smp.t].mean(axis=1))[:, _coil_idx]
        P_cu_ac_ser2, P_cu_ac_avg2 = _proximity_loss_series(
            _hcx2, _hcy2, _coil_idx, _cc, areas_s, _sig_cu2,
            _w_cu2, _h_cu2, p.stack_length, n_total,
            _central_difference(dt), scale=NS)
    P_cu_ser2 = [P_cu_dc2 + ac for ac in P_cu_ac_ser2]

    # Iron loss: ONE implementation (simulation/losses.py) for both element
    # orders.  The only genuine difference is the dB/dt estimator — the P2
    # field is smooth in time, so a plain central difference is enough.
    _fe_terms_s: dict = {}
    _fe_terms_r: dict = {}

    def _iron_p2(hx, hy, idx, areas_half, mat, terms=None):
        # n_periods: the DFT behind the measured-surface path needs to know how
        # many electrical periods the captured window spans, or it puts every
        # harmonic at the wrong frequency.  n_total/n_periods are the TRIMMED
        # values here — the voltage settling and demag prefixes were already
        # dropped above, and both were decremented with them.
        return _iron_loss_series(
            hx, hy, idx, areas_half, mat, p.stack_length, f_elec, n_total,
            _central_difference(dt), _mat_lib.effective_bertotti, terms=terms,
            n_periods=float(n_periods))

    _pcl_s, _ph_s = _iron_p2(_hsx2, _hsy2, _iron_s_idx, areas_s, _steel_s,
                             _fe_terms_s)
    _pcl_r, _ph_r = _iron_p2(_hrx2, _hry2, _iron_r_idx, areas_r, _steel_r,
                             _fe_terms_r)
    _P_fe_t = (_pcl_s + _pcl_r) * NS + (_ph_s + _ph_r) * NS
    _P_fe_t = np.maximum(_P_fe_t, 0.0)
    P_fe_ser2 = _P_fe_t.tolist(); P_fe_avg2 = float(np.mean(_P_fe_t))
    # Per-term, per-half split of the iron loss (scaled to the whole machine).
    # Reported rather than re-derived: "the core loss looks low" is answerable
    # only by which TERM is low, and until now nothing downstream could see the
    # hysteresis/eddy/excess split or the k_f each half was billed at.
    _fe_break = {}
    for _half, _tm in (("stator", _fe_terms_s), ("rotor", _fe_terms_r)):
        if _tm:
            _fe_break[_half] = {
                "hysteresis_W": round(_tm["hysteresis_W"] * NS, 3),
                "eddy_W": round(_tm["eddy_W"] * NS, 3),
                "excess_W": round(_tm["excess_W"] * NS, 3),
                "k_f": _tm["k_f"],
                # WHICH loss model produced the number above.  Carried to the
                # view because the Core tile's tooltip names the model, and it
                # must not say "Bertotti" for a steel whose measured P(B, f)
                # surface was interpolated directly.  On the surface path the
                # hysteresis/excess pair is the fit's PROPORTION of the
                # measured remainder, not a separately measured split.
                "model": _tm.get("model", "bertotti"),
            }
    if _fe_break:
        log.info("iron loss | %s | total %.2f W",
                 " | ".join("%s: hyst %.2f + eddy %.2f + excess %.2f = %.2f W "
                            "(k_f %.3f)"
                            % (_h, _v["hysteresis_W"], _v["eddy_W"],
                               _v["excess_W"],
                               _v["hysteresis_W"] + _v["eddy_W"] + _v["excess_W"],
                               _v["k_f"])
                            for _h, _v in _fe_break.items()), P_fe_avg2)
    # ── AXIAL magnet segmentation (geo `magnet_lamination`, mm) ───────────
    # BOTH routes to the magnet eddy loss below are 2-D, i.e. they solve an
    # axially INFINITE magnet whose induced current never has to turn round.
    # Slicing the magnet axially is the standard cure for that loss and nothing
    # here could see it.  The factor is computed ONCE, from the magnet bodies'
    # own mesh nodes, and applied to whichever route ends up reporting — see
    # simulation/losses.magnet_segmentation for the model and its caveats.
    # 0 (solid) returns exactly 1.0, so an unsegmented run is bit-identical.
    _seg_k, _seg_rep = 1.0, {}
    if half.get("r") is not None:
        try:
            _rm_seg = half["r"]["mesh"]
            _pt_seg = np.asarray(_rm_seg.p, float)
            _tt_seg = np.asarray(_rm_seg.t, int)
            _bodies_seg = [_pt_seg[:, np.unique(_tt_seg[:, np.asarray(_e, int)])]
                           for _tg, _e in half["r"]["cells"].items()
                           if int(_tg) >= DOM_MAG_BASE and np.size(_e)]
            _seg_k, _seg_rep = _magnet_segmentation(
                geo, _bodies_seg, float(p.stack_length))
            if _seg_k < 1.0:
                log.info("magnet segmentation | %.4g mm slices of a %.4g mm "
                         "stack, loop width %.4g mm (%d bodies) -> magnet eddy "
                         "loss x %.4g (MODEL, pending 3-D validation)",
                         _seg_rep.get("slice_mm", 0.0), _seg_rep.get("stack_mm", 0.0),
                         _seg_rep.get("width_mm", 0.0), _seg_rep.get("n_bodies", 0),
                         _seg_k)
        except Exception as _e_seg:      # noqa: BLE001 — no factor, not no run
            log.warning("magnet segmentation factor unavailable: %s", _e_seg)
            _seg_k, _seg_rep = 1.0, {}
    # magnet + shaft eddy: honest (reaction-included) rotor solve on the
    # rotor-frame A(t) history — the SAME function the P1 path uses.
    if _histA_rot2:
        try:
            from motor_ai_sim.simulation.eddy_solver_2d import (
                honest_rotor_eddy as _hre2)
            _rm = half["r"]["mesh"]
            # Tags + magnet list + mu lookup: shared with P1 (losses.py).
            _tags_r2, _magt2 = _rotor_eddy_tags(
                half["r"]["cells"], _rm.t.shape[1], DOM_MAG_BASE)
            # rotor back-iron μ_r from the CONVERGED P2 ν (last frame)
            _rir = half["r"]["cells"].get(int(DOM_ROTOR))
            _mur_bi = (1.0 / (MU0 * float(np.mean(
                nu_all2[np.asarray(_rir, int) + nst])))
                if _rir is not None and np.size(_rir) else 1000.0)
            _muf2 = _rotor_mu_lookup(_mur_bi, DOM_MAG_BASE, DOM_ROTOR)
            P_mag_avg2, P_shaft_avg2, _hf2 = _hre2(
                np.asarray(_rm.p, float), np.asarray(_rm.t, int), _tags_r2,
                _muf2, _sigma_of_tag, _magt2, DOM_SHAFT,
                np.asarray(_histA_rot2, float), float(n_total) * dt,
                float(p.stack_length), float(NS))
            P_mag_ser2 = [float(P_mag_avg2)] * n_total
            P_shaft_ser2 = [float(P_shaft_avg2)] * n_total
            _lm2 = "field+honest (P2 magnetostatic + coupled rotor eddy)"
            log.info("P2 rotor eddy: mag=%.3f shaft=%.3f W (%d harmonics); "
                     "iron=%.3f W, copper(dc)=%.1f W", P_mag_avg2,
                     P_shaft_avg2, len(_hf2), P_fe_avg2, P_cu_dc2)
        except Exception as _e2:
            log.warning("P2 honest rotor eddy failed: %s", _e2)

    # ── COUPLED EDDY: the solved σ∫E² REPLACES the modelled numbers ───────
    # Everything above is a MODEL of a loss the magnetostatic field cannot
    # produce: the copper AC term is the proximity/skin formula, magnet and
    # shaft come from the frequency-domain rotor solve driven by a history.
    # When the coupled solve ran, the loss is not modelled any more — it is
    # ∫σE² of the field that was actually solved with σ·∂A/∂t in it, so that
    # is what gets reported.  The modelled numbers stay computed and are
    # returned + logged beside it: two independent routes to the same watts
    # is the only cross-check this code has.
    #
    # COPPER SPLIT.  The 2-D solve knows nothing about end windings, so its
    # total is the ACTIVE-LENGTH loss.  Subtracting the active-length DC
    # (ΣI²/S, the same bars at ∂A/∂t = 0) leaves the pure AC increment,
    # which is then added to the end-winding-corrected DC — instead of
    # subtracting the k_end-inflated DC from a 2-D total, which is how the
    # P1 path reports a NEGATIVE copper AC at low current.
    P_cu_ac_prox_avg2 = float(P_cu_ac_avg2)
    P_mag_prox_avg2 = float(P_mag_avg2); P_shaft_prox_avg2 = float(P_shaft_avg2)
    P_cu_solve_avg2 = P_cu_dc2d_avg2 = 0.0
    if eddy and _ed_cu:
        P_cu_solve_avg2 = float(np.mean(_ed_cu))
        P_cu_dc2d_avg2 = float(np.mean(_ed_dc2d))
        P_cu_ac_ser2 = [t - d for t, d in zip(_ed_cu, _ed_dc2d)]
        P_cu_ac_avg2 = float(np.mean(P_cu_ac_ser2))
        P_cu_ser2 = [P_cu_dc2 + ac for ac in P_cu_ac_ser2]
        _lm2 = ("field+coupled eddy (P2 sigma*dA/dt solve: copper"
                + (" + magnet/shaft)" if rotor_eddy else ")"))
        log.info("EDDY-SOLVE(P2) copper total=%.2f W (2-D DC=%.2f + AC=%.2f) "
                 "vs slab DC+AC=%.2f W (prox AC=%.2f); reported DC(+end "
                 "winding)=%.2f W", P_cu_solve_avg2, P_cu_dc2d_avg2,
                 P_cu_ac_avg2, P_cu_dc2 + P_cu_ac_prox_avg2,
                 P_cu_ac_prox_avg2, P_cu_dc2)
        if rotor_eddy:
            P_mag_ser2 = list(_ed_mag); P_mag_avg2 = float(np.mean(_ed_mag))
            P_shaft_ser2 = list(_ed_sh); P_shaft_avg2 = float(np.mean(_ed_sh))
            log.info("EDDY-SOLVE(P2) magnet=%.3f shaft=%.3f W vs honest "
                     "frequency-domain magnet=%.3f shaft=%.3f W",
                     P_mag_avg2, P_shaft_avg2, P_mag_prox_avg2,
                     P_shaft_prox_avg2)
    # The segmentation factor multiplies whichever magnet number is REPORTED —
    # coupled or frequency-domain — and the other one too, because they are two
    # routes to the same physical watts and are read against each other.  The
    # SHAFT is untouched: it is one continuous conductor, not a stack of slices.
    if _seg_k < 1.0:
        _P_mag_solid2 = float(P_mag_avg2)
        P_mag_ser2 = [float(v) * _seg_k for v in P_mag_ser2]
        P_mag_avg2 = float(P_mag_avg2) * _seg_k
        P_mag_prox_avg2 = float(P_mag_prox_avg2) * _seg_k
        log.info("magnet segmentation | P_mag %.4g -> %.4g W (x %.4g)",
                 _P_mag_solid2, P_mag_avg2, _seg_k)
    P_tot_ser2 = [c + f + m + s for c, f, m, s in
                  zip(P_cu_ser2, P_fe_ser2, P_mag_ser2, P_shaft_ser2)]
    P_loss_avg2 = float(np.mean(P_tot_ser2)) if P_tot_ser2 else 0.0

    # ── Per-element loss DENSITY (W/m³) for the Loss map ──────────────────
    # simulation/losses.py, the SAME map the field views render.  It lives
    # in the snapshot (not the top-level result) because it is per-ELEMENT
    # data whose ordering is the snapshot's [stator-half | rotor-half].
    # The derivative operator is the only element-order difference: the P2
    # field is smooth in time, so the plain central difference the P2 loss
    # totals already use is enough (P1 needed the slip-jitter smoother).
    # The map self-normalises each MODELLED component to the reported watts
    # above, so a 1-frame view (no B history) yields zeros rather than a wrong
    # picture.  The components the COUPLED eddy solve produced are not modelled
    # and not normalised: they are the per-element σE² of the field that was
    # actually solved, cycle-averaged over the SAME reported window the watts
    # come from.
    if _snap2 is not None:
        try:
            _cd2 = _central_difference(dt)
            _cc2 = ((half["s"]["mesh"].p[:, half["s"]["mesh"].t].mean(axis=1))
                    [:, _coil_idx] if _coil_idx.size else np.zeros((2, 0)))
            # Cycle-averaged per-element σE², expanded to the snapshot's global
            # element order (zero wherever σ = 0 — air and laminated iron carry
            # no solved eddy current by construction).
            _sol_dens = None; _sol_groups = (); _sol_elems = {}
            if eddy and _ed_dens_hist:
                _sol_dens = np.zeros(int(mesh_all.t.shape[1]))
                _sol_dens[_ed_elems] = np.mean(
                    np.asarray(_ed_dens_hist, float), axis=0)
                # Axial segmentation scales the magnet WATTS above; the map is
                # the same watts per m³, so it has to carry the same factor or
                # the picture stops integrating to the number beside it.  The
                # SHAPE is left alone — this factor is a lumped end-resistance
                # correction and knows nothing about where in the block the
                # current crowds.
                if _seg_k < 1.0 and _ed_gmask.get("mag") is not None \
                        and _ed_gmask["mag"].any():
                    _sol_dens[_ed_elems[_ed_gmask["mag"]]] *= _seg_k
                _sol_elems = {_k: _ed_elems[_m]
                              for _k, _m in _ed_gmask.items() if _m.any()}
                _sol_groups = tuple(_sol_elems.keys())
                log.info("P2 loss map: solved σE² covers %s (%d conductor "
                         "elements, %d frames averaged)",
                         "+".join(_sol_groups) or "nothing",
                         int(_ed_elems.size), len(_ed_dens_hist))
            (_snap2["loss_dens"], _snap2["loss_dens_label"],
             _snap2["loss_dens_unmodelled"]) = _loss_density_map(
                n_stator_elems=int(Tts.shape[1]),
                n_elems=int(mesh_all.t.shape[1]),
                hist_sx=_hsx2, hist_sy=_hsy2, hist_rx=_hrx2, hist_ry=_hry2,
                hist_mx=_hmx2, hist_my=_hmy2, hist_cx=_hcx2, hist_cy=_hcy2,
                iron_s_idx=_iron_s_idx, iron_r_idx=_iron_r_idx,
                mag_idx=_mag_idx, coil_idx=_coil_idx,
                areas_s=areas_s, areas_r=areas_r, coil_centroids=_cc2,
                steel_s=_steel_s, steel_r=_steel_r,
                bertotti=_mat_lib.effective_bertotti,
                f_elec_hz=f_elec, stack_length_m=p.stack_length,
                # Same reason as `_iron_p2`: the map's iron shape is the SAME
                # loss model as the reported watts, so it needs the same
                # harmonic frequencies.
                n_periods=float(n_periods),
                sector_scale=NS,
                P_fe_avg=P_fe_avg2, P_mag_avg=P_mag_avg2,
                P_cu_dc=P_cu_dc2, P_cu_ac_avg=P_cu_ac_avg2,
                sigma_cu=_sig_cu2, d_cu_r=_w_cu2, d_cu_t=_h_cu2,
                ddt=lambda X, qp=None: _cd2(X),
                solved_dens=_sol_dens, solved_groups=_sol_groups,
                solved_elems=_sol_elems,
                # The end turns are copper the 2-D plane does not contain: the
                # reported DC includes them (k_end), the solved active-length
                # σE² cannot.  Pass the DIFFERENCE so the map's copper integral
                # still closes on the reported watts without pretending the end
                # turns crowd like the slot copper does.
                P_cu_end_winding_W=(max(0.0, P_cu_dc2 - P_cu_dc2d_avg2)
                                    if (eddy and _ed_cu) else 0.0),
                log_line=log.info)
            _snap2["loss_dens"] = _snap2["loss_dens"].tolist()
            log.info("P2 loss map label: %s", _snap2["loss_dens_label"])
        except Exception as _lde:
            log.warning("P2 loss-density map failed: %s", _lde)

    # ── metrics ──────────────────────────────────────────────────────────
    # Raw Maxwell-stress (Arkkio) torque — kept as a DIAGNOSTIC only.  On the
    # node-repaired sliding band the gap field is contaminated UNDER LOAD, so
    # the volume-weighted Maxwell integral is radius-INCONSISTENT (measured
    # 0.78..1.30 Nm across integration bands for one converged frame) and
    # over-reads the mean torque ~35 % vs the energy method / ANSYS.
    _T2raw = list(_T2)                       # preserve the Maxwell series (diag)
    # ── Torque harmonic spectrum over ONE electrical period ──────────────────
    # The single most telling diagnostic for "is this periodic or chaotic": a
    # clean ripple shows a few DISCRETE peaks (the cogging / 6·k 3-phase orders);
    # broadband noise spreads across all orders.  Orders are multiples of the
    # ELECTRICAL fundamental; amplitude is the single-sided FFT magnitude [N·m].
    # ALWAYS the RAW per-frame torque (not the band-limited series), so the UI
    # shows every order and the user can SEE which bars the 6·k filter keeps
    # (orange) vs drops.  Computed on the P1 path only until now, so the UI's
    # harmonic chart went blank the day P2 became the default — the helper is
    # element-order-agnostic and belongs beside the ripple it explains.
    T_harm_order, T_harm_amp = _torque_harmonics(_T2raw, n_steps_per_period)
    T_arr = np.asarray(_T2, float)
    T_maxwell_avg = float(T_arr.mean()) if T_arr.size else 0.0
    # HYBRID torque (energy-consistent MEAN + Maxwell-stress RIPPLE):
    # simulation/sb_postproc.py — ONE definition, shared with the P1 path.
    _torque_method = "maxwell_stress"
    try:
        _T2, _torque_method = _hybrid_torque(
            _psiA, _psiB, _psiC, _IA, _IB, _IC, _T2raw, pole_pairs,
            n_parallel=int(n_parallel))
    except Exception as _te:
        log.warning("P2 hybrid torque failed (%s) — using Maxwell series", _te)
    T_arr = np.asarray(_T2, float)
    Tavg = float(T_arr.mean()) if T_arr.size else 0.0
    _Tf, Trip, Trip_raw, Tnoise = band_limit_torque(
        _T2, int(n_steps_per_period), int(round(n_periods)))
    _omega_m2 = 2.0 * math.pi * rpm / 60.0
    P_airgap_avg2 = float(Tavg * _omega_m2)
    P_mech_avg2 = P_airgap_avg2 - (P_fe_avg2 + P_mag_avg2 + P_shaft_avg2)
    # Terminal voltage V = R·i + dψ/dt — the SAME two-term formula and the
    # SAME spectral estimator the P1 path uses.  Two reporting-only bugs
    # lived here, and together they made the two element orders disagree on
    # V_peak by ~11-13 % for runs whose FIELDS agree to ~1.5 %:
    #   • np.gradient is NOT periodic — it drops to a one-sided difference
    #     at the first and last frame, and V_peak is a max over the series,
    #     so those two edge frames set the reported peak.  The spectral
    #     derivative (`_spectral_ddt_series`, shared with P1) is periodic by
    #     construction and truncates the slip-node quantisation jitter.
    #   • the R·i drop was in the comment but never in the code, so P2
    #     reported the back-EMF where P1 reports the terminal voltage.
    # ψ and I are per-branch on BOTH paths (identical sc_psi = L·NS/n_par),
    # so the two are directly comparable.  Reporting only: the circuit solve
    # closes on its own residual and never reads this series.
    _Kv2 = max(1, min(5, (int(n_total) // 2) - 1))

    def _ddt(arr):
        a = np.asarray(arr, float)
        return (_spectral_ddt_series(a, _Kv2, dt).tolist() if a.size > 1
                else [0.0] * a.size)
    VA = [R_phase * i + e for i, e in zip(_IA, _ddt(_psiA))]
    VB = [R_phase * i + e for i, e in zip(_IB, _ddt(_psiB))]
    VC = [R_phase * i + e for i, e in zip(_IC, _ddt(_psiC))]
    Vpk = float(np.max(np.abs(VA + VB + VC))) if _psiA else 0.0
    # Terminal electrical input ⟨Σ v·i⟩ (EXACTLY 0 at no-load).  IA/IB/IC are
    # PER-BRANCH conductor currents, so one branch per phase is what ⟨Σ v·i⟩
    # measures and the machine total carries the n_parallel factor — the same
    # correction the P1 path has.  The P2 return simply did not have this key,
    # so every consumer that computes efficiency as (P_elec−P_loss)/P_elec —
    # the field view's sidebar among them — read a missing 0.0 and reported
    # 0 % efficiency for a machine doing real work.
    P_elec_in2 = (float(np.mean(np.asarray(VA) * np.asarray(_IA)
                                + np.asarray(VB) * np.asarray(_IB)
                                + np.asarray(VC) * np.asarray(_IC)))
                  * float(n_parallel) if _IA else 0.0)
    _ang = [(k / n_total) * period_mech * n_periods for k in range(n_total)]
    log.info("P2 belt transient done: %d frames reported (%d SOLVED incl. "
             "settling) in %.1f s, T_avg=%.5f Nm, ripple_raw=%.2f%%, max "
             "nonlinear resid=%.2e, picard-fallback frames=%s",
             n_total, _n_solved, _t.time() - t0, Tavg, Trip_raw,
             _pic_res_max, _pic_fallback or "none")
    # What the two per-run caches actually saved, so a slow run can be read
    # instead of guessed: a healthy run reuses ONE symbolic factorization for a
    # whole frame's Newton sweep and serves ~45 % of its Kpw calls from the memo.
    log.info("P2 cost: %d linear solves on %d symbolic factorizations "
             "(%.1f solves/analysis), Kpw %d assembled + %d memo hits, "
             "perturbed-pivot solves=%d",
             _p2.pardiso_solves, _p2.pardiso_analyses,
             _p2.pardiso_solves / max(_p2.pardiso_analyses, 1),
             _p2.kpw_calls, _p2.kpw_hits, _p2.pardiso_perturbed)
    if _pic_unconv:
        # Loud, because it means the reported window contains a frame whose
        # field never met a convergence test — the averages below are then an
        # average over one unconverged sample.
        log.warning("P2: %d frame(s) NOT converged (%s) — max resid %.2e "
                    "against tol %.1e", len(_pic_unconv), _pic_unconv,
                    _pic_res_max, _PIC_TOL2)
    # Demag map + per-magnet report.
    _dcoef2 = _dfield2 = None
    _drep2 = []
    if demag and _dmst is not None:
        _dcoef2, _dfield2, _rep2 = _dmst.payload(
            int(Tts.shape[1]), mesh_all.p * 1e3, mesh_all.t,
            np.concatenate([np.asarray(ts), np.asarray(tr)]),
            dump_H=_os_sb.environ.get("SB_DEMAG_H_DUMP") == "1")
        for _row in _rep2:
            _row["magnet_index"] = int(_row["magnet_index"] - DOM_MAG_BASE)
            _drep2.append(_row)
        log.warning("P2 demag: %d/%d magnet elems de-rated, min Br_factor %.3f",
                    int(np.sum(_br_glob < 0.999)), int(_mag_idx.size),
                    float(_br_glob.min()))
    return {
        "method": "sliding_band_p2", "element_order": 2,
        # WHICH ELECTRICAL FRAME THIS RUN WAS SOLVED IN.  gamma is measured
        # from the q-axis, and the q-axis is wherever the d-axis calibration
        # put it — so a run whose calibration landed on the wrong sample of
        # psi_A solves a DIFFERENT operating point than the gamma on screen,
        # with nothing in the result to say so.  It cost an hour of bisection
        # to recover this number for one run by fitting T and V_peak against a
        # gamma sweep; it is one float, and it belongs in every payload.
        "daxis_deg": round(float(daxis_eff), 4),
        "gamma_effective_deg": round(float(gamma_deg), 4),
        "loss_model": _lm2,
        "demag_coef_per_tri": (_dcoef2.tolist() if _dcoef2 is not None else None),
        "demag_report": _drep2,
        "demag_field": _dfield2,
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        # What the run COST.  n_steps is the REPORTED window; these two are the
        # frames actually solved (settling included) and the wall seconds they
        # took, which is what the Simulation tab turns into its pre-run estimate.
        # Solver time only — the route's summary build and JSON serialisation
        # sit outside it, so this UNDERSTATES the click-to-chart wait slightly
        # and can never overstate it.
        "n_frames_solved": int(_n_solved),
        "solve_wall_s": round(float(_t.time() - t0), 1),
        # What the caller ASKED for, beside what actually ran.  The whole-node
        # snap silently changed the time resolution of every run whose requested
        # count was not a divisor of the slip-node grid; a consumer can now say
        # "requested 40 -> ran 36" instead of presenting 36 as if it were asked
        # for.  ``slip_nodes_per_period`` is the grid that decides the snap.
        "n_steps_per_period_requested": int(_req_steps),
        "steps_snapped": bool(int(n_steps_per_period) != int(_req_steps)),
        "slip_nodes_per_period": int(_nodes_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
        "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": _tt, "rotor_angle_deg": _ang,
        "T_em_Nm": _T2, "T_avg_Nm": Tavg, "T_ripple_pct": Trip_raw,
        "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip,
        "T_noise_floor_pct": round(float(Tnoise), 2),
        "T_em_raw_Nm": list(_T2), "T_em_filt_Nm": _Tf,
        "torque_method": _torque_method,
        "T_avg_maxwell_Nm": round(T_maxwell_avg, 4),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
        "T_em_maxwell_Nm": list(_T2raw),
        "psi_A_Wb": _psiA, "psi_B_Wb": _psiB, "psi_C_Wb": _psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": _IA, "I_B": _IB, "I_C": _IC,
        "P_cu_W": P_cu_ser2, "P_fe_W": P_fe_ser2,
        "P_mag_eddy_W": P_mag_ser2, "P_shaft_eddy_W": P_shaft_ser2,
        "P_loss_total_W": P_tot_ser2,
        "P_cu_dc_W": P_cu_dc2, "P_cu_ac_W": P_cu_ac_ser2,
        # Coupled σ·∂A/∂t solve (eddy=True) — the REPORTED copper AC / magnet
        # / shaft above come from these when it ran; the modelled numbers are
        # kept beside them as the cross-check (see the swap block).
        "eddy_coupled": bool(eddy),
        # ── coupled-eddy warm-up honesty ─────────────────────────────────
        # How many discarded frames at θ<0 it took to settle the σ·∂A/∂t
        # history, and how much start-up transient was STILL left when the
        # reported window started (as a fraction of the settled solid loss).
        # A run whose residual is above eddy_warmup_tol has its P_mag/P_shaft
        # cycle means — and its efficiency — over-read by roughly that much,
        # and says so here instead of only in a log line.
        "eddy_warmup_frames": int(_n_warm),
        # None (not inf/NaN — this dict is serialised to JSON) when the march
        # was too short to say anything, which only happens if SB_EDDY_WARM
        # pinned it below the probe length.
        "eddy_warmup_resid": (None if (_warm_resid is None
                                       or not math.isfinite(_warm_resid))
                              else float("%.3g" % _warm_resid)),
        "eddy_warmup_tol": float(_EDDY_SETTLE_TOL),
        # NO tau here on purpose.  The decay is not one exponential: on the
        # 150 mm the fit off the first frames reads 35 us and the fit off the
        # settled tail reads 680 us, while the transient actually needed ~0.4
        # ms to die — so a single "tau_est" would under-read the settling time
        # by 10x in exactly the direction that makes a run look trustworthy
        # when it is not.  The frame count and the residual are measured, not
        # fitted; those are what ship.  (The local fit is still logged, where
        # it is labelled for what it is.)
        "P_cu_total_solve_W": round(float(P_cu_solve_avg2), 3),
        "P_cu_dc_2d_solve_W": round(float(P_cu_dc2d_avg2), 3),
        "P_cu_ac_solve_W": round(float(P_cu_ac_avg2), 3) if eddy else 0.0,
        "P_cu_ac_prox_W": round(float(P_cu_ac_prox_avg2), 3),
        "P_mag_solve_W": (round(float(P_mag_avg2), 3)
                          if (eddy and rotor_eddy) else 0.0),
        "P_shaft_solve_W": (round(float(P_shaft_avg2), 3)
                            if (eddy and rotor_eddy) else 0.0),
        "P_mag_honest_W": round(float(P_mag_prox_avg2), 3),
        "P_shaft_honest_W": round(float(P_shaft_prox_avg2), 3),
        # AXIAL magnet segmentation — what `magnet_lamination` did to the two
        # magnet numbers above.  Always present (factor 1.0 + the measured loop
        # width on a solid magnet), so the card can say "solid" rather than say
        # nothing.  See simulation/losses.magnet_segmentation: it is a MODEL.
        "magnet_segmentation": _seg_rep,
        "P_fe_avg_W": round(float(P_fe_avg2), 3),
        "P_fe_terms": _fe_break,   # {stator|rotor: hyst/eddy/excess/k_f/model}
        "P_loss_total_avg_W": round(float(P_loss_avg2), 3),
        "P_airgap_W": P_airgap_avg2, "P_mech_avg_W": P_mech_avg2,
        "P_elec_in_W": P_elec_in2,               # ⟨Σ v·i⟩ (0 at no-load)
        "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
        "n_parallel": int(n_parallel),
        # The LABEL the paths came from, carried with them.  n_parallel alone
        # cannot be read back to a connection (2S-2P and 1S-2P both say 2), and
        # the card that shows these numbers has to name the winding they belong
        # to — "changed the connection, nothing moved" is unanswerable without
        # it.  Empty = no label, or a label that disagrees with the paths that
        # were actually solved (an explicit n_parallel wins over it) — naming it
        # then would be a lie, and no name is the honest answer.
        "connection": _conn_label_used,
        "picard_iters_mean": (round(float(np.mean(_pic_iters)), 1)
                              if _pic_iters else 0.0),
        "picard_iters_max": (int(max(_pic_iters)) if _pic_iters else 0),
        # 3 significant figures, not 6 DECIMALS: a Newton-solved window sits at
        # ~1e-7 and round(...,6) reported that as a flat 0.0 — a convergence
        # gauge that cannot show convergence.
        "picard_resid_max": float("%.3g" % _pic_res_max),
        "picard_tol": float(_PIC_TOL2),
        # True only if EVERY reported frame met the tolerance of the path that
        # solved it (Newton 1e-7 on the field residual, Picard _PIC_TOL2 on the
        # ν fixed point) — see the per-frame bookkeeping in the frame loop.
        "picard_converged": not _pic_unconv,
        "picard_unconverged_frames": list(_pic_unconv),
        # Frames the Newton path did not solve.  Not an error by itself (the
        # fallback has its own tolerance), but it IS the thing to look at when
        # one frame's numbers sit apart from its neighbours'.
        "picard_fallback_frames": list(_pic_fallback),
        "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        # Drive mode: "current" (imposed sinusoidal I) or "voltage" (imposed
        # sinusoidal V — the currents above are the machine's own response).
        "drive": ("voltage" if _vdrive else "current"),
        "v_phase_peak_V": float(v_phase_peak) if _vdrive else None,
        "v_delta_deg": float(v_delta_deg) if _vdrive else None,
        # Aitken settling anchor: attempts vs applications.  applied == 0 with
        # attempts > 0 means the guards skipped every one and the anchor did
        # nothing for this run — the measured state on the pinned machine.
        "v_anchor_attempts": int(_v_anchor_tries),
        "v_anchor_applied": int(_v_anchor_applied),
        # circuit-iteration convergence stats, one entry per frame of the
        # REPORTED window (settling frames stripped with every other series,
        # so the indices line up with I/psi/T) + the honest steady-state
        # quality gauge: mean phase current over that window (≈0 A on a
        # converged periodic orbit).
        "v_drive_diag": (_v_diag if _vdrive else None),
        "v_dc_residual_A": (round(float(np.mean(np.asarray(_IA, float))), 3)
                            if (_vdrive and _IA) else None),
        "field": _snap2,
        # Animation keyframes (empty unless return_frames>0).  ONE mesh for the
        # whole run — that is the whole point of the sliding band — so the
        # topology travels ONCE in frames_mesh and each frame carries only what
        # actually changes: its rotor-rotated node coordinates and its field.
        "frames": _frames2,
        "frames_mesh": ({"T": mesh_all.t.copy(),
                         "tags": np.concatenate(
                             [np.asarray(ts), np.asarray(tr)]).astype(int),
                         "nsn": int(nsn)} if _frames2 else None),
    }


def _build_full_disk_from_halves(polys, rotor_angle_deg, mesh_size_mm,
                                 min_size_mm, outer_air_factor, motion_band,
                                 band_thickness_mm, geo_cfg, component_mesh_mm,
                                 normal_deviation_deg=6.0, aspect_ratio=10.0,
                                 gap_layers=3.0):
    """Build a CLEAN full-disk (n_sectors=1) mesh by stitching TWO 1/2 sector
    meshes (the half is meshed cleanly by OCC; the full 360° is NOT).

    Steps: build the clean half (n_sectors=2) → duplicate it rotated 180° →
    weld the coincident seam nodes → reclassify every triangle by its centroid
    against the FULL (un-clipped) polygons.  Result is a manifold (no
    overlapping/double-meshed iron) full disk that the magnetostatics solver
    handles as the genuine 360° motor (no periodic BC).

    Returns (MeshTri, cell_tags int16, classify_fn) — same contract as
    build_mesh_from_polygons.  classify_fn.polys = the full polys so
    build_materials assigns per-magnet/per-coil materials correctly.
    """
    import numpy as _np
    import scipy.sparse as _sp
    from scipy.sparse.csgraph import connected_components as _cc
    from scipy.spatial import cKDTree as _KD
    import shapely as _sh
    from skfem import MeshTri as _MT

    # 1) clean half (n_sectors=2 → OCC meshes the open wedge without overlaps)
    mesh2, _ct2, _cf2 = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor, motion_band=motion_band,
        band_thickness_mm=band_thickness_mm, gap_layers=gap_layers, n_sectors=2,
        geo_cfg=geo_cfg, component_mesh_mm=component_mesh_mm)
    V = mesh2.p.T; T = mesh2.t.T; N = len(V)

    # 2) stitch: half + 180°-rotated copy, then weld coincident seam nodes
    Vf = _np.vstack([V, -V])              # 180° rotation = (x,y)->(-x,-y)
    Tf = _np.vstack([T, T + N]); n2 = len(Vf)
    pairs = _KD(Vf).query_pairs(r=1e-7)
    if pairs:
        ij = _np.array(list(pairs)).T
        g = _sp.coo_matrix((_np.ones(ij.shape[1]), (ij[0], ij[1])), shape=(n2, n2))
        _, lab = _cc(g + g.T, directed=False)
    else:
        lab = _np.arange(n2)
    uniq, inv = _np.unique(lab, return_inverse=True)
    Vw = _np.zeros((len(uniq), 2)); _np.add.at(Vw, inv, Vf)
    Vw /= _np.bincount(inv)[:, None]
    Tw = inv[Tf]
    good = ((Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2]))
    Tw = Tw[good]
    meshF = _MT(Vw.T, Tw.T.copy())

    # 3) classify each triangle by centroid against the FULL (un-clipped) polys
    cen = Vw[Tw].mean(axis=1) * 1000.0    # mesh metres → polygon mm
    rr = _np.hypot(cen[:, 0], cen[:, 1])
    _gc = geo_cfg or {}
    r_ro = float(_gc.get("rotor_outer_radius", 0.0))
    r_si = float(_gc.get("stator_inner_radius", 0.0))
    ct = _np.full(len(Tw), DOM_AIR, dtype=_np.int32)
    if r_ro > 0.0 and r_si > r_ro:
        ct[(rr >= r_ro) & (rr <= r_si)] = DOM_AIRGAP
    clf = []
    for i, (mp, _pl) in enumerate(polys.get("magnets", [])):
        if mp is not None and not mp.is_empty:
            clf.append((mp, DOM_MAG_BASE + i))
    for i, cp in enumerate(polys.get("coils", [])):
        if cp is not None and not cp.is_empty:
            clf.append((cp, DOM_COIL_BASE + i))
    for k, dm in (("shaft", DOM_SHAFT), ("rotor", DOM_ROTOR), ("stator", DOM_STATOR)):
        gg = polys.get(k)
        if gg is not None and not gg.is_empty:
            clf.append((gg, dm))
    # least-specific first so magnets/coils (front of clf) overwrite last → win
    for gg, tag in reversed(clf):
        try:
            ct[_sh.contains_xy(gg, cen[:, 0], cen[:, 1])] = tag
        except Exception:
            pass

    class _CF:
        pass
    cf = _CF(); cf.polys = polys
    log.info("FEM-sim: stitched full disk from 2 halves — %d nodes, %d tris",
             len(Vw), len(Tw))
    return meshF, ct.astype(_np.int16), cf


def em_transient_eval(
    *,
    n_steps_per_period: int,
    n_periods: float,
    gamma_deg: float,
    I_phase_rms: float,
    rpm: Optional[float] = None,     # mechanical speed [rpm]; None = the global
                                     # config's simulation.rpm (see
                                     # fem_transient_sliding_band)
    n_parallel: Optional[int] = None,   # winding parallel paths; None = the global
                                     # config's winding.n_parallel
    connection: Optional[str] = None,   # winding connection label ("2S-2P"); supplies
                                     # n_parallel and the d-axis topology key
    mesh_size_mm: float = 4.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,
    n_sectors: int = -1,
    stator_fillet_mm: float = 0.0,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    rotor_eddy: bool = False,
    demag: bool = False,
    torque_filter: bool = False,
    pole_copy=None,
    component_mesh_mm=None,
    geo_override=None,
    progress_cb=None,
    hi_fidelity: bool = False,
    structured_gap: bool = False,
    iron_template=None,
    geo_mesh=None,
    airgap_macro: bool = False,
    frozen_nu: bool = False,
    drive: str = "current",
    v_phase_peak: float = 0.0,
    v_delta_deg: float = 0.0,
    element_order: int = 2,          # 2 = P2, the only basis (see fem_transient_sliding_band)
    return_frames: int = 0,          # >0: also return N animation keyframes
    eddy: bool = False,              # coupled sigma*dA/dt eddy-current solve (the J-view physics)
    return_field: bool = False,      # ALSO return the LAST frame's field snapshot
                                     # (mesh + A + B + tags + Jeddy + loss_dens) under
                                     # result["field"].  No extra solve: it is the frame
                                     # the transient just finished, kept instead of thrown
                                     # away, so the field views can render the run's own
                                     # field instead of re-solving it.
) -> Dict:
    """THE single canonical sliding-band transient invocation.

    Every consumer that needs a 2-D transient solve funnels through here — the
    Simulation route (get_fem_transient), the optimizer (refine_proc.run_one) and
    the solver.em_transient module — so the optimizer's physics can NEVER drift
    from what the Simulation tab shows. Pure: no caching, no global progress, no
    disk-save (those UI concerns stay in the route, which wraps this). Returns the
    raw sliding-band result dict (sbres).
    """
    return fem_transient_sliding_band(
        n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
        gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
        rpm=(None if rpm is None else float(rpm)),
        n_parallel=(None if n_parallel is None else int(n_parallel)),
        connection=(None if connection is None else str(connection)),
        mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
        outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
        n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
        stator_fillet_mm=float(stator_fillet_mm),
        coil_temp_c=float(coil_temp_c), end_winding_factor=float(end_winding_factor),
        rotor_eddy=bool(rotor_eddy), demag=bool(demag),
        torque_filter=bool(torque_filter), pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh,
        component_mesh_mm=(component_mesh_mm or {}), geo_override=geo_override,
        progress_cb=progress_cb, hi_fidelity=bool(hi_fidelity),
        structured_gap=bool(structured_gap), airgap_macro=bool(airgap_macro),
        frozen_nu=bool(frozen_nu),
        drive=str(drive or "current"), v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg), element_order=int(element_order),
        return_frames=int(return_frames),
        eddy=bool(eddy),
        # field_first is NOT set: the snapshot is the LAST frame, the one whose
        # B(t) history is complete, which is what the loss map and the coupled
        # eddy J are derived from.
        return_field=bool(return_field))
